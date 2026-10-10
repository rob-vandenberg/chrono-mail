from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
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

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.accountdialog import POLICIES, SECURITY_CHOICES
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import inspect_and_ask

# --- Version ------------------------------------------------------------
__version__ = "accountedit.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: check interval and the folders of an IMAP account
# v0.0.43: accepted certificates
# v0.0.31: IMAP accounts have no server copies tab
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
        self.check_minutes = QSpinBox(general)
        self.check_minutes.setRange(0, 240)
        self.check_minutes.setSpecialValueText("As set in the general settings")
        self.check_minutes.setSuffix(" minutes")
        self.check_minutes.setValue(int(getattr(account, "check_minutes", 0)))
        general_form.addRow("Check this account every", self.check_minutes)
        self.tabs.addTab(general, "General")

        servers = QWidget(self)
        servers_layout = QVBoxLayout(servers)
        self.username = QLineEdit(str(getattr(account, "incoming_username")), servers)
        self.password = QLineEdit(servers)
        self.incoming_host = QLineEdit(str(getattr(account, "incoming_host")), servers)
        self.incoming_port = QSpinBox(servers)
        self.incoming_security = self._security_combo(str(getattr(account, "incoming_security")))
        servers_layout.addWidget(self._group("Incoming mail (IMAP)" if str(getattr(account, "kind", "pop3")) == "imap" else "Incoming mail (POP3)", self.incoming_host, self.incoming_port, int(getattr(account, "incoming_port")), self.incoming_security, self.username, self.password))
        self.outgoing_username = QLineEdit(str(getattr(account, "outgoing_username")), servers)
        self.outgoing_password = QLineEdit(servers)
        self.outgoing_host = QLineEdit(str(getattr(account, "outgoing_host")), servers)
        self.outgoing_port = QSpinBox(servers)
        self.outgoing_security = self._security_combo(str(getattr(account, "outgoing_security")))
        servers_layout.addWidget(self._group("Outgoing mail (SMTP)", self.outgoing_host, self.outgoing_port, int(getattr(account, "outgoing_port")), self.outgoing_security, self.outgoing_username, self.outgoing_password))
        self.test_button = QPushButton("Test connection", servers)
        servers_layout.addWidget(self.test_button)
        self.trusted: list[str] = list(getattr(account, "trusted_certificates", ()))
        self.certificates_label = QLabel("", servers)
        self.certificates_label.setWordWrap(True)
        self.incoming_certificate_button = QPushButton("Look at the incoming server's certificate…", servers)
        self.outgoing_certificate_button = QPushButton("Look at the outgoing server's certificate…", servers)
        self.forget_certificates_button = QPushButton("Forget the accepted certificates", servers)
        for widget in (self.certificates_label, self.incoming_certificate_button, self.outgoing_certificate_button, self.forget_certificates_button):
            servers_layout.addWidget(widget)
        self._show_certificates()
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
        self.folder_table: QTableWidget | None = None
        self._folders_loaded: dict[str, tuple[bool, str]] = {}  # server folder -> (synchronised, chosen role) as they were
        if str(getattr(account, "kind", "pop3")) == "imap":
            self.tabs.addTab(self._folders_tab(), "Folders")
        if str(getattr(account, "kind", "pop3")) == "pop3":  # copies on the server are a POP3 matter; an IMAP account is the server's mail
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
            "check_minutes": self.check_minutes.value(),
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
            "check_minutes": getattr(account, "check_minutes", 0),
        }
        values: dict[str, object] = {name: value for name, value in wanted.items() if value != stored[name]}
        if self.password.text():
            values["password"] = self.password.text()
        if self.outgoing_password.text():
            values["outgoing_password"] = self.outgoing_password.text()
        return values

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
        self._backend.call("account.folders", {"account": str(getattr(self._account, "account_id"))}, self._folders_arrived, self._folders_failed, owner=self)
        return tab

    def _folders_failed(self, error: ChronoMailError) -> None:
        self.folders_label.setText(f"<span style='{RED}'>The folders could not be listed: {error}</span>")

    def _folders_arrived(self, result: object) -> None:
        table = self.folder_table
        if table is None:
            return
        choices: list[object] = list(result) if isinstance(result, (tuple, list)) else []
        self.folders_label.setText("Folders that are not synchronised are not shown here; mail already stored stays. A folder can be told to be the Sent, Drafts, Trash, Junk or Archive folder.")
        table.setRowCount(len(choices))
        for row, choice in enumerate(choices):
            name = str(getattr(choice, "server_name"))
            shown = QTableWidgetItem()
            shown.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            shown.setCheckState(Qt.CheckState.Checked if getattr(choice, "shown") else Qt.CheckState.Unchecked)
            if name.upper() == "INBOX":
                shown.setFlags(Qt.ItemFlag.ItemIsEnabled)  # the Inbox is always synchronised
            table.setItem(row, 0, shown)
            label = QTableWidgetItem(name)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 1, label)
            roles = QComboBox()
            automatic = str(getattr(choice, "automatic_role"))
            roles.addItem(f"Automatic ({automatic})" if automatic != "other" else "Automatic (an ordinary folder)", "")
            for value, text in (("sent", "Sent"), ("drafts", "Drafts"), ("trash", "Trash"), ("junk", "Junk"), ("archive", "Archive")):
                roles.addItem(text, value)
            roles.setCurrentIndex(max(0, roles.findData(str(getattr(choice, "chosen_role")))))
            roles.setEnabled(name.upper() != "INBOX")
            table.setCellWidget(row, 2, roles)
            self._folders_loaded[name] = (bool(getattr(choice, "shown")), str(getattr(choice, "chosen_role")))

    def _folder_changes(self) -> dict[str, object]:
        """What the person changed in the table: folders to show, to hide, and roles."""
        table = self.folder_table
        if table is None or not self._folders_loaded:
            return {}
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
        changes: dict[str, object] = {}
        if show:
            changes["show"] = show
        if hide:
            changes["hide"] = hide
        if roles:
            changes["role"] = roles
        return changes

    def _show_certificates(self) -> None:
        count = len(self.trusted)
        self.certificates_label.setText(
            f"{count} server certificate(s) accepted for this account. Only exactly those certificates are trusted; a different one is refused until you look at it again."
            if count else "No server certificate was accepted for this account: the usual checks apply."
        )
        self.forget_certificates_button.setEnabled(count > 0)

    def _look_at(self, which: str) -> None:
        if which == "incoming":
            host, port, security = self.incoming_host.text().strip(), self.incoming_port.value(), str(self.incoming_security.currentData())
            kind = "imap" if str(getattr(self._account, "kind", "pop3")) == "imap" else "pop3"
        else:
            host, port, security, kind = self.outgoing_host.text().strip(), self.outgoing_port.value(), str(self.outgoing_security.currentData()), "smtp"

        def accepted(fingerprint: str) -> None:
            def done(result: object) -> None:
                self.trusted = list(getattr(result, "trusted_certificates"))
                self._show_certificates()
                self.result_label.setText("The certificate was accepted.")

            self._backend.call("account.trust-certificate", {"account": str(getattr(self._account, "account_id")), "fingerprint": fingerprint}, done, self._certificate_failed, owner=self)

        inspect_and_ask(self._backend, self, host, port, security, kind, accepted)

    def _certificate_failed(self, error: ChronoMailError) -> None:
        self.result_label.setText(f"<span style='{RED}'>{error}</span>")

    def _forget_certificates(self) -> None:
        if QMessageBox.question(self, "Accepted certificates", "Forget every accepted certificate of this account? Servers with such certificates can no longer be reached until you accept them again.") != QMessageBox.StandardButton.Yes:
            return

        def done(result: object) -> None:
            self.trusted = []
            self._show_certificates()
            self.result_label.setText("The accepted certificates were forgotten.")

        self._backend.call("account.forget-certificates", {"account": str(getattr(self._account, "account_id"))}, done, self._certificate_failed, owner=self)

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
            "kind": str(getattr(self._account, "kind", "pop3")),
            "trusted_certificate": list(self.trusted),
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
        folders = self._folder_changes()
        if not values and not folders:
            self.accept()
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: object) -> None:
            if folders:
                self._backend.call("account.set-folders", {"account": str(getattr(self._account, "account_id")), **folders}, lambda outcome: self.accept(), failed, owner=self)
            else:
                self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='{RED}'>The account could not be changed: {error}</span>")

        if values:
            self._backend.call("account.edit", {"account": str(getattr(self._account, "account_id")), **values}, done, failed, owner=self)
        else:
            done(None)
