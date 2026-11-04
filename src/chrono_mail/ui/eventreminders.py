from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget

from chrono_mail.engine.commands_calendar import MOST_REMINDERS, ReminderInfo
from chrono_mail.core.words import counted
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "eventreminders.py 4.4.536"

# --- Version history ----------------------------------------------------
# v4.4.536: Initial version: the reminders of an event, each Notification or Email, a number and a unit (round 4)

UNITS: tuple[tuple[str, int], ...] = (("minutes", 1), ("hours", 60), ("days", 1440), ("weeks", 10080))
MOST_OF_A_UNIT: int = 99999  # the field needs an end


def _unit_of(minutes: int) -> tuple[int, int]:
    """A number and the index of the largest unit that says it exactly: 120 minutes is 2 hours."""
    index = next((index for index in range(len(UNITS) - 1, 0, -1) if minutes and minutes % UNITS[index][1] == 0), 0)
    return minutes // UNITS[index][1], index


def _fits(reminder: ReminderInfo) -> bool:
    return not reminder.at and not reminder.relative_to_end and reminder.minutes_before is not None and reminder.minutes_before >= 0 and reminder.action in ("DISPLAY", "AUDIO", "EMAIL")


def _words(reminder: ReminderInfo) -> str:
    """A reminder from elsewhere the fields cannot say: shown as it is, and it stays as it is."""
    if reminder.at or reminder.minutes_before is None:
        return f"At {reminder.at.replace('T', ' ')[:16]}" if reminder.at else "At a set time"
    place = "the end" if reminder.relative_to_end else "the start"
    return f"{counted(abs(reminder.minutes_before), 'minute')} {'before' if reminder.minutes_before >= 0 else 'after'} {place}"


class ReminderRow(QWidget):
    def __init__(self, removed: Callable[["ReminderRow"], None], reminder: ReminderInfo | None = None, index: int = -1) -> None:
        super().__init__()
        form = forms.fill(self, "event_reminder")  # D-276
        self.kind: QComboBox = part(form, QComboBox, "kind")
        self.kind.addItem("Notification", "")
        self.kind.addItem("Email", "email:")
        self.number: QSpinBox = part(form, QSpinBox, "number")
        self.number.setRange(0, MOST_OF_A_UNIT)
        self.unit: QComboBox = part(form, QComboBox, "unit")
        for name, minutes in UNITS:
            self.unit.addItem(name, minutes)
        self.fixed: QLabel = part(form, QLabel, "fixed")
        self.remove: QToolButton = part(form, QToolButton, "remove")
        self.remove.clicked.connect(lambda: removed(self))
        self._index: int = index  # its place in the event: left as it was, it stays exactly as it was
        fits = reminder is None or _fits(reminder)
        self.fixed.setVisible(not fits)
        for field in (self.kind, self.number, self.unit):
            field.setVisible(fits)
        if reminder is None:
            self.number.setValue(15)
        elif fits:
            number, unit = _unit_of(reminder.minutes_before or 0)
            self.kind.setCurrentIndex(1 if reminder.action == "EMAIL" else 0)
            self.number.setValue(number)
            self.unit.setCurrentIndex(unit)
        else:
            self.fixed.setText(_words(reminder))
        self._first: str = self._text()

    def _text(self) -> str:
        return f"{self.kind.currentData()}{self.number.value() * int(self.unit.currentData())}"

    def value(self) -> str:
        """As event.add and event.edit take it."""
        return f"keep:{self._index}" if self._index >= 0 and self._text() == self._first else self._text()


class ReminderList:
    """The rows in the window, and Add notification, greyed at the owner's number."""

    def __init__(self, rows: QVBoxLayout, add_button: QPushButton) -> None:
        self._layout: QVBoxLayout = rows
        self.add_button: QPushButton = add_button
        self.rows: list[ReminderRow] = []
        self._before: tuple[str, ...] = ()
        self._on: bool = True
        add_button.clicked.connect(self.add)

    def show_reminders(self, reminders: tuple[ReminderInfo, ...]) -> None:
        for row in self.rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self.rows = []
        for index, reminder in enumerate(reminders):
            self._add_row(ReminderRow(self.remove, reminder, index))
        self._before = self.values()
        self._follow()

    def _add_row(self, row: ReminderRow) -> None:
        self.rows.append(row)
        self._layout.addWidget(row)

    def add(self) -> None:
        self._add_row(ReminderRow(self.remove))
        self._follow()

    def remove(self, row: ReminderRow) -> None:
        self.rows.remove(row)
        self._layout.removeWidget(row)
        row.deleteLater()
        self._follow()

    def set_enabled(self, on: bool) -> None:
        self._on = on
        self._follow()

    def _follow(self) -> None:
        for row in self.rows:
            row.setEnabled(self._on)
        self.add_button.setEnabled(self._on and len(self.rows) < MOST_REMINDERS)

    def values(self) -> tuple[str, ...]:
        return tuple(row.value() for row in self.rows)

    def changed(self) -> bool:
        return self.values() != self._before
