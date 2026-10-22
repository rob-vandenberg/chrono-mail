from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_mail.ui.searchfield import FOCUSED_HINT, IDLE_HINT
from chrono_mail.ui.searchhistory import history_text, matching, read_history, remove_rect, with_search
from tests.ui.test_ui_window import started
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_searchfield.py 1.5.259"

# --- Version history ----------------------------------------------------
# v1.5.259: the history watches the main window only (no filter on the whole program: a crash); focus into the message text or a press in the list closes it
# v1.5.258: the history never takes the mouse or the keyboard: a double click with a drag and a plain drag select text in the field while it is shown; its keys come from the field; a press outside closes it
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
    assert world.window.search_pill.look() == "focused"
    for key in (Qt.Key.Key_W, Qt.Key.Key_E):
        qtbot.keyClick(world.window.search_box, key, delay=0)  # the field keeps the keyboard while the history is shown
    assert world.window.search_box.text() == "we" and history.entries() == ["West Coast", "Westcott"]
    cross = remove_rect(history.list.visualItemRect(history.list.item(0))).center()
    qtbot.mouseClick(history.list.viewport(), Qt.MouseButton.LeftButton, pos=cross)
    assert history.entries() == ["Westcott"]
    assert read_history(str(world.window.settings.value("search_history", ""))) == ["invoice", "Westcott"]
    qtbot.keyClick(world.window.search_box, Qt.Key.Key_Z, delay=0)
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
    qtbot.keyClick(world.window.search_box, Qt.Key.Key_Question, delay=0)
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


def history_shown_over(world: World, qtbot: QtBot, text: str, searches: tuple[str, ...]) -> None:
    """Earlier searches, the field holding this text, and the history shown by a click in the field."""
    started(qtbot, world)
    world.window.show()
    settle(qtbot, world, 3)
    for search in searches:
        world.window.search_box.setText(search)
        world.window.search_box.returnPressed.emit()
    world.window.search_box.setText(text)
    qtbot.mouseClick(world.window.search_box, Qt.MouseButton.LeftButton)
    wait(qtbot, world.window.search_history.isVisible)


def test_with_the_history_shown_a_double_click_and_a_drag_select_text_in_the_field(world: World, qtbot: QtBot) -> None:
    """The owner's bug (1.5.257): the history opened at the first press and, being a popup, took the mouse, so a double click
    (or a tap-tap-drag on a touchpad) and a drag could never select text in the field. The presses go through the window, as
    real ones do. Offscreen no window grabs the mouse as on a real screen, so the check on the cause (no popup) is what fails on 1.5.257."""
    history_shown_over(world, qtbot, "invoice march", ("invoice march 2026",))
    box = world.window.search_box
    assert QApplication.activePopupWidget() is None  # the cause: a popup takes every press and key until it closes
    handle = world.window.windowHandle()
    left = box.mapTo(world.window, QPoint(4, box.height() // 2))
    box.setCursorPosition(len(box.text()))
    right = box.mapTo(world.window, QPoint(box.cursorRect().center().x(), box.height() // 2))
    box.deselect()
    QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, left)
    assert box.selectedText() == "invoice"  # the double click reached the field: it selected the word
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, left)
    QTest.mouseMove(handle, right)
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, right)
    assert box.selectedText() == "invoice march"  # a drag selects as in any field
    assert QApplication.focusWidget() is box and QApplication.activePopupWidget() is None


def test_the_field_keeps_the_keyboard_and_works_the_history(world: World, qtbot: QtBot) -> None:
    history_shown_over(world, qtbot, "", ("Message 1", "Message 2"))
    box, history = world.window.search_box, world.window.search_history
    assert history.entries() == ["Message 2", "Message 1"] and QApplication.focusWidget() is box
    for key in (Qt.Key.Key_Down, Qt.Key.Key_Down):
        qtbot.keyClick(box, key, delay=0)
    assert history.list.currentRow() == 1 and box.text() == ""  # the keys moved the highlight, they typed nothing
    qtbot.keyClick(box, Qt.Key.Key_Up, delay=0)
    assert history.list.currentRow() == 0
    qtbot.keyClick(box, Qt.Key.Key_Return, delay=0)
    assert not history.isVisible() and box.text() == "Message 2"
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    qtbot.mouseClick(box, Qt.MouseButton.LeftButton)
    wait(qtbot, history.isVisible)
    qtbot.keyClick(box, Qt.Key.Key_Escape, delay=0)
    assert not history.isVisible() and QApplication.focusWidget() is box


def test_a_press_outside_the_field_and_the_history_closes_it(world: World, qtbot: QtBot) -> None:
    history_shown_over(world, qtbot, "", ("Message 1",))
    history = world.window.search_history
    rect = history.list.visualItemRect(history.list.item(0))
    qtbot.mousePress(history.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert history.isVisible()  # a press on the list itself keeps it
    qtbot.mouseRelease(history.list.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.right() + 50, rect.center().y()))
    qtbot.mouseClick(world.window.list_view.viewport(), Qt.MouseButton.LeftButton)
    assert not history.isVisible()


def test_nothing_puts_a_python_filter_on_the_whole_program() -> None:
    """The crash of 2026-10-21 (1.5.258): a Python event filter on the whole program saw every event of every object, also the
    inner parts of the web view, and PySide crashed making one of them known to Python while the window became active."""
    import re
    from pathlib import Path
    source = Path(__file__).resolve().parents[2] / "src" / "chrono_mail"
    found = [f"{path.relative_to(source)}:{number}" for path in sorted(source.rglob("*.py"))
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if re.search(r"(application|app|instance\(\))\.installEventFilter", line)]
    assert found == []


def test_the_history_closes_when_the_focus_leaves_the_field_or_the_window_is_pressed_elsewhere(world: World, qtbot: QtBot) -> None:
    history_shown_over(world, qtbot, "", ("Message 1",))
    window, history = world.window, world.window.search_history
    window.list_view.setFocus()  # the keyboard goes elsewhere
    wait(qtbot, lambda: not history.isVisible())
    qtbot.mouseClick(window.search_box, Qt.MouseButton.LeftButton)
    wait(qtbot, history.isVisible)
    status = window.statusBar()  # never takes the focus: only the watch on the window can close the list
    where = status.mapTo(window, status.rect().center())
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, where)
    assert not history.isVisible() and QApplication.focusWidget() is window.search_box
