from collections.abc import Callable
from datetime import datetime
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import override

from PySide6.QtCore import QTimer, Signal
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
__version__ = "composer.py 0.0.16"

# --- Version history ----------------------------------------------------
# v0.0.16: drafts: save, autosave, resume, keep attachments, save/discard/cancel on close
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


AUTOSAVE_SECONDS: int = 30
ROLE_PATH: int = 256  # an attachment that is a file on this computer
ROLE_KEPT: int = 257  # an attachment that is already inside the stored draft (its position there)


class ComposeWindow(QMainWindow):
    """Writes a new message, a reply, or a forward, and sends it through the engine.

    The message is saved as a draft when asked and automatically every 30 seconds while it changes; a
    message that cannot be sent is kept in the Outbox folder by the engine. Nothing the person wrote
    is lost, and nothing is sent again by itself.
    """

    sent = Signal(str)  # account id
    draft_saved = Signal(str)  # account id

    def __init__(
        self,
        backend: Backend,
        accounts: list[object],
        account_id: str,
        draft: object | None = None,
        source: tuple[str, str] | None = None,
        resume: tuple[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        resumed_source = (str(getattr(draft, "source_folder")), str(getattr(draft, "source_message"))) if getattr(draft, "source_message", None) else None
        self._source: tuple[str, str] | None = source or resumed_source
        self._kind: str = str(getattr(draft, "kind")) if draft is not None else "new"
        self._in_reply_to: str | None = getattr(draft, "in_reply_to") if draft is not None else None
        self._references: tuple[str, ...] = tuple(getattr(draft, "references")) if draft is not None else ()
        self._stored: tuple[str, str] | None = resume  # the saved version of this message (folder, id), if there is one
        self._created_here: bool = resume is None  # a draft this window made itself is removed again on "Discard"
        self._sending: bool = False
        self._saving: bool = False
        self._finished: bool = False
        self._account_id: str = account_id
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
        self.save_button = QPushButton("Save draft", central)
        self.send_button = QPushButton("Send", central)
        self.send_button.setDefault(True)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)
        self.setCentralWidget(central)
        self.resize(760, 640)

        if draft is not None:
            self.to.setText(", ".join(getattr(draft, "to")))
            self.cc.setText(", ".join(getattr(draft, "cc")))
            self.bcc.setText(", ".join(getattr(draft, "bcc", ())))
            self.subject.setText(str(getattr(draft, "subject")))
            self.body.setPlainText(str(getattr(draft, "body")))
            names: tuple[str, ...] = tuple(getattr(draft, "attachment_names", ()))
            if names:
                self.include_original.setText(f"Include the attachments of the original message ({', '.join(names)})")
                self.include_original.setChecked(True)
                self.include_original.show()
            for stored in getattr(draft, "attachments", ()):
                self._add_kept(int(getattr(stored, "position")), str(getattr(stored, "name")), int(getattr(stored, "size")))
            self.setWindowTitle(str(getattr(draft, "subject")) or "New message")
            self.body.moveCursor(self.body.textCursor().MoveOperation.Start)
        self.body.document().setModified(False)
        self._saved_snapshot: tuple[object, ...] = self._snapshot()

        for text, shortcut, handler in (("Send", "Ctrl+Return", self._send_clicked), ("Save draft", "Ctrl+S", self._save_clicked)):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(handler)
            self.addAction(action)
        self.send_button.clicked.connect(self._send_clicked)
        self.save_button.clicked.connect(self._save_clicked)
        self.attach_button.clicked.connect(self._choose_files)
        self.remove_button.clicked.connect(self._remove_selected)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start(AUTOSAVE_SECONDS * 1000)
        self.to.setFocus()

    # --- state ------------------------------------------------------------------------------------

    def stored_as(self) -> tuple[str, str] | None:
        """The folder and id of the saved version of this message, if there is one."""
        return self._stored

    def _fields(self) -> tuple[str, str, str, str, str]:
        return (self.to.text(), self.cc.text(), self.bcc.text(), self.subject.text(), self.body.toPlainText())

    def _snapshot(self) -> tuple[object, ...]:
        return (self._fields(), tuple((self.attachments.item(row).data(ROLE_PATH), self.attachments.item(row).data(ROLE_KEPT)) for row in range(self.attachments.count())), str(self.from_box.currentData()))

    def is_modified(self) -> bool:
        return self._snapshot() != self._saved_snapshot

    def attached_paths(self) -> list[str]:
        return [str(self.attachments.item(row).data(ROLE_PATH)) for row in range(self.attachments.count()) if self.attachments.item(row).data(ROLE_PATH) is not None]

    def kept_positions(self) -> list[str]:
        return [str(self.attachments.item(row).data(ROLE_KEPT)) for row in range(self.attachments.count()) if self.attachments.item(row).data(ROLE_KEPT) is not None]

    def add_attachment(self, path: str) -> None:
        item_text = f"{Path(path).name}  ({Path(path).stat().st_size // 1024 + 1} KB)" if Path(path).exists() else Path(path).name
        self.attachments.addItem(item_text)
        self.attachments.item(self.attachments.count() - 1).setData(ROLE_PATH, path)

    def _add_kept(self, position: int, name: str, size: int) -> None:
        self.attachments.addItem(f"{name}  ({size // 1024 + 1} KB)")
        self.attachments.item(self.attachments.count() - 1).setData(ROLE_KEPT, position)

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for path in paths:
            self.add_attachment(path)

    def _remove_selected(self) -> None:
        for item in self.attachments.selectedItems():
            self.attachments.takeItem(self.attachments.row(item))

    def _recipients(self) -> tuple[list[str], list[str], list[str]] | None:
        parsed = {name: parse_recipients(field.text()) for name, field in (("To", self.to), ("Cc", self.cc), ("Bcc", self.bcc))}
        for name, value in parsed.items():
            if value is None:
                self._bad_field = name
                return None
        return parsed["To"] or [], parsed["Cc"] or [], parsed["Bcc"] or []

    _bad_field: str = ""

    # --- drafts -------------------------------------------------------------------------------------

    def _draft_values(self, to: list[str], cc: list[str], bcc: list[str]) -> dict[str, object]:
        values: dict[str, object] = {
            "account": str(self.from_box.currentData()),
            "subject": self.subject.text(),
            "body": self.body.toPlainText(),
            "attach": self.attached_paths(),
            "keep_attachments": self.kept_positions(),
            "kind": self._kind,
        }
        for name, value in (("to", to), ("cc", cc), ("bcc", bcc)):
            if value:
                values[name] = value
        if self._in_reply_to:
            values["in_reply_to"] = self._in_reply_to
        if self._references:
            values["references"] = list(self._references)
        if self._source is not None:
            values["source_folder"], values["source_message"] = self._source
        if self._stored is not None:
            values["replace_folder"], values["replace_message"] = self._stored
        return values

    def _save_clicked(self, checked: bool = False) -> None:
        self.save_draft()

    def _send_clicked(self, checked: bool = False) -> None:
        self.send()

    def save_draft(self, then: Callable[[], None] | None = None, quiet: bool = False) -> None:
        """Save the message as a draft (replacing the version saved before)."""
        if self._sending or self._saving:
            return
        recipients = self._recipients()
        if recipients is None:
            if not quiet:
                QMessageBox.warning(self, "Save draft", f"The {self._bad_field} field contains something that is not an e-mail address.")
            else:
                self.status.setText("Not saved yet: check the addresses")
            return
        values = self._draft_values(*recipients)
        snapshot = self._snapshot()
        self._saving = True
        self.status.setText("Saving the draft…")

        def saved(result: object) -> None:
            self._saving = False
            self._stored = (str(getattr(result, "folder")), str(getattr(result, "message_id")))
            self.status.setText(f"Draft saved at {datetime.now().strftime('%H:%M')}")
            self._refresh_kept_attachments(snapshot, then)

        def failed(error: ChronoMailError) -> None:
            self._saving = False
            self.status.setText("The draft could not be saved")
            QMessageBox.warning(self, "Save draft", f"The draft could not be saved: {error}")

        self._backend.call("draft.save", values, saved, failed, owner=self)

    def _refresh_kept_attachments(self, snapshot: tuple[object, ...], then: Callable[[], None] | None) -> None:
        """After a save every attachment is inside the stored draft: list them as such, so that the next save keeps them."""
        stored = self._stored
        account = str(self.from_box.currentData())

        def opened(result: object) -> None:
            if self._fields() == snapshot[0]:  # nothing was typed in the meantime
                self.attachments.clear()
                for attachment in getattr(result, "attachments"):
                    self._add_kept(int(getattr(attachment, "position")), str(getattr(attachment, "name")), int(getattr(attachment, "size")))
                self._saved_snapshot = self._snapshot()
            self.draft_saved.emit(account)
            if then is not None:
                then()

        def failed(error: ChronoMailError) -> None:
            self._saved_snapshot = snapshot
            self.draft_saved.emit(account)
            if then is not None:
                then()

        if stored is None:
            return
        self._backend.call("draft.open", {"account": account, "folder": stored[0], "message": stored[1]}, opened, failed, owner=self)

    def _autosave(self) -> None:
        if self._finished or self._sending or self._saving or not self.is_modified():
            return
        if not (self.to.text().strip() or self.cc.text().strip() or self.bcc.text().strip() or self.subject.text().strip() or self.body.toPlainText().strip() or self.attachments.count()):
            return  # nothing worth saving yet
        self.save_draft(quiet=True)

    # --- sending ------------------------------------------------------------------------------------

    def send(self) -> None:
        if self._sending or self._saving:
            return
        recipients = self._recipients()
        if recipients is None:
            QMessageBox.warning(self, "Send", f"The {self._bad_field} field contains something that is not an e-mail address.")
            return
        to, cc, bcc = recipients
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
        if self._stored is not None:
            values["replaces_folder"], values["replaces_message"] = self._stored  # the draft (or Outbox message) becomes the sent message
            if self.kept_positions():
                values["keep_folder"], values["keep_message"] = self._stored
                values["keep_attachments"] = self.kept_positions()
        self._set_sending(True)
        self._backend.call("mail.send", values, self._sent, self._failed, owner=self)

    def _set_sending(self, sending: bool) -> None:
        self._sending = sending
        for widget in (self.send_button, self.save_button, self.to, self.cc, self.bcc, self.subject, self.body, self.from_box, self.attach_button, self.remove_button):
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

    # --- closing ------------------------------------------------------------------------------------

    def _close_for_good(self) -> None:
        self._finished = True
        self.close()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._finished or not self.is_modified():
            self.autosave_timer.stop()
            event.accept()
            return
        buttons = QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        answer = QMessageBox.question(self, "Save this message?", "The message has not been sent. Save it as a draft?", buttons, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            event.ignore()
            self.save_draft(then=self._close_for_good)  # the window closes when the draft is safely stored
        elif answer == QMessageBox.StandardButton.Discard:
            if self._created_here and self._stored is not None:  # a draft that only this window made (by autosave) goes with it
                folder, message = self._stored
                self._backend.call("message.delete", {"account": str(self.from_box.currentData()), "folder": folder, "message": message, "permanent": True}, self._ignore, None)
            self.autosave_timer.stop()
            event.accept()
        else:
            event.ignore()

    @staticmethod
    def _ignore(result: object) -> None:
        """The answer of a command whose result is of no interest."""
