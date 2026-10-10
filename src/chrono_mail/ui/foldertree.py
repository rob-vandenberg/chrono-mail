from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QTreeView, QWidget

from chrono_mail.ui.dragdata import read_messages_mime_data
from chrono_mail.ui.models import ACCOUNT_ROLE, FOLDER_PATH_ROLE, KIND_ROLE

# --- Version ------------------------------------------------------------
__version__ = "foldertree.py 0.0.17"

# --- Version history ----------------------------------------------------
# v0.0.17: Initial folder tree that accepts dropped messages

class FolderTree(QTreeView):
    """The folder tree. Messages dragged from the list can be dropped on a folder of the same account."""

    messages_dropped = Signal(str, str, list)  # account id, target folder path, [(folder, message id)]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)

    def _target(self, event: QDragMoveEvent | QDropEvent) -> tuple[str, str] | None:
        """The (account, folder) under the pointer when the dragged messages may go there."""
        dragged = read_messages_mime_data(event.mimeData())
        index = self.indexAt(event.position().toPoint())
        if dragged is None or not index.isValid() or index.data(KIND_ROLE) != "folder":
            return None
        account, folder = str(index.data(ACCOUNT_ROLE)), str(index.data(FOLDER_PATH_ROLE))
        if account != dragged[0] or folder == "Outbox" or all(source == folder for source, _ in dragged[1]):
            return None
        return account, folder

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if read_messages_mime_data(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._target(event) is not None:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        target = self._target(event)
        dragged = read_messages_mime_data(event.mimeData())
        if target is None or dragged is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.messages_dropped.emit(target[0], target[1], dragged[1])
