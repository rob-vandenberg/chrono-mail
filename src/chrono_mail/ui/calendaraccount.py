import dataclasses
import urllib.parse
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QWidget

from chrono_mail.core import CertificateError, CertificateTrouble, ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_FORGET_CERTIFICATE, ACCOUNT_TRUST_CERTIFICATE, AccountAddResult, AccountSummary, TrustParameters
from chrono_mail.engine.commands_dav import (
    ACCOUNT_ADD_DAV, DAV_FIND, DAV_FIND_NEW, DAV_LINK, AddDavParameters, FindNewParameters, FindParameters, FoundServer, LinkParameters, web_address,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatequestions import questions_for
from chrono_mail.ui.colours import green, red_style
from chrono_mail.ui.forms import part
from chrono_mail.ui.foundfolders import FoundFoldersDialog
from chrono_mail.ui.passwordfield import make_password_field
from chrono_mail.ui.serverfields import account_line
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "calendaraccount.py 4.2.447"

# --- Version history ----------------------------------------------------
# v4.2.447: Find always checks the certificate itself; the Find after Connect uses the answer; the tick starts as kept, and Connect without it
#           takes the certificate back (D-299)
# v4.2.444: For lists each mail account as Settings > Accounts does: name, address, server and kind (account_line)
# v4.2.441: a server whose certificate could not be verified asks at once (Verify certificate) after Find; Connect finds again; the Look at
#           the server's certificate… button is gone (UR-05, D-299)
# v4.2.439: the calendar window is a part of Add account (CalendarFields, ui/forms/calendar_fields.ui): CalDAV and CardDAV are choices of its
#           Account type; CalDAV starts with the calendars and task lists ticked, CardDAV with the address books (UR-04, D-298)
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
CALDAV_KINDS: frozenset[str] = frozenset({"events", "tasks"})  # what CalDAV starts with ticked in Additional folders found
CARDDAV_KINDS: frozenset[str] = frozenset({"contacts"})  # what CardDAV starts with ticked


class CalendarFields(QWidget):
    """The CalDAV and CardDAV part of Add account: a calendar and contacts server typed by hand, for a mail account that has none or as an
    account of its own (D-185, D-298). Find asks the server; the window's Add shows the folders found with a tick each, and their OK adds the
    account (or links the mail account). It has its own result line; the window's Add asks `found` (and is told by `changed`), so an answer
    of this part never touches the mail part, shown or not."""

    def __init__(self, backend: Backend, mail_accounts: list[AccountSummary], parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._mail_accounts: list[AccountSummary] = list(mail_accounts)  # For's accounts, with the certificates kept with each
        self._adding: bool = False  # Add is running: nothing here changes until the engine answered (RV-02, RV-209)
        self._found: FoundServer | None = None
        self._finding: bool = False  # a Find runs
        self._fingerprint: str = ""  # a certificate the person accepted in this window, for a new account
        self.ticked_kinds: frozenset[str] = CALDAV_KINDS  # which folders Additional folders found starts with ticked
        self.changed: Callable[[], None] = lambda: None  # what Find found changed: the window's Add follows
        form = forms.fill(self, "calendar_fields")  # D-276
        self.target: QComboBox = part(form, QComboBox, "target")
        self.target.addItem(NEW_ACCOUNT_TEXT, NEW_ACCOUNT)
        for account in mail_accounts:
            if account.kind != "local":
                self.target.addItem(account_line(account), account.account_id)  # as Settings > Accounts lists it
        self.account_name: QLineEdit = part(form, QLineEdit, "accountName")
        self.url: QLineEdit = part(form, QLineEdit, "url")
        self.username: QLineEdit = part(form, QLineEdit, "username")
        self.password: QLineEdit = part(form, QLineEdit, "password")
        make_password_field(self.password)
        self.find_button: QPushButton = part(form, QPushButton, "find")
        self.result_label: QLabel = part(form, QLabel, "result")
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        self.find_button.clicked.connect(self._find)
        self.target.currentIndexChanged.connect(self._target_changed)
        for field in (self.url, self.username, self.password, self.account_name):
            field.textChanged.connect(self._forget_found)
        self._target_changed()

    @property
    def found(self) -> FoundServer | None:
        """What Find found as the fields are now; None until it found something (the window's Add needs it)."""
        return self._found

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
        self.changed()

    def _show_problem(self, text: str) -> None:
        self.result_label.setText(f"<span style='{red_style()}'>{text.replace('<', '&lt;')}</span>")

    def _find(self) -> None:
        """Find: it always checks the certificate itself, whatever was answered before (D-299)."""
        self._look(fresh=True)

    def _refind(self) -> None:
        """The Find after Connect: with what was answered."""
        self._look(fresh=False)

    def _look(self, fresh: bool) -> None:
        if self._adding or self._finding:  # never during an add, nor a second Find (a Connect ends in one)
            return
        url = self.url.text().strip()
        username, password = self.username.text().strip(), self.password.text()
        if self._new() and (not url or not username or not password):
            self._show_problem("Type the server address, the user name and the password.")
            return
        self._forget_found()
        self._looking(True)
        self.result_label.setText("Looking…")

        def found(result: FoundServer) -> None:
            self._looking(False)
            self._ask_about(result.certificate, fresh)
            if not result.folders:
                self._show_problem(result.problem or "No calendars, contacts or tasks were found")
                return
            self._found = result
            self.changed()  # the window's Add, never while an add runs (RV-209)
            self.result_label.setText(f"<span style='color:{green()}'><b>✓</b> {counted(len(result.folders), 'folder')} found on {result.url.replace('<', '&lt;')}.</span>")

        def failed(error: ChronoMailError) -> None:
            self._looking(False)
            self._show_problem(str(error))
            if isinstance(error, CertificateError):
                self._ask_about(error.trouble, fresh)

        if self._new():
            self._backend.ask(DAV_FIND_NEW, FindNewParameters(url, username, password, self._fingerprint, fresh), found, failed, owner=self)
        else:
            self._backend.ask(DAV_FIND, FindParameters(str(self.target.currentData()), url, username, password or None, fresh), found, failed, owner=self)

    def _looking(self, looking: bool) -> None:
        """A Find runs: no second Find until it answered."""
        self._finding = looking
        self.find_button.setEnabled(not looking and not self._adding)

    def _ask_about(self, trouble: CertificateTrouble | None, fresh: bool = False) -> None:
        """A server whose certificate could not be verified: the Verify certificate window asks at once; Connect finds again (D-299)."""
        if trouble is not None:
            questions_for(self._backend).ask(trouble, self._refind, self, self._keep, kept=self._kept, fresh=fresh)

    def _kept(self) -> list[str]:
        """What is kept for this server: with the new account when it is added, or with the mail account chosen in For (D-299)."""
        if not self._new():
            chosen = str(self.target.currentData())
            return [pin for account in self._mail_accounts if account.account_id == chosen for pin in account.trusted_certificates]
        parts = urllib.parse.urlsplit(web_address(self.url.text()))
        try:
            port = parts.port or 443
        except ValueError:
            return []
        return [f"{(parts.hostname or '').lower()}:{port}={self._fingerprint}"] if self._fingerprint else []

    def _keep(self, pin: str, keep: bool) -> None:
        """Don't ask me again: kept with the new account when it is added, or at once with the mail account chosen in For; Connect without it
        takes back one kept before."""
        server, _, fingerprint = pin.rpartition("=")
        parts = urllib.parse.urlsplit(web_address(self.url.text()))
        try:
            port = parts.port or 443
        except ValueError:  # a port that is no number: not the server the certificate came from
            return
        if f"{(parts.hostname or '').lower()}:{port}" != server:
            return  # the address was changed meanwhile: the certificate was for another server
        if self._new():
            if keep:
                self._fingerprint = fingerprint
            elif self._fingerprint == fingerprint:
                self._fingerprint = ""
            return
        host, _, port = server.rpartition(":")
        values = TrustParameters(str(self.target.currentData()), host, int(port), fingerprint)
        command = ACCOUNT_TRUST_CERTIFICATE if keep else ACCOUNT_FORGET_CERTIFICATE
        self._backend.ask(command, values, self._kept_changed, lambda error: self._show_problem(str(error)), owner=self)

    def _kept_changed(self, result: AccountSummary) -> None:
        """The mail account's kept certificates as they are now, so the tick of the next question shows them."""
        self._mail_accounts = [result if account.account_id == result.account_id else account for account in self._mail_accounts]

    def add(self, adding: Callable[[bool], None], added: Callable[[AccountAddResult | None], None]) -> None:
        """Additional folders found, CalDAV's or CardDAV's ticked first (the rest listed to tick); its OK adds the account, or gives the mail
        account its server. adding(True) when the engine starts, adding(False) when it failed; added(the result, or None for a mail account)."""
        found = self._found
        if found is None:
            return
        offered = dataclasses.replace(found, folders=tuple(dataclasses.replace(folder, ticked=folder.ticked and folder.kind in self.ticked_kinds)
                                                           for folder in found.folders))
        dialog = FoundFoldersDialog(offered, self.window())
        if dialog.exec() != FoundFoldersDialog.DialogCode.Accepted:
            return
        adding(True)
        self.result_label.setText("Adding…")
        username, password = self.username.text().strip(), self.password.text()

        def failed(error: ChronoMailError) -> None:
            adding(False)
            self._show_problem(str(error))

        if self._new():
            parameters = AddDavParameters(found.url, username, password, self.account_name.text().strip(), dialog.left_out(), self._fingerprint)
            self._backend.ask(ACCOUNT_ADD_DAV, parameters, added, failed, owner=self)
        else:
            link = LinkParameters(str(self.target.currentData()), found.url, dialog.left_out(), found.pin, username, password or None)
            self._backend.ask(DAV_LINK, link, lambda _result: added(None), failed, owner=self)

    def set_adding(self, adding: bool) -> None:
        """While the account is being added nothing here can be changed: no Find, no field (RV-209)."""
        self._adding = adding
        for widget in (self.find_button, self.target, self.account_name, self.url, self.username, self.password):
            widget.setEnabled(not adding)
        if not adding:
            self.account_name.setEnabled(self._new())  # a mail account has its own name
            self._looking(self._finding)  # a Find still runs (started before a change of kind): Find waits for it
