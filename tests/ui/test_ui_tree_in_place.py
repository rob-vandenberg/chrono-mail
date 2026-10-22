from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_sidebar import FAVORITE_PIN, GROUP_ADD, GroupAddParameters, PinParameters
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import FOLDER_PATH_ROLE, KIND_ROLE, PIN_ROLE
from tests.ui.test_ui_tree_rows import rows_of
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tree_in_place.py 1.9.270"

# --- Version history ----------------------------------------------------
# v1.9.270: The sidebar changes are typed calls (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.252: a selected pin keeps its entry when its folder is renamed (R-41)
# v1.1.251: Initial version: the folder list is built once and never again; every change happens in place, the selection and the open
#           branches stay with their entries (R-11)


class Resets:
    """Counts how often the folder list was emptied as a whole (Qt's reset), and every entry taken away."""

    def __init__(self, window: MainWindow) -> None:
        self.resets = 0
        self.removed = 0
        window.tree_model.modelReset.connect(self._reset)
        window.tree_model.rowsRemoved.connect(self._removed)

    def _reset(self) -> None:
        self.resets += 1

    def _removed(self, parent: QModelIndex, first: int, last: int) -> None:
        self.removed += last - first + 1


def folder(window: MainWindow, path: str) -> QModelIndex:
    return next(index for index in rows_of(window, "folder") if index.data(FOLDER_PATH_ROLE) == path)


def settle(qtbot: QtBot, world: World) -> None:
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


def test_no_change_ever_empties_the_list_and_the_selection_stays(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.call("folder.create", account=account, folder="Projects")
    settle(qtbot, world)
    selected = QPersistentModelIndex(folder(window, "Inbox"))
    window.tree.setCurrentIndex(QModelIndex(selected))
    counted = Resets(window)
    window.apply_preferences()  # Settings OK
    window.tags.arrived.emit()  # noqa: SLF001  the tags changed
    window.folders.sidebar_call(FAVORITE_PIN, PinParameters(account, "Projects"))  # noqa: SLF001
    settle(qtbot, world)
    world.call("folder.create", account=account, folder="Clients")
    world.call("folder.rename", account=account, folder="Clients", new_name="Customers")
    world.call("folder.delete", account=account, folder="Customers")
    window.folders.recount(account)  # noqa: SLF001  a change from outside
    settle(qtbot, world)
    assert counted.resets == 0  # before: every one of these emptied the list and built it again
    assert counted.removed == 1  # only the folder that was deleted
    assert selected.isValid() and window.tree.currentIndex() == QModelIndex(selected)  # the same entry, never put back by hand
    assert any(index.data(KIND_ROLE) == "pinlink" for index in rows_of(window, "pinlink"))


def test_a_renamed_folder_keeps_its_entry_its_selection_and_its_open_branch(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.call("folder.create", account=account, folder="Work")
    world.call("folder.create", account=account, folder="Work/2026")
    settle(qtbot, world)
    work = QPersistentModelIndex(folder(window, "Work"))
    window.tree.expand(QModelIndex(work))
    window.tree.setCurrentIndex(folder(window, "Work/2026"))
    selected = QPersistentModelIndex(window.tree.currentIndex())
    counted = Resets(window)
    world.call("folder.rename", account=account, folder="Work", new_name="Jobs")
    settle(qtbot, world)
    assert counted.resets == 0 and counted.removed == 0
    assert work.isValid() and work.data(FOLDER_PATH_ROLE) == "Jobs" and window.tree.isExpanded(QModelIndex(work))
    assert window.tree.currentIndex() == QModelIndex(selected) and selected.data(FOLDER_PATH_ROLE) == "Jobs/2026"


def test_a_pin_moved_up_keeps_its_selection(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    for name in ("First", "Second"):
        world.call("folder.create", account=account, folder=name)
        world.call("favorite.pin", account=account, folder=name)
    window.folders.load(select_saved=False)  # noqa: SLF001
    settle(qtbot, world)
    second = next(index for index in rows_of(window, "pinlink") if index.data(FOLDER_PATH_ROLE) == "Second")
    window.tree.setCurrentIndex(second)
    selected = QPersistentModelIndex(second)
    row = second.row()
    counted = Resets(window)
    order = window.folders.pin_order()  # noqa: SLF001
    window.folders.move_pin(order, order.index(str(second.data(PIN_ROLE))), -1)  # noqa: SLF001
    settle(qtbot, world)
    assert counted.resets == 0 and counted.removed == 0
    assert selected.row() == row - 1 and window.tree.currentIndex() == QModelIndex(selected)


def test_an_account_put_in_a_group_keeps_its_selected_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    inbox = QPersistentModelIndex(folder(window, "Inbox"))
    window.tree.setCurrentIndex(QModelIndex(inbox))
    counted = Resets(window)
    window.folders.sidebar_call(GROUP_ADD, GroupAddParameters("Work", (account,)))  # noqa: SLF001
    settle(qtbot, world)
    assert any(index.data(KIND_ROLE) == "group" for index in rows_of(window, "group"))
    assert counted.resets == 0 and inbox.isValid() and window.tree.currentIndex() == QModelIndex(inbox)  # moved into the group, not made again
    assert inbox.parent().parent().data(KIND_ROLE) == "group"


def test_the_selected_folder_deleted_selects_its_accounts_inbox(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.call("folder.create", account=account, folder="Old")
    settle(qtbot, world)
    window.tree.setCurrentIndex(folder(window, "Old"))
    settle(qtbot, world)
    world.call("folder.delete", account=account, folder="Old")
    settle(qtbot, world)
    assert window.tree.currentIndex() == folder(window, "Inbox")
    assert window.mail_state.view.folder == "Inbox"  # noqa: SLF001  the list shows the Inbox, not a neighbour Qt picked


def test_a_selected_pin_keeps_its_entry_when_its_folder_is_renamed(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.call("folder.create", account=account, folder="Clients")
    world.call("favorite.pin", account=account, folder="Clients")
    window.folders.load(select_saved=False)  # noqa: SLF001
    settle(qtbot, world)
    pin = next(index for index in rows_of(window, "pinlink") if index.data(FOLDER_PATH_ROLE) == "Clients")
    window.tree.setCurrentIndex(pin)
    selected = QPersistentModelIndex(pin)
    counted = Resets(window)
    world.call("folder.rename", account=account, folder="Clients", new_name="Customers")
    settle(qtbot, world)
    assert counted.resets == 0 and counted.removed == 0  # before: the pin pointed at the old name and its entry went (R-41)
    assert selected.isValid() and selected.data(FOLDER_PATH_ROLE) == "Customers" and window.tree.currentIndex() == QModelIndex(selected)
