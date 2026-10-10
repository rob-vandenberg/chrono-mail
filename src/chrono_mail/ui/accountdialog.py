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
from chrono_mail.ui.certificatedialog import inspect_and_ask
from chrono_mail.ui.colours import green, red

# --- Version ------------------------------------------------------------
__version__ = "accountdialog.py 0.0.51"

# --- Version history ----------------------------------------------------
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.43: accepted certificates
# v0.0.31: IMAP accounts
# v0.0.16: Drafts
# v0.0.13: Initial account dialog

SECURITY_CHOICES: tuple[tuple[str, str], ...] = (("tls", "TLS (encrypted from the start)"), ("starttls", "STARTTLS (upgrade to encrypted)"), ("none", "None (not encrypted)"))
INCOMING_PORTS: dict[str, int] = {"tls": 995, "starttls": 110, "none": 110}
IMAP_PORTS: dict[str, int] = {"tls": 993, "starttls": 143, "none": 143}
KINDS: tuple[tuple[str, str], ...] = (("pop3", "POP3: the mail is downloaded to this computer"), ("imap", "IMAP: the mail stays on the server and is kept in step"))
OUTGOING_PORTS: dict[str, int] = {"tls": 465, "starttls": 587, "none": 25}
POLICIES: tuple[tuple[str, str], ...] = (
    ("keep", "Leave all mail on the server (default)"),
    ("delete_after_download", "Delete from the server once downloaded and verified"),
    ("delete_after_days", "Delete from the server 30 days after download"),
    ("delete_when_deleted_locally", "Delete from the server when I delete it here"),
)


class AccountDialog(QDialog):
    """Adds a POP3/SMTP account. Nothing is created until the connection has been tested or the person insists."""

    def __init__(self, backend: Backend, first_account: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.trusted: list[str] = []  # fingerprints of server certificates the person accepted in this dialog
        self._cert_servers: list[str] = []
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
        self.kind = QComboBox(who)
        for value, label in KINDS:
            self.kind.addItem(label, value)
        who_form.addRow("Account type", self.kind)
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
        self.incoming_group = self._server_group("Incoming mail (POP3)", self.incoming_host, self.incoming_port, self.incoming_security)
        layout.addWidget(self.incoming_group)
        self.outgoing_host = QLineEdit(self)
        self.outgoing_port = QSpinBox(self)
        self.outgoing_security = self._security_combo()
        layout.addWidget(self._server_group("Outgoing mail (SMTP)", self.outgoing_host, self.outgoing_port, self.outgoing_security))

        options = QGroupBox("Options", self)
        options_layout = QFormLayout(options)
        self.policy = QComboBox(options)
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.storage = QComboBox(options)
        self.storage.addItem("A mailbox of its own, with its own folders", "own")
        self.storage.addItem("Local Folders, together with the other accounts that keep their mail there", "local")
        self.storage.setToolTip("Only for POP3. You can change this later in the settings of the account.")
        self.encrypt = QCheckBox("Encrypt the stored mail on this computer (recommended)", options)
        self.encrypt.setChecked(True)
        options_layout.addRow("Server copies", self.policy)
        options_layout.addRow("Keep the mail in", self.storage)
        options_layout.addRow("", self.encrypt)
        layout.addWidget(options)

        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.result_label)

        self.certificate_button = QPushButton("Look at the server's certificate…", self)
        self.certificate_button.hide()
        layout.addWidget(self.certificate_button)  # not in the button box: the box shows every button it holds
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
        self.incoming_security.currentIndexChanged.connect(self._incoming_security_changed)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.outgoing_security.currentIndexChanged.connect(lambda: self.outgoing_port.setValue(OUTGOING_PORTS[str(self.outgoing_security.currentData())]))
        self.incoming_port.setValue(INCOMING_PORTS["tls"])
        self.outgoing_port.setValue(OUTGOING_PORTS["starttls"])
        self.outgoing_security.setCurrentIndex(1)
        self.email.textEdited.connect(self._guess_from_address)
        self.certificate_button.clicked.connect(self._look_at_certificates)
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

    def _is_imap(self) -> bool:
        return str(self.kind.currentData()) == "imap"

    def _incoming_security_changed(self) -> None:
        self.incoming_port.setValue((IMAP_PORTS if self._is_imap() else INCOMING_PORTS)[str(self.incoming_security.currentData())])

    def _kind_changed(self) -> None:
        """POP3 or IMAP: the title and the port follow, and the choice about copies on the server only matters for POP3."""
        self.incoming_group.setTitle("Incoming mail (IMAP)" if self._is_imap() else "Incoming mail (POP3)")
        self._incoming_security_changed()
        self.policy.setEnabled(not self._is_imap())
        self.storage.setEnabled(not self._is_imap())
        if self._is_imap():
            self.storage.setCurrentIndex(0)  # an IMAP account always has the folders of its server

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

    def _certificate_trouble(self, *messages: tuple[str, str]) -> None:
        """When a server's certificate was the problem, offer to look at it (and accept it, if the person knows it is theirs)."""
        self._cert_servers = [name for name, message in messages if "certificate" in message.lower()]
        self.certificate_button.setVisible(bool(self._cert_servers))

    def _look_at_certificates(self) -> None:
        servers = list(self._cert_servers)

        def ask_next() -> None:
            if not servers:
                self._test()  # try again with what was accepted
                return
            which = servers.pop(0)
            if which == "incoming":
                host, port, security, kind = self.incoming_host.text().strip(), self.incoming_port.value(), str(self.incoming_security.currentData()), str(self.kind.currentData())
            else:
                host, port, security, kind = self.outgoing_host.text().strip(), self.outgoing_port.value(), str(self.outgoing_security.currentData()), "smtp"

            def accepted(fingerprint: str) -> None:
                if fingerprint not in self.trusted:
                    self.trusted.append(fingerprint)
                ask_next()

            inspect_and_ask(self._backend, self, host, port, security, kind, accepted)

        ask_next()

    _last_guess_user: str = ""
    _last_guess_host: str = ""

    # --- the commands -----------------------------------------------------------------------------

    def _values(self) -> dict[str, object]:
        return {
            "kind": str(self.kind.currentData()),
            "trusted_certificate": list(self.trusted),
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
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the e-mail address, user name, password and both servers.</span>")
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
            self._certificate_trouble(("incoming", str(getattr(result, "incoming_message"))), ("outgoing", str(getattr(result, "outgoing_message"))))

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>{error}</span>")

        self._backend.call("account.check", self._values(), done, failed, owner=self)

    @staticmethod
    def _describe(result: object) -> str:
        def line(ok: object, name: str, message: object) -> str:
            colour, mark = (green(), "✓") if ok else (red(), "✗")
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
            "storage": "own" if self._is_imap() else str(self.storage.currentData()),
        }

        def done(result: object) -> None:
            self.created_account_id = str(getattr(result, "account_id"))
            self.key_created = bool(getattr(result, "key_created"))
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>The account could not be added: {error}</span>")
            self._certificate_trouble(("incoming", str(error)))

        self._backend.call("account.add", values, done, failed, owner=self)

    @override
    def reject(self) -> None:
        super().reject()


def explain_error(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
