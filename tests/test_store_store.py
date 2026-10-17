import os
import threading
from pathlib import Path

import pytest

from chrono_mail.core import CryptoError, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.crypto import generate_data_key
from chrono_mail.store import Store
from chrono_mail.store.store import MessageNotFoundError

# --- Version ------------------------------------------------------------
__version__ = "test_store_store.py 0.5.165"

# --- Version history ----------------------------------------------------
# v0.5.165: the count of fsync calls outside a batch follows Windows too
# v0.5.164: a read never finds a name that a change of flags took away; the tests that replace os.sync also run where it does not exist (Windows)
# v0.5.161: flags: the flags of one message from the name of its file
# v0.5.133: written_together: a batch made safe on disk at once
# v0.0.7: Initial store tests

INBOX = ("Inbox",)
RAW = b"From: a@example.com\r\nSubject: secret subject\r\n\r\nsecret body\r\n"


def make_store(tmp_path: Path, encrypted: bool = True, durable: bool = False) -> Store:
    key = generate_data_key() if encrypted else None
    store = Store.create(tmp_path / "store", "account1", key, durable=durable)
    store.create_folder(INBOX, FolderRole.INBOX)
    return store


def all_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def test_deliver_and_read_back(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    assert store.read(reference) == RAW
    assert reference.account_id == "account1" and reference.folder_path == INBOX


def test_durable_mode_works_too(tmp_path: Path) -> None:
    store = make_store(tmp_path, durable=True)
    assert store.read(store.deliver(INBOX, RAW)) == RAW


def test_new_message_is_unseen_in_new(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.deliver(INBOX, RAW)
    (message,) = store.list_messages(INBOX)
    assert message.relative_path.startswith("new/")
    assert message.flags == frozenset()
    assert message.size == len(RAW)


def test_message_files_are_encrypted_with_random_names(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.deliver(INBOX, RAW)
    for path in all_files(tmp_path / "store"):
        if path.name in ("store.json", "folder.json"):
            continue
        content = path.read_bytes()
        assert b"secret" not in content and b"example.com" not in content
        assert len(path.name) == 32
    assert b"secret" not in b"".join(path.read_bytes() for path in all_files(tmp_path / "store"))


def test_unencrypted_store_keeps_the_message_readable_on_disk(tmp_path: Path) -> None:
    store = make_store(tmp_path, encrypted=False)
    reference = store.deliver(INBOX, RAW)
    assert store.read(reference) == RAW
    assert any(RAW == path.read_bytes() for path in all_files(tmp_path / "store"))
    assert not store.encrypted


def test_no_path_contains_a_colon(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    store.set_flags(reference, frozenset({MessageFlag.SEEN}))
    assert all(":" not in str(path.relative_to(tmp_path)) for path in (tmp_path / "store").rglob("*"))


def test_reopen_with_the_right_key(tmp_path: Path) -> None:
    key = generate_data_key()
    store = Store.create(tmp_path / "store", "account1", key, durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    reference = store.deliver(INBOX, RAW)
    reopened = Store.open(tmp_path / "store", key, durable=False)
    assert reopened.account_id == "account1"
    assert reopened.read(reference) == RAW
    assert [folder.path for folder in reopened.list_folders()] == [INBOX]


def test_wrong_or_missing_key_is_refused(tmp_path: Path) -> None:
    key = generate_data_key()
    Store.create(tmp_path / "store", "account1", key, durable=False)
    with pytest.raises(StorageError):
        Store.open(tmp_path / "store", None)
    with pytest.raises(StorageError):
        Store.open(tmp_path / "store", generate_data_key())


def test_key_on_an_unencrypted_store_is_refused(tmp_path: Path) -> None:
    Store.create(tmp_path / "store", "account1", None, durable=False)
    with pytest.raises(StorageError):
        Store.open(tmp_path / "store", generate_data_key())


def test_create_refuses_to_overwrite_a_store(tmp_path: Path) -> None:
    Store.create(tmp_path / "store", "account1", None, durable=False)
    with pytest.raises(StorageError):
        Store.create(tmp_path / "store", "account1", None, durable=False)


def test_missing_or_damaged_description_is_a_storage_error(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        Store.open(tmp_path / "nothing", None)
    Store.create(tmp_path / "store", "account1", None, durable=False)
    (tmp_path / "store" / "store.json").write_text("{not json")
    with pytest.raises(StorageError):
        Store.open(tmp_path / "store", None)


def test_flags_are_renames_and_the_message_moves_to_cur(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    path = store.set_flags(reference, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}))
    assert path == f"cur/{reference.store_id}!2,FS"
    (message,) = store.list_messages(INBOX)
    assert message.flags == {MessageFlag.SEEN, MessageFlag.FLAGGED}
    assert message.ref == reference
    assert store.read(reference) == RAW
    store.set_flags(reference, frozenset())
    assert store.list_messages(INBOX)[0].relative_path == f"cur/{reference.store_id}!2,"


def test_the_flags_of_one_message_come_from_its_file_name(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    other = store.deliver(INBOX, RAW, flags=frozenset({MessageFlag.SEEN}))
    assert store.flags(reference) == frozenset() and store.flags(other) == {MessageFlag.SEEN}
    path = store.set_flags(reference, frozenset({MessageFlag.FLAGGED, MessageFlag.REPLIED}))
    assert store.flags(reference) == store.flags(reference, hint=path) == {MessageFlag.FLAGGED, MessageFlag.REPLIED}
    store.delete(other)
    with pytest.raises(MessageNotFoundError):
        store.flags(other)


def test_a_read_never_finds_a_name_that_a_change_of_flags_took_away(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    real_locate = Store._locate
    renames: list[threading.Thread] = []

    def locate_then_rename(self: Store, directory: Path, store_id: str, hint: str | None = None) -> Path:
        path = real_locate(self, directory, store_id, hint)
        if not renames:  # another thread marks the message read right after it was found
            renames.append(threading.Thread(target=lambda: store.set_flags(reference, frozenset({MessageFlag.SEEN}))))
            renames[0].start()
            renames[0].join(0.3)  # it waits for the reader (the message's lock), or renames the file at once (no lock)
        return path

    monkeypatch.setattr(Store, "_locate", locate_then_rename)
    assert store.read(reference) == RAW  # the name found is still there when it is read
    renames[0].join(5)
    monkeypatch.undo()
    assert store.flags(reference) == {MessageFlag.SEEN}  # and the change of flags was made afterwards


def test_unseen_message_with_no_flags_stays_in_new(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    assert store.set_flags(reference, frozenset()) == f"new/{reference.store_id}"


def test_delivery_with_flags_goes_to_cur(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW, flags=frozenset({MessageFlag.SEEN}))
    assert store.list_messages(INBOX)[0].relative_path == f"cur/{reference.store_id}!2,S"


def test_unknown_flag_letters_survive_flag_changes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    directory = next((tmp_path / "store" / "folders").iterdir())
    (directory / "new" / reference.store_id).rename(directory / "cur" / f"{reference.store_id}!2,PSa")
    (message,) = store.list_messages(INBOX)
    assert message.flags == {MessageFlag.SEEN} and message.extra_letters == "Pa"
    store.set_flags(reference, frozenset({MessageFlag.SEEN, MessageFlag.REPLIED}))
    assert store.list_messages(INBOX)[0].relative_path == f"cur/{reference.store_id}!2,PRSa"
    store.set_flags(reference, frozenset())
    assert store.list_messages(INBOX)[0].relative_path == f"cur/{reference.store_id}!2,Pa"


def test_hint_speeds_up_but_is_not_trusted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    assert store.read(reference, hint=f"new/{reference.store_id}") == RAW
    assert store.read(reference, hint="cur/" + "0" * 32) == RAW  # wrong hint: found by searching
    assert store.read(reference, hint="../../etc/passwd") == RAW  # ignored, not followed


def test_move_keeps_identity_and_flags(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Archive",), FolderRole.ARCHIVE)
    reference = store.deliver(INBOX, RAW)
    store.set_flags(reference, frozenset({MessageFlag.SEEN}))
    moved = store.move(reference, ("Archive",))
    assert moved.store_id == reference.store_id and moved.folder_path == ("Archive",)
    assert store.list_messages(INBOX) == []
    (message,) = store.list_messages(("Archive",))
    assert message.flags == {MessageFlag.SEEN}
    assert store.read(moved) == RAW


def test_copy_gets_a_new_identity_and_is_verified(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Archive",), FolderRole.ARCHIVE)
    reference = store.deliver(INBOX, RAW, flags=frozenset({MessageFlag.FLAGGED}))
    copy = store.copy(reference, ("Archive",))
    assert copy.store_id != reference.store_id
    assert store.read(copy) == RAW == store.read(reference)
    assert store.list_messages(("Archive",))[0].flags == {MessageFlag.FLAGGED}


def test_delete_removes_the_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    store.delete(reference)
    assert store.list_messages(INBOX) == []
    with pytest.raises(StorageError):
        store.read(reference)


def test_missing_message_is_a_storage_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(StorageError):
        store.read(MessageRef("account1", INBOX, "0" * 32))
    with pytest.raises(StorageError):
        store.read(MessageRef("account1", ("Nowhere",), "0" * 32))


def test_damaged_file_affects_only_that_message(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    good, bad = store.deliver(INBOX, RAW), store.deliver(INBOX, RAW + b"x")
    directory = next((tmp_path / "store" / "folders").iterdir())
    path = directory / "new" / bad.store_id
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(CryptoError):
        store.read(bad)
    with pytest.raises(CryptoError):
        store.verify(bad)
    assert store.read(good) == RAW
    assert len(store.list_messages(INBOX)) == 2


def test_foreign_files_are_ignored(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    reference = store.deliver(INBOX, RAW)
    directory = next((tmp_path / "store" / "folders").iterdir())
    for name in (".DS_Store", "Thumbs.db", "notes.txt"):
        (directory / "cur" / name).write_text("x")
    assert [message.ref for message in store.list_messages(INBOX)] == [reference]
    assert store.all_store_ids() == {reference.store_id}


def test_folders_create_list_and_duplicates(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Archive", "2026"), FolderRole.ARCHIVE)
    assert [folder.path for folder in store.list_folders()] == [("Archive", "2026"), INBOX]
    assert store.virtual_parent_paths() == {("Archive",)}
    with pytest.raises(StorageError):
        store.create_folder(INBOX)
    with pytest.raises(ValueError):
        store.create_folder(())
    with pytest.raises(ValueError):
        store.create_folder(("a", ""))


@pytest.mark.parametrize("name", ["a/b", "..", "con", "CON", "a:b", 'q"uote', "tab\tname", "é☕", "x" * 300, "A", "a"])
def test_any_folder_name_is_safe_because_directories_are_random(tmp_path: Path, name: str) -> None:
    store = make_store(tmp_path)
    store.create_folder((name,))
    reopened = Store.open(tmp_path / "store", store._key, durable=False)  # noqa: SLF001
    assert (name,) in [folder.path for folder in reopened.list_folders()]


def test_folders_differing_only_in_case_do_not_collide(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Work",))
    store.create_folder(("work",))
    assert len(store.list_folders()) == 3


def test_folder_rename_moves_children_and_keeps_messages(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("A",))
    store.create_folder(("A", "B"))
    reference = store.deliver(("A", "B"), RAW)
    store.rename_folder(("A",), ("C",))
    assert [folder.path for folder in store.list_folders()] == [("C",), ("C", "B"), INBOX]
    assert store.read(MessageRef("account1", ("C", "B"), reference.store_id)) == RAW


def test_folder_rename_errors(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("A",))
    store.create_folder(("B",))
    with pytest.raises(StorageError):
        store.rename_folder(("A",), ("B",))
    with pytest.raises(StorageError):
        store.rename_folder(("Missing",), ("X",))


def test_delete_folder_refuses_when_not_empty_and_keeps_files_until_purge(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Old",))
    reference = store.deliver(("Old",), RAW)
    with pytest.raises(StorageError):
        store.delete_folder(("Old",))
    store.delete_folder(("Old",), require_empty=False)
    assert ("Old",) not in [folder.path for folder in store.list_folders()]
    assert len(list((tmp_path / "store" / ".deleted").iterdir())) == 1
    assert store.purge_deleted_folders() == 1
    assert list((tmp_path / "store" / ".deleted").iterdir()) == []
    assert reference.store_id not in store.all_store_ids()


def test_damaged_folder_description_is_reported_not_fatal(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Other",))
    reference = store.deliver(INBOX, RAW)
    for directory in (tmp_path / "store" / "folders").iterdir():
        if '"Other"' in (directory / "folder.json").read_text():
            (directory / "folder.json").write_text("{broken")
    store.refresh()
    assert len(store.damaged_folders) == 1
    assert [folder.path for folder in store.list_folders()] == [INBOX]
    assert store.read(reference) == RAW


def test_size_of_message_is_the_size_of_the_raw_message(tmp_path: Path) -> None:
    for encrypted in (True, False):
        store = make_store(tmp_path / str(encrypted), encrypted=encrypted)
        store.deliver(INBOX, os.urandom(1234))
        assert store.list_messages(INBOX)[0].size == 1234


def test_locate_and_locate_all(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_folder(("Archive",), FolderRole.ARCHIVE)
    first = store.deliver(INBOX, RAW)
    second = store.deliver(("Archive",), RAW, flags=frozenset({MessageFlag.SEEN}))
    assert store.locate(first) == f"new/{first.store_id}"
    assert store.locate(second) == f"cur/{second.store_id}!2,S"
    assert store.locate(first, hint=f"new/{first.store_id}") == f"new/{first.store_id}"
    assert store.locate_all() == {first.store_id: (INBOX, f"new/{first.store_id}"), second.store_id: (("Archive",), f"cur/{second.store_id}!2,S")}
    store.set_flags(first, frozenset({MessageFlag.FLAGGED}))
    assert store.locate(first) == f"cur/{first.store_id}!2,F"
    with pytest.raises(StorageError):
        store.locate(MessageRef("account1", INBOX, "0" * 32))


def test_bulk_delivery_gives_unique_identities(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ids = {store.deliver(INBOX, RAW + str(number).encode()).store_id for number in range(200)}
    assert len(ids) == 200 == len(store.list_messages(INBOX))


def test_written_together_makes_the_whole_batch_safe_once_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path, durable=True)
    calls: list[str] = []
    monkeypatch.setattr(os, "fsync", lambda _descriptor=0: calls.append("fsync"))
    monkeypatch.setattr(os, "sync", lambda: calls.append("sync"), raising=False)
    with store.written_together():
        first = store.deliver(INBOX, b"Subject: a\r\n\r\nA")
        store.deliver(INBOX, b"Subject: b\r\n\r\nB")
        assert calls == []  # nothing made safe one by one
    assert calls == ["sync"]
    calls.clear()
    store.deliver(INBOX, b"Subject: c\r\n\r\nC")
    assert calls == (["fsync", "fsync"] if os.name != "nt" else ["fsync"])  # outside it every message is made safe again (the file and its folder; Windows cannot open folders)
    assert store.read(first) == b"Subject: a\r\n\r\nA"


def test_written_together_without_a_sync_of_everything_makes_each_file_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path, durable=True)
    calls: list[str] = []
    monkeypatch.delattr(os, "sync", raising=False)  # as on Windows
    monkeypatch.setattr(os, "fsync", lambda _descriptor=0: calls.append("fsync"))
    with store.written_together():
        store.deliver(INBOX, b"Subject: a\r\n\r\nA")
        store.deliver(INBOX, b"Subject: b\r\n\r\nB")
    assert calls.count("fsync") == (3 if os.name != "nt" else 2)  # two files, and their one folder (Windows cannot open folders)


def test_written_together_also_makes_safe_what_was_written_before_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path, durable=True)
    calls: list[str] = []
    monkeypatch.setattr(os, "sync", lambda: calls.append("sync"), raising=False)
    with pytest.raises(RuntimeError), store.written_together():
        store.deliver(INBOX, b"Subject: a\r\n\r\nA")
        raise RuntimeError("stopped")
    assert calls == ["sync"]

