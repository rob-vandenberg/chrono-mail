from typing import Literal

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, AccountSummary, NoParameters
from chrono_mail.engine.commands_drafts import DRAFT_OPEN, DraftOpenParameters, DraftOpenResult
from chrono_mail.engine.commands_mail import MESSAGE_COMPOSE, ComposeParameters, ComposeResult
from chrono_mail.engine.commands_outbox import MAIL_CANCEL_SEND, CancelSendResult, OutboxMessageParameters, SentAlready
from chrono_mail.engine.commands_schedule import MAIL_SCHEDULED, AccountOptional, ScheduledInfo
from chrono_mail.engine.compose import ComposeKind
from chrono_mail.ui import appearance, prefs, scale, theme
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH
from chrono_mail.ui.composer import ComposeOptions, ComposeWindow
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.prefs import InUse
from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "composing.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: an Outbox message that was sent already opens to be read (sent_already), never as a draft (RV-181)
# v3.1.341: a message of the Outbox opened to be changed is taken out of the Outbox into Drafts first (left_outbox; RV-156)
# v2.4.334: New asks the engine for the accounts while the window does not know them yet (the index rebuild after a restore, W9b)
# v2.4.332: New works while no folder is selected: the default account, else the open one, else the first (W9)
# v2.2.314: send_waiting: a delayed send of a message window waits in the Outbox; open_taken_back: a cancelled send opens again (B-21)
# v1.9.274: R-19 round 4: the rows and the draft or reply it opens are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: every engine call typed
# v1.9.264: R-17: writing mail as a part of its own (out of ui/mainwindow.py): New, Reply, Reply all, Forward, a draft or a scheduled
#           message opened again (it stays scheduled, D-177), a message to a contact or a mailto link; the open message windows


COMPOSE_KINDS: dict[str, ComposeKind] = {"reply": "reply", "reply_all": "reply_all", "forward": "forward", "forward_attachment": "forward_attachment"}
QUOTES: dict[str, Literal["above", "below", "none"]] = {"above": "above", "below": "below", "none": "none"}


class Composing(QObject):
    """The message windows that write mail: the one owner of the list of open ones."""

    send_waiting = Signal(str, str, str, int)  # a delayed send waits in the Outbox: account, message id, subject, seconds of its countdown (B-21)
    left_outbox = Signal(str)  # a message was taken out of the Outbox to be changed: its message id (a countdown for it goes, RV-156)
    sent_already = Signal(str, str, str)  # the Outbox message opened was sent already (its move to Sent failed): its account, folder and id,
    # to open exactly it to be read (RV-181)

    def __init__(self, backend: Backend, state: MailState, status: StatusReport, prefs_in_use: InUse, messages: MessageListPane, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._prefs: InUse = prefs_in_use
        self._messages: MessageListPane = messages
        self.composers: list[ComposeWindow] = []

    def new_message(self) -> None:
        """New: from the default account, else the open folder's, else the first account; also while no folder is selected (just after the
        start or a restore, W9) and while the window does not know its accounts yet (the index rebuild at start or after a restore: the
        engine is asked for them, W9b). Without a mail account there is nothing to send from."""
        if self._state.mail_accounts():
            self._new_from(self._state.mail_accounts())
            return

        def listed(found: tuple[AccountSummary, ...]) -> None:
            self._new_from([account for account in found if account.kind not in ("local", "dav")])

        self._backend.ask(ACCOUNT_LIST, NoParameters(), listed, lambda error: self._status.report("New message", error), owner=self)

    def _new_from(self, accounts: list[AccountSummary]) -> None:
        wanted = self._prefs.read().default_account
        known = [account.account_id for account in accounts]
        account = wanted if wanted in known else self._state.view.account or (known[0] if known else None)
        if account is not None:
            self.open_composer(account, None, None, accounts=accounts)

    def compose_to(self, address: str) -> None:
        """A new message to this address (from a contact)."""
        if self._state.mail_accounts():
            self.open_composer(self._state.view.account or "", None, None, to=address)

    def new_scheduled_message(self, panel: ScheduledPanel | None) -> None:
        """New on Settings > Scheduled messages, or in the menu of a Scheduled view: a message window whose main action
        schedules. From Settings, which waits for it, it is modal (or it could not be used), and the new message joins the page."""
        window = self.open_composer(self._state.view.account or "", None, None, schedule_only=True, modal=panel is not None)
        if panel is not None:
            window.scheduled.connect(panel.add_scheduled)

    def edit_scheduled(self, account: str, info: ScheduledInfo, panel: ScheduledPanel | None) -> None:
        """A message that waits in the Scheduled folder, opened to change it: it stays scheduled and keeps its id (D-177). From
        Settings the window is modal (as New), and the page's line follows when it is saved."""
        message_id = info.message_id
        if self._bring_forward(SCHEDULED_FOLDER_PATH, message_id):
            return

        def opened(result: DraftOpenResult) -> None:
            window = self.open_composer(account, result, None, resume=(SCHEDULED_FOLDER_PATH, message_id), modal=panel is not None, editing=info)
            if panel is not None:
                window.scheduled_changed.connect(panel.replace_scheduled)

        self._backend.ask(DRAFT_OPEN, DraftOpenParameters(account, message_id, SCHEDULED_FOLDER_PATH), opened, lambda error: self._status.report("Could not open the message", error), owner=self)

    def _bring_forward(self, folder: str, message_id: str) -> bool:
        """The message is open in a window already: that window comes to the front."""
        for window in self.composers:
            if window.stored_as() == (folder, message_id):
                window.raise_()
                window.activateWindow()
                return True
        return False

    def open_composer(
        self, account_id: str, draft: DraftOpenResult | ComposeResult | None, source: tuple[str, str] | None, to: str = "", resume: tuple[str, str] | None = None, schedule_only: bool = False, modal: bool = False,
        editing: ScheduledInfo | None = None, taken_back: bool = False, accounts: list[AccountSummary] | None = None,
    ) -> ComposeWindow:
        accounts = self._state.mail_accounts() if accounts is None else accounts  # given: the engine's, while the window does not know them yet
        mail_ids = [account.account_id for account in accounts]
        if account_id not in mail_ids:  # Local Folders has no server: the answer comes from the account chosen in the settings, or the first
            wanted = self._prefs.read().default_account
            account_id = wanted if wanted in mail_ids else (mail_ids[0] if mail_ids else account_id)
        window = ComposeWindow(
            self._backend, accounts, account_id, draft, source, resume, self._prefs.read().compose_html, self.compose_options(),
            settings=self._prefs.settings, schedule_only=schedule_only, editing=editing, taken_back=taken_back,
        )
        window.waiting.connect(self.send_waiting)
        if to:
            window.to.setText(to)
        window.destroyed.connect(lambda: self.composers.remove(window) if window in self.composers else None)
        self.composers.append(window)
        if modal:  # opened from a modal window (Settings): it takes the input until it closes, and that window waits for it
            window.setWindowModality(Qt.WindowModality.ApplicationModal)
        window.show()
        return window

    def compose_in_window(self, kind: str, account: str, folder: str, message_id: str) -> None:
        """Reply, Reply all or Forward from a message window."""
        self._compose(kind, account, folder, message_id)

    def compose_from_selection(self, kind: str) -> None:
        row = self._messages.current_row()
        if row is None or self._state.view.account is None:
            return
        self._compose(kind, self._state.account_of(row), row.folder, row.message_id)

    def _compose(self, kind: str, account: str, folder: str, message_id: str) -> None:
        def drafted(result: ComposeResult) -> None:
            self.open_composer(result.from_account or account, result, (folder, message_id))

        how = COMPOSE_KINDS.get(kind)
        if how is None:
            return
        self._backend.ask(
            MESSAGE_COMPOSE, ComposeParameters(account, folder, message_id, how, QUOTES.get(self._prefs.read().reply_quote, "above")),
            drafted, lambda error: self._status.report("Could not start the message", error), owner=self,
        )

    def edit_draft(self) -> None:
        """Open the selected message of Drafts (or Outbox) in the composer, to continue writing or to send it."""
        row = self._messages.current_row()
        if row is None or self._state.view.account is None or not self._state.in_drafts():
            return
        account, folder, message_id = self._state.account_of(row), row.folder, row.message_id
        if folder == SCHEDULED_FOLDER_PATH:  # it stays scheduled while it is changed: never a draft (an autosave made it one before 1.1.207)

            def listed(result: tuple[ScheduledInfo, ...]) -> None:
                info = next((item for item in result if item.message_id == message_id), None)
                if info is None:
                    self._status.show("This message is not scheduled (any more)", error=True)
                    return
                self.edit_scheduled(info.account, info, None)

            self._backend.ask(MAIL_SCHEDULED, AccountOptional(account), listed, lambda error: self._status.report("Could not open the message", error), owner=self)
            return
        if folder == "Outbox":  # never changed while it waits to be sent: a refresh would send it, then Send a second copy (RV-156)
            self._take_out_of_outbox(account, message_id)
            return
        if self._bring_forward(folder, message_id):
            return

        def opened(result: DraftOpenResult) -> None:
            self.open_composer(account, result, None, resume=(result.folder, result.message_id))

        self._backend.ask(DRAFT_OPEN, DraftOpenParameters(account, message_id, folder), opened, lambda error: self._status.report("Could not open the draft", error), owner=self)

    def _take_out_of_outbox(self, account: str, message_id: str) -> None:
        """A message of the Outbox opened to be changed goes to Drafts first, as Cancel of a delayed send does, and opens from there."""

        def taken_out(result: CancelSendResult) -> None:
            self.left_outbox.emit(message_id)
            self._status.show("Taken out of the Outbox to be changed: it waits in Drafts until you send it")

            def opened(draft: DraftOpenResult) -> None:
                self.open_composer(result.account, draft, None, resume=(draft.folder, draft.message_id))

            self._backend.ask(
                DRAFT_OPEN, DraftOpenParameters(result.account, result.message_id, result.folder), opened,
                lambda error: self._status.report("Could not open the draft", error), owner=self,
            )

        def refused(error: ChronoMailError) -> None:
            if isinstance(error, SentAlready):
                self._status.show(str(error))
                self.sent_already.emit(account, "Outbox", message_id)
                return
            self._status.report("Could not open the message", error)

        self._backend.ask(
            MAIL_CANCEL_SEND, OutboxMessageParameters(account, message_id), taken_out, refused,
            owner=self,
        )

    def open_taken_back(self, account: str, folder: str, message_id: str) -> None:
        """A cancelled send (B-21): the message, a draft again, opens in a message window as one not saved yet (closing asks first)."""

        def opened(result: DraftOpenResult) -> None:
            self.open_composer(account, result, None, resume=(result.folder, result.message_id), taken_back=True)

        self._backend.ask(DRAFT_OPEN, DraftOpenParameters(account, message_id, folder), opened, lambda error: self._status.report("Could not open the message", error), owner=self)

    # --- the size of the text and the look ---------------------------------------------------------------------------

    def writing_points(self, factor: float | None = None) -> float:
        """The text size of the composer: the system's size times the size of the text of messages (reading and writing are one size)."""
        app = QApplication.instance()
        system = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        return round(system * (factor if factor is not None else prefs.saved_reading(self._prefs.settings)), 2)

    def compose_options(self) -> ComposeOptions:
        chosen = self._prefs.read()
        return ComposeOptions(chosen.autosave_seconds, chosen.warn_no_subject, self.writing_points(), chosen.reply_quote == "below")

    def set_text_size(self, factor: float | None = None) -> None:
        """The text of the open windows at this size factor (tried out from Settings), or at the saved one."""
        for composer in self.composers:
            composer.set_text_size(self.writing_points(factor))

    def restyle(self) -> None:
        for window in self.composers:
            window.setStyleSheet(theme.stylesheet(scale.windows()))

    def close_all(self) -> bool:
        """The program ends: every window closes (each asks about what is not saved); False when one of them stays."""
        return all(composer.close() for composer in list(self.composers))
