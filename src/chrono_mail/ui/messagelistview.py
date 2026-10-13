from collections.abc import Callable
from typing import override

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFocusEvent, QFontMetrics, QIcon, QMouseEvent, QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QTreeView, QWidget

from chrono_mail.ui.models import HEADER_ICON_ROLE

from chrono_mail.ui.theme import show_focus

# --- Version ------------------------------------------------------------
__version__ = "messagelistview.py 0.1.89"

# --- Version history ----------------------------------------------------
# v0.1.89: Clicks on the Contact and Delete icons; tells when the width of its rows changes; a header that centres its icons and remembers where a column drag started
# v0.0.88: The selection is blue while the message list has the focus and grey without it
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


HEADER_ICON: int = 16


class ListHeader(QHeaderView):
    """The header of the message list. It draws the icon of a column (Contact, Delete) in the middle of the column, and it
    does the dragging of column lines itself: it remembers the width of every column and the pointer position at the press
    and reports the whole distance moved, so the window can work out all widths from where the drag started (Qt itself
    would report the left column only, and never below its smallest width)."""

    column_dragged = Signal(int, int)  # the model column left of the line, the distance the pointer moved since the press

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.drag_start_sizes: list[int] = []  # the width of every column (by model column) when the line was taken
        self.drag_section: int = -1  # the model column left of the line being dragged, or -1
        self._drag_start_x: int = 0
        self.viewport().installEventFilter(self)

    def line_at(self, x: int) -> int:
        """The model column whose right-hand line is under this x (within the grip margin of the style), or -1."""
        section = self.logicalIndexAt(x)
        if section < 0:
            return -1
        grip = self.style().pixelMetric(self.style().PixelMetric.PM_HeaderGripMargin)
        left = self.sectionViewportPosition(section)
        if x < left + grip:
            visual = self.visualIndex(section) - 1
            section = self.logicalIndex(visual) if visual >= 0 else -1
        elif x <= left + self.sectionSize(section) - grip:
            return -1
        if section < 0 or self.sectionResizeMode(section) != QHeaderView.ResizeMode.Interactive:
            return -1
        return section

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport() and isinstance(event, QMouseEvent):
            x = int(event.position().x())
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.drag_section = self.line_at(x)
                if self.drag_section >= 0:
                    self.drag_start_sizes = [self.sectionSize(section) for section in range(self.count())]
                    self._drag_start_x = x
                    return True
            elif event.type() == QEvent.Type.MouseMove and self.drag_section >= 0:
                self.column_dragged.emit(self.drag_section, x - self._drag_start_x)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease and self.drag_section >= 0:
                self.drag_section = -1
                self.drag_start_sizes = []
                return True
        return super().eventFilter(watched, event)

    @override
    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        painter.save()
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()
        model = self.model()
        icon = model.headerData(logicalIndex, Qt.Orientation.Horizontal, HEADER_ICON_ROLE) if model is not None else None
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(rect.center().x() - HEADER_ICON // 2 + 1, rect.center().y() - HEADER_ICON // 2 + 1, HEADER_ICON, HEADER_ICON))


class MessageListView(QTreeView):
    """The message list. Dragging messages shows a small badge with their number, never the rows. The selection is grey while the list has no focus."""

    icon_clicked = Signal(int, str)  # row, column id ("contact" or "trash")
    width_changed = Signal()  # the room for the columns changed (window, pane or scroll bar)
    ICON_COLUMNS: tuple[str, ...] = ("contact", "trash")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list_header = ListHeader(self)
        self.setHeader(self.list_header)
        self.setProperty("focused", False)
        self.setMouseTracking(True)  # the Delete icon shows on the row under the pointer
        self.column_of: Callable[[int], str] = lambda column: ""  # the column id at a model column

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint())
        column_id = self.column_of(index.column()) if index.isValid() else ""
        if event.button() == Qt.MouseButton.LeftButton and column_id in self.ICON_COLUMNS:
            event.accept()  # the click acts on that one message and leaves the selection alone
            self.icon_clicked.emit(index.row(), column_id)
            return
        super().mousePressEvent(event)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and self.column_of(index.column()) in self.ICON_COLUMNS:
            event.accept()  # a quick second click on an icon does not open the message
            return
        super().mouseDoubleClickEvent(event)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.width_changed.emit()

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        show_focus(self, True)

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:  # a context menu leaves the selection as it is
            show_focus(self, False)

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
