from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from chrono_mail.ui.colours import theme

# --- Version ------------------------------------------------------------
__version__ = "splitter.py 1.1.215"

# --- Version history ----------------------------------------------------
# v1.1.215: Initial: one separator between the panes everywhere, 3 px with its dots exactly in the middle (D-188)

PANE_SEPARATOR_WIDTH: int = 3  # every separator between panes (D-188); odd, so the dots sit exactly in the middle
DOT_COUNT: int = 7
DOT_LENGTH: int = 2  # along the separator
DOT_GAP: int = 2


class PaneHandle(QSplitterHandle):
    """The bar between two panes: the theme's separator colour, and a short row of dots exactly in its middle, across and along."""

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt's name
        colours = theme(self.palette())
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(colours.pane_separator))
        length = DOT_COUNT * DOT_LENGTH + (DOT_COUNT - 1) * DOT_GAP
        dots = QColor(colours.pane_separator_dots)
        if self.orientation() == Qt.Orientation.Horizontal:  # panes side by side: an upright bar, dots one above the other
            x = (self.width() - 1) // 2
            top = (self.height() - length) // 2
            for number in range(DOT_COUNT):
                painter.fillRect(x, top + number * (DOT_LENGTH + DOT_GAP), 1, DOT_LENGTH, dots)
        else:  # one pane above the other: a lying bar, dots side by side
            y = (self.height() - 1) // 2
            left = (self.width() - length) // 2
            for number in range(DOT_COUNT):
                painter.fillRect(left + number * (DOT_LENGTH + DOT_GAP), y, DOT_LENGTH, 1, dots)
        painter.end()


class PaneSplitter(QSplitter):
    """A splitter between panes with the program's one separator (D-188)."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(PANE_SEPARATOR_WIDTH)

    @override
    def createHandle(self) -> QSplitterHandle:  # noqa: N802 - Qt's name
        return PaneHandle(self.orientation(), self)
