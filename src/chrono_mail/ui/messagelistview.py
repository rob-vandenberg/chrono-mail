import os
from collections.abc import Callable, Iterable, Sequence
from typing import cast, override

from PySide6.QtCore import QAbstractItemModel, QEvent, QItemSelection, QModelIndex, QObject, QPersistentModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFocusEvent, QFontMetrics, QIcon, QMouseEvent, QPainter, QPaintEvent, QPalette, QPixmap, QRegion, QResizeEvent, QShowEvent, QHideEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QStyleOptionViewItem, QTreeView, QWidget

from chrono_mail.ui.colours import theme
from chrono_mail.ui.messagelist import HEADER_ICON_ROLE, MessageListModel
from chrono_mail.ui.scale import lpx

from chrono_mail.ui.theme import show_focus
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "messagelistview.py 3.6.352"

# --- Version history ----------------------------------------------------
# v3.6.352: a removal moves the rows below it up as they are on screen and paints only the new strip and the changed rows; the removal is painted in the same moment as the rest of its change (D-266)
# v3.5.346: The drag is made from the selected rows by their ranges and its badge counts exactly the messages that go along (RV-77)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.6.260: The header icon role from messagelist.py (R-15)
# v1.1.172: The drag badge's colours from the theme (D-158)
# v0.5.140: A group row is drawn from the left edge to the right edge, whatever the order of the columns (the first column can be moved)
# v0.5.123: An empty list says "There are no messages to show in this view." once it has finished loading (D-119)
# v0.3.108: The grey icons of the column header follow the size factor of the message list
# v0.2.105: The badge hangs just past the bottom-right corner of the cursor, by the desktop's cursor size (it covered the first digit)
# v0.2.104: The drag is offered as the copy kind, so the desktop shows its arrow with a plus (not its hand); the messages are still moved
# v0.2.103: The drag has its own cursors (arrow with a move sign where the messages can go, with a red cross elsewhere) instead of the theme's large hand
# v0.2.94: A click on a group header folds or unfolds it
# v0.1.89: Clicks on the Contact and Delete icons; tells when the width of its rows changes; a header that centres its icons and remembers where a column drag started
# v0.0.88: The selection is blue while the message list has the focus and grey without it
# v0.0.48: Initial message list that drags a small badge instead of the selected rows

STANDARD_CURSOR_SIZE: int = 32  # pixels, when the desktop does not say how large its cursor is


def cursor_size() -> int:
    """The size of the desktop's cursor (XCURSOR_SIZE on Linux), else the usual 32 pixels."""
    try:
        size = int(os.environ.get("XCURSOR_SIZE", ""))
    except ValueError:
        return STANDARD_CURSOR_SIZE
    return size if 16 <= size <= 256 else STANDARD_CURSOR_SIZE


def drag_badge(count: int) -> QPixmap:
    """A small label ("3 messages") for a drag. The rows themselves are not drawn: a long selection would hide the folder under the pointer."""
    text = counted(count, "message")
    font = QApplication.font()
    metrics = QFontMetrics(font)
    width, height = metrics.horizontalAdvance(text) + 20, metrics.height() + 10
    offset = cursor_size()  # the badge hangs just past the bottom-right corner of the cursor, so the cursor never covers it
    pixmap = QPixmap(width + offset, height + offset)
    pixmap.fill(Qt.GlobalColor.transparent)  # empty margin above and left: the pointer stays free
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    area = QRect(offset, offset, width, height)
    painter.setBrush(QColor(theme().drag_badge))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(area, 8, 8)
    painter.setFont(font)
    painter.setPen(QColor(theme().on_blue))
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
            size = lpx(HEADER_ICON)
            icon.paint(painter, QRect(rect.center().x() - size // 2 + 1, rect.center().y() - size // 2 + 1, size, size))


EMPTY_TEXT: str = "There are no messages to show in this view."


def rectangles(region: QRegion) -> list[QRect]:
    """The rectangles a region is made of."""
    return list(cast(Iterable[QRect], region))  # cast: the stubs do not tell that a region gives its rectangles


class MessageListView(QTreeView):
    """The message list. Dragging messages shows a small badge with their number, never the rows. The selection is grey while the list has no focus."""

    icon_clicked = Signal(int, str)  # row, column id ("contact" or "trash")
    header_clicked = Signal(int)  # the row of a group header: fold or unfold it
    width_changed = Signal()  # the room for the columns changed (window, pane or scroll bar)
    ICON_COLUMNS: tuple[str, ...] = ("contact", "trash")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list_header = ListHeader(self)
        self.setHeader(self.list_header)
        self.setProperty("focused", False)
        self.setMouseTracking(True)  # the Delete icon shows on the row under the pointer
        self.column_of: Callable[[int], str] = lambda column: ""  # the column id at a model column
        self.is_header: Callable[[int], bool] = lambda row: False  # whether a row is a group header
        self._watched: QAbstractItemModel | None = None
        # A removal moves the rows below it up as they are on screen, and only what really changed is painted (D-266). While _moved
        # is None every paint is whole; it holds what must be painted at the next paint: the strip that came into view.
        self._moved: QRegion | None = None
        self._kept = False  # nothing changed on screen since the last paint other than what _changed_rows names: a move is allowed
        self._changed_rows: list[QPersistentModelIndex] = []  # rows changed since the last paint (their look, their selection)
        self.viewport().setAutoFillBackground(False)  # the view fills the background of what it paints itself: moved rows stay
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.entered.connect(self.repaint_all)
        self.viewportEntered.connect(self.repaint_all)
        self.list_header.sectionResized.connect(self.repaint_all)
        self.list_header.sectionMoved.connect(self.repaint_all)

    _WHOLE_SIGNALS: tuple[str, ...] = ("layoutChanged", "modelReset", "rowsInserted", "rowsMoved", "headerDataChanged", "columnsInserted",
                                       "columnsRemoved", "columnsMoved", "groups_changed", "header_rows_added")

    @override
    def setModel(self, model: QAbstractItemModel | None) -> None:
        if self._watched is not None:
            self._watched.rowsAboutToBeRemoved.disconnect(self._rows_leaving)
            self._watched.rowsRemoved.disconnect(self._rows_left)
            for name in self._WHOLE_SIGNALS:
                if hasattr(self._watched, name):
                    getattr(self._watched, name).disconnect(self.repaint_all)  # getattr: the signals by their names
        if model is not None:
            model.rowsAboutToBeRemoved.connect(self._rows_leaving)  # before the view's own: it forgets where its rows are
        super().setModel(model)
        self._watched = model
        self.repaint_all()
        if model is not None:
            model.rowsRemoved.connect(self._rows_left)  # after the view's own
            for name in self._WHOLE_SIGNALS:
                if hasattr(model, name):
                    getattr(model, name).connect(self.repaint_all)  # getattr: the signals by their names

    def repaint_all(self, *_: object) -> None:
        """The whole list is painted at the next paint (something changed that may touch every row)."""
        self._moved = None
        self._kept = False
        self._changed_rows.clear()
        self.viewport().update()

    def _rows_leaving(self, parent: QModelIndex | QPersistentModelIndex, first: int, last: int) -> None:
        """Rows leave the list: the rows below them are moved up on screen as they are, not painted again, so a Delete shows at once
        and a held Delete key keeps up. Only the strip that comes into view at the bottom is painted (with the rows whose look changed)."""
        model = self.model()
        if not self._kept or model is None or parent.isValid() or not self.isVisible():
            self.repaint_all()
            return
        column = self.header().logicalIndexAt(0)
        top_rect = self.visualRect(model.index(first, column)) if column >= 0 else QRect()
        bottom_rect = self.visualRect(model.index(last, column)) if column >= 0 else QRect()
        height = self.viewport().height()
        if not top_rect.isValid() or not bottom_rect.isValid() or top_rect.top() < 0:
            self.repaint_all()  # the rows above the view move too, or the place is not known: all of it
            return
        top, gone = top_rect.top(), bottom_rect.bottom() + 1 - top_rect.top()
        if top >= height:
            return  # below what is shown: nothing on screen changes
        width = self.viewport().width()
        moved = self._moved if self._moved is not None else QRegion()
        above, below = QRegion(0, 0, width, top), QRegion(0, top + gone, width, max(0, height - top - gone))
        self._moved = moved.intersected(above).united(moved.intersected(below).translated(0, -gone)).united(QRegion(0, max(top, height - gone), width, height - max(top, height - gone)))
        self.viewport().scroll(0, -gone, QRect(0, top, width, height - top))  # within the rows from the removed one down
        self.viewport().update(QRect(0, max(top, height - gone), width, height - max(top, height - gone)))

    def _rows_left(self, *_: object) -> None:
        """Rows left: the view lays its rows out now, not in a later pass of its own, so the removal is painted in one moment with
        the rest of its change (the folder numbers) and that later pass paints nothing again (D-266)."""
        self.executeDelayedItemsLayout()

    def _whole_rows(self, region: QRegion) -> QRegion:
        """The region grown to the whole rows it touches: Qt paints every row it touches whole, and its background must be filled first."""
        whole = QRegion()
        width = self.viewport().width()
        for rect in rectangles(region):
            top, bottom = rect.top(), rect.bottom()
            first, last = self.indexAt(QPoint(0, top)), self.indexAt(QPoint(0, bottom))
            if first.isValid():
                top = self.visualRect(first).top()
            if last.isValid():
                bottom = self.visualRect(last).bottom()
            whole = whole.united(QRegion(QRect(0, top, width, bottom - top + 1)))
        return whole

    def _note_rows(self, first: int, last: int) -> None:
        model = self.model()
        if model is None or last - first > 200:
            self.repaint_all()
            return
        self._changed_rows.extend(QPersistentModelIndex(model.index(row, 0)) for row in range(first, last + 1))

    @override
    def dataChanged(self, topLeft: QModelIndex | QPersistentModelIndex, bottomRight: QModelIndex | QPersistentModelIndex, /, roles: Sequence[int] = ()) -> None:
        self._note_rows(topLeft.row(), bottomRight.row())
        super().dataChanged(topLeft, bottomRight, roles)

    @override
    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        for chosen in (selected, deselected):
            for number in range(chosen.count()):
                self._note_rows(chosen.at(number).top(), chosen.at(number).bottom())
        super().selectionChanged(selected, deselected)

    @override
    def currentChanged(self, current: QModelIndex | QPersistentModelIndex, previous: QModelIndex | QPersistentModelIndex) -> None:
        for index in (current, previous):
            if index.isValid():
                self._note_rows(index.row(), index.row())
        super().currentChanged(current, previous)

    @override
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.repaint_all()
        super().scrollContentsBy(dx, dy)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        self.repaint_all()
        super().showEvent(event)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        self.repaint_all()
        super().hideEvent(event)

    @override
    def changeEvent(self, event: QEvent) -> None:
        self.repaint_all()  # the palette, the style, the font
        super().changeEvent(event)

    @override
    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Leave, QEvent.Type.Resize):
            self.repaint_all()  # the pointer left (no row is hovered any more), or the room changed
        return super().viewportEvent(event)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and self.is_header(index.row()):
            event.accept()
            if event.button() == Qt.MouseButton.LeftButton:
                self.header_clicked.emit(index.row())
            return
        column_id = self.column_of(index.column()) if index.isValid() else ""
        if event.button() == Qt.MouseButton.LeftButton and column_id in self.ICON_COLUMNS:
            event.accept()  # the click acts on that one message and leaves the selection alone
            self.icon_clicked.emit(index.row(), column_id)
            return
        super().mousePressEvent(event)

    @override
    def drawRow(self, painter: QPainter, options: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """A group row is one bar from the left edge to the right edge. Qt spans it from the place of the model's first
        column, which is not the left edge once that column was dragged elsewhere."""
        model = self.model()
        if model is None or index.parent().isValid() or not self.is_header(index.row()):
            super().drawRow(painter, options, index)
            return
        area: object = getattr(options, "rect")  # getattr: the stubs do not describe the rect of a style option
        if not isinstance(area, QRect):
            super().drawRow(painter, options, index)
            return
        full = QStyleOptionViewItem(options)
        setattr(full, "rect", QRect(0, area.top(), max(self.viewport().width(), self.header().length()), area.height()))
        self.itemDelegate().paint(painter, full, model.index(index.row(), 0))

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and (self.is_header(index.row()) or self.column_of(index.column()) in self.ICON_COLUMNS):
            event.accept()  # a quick second click on an icon does not open the message
            return
        super().mouseDoubleClickEvent(event)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.width_changed.emit()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        region = event.region()
        if self._moved is not None:  # after a removal: the moved rows stay as they are; the new strip and the changed rows are painted
            region = QRegion(self._moved)
            model = self.model()
            rows = [kept.row() for kept in self._changed_rows if kept.isValid()]
            rows += model.header_rows() if isinstance(model, MessageListModel) else []  # a group header may tell its new count
            for row in rows:
                rect = self.visualRect(model.index(row, 0)) if model is not None else QRect()
                if rect.isValid():
                    region = region.united(QRegion(QRect(0, rect.top() - 1, self.viewport().width(), rect.height() + 2)))  # with its neighbours (a selection draws over their edge)
            region = self._whole_rows(region.intersected(QRegion(self.viewport().rect())))
        painter = QPainter(self.viewport())
        brush = self.viewport().palette().brush(self.viewport().backgroundRole())
        for rect in rectangles(region):
            painter.fillRect(rect, brush)  # the background of what is painted now (the view fills it itself: moved rows stay)
        painter.end()
        super().paintEvent(QPaintEvent(region) if self._moved is not None else event)
        self._moved = None
        self._changed_rows.clear()
        self._kept = True
        model = self.model()
        if model is None or model.rowCount() > 0 or (isinstance(model, MessageListModel) and not model.settled):
            return  # never while the list is still loading
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        height = QFontMetrics(self.font()).height()
        painter.drawText(QRect(0, lpx(10), self.viewport().width(), height), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, EMPTY_TEXT)
        painter.end()

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        show_focus(self, True)
        self.repaint_all()

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:  # a context menu leaves the selection as it is
            show_focus(self, False)
            self.repaint_all()

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        drag = self.make_drag()
        if drag is not None:
            drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)  # the cursor of the copy kind; the folder list moves them

    def make_drag(self) -> QDrag | None:
        """The drag of the selected messages: their ids and a small badge. The cursors are the desktop's own: offered as
        the copy kind, the drag shows the arrow with a plus over a folder that takes it (the move kind shows a hand) and
        the arrow with a no-entry sign elsewhere."""
        model = self.model()
        if not isinstance(model, MessageListModel):
            return None
        rows: set[int] = set()
        selection = self.selectionModel().selection()
        for number in range(selection.count()):  # the selected rows by their ranges: Ctrl+A on 500,000 messages makes no index per cell
            chosen = selection.at(number)
            rows.update(range(chosen.top(), chosen.bottom() + 1))
        data, count = model.drag_data(sorted(rows))
        if count == 0:
            return None
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(drag_badge(count))  # exactly the messages that go along
        drag.setHotSpot(QPoint(0, 0))
        return drag
