import pytest

from chrono_mail.core import ChronoMailError, ProtocolError
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from chrono_mail.sync import ImapState
from tests.test_engine_commands import call
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_online import filled, in_step

__all__ = ["world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_imap_folder_actions.py 0.5.123"

# --- Version history ----------------------------------------------------
# v0.5.123: Initial tests: folders of an IMAP account are made, renamed, deleted, emptied and marked read on the server first (D-118); a folder left out is ignored (D-120)


def state(world: World) -> ImapState:
    return ImapState(world.engine.directories.imap_state_file(world.account), durable=False)


def local_folders(world: World) -> set[str]:
    return {item.path for item in call(world.engine, "folder.list", account=world.account)}


def offline(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)


def two_folders_with_mail(world: World) -> None:
    """Work and Work/Sub, one message in each, known to the sync."""
    rows = filled(world, 2)
    call(world.engine, "folder.create", account=world.account, folder="Work")
    call(world.engine, "folder.create", account=world.account, folder="Work/Sub")
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=rows[0].message_id, target="Work")
    call(world.engine, "message.move", account=world.account, folder="Inbox", message=rows[1].message_id, target="Work/Sub")
    in_step(world, ("Work", "Work"), ("Work/Sub", "Work/Sub"))


def test_a_new_folder_is_on_the_server_at_once(world: World) -> None:
    call(world.engine, "folder.create", account=world.account, folder="Taxes")
    assert "Taxes" in world.imap.folders and world.imap.folders["Taxes"].subscribed
    assert "Taxes" in local_folders(world)


def test_a_rename_is_done_on_the_server_with_the_folders_below_and_nothing_is_uploaded_or_hidden(world: World) -> None:
    two_folders_with_mail(world)
    call(world.engine, "folder.rename", account=world.account, folder="Work", new_name="Projects")
    assert "Work" not in world.imap.folders and "Work/Sub" not in world.imap.folders  # at once, before any check
    assert len(world.imap.uids("Projects")) == 1 and len(world.imap.uids("Projects/Sub")) == 1
    assert {"Projects", "Projects/Sub"} <= local_folders(world) and "Work" not in local_folders(world)
    in_step(world, ("Projects", "Projects"), ("Projects/Sub", "Projects/Sub"))
    assert len(world.imap.uids("Projects")) == 1 and len(world.imap.uids("Projects/Sub")) == 1  # nothing uploaded twice
    known = state(world)
    assert known.hidden == set() and ("Work",) not in known.validity and len(known.folder_entries(("Projects", "Sub"))) == 1
    assert "Work" not in world.imap.folders  # the next check did not bring the old name back
    assert world.imap.stale_subscriptions == set()  # the old names are unsubscribed
    assert world.imap.folders["Projects"].subscribed and world.imap.folders["Projects/Sub"].subscribed


def test_a_deleted_folder_is_deleted_on_the_server_and_does_not_come_back(world: World) -> None:
    two_folders_with_mail(world)
    call(world.engine, "folder.delete", account=world.account, folder="Work", with_messages=True, include_subfolders=True)
    assert "Work" not in world.imap.folders and "Work/Sub" not in world.imap.folders
    assert not {"Work", "Work/Sub"} & local_folders(world)
    world.fetch()
    assert not {"Work", "Work/Sub"} & local_folders(world) and "Work" not in world.imap.folders
    known = state(world)
    assert known.hidden == set() and ("Work",) not in known.validity and ("Work", "Sub") not in known.validity


def test_offline_a_rename_or_delete_is_refused_and_a_new_folder_follows_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    two_folders_with_mail(world)
    offline(world, monkeypatch)
    with pytest.raises(ChronoMailError, match="cannot be reached"):
        call(world.engine, "folder.rename", account=world.account, folder="Work", new_name="Projects")
    with pytest.raises(ChronoMailError, match="cannot be reached"):
        call(world.engine, "folder.delete", account=world.account, folder="Work", with_messages=True, include_subfolders=True)
    assert {"Work", "Work/Sub"} <= local_folders(world) and "Work" in world.imap.folders  # nothing changed anywhere
    call(world.engine, "folder.create", account=world.account, folder="Later")
    assert "Later" in local_folders(world) and "Later" not in world.imap.folders
    monkeypatch.undo()
    world.fetch()
    assert "Later" in world.imap.folders


def test_a_refusal_of_the_server_changes_nothing_here(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    two_folders_with_mail(world)

    def refuse(self: ImapClient, *names: str) -> None:
        raise ImapRefused("NO not allowed")

    monkeypatch.setattr(ImapClient, "rename_folder", refuse)
    monkeypatch.setattr(ImapClient, "delete_folder", refuse)
    monkeypatch.setattr(ImapClient, "create_folder", refuse)
    with pytest.raises(ImapRefused):
        call(world.engine, "folder.rename", account=world.account, folder="Work", new_name="Projects")
    with pytest.raises(ImapRefused):
        call(world.engine, "folder.delete", account=world.account, folder="Work", with_messages=True, include_subfolders=True)
    with pytest.raises(ImapRefused):
        call(world.engine, "folder.create", account=world.account, folder="Nope")
    assert {"Work", "Work/Sub"} <= local_folders(world) and "Nope" not in local_folders(world)
    monkeypatch.undo()
    in_step(world, ("Work", "Work"), ("Work/Sub", "Work/Sub"))


def test_a_standard_folder_cannot_be_renamed(world: World) -> None:
    with pytest.raises(ChronoMailError, match="standard folders"):
        call(world.engine, "folder.rename", account=world.account, folder="Junk", new_name="Spam")
    assert "Junk" in world.imap.folders and "Spam" not in world.imap.folders


def test_emptying_trash_empties_the_server_folder_also_mail_not_downloaded_yet(world: World) -> None:
    rows = filled(world, 3)
    for row in rows:
        call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id)
    world.imap.add("Trash", mail(9))  # arrived on the server after the last check
    result = call(world.engine, "folder.empty", account=world.account, folder="Trash")
    assert result.removed == 3 and world.imap.uids("Trash") == [] and world.rows("Trash") == []
    in_step(world, ("Trash", "Trash"))


def test_mark_all_as_read_marks_the_server_folder_also_mail_not_downloaded_yet(world: World) -> None:
    filled(world, 3)
    late = world.imap.add("INBOX", mail(9))
    result = call(world.engine, "folder.mark-read", account=world.account, folder="Inbox")
    assert result.changed == 3
    assert all("\\Seen" in world.imap.flags_of("INBOX", uid) for uid in world.imap.uids("INBOX")) and late in world.imap.uids("INBOX")
    world.fetch()
    assert all("seen" in row.flags for row in world.rows("Inbox"))
    in_step(world, ("Inbox", "INBOX"))


def test_a_message_gone_from_the_server_is_not_moved_but_can_be_deleted(world: World) -> None:
    rows = filled(world, 2)
    first, second = (row.message_id for row in rows)
    for uid in world.imap.uids("INBOX"):
        world.imap.remove("INBOX", uid)  # deleted by another program
    for action, extra in (("message.move", {"target": "Junk"}), ("message.copy", {"target": "Junk"}), ("message.flag", {"flagged": True})):
        with pytest.raises(ImapRefused, match="no longer on the server"):
            call(world.engine, action, account=world.account, folder="Inbox", message=first, **extra)
    assert world.imap.uids("Junk") == [] and len(world.rows("Inbox")) == 2  # nothing done here either
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=first)  # to Trash: it is gone already
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=second, permanent=True)
    assert world.rows("Inbox") == [] and world.rows("Trash") == [] and world.imap.uids("Trash") == []
    in_step(world, ("Inbox", "INBOX"), ("Trash", "Trash"))


def test_a_folder_left_out_is_ignored_here_and_stays_on_the_server(world: World) -> None:
    two_folders_with_mail(world)
    call(world.engine, "account.set-folders", account=world.account, hide=("Work",))
    assert "Work" not in local_folders(world) and "Work/Sub" in local_folders(world)  # only the folder left out
    assert len(world.imap.uids("Work")) == 1  # nothing changed on the server
    world.fetch()
    assert "Work" not in local_folders(world) and len(world.imap.uids("Work")) == 1
    assert "Work" in state(world).hidden
    call(world.engine, "account.set-folders", account=world.account, show=("Work",))
    world.fetch()
    assert len(world.rows("Work")) == 1 and len(world.imap.uids("Work")) == 1  # back, downloaded once, nothing uploaded
    in_step(world, ("Work", "Work"), ("Work/Sub", "Work/Sub"))


def test_leaving_out_is_refused_offline_for_standard_folders_and_with_mail_not_on_the_server(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    two_folders_with_mail(world)
    with pytest.raises(ChronoMailError, match="standard folders"):
        call(world.engine, "account.set-folders", account=world.account, hide=("Junk",))
    offline(world, monkeypatch)
    with pytest.raises(ChronoMailError, match="cannot be reached"):
        call(world.engine, "account.set-folders", account=world.account, hide=("Work",))
    (row,) = world.rows("Work/Sub")
    call(world.engine, "message.move", account=world.account, folder="Work/Sub", message=row.message_id, target="Work")  # here only, while offline
    monkeypatch.undo()
    world.engine.close_imap_links()
    with pytest.raises(ChronoMailError, match="check mail first"):
        call(world.engine, "account.set-folders", account=world.account, hide=("Work",))
    assert len(world.rows("Work")) == 2 and "Work" not in state(world).hidden  # nothing changed
