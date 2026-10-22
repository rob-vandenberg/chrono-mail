from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget

from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.scale import px

# --- Version ------------------------------------------------------------
__version__ = "searchhelp.py 0.3.106"

# --- Version history ----------------------------------------------------
# v0.3.106: Icons, spacing and width follow the size factor of the interface
# v0.1.92: Initial help list under the search box, opened by typing "?"


@dataclass(frozen=True)
class HelpLine:
    """One line of the search help: what it is called, what it does, and what picking it puts in the search box."""

    icon: str
    name: str
    explanation: str
    inserted: str
    cursor_back: int = 0  # how far the cursor goes back from the end after inserting (between the quotes of a phrase)


HELP_LINES: tuple[HelpLine, ...] = (
    HelpLine("edit-find", "words", "Search in subject, sender, recipients and message text.", ""),
    HelpLine("dialog-question", "subject", "Search for words in the subject.", "subject:"),
    HelpLine("dialog-question", "body", "Search in the message text.", "body:"),
    HelpLine("dialog-question", "from", "Search for the sender.", "from:"),
    HelpLine("dialog-question", "to", "Search for a recipient.", "to:"),
    HelpLine("dialog-question", '"exact phrase"', "Find these words together, in this order.", '""', 1),
    HelpLine("dialog-question", "-word", "Leave out messages that contain this word.", "-"),
)

ICON_SIZE = 32
PADDING = 6
LINE_ROLE = Qt.ItemDataRole.UserRole + 1


class HelpLineDelegate(QStyledItemDelegate):
    """Draws a help line: the icon, the name, and the explanation in grey under it."""

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        metrics = QFontMetrics(_typed(option, "font", QFont))
        return QSize(px(320), max(px(ICON_SIZE), 2 * metrics.height()) + 2 * px(PADDING))

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        line = HELP_LINES[int(index.data(LINE_ROLE))]
        rect = _rect(option)
        palette = _typed(option, "palette", QPalette)
        font = _typed(option, "font", QFont)
        selected = _is_selected(option)
        painter.save()
        if selected:
            painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight))
        icon_top = rect.top() + (rect.height() - px(ICON_SIZE)) // 2
        theme_icon(line.icon).paint(painter, rect.left() + px(PADDING), icon_top, px(ICON_SIZE), px(ICON_SIZE))
        text_left = rect.left() + 2 * px(PADDING) + px(ICON_SIZE)
        width = rect.right() - text_left - px(PADDING)
        metrics = QFontMetrics(font)
        top = rect.top() + (rect.height() - 2 * metrics.height()) // 2
        text = palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        painter.setFont(font)
        painter.setPen(text)
        painter.drawText(text_left, top, width, metrics.height(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line.name)
        painter.setPen(text if selected else palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
        explanation = metrics.elidedText(line.explanation, Qt.TextElideMode.ElideRight, width)
        painter.drawText(text_left, top + metrics.height(), width, metrics.height(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, explanation)
        painter.restore()


class HelpList(QListWidget):
    """The list inside the help window. Arrows move, Enter picks, Escape closes; any other key goes back to the search box."""

    picked = Signal(int)
    typed = Signal(QKeyEvent)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.currentRow() >= 0:
                self.picked.emit(self.currentRow())
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Escape):
            super().keyPressEvent(event)
            return
        self.typed.emit(event)


class SearchHelp(QFrame):
    """The help list that opens under the search box when it holds only "?"; picking a line hands its text to the window."""

    picked = Signal(object)  # the HelpLine that was picked
    typed = Signal(QKeyEvent)  # a key that belongs to the search box

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("searchHelp")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self.list = HelpList(self)
        self.list.setItemDelegate(HelpLineDelegate(self.list))
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for position, line in enumerate(HELP_LINES):
            item = QListWidgetItem(line.name, self.list)
            item.setData(LINE_ROLE, position)
        layout.addWidget(self.list)
        self.list.picked.connect(self._pick)
        self.list.itemClicked.connect(self._clicked)
        self.list.typed.connect(self._typed)

    def open_under(self, anchor: QWidget) -> None:
        """Shows the list under this widget, as wide as it, with the first line highlighted."""
        height = sum(self.list.sizeHintForRow(row) for row in range(self.list.count())) + 2 * self.list.frameWidth() + 2
        self.resize(max(anchor.width(), px(320)), height + 2)
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 2)))
        self.list.setCurrentRow(0)
        self.show()
        self.list.setFocus()

    def _clicked(self, item: QListWidgetItem) -> None:
        self._pick(self.list.row(item))

    def _pick(self, row: int) -> None:
        self.hide()
        self.picked.emit(HELP_LINES[row])

    def _typed(self, event: QKeyEvent) -> None:
        self.hide()
        self.typed.emit(event)


def send_key(target: QWidget, event: QKeyEvent) -> None:
    """Gives a key that was pressed in the help list to the search box."""
    target.setFocus()
    QApplication.sendEvent(target, QKeyEvent(event.type(), event.key(), event.modifiers(), event.text()))
