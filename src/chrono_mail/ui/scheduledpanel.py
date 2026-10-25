import html
from typing import override

from PySide6.QtCore import Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_mail import MessageParameters
from chrono_mail.engine.commands_schedule import (
    MAIL_REPEAT_PAUSE, MAIL_REPEAT_RESUME, MAIL_SCHEDULED, MAIL_SEND_DUE, MESSAGE_SNOOZED, MESSAGE_WAKE, AccountOptional, RepeatState, ScheduledInfo, SendDueParameters,
    SendDueResult, SnoozedInfo, WakeParameters, WakeResult,
)
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH

# --- Version ------------------------------------------------------------
__version__ = "scheduledpanel.py 3.6.354"

# --- Version history ----------------------------------------------------
# v3.6.354: Chrono Mail's own folders' names written once (CR-36)
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v2.4.327: Pause and Resume for a one-off scheduled message too; a paused one says so in its line (D-245)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.268: R-21: Send now, Cancel snooze, Pause and Resume change only their own line (it leaves, the next one is selected; or it
#           moves to its new time, selected); showing the page again brings the list in line in place, the selection kept; Send now
#           says what failed; the commands are typed (D-224)
# v1.1.208: Move back to drafts and Stop repeating are gone: a drag to Drafts in the main window, and Edit, do those (D-178)
# v1.1.207: New and Edit (no dots, the owner); Edit or a double click opens a scheduled message to change it, and only its line follows (replace_scheduled) (D-177)
# v1.1.203: The first button says the one action that applies: Send now (scheduled) or Cancel snooze (snoozed, with where it goes back to)
# v1.1.201: "Take back as a draft" is "Move back to drafts"
# v1.1.200: The waiting messages are in the Scheduled folder
# v1.1.198: New… makes a new scheduled message (new_requested); add_scheduled puts it in the list at its place, selected, without a reload
# v0.0.67: Messages that repeat can be paused, resumed and stopped
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.34: Initial window for scheduled and snoozed messages

ROLE_INFO: int = 256


def scheduled_text(item: ScheduledInfo) -> str:
    """How the list shows a message that waits to be sent (with its repeat, when it is sent again and again)."""
    text = f"Send {item.send_at}   {item.subject or '(no subject)'}  to {', '.join(item.to)}"
    if item.repeat:
        state = "paused" if item.paused else f"sent {item.times_sent} times so far"
        text += f"   [{item.repeat}; {state}]"
    elif item.paused:
        text += "   [paused]"
    return text


def line_text(info: ScheduledInfo | SnoozedInfo) -> str:
    """How the list shows a waiting or a snoozed message."""
    if isinstance(info, ScheduledInfo):
        return scheduled_text(info)
    return f"Back {info.until}   a snoozed message (returns to {info.comes_back_to})"


class ScheduledPanel(QWidget):
    """The messages waiting to be sent at a time, and the messages snoozed until a time: a new scheduled message (New) or a
    change to one (Edit), both made in a message window by the main window; send now or cancel the snooze; pause or resume."""

    new_requested = Signal()  # New was clicked: the main window opens a message window that schedules (see add_scheduled)
    edit_requested = Signal(object)  # Edit (or a double click) on a scheduled message: its ScheduledInfo; the main window opens it (see replace_scheduled)

    def __init__(self, backend: Backend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.changed: bool = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Waiting to be sent, and put away until a time:", self))
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.new_button = QPushButton("New", self)
        self.new_button.setToolTip("Write a new message that is sent at a time you choose")
        row.addWidget(self.new_button)
        self.edit_button = QPushButton("Edit", self)
        self.edit_button.setToolTip("Change this message; it stays scheduled")
        row.addWidget(self.edit_button)
        self.now_button = QPushButton("Send now", self)  # or Cancel snooze: the one action that applies to the selected line
        row.addWidget(self.now_button)
        self.pause_button = QPushButton("Pause", self)
        row.addWidget(self.pause_button)  # back to drafts: drag it to Drafts in the main window; to stop repeating: Edit (D-178)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.new_button.clicked.connect(self.new_requested)
        self.edit_button.clicked.connect(self.edit)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.now_button.clicked.connect(self.now)
        self.pause_button.clicked.connect(self.pause_or_resume)
        self.list.currentItemChanged.connect(self._selection_changed)
        self._selection_changed()
        self.reload()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()  # messages may have been sent, scheduled or woken while the page was not shown

    def reload(self) -> None:
        """Bring the list in line with what waits now, in place: lines that went are taken out, new ones put at their place in
        time, changed ones updated; the other lines and the selected message stay (D-224)."""

        def got_scheduled(scheduled: tuple[ScheduledInfo, ...]) -> None:
            def got_snoozed(snoozed: tuple[SnoozedInfo, ...]) -> None:
                infos: list[ScheduledInfo | SnoozedInfo] = [*scheduled, *snoozed]
                self._bring_in_line(sorted(infos, key=lambda info: info.seconds))

            self._backend.ask(MESSAGE_SNOOZED, AccountOptional(), got_snoozed, self._failed, owner=self)

        self._backend.ask(MAIL_SCHEDULED, AccountOptional(), got_scheduled, self._failed, owner=self)

    def _bring_in_line(self, infos: list[ScheduledInfo | SnoozedInfo]) -> None:
        current = self._selected()
        chosen = "" if current is None else current.message_id
        wanted = {info.message_id for info in infos}
        for row in reversed(range(self.list.count())):
            if self._id_at(row) not in wanted:
                self.list.takeItem(row)
        for row, info in enumerate(infos):
            found = next((number for number in range(row, self.list.count()) if self._id_at(number) == info.message_id), -1)
            entry = self.list.takeItem(found) if found > row else self.list.item(row) if found == row else QListWidgetItem()
            if entry is None:
                continue
            if found != row:
                self.list.insertItem(row, entry)
            text = line_text(info)
            if entry.text() != text:
                entry.setText(text)
            entry.setData(ROLE_INFO, info)
        place = next((number for number in range(self.list.count()) if self._id_at(number) == chosen), 0 if chosen == "" else -1)
        if place < 0:
            place = 0  # the selected message left while the page was not shown
        if self.list.count() and self.list.currentRow() != place:
            self.list.setCurrentRow(place)
        self._selection_changed()

    def _info_at(self, row: int) -> ScheduledInfo | SnoozedInfo | None:
        """The message a line of the list stands for."""
        entry = self.list.item(row)
        info = None if entry is None else entry.data(ROLE_INFO)
        return info if isinstance(info, (ScheduledInfo, SnoozedInfo)) else None

    def _id_at(self, row: int) -> str:
        info = self._info_at(row)
        return "" if info is None else info.message_id

    def _row_of(self, message_id: str) -> int:
        return next((number for number in range(self.list.count()) if self._id_at(number) == message_id), -1)

    def _place_line(self, info: ScheduledInfo) -> None:
        """Only this message's line changes: its text, and its place when its time changed; it is selected."""
        old = self._row_of(info.message_id)
        entry = self.list.takeItem(old) if old >= 0 else QListWidgetItem()
        if entry is None:
            return
        entry.setText(scheduled_text(info))
        entry.setData(ROLE_INFO, info)
        row = next((number for number in range(self.list.count()) if (line := self._info_at(number)) is not None and line.seconds > info.seconds), self.list.count())
        self.list.insertItem(row, entry)
        self.list.setCurrentRow(row)
        self._selection_changed()

    def _remove_line(self, message_id: str) -> None:
        """Only this message's line leaves; the line after it is selected (at the end: the one before), as in the message list (D-098)."""
        row = self._row_of(message_id)
        if row < 0:
            return
        self.list.takeItem(row)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._selection_changed()

    def add_scheduled(self, message_id: str) -> None:
        """A message was just scheduled (New): only it is added, at its place in time, and selected; the rest of the list and
        what was selected stay as they are."""

        def got(scheduled: tuple[ScheduledInfo, ...]) -> None:
            found = next((item for item in scheduled if item.message_id == message_id), None)
            if found is None:
                return  # sent or taken back meanwhile: nothing to add
            self._place_line(found)
            self.status.setText("The new message waits to be sent")

        self._backend.ask(MAIL_SCHEDULED, AccountOptional(), got, self._failed, owner=self)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.edit()

    def edit(self) -> None:
        info = self._selected()
        if isinstance(info, ScheduledInfo):  # a snoozed message is received mail: it is never changed
            self.edit_requested.emit(info)

    def replace_scheduled(self, info: object) -> None:
        """A scheduled message was edited (it keeps its id): only its line changes, moved to its place in time if its time
        changed, and it stays selected; the rest of the list stays as it is."""
        if not isinstance(info, ScheduledInfo) or self._row_of(info.message_id) < 0:
            return
        self._place_line(info)  # the main window's list follows the engine's fact; nothing is fetched again
        self.status.setText("The changes are saved; the message stays scheduled")

    def _failed(self, error: ChronoMailError) -> None:
        self.status.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

    def _selected(self) -> ScheduledInfo | SnoozedInfo | None:
        item = self.list.currentItem()
        return None if item is None else item.data(ROLE_INFO)

    def _selection_changed(self) -> None:
        info = self._selected()
        self.now_button.setEnabled(info is not None)
        if isinstance(info, SnoozedInfo):  # the same action as Cancel snooze in the main window: one action, one name
            self.now_button.setText("Cancel snooze")
            self.now_button.setToolTip(f"The message goes back to {info.comes_back_to} now, unread")
        else:
            self.now_button.setText("Send now")
            self.now_button.setToolTip("Send this message now, without waiting for its time")
        self.edit_button.setEnabled(isinstance(info, ScheduledInfo))
        scheduled = isinstance(info, ScheduledInfo)  # a snoozed message is not sent: nothing to pause
        self.pause_button.setEnabled(scheduled)
        self.pause_button.setText("Resume" if isinstance(info, ScheduledInfo) and info.paused else "Pause")

    def now(self) -> None:
        info = self._selected()
        if isinstance(info, ScheduledInfo):
            self._backend.ask(MAIL_SEND_DUE, SendDueParameters(info.account, info.message_id), lambda result: self._sent(info.message_id, result), self._failed, owner=self)
        elif isinstance(info, SnoozedInfo):
            self._backend.ask(MESSAGE_WAKE, WakeParameters(info.account, info.message_id), lambda result: self._woken(info, result), self._failed, owner=self)

    def _sent(self, message_id: str, result: SendDueResult) -> None:
        self.changed = True
        if result.line is not None:
            self._place_line(result.line)  # a repeating message waits for its next send
        else:
            self._remove_line(message_id)
        if result.failed:
            self._failed(ChronoMailError("; ".join(result.failed)))
        else:
            self.status.setText("Sent")

    def _woken(self, info: SnoozedInfo, result: WakeResult) -> None:
        if not result.woken:
            self._failed(ChronoMailError("The message could not be brought back now; it comes back at its time"))
            return
        self.changed = True
        self._remove_line(info.message_id)
        self.status.setText(f"Back in {info.comes_back_to}, unread")

    def pause_or_resume(self) -> None:
        info = self._selected()
        if not isinstance(info, ScheduledInfo):
            return
        resumed = "It is sent again from now on" if info.repeat else "It is sent at its time; when that has passed, within a minute"
        command, text = (MAIL_REPEAT_RESUME, resumed) if info.paused else (MAIL_REPEAT_PAUSE, "Paused")
        parameters = MessageParameters(info.account, SCHEDULED_FOLDER_PATH, info.message_id)
        self._backend.ask(command, parameters, lambda result: self._repeat_changed(info.message_id, text, result), self._failed, owner=self)

    def _repeat_changed(self, message_id: str, text: str, result: RepeatState) -> None:
        self.changed = True
        if result.line is not None:
            self._place_line(result.line)
        else:
            self._remove_line(message_id)
        self.status.setText(text)
