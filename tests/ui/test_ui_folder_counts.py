from collections.abc import Mapping

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.engine.changes import Change
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import COUNT_ROLE
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folder_counts.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.158: Initial tests: the folder list follows the facts; a change from outside is fetched once, one at a time (D-146 step 4)


def started(qtbot: QtBot, world: World) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.backend._pending == {})  # noqa: SLF001


def asked(world: World, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The commands the window asks for from now on."""
    found: list[str] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        found.append(command)
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    return found


def count(window: MainWindow, account: str, kind: str, path: str) -> int:
    item = window.tree_model.find_item(account, kind, path)
    assert item is not None, (kind, path)
    return int(item.data(COUNT_ROLE) or 0)


def ids(world: World, folder: str = "Inbox") -> dict[str, str]:
    return {row.subject: row.message_id for row in world.call("message.list", account=world.account_id, folder=folder).messages}


def test_the_numbers_follow_each_change_without_counting_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    assert (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "unread"), count(window, "", "favorite", "inboxes")) == (3, 3, 3)
    commands = asked(world, monkeypatch)
    world.call("message.flag", account=account, folder="Inbox", message=ids(world)["Message 1"], seen=True)
    assert (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "unread"), count(window, "", "favorite", "inboxes")) == (2, 2, 2)
    world.call("message.delete", account=account, folder="Inbox", message=ids(world)["Message 2"])
    assert (count(window, account, "folder", "Inbox"), count(window, account, "folder", "Trash"), count(window, "", "favorite", "unread")) == (1, 1, 1)
    world.call("folder.mark-read", account=account, folder="Inbox")
    assert (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "inboxes")) == (0, 0)
    world.call("folder.empty", account=account, folder="Trash")
    assert count(window, account, "folder", "Trash") == 0
    assert "folder.list" not in commands and "sidebar.show" not in commands  # nothing was counted again


def test_a_folder_made_renamed_and_deleted_is_one_entry_each(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    commands = asked(world, monkeypatch)
    world.call("folder.create", account=account, folder="Projects")
    world.call("folder.create", account=account, folder="Projects/Alpha")
    assert window.tree_model.find_item(account, "folder", "Projects") is not None and window.tree_model.find_item(account, "folder", "Projects/Alpha") is not None
    world.call("folder.rename", account=account, folder="Projects", new_name="Work")
    assert window.tree_model.find_item(account, "folder", "Projects") is None and window.tree_model.find_item(account, "folder", "Work/Alpha") is not None
    world.call("folder.delete", account=account, folder="Work", include_subfolders=True)
    assert window.tree_model.find_item(account, "folder", "Work") is None and window.tree_model.find_item(account, "folder", "Work/Alpha") is None
    assert "folder.list" not in commands and "sidebar.show" not in commands


def test_changes_from_outside_are_fetched_once_one_at_a_time_and_keep_the_selection(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    window.list_view.setCurrentIndex(window.list_model.index(1, 0))
    chosen = str(getattr(window.list_model.row_at(1), "message_id"))
    commands = asked(world, monkeypatch)
    for _ in range(3):  # three big jobs report the mailbox in a row
        world.backend.changed.emit(Change("mailbox_changed", account))
    wait(qtbot, lambda: world.backend._pending == {} and not window._refetching and not window._recounting)  # noqa: SLF001
    assert commands.count("folder.list") == 2 and commands.count("message.list") == 2  # one, then once more for what came meanwhile
    assert "sidebar.show" not in commands  # only that mailbox was counted
    current = window.list_view.currentIndex()
    assert current.isValid() and str(getattr(window.list_model.row_at(current.row()), "message_id")) == chosen


def test_sending_puts_the_message_in_sent_and_takes_it_out_of_the_outbox_without_a_reload(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.engine.passwords.set(account, ServerRole.OUTGOING, "wrong")
    world.call("mail.send", account=account, to="you@example.com", subject="Waiting", body="x")  # fails: it stays in the Outbox
    world.engine.passwords.set(account, ServerRole.OUTGOING, "secret")
    outbox = window.tree_model.find_item(account, "folder", "Outbox")
    assert outbox is not None
    window.tree.setCurrentIndex(outbox.index())
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and world.backend._pending == {})  # noqa: SLF001
    commands = asked(world, monkeypatch)
    world.call("mail.send-outbox")
    assert window.list_model.rowCount() == 0 and "message.list" not in commands
    sent = window.tree_model.find_item(account, "folder", "Sent")
    assert sent is not None
    window.tree.setCurrentIndex(sent.index())
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and world.backend._pending == {})  # noqa: SLF001
    commands.clear()
    world.call("mail.send", account=account, to="you@example.com", subject="Straight away", body="y")
    wait(qtbot, lambda: window.list_model.rowCount() == 2)
    assert "message.list" not in commands
