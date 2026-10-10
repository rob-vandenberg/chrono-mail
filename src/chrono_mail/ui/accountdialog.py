from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "accountdialog.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial account dialog

SECURITY_CHOICES: tuple[tuple[str, str], ...] = (("tls", "TLS (encrypted from the start)"), ("starttls", "STARTTLS (upgrade to encrypted)"), ("none", "None (not encrypted)"))
INCOMING_PORTS: dict[str, int] = {"tls": 995, "starttls": 110, "none": 110}
OUTGOING_PORTS: dict[str, int] = {"tls": 465, "starttls": 587, "none": 25}
POLICIES: tuple[tuple[str, str], ...] = (
    ("keep", "Keep every message on the server"),
    ("delete_after_download", "Delete from the server once downloaded and verified"),
    ("delete_after_days", "Delete from the server 30 days after download"),
    ("delete_when_deleted_locally", "Delete from the server when I delete it here"),
)


class AccountDialog(QDialog):
    """Adds a POP3/SMTP account. Nothing is created until the connection has been tested or the person insists."""

    def __init__(self, backend: Backend, first_account: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.created_account_id: str | None = None
        self.key_created: bool = False
        self.setWindowTitle("Add an account")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        if first_account:
            welcome = QLabel("<b>Welcome to Chrono Mail.</b><br>Add your first e-mail account to get started.", self)
            welcome.setWordWrap(True)
            layout.addWidget(welcome)

        who = QGroupBox("Your account", self)
        who_form = QFormLayout(who)
        self.name = QLineEdit(who)
        self.email = QLineEdit(who)
        self.username = QLineEdit(who)
        self.password = QLineEdit(who)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        who_form.addRow("Name", self.name)
        who_form.addRow("E-mail address", self.email)
        who_form.addRow("User name", self.username)
        who_form.addRow("Password", self.password)
        layout.addWidget(who)

        self.incoming_host = QLineEdit(self)
        self.incoming_port = QSpinBox(self)
        self.incoming_security = self._security_combo()
        layout.addWidget(self._server_group("Incoming mail (POP3)", self.incoming_host, self.incoming_port, self.incoming_security))
        self.outgoing_host = QLineEdit(self)
        self.outgoing_port = QSpinBox(self)
        self.outgoing_security = self._security_combo()
        layout.addWidget(self._server_group("Outgoing mail (SMTP)", self.outgoing_host, self.outgoing_port, self.outgoing_security))

        options = QGroupBox("Options", self)
        options_layout = QFormLayout(options)
        self.policy = QComboBox(options)
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.encrypt = QCheckBox("Encrypt the stored mail on this computer (recommended)", options)
        self.encrypt.setChecked(True)
        options_layout.addRow("Server copies", self.policy)
        options_layout.addRow("", self.encrypt)
        layout.addWidget(options)

        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(self)
        self.test_button = QPushButton("Test connection", self)
        self.add_button = QPushButton("Add account", self)
        self.add_button.setDefault(True)
        buttons.addButton(self.test_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.add_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for security, port in ((self.incoming_security, self.incoming_port), (self.outgoing_security, self.outgoing_port)):
            port.setRange(1, 65535)
        self.incoming_security.currentIndexChanged.connect(lambda: self.incoming_port.setValue(INCOMING_PORTS[str(self.incoming_security.currentData())]))
        self.outgoing_security.currentIndexChanged.connect(lambda: self.outgoing_port.setValue(OUTGOING_PORTS[str(self.outgoing_security.currentData())]))
        self.incoming_port.setValue(INCOMING_PORTS["tls"])
        self.outgoing_port.setValue(OUTGOING_PORTS["starttls"])
        self.outgoing_security.setCurrentIndex(1)
        self.email.textEdited.connect(self._guess_from_address)
        self.test_button.clicked.connect(self._test)
        self.add_button.clicked.connect(self._add)

    # --- layout helpers ---------------------------------------------------------------------------

    @staticmethod
    def _security_combo() -> QComboBox:
        combo = QComboBox()
        for value, label in SECURITY_CHOICES:
            combo.addItem(label, value)
        return combo

    def _server_group(self, title: str, host: QLineEdit, port: QSpinBox, security: QComboBox) -> QGroupBox:
        group = QGroupBox(title, self)
        form = QFormLayout(group)
        form.addRow("Server", host)
        form.addRow("Security", security)
        form.addRow("Port", port)
        return group

    def _guess_from_address(self, text: str) -> None:
        """Suggest the user name and the server names from the e-mail address, until the person types their own."""
        if self.username.text() in ("", self._last_guess_user):
            self.username.setText(text)
        domain = text.partition("@")[2]
        if domain:
            for field, prefix in ((self.incoming_host, "mail."), (self.outgoing_host, "mail.")):
                if field.text() in ("", self._last_guess_host):
                    field.setText(prefix + domain)
            self._last_guess_host = f"mail.{domain}"
        self._last_guess_user = text

    _last_guess_user: str = ""
    _last_guess_host: str = ""

    # --- the commands -----------------------------------------------------------------------------

    def _values(self) -> dict[str, object]:
        return {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "incoming_host": self.incoming_host.text().strip(),
            "incoming_port": self.incoming_port.value(),
            "incoming_security": str(self.incoming_security.currentData()),
            "outgoing_host": self.outgoing_host.text().strip(),
            "outgoing_port": self.outgoing_port.value(),
            "outgoing_security": str(self.outgoing_security.currentData()),
        }

    def _busy(self, busy: bool) -> None:
        for widget in (self.test_button, self.add_button):
            widget.setEnabled(not busy)

    def _complete(self) -> bool:
        if not (self.email.text().strip() and self.username.text().strip() and self.password.text() and self.incoming_host.text().strip() and self.outgoing_host.text().strip()):
            self.result_label.setText("<span style='color:#c0392b'>Please fill in the e-mail address, user name, password and both servers.</span>")
            return False
        return True

    def _test(self) -> None:
        if not self._complete():
            return
        self._busy(True)
        self.result_label.setText("Testing…")

        def done(result: object) -> None:
            self._busy(False)
            self.result_label.setText(self._describe(result))

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:#c0392b'>{error}</span>")

        self._backend.call("account.check", self._values(), done, failed, owner=self)

    @staticmethod
    def _describe(result: object) -> str:
        def line(ok: object, name: str, message: object) -> str:
            colour, mark = ("#1e8449", "✓") if ok else ("#c0392b", "✗")
            return f"<span style='color:{colour}'><b>{mark} {name}:</b> {str(message).replace('<', '&lt;')}</span>"

        return "<br>".join((
            line(getattr(result, "incoming_ok"), "Incoming", getattr(result, "incoming_message")),
            line(getattr(result, "outgoing_ok"), "Outgoing", getattr(result, "outgoing_message")),
        ))

    def _add(self) -> None:
        if not self._complete():
            return
        self._busy(True)
        self.result_label.setText("Adding the account…")
        values = {
            **self._values(),
            "display_name": self.name.text().strip() or self.email.text().strip(),
            "email_address": self.email.text().strip(),
            "encrypted": self.encrypt.isChecked(),
            "delete_policy": str(self.policy.currentData()),
        }

        def done(result: object) -> None:
            self.created_account_id = str(getattr(result, "account_id"))
            self.key_created = bool(getattr(result, "key_created"))
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:#c0392b'>The account could not be added: {error}</span>")

        self._backend.call("account.add", values, done, failed, owner=self)

    @override
    def reject(self) -> None:
        super().reject()


def explain_error(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
