import urllib.parse
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
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import AccountKind, ChronoMailError
from chrono_mail.engine.commands_accounts import (
    ACCOUNT_CHECK, ACCOUNT_EDIT, ACCOUNT_FORGET_CERTIFICATES, ACCOUNT_TRUST_CERTIFICATE, AccountCheckResult, AccountEditParameters, AccountParameters, AccountSummary,
    TrustParameters,
)
from chrono_mail.engine.commands_dav import ACCOUNT_SET_DAV, SetDavParameters
from chrono_mail.engine.commands_imap import ACCOUNT_FOLDERS, ACCOUNT_SET_FOLDERS, FolderAccount, FolderChoice, SetFolders
from chrono_mail.engine.commands_local import ACCOUNT_SET_STORAGE, StorageParameters
from chrono_mail.engine.commands_dav import DAV_FIND, DAV_LINK, FindParameters, FoundServer, LinkParameters
from chrono_mail.ui.accountdialog import POLICIES, add_name_rows
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import inspect_and_ask
from chrono_mail.ui.colours import green, red_style
from chrono_mail.ui.foundfolders import FolderTicks
from chrono_mail.ui.serverfields import ServerFields, kind_of, mail_server_kind, policy_of, security_of, start_values_of, test_result_text
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "accountedit.py 2.4.334"

# --- Version history ----------------------------------------------------
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
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        general = QWidget(self)
        general_form = QFormLayout(general)
        self.account_name = QLineEdit(account.account_name, general)
        self.name = QLineEdit(account.display_name, general)
        self.email = QLineEdit(account.email_address, general)
        self.signature = QPlainTextEdit(general)
        self.signature.setPlainText(account.signature)
        self.signature.setPlaceholderText("Added at the end of new messages, replies and forwards")
        self.signature.setMinimumHeight(110)
        self.signature_count = QLabel("", general)
        mail_form = general_form
        if calendars_only:
            general_form.addRow("Account name", self.account_name)
            unused = QWidget(general)  # the mail fields keep their stored values (nothing about them is sent) and are never shown
            unused.hide()
            mail_form = QFormLayout(unused)
            mail_form.addRow("Your name", self.name)
            mail_form.addRow("E-mail address", self.email)
        else:
            add_name_rows(general_form, self.account_name, self.name, self.email)
        mail_form.addRow("Signature", self.signature)
        mail_form.addRow("", self.signature_count)
        self.check_minutes = QSpinBox(general)
        self.check_minutes.setRange(0, 240)
        self.check_minutes.setSpecialValueText("As set in the general settings")
        self.check_minutes.setSuffix(" minutes")
        self.check_minutes.setValue(account.check_minutes)
        mail_form.addRow("Check this account every", self.check_minutes)
        self.junk_filter = QCheckBox("Filter junk mail", general)
        self.junk_filter.setChecked(account.junk_filter)
        self.junk_filter.setToolTip("New mail that the server marked as spam, or that looks very much like junk, goes to the Junk folder before your rules run")
        mail_form.addRow("", self.junk_filter)
        self.storage: QComboBox | None = None
        if kind is AccountKind.POP3:  # an IMAP account always has the folders of its server
            self.storage = QComboBox(general)
            self.storage.addItem("A mailbox of its own, with its own folders", "own")
            self.storage.addItem("Local Folders, together with the other accounts that keep their mail there", "local")
            self.storage.setCurrentIndex(max(0, self.storage.findData(account.storage)))
            self.storage.setToolTip("Changing this moves the stored mail of this account. Nothing is deleted before the copy has been checked.")
            general_form.addRow("Keep the mail in", self.storage)
        self.tabs.addTab(general, "General")

        servers = QWidget(self)
        servers_layout = QVBoxLayout(servers)
        self.servers = ServerFields(servers, kind, editing=True, start=start_values_of(account))
        fields = self.servers
        self.username, self.password, self.same_login = fields.username, fields.password, fields.same_login
        self.incoming_host, self.incoming_port, self.incoming_security = fields.incoming_host, fields.incoming_port, fields.incoming_security
        self.outgoing_host, self.outgoing_port, self.outgoing_security = fields.outgoing_host, fields.outgoing_port, fields.outgoing_security
        self.outgoing_username, self.outgoing_password = fields.outgoing_username, fields.outgoing_password
        servers_layout.addWidget(fields.incoming_group)
        servers_layout.addWidget(fields.outgoing_group)
        self.test_button = QPushButton("Test connection", servers)
        servers_layout.addWidget(self.test_button)
        self.trusted: list[str] = list(account.trusted_certificates)
        self.certificates_label = QLabel("", servers)
        self.certificates_label.setWordWrap(True)
        self.incoming_certificate_button = QPushButton("Look at the incoming server's certificate…", servers)
        self.outgoing_certificate_button = QPushButton("Look at the outgoing server's certificate…", servers)
        self.forget_certificates_button = QPushButton("Forget the accepted certificates", servers)
        for widget in (self.certificates_label, self.incoming_certificate_button, self.outgoing_certificate_button, self.forget_certificates_button):
            servers_layout.addWidget(widget)
        self._show_certificates()
        servers_layout.addStretch(1)
        if calendars_only:
            servers.hide()  # its server is the calendar server, on the Calendars and contacts tab
        else:
            self.tabs.addTab(servers, "Servers")

        self.dav_url = QLineEdit(account.dav_url, self)
        self.dav_username = QLineEdit(account.dav_username, self)
        self.dav_password = QLineEdit(self)
        self.dav_result = QLabel("", self)
        self.dav_check_button = QPushButton("Find calendars, contacts and tasks", self)
        self.dav_folders = FolderTicks(self)
        self._dav_found: FoundServer | None = None  # the folders shown, as the server gave them
        self.dav_certificate_button = QPushButton("Look at the server's certificate…", self)
        if kind is not AccountKind.LOCAL:
            self.tabs.addTab(self._calendars_tab(), "Calendars and contacts")
            if account.dav_url:
                self._find_dav_folders(loading=True)

        copies = QWidget(self)
        copies_form = QFormLayout(copies)
        self.policy = QComboBox(copies)
        for value, label in POLICIES:
            self.policy.addItem(label, value)
        self.policy.setCurrentIndex(max(0, self.policy.findData(account.delete_policy)))
        self.days = QSpinBox(copies)
        self.days.setRange(1, 3650)
        self.days.setValue(account.delete_days)
        self.days.setSuffix(" days")
        copies_form.addRow("Mail on the server", self.policy)
        copies_form.addRow("Delete after", self.days)
        self.folder_table: QTableWidget | None = None
        self._folders_loaded: dict[str, tuple[bool, str]] = {}  # server folder -> (synchronised, chosen role) as they were
        if kind is AccountKind.IMAP:
            self.tabs.addTab(self._folders_tab(), "Folders")
        if kind is AccountKind.POP3:  # copies on the server are a POP3 matter; an IMAP account is the server's mail
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
        self.incoming_certificate_button.clicked.connect(lambda: self._look_at("incoming"))
        self.outgoing_certificate_button.clicked.connect(lambda: self._look_at("outgoing"))
        self.forget_certificates_button.clicked.connect(self._forget_certificates)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        self._policy_changed()
        self._count_signature()

    def _calendars_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        explanation = QLabel(
            "Calendars, task lists and address books of this account are kept in step with a CalDAV and CardDAV server. "
            "Leave the address empty if this account has none.", tab,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.dav_url.setPlaceholderText("https://server.example.com/caldav/")
        self.dav_username.setPlaceholderText("(the same as for incoming mail)")
        self.dav_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.dav_password.setPlaceholderText("(unchanged; or the mail password)")
        form.addRow("Server address", self.dav_url)
        form.addRow("User name", self.dav_username)
        form.addRow("New password", self.dav_password)
        form.addRow("Folders", self.dav_folders)
        layout.addLayout(form)
        self.dav_result.setWordWrap(True)
        self.dav_result.setTextFormat(Qt.TextFormat.RichText)
        for widget in (self.dav_check_button, self.dav_certificate_button, self.dav_result):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.dav_check_button.clicked.connect(self._check_dav)
        self.dav_certificate_button.clicked.connect(self._look_at_dav)
        return tab

    def _dav_address(self) -> tuple[str, int] | None:
        parts = urllib.parse.urlsplit(self.dav_url.text().strip())
        if parts.scheme != "https" or not parts.hostname:
            return None
        return parts.hostname, parts.port or 443

    def _look_at_dav(self) -> None:
        address = self._dav_address()
        if address is None:
            self.dav_result.setText("Type an address that starts with https:// first.")
            return

        def accepted(fingerprint: str) -> None:
            def done(result: AccountSummary) -> None:
                self.trusted = list(result.trusted_certificates)
                self._show_certificates()
                self.dav_result.setText("The certificate was accepted.")

            def failed(error: ChronoMailError) -> None:
                self.dav_result.setText(f"<span style='{red_style()}'>{error}</span>")

            self._backend.ask(ACCOUNT_TRUST_CERTIFICATE, TrustParameters(self._account.account_id, address[0], address[1], fingerprint), done, failed, owner=self)

        inspect_and_ask(self._backend, self, address[0], address[1], "tls", "https", accepted)

    def _check_dav(self) -> None:
        self._find_dav_folders(loading=False)

    def _find_dav_folders(self, loading: bool) -> None:
        """Ask the server which folders it has and show them with their ticks (kept in step now; all for a new address). Loading: when the editor
        opens, for the stored server. Nothing is saved before Save (D-187)."""
        self.dav_check_button.setEnabled(False)
        self.dav_result.setText("Loading the folders from the server…" if loading else "Looking…")
        account_id = self._account.account_id
        username, password = self.dav_username.text().strip(), self.dav_password.text() or None

        def found(result: FoundServer) -> None:
            self.dav_check_button.setEnabled(True)
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

        self._backend.ask(DAV_FIND, FindParameters(account_id, self.dav_url.text().strip(), username, password), found, failed, owner=self)

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

    def _folders_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.folders_label = QLabel("Loading the folders from the server…", tab)
        self.folders_label.setWordWrap(True)
        layout.addWidget(self.folders_label)
        self.folder_table = QTableWidget(0, 3, tab)
        self.folder_table.setHorizontalHeaderLabels(["Synchronise", "Folder on the server", "Used as"])
        self.folder_table.verticalHeader().hide()
        self.folder_table.horizontalHeader().setStretchLastSection(True)
        self.folder_table.setColumnWidth(0, 100)
        self.folder_table.setColumnWidth(1, 280)
        layout.addWidget(self.folder_table, 1)
        self._backend.ask(ACCOUNT_FOLDERS, FolderAccount(self._account.account_id), self._folders_arrived, self._folders_failed, owner=self)
        return tab

    def _folders_failed(self, error: ChronoMailError) -> None:
        self.folders_label.setText(f"<span style='{red_style()}'>The folders could not be listed: {error}</span>")

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
            f"{counted(count, 'server certificate')} accepted for this account. Each is trusted only for the server it was accepted for, and only exactly that certificate; a different one from that server is refused until you look at it again."
            if count else "No server certificate was accepted for this account: the usual checks apply."
        )
        self.forget_certificates_button.setEnabled(count > 0)

    def _look_at(self, which: str) -> None:
        host, port, security = self.servers.endpoint(which)
        kind = mail_server_kind(self.servers.kind.value) if which == "incoming" else "smtp"

        def accepted(fingerprint: str) -> None:
            def done(result: AccountSummary) -> None:
                self.trusted = list(result.trusted_certificates)
                self._show_certificates()
                self.result_label.setText("The certificate was accepted.")

            self._backend.ask(ACCOUNT_TRUST_CERTIFICATE, TrustParameters(self._account.account_id, host, port, fingerprint), done, self._certificate_failed, owner=self)

        inspect_and_ask(self._backend, self, host, port, security_of(security), kind, accepted)

    def _certificate_failed(self, error: ChronoMailError) -> None:
        self.result_label.setText(f"<span style='{red_style()}'>{error}</span>")

    def _forget_certificates(self) -> None:
        if QMessageBox.question(self, "Accepted certificates", "Forget every accepted certificate of this account? Servers with such certificates can no longer be reached until you accept them again.") != QMessageBox.StandardButton.Yes:
            return

        def done(result: AccountSummary) -> None:
            self.trusted = []
            self._show_certificates()
            self.result_label.setText("The accepted certificates were forgotten.")

        self._backend.ask(ACCOUNT_FORGET_CERTIFICATES, AccountParameters(self._account.account_id), done, self._certificate_failed, owner=self)

    def _test(self) -> None:
        """The servers as they are in the boxes now; a password not typed is the stored one, taken by the engine (never asked for again)."""
        self.test_button.setEnabled(False)
        self.result_label.setText("Testing…")
        parameters = self.servers.check_parameters(self._account.account_id, tuple(self.trusted))

        def done(result: AccountCheckResult) -> None:
            self.test_button.setEnabled(True)
            self.result_label.setText(test_result_text(result))

        def failed(error: ChronoMailError) -> None:
            self.test_button.setEnabled(True)
            self.result_label.setText(f"<span style='{red_style()}'>{error}</span>")

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
            self.result_label.setText(f"<span style='{red_style()}'>The account could not be changed: {error}</span>")

        if values is not None:
            self._backend.ask(ACCOUNT_EDIT, values, lambda _result: done(), failed, owner=self)
        else:
            done()
