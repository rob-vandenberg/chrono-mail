from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.engine import UsageError
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_actions.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Junk and unread navigation
# v0.0.17: Initial tests of archive, mark folder as read, empty Trash and Junk


def rows(setup: Setup, folder: str) -> list[Any]:
    return list(call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages)


def fetched(setup: Setup) -> list[Any]:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    return rows(setup, "Inbox")


def test_archiving_creates_the_archive_folder_once_and_moves_the_message(setup: Setup) -> None:
    inbox = fetched(setup)
    first = call(setup.engine, "message.archive", account=setup.account_id, folder="Inbox", message=inbox[0].message_id)
    assert first.folder == "Archive" and first.created_folder
    second = call(setup.engine, "message.archive", account=setup.account_id, folder="Inbox", message=inbox[1].message_id)
    assert second.folder == "Archive" and not second.created_folder
    assert len(rows(setup, "Archive")) == 2 and len(rows(setup, "Inbox")) == 1
    folders = {info.path: info.role for info in call(setup.engine, "folder.list", account=setup.account_id)}
    assert folders["Archive"] == "archive"
    assert call(setup.engine, "message.search", account=setup.account_id, query="Message")  # still found after the move


def test_archiving_a_message_that_is_archived_already_says_so(setup: Setup) -> None:
    inbox = fetched(setup)
    moved = call(setup.engine, "message.archive", account=setup.account_id, folder="Inbox", message=inbox[0].message_id)
    with pytest.raises(UsageError, match="already in the archive"):
        call(setup.engine, "message.archive", account=setup.account_id, folder="Archive", message=moved.message_id)


def test_an_existing_folder_called_archive_is_used(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="Archive")
    inbox = fetched(setup)
    result = call(setup.engine, "message.archive", account=setup.account_id, folder="Inbox", message=inbox[0].message_id)
    assert not result.created_folder and len(rows(setup, "Archive")) == 1


def test_marking_a_folder_as_read_changes_only_the_unread_messages(setup: Setup) -> None:
    inbox = fetched(setup)
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, seen=True, flagged=True)
    result = call(setup.engine, "folder.mark-read", account=setup.account_id, folder="Inbox")
    assert result.changed == 2
    assert all("seen" in row.flags for row in rows(setup, "Inbox"))
    assert sum("flagged" in row.flags for row in rows(setup, "Inbox")) == 1  # other marks stay
    assert call(setup.engine, "folder.mark-read", account=setup.account_id, folder="inbox").changed == 0
    assert next(info for info in call(setup.engine, "folder.list", account=setup.account_id) if info.path == "Inbox").unread == 0


def test_emptying_trash_and_junk_removes_for_good_and_nothing_else(setup: Setup) -> None:
    inbox = fetched(setup)
    call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=inbox[0].message_id)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[1].message_id, target="Junk")
    assert len(rows(setup, "Trash")) == 1 and len(rows(setup, "Junk")) == 1
    assert call(setup.engine, "folder.empty", account=setup.account_id, folder="Trash").removed == 1
    assert call(setup.engine, "folder.empty", account=setup.account_id, folder="Junk").removed == 1
    assert rows(setup, "Trash") == [] and rows(setup, "Junk") == [] and len(rows(setup, "Inbox")) == 1
    assert call(setup.engine, "folder.empty", account=setup.account_id, folder="Trash").removed == 0


def test_other_folders_cannot_be_emptied(setup: Setup) -> None:
    fetched(setup)
    for folder in ("Inbox", "Sent", "Drafts"):
        with pytest.raises(UsageError, match="Only the Trash and Junk"):
            call(setup.engine, "folder.empty", account=setup.account_id, folder=folder)
    assert len(rows(setup, "Inbox")) == 3
    with pytest.raises(UsageError):
        call(setup.engine, "folder.empty", account=setup.account_id, folder="Nowhere")


def test_junk_and_not_junk(setup: Setup) -> None:
    inbox = fetched(setup)
    moved = call(setup.engine, "message.junk", account=setup.account_id, folder="Inbox", message=inbox[0].message_id)
    assert moved.folder == "Junk" and len(rows(setup, "Junk")) == 1 and len(rows(setup, "Inbox")) == 2
    with pytest.raises(UsageError, match="already"):
        call(setup.engine, "message.junk", account=setup.account_id, folder="Junk", message=moved.message_id)
    with pytest.raises(UsageError, match="not in the Junk"):
        call(setup.engine, "message.not-junk", account=setup.account_id, folder="Inbox", message=inbox[1].message_id)
    back = call(setup.engine, "message.not-junk", account=setup.account_id, folder="Junk", message=moved.message_id)
    assert back.folder == "Inbox" and rows(setup, "Junk") == [] and len(rows(setup, "Inbox")) == 3
