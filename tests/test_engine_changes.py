from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, ProtocolError
from chrono_mail.engine.changes import Change
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_import_emclient import make_exports

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_changes.py 0.5.156"

# --- Version history ----------------------------------------------------
# v0.5.156: the three bulk commands tell one fact; an action with an outdated folder is refused and never hides the message
# v0.5.155: Initial tests: every change of mail reports exactly its fact, right after it happened (D-146)


def listen(holder: Setup | World) -> list[Change]:
    told: list[Change] = []
    holder.engine.changed = told.append
    return told


def inbox(setup: Setup) -> list[Any]:
    return list(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages)


def kinds(told: list[Change]) -> list[str]:
    return [change.kind for change in told]


def test_a_mail_check_is_one_fact_for_its_mailbox_not_one_per_message(setup: Setup) -> None:
    told = listen(setup)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert told == [Change("mailbox_changed", setup.account_id)]
    told.clear()
    call(setup.engine, "mail.fetch", account=setup.account_id)
    assert told == []  # nothing new: nothing to tell, so no view fetches again


def test_marks_tell_the_message_its_marks_and_unread_before_and_after(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    first = inbox(setup)[0]
    told = listen(setup)
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=first.message_id, seen=True)
    (change,) = told
    assert (change.kind, change.mailbox, change.folder, change.message_id) == ("flags", setup.account_id, "Inbox", first.message_id)
    assert (change.was_unread, change.unread, change.flags) == (True, False, ("seen",))


def test_four_quick_deletes_are_four_facts_in_order_each_about_its_own_message(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    rows = inbox(setup)
    told = listen(setup)
    for row in rows:
        call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=row.message_id)
    assert kinds(told) == ["moved"] * len(rows)
    assert [change.message_id for change in told] == [row.message_id for row in rows]
    for change, row in zip(told, rows, strict=True):
        assert (change.folder, change.to_folder, change.was_unread, change.unread) == ("Inbox", "Trash", True, True)
        assert change.row is not None and change.row.folder == "Trash" and change.row.subject == row.subject and change.new_message_id == change.row.message_id
    told.clear()
    call(setup.engine, "message.delete", account=setup.account_id, folder="Trash", message=told_id(rows[0]))
    assert [(change.kind, change.folder, change.message_id, change.was_unread) for change in told] == [("removed", "Trash", rows[0].message_id, True)]


def told_id(row: Any) -> str:  # noqa: ANN401 - a message row
    return str(row.message_id)  # a move keeps the store id


def test_copy_and_tags_tell_what_a_list_shows(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    first = inbox(setup)[0]
    call(setup.engine, "folder.create", account=setup.account_id, folder="Kept")
    told = listen(setup)
    copied = call(setup.engine, "message.copy", account=setup.account_id, folder="Inbox", message=first.message_id, target="Kept")
    (added,) = told
    assert added.kind == "added" and added.folder == "Kept" and added.row is not None and added.row.subject == first.subject and added.message_id == copied.message_id
    told.clear()
    call(setup.engine, "message.tag", account=setup.account_id, folder="Inbox", messages=(first.message_id,), add=("Work",))
    assert [(change.kind, change.message_id, change.tags) for change in told] == [("tags", first.message_id, ("Work",))]


def test_folder_actions_are_one_fact_each(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    told = listen(setup)
    call(setup.engine, "folder.mark-read", account=setup.account_id, folder="Inbox")
    assert [(change.kind, change.folder, change.count) for change in told] == [("folder_read", "Inbox", 3)]
    told.clear()
    for row in inbox(setup):
        call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=row.message_id)
    told.clear()
    call(setup.engine, "folder.empty", account=setup.account_id, folder="Trash")
    assert [(change.kind, change.folder, change.count) for change in told] == [("folder_emptied", "Trash", 3)]
    told.clear()
    call(setup.engine, "folder.create", account=setup.account_id, folder="Projects")
    call(setup.engine, "folder.rename", account=setup.account_id, folder="Projects", new_name="Work")
    call(setup.engine, "folder.delete", account=setup.account_id, folder="Work")
    assert [(change.kind, change.folder, change.to_folder) for change in told] == [("folder_made", "Projects", ""), ("folder_renamed", "Projects", "Work"), ("folder_deleted", "Work", "")]


def test_a_rebuilt_index_is_one_fact(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    told = listen(setup)
    call(setup.engine, "index.rebuild", account=setup.account_id)
    assert told == [Change("mailbox_changed", setup.account_id)]


def test_an_import_is_one_fact_per_batch_not_one_per_message(setup: Setup, tmp_path: Path) -> None:
    (tmp_path / "exports").mkdir()
    exports = make_exports(tmp_path / "exports")
    told = listen(setup)
    call(setup.engine, "import.emclient", settings_file=None, mail_folder=str(exports.mail), backup_file=None, accounts=False, rules=False, contacts=False)
    assert told == [Change("mailbox_changed", LOCAL_ACCOUNT_ID)]


def test_imap_tells_only_after_the_server_confirmed_and_nothing_on_a_refusal(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.imap.add("INBOX", mail(1))
    world.fetch()
    (row,) = world.rows("Inbox")
    told = listen(world)
    on_server_when_told: list[tuple[int, int]] = []

    def heard(change: Change) -> None:
        told.append(change)
        on_server_when_told.append((len(world.imap.uids("INBOX")), len(world.imap.uids("Trash"))))

    world.engine.changed = heard
    call(world.engine, "message.delete", account=world.account, folder="Inbox", message=row.message_id)
    assert kinds(told) == ["moved"] and on_server_when_told == [(0, 1)]  # the server had it in Trash before the window was told
    told.clear()

    def refuse(self: ImapClient, uids: str, flags: object, mode: str = "set") -> None:
        raise ImapRefused("NO not allowed")

    monkeypatch.setattr(ImapClient, "uid_store", refuse)
    with pytest.raises(ImapRefused):
        call(world.engine, "message.flag", account=world.account, folder="Trash", message=world.rows("Trash")[0].message_id, flagged=True)
    world.imap.refuse_appends = True
    with pytest.raises(ProtocolError):
        call(world.engine, "draft.save", account=world.account, subject="refused", body="text")
    assert told == []


def test_the_three_bulk_commands_tell_one_fact_at_their_end(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    rows = inbox(setup)
    call(setup.engine, "message.tag", account=setup.account_id, folder="Inbox", messages=tuple(row.message_id for row in rows), add=("Work",))
    told = listen(setup)
    call(setup.engine, "tag.rename", name="Work", new_name="Office")
    assert told == [Change("mailbox_changed", setup.account_id)]
    told.clear()
    call(setup.engine, "tag.delete", name="Office")
    assert told == [Change("mailbox_changed", setup.account_id)]
    told.clear()
    call(setup.engine, "rule.add", name="Read them", condition=("subject:contains:Message",), action=("mark_read",))
    call(setup.engine, "rule.run", account=setup.account_id, folder="Inbox")
    assert told == [Change("mailbox_changed", setup.account_id)]


def test_an_action_with_an_outdated_folder_is_refused_and_never_hides_the_message(setup: Setup) -> None:
    from chrono_mail.engine import UsageError

    call(setup.engine, "mail.fetch", account=setup.account_id)
    first = inbox(setup)[0]
    call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=first.message_id)
    for again in ({"permanent": False}, {"permanent": True}):
        with pytest.raises(UsageError, match="no longer in Inbox"):
            call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=first.message_id, **again)
    with pytest.raises(UsageError, match="no longer in Inbox"):
        call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=first.message_id, seen=True)
    trash = call(setup.engine, "message.list", account=setup.account_id, folder="Trash").messages
    assert [row.message_id for row in trash] == [first.message_id]  # still there, still in the index
