from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QFocusEvent, QFont, QFontMetrics, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget

from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.dragdata import read_messages_mime_data
from chrono_mail.ui.models import ACCOUNT_ROLE, COUNT_ROLE, FOLDER_PATH_ROLE, KIND_ROLE
from chrono_mail.ui.theme import show_focus

# --- Version ------------------------------------------------------------
__version__ = "foldertree.py 0.0.88"

# --- Version history ----------------------------------------------------
# v0.0.88: The selection is blue while the folder list has the focus and grey without it
# v0.0.87: The number of unread messages is drawn as a bold number at the right edge
# v0.0.17: Initial folder tree that accepts dropped messages

COUNT_MARGIN: int = 8  # pixels between the number of unread messages and the right edge
COUNT_GAP: int = 6  # pixels kept free between the name and the number


class FolderItemDelegate(QStyledItemDelegate):
    """Draws a folder as the style does, with the number of unread messages (when there are any) in bold at the right edge; the name is shortened before it reaches the number."""

    @staticmethod
    def count_text(index: QModelIndex | QPersistentModelIndex) -> str:
        count = int(index.data(COUNT_ROLE) or 0)
        return str(count) if count > 0 else ""

    @staticmethod
    def has_focus(option: QStyleOptionViewItem) -> bool:
        found: object = getattr(option, "widget")
        return not isinstance(found, QWidget) or found.property("focused") is not False

    @override
    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        super().initStyleOption(option, index)
        if _is_selected(option) and not self.has_focus(option):  # on the grey selection the icon keeps the colour of text
            icon = _typed(option, "icon", QIcon)
            normal = icon.pixmap(_typed(option, "decorationSize", QSize), QIcon.Mode.Normal)
            plain = QIcon()
            plain.addPixmap(normal, QIcon.Mode.Normal)
            plain.addPixmap(normal, QIcon.Mode.Selected)
            setattr(option, "icon", plain)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        count = self.count_text(index)
        if not count:
            super().paint(painter, option, index)
            return
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        bold = QFont(_typed(item, "font", QFont))
        bold.setBold(True)
        count_width = QFontMetrics(bold).horizontalAdvance(count)
        found: object = getattr(option, "widget")
        widget = found if isinstance(found, QWidget) else None
        style = widget.style() if widget is not None else QApplication.style()
        full = _rect(option)
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        room = full.right() - COUNT_MARGIN - count_width - COUNT_GAP - text_rect.left()
        name = _typed(item, "fontMetrics", QFontMetrics).elidedText(_typed(item, "text", str), Qt.TextElideMode.ElideRight, max(room, 0))
        setattr(item, "text", name)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, widget)
        palette = _typed(option, "palette", QPalette)
        painter.save()
        painter.setFont(bold)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText if _is_selected(option) and self.has_focus(option) else QPalette.ColorRole.Text))
        painter.drawText(full.adjusted(0, 0, -COUNT_MARGIN, 0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), count)
        painter.restore()

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        count = self.count_text(index)
        if count:
            bold = QFont(_typed(option, "font", QFont))
            bold.setBold(True)
            size.setWidth(size.width() + COUNT_GAP + QFontMetrics(bold).horizontalAdvance(count) + COUNT_MARGIN)
        return size


class FolderTree(QTreeView):
    """The folder tree. Messages dragged from the list can be dropped on a folder of the same account."""

    messages_dropped = Signal(str, str, list)  # account id, target folder path, [(folder, message id)]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)
        self.folder_delegate = FolderItemDelegate(self)
        self.setItemDelegate(self.folder_delegate)
        self.setProperty("focused", False)

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        show_focus(self, True)

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:  # a context menu leaves the selection as it is
            show_focus(self, False)

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
