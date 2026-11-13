from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "accountrows.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: Initial: an account in Settings > Accounts on two lines: its name and address, below it smaller and grey what it holds (D-346)

SECOND_LINE_ROLE: int = 257  # the row's second line: what the account holds, "Mail (POP3)", "Google Calendar" (D-346)
SECOND_LINE_SCALE: float = 0.85  # the second line's font, as part of the first's


def _field[T](option: QStyleOptionViewItem, name: str, kind: type[T]) -> T:
    """A field of a style option; getattr: PySide's stubs give the fields of a style option no type."""
    value: object = getattr(option, name)
    if not isinstance(value, kind):
        raise TypeError(f"the style option's {name} is not a {kind.__name__}")
    return value


def second_line_font(font: QFont) -> QFont:
    smaller = QFont(font)
    if font.pointSizeF() > 0:
        smaller.setPointSizeF(font.pointSizeF() * SECOND_LINE_SCALE)
    else:
        smaller.setPixelSize(max(1, round(font.pixelSize() * SECOND_LINE_SCALE)))
    return smaller


class TwoLineRows(QStyledItemDelegate):
    """Paints a row's text and, below it in a smaller font in the greyed text colour of the theme (src/chrono_mail/core/colours.py), its
    second line (SECOND_LINE_ROLE); the selection's own text colour on a selected row."""

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        font = _field(option, "font", QFont)
        first = QFontMetrics(font)
        second = QFontMetrics(second_line_font(font))
        return QSize(0, first.height() + second.height() + 2 * wpx(3))  # no width of its own: the row is as wide as the list, a long text is cut with …

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        below = str(index.data(SECOND_LINE_ROLE) or "")
        found: object = getattr(option, "widget")  # getattr: PySide's stubs give the fields of a style option no type
        widget = found if isinstance(found, QWidget) else None
        style = widget.style() if widget is not None else QApplication.style()
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        setattr(background, "text", "")  # the background, the selection and the focus only: the two lines are painted below
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, widget)
        rect = _field(option, "rect", QRect).adjusted(wpx(6), wpx(3), -wpx(6), -wpx(3))
        state: object = getattr(option, "state")
        selected = isinstance(state, QStyle.StateFlag) and bool(state & QStyle.StateFlag.State_Selected)
        palette = _field(option, "palette", QPalette)
        font = _field(option, "font", QFont)
        first = QFontMetrics(font)
        painter.save()
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        painter.drawText(QRect(rect.left(), rect.top(), rect.width(), first.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         first.elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        smaller = second_line_font(font)
        second = QFontMetrics(smaller)
        painter.setFont(smaller)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.PlaceholderText))
        painter.drawText(QRect(rect.left(), rect.top() + first.height(), rect.width(), second.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         second.elidedText(below, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()
