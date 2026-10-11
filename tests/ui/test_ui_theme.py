from typing import Any

import pytest
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QFrame, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui import theme
from chrono_mail.ui.columns import BY_ID, READ_MARK, cell_text
from chrono_mail.ui.icons import TintedSvgEngine, section_icon
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, SECTION_ICONS, SECTIONS, TASKS
from tests.ui.world import World, dialogs, menu_named, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_theme.py 0.0.77"

# --- Version history ----------------------------------------------------
# v0.0.77: Tests of the message list and the reading pane
# v0.0.76: Tests of the root of the Menu, blue accent and the section icons
# v0.0.73: Initial tests of the look of the main window


def test_every_widget_the_stylesheet_names_exists(world: World) -> None:
    window = world.window
    sheet = theme.stylesheet()
    names = {"mainToolbar": window.toolbar, "newButton": window.new_button, "folderTree": window.tree, "sideHeading": window.side_heading, "sectionBar": window.section_bar}
    for name, widget in names.items():
        assert f"#{name}" in sheet and widget.objectName() == name
    assert window.findChild(QFrame, "searchPill") is not None and window.scope.parent() is window.findChild(QFrame, "searchPill") and window.search_box.parent() is window.scope.parent()
    assert all(button.objectName() == "sectionButton" for button in window.section_bar.buttons.values()) and "#sectionButton" in sheet
    assert window.new_button.defaultAction() is window.act_new and isinstance(window.new_button, QToolButton)
    assert window.styleSheet() == sheet


def test_one_menu_button_holds_the_menus_and_the_heading_follows_the_section(world: World, qtbot: QtBot) -> None:
    window = world.window
    bar_actions = window.menuBar().actions()
    assert len(bar_actions) == 1 and "Menu" in bar_actions[0].text()
    assert [action.text().replace("&", "") for action in menu_named(window).actions()] == ["Settings…", "Accounts…", "Rules…", "", "File", "Message", "Folder", "View", "Help", "", "Exit"]
    assert window.act_settings.shortcut().toString() == "Ctrl+," and window.act_quit.text() == "Exit" and window.act_quit.shortcut().toString() == ""
    assert [action.text() for action in menu_named(window, "File").actions()] == ["New", "Refresh", "", "Back up the encryption key…", "Restore the encryption key…"]
    assert window.side_heading.text() == "Mail"
    window.section_bar.buttons[CALENDAR].click()
    assert window.side_heading.text() == "Calendar"
    window.section_bar.buttons[MAIL].click()
    assert window.side_heading.text() == "Mail"


def test_the_look_is_kept_in_both_colour_schemes(world: World) -> None:
    for scheme in ("light", "dark", "light"):
        world.window.settings.setValue("color_scheme", scheme)
        world.window.apply_preferences()
        assert world.window.styleSheet() == theme.stylesheet()


def test_settings_accounts_and_rules_open_the_settings_window_on_their_page(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Any] = []
    def record(page: str | None = None) -> None:
        opened.append(page)

    monkeypatch.setattr(world.window, "open_settings", record)
    for action in (world.window.act_settings, world.window.act_accounts, world.window.act_rules):
        action.trigger()
    assert opened == [None, "Accounts", "Rules"]


def test_the_accent_is_the_blue_of_the_menus_and_the_section_icons_are_plain() -> None:
    sheet = theme.stylesheet()
    assert "#ff8c00" not in sheet.lower() and "QToolButton#newButton {" in sheet and "palette(highlight)" in sheet
    assert [name for name, *_ in SECTIONS] == [MAIL, CALENDAR, CONTACTS, TASKS] and set(SECTION_ICONS) == {MAIL, CALENDAR, CONTACTS, TASKS}
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eeeeee"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2a6fc4"))
    assert TintedSvgEngine.colour_for(QIcon.Mode.Normal, palette) == "#eeeeee" and TintedSvgEngine.colour_for(QIcon.Mode.Normal, palette, accent=True) == "#2a6fc4"
    assert not section_icon("check").isNull() and all(not section_icon(stem).isNull() for stem in SECTION_ICONS.values())


def test_the_list_says_received_and_marks_unread_with_a_blue_dot_and_read_with_a_circle(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush, QGuiApplication

    from tests.ui.test_ui_window import started

    started(qtbot, world)
    window = world.window
    assert BY_ID["date"].label == "Received" and window.sort_button.text() == "Sorted by Received \u25be"
    row = window.list_model.row_at(0)
    assert "seen" not in getattr(row, "flags") and cell_text("status", row, False) == "\u25cf"
    column = window.list_model.columns.index("status")
    unread = window.list_model.index(0, column).data(Qt.ItemDataRole.ForegroundRole)
    assert isinstance(unread, QBrush) and unread.color() == QGuiApplication.palette().highlight().color()
    seen = type("Seen", (), {"flags": ("seen",), "sender": "", "recipients": ""})()
    assert cell_text("status", seen, False) == READ_MARK


def test_the_list_header_the_reading_pane_header_and_the_banner_are_styled_and_a_message_window_has_the_look(world: World, qtbot: QtBot) -> None:
    window = world.window
    sheet = theme.stylesheet()
    assert window.list_view.objectName() == "messageList" and "QTreeView#messageList" in sheet
    assert window.reader._header.objectName() == "readerHeader" and "QFrame#readerHeader" in sheet  # noqa: SLF001
    assert window.reader._banner.objectName() == "remoteBanner" and "QFrame#remoteBanner" in sheet  # noqa: SLF001
    from tests.ui.test_ui_reader import shown

    from chrono_mail.ui.messagewindow import MessageWindow

    opened = MessageWindow(window.backend, "a", "INBOX", "m", shown(html="", text="x"), lambda *args: None, lambda url: None, window._prepare_message_reader)  # noqa: SLF001
    qtbot.addWidget(opened)
    assert opened.styleSheet() == sheet
