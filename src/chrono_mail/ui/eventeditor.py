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
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "eventeditor.py 0.0.64"

# --- Version history ----------------------------------------------------
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
    local = moment.astimezone() if moment.tzinfo else moment
    return QDateTime(QDate(local.year, local.month, local.day), QTime(local.hour, local.minute))


class EventEditor(QDialog):
    """Makes an event or changes one (the series, or one occurrence of it). The commands do the saving."""

    saved = Signal(str)

    def __init__(
        self, backend: Backend, calendars: list[object], known_tags: list[str], event: object | None = None, collection: str = "", occurrence: str = "",
        start: str = "", end: str = "", parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: object | None = event
        self._item: str | None = None if event is None else str(getattr(event, "item"))
        self._occurrence: str = occurrence
        self.setWindowTitle("New event" if event is None else (f"Occurrence: {getattr(event, 'summary')}" if occurrence else f"Event: {getattr(event, 'summary')}"))
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.summary = QLineEdit(self)
        self.calendar = QComboBox(self)
        for info in calendars:
            self.calendar.addItem(f"{getattr(info, 'account_name')} — {getattr(info, 'name')}", str(getattr(info, "collection")))
        self.calendar.setCurrentIndex(max(0, self.calendar.findData(collection)))
        self.calendar.setEnabled(event is None and self.calendar.count() > 1)
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

    def _fill(self, event: object, start: str, end: str) -> None:
        self.summary.setText(str(getattr(event, "summary")))
        self.location.setText(str(getattr(event, "location")))
        self.description.setPlainText(str(getattr(event, "description")))
        first, last = start or str(getattr(event, "start")), end or str(getattr(event, "end"))
        all_day = bool(getattr(event, "all_day")) if not start else len(start) == 10
        self.all_day.setChecked(all_day)
        self.start.setDateTime(to_qdatetime(first))
        stop = to_qdatetime(last)
        self.end.setDateTime(stop.addDays(-1) if all_day else stop)  # an all-day event ends the day after its last day: the editor shows the last day
        rule = str(getattr(event, "rrule"))
        index = self.repeat.findData(rule)
        if index < 0 and rule:
            self.repeat.addItem(f"Custom: {rule}", rule)
            index = self.repeat.count() - 1
        self.repeat.setCurrentIndex(max(0, index))
        reminders = tuple(getattr(event, "reminders"))
        minutes = str(getattr(reminders[0], "minutes_before")) if reminders and getattr(reminders[0], "minutes_before") is not None else ""
        index = self.reminder.findData(minutes)
        if index < 0 and minutes:
            self.reminder.addItem(f"{minutes} minutes before", minutes)
            index = self.reminder.count() - 1
        self.reminder.setCurrentIndex(max(0, index))
        self.busy.setCurrentIndex(0 if bool(getattr(event, "busy")) else 1)
        self.attendees.setText(", ".join(str(getattr(person, "address")) for person in getattr(event, "attendees")))
        self.tags.setText(", ".join(getattr(event, "tags")))

    def _all_day_changed(self, on: bool) -> None:
        self.start.setDisplayFormat(DATE_FORMAT if on else TIME_FORMAT)
        self.end.setDisplayFormat(DATE_FORMAT if on else TIME_FORMAT)

    def _start_changed(self, moment: QDateTime) -> None:
        """When the start moves the end moves with it, so that the length stays."""
        self.end.setDateTime(moment.addSecs(self._length))

    # --- the command --------------------------------------------------------------------------

    def parameters(self) -> dict[str, object]:
        """The parameters of event.add or event.edit for what is in the editor."""
        if self.all_day.isChecked():
            start = self.start.date().toString(DATE_FORMAT)
            end = self.end.date().addDays(1).toString(DATE_FORMAT)
        else:
            start = self.start.dateTime().toString("yyyy-MM-dd'T'HH:mm")
            end = self.end.dateTime().toString("yyyy-MM-dd'T'HH:mm")
        values: dict[str, object] = {
            "summary": self.summary.text().strip(), "start": start, "end": end, "location": self.location.text().strip(), "description": self.description.toPlainText().strip(),
            "busy": bool(self.busy.currentData()),
        }
        if not self._occurrence:
            values["rrule"] = str(self.repeat.currentData())
        attendees = [part.strip() for part in self.attendees.text().split(",") if part.strip()]
        tags = [part.strip() for part in self.tags.text().split(",") if part.strip()]
        clear: list[str] = []
        for parameter, entries, attribute in (("attendee", attendees, "attendees"), ("tag", tags, "tags")):
            if entries:
                values[parameter] = entries
            elif self._original is not None and getattr(self._original, attribute):
                clear.append(parameter)
        if self.reminder.currentData() != "":
            values["reminder"] = [str(self.reminder.currentData())]
        elif self._original is not None and getattr(self._original, "reminders"):
            clear.append("reminder")
        if clear:
            values["clear"] = clear
        return values

    def _save(self) -> None:
        values = self.parameters()
        if not str(values["summary"]):
            self.result_label.setText(f"<span style='color:{red()}'>The event needs a title.</span>")
            return
        if not self.all_day.isChecked() and self.end.dateTime() < self.start.dateTime() or self.all_day.isChecked() and self.end.date() < self.start.date():
            self.result_label.setText(f"<span style='color:{red()}'>The event cannot end before it starts.</span>")
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: object) -> None:
            self.saved.emit(str(getattr(result, "item")))
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{error}</span>")

        if self._item is None:
            self._backend.call("event.add", {**values, "calendar": str(self.calendar.currentData())}, done, failed, owner=self)
        else:
            edit: dict[str, object] = {**values, "item": self._item}
            if self._occurrence:
                edit["occurrence"] = self._occurrence
            self._backend.call("event.edit", edit, done, failed, owner=self)
