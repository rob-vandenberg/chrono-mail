from datetime import date, datetime

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

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "taskeditor.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: Initial editor for a task

PRIORITIES: tuple[tuple[int, str], ...] = ((0, "None"), (1, "High"), (5, "Normal"), (9, "Low"))
REPEATS: tuple[tuple[str, str], ...] = (
    ("", "Does not repeat"), ("FREQ=DAILY", "Every day"), ("FREQ=WEEKLY", "Every week"), ("FREQ=MONTHLY", "Every month"), ("FREQ=YEARLY", "Every year"),
)
REMINDERS: tuple[tuple[str, str], ...] = (
    ("", "No reminder"), ("0", "At the due time"), ("15", "15 minutes before"), ("60", "1 hour before"), ("1440", "1 day before"), ("10080", "1 week before"),
)
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
        self.stamp.setDateTime(QDateTime(QDate(moment.year, moment.month, moment.day), self.stamp.time().fromString(f"{moment.hour:02d}:{moment.minute:02d}", "HH:mm")))

    def value(self) -> str:
        """The text for the commands: empty when none; a date for all day; else a time in this computer's zone."""
        if not self.enabled.isChecked():
            return ""
        return self.stamp.date().toString("yyyy-MM-dd") if self.all_day.isChecked() else self.stamp.dateTime().toString("yyyy-MM-dd'T'HH:mm")


class TaskEditor(QDialog):
    """Makes a task or changes one. The commands do the saving."""

    saved = Signal(str)

    def __init__(self, backend: Backend, lists: list[object], known_tags: list[str], task: object | None = None, collection: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: object | None = task
        self._item: str | None = None if task is None else str(getattr(task, "item"))
        self.setWindowTitle("New task" if task is None else f"Task: {getattr(task, 'summary')}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.summary = QLineEdit(self)
        self.list = QComboBox(self)
        for info in lists:
            self.list.addItem(f"{getattr(info, 'account_name')} — {getattr(info, 'name')}", str(getattr(info, "collection")))
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

    def _fill(self, task: object) -> None:
        self.summary.setText(str(getattr(task, "summary")))
        self.start.set_value(str(getattr(task, "start")))
        self.due.set_value(str(getattr(task, "due")))
        priority = int(getattr(task, "priority"))
        self.priority.setCurrentIndex(max(0, self.priority.findData(priority if priority in (0, 1, 5, 9) else (1 if priority < 5 else (5 if priority == 5 else 9)))))
        self.status.setCurrentIndex(max(0, self.status.findData(str(getattr(task, "status")).lower())))
        self.percent.setValue(int(getattr(task, "percent")))
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(str(getattr(task, "rrule")))))
        reminders = tuple(getattr(task, "reminders"))
        self.reminder.setCurrentIndex(max(0, self.reminder.findData(str(reminders[0]) if reminders and reminders[0] is not None else "")))
        self.note.setPlainText(str(getattr(task, "description")))
        self.location.setText(str(getattr(task, "location")))
        self.tags.setText(", ".join(getattr(task, "tags")))

    def parameters(self) -> dict[str, object]:
        """The parameters of task.add or task.edit for what is in the editor."""
        values: dict[str, object] = {
            "summary": self.summary.text().strip(), "description": self.note.toPlainText().strip(), "location": self.location.text().strip(),
            "start": self.start.value(), "due": self.due.value(), "priority": int(self.priority.currentData()), "percent": self.percent.value(),
            "status": str(self.status.currentData()), "rrule": str(self.repeat.currentData()),
        }
        tags = [part.strip() for part in self.tags.text().split(",") if part.strip()]
        clear: list[str] = []
        if tags:
            values["tag"] = tags
        elif self._original is not None and getattr(self._original, "tags"):
            clear.append("tag")
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
            self.result_label.setText(f"<span style='color:{red()}'>The task needs a summary.</span>")
            return
        if values["rrule"] and not values["due"] and not values["start"]:
            self.result_label.setText(f"<span style='color:{red()}'>A repeating task needs a start or a due date.</span>")
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
            self._backend.call("task.add", {**values, "list": str(self.list.currentData())}, done, failed, owner=self)
        else:
            self._backend.call("task.edit", {**values, "item": self._item}, done, failed, owner=self)
