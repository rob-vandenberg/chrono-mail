from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.accountdialog import POLICIES, SECURITY_CHOICES
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "accountedit.py 0.0.20"

# --- Version history ----------------------------------------------------
# v0.0.20: Initial dialog for changing an account

RED = "color:#c0392b"
MAXIMUM_SIGNATURE_CHARACTERS: int = 4000


class AccountEditDialog(QDialog):
    """Changes an account: name, address, signature, servers, new passwords and what happens to mail on the server.
    The stored mail is not touched. A password field left empty keeps the stored password."""

    def __init__(self, backend: Backend, account: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._account: object = account
        self.setWindowTitle(f"Account: {getattr(account, 'email_address')}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        general = QWidget(self)
        general_form = QFormLayout(general)
        self.name = QLineEdit(str(getattr(account, "display_name")), general)
        self.email = QLineEdit(str(getattr(account, "email_address")), general)
        self.signature = QPlainTextEdit(general)
        self.signature.setPlainText(str(getattr(account, "signature", "")))
        self.signature.setPlaceholderText("Added at the end of new messages, replies and forwards")
        self.signature.setMinimumHeight(110)
        self.signature_count = QLabel("", general)
        general_form.addRow("Name", self.name)
        general_form.addRow("E-mail address", self.email)
        general_form.addRow("Signature", self.signature)
        general_form.addRow("", self.signature_count)
        self.tabs.addTab(general, "General")

        servers = QWidget(self)
        servers_layout = QVBoxLayout(servers)
        self.username = QLineEdit(str(getattr(account, "incoming_username")), servers)
        self.password = QLineEdit(servers)
        self.incoming_host = QLineEdit(str(getattr(account, "incoming_host")), servers)
        self.incoming_port = QSpinBox(servers)
        self.incoming_security = self._security_combo(str(getattr(account, "incoming_security")))
        servers_layout.addWidget(self._group("Incoming mail (POP3)", self.incoming_host, self.incoming_port, int(getattr(account, "incoming_port")), self.incoming_security, self.username, self.password))
        self.outgoing_username = QLineEdit(str(getattr(account, "outgoing_username")), servers)
        self.outgoing_password = QLineEdit(servers)
        self.outgoing_host = QLineEdit(str(getattr(account, "outgoing_host")), servers)
        self.outgoing_port = QSpinBox(servers)
        self.outgoing_security = self._security_combo(str(getattr(account, "outgoing_security")))
        servers_layout.addWidget(self._group("Outgoing mail (SMTP)", self.outgoing_host, self.outgoing_port, int(getattr(account, "outgoing_port")), self.outgoing_security, self.outgoing_username, self.outgoing_password))
        self.test_button = QPushButton("Test connection", servers)
        servers_layout.addWidget(self.test_button)
        servers_layout.addStretch(1)
        self.tabs.addTab(servers, "Servers")

        copies = QWidget(self)
        copies_form = QFormLayout(copies)
        self.policy = QComboBox(copies)
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.policy.setCurrentIndex(max(0, self.policy.findData(str(getattr(account, "delete_policy")))))
        self.days = QSpinBox(copies)
        self.days.setRange(1, 3650)
        self.days.setValue(int(getattr(account, "delete_days", 30)))
        self.days.setSuffix(" days")
        copies_form.addRow("Mail on the server", self.policy)
        copies_form.addRow("Delete after", self.days)
        self.tabs.addTab(copies, "Server copies")

        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.result_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.signature.textChanged.connect(self._count_signature)
        self.test_button.clicked.connect(self._test)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        self._policy_changed()
        self._count_signature()

    # --- layout -------------------------------------------------------------------------------------------

    @staticmethod
    def _security_combo(current: str) -> QComboBox:
        combo = QComboBox()
        for value, label in SECURITY_CHOICES:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def _group(self, title: str, host: QLineEdit, port: QSpinBox, port_value: int, security: QComboBox, user: QLineEdit, password: QLineEdit) -> QGroupBox:
        group = QGroupBox(title, self)
        form = QFormLayout(group)
        port.setRange(1, 65535)
        port.setValue(port_value)
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("(unchanged)")
        form.addRow("Server", host)
        form.addRow("Security", security)
        form.addRow("Port", port)
        form.addRow("User name", user)
        form.addRow("New password", password)
        return group

    def _count_signature(self) -> None:
        count = len(self.signature.toPlainText())
        self.signature_count.setText(f"{count} of {MAXIMUM_SIGNATURE_CHARACTERS} characters")
        self.signature_count.setStyleSheet(RED if count > MAXIMUM_SIGNATURE_CHARACTERS else "")
        self.save_button.setEnabled(count <= MAXIMUM_SIGNATURE_CHARACTERS)

    def _policy_changed(self) -> None:
        self.days.setEnabled(str(self.policy.currentData()) == "delete_after_days")

    # --- commands -------------------------------------------------------------------------------------------

    def _values(self) -> dict[str, object]:
        """Only what differs from the stored account (and the passwords that were typed)."""
        account = self._account
        wanted: dict[str, object] = {
            "display_name": self.name.text().strip(),
            "email_address": self.email.text().strip(),
            "signature": self.signature.toPlainText(),
            "username": self.username.text().strip(),
            "incoming_host": self.incoming_host.text().strip(),
            "incoming_port": self.incoming_port.value(),
            "incoming_security": str(self.incoming_security.currentData()),
            "outgoing_username": self.outgoing_username.text().strip(),
            "outgoing_host": self.outgoing_host.text().strip(),
            "outgoing_port": self.outgoing_port.value(),
            "outgoing_security": str(self.outgoing_security.currentData()),
            "delete_policy": str(self.policy.currentData()),
            "delete_days": self.days.value(),
        }
        stored: dict[str, object] = {
            "display_name": getattr(account, "display_name"), "email_address": getattr(account, "email_address"), "signature": getattr(account, "signature", ""),
            "username": getattr(account, "incoming_username"), "incoming_host": getattr(account, "incoming_host"), "incoming_port": getattr(account, "incoming_port"),
            "incoming_security": getattr(account, "incoming_security"), "outgoing_username": getattr(account, "outgoing_username"),
            "outgoing_host": getattr(account, "outgoing_host"), "outgoing_port": getattr(account, "outgoing_port"), "outgoing_security": getattr(account, "outgoing_security"),
            "delete_policy": getattr(account, "delete_policy"), "delete_days": getattr(account, "delete_days", 30),
        }
        values: dict[str, object] = {name: value for name, value in wanted.items() if value != stored[name]}
        if self.password.text():
            values["password"] = self.password.text()
        if self.outgoing_password.text():
            values["outgoing_password"] = self.outgoing_password.text()
        return values

    def _test(self) -> None:
        if not self.password.text():
            self.result_label.setText("Type the password in the Incoming section to test the connection.")
            return
        self.test_button.setEnabled(False)
        self.result_label.setText("Testing…")
        values: dict[str, object] = {
            "username": self.username.text().strip(), "password": self.password.text(),
            "incoming_host": self.incoming_host.text().strip(), "incoming_port": self.incoming_port.value(), "incoming_security": str(self.incoming_security.currentData()),
            "outgoing_host": self.outgoing_host.text().strip(), "outgoing_port": self.outgoing_port.value(), "outgoing_security": str(self.outgoing_security.currentData()),
            "outgoing_username": self.outgoing_username.text().strip() or self.username.text().strip(),
        }
        if self.outgoing_password.text():
            values["outgoing_password"] = self.outgoing_password.text()

        def done(result: object) -> None:
            self.test_button.setEnabled(True)
            parts = []
            for name, ok, message in (("Incoming", getattr(result, "incoming_ok"), getattr(result, "incoming_message")), ("Outgoing", getattr(result, "outgoing_ok"), getattr(result, "outgoing_message"))):
                colour, mark = ("#1e8449", "✓") if ok else ("#c0392b", "✗")
                parts.append(f"<span style='color:{colour}'><b>{mark} {name}:</b> {str(message).replace('<', '&lt;')}</span>")
            self.result_label.setText("<br>".join(parts))

        def failed(error: ChronoMailError) -> None:
            self.test_button.setEnabled(True)
            self.result_label.setText(f"<span style='{RED}'>{error}</span>")

        self._backend.call("account.check", values, done, failed, owner=self)

    def _save(self) -> None:
        values = self._values()
        if not values:
            self.accept()
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: object) -> None:
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='{RED}'>The account could not be changed: {error}</span>")

        self._backend.call("account.edit", {"account": str(getattr(self._account, "account_id")), **values}, done, failed, owner=self)
