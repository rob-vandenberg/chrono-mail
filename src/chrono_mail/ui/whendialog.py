from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "whendialog.py 0.0.34"

# --- Version history ----------------------------------------------------
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

    def __init__(self, title: str, question: str, ok_text: str, parent: QWidget | None = None, now: datetime | None = None) -> None:
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
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def chosen(self) -> str:
        """The time as the commands want it: 2026-10-11T09:00."""
        return self.edit.dateTime().toString("yyyy-MM-ddTHH:mm")
