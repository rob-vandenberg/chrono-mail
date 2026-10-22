import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chrono_mail.core import FolderRole, MessageFlag, MessageRef
from chrono_mail.crypto import generate_data_key
from chrono_mail.index import UNREADABLE_SUBJECT, Index, IndexBuilder, SortField
from chrono_mail.store import Store
from chrono_mail.sync import rebuild_index, reconcile_index
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_rebuild.py 2.0.281"

# --- Version history ----------------------------------------------------
# v2.0.281: progress says how many of how many (D-229)
# v1.9.276: A message without any time keeps the moment its file was stored, through a rebuild (D-226)
# v0.0.8: Initial index rebuild tests

INBOX = ("Inbox",)
ARCHIVE = ("Archive",)
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def make_store(tmp_path: Path, count: int = 12) -> Store:
    store = Store.create(tmp_path / "store", "account1", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    store.create_folder(ARCHIVE, FolderRole.ARCHIVE)
    for number in range(count):
        raw = raw_message(subject=f"Message {number}", body=f"unique{number} common\n", date=BASE + timedelta(days=number), message_id=f"<m{number}@x>")
        flags = frozenset({MessageFlag.SEEN}) if number % 3 == 0 else frozenset()
        store.deliver(INBOX if number < 8 else ARCHIVE, raw, flags=flags)
    return store


def dump(index: Index) -> list[tuple[str, tuple[str, ...], str, frozenset[MessageFlag], str]]:
    rows = [message for folder in (INBOX, ARCHIVE) for message in index.page(folder, SortField.DATE, True, 0, 1000)]
    return sorted((m.store_id, m.folder, m.path, m.flags, m.subject) for m in rows)


def test_rebuild_indexes_every_message(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    index, report = rebuild_index(store, tmp_path / "index.sqlite")
    assert (report.indexed, report.unreadable) == (12, 0)
    assert index.count(INBOX) == 8 and index.count(ARCHIVE) == 4
    assert [m.subject for m in index.search("unique5")] == ["Message 5"]
    assert len(index.search("common")) == 12


def test_deleting_the_index_loses_nothing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first, _ = rebuild_index(store, tmp_path / "index.sqlite")
    before = dump(first)
    first.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{tmp_path / 'index.sqlite'}{suffix}").unlink(missing_ok=True)
    second, _ = rebuild_index(store, tmp_path / "index.sqlite")
    assert dump(second) == before
    assert len(store.all_store_ids()) == 12  # the store was never touched


def test_rebuild_matches_incremental_updates(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rebuilt, _ = rebuild_index(store, tmp_path / "rebuilt.sqlite")
    other = make_store(tmp_path / "other")
    incremental, _ = rebuild_index(other, tmp_path / "incremental.sqlite")
    message = incremental.page(INBOX, limit=1)[0]
    reference = MessageRef("account1", INBOX, message.store_id)
    path = other.set_flags(reference, frozenset({MessageFlag.FLAGGED}))
    incremental.update_flags(message.store_id, frozenset({MessageFlag.FLAGGED}), path)
    moved = other.move(reference, ARCHIVE)
    incremental.move(moved.store_id, ARCHIVE, path)
    from_scratch, _ = rebuild_index(other, tmp_path / "scratch.sqlite")
    assert dump(from_scratch) == dump(incremental)
    assert rebuilt.message_count() == 12


def test_damaged_message_stays_visible_as_a_placeholder(tmp_path: Path) -> None:
    store = make_store(tmp_path, 3)
    victim = store.list_messages(INBOX)[0]
    directory = next(path for path in (tmp_path / "store" / "folders").iterdir() if (path / victim.relative_path).exists())
    file = directory / victim.relative_path
    data = bytearray(file.read_bytes())
    data[-1] ^= 1
    file.write_bytes(bytes(data))
    index, report = rebuild_index(store, tmp_path / "index.sqlite")
    assert (report.indexed, report.unreadable) == (3, 1)
    assert index.get(victim.ref.store_id) is not None
    assert [m.subject for m in index.page(INBOX, limit=10)].count(UNREADABLE_SUBJECT) == 1


def test_failed_rebuild_leaves_the_old_index_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path)
    index, _ = rebuild_index(store, tmp_path / "index.sqlite")
    before = dump(index)
    index.close()
    calls = {"count": 0}
    original = Store.read

    def failing_read(self: Store, ref: MessageRef, hint: str | None = None) -> bytes:
        calls["count"] += 1
        if calls["count"] == 6:
            raise KeyboardInterrupt  # a hard stop in the middle of the rebuild
        return original(self, ref, hint)

    monkeypatch.setattr(Store, "read", failing_read)
    with pytest.raises(KeyboardInterrupt):
        rebuild_index(store, tmp_path / "index.sqlite")
    monkeypatch.undo()
    assert dump(Index(tmp_path / "index.sqlite")) == before
    assert not (tmp_path / "index.sqlite.new").exists()


def test_leftover_build_file_from_a_crash_is_ignored(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    (tmp_path / "index.sqlite.new").write_bytes(b"half written garbage")
    index, report = rebuild_index(store, tmp_path / "index.sqlite")
    assert report.indexed == 12 and index.message_count() == 12


def test_progress_is_reported(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seen: list[tuple[int, int]] = []
    rebuild_index(store, tmp_path / "index.sqlite", progress=lambda done, total: seen.append((done, total)))
    assert seen[0] == (0, 12) and seen[-1] == (12, 12)  # counted first: how many of how many from the start


def test_builder_can_be_abandoned(tmp_path: Path) -> None:
    builder = IndexBuilder(tmp_path / "index.sqlite")
    builder.abandon()
    assert list(tmp_path.iterdir()) == []


def test_reconcile_repairs_what_an_interruption_left(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    index, _ = rebuild_index(store, tmp_path / "index.sqlite")
    # 1: stored but never indexed (crash after the store write)
    newcomer = store.deliver(INBOX, raw_message(subject="Newcomer", date=BASE))
    # 2: deleted from the store but still indexed
    victim = store.list_messages(ARCHIVE)[0]
    store.delete(victim.ref)
    # 3: flag changed in the store but not in the index
    target = next(message for message in store.list_messages(INBOX) if message.ref.store_id != newcomer.store_id)
    store.set_flags(target.ref, frozenset({MessageFlag.FLAGGED, MessageFlag.SEEN}))
    report = reconcile_index(store, index)
    assert (report.added, report.removed, report.corrected) == (1, 1, 1)
    assert index.get(newcomer.store_id) is not None and index.get(victim.ref.store_id) is None
    corrected = index.get(target.ref.store_id)
    assert corrected is not None and corrected.flags == {MessageFlag.FLAGGED, MessageFlag.SEEN}
    assert reconcile_index(store, index) == type(report)(0, 0, 0)  # and a second pass has nothing to do
    assert index.all_store_ids() == store.all_store_ids()


def test_a_rebuilt_message_without_any_time_keeps_when_its_file_was_stored(tmp_path: Path) -> None:
    """D-226's last resort survives a rebuild: the moment the file was written here, not the moment of the rebuild."""
    store = Store.create(tmp_path / "store", "account1", generate_data_key(), durable=False)
    store.create_folder(INBOX, FolderRole.INBOX)
    reference = store.deliver(INBOX, raw_message(subject="No time", date=None))
    stored = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    file = next((tmp_path / "store").rglob(f"{reference.store_id}*"))
    os.utime(file, (stored.timestamp(), stored.timestamp()))
    index, _ = rebuild_index(store, tmp_path / "index.sqlite")
    assert [row.date for row in index.page(INBOX)] == [stored]
