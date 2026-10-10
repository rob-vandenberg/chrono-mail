import os
from collections.abc import Callable
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.crypto import DataKey, generate_data_key
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "test_store_interruption.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: Initial store interruption tests

# AGENTS.md section 4.1.4: everything that could destroy data is tested for being safe when
# interrupted at any point. A crash is simulated by an exception that is not an Exception, so
# nothing in the store can catch it and tidy up, exactly like a killed process.

INBOX = ("Inbox",)
ARCHIVE = ("Archive",)
RAW = b"From: a@example.com\r\nSubject: precious\r\n\r\nprecious body\r\n"
OTHER = b"From: b@example.com\r\nSubject: bystander\r\n\r\nbystander body\r\n"


class SimulatedCrash(BaseException):
    pass


def crash_at(name: str, occurrence: int = 1) -> Callable[[str], None]:
    seen = {"count": 0}

    def checkpoint(current: str) -> None:
        if current == name:
            seen["count"] += 1
            if seen["count"] == occurrence:
                raise SimulatedCrash(name)

    return checkpoint


class Case:
    """A store with a bystander message, a way to run one operation with a crash, and a restart."""

    def __init__(self, root: Path) -> None:
        self.root = root / "store"
        self.key: DataKey = generate_data_key()
        store = Store.create(self.root, "account1", self.key, durable=False)
        store.create_folder(INBOX, FolderRole.INBOX)
        store.create_folder(ARCHIVE, FolderRole.ARCHIVE)
        self.bystander: MessageRef = store.deliver(INBOX, OTHER)
        self.store: Store = store

    def with_crash(self, name: str, occurrence: int = 1) -> Store:
        return Store.open(self.root, self.key, durable=False, checkpoint=crash_at(name, occurrence))

    def restart(self) -> Store:
        return Store.open(self.root, self.key, durable=False)

    def assert_bystander_intact(self, store: Store) -> None:
        assert store.read(self.bystander) == OTHER


def message_files(store: Store, name: str) -> list[Path]:
    return [path for path in (store.root / "folders").rglob(name + "*") if path.is_file()]


@pytest.mark.parametrize("point", ["write:before-temporary", "write:after-temporary", "write:after-rename"])
def test_crash_while_delivering(tmp_path: Path, point: str) -> None:
    case = Case(tmp_path)
    crashing = case.with_crash(point)
    with pytest.raises(SimulatedCrash):
        crashing.deliver(INBOX, RAW)
    store = case.restart()
    case.assert_bystander_intact(store)
    messages = store.list_messages(INBOX)
    if point == "write:after-rename":
        assert len(messages) == 2  # complete, because the rename comes after the flush
        assert RAW in [store.read(message.ref) for message in messages]
    else:
        assert len(messages) == 1  # nothing half-written is ever visible
    for message in messages:
        store.verify(message.ref)


def test_leftover_temporary_file_is_cleaned_up_by_age(tmp_path: Path) -> None:
    case = Case(tmp_path)
    with pytest.raises(SimulatedCrash):
        case.with_crash("write:after-temporary").deliver(INBOX, RAW)
    store = case.restart()
    temporaries = [path for path in (case.root / "folders").rglob("tmp/*")]
    assert len(temporaries) == 1
    assert store.recover().temporary_files_removed == 0  # fresh: another process may still be writing
    assert len(list((case.root / "folders").rglob("tmp/*"))) == 1
    assert store.recover(temporary_file_maximum_age=0).temporary_files_removed == 1
    assert list((case.root / "folders").rglob("tmp/*")) == []
    case.assert_bystander_intact(store)


def test_recover_never_touches_messages(tmp_path: Path) -> None:
    case = Case(tmp_path)
    store = case.restart()
    for number in range(20):
        store.deliver(INBOX, RAW + str(number).encode(), flags=frozenset({MessageFlag.SEEN}) if number % 2 else frozenset())
    before = sorted(message.relative_path for message in store.list_messages(INBOX))
    store.recover(temporary_file_maximum_age=0)
    assert sorted(message.relative_path for message in store.list_messages(INBOX)) == before


@pytest.mark.parametrize("point", ["flags:before-rename", "flags:after-rename"])
def test_crash_while_changing_flags(tmp_path: Path, point: str) -> None:
    case = Case(tmp_path)
    reference = case.store.deliver(INBOX, RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash(point).set_flags(reference, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}))
    store = case.restart()
    case.assert_bystander_intact(store)
    assert store.read(reference) == RAW
    assert len(message_files(store, reference.store_id)) == 1  # exactly one file, never two, never none
    (message,) = [m for m in store.list_messages(INBOX) if m.ref == reference]
    expected = {MessageFlag.SEEN, MessageFlag.FLAGGED} if point == "flags:after-rename" else set()
    assert message.flags == expected


def test_flag_change_can_be_repeated_after_a_crash(tmp_path: Path) -> None:
    case = Case(tmp_path)
    reference = case.store.deliver(INBOX, RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash("flags:before-rename").set_flags(reference, frozenset({MessageFlag.SEEN}))
    store = case.restart()
    store.set_flags(reference, frozenset({MessageFlag.SEEN}))
    assert store.list_messages(INBOX)[0].flags | store.list_messages(INBOX)[1].flags >= {MessageFlag.SEEN}


@pytest.mark.parametrize("point", ["move:before-rename", "move:after-rename"])
def test_crash_while_moving(tmp_path: Path, point: str) -> None:
    case = Case(tmp_path)
    reference = case.store.deliver(INBOX, RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash(point).move(reference, ARCHIVE)
    store = case.restart()
    case.assert_bystander_intact(store)
    assert len(message_files(store, reference.store_id)) == 1
    where = ARCHIVE if point == "move:after-rename" else INBOX
    assert store.read(MessageRef("account1", where, reference.store_id)) == RAW


@pytest.mark.parametrize("point", ["write:before-temporary", "write:after-temporary", "write:after-rename"])
def test_crash_while_copying_never_damages_the_original(tmp_path: Path, point: str) -> None:
    case = Case(tmp_path)
    reference = case.store.deliver(INBOX, RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash(point).copy(reference, ARCHIVE)
    store = case.restart()
    assert store.read(reference) == RAW
    case.assert_bystander_intact(store)
    for message in store.list_messages(ARCHIVE):
        assert store.read(message.ref) == RAW  # a copy that is visible is complete


def test_crash_while_deleting_a_message(tmp_path: Path) -> None:
    case = Case(tmp_path)
    reference = case.store.deliver(INBOX, RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash("delete:before-unlink").delete(reference)
    store = case.restart()
    assert store.read(reference) == RAW  # nothing happened
    case.assert_bystander_intact(store)
    store.delete(reference)
    assert message_files(store, reference.store_id) == []
    case.assert_bystander_intact(store)


@pytest.mark.parametrize("point", ["delete-folder:before-move", "delete-folder:after-move"])
def test_crash_while_deleting_a_folder(tmp_path: Path, point: str) -> None:
    case = Case(tmp_path)
    case.store.create_folder(("Doomed",))
    reference = case.store.deliver(("Doomed",), RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash(point).delete_folder(("Doomed",), require_empty=False)
    store = case.restart()
    case.assert_bystander_intact(store)
    if point == "delete-folder:before-move":
        assert store.read(MessageRef("account1", ("Doomed",), reference.store_id)) == RAW
    else:
        assert ("Doomed",) not in [folder.path for folder in store.list_folders()]
        assert message_files(store, reference.store_id) == []  # no longer part of the store
        kept = list((case.root / ".deleted").rglob(reference.store_id + "*"))
        assert len(kept) == 1  # set aside, not destroyed, until purged


def test_crash_while_purging_deleted_folders(tmp_path: Path) -> None:
    case = Case(tmp_path)
    for name in ("One", "Two", "Three"):
        case.store.create_folder((name,))
        case.store.deliver((name,), RAW)
        case.store.delete_folder((name,), require_empty=False)
    with pytest.raises(SimulatedCrash):
        case.with_crash("purge:before-remove", occurrence=2).purge_deleted_folders()
    store = case.restart()
    case.assert_bystander_intact(store)
    assert len(list((case.root / ".deleted").iterdir())) == 2
    assert store.purge_deleted_folders() == 2
    assert list((case.root / ".deleted").iterdir()) == []
    case.assert_bystander_intact(store)


def valid_tree(store: Store, expected: set[tuple[str, ...]]) -> None:
    paths = {folder.path for folder in store.list_folders()}
    assert paths == expected
    assert store.damaged_folders == []


@pytest.mark.parametrize("occurrence", [1, 2, 3, 4])
@pytest.mark.parametrize("point", ["rename-folder:before-folder", "rename-folder:after-folder"])
def test_crash_while_renaming_a_subtree(tmp_path: Path, point: str, occurrence: int) -> None:
    case = Case(tmp_path)
    for path in (("A",), ("A", "B"), ("A", "B", "C"), ("A", "D")):
        case.store.create_folder(path)
    reference = case.store.deliver(("A", "B", "C"), RAW)
    with pytest.raises(SimulatedCrash):
        case.with_crash(point, occurrence).rename_folder(("A",), ("X",))
    store = case.restart()
    case.assert_bystander_intact(store)
    paths = {folder.path for folder in store.list_folders()}
    assert len(paths) == 6  # INBOX, ARCHIVE and the four renamed-or-not folders: none lost, none doubled
    assert len({store._entry(path).directory for path in paths}) == 6  # noqa: SLF001
    # whatever state it stopped in, repeating the rename finishes it
    store.rename_folder(("A",), ("X",))
    valid_tree(store, {INBOX, ARCHIVE, ("X",), ("X", "B"), ("X", "B", "C"), ("X", "D")})
    assert store.read(MessageRef("account1", ("X", "B", "C"), reference.store_id)) == RAW


def test_partial_rename_leaves_a_virtual_parent_not_a_broken_tree(tmp_path: Path) -> None:
    case = Case(tmp_path)
    case.store.create_folder(("A",))
    case.store.create_folder(("A", "B"))
    with pytest.raises(SimulatedCrash):
        case.with_crash("rename-folder:after-folder", 1).rename_folder(("A",), ("X",))
    store = case.restart()
    # The deepest folder is renamed first, so X/B exists while X does not (yet).
    assert {folder.path for folder in store.list_folders()} == {INBOX, ARCHIVE, ("A",), ("X", "B")}
    assert store.virtual_parent_paths() == {("X",)}
    store.rename_folder(("A",), ("X",))
    assert {folder.path for folder in store.list_folders()} == {INBOX, ARCHIVE, ("X",), ("X", "B")}
    assert store.virtual_parent_paths() == set()


def test_repeating_a_finished_rename_does_nothing(tmp_path: Path) -> None:
    case = Case(tmp_path)
    case.store.create_folder(("A",))
    case.store.rename_folder(("A",), ("X",))
    case.store.rename_folder(("A",), ("X",))
    assert ("X",) in [folder.path for folder in case.store.list_folders()]
    with pytest.raises(StorageError):
        case.store.rename_folder(("Never",), ("Existed",))


def test_crash_while_creating_a_folder(tmp_path: Path) -> None:
    case = Case(tmp_path)
    with pytest.raises(SimulatedCrash):
        case.with_crash("create-folder:before-description").create_folder(("New",))
    store = case.restart()
    assert ("New",) not in [folder.path for folder in store.list_folders()]
    assert len(store.damaged_folders) == 1
    report = store.recover()
    assert report.abandoned_folders_removed == 1 and store.damaged_folders == []
    store.create_folder(("New",))  # and the name is free again
    case.assert_bystander_intact(store)


def test_real_write_error_cleans_up_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case(tmp_path)
    store = case.restart()

    def failing_rename(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "rename", failing_rename)
    with pytest.raises(StorageError, match="No space left"):
        store.deliver(INBOX, RAW)
    monkeypatch.undo()
    assert list((case.root / "folders").rglob("tmp/*")) == []  # a handled error leaves no litter
    assert len(store.list_messages(INBOX)) == 1
    case.assert_bystander_intact(store)


def test_verification_failure_removes_the_bad_copy_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case(tmp_path)
    store = case.restart()
    original = Store._decode  # noqa: SLF001

    def corrupt(self: Store, blob: bytes) -> bytes:
        return original(self, blob) + b"!"

    monkeypatch.setattr(Store, "_decode", corrupt)
    with pytest.raises(StorageError, match="differs"):
        store.deliver(INBOX, RAW)
    monkeypatch.undo()
    assert len(store.list_messages(INBOX)) == 1  # only the bystander: the unverified copy is gone
