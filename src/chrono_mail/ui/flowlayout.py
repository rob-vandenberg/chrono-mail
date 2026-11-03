from typing import override

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

# --- Version ------------------------------------------------------------
__version__ = "flowlayout.py 4.4.504"

# --- Version history ----------------------------------------------------
# v4.4.504: set_spacing: the gap between the parts follows the size factor (the invitation card's attendees, D-310)
# v4.2.467: Initial: parts side by side that go on in a next row when the width runs out, as words in a text (D-304); a part wider than
#           the row gets the row's width; wraps=False: one row, as wide as its parts, as a row of buttons always was


class FlowRow(QWidget):
    """Parts side by side, left to right; one that no longer fits goes to the start of the next row. Its smallest width is that of its
    widest part, so it never makes the window around it wider; a part wider than the row gets the row's width (it shortens its own
    text). wraps=False: one row, never narrower than its parts. It places its parts itself: no layout items, whose Python stand-ins
    could outlive the window (4.0.374)."""

    def __init__(self, parent: QWidget | None = None, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0, spacing: int = 6) -> None:
        super().__init__(parent)
        self._parts: list[QWidget] = []
        self._margins: tuple[int, int, int, int] = (left, top, right, bottom)
        self._spacing: int = spacing
        self.wraps: bool = True
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, part: QWidget) -> None:
        part.setParent(self)
        self._parts.append(part)
        part.show()
        self._place()
        self.updateGeometry()

    def set_wraps(self, wraps: bool) -> None:
        self.wraps = wraps
        self._place()
        self.updateGeometry()

    def set_spacing(self, spacing: int) -> None:
        self._spacing = spacing
        self._place()
        self.updateGeometry()

    def clear(self) -> None:
        """Every part goes (deleted once the event loop comes round)."""
        for part in self._parts:
            part.hide()
            part.deleteLater()
        self._parts.clear()
        self.updateGeometry()

    @override
    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    @override
    def heightForWidth(self, width: int, /) -> int:  # noqa: N802 - Qt's name
        return self._lay_out(width, place=False)

    @override
    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        left, _, right, _ = self._margins
        width = left + right + sum(part.sizeHint().width() for part in self._parts) + self._spacing * max(0, len(self._parts) - 1)
        return QSize(width, self._lay_out(width, place=False))

    @override
    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        left, _, right, _ = self._margins
        if not self.wraps:
            return self.sizeHint()
        width = left + right + max((part.minimumSizeHint().width() for part in self._parts), default=0)
        return QSize(width, self._lay_out(max(width, self.width()), place=False))

    @override
    def resizeEvent(self, event: QResizeEvent, /) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        self._lay_out(self.width(), place=True)

    def _lay_out(self, width: int, place: bool) -> int:
        """Puts the parts in rows at this width (only when place); gives the height the rows need."""
        left, top, right, bottom = self._margins
        end = width - right
        x, y, row_height = left, top, 0
        for part in self._parts:
            size = part.sizeHint()
            if self.wraps:
                size.setWidth(max(1, min(size.width(), end - left)))  # never wider than the row: the part shortens its text
            if self.wraps and x > left and x + size.width() > end:  # no room left in this row: the next one
                x, y, row_height = left, y + row_height + self._spacing, 0
            if place:
                part.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self._spacing
            row_height = max(row_height, size.height())
        return y + row_height + bottom
