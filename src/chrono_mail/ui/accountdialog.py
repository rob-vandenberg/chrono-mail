import dataclasses
import html
from collections.abc import Callable
from typing import Literal, override

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QResizeEvent
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
from chrono_mail.core import AccountKind, CertificateTrouble, ChronoMailError
from chrono_mail.engine.commands_accounts import (
    ACCOUNT_ADD, ACCOUNT_CHECK, AccountAddParameters, AccountAddResult, AccountCheckParameters, AccountCheckResult, AccountSummary,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.calendaraccount import CALDAV_KINDS, CARDDAV_KINDS, CalendarFields
from chrono_mail.ui.certificatequestions import questions_for
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.serverfields import SAME_LOGIN_TEXT, ServerFields, policy_of, test_result_text

# --- Version ------------------------------------------------------------
__version__ = "accountdialog.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: calendars_account_id: the account that got the calendar server, also a mail account given one (its first fill, D-300)
# v4.2.447: Test connection always checks the certificates itself; the test after Connect uses the answer; Connect without the tick takes back
#           a certificate kept here (D-299)
# v4.2.441: a server whose certificate could not be verified asks at once (Verify certificate) after Test and Add; Connect tests or adds
#           again; the Look at the server's certificate… button is gone (UR-05, D-299)
# v4.2.440: a long error text is shown whole: the smallest height follows what the wrapping texts need at the width, never above the screen
# v4.2.439: Account type offers POP3, IMAP, CalDAV and CardDAV: the calendar window is a part of this one (the sidebars' Add account…
#           opens it on CalDAV or CardDAV); the first-account window says "your first account" (UR-04, D-298)
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
CALDAV: str = "caldav"  # the Account type choices of a calendar and contacts server (D-185, D-298): one kind of account, other folders ticked
CARDDAV: str = "carddav"
CALENDAR_KINDS: tuple[tuple[str, str], ...] = ((CALDAV, "CalDAV: calendars and tasks from a server"), (CARDDAV, "CardDAV: contacts from a server"))
POLICIES: tuple[tuple[str, str], ...] = (
    ("keep", "Leave all mail on the server (default)"),
    ("delete_after_download", "Delete from the server once downloaded and verified"),
    ("delete_after_days", "Delete from the server 30 days after download"),
    ("delete_when_deleted_locally", "Delete from the server when I delete it here"),
)


class AccountDialog(QDialog):
    """Adds an account of any kind (D-298): POP3 or IMAP with SMTP (nothing is created until the connection has been tested or the person
    insists), or CalDAV or CardDAV, a calendar and contacts server (the CalendarFields part), as an account of its own or for a mail account."""

    def _restore_instead(self) -> None:
        """Restore a backup… instead of adding an account: this window closes, then the restore starts (its windows are never under this one)."""
        if self._adding:
            return  # never while an account is being added (RV-207)
        self.reject()
        if self._restore is not None:
            QTimer.singleShot(0, self._restore)

    def __init__(
        self, backend: Backend, first_account: bool = False, parent: QWidget | None = None, restore: Callable[[], None] | None = None,
        mail_accounts: list[AccountSummary] | None = None, start_kind: str = AccountKind.POP3.value,
    ) -> None:
        super().__init__(parent)
        self._mail_accounts: list[AccountSummary] = list(mail_accounts or [])  # those CalDAV and CardDAV may give a server (For)
        self._backend: Backend = backend
        self._pins: dict[str, tuple[tuple[str, int, str], str]] = {}  # incoming/outgoing -> (host, port, security) it was accepted for, fingerprint
        self.created_account_id: str | None = None
        self.key_created: bool = False
        self.show_recovery_code: bool = False  # this computer's recovery code was never confirmed as stored: shown after the add (RV-03)
        self.calendars_added: bool = False  # a calendar and contacts server was added or given to a mail account: synchronise it
        self.calendars_account_id: str | None = None  # the account that has it now
        self._adding: bool = False  # Add account is running: the window stays until the engine answered (RV-02)
        self._restore: Callable[[], None] | None = restore  # the first-account window's Restore a backup…: the main window's restore
        self._testing: bool = False  # Test connection of the mail part runs: its Add waits
        form = forms.fill(self, "account_add", {"servers": self._server_fields, "calendarFields": self._calendar_fields})  # D-276
        part(form, QLabel, "welcome").setVisible(first_account)
        self.restore_button: QPushButton = part(form, QPushButton, "restoreBackup")
        self.restore_button.setVisible(first_account and restore is not None)
        if first_account and restore is not None:
            self.restore_button.clicked.connect(self._restore_instead)  # a person on a new computer looks for it here first (W2)
        self._who: QGroupBox = part(form, QGroupBox, "yourAccount")
        self.kind: QComboBox = part(form, QComboBox, "kind")
        for kind, label in KINDS:
            self.kind.addItem(label, kind.value)
        for value, label in CALENDAR_KINDS:
            self.kind.addItem(label, value)
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
        self.result_label: QLabel = part(form, QLabel, "result")  # the mail part's; the calendar part has its own
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        buttons = part(form, QDialogButtonBox, "buttons")
        self.test_button = QPushButton("Test connection", buttons)
        self.add_button = QPushButton("Add account", buttons)
        self.add_button.setDefault(True)
        buttons.addButton(self.test_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.add_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button: QPushButton | None = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        self.calendar.changed = self._allow_add
        self.kind.setCurrentIndex(max(0, self.kind.findData(start_kind)))
        self._kind_changed()  # only the chosen kind's part is shown when the window measures itself: it opens at that part's size
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size
        layout = self.layout()
        sides = 0 if layout is None else layout.contentsMargins().left() + layout.contentsMargins().right()
        mail_width = max(widget.minimumSizeHint().width() for widget in (self._who, self.servers.widget, self._options))
        self.setMinimumWidth(max(self.minimumWidth(), mail_width + sides))  # the mail part is the wider: neither part is ever cut off

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.email.textEdited.connect(self._guess_from_address)
        self.test_button.clicked.connect(self._test)
        self.add_button.clicked.connect(self._add)

    def _server_fields(self, parent: QWidget) -> QWidget:
        """The incoming and outgoing server boxes (shared with Edit account), made at their place in the form."""
        self.servers = ServerFields(parent, AccountKind.POP3, editing=False)
        return self.servers.widget

    def _calendar_fields(self, parent: QWidget) -> QWidget:
        """The CalDAV and CardDAV part, made at its place in the form; it shares the window's Add button and result line."""
        self.calendar = CalendarFields(self._backend, self._mail_accounts, parent)
        return self.calendar

    # --- what follows what ------------------------------------------------------------------------

    def _is_imap(self) -> bool:
        return str(self.kind.currentData()) == AccountKind.IMAP.value

    def _is_calendar(self) -> bool:
        return str(self.kind.currentData()) in (CALDAV, CARDDAV)

    def _kind_changed(self) -> None:
        """POP3 or IMAP: the mail part, its title and port follow, and the choice about copies on the server only matters for POP3. CalDAV or
        CardDAV: the calendar part instead (the mail fields belong to another kind of account, so they are not shown). Each part keeps what was
        typed, its own result line and what its work answered, so an answer that comes after a change of kind stays with its own part."""
        calendar = self._is_calendar()
        for widget in (self._who, self.servers.widget, self._options, self.test_button, self.result_label):
            widget.setVisible(not calendar)
        self.calendar.setVisible(calendar)
        if calendar:
            self.calendar.ticked_kinds = CARDDAV_KINDS if str(self.kind.currentData()) == CARDDAV else CALDAV_KINDS
        else:
            self.servers.set_kind(AccountKind(str(self.kind.currentData())))
            self.policy.setEnabled(not self._is_imap())
            self.storage.setEnabled(not self._is_imap())
            if self._is_imap():
                self.storage.setCurrentIndex(0)  # an IMAP account always has the folders of its server
        self._allow_add()
        if self.isVisible():  # before the window is shown Qt sizes it to the part shown when it opens
            QTimer.singleShot(0, self, self._fit)  # tied to the window (never after it is gone), once Qt has laid out the part shown again (measured before that, it came out far too tall)

    def _fit(self) -> None:
        """The window takes the size of the kind's part now shown, from a fresh measure (a stale one opened it far too tall)."""
        layout = self.layout()
        if layout is not None:
            layout.activate()
        needed = self._height_needed()
        self.setMinimumHeight(needed)  # this kind's; the smallest width keeps the mail part's, the wider one
        self.resize(self.width(), max(needed, self.sizeHint().height()))  # a width the person chose stays

    def _height_needed(self) -> int:
        """The height the window needs at its width, a result line that wraps over several lines counted too (Qt's own measure leaves
        such a text out, so a long error was cut off)."""
        layout = self.layout()
        wrapped = layout.heightForWidth(self.width()) if layout is not None and layout.hasHeightForWidth() else 0
        needed = max(self.minimumSizeHint().height(), wrapped)
        screen = self.screen()
        return needed if screen is None else min(needed, screen.availableGeometry().height())  # never taller than its screen

    def _follow(self) -> None:
        """The smallest height is always what the window needs at its width now, up or down; the window grows when it is shorter than
        that, and never shrinks here (a height the person chose stays)."""
        needed = self._height_needed()
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)
        if needed > self.height():
            self.resize(self.width(), needed)

    @override
    def event(self, event: QEvent) -> bool:
        """Qt asks the window to lay itself out again (another result text, a part shown or hidden): its height follows."""
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and self.isVisible():
            QTimer.singleShot(0, self, self._follow)
        return handled

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Another width wraps the texts otherwise: the height they need is measured again."""
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width() and self.isVisible():
            QTimer.singleShot(0, self, self._follow)

    def _allow_add(self) -> None:
        """Add account as the kind now shown allows it: the mail part while no test runs, the calendar part once Find found something; never
        while an account is being added."""
        allowed = self.calendar.found is not None if self._is_calendar() else not self._testing
        self.add_button.setEnabled(allowed and not self._adding)

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

    def _ask_about(self, trouble: CertificateTrouble | None, again: Callable[[], None], fresh: bool = False) -> None:
        """A server whose certificate could not be verified: the Verify certificate window asks at once; Connect does the work again (D-299)."""
        if trouble is not None:
            questions_for(self._backend).ask(trouble, again, self, self._keep, kept=lambda: self.trusted, fresh=fresh)

    def _keep(self, pin: str, keep: bool) -> None:
        """Don't ask me again for an account not added yet: the certificate goes with it when it is added, for the server it was met at;
        Connect without it takes back one kept here before."""
        server, _, fingerprint = pin.rpartition("=")
        for which in ("incoming", "outgoing"):
            endpoint = self._endpoint(which)
            if f"{endpoint[0].strip().lower()}:{endpoint[1]}" == server:
                if keep:
                    self._pins[which] = (endpoint, fingerprint)
                elif which in self._pins and self._pins[which][1] == fingerprint:
                    del self._pins[which]

    def _endpoint(self, which: str) -> tuple[str, int, str]:
        return self.servers.endpoint(which)

    @property
    def trusted(self) -> list[str]:
        """The certificates the person accepted for the servers as they are now, each for its own server (host:port=fingerprint): one accepted for an address that was changed since is dropped."""
        return [server_pin(endpoint[0], endpoint[1], fingerprint) for which, (endpoint, fingerprint) in self._pins.items() if endpoint == self._endpoint(which)]

    _last_guess_user: str = ""
    _last_guess_host: str = ""

    # --- the commands -----------------------------------------------------------------------------

    def _values(self, fresh: bool = False) -> AccountCheckParameters:
        """fresh: a test the person starts, which checks the certificates with the usual checks only (D-299)."""
        return dataclasses.replace(self.servers.check_parameters(trusted=tuple(self.trusted)), fresh=fresh)

    def _busy(self, busy: bool) -> None:
        """A test runs: Test waits for its answer (it would start another test), and so does the mail part's Add."""
        self._testing = busy
        self.test_button.setEnabled(not busy and not self._adding)  # a test that ends during an add never brings Add back (RV-208)
        self._allow_add()

    def _set_adding(self, adding: bool) -> None:
        """While the account is being added nothing else can be done here: no Cancel, no test, no restore, no field
        changed (a test started then let the window close half-way, and Add start twice; RV-02, RV-207, RV-208)."""
        self._adding = adding
        widgets: list[QWidget | None] = [
            self.test_button, self.add_button, self.cancel_button, self.restore_button, self._who,
            self.servers.incoming_group, self.servers.outgoing_group, self._options,
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not adding)
        if not adding and self._testing:  # a test still runs (started before a change of kind): Test waits for it
            self.test_button.setEnabled(False)
        self.kind.setEnabled(not adding)
        self.calendar.set_adding(adding)
        self._allow_add()  # after a failed add: as the kind shown allows it (the calendar part's only with what Find found)

    def _complete(self) -> bool:
        if not (self.email.text().strip() and self.username.text().strip() and self.password.text() and self.incoming_host.text().strip() and self.outgoing_host.text().strip()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the e-mail address, user name, password and both servers.</span>")
            return False
        if not self.same_login.isChecked() and not (self.outgoing_username.text().strip() and self.outgoing_password.text()):
            self.result_label.setText(f"<span style='color:{red()}'>Please fill in the user name and password of the outgoing server, or tick {SAME_LOGIN_TEXT}.</span>")
            return False
        return True

    def _test(self) -> None:
        """Test connection: it always checks the certificates itself, whatever was answered before (D-299)."""
        self._check(fresh=True)

    def _retest(self) -> None:
        """The test after Connect: with what was answered."""
        self._check(fresh=False)

    def _check(self, fresh: bool) -> None:
        if self._adding or self._testing or not self._complete():  # never during an add, nor a second test (a Connect ends in one)
            return
        self._busy(True)
        self.result_label.setText("Testing…")

        def done(result: AccountCheckResult) -> None:
            self._busy(False)
            self.result_label.setText(test_result_text(result))
            self._ask_about(result.incoming_certificate, self._retest, fresh)
            self._ask_about(result.outgoing_certificate, self._retest, fresh)

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        self._backend.ask(ACCOUNT_CHECK, self._values(fresh), done, failed, owner=self)

    def _add(self) -> None:
        if self._is_calendar():
            self._add_calendars()
            return
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

        self._backend.ask(ACCOUNT_ADD, parameters, done, failed, owner=self)

    def _add_calendars(self) -> None:
        def added(result: AccountAddResult | None) -> None:
            self._adding = False
            self.calendars_added = True
            self.calendars_account_id = result.account_id if result is not None else str(self.calendar.target.currentData())
            if result is not None:  # an account of its own (a mail account given its server has nothing more to tell)
                self.created_account_id = result.account_id
                self.key_created = result.key_created
                self.show_recovery_code = result.show_recovery_code
            self.accept()

        self.calendar.add(self._set_adding, added)

    @override
    def reject(self) -> None:
        """Cancel, Escape or the close button: not while the account is being added (it is then added, and its recovery code is shown)."""
        if not self._adding:
            super().reject()



def explain_error(parent: QWidget | None, title: str, error: ChronoMailError) -> None:
    QMessageBox.warning(parent, title, str(error))
