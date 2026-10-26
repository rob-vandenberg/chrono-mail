import html
from collections.abc import Callable
from typing import Literal, override

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from chrono_mail.config import server_pin
from chrono_mail.core import AccountKind, ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_ADD, ACCOUNT_CHECK, AccountAddParameters, AccountAddResult, AccountCheckParameters, AccountCheckResult
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import inspect_and_ask
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.serverfields import SAME_LOGIN_TEXT, ServerFields, mail_server_kind, policy_of, security_of, test_result_text

# --- Version ------------------------------------------------------------
__version__ = "accountdialog.py 4.0.376"

# --- Version history ----------------------------------------------------
# v4.0.376: built from its Qt Designer form (ui/forms/account_add.ui, D-276), the server boxes at its "servers" place; its smallest width
#           follows the size factor and never cuts a text off; add_name_rows is gone (each form has its own rows)
# v3.6.350: a server's error text is shown as text, never markup (D-259)
# v3.6.349: while the account is being added everything else is greyed: Restore a backup…, the certificate, the test and the fields
#           (a test then let it close half-way, RV-207, RV-208); a test reached from the certificate does nothing then
# v3.5.346: While the account is being added the window cannot close: Cancel greyed, Escape and the close button wait (half-way the account
#           and its key were made, but its recovery code was never shown and the list not updated; RV-02)
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
        if self._adding:
            return  # never while an account is being added (RV-207)
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
        self.show_recovery_code: bool = False  # this computer's recovery code was never confirmed as stored: shown after the add (RV-03)
        self._adding: bool = False  # Add account is running: the window stays until the engine answered (RV-02)
        self._restore: Callable[[], None] | None = restore  # the first-account window's Restore a backup…: the main window's restore
        form = forms.fill(self, "account_add", {"servers": self._server_fields})  # D-276
        part(form, QLabel, "welcome").setVisible(first_account)
        self.restore_button: QPushButton = part(form, QPushButton, "restoreBackup")
        self.restore_button.setVisible(first_account and restore is not None)
        if first_account and restore is not None:
            self.restore_button.clicked.connect(self._restore_instead)  # a person on a new computer looks for it here first (W2)
        self._who: QGroupBox = part(form, QGroupBox, "yourAccount")
        self.kind: QComboBox = part(form, QComboBox, "kind")
        for kind, label in KINDS:
            self.kind.addItem(label, kind.value)
        self.account_name: QLineEdit = part(form, QLineEdit, "accountName")
        self.name: QLineEdit = part(form, QLineEdit, "yourName")
        self.email: QLineEdit = part(form, QLineEdit, "email")
        servers = self.servers
        self.username, self.password, self.same_login = servers.username, servers.password, servers.same_login
        self.incoming_host, self.incoming_port, self.incoming_security = servers.incoming_host, servers.incoming_port, servers.incoming_security
        self.outgoing_host, self.outgoing_port, self.outgoing_security = servers.outgoing_host, servers.outgoing_port, servers.outgoing_security
        self.outgoing_username, self.outgoing_password = servers.outgoing_username, servers.outgoing_password
        self.incoming_group = servers.incoming_group
        self._options: QGroupBox = part(form, QGroupBox, "options")
        self.policy: QComboBox = part(form, QComboBox, "policy")
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.storage: QComboBox = part(form, QComboBox, "storage")
        self.storage.addItem("A mailbox of its own, with its own folders", "own")
        self.storage.addItem("Local Folders, together with the other accounts that keep their mail there", "local")
        self.encrypt: QCheckBox = part(form, QCheckBox, "encrypt")
        self.encrypt.setChecked(True)
        self.result_label: QLabel = part(form, QLabel, "result")
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        self.certificate_button: QPushButton = part(form, QPushButton, "lookAtCertificate")
        self.certificate_button.hide()  # not in the button box: the box shows every button it holds
        buttons = part(form, QDialogButtonBox, "buttons")
        self.test_button = QPushButton("Test connection", buttons)
        self.add_button = QPushButton("Add account", buttons)
        self.add_button.setDefault(True)
        buttons.addButton(self.test_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.add_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button: QPushButton | None = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.email.textEdited.connect(self._guess_from_address)
        self.certificate_button.clicked.connect(self._look_at_certificates)
        self.test_button.clicked.connect(self._test)
        self.add_button.clicked.connect(self._add)

    def _server_fields(self, parent: QWidget) -> QWidget:
        """The incoming and outgoing server boxes (shared with Edit account), made at their place in the form."""
        self.servers = ServerFields(parent, AccountKind.POP3, editing=False)
        return self.servers.widget

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
            widget.setEnabled(not busy and not self._adding)  # a test that ends during an add never brings Add back (RV-208)

    def _set_adding(self, adding: bool) -> None:
        """While the account is being added nothing else can be done here: no Cancel, no test, no certificate, no restore, no field
        changed (a test started then let the window close half-way, and Add start twice; RV-02, RV-207, RV-208)."""
        self._adding = adding
        widgets: list[QWidget | None] = [
            self.test_button, self.add_button, self.cancel_button, self.restore_button, self.certificate_button, self._who,
            self.servers.incoming_group, self.servers.outgoing_group, self._options,
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not adding)

    def _complete(self) -> bool:
        if not (self.email.text().strip() and self.username.text().strip() and self.password.text() and self.incoming_host.text().strip() and self.outgoing_host.text().strip()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the e-mail address, user name, password and both servers.</span>")
            return False
        if not self.same_login.isChecked() and not (self.outgoing_username.text().strip() and self.outgoing_password.text()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the user name and password of the outgoing server, or tick {SAME_LOGIN_TEXT}.</span>")
            return False
        return True

    def _test(self) -> None:
        if self._adding or not self._complete():  # reached from an accepted certificate while the account is being added: never then
            return
        self._busy(True)
        self.result_label.setText("Testing…")

        def done(result: AccountCheckResult) -> None:
            self._busy(False)
            self.result_label.setText(test_result_text(result))
            self._certificate_trouble(("incoming", result.incoming_message), ("outgoing", result.outgoing_message))

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        self._backend.ask(ACCOUNT_CHECK, self._values(), done, failed, owner=self)

    def _add(self) -> None:
        if not self._complete():
            return
        self._set_adding(True)
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
            self._adding = False
            self.created_account_id = result.account_id
            self.key_created = result.key_created
            self.show_recovery_code = result.show_recovery_code
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self._set_adding(False)
            self.result_label.setText(f"<span style='color:{red()}'>The account could not be added: {html.escape(str(error))}</span>")
            self._certificate_trouble(("incoming", str(error)))

        self._backend.ask(ACCOUNT_ADD, parameters, done, failed, owner=self)

    @override
    def reject(self) -> None:
        """Cancel, Escape or the close button: not while the account is being added (it is then added, and its recovery code is shown)."""
        if not self._adding:
            super().reject()



def explain_error(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
