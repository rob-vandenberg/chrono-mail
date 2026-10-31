import dataclasses
import html
from collections.abc import Callable
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from chrono_mail.core import AccountKind, CertificateError, CertificateTrouble, ChronoMailError
from chrono_mail.engine.commands_accounts import (
    ACCOUNT_CHECK, ACCOUNT_EDIT, ACCOUNT_FORGET_CERTIFICATE, ACCOUNT_FORGET_CERTIFICATES, ACCOUNT_TRUST_CERTIFICATE, AccountCheckResult, AccountEditParameters, AccountParameters, AccountSummary,
    TrustParameters,
)
from chrono_mail.engine.commands_dav import ACCOUNT_SET_DAV, SetDavParameters
from chrono_mail.engine.commands_imap import ACCOUNT_FOLDERS, ACCOUNT_SET_FOLDERS, FolderAccount, FolderChoice, SetFolders
from chrono_mail.engine.commands_local import ACCOUNT_SET_STORAGE, StorageParameters
from chrono_mail.engine.commands_dav import DAV_FIND, DAV_LINK, FindParameters, FoundServer, LinkParameters
from chrono_mail.ui.accountdialog import POLICIES
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatequestions import questions_for
from chrono_mail.ui.colours import green, red_style
from chrono_mail.ui.forms import part
from chrono_mail.ui.foundfolders import FolderTicks
from chrono_mail.ui.passwordfield import make_password_field
from chrono_mail.ui.serverfields import ServerFields, kind_of, policy_of, security_of, start_values_of, test_result_text
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "accountedit.py 4.2.447"

# --- Version history ----------------------------------------------------
# v4.2.447: Test connection and the calendar server's check always check the certificates themselves; the test after Connect uses the
#           answer; Connect without the tick takes back the account's kept certificate (D-299)
# v4.2.441: a server whose certificate could not be verified asks at once (Verify certificate) after Test connection and the calendar
#           server's check; Connect tests or looks again; the three Look at the … certificate… buttons are gone (UR-05, D-299)
# v4.2.437: the calendar server's password field has the eye (UR-03, D-296)
# v4.0.376: built from its Qt Designer form (ui/forms/account_edit.ui, D-276) with every tab in it; the tabs and rows an account of this kind
#           has not are taken out (before, the unused Server copies page of an IMAP account was drawn over the tabs at the top left); its
#           smallest width follows the size factor and never cuts a text off
# v3.6.350: a server's error text is shown as text, never markup (D-259)
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v2.1.308: General: Filter junk mail (D-238)
# v1.9.272: Look at the calendar server's certificate asks with kind https (it pretended to be IMAP)
# v1.9.271: R-19: every engine call typed; the account is an AccountSummary; the changes are typed (AccountEditParameters with only what differs, SetFolders, SetDavParameters)
# v1.1.243: The Servers tab is the shared ServerFields (the same as Add account): the port follows Security as there, a typed port stays; Test
#           connection uses the stored passwords when none was typed (never asked for again); the account kinds by name (R-05)
# v1.1.214: Calendars and contacts lists the folders of the server with a tick each, filled when the editor opens; the ticks are applied on Save (D-187)
# v1.1.213: Outgoing mail: Use the same user name and password as for incoming mail; no help lines (D-186)
# v1.1.212: An account of calendars and contacts only has two tabs: General (its account name) and Calendars and contacts (D-185)
# v1.1.211: Account name above Your name (was Name), as in Add account (D-184)
# v1.1.210: Find calendars, contacts and tasks: checks the address typed, or looks for the server; the ticked folders are kept in step (D-181)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: Tab for the calendar and contacts server
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: check interval and the folders of an IMAP account
# v0.0.43: accepted certificates
# v0.0.31: IMAP accounts have no server copies tab
# v0.0.20: Initial dialog for changing an account

MAXIMUM_SIGNATURE_CHARACTERS: int = 4000


def differing[T](wanted: T, stored: object) -> T | None:
    """A value for account.edit: what the person chose, or None (left as it is) when it is what is stored."""
    return None if wanted == stored else wanted


class AccountEditDialog(QDialog):
    """Changes an account: name, address, signature, servers, new passwords and what happens to mail on the server.
    The stored mail is not touched. A password field left empty keeps the stored password."""

    def __init__(self, backend: Backend, account: AccountSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._account: AccountSummary = account
        self.dav_changed: bool = False  # the calendar and contacts server was changed: the caller should synchronise
        kind = kind_of(account)
        calendars_only = kind is AccountKind.DAV  # no mail: only its name and its calendar server can be changed
        self.setWindowTitle(f"Account: {account.email_address or account.shown_name}")
        form = forms.fill(self, "account_edit", {"servers": self._server_fields, "davFolders": FolderTicks})  # D-276
        self.tabs: QTabWidget = part(form, QTabWidget, "tabs")

        general = part(form, QFormLayout, "generalForm")
        self.account_name: QLineEdit = part(form, QLineEdit, "accountName")
        self.account_name.setText(account.account_name)
        self.name: QLineEdit = part(form, QLineEdit, "yourName")
        self.name.setText(account.display_name)
        self.email: QLineEdit = part(form, QLineEdit, "email")
        self.email.setText(account.email_address)
        self.signature: QPlainTextEdit = part(form, QPlainTextEdit, "signature")
        self.signature.setPlainText(account.signature)
        self.signature.setMinimumHeight(110)
        self.signature_count: QLabel = part(form, QLabel, "signatureCount")
        self.check_minutes: QSpinBox = part(form, QSpinBox, "checkMinutes")
        self.check_minutes.setRange(0, 240)
        self.check_minutes.setValue(account.check_minutes)
        self.junk_filter: QCheckBox = part(form, QCheckBox, "junkFilter")
        self.junk_filter.setChecked(account.junk_filter)
        if calendars_only:  # the mail fields keep their stored values (nothing about them is sent) and are never shown
            for field in (self.name, self.email, self.signature, self.signature_count, self.check_minutes, self.junk_filter):
                general.setRowVisible(field, False)
        storage = part(form, QComboBox, "storage")
        self.storage: QComboBox | None = None
        if kind is AccountKind.POP3:  # an IMAP account always has the folders of its server
            self.storage = storage
            self.storage.addItem("A mailbox of its own, with its own folders", "own")
            self.storage.addItem("Local Folders, together with the other accounts that keep their mail there", "local")
            self.storage.setCurrentIndex(max(0, self.storage.findData(account.storage)))
        else:
            general.setRowVisible(storage, False)

        fields = self.servers
        self.username, self.password, self.same_login = fields.username, fields.password, fields.same_login
        self.incoming_host, self.incoming_port, self.incoming_security = fields.incoming_host, fields.incoming_port, fields.incoming_security
        self.outgoing_host, self.outgoing_port, self.outgoing_security = fields.outgoing_host, fields.outgoing_port, fields.outgoing_security
        self.outgoing_username, self.outgoing_password = fields.outgoing_username, fields.outgoing_password
        self.test_button: QPushButton = part(form, QPushButton, "testConnection")
        self.trusted: list[str] = list(account.trusted_certificates)
        self.certificates_label: QLabel = part(form, QLabel, "certificates")
        self.forget_certificates_button: QPushButton = part(form, QPushButton, "forgetCertificates")
        self._show_certificates()

        self.dav_url: QLineEdit = part(form, QLineEdit, "davUrl")
        self.dav_url.setText(account.dav_url)
        self.dav_username: QLineEdit = part(form, QLineEdit, "davUsername")
        self.dav_username.setText(account.dav_username)
        self.dav_password: QLineEdit = part(form, QLineEdit, "davPassword")
        make_password_field(self.dav_password)
        self.dav_result: QLabel = part(form, QLabel, "davResult")
        self.dav_result.setTextFormat(Qt.TextFormat.RichText)
        self.dav_check_button: QPushButton = part(form, QPushButton, "findDav")
        self.dav_folders: FolderTicks = part(form, FolderTicks, "davFolders")
        self._dav_found: FoundServer | None = None  # the folders shown, as the server gave them
        self.dav_check_button.clicked.connect(self._check_dav)

        self.policy: QComboBox = part(form, QComboBox, "policy")
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.policy.setCurrentIndex(max(0, self.policy.findData(account.delete_policy)))
        self.days: QSpinBox = part(form, QSpinBox, "days")
        self.days.setRange(1, 3650)
        self.days.setValue(account.delete_days)
        self.folder_table: QTableWidget | None = None
        self._folders_loaded: dict[str, tuple[bool, str]] = {}  # server folder -> (synchronised, chosen role) as they were
        self.folders_label: QLabel = part(form, QLabel, "foldersNote")

        # the tabs this kind of account has; the others are taken out (their fields keep the stored values and send nothing)
        wanted = {
            "serversPage": not calendars_only,  # its server is the calendar server, on the Calendars and contacts tab
            "calendars": kind is not AccountKind.LOCAL,
            "folders": kind is AccountKind.IMAP,
            "copies": kind is AccountKind.POP3,  # copies on the server are a POP3 matter; an IMAP account is the server's mail
        }
        for name, keep in wanted.items():
            if not keep:
                page = part(form, QWidget, name)
                self.tabs.removeTab(self.tabs.indexOf(page))
                page.hide()
        if kind is not AccountKind.LOCAL and account.dav_url:
            self._find_dav_folders(loading=True)
        if kind is AccountKind.IMAP:
            self.folder_table = part(form, QTableWidget, "folderTable")
            self.folder_table.setColumnCount(3)
            self.folder_table.setHorizontalHeaderLabels(["Synchronise", "Folder on the server", "Used as"])
            self.folder_table.verticalHeader().hide()
            self.folder_table.horizontalHeader().setStretchLastSection(True)
            self.folder_table.setColumnWidth(0, 100)
            self.folder_table.setColumnWidth(1, 280)
            self._backend.ask(ACCOUNT_FOLDERS, FolderAccount(self._account.account_id), self._folders_arrived, self._folders_failed, owner=self)

        self.result_label: QLabel = part(form, QLabel, "result")
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        buttons = part(form, QDialogButtonBox, "buttons")
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        forms.smallest(self, 560)  # its texts are never cut off, also at the largest size
        self.signature.textChanged.connect(self._count_signature)
        self.test_button.clicked.connect(self._test)
        self.forget_certificates_button.clicked.connect(self._forget_certificates)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        self._policy_changed()
        self._count_signature()

    def _server_fields(self, parent: QWidget) -> QWidget:
        """The incoming and outgoing server boxes (shared with Add account), made at their place in the form."""
        account = self._account
        self.servers = ServerFields(parent, kind_of(account), editing=True, start=start_values_of(account))
        return self.servers.widget

    def _check_dav(self) -> None:
        self._find_dav_folders(loading=False, fresh=True)  # the person's check: the certificates with the usual checks only (D-299)

    def _find_dav_folders(self, loading: bool, fresh: bool = False) -> None:
        """Ask the server which folders it has and show them with their ticks (kept in step now; all for a new address). Loading: when the editor
        opens, for the stored server. Nothing is saved before Save (D-187)."""
        self.dav_check_button.setEnabled(False)
        self.dav_result.setText("Loading the folders from the server…" if loading else "Looking…")
        account_id = self._account.account_id
        username, password = self.dav_username.text().strip(), self.dav_password.text() or None

        def again() -> None:
            self._find_dav_folders(loading)

        def found(result: FoundServer) -> None:
            self.dav_check_button.setEnabled(True)
            self._ask_about(result.certificate, again, fresh)
            if not result.folders:
                problem = result.problem or "No calendars, contacts or tasks were found on the incoming mail server or the domain of the address"
                self.dav_result.setText(f"<span style='{red_style()}'>{problem.replace('<', '&lt;')}.</span>")
                return
            self._dav_found = result
            self.dav_folders.show_folders(result.folders)
            self.dav_url.setText(result.url)
            self.dav_result.setText("" if loading else f"<span style='color:{green()}'><b>✓</b> {counted(len(result.folders), 'folder')} found. The ticks take effect with Save.</span>")

        def failed(error: ChronoMailError) -> None:
            self.dav_check_button.setEnabled(True)
            self.dav_result.setText(f"<span style='{red_style()}'>{str(error).replace('<', '&lt;')}</span>")
            if isinstance(error, CertificateError):
                self._ask_about(error.trouble, again, fresh)

        self._backend.ask(DAV_FIND, FindParameters(account_id, self.dav_url.text().strip(), username, password, fresh), found, failed, owner=self)

    def _dav_link(self) -> LinkParameters | None:
        """What Save sends when the folders shown belong to the address in the field and the ticks or the server details changed."""
        found = self._dav_found
        url = self.dav_url.text().strip()
        if found is None or found.url != url:
            return None
        account = self._account
        username = self.dav_username.text().strip()
        same_server = url == account.dav_url and username == account.dav_username and not self.dav_password.text()
        if same_server and not self.dav_folders.changed():
            return None
        return LinkParameters(account.account_id, url, self.dav_folders.left_out(), found.pin, username, self.dav_password.text() or None)

    def _dav_values(self) -> SetDavParameters | None:
        """The values for account.set-dav when something about the server changed."""
        account = self._account
        url, username = self.dav_url.text().strip(), self.dav_username.text().strip()
        if url == account.dav_url and username == account.dav_username and not self.dav_password.text():
            return None
        return SetDavParameters(account.account_id, url, username, self.dav_password.text() or None)

    # --- layout -------------------------------------------------------------------------------------------

    def _count_signature(self) -> None:
        count = len(self.signature.toPlainText())
        self.signature_count.setText(f"{count} of {MAXIMUM_SIGNATURE_CHARACTERS} characters")
        self.signature_count.setStyleSheet(red_style() if count > MAXIMUM_SIGNATURE_CHARACTERS else "")
        self.save_button.setEnabled(count <= MAXIMUM_SIGNATURE_CHARACTERS)

    def _policy_changed(self) -> None:
        self.days.setEnabled(str(self.policy.currentData()) == "delete_after_days")

    # --- commands -------------------------------------------------------------------------------------------

    def _values(self) -> AccountEditParameters | None:
        """Only what differs from the stored account (and the passwords that were typed); None: nothing changed."""
        account = self._account
        parameters = AccountEditParameters(
            account.account_id,
            display_name=differing(self.name.text().strip(), account.display_name),
            account_name=differing(self.account_name.text().strip(), account.account_name),
            email_address=differing(self.email.text().strip(), account.email_address),
            signature=differing(self.signature.toPlainText(), account.signature),
            username=differing(self.username.text().strip(), account.incoming_username),
            password=self.password.text() or None,
            incoming_host=differing(self.incoming_host.text().strip(), account.incoming_host),
            incoming_port=differing(self.incoming_port.value(), account.incoming_port),
            incoming_security=differing(security_of(self.incoming_security.currentData()), account.incoming_security),
            outgoing_username=differing(self.outgoing_username.text().strip(), account.outgoing_username),
            outgoing_password=self.outgoing_password.text() or None,
            outgoing_host=differing(self.outgoing_host.text().strip(), account.outgoing_host),
            outgoing_port=differing(self.outgoing_port.value(), account.outgoing_port),
            outgoing_security=differing(security_of(self.outgoing_security.currentData()), account.outgoing_security),
            delete_policy=differing(policy_of(self.policy.currentData()), account.delete_policy),
            delete_days=differing(self.days.value(), account.delete_days),
            check_minutes=differing(self.check_minutes.value(), account.check_minutes),
            outgoing_same_login=differing(self.same_login.isChecked(), account.outgoing_same_login),
            junk_filter=differing(self.junk_filter.isChecked(), account.junk_filter),
        )
        return None if parameters == AccountEditParameters(account.account_id) else parameters

    def _folders_failed(self, error: ChronoMailError) -> None:
        self.folders_label.setText(f"<span style='{red_style()}'>The folders could not be listed: {html.escape(str(error))}</span>")

    def _folders_arrived(self, result: tuple[FolderChoice, ...]) -> None:
        table = self.folder_table
        if table is None:
            return
        choices = list(result)
        self.folders_label.setText("Folders that are not synchronised are not shown here; mail already stored stays. A folder can be told to be the Sent, Drafts, Trash, Junk or Archive folder.")
        table.setRowCount(len(choices))
        for row, choice in enumerate(choices):
            name = choice.server_name
            shown = QTableWidgetItem()
            shown.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            shown.setCheckState(Qt.CheckState.Checked if choice.shown else Qt.CheckState.Unchecked)
            if name.upper() == "INBOX":
                shown.setFlags(Qt.ItemFlag.ItemIsEnabled)  # the Inbox is always synchronised
            table.setItem(row, 0, shown)
            label = QTableWidgetItem(name)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 1, label)
            roles = QComboBox()
            automatic = choice.automatic_role
            roles.addItem(f"Automatic ({automatic})" if automatic != "other" else "Automatic (an ordinary folder)", "")
            for value, text in (("sent", "Sent"), ("drafts", "Drafts"), ("trash", "Trash"), ("junk", "Junk"), ("archive", "Archive")):
                roles.addItem(text, value)
            roles.setCurrentIndex(max(0, roles.findData(choice.chosen_role)))
            roles.setEnabled(name.upper() != "INBOX")
            table.setCellWidget(row, 2, roles)
            self._folders_loaded[name] = (choice.shown, choice.chosen_role)

    def _folder_changes(self) -> SetFolders | None:
        """What the person changed in the table: folders to show, to hide, and roles (None: nothing)."""
        table = self.folder_table
        if table is None or not self._folders_loaded:
            return None
        show: list[str] = []
        hide: list[str] = []
        roles: list[str] = []
        for row in range(table.rowCount()):
            name_item, shown_item, chooser = table.item(row, 1), table.item(row, 0), table.cellWidget(row, 2)
            if name_item is None or shown_item is None:
                continue
            name = name_item.text()
            wanted = shown_item.checkState() == Qt.CheckState.Checked
            role = str(chooser.currentData() or "") if isinstance(chooser, QComboBox) else ""
            was_shown, was_role = self._folders_loaded.get(name, (wanted, role))
            if wanted != was_shown:
                (show if wanted else hide).append(name)
            if role != was_role:
                roles.append(f"{name}={role}")
        if not (show or hide or roles):
            return None
        return SetFolders(self._account.account_id, tuple(show), tuple(hide), tuple(roles))

    def _show_certificates(self) -> None:
        count = len(self.trusted)
        self.certificates_label.setText(
            f"{counted(count, 'server certificate')} accepted for this account. Each is trusted only for the server it was accepted for, and only exactly that certificate; when that server shows another one, Chrono Mail asks again."
            if count else "No server certificate was accepted for this account: the usual checks apply."
        )
        self.forget_certificates_button.setEnabled(count > 0)

    def _ask_about(self, trouble: CertificateTrouble | None, again: Callable[[], None], fresh: bool = False) -> None:
        """A server whose certificate could not be verified: the Verify certificate window asks at once; Connect does the work again (D-299)."""
        if trouble is not None:
            questions_for(self._backend).ask(trouble, again, self, self._keep, kept=lambda: self.trusted, fresh=fresh)

    def _keep(self, pin: str, keep: bool) -> None:
        """Don't ask me again: the certificate is kept with this account for good (or, Connect without it, taken back), and the list of accepted
        certificates here follows."""
        server, _, fingerprint = pin.rpartition("=")
        host, _, port = server.rpartition(":")

        def done(result: AccountSummary) -> None:
            self.trusted = list(result.trusted_certificates)
            self._show_certificates()

        command = ACCOUNT_TRUST_CERTIFICATE if keep else ACCOUNT_FORGET_CERTIFICATE
        self._backend.ask(command, TrustParameters(self._account.account_id, host, int(port), fingerprint), done, self._certificate_failed, owner=self)

    def _certificate_failed(self, error: ChronoMailError) -> None:
        self.result_label.setText(f"<span style='{red_style()}'>{html.escape(str(error))}</span>")

    def _forget_certificates(self) -> None:
        if QMessageBox.question(self, "Accepted certificates", "Forget every accepted certificate of this account? Servers with such certificates can no longer be reached until you accept them again.") != QMessageBox.StandardButton.Yes:
            return

        def done(result: AccountSummary) -> None:
            self.trusted = []
            self._show_certificates()
            self.result_label.setText("The accepted certificates were forgotten.")

        self._backend.ask(ACCOUNT_FORGET_CERTIFICATES, AccountParameters(self._account.account_id), done, self._certificate_failed, owner=self)

    def _test(self) -> None:
        """Test connection: it always checks the certificates itself, whatever was answered before (D-299)."""
        self._check(fresh=True)

    def _retest(self) -> None:
        """The test after Connect: with what was answered."""
        self._check(fresh=False)

    def _check(self, fresh: bool) -> None:
        """The servers as they are in the boxes now; a password not typed is the stored one, taken by the engine (never asked for again)."""
        if not self.test_button.isEnabled():
            return  # a test runs (a Connect ends in another one once it is answered)
        self.test_button.setEnabled(False)
        self.result_label.setText("Testing…")
        parameters = dataclasses.replace(self.servers.check_parameters(self._account.account_id, tuple(self.trusted)), fresh=fresh)

        def done(result: AccountCheckResult) -> None:
            self.test_button.setEnabled(True)
            self.result_label.setText(test_result_text(result))
            self._ask_about(result.incoming_certificate, self._retest, fresh)
            self._ask_about(result.outgoing_certificate, self._retest, fresh)

        def failed(error: ChronoMailError) -> None:
            self.test_button.setEnabled(True)
            self.result_label.setText(f"<span style='{red_style()}'>{html.escape(str(error))}</span>")

        self._backend.ask(ACCOUNT_CHECK, parameters, done, failed, owner=self)

    def _storage_change(self) -> Literal["own", "local"] | None:
        """The new place for the mail when the choice was changed."""
        if self.storage is None:
            return None
        chosen: Literal["own", "local"] = "local" if str(self.storage.currentData()) == "local" else "own"
        return chosen if chosen != self._account.storage else None

    def _save(self) -> None:
        values = self._values()
        folders = self._folder_changes()
        storage = self._storage_change()
        link = self._dav_link()
        dav = None if link is not None else self._dav_values()
        if values is None and folders is None and storage is None and dav is None and link is None:
            self.accept()
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")
        account_id = self._account.account_id

        def linked(_result: AccountSummary) -> None:
            self.dav_changed = True
            self.accept()

        def finish_dav() -> None:
            if link is not None:
                self._backend.ask(DAV_LINK, link, linked, failed, owner=self)  # a folder that is unticked goes from here; refused while it has unsent changes
                return
            if dav is None:
                self.accept()
                return
            self.dav_changed = True
            self._backend.ask(ACCOUNT_SET_DAV, dav, lambda _result: self.accept(), failed, owner=self)

        def finish() -> None:
            if storage is None:
                finish_dav()
                return
            self.result_label.setText("Moving the mail. This can take a while…")
            self._backend.ask(ACCOUNT_SET_STORAGE, StorageParameters(account_id, storage), lambda _result: finish_dav(), failed, owner=self)

        def done() -> None:
            if folders is not None:
                self._backend.ask(ACCOUNT_SET_FOLDERS, folders, lambda _result: finish(), failed, owner=self)
            else:
                finish()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='{red_style()}'>The account could not be changed: {html.escape(str(error))}</span>")

        if values is not None:
            self._backend.ask(ACCOUNT_EDIT, values, lambda _result: done(), failed, owner=self)
        else:
            done()
