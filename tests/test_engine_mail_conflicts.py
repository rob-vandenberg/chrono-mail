from typing import Any

import pytest

from chrono_mail.core import ProtocolError
from chrono_mail.protocols.imap import ImapClient
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_online import filled, in_step

# --- Version ------------------------------------------------------------
__version__ = "test_engine_mail_conflicts.py 1.1.237"

# --- Version history ----------------------------------------------------
# v1.1.237: a folder deleted on the server with changes here: Use Chrono Mail makes it again with its mail, Use Server data deletes it here,
#           Keep here only when mail exists only here; a folder renamed on the server is told as renamed (R-30)
# v1.1.228: Retry sync: refused again, the same row comes back once; taken, the change reaches the server
# v1.1.227: Initial: mail changes made offline that the server refuses for good are sync conflicts; marks merged per mark; what waits (D-195)


def offline(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The server cannot be reached for the person's actions (they are done here and wait); the next check reaches it again."""
    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("The server could not be reached")
    world.engine.imap_link(world.account).close()  # the connection kept open by an earlier action is gone too
    monkeypatch.setattr(ImapClient, "connect", unreachable)


def set_server_flags(world: World, uid: int, flags: set[str]) -> None:
    """Another program changes the marks of a message in the Inbox on the server."""
    with world.imap.lock:
        next(message for message in world.imap.folders["INBOX"].messages if message.uid == uid).flags = set(flags)


def mail_conflicts(world: World) -> list[Any]:
    return [c for c in call(world.engine, "conflict.list").conflicts if c.kind == "mail"]


def waiting(world: World) -> int:
    return sum(account.changes for account in call(world.engine, "sync.waiting").accounts if account.account == world.account)


def test_marks_changed_offline_and_refused_are_a_conflict_settled_with_the_server_s_marks(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    (row,) = filled(world, 1)
    offline(world, monkeypatch)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=row.message_id, seen=True)
    monkeypatch.undo()
    assert waiting(world) == 1
    world.imap.refuse_stores = True
    result = world.fetch()
    assert result.failed == ()  # no line at every check: a conflict
    (conflict,) = mail_conflicts(world)
    assert (conflict.reason, conflict.name, conflict.local_allowed, conflict.server_allowed, conflict.keep_allowed) == ("marks_refused", "Message 1", False, True, False)
    assert [(f.label, f.local, f.server) for f in conflict.fields] == [("Marks", "read", "unread")] and "read only" in conflict.detail
    assert waiting(world) == 0  # held by its conflict: it no longer waits
    assert world.fetch().failed == () and len(mail_conflicts(world)) == 1  # not sent again, not doubled
    refused = call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
    assert refused.open == (conflict.conflict,)  # Chrono Mail's side is never taken: the server refused it
    assert call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
    assert "seen" not in world.rows("Inbox")[0].flags and mail_conflicts(world) == []
    world.imap.refuse_stores = False
    in_step(world, ("Inbox", "INBOX"))


def test_a_move_made_offline_and_refused_is_moved_back_here(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    (row,) = filled(world, 1)
    call(world.engine, "folder.create", account=world.account, folder="Work")
    offline(world, monkeypatch)
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=row.message_id, target="Work")
    monkeypatch.undo()
    world.imap.refuse_moves = True
    world.fetch()
    (conflict,) = mail_conflicts(world)
    assert conflict.reason == "move_refused" and [(f.local, f.server) for f in conflict.fields] == [("Work", "Inbox")]
    world.fetch()
    assert world.imap.uids("Work") == [] and len(world.imap.uids("INBOX")) == 1  # never copied there behind the person's back
    call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
    assert len(world.rows("Inbox")) == 1 and world.rows("Work") == []
    world.imap.refuse_moves = False
    in_step(world, ("Inbox", "INBOX"), ("Work", "Work"))


def test_a_folder_made_offline_and_refused_is_kept_here_only_never_deleted(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    filled(world, 1)
    offline(world, monkeypatch)
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    monkeypatch.undo()
    world.imap.refuse_creates = True
    world.fetch()
    (conflict,) = mail_conflicts(world)
    assert (conflict.reason, conflict.server_allowed, conflict.keep_allowed) == ("folder_refused", False, True)
    assert call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").open == (conflict.conflict,)  # would delete mail
    assert call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="keep").settled == (conflict.conflict,)
    world.imap.refuse_creates = False
    world.fetch()
    assert mail_conflicts(world) == [] and "Projects" not in world.imap.folders  # kept here only: never made there, never asked again
    assert any(folder.path == "Projects" for folder in call(world.engine, "folder.list", account=world.account))


def test_different_marks_changed_on_each_side_are_both_kept_and_the_same_mark_is_no_conflict(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = filled(world, 2)
    offline(world, monkeypatch)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=first.message_id, seen=True)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=second.message_id, seen=True)
    monkeypatch.undo()
    set_server_flags(world, 1, {"\\Flagged"})  # the phone flags the first meanwhile
    set_server_flags(world, 2, {"\\Seen"})  # and reads the second: the same change
    world.fetch()
    world.fetch()
    assert world.imap.flags_of("INBOX", 1) == {"\\Seen", "\\Flagged"} and world.imap.flags_of("INBOX", 2) == {"\\Seen"}
    by_subject = {row.subject: row for row in world.rows("Inbox")}
    assert {"seen", "flagged"} <= set(by_subject["Message 1"].flags) and mail_conflicts(world) == []


def test_retry_sync_sends_a_refused_change_again(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    (row,) = filled(world, 1)
    offline(world, monkeypatch)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=row.message_id, seen=True)
    monkeypatch.undo()
    world.imap.refuse_stores = True  # read only for now
    world.fetch()
    (conflict,) = mail_conflicts(world)
    result = call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="retry")
    assert result.settled == (conflict.conflict,) and result.retried == (world.account,) and mail_conflicts(world) == []
    world.fetch()  # still refused: the same row comes back, once
    assert [c.reason for c in mail_conflicts(world)] == ["marks_refused"]
    world.imap.refuse_stores = False  # allowed again
    (again,) = mail_conflicts(world)
    call(world.engine, "conflict.settle", conflicts=(again.conflict,), side="retry")
    world.fetch()
    assert mail_conflicts(world) == [] and "\\Seen" in world.imap.flags_of("INBOX", 1)


def folder_with_a_change_here(world: World, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401 - a ConflictSummary
    """Projects with one message; flagged here offline; then Projects deleted on the server: one folder conflict."""
    world.imap.add("Projects", mail(7))
    world.fetch()
    (row,) = world.rows("Projects")
    offline(world, monkeypatch)
    call(world.engine, "message.flag", account=world.account, folder="Projects", message=row.message_id, flagged=True)
    monkeypatch.undo()
    with world.imap.lock:
        del world.imap.folders["Projects"]
    world.fetch()
    (conflict,) = [c for c in call(world.engine, "conflict.list").conflicts if c.kind == "folder"]
    return conflict


def folders_here(world: World) -> list[str]:
    return [str(folder) for folder in call(world.engine, "folder.list", account=world.account)]


def test_a_folder_deleted_on_the_server_with_marks_changed_here_can_be_deleted_here(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = folder_with_a_change_here(world, monkeypatch)
    assert (conflict.reason, conflict.name, conflict.local_allowed, conflict.server_allowed, conflict.keep_allowed) == (
        "folder_deleted_on_server", "Projects", True, True, False,
    )
    assert call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
    assert "Projects" not in world.imap.folders
    assert all("Projects" not in str(folder) for folder in folders_here(world))


def test_use_chrono_mail_makes_a_folder_deleted_on_the_server_again_with_its_mail(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = folder_with_a_change_here(world, monkeypatch)
    result = call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
    assert result.retried == (world.account,)
    world.fetch()
    assert "Projects" in world.imap.folders and len(world.imap.folders["Projects"].messages) == 1
    assert "\\Flagged" in world.imap.folders["Projects"].messages[0].flags
    assert [c for c in call(world.engine, "conflict.list").conflicts if c.kind == "folder"] == []


def test_a_folder_with_mail_only_here_is_never_deleted_by_use_server_data(world: World) -> None:
    from chrono_mail.engine.mailbox import open_mailbox

    world.imap.add("Projects", mail(7))
    world.fetch()
    with open_mailbox(world.engine, world.account) as mailbox:
        mailbox.store.deliver(("Projects",), mail(8), verify=True)  # the server never had it
    with world.imap.lock:
        del world.imap.folders["Projects"]
    world.fetch()
    (conflict,) = [c for c in call(world.engine, "conflict.list").conflicts if c.kind == "folder"]
    assert (conflict.reason, conflict.server_allowed, conflict.keep_allowed) == ("folder_deleted_with_mail_here", False, True)
    refused = call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
    assert refused.open == (conflict.conflict,) and "would lose it" in refused.problems[0]
    assert call(world.engine, "conflict.settle", conflicts=(conflict.conflict,), side="keep").settled == (conflict.conflict,)
    world.fetch()
    assert "Projects" not in world.imap.folders and [c for c in call(world.engine, "conflict.list").conflicts if c.kind == "folder"] == []
    with open_mailbox(world.engine, world.account) as mailbox:
        assert len(mailbox.store.list_messages(("Projects",))) == 2  # kept here, nothing lost


def test_a_folder_renamed_on_the_server_is_told_as_renamed(world: World) -> None:
    told: list[tuple[str, str, str]] = []
    world.engine.changed = lambda change: told.append((change.kind, change.folder, change.to_folder))
    world.imap.add("Projects", mail(7))
    world.fetch()
    with world.imap.lock:
        world.imap.folders["Work"] = world.imap.folders.pop("Projects")
    world.fetch()
    assert ("folder_renamed", "Projects", "Work") in told and not any(kind == "folder_deleted" for kind, _, _ in told)
    assert [row.subject for row in world.rows("Work")] == ["Message 7"]
