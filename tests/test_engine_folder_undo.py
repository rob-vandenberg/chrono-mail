from collections.abc import Generator
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import load_rules
from chrono_mail.core import ProtocolError
from chrono_mail.engine.folderundo import RENAMED_SINCE, TAKEN_NOW
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, mail, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_online import in_step

# --- Version ------------------------------------------------------------
__version__ = "test_engine_folder_undo.py 4.7.657"

# --- Version history ----------------------------------------------------
# v4.7.657: Initial (B-18 step 2, D-369): a folder renamed or deleted is undone and redone, on the server first; with its mail, marks,
#           tags and rules; changed since, erased, taken; mail and folder actions in one list; offline and refused


def rows(setup: Setup, folder: str) -> list[Any]:
    return list(call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages)


def folders(engine: Any, account: str) -> set[str]:  # noqa: ANN401 - the engine
    return {info.path for info in call(engine, "folder.list", account=account)}


@pytest.fixture
def filled(setup: Setup) -> Generator[Setup]:
    """Projects with Projects/Done, a message in each; one flagged and tagged."""
    call(setup.engine, "mail.fetch", account=setup.account_id)
    for folder in ("Projects", "Projects/Done"):
        call(setup.engine, "folder.create", account=setup.account_id, folder=folder)
    inbox = sorted(rows(setup, "Inbox"), key=lambda row: row.subject)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, target="Projects")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[1].message_id, target="Projects/Done")
    call(setup.engine, "message.flag", account=setup.account_id, folder="Projects", message=inbox[0].message_id, flagged=True)
    call(setup.engine, "message.tag", account=setup.account_id, folder="Projects", messages=(inbox[0].message_id,), add=("Work",))
    yield setup


def test_a_rename_is_undone_and_redone_with_the_folders_inside_it(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.rename", account=account, folder="Projects", new_name="Work", undo=1)
    assert call(engine, "mail.undo-labels").undo == "Rename folder “Projects”"
    undone = call(engine, "mail.undo")
    assert undone.action == "Rename folder “Projects”" and undone.not_done == 0
    assert (undone.account, undone.folder) == (account, "Projects")
    assert {"Projects", "Projects/Done"} <= folders(engine, account) and "Work" not in folders(engine, account)
    assert len(rows(filled, "Projects/Done")) == 1
    redone = call(engine, "mail.redo")
    assert redone.folder == "Work" and {"Work", "Work/Done"} <= folders(engine, account) and "Projects" not in folders(engine, account)


def test_a_deleted_folder_comes_back_with_its_folders_mail_marks_and_tags(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    before = {row.message_id: row for folder in ("Projects", "Projects/Done") for row in rows(filled, folder)}
    call(engine, "folder.delete", account=account, folder="Projects", with_messages=True, include_subfolders=True, undo=1)
    assert "Projects" not in folders(engine, account)
    assert call(engine, "mail.undo-labels").undo == "Delete folder “Projects”"
    undone = call(engine, "mail.undo")
    assert undone.not_done == 0 and (undone.account, undone.folder) == (account, "Projects")
    after = {row.message_id: row for folder in ("Projects", "Projects/Done") for row in rows(filled, folder)}
    assert after.keys() == before.keys()  # the same messages, so an older Undo on them still works
    assert all((after[key].folder, after[key].flags, after[key].tags) == (before[key].folder, before[key].flags, before[key].tags) for key in before)
    assert any("Work" in row.tags and "flagged" in row.flags for row in after.values())
    assert call(engine, "message.search", account=account, query="Message")  # in the index again
    redone = call(engine, "mail.redo")
    assert redone.not_done == 0 and redone.folder == "" and "Projects" not in folders(engine, account)
    assert call(engine, "mail.undo").not_done == 0 and {"Projects", "Projects/Done"} <= folders(engine, account)  # kept again by the Redo


def test_a_folder_renamed_since_is_left_alone_and_the_reason_is_said(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.rename", account=account, folder="Projects", new_name="Work", undo=1)
    call(engine, "folder.rename", account=account, folder="Work", new_name="Other")  # not by an action of Undo's list
    undone = call(engine, "mail.undo")
    assert undone.not_done == 1 and undone.reason == RENAMED_SINCE and "Other" in folders(engine, account)


def test_a_deleted_folder_whose_name_is_taken_now_is_not_brought_back(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.delete", account=account, folder="Projects", with_messages=True, include_subfolders=True, undo=1)
    call(engine, "folder.create", account=account, folder="Projects")
    undone = call(engine, "mail.undo")
    assert undone.not_done == 2 and undone.reason == TAKEN_NOW and rows(filled, "Projects") == []


def test_a_deleted_folder_erased_since_says_so(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.delete", account=account, folder="Projects", with_messages=True, include_subfolders=True, undo=1)
    with open_mailbox(engine, account) as mailbox:
        mailbox.store.purge_deleted_folders()
    undone = call(engine, "mail.undo")
    assert undone.not_done == 2 and "erased" in undone.reason and "Projects" not in folders(engine, account)


def test_the_rules_a_delete_switched_off_are_on_again(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "rule.add", name="Projects", condition=["subject:contains:x"], action=["move_to:Projects/Done"], account=[account])
    call(engine, "rule.add", name="Off already", condition=["subject:contains:y"], action=["move_to:Projects"], account=[account], disabled=True)
    call(engine, "folder.delete", account=account, folder="Projects", with_messages=True, include_subfolders=True, undo=1)
    assert not any(rule.enabled for rule in load_rules(engine.directories.rules_file).rules)
    undone = call(engine, "mail.undo")
    on = {rule.name: rule.enabled for rule in load_rules(engine.directories.rules_file).rules}
    assert on == {"Projects": True, "Off already": False} and "Rule “Projects” is on again" in undone.rules
    call(engine, "mail.redo")
    assert not any(rule.enabled for rule in load_rules(engine.directories.rules_file).rules)


def test_mail_and_folder_actions_are_undone_in_their_order(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    message = rows(filled, "Inbox")[0].message_id
    call(engine, "message.move-many", account=account, folder="Inbox", messages=(message,), target="Projects", undo=1)
    call(engine, "folder.rename", account=account, folder="Projects", new_name="Work", undo=2)
    assert call(engine, "mail.undo").folder == "Projects"
    back = call(engine, "mail.undo")
    assert back.messages == (message,) and (back.account, back.folder) == ("", "")  # an action on mail opens no folder
    assert [row.message_id for row in rows(filled, "Inbox")] == [message]


def test_a_folder_action_without_a_number_is_not_kept(filled: Setup) -> None:
    call(filled.engine, "folder.rename", account=filled.account_id, folder="Projects", new_name="Work")
    call(filled.engine, "folder.create", account=filled.account_id, folder="New one")
    assert call(filled.engine, "mail.undo-labels").undo == ""


# --- an IMAP account: on the server first ----------------------------------------------------------------------------------------------------------


def imap_folder(world: World) -> None:
    """Projects on the server with two messages, here too."""
    call(world.engine, "folder.create", account=world.account, folder="Projects")
    world.imap.add("Projects", mail(1), {"\\Seen"})
    world.imap.add("Projects", mail(2))
    world.fetch()


def offline(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.engine.close_imap_links()

    def unreachable(self: ImapClient) -> None:
        raise ProtocolError("Connection failed: no route")

    monkeypatch.setattr(ImapClient, "connect", unreachable)


def test_an_imap_rename_is_undone_on_the_server(world: World) -> None:
    imap_folder(world)
    call(world.engine, "folder.rename", account=world.account, folder="Projects", new_name="Work", undo=1)
    assert "Work" in world.imap.folders
    call(world.engine, "mail.undo")
    assert "Projects" in world.imap.folders and "Work" not in world.imap.folders
    in_step(world, ("Projects", "Projects"))


def test_an_imap_folder_deleted_comes_back_on_the_server_with_its_mail(world: World) -> None:
    imap_folder(world)
    kept = {row.message_id for row in world.rows("Projects")}
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, undo=1)
    assert "Projects" not in world.imap.folders
    undone = call(world.engine, "mail.undo")
    assert undone.not_done == 0 and "Projects" in world.imap.folders  # the server first
    assert {row.message_id for row in world.rows("Projects")} == kept
    world.fetch()  # its mail goes up
    assert len(world.imap.uids("Projects")) == 2 and sum("\\Seen" in world.imap.flags_of("Projects", uid) for uid in world.imap.uids("Projects")) == 1
    in_step(world, ("Projects", "Projects"))
    assert {row.message_id for row in world.rows("Projects")} == kept


def test_an_imap_folder_deleted_and_brought_back_offline_stays_on_the_server_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    imap_folder(world)
    offline(world, monkeypatch)
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, undo=1)
    assert call(world.engine, "mail.undo").not_done == 0 and len(world.rows("Projects")) == 2
    monkeypatch.undo()
    world.fetch()
    assert "Projects" in world.imap.folders and len(world.imap.uids("Projects")) == 2  # never deleted there, nothing sent twice
    in_step(world, ("Projects", "Projects"))


def test_an_imap_folder_brought_back_offline_is_made_on_the_server_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    imap_folder(world)
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, undo=1)
    offline(world, monkeypatch)
    assert call(world.engine, "mail.undo").not_done == 0 and len(world.rows("Projects")) == 2
    monkeypatch.undo()
    world.fetch()
    assert len(world.imap.uids("Projects")) == 2
    in_step(world, ("Projects", "Projects"))


def test_a_refusal_of_the_server_changes_nothing_here(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    imap_folder(world)
    call(world.engine, "folder.delete", account=world.account, folder="Projects", with_messages=True, undo=1)

    def refused(self: ImapClient, folder: str) -> None:
        raise ImapRefused("NO [CANNOT] not allowed")

    monkeypatch.setattr(ImapClient, "create_folder", refused)
    undone = call(world.engine, "mail.undo")
    assert undone.not_done == 1 and "not allowed" in undone.reason and "Projects" not in folders(world.engine, world.account)
    monkeypatch.undo()
    assert call(world.engine, "mail.undo-labels").undo == ""  # a refused Undo leaves the list, as in Mail
