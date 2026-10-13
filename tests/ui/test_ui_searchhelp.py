from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui.searchhelp import HELP_LINES
from tests.ui.test_ui_window import started
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_searchhelp.py 0.1.92"

# --- Version history ----------------------------------------------------
# v0.1.92: Initial tests of the search help


def test_the_search_box_tells_how_to_get_help(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    assert world.window.search_box.placeholderText() == "Search (type '?' for help)"


def test_the_help_lines_and_their_order() -> None:
    assert [line.name for line in HELP_LINES] == ["words", "subject", "body", "from", "to", '"exact phrase"', "-word"]
    assert [line.inserted for line in HELP_LINES] == ["", "subject:", "body:", "from:", "to:", '""', "-"]
    assert HELP_LINES[0].icon == "edit-find" and all(line.icon == "dialog-question" for line in HELP_LINES[1:])


def test_typing_a_question_mark_opens_the_help_and_picking_from_fills_the_box(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    help_list = world.window.search_help
    world.window.search_box.setText("?")
    wait(qtbot, help_list.isVisible)
    assert help_list.list.currentRow() == 0 and help_list.list.count() == len(HELP_LINES)
    qtbot.keyClick(help_list.list, Qt.Key.Key_Down, delay=0)
    qtbot.keyClick(help_list.list, Qt.Key.Key_Down, delay=0)
    qtbot.keyClick(help_list.list, Qt.Key.Key_Down, delay=0)
    qtbot.keyClick(help_list.list, Qt.Key.Key_Return, delay=0)
    assert not help_list.isVisible()
    assert world.window.search_box.text() == "from:"
    assert world.window.search_box.cursorPosition() == len("from:")


def test_the_phrase_line_puts_the_cursor_between_the_quotes(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    world.window.search_box.setText("?")
    wait(qtbot, world.window.search_help.isVisible)
    world.window.search_help.list.setCurrentRow(5)
    qtbot.keyClick(world.window.search_help.list, Qt.Key.Key_Return, delay=0)
    assert world.window.search_box.text() == '""' and world.window.search_box.cursorPosition() == 1


def test_escape_closes_the_help_and_other_keys_go_to_the_search_box(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    help_list = world.window.search_help
    world.window.search_box.setText("?")
    wait(qtbot, help_list.isVisible)
    qtbot.keyClick(help_list.list, Qt.Key.Key_Escape, delay=0)
    wait(qtbot, lambda: not help_list.isVisible())
    assert world.window.search_box.text() == "?"
    world.window.search_box.clear()
    world.window.search_box.setText("?")
    wait(qtbot, help_list.isVisible)
    qtbot.keyClick(help_list.list, Qt.Key.Key_A, delay=0)
    assert not help_list.isVisible()
    assert world.window.search_box.text() == "?a"
