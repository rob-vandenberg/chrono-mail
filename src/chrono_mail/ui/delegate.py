from collections.abc import Callable
from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from chrono_mail.ui.colours import group_header_colour, selection_grey

from chrono_mail.ui.formatting import avatar_colour, display_name, initials, short_date
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.models import CONTACT_ROLE, GROUP_HEADER_ROLE, ROW_ROLE

# --- Version ------------------------------------------------------------
__version__ = "delegate.py 0.2.96"

# --- Version history ----------------------------------------------------
# v0.2.96: In the card layout a group header is 36 px high
# v0.2.95: Group headers have a fixed background colour per scheme (darker in the dark scheme)
# v0.2.94: Group headers: a bar with a chevron and the group's name in bold, in both layouts
# v0.1.89: ColumnDelegate: fixed row height per density, the Contact icon and the Delete icon on the row under the pointer
# v0.0.88: A selected row is grey while the list has no focus
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.14: secondary text on a selected row has more contrast
# v0.0.13: Initial message row painting

ROW_HEIGHT: int = 74
CARD_HEADER_HEIGHT: int = 36  # a group header in the card layout
COLUMN_ROW_HEIGHTS: dict[str, int] = {"compact": 22, "normal": 29, "roomy": 36}  # rows of the column layout, per density
CELL_ICON: int = 16
AVATAR_SIZE: int = 40
QUIET_ALPHA: int = 170
QUIET_ALPHA_SELECTED: int = 255  # on the highlight, secondary text is as strong as the main text: the theme's own pairing is the limit
MARGIN: int = 12
UNREAD_DOT: int = 8


def _typed[T](option: QStyleOptionViewItem, name: str, expected: type[T]) -> T:
    """The stubs of PySide6 leave the attributes of QStyleOption untyped; this reads one with a check."""
    value: object = getattr(option, name)
    if not isinstance(value, expected):
        raise TypeError(f"option.{name} is not a {expected.__name__}")
    return value


def _rect(option: QStyleOptionViewItem) -> QRect:
    return _typed(option, "rect", QRect)


def _is_selected(option: QStyleOptionViewItem) -> bool:
    state: object = getattr(option, "state")
    return isinstance(state, QStyle.StateFlag) and bool(state & QStyle.StateFlag.State_Selected)


def paint_group_header(painter: QPainter, option: QStyleOptionViewItem, header: object) -> bool:
    """Draws a group header (a bar over the whole row: a chevron, open or folded, and the name in bold); False on a message row."""
    if not isinstance(header, tuple) or len(header) != 2:
        return False
    name, folded = str(header[0]), bool(header[1])
    full = _rect(option)
    palette = _typed(option, "palette", QPalette)
    painter.save()
    active = QPalette.ColorGroup.Active  # a header is not "enabled" (the arrow keys pass it), but it is drawn in the colours of text
    painter.fillRect(full, QColor(group_header_colour(palette)))
    text = palette.color(active, QPalette.ColorRole.Text)
    painter.setPen(QPen(text, 1.4))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    middle = QPoint(full.left() + 14, full.center().y() + 1)
    if folded:
        painter.drawPolyline([QPoint(middle.x() - 2, middle.y() - 4), QPoint(middle.x() + 2, middle.y()), QPoint(middle.x() - 2, middle.y() + 4)])
    else:
        painter.drawPolyline([QPoint(middle.x() - 4, middle.y() - 2), QPoint(middle.x(), middle.y() + 2), QPoint(middle.x() + 4, middle.y() - 2)])
    font = QFont(_typed(option, "font", QFont))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(text)
    area = QRect(full.left() + 28, full.top(), full.width() - 36, full.height())
    painter.drawText(area, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, area.width()))
    painter.restore()
    return True


class MessageDelegate(QStyledItemDelegate):
    """Paints one message as a row: avatar, sender and date, subject, and a line of preview."""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(None)
        self.show_recipients: bool = False  # in Sent, Drafts and Outbox the people shown are the recipients

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(_rect(option).width(), CARD_HEADER_HEIGHT if index.data(GROUP_HEADER_ROLE) is not None else ROW_HEIGHT)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        if paint_group_header(painter, option, index.data(GROUP_HEADER_ROLE)):
            return
        row = index.data(ROW_ROLE)
        painter.save()
        selected = _is_selected(option)
        full = _rect(option)
        palette = _typed(option, "palette", QPalette)
        found: object = getattr(option, "widget")
        focused = not isinstance(found, QWidget) or found.property("focused") is not False  # grey while the list has no focus
        background = (palette.color(QPalette.ColorRole.Highlight) if focused else QColor(selection_grey(palette))) if selected else palette.color(QPalette.ColorRole.Base)
        painter.fillRect(full, background)
        text = palette.color(QPalette.ColorRole.HighlightedText if selected and focused else QPalette.ColorRole.Text)
        quiet = QColor(text)
        quiet.setAlpha(QUIET_ALPHA_SELECTED if selected else QUIET_ALPHA)  # secondary text must stay readable on the highlight
        if row is None:  # the page with this row is still on its way
            painter.setPen(quiet)
            painter.drawText(full.adjusted(MARGIN * 2 + AVATAR_SIZE, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "…")
            self._separator(painter, full, palette)
            painter.restore()
            return
        flags: tuple[str, ...] = getattr(row, "flags")
        unread = "seen" not in flags
        who_text = str(getattr(row, "recipients" if self.show_recipients else "sender")) or "(no one)"
        who = display_name(who_text.split(",")[0]) if self.show_recipients else display_name(who_text)
        rect = full
        # avatar
        avatar = QRect(rect.left() + MARGIN, rect.top() + (rect.height() - AVATAR_SIZE) // 2, AVATAR_SIZE, AVATAR_SIZE)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(avatar_colour(who_text))
        painter.drawEllipse(avatar)
        painter.setPen(QColor("white"))
        font = QFont(_typed(option, "font", QFont))
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.95)
        painter.setFont(font)
        painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, initials(who))
        # text area
        left = rect.left() + MARGIN * 2 + AVATAR_SIZE
        right = rect.right() - MARGIN
        line = QFontMetrics(_typed(option, "font", QFont)).height() + 3
        top = rect.top() + (rect.height() - 3 * line) // 2
        base = QFont(_typed(option, "font", QFont))
        strong = QFont(base)
        strong.setBold(unread)
        # first line: who, and the date at the right
        date_text = short_date(getattr(row, "date"))
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.9)
        painter.setFont(small)
        date_width = QFontMetrics(small).horizontalAdvance(date_text)
        painter.setPen(quiet if not unread else text)
        painter.drawText(QRect(right - date_width, top, date_width, line), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, date_text)
        painter.setFont(strong)
        painter.setPen(text)
        who_rect = QRect(left, top, right - left - date_width - MARGIN, line)
        painter.drawText(who_rect, Qt.AlignmentFlag.AlignVCenter, QFontMetrics(strong).elidedText(who, Qt.TextElideMode.ElideRight, who_rect.width()))
        # second line: subject, with marks at the right
        marks = ("⚑ " if "flagged" in flags else "") + ("📎" if getattr(row, "attachments") else "")
        marks_width = QFontMetrics(base).horizontalAdvance(marks) + (MARGIN if marks else 0)
        subject = str(getattr(row, "subject")) or "(no subject)"
        size = int(getattr(row, "thread_size", 1))
        badge = str(size) if size > 1 else ""
        badge_width = QFontMetrics(small).horizontalAdvance(badge) + 12 + MARGIN if badge else 0
        subject_rect = QRect(left, top + line, right - left - marks_width - badge_width, line)
        painter.setFont(strong)
        painter.setPen(text)
        painter.drawText(subject_rect, Qt.AlignmentFlag.AlignVCenter, QFontMetrics(strong).elidedText(subject, Qt.TextElideMode.ElideRight, subject_rect.width()))
        if badge:  # a conversation of several messages: the number in a small rounded box
            box = QRect(right - marks_width - badge_width + MARGIN, top + line + 3, badge_width - MARGIN, line - 6)
            painter.setFont(small)
            painter.setPen(quiet)
            painter.drawRoundedRect(box, 4, 4)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
        if marks:
            painter.setFont(base)
            painter.setPen(text)
            painter.drawText(QRect(right - marks_width, top + line, marks_width, line), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, marks)
        # third line: preview
        painter.setFont(small)
        painter.setPen(quiet)
        preview_rect = QRect(left, top + 2 * line, right - left, line)
        preview = str(getattr(row, "preview")) or " "
        painter.drawText(preview_rect, Qt.AlignmentFlag.AlignVCenter, QFontMetrics(small).elidedText(preview, Qt.TextElideMode.ElideRight, preview_rect.width()))
        if unread:  # a blue dot in the left margin (white on the selected row, which is blue itself)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.color(QPalette.ColorRole.HighlightedText if selected and focused else QPalette.ColorRole.Highlight))
            painter.drawEllipse(QRect(rect.left() + 2, rect.top() + (rect.height() - UNREAD_DOT) // 2, UNREAD_DOT, UNREAD_DOT))
        self._separator(painter, full, palette)
        painter.restore()

    @staticmethod
    def _separator(painter: QPainter, rect: QRect, palette: QPalette) -> None:
        line = QColor(palette.color(QPalette.ColorRole.Text))
        line.setAlpha(28)
        painter.setPen(line)
        painter.drawLine(rect.left() + MARGIN, rect.bottom(), rect.right(), rect.bottom())


class ColumnDelegate(QStyledItemDelegate):
    """A cell of the column layout: rows of a fixed height; the Contact column shows a person when the sender is a contact,
    the Delete column a bin on the row under the pointer."""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(None)
        self.row_height: int = COLUMN_ROW_HEIGHTS["normal"]
        self.column_of: Callable[[int], str] = lambda column: ""  # the column id at a model column

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(super().sizeHint(option, index).width(), self.row_height)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        if paint_group_header(painter, option, index.data(GROUP_HEADER_ROLE)):
            return
        super().paint(painter, option, index)
        column_id = self.column_of(index.column())
        state: object = getattr(option, "state")
        hovered = isinstance(state, QStyle.StateFlag) and bool(state & QStyle.StateFlag.State_MouseOver)
        if column_id == "contact" and index.data(CONTACT_ROLE):
            self._icon(painter, option, theme_icon("user"))
        elif column_id == "trash" and hovered:
            self._icon(painter, option, theme_icon("edit-delete"))

    @staticmethod
    def _icon(painter: QPainter, option: QStyleOptionViewItem, icon: QIcon) -> None:
        full = _rect(option)
        area = QRect(full.center().x() - CELL_ICON // 2 + 1, full.center().y() - CELL_ICON // 2 + 1, CELL_ICON, CELL_ICON)
        icon.paint(painter, area)
