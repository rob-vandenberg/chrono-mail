import pytest
from PySide6.QtCore import QModelIndex, QSize
from PySide6.QtGui import QIcon
from pytestqt.qtbot import QtBot

import chrono_mail.ui.models as models
from chrono_mail.ui.icons import FOLDER_ICONS, RESOURCES, application_icon, folder_icon, theme_icon
from chrono_mail.ui.models import FolderTreeModel
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_icons.py 0.0.85"

# --- Version history ----------------------------------------------------
# v0.0.85: All icons are the program's own Breeze files, drawn in the text colour with other colours kept
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.14: Initial icon tests


def test_the_application_icon_comes_with_the_program_and_renders(qtbot: QtBot) -> None:
    assert (RESOURCES / "chrono-mail.svg").is_file()
    icon = application_icon()
    assert not icon.isNull()
    for size in (16, 32, 64, 128):
        image = icon.pixmap(QSize(size, size)).toImage()
        assert image.width() > 0 and len({image.pixelColor(x, y).rgba() for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)}) > 3


def test_windows_carry_the_application_icon(world: World, qtbot: QtBot) -> None:
    assert not world.window.windowIcon().isNull()


def test_a_theme_without_the_icon_gives_an_empty_icon_not_an_error() -> None:
    assert theme_icon("no-such-icon-name-1", "no-such-icon-name-2").isNull()
    assert isinstance(folder_icon("no-such-kind"), QIcon)


def test_every_kind_of_folder_has_a_list_of_icon_names() -> None:
    for kind in ("inbox", "sent", "drafts", "trash", "junk", "archive", "outbox", "other", "account", "unread", "flagged"):
        names = FOLDER_ICONS[kind]
        assert names and all(name == name.lower() and " " not in name for name in names)


class Info:
    def __init__(self, path: str, role: str) -> None:
        self.path, self.role, self.unread = path, role, 0


class Account:
    account_id, email_address, shown_name = "a" * 32, "me@example.com", "me@example.com"


def test_the_tree_asks_for_the_icon_of_each_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def record(kind: str) -> QIcon:
        asked.append(kind)
        return QIcon()

    monkeypatch.setattr(models, "folder_icon", record)
    model = FolderTreeModel()
    model.show_favorites = False
    model.populate([Account()], {"a" * 32: [Info("Inbox", "inbox"), Info("Sent", "sent"), Info("Outbox", "other"), Info("Trash", "trash"), Info("Mine/Sub", "other")]})
    assert asked[:3] == ["account", "unread", "flagged"]
    assert {"inbox", "sent", "outbox", "trash", "other"} <= set(asked)
    assert model.rowCount(QModelIndex()) == 1
    asked.clear()
    model.populate([Account()], {"a" * 32: [Info("Mine/Sub", "other")]})
    assert asked.count("other") >= 2  # the folder and the parent that exists only in its path both get an icon


def test_all_icons_are_the_programs_own_breeze_files_and_ignore_the_desktop_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = RESOURCES / "icons"
    assert (folder / "LICENSE-breeze.txt").is_file() and not (folder / "LICENSE-lucide.txt").exists()
    for file in folder.glob("*.svg"):
        assert "ColorScheme-" in file.read_text("utf-8") or file.stem == "color-management", file.name
    def no_theme(*_: object) -> QIcon:
        raise AssertionError("the desktop theme was asked")

    monkeypatch.setattr(QIcon, "fromTheme", no_theme)
    assert not theme_icon("crosshairs").isNull() and not folder_icon("inbox").isNull()


def test_an_icon_takes_the_text_colour_and_keeps_its_other_colours() -> None:
    from PySide6.QtGui import QColor, QGuiApplication, QPalette

    from chrono_mail.ui.icons import TintedSvgEngine

    original = QGuiApplication.palette()
    palette = QPalette(original)
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#123456"))
    QGuiApplication.setPalette(palette)
    try:
        def colours(name: str) -> set[str]:
            image = QIcon(TintedSvgEngine(RESOURCES / "icons" / f"{name}.svg")).pixmap(QSize(22, 22)).toImage()
            return {image.pixelColor(x, y).name() for x in range(22) for y in range(22) if image.pixelColor(x, y).alpha() == 255}

        assert colours("mail-reply-sender") == {"#123456"}
        assert "#da4453" in colours("edit-delete")
    finally:
        QGuiApplication.setPalette(original)
