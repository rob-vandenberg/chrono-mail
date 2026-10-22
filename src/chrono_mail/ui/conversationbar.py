from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from chrono_mail.engine.changes import MessageRow

# --- Version ------------------------------------------------------------
__version__ = "conversationbar.py 1.9.274"

# --- Version history ----------------------------------------------------
# v1.9.274: R-19 round 4: the rows of the conversation are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v0.0.18: Initial conversation bar

ROLE_FOLDER: int = Qt.ItemDataRole.UserRole + 1
ROLE_MESSAGE: int = Qt.ItemDataRole.UserRole + 2
ROLE_SEEN: int = Qt.ItemDataRole.UserRole + 3
VISIBLE_ROWS: int = 4


def _who(sender: str) -> str:
    """The name part of 'Name <address>', else the address."""
    name, separator, _ = sender.partition("<")
    return name.strip().strip('"') or sender.strip("<> ") if separator else sender


class ConversationBar(QFrame):
    """The messages of the conversation of the message being read, above the reading pane. Clicking one shows it."""

    message_chosen = Signal(str, str, bool)  # folder, message id, whether it is read already

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        self._title = QLabel(self)
        self._list = QListWidget(self)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        layout.addWidget(self._title)
        layout.addWidget(self._list)
        self._list.itemClicked.connect(self._chosen)
        self.hide()

    def clear(self) -> None:
        self._list.clear()
        self.hide()

    def show_thread(self, rows: list[MessageRow], current_id: str) -> None:
        """Show the conversation (oldest first). One message alone is not a conversation: the bar stays hidden."""
        self._list.clear()
        if len(rows) < 2:
            self.hide()
            return
        self._title.setText(f"Conversation: {len(rows)} messages")
        for row in rows:
            date = row.date
            when = date.astimezone().strftime("%d %b %H:%M") if isinstance(date, datetime) else ""
            folder = row.folder
            where = "" if folder == "Inbox" else f"  [{folder}]"
            item = QListWidgetItem(f"{_who(row.sender or row.recipients)}  ·  {when}{where}")
            item.setData(ROLE_FOLDER, folder)
            item.setData(ROLE_MESSAGE, row.message_id)
            item.setData(ROLE_SEEN, "seen" in row.flags)
            if "seen" not in row.flags:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            item.setToolTip(row.subject)
            self._list.addItem(item)
            if row.message_id == current_id:
                self._list.setCurrentItem(item)
        line = self._list.sizeHintForRow(0) if self._list.count() else 20
        self._list.setFixedHeight(min(self._list.count(), VISIBLE_ROWS) * max(line, 20) + 4)
        self.show()

    def _chosen(self, item: QListWidgetItem) -> None:
        self.message_chosen.emit(str(item.data(ROLE_FOLDER)), str(item.data(ROLE_MESSAGE)), bool(item.data(ROLE_SEEN)))
