import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from tests.ui.test_ui_rule_folders import started_with_projects
from tests.ui.world import World, answer_input, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folder_undo.py 4.7.657"

# --- Version history ----------------------------------------------------
# v4.7.657: Initial (B-18 step 2, D-369): a folder renamed or deleted in the folder list is taken back with Undo, comes back selected,
#           and Ctrl+Z right after waits for the action


def shown_folder(world: World) -> str | None:
    return world.window.folders._state.view.folder  # noqa: SLF001


def test_a_renamed_folder_gets_its_name_back_and_stays_selected(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started_with_projects(qtbot, world)
    answer_input(monkeypatch, [("Work", True)])
    world.window.folders.rename_folder()
    window = world.window
    wait(qtbot, lambda: window.act_undo.text() == "Undo Rename folder “Projects”" and shown_folder(world) == "Work")
    window.act_undo.trigger()
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Projects")) is not None and window.act_redo.text() == "Redo Rename folder “Projects”")
    wait(qtbot, lambda: shown_folder(world) == "Projects" and window.status_label.text().startswith("Undone: Rename folder “Projects”"))


def test_a_deleted_folder_comes_back_selected_with_its_rule_on_again(world: World, qtbot: QtBot) -> None:
    started_with_projects(qtbot, world)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    window = world.window
    window.folders.delete_folder()
    window.act_undo.trigger()  # right after the delete: it waits for it, then undoes it
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Projects")) is not None and window.act_redo.text() == "Redo Delete folder “Projects”")
    wait(qtbot, lambda: shown_folder(world) == "Projects")
    assert window.status_label.text() == "Undone: Delete folder “Projects”. Rule “Projects rule” is on again"
    window.act_redo.trigger()
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Projects")) is None and window.act_undo.text() == "Undo Delete folder “Projects”")
