import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer

# --- Version ------------------------------------------------------------
__version__ = "icons.py 4.4.586"

# --- Version history ----------------------------------------------------
# v4.4.586: The icon of Favorites > All messages (D-334)
# v4.4.504: place_icon: a pin, the place of a meeting on the invitation card (D-310), drawn in the colour of text
# v4.3.498: split_icon: the owner's split of a row (an arrow, and a dotted line from under it to a second arrow), drawn in the colour of text;
#           keep_both_icon: the owner's one square above two (Keep both of a sync conflict, B-30, B-31), drawn the same way
# v4.2.424: accent_icon: an icon always in the accent colour (the (i) after a greyed option, D-294)
# v1.1.200: An icon for the Scheduled folder
# v1.1.191: Icons for the Favorites Snoozed and Unreplied
# v0.1.89: grey_icon: an icon drawn wholly in grey (the column headers of Contact and Delete)
# v0.0.87: Other folders use folder-open-symbolic; Local Folders uses user-home-symbolic
# v0.0.85: Every icon comes from the program's own copy of the Breeze icons (LGPL), on every platform; no icon theme of the desktop and no Lucide icons any more
# v0.0.76: Section icons in plain colours, blue while the section is open
# v0.0.63: The tasks section
# v0.0.61: The four section buttons and the sidebars of the calendar, task and contact sections
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.55: Application id and icon file for Windows
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.14: Initial icons: the application icon and the theme icons of the folder tree

RESOURCES: Path = Path(__file__).resolve().parent / "resources"

# Names from the freedesktop icon naming specification, best match first. The pictures are the program's own copy
# of the Breeze icons (resources/icons, LGPL, see LICENSE-breeze.txt), the same on every platform.
FOLDER_ICONS: dict[str, tuple[str, ...]] = {
    "inbox": ("mail-folder-inbox", "folder-mail", "folder"),
    "sent": ("mail-folder-sent", "mail-sent", "mail-folder-outbox", "folder"),
    "drafts": ("document-edit", "folder-documents", "folder"),
    "trash": ("user-trash", "edit-delete", "folder"),
    "junk": ("mail-mark-junk", "dialog-warning", "folder"),
    "archive": ("folder-archive", "archive-insert", "folder"),
    "outbox": ("mail-folder-outbox", "mail-send", "folder"),
    "scheduled": ("appointment-new", "clock", "folder"),
    "other": ("folder-open-symbolic", "folder"),
    "account": ("mail-message", "user-identity", "folder"),
    "local": ("user-home-symbolic", "folder"),
    "unread": ("mail-unread", "mail-unread-new", "mail-message-new"),
    "flagged": ("mail-mark-important", "flag", "emblem-important"),
    "snoozed": ("clock", "folder"),
    "unreplied": ("mail-reply-sender", "folder"),
    "allmessages": ("mail-message", "folder"),  # Favorites > All Messages (D-334)
}


_TEXT_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-Text\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")
_ANY_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-[A-Za-z]+\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")


class TintedSvgEngine(QIconEngine):
    """An icon drawn from a Breeze SVG with its text colour replaced by the colour the palette gives text at that moment, so that it suits the light and the dark scheme; other colours (the red of Delete) stay."""

    def __init__(self, path: Path, accent_when_on: bool = False, grey: bool = False, accent: bool = False) -> None:
        super().__init__()
        self._path: Path = path
        self._accent_when_on: bool = accent_when_on  # in the blue of the highlight while the button is pressed (checked)
        self._accent: bool = accent  # always in the blue of the highlight
        self._grey: bool = grey  # every colour of the icon (also the red of Delete) in the grey of placeholder text
        self._source: str = path.read_text("utf-8")

    @staticmethod
    def colour_for(mode: QIcon.Mode, palette: QPalette, accent: bool = False) -> str:
        if mode == QIcon.Mode.Disabled:
            return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name()
        if mode == QIcon.Mode.Selected:
            return palette.color(QPalette.ColorRole.HighlightedText).name()
        if accent:
            return palette.color(QPalette.ColorRole.Highlight).name()
        return palette.color(QPalette.ColorRole.ButtonText).name()

    @override
    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        palette = QGuiApplication.palette()
        if self._grey:
            source = _ANY_COLOUR.sub(rf"\g<1>{palette.color(QPalette.ColorRole.PlaceholderText).name()}", self._source)
        else:
            source = _TEXT_COLOUR.sub(rf"\g<1>{self.colour_for(mode, palette, self._accent or (self._accent_when_on and state == QIcon.State.On))}", self._source)
        QSvgRenderer(source.encode("utf-8")).render(painter, QRectF(rect))

    @override
    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        image = QPixmap(size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        return image

    @override
    def clone(self) -> "TintedSvgEngine":
        return TintedSvgEngine(self._path, self._accent_when_on, self._grey, self._accent)


class DrawnEngine(QIconEngine):
    """An icon drawn in code in the colour of text, as the bundled icons are (TintedSvgEngine.colour_for): `draw` paints it in a square of
    side 1 placed in the middle of the rect, its places 0 to 1 each way."""

    def draw(self, painter: QPainter, at: Callable[[float, float], QPointF], size: float) -> None:
        raise NotImplementedError

    @override
    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        colour = QColor(TintedSvgEngine.colour_for(mode, QGuiApplication.palette()))
        size = min(rect.width(), rect.height())
        left, top = rect.left() + (rect.width() - size) / 2, rect.top() + (rect.height() - size) / 2

        def at(x: float, y: float) -> QPointF:  # a place in the owner's drawing, 0 to 1 each way
            return QPointF(left + x * size, top + y * size)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QBrush(colour), max(size * 0.06, 1.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.setBrush(QBrush(colour))
        self.draw(painter, at, size)
        painter.restore()

    @override
    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        image = QPixmap(size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        return image


class SplitEngine(DrawnEngine):
    """The owner's icon for splitting a row into two (B-31): a solid arrow to the right at the top, and from under it a dotted line going down,
    then right into a second arrow below."""

    @override
    def draw(self, painter: QPainter, at: Callable[[float, float], QPointF], size: float) -> None:
        painter.drawLine(at(0.13, 0.26), at(0.72, 0.26))
        dotted = QPen(painter.pen())
        dotted.setStyle(Qt.PenStyle.CustomDashLine)
        dotted.setDashPattern([1.0, 1.0])  # squares with a square's gap between them
        painter.setPen(dotted)
        painter.drawPolyline(QPolygonF([at(0.39, 0.29), at(0.39, 0.71), at(0.72, 0.71)]))
        painter.setPen(Qt.PenStyle.NoPen)
        for y in (0.26, 0.71):
            painter.drawPolygon(QPolygonF([at(0.69, y - 0.085), at(0.87, y), at(0.69, y + 0.085)]))

    @override
    def clone(self) -> "SplitEngine":
        return SplitEngine()


class KeepBothEngine(DrawnEngine):
    """The owner's icon for Keep both (B-30): a square at the top, a line down from it to a bar, and from the bar's ends two squares below."""

    @override
    def draw(self, painter: QPainter, at: Callable[[float, float], QPointF], size: float) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for x0, y0, x1, y1 in (
            (0.405, 0.117, 0.592, 0.305),  # the square at the top
            (0.476, 0.300, 0.522, 0.505),  # the line down to the bar
            (0.225, 0.500, 0.773, 0.546),  # the bar
            (0.225, 0.540, 0.272, 0.700), (0.726, 0.540, 0.773, 0.700),  # its ends down
            (0.155, 0.695, 0.342, 0.882), (0.656, 0.695, 0.843, 0.882),  # the two squares below
        ):
            painter.drawRect(QRectF(at(x0, y0), at(x1, y1)))

    @override
    def clone(self) -> "KeepBothEngine":
        return KeepBothEngine()


class PlaceEngine(DrawnEngine):
    """A pin: the place of a meeting (the invitation card, D-310). A drop-shaped outline, its point down, with a hole in the middle."""

    @override
    def draw(self, painter: QPainter, at: Callable[[float, float], QPointF], size: float) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        outline = QPainterPath(at(0.5, 0.9))
        outline.cubicTo(at(0.38, 0.72), at(0.23, 0.52), at(0.23, 0.36))
        outline.arcTo(QRectF(at(0.23, 0.09), at(0.77, 0.63)), 180, -180)
        outline.cubicTo(at(0.77, 0.52), at(0.62, 0.72), at(0.5, 0.9))
        painter.drawPath(outline)
        painter.drawEllipse(QRectF(at(0.41, 0.27), at(0.59, 0.45)))

    @override
    def clone(self) -> "PlaceEngine":
        return PlaceEngine()


def place_icon() -> QIcon:
    """The place of a meeting: a pin."""
    return QIcon(PlaceEngine())


def split_icon() -> QIcon:
    """Split a row of a sync conflict into two, both kept (B-31)."""
    return QIcon(SplitEngine())


def keep_both_icon() -> QIcon:
    """Keep both versions of an item in a sync conflict as two (B-30): one square that becomes two."""
    return QIcon(KeepBothEngine())


def grey_icon(name: str) -> QIcon:
    """The icon that comes with the program for this icon name, drawn wholly in grey; an empty icon when there is none."""
    file = RESOURCES / "icons" / f"{name}.svg"
    return QIcon(TintedSvgEngine(file, grey=True)) if name and "/" not in name and file.is_file() else QIcon()


def bundled_icon(name: str) -> QIcon:
    """The icon that comes with the program for this icon name, or an empty icon."""
    file = RESOURCES / "icons" / f"{name}.svg"
    return QIcon(TintedSvgEngine(file)) if name and "/" not in name and file.is_file() else QIcon()


def accent_icon(name: str) -> QIcon:
    """The icon that comes with the program for this icon name, in the accent colour (the theme's blue); an empty icon when there is none."""
    file = RESOURCES / "icons" / f"{name}.svg"
    return QIcon(TintedSvgEngine(file, accent=True)) if name and "/" not in name and file.is_file() else QIcon()


def section_icon(stem: str) -> QIcon:
    """The plain icon of a section button, from the file shipped with the program: the colour of text, blue while the section is open."""
    return QIcon(TintedSvgEngine(RESOURCES / "icons" / f"{stem}.svg", accent_when_on=True))


def theme_icon(*names: str) -> QIcon:
    """The icon of the program for the first of these names it has one for; else an empty icon. The icon theme of the desktop is not used."""
    for name in names:
        icon = bundled_icon(name)
        if not icon.isNull():
            return icon
    return QIcon()


def folder_icon(kind: str) -> QIcon:
    return theme_icon(*FOLDER_ICONS.get(kind, FOLDER_ICONS["other"]))


def application_icon() -> QIcon:
    """The icon of Chrono Mail (an envelope with a clock), from the file shipped with the program: the .ico on Windows, the SVG elsewhere."""
    windows = RESOURCES / "chrono-mail.ico"
    icon = QIcon(str(windows)) if sys.platform == "win32" and windows.exists() else QIcon()
    if icon.isNull():
        icon = QIcon(str(RESOURCES / "chrono-mail.svg"))
    return icon if not icon.isNull() else theme_icon("mail-message", "internet-mail")
