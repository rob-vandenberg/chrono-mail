from collections.abc import Generator

import pytest

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import MOVING_FOLDER, RENAMING_FOLDER
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_folder_undo import folders, rows
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture
from tests.test_engine_imap_folder_actions import local_folders, offline, restarted, two_folders_with_mail
from tests.test_engine_imap_online import in_step
from tests.test_engine_rule_folders import add_rule, by_name, listen, moves_to

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_folder_move.py 4.8.661"

# --- Version history ----------------------------------------------------
# v4.8.661: Initial (B-38, D-372): a folder moved into another folder or to the top, with its folders and mail; "Moving folder…" while it
#           runs and "Move folder" for Undo; what is refused; an IMAP folder moved on the server first, offline at the next check


@pytest.fixture
def filled(setup: Setup) -> Generator[Setup]:
    """Projects with Projects/Done and Archive, a message in Projects and in Projects/Done."""
    call(setup.engine, "mail.fetch", account=setup.account_id)
    for folder in ("Projects", "Projects/Done", "Archive"):
        call(setup.engine, "folder.create", account=setup.account_id, folder=folder)
    inbox = sorted(rows(setup, "Inbox"), key=lambda row: row.subject)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, target="Projects")
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[1].message_id, target="Projects/Done")
    yield setup


def test_a_folder_moves_into_another_with_its_folders_mail_and_rules(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    add_rule(engine, "Done rule", "Projects/Done")
    told = listen(engine)
    result = call(engine, "folder.rename", account=account, folder="Projects", new_name="Archive/Projects", undo=1)
    assert result.folder == "Archive/Projects"
    assert {"Archive/Projects", "Archive/Projects/Done"} <= folders(engine, account) and "Projects" not in folders(engine, account)
    assert len(rows(filled, "Archive/Projects")) == 1 and len(rows(filled, "Archive/Projects/Done")) == 1
    assert [change.notes for change in told if change.kind == "working"][0] == (MOVING_FOLDER,)
    assert moves_to(by_name(engine)["Done rule"]) == "Archive/Projects/Done"  # the rules follow the folder (D-302)
    assert call(engine, "mail.undo-labels").undo == "Move folder “Projects”"


def test_a_moved_folder_is_moved_back_by_undo_and_again_by_redo(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.rename", account=account, folder="Projects/Done", new_name="Archive/Done", undo=1)
    undone = call(engine, "mail.undo")
    assert undone.action == "Move folder “Done”" and undone.not_done == 0
    assert "Projects/Done" in folders(engine, account) and "Archive/Done" not in folders(engine, account)
    assert len(rows(filled, "Projects/Done")) == 1
    call(engine, "mail.redo")
    assert "Archive/Done" in folders(engine, account) and "Projects/Done" not in folders(engine, account)


def test_a_folder_moves_to_the_top(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    call(engine, "folder.rename", account=account, folder="Projects/Done", new_name="Done", undo=1)
    assert "Done" in folders(engine, account) and "Projects/Done" not in folders(engine, account) and len(rows(filled, "Done")) == 1
    assert call(engine, "mail.undo-labels").undo == "Move folder “Done”"


def test_a_rename_in_place_is_still_called_a_rename(filled: Setup) -> None:
    engine, account = filled.engine, filled.account_id
    told = listen(engine)
    call(engine, "folder.rename", account=account, folder="Projects/Done", new_name="Projects/Finished", undo=1)
    assert [change.notes for change in told if change.kind == "working"][0] == (RENAMING_FOLDER,)
    assert call(engine, "mail.undo-labels").undo == "Rename folder “Done”"


@pytest.mark.parametrize(("folder", "target"), [
    ("Projects", "Projects/Done/Projects"),  # into a folder inside itself
    ("Archive", "Projects/Done"),  # where a folder of that name is: never merged into it
], ids=["into itself", "name taken there"])
def test_a_move_that_cannot_be_done_is_refused_and_changes_nothing(filled: Setup, folder: str, target: str) -> None:
    engine, account = filled.engine, filled.account_id
    before = folders(engine, account)
    with pytest.raises(ChronoMailError):
        call(engine, "folder.rename", account=account, folder=folder, new_name=target, undo=1)
    assert folders(engine, account) == before and call(engine, "mail.undo-labels").undo == ""


def test_an_imap_folder_moves_on_the_server_first(world: World) -> None:
    two_folders_with_mail(world)
    call(world.engine, "folder.create", account=world.account, folder="Archive")
    call(world.engine, "folder.rename", account=world.account, folder="Work", new_name="Archive/Work", undo=1)
    assert "Work" not in world.imap.folders and len(world.imap.uids("Archive/Work")) == 1 and len(world.imap.uids("Archive/Work/Sub")) == 1
    assert {"Archive/Work", "Archive/Work/Sub"} <= local_folders(world) and "Work" not in local_folders(world)
    in_step(world, ("Archive/Work", "Archive/Work"), ("Archive/Work/Sub", "Archive/Work/Sub"))
    call(world.engine, "mail.undo")
    assert "Work" in world.imap.folders and "Archive/Work" not in world.imap.folders  # moved back on the server too


def test_an_imap_standard_folder_is_not_moved(world: World) -> None:
    call(world.engine, "folder.create", account=world.account, folder="Archive")
    with pytest.raises(ChronoMailError):
        call(world.engine, "folder.rename", account=world.account, folder="Sent", new_name="Archive/Sent")
    assert "Sent" in world.imap.folders


def test_offline_an_imap_move_is_done_here_at_once_and_on_the_server_at_the_next_check(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    two_folders_with_mail(world)
    call(world.engine, "folder.create", account=world.account, folder="Archive")
    offline(world, monkeypatch)
    call(world.engine, "folder.rename", account=world.account, folder="Work", new_name="Archive/Work", undo=1)
    assert {"Archive/Work", "Archive/Work/Sub"} <= local_folders(world) and "Work" in world.imap.folders  # here at once
    restarted(world)
    monkeypatch.undo()
    assert world.fetch().failed == ()
    assert "Work" not in world.imap.folders and len(world.imap.uids("Archive/Work")) == 1 and len(world.imap.uids("Archive/Work/Sub")) == 1
    in_step(world, ("Archive/Work", "Archive/Work"), ("Archive/Work/Sub", "Archive/Work/Sub"))
