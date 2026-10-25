"""CR-36: Delete folder… is greyed for a standard folder, known by its role (also Chrono Mail's own folders), and usable on a folder the person made."""

from pytestqt.qtbot import QtBot

from tests.ui.test_ui_window import started
from tests.ui.world import World, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_standard_folders.py 3.6.354"

# --- Version history ----------------------------------------------------
# v3.6.354: Initial: Delete folder… greyed for a standard folder (CR-36)


def test_delete_folder_is_greyed_for_a_standard_folder(world: World, qtbot: QtBot) -> None:
    world.call("folder.create", account=world.account_id, folder="Projects")
    started(qtbot, world)
    window = world.window
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Projects")) is not None)  # noqa: SLF001
    for folder, usable in (("Inbox", False), ("Outbox", False), ("Trash", False), ("Projects", True)):
        found = window.folders.find_item((world.account_id, "folder", folder))  # noqa: SLF001
        assert found is not None
        window.tree.setCurrentIndex(found)
        wait(qtbot, lambda: window.mail_state.view.folder == folder)  # noqa: SLF001
        assert window.act_delete_folder.isEnabled() == usable, folder
