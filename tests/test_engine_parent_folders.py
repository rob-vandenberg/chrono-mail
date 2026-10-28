import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.engine.registry import UsageError
from chrono_mail.store import RENAME_NOTE, Store
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_store_interruption import SimulatedCrash

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_parent_folders.py 4.2.403"

# --- Version history ----------------------------------------------------
# v4.2.403: Initial: a folder that was only the parent of others is a normal folder; an interrupted rename is finished at the next
#           opening; a store opened during a rename leaves its new parent to it; a refused rename leaves no note; a damaged note never stops
#           the account; a rename waits for an earlier unfinished one; an IMAP rename leaves no note; a folder with subfolders is deleted only with them (D-285)


def folders(setup: Setup) -> set[str]:
    return {info.path for info in call(setup.engine, "folder.list", account=setup.account_id)}


def made_by_an_old_version(setup: Setup, *paths: str) -> None:
    """Folders made straight in the store, as an import or a folder made inside another did before 4.2.403: their parents only implied."""
    engine = setup.engine
    store = Store.open(engine.store_directory(engine.resolve_account(setup.account_id)), engine.data_key(engine.config()), durable=False)
    for path in paths:
        store.create_folder(tuple(path.split("/")))


def test_a_folder_that_was_only_a_parent_is_a_normal_folder(setup: Setup) -> None:
    made_by_an_old_version(setup, "@acme/admin@example.nl", "@acme/info@example.nl/2016")
    assert {"@acme", "@acme/info@example.nl"} <= folders(setup)  # each a folder of its own once the account opens
    call(setup.engine, "mail.fetch", account=setup.account_id)
    inbox: list[Any] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, target="@acme")
    assert len(call(setup.engine, "message.list", account=setup.account_id, folder="@acme").messages) == 1  # it holds mail
    call(setup.engine, "folder.rename", account=setup.account_id, folder="@acme", new_name="@renamed")
    assert {"@renamed", "@renamed/admin@example.nl", "@renamed/info@example.nl", "@renamed/info@example.nl/2016"} <= folders(setup)
    assert not any(path.startswith("@acme") for path in folders(setup))
    assert len(call(setup.engine, "message.list", account=setup.account_id, folder="@renamed").messages) == 1
    call(setup.engine, "folder.delete", account=setup.account_id, folder="@renamed", with_messages=True, include_subfolders=True)
    assert not any(path.startswith("@renamed") for path in folders(setup))


def test_a_new_folder_inside_folders_that_are_not_there_makes_them(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="Clients/North/2026")
    assert {"Clients", "Clients/North", "Clients/North/2026"} <= folders(setup)


def test_making_the_parents_can_be_interrupted_and_finishes_at_the_next_opening(tmp_path: Path) -> None:
    key = generate_data_key()
    store = Store.create(tmp_path / "store", "account1", key, durable=False)
    for path in (("A", "B", "C"), ("D", "E")):
        store.create_folder(path)
    crashes: list[str] = []

    def crash_once(name: str) -> None:
        if name == "create-folder:before-description" and not crashes:
            crashes.append(name)
            raise SimulatedCrash(name)

    crashing: Callable[[str], None] = crash_once
    with pytest.raises(SimulatedCrash):
        Store.open(tmp_path / "store", key, durable=False, checkpoint=crashing).make_parents_real()
    again = Store.open(tmp_path / "store", key, durable=False)
    again.make_parents_real()
    assert {folder.path for folder in again.list_folders()} == {("A",), ("A", "B"), ("A", "B", "C"), ("D",), ("D", "E")}
    assert again.virtual_parent_paths() == set()


def test_an_imap_account_gets_no_folder_the_server_has_not_got(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False, idle=False) as server:
        server.add("INBOX", b"Subject: x\r\n\r\nbody\r\n")
        created = call(engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
                       incoming_host="127.0.0.1", incoming_port=server.port, incoming_security="none",
                       outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none")
        account = str(getattr(created, "account_id"))
        store = Store.open(engine.store_directory(engine.resolve_account(account)), engine.data_key(engine.config()), durable=False)
        store.create_folder(("Shared", "Team"))  # a parent only implied: on IMAP the server decides which folders exist (D-193)
        listed = {info.path for info in call(engine, "folder.list", account=account)}
        assert "Shared/Team" in listed and "Shared" not in listed


def store_of(setup: Setup, checkpoint: Callable[[str], None] | None = None) -> Store:
    engine = setup.engine
    directory, key = engine.store_directory(engine.resolve_account(setup.account_id)), engine.data_key(engine.config())
    return Store.open(directory, key, durable=False, checkpoint=checkpoint) if checkpoint is not None else Store.open(directory, key, durable=False)


def test_an_interrupted_rename_is_finished_at_the_next_opening(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    call(setup.engine, "mail.fetch", account=setup.account_id)
    inbox: list[Any] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, target="A/x")

    def crash(name: str) -> None:
        if name == "rename-folder:after-folder":
            raise SimulatedCrash(name)

    with pytest.raises(SimulatedCrash):
        store_of(setup, crash).rename_folder(("A",), ("B",), noted=True)  # as the engine renames mail kept here  # stopped after A/x became B/x: B only implied, A still there, the index not changed
    assert {("A",), ("B", "x")} <= {folder.path for folder in store_of(setup).list_folders()}
    listed = folders(setup)  # the next opening finishes it before the parents are made
    assert {"B", "B/x"} <= listed and not any(path == "A" or path.startswith("A/") for path in listed)
    assert len(call(setup.engine, "message.list", account=setup.account_id, folder="B/x").messages) == 1  # the index followed
    assert store_of(setup).read_private(RENAME_NOTE) is None


def test_a_store_opened_during_a_rename_makes_no_folder_twice(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    seen: list[set[tuple[str, ...]]] = []

    def meanwhile(name: str) -> None:
        if name == "rename-folder:after-folder" and not seen:  # B/x made, B only implied for a moment
            other = threading.Thread(target=lambda: store_of(setup).make_parents_real())  # another command opens the store
            other.start()
            other.join(5)
            seen.append({folder.path for folder in store_of(setup).list_folders()})

    store_of(setup, meanwhile).rename_folder(("A",), ("B",), noted=True)
    assert ("B",) not in seen[0]  # the rename under way: its new parent is left to it
    after = store_of(setup)
    assert not after.damaged_folders and [folder.path for folder in after.list_folders()].count(("B",)) == 1


def test_a_folder_with_subfolders_is_deleted_only_with_them(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    with pytest.raises(UsageError, match="subfolders"):
        call(setup.engine, "folder.delete", account=setup.account_id, folder="A")
    assert {"A", "A/x"} <= folders(setup)


def test_a_refused_rename_leaves_nothing_to_finish(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    call(setup.engine, "folder.create", account=setup.account_id, folder="B")
    with pytest.raises(StorageError, match="already exists"):
        call(setup.engine, "folder.rename", account=setup.account_id, folder="A", new_name="B")  # B is there: refused
    with pytest.raises(UsageError, match="into itself"):
        call(setup.engine, "folder.rename", account=setup.account_id, folder="A", new_name="A/y")
    assert store_of(setup).read_private(RENAME_NOTE) is None
    call(setup.engine, "folder.delete", account=setup.account_id, folder="B", with_messages=True)
    assert {"A", "A/x"} <= folders(setup) and "B" not in folders(setup)  # nothing renames A later on its own


def test_a_damaged_note_never_stops_the_account_from_opening(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    note = store_of(setup)._private_path(RENAME_NOTE)  # noqa: SLF001 - the note's file, damaged on purpose
    note.parent.mkdir(exist_ok=True)
    note.write_bytes(b"not a sealed note")
    assert {"A", "A/x"} <= folders(setup)
    assert not note.exists()


def test_a_rename_waits_for_an_earlier_one_that_is_not_finished(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    call(setup.engine, "folder.create", account=setup.account_id, folder="C")
    store = store_of(setup)
    store.write_private(RENAME_NOTE, '{"old": ["A"], "new": ["B"]}')  # an earlier rename that could not be finished (a full disk)

    def disk_full(self: Store, directory: Path, folder: Any) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(Store, "_write_description", disk_full)
        with pytest.raises(StorageError, match="could not be finished: .*No space left"):  # the real cause, never a vague "try again"
            store.rename_folder(("C",), ("D",), noted=True)
    assert store.read_private(RENAME_NOTE) == '{"old": ["A"], "new": ["B"]}'  # never replaced
    assert {"B", "B/x", "C"} <= folders(setup) and "A" not in folders(setup)  # the next opening finished it; C waits for its own rename
    call(setup.engine, "folder.rename", account=setup.account_id, folder="C", new_name="D")
    assert "D" in folders(setup)


def test_a_rename_to_the_same_name_does_nothing(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A")
    call(setup.engine, "folder.rename", account=setup.account_id, folder="A", new_name="A")
    assert "A" in folders(setup)


def test_an_imap_rename_leaves_no_note(tmp_path: Path) -> None:
    key = generate_data_key()
    store = Store.create(tmp_path / "store", "account1", key, durable=False)
    store.create_folder(("A", "x"))
    store.rename_folder(("A",), ("B",))  # the synchronisation of an IMAP account follows its renames itself (D-193)
    assert not (tmp_path / "store" / "private" / RENAME_NOTE).exists()


def test_an_earlier_rename_finished_by_the_next_one_is_followed_by_the_index(setup: Setup) -> None:
    call(setup.engine, "folder.create", account=setup.account_id, folder="A/x")
    call(setup.engine, "folder.create", account=setup.account_id, folder="C")
    call(setup.engine, "mail.fetch", account=setup.account_id)
    inbox: list[Any] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages)
    call(setup.engine, "message.move", account=setup.account_id, folder="Inbox", message=inbox[0].message_id, target="A/x")
    store = store_of(setup)
    store.write_private(RENAME_NOTE, '{"old": ["A"], "new": ["B"]}')  # an earlier rename the last opening could not finish
    with pytest.raises(StorageError, match="just finished"):
        store.rename_folder(("C",), ("D",), noted=True)  # it finishes the earlier one first, and asks to try again
    assert len(call(setup.engine, "message.list", account=setup.account_id, folder="B/x").messages) == 1  # the next opening: the index followed
    assert store_of(setup).read_private(RENAME_NOTE) is None
    call(setup.engine, "folder.rename", account=setup.account_id, folder="C", new_name="D")
    assert "D" in folders(setup)

