from typing import override

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QTreeView

# --- Version ------------------------------------------------------------
__version__ = "messagelistview.py 0.0.48"

# --- Version history ----------------------------------------------------
# v0.0.48: Initial message list that drags a small badge instead of the selected rows

BADGE_OFFSET: int = 22  # the badge hangs this far right of and below the pointer, so that it never covers what is dropped on


def drag_badge(count: int) -> QPixmap:
    """A small label ("3 messages") for a drag. The rows themselves are not drawn: a long selection would hide the folder under the pointer."""
    text = "1 message" if count == 1 else f"{count} messages"
    font = QApplication.font()
    metrics = QFontMetrics(font)
    width, height = metrics.horizontalAdvance(text) + 20, metrics.height() + 10
    pixmap = QPixmap(width + BADGE_OFFSET, height + BADGE_OFFSET)
    pixmap.fill(Qt.GlobalColor.transparent)  # empty margin above and left: the pointer stays free
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    area = QRect(BADGE_OFFSET, BADGE_OFFSET, width, height)
    painter.setBrush(QColor(40, 90, 160, 230))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(area, 8, 8)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(area, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


class MessageListView(QTreeView):
    """The message list. Dragging messages shows a small badge with their number, never the rows."""

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        indexes = [index for index in self.selectedIndexes() if index.column() == 0]
        model = self.model()
        data = model.mimeData(indexes) if indexes else None
        if data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(drag_badge(len(indexes)))
        drag.setHotSpot(QPoint(0, 0))
        drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
