from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.pim.times import in_local_zone
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import OWN_COLOUR_ID, ChronoMailError
from chrono_mail.engine.commands_calendar import EVENT_ADD, EVENT_EDIT, EventInfo
from chrono_mail.engine.commands_calendar import AddParameters as EventAdd
from chrono_mail.engine.commands_calendar import EditParameters as EventEdit
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.colours import red
from chrono_mail.ui.paletteeditor import OTHER_COLOUR_NAME, PaletteEditor, circle_icon, palette_of_summary
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "eventeditor.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: an event's times through the zone the engine reads them back in (also never the platform's conversion, RV-201)
# v3.2.342: the reminders are sent only when the reminder choice was changed: a second alarm or one at a fixed time stays (RV-18)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: parameters gives the typed edit (EditParameters); add and edit are typed calls; the event is an EventInfo
# v1.1.219: Colour: one of the calendar's colours (its own by default), with Edit colours… (D-191)
# v0.0.64: Initial editor for an event

REPEATS: tuple[tuple[str, str], ...] = (
    ("", "Does not repeat"), ("FREQ=DAILY", "Every day"), ("FREQ=WEEKLY", "Every week"), ("FREQ=MONTHLY", "Every month"), ("FREQ=YEARLY", "Every year"),
)
REMINDERS: tuple[tuple[str, str], ...] = (
    ("", "No reminder"), ("0", "At the start"), ("5", "5 minutes before"), ("15", "15 minutes before"), ("30", "30 minutes before"), ("60", "1 hour before"),
    ("1440", "1 day before"), ("10080", "1 week before"),
)
DATE_FORMAT: str = "yyyy-MM-dd"
TIME_FORMAT: str = "yyyy-MM-dd HH:mm"


def to_qdatetime(text: str) -> QDateTime:
    """From the ISO text of the commands (a date, or a time with an offset) to a time on the clock of this computer."""
    if len(text) == 10:
        return QDateTime(QDate.fromString(text, DATE_FORMAT), QTime(0, 0))
    moment = datetime.fromisoformat(text)
    local = in_local_zone(moment)  # shown in the zone the engine reads the times back in
    return QDateTime(QDate(local.year, local.month, local.day), QTime(local.hour, local.minute))


class EventEditor(QDialog):
    """Makes an event or changes one (the series, or one occurrence of it). The commands do the saving."""

    saved = Signal(str)

    def __init__(
        self, backend: Backend, calendars: list[CollectionSummary], known_tags: list[str], event: EventInfo | None = None, collection: str = "", occurrence: str = "",
        start: str = "", end: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: EventInfo | None = event
        self._reminder_before: str = ""  # the reminder choice as the event had it: the reminders are sent only when it changed (RV-18)
        self._item: str | None = None if event is None else event.item
        self._occurrence: str = occurrence
        self.setWindowTitle("New event" if event is None else (f"Occurrence: {event.summary}" if occurrence else f"Event: {event.summary}"))
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.summary = QLineEdit(self)
        self.calendar = QComboBox(self)
        for info in calendars:
            self.calendar.addItem(f"{info.account_name} — {info.name}", info.collection)
        self.calendar.setCurrentIndex(max(0, self.calendar.findData(collection)))
        self.calendar.setEnabled(event is None and self.calendar.count() > 1)
        self._calendars: list[CollectionSummary] = list(calendars)
        self.colour = QToolButton(self)  # "● name ▾": the colour of the appointment, one of its calendar's (D-191)
        self.colour.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.colour.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.colour_menu = QMenu(self.colour)
        self.colour.setMenu(self.colour_menu)
        self._original_colour: str = event.colour_id if event is not None else ""
        self._other_colour: str = event.other_colour if event is not None else ""
        self.colour_id: str = self._original_colour or (OWN_COLOUR_ID if not self._other_colour else "")
        self.location = QLineEdit(self)
        self.all_day = QCheckBox("All day", self)
        self.start = QDateTimeEdit(self)
        self.end = QDateTimeEdit(self)
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat(TIME_FORMAT)
        now = QDateTime.currentDateTime()
        self.start.setDateTime(QDateTime(now.date(), QTime(now.time().hour() + 1 if now.time().hour() < 23 else 23, 0)))
        self.end.setDateTime(self.start.dateTime().addSecs(3600))
        self.repeat = QComboBox(self)
        for value, label in REPEATS:
            self.repeat.addItem(label, value)
        self.reminder = QComboBox(self)
        for value, label in REMINDERS:
            self.reminder.addItem(label, value)
        self.busy = QComboBox(self)
        self.busy.addItem("Busy", True)
        self.busy.addItem("Free", False)
        self.attendees = QLineEdit(self)
        self.attendees.setPlaceholderText("Addresses, separated by commas")
        self.tags = QLineEdit(self)
        self.tags.setPlaceholderText("Tags, separated by commas")
        completer = QCompleter(known_tags, self.tags)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags.setCompleter(completer)
        self.description = QPlainTextEdit(self)
        self.description.setMinimumHeight(90)
        form.addRow("Title", self.summary)
        form.addRow("Calendar", self.calendar)
        form.addRow("Colour", self.colour)
        form.addRow("Location", self.location)
        form.addRow("", self.all_day)
        form.addRow("Starts", self.start)
        form.addRow("Ends", self.end)
        form.addRow("Repeat", self.repeat)
        form.addRow("Reminder", self.reminder)
        form.addRow("Show as", self.busy)
        form.addRow("Attendees", self.attendees)
        form.addRow("Tags", self.tags)
        layout.addLayout(form)
        layout.addWidget(QLabel("Description", self))
        layout.addWidget(self.description)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.calendar.currentIndexChanged.connect(self._calendar_changed)
        self._show_colours()
        self.all_day.toggled.connect(self._all_day_changed)
        self.start.dateTimeChanged.connect(self._start_changed)
        self._length: int = 3600
        if event is not None:
            self._fill(event, start, end)
        elif start:
            all_day = len(start) == 10
            self.all_day.setChecked(all_day)
            self.start.setDateTime(to_qdatetime(start))
            stop = to_qdatetime(end) if end else self.start.dateTime().addSecs(3600)
            self.end.setDateTime(stop.addDays(-1) if all_day and end else stop)  # all day: the editor shows the last day, the end is the day after it
        self._length = max(0, self.start.dateTime().secsTo(self.end.dateTime()))
        if occurrence:
            self.repeat.setEnabled(False)  # one occurrence has no rule of its own

    # --- filling ------------------------------------------------------------------------------

    def _fill(self, event: EventInfo, start: str, end: str) -> None:
        self.summary.setText(event.summary)
        self.location.setText(event.location)
        self.description.setPlainText(event.description)
        first, last = start or event.start, end or event.end
        all_day = event.all_day if not start else len(start) == 10
        self.all_day.setChecked(all_day)
        self.start.setDateTime(to_qdatetime(first))
        stop = to_qdatetime(last)
        self.end.setDateTime(stop.addDays(-1) if all_day else stop)  # an all-day event ends the day after its last day: the editor shows the last day
        rule = event.rrule
        index = self.repeat.findData(rule)
        if index < 0 and rule:
            self.repeat.addItem(f"Custom: {rule}", rule)
            index = self.repeat.count() - 1
        self.repeat.setCurrentIndex(max(0, index))
        reminders = tuple(event.reminders)
        minutes = str(reminders[0].minutes_before) if reminders and reminders[0].minutes_before is not None else ""
        index = self.reminder.findData(minutes)
        if index < 0 and minutes:
            self.reminder.addItem(f"{counted(int(minutes), 'minute')} before" if minutes.isdigit() else f"{minutes} minutes before", minutes)
            index = self.reminder.count() - 1
        self.reminder.setCurrentIndex(max(0, index))
        self._reminder_before = str(self.reminder.currentData())
        self.busy.setCurrentIndex(0 if event.busy else 1)
        self.attendees.setText(", ".join(person.address for person in event.attendees))
        self.tags.setText(", ".join(event.tags))

    # --- the colour (D-191) -------------------------------------------------------------------------

    def _current_calendar(self) -> CollectionSummary | None:
        wanted = str(self.calendar.currentData())
        return next((info for info in self._calendars if info.collection == wanted), None)

    def _show_colours(self) -> None:
        """The button shows the chosen colour and its name; its list holds the calendar's colours (an "Other colour" first when another
        program coloured the appointment and no colour was picked here), then Edit colours…."""
        calendar = self._current_calendar()
        palette = palette_of_summary(calendar)
        if self.colour_id and all(entry.colour_id != self.colour_id for entry in palette):
            self.colour_id = OWN_COLOUR_ID  # a colour this calendar does not have: its own (another calendar was chosen, or it was deleted)
        self.colour_menu.clear()
        if not self.colour_id and self._other_colour:
            other = self.colour_menu.addAction(circle_icon(self._other_colour), OTHER_COLOUR_NAME)
            other.setCheckable(True)
            other.setChecked(True)
            self.colour_menu.addSeparator()
            self.colour.setIcon(circle_icon(self._other_colour))
            self.colour.setText(OTHER_COLOUR_NAME)
        for entry in palette:
            action = self.colour_menu.addAction(circle_icon(entry.colour), entry.name)
            action.setCheckable(True)
            action.setChecked(entry.colour_id == self.colour_id)
            action.triggered.connect(lambda _checked=False, chosen=entry.colour_id: self._choose_colour(chosen))
            if entry.colour_id == self.colour_id:
                self.colour.setIcon(circle_icon(entry.colour))
                self.colour.setText(entry.name)
        self.colour_menu.addSeparator()
        edit = self.colour_menu.addAction("Edit colours…", self._edit_colours)
        read_only = calendar is not None and calendar.read_only
        edit.setEnabled(not read_only)
        self.colour.setEnabled(not read_only and bool(palette))  # greyed, never hidden

    def _choose_colour(self, colour_id: str) -> None:
        self.colour_id = colour_id
        self._show_colours()

    def _calendar_changed(self) -> None:
        self._show_colours()

    def _edit_colours(self) -> None:
        calendar = self._current_calendar()
        if calendar is None:
            return
        editor = PaletteEditor(self._backend, calendar, self)
        if editor.exec() == PaletteEditor.DialogCode.Accepted and editor.result_summary is not None:
            fresh = editor.result_summary
            self._calendars = [fresh if info.collection == fresh.collection else info for info in self._calendars]
            self._show_colours()

    def _all_day_changed(self, on: bool) -> None:
        self.start.setDisplayFormat(DATE_FORMAT if on else TIME_FORMAT)
        self.end.setDisplayFormat(DATE_FORMAT if on else TIME_FORMAT)

    def _start_changed(self, moment: QDateTime) -> None:
        """When the start moves the end moves with it, so that the length stays."""
        self.end.setDateTime(moment.addSecs(self._length))

    # --- the command --------------------------------------------------------------------------

    def parameters(self) -> EventEdit:
        """What is in the editor, as event.add and event.edit take it: the fields, and the lists emptied here (clear)."""
        if self.all_day.isChecked():
            start = self.start.date().toString(DATE_FORMAT)
            end = self.end.date().addDays(1).toString(DATE_FORMAT)
        else:
            start = self.start.dateTime().toString("yyyy-MM-dd'T'HH:mm")
            end = self.end.dateTime().toString("yyyy-MM-dd'T'HH:mm")
        attendees = tuple(part.strip() for part in self.attendees.text().split(",") if part.strip())
        tags = tuple(part.strip() for part in self.tags.text().split(",") if part.strip())
        reminder = (str(self.reminder.currentData()),) if self.reminder.currentData() != "" else ()
        original = self._original
        reminder_changed = original is None or str(self.reminder.currentData()) != self._reminder_before
        if not reminder_changed:  # the window shows one reminder: the others (a second alarm, one at a fixed time) stay as they are (RV-18)
            reminder = ()
        clear: list[str] = []
        for parameter, entries, had in (("attendee", attendees, original is not None and bool(original.attendees)), ("tag", tags, original is not None and bool(original.tags)),
                                        ("reminder", reminder, original is not None and bool(original.reminders) and reminder_changed)):
            if not entries and had:
                clear.append(parameter)
        chosen = "" if self.colour_id == OWN_COLOUR_ID else self.colour_id
        picked = bool(self.colour_id) and (original is None and bool(chosen) or original is not None and chosen != self._original_colour)
        return EventEdit(
            summary=self.summary.text().strip(), start=start, end=end, location=self.location.text().strip(), description=self.description.toPlainText().strip(),
            busy=bool(self.busy.currentData()), rrule=None if self._occurrence else str(self.repeat.currentData()), attendee=attendees, tag=tags, reminder=reminder,
            colour=chosen if picked else None,  # only when picked here: an "Other colour" left as it is stays as another program wrote it
            item=self._item or "", occurrence=self._occurrence or "", clear=tuple(clear),
        )

    def _save(self) -> None:
        values = self.parameters()
        if not values.summary:
            self.result_label.setText(f"<span style='color:{red()}'>The event needs a title.</span>")
            return
        if not self.all_day.isChecked() and self.end.dateTime() < self.start.dateTime() or self.all_day.isChecked() and self.end.date() < self.start.date():
            self.result_label.setText(f"<span style='color:{red()}'>The event cannot end before it starts.</span>")
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: EventInfo) -> None:
            self.saved.emit(result.item)
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{error}</span>")

        if self._item is None:
            self._backend.ask(EVENT_ADD, carried(values, EventAdd, calendar=str(self.calendar.currentData())), done, failed, owner=self)
        else:
            self._backend.ask(EVENT_EDIT, values, done, failed, owner=self)
