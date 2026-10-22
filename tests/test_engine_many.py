import time
from datetime import datetime, timedelta
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ProtocolError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.protocols.imap import ImapClient, uid_set
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_online import filled, in_step

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_many.py 1.9.265"

# --- Version history ----------------------------------------------------
# v1.9.265: a message not done because its file is gone here is marked missing (the window warns, D-109)
# v1.1.247: Initial: actions on many messages go to the IMAP server in groups; a refused group, a message gone there, offline (R-08)


def sent(world: World, verb: str) -> int:
    return sum(1 for command in world.imap.commands if command.upper().startswith(verb))


def ids(rows: list[Any]) -> tuple[str, ...]:
    return tuple(row.message_id for row in rows)


def test_a_move_of_many_messages_is_one_command_on_the_server(world: World) -> None:
    rows = filled(world, 30)
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    world.imap.commands.clear()
    result = call(world.engine, "message.move-many", account=world.account, folder="Inbox", target="Projects", messages=ids(rows[:20]))
    assert len(result.done) == 20 and result.failed == ()
    assert sent(world, "UID MOVE") == 1 and sent(world, "UID SEARCH") == 1 and sent(world, "NOOP") <= 2
    assert len(world.rows("Inbox")) == 10 and len(world.rows("Projects")) == 20 and len(world.imap.uids("Projects")) == 20
    in_step(world, ("Inbox", "INBOX"), ("Projects", "Projects"))  # each new UID was recorded: nothing downloaded again


def test_delete_to_trash_and_for_good_and_marks_go_in_groups(world: World) -> None:
    rows = filled(world, 12)
    world.imap.commands.clear()
    marked = call(world.engine, "message.flag-many", account=world.account, folder="Inbox", messages=ids(rows), seen=True, flagged=True)
    assert len(marked.done) == 12 and sent(world, "UID STORE") == 1 and all("seen" in flags and "flagged" in flags for _, flags in marked.flags)
    world.imap.commands.clear()
    trashed = call(world.engine, "message.delete-many", account=world.account, folder="Inbox", messages=ids(rows[:8]))
    assert len(trashed.done) == 8 and sent(world, "UID MOVE") == 1 and len(world.rows("Trash")) == 8
    world.imap.commands.clear()
    in_trash = ids(world.rows("Trash"))
    gone = call(world.engine, "message.delete-many", account=world.account, folder="Trash", messages=in_trash)
    assert len(gone.done) == 8 and sent(world, "UID STORE") == 1 and sent(world, "UID EXPUNGE") == 1
    assert world.rows("Trash") == [] and world.imap.uids("Trash") == []
    in_step(world, ("Inbox", "INBOX"), ("Trash", "Trash"))


def test_a_copy_of_many_is_one_command_and_nothing_comes_twice(world: World) -> None:
    rows = filled(world, 6)
    call(world.engine, "folder.create", account=world.account, folder="Kept")
    world.imap.commands.clear()
    copied = call(world.engine, "message.copy-many", account=world.account, folder="Inbox", target="Kept", messages=ids(rows))
    assert len(copied.done) == 6 and sent(world, "UID COPY") == 1
    in_step(world, ("Inbox", "INBOX"), ("Kept", "Kept"))


def test_archive_junk_snooze_and_back_go_in_groups(world: World) -> None:
    rows = filled(world, 9)
    world.imap.commands.clear()
    archived = call(world.engine, "message.archive-many", account=world.account, folder="Inbox", messages=ids(rows[:3]))
    junked = call(world.engine, "message.junk-many", account=world.account, folder="Inbox", messages=ids(rows[3:6]))
    snoozed = call(world.engine, "message.snooze-many", account=world.account, folder="Inbox", until=(datetime.fromtimestamp(world.engine.clock()) + timedelta(days=2)).strftime("%Y-%m-%dT09:00"), messages=ids(rows[6:]))
    assert (len(archived.done), len(junked.done), len(snoozed.done)) == (3, 3, 3) and sent(world, "UID MOVE") == 3
    back = call(world.engine, "message.unarchive-many", account=world.account, folder="Archive", messages=ids(world.rows("Archive")))
    clean = call(world.engine, "message.not-junk-many", account=world.account, folder="Junk", messages=ids(world.rows("Junk")))
    assert len(back.done) == 3 and len(clean.done) == 3 and len(world.rows("Inbox")) == 6


def test_a_refused_group_changes_nothing_here_and_says_why_once_per_message(world: World) -> None:
    rows = filled(world, 5)
    world.imap.refuse_moves = True
    result = call(world.engine, "message.junk-many", account=world.account, folder="Inbox", messages=ids(rows))
    assert result.done == () and len(result.failed) == 5 and all("not allowed" in item.reason for item in result.failed)
    assert len(world.rows("Inbox")) == 5 and len(world.imap.uids("INBOX")) == 5


def test_a_message_gone_on_the_server_does_not_stop_the_others(world: World) -> None:
    rows = filled(world, 4)
    world.imap.folders["INBOX"].messages.pop(0)  # another program deleted one meanwhile
    result = call(world.engine, "message.delete-many", account=world.account, folder="Inbox", messages=ids(rows), permanent=True)
    assert len(result.done) == 4 and result.failed == ()  # deleting what the server deleted already is what the person wanted
    assert world.rows("Inbox") == [] and world.imap.uids("INBOX") == []


def test_offline_the_group_is_done_here_and_sent_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = filled(world, 10)
    world.engine.notice = lambda text: None
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    result = call(world.engine, "message.delete-many", account=world.account, folder="Inbox", messages=ids(rows[:6]), permanent=True)
    assert len(result.done) == 6 and len(world.rows("Inbox")) == 4 and len(world.imap.uids("INBOX")) == 10
    monkeypatch.undo()
    world.fetch()
    assert len(world.imap.uids("INBOX")) == 4
    in_step(world, ("Inbox", "INBOX"))


def test_a_uid_set_from_the_server_is_read_and_capped() -> None:
    assert uid_set("4:6,9") == [4, 5, 6, 9] and uid_set("7:5") == [7, 6, 5]
    for bad in ("", "a", "4:", "1:200000"):
        with pytest.raises(ProtocolError):
            uid_set(bad)


def test_a_thousand_messages_move_in_a_few_commands(world: World) -> None:
    filled(world, 1000)
    rows = list(call(world.engine, "message.list", account=world.account, folder="Inbox", limit=1000).messages)
    call(world.engine, "folder.create", account=world.account, folder="Old")
    world.imap.commands.clear()
    started = time.monotonic()
    result = call(world.engine, "message.move-many", account=world.account, folder="Inbox", target="Old", messages=ids(rows))
    seconds = time.monotonic() - started
    assert len(result.done) == 1000 and sent(world, "UID MOVE") == 2 and len(world.imap.commands) < 20
    print(f"1,000 messages moved in {seconds:.2f} s, {len(world.imap.commands)} server commands")


def test_a_message_whose_file_is_gone_here_is_marked_missing_and_the_others_are_done(world: World) -> None:
    rows = filled(world, 3)
    with open_mailbox(world.engine, world.account) as mailbox:
        for path in mailbox.store.root.rglob(f"{rows[1].message_id}*"):
            path.unlink()  # the file is gone; the index still lists the message
    marked = call(world.engine, "message.flag-many", account=world.account, folder="Inbox", messages=ids(rows), flagged=True)
    assert set(marked.done) == {rows[0].message_id, rows[2].message_id}
    assert [(item.message, item.missing) for item in marked.failed] == [(rows[1].message_id, True)]
