import pytest
from PySide6.QtCore import QMimeData, QModelIndex, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from pytestqt.qtbot import QtBot

from chrono_mail.ui.dragdata import MESSAGES_MIME_TYPE, messages_mime_data, read_messages_mime_data
from chrono_mail.ui.models import FOLDER_PATH_ROLE
from tests.ui.world import account_node, World, row_of, wait
from tests.ui.test_ui_window import started

# --- Version ------------------------------------------------------------
__version__ = "test_ui_dragdrop.py 0.2.104"

# --- Version history ----------------------------------------------------
# v0.2.104: The drag is offered as the copy kind (the desktop's arrow with a plus) and the drop still moves
# v0.0.48: Dragging messages shows a small badge, not the selected rows
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.17: Initial drag and drop tests


def folder_point(world: World, name: str) -> QPoint:
    account = account_node(world.window)
    for row in range(world.window.tree_model.rowCount(account)):
        index = world.window.tree_model.index(row, 0, account)
        if index.data(FOLDER_PATH_ROLE) == name:
            return world.window.tree.visualRect(index).center()
    raise AssertionError(f"no folder {name}")


def drop(world: World, point: QPoint, data: QMimeData) -> bool:
    event = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    world.window.tree.dropEvent(event)
    return event.isAccepted()


def move_probe(world: World, point: QPoint, data: QMimeData) -> bool:
    event = QDragMoveEvent(QPointF(point), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.setAccepted(False)
    world.window.tree.dragMoveEvent(event)
    return event.isAccepted()


def test_the_list_offers_the_selected_messages_for_dragging(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    indexes = [world.window.list_model.index(0), world.window.list_model.index(1)]
    assert world.window.list_model.flags(indexes[0]) & Qt.ItemFlag.ItemIsDragEnabled
    assert not world.window.list_model.flags(QModelIndex()) & Qt.ItemFlag.ItemIsDragEnabled
    data = world.window.list_model.mimeData(indexes)
    account, messages = read_messages_mime_data(data) or ("", [])
    assert account == world.account_id and [message for _, message in messages] == [row_of(world.window, 0).message_id, row_of(world.window, 1).message_id]
    assert MESSAGES_MIME_TYPE in world.window.list_model.mimeTypes()
    assert world.window.list_view.dragEnabled()


def test_dropping_messages_on_a_folder_moves_them(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    indexes = [world.window.list_model.index(0), world.window.list_model.index(1)]
    data = world.window.list_model.mimeData(indexes)
    junk = folder_point(world, "Junk")
    assert move_probe(world, junk, data)
    assert drop(world, junk, data)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.call("message.list", account=world.account_id, folder="Junk").total == 2


def test_drops_that_make_no_sense_are_refused(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    data = world.window.list_model.mimeData([world.window.list_model.index(0)])
    for point in (folder_point(world, "Inbox"), folder_point(world, "Outbox"), QPoint(2, 2000)):  # same folder, Outbox, nothing there
        assert not move_probe(world, point, data)
        assert not drop(world, point, data)
    other_account = messages_mime_data("someone-else", [("Inbox", "x")])
    assert not drop(world, folder_point(world, "Junk"), other_account)
    for foreign in (QMimeData(), QMimeData()):
        foreign.setData(MESSAGES_MIME_TYPE, b"not json")
        assert read_messages_mime_data(foreign) is None
        assert not drop(world, folder_point(world, "Junk"), foreign)
    foreign_text = QMimeData()
    foreign_text.setText("hello")
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.MoveAction, foreign_text, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    enter.setAccepted(True)
    world.window.tree.dragEnterEvent(enter)
    assert not enter.isAccepted()
    qtbot.wait(100)
    assert world.window.list_model.rowCount() == 3


def test_dragging_shows_a_small_badge_and_not_the_selected_rows(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtGui import QDrag

    from chrono_mail.ui.messagelistview import BADGE_OFFSET, MessageListView, drag_badge

    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    one, many = drag_badge(1), drag_badge(300)
    assert many.width() > one.width() and many.height() == one.height() <= 60  # as high as one line, whatever the selection
    assert one.toImage().pixelColor(2, 2).alpha() == 0 and BADGE_OFFSET > 0  # the corner near the pointer is empty
    view = world.window.list_view
    assert isinstance(view, MessageListView)
    for row in range(3):
        view.selectionModel().select(world.window.list_model.index(row), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    started: list[tuple[int, int]] = []
    kinds: list[object] = []

    def fake_exec(self: QDrag, *args: object) -> object:
        started.append((self.pixmap().width(), self.pixmap().height()))
        kinds.extend(args)
        assert self.mimeData().hasFormat("application/x-chrono-mail-messages") or self.mimeData().formats()
        return Qt.DropAction.IgnoreAction

    monkeypatch.setattr(QDrag, "exec", fake_exec)
    view.startDrag(Qt.DropAction.MoveAction)
    assert started == [(drag_badge(3).width(), drag_badge(3).height())]
    assert kinds and all(kind == Qt.DropAction.CopyAction for kind in kinds)  # the copy kind: the desktop's arrow with a plus, not its hand
