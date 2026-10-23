from collections.abc import Mapping

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.engine.changes import Change, FolderNumbers
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import COUNT_ROLE, UNREAD_ROLE
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folder_counts.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: the numbers of quick changes after each other come within a tenth of a second (they follow ten times a second): the test waits for them (D-223)
# v1.9.269: Drafts, Outbox, Scheduled and Snoozed show how many messages they hold, the other folders their unread messages (D-225)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: A refetch asks the order of the view (message.order, R-15)
# v1.1.250: the folder list shows the engine's numbers: a fact told twice or one never heard leaves no wrong number (R-10)
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
    wait(qtbot, lambda: (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "unread"), count(window, "", "favorite", "inboxes")) == (2, 2, 2))
    world.call("message.delete", account=account, folder="Inbox", message=ids(world)["Message 2"])
    wait(qtbot, lambda: (count(window, account, "folder", "Inbox"), count(window, account, "folder", "Trash"), count(window, "", "favorite", "unread")) == (1, 1, 1))
    world.call("folder.mark-read", account=account, folder="Inbox")
    wait(qtbot, lambda: (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "inboxes")) == (0, 0))
    world.call("folder.empty", account=account, folder="Trash")
    wait(qtbot, lambda: count(window, account, "folder", "Trash") == 0)
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
    wait(qtbot, lambda: world.backend._pending == {} and not window.messages.refetching and not window.folders.recounting)  # noqa: SLF001
    assert commands.count("folder.list") == 2 and commands.count("message.order") == 2  # one, then once more for what came meanwhile
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


def test_a_fact_told_twice_leaves_the_engine_numbers(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    message = ids(world)["Message 1"]
    world.call("message.flag", account=account, folder="Inbox", message=message, seen=True)
    assert count(window, account, "folder", "Inbox") == 2
    again = Change("flags", account, "Inbox", message, was_unread=True, unread=False, flags=("seen",), numbers=(FolderNumbers("Inbox", 3, 2),))
    world.backend.changed.emit(again)
    world.backend.changed.emit(again)
    assert count(window, account, "folder", "Inbox") == 2  # before: the window took one off for every time it heard it (0)


def test_a_fact_never_heard_is_put_right_by_the_next(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    tell = world.engine.changed
    world.engine.changed = lambda change: None  # the window never hears this one
    world.call("message.flag", account=account, folder="Inbox", message=ids(world)["Message 1"], seen=True)
    world.engine.changed = tell
    assert count(window, account, "folder", "Inbox") == 3  # not heard: the old number
    world.call("message.flag", account=account, folder="Inbox", message=ids(world)["Message 2"], seen=True)
    assert count(window, account, "folder", "Inbox") == 1  # the engine's number (before: 2, the window's own count stayed off by one)
    assert (count(window, "", "favorite", "unread"), count(window, "", "favorite", "inboxes")) == (1, 1)


def outgoing_numbers(window: MainWindow, account: str) -> tuple[int, ...]:
    """The numbers of Drafts, Outbox, Scheduled and Snoozed: the folders of the account, then their Favorites entries."""
    folders = tuple(count(window, account, "folder", path) for path in ("Drafts", "Outbox", "Scheduled", "Snoozed"))
    return folders + tuple(count(window, "", "favorite", view) for view in ("drafts", "outbox", "scheduled", "snoozed"))


def test_folders_of_outgoing_messages_show_how_many_wait_and_the_others_their_unread(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    world.call("draft.save", account=account, to="you@example.com", subject="Draft", body="x")  # saved read, as every draft
    world.engine.passwords.set(account, ServerRole.OUTGOING, "wrong")
    world.call("mail.send", account=account, to="you@example.com", subject="Failed", body="x")  # stays in the Outbox
    world.engine.passwords.set(account, ServerRole.OUTGOING, "secret")
    for hour in ("08", "09"):
        world.call("mail.send", account=account, to="you@example.com", subject=f"At {hour}", body="x", send_at=f"2030-01-01T{hour}:00")
    world.call("message.snooze", account=account, folder="Inbox", message=ids(world)["Message 1"], until="2030-01-01T10:00")
    wait(qtbot, lambda: window.tree_model.find_item(account, "folder", "Snoozed") is not None and outgoing_numbers(window, account) == (1, 1, 2, 1, 1, 1, 2, 1))
    assert outgoing_numbers(window, account) == (1, 1, 2, 1, 1, 1, 2, 1)  # every message not sent or delivered yet, read or not (D-225)
    assert (count(window, account, "folder", "Inbox"), count(window, "", "favorite", "unread")) == (2, 3)  # the others: unread (Unread counts the snoozed one too, D-061)
    scheduled = window.tree_model.find_item(account, "folder", "Scheduled")
    assert scheduled is not None and int(scheduled.data(UNREAD_ROLE) or 0) == 0  # the window title still counts unread mail


def test_the_number_of_waiting_messages_follows_each_change(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window, account = world.window, world.account_id
    first = world.call("mail.send", account=account, to="you@example.com", subject="One", body="x", send_at="2030-01-01T08:00").message_id
    world.call("mail.send", account=account, to="you@example.com", subject="Two", body="x", send_at="2030-01-01T09:00")
    wait(qtbot, lambda: window.tree_model.find_item(account, "folder", "Scheduled") is not None and count(window, account, "folder", "Scheduled") == 2)
    commands = asked(world, monkeypatch)
    world.call("mail.send-due", account=account, message=first)  # sent now: one fewer waits
    wait(qtbot, lambda: (count(window, account, "folder", "Scheduled"), count(window, "", "favorite", "scheduled")) == (1, 1))
    assert "folder.list" not in commands  # the engine's numbers came with the fact; nothing was counted again
