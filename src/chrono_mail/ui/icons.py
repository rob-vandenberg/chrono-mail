import re
import sys
from pathlib import Path
from typing import override

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QIconEngine, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# --- Version ------------------------------------------------------------
__version__ = "icons.py 1.1.200"

# --- Version history ----------------------------------------------------
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
}


_TEXT_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-Text\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")
_ANY_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-[A-Za-z]+\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")


class TintedSvgEngine(QIconEngine):
    """An icon drawn from a Breeze SVG with its text colour replaced by the colour the palette gives text at that moment, so that it suits the light and the dark scheme; other colours (the red of Delete) stay."""

    def __init__(self, path: Path, accent_when_on: bool = False, grey: bool = False) -> None:
        super().__init__()
        self._path: Path = path
        self._accent_when_on: bool = accent_when_on  # in the blue of the highlight while the button is pressed (checked)
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
            source = _TEXT_COLOUR.sub(rf"\g<1>{self.colour_for(mode, palette, self._accent_when_on and state == QIcon.State.On)}", self._source)
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
        return TintedSvgEngine(self._path, self._accent_when_on, self._grey)


def grey_icon(name: str) -> QIcon:
    """The icon that comes with the program for this icon name, drawn wholly in grey; an empty icon when there is none."""
    file = RESOURCES / "icons" / f"{name}.svg"
    return QIcon(TintedSvgEngine(file, grey=True)) if name and "/" not in name and file.is_file() else QIcon()


def bundled_icon(name: str) -> QIcon:
    """The icon that comes with the program for this icon name, or an empty icon."""
    file = RESOURCES / "icons" / f"{name}.svg"
    return QIcon(TintedSvgEngine(file)) if name and "/" not in name and file.is_file() else QIcon()


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
