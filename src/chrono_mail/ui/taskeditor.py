import html
from datetime import date, datetime
from typing import Literal, override

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, Signal
from PySide6.QtGui import QPaintEvent, QPalette, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTimeEdit,
    QWidget,
)

from chrono_mail.pim.times import in_local_zone
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_tasklist import TASK_ADD, TASK_EDIT, TaskInfo
from chrono_mail.engine.commands_tasklist import AddParameters as TaskAdd
from chrono_mail.engine.commands_tasklist import EditParameters as TaskEdit
from chrono_mail.core.words import counted
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "taskeditor.py 4.4.546"

# --- Version history ----------------------------------------------------
# v4.4.546: the Reminder list always offers At a set time… with a date and a time; without a due date the choices counted from it are greyed with a hover text, and a task that has one keeps it, shown greyed (D-316)
# v4.0.378: the task editor and its date fields are built from their Qt Designer forms (ui/forms/task_editor.ui, task_date.ui, D-276); its
#           sizes follow the size factor
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v3.6.349: a task's times through the zone the engine reads them back in (also never the platform's conversion, RV-201)
# v3.2.342: a repeat or reminder the lists do not offer is shown as it is and kept; both are sent only when changed (RV-19); due and start
#           times are shown on this computer's clock (RV-23)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: parameters gives the typed edit (EditParameters); add and edit are typed calls; the task is a TaskInfo
# v0.0.63: Initial editor for a task

PRIORITIES: tuple[tuple[int, str], ...] = ((0, "None"), (1, "High"), (5, "Normal"), (9, "Low"))
REPEATS: tuple[tuple[str, str], ...] = (
    ("", "Does not repeat"), ("FREQ=DAILY", "Every day"), ("FREQ=WEEKLY", "Every week"), ("FREQ=MONTHLY", "Every month"), ("FREQ=YEARLY", "Every year"),
)
AT_A_SET_TIME: str = "at"
REMINDERS: tuple[tuple[str, str], ...] = (
    ("", "No reminder"), ("0", "At the due time"), ("15", "15 minutes before"), ("60", "1 hour before"), ("1440", "1 day before"), ("10080", "1 week before"),
    (AT_A_SET_TIME, "At a set time…"),
)
NEEDS_A_DUE_DATE: str = "This counts from the due date. Give the task a due date to use it."
TASK_STATUSES: dict[str, Literal["needs-action", "in-process", "completed", "cancelled"]] = {
    "needs-action": "needs-action", "in-process": "in-process", "completed": "completed", "cancelled": "cancelled",
}
STATUSES: tuple[tuple[str, str], ...] = (("needs-action", "Not started"), ("in-process", "In progress"), ("completed", "Completed"), ("cancelled", "Cancelled"))


class DateField(QWidget):
    """A date that may be left out, with a time that may be left out."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "task_date")  # the tick, the date and All day (D-276)
        self.enabled: QCheckBox = part(form, QCheckBox, "enabled")
        self.enabled.setText(label)
        self.stamp: QDateTimeEdit = part(form, QDateTimeEdit, "stamp")
        self.stamp.setCalendarPopup(True)
        self.stamp.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.stamp.setDateTime(QDateTime.currentDateTime())
        self.all_day: QCheckBox = part(form, QCheckBox, "allDay")
        self.enabled.toggled.connect(self._changed)
        self.all_day.toggled.connect(self._changed)
        self._changed()

    def _changed(self) -> None:
        self.stamp.setEnabled(self.enabled.isChecked())
        self.all_day.setEnabled(self.enabled.isChecked())
        self.stamp.setDisplayFormat("yyyy-MM-dd" if self.all_day.isChecked() else "yyyy-MM-dd HH:mm")

    def set_value(self, text: str) -> None:
        """From the ISO text of a task ('2026-10-12' or '2026-10-12T17:00:00+02:00'); empty means none."""
        if not text:
            self.enabled.setChecked(False)
            return
        self.enabled.setChecked(True)
        if len(text) == 10:
            self.all_day.setChecked(True)
            self.stamp.setDate(QDate.fromString(text, "yyyy-MM-dd"))
            return
        self.all_day.setChecked(False)
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:  # shown on this computer's clock, as the list shows it and as value() gives it back (RV-23)
            moment = in_local_zone(moment)  # shown in the zone the engine reads it back in
        self.stamp.setDateTime(QDateTime(QDate(moment.year, moment.month, moment.day), self.stamp.time().fromString(f"{moment.hour:02d}:{moment.minute:02d}", "HH:mm")))

    def value(self) -> str:
        """The text for the commands: empty when none; a date for all day; else a time in this computer's zone."""
        if not self.enabled.isChecked():
            return ""
        return self.stamp.date().toString("yyyy-MM-dd") if self.all_day.isChecked() else self.stamp.dateTime().toString("yyyy-MM-dd'T'HH:mm")


class ReminderChoice(QComboBox):
    """The Reminder list. Without a due date the choices counted from it are greyed; one the task already has stays chosen, its text greyed as well."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: QStandardItemModel = QStandardItemModel(self)
        self.setModel(self._items)
        self._has_due: bool = True
        self.currentIndexChanged.connect(self._follow_choice)

    def _counts_from_due(self, index: int) -> bool:
        return str(self.itemData(index)) not in ("", AT_A_SET_TIME)

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


class TaskEditor(QDialog):
    """Makes a task or changes one. The commands do the saving."""

    saved = Signal(str)

    def __init__(self, backend: Backend, lists: list[CollectionSummary], known_tags: list[str], task: TaskInfo | None = None, collection: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: TaskInfo | None = task
        self._repeat_before: str = ""  # the repeat and reminder choices as the task had them: sent only when changed (RV-19)
        self._reminder_before: str = ""
        self._item: str | None = None if task is None else task.item
        self.setWindowTitle("New task" if task is None else f"Task: {task.summary}")
        form = forms.fill(self, "task_editor", {"startField": lambda parent: DateField("Start", parent), "dueField": lambda parent: DateField("Due", parent),
                                                 "reminder": ReminderChoice})  # D-276
        self.summary: QLineEdit = part(form, QLineEdit, "summary")
        self.list: QComboBox = part(form, QComboBox, "list")
        for info in lists:
            self.list.addItem(f"{info.account_name} — {info.name}", info.collection)
        self.list.setCurrentIndex(max(0, self.list.findData(collection)))
        self.list.setEnabled(task is None and self.list.count() > 1)
        self.start: DateField = part(form, DateField, "startField")
        self.due: DateField = part(form, DateField, "dueField")
        self.priority: QComboBox = part(form, QComboBox, "priority")
        for value, label in PRIORITIES:
            self.priority.addItem(label, value)
        self.status: QComboBox = part(form, QComboBox, "status")
        for value, label in STATUSES:
            self.status.addItem(label, value)
        self.percent: QSpinBox = part(form, QSpinBox, "percent")
        self.percent.setRange(0, 100)
        self.repeat: QComboBox = part(form, QComboBox, "repeat")
        for value, label in REPEATS:
            self.repeat.addItem(label, value)
        self.repeat.setEditable(False)
        self.reminder: ReminderChoice = part(form, ReminderChoice, "reminder")
        for value, label in REMINDERS:
            self.reminder.addItem(label, value)
        self.reminder_date: QDateEdit = part(form, QDateEdit, "reminderDate")
        self.reminder_date.setCalendarPopup(True)
        self.reminder_date.setDisplayFormat("yyyy-MM-dd")
        self.reminder_time: QTimeEdit = part(form, QTimeEdit, "reminderTime")
        self.reminder_time.setDisplayFormat("HH:mm")
        next_hour = QDateTime.currentDateTime().addSecs(3600)  # what a first set time starts from: a moment still to come
        self.reminder_date.setDate(next_hour.date())
        self.reminder_time.setTime(QTime(next_hour.time().hour(), 0))
        self.location: QLineEdit = part(form, QLineEdit, "location")
        self.note: QPlainTextEdit = part(form, QPlainTextEdit, "note")
        self.note.setMinimumHeight(wpx(90))
        self.tags: QLineEdit = part(form, QLineEdit, "tags")
        completer = QCompleter(known_tags, self.tags)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags.setCompleter(completer)
        self.result_label: QLabel = part(form, QLabel, "result")
        buttons = part(form, QDialogButtonBox, "buttons")
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size, the date and time of the reminder counted
        if task is not None:
            self._fill(task)
        self.due.enabled.toggled.connect(self.reminder.follow_due)
        self.reminder.currentIndexChanged.connect(self._follow_reminder)
        self.reminder.follow_due(self.due.enabled.isChecked())
        self._follow_reminder()

    def _fill(self, task: TaskInfo) -> None:
        self.summary.setText(task.summary)
        self.start.set_value(task.start)
        self.due.set_value(task.due)
        priority = task.priority
        self.priority.setCurrentIndex(max(0, self.priority.findData(priority if priority in (0, 1, 5, 9) else (1 if priority < 5 else (5 if priority == 5 else 9)))))
        self.status.setCurrentIndex(max(0, self.status.findData(task.status.lower())))
        self.percent.setValue(task.percent)
        if task.rrule and self.repeat.findData(task.rrule) < 0:  # a rule from another program: shown and kept as it is (RV-19)
            self.repeat.addItem(f"Custom: {task.rrule}", task.rrule)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(task.rrule)))
        reminders = tuple(task.reminders)
        first = ("at" if reminders[0] is None else str(reminders[0])) if reminders else ""
        if first == AT_A_SET_TIME:
            self._show_moment(task.reminder_moments[0])
        elif first and self.reminder.findData(first) < 0:  # a time the list does not offer: shown and kept (RV-19)
            self.reminder.addItem(f"{counted(int(first), 'minute')} before", first)
        self.reminder.setCurrentIndex(max(0, self.reminder.findData(first)))
        self._repeat_before, self._reminder_before = str(self.repeat.currentData()), self._reminder_value()
        self.note.setPlainText(task.description)
        self.location.setText(task.location)
        self.tags.setText(", ".join(task.tags))

    def _show_moment(self, text: str) -> None:
        """The fixed time of a reminder, on this computer's clock, as the due date is shown (RV-23)."""
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is not None:
            moment = in_local_zone(moment)
        self.reminder_date.setDate(QDate(moment.year, moment.month, moment.day))
        self.reminder_time.setTime(QTime(moment.hour, moment.minute))

    def _follow_reminder(self) -> None:
        at_a_set_time = self.reminder.currentData() == AT_A_SET_TIME
        self.reminder_date.setVisible(at_a_set_time)
        self.reminder_time.setVisible(at_a_set_time)

    def _reminder_value(self) -> str:
        """The choice as the commands take it: empty for none."""
        chosen = str(self.reminder.currentData())
        if chosen != AT_A_SET_TIME:
            return chosen
        return f"at:{self.reminder_date.date().toString('yyyy-MM-dd')}T{self.reminder_time.time().toString('HH:mm')}"

    def parameters(self) -> TaskEdit:
        """What is in the editor, as task.add and task.edit take it: the fields, and the lists emptied here (clear)."""
        tags = tuple(part.strip() for part in self.tags.text().split(",") if part.strip())
        original = self._original
        chosen = self._reminder_value()
        reminder_changed = original is None or chosen != self._reminder_before  # sent only when changed (RV-19)
        reminder = (chosen,) if chosen and reminder_changed else ()
        repeat = str(self.repeat.currentData())
        clear = tuple(name for name, entries, had in (("tag", tags, original is not None and bool(original.tags)),
                                                      ("reminder", reminder, original is not None and bool(original.reminders) and reminder_changed))
                      if not entries and had)
        return TaskEdit(
            summary=self.summary.text().strip(), description=self.note.toPlainText().strip(), location=self.location.text().strip(), start=self.start.value(),
            due=self.due.value(), priority=int(self.priority.currentData()), percent=self.percent.value(), status=TASK_STATUSES.get(str(self.status.currentData()), "needs-action"),
            rrule=repeat if original is None or repeat != self._repeat_before else None, tag=tags, reminder=reminder, item=self._item or "", clear=clear,
        )

    def _save(self) -> None:
        values = self.parameters()
        if not values.summary:
            self.result_label.setText(f"<span style='color:{red()}'>The task needs a summary.</span>")
            return
        if values.rrule and not values.due and not values.start:
            self.result_label.setText(f"<span style='color:{red()}'>A repeating task needs a start or a due date.</span>")
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: TaskInfo) -> None:
            self.saved.emit(result.item)
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        if self._item is None:
            self._backend.ask(TASK_ADD, carried(values, TaskAdd, list=str(self.list.currentData())), done, failed, owner=self)
        else:
            self._backend.ask(TASK_EDIT, values, done, failed, owner=self)
