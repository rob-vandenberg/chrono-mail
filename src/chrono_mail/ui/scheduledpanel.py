from collections.abc import Callable
from typing import override

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "scheduledpanel.py 0.0.67"

# --- Version history ----------------------------------------------------
# v0.0.67: Messages that repeat can be paused, resumed and stopped
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.34: Initial window for scheduled and snoozed messages

ROLE_INFO: int = 256


class ScheduledPanel(QWidget):
    """The messages waiting to be sent at a time, and the messages snoozed until a time: send or bring back now, or take back."""

    def __init__(self, backend: Backend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.changed: bool = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Waiting to be sent, and put away until a time:", self))
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.now_button = QPushButton("Send or bring back now", self)
        self.back_button = QPushButton("Take back as a draft", self)
        row.addWidget(self.now_button)
        row.addWidget(self.back_button)
        self.pause_button = QPushButton("Pause", self)
        self.stop_button = QPushButton("Stop repeating", self)
        row.addWidget(self.pause_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.now_button.clicked.connect(self.now)
        self.back_button.clicked.connect(self.take_back)
        self.pause_button.clicked.connect(self.pause_or_resume)
        self.stop_button.clicked.connect(self.stop_repeating)
        self.list.currentItemChanged.connect(self._selection_changed)
        self._selection_changed()
        self.reload()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()  # messages may have been scheduled since the page was made

    def reload(self) -> None:
        entries: list[tuple[int, str, object]] = []

        def got_scheduled(result: object) -> None:
            for item in list(result) if isinstance(result, (tuple, list)) else []:
                text = f"Send {getattr(item, 'send_at')}   {getattr(item, 'subject') or '(no subject)'}  to {', '.join(getattr(item, 'to'))}"
                if getattr(item, "repeat"):
                    state = "paused" if getattr(item, "paused") else f"sent {getattr(item, 'times_sent')} times so far"
                    text += f"   [{getattr(item, 'repeat')}; {state}]"
                entries.append((int(getattr(item, "seconds")), text, item))
            self._backend.call("message.snoozed", {}, got_snoozed, self._failed, owner=self)

        def got_snoozed(result: object) -> None:
            for item in list(result) if isinstance(result, (tuple, list)) else []:
                entries.append((int(getattr(item, "seconds")), f"Back {getattr(item, 'until')}   a snoozed message (returns to {getattr(item, 'comes_back_to')})", item))
            self.list.clear()
            for _, text, info in sorted(entries, key=lambda entry: entry[0]):
                item = QListWidgetItem(text)
                item.setData(ROLE_INFO, info)
                self.list.addItem(item)
            if self.list.count():
                self.list.setCurrentRow(0)
            self._selection_changed()

        self._backend.call("mail.scheduled", {}, got_scheduled, self._failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        self.status.setText(f"<span style='color:{red()}'>{error}</span>")

    def _selected(self) -> object | None:
        item = self.list.currentItem()
        return None if item is None else item.data(ROLE_INFO)

    def _selection_changed(self) -> None:
        info = self._selected()
        self.now_button.setEnabled(info is not None)
        self.back_button.setEnabled(info is not None and hasattr(info, "send_at"))
        repeating = info is not None and bool(getattr(info, "repeat", ""))
        self.pause_button.setEnabled(repeating)
        self.stop_button.setEnabled(repeating)
        self.pause_button.setText("Resume" if repeating and bool(getattr(info, "paused", False)) else "Pause")

    def now(self) -> None:
        info = self._selected()
        if info is None:
            return
        done = self._done("Done")
        if hasattr(info, "send_at"):
            self._backend.call("mail.send-due", {"account": str(getattr(info, "account")), "message": str(getattr(info, "message_id"))}, done, self._failed, owner=self)
        else:
            self._backend.call("message.wake", {"account": str(getattr(info, "account")), "message": str(getattr(info, "message_id"))}, done, self._failed, owner=self)

    def _repeat_call(self, command: str, text: str) -> None:
        info = self._selected()
        if info is None or not getattr(info, "repeat", ""):
            return
        self._backend.call(command, {"account": str(getattr(info, "account")), "folder": "Outbox", "message": str(getattr(info, "message_id"))}, self._done(text), self._failed, owner=self)

    def pause_or_resume(self) -> None:
        info = self._selected()
        if info is not None and bool(getattr(info, "paused", False)):
            self._repeat_call("mail.repeat-resume", "It is sent again from now on")
        else:
            self._repeat_call("mail.repeat-pause", "Paused")

    def stop_repeating(self) -> None:
        if QMessageBox.question(self, "Stop repeating", "Stop sending this message and remove it? What was sent already stays in the Sent folder.") == QMessageBox.StandardButton.Yes:
            self._repeat_call("mail.repeat-cancel", "Stopped")

    def take_back(self) -> None:
        info = self._selected()
        if info is None or not hasattr(info, "send_at"):
            return
        self._backend.call("mail.unschedule", {"account": str(getattr(info, "account")), "folder": "Outbox", "message": str(getattr(info, "message_id"))}, self._done("It is a draft again"), self._failed, owner=self)

    def _done(self, text: str) -> Callable[[object], None]:
        def finished(result: object) -> None:
            self.changed = True
            self.status.setText(text)
            self.reload()

        return finished
