"""Tags in the mail window: the coloured tag icon, the Tag menu (the tags with coloured ticks, as the reference client's
drop-down), and the tags drawn in the message list (small tag icons, a coloured pill with the name on the selected row)."""

from collections.abc import Callable, Iterable

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu

from chrono_mail.ui.tagchips import TagColourBook, paint_chips

# --- Version ------------------------------------------------------------
__version__ = "tagging.py 0.5.135"

# --- Version history ----------------------------------------------------
# v0.5.135: Initial version: tag icon, Tag menu, tags in the message list

ICON_SIZE: int = 16
MARK_WIDTH: int = 12  # one small tag icon in the Tag column
MARK_GAP: int = 2


def tag_path(area: QRect) -> QPainterPath:
    """A label shape: a rectangle with a point on the left and a hole, as the reference client's tag icons."""
    left, top, right, bottom = area.left(), area.top(), area.right(), area.bottom()
    middle = (top + bottom) / 2
    point = max(2.0, area.width() * 0.35)
    path = QPainterPath()
    path.moveTo(QPointF(left, middle))
    path.lineTo(QPointF(left + point, top))
    path.lineTo(QPointF(right, top))
    path.lineTo(QPointF(right, bottom))
    path.lineTo(QPointF(left + point, bottom))
    path.closeSubpath()
    hole = max(1.0, area.height() * 0.12)
    path.addEllipse(QPointF(left + point * 0.85, middle), hole, hole)
    return path


def tag_icon(colour: QColor, checked: bool = False, size: int = ICON_SIZE) -> QIcon:
    """The coloured tag of a menu entry; with a tick in it when the tag is on (the reference client's coloured ticks)."""
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    area = QRect(1, size // 5, size - 2, size - 2 * (size // 5))
    if checked:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRect(1, 1, size - 2, size - 2), 3, 3)
        painter.setPen(TagColourBook.text_colour(colour))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.8))
        painter.setFont(font)
        painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "✓")
    else:
        painter.setPen(colour.darker(130))
        painter.setBrush(colour)
        painter.drawPath(tag_path(area))
    painter.end()
    return QIcon(pixmap)


def offered_for_mail(tag: object) -> bool:
    return "mail" in tuple(getattr(tag, "kinds", ("mail",)))


def fill_tag_menu(
    menu: QMenu, tags: Iterable[object], book: TagColourBook, selected: list[tuple[str, ...]], toggle: Callable[[str, bool], None],
    new_tag: Callable[[], None], manage: Callable[[], None],
) -> None:
    """The Tag menu: every tag for mail, ticked when all selected messages carry it; a click gives it to all of them, or
    takes it away when all had it. Then New tag… and Manage tags…."""
    menu.clear()
    enabled = bool(selected)
    for tag in tags:
        if not offered_for_mail(tag):
            continue
        name = str(getattr(tag, "name"))
        everyone = enabled and all(any(have.casefold() == name.casefold() for have in tags_of) for tags_of in selected)
        action = menu.addAction(tag_icon(book.colour(name), everyone), name)
        action.setEnabled(enabled)
        shortcut = str(getattr(tag, "shortcut", ""))
        if shortcut:
            action.setShortcut(shortcut)
            action.setShortcutVisibleInContextMenu(True)
        action.triggered.connect(lambda checked=False, chosen=name, add=not everyone: toggle(chosen, add))
    if menu.actions():
        menu.addSeparator()
    new = menu.addAction("New tag…")
    new.setEnabled(enabled)
    new.triggered.connect(new_tag)
    menu.addAction("Manage tags…").triggered.connect(manage)


def paint_tag_marks(painter: QPainter, area: QRect, tags: tuple[str, ...], book: TagColourBook, selected: bool, font: QFont) -> None:
    """The Tag column: on the selected row the names as coloured pills (as many as fit), on the others a small coloured tag each."""
    if not tags:
        return
    if selected and paint_chips(painter, area.adjusted(2, 2, -2, -2), tags, book, font):
        return  # the names fit as pills; in a narrow column the small tags are drawn instead
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    height = max(6, min(area.height() - 6, QFontMetrics(font).height() - 4))
    left = area.left() + 4
    top = area.top() + (area.height() - height) // 2
    for tag in tags:
        if left + MARK_WIDTH > area.right():
            break
        colour = book.colour(tag)
        painter.setPen(colour.darker(130))
        painter.setBrush(colour)
        painter.drawPath(tag_path(QRect(left, top, MARK_WIDTH, height)))
        left += MARK_WIDTH + MARK_GAP
    painter.restore()


def first_tag_colour(tags: tuple[str, ...], book: TagColourBook) -> QColor | None:
    """The colour of a message's first tag, for the setting "use the tag colour for the text"."""
    return book.colour(tags[0]) if tags else None
