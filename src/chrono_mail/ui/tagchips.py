from collections.abc import Iterable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

from chrono_mail.ui.colours import contrast, theme
from chrono_mail.ui.formatting import avatar_colour

# --- Version ------------------------------------------------------------
__version__ = "tagchips.py 1.1.172"

# --- Version history ----------------------------------------------------
# v1.1.172: The text colours of tag labels from the theme (D-158)
# v0.0.62: Initial tag colours and the small coloured labels that show tags

CHIP_PADDING: int = 8
CHIP_GAP: int = 4


class TagColourBook:
    """The colours of tags for the interface: the ones the user chose, and for the others a colour made from the name (always the same one)."""

    def __init__(self) -> None:
        self._chosen: dict[str, str] = {}

    def update(self, tags: Iterable[object]) -> None:
        """Take the answer of tag.list."""
        self._chosen = {str(getattr(tag, "name")).casefold(): str(getattr(tag, "colour")) for tag in tags if str(getattr(tag, "colour"))}

    def colour(self, name: str) -> QColor:
        chosen = self._chosen.get(name.casefold())
        return QColor(chosen) if chosen else avatar_colour(name.casefold())

    @staticmethod
    def text_colour(background: QColor) -> QColor:
        """White or black, whichever reads better on this background."""
        white, black = QColor(theme().chip_text_light), QColor(theme().chip_text_dark)
        return white if contrast(white, background) >= contrast(black, background) else black

    def html(self, tags: Iterable[str]) -> str:
        """The tags as small coloured labels in rich text."""
        parts = []
        for tag in tags:
            background = self.colour(tag)
            parts.append(
                f"<span style='background-color:{background.name()}; color:{self.text_colour(background).name()};'>&nbsp;{_escape(tag)}&nbsp;</span>"
            )
        return "&nbsp;".join(parts)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paint_chips(painter: QPainter, area: QRect, tags: Iterable[str], book: TagColourBook, font: QFont) -> int:
    """Draw the labels of the tags in a row, from the right end of the area to the left, as many as fit. Returns the width taken."""
    small = QFont(font)
    small.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
    metrics = QFontMetrics(small)
    right = area.right()
    taken = 0
    painter.save()
    painter.setFont(small)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    height = metrics.height() + 4
    top = area.top() + (area.height() - height) // 2
    for tag in reversed(list(tags)):
        width = metrics.horizontalAdvance(tag) + 2 * CHIP_PADDING
        if taken + width > area.width():
            break
        left = right - width + 1
        box = QRect(left, top, width, height)
        background = book.colour(tag)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(box, height / 2, height / 2)
        painter.setPen(book.text_colour(background))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, tag)
        right = left - CHIP_GAP - 1
        taken += width + CHIP_GAP
    painter.restore()
    return taken
