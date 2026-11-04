from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_reminders import REMINDER_CHECK, ReminderCheck, ReminderNote
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.tray import TrayController

# --- Version ------------------------------------------------------------
__version__ = "reminders.py 4.4.536"

# --- Version history ----------------------------------------------------
# v4.4.536: Initial version: while Chrono Mail runs it looks for reminders that came due, and shows them (D-313)

LOOK_EVERY_MILLISECONDS: int = 30_000  # half a minute: the smallest step of a reminder is a minute


class ReminderWatch(QObject):
    """Asks the engine every half minute; the engine sends the e-mails, this shows the notifications: by the tray, or in a window of its own without one."""

    def __init__(self, backend: Backend, tray: TrayController, window: QWidget) -> None:
        super().__init__(window)
        self._backend: Backend = backend
        self._tray: TrayController = tray
        self._window: QWidget = window
        self.timer = QTimer(self)
        self.timer.setInterval(LOOK_EVERY_MILLISECONDS)
        self.timer.timeout.connect(self.look)
        self.timer.start()

    def look(self) -> None:
        self._backend.ask(REMINDER_CHECK, NoParameters(), self._arrived, self._failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        """Looked at again in half a minute: what came due stays due, the last look moved only when it was done."""

    def _arrived(self, result: ReminderCheck) -> None:
        for note in result.notes:
            self.show_note(note)

    def show_note(self, note: ReminderNote) -> None:
        text = "\n".join(line for line in (note.when, note.location) if line)
        if self._tray.notify(f"Reminder: {note.summary}", text):
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Reminder", f"{note.summary}\n{text}", QMessageBox.StandardButton.Ok, self._window)
        box.setTextFormat(Qt.TextFormat.PlainText)  # never markup (RV-167)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()
