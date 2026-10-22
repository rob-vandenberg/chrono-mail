import json
from typing import override

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QHideEvent, QKeyEvent, QMouseEvent, QPainter, QPalette, QShowEvent, QWindow
from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget

from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.icons import grey_icon, theme_icon
from chrono_mail.ui.scale import px

# --- Version ------------------------------------------------------------
__version__ = "searchhistory.py 1.5.259"

# --- Version history ----------------------------------------------------
# v1.5.259: A press outside is seen on the main window only, never by a filter on the whole program (with it, a Python filter saw
#           every event of every object, also the web view's inner parts, and PySide crashed making them known to Python)
# v1.5.258: The list never takes the mouse or the keyboard from the search field (it was a popup, which takes both: a double click,
#           a tap-tap-drag or a drag in the field could not select text). The field keeps the keyboard: Down, Up, Page Up, Page Down
#           and Enter work the list from it, Escape closes it; a press outside the field and the list, or the focus leaving the field
#           (another window included), closes it
# v0.3.106: Icons, spacing and width follow the size factor of the interface
# v0.1.93: Initial search history: the last 10 searches under the search box


HISTORY_SIZE = 10
ICON_SIZE = 32
REMOVE_SIZE = 16
PADDING = 6
TEXT_ROLE = Qt.ItemDataRole.UserRole + 1


def read_history(saved: str) -> list[str]:
    """The searches kept in the settings, newest first; anything that is not a list of texts counts as no history."""
    try:
        found: object = json.loads(saved) if saved else []
    except ValueError:
        return []
    if not isinstance(found, list):
        return []
    return [entry for entry in found if isinstance(entry, str) and entry.strip()][:HISTORY_SIZE]


def history_text(history: list[str]) -> str:
    return json.dumps(history[:HISTORY_SIZE])


def with_search(history: list[str], text: str) -> list[str]:
    """The history after searching for this text: it goes to the top, appears only once, and the oldest falls off after 10."""
    text = text.strip()
    if not text:
        return history[:HISTORY_SIZE]
    return [text, *[entry for entry in history if entry != text]][:HISTORY_SIZE]


def matching(history: list[str], typed: str) -> list[str]:
    """The searches that contain what was typed so far (capitals do not matter); all of them while nothing is typed."""
    wanted = typed.strip().casefold()
    return [entry for entry in history if wanted in entry.casefold()]


def remove_rect(row: QRect) -> QRect:
    """Where the ✕ of a history line is drawn and can be clicked."""
    return QRect(row.right() - px(PADDING) - px(REMOVE_SIZE), row.top() + (row.height() - px(REMOVE_SIZE)) // 2, px(REMOVE_SIZE), px(REMOVE_SIZE))


class HistoryLineDelegate(QStyledItemDelegate):
    """Draws a history line: a large magnifying glass, the search text, and a grey ✕ at the right."""

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(px(320), px(ICON_SIZE) + 2 * px(PADDING))

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        rect = _rect(option)
        palette = _typed(option, "palette", QPalette)
        font = _typed(option, "font", QFont)
        selected = _is_selected(option)
        painter.save()
        if selected:
            painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight))
        theme_icon("edit-find").paint(painter, rect.left() + px(PADDING), rect.top() + (rect.height() - px(ICON_SIZE)) // 2, px(ICON_SIZE), px(ICON_SIZE))
        remove = remove_rect(rect)
        grey_icon("edit-delete-remove").paint(painter, remove)
        text_left = rect.left() + 2 * px(PADDING) + px(ICON_SIZE)
        text_rect = QRect(text_left, rect.top(), remove.left() - px(PADDING) - text_left, rect.height())
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        text = QFontMetrics(font).elidedText(str(index.data(TEXT_ROLE)), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


class HistoryList(QListWidget):
    """The list inside the history window: a line is clicked (its ✕ removes it). It never takes the keyboard: the search field
    keeps it and moves the highlight here (step)."""

    picked = Signal(str)
    removed = Signal(str)

    def step(self, key: int) -> None:
        """Down and Up move the highlight one line (Up from the first line: none), Page Down and Page Up to the last and the first."""
        row, last = self.currentRow(), self.count() - 1
        wanted = {Qt.Key.Key_Down: min(row + 1, last), Qt.Key.Key_Up: max(row - 1, -1), Qt.Key.Key_PageDown: last, Qt.Key.Key_PageUp: 0}.get(Qt.Key(key), row)
        self.setCurrentRow(wanted)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        item = self.itemAt(event.position().toPoint())
        if item is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        text = str(item.data(TEXT_ROLE))
        if remove_rect(self.visualItemRect(item)).contains(event.position().toPoint()):
            self.removed.emit(text)
        else:
            self.picked.emit(text)


class SearchHistory(QFrame):
    """The earlier searches under the search box. It never takes the mouse or the keyboard from the search field, so the field
    works as always while the list is shown (a click places the cursor, a double click or a drag selects text, typing goes on)."""

    picked = Signal(str)  # the search to run
    removed = Signal(str)  # the search to take out of the history

    STEP_KEYS: tuple[Qt.Key, ...] = (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp)

    def __init__(self, parent: QWidget, field: QLineEdit) -> None:
        # a window that is never activated and grabs nothing (a popup would take every press and key until it closes)
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("searchHistory")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self.list = HistoryList(self)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # a click on a line leaves the keyboard with the field
        self.list.setItemDelegate(HistoryLineDelegate(self.list))
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list)
        self.list.picked.connect(self._picked)
        self.list.removed.connect(self.removed)
        self._field: QLineEdit = field
        field.installEventFilter(self)  # its keys that work the list
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._focus_changed)

    def entries(self) -> list[str]:
        return [str(self.list.item(row).data(TEXT_ROLE)) for row in range(self.list.count())]

    def show_entries(self, entries: list[str], anchor: QWidget) -> None:
        """Shows these searches under the widget, as wide as it, with no line highlighted; closes the list when there are none."""
        if not entries:
            self.hide()
            return
        self.list.clear()
        for text in entries:
            item = QListWidgetItem(text, self.list)
            item.setData(TEXT_ROLE, text)
        self.list.setCurrentRow(-1)
        height = sum(self.list.sizeHintForRow(row) for row in range(self.list.count()))
        self.resize(max(anchor.width(), px(320)), height + 2 * self.list.frameWidth() + 4)
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 2)))
        if not self.isVisible():
            self.show()

    def _picked(self, text: str) -> None:
        self.hide()
        self.picked.emit(text)

    def _focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        """The keyboard went elsewhere (another field, a menu, another program): the list closes."""
        if self.isVisible() and new is not self._field:
            self.hide()

    def _field_window(self) -> QWindow | None:
        """The window the search field is in (the main window), as the window system knows it."""
        return self._field.window().windowHandle()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        window = self._field_window()
        if window is not None:
            window.installEventFilter(self)  # a press in that window outside the field closes the list; never a filter on the whole program (D-216)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        window = self._field_window()
        if window is not None:
            window.removeEventFilter(self)
        super().hideEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self.isVisible():
            return super().eventFilter(watched, event)
        if watched is self._field and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in self.STEP_KEYS:
                self.list.step(key)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.list.currentRow() >= 0:
                self._picked(str(self.list.currentItem().data(TEXT_ROLE)))
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        elif watched is self._field_window() and event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            where = event.globalPosition().toPoint()  # a press on the list itself never comes here: it is a window of its own
            field = QRect(self._field.mapToGlobal(QPoint(0, 0)), self._field.size())
            if not self.geometry().contains(where) and not field.contains(where):
                self.hide()  # the press itself goes on to where it was made
        return super().eventFilter(watched, event)
