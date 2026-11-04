import re
from collections.abc import Callable
from dataclasses import replace as changed
from functools import partial
from pathlib import Path

from PySide6.QtCore import QDateTime, QLocale, QModelIndex, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMenu, QMessageBox, QVBoxLayout, QWidget
from shiboken6 import isValid

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.commands_export import MESSAGE_EXPORT, MESSAGE_SOURCE, MESSAGE_TEXT, ExportParameters, SourceResult, TextResult
from chrono_mail.engine.commands_mail import MESSAGE_ATTACHMENT_SAVE, MESSAGE_SHOW, MESSAGE_THREAD, AttachmentSaveParameters, MessageParameters, ShowResult
from chrono_mail.ui import prefs, scale, theme
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_invitations import SeriesClash, INVITATION_ADD, INVITATION_ANSWER, INVITATION_APPLY, INVITATION_BLOCKS, INVITATION_BUSY, INVITATION_CARD, INVITATION_CHOICES, INVITATION_DELEGATE, INVITATION_ORGANIZE, INVITATION_REOPEN, AddParameters, AddResult, Answer, BusyParameters, BusyResult, ChoicesParameters, Organize, OrganizeParameters, OrganizeResult, AnswerParameters, ApplyParameters, ApplyResult, DelegateParameters, DelegateResult, AnswerResult, BlocksParameters, BlocksResult, ChoicesResult, SourceParameters
from chrono_mail.engine.invitations import BLOCKS_AT_ONCE, InvitationInfo
from chrono_mail.engine.commands_attachments import MESSAGE_ATTACHMENT_CLEAN_OPENED, CleanResult
from chrono_mail.ui.attachmentopening import open_attachment
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.composing import Composing
from chrono_mail.ui.conversationbar import ConversationBar
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.mailtags import MailTags
from chrono_mail.ui.messageactions import MessageActions
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.calendarmail import EXPLANATION, MISSING_ORGANIZER, CalendarMailDialog, Choice
from chrono_mail.ui.plainboxes import plain_box
from chrono_mail.ui.prefs import READING_PANES, InUse
from chrono_mail.ui.printing import print_message
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.invitationcalendar import InvitationBusyDialog, InvitationCalendarDialog, InvitationDelegateDialog
from chrono_mail.ui.invitationcard import InvitationCard, adds_all, home_word, item_word
from chrono_mail.ui.invitationwindow import CalendarSource, InvitationWindow, is_calendar_file
from chrono_mail.ui.sourcedialog import SourceDialog
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "readingpane.py 4.4.532"

# --- Version history ----------------------------------------------------
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.529: The clash warning on a series names how many and when; invitations and .ics files up to 1 MB (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.527: a warning before Accept or Tentative when you already have something at that time; a calendar attachment opens as a card by what it is (D-310)
# v4.4.526: results and errors of a card show in the window that was clicked (_tell); Delegate and offline say what really happens (D-310)
# v4.4.525: the question Replace or Skip is gone: Add all skips what is kept (D-310)
# v4.4.524: Add all works in the main window (D-310)
# v4.4.520: the card is shown again when an answer that waited has been sent (D-310)
# v4.4.518: Accept proposal asks whether the attendees are told (D-310)
# v4.4.517: Change my answer asks nothing when no copy is in a calendar (D-310)
# v4.4.516: Change my answer, confirmed first (D-310)
# v4.4.515: the blocks of a big text come as the person scrolls; after a click only that block changes, the place in the list stays (D-310)
# v4.4.514: a window never closes by itself: the person closes it; every dialog of an invite belongs to the main window (D-310)
# v4.4.512: a date added to a series is answered, never added by a button of its own; it asks for no calendar (D-310)
# v4.4.511: after an answer every open window of that mail or file shows the new card; Choose a calendar opens on the main window when the card window is gone, and the answer finishes (D-310)
# v4.4.510: the answer follows the account, no longer the tick of Settings > Calendar (D-310)
# v4.4.508: a text with several items: every button of a block works on its own item, Add all asks for a calendar and a task list and to
#           Replace or Skip what is there already; a card window closes when everything in it is added (D-310)
# v4.4.507: Open attachment on an .ics file, and a calendar file given to the program, show a card in a window of its own (InvitationWindow), wired
#           to the same functions, which take a CalendarSource; Send my busy times… (invitation.busy); a task says task and task list (D-310 step 4)
# v4.4.506: your own meeting: Take their answer, Accept or Decline proposal, Send the latest version (invitation.organize), D-310 step 3
# v4.4.505: Accept, Tentative and Decline on an invitation card, in the reading pane and in every message window: the calendar window when the
#           item is not kept yet, the answer through the engine, the answer mail opened in the composer (D-310)
# v4.3.503: every reader knows the addresses of the person's accounts, so a mailto link to one has no warning (D-215)
# v4.2.479: the reader is given the message's tag names and their colours (small squares under the date)
# v4.2.467: the pane keeps the width the person gave it, whatever the message (D-304); Settings > Reading may let it widen to fit
# v4.2.401: the list and the pane get back the widths or heights they had at a place when the pane returns there
#           (Settings > Message list tries a place, Cancel)
# v3.6.349: an Outbox message that was sent already opens in a message window to be read (RV-181)
# v2.0.302: Open attachment from the attachment row and the pictures (D-236)
# v1.9.274: R-19 round 4: the rows and tags are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: every engine call typed; the answers read by their fields
# v1.9.264: R-17: reading mail as a part of its own (out of ui/mainwindow.py): the reading pane with the conversation bar, the message
#           it shows and marking it as read, where the pane is, the message windows, attachments, links, Save as, View source and Print

SERIES_CLASHES_NAMED: int = 3  # the warning names the first few, the rest as a number
SMALLEST_WIDTH: int = 400  # the reading pane's smallest width while it shows a message and keeps its own width (D-304), at the main window's size factor
PANE_PLACES: tuple[tuple[str, str], ...] = (("right", "At the right"), ("bottom", "At the bottom"), ("off", "Off"))


def series_clash_text(clashes: tuple[SeriesClash, ...]) -> str:
    """The warning before a series goes in: how many of your appointments it meets in the coming year and when, the first few and how many more; "": none."""
    named = [f"“{each.title}” on {QLocale().toString(QDateTime.fromString(each.start, Qt.DateFormat.ISODate).date(), QLocale.FormatType.ShortFormat)}" for each in clashes[:SERIES_CLASHES_NAMED]]
    if not named:
        return ""
    more = f" and {len(clashes) - len(named)} more" if len(clashes) > len(named) else ""
    return f"This series clashes with {len(clashes)} of your appointment{'s' if len(clashes) != 1 else ''}: {', '.join(named)}{more}. Add it anyway?"


_ANSWER_QUESTIONS: dict[str, str] = {"ACCEPTED": "Tell the organizer that you accept?", "TENTATIVE": "Tell the organizer that you accept tentatively?", "DECLINED": "Tell the organizer that you decline?"}
_ORGANIZE_QUESTIONS: dict[str, tuple[str, str]] = {"accept_proposal": ("Accept proposal", "Send the attendees a mail about this change?"), "decline_proposal": ("Decline proposal", "Tell them that you decline the proposal?"),
                                                   "send_latest": ("Send the latest version", "Send them the latest version?")}


class ReadingPane(QObject):
    """The reading pane and the message windows: the owner of what is shown (account, folder, message id) and of where the pane is."""

    def __init__(
        self, backend: Backend, state: MailState, status: StatusReport, prefs_in_use: InUse, messages: MessageListPane, tags: MailTags,
        actions: MessageActions, composing: Composing, content_splitter: PaneSplitter, wide: Callable[[], bool], parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._prefs: InUse = prefs_in_use
        self._messages: MessageListPane = messages
        self._tags: MailTags = tags
        self._actions: MessageActions = actions
        self._composing: Composing = composing
        composing.sent_already.connect(self.open_window_of)  # an Outbox message sent already opens to be read, never as a draft (RV-181)
        self._splitter: PaneSplitter = content_splitter  # the list and the reading pane, side by side or one above the other
        self._wide: Callable[[], bool] = wide  # the list is laid out in columns: the reading pane at the right starts narrower
        self._parent: QWidget = parent
        self.reader: ReaderWidget = ReaderWidget(parent)
        self.conversation_bar: ConversationBar = ConversationBar(parent)
        self.widget: QWidget = QWidget(parent)
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.conversation_bar)
        layout.addWidget(self.reader, 1)
        place = prefs_in_use.read().reading_pane
        self.place: str = place if place in ("right", "bottom", "off") else "right"
        self._place_sizes: dict[str, list[int]] = {}  # per place, the widths or heights the list and the pane had there last
        self.pane_actions: dict[str, QAction] = {}
        self.shown: tuple[str, str, str] | None = None  # account, folder, message id of what the reader shows
        self._show_request: int = 0
        self.message_windows: list[MessageWindow] = []
        self._card_windows: list[tuple[InvitationWindow, CalendarSource]] = []  # the windows of a calendar attachment or file, with what they show
        self.card_windows: list[InvitationWindow] = []  # calendar attachments and files, each a card in a window of its own
        self.mark_read_timer: QTimer = QTimer(self)
        self.mark_read_timer.setSingleShot(True)
        self.mark_read_timer.timeout.connect(self._mark_shown_as_read)
        self.conversation_bar.message_chosen.connect(self._show_conversation_member)
        self.reader.sender_allowed.connect(self.sender_allowed)
        self.reader.attachment_save_requested.connect(self._save_attachment)
        self.reader.attachment_open_requested.connect(self._open_attachment)
        self.reader.link_activated.connect(self.open_link)
        self.reader.link_hovered.connect(status.line.show_link)
        self.reader.invitation_answer_requested.connect(self._answer_in_pane)
        self.reader.card.blocks_wanted.connect(self._more_in_pane)
        self.reader.invitation_change_requested.connect(self._change_in_pane)
        self.reader.invitation_apply_requested.connect(self._apply_in_pane)
        self.reader.invitation_add_all_requested.connect(self._add_all_in_pane)
        self.reader.invitation_delegate_requested.connect(self._delegate_in_pane)
        self.reader.invitation_organize_requested.connect(self._organize_in_pane)
        self.reader.invitation_busy_requested.connect(self._busy_in_pane)
        messages.view.selectionModel().currentChanged.connect(self.row_changed)
        messages.taken_away.connect(self._taken_away)
        messages.conversations_toggled.connect(self.conversation_bar.clear)
        tags.message_tags_changed.connect(self._message_tags_changed)
        self.apply_width_rule()

    # --- what is shown ---------------------------------------------------------------------------------------------

    def clear(self) -> None:
        """Nothing is shown (another view, or the message went)."""
        self.reader.clear()
        self.conversation_bar.clear()
        self.shown = None

    def _taken_away(self, nothing_selected: bool) -> None:
        self._show_request += 1  # the message that is being opened is moved away: its opening no longer counts
        self.mark_read_timer.stop()
        if nothing_selected:
            self.clear()

    def _message_tags_changed(self, message_id: str, tags: tuple[str, ...]) -> None:
        if self.shown is not None and self.shown[2] == message_id and isinstance(tags, tuple):
            self.reader.set_tags(tuple(tag for tag in tags), self._tags.book.colour)

    def row_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self.mark_read_timer.stop()
        if self.place == "off":
            return  # nothing is shown, so nothing is read: a message is opened in a window of its own
        model = self._messages.model
        row = model.row_at(current.row()) if current.isValid() else None
        if row is None or self._state.view.account is None:
            if not self._messages.has_selection() and not self._messages.refetching:  # a refetch selects the same message again
                self.clear()
            return
        folder, message_id = row.folder, row.message_id
        account = self._state.account_of(row)
        if self.shown == (account, folder, message_id):
            self._show_request += 1  # back to the message on screen: the one still opening is called off
            return
        self._show_request += 1
        request = self._show_request

        def shown(result: ShowResult) -> None:
            if request != self._show_request:
                return
            self.shown = (account, folder, message_id)
            self.reader.show_message(result)
            self.reader.set_tags(self._tags.tags_of_message(message_id), self._tags.book.colour)
            if row.thread_size > 1:
                self._load_conversation(account, folder, message_id, request)
            else:
                self.conversation_bar.clear()
            if "seen" not in row.flags and self._prefs.read().mark_read == "delay":
                self.mark_read_timer.start(self._prefs.read().mark_read_delay_ms)

        def failed(error: ChronoMailError) -> None:
            if request == self._show_request:  # an answer for a message that is no longer the one being opened is of no interest
                self._status.report("Could not open the message", error)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), shown, failed, owner=self)

    def show_current(self) -> None:
        """The message selected in the list, shown again (the reading pane came back)."""
        current = self._messages.view.currentIndex()
        if current.isValid():
            self.row_changed(current, QModelIndex())

    def _show_conversation_member(self, folder: str, message_id: str, seen: bool) -> None:
        """A message of the conversation was chosen in the bar: show it in the reading pane."""
        if self._state.view.account is None:
            return
        self.mark_read_timer.stop()
        self._show_request += 1
        request, account = self._show_request, self._state.view.account

        def shown(result: ShowResult) -> None:
            if request != self._show_request:
                return
            self.shown = (account, folder, message_id)
            self.reader.show_message(result)
            self.reader.set_tags(self._tags.tags_of_message(message_id), self._tags.book.colour)
            if not seen and self._prefs.read().mark_read == "delay":
                self.mark_read_timer.start(self._prefs.read().mark_read_delay_ms)

        def failed(error: ChronoMailError) -> None:
            if request == self._show_request:
                self._status.report("Could not open the message", error)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), shown, failed, owner=self)

    def _load_conversation(self, account: str, folder: str, message_id: str, request: int) -> None:
        def arrived(result: tuple[MessageRow, ...]) -> None:
            if request == self._show_request:
                self.conversation_bar.show_thread(list(result), message_id)

        self._backend.ask(MESSAGE_THREAD, MessageParameters(account, folder, message_id), arrived, lambda error: self.conversation_bar.clear(), owner=self)

    def _mark_shown_as_read(self) -> None:
        if self.shown is None:
            return
        account, folder, message_id = self.shown
        self._actions.set_flags([(folder, message_id)], {"seen": True}, account)

    # --- where the pane is --------------------------------------------------------------------------------------------

    def fill_pane_menu(self, menu: QMenu) -> None:
        """View > Reading pane: at the right, at the bottom, or off."""
        for value, label in PANE_PLACES:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.place == value)
            action.triggered.connect(lambda checked=False, chosen=value: self.set_place(chosen))
            self.pane_actions[value] = action

    def set_place(self, where: str) -> None:
        if self.place != "off" and self.widget.isVisible():
            self._place_sizes[self.place] = self._splitter.sizes()  # coming back to this place (Settings Cancel, the menu) gives them again
        self.place = where if where in READING_PANES else "right"
        self._prefs.keep(changed(self._prefs.read(), reading_pane=self.place))
        self.apply_place()

    def apply_place(self) -> None:
        for value, action in self.pane_actions.items():
            action.setChecked(value == self.place)
        if self.place == "off":
            self.widget.hide()
            self.clear()
            return
        self._splitter.setOrientation(Qt.Orientation.Horizontal if self.place == "right" else Qt.Orientation.Vertical)
        self.widget.show()
        self._splitter.setSizes(self._place_sizes.get(self.place) or (([640, 400] if self._wide() else [400, 640]) if self.place == "right" else [320, 380]))
        self.show_current()

    # --- the readers' settings ------------------------------------------------------------------------------------------

    def readers(self) -> list[ReaderWidget]:
        return [self.reader, *(window.reader for window in self.message_windows)]

    def apply_reader_preferences(self) -> None:
        for reader in self.readers():
            self._set_reader_preferences(reader)
        self.restyle_message_windows()

    def take_in_reader_preferences(self) -> None:
        """Settings OK changed what the readers show (remote content, plain text, the size of the text)."""
        for reader in self.readers():
            self._set_reader_preferences(reader)

    def restyle_message_windows(self) -> None:
        for window in self.message_windows:
            window.setStyleSheet(theme.stylesheet(scale.windows()))

    def apply_scale(self) -> None:
        for reader in self.readers():
            reader.apply_scale()
        self.apply_width_rule()  # the smallest width follows the main window's size factor

    def apply_width_rule(self) -> None:
        """The pane keeps the width the person gave it with the separator: while a message is shown its smallest width is a fixed one,
        and the message never sets it (D-304). With Settings > Reading's "Widen the reading pane to fit the message", what the message
        needs sets it, as before."""
        fits = self._prefs.read().reading_pane_fits_message
        self.reader.set_width_rule(0 if fits else scale.px(SMALLEST_WIDTH))

    def try_reading_scale(self, factor: float) -> None:
        for reader in self.readers():
            reader.set_zoom(round(factor * 100))

    def _set_reader_preferences(self, reader: ReaderWidget) -> None:
        chosen = self._prefs.read()
        reader.always_remote = chosen.remote_always
        reader.allowed_senders = set(chosen.remote_senders)
        reader.prefer_plain = chosen.prefer_plain
        reader.account_addresses = self._account_addresses
        reader.set_zoom(round(prefs.saved_reading(self._prefs.settings) * 100))

    def _account_addresses(self) -> frozenset[str]:
        """The addresses of the person's accounts, in lower case, as they are now (an account added later counts at once)."""
        return frozenset(address for account in self._state.all_accounts if (address := account.email_address.strip().lower()))

    def prepare_message_reader(self, reader: ReaderWidget) -> None:
        """A message window: the settings first, and its Always for this sender saves like the one in the reading pane."""
        self._set_reader_preferences(reader)
        reader.sender_allowed.connect(self.sender_allowed)

    def sender_allowed(self, address: str) -> None:
        chosen = self._prefs.read()
        self._prefs.keep(changed(chosen, remote_senders=tuple(sorted(set(chosen.remote_senders) | {address.lower()}))))

    # --- the selected message ----------------------------------------------------------------------------------------

    def selected_message(self) -> tuple[str, str, str, str] | None:
        """(account, folder, message id, subject) of the message in the list, if there is one."""
        row = self._messages.current_row()
        if row is None or self._state.view.account is None:
            return None
        return self._state.account_of(row), row.folder, row.message_id, row.subject

    def open_message_window(self) -> None:
        """Enter or a double click on a message that is not a draft: read it in a window of its own."""
        chosen = self.selected_message()
        if chosen is not None:
            self.open_window_of(*chosen[:3])

    def open_window_of(self, account: str, folder: str, message_id: str) -> None:
        """This message read in a window of its own (also when the selection moved meanwhile)."""

        def shown(result: ShowResult) -> None:
            window = MessageWindow(self._backend, account, folder, message_id, result, self._composing.compose_in_window, self.open_link, self.prepare_message_reader, self._prefs.settings)
            reader = window.findChild(ReaderWidget)
            if reader is not None:
                found: ReaderWidget = reader
                place = CalendarSource(account, folder, message_id)
                found.card.blocks_wanted.connect(partial(self._more_blocks, found.card, found, place))

                def on_block() -> CalendarSource:
                    return changed(place, item=found.pressed_item)

                def answer_in_window(answer: str) -> None:
                    self.answer_invitation(found, on_block(), answer)

                reader.invitation_answer_requested.connect(answer_in_window)

                def apply_in_window() -> None:
                    self.apply_invitation(found, on_block())

                reader.invitation_apply_requested.connect(apply_in_window)

                def delegate_in_window() -> None:
                    self.delegate_invitation(found, on_block())

                reader.invitation_delegate_requested.connect(delegate_in_window)

                def change_in_window() -> None:
                    self.change_answer(found, on_block())

                reader.invitation_change_requested.connect(change_in_window)

                def organize_in_window(action: str) -> None:
                    self.organize_invitation(found, on_block(), action)

                reader.invitation_organize_requested.connect(organize_in_window)

                def busy_in_window() -> None:
                    self.busy_invitation(found, on_block())

                reader.invitation_busy_requested.connect(busy_in_window)

                def add_all_in_window() -> None:
                    self.add_all_invitation(found, place)

                reader.invitation_add_all_requested.connect(add_all_in_window)

            def calendar_in_window(position: int, name: str) -> None:
                self.open_calendar_card(CalendarSource(account, folder, message_id, part=position), name, where=window)

            window.calendar_open_requested.connect(calendar_in_window)
            self.message_windows.append(window)
            window.destroyed.connect(lambda: self.message_windows.remove(window) if window in self.message_windows else None)
            window.show()
            if self._prefs.read().mark_read != "never":
                self._actions.set_flags([(folder, message_id)], {"seen": True}, account)
            self.apply_reader_preferences()

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), shown, lambda error: self._status.report("Could not open the message", error), owner=self)

    # --- invitations (D-310) ------------------------------------------------------------------------------------------

    def _tell(self, where: QWidget, text: str, error: ChronoMailError | None = None) -> None:
        """A result or error of a card shows in the window that was clicked, not behind it; the main window's line for the reading pane, and when that window has closed meanwhile."""
        window = where.window() if isValid(where) else None
        if isinstance(window, QMainWindow) and window is not self._parent.window():
            window.statusBar().showMessage(text if error is None else f"{text}: {error}", 8000)
        elif error is None:
            self._status.show(text)
        else:
            self._status.report(text, error)

    def _in_pane(self) -> CalendarSource | None:
        """The message in the reading pane, as the source of its card."""
        return CalendarSource(*self.shown, item=self.reader.pressed_item) if self.shown is not None else None

    def _change_in_pane(self) -> None:
        self.change_answer(self.reader, self._in_pane())

    def change_answer(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None) -> None:
        """Change my answer: after the person confirmed, the appointment goes and the invitation asks for an answer again (D-310)."""
        info = self._card_of(reader, place) if place is not None else None
        if place is None or info is None:
            return
        what, home = ("task", "task list") if info.kind == "task" else ("appointment", "calendar")
        text = f"Changing the status of the invitation will remove the {what} from your {home} until you make a new choice."
        box = plain_box(QMessageBox.Icon.Question, self._parent, "Change my answer", text, QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if not info.answered_on_mail and info.your_answer != "DECLINED" and QMessageBox.StandardButton(box.exec()) != QMessageBox.StandardButton.Ok:  # no copy: nothing leaves your calendar, so nothing to ask
            return
        parameters = SourceParameters(place.account, place.folder, place.message, part=place.part, file=place.file, item=place.item)
        self._backend.ask(INVITATION_REOPEN, parameters, lambda _: self._show_again(reader, place), lambda error: self._tell(reader, "Could not change your answer", error), owner=self)

    def _more_in_pane(self, first: int) -> None:
        if self.shown is not None:
            self._more_blocks(self.reader.card, self.reader, CalendarSource(*self.shown), first)

    def _more_blocks(self, card: InvitationCard, owner: QObject, source: CalendarSource, first: int) -> None:
        """The next blocks of a card, as the person scrolls near them (D-310)."""
        showing = card.showing

        def got(result: BlocksResult) -> None:
            if isValid(card) and card.showing == showing:  # (another message may have come)
                card.add_blocks(first, result.parts)

        def failed(error: ChronoMailError) -> None:
            self._tell(card, "Could not read the rest of the items", error)
            if isValid(card) and card.showing == showing:
                card.blocks_failed()

        parameters = BlocksParameters(source.account, source.folder, source.message, part=source.part, file=source.file, first=first, count=BLOCKS_AT_ONCE)
        self._backend.ask(INVITATION_BLOCKS, parameters, got, failed, owner=owner)

    def _renew_blocks(self, card: InvitationCard, owner: QObject, source: CalendarSource, item: int) -> bool:
        """A card with several items takes only what changed, so the person keeps their place in the list (D-310). False: it has none."""
        info = card.info
        if info is None or not info.parts:
            return False
        showing = card.showing

        def got(result: BlocksResult) -> None:
            if isValid(card) and card.showing == showing:
                card.update_blocks(result.parts, result.left)

        first, count = (item, 1) if item >= 0 else (0, len(info.parts))
        parameters = BlocksParameters(source.account, source.folder, source.message, part=source.part, file=source.file, first=first, count=count)
        self._backend.ask(INVITATION_BLOCKS, parameters, got, lambda error: None, owner=owner)
        return True

    @staticmethod
    def _card_of(reader: ReaderWidget | InvitationWindow, place: CalendarSource) -> InvitationInfo | None:
        """The card of the item meant: of a text with several items, that item's own."""
        info = reader.invitation
        return next((each for each in info.parts if each.item == place.item), info) if info is not None else None

    @staticmethod
    def _parameters(place: CalendarSource) -> ChoicesParameters:
        return ChoicesParameters(place.account, place.folder, place.message, part=place.part, file=place.file, event=place.event, occurrence=place.occurrence, item=place.item)

    def open_calendar_file(self, path: str, failed: Callable[[], None] | None = None) -> None:
        """A calendar file on this computer as a card; `failed` is called when it cannot be shown."""
        self.open_calendar_card(CalendarSource(file=path), Path(path).name, failed)

    def open_calendar_card(self, source: CalendarSource, title: str, failed: Callable[[], None] | None = None, where: QWidget | None = None) -> None:
        """A calendar attachment or file as a card in a window of its own. What cannot be shown is said in the status line, never handed to the
        system's program: Chrono Mail may be that program (D-310 step 4)."""

        def shown(info: InvitationInfo) -> None:
            window = InvitationWindow(info, title, self.open_link, self._prefs.settings)
            self._card_windows = [*(each for each in self._card_windows if isValid(each[0])), (window, source)]

            def on_block() -> CalendarSource:
                return changed(source, item=window.card.pressed_item)

            def answer_here(answer: str) -> None:
                self.answer_invitation(window, on_block(), answer)

            def apply_here() -> None:
                self.apply_invitation(window, on_block())

            def delegate_here() -> None:
                self.delegate_invitation(window, on_block())

            def organize_here(action: str) -> None:
                self.organize_invitation(window, on_block(), action)

            def busy_here() -> None:
                self.busy_invitation(window, on_block())

            window.card.answer_requested.connect(answer_here)
            window.card.apply_requested.connect(apply_here)
            window.card.delegate_requested.connect(delegate_here)

            def change_here() -> None:
                self.change_answer(window, on_block())

            window.card.change_requested.connect(change_here)
            window.card.organize_requested.connect(organize_here)
            window.card.busy_requested.connect(busy_here)

            def add_all_here() -> None:
                self.add_all_invitation(window, source)

            window.card.add_all_requested.connect(add_all_here)
            window.card.blocks_wanted.connect(partial(self._more_blocks, window.card, window, source))
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.card_windows.append(window)
            window.destroyed.connect(lambda: self.card_windows.remove(window) if window in self.card_windows else None)
            window.show()
            window.raise_()
            window.activateWindow()

        def refused(error: ChronoMailError) -> None:
            self._tell(where or self._parent, "Could not show this calendar file", error)
            if failed is not None:
                failed()

        self._backend.ask(INVITATION_CARD, SourceParameters(source.account, source.folder, source.message, part=source.part, file=source.file), shown, refused, owner=self)

    def _busy_in_pane(self) -> None:
        self.busy_invitation(self.reader, self._in_pane())

    def busy_invitation(self, reader: ReaderWidget | InvitationWindow, source: CalendarSource | None) -> None:
        """Send my busy times…: the calendars they are made from, then the question whether to send them (D-310 step 4)."""
        if source is None:
            return

        def listed(choices: ChoicesResult) -> None:
            dialog = InvitationBusyDialog(choices, self._parent)
            if dialog.exec() == InvitationBusyDialog.DialogCode.Accepted and dialog.chosen:
                question = self._mail_dialog("Send my busy times", "Send your busy times?")
                if question.ask() is not Choice.SEND:
                    return

                def made(result: BusyResult) -> None:
                    self._tell(reader, "Your busy times are on their way.")
                    self._composing.send_calendar_mail(result.account, result.folder, result.message, question.note)

                parameters = BusyParameters(source.account, source.folder, source.message, part=source.part, file=source.file, item=source.item, calendars=dialog.chosen)
                self._backend.ask(INVITATION_BUSY, parameters, made, lambda error: self._tell(reader, "Could not make your busy times", error), owner=self)

        self._backend.ask(INVITATION_CHOICES, self._parameters(source), listed, lambda error: self._tell(reader, "Could not read the request", error), owner=self)

    def _organize_in_pane(self, action: str) -> None:
        self.organize_invitation(self.reader, self._in_pane(), action)

    def organize_invitation(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None, action: str) -> None:
        """Your own meeting (D-310 step 3): an attendee's answer into it, a proposal accepted or declined, the latest version sent; a mail to
        the attendees or the proposer goes after the question whether to send it."""
        if place is None or action not in ("take", "accept_proposal", "decline_proposal", "send_latest"):
            return
        info = self._card_of(reader, place)
        item = item_word(info) if info is not None else "meeting"
        chosen: Organize = "take" if action == "take" else ("accept_proposal" if action == "accept_proposal" else ("decline_proposal" if action == "decline_proposal" else "send_latest"))

        note = ""

        def organized(result: OrganizeResult) -> None:
            mailed = result.draft_message is not None
            words = {"taken": f"Their answer is in your {item}.", "moved": f"Your {item} follows the proposal.", "declined": "Your answer to the proposal is on its way." if mailed else "You declined the proposal.",
                     "sent": "The latest version is on its way." if mailed else "Nothing was sent."}
            mailer = " The calendar server tells the attendees." if result.server_mails and result.done == "moved" else ""
            self._tell(reader, words.get(result.done, "Nothing was sent.") + mailer)
            if result.draft_message is not None and result.draft_folder is not None and result.draft_account is not None:
                self._composing.send_calendar_mail(result.draft_account, result.draft_folder, result.draft_message, note, lambda: self._show_again(reader, place))
            self._show_again(reader, place)

        def refused(error: ChronoMailError) -> None:
            self._tell(reader, f"Could not change your {item}", error)

        parameters = OrganizeParameters(place.account, place.folder, place.message, part=place.part, file=place.file, item=place.item, action=chosen)

        def checked(check: OrganizeResult) -> None:
            nonlocal note, parameters
            if check.chrono:
                title, question = _ORGANIZE_QUESTIONS[chosen]
                dialog = self._mail_dialog(title, question)
                choice = dialog.ask()
                if choice is Choice.BACK:
                    return
                note, parameters = dialog.note, changed(parameters, mail=choice is Choice.SEND)
            self._backend.ask(INVITATION_ORGANIZE, parameters, organized, refused, owner=self)

        self._backend.ask(INVITATION_ORGANIZE, changed(parameters, dry=True), checked, refused, owner=self)

    def _delegate_in_pane(self) -> None:
        self.delegate_invitation(self.reader, self._in_pane())

    def delegate_invitation(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None) -> None:
        """Delegate…: the address of who goes in your place, then the question whether the invitation for them and your answer to the organizer are sent."""
        if place is None:
            return
        info = self._card_of(reader, place) if not place.event else None
        dialog = InvitationDelegateDialog(self._parent, task=info is not None and info.kind == "task")
        if dialog.exec() != InvitationDelegateDialog.DialogCode.Accepted:
            return
        question = self._mail_dialog("Delegate", f"Send {dialog.address} the invitation, and tell the organizer?")
        choice = question.ask()
        if choice is Choice.BACK:
            return

        def delegated(result: DelegateResult) -> None:
            told = " The calendar server tells the organizer." if result.server_answers else " Nothing goes to the organizer: confirmation emails are off for this account."
            if not result.request_message:
                self._tell(reader, "You passed the invitation on. Nobody got a mail.")
            else:
                self._tell(reader, "The invitation for them and your answer to the organizer are on their way." if result.answer_message else f"The invitation for them is on its way.{told}")
                self._composing.send_calendar_mail(result.account, result.folder, result.request_message, question.note, lambda: self._show_again(reader, place))
            if result.answer_message:  # "": the calendar's server mails your answer
                self._composing.send_calendar_mail(result.account, result.folder, result.answer_message, question.note, lambda: self._show_again(reader, place))
            self._show_again(reader, place)

        self._backend.ask(INVITATION_DELEGATE, DelegateParameters(place.account, place.folder, place.message, part=place.part, file=place.file, event=place.event, occurrence=place.occurrence, item=place.item, to=dialog.address, mail=choice is Choice.SEND), delegated,
                          lambda error: self._tell(reader, "Could not pass the invitation on", error), owner=self)

    def _apply_in_pane(self) -> None:
        self.apply_invitation(self.reader, self._in_pane())

    def apply_invitation(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None) -> None:
        """The organizer's change into your calendar (D-310); a request for the latest version goes after the question whether to send it."""
        if place is None:
            return
        info = self._card_of(reader, place)
        home = home_word(info) if info is not None else "calendar"

        note = ""

        def applied(result: ApplyResult) -> None:
            words = {"updated": f"Your {home} has the new version.", "removed": f"Removed from your {home}.", "asked": "Your request to resend the original invite is on its way."}
            self._tell(reader, words.get(result.done, "Done."))
            if result.draft_message is not None and result.draft_folder is not None and result.draft_account is not None:
                self._composing.send_calendar_mail(result.draft_account, result.draft_folder, result.draft_message, note, lambda: self._show_again(reader, place))
            self._show_again(reader, place)

        def refused(error: ChronoMailError) -> None:
            self._tell(reader, f"Could not change your {home}", error)

        parameters = carried(self._parameters(place), ApplyParameters)

        def checked(check: ApplyResult) -> None:
            nonlocal note
            if check.done == "asked":  # the mail is all this does: without it nothing happens
                dialog = self._mail_dialog("Ask for the invitation", "Ask the organizer to send the original invite again?")
                if dialog.ask() is not Choice.SEND:
                    return
                note = dialog.note
            self._backend.ask(INVITATION_APPLY, parameters, applied, refused, owner=self)

        self._backend.ask(INVITATION_APPLY, changed(parameters, dry=True), checked, refused, owner=self)

    def _refresh_others(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource) -> None:
        """Every other open window of that mail or file says what your calendar holds now (D-310): the reading pane, message windows, card windows."""
        where = (place.account, place.folder, place.message)
        for window, source in self._card_windows:
            if isValid(window) and window is not reader and (source.account, source.folder, source.message, source.file) == (*where, place.file):
                if self._renew_blocks(window.card, window, source, place.item if source.part == place.part else -1):
                    continue
                self._backend.ask(INVITATION_CARD, SourceParameters(source.account, source.folder, source.message, part=source.part, file=source.file), window.show_card, lambda error: None, owner=window)
        views = [self.reader] if self.shown == where else []
        views += [window.reader for window in self.message_windows if window.where == where]
        views = [view for view in views if view is not reader and not self._renew_blocks(view.card, view, CalendarSource(*where), place.item if place.part < 0 else -1)]
        if not place.message or not views:
            return

        def again(shown: ShowResult) -> None:
            for view in views:
                if view is self.reader and self.shown != where:
                    continue  # another message was chosen meanwhile: it stays on screen
                if isValid(view) and view.invitation is not None and shown.invitation is not None and view.invitation.uid == shown.invitation.uid:
                    view.show_message(shown)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(*where), again, lambda error: None, owner=self)

    def _show_again(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource) -> None:
        """The message shown again in its reader, when it still shows it: the card says what your calendar holds now. A window stays open: it is
        always the person who closes it (D-310)."""
        account, folder, message_id = place.account, place.folder, place.message
        self._refresh_others(reader, place)
        if not isValid(reader):
            return  # closed meanwhile: there is nothing to show it in
        if isinstance(reader, InvitationWindow):  # (a file gone meanwhile: its card stays as it is)
            if self._renew_blocks(reader.card, reader, place, place.item):
                return
            self._backend.ask(INVITATION_CARD, SourceParameters(account, folder, message_id, part=place.part, file=place.file), reader.show_card, lambda error: None, owner=reader)
            return
        if reader is self.reader and self.shown != (account, folder, message_id):
            return  # another message was chosen meanwhile: it stays on screen
        if self._renew_blocks(reader.card, reader, place, place.item):
            return
        uid = reader.invitation.uid if reader.invitation is not None else ""

        def again(shown: ShowResult) -> None:
            if not isValid(reader):
                return  # its window was closed meanwhile
            if reader is self.reader and self.shown != (account, folder, message_id):
                return  # another message was chosen meanwhile: it stays on screen
            if reader.invitation is not None and reader.invitation.uid == uid and shown.invitation is not None and shown.invitation.uid == uid:
                reader.show_message(shown)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), again, lambda error: None, owner=self)

    def _answer_in_pane(self, answer: str) -> None:
        self.answer_invitation(self.reader, self._in_pane(), answer)

    def answer_invitation(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None, answer: str) -> None:
        """Accept, Tentative, Decline (or Add to calendar) on the card of the message in this reader: an item not kept yet asks for its calendar
        first; the answer mail, when Chrono Mail makes it, opens in the composer and goes only with Send."""
        info = self._card_of(reader, place) if place is not None else None
        if place is None or info is None:
            return
        if answer == "DECLINED":
            self._send_answer(reader, place, answer, "")
            return
        self._choose_and_answer(reader, place, answer, info)

    def answer_event(self, event: str, occurrence: str, answer: str) -> None:
        """Accept or Tentative on a hidden event in the calendar: as on a card, and it shows in the calendar when it is done (D-310)."""
        self._choose_and_answer(self.reader, CalendarSource(event=event, occurrence=occurrence), answer, None)

    def delegate_event(self, event: str, occurrence: str) -> None:
        self.delegate_invitation(self.reader, CalendarSource(event=event, occurrence=occurrence))

    def _choose_and_answer(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource, answer: str, info: InvitationInfo | None) -> None:
        def listed(choices: ChoicesResult) -> None:
            if choices.clashes and not self._add_anyway(choices, "Tentative" if answer == "TENTATIVE" else "Accept"):
                return
            if choices.stored or (info is not None and info.method == "ADD"):  # your calendar holds it now (asked afresh: it may have gone since the card was shown; an added date never goes elsewhere)
                self._send_answer(reader, place, answer, "", info)
                return
            dialog = InvitationCalendarDialog(choices, task=info is not None and info.kind == "task", parent=self._parent)
            if dialog.exec() == InvitationCalendarDialog.DialogCode.Accepted and dialog.chosen:
                self._send_answer(reader, place, answer, dialog.chosen, info)

        self._backend.ask(INVITATION_CHOICES, self._parameters(place), listed, lambda error: self._tell(reader, "Could not read the invitation", error), owner=self)

    def _add_anyway(self, choices: ChoicesResult, title: str) -> bool:
        """You already have something at that time: the person says whether it goes in all the same. No leaves the invitation as it is."""
        clashes = choices.clashes
        have = f'"{clashes[0]}"' if len(clashes) == 1 else f"{len(clashes)} appointments"
        box = plain_box(QMessageBox.Icon.Question, self._parent, title, series_clash_text(choices.series_clashes) or f"You already have {have} at this time. Add it anyway?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        return QMessageBox.StandardButton(box.exec()) == QMessageBox.StandardButton.Yes

    def _send_answer(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource, answer: str, calendar: str, card: InvitationInfo | None = None) -> None:
        chosen: Answer = "DECLINED" if answer == "DECLINED" else ("TENTATIVE" if answer == "TENTATIVE" else "ACCEPTED")
        parameters = AnswerParameters(place.account, place.folder, place.message, part=place.part, file=place.file, event=place.event, occurrence=place.occurrence, item=place.item, answer=chosen, calendar=calendar)

        info = card or (self._card_of(reader, place) if isValid(reader) and not place.event else None)  # card: as it was asked, the window may be closed by now
        shared = info is not None and info.method == "PUBLISH"
        home = home_word(info) if info is not None else "calendar"

        note = ""

        def answered(result: AnswerResult) -> None:
            words = {"ACCEPTED": "accepted", "TENTATIVE": "accepted tentatively", "DECLINED": "declined"}[result.answer]
            where = f" It is in {result.stored_in}." if result.stored_in else ""
            mailer = (" The calendar server sends your answer when the connection is back." if result.waiting else " The calendar server sends your answer.") if result.server_answers else ""
            self._tell(reader, f"Added to your {home}.{where}" if shared else f"You {words} the invitation.{where}{mailer}")
            if result.draft_message is not None and result.draft_folder is not None and result.draft_account is not None:
                self._composing.send_calendar_mail(result.draft_account, result.draft_folder, result.draft_message, note, lambda: self._show_again(reader, place))
            self._show_again(reader, place)  # the card says what your calendar holds now

        def refused(error: ChronoMailError) -> None:
            self._tell(reader, "Could not answer the invitation", error)

        def checked(check: AnswerResult) -> None:
            nonlocal note
            chosen_parameters = parameters
            if check.chrono:
                dialog = self._mail_dialog("Missing organizer", MISSING_ORGANIZER, check.sender) if check.no_organizer else self._mail_dialog("Send your answer", _ANSWER_QUESTIONS[chosen])
                choice = dialog.ask()
                if choice is Choice.BACK:
                    return
                note = dialog.note
                chosen_parameters = changed(parameters, mail=choice is Choice.SEND, to=dialog.address if check.no_organizer and choice is Choice.SEND else "")
            self._backend.ask(INVITATION_ANSWER, chosen_parameters, answered, refused, owner=self)

        self._backend.ask(INVITATION_ANSWER, changed(parameters, dry=True), checked, refused, owner=self)

    def _mail_dialog(self, title: str, question: str, address: str | None = None) -> CalendarMailDialog:
        """The one question of every calendar mail; as every dialog of an invitation it belongs to the main window (the action outlives the message window)."""
        return CalendarMailDialog(title, question, EXPLANATION, self._parent, address)

    def _add_all_in_pane(self) -> None:
        self.add_all_invitation(self.reader, self._in_pane())

    def add_all_invitation(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource | None) -> None:
        """Add all: the calendar for the events and the task list for the tasks the text holds, then every item that is in none of your calendars yet (D-310)."""
        info = reader.invitation
        if place is None or info is None or not adds_all(info):
            return

        def chosen_for(task: bool, then: Callable[[str], None]) -> None:
            def listed(choices: ChoicesResult) -> None:
                dialog = InvitationCalendarDialog(choices, task=task, parent=self._parent)
                if dialog.exec() == InvitationCalendarDialog.DialogCode.Accepted and dialog.chosen:
                    then(dialog.chosen)

            parameters = ChoicesParameters(place.account, place.folder, place.message, part=place.part, file=place.file, kind="task" if task else "event")
            self._backend.ask(INVITATION_CHOICES, parameters, listed, lambda error: self._tell(reader, "Could not read the items", error), owner=self)

        def tasks_for(calendar: str) -> None:
            if info.tasks:
                chosen_for(True, lambda task_list: self._add_items(reader, place, calendar, task_list))
            else:
                self._add_items(reader, place, calendar, "")

        if info.events:
            chosen_for(False, tasks_for)
        else:
            tasks_for("")

    def _add_items(self, reader: ReaderWidget | InvitationWindow, place: CalendarSource, calendar: str, task_list: str) -> None:
        def added(result: AddResult) -> None:
            done = f"Added {result.added} {'item' if result.added == 1 else 'items'}."
            self._tell(reader, done if not result.skipped else f"{done} Skipped {result.skipped} that {'was' if result.skipped == 1 else 'were'} there already.")
            self._show_again(reader, place)

        parameters = AddParameters(place.account, place.folder, place.message, part=place.part, file=place.file, calendar=calendar, task_list=task_list)
        self._backend.ask(INVITATION_ADD, parameters, added, lambda error: self._tell(reader, "Could not add the items", error), owner=self)

    def _save_attachment(self, position: int, suggested: str) -> None:
        if self.shown is None:
            return
        account, folder, message_id = self.shown
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(self._parent, "Save attachment", start)
        if not path:
            return
        self._backend.ask(
            MESSAGE_ATTACHMENT_SAVE, AttachmentSaveParameters(account, folder, message_id, position, path, overwrite=True),
            lambda result: self._status.show(f"Saved {result.path}"),
            lambda error: self._status.report("Could not save the attachment", error),
            owner=self,
        )

    def forget_opened_copies(self) -> None:
        """At start: the copies of attachments opened in an earlier run go; nothing to tell either way (D-236)."""
        self._backend.ask(MESSAGE_ATTACHMENT_CLEAN_OPENED, NoParameters(), self._copies_forgotten, self._copies_forgotten, owner=self)

    def _copies_forgotten(self, outcome: CleanResult | ChronoMailError) -> None:
        pass  # a copy another program still holds stays until the next start

    def _open_attachment(self, position: int, name: str) -> None:
        if self.shown is not None and (is_calendar_file(name) or position in self.reader.calendar_parts):
            self.open_calendar_card(CalendarSource(*self.shown, part=position), name)
        elif self.shown is not None:
            open_attachment(self._parent, self._backend, self.shown, position, name, self._open_failed, self)

    def _open_failed(self, text: str, error: ChronoMailError | None) -> None:
        if error is None:
            self._status.show(text, error=True)
        else:
            self._status.report(text, error)

    def save_selected_as(self) -> None:
        """Message > Save as: a copy of the message as an .eml file."""
        chosen = self.selected_message()
        if chosen is None:
            return
        account, folder, message_id, subject = chosen
        name = re.sub(r"[^\w\- ]+", "", subject).strip()[:60] or "message"
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / f"{name}.eml")
        path, _ = QFileDialog.getSaveFileName(self._parent, "Save message as", start, "E-mail messages (*.eml);;All files (*)")
        if not path:
            return
        self._backend.ask(
            MESSAGE_EXPORT, ExportParameters(account, folder, message_id, path, overwrite=True),
            lambda result: self._status.show(f"Saved {result.path}"),
            lambda error: self._status.report("Could not save the message", error), owner=self,
        )

    def view_source(self) -> None:
        chosen = self.selected_message()
        if chosen is None:
            return
        account, folder, message_id, subject = chosen

        def shown(result: SourceResult) -> None:
            SourceDialog(subject, result, self._parent).exec()

        self._backend.ask(MESSAGE_SOURCE, MessageParameters(account, folder, message_id), shown, lambda error: self._status.report("Could not read the message", error), owner=self)

    def print_selected(self) -> None:
        chosen = self.selected_message()
        if chosen is None:
            return
        account, folder, message_id, _ = chosen

        def ready(result: TextResult) -> None:
            if print_message(self._parent, result):
                self._status.show("Sent to the printer")

        self._backend.ask(MESSAGE_TEXT, MessageParameters(account, folder, message_id), ready, lambda error: self._status.report("Could not print the message", error), owner=self)

    def open_link(self, url: QUrl) -> None:
        if url.scheme() == "mailto":
            if self._state.view.account is not None:
                self._composing.open_composer(self._state.view.account, None, None, to=url.path())
            return
        if url.scheme() not in ("http", "https"):
            return
        if not self._prefs.read().links_ask:
            QDesktopServices.openUrl(url)
            return
        answer = QMessageBox.question(self._parent, "Open link", f"Open this link in your web browser?\n\n{url.toString()}\n\nLinks in e-mail can lead to harmful sites. Only open it if you trust the sender.")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

