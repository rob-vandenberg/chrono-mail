from collections.abc import Callable
from dataclasses import replace as changed
from typing import Literal

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QWidget

from chrono_mail.core import CertificateError, CertificateTrouble, ChronoMailError
from chrono_mail.engine.commands_accounts import WALLET_LABEL, AccountParameters, NoParameters, WalletLabelResult
from chrono_mail.engine.commands_dav import DAV_SYNC, DavSyncSummary, SyncParameters
from chrono_mail.engine.commands_mail import MAIL_FETCH, MAIL_SETTLE, BlockedFolder, FetchResult, SettleParameters
from chrono_mail.engine.commands_outbox import MAIL_SEND_OUTBOX, SendOutboxParameters, SendOutboxResult
from chrono_mail.engine.commands_schedule import MAIL_SEND_DUE, MESSAGE_WAKE, SendDueParameters, SendDueResult, WakeParameters, WakeResult
from chrono_mail.engine.commands_watch import MAIL_WAIT, WaitParameters, WaitResult
from chrono_mail.engine.junkfilter import JUNK_LEARN_EXISTING, JunkLearnResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatequestions import questions_for, with_account
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.operations import OperationsWindow
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.prefs import InUse
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "mailchecking.py 4.2.442"

# --- Version history ----------------------------------------------------
# v4.2.442: ask_after_send: a message window's Send stopped by a certificate asks once that window has closed; Connect sends that message (D-299)
# v4.2.441: a Refresh that meets a server whose certificate could not be verified (receiving, sending, the calendar sync) asks: Verify
#           certificate over the main window; Connect does that account's work again (UR-05, D-299)
# v4.2.419: went_online: a mail check, a send that reached a server or a calendar sync tried the internet; the check for updates goes along (D-293)
# v3.6.371: label_wallet at start: readable labels in the wallet (B-23, D-275)
# v3.5.348: A question with buttons of its own shows plain text (RV-167, without a filter on the whole program)
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v2.4.327: check_mail (after a rebuild, an import, a restore, a first account) only receives; a refresh a rebuild held back runs after it
#           with its own choice (D-245, the Windows computer's W6)
# v2.3.316: start_refresh: the refresh at start does its own choice of Settings > General (the owner)
# v2.3.315: Send only: a scheduled refresh only sends the Outbox (the calendars stay in step), and the server's "new mail" fetches nothing (the owner)
# v2.2.314: a refresh the person did not click (the timers, the check at start and after a rebuild) sends the Outbox first, as Refresh does,
#           unless Settings > General says Receive only; the server's "new mail" only fetches; send_outbox can send one message (B-21)
# v2.1.311: the busy bar names the learning by itself (the general rule): no hint of its own
# v2.1.310: learn_existing_junk tells when it is done (and the bar's hover text says what it does meanwhile)
# v2.1.308: the status line says how many new messages went to Junk; learn_existing_junk at start (D-238)
# v2.0.281: no automatic mail check while an index is rebuilt or filled in; one when it ends (D-229)
# v1.9.274: R-19 round 4: the accounts and change notices are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: every engine call typed; the answers read by their fields
# v1.9.264: R-17: checking and sending mail as a part of its own (out of ui/mainwindow.py): Refresh, Send All, the general timer and the
#           timers of accounts with an interval of their own, the push of IMAP servers (IDLE), scheduled sending and snoozed mail coming
#           back, the Operations window, the questions about folders held back by a safety rule, the calendar synchronisation

DUE_CHECK_MS: int = 60_000
WATCH_SECONDS: int = 300
WATCH_RETRY_MS: int = 60_000
CALENDARS_WORDS: str = "Calendars, contacts and tasks"  # how the status line names what a calendar and contacts server keeps in step
SettleChoice = Literal["delete_on_server", "restore_here", "remove_here", "keep_here"]
BLOCKED_QUESTIONS: dict[str, tuple[str, tuple[tuple[str, SettleChoice, str], ...]]] = {  # kind -> question, (button, choice, what was done)
    "missing_here": (
        "{count} messages that you deleted in Chrono Mail are still in {folder} on the server of {account}. What should happen?",
        (("Delete them on the server too", "delete_on_server", "deleted on the server"), ("Bring them back here", "restore_here", "brought back")),
    ),
    "gone_from_server": (
        "{count} messages in {folder} of {account} are gone from the server, but are still here. What should happen?",
        (("Remove them here too", "remove_here", "removed here"), ("Keep them here", "keep_here", "kept and uploaded again")),
    ),
}


def ask_choice(parent: QWidget, title: str, text: str, buttons: list[str]) -> int | None:
    """A question with buttons of its own: the position of the button that was clicked, None when the window was closed."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)  # the text may hold server and mail words: never markup (RV-167)
    made = [box.addButton(label, QMessageBox.ButtonRole.AcceptRole) for label in buttons]
    box.exec()
    clicked = box.clickedButton()
    return next((position for position, button in enumerate(made) if button is clicked), None)


class MailChecking(QObject):
    """Sending and receiving: the owner of the timers, the waits for new mail, the Operations window's rounds and the calendar sync."""

    new_mail = Signal(object)  # a check brought new mail: {account id: count}
    went_online = Signal()  # a Refresh tried to reach a server (a mail check, a send that reached one, a calendar sync): updates may be looked for (D-293)

    def __init__(
        self, backend: Backend, state: MailState, status: StatusReport, prefs_in_use: InUse, messages: MessageListPane, refresh: QAction, auto: QAction,
        pages: dict[str, PimPage], section: Callable[[], str], parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._prefs: InUse = prefs_in_use
        self._messages: MessageListPane = messages
        self._refresh: QAction = refresh  # greyed while a check runs
        self._auto: QAction = auto  # Refresh automatically
        self._pages: dict[str, PimPage] = pages
        self._section: Callable[[], str] = section
        self._parent: QWidget = parent
        self.operations: OperationsWindow = OperationsWindow(self._operation_name, prefs_in_use.settings, parent)
        self.checking: bool = False
        self.sending: bool = False  # Send All is running
        self.importing: bool = False  # an import runs: mail checks wait (a check meanwhile could store a message the import stores again)
        self.rebuilding: bool = False  # an index is rebuilt or filled in: the automatic checks wait (D-229)
        self._held_refresh: str | None = None  # the choice of a refresh the rebuild held back (send_receive or receive): it runs afterwards
        self.dav_syncing: bool = False
        self.watching: set[str] = set()  # accounts with a wait for new mail in progress
        self.no_push: set[str] = set()  # accounts whose server cannot tell about new mail
        self.quitting: bool = False
        self._asking_blocked: bool = False  # a question about a folder held back by a safety rule is open
        self._sync_summary: str = ""  # what Refresh in a section brought in, said with that section's count once it has read its list again
        self.due_timer: QTimer = QTimer(self)
        self.due_timer.timeout.connect(self.run_due)
        self.due_timer.start(DUE_CHECK_MS)
        self.timer: QTimer = QTimer(self)
        self.timer.timeout.connect(self.check_on_general_interval)
        self.account_timers: dict[str, QTimer] = {}  # accounts that have a check interval of their own
        if prefs_in_use.read().auto_check:
            self.timer.start(self.auto_minutes() * 60 * 1000)
        auto.toggled.connect(self._toggle_auto_check)

    def stop(self) -> None:
        """The program ends: no more checks and no more waits for new mail."""
        self.quitting = True
        self.due_timer.stop()
        for timer in self.account_timers.values():
            timer.stop()

    def auto_minutes(self) -> int:
        return self._prefs.read().auto_check_minutes  # 1 to 240, checked where it is read

    # --- timers and waits ------------------------------------------------------------------------------------------

    def accounts_loaded(self) -> None:
        """The accounts were loaded: the waits for new mail and the timers of the accounts follow them."""
        self.start_watchers()
        self.apply_account_timers()

    def apply_account_timers(self) -> None:
        """A timer for every account that has an interval of its own (and only while automatic checking is on)."""
        wanted = {
            account.account_id: account.check_minutes for account in self._state.mail_accounts()
            if account.check_minutes > 0 and self._auto.isChecked()
        }
        for account_id in list(self.account_timers):
            if account_id not in wanted:
                self.account_timers.pop(account_id).stop()
        for account_id, minutes in wanted.items():
            timer = self.account_timers.get(account_id)
            if timer is None:
                timer = QTimer(self)
                timer.timeout.connect(lambda chosen=account_id: self.scheduled_refresh({chosen}))
                self.account_timers[account_id] = timer
            if timer.interval() != minutes * 60_000 or not timer.isActive():
                timer.start(minutes * 60_000)

    def start_watchers(self) -> None:
        """For every IMAP account: wait for the server to report new mail (IDLE) and check at once when it does."""
        if not self._auto.isChecked():
            return
        for account in self._state.mail_accounts():
            if account.kind == "imap":
                self._watch(account.account_id)

    def _watch(self, account_id: str) -> None:
        if account_id in self.watching or self.quitting or account_id in self.no_push:
            return
        self.watching.add(account_id)

        def waited(result: WaitResult) -> None:
            self.watching.discard(account_id)
            if not result.supported:
                self.no_push.add(account_id)  # this server cannot tell: the timer checks
                return
            if self._auto.isChecked() and not self.quitting:
                if result.changed and self._prefs.read().scheduled_refresh != "send":
                    self.check_accounts(None)  # news of incoming mail: only fetched (never when the person chose to send only)
                self._watch(account_id)

        def failed(error: ChronoMailError) -> None:
            self.watching.discard(account_id)
            if not self.quitting and self._auto.isChecked():
                QTimer.singleShot(WATCH_RETRY_MS, lambda: self._watch(account_id))  # a lost connection is tried again after a pause

        self._backend.ask(MAIL_WAIT, WaitParameters(account_id, WATCH_SECONDS), waited, failed, owner=self, waiting=True)

    def _toggle_auto_check(self, on: bool) -> None:
        self._prefs.keep(changed(self._prefs.read(), auto_check=on))
        if on:
            self.start_watchers()
            self.timer.start(self.auto_minutes() * 60 * 1000)
        else:
            self.timer.stop()
        self.apply_account_timers()

    def take_in_auto_check(self, on: bool) -> None:
        """Settings OK changed the automatic check or its interval."""
        if on and self._auto.isChecked():
            self.timer.start(self.auto_minutes() * 60 * 1000)  # a new interval
        self._auto.setChecked(on)

    def run_due(self) -> None:
        """Every minute: send the scheduled messages whose time has come and bring snoozed messages back."""
        if not self._state.accounts or self.quitting:
            return
        changes = {"sent": 0, "woken": 0}

        def finish() -> None:
            if changes["sent"] or changes["woken"]:
                self._status.finished(f"{counted(changes['sent'], 'scheduled message')} sent, {counted(changes['woken'], 'snoozed message')} back")

        def sent(result: SendDueResult) -> None:
            changes["sent"] = result.sent
            for text in result.failed:
                self._status.show(f"Scheduled message: {text}", error=True)
            for trouble in result.certificates:  # Connect sends that account's Outbox (a one-off waits there; a repeating one goes at its next try)
                self._ask_about(trouble, lambda account=trouble.account: self.send_outbox(account=account or None))
            self._backend.ask(MESSAGE_WAKE, WakeParameters(), woke, lambda error: finish(), owner=self)

        def woke(result: WakeResult) -> None:
            changes["woken"] = result.woken
            finish()

        self._backend.ask(MAIL_SEND_DUE, SendDueParameters(), sent, lambda error: self._status.report("Scheduled messages", error), owner=self)

    def rebuild_running(self, running: bool) -> None:
        """An index is rebuilt or filled in: the timers and the push skip their turn (Refresh still works); when it ends, one check (D-229):
        the refresh it held back with that refresh's own choice (the one at start, a timer's), else one that only receives (D-245)."""
        was, self.rebuilding = self.rebuilding, running
        if was and not running:
            held, self._held_refresh = self._held_refresh, None
            if held is not None:
                self.scheduled_refresh(None, how=held)
            else:
                self.check_mail()

    def import_running(self, running: bool) -> None:
        """An import starts or ends: no mail checks meanwhile, and Refresh is off."""
        self.importing = running
        self._refresh.setEnabled(not running and not self.checking)

    # --- send and receive ----------------------------------------------------------------------------------------------

    def check_mail(self) -> None:
        """The check after an index rebuild, an import, a restore or a first account: it brings the servers' mail in and never sends the
        Outbox, which only the person, the refresh at start and the timers do (D-245: a restore of an old backup sent its old Outbox)."""
        self.check_accounts(None)

    def start_refresh(self) -> None:
        """The refresh when the program starts: what Settings > General chose for it."""
        self.scheduled_refresh(None, how=self._prefs.read().start_refresh)

    def scheduled_refresh(self, only: set[str] | None, also_calendars: set[str] | None = None, how: str | None = None) -> None:
        """A refresh the person did not click: Send and Receive, exactly as Refresh (the Outbox of these accounts first, then their mail),
        or only receive, or only send, as Settings > General says (B-21; Send only keeps the calendars in step, they are no mail). It never brings
        the Operations window forward."""
        how = how or self._prefs.read().scheduled_refresh
        if how == "send":
            self.sync_calendars(None if only is None else only | (also_calendars or set()))
            if not (self.importing or self.sending):
                self.send_outbox(account=next(iter(only)) if only is not None and len(only) == 1 else None)
            return
        if self.rebuilding and not self.importing:  # it runs, with its own choice, when the rebuild is done
            self._held_refresh = "send_receive" if "send_receive" in (how, self._held_refresh) else how
            return
        if how != "send_receive" or self.importing or (self.checking or self.sending):
            self.check_accounts(only, also_calendars=also_calendars)
            return

        def receive() -> None:
            self.check_accounts(only, new_round=False, also_calendars=also_calendars)
            self._settle_operations()  # nothing to check: the round ends with the send

        self.send_outbox(then=receive, account=next(iter(only)) if only is not None and len(only) == 1 else None)

    def send_and_receive(self) -> None:
        """Refresh: first send what waits in the Outbox, then check every account. Refresh stays greyed out meanwhile."""
        self._refresh.setEnabled(False)

        if self.importing:
            return

        def receive() -> None:
            if not self.checking:
                self._refresh.setEnabled(not self.importing)
            self.check_accounts(None, manual=True, new_round=False)
            self._settle_operations()  # nothing to check: the round ends with the send

        self.send_outbox(then=receive, manual=True)

    def send_outbox(
        self, then: Callable[[], None] | None = None, manual: bool = False, account: str | None = None, message: str | None = None, now: bool = False,
    ) -> None:
        """Send All: the messages that wait in the Outbox (scheduled ones, and delayed ones whose countdown runs, wait for their time). Manual:
        the person asked for it (the Operations window may open). account: only its Outbox; message: only that one, now (B-21). Nothing is
        sent while an import runs (then still follows). now: also the delayed ones whose countdown runs (Quit)."""
        if self.importing:
            if then is not None:
                then()
            return
        self._begin_operations(manual)
        self.sending = True

        def done(result: SendOutboxResult) -> None:
            self.sending = False
            sent, failed, accounts = result.sent, result.failed, result.failed_accounts
            for position, text in enumerate(failed):
                self._finish_operation(accounts[position] if position < len(accounts) else "", "send", text)
            for trouble in result.certificates:  # Connect sends that account's Outbox again (D-299)
                self._ask_about(trouble, lambda account=trouble.account: self.send_outbox(account=account or None))
            self.operations.finish_all("send")
            if failed:
                self._status.show("Sending: " + "; ".join(failed), error=True)
            elif sent:
                self._status.show(f"Sent {counted(sent, 'message')} from the Outbox")
            if sent:
                self.went_online.emit()  # only a send that reached a server: an empty Outbox connects nowhere
            if then is not None:
                then()
            else:
                self._settle_operations()

        def failed(error: ChronoMailError) -> None:
            self.sending = False
            self._status.report("Could not send the Outbox", error)
            self._finish_operation("", "send", str(error))
            self.operations.finish_all("send")
            if then is not None:
                then()
            else:
                self._settle_operations()

        self._backend.ask(MAIL_SEND_OUTBOX, SendOutboxParameters(account, message, now), done, failed, owner=self)

    def ask_after_send(self, trouble: CertificateTrouble, message_id: str) -> None:
        """A message window's Send stopped by the outgoing server's certificate: the message waits in the Outbox, and the Verify certificate
        window asks now the message window has closed; Connect sends exactly that message (D-299)."""
        self._status.show(f"The message waits in your Outbox: the certificate of {trouble.host} could not be verified", error=True)
        questions_for(self._backend).ask(trouble, lambda: self.send_outbox(manual=True, account=trouble.account or None, message=message_id), by_person=True)

    def _ask_about(self, trouble: CertificateTrouble, again: Callable[[], None]) -> None:
        """A Refresh met a server whose certificate could not be verified: Verify certificate asks over the main window as soon as it is on
        screen (never twice for one server, never again after Do not connect in this run); Connect does that work again (UR-05, D-299)."""
        questions_for(self._backend).ask(trouble, again)

    def check_on_general_interval(self) -> None:
        """The general timer: the accounts that have no interval of their own."""
        calendars_only = {account.account_id for account in self._state.all_accounts if account.kind == "dav"}
        due = {account.account_id for account in self._state.mail_accounts() if account.check_minutes == 0}
        self.scheduled_refresh(due, also_calendars=calendars_only)

    def check_accounts(self, only: set[str] | None, manual: bool = False, new_round: bool = True, also_calendars: set[str] | None = None) -> None:
        """Check these accounts (all when None) for new mail, and bring their calendars, task lists and address books in step (also_calendars: the
        accounts of calendars only that the general timer adds). Manual: the person asked for it (the Operations window may open); new_round False:
        it goes on from a send (Refresh), whose rows stay. Nothing is checked while an import runs."""
        if self.importing or (self.rebuilding and not manual):
            return  # the timers and the push skip their turn; one check runs when the import or the rebuild is done
        self._messages.load_contact_addresses()  # Refresh also brings contacts that came from a server
        mail_ids = set(self._state.mail_account_ids())
        wanted = mail_ids if only is None else only & mail_ids
        self.sync_calendars(None if only is None else only | (also_calendars or set()))
        if self.checking or not wanted:
            return
        if new_round:
            self._begin_operations(manual)
        for account_id in sorted(wanted, key=self._state.account_name):
            self.operations.add(account_id, "receive")
        self.checking = True
        self._refresh.setEnabled(False)
        remaining = set(wanted)
        totals = {"new": 0, "junk": 0}
        new_by_account: dict[str, int] = {}
        problems: list[str] = []
        blocked: list[tuple[str, BlockedFolder]] = []  # (account, folder held back by a safety rule)

        def finish() -> None:
            self.checking = False
            self._refresh.setEnabled(not self.importing)
            if problems:
                self._status.show("Mail check: " + "; ".join(problems), error=True)
            else:
                junk = f", {totals['junk']} moved to Junk" if totals["junk"] else ""
                self._status.show(f"Mail checked: {counted(totals['new'], 'new message')}{junk}" if totals["new"] else "Mail checked: nothing new")
            self.new_mail.emit(new_by_account)
            if not self._asking_blocked:  # a check during an open question (the timer) does not ask a second time
                self._asking_blocked = True
                try:
                    for account_id, item in blocked:
                        self.ask_about_blocked(account_id, item)
                finally:
                    self._asking_blocked = False
            self._settle_operations()  # new mail came as "mailbox changed": the list and the numbers of that mailbox follow (D-146)

        def make_done(account_id: str) -> Callable[[FetchResult], None]:
            def done(result: FetchResult) -> None:
                totals["new"] += result.downloaded
                totals["junk"] += result.junked
                new_by_account[account_id] = result.downloaded
                own = list(result.failed)
                blocked.extend((account_id, item) for item in result.blocked)
                if result.aborted:
                    own.append(result.aborted)
                problems.extend(own)
                self._finish_operation(account_id, "receive", "; ".join(own) if own else None)
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                if isinstance(error, CertificateError):  # Connect checks that account again (D-299)
                    self._ask_about(with_account(error.trouble, account_id), lambda: self.check_accounts({account_id}))
                problems.append(str(error))
                self._finish_operation(account_id, "receive", str(error))
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return failed

        self._status.show("Checking for mail…")
        for account_id in list(remaining):
            self._backend.ask(MAIL_FETCH, AccountParameters(account_id), make_done(account_id), make_failed(account_id), owner=self, checking=True)
        self.went_online.emit()

    def learn_existing_junk(self) -> None:
        """At start: the junk filter learns once from the mail already kept in each place (D-238); the status line follows how far it is."""

        def done(result: JunkLearnResult) -> None:
            if result.places:
                self._status.show(f"Junk filter: learned from {result.places} mailbox{'es' if result.places != 1 else ''}")

        def failed(error: ChronoMailError) -> None:
            self._status.report("Junk filter: learning from your mail", error)

        self._backend.ask(JUNK_LEARN_EXISTING, NoParameters(), done, failed, owner=self)

    def label_wallet(self) -> None:
        """At start: Chrono Mail's entries in the wallet get labels a person can read (B-23). Silent: only labels change, and one that
        could not be written is tried again at the next start."""

        def done(result: WalletLabelResult) -> None:
            pass

        def failed(error: ChronoMailError) -> None:
            pass

        self._backend.ask(WALLET_LABEL, NoParameters(), done, failed, owner=self)

    def ask_about_blocked(self, account_id: str, item: BlockedFolder) -> None:
        """A safety rule held an IMAP folder back: the person decides, and the account is synchronised with that answer."""
        kind, folder, count = item.kind, item.folder, item.count
        if kind not in BLOCKED_QUESTIONS:
            return
        name = self._state.account_name(account_id)
        question, answers = BLOCKED_QUESTIONS[kind]
        chosen = ask_choice(self._parent, "Messages out of step", question.format(count=f"{count:,}", folder=folder, account=name), [label for label, _, _ in answers])
        if chosen is None:
            return  # asked again at the next check
        _, choice, done_text = answers[chosen]

        def done(result: FetchResult) -> None:
            self._status.finished(f"{folder} of {name}: {counted(count, 'message')} {done_text}")  # its "mailbox changed" fetches again

        self._status.show(f"{folder} of {name}: synchronising…")
        self._backend.ask(MAIL_SETTLE, SettleParameters(account_id, folder, choice), done, lambda error: self._status.report(f"Could not settle {folder}", error), owner=self)

    # --- the Operations window -------------------------------------------------------------------------------------------------

    def show_operations(self) -> None:
        self.operations.show()
        self.operations.raise_()
        self.operations.activateWindow()

    def _operation_name(self, account_id: str, direction: str) -> str:
        """The name of a row: the address and the protocol, as "rob@example.com (POP3)"."""
        account = self._state.account(account_id)
        protocol = "SMTP" if direction == "send" else account.kind.upper() if account is not None else ""
        return f"{self._state.account_name(account_id)} ({protocol})" if protocol else self._state.account_name(account_id)

    def _show_operations_quietly(self) -> None:
        """Open the Operations window without taking the keyboard from what the person is doing."""
        if self.operations.isVisible():
            return
        self.operations.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.operations.show()
        self.operations.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

    def _begin_operations(self, manual: bool) -> None:
        """A send and receive starts: the rows of the last one go and the window opens when that is wanted; only one the
        person started (manual: Refresh) brings it to the front with the keyboard focus."""
        if self.checking or self.sending:
            return
        self.operations.begin_round()
        if not self._prefs.read().operations_show_on_send_receive:
            return
        if manual:
            self.show_operations()
        else:
            self._show_operations_quietly()

    def activity(self, account_id: str, direction: str, step: str, done: object, total: object) -> None:
        self.operations.step(account_id, direction, step, done if isinstance(done, int) else None, total if isinstance(total, int) else None)

    def _finish_operation(self, account_id: str, direction: str, error: str | None) -> None:
        self.operations.finish(account_id, direction, error)
        if error is not None and self._prefs.read().operations_show_on_error:
            self._show_operations_quietly()

    def _settle_operations(self) -> None:
        """Everything is done: the window closes itself when that is wanted, but stays after an error when errors show it."""
        if self.checking or self.sending or self.operations.running() or not self.operations.isVisible():
            return
        if self.operations.round_errors and self._prefs.read().operations_show_on_error:
            return
        if self._prefs.read().operations_hide_when_done:
            self.operations.hide()

    # --- calendars, contacts and tasks ----------------------------------------------------------------------------------------

    def page_status(self, page: PimPage, text: str) -> None:
        """A section says how many items it shows: only the section on the screen writes the status line (a hidden one that re-read its list
        must not cover what the shown one says), and right after Refresh brought something in, what came of it goes in front."""
        if self._pages.get(self._section()) is not page:
            return
        if self._sync_summary:
            text, self._sync_summary = f"{self._sync_summary}. {text}", ""
        self._status.show(text)

    def refresh_calendars(self) -> None:
        """Refresh in Calendar, Contacts and Tasks: bring every calendar, task list and address book in step with its server, and say so."""
        if not self._state.calendar_accounts():
            for page in self._pages.values():
                page.refresh()
            self._status.show("No account has a calendar and contacts server; what is on this computer is shown")
            return
        if self.dav_syncing:
            self._status.show(f"{CALENDARS_WORDS}: synchronising…")  # one synchronisation at a time: the one under way will tell
            return
        self.sync_calendars(None, manual=True)

    def _calendar_buttons(self, enabled: bool) -> None:
        for page in self._pages.values():
            page.act_refresh.setEnabled(enabled)

    def sync_calendars(self, only: set[str] | None = None, manual: bool = False) -> None:
        """Bring the calendars, task lists and address books of the accounts that have a server (all, or only these) in step with it, in the
        background, one synchronisation at a time. Manual (Refresh in a section): the status line says it runs and what came of it."""
        accounts = self._state.calendar_accounts(only)
        if self.dav_syncing or not accounts:
            return
        self.dav_syncing = True
        self._calendar_buttons(False)  # greyed while it runs, never hidden
        if manual:
            self._status.show(f"{CALENDARS_WORDS}: synchronising…")

        def done(result: tuple[DavSyncSummary, ...]) -> None:
            self.dav_syncing = False
            self._calendar_buttons(True)
            self._messages.load_contact_addresses()  # contacts that came from a server get their icon in the message list at once
            rows = list(result)
            for row in rows:
                if row.certificate is not None:  # Connect synchronises that account again (D-299)
                    self._ask_about(row.certificate, lambda account=row.account: self.sync_calendars({account}, manual))
            problems = [text for row in rows for text in row.errors]
            pulled = sum(row.pulled for row in rows)
            removed = sum(row.deleted_here for row in rows)
            sent = sum(row.pushed + row.deleted_on_server + row.made_on_server for row in rows)
            gone = sum(row.removed for row in rows)  # whole collections the server confirmed deleted (R-25)
            changed_here = pulled + removed + gone + sum(row.created for row in rows)
            parts = [text for count, text in (
                (pulled, f"{pulled} added or changed here"), (removed, f"{removed} removed here"), (gone, f"{counted(gone, 'calendar or list', 'calendars or lists')} gone from the server, removed here"),
                (sent, f"{sent} sent to the server"),
            ) if count]
            summary = f"{CALENDARS_WORDS}: " + ", ".join(parts) if parts else f"{CALENDARS_WORDS} are up to date"
            if problems:
                self._status.show(f"{CALENDARS_WORDS}: " + "; ".join(problems), error=True)
            elif manual and changed_here and self._section() in self._pages:
                self._sync_summary = summary  # said with the count of the shown section once it has read its list again
            elif manual:
                self._status.show(summary)
            if changed_here:
                for page in self._pages.values():
                    page.refresh()

        def failed(error: ChronoMailError) -> None:
            self.dav_syncing = False
            self._calendar_buttons(True)
            self._status.show(f"{CALENDARS_WORDS}: {error}", error=True)
            if isinstance(error, CertificateError):
                self._ask_about(error.trouble, lambda: self.sync_calendars(only, manual))

        self._backend.ask(DAV_SYNC, SyncParameters() if only is None else SyncParameters(accounts=tuple(accounts)), done, failed, owner=self)
        self.went_online.emit()
