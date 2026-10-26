from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtWidgets import QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "whendialog.py 4.0.377"

# --- Version history ----------------------------------------------------
# v4.0.377: built from its Qt Designer form (ui/forms/send_later.ui, D-276), the quick choices at their place; the Tab order follows the
#           window from top to bottom (the date came before the quick choices)
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
        self._moment: datetime = moment
        self.preset_buttons: list[QPushButton] = []
        form = forms.fill(self, "send_later", {"quick": self._quick})  # D-276
        part(form, QLabel, "question").setText(question)
        self.edit: QDateTimeEdit = part(form, QDateTimeEdit, "edit")
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        tomorrow = presets(moment)[-2][1]
        self.edit.setDateTime(to_qt(tomorrow))
        self.edit.setMinimumDateTime(to_qt(moment))
        self.repeat_box: QCheckBox = part(form, QCheckBox, "repeat")
        self.months: QSpinBox = part(form, QSpinBox, "months")
        self.weeks: QSpinBox = part(form, QSpinBox, "weeks")
        self.days: QSpinBox = part(form, QSpinBox, "days")
        self.until_box: QCheckBox = part(form, QCheckBox, "untilBox")
        self.until: QDateTimeEdit = part(form, QDateTimeEdit, "until")
        self.until.setCalendarPopup(True)
        self.until.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.until.setDateTime(to_qt(moment + timedelta(days=30)))
        for spin, maximum in ((self.months, 120), (self.weeks, 520), (self.days, 3650)):
            spin.setRange(0, maximum)
        self.days.setValue(7)
        self.repeating: QWidget = part(form, QWidget, "repeating")
        self.until_widget: QWidget = part(form, QWidget, "untilWidget")
        if not repeat:
            for widget in (self.repeat_box, self.repeating, self.until_widget):
                widget.hide()
        self.repeat_box.toggled.connect(self._repeat_toggled)
        self.until_box.toggled.connect(self.until.setEnabled)
        self._repeat_toggled(False)
        self.until.setEnabled(False)
        box = part(form, QDialogButtonBox, "buttons")
        box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.edit.setFocus()  # it opens on the date, as before: Enter schedules at the time shown (a focused quick choice would take Enter)

    def _quick(self, parent: QWidget) -> QWidget:
        """The quick choices from this moment on, made at their place in the form (they change with the time of day)."""
        quick = QWidget(parent)
        row = QHBoxLayout(quick)
        row.setContentsMargins(0, 0, 0, 0)
        for label, when in presets(self._moment):
            button = QPushButton(label, quick)
            button.clicked.connect(lambda checked=False, chosen=when: self.edit.setDateTime(to_qt(chosen)))
            row.addWidget(button)
            self.preset_buttons.append(button)
        return quick

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
