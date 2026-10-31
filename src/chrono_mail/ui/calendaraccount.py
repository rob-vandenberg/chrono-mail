import urllib.parse

from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_TRUST_CERTIFICATE, AccountAddResult, AccountSummary, TrustParameters
from chrono_mail.engine.commands_dav import (
    ACCOUNT_ADD_DAV, DAV_FIND, DAV_FIND_NEW, DAV_LINK, AddDavParameters, FindNewParameters, FindParameters, FoundServer, LinkParameters, web_address,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import inspect_and_ask
from chrono_mail.ui.colours import green, red_style
from chrono_mail.ui.forms import part
from chrono_mail.ui.foundfolders import FoundFoldersDialog
from chrono_mail.ui.passwordfield import make_password_field
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "calendaraccount.py 4.2.437"

# --- Version history ----------------------------------------------------
# v4.2.437: the password field has the eye (UR-03, D-296)
# v4.0.376: built from its Qt Designer form (ui/forms/calendar_account.ui, D-276); its smallest width follows the size factor
# v3.6.349: while the account is being added Find, the certificate and the fields are greyed too, and a Find never enables a second
#           Add (RV-209); Find does nothing during an add, and Add after a failed add only with what Find found
# v3.5.346: While the account is being added or linked the window cannot close: Cancel greyed, Escape and the close button wait (RV-02)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.274: R-19 round 4: the mail accounts offered are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.271: R-19: accepting a certificate is a typed call
# v1.1.213: No help line under Account name
# v1.1.212: Initial window: Add account… in the Calendar, Contacts and Tasks sections, for a mail account or calendars and contacts only (D-185)

NEW_ACCOUNT: str = ""  # the For choice of a new account of calendars, contacts and tasks only
NEW_ACCOUNT_TEXT: str = "A new account (calendars, contacts and tasks only)"


class CalendarAccountDialog(QDialog):
    """Add account: a calendar and contacts server typed by hand, for a mail account that has none or as an account of its own. Find asks the
    server; Add shows the folders found with a tick each, and their OK adds the account (or links the mail account)."""

    def __init__(self, backend: Backend, mail_accounts: list[AccountSummary], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.key_created: bool = False
        self.show_recovery_code: bool = False  # this computer's recovery code was never confirmed as stored: shown after the add (RV-03)
        self._adding: bool = False  # Add is running: the window stays until the engine answered (RV-02)
        self._found: FoundServer | None = None
        self._fingerprint: str = ""  # a certificate the person accepted in this window, for a new account
        form = forms.fill(self, "calendar_account")  # D-276
        self.target: QComboBox = part(form, QComboBox, "target")
        self.target.addItem(NEW_ACCOUNT_TEXT, NEW_ACCOUNT)
        for account in mail_accounts:
            if account.kind != "local":
                self.target.addItem((account.shown_name or account.email_address), account.account_id)
        self.account_name: QLineEdit = part(form, QLineEdit, "accountName")
        self.url: QLineEdit = part(form, QLineEdit, "url")
        self.username: QLineEdit = part(form, QLineEdit, "username")
        self.password: QLineEdit = part(form, QLineEdit, "password")
        make_password_field(self.password)
        self.find_button: QPushButton = part(form, QPushButton, "find")
        self.certificate_button: QPushButton = part(form, QPushButton, "lookAtCertificate")
        self.result_label: QLabel = part(form, QLabel, "result")
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        box = part(form, QDialogButtonBox, "buttons")
        self.add_button = box.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button: QPushButton | None = box.addButton(QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._add)
        box.rejected.connect(self.reject)
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size
        self.find_button.clicked.connect(self._find)
        self.certificate_button.clicked.connect(self._look_at_certificate)
        self.target.currentIndexChanged.connect(self._target_changed)
        for field in (self.url, self.username, self.password, self.account_name):
            field.textChanged.connect(self._forget_found)
        self._target_changed()

    def _new(self) -> bool:
        return str(self.target.currentData()) == NEW_ACCOUNT

    def _target_changed(self) -> None:
        """A mail account has its own name and, by default, its mail login: Account name is greyed and the login may stay empty."""
        new = self._new()
        self.account_name.setEnabled(new)
        self.username.setPlaceholderText("" if new else "(the same as for mail)")
        self.password.setPlaceholderText("" if new else "(the same as for mail)")
        self._forget_found()

    def _forget_found(self) -> None:
        self._found = None
        self.add_button.setEnabled(False)

    def _show_problem(self, text: str) -> None:
        self.result_label.setText(f"<span style='{red_style()}'>{text.replace('<', '&lt;')}</span>")

    def _find(self) -> None:
        if self._adding:  # reached from an accepted certificate while the account is being added: never then
            return
        url = self.url.text().strip()
        username, password = self.username.text().strip(), self.password.text()
        if self._new() and (not url or not username or not password):
            self._show_problem("Type the server address, the user name and the password.")
            return
        self._forget_found()
        self.find_button.setEnabled(False)
        self.result_label.setText("Looking…")

        def found(result: FoundServer) -> None:
            self.find_button.setEnabled(not self._adding)
            if not result.folders:
                self._show_problem(result.problem or "No calendars, contacts or tasks were found")
                return
            self._found = result
            self.add_button.setEnabled(not self._adding)  # never a second Add while one runs (RV-209)
            self.result_label.setText(f"<span style='color:{green()}'><b>✓</b> {counted(len(result.folders), 'folder')} found on {result.url.replace('<', '&lt;')}.</span>")

        def failed(error: ChronoMailError) -> None:
            self.find_button.setEnabled(not self._adding)
            self._show_problem(str(error))

        if self._new():
            self._backend.ask(DAV_FIND_NEW, FindNewParameters(url, username, password, self._fingerprint), found, failed, owner=self)
        else:
            self._backend.ask(DAV_FIND, FindParameters(str(self.target.currentData()), url, username, password or None), found, failed, owner=self)

    def _look_at_certificate(self) -> None:
        parts = urllib.parse.urlsplit(web_address(self.url.text()))
        if parts.scheme != "https" or not parts.hostname:
            self._show_problem("Type an address that starts with https:// (or a host name) first.")
            return
        host, port = parts.hostname, parts.port or 443

        def accepted(fingerprint: str) -> None:
            if self._new():
                self._fingerprint = fingerprint
                self._find()
                return

            def trusted(_result: AccountSummary) -> None:
                self._find()

            values = TrustParameters(str(self.target.currentData()), host, port, fingerprint)
            self._backend.ask(ACCOUNT_TRUST_CERTIFICATE, values, trusted, lambda error: self._show_problem(str(error)), owner=self)

        inspect_and_ask(self._backend, self, host, port, "tls", "https", accepted)

    def _add(self) -> None:
        found = self._found
        if found is None:
            return
        dialog = FoundFoldersDialog(found, self)
        if dialog.exec() != FoundFoldersDialog.DialogCode.Accepted:
            return
        self._set_adding(True)
        self.result_label.setText("Adding…")
        username, password = self.username.text().strip(), self.password.text()

        def failed(error: ChronoMailError) -> None:
            self._set_adding(False)
            self._show_problem(str(error))

        def added(result: AccountAddResult) -> None:
            self._adding = False
            self.key_created = result.key_created
            self.show_recovery_code = result.show_recovery_code
            self.accept()

        def linked(_result: AccountSummary) -> None:
            self._adding = False
            self.accept()

        if self._new():
            parameters = AddDavParameters(found.url, username, password, self.account_name.text().strip(), dialog.left_out(), self._fingerprint)
            self._backend.ask(ACCOUNT_ADD_DAV, parameters, added, failed, owner=self)
        else:
            link = LinkParameters(str(self.target.currentData()), found.url, dialog.left_out(), found.pin, username, password or None)
            self._backend.ask(DAV_LINK, link, linked, failed, owner=self)

    def _set_adding(self, adding: bool) -> None:
        """While the account is being added nothing else can be done here: no Cancel, no Find, no certificate, no field changed (RV-209)."""
        self._adding = adding
        widgets: list[QWidget | None] = [
            self.add_button, self.cancel_button, self.find_button, self.certificate_button, self.target, self.account_name, self.url, self.username, self.password,
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not adding)
        if not adding:
            self.account_name.setEnabled(self._new())  # a mail account has its own name
            self.add_button.setEnabled(self._found is not None)  # Add needs what Find found

    @override
    def reject(self) -> None:
        """Cancel, Escape or the close button: not while the account is being added (it is then added, and its recovery code is shown)."""
        if not self._adding:
            super().reject()
