import re
from collections.abc import Callable
from dataclasses import replace as changed
from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.commands_export import MESSAGE_EXPORT, MESSAGE_SOURCE, MESSAGE_TEXT, ExportParameters, SourceResult, TextResult
from chrono_mail.engine.commands_mail import MESSAGE_ATTACHMENT_SAVE, MESSAGE_SHOW, MESSAGE_THREAD, AttachmentSaveParameters, MessageParameters, ShowResult
from chrono_mail.ui import prefs, scale, theme
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_invitations import INVITATION_ANSWER, INVITATION_APPLY, INVITATION_CHOICES, INVITATION_DELEGATE, Answer, AnswerParameters, ApplyResult, DelegateParameters, DelegateResult, AnswerResult, ChoicesResult
from chrono_mail.engine.commands_attachments import MESSAGE_ATTACHMENT_CLEAN_OPENED, CleanResult
from chrono_mail.ui.attachmentopening import open_attachment
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.composing import Composing
from chrono_mail.ui.conversationbar import ConversationBar
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.mailtags import MailTags
from chrono_mail.ui.messageactions import MessageActions
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.prefs import READING_PANES, InUse
from chrono_mail.ui.printing import print_message
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.invitationcalendar import InvitationCalendarDialog, InvitationDelegateDialog
from chrono_mail.ui.sourcedialog import SourceDialog
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "readingpane.py 4.4.505"

# --- Version history ----------------------------------------------------
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

SMALLEST_WIDTH: int = 400  # the reading pane's smallest width while it shows a message and keeps its own width (D-304), at the main window's size factor
PANE_PLACES: tuple[tuple[str, str], ...] = (("right", "At the right"), ("bottom", "At the bottom"), ("off", "Off"))


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
        self.reader.invitation_apply_requested.connect(self._apply_in_pane)
        self.reader.invitation_delegate_requested.connect(self._delegate_in_pane)
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
                place = (account, folder, message_id)

                def answer_in_window(answer: str) -> None:
                    self.answer_invitation(found, place, answer)

                reader.invitation_answer_requested.connect(answer_in_window)

                def apply_in_window() -> None:
                    self.apply_invitation(found, place)

                reader.invitation_apply_requested.connect(apply_in_window)

                def delegate_in_window() -> None:
                    self.delegate_invitation(found, place)

                reader.invitation_delegate_requested.connect(delegate_in_window)
            self.message_windows.append(window)
            window.destroyed.connect(lambda: self.message_windows.remove(window) if window in self.message_windows else None)
            window.show()
            if self._prefs.read().mark_read != "never":
                self._actions.set_flags([(folder, message_id)], {"seen": True}, account)
            self.apply_reader_preferences()

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), shown, lambda error: self._status.report("Could not open the message", error), owner=self)

    # --- invitations (D-310) ------------------------------------------------------------------------------------------

    def _delegate_in_pane(self) -> None:
        self.delegate_invitation(self.reader, self.shown)

    def delegate_invitation(self, reader: ReaderWidget, place: tuple[str, str, str] | None) -> None:
        """Delegate…: the address of who goes in your place, then the invitation for them and your answer to the organizer in the composer."""
        if place is None:
            return
        dialog = InvitationDelegateDialog(reader.window())
        if dialog.exec() != InvitationDelegateDialog.DialogCode.Accepted:
            return
        account, folder, message_id = place

        def delegated(result: DelegateResult) -> None:
            self._status.show("The invitation for them and your answer to the organizer are ready to send.")
            self._composing.open_draft(result.account, result.folder, result.request_message)
            if result.answer_message:  # "": the calendar's server mails your answer
                self._composing.open_draft(result.account, result.folder, result.answer_message)
            self._show_again(reader, place)

        self._backend.ask(INVITATION_DELEGATE, DelegateParameters(account, folder, message_id, to=dialog.address), delegated,
                          lambda error: self._status.report("Could not pass the invitation on", error), owner=self)

    def _apply_in_pane(self) -> None:
        self.apply_invitation(self.reader, self.shown)

    def apply_invitation(self, reader: ReaderWidget, place: tuple[str, str, str] | None) -> None:
        """The organizer's change into your calendar (D-310); a request for the latest version opens in the composer."""
        if place is None:
            return
        account, folder, message_id = place

        def applied(result: ApplyResult) -> None:
            words = {"updated": "Your calendar has the new version.", "removed": "Removed from your calendar.", "added": "Added to your calendar.",
                     "asked": "Your request for the latest version is ready to send."}
            self._status.show(words.get(result.done, "Done."))
            if result.draft_message is not None and result.draft_folder is not None and result.draft_account is not None:
                self._composing.open_draft(result.draft_account, result.draft_folder, result.draft_message)
            self._show_again(reader, place)

        self._backend.ask(INVITATION_APPLY, MessageParameters(account, folder, message_id), applied,
                          lambda error: self._status.report("Could not change your calendar", error), owner=self)

    def _show_again(self, reader: ReaderWidget, place: tuple[str, str, str]) -> None:
        """The message shown again in its reader, when it still shows it: the card says what your calendar holds now."""
        account, folder, message_id = place
        uid = reader.invitation.uid if reader.invitation is not None else ""

        def again(shown: ShowResult) -> None:
            if reader is self.reader and self.shown != place:
                return  # another message was chosen meanwhile: it stays on screen
            if reader.invitation is not None and reader.invitation.uid == uid and shown.invitation is not None and shown.invitation.uid == uid:
                reader.show_message(shown)

        self._backend.ask(MESSAGE_SHOW, MessageParameters(account, folder, message_id), again, lambda error: None, owner=self)

    def _answer_in_pane(self, answer: str) -> None:
        self.answer_invitation(self.reader, self.shown, answer)

    def answer_invitation(self, reader: ReaderWidget, place: tuple[str, str, str] | None, answer: str) -> None:
        """Accept, Tentative, Decline (or Add to calendar) on the card of the message in this reader: an item not kept yet asks for its calendar
        first; the answer mail, when Chrono Mail makes it, opens in the composer and goes only with Send."""
        info = reader.invitation
        if place is None or info is None:
            return
        account, folder, message_id = place
        if answer == "DECLINED":
            self._send_answer(reader, place, answer, "")
            return

        def listed(choices: ChoicesResult) -> None:
            if choices.stored:  # your calendar holds it now (asked afresh: it may have gone since the card was shown)
                self._send_answer(reader, place, answer, "")
                return
            dialog = InvitationCalendarDialog(choices, task=info.kind == "task", parent=reader.window())
            if dialog.exec() == InvitationCalendarDialog.DialogCode.Accepted and dialog.chosen:
                self._send_answer(reader, place, answer, dialog.chosen)

        self._backend.ask(INVITATION_CHOICES, MessageParameters(account, folder, message_id), listed,
                          lambda error: self._status.report("Could not read the invitation", error), owner=self)

    def _send_answer(self, reader: ReaderWidget, place: tuple[str, str, str], answer: str, calendar: str) -> None:
        account, folder, message_id = place
        chosen: Answer = "DECLINED" if answer == "DECLINED" else ("TENTATIVE" if answer == "TENTATIVE" else "ACCEPTED")
        parameters = AnswerParameters(account, folder, message_id, answer=chosen, calendar=calendar, mail=self._prefs.read().send_invitation_answers)

        shared = reader.invitation is not None and reader.invitation.method == "PUBLISH"

        def answered(result: AnswerResult) -> None:
            words = {"ACCEPTED": "accepted", "TENTATIVE": "accepted tentatively", "DECLINED": "declined"}[result.answer]
            where = f" It is in {result.stored_in}." if result.stored_in else ""
            mailer = " The calendar server sends your answer." if result.server_answers else ""
            self._status.show(f"Added to your calendar.{where}" if shared else f"You {words} the invitation.{where}{mailer}")
            if result.draft_message is not None and result.draft_folder is not None and result.draft_account is not None:
                self._composing.open_draft(result.draft_account, result.draft_folder, result.draft_message)
            self._show_again(reader, place)  # the card says what your calendar holds now

        self._backend.ask(INVITATION_ANSWER, parameters, answered, lambda error: self._status.report("Could not answer the invitation", error), owner=self)

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
        if self.shown is not None:
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

