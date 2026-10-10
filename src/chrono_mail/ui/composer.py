from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import override

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon

# --- Version ------------------------------------------------------------
__version__ = "composer.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: window icon
# v0.0.13: Initial composer; recipients are parsed one by one


def _split_addresses(text: str) -> list[str]:
    """Split on commas, semicolons and line breaks that are not inside quotes or angle brackets."""
    parts: list[str] = []
    current: list[str] = []
    quoted = angle = False
    for character in text:
        if character == '"':
            quoted = not quoted
        elif character == "<" and not quoted:
            angle = True
        elif character == ">" and not quoted:
            angle = False
        if character in ",;\n" and not quoted and not angle:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def parse_recipients(text: str) -> list[str] | None:
    """'Alice <a@x.org>, b@y.org' as a list of addresses, or None when something in it is not an address.

    Each address is read on its own: getaddresses() of Python 3.14 silently returns nothing for the
    whole field when one part is malformed, which would drop valid addresses.
    """
    found: list[str] = []
    for part in _split_addresses(text):
        name, address = parseaddr(part)
        if address.count("@") != 1 or address.startswith("@") or address.endswith("@") or any(character.isspace() for character in address):
            return None
        found.append(formataddr((name, address)) if name else address)
    return found


class ComposeWindow(QMainWindow):
    """Writes a new message, a reply, or a forward, and sends it through the engine.

    A message that cannot be sent is kept in the Outbox folder by the engine; it is never lost and
    never sent again by itself.
    """

    sent = Signal(str)  # account id

    def __init__(
        self,
        backend: Backend,
        accounts: list[object],
        account_id: str,
        draft: object | None = None,
        source: tuple[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._source: tuple[str, str] | None = source
        self._kind: str = str(getattr(draft, "kind")) if draft is not None else "new"
        self._in_reply_to: str | None = getattr(draft, "in_reply_to") if draft is not None else None
        self._references: tuple[str, ...] = tuple(getattr(draft, "references")) if draft is not None else ()
        self._sending: bool = False
        self._finished: bool = False
        self.setWindowTitle("New message")
        self.setWindowIcon(application_icon())
        central = QWidget(self)
        layout = QVBoxLayout(central)
        form = QFormLayout()
        self.from_box = QComboBox(central)
        for account in accounts:
            self.from_box.addItem(f"{getattr(account, 'display_name')} <{getattr(account, 'email_address')}>", str(getattr(account, "account_id")))
        index = self.from_box.findData(account_id)
        self.from_box.setCurrentIndex(max(0, index))
        self.to = QLineEdit(central)
        self.cc = QLineEdit(central)
        self.bcc = QLineEdit(central)
        self.subject = QLineEdit(central)
        form.addRow("From", self.from_box)
        form.addRow("To", self.to)
        form.addRow("Cc", self.cc)
        form.addRow("Bcc", self.bcc)
        form.addRow("Subject", self.subject)
        layout.addLayout(form)
        self.body = QPlainTextEdit(central)
        layout.addWidget(self.body, 1)

        files = QHBoxLayout()
        self.attachments = QListWidget(central)
        self.attachments.setMaximumHeight(70)
        side = QVBoxLayout()
        self.attach_button = QPushButton("Attach a file…", central)
        self.remove_button = QPushButton("Remove", central)
        side.addWidget(self.attach_button)
        side.addWidget(self.remove_button)
        side.addStretch(1)
        files.addWidget(self.attachments, 1)
        files.addLayout(side)
        layout.addLayout(files)
        self.include_original = QCheckBox("", central)
        self.include_original.hide()
        layout.addWidget(self.include_original)

        buttons = QHBoxLayout()
        self.status = QLabel("", central)
        self.send_button = QPushButton("Send", central)
        self.send_button.setDefault(True)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)
        self.setCentralWidget(central)
        self.resize(760, 640)

        if draft is not None:
            self.to.setText(", ".join(getattr(draft, "to")))
            self.cc.setText(", ".join(getattr(draft, "cc")))
            self.subject.setText(str(getattr(draft, "subject")))
            self.body.setPlainText(str(getattr(draft, "body")))
            names: tuple[str, ...] = tuple(getattr(draft, "attachment_names"))
            if names:
                self.include_original.setText(f"Include the attachments of the original message ({', '.join(names)})")
                self.include_original.setChecked(True)
                self.include_original.show()
            self.setWindowTitle(str(getattr(draft, "subject")) or "New message")
            self.body.moveCursor(self.body.textCursor().MoveOperation.Start)
        self.body.document().setModified(False)
        self._text_at_start: tuple[str, str, str, str, str] = self._fields()

        send_action = QAction("Send", self)
        send_action.setShortcut(QKeySequence("Ctrl+Return"))
        send_action.triggered.connect(self.send)
        self.addAction(send_action)
        self.send_button.clicked.connect(self.send)
        self.attach_button.clicked.connect(self._choose_files)
        self.remove_button.clicked.connect(self._remove_selected)
        self.to.setFocus()

    # --- state ------------------------------------------------------------------------------------

    def _fields(self) -> tuple[str, str, str, str, str]:
        return (self.to.text(), self.cc.text(), self.bcc.text(), self.subject.text(), self.body.toPlainText())

    def is_modified(self) -> bool:
        return self._fields() != self._text_at_start or self.attachments.count() > 0

    def attached_paths(self) -> list[str]:
        return [str(self.attachments.item(row).data(256)) for row in range(self.attachments.count())]

    def add_attachment(self, path: str) -> None:
        item_text = f"{Path(path).name}  ({Path(path).stat().st_size // 1024 + 1} KB)" if Path(path).exists() else Path(path).name
        self.attachments.addItem(item_text)
        self.attachments.item(self.attachments.count() - 1).setData(256, path)

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for path in paths:
            self.add_attachment(path)

    def _remove_selected(self) -> None:
        for item in self.attachments.selectedItems():
            self.attachments.takeItem(self.attachments.row(item))

    # --- sending ------------------------------------------------------------------------------------

    def send(self) -> None:
        if self._sending:
            return
        recipients = {name: parse_recipients(field.text()) for name, field in (("To", self.to), ("Cc", self.cc), ("Bcc", self.bcc))}
        for name, value in recipients.items():
            if value is None:
                QMessageBox.warning(self, "Send", f"The {name} field contains something that is not an e-mail address.")
                return
        to, cc, bcc = recipients["To"] or [], recipients["Cc"] or [], recipients["Bcc"] or []
        if not (to or cc or bcc):
            QMessageBox.warning(self, "Send", "Add at least one recipient.")
            return
        if not self.subject.text().strip():
            answer = QMessageBox.question(self, "Send", "This message has no subject. Send it anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        values: dict[str, object] = {
            "account": str(self.from_box.currentData()),
            "to": to,
            "subject": self.subject.text(),
            "body": self.body.toPlainText(),
            "attach": self.attached_paths(),
        }
        if cc:
            values["cc"] = cc
        if bcc:
            values["bcc"] = bcc
        if self._in_reply_to:
            values["in_reply_to"] = self._in_reply_to
        if self._references:
            values["references"] = list(self._references)
        if self._source is not None and self._kind in ("reply", "reply_all", "forward"):
            values["source_folder"], values["source_message"] = self._source
            values["mark_replied"] = self._kind in ("reply", "reply_all")
            values["include_source_attachments"] = self._kind == "forward" and self.include_original.isChecked()
        self._set_sending(True)
        self._backend.call("mail.send", values, self._sent, self._failed, owner=self)

    def _set_sending(self, sending: bool) -> None:
        self._sending = sending
        for widget in (self.send_button, self.to, self.cc, self.bcc, self.subject, self.body, self.from_box, self.attach_button, self.remove_button):
            widget.setEnabled(not sending)
        self.status.setText("Sending…" if sending else "")

    def _sent(self, result: object) -> None:
        self._set_sending(False)
        if not getattr(result, "sent"):
            QMessageBox.warning(
                self,
                "The message was not sent",
                f"{getattr(result, 'error')}\n\nThe message has been kept in your Outbox folder, so nothing is lost. "
                "It will not be sent again by itself.",
            )
            self.sent.emit(str(self.from_box.currentData()))  # the Outbox changed
            return
        problems = list(getattr(result, "refused")) + ([str(getattr(result, "warning"))] if getattr(result, "warning") else [])
        if problems:
            QMessageBox.information(self, "Sent", "The message was sent, with remarks:\n\n" + "\n".join(problems))
        self._finished = True
        self.sent.emit(str(self.from_box.currentData()))
        self.close()

    def _failed(self, error: ChronoMailError) -> None:
        self._set_sending(False)
        QMessageBox.warning(self, "The message was not sent", f"{error}\n\nNothing was stored or sent. You can correct this and try again.")

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._finished or not self.is_modified():
            event.accept()
            return
        answer = QMessageBox.question(self, "Discard this message?", "The message has not been sent. Discard it?")
        if answer == QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()
