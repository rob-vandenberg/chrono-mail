from typing import override

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

# --- Version ------------------------------------------------------------
__version__ = "elidedlabel.py 1.9.265"

# --- Version history ----------------------------------------------------
# v1.9.265: Initial: a line of text that is cut with "…" when it does not fit, the whole text on hover; it never makes the window wider
#           (the owner, 2026-10-21: the message count line)


class ElidedLabel(QLabel):
    """A label whose text is drawn cut with "…" when its room is too small. Its text stays the whole text (text()); what it needs at
    least is the room of "…", so it never holds a window wider than the window's own minimum. Hovering shows the whole text when cut."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _shown(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())

    @override
    def minimumSizeHint(self) -> QSize:
        margins = self.contentsMargins()
        return QSize(self.fontMetrics().horizontalAdvance("…") + margins.left() + margins.right(), super().minimumSizeHint().height())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(self.contentsRect(), int(self.alignment()), self._shown())
        painter.end()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._follow()

    @override
    def setText(self, text: str) -> None:
        super().setText(text)
        self._follow()

    def _follow(self) -> None:
        """The whole text on hover only while it is cut."""
        self.setToolTip(self.text() if self._shown() != self.text() else "")
