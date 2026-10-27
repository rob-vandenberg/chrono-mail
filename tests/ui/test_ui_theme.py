from collections import Counter
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFocusEvent, QIcon, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFrame, QSplitter, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui import theme
from chrono_mail.ui.colours import selection_grey
from chrono_mail.ui.colours import theme as theme_in_use
from chrono_mail.ui.columns import BY_ID, READ_MARK, cell_text
from chrono_mail.ui.icons import TintedSvgEngine, section_icon
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, SECTION_ICONS, SECTIONS, TASKS
from chrono_mail.ui.splitter import PANE_SEPARATOR_WIDTH, PaneHandle, PaneSplitter
from chrono_mail.ui.toolbar import ToolbarRow
from tests.ui.world import World, dialogs, menu_named, settle, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_theme.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: File has Export… below Import…
# v2.4.323: Backup… is one word (the owner)
# v2.4.322: the File menu has Back up… and Restore…, and the key items are named after the recovery code (D-244)
# v2.0.293: the selection colour is the row's most common colour, never one pixel a letter can cover (the Windows computer's report of 2.0.287)
# v1.9.265: the Menu has Sync conflicts… (1.1.229); the selection colour is read in the Subject column, not on the unread dot (R-44)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.215: one separator between every pair of panes, 3 px, its dots exactly in the middle (D-188)
# v1.1.180: the account to send from is the first field of the composer, From, above To
# v0.5.167: the toolbars are folding toolbars: their rules, the Menu button first in the row of each, Mark then Tag then Archive in Mail's row (D-154)
# v0.5.136: the Menu has Tags…, File has Import… (0.5.132), the Tag button follows Mark
# v0.0.88: Selection grey without the focus, black 1 px lines between the panes and the column headers, Sorted by in bold
# v0.0.85: Mark and Move to have icons, Archive is in the toolbar, the menu arrows sit beside the text; section icons are Breeze files
# v0.0.82: The Menu button shows the three-bar character U+2630
# v0.0.81: The Menu button is the first, blue button of every toolbar and the row of the menu bar is gone; New is a plain toolbar button
# v0.0.79: A test of the lines between the column headers
# v0.0.78: Tests of the composer look and of rich text as the default
# v0.0.77: Tests of the message list and the reading pane
# v0.0.76: Tests of the root of the Menu, blue accent and the section icons
# v0.0.73: Initial tests of the look of the main window


def test_every_widget_the_stylesheet_names_exists(world: World) -> None:
    window = world.window
    sheet = theme.stylesheet()
    names = {"folderTree": window.tree, "sideHeading": window.side_heading, "sectionBar": window.section_bar}
    for name, widget in names.items():
        assert f"#{name}" in sheet and widget.objectName() == name
    assert 'QToolBar[folding="true"]' in sheet and window.window_toolbar.property("folding") is True
    assert window.search_pill.objectName() == "searchPill" and window.scope.parent() is window.search_pill and window.search_box.parent() is window.scope.parent()
    assert all(button.objectName() == "sectionButton" for button in window.section_bar.buttons.values()) and "#sectionButton" in sheet
    assert window.toolbar.button_for(window.act_new).isVisibleTo(window.toolbar) and all(isinstance(button, QToolButton) and button.objectName() == "menuButton" for button in window.menu_buttons) and "#menuButton" in sheet
    assert window.styleSheet() == sheet


def test_one_menu_button_holds_the_menus_and_the_heading_follows_the_section(world: World, qtbot: QtBot) -> None:
    window = world.window
    bar_actions = window.menuBar().actions()
    assert len(bar_actions) == 1 and "Menu" in bar_actions[0].text()
    assert [action.text().replace("&", "") for action in menu_named(window).actions()] == ["Settings…", "Accounts…", "Rules…", "Tags…", "Sync conflicts…", "", "File", "Message", "Folder", "View", "Help", "", "Exit"]
    assert window.act_settings.shortcut().toString() == "Ctrl+," and window.act_quit.text() == "Exit" and window.act_quit.shortcut().toString() == ""
    assert [action.text() for action in menu_named(window, "File").actions()] == ["New", "Refresh", "", "Backup…", "Restore…", "", "Import…", "Export…", "", "Show the recovery code…", "Use a recovery code…"]
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
    assert "#ff8c00" not in sheet.lower() and "QToolButton#menuButton, " in sheet and "palette(highlight)" in sheet
    assert [name for name, *_ in SECTIONS] == [MAIL, CALENDAR, CONTACTS, TASKS] and set(SECTION_ICONS) == {MAIL, CALENDAR, CONTACTS, TASKS}
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eeeeee"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2a6fc4"))
    assert TintedSvgEngine.colour_for(QIcon.Mode.Normal, palette) == "#eeeeee" and TintedSvgEngine.colour_for(QIcon.Mode.Normal, palette, accent=True) == "#2a6fc4"
    assert not section_icon("checkmark").isNull() and all(not section_icon(stem).isNull() for stem in SECTION_ICONS.values())


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

    opened = MessageWindow(window.backend, "a", "INBOX", "m", shown(html="", text="x"), lambda *args: None, lambda url: None, window.reading.prepare_message_reader)  # noqa: SLF001
    qtbot.addWidget(opened)
    assert opened.styleSheet() == sheet


def test_rich_text_is_the_default_for_new_messages() -> None:
    from chrono_mail.ui import prefs

    assert prefs.DEFAULTS["compose_html"] is True


def test_the_composer_has_send_first_and_the_look_of_the_main_window(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.composer import ComposeWindow

    composer = ComposeWindow(world.window.backend, world.window.mail_state.mail_accounts(), world.account_id, rich=True)  # noqa: SLF001
    qtbot.addWidget(composer)
    composer.show()
    sheet = theme.stylesheet()
    assert composer.objectName() == "composeWindow" and composer.styleSheet() == sheet and "QMainWindow#composeWindow" in sheet
    assert composer.send_button.objectName() == "sendButton" and "QPushButton#sendButton" in sheet
    assert composer.later_button.objectName() == composer.save_button.objectName() == composer.rich_button.objectName() == "flatButton"
    assert composer.rich.toolbar.objectName() == "richToolbar" and composer.is_rich()
    assert composer.send_button.x() < composer.later_button.x() < composer.save_button.x()  # one row, in this order
    assert composer.send_button.y() < composer.from_box.y() < composer.to.y()  # above the fields; From is the first field
    assert composer.from_box.x() == composer.to.x() and composer.from_box.width() == composer.to.width()  # a field like the others


def test_the_column_headers_of_the_list_have_a_line_between_them() -> None:
    rule = next(line for line in theme.stylesheet().splitlines() if line.startswith("QTreeView#messageList QHeaderView::section"))
    separator = theme_in_use().separator
    assert f"border-right: 1px solid {separator}" in rule and f"border-bottom: 1px solid {separator}" in rule and separator == "#000000"  # the place to grab a column edge


def test_the_menu_button_is_the_first_button_of_every_toolbar_and_the_menu_bar_row_is_gone(world: World) -> None:
    window = world.window
    from PySide6.QtWidgets import QToolBar

    rows: list[ToolbarRow] = [window.toolbar, *(page.toolbar for page in window.pages.values())]
    assert len(rows) == 4 and len(window.menu_buttons) == 4 and window.findChildren(QToolBar) == [window.window_toolbar]  # one toolbar, a row per section
    for row, button in zip(rows, window.menu_buttons, strict=True):
        assert row.widgets_in_order()[0] is button and button.text() == "\u2630  Menu" and button.menu() is menu_named(window)
    row = window.toolbar
    assert row.widgets_in_order()[1:3] == [window.toolbar.button_for(window.act_new), window.toolbar.button_for(window.act_refresh)]
    assert window.menuBar().isHidden() and window.act_settings in window.actions() and window.act_quit in window.actions()
    rule = next(line for line in theme.stylesheet().splitlines() if line.startswith("QToolButton#menuButton, "))
    assert "palette(highlight)" in rule and "newButton" not in theme.stylesheet()


def test_mark_and_move_to_have_icons_and_archive_is_in_the_toolbar(world: World) -> None:
    window = world.window
    from PySide6.QtCore import Qt

    for button in (window.mark_button, window.move_button):
        assert not button.icon().isNull() and button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    order = window.toolbar.widgets_in_order()
    mark = order.index(window.mark_button)
    assert order[mark + 1] is window.tag_button  # Tag comes after Mark (0.5.135)
    assert order[mark + 2] is window.toolbar.button_for(window.act_archive)
    assert "QToolBar[folding=\"true\"] QToolButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center;".replace("{{", "{") in theme.stylesheet()


def focus(view: QAbstractItemView, focus_in: bool, reason: Qt.FocusReason = Qt.FocusReason.OtherFocusReason) -> None:
    QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusIn if focus_in else QEvent.Type.FocusOut, reason))


def test_the_selection_is_blue_with_the_focus_and_grey_without(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.resize(1100, 600)
    window.show()
    window.start()
    settle(qtbot, world, 3)
    window.list_view.setCurrentIndex(window.list_model.index(0, window.list_model.columns.index("subject")))  # a text column: the row's own colour, not an icon on it
    blue = window.palette().color(QPalette.ColorRole.Highlight).name()

    def colour(view: QAbstractItemView) -> str:
        qtbot.wait(50)
        rect = view.visualRect(view.currentIndex())
        image = view.viewport().grab().toImage()
        found = Counter(image.pixelColor(x, y).name() for x in range(rect.left(), rect.right()) for y in range(rect.top() + 1, rect.bottom()))
        return found.most_common(1)[0][0]  # the row's own colour: one pixel could be a letter of its text

    for view in (window.tree, window.list_view):
        assert view.currentIndex().isValid()
        focus(view, True)
        assert view.property("focused") is True and colour(view) == blue
        focus(view, False, Qt.FocusReason.PopupFocusReason)  # a context menu leaves the selection blue
        assert view.property("focused") is True and colour(view) == blue
        focus(view, False)
        assert view.property("focused") is False and colour(view) == selection_grey()
    rect = window.tree.visualRect(window.tree.currentIndex())
    image = window.tree.viewport().grab().toImage()
    darkest = min(image.pixelColor(x, y).lightness() for x in range(rect.right() - 30, rect.right()) for y in range(rect.top(), rect.bottom()))
    assert darkest < 80  # on grey the number is drawn in the colour of text, not white


def dots_of(handle: PaneHandle) -> set[tuple[int, int]]:
    """The places where the separator is drawn in the colour of its dots."""
    image = handle.grab().toImage()
    wanted = QColor(theme_in_use().pane_separator_dots).name()
    return {(x, y) for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).name() == wanted}


def test_every_separator_between_panes_is_the_same_with_its_dots_in_the_middle(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.show()
    splitters = window.findChildren(QSplitter)
    assert {splitter.objectName() for splitter in splitters} >= {"mainSplitter", "contentSplitter"} and len(splitters) >= 4  # also Contacts and Tasks
    for splitter in splitters:
        assert isinstance(splitter, PaneSplitter) and splitter.handleWidth() == PANE_SEPARATOR_WIDTH == 3
        assert all(isinstance(splitter.handle(index), PaneHandle) for index in range(1, splitter.count()))
    assert "QSplitter" not in theme.stylesheet()  # no black lines of a style sheet any more
    handle = window.splitter.handle(1)
    handle.resize(3, 200)
    dots = dots_of(handle)
    assert dots and {x for x, _ in dots} == {1}  # the middle column of three
    rows = sorted({y for _, y in dots})
    assert abs(rows[0] - (199 - rows[-1])) <= 1  # as far from the top as from the bottom
    window.reading.set_place("bottom")
    flat = window.content_splitter.handle(1)
    assert window.content_splitter.orientation() == Qt.Orientation.Vertical and flat.orientation() == Qt.Orientation.Vertical
    flat.resize(200, 3)
    assert {y for _, y in dots_of(flat)} == {1}
    window.reading.set_place("right")


def test_sorted_by_is_bold(world: World) -> None:
    window = world.window
    sheet = theme.stylesheet()
    assert window.sort_button.objectName() == "sortButton" and "QToolButton#sortButton { font-weight: bold; color: palette(text); }" in sheet
