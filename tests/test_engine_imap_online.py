import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ChronoMailError, ProtocolError, StorageError
from chrono_mail.engine.mailbox import Mailbox
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from tests.enginefactory import make_engine
from tests.fakeimap import FakeFolder, FakeImapServer
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_imap_online.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: a message of a folder that exists only here is deleted without connecting to the server (RV-183, RV-184); after a folder
#           delete an action in Inbox works (RV-170); a folder tree is looked at whole before any of it is deleted (RV-176); mail too
#           large to download is named (RV-172)
# v2.4.330: Junk by hand works when the server's Junk folder is not subscribed (it failed with "Mailbox already exists", W7)
# v1.1.238: a move on a server without MOVE and UIDPLUS leaves another program's deleted-marked mail alone (R-35)
# v0.4.121: Many actions at once, a mail check while deleting, a connection the server closed, a renumbered folder
# v0.4.120: Initial tests: the actions of an IMAP account are done on the server first (D-117)


def filled(world: World, count: int) -> list[Any]:
    for number in range(1, count + 1):
        world.imap.add("INBOX", mail(number))
    world.fetch()
    return sorted(world.rows("Inbox"), key=lambda row: row.subject)


def in_step(world: World, *folders: tuple[str, str]) -> None:
    """A sync finds nothing to do, and here and the server hold the same number of messages per folder."""
    result = world.fetch()
    assert result.downloaded == 0 and result.failed == () and result.blocked == ()
    for local, server in folders:
        assert len(world.rows(local)) == len(world.imap.uids(server)), local


def test_every_action_is_done_on_the_server_before_any_sync(world: World) -> None:
    rows = filled(world, 6)
    first, second, third, fourth, fifth, sixth = (row.message_id for row in rows)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=first, seen=True, flagged=True)
    assert world.imap.flags_of("INBOX", 1) == {"\\Seen", "\\Flagged"}
    call(world.engine, "message.copy", account=world.account, folder="Inbox", message=second, target="Junk")
    assert len(world.imap.uids("Junk")) == 1 and len(world.imap.uids("INBOX")) == 6
    call(world.engine, "folder.create", account=world.account, folder="Work")
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=third, target="Work")
    assert len(world.imap.uids("Work")) == 1 and len(world.imap.uids("INBOX")) == 5
    call(world.engine, "message.archive", account=world.account, folder="Inbox", message=fourth)
    assert len(world.imap.uids("Archive")) == 1 and len(world.imap.uids("INBOX")) == 4
    call(world.engine, "message.junk", account=world.account, folder="Inbox", message=fifth)
    assert len(world.imap.uids("Junk")) == 2 and len(world.imap.uids("INBOX")) == 3
    call(world.engine, "message.snooze", account=world.account, folder="Inbox", message=sixth, until="2030-01-01T09:00")
    assert len(world.imap.uids("Snoozed")) == 1 and len(world.imap.uids("INBOX")) == 2
    call(world.engine, "message.wake", account=world.account, message=sixth)  # Cancel snooze
    assert world.imap.uids("Snoozed") == [] and len(world.imap.uids("INBOX")) == 3
    in_step(world, ("Inbox", "INBOX"), ("Junk", "Junk"), ("Work", "Work"), ("Archive", "Archive"), ("Snoozed", "Snoozed"))
    assert world.imap.flags_of("INBOX", 1) == {"\\Seen", "\\Flagged"}


def test_deleting_most_of_a_folder_yourself_is_done_on_the_server_without_a_question(world: World) -> None:
    rows = filled(world, 30)
    for row in rows[:29]:
        call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id, permanent=True)
    assert len(world.imap.uids("INBOX")) == 1  # at once
    for row in world.rows("Inbox"):
        call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id)  # to Trash
    assert world.imap.uids("INBOX") == [] and len(world.imap.uids("Trash")) == 1
    call(world.engine, "folder.empty", account=world.account, folder="Trash")
    assert world.imap.uids("Trash") == []
    in_step(world, ("Inbox", "INBOX"), ("Trash", "Trash"))


def test_drafts_and_the_sent_copy_are_on_the_server_at_once(world: World) -> None:
    first = call(world.engine, "draft.save", account=world.account, subject="v1", body="one")
    assert len(world.imap.uids("Drafts")) == 1
    call(world.engine, "draft.save", account=world.account, subject="v2", body="two", replace_message=first.message_id)
    (uid,) = world.imap.uids("Drafts")  # the older version is gone there too
    assert b"Subject: v2" in world.imap.raw_of("Drafts", uid)
    call(world.engine, "mail.send", account=world.account, to="you@example.com", subject="Outgoing", body="hello")
    assert len(world.imap.uids("Sent")) == 1
    in_step(world, ("Drafts", "Drafts"), ("Sent", "Sent"))


def test_a_refusal_of_the_server_changes_nothing_here(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = filled(world, 2)

    def refuse(self: ImapClient, uids: str, flags: object, mode: str = "set") -> None:
        raise ImapRefused("NO not allowed")

    monkeypatch.setattr(ImapClient, "uid_store", refuse)
    with pytest.raises(ImapRefused):
        call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[0].message_id, flagged=True)
    assert "flagged" not in next(row for row in world.rows("Inbox") if row.message_id == rows[0].message_id).flags
    world.imap.refuse_appends = True
    with pytest.raises(ProtocolError):
        call(world.engine, "draft.save", account=world.account, subject="kept in the window", body="text")
    assert world.rows("Drafts") == [] and world.imap.uids("Drafts") == []


def test_offline_the_action_is_done_here_and_sent_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = filled(world, 25)
    notices: list[str] = []
    world.engine.notice = notices.append
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    for row in rows[:20]:
        call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id, permanent=True)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[20].message_id, flagged=True)
    assert len(world.rows("Inbox")) == 5 and len(world.imap.uids("INBOX")) == 25  # done here; the server keeps everything meanwhile
    assert notices and "could not be reached" in notices[0]
    monkeypatch.undo()  # the server can be reached again
    result = world.fetch()
    assert result.blocked == () and len(world.imap.uids("INBOX")) == 5  # deleted by the person: never held back
    assert any("\\Flagged" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))
    in_step(world, ("Inbox", "INBOX"))


def test_a_crash_between_the_server_and_here_is_completed_by_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = filled(world, 2)
    call(world.engine, "folder.create", account=world.account, folder="Work")

    def crash(self: Mailbox, ref: object, target: object) -> None:
        raise StorageError("the program stopped here")

    monkeypatch.setattr(Mailbox, "_move_here", crash)
    with pytest.raises(StorageError):
        call(world.engine, "message.move", account=world.account, folder="Inbox", message=rows[0].message_id, target="Work")
    monkeypatch.undo()
    assert len(world.imap.uids("Work")) == 1  # the server did it
    world.fetch()
    assert [row.subject for row in world.rows("Work")] == [rows[0].subject] and len(world.rows("Inbox")) == 1  # once, never lost
    in_step(world, ("Inbox", "INBOX"), ("Work", "Work"))


@pytest.fixture
def old_server(tmp_path: Path) -> Generator[World]:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False, uidplus=False, move=False) as imap, FakeSmtpServer() as smtp:
        result = call(
            engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=imap.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
        )
        yield World(engine, imap, smtp, str(result.account_id))


def test_a_server_without_move_or_single_removal_loses_nothing(old_server: World) -> None:
    world = old_server
    rows = filled(world, 3)
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=rows[0].message_id, target="Junk")
    (junk,) = world.imap.uids("Junk")  # copied first; the original is only marked deleted, the folder is never expunged
    assert b"Message 1" in world.imap.raw_of("Junk", junk) and len(world.imap.uids("INBOX")) == 3
    assert any("\\Deleted" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=rows[1].message_id, permanent=True)
    assert sum("\\Deleted" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX")) == 2
    assert [row.subject for row in world.rows("Inbox")] == [rows[2].subject] and len(world.rows("Junk")) == 1


def test_a_move_never_removes_another_program_s_deleted_mail(old_server: World) -> None:
    world = old_server
    rows = filled(world, 2)
    with world.imap.lock:  # another program marked the second deleted and has not expunged the folder yet
        world.imap.folders["INBOX"].messages[1].flags.add("\\Deleted")
    before = world.imap.uids("INBOX")
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=rows[0].message_id, target="Junk")
    assert world.imap.uids("INBOX") == before and len(world.imap.uids("Junk")) == 1


def test_the_client_refuses_to_remove_more_than_the_messages_named(old_server: World) -> None:
    from chrono_mail.core import ConnectionSecurity, ProtocolError, ServerSettings
    from chrono_mail.protocols.imap import ImapClient

    with ImapClient(ServerSettings("127.0.0.1", old_server.imap.port, ConnectionSecurity.NONE, "user"), "secret") as client:
        client.select("INBOX")
        with pytest.raises(ProtocolError, match="no UIDPLUS"):
            client.uid_expunge("1")


def test_many_actions_at_the_same_moment_all_reach_the_server(world: World) -> None:
    rows = filled(world, 40)
    errors: list[BaseException] = []

    def delete(message_id: str) -> None:
        try:
            call(world.engine, "message.delete", account=world.account, folder="Inbox", message=message_id, permanent=True)
        except BaseException as error:  # noqa: BLE001 - any failure is the finding
            errors.append(error)

    workers = [threading.Thread(target=delete, args=(row.message_id,)) for row in rows[:30]]  # the window runs several at once
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == [] and len(world.imap.uids("INBOX")) == 10 and len(world.rows("Inbox")) == 10
    in_step(world, ("Inbox", "INBOX"))


def test_a_mail_check_while_deleting_loses_nothing_and_holds_nothing_back(world: World) -> None:
    rows = filled(world, 30)
    world.imap.add("INBOX", mail(99))  # new mail for the check
    errors: list[BaseException] = []

    def delete_all() -> None:
        for row in rows[:20]:
            try:
                call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id, permanent=True)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

    deleting = threading.Thread(target=delete_all)
    deleting.start()
    checked = world.fetch()
    deleting.join()
    assert errors == [] and checked.blocked == () and checked.failed == ()
    assert len(world.imap.uids("INBOX")) == 11 and len(world.rows("Inbox")) == 11  # 10 kept, 1 new, 20 deleted on both sides
    in_step(world, ("Inbox", "INBOX"))


def test_a_connection_the_server_closed_is_made_again(world: World) -> None:
    rows = filled(world, 2)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[0].message_id, flagged=True)
    link = world.engine.imap_link(world.account)
    assert link.client is not None
    link.client.close()  # the server dropped the idle connection
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[1].message_id, flagged=True)
    assert all("\\Flagged" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))


def test_a_folder_the_server_renumbered_is_left_to_the_sync_and_nothing_is_lost(world: World) -> None:
    rows = filled(world, 3)
    world.imap.new_uid_validity("INBOX")
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=rows[0].message_id, permanent=True)
    assert len(world.imap.uids("INBOX")) == 3  # the UIDs mean something else now: nothing is touched on the server by number
    world.fetch()  # the sync matches the messages again by their Message-ID
    # Nothing is lost: the delete made while the numbers did not match is not done, and the message is shown again.
    assert sorted(row.subject for row in world.rows("Inbox")) == ["Message 1", "Message 2", "Message 3"] and len(world.imap.uids("INBOX")) == 3


def test_junk_by_hand_uses_a_junk_folder_the_server_does_not_show(world: World) -> None:
    """The Windows computer's W7: the server's Junk was not subscribed, so every move to Junk tried to make it and was refused."""
    world.imap.folders["Junk"] = FakeFolder(uid_validity=3000, subscribed=False)
    rows = filled(world, 2)
    call(world.engine, "message.junk-many", account=world.account, folder="Inbox", messages=[rows[0].message_id])
    assert len(world.imap.uids("Junk")) == 1 and [row.subject for row in world.rows("Junk")] == [rows[0].subject]
    assert not any(command.startswith("CREATE") and "Junk" in command for command in world.imap.commands)


def test_a_message_of_a_folder_only_here_is_deleted_without_the_server(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-183, RV-184: removing a message of the Outbox or Scheduled (they exist only here) waited for the server session, behind a whole
    mail check, with the Outbox lock held; offline it waited for a connection that could not come."""
    from chrono_mail.engine.mailbox import open_mailbox

    with open_mailbox(world.engine, world.account) as mailbox:
        reference = mailbox.deliver(mailbox.folder("Outbox"), mail(1), frozenset())
    world.engine.close_imap_links()
    tried: list[str] = []

    def unreachable(self: ImapClient) -> None:
        tried.append("connect")
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    with open_mailbox(world.engine, world.account) as mailbox:
        assert mailbox.delete(reference, permanent=True) == "deleted"
    assert tried == [] and world.rows("Outbox") == []


def test_after_a_folder_delete_an_action_in_inbox_works(world: World) -> None:
    """RV-170: deleting a folder left Inbox open read-only; the next flag, move or delete in Inbox was refused until the next check."""
    rows = filled(world, 2)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[0].message_id, flagged=True)  # Inbox open now
    call(world.engine, "folder.create", account=world.account, folder="Old")
    call(world.engine, "folder.delete", account=world.account, folder="Old", with_messages=True)
    call(world.engine, "message.flag", account=world.account, folder="Inbox", message=rows[1].message_id, flagged=True)
    assert all("\\Flagged" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX"))


def test_a_folder_tree_is_looked_at_whole_before_any_of_it_is_deleted(world: World) -> None:
    """RV-176: the subfolders were deleted first; then the parent was refused for its new mail, and the tree was half gone."""
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    call(world.engine, "folder.create", account=world.account, folder="Projects/Done")
    world.imap.add("Projects", mail(1))  # new mail on the server, not here yet
    with pytest.raises(ChronoMailError, match="check for mail first"):
        call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, include_subfolders=True)
    assert "Projects/Done" in world.imap.folders and "Projects" in world.imap.folders


def test_a_folder_with_mail_too_large_to_download_says_so(world: World) -> None:
    """RV-172: a message over 256 MB is never downloaded, so "check for mail first" could never help; the folder could never be deleted."""
    from chrono_mail.engine import imap_online

    call(world.engine, "folder.create", account=world.account, folder="Huge")
    world.imap.add("Huge", mail(1))
    world.fetch()
    world.imap.add("Huge", mail(2))
    real = imap_online.TOO_LARGE_TO_DOWNLOAD
    try:
        imap_online.TOO_LARGE_TO_DOWNLOAD = 10  # every message counts as too large
        with pytest.raises(ChronoMailError, match="too large for Chrono Mail to download"):
            call(world.engine, "folder.delete", account=world.account, folder="Huge", with_messages=True)
    finally:
        imap_online.TOO_LARGE_TO_DOWNLOAD = real
    assert "Huge" in world.imap.folders


def test_a_folder_tree_deleted_offline_is_deleted_on_the_server_whole_or_not_at_all(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-176: deleted here offline, the subfolders went on the server at the next check and the parent, with new mail
    meanwhile, became a sync conflict: the tree was half gone."""
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    call(world.engine, "folder.create", account=world.account, folder="Projects/Done")
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, include_subfolders=True)
    monkeypatch.undo()
    world.imap.add("Projects", mail(1))  # new mail on the server meanwhile
    world.fetch()
    assert "Projects" in world.imap.folders and "Projects/Done" in world.imap.folders


def test_many_messages_of_the_outbox_are_deleted_without_the_server(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-183: deleting several messages of the Outbox at once still waited for the server session and connected to it."""
    from chrono_mail.engine.mailbox import open_mailbox

    with open_mailbox(world.engine, world.account) as mailbox:
        ids = [mailbox.deliver(mailbox.folder("Outbox"), mail(number), frozenset()).store_id for number in (1, 2, 3)]
    world.engine.close_imap_links()
    tried: list[str] = []

    def unreachable(self: ImapClient) -> None:
        tried.append("connect")
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    done = call(world.engine, "message.delete-many", account=world.account, folder="Outbox", messages=tuple(ids), permanent=True)
    assert tried == [] and len(done.done) == 3 and world.rows("Outbox") == []


def test_a_folder_tree_deleted_offline_waits_while_part_of_it_is_in_conflict(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second review of RV-176: at the second check the subfolder with new mail was held by its conflict and left out of the look, so
    its parent was deleted on the server while that conflict was still open."""
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    call(world.engine, "folder.create", account=world.account, folder="Projects/Done")
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, include_subfolders=True)
    monkeypatch.undo()
    world.imap.add("Projects/Done", mail(1))  # new mail in the subfolder on the server meanwhile
    world.fetch()
    world.fetch()  # the second check: the subfolder is held by its conflict now
    assert "Projects" in world.imap.folders and "Projects/Done" in world.imap.folders

