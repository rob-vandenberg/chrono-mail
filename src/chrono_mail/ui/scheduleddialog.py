from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "scheduleddialog.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial window for scheduled and snoozed messages

ROLE_INFO: int = 256


class ScheduledDialog(QDialog):
    """The messages waiting to be sent at a time, and the messages snoozed until a time: send or bring back now, or take back."""

    def __init__(self, backend: Backend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.changed: bool = False
        self.setWindowTitle("Scheduled and snoozed messages")
        self.setMinimumSize(560, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Waiting to be sent, and put away until a time:", self))
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.now_button = QPushButton("Send or bring back now", self)
        self.back_button = QPushButton("Take back as a draft", self)
        row.addWidget(self.now_button)
        row.addWidget(self.back_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.now_button.clicked.connect(self.now)
        self.back_button.clicked.connect(self.take_back)
        self.list.currentItemChanged.connect(self._selection_changed)
        self._selection_changed()
        self.reload()

    def reload(self) -> None:
        entries: list[tuple[int, str, object]] = []

        def got_scheduled(result: object) -> None:
            for item in list(result) if isinstance(result, (tuple, list)) else []:
                entries.append((int(getattr(item, "seconds")), f"Send {getattr(item, 'send_at')}   {getattr(item, 'subject') or '(no subject)'}  to {', '.join(getattr(item, 'to'))}", item))
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
        self.status.setText(f"<span style='color:#c0392b'>{error}</span>")

    def _selected(self) -> object | None:
        item = self.list.currentItem()
        return None if item is None else item.data(ROLE_INFO)

    def _selection_changed(self) -> None:
        info = self._selected()
        self.now_button.setEnabled(info is not None)
        self.back_button.setEnabled(info is not None and hasattr(info, "send_at"))

    def now(self) -> None:
        info = self._selected()
        if info is None:
            return
        done = self._done("Done")
        if hasattr(info, "send_at"):
            self._backend.call("mail.send-due", {"account": str(getattr(info, "account")), "message": str(getattr(info, "message_id"))}, done, self._failed, owner=self)
        else:
            self._backend.call("message.wake", {"account": str(getattr(info, "account")), "message": str(getattr(info, "message_id"))}, done, self._failed, owner=self)

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
