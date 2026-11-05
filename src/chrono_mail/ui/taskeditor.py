import html
from datetime import date, datetime
from typing import Literal

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.pim.times import in_local_zone
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_tasklist import TASK_ADD, TASK_EDIT, TaskInfo
from chrono_mail.engine.commands_tasklist import AddParameters as TaskAdd
from chrono_mail.engine.commands_tasklist import EditParameters as TaskEdit
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.colours import red
from chrono_mail.ui.eventreminders import ReminderList
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "taskeditor.py 4.4.548"

# --- Version history ----------------------------------------------------
# v4.4.548: the width of a reminder row is measured without a row in the window: a row removed again stayed on screen over Done and Repeat while the window was open
# v4.4.547: the reminders are the rows of an appointment (eventreminders.py): every reminder of a task is shown and kept, each Notification or Email, a number and a unit or a set time, at most 5 (D-316)
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


class TaskEditor(QDialog):
    """Makes a task or changes one. The commands do the saving."""

    saved = Signal(str)

    def __init__(self, backend: Backend, lists: list[CollectionSummary], known_tags: list[str], task: TaskInfo | None = None, collection: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: TaskInfo | None = task
        self._repeat_before: str = ""  # the repeat choice as the task had it: sent only when changed (RV-19)
        self._item: str | None = None if task is None else task.item
        self.setWindowTitle("New task" if task is None else f"Task: {task.summary}")
        form = forms.fill(self, "task_editor", {"startField": lambda parent: DateField("Start", parent), "dueField": lambda parent: DateField("Due", parent)})  # D-276
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
        self.reminders = ReminderList(part(form, QVBoxLayout, "reminderRows"), part(form, QPushButton, "addReminder"), of_task=True)
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
        self.reminders.keep_room_for_the_widest_row()
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size, the date and time of a reminder counted
        if task is not None:
            self._fill(task)
        self.due.enabled.toggled.connect(self.reminders.follow_due)
        self.reminders.follow_due(self.due.enabled.isChecked())

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
        self.reminders.show_reminders(tuple(task.reminders))
        self._repeat_before = str(self.repeat.currentData())
        self.note.setPlainText(task.description)
        self.location.setText(task.location)
        self.tags.setText(", ".join(task.tags))

    def parameters(self) -> TaskEdit:
        """What is in the editor, as task.add and task.edit take it: the fields, and the lists emptied here (clear)."""
        tags = tuple(part.strip() for part in self.tags.text().split(",") if part.strip())
        original = self._original
        reminder_changed = self.reminders.changed()  # sent only when changed; one left alone is kept whole (keep:, RV-19)
        reminder = self.reminders.values() if reminder_changed else ()
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
