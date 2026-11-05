from collections.abc import Callable
from datetime import datetime
from typing import override

from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtGui import QPaintEvent, QPalette, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.pim.times import in_local_zone
from chrono_mail.engine.commands_calendar import MOST_REMINDERS, ReminderInfo
from chrono_mail.core.words import counted
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "eventreminders.py 4.4.548"

# --- Version history ----------------------------------------------------
# v4.4.548: keep_room_for_the_widest_row: a row that is only measured is never in a window, so no row can stay behind in an open task editor; a row ends with its last reference
# v4.4.547: the task editor uses these rows too: in a task a row can be at a set time, and without a due date the rows counted from it are greyed with a hover text (D-316)
# v4.4.536: Initial version: the reminders of an event, each Notification or Email, a number and a unit (round 4)

UNITS: tuple[tuple[str, int], ...] = (("minutes", 1), ("hours", 60), ("days", 1440), ("weeks", 10080))
MOST_OF_A_UNIT: int = 99999  # the field needs an end
AT_A_SET_TIME: str = "at"
NEEDS_A_DUE_DATE: str = "This counts from the due date. Give the task a due date to use it."


def _unit_of(minutes: int) -> tuple[int, int]:
    """A number and the index of the largest unit that says it exactly: 120 minutes is 2 hours."""
    index = next((index for index in range(len(UNITS) - 1, 0, -1) if minutes and minutes % UNITS[index][1] == 0), 0)
    return minutes // UNITS[index][1], index


def _fits(reminder: ReminderInfo, of_task: bool) -> bool:
    if reminder.action not in ("DISPLAY", "AUDIO", "EMAIL"):
        return False
    if of_task:  # minutes always count from the due time, and a row can say a set time
        return bool(reminder.at) or (reminder.minutes_before is not None and reminder.minutes_before >= 0)
    return not reminder.at and not reminder.relative_to_end and reminder.minutes_before is not None and reminder.minutes_before >= 0


def _words(reminder: ReminderInfo, of_task: bool) -> str:
    """A reminder from elsewhere the fields cannot say: shown as it is, and it stays as it is."""
    if reminder.at or reminder.minutes_before is None:
        return f"At {reminder.at.replace('T', ' ')[:16]}" if reminder.at else "At a set time"
    place = "the due time" if of_task else "the end" if reminder.relative_to_end else "the start"
    return f"{counted(abs(reminder.minutes_before), 'minute')} {'before' if reminder.minutes_before >= 0 else 'after'} {place}"


class UnitChoice(QComboBox):
    """The unit of a row. Without a due date the units counted from it are greyed; one the row already has stays chosen, its text greyed as well."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: QStandardItemModel = QStandardItemModel(self)
        self.setModel(self._items)
        self._has_due: bool = True
        self.currentIndexChanged.connect(self._follow_choice)

    def _counts_from_due(self, index: int) -> bool:
        return self.itemData(index) != AT_A_SET_TIME

    def _greyed(self) -> bool:
        return not self._has_due and self._counts_from_due(self.currentIndex())

    def _follow_choice(self) -> None:
        self.setToolTip(NEEDS_A_DUE_DATE if self._greyed() else "")
        self.update()

    def follow_due(self, has_due: bool) -> None:
        self._has_due = has_due
        for index in range(self.count()):
            usable = has_due or not self._counts_from_due(index)
            row = self._items.item(index)
            row.setEnabled(usable)
            row.setToolTip("" if usable else NEEDS_A_DUE_DATE)
        self._follow_choice()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt's name
        if not self._greyed():
            super().paintEvent(event)
            return
        painter = QStylePainter(self)  # the list itself stays usable: only its text is drawn as greyed
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        palette = QPalette(self.palette())
        palette.setCurrentColorGroup(QPalette.ColorGroup.Disabled)
        setattr(option, "palette", palette)  # the stubs do not know the field
        painter.setPen(palette.color(QPalette.ColorRole.ButtonText))
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class ReminderRow(QWidget):
    def __init__(self, removed: Callable[["ReminderRow"], None], reminder: ReminderInfo | None = None, index: int = -1, of_task: bool = False, at_a_set_time: bool = False) -> None:
        super().__init__()
        form = forms.fill(self, "event_reminder", {"unit": UnitChoice})  # D-276
        self.kind: QComboBox = part(form, QComboBox, "kind")
        self.kind.addItem("Notification", "")
        self.kind.addItem("Email", "email:")
        self.number: QSpinBox = part(form, QSpinBox, "number")
        self.number.setRange(0, MOST_OF_A_UNIT)
        self.unit: UnitChoice = part(form, UnitChoice, "unit")
        for name, minutes in UNITS:
            self.unit.addItem(name, minutes)
        self.date: QDateEdit = part(form, QDateEdit, "date")
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.time: QTimeEdit = part(form, QTimeEdit, "time")
        self.time.setDisplayFormat("HH:mm")
        if of_task:
            self.unit.addItem("At a set time…", AT_A_SET_TIME)
            next_hour = QDateTime.currentDateTime().addSecs(3600)  # what a first set time starts from: a moment still to come
            self.date.setDate(next_hour.date())
            self.time.setTime(QTime(next_hour.time().hour(), 0))
        self.fixed: QLabel = part(form, QLabel, "fixed")
        self.remove: QToolButton = part(form, QToolButton, "remove")
        self._removed: Callable[["ReminderRow"], None] = removed
        self.remove.clicked.connect(self._remove)
        self._index: int = index  # its place in the event: left as it was, it stays exactly as it was
        self._has_due: bool = True
        self._fits: bool = reminder is None or _fits(reminder, of_task)
        self._fixed_at_a_set_time: bool = not self._fits and reminder is not None and bool(reminder.at)  # of a row that can change, its unit says
        self.fixed.setVisible(not self._fits)
        self.kind.setVisible(self._fits)
        self.unit.setVisible(self._fits)
        self.number.setValue(15)
        if reminder is None:
            if at_a_set_time:
                self.unit.setCurrentIndex(self.unit.findData(AT_A_SET_TIME))
        elif self._fits:
            self.kind.setCurrentIndex(1 if reminder.action == "EMAIL" else 0)
            if reminder.at:
                self._show_moment(reminder.at)
                self.unit.setCurrentIndex(self.unit.findData(AT_A_SET_TIME))
            else:
                number, unit = _unit_of(reminder.minutes_before or 0)
                self.number.setValue(number)
                self.unit.setCurrentIndex(unit)
        else:
            self.fixed.setText(_words(reminder, of_task))
        self._first: str = self._text()
        self.unit.currentIndexChanged.connect(self._follow)
        self._follow()

    def _remove(self) -> None:
        self._removed(self)

    def _show_moment(self, text: str) -> None:
        """The fixed time of a reminder, on this computer's clock, as the due date is shown (RV-23)."""
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:
            moment = in_local_zone(moment)
        self.date.setDate(QDate(moment.year, moment.month, moment.day))
        self.time.setTime(QTime(moment.hour, moment.minute))

    def _at_a_set_time(self) -> bool:
        return self.unit.currentData() == AT_A_SET_TIME

    def _follow(self) -> None:
        at_a_set_time = self._at_a_set_time()
        self.number.setVisible(self._fits and not at_a_set_time)
        self.date.setVisible(at_a_set_time)
        self.time.setVisible(at_a_set_time)
        does_nothing = not self._has_due and not at_a_set_time and not self._fixed_at_a_set_time
        for field in (self.kind, self.number, self.fixed):  # the unit stays usable: it leads to a set time
            field.setEnabled(not does_nothing)
            field.setToolTip(NEEDS_A_DUE_DATE if does_nothing else "")

    def follow_due(self, has_due: bool) -> None:
        self._has_due = has_due
        self.unit.follow_due(has_due)
        self._follow()

    def _text(self) -> str:
        if self._at_a_set_time():
            return f"{self.kind.currentData()}at:{self.date.date().toString('yyyy-MM-dd')}T{self.time.time().toString('HH:mm')}"
        return f"{self.kind.currentData()}{self.number.value() * int(self.unit.currentData())}"

    def value(self) -> str:
        """As event.add, event.edit, task.add and task.edit take it."""
        return f"keep:{self._index}" if self._index >= 0 and self._text() == self._first else self._text()


class ReminderList:
    """The rows in the window, and Add notification, greyed at the owner's number."""

    def __init__(self, rows: QVBoxLayout, add_button: QPushButton, of_task: bool = False) -> None:
        self._layout: QVBoxLayout = rows
        self.add_button: QPushButton = add_button
        self.rows: list[ReminderRow] = []
        self._before: tuple[str, ...] = ()
        self._on: bool = True
        self._of_task: bool = of_task
        self._has_due: bool = True
        add_button.clicked.connect(self.add)

    def keep_room_for_the_widest_row(self) -> None:
        """The rows' part is as wide as a row at a set time, also while it has no row. That row is only measured: it is never in the window."""
        widest = ReminderRow(self.remove, of_task=self._of_task, at_a_set_time=True)
        self._layout.addSpacerItem(QSpacerItem(widest.minimumSizeHint().width(), 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))

    def show_reminders(self, reminders: tuple[ReminderInfo, ...]) -> None:
        for row in self.rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self.rows = []
        for index, reminder in enumerate(reminders):
            self._add_row(ReminderRow(self.remove, reminder, index, self._of_task))
        self._before = self.values()
        self._follow()

    def _add_row(self, row: ReminderRow) -> None:
        self.rows.append(row)
        self._layout.addWidget(row)
        row.follow_due(self._has_due)

    def add(self) -> None:
        self._add_row(ReminderRow(self.remove, of_task=self._of_task, at_a_set_time=not self._has_due))  # without a due date only a set time does something
        self._follow()

    def remove(self, row: ReminderRow) -> None:
        self.rows.remove(row)
        self._layout.removeWidget(row)
        row.deleteLater()
        self._follow()

    def follow_due(self, has_due: bool) -> None:
        self._has_due = has_due
        for row in self.rows:
            row.follow_due(has_due)

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
