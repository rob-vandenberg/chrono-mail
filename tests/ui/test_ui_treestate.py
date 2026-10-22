from PySide6.QtCore import QModelIndex, QSettings
from pytestqt.qtbot import QtBot

from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import KIND_ROLE
from tests.ui.test_ui_models import Info
from tests.ui.test_ui_window import started
from tests.ui.world import World, dialogs, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_treestate.py 1.9.264"

# --- Version history ----------------------------------------------------
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v0.0.75: Initial tests of the remembered state of the folder list


def node(window: MainWindow, kind: str, text: str | None = None) -> QModelIndex:
    for row in range(window.tree_model.rowCount()):
        index = window.tree_model.index(row, 0)
        if index.data(KIND_ROLE) == kind and (text is None or str(index.data()).startswith(text)):
            return index
    raise AssertionError(f"no {kind} {text}")


def test_a_closed_heading_and_account_stay_closed_when_the_tree_is_rebuilt(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    favorites, account = node(window, "section", "Favorites"), node(window, "account")
    assert window.tree.isExpanded(favorites) and window.tree.isExpanded(account)  # the defaults
    window.tree.collapse(favorites)
    window.tree.collapse(account)
    window.folders.lay_out()  # noqa: SLF001 - what every refresh does
    assert not window.tree.isExpanded(node(window, "section", "Favorites")) and not window.tree.isExpanded(node(window, "account"))
    window.tree.expand(node(window, "account"))
    window.folders.lay_out()  # noqa: SLF001
    assert window.tree.isExpanded(node(window, "account")) and not window.tree.isExpanded(node(window, "section", "Favorites"))


def test_the_state_is_kept_for_the_next_start(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.tree.collapse(node(window, "section", "Favorites"))
    window.tree.collapse(node(window, "account"))
    window.settings.sync()
    again = MainWindow(world.backend, QSettings(str(world.settings_path), QSettings.Format.IniFormat))
    qtbot.addWidget(again)
    again.show()
    again.start()
    wait(qtbot, lambda: again.tree_model.rowCount() > 0 and again.list_model.rowCount() > 0)
    assert not again.tree.isExpanded(node(again, "section", "Favorites")) and not again.tree.isExpanded(node(again, "account"))
    again.close()


def test_a_damaged_saved_state_is_ignored(world: World) -> None:
    world.window.settings.setValue("tree_state", "{not json")
    assert world.window.memory.tree_state == {}  # noqa: SLF001
    world.window.settings.setValue("tree_state", '{"a": true, "b": 3, "c": false}')
    assert world.window.memory.tree_state == {"a": True, "c": False}  # noqa: SLF001


def test_a_folder_with_subfolders_remembers_its_state(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.mail_state.folders[world.account_id] = [*window.mail_state.folders[world.account_id], Info("Archive/2026/Q1", "other"), Info("Archive/2025", "other")]  # noqa: SLF001
    window.folders.lay_out()  # noqa: SLF001

    def archive() -> QModelIndex:
        account = node(window, "account")
        return next(window.tree_model.index(row, 0, account) for row in range(window.tree_model.rowCount(account)) if str(window.tree_model.index(row, 0, account).data()).startswith("Archive"))

    assert not window.tree.isExpanded(archive())  # folders start closed
    window.tree.expand(archive())
    window.folders.lay_out()  # noqa: SLF001
    assert window.tree.isExpanded(archive())
    window.tree.collapse(archive())
    window.folders.lay_out()  # noqa: SLF001
    assert not window.tree.isExpanded(archive())
