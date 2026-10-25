from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtWidgets import QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "whendialog.py 1.1.207"

# --- Version history ----------------------------------------------------
# v1.1.207: show_current: opens on the time, interval and end a scheduled message has now (D-177)
# v0.0.67: Repeating: an interval and an end
# v0.0.34: Initial dialog for choosing a time (send later, snooze)

MORNING_HOUR: int = 8
EVENING_HOUR: int = 18


def to_qt(moment: datetime) -> QDateTime:
    return QDateTime(QDate(moment.year, moment.month, moment.day), QTime(moment.hour, moment.minute))


def presets(now: datetime) -> list[tuple[str, datetime]]:
    """The usual choices from this moment on."""
    tomorrow = (now + timedelta(days=1)).replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    monday = (now + timedelta(days=7 - now.weekday())).replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    evening = now.replace(hour=EVENING_HOUR, minute=0, second=0, microsecond=0)
    choices = [("In an hour", (now + timedelta(hours=1)).replace(second=0, microsecond=0))]
    if evening > now + timedelta(minutes=30):
        choices.append(("This evening", evening))
    choices.extend([("Tomorrow morning", tomorrow), ("Monday morning", monday)])
    return choices


class WhenDialog(QDialog):
    """Asks for a point in time ahead: quick choices, or a date and time of one's own."""

    def __init__(self, title: str, question: str, ok_text: str, parent: QWidget | None = None, now: datetime | None = None, repeat: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        moment = now or datetime.now()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(question, self))
        quick = QHBoxLayout()
        self.preset_buttons: list[QPushButton] = []
        self.edit = QDateTimeEdit(self)
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        for label, when in presets(moment):
            button = QPushButton(label, self)
            button.clicked.connect(lambda checked=False, chosen=when: self.edit.setDateTime(to_qt(chosen)))
            quick.addWidget(button)
            self.preset_buttons.append(button)
        layout.addLayout(quick)
        tomorrow = presets(moment)[-2][1]
        self.edit.setDateTime(to_qt(tomorrow))
        self.edit.setMinimumDateTime(to_qt(moment))
        layout.addWidget(self.edit)
        self.repeat_box = QCheckBox("Send it again and again, every", self)
        self.months = QSpinBox(self)
        self.weeks = QSpinBox(self)
        self.days = QSpinBox(self)
        self.until_box = QCheckBox("Stop after", self)
        self.until = QDateTimeEdit(self)
        self.until.setCalendarPopup(True)
        self.until.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.until.setDateTime(to_qt(moment + timedelta(days=30)))
        for spin, suffix, maximum in ((self.months, " months", 120), (self.weeks, " weeks", 520), (self.days, " days", 3650)):
            spin.setRange(0, maximum)
            spin.setSuffix(suffix)
        self.days.setValue(7)
        self.repeating = QWidget(self)
        row = QHBoxLayout(self.repeating)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.months, self.weeks, self.days):
            row.addWidget(widget)
        until_row = QHBoxLayout()
        until_row.addWidget(self.until_box)
        until_row.addWidget(self.until, 1)
        self.until_widget = QWidget(self)
        self.until_widget.setLayout(until_row)
        if repeat:
            for widget in (self.repeat_box, self.repeating, self.until_widget):
                layout.addWidget(widget)
        else:
            for widget in (self.repeat_box, self.repeating, self.until_widget):
                widget.hide()
        self.repeat_box.toggled.connect(self._repeat_toggled)
        self.until_box.toggled.connect(self.until.setEnabled)
        self._repeat_toggled(False)
        self.until.setEnabled(False)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def show_current(self, moment: datetime, months: int = 0, weeks: int = 0, days: int = 0, end: datetime | None = None) -> None:
        """Open on what a scheduled message has now: its time, and for a repeating one its interval and end."""
        self.edit.setDateTime(to_qt(moment))
        repeating = months + weeks + days > 0
        self.repeat_box.setChecked(repeating)
        if repeating:
            self.months.setValue(months)
            self.weeks.setValue(weeks)
            self.days.setValue(days)
        self.until_box.setChecked(end is not None)
        if end is not None:
            self.until.setDateTime(to_qt(end))

    def _repeat_toggled(self, on: bool) -> None:
        self.repeating.setEnabled(on)
        self.until_widget.setEnabled(on)

    def repeat_values(self) -> dict[str, object]:
        """The repeat_* values for mail.send, or nothing when the message is not to be repeated."""
        if not self.repeat_box.isChecked():
            return {}
        values: dict[str, object] = {"repeat_months": self.months.value(), "repeat_weeks": self.weeks.value(), "repeat_days": self.days.value()}
        if self.until_box.isChecked():
            values["repeat_until"] = self.until.dateTime().toString("yyyy-MM-ddTHH:mm")
        return values

    def chosen(self) -> str:
        """The time as the commands want it: 2026-10-11T09:00."""
        return self.edit.dateTime().toString("yyyy-MM-ddTHH:mm")
