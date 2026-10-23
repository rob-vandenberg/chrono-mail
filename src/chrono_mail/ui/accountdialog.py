from collections.abc import Callable
from typing import Literal, override

from PySide6.QtCore import Qt, QTimer
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
    QVBoxLayout,
    QWidget,
)

from chrono_mail.config import server_pin
from chrono_mail.core import AccountKind, ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_ADD, ACCOUNT_CHECK, AccountAddParameters, AccountAddResult, AccountCheckParameters, AccountCheckResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import inspect_and_ask
from chrono_mail.ui.colours import red
from chrono_mail.ui.serverfields import SAME_LOGIN_TEXT, ServerFields, mail_server_kind, policy_of, security_of, test_result_text

# --- Version ------------------------------------------------------------
__version__ = "accountdialog.py 2.4.332"

# --- Version history ----------------------------------------------------
# v2.4.332: the first-account window offers Restore a backup… (the Windows computer's W2)
# v1.9.272: the certificate look gets typed security and kind
# v1.9.271: R-19: Test connection and Add are typed calls
# v1.1.243: The server boxes are the shared ServerFields (the same as the account editor's); a typed port stays when Security changes; the
#           account kinds by name (R-05)
# v1.1.213: The user name and password per server, with "Use the same user name and password as for incoming mail" for the outgoing one; no help lines (D-186)
# v1.1.212: The help line of an account of calendars and contacts only
# v1.1.211: Account name (only in Chrono Mail) above Your name (was Name), each with a grey line that says who sees it (D-184)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.71: A certificate accepted for a server address is no longer used after the address, port or security changed
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.43: accepted certificates
# v0.0.31: IMAP accounts
# v0.0.16: Drafts
# v0.0.13: Initial account dialog

KINDS: tuple[tuple[AccountKind, str], ...] = ((AccountKind.POP3, "POP3: the mail is downloaded to this computer"), (AccountKind.IMAP, "IMAP: the mail stays on the server and is kept in step"))
POLICIES: tuple[tuple[str, str], ...] = (
    ("keep", "Leave all mail on the server (default)"),
    ("delete_after_download", "Delete from the server once downloaded and verified"),
    ("delete_after_days", "Delete from the server 30 days after download"),
    ("delete_when_deleted_locally", "Delete from the server when I delete it here"),
)


class AccountDialog(QDialog):
    """Adds a POP3/SMTP account. Nothing is created until the connection has been tested or the person insists."""

    def _restore_instead(self) -> None:
        """Restore a backup… instead of adding an account: this window closes, then the restore starts (its windows are never under this one)."""
        self.reject()
        if self._restore is not None:
            QTimer.singleShot(0, self._restore)

    def __init__(self, backend: Backend, first_account: bool = False, parent: QWidget | None = None, restore: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._pins: dict[str, tuple[tuple[str, int, str], str]] = {}  # incoming/outgoing -> (host, port, security) it was accepted for, fingerprint
        self._cert_servers: list[str] = []
        self.created_account_id: str | None = None
        self.key_created: bool = False
        self._restore: Callable[[], None] | None = restore  # the first-account window's Restore a backup…: the main window's restore
        self.setWindowTitle("Add an account")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        if first_account:
            welcome = QLabel("<b>Welcome to Chrono Mail.</b><br>Add your first e-mail account to get started, or bring back a backup of Chrono Mail.", self)
            welcome.setWordWrap(True)
            layout.addWidget(welcome)
        self.restore_button: QPushButton = QPushButton("Restore a backup…", self)
        self.restore_button.setVisible(first_account and restore is not None)
        if first_account and restore is not None:
            self.restore_button.clicked.connect(self._restore_instead)  # a person on a new computer looks for it here first (W2)
            layout.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignLeft)

        who = QGroupBox("Your account", self)
        who_form = QFormLayout(who)
        self.kind = QComboBox(who)
        for kind, label in KINDS:
            self.kind.addItem(label, kind.value)
        who_form.addRow("Account type", self.kind)
        self.account_name = QLineEdit(who)
        self.name = QLineEdit(who)
        self.email = QLineEdit(who)
        add_name_rows(who_form, self.account_name, self.name, self.email)
        layout.addWidget(who)

        self.servers = ServerFields(self, AccountKind.POP3, editing=False)
        servers = self.servers
        self.username, self.password, self.same_login = servers.username, servers.password, servers.same_login
        self.incoming_host, self.incoming_port, self.incoming_security = servers.incoming_host, servers.incoming_port, servers.incoming_security
        self.outgoing_host, self.outgoing_port, self.outgoing_security = servers.outgoing_host, servers.outgoing_port, servers.outgoing_security
        self.outgoing_username, self.outgoing_password = servers.outgoing_username, servers.outgoing_password
        self.incoming_group = servers.incoming_group
        layout.addWidget(servers.incoming_group)
        layout.addWidget(servers.outgoing_group)

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

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.email.textEdited.connect(self._guess_from_address)
        self.certificate_button.clicked.connect(self._look_at_certificates)
        self.test_button.clicked.connect(self._test)
        self.add_button.clicked.connect(self._add)

    # --- what follows what ------------------------------------------------------------------------

    def _is_imap(self) -> bool:
        return str(self.kind.currentData()) == AccountKind.IMAP.value

    def _kind_changed(self) -> None:
        """POP3 or IMAP: the title and the port follow, and the choice about copies on the server only matters for POP3."""
        self.servers.set_kind(AccountKind(str(self.kind.currentData())))
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

    def _endpoint(self, which: str) -> tuple[str, int, str]:
        return self.servers.endpoint(which)

    @property
    def trusted(self) -> list[str]:
        """The certificates the person accepted for the servers as they are now, each for its own server (host:port=fingerprint): one accepted for an address that was changed since is dropped."""
        return [server_pin(endpoint[0], endpoint[1], fingerprint) for which, (endpoint, fingerprint) in self._pins.items() if endpoint == self._endpoint(which)]

    def _look_at_certificates(self) -> None:
        servers = list(self._cert_servers)

        def ask_next() -> None:
            if not servers:
                self._test()  # try again with what was accepted
                return
            which = servers.pop(0)
            host, port, security = self._endpoint(which)
            kind = mail_server_kind(str(self.kind.currentData())) if which == "incoming" else "smtp"

            def accepted(fingerprint: str) -> None:
                self._pins[which] = ((host, port, security), fingerprint)
                ask_next()

            inspect_and_ask(self._backend, self, host, port, security_of(security), kind, accepted)

        ask_next()

    _last_guess_user: str = ""
    _last_guess_host: str = ""

    # --- the commands -----------------------------------------------------------------------------

    def _values(self) -> AccountCheckParameters:
        return self.servers.check_parameters(trusted=tuple(self.trusted))

    def _busy(self, busy: bool) -> None:
        for widget in (self.test_button, self.add_button):
            widget.setEnabled(not busy)

    def _complete(self) -> bool:
        if not (self.email.text().strip() and self.username.text().strip() and self.password.text() and self.incoming_host.text().strip() and self.outgoing_host.text().strip()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the e-mail address, user name, password and both servers.</span>")
            return False
        if not self.same_login.isChecked() and not (self.outgoing_username.text().strip() and self.outgoing_password.text()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the user name and password of the outgoing server, or tick {SAME_LOGIN_TEXT}.</span>")
            return False
        return True

    def _test(self) -> None:
        if not self._complete():
            return
        self._busy(True)
        self.result_label.setText("Testing…")

        def done(result: AccountCheckResult) -> None:
            self._busy(False)
            self.result_label.setText(test_result_text(result))
            self._certificate_trouble(("incoming", result.incoming_message), ("outgoing", result.outgoing_message))

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>{error}</span>")

        self._backend.ask(ACCOUNT_CHECK, self._values(), done, failed, owner=self)

    def _add(self) -> None:
        if not self._complete():
            return
        self._busy(True)
        self.result_label.setText("Adding the account…")
        servers = self._values()
        storage: Literal["own", "local"] = "local" if not self._is_imap() and str(self.storage.currentData()) == "local" else "own"
        parameters = AccountAddParameters(
            display_name=self.name.text().strip() or self.email.text().strip(), email_address=self.email.text().strip(), username=servers.username,
            password=servers.password, incoming_host=servers.incoming_host, outgoing_host=servers.outgoing_host, incoming_port=servers.incoming_port,
            incoming_security=servers.incoming_security, outgoing_port=servers.outgoing_port, outgoing_security=servers.outgoing_security,
            outgoing_username=servers.outgoing_username, outgoing_password=servers.outgoing_password, encrypted=self.encrypt.isChecked(),
            delete_policy=policy_of(self.policy.currentData()), kind=servers.kind, trusted_certificate=servers.trusted_certificate, storage=storage,
            account_name=self.account_name.text().strip(),
        )

        def done(result: AccountAddResult) -> None:
            self.created_account_id = result.account_id
            self.key_created = result.key_created
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>The account could not be added: {error}</span>")
            self._certificate_trouble(("incoming", str(error)))

        self._backend.ask(ACCOUNT_ADD, parameters, done, failed, owner=self)

    @override
    def reject(self) -> None:
        super().reject()



def add_name_rows(form: QFormLayout, account_name: QLineEdit, your_name: QLineEdit, email: QLineEdit) -> None:
    """Account name, Your name and E-mail address (D-184): the same in Add account and the editor."""
    form.addRow("Account name", account_name)
    form.addRow("Your name", your_name)
    form.addRow("E-mail address", email)


def explain_error(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
