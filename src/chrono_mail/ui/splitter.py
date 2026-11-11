from typing import override

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from chrono_mail.ui.colours import theme

# --- Version ------------------------------------------------------------
__version__ = "splitter.py 4.4.577"

# --- Version history ----------------------------------------------------
# v4.4.577: SideSplitter: the separator between a section's folder list and its Tags list, its place remembered per section (D-330)
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


SIDE_PARTS: tuple[int, int] = (3, 2)  # the folder list and the Tags list, the first time and when the window grows (D-330)


class SideSplitter(PaneSplitter):
    """The folder list above, the Tags list below, and a separator to drag between them (D-330). Each section keeps its own place under its
    own key; while the Tags list is hidden the place stays as it was, and comes back with the list."""

    def __init__(self, key: str, settings: QSettings, folders: QWidget, tags: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._key = key
        self._settings = settings
        self._laid_out: bool = False  # shown once in this run: before that its sizes are Qt's first guess, never a place to keep
        self.addWidget(folders)
        self.addWidget(tags)
        self.setChildrenCollapsible(False)
        for index, part in enumerate(SIDE_PARTS):
            self.setStretchFactor(index, part)
        saved = self._saved()
        self.setSizes(list(saved) if saved is not None else [part * 1000 for part in SIDE_PARTS])

    def _saved(self) -> tuple[int, int] | None:
        """The place kept last time, or None when there is none or what is kept is not two sizes above 0."""
        value = self._settings.value(self._key)
        if not isinstance(value, list) or len(value) != 2:
            return None
        try:
            first, second = (int(str(size)) for size in value)
        except ValueError:
            return None
        return (first, second) if first > 0 and second > 0 else None

    @override
    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self._laid_out = True

    def remember(self) -> None:
        """Keep the place of the separator; while the Tags list is hidden, or the section was not opened in this run, the place kept before
        stays."""
        sizes = self.sizes()
        tags = self.widget(1)
        if self._laid_out and tags is not None and not tags.isHidden() and len(sizes) == 2 and min(sizes) > 0:
            self._settings.setValue(self._key, sizes)
