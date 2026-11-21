import pytest
from PySide6.QtCore import QMimeData, QModelIndex, QPoint, QPointF, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from pytestqt.qtbot import QtBot

from chrono_mail.ui.dragdata import FOLDER_MIME_TYPE, folder_mime_data, messages_mime_data, read_folder_mime_data
from chrono_mail.ui.foldertree import OPEN_AFTER
from tests.ui.test_ui_window import settle
from tests.ui.world import World, account_node, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folder_drag.py 4.8.661"

# --- Version history ----------------------------------------------------
# v4.8.661: Initial (B-38, D-372): a folder the person made is dragged onto another folder or onto its account and moves there, stays
#           selected, and Ctrl+Z moves it back; standard folders cannot be picked up; drops that make no sense are refused


def item(world: World, path: str) -> QModelIndex | None:
    return world.window.folders.find_item((world.account_id, "folder", path))


def shown_folder(world: World) -> str | None:
    return world.window.folders._state.view.folder  # noqa: SLF001


def started_with_folders(qtbot: QtBot, world: World) -> None:
    """The window on screen with Projects, Projects/Done and Archive, every branch open, Projects selected."""
    world.window.start()
    settle(qtbot, world, 3)
    for folder in ("Projects", "Projects/Done", "Archive"):
        world.call("folder.create", account=world.account_id, folder=folder)
    wait(qtbot, lambda: all(item(world, folder) is not None for folder in ("Projects", "Projects/Done", "Archive")))
    world.window.tree.expandAll()
    projects = item(world, "Projects")
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - its (empty) list loaded before anything moves


def point(world: World, index: QModelIndex | None) -> QPoint:
    assert index is not None
    rect = world.window.tree.visualRect(index)
    assert rect.isValid()
    return rect.center()


def probe(world: World, at: QPoint, data: QMimeData) -> bool:
    event = QDragMoveEvent(QPointF(at), Qt.DropAction.MoveAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.setAccepted(False)
    world.window.tree.dragMoveEvent(event)
    return event.isAccepted()


def drop(world: World, at: QPoint, data: QMimeData) -> bool:
    event = QDropEvent(QPointF(at), Qt.DropAction.MoveAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    world.window.tree.dropEvent(event)
    return event.isAccepted()


def dragged(world: World, path: str) -> QMimeData:
    return folder_mime_data(world.account_id, path)


def test_a_folder_the_person_made_is_picked_up_and_a_standard_folder_is_not(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started_with_folders(qtbot, world)
    tree = world.window.tree
    taken: list[tuple[str, str] | None] = []

    def fake_exec(self: QDrag, *args: object) -> Qt.DropAction:
        taken.append(read_folder_mime_data(self.mimeData()))
        return Qt.DropAction.IgnoreAction  # let go where nothing takes it (or Escape): nothing changes

    monkeypatch.setattr(QDrag, "exec", fake_exec)
    for path, picked in (("Projects", True), ("Inbox", False), ("Junk", False)):
        index = item(world, path)
        assert index is not None and bool(index.flags() & Qt.ItemFlag.ItemIsDragEnabled) is picked
        qtbot.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, pos=point(world, index))
        qtbot.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, pos=point(world, index))
        tree.startDrag(Qt.DropAction.MoveAction)
    assert taken == [(world.account_id, "Projects")]  # only the folder the person made
    assert not bool(account_node(world.window).flags() & Qt.ItemFlag.ItemIsDragEnabled)
    assert tree.drop_row is None and item(world, "Projects") is not None and item(world, "Archive/Projects") is None


def test_a_folder_dropped_on_another_folder_moves_there_and_stays_selected(world: World, qtbot: QtBot) -> None:
    started_with_folders(qtbot, world)
    archive = point(world, item(world, "Archive"))
    assert probe(world, archive, dragged(world, "Projects"))
    assert world.window.tree.drop_row is not None  # the blue bar on Archive
    assert drop(world, archive, dragged(world, "Projects"))
    assert world.window.tree.drop_row is None
    wait(qtbot, lambda: item(world, "Archive/Projects") is not None and item(world, "Archive/Projects/Done") is not None and item(world, "Projects") is None)
    wait(qtbot, lambda: shown_folder(world) == "Archive/Projects" and world.window.status_label.text() == "Folder moved")
    assert world.window.tree.isExpanded(item(world, "Archive") or QModelIndex())  # the folder it went into is open
    assert {"Archive/Projects", "Archive/Projects/Done"} <= {info.path for info in world.call("folder.list", account=world.account_id)}


def test_a_folder_dropped_on_its_account_goes_to_the_top(world: World, qtbot: QtBot) -> None:
    started_with_folders(qtbot, world)
    top = point(world, account_node(world.window))
    assert probe(world, top, dragged(world, "Projects/Done"))
    assert drop(world, top, dragged(world, "Projects/Done"))
    wait(qtbot, lambda: item(world, "Done") is not None and item(world, "Projects/Done") is None)


def test_ctrl_z_moves_the_folder_back(world: World, qtbot: QtBot) -> None:
    started_with_folders(qtbot, world)
    window = world.window
    assert drop(world, point(world, item(world, "Archive")), dragged(world, "Projects"))
    wait(qtbot, lambda: window.act_undo.text() == "Undo Move folder “Projects”")
    window.act_undo.trigger()
    wait(qtbot, lambda: item(world, "Projects") is not None and item(world, "Archive/Projects") is None and window.act_redo.text() == "Redo Move folder “Projects”")
    wait(qtbot, lambda: shown_folder(world) == "Projects")


def test_drops_that_make_no_sense_are_refused(world: World, qtbot: QtBot) -> None:
    started_with_folders(qtbot, world)
    world.call("folder.create", account=world.account_id, folder="Archive/Done")
    wait(qtbot, lambda: item(world, "Archive/Done") is not None)
    world.window.tree.expandAll()
    favorite = world.window.folders.find_item(("", "favorite", "inboxes"))
    unread = world.window.folders.find_item((world.account_id, "unread", ""))
    refused = (
        ("Projects", item(world, "Projects")),  # onto itself
        ("Projects", item(world, "Projects/Done")),  # into a folder inside itself
        ("Projects/Done", item(world, "Projects")),  # where it is already
        ("Projects", account_node(world.window)),  # at the top already
        ("Projects/Done", item(world, "Archive")),  # a folder named Done is there
        ("Projects", item(world, "Outbox")),  # Chrono Mail's own folder
        ("Projects", favorite),  # Favorites
        ("Projects", unread),  # the account's Unread
        ("Inbox", item(world, "Archive")),  # a standard folder never moves
    )
    for path, onto in refused:
        at = point(world, onto)
        assert not probe(world, at, dragged(world, path)), (path, onto.data() if onto is not None else None)
        assert world.window.tree.drop_row is None
        assert not drop(world, at, dragged(world, path))
    archive = point(world, item(world, "Archive"))
    assert not drop(world, archive, folder_mime_data("someone-else", "Projects"))  # another account's folder
    broken = QMimeData()
    broken.setData(FOLDER_MIME_TYPE, b"not json")
    assert read_folder_mime_data(broken) is None and not drop(world, archive, broken)
    qtbot.wait(100)
    assert {"Projects", "Projects/Done", "Archive", "Archive/Done"} <= {info.path for info in world.call("folder.list", account=world.account_id)}


def test_a_held_folder_opens_closed_folders_and_dragged_messages_do_not(world: World, qtbot: QtBot) -> None:
    started_with_folders(qtbot, world)
    tree = world.window.tree
    for data, delay in ((dragged(world, "Projects"), OPEN_AFTER), (messages_mime_data(world.account_id, [("Inbox", "x")]), -1)):
        enter = QDragEnterEvent(QPointF(5, 5), Qt.DropAction.MoveAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        enter.setAccepted(False)
        tree.dragEnterEvent(enter)
        assert enter.isAccepted() and tree.autoExpandDelay() == delay
