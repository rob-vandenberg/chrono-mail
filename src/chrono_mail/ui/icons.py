from pathlib import Path

from PySide6.QtGui import QIcon

# --- Version ------------------------------------------------------------
__version__ = "icons.py 0.0.14"

# --- Version history ----------------------------------------------------
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


def theme_icon(*names: str) -> QIcon:
    """The first of these names the icon theme has, or an empty icon."""
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def folder_icon(kind: str) -> QIcon:
    return theme_icon(*FOLDER_ICONS.get(kind, FOLDER_ICONS["other"]))


def application_icon() -> QIcon:
    """The icon of Chrono Mail (an envelope with a clock), from the file shipped with the program."""
    icon = QIcon(str(RESOURCES / "chrono-mail.svg"))
    return icon if not icon.isNull() else theme_icon("mail-message", "internet-mail")
