import sys
from pathlib import Path
from typing import override

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QIconEngine, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# --- Version ------------------------------------------------------------
__version__ = "icons.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: The tasks section
# v0.0.61: The four section buttons and the sidebars of the calendar, task and contact sections
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.55: Application id and icon file for Windows
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.14: Initial icons: the application icon and the theme icons of the folder tree

RESOURCES: Path = Path(__file__).resolve().parent / "resources"

# Names from the freedesktop icon naming specification, best match first; the platform's icon theme
# (Breeze on KDE, Adwaita on GNOME) provides the pictures. A theme without one gives no icon.
FOLDER_ICONS: dict[str, tuple[str, ...]] = {
    "inbox": ("mail-folder-inbox", "folder-mail", "folder"),
    "sent": ("mail-folder-sent", "mail-sent", "mail-folder-outbox", "folder"),
    "drafts": ("document-edit", "folder-documents", "folder"),
    "trash": ("user-trash", "edit-delete", "folder"),
    "junk": ("mail-mark-junk", "dialog-warning", "folder"),
    "archive": ("folder-archive", "archive-insert", "folder"),
    "outbox": ("mail-folder-outbox", "mail-send", "folder"),
    "other": ("folder", "folder-open"),
    "account": ("mail-message", "user-identity", "folder"),
    "unread": ("mail-unread", "mail-unread-new", "mail-message-new"),
    "flagged": ("mail-mark-important", "flag", "emblem-important"),
}


# Where the platform has no icon theme (Windows, macOS) the icons that come with the program are used: simple line
# drawings (Lucide, ISC licence, see resources/icons/LICENSE-lucide.txt), drawn in the colour of the text.
BUNDLED_ICONS: dict[str, str] = {
    "mail-folder-inbox": "inbox", "folder-mail": "inbox", "mail-folder-sent": "send", "mail-sent": "send", "document-edit": "pencil",
    "user-trash": "trash", "edit-delete": "trash", "mail-mark-junk": "shield-alert", "dialog-warning": "shield-alert", "folder-documents": "folder", "folder-archive": "archive", "archive-insert": "archive",
    "mail-archive": "archive", "mail-folder-outbox": "send", "mail-send": "send", "folder": "folder", "folder-open": "folder-open",
    "mail-message": "mail", "user-identity": "user", "mail-unread": "mail", "mail-unread-new": "mail", "mail-message-new": "mail-plus",
    "mail-mark-important": "flag", "flag": "flag", "emblem-important": "flag", "application-exit": "log-out", "appointment-soon": "clock",
    "configure": "settings", "document-open": "folder-open", "document-print": "printer", "document-save-as": "save", "edit-clear": "eraser",
    "list-add": "plus", "mail-forward": "forward", "mail-mark-notjunk": "shield-check", "mail-mark-read": "mail-check", "mail-receive": "download", "view-refresh": "refresh-cw",
    "mail-reply-all": "reply-all", "mail-reply-sender": "reply", "view-filter": "funnel",
    "x-office-calendar": "calendar-days", "view-calendar": "calendar-days", "x-office-address-book": "book-user", "view-calendar-tasks": "list-checks", "view-task": "list-checks",
    "task-complete": "circle-check", "appointment-new": "calendar-plus", "contact-new": "user-plus", "color-management": "palette", "edit-find": "search",
}


class TintedSvgEngine(QIconEngine):
    """An icon drawn from a one-colour SVG in the colour the palette gives text at that moment, so that it suits the light and the dark scheme."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path: Path = path
        self._source: str = path.read_text("utf-8")

    @staticmethod
    def colour_for(mode: QIcon.Mode, palette: QPalette) -> str:
        if mode == QIcon.Mode.Disabled:
            return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name()
        if mode == QIcon.Mode.Selected:
            return palette.color(QPalette.ColorRole.HighlightedText).name()
        return palette.color(QPalette.ColorRole.ButtonText).name()

    @override
    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        colour = self.colour_for(mode, QGuiApplication.palette())
        QSvgRenderer(self._source.replace("currentColor", colour).encode("utf-8")).render(painter, QRectF(rect))

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
        return TintedSvgEngine(self._path)


def bundled_icon(name: str) -> QIcon:
    """The icon that comes with the program for this theme icon name, or an empty icon."""
    file = RESOURCES / "icons" / f"{BUNDLED_ICONS.get(name, '')}.svg"
    return QIcon(TintedSvgEngine(file)) if name in BUNDLED_ICONS and file.exists() else QIcon()


def theme_icon(*names: str) -> QIcon:
    """The first of these names the icon theme has; else the icon of the program for the first name it has one for; else an empty icon."""
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
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
