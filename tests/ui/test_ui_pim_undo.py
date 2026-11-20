from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_mail.ui.sections import TASKS
from tests.ui.test_ui_calendar_title_in_place import click, editing, place, settled, shown, title_of
from tests.ui.test_ui_contacts import open_contacts
from tests.ui.test_ui_contacts import seed as seed_contacts
from tests.ui.test_ui_contacts import select_rows
from tests.ui.test_ui_tasks import names, open_tasks, select
from tests.ui.test_ui_tasks import seed as seed_tasks
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pim_undo.py 4.7.656"

# --- Version history ----------------------------------------------------
# v4.7.656: Initial (B-18 step 3, D-368): Ctrl+Z and Ctrl+Y in Tasks, Contacts and Calendar; the menu names the action of the section shown;
#           Ctrl+Z right after Delete undoes it; a text field keeps its own Ctrl+Z; what comes back is selected


def press(key: Qt.Key, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.ControlModifier) -> None:
    focused = QApplication.focusWidget()
    assert focused is not None
    QTest.keyClick(focused, key, modifiers)


def test_ctrl_z_brings_a_deleted_task_back_selected_and_ctrl_y_deletes_it_again(world: World, qtbot: QtBot) -> None:
    seed_tasks(world)
    page = open_tasks(world, qtbot, 4)
    window = world.window
    window.show()
    page.list.setFocus()
    select(page, "Tidy the desk")
    page.delete_selected()
    wait(qtbot, lambda: "Tidy the desk" not in names(page) and window.act_undo.text() == "Undo Delete (1 task)")
    assert window.act_undo.isEnabled() and not window.act_redo.isEnabled()
    page.list.setFocus()
    press(Qt.Key.Key_Z)
    wait(qtbot, lambda: "Tidy the desk" in names(page) and window.act_redo.text() == "Redo Delete (1 task)")
    back = next(str(getattr(row, "item")) for row in page.model.rows() if getattr(row, "summary") == "Tidy the desk")
    wait(qtbot, lambda: page._selected_items() == [back])  # noqa: SLF001
    assert window.act_undo.text() == "Undo" and not window.act_undo.isEnabled()
    page.list.setFocus()
    press(Qt.Key.Key_Y)
    wait(qtbot, lambda: "Tidy the desk" not in names(page) and window.act_undo.text() == "Undo Delete (1 task)")


def test_ctrl_z_right_after_delete_waits_for_it_and_undoes_it(world: World, qtbot: QtBot) -> None:
    seed_tasks(world)
    page = open_tasks(world, qtbot, 4)
    world.window.show()
    page.list.setFocus()
    select(page, "Pay rent", "Plan holiday")
    page.delete_selected()
    press(Qt.Key.Key_Z)  # before the engine answered the delete
    wait(qtbot, lambda: sorted(names(page)) == ["Old job", "Pay rent", "Plan holiday", "Tidy the desk"] and world.window.act_redo.text() == "Redo Delete (2 tasks)")


def test_each_section_shows_its_own_undo(world: World, qtbot: QtBot) -> None:
    seed_tasks(world)
    seed_contacts(world)
    page = open_contacts(world, qtbot, 3)
    window = world.window
    select_rows(page, 0)
    page.delete_selected()
    wait(qtbot, lambda: window.act_undo.text() == "Undo Delete (1 contact)")
    window.switch_section(TASKS)
    assert window.act_undo.text() == "Undo" and not window.act_undo.isEnabled()


def test_ctrl_z_in_the_search_field_undoes_the_typing_and_not_the_delete(world: World, qtbot: QtBot) -> None:
    seed_tasks(world)
    page = open_tasks(world, qtbot, 4)
    world.window.show()
    select(page, "Tidy the desk")
    page.delete_selected()
    wait(qtbot, lambda: world.window.act_undo.text() == "Undo Delete (1 task)")
    page.search.setFocus()
    QTest.keyClicks(page.search, "abc")
    press(Qt.Key.Key_Z)
    assert page.search.text() == "" and world.window.act_undo.text() == "Undo Delete (1 task)"


def test_a_title_typed_in_place_is_taken_back_with_ctrl_z(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    click(qtbot, canvas, rect)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Team lunch")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch" and world.window.act_undo.text() == "Undo Edit (1 appointment)")
    settled(qtbot, world)
    canvas.setFocus()
    press(Qt.Key.Key_Z)
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Lunch" and any(one.summary == "Lunch" for one in page.entries))
    wait(qtbot, lambda: page.selected is not None and page.selected.row.item == entry.row.item)
