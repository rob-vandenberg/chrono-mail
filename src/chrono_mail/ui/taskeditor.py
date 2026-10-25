import html
from datetime import date, datetime
from typing import Literal

from PySide6.QtCore import QDate, QDateTime, Qt, Signal
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
from chrono_mail.core.words import counted
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "taskeditor.py 3.6.350"

# --- Version history ----------------------------------------------------
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
REMINDERS: tuple[tuple[str, str], ...] = (
    ("", "No reminder"), ("0", "At the due time"), ("15", "15 minutes before"), ("60", "1 hour before"), ("1440", "1 day before"), ("10080", "1 week before"),
)
TASK_STATUSES: dict[str, Literal["needs-action", "in-process", "completed", "cancelled"]] = {
    "needs-action": "needs-action", "in-process": "in-process", "completed": "completed", "cancelled": "cancelled",
}
STATUSES: tuple[tuple[str, str], ...] = (("needs-action", "Not started"), ("in-process", "In progress"), ("completed", "Completed"), ("cancelled", "Cancelled"))


class DateField(QWidget):
    """A date that may be left out, with a time that may be left out."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox(label, self)
        self.stamp = QDateTimeEdit(self)
        self.stamp.setCalendarPopup(True)
        self.stamp.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.stamp.setDateTime(QDateTime.currentDateTime())
        self.all_day = QCheckBox("All day", self)
        layout.addWidget(self.enabled)
        layout.addWidget(self.stamp)
        layout.addWidget(self.all_day)
        layout.addStretch(1)
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
        self._repeat_before: str = ""  # the repeat and reminder choices as the task had them: sent only when changed (RV-19)
        self._reminder_before: str = ""
        self._item: str | None = None if task is None else task.item
        self.setWindowTitle("New task" if task is None else f"Task: {task.summary}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.summary = QLineEdit(self)
        self.list = QComboBox(self)
        for info in lists:
            self.list.addItem(f"{info.account_name} — {info.name}", info.collection)
        self.list.setCurrentIndex(max(0, self.list.findData(collection)))
        self.list.setEnabled(task is None and self.list.count() > 1)
        self.start = DateField("Start", self)
        self.due = DateField("Due", self)
        self.priority = QComboBox(self)
        for value, label in PRIORITIES:
            self.priority.addItem(label, value)
        self.status = QComboBox(self)
        for value, label in STATUSES:
            self.status.addItem(label, value)
        self.percent = QSpinBox(self)
        self.percent.setRange(0, 100)
        self.percent.setSuffix(" %")
        self.repeat = QComboBox(self)
        for value, label in REPEATS:
            self.repeat.addItem(label, value)
        self.repeat.setEditable(False)
        self.reminder = QComboBox(self)
        for value, label in REMINDERS:
            self.reminder.addItem(label, value)
        self.location = QLineEdit(self)
        self.note = QPlainTextEdit(self)
        self.note.setMinimumHeight(90)
        self.tags = QLineEdit(self)
        self.tags.setPlaceholderText("Tags, separated by commas")
        completer = QCompleter(known_tags, self.tags)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags.setCompleter(completer)
        form.addRow("Summary", self.summary)
        form.addRow("Task list", self.list)
        form.addRow("", self.start)
        form.addRow("", self.due)
        form.addRow("Priority", self.priority)
        form.addRow("Status", self.status)
        form.addRow("Done", self.percent)
        form.addRow("Repeat", self.repeat)
        form.addRow("Reminder", self.reminder)
        form.addRow("Location", self.location)
        form.addRow("Tags", self.tags)
        layout.addLayout(form)
        layout.addWidget(QLabel("Note", self))
        layout.addWidget(self.note)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if task is not None:
            self._fill(task)

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
        if first and self.reminder.findData(first) < 0:  # a time the list does not offer, or a fixed moment: shown and kept (RV-19)
            self.reminder.addItem("At a set time" if first == "at" else f"{counted(int(first), 'minute')} before", first)
        self.reminder.setCurrentIndex(max(0, self.reminder.findData(first)))
        self._repeat_before, self._reminder_before = str(self.repeat.currentData()), str(self.reminder.currentData())
        self.note.setPlainText(task.description)
        self.location.setText(task.location)
        self.tags.setText(", ".join(task.tags))

    def parameters(self) -> TaskEdit:
        """What is in the editor, as task.add and task.edit take it: the fields, and the lists emptied here (clear)."""
        tags = tuple(part.strip() for part in self.tags.text().split(",") if part.strip())
        original = self._original
        reminder_changed = original is None or str(self.reminder.currentData()) != self._reminder_before  # sent only when changed (RV-19)
        reminder = (str(self.reminder.currentData()),) if self.reminder.currentData() != "" and reminder_changed else ()
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
