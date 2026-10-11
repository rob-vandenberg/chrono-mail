from PySide6.QtWidgets import QFrame, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui import theme
from chrono_mail.ui.sections import CALENDAR, MAIL
from tests.ui.world import World, dialogs, menu_named, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_theme.py 0.0.73"

# --- Version history ----------------------------------------------------
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
    assert [action.text().replace("&", "") for action in menu_named(window).actions()] == ["File", "Message", "Folder", "View", "Help"]
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
