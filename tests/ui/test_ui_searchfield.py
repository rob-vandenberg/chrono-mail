from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui.searchfield import FOCUSED_HINT, IDLE_HINT
from chrono_mail.ui.searchhistory import history_text, matching, read_history, remove_rect, with_search
from tests.ui.test_ui_window import started
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_searchfield.py 0.2.97"

# --- Version history ----------------------------------------------------
# v0.2.97: The ends stay round when the field is higher
# v0.1.93: Initial tests of the looks of the search field and the search history


def focus(world: World, qtbot: QtBot, focus_in: bool) -> None:
    (world.window.search_box if focus_in else world.window.tree).setFocus()
    qtbot.wait(50)


def test_the_history_keeps_ten_searches_newest_first_and_each_once() -> None:
    history: list[str] = []
    for number in range(12):
        history = with_search(history, f"search {number}")
    assert history == [f"search {number}" for number in range(11, 1, -1)]
    history = with_search(history, "search 5")
    assert history[0] == "search 5" and history.count("search 5") == 1 and len(history) == 10
    assert with_search(history, "   ") == history


def test_the_history_matches_what_was_typed_and_survives_bad_settings() -> None:
    history = ["Westcott", "invoice march", "West Coast"]
    assert matching(history, "west") == ["Westcott", "West Coast"]
    assert matching(history, "") == history
    assert read_history(history_text(history)) == history
    assert read_history("not json") == [] and read_history('{"a": 1}') == [] and read_history("") == []


def test_the_three_looks_of_the_search_field(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    world.window.activateWindow()
    settle(qtbot, world, 3)
    world.window.tree.setFocus()
    field = world.window.search_pill
    wait(qtbot, lambda: field.look() == "idle")
    assert field.look() == "idle" and field.line_edit.placeholderText() == IDLE_HINT and field.line_edit.font().italic()
    assert not field.start_icon.isHidden() and field.end_button.isHidden()
    focus(world, qtbot, True)
    assert field.look() == "focused" and field.line_edit.placeholderText() == FOCUSED_HINT and not field.line_edit.font().italic()
    assert field.start_icon.isHidden() and not field.end_button.isHidden()
    world.window.search_box.setText("Message 2")
    world.window.search_box.returnPressed.emit()
    assert field.look() == "active"
    focus(world, qtbot, False)
    assert field.look() == "active" and not field.end_button.isHidden()  # orange for as long as the search is active
    field.end_button.click()
    assert field.look() != "active" and world.window.search_box.text() == ""


def test_the_red_cross_shows_the_whole_folder_again(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    settle(qtbot, world, 3)
    world.window.search_box.setText("Message 2")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.search_pill.end_button.click()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)


def test_searches_go_into_the_history_and_a_click_runs_one_again(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    for text in ("Message 1", "Message 2"):
        world.window.search_box.setText(text)
        world.window.search_box.returnPressed.emit()
    assert read_history(str(world.window.settings.value("search_history", ""))) == ["Message 2", "Message 1"]
    world.window.search_box.clear()
    history = world.window.search_history
    qtbot.mouseClick(world.window.search_box, Qt.MouseButton.LeftButton)
    wait(qtbot, history.isVisible)
    assert history.entries() == ["Message 2", "Message 1"]
    rect = history.list.visualItemRect(history.list.item(1))
    qtbot.mouseClick(history.list.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.left() + 60, rect.center().y()))
    assert not history.isVisible() and world.window.search_box.text() == "Message 1"
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.search_pill.look() == "active"


def test_typing_narrows_the_history_and_the_cross_removes_a_search(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    for text in ("Westcott", "invoice", "West Coast"):
        world.window.search_box.setText(text)
        world.window.search_box.returnPressed.emit()
    world.window.search_box.clear()
    history = world.window.search_history
    qtbot.mouseClick(world.window.search_box, Qt.MouseButton.LeftButton)
    wait(qtbot, history.isVisible)
    assert world.window.search_pill.look() == "focused"  # the history has the keyboard, the field still looks clicked in
    for key in (Qt.Key.Key_W, Qt.Key.Key_E):
        qtbot.keyClick(history.list, key, delay=0)
    assert world.window.search_box.text() == "we" and history.entries() == ["West Coast", "Westcott"]
    cross = remove_rect(history.list.visualItemRect(history.list.item(0))).center()
    qtbot.mouseClick(history.list.viewport(), Qt.MouseButton.LeftButton, pos=cross)
    assert history.entries() == ["Westcott"]
    assert read_history(str(world.window.settings.value("search_history", ""))) == ["invoice", "Westcott"]
    qtbot.keyClick(history.list, Qt.Key.Key_Z, delay=0)
    assert not history.isVisible()  # nothing matches "wez"


def test_a_question_mark_shows_the_help_instead_of_the_history(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    world.window.search_box.setText("Westcott")
    world.window.search_box.returnPressed.emit()
    world.window.search_box.clear()
    qtbot.mouseClick(world.window.search_box, Qt.MouseButton.LeftButton)
    wait(qtbot, world.window.search_history.isVisible)
    qtbot.keyClick(world.window.search_history.list, Qt.Key.Key_Question, delay=0)
    wait(qtbot, world.window.search_help.isVisible)
    assert not world.window.search_history.isVisible()


def test_the_ends_stay_round_at_any_height(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.resize(1900, 700)  # wide enough for the whole toolbar (else the field is in its overflow menu)
    world.window.show()  # a hidden window gets no sizes
    field = world.window.search_pill
    wait(qtbot, lambda: field.corner_radius == field.height() // 2 > 0)
    field.setFixedHeight(40)  # as with a larger text size
    wait(qtbot, lambda: field.corner_radius == 20)
    assert "border-radius: 20px" in field.styleSheet()
