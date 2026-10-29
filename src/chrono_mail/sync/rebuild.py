import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import ChronoMailError, RebuildAborted, StorageError
from chrono_mail.index import Index, IndexBuilder, IndexEntry, entry_from_raw, unreadable_entry
from chrono_mail.index.model import Receiving
from chrono_mail.store import Store, StoredMessage

# --- Version ------------------------------------------------------------
__version__ = "rebuild.py 4.2.417"

# --- Version history ----------------------------------------------------
# v4.2.417: rebuild_index takes the receiver that decides the account that received each message (D-292)
# v2.0.281: the messages are counted first, so progress says how many of how many; a rebuild can be aborted (RebuildAborted: the new
#           index is put away, the old one stays); the new index replaces the old one only when the engine allows it (D-229)
# v1.9.276: a rebuilt entry's last-resort Received time is when its file was written here (D-226)
# v1.1.206: The tags come from the messages' properties files (D-176)
# v1.1.200: fill_send_times reads the Outbox and the Scheduled folder
# v1.1.196: fill_send_times: after the upgrade to index format 9, the send times of the messages already in the Outbox
# v0.5.134: The tags come from the store's tags file, in a rebuild and in a repair
# v0.0.8: Initial index rebuild and reconcile

BATCH_SIZE: int = 1000
Progress = Callable[[int, int], None]  # done, total


@dataclass(frozen=True, slots=True)
class RebuildReport:
    indexed: int
    unreadable: int
    seconds: float


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    added: int
    removed: int
    corrected: int


def _stored_tags(store: Store) -> dict[str, tuple[str, ...]]:
    """The tags of the store's messages; a damaged properties file (kept as .bad by the store) gives none for that message, the mail still gets indexed."""
    try:
        return store.properties.tags()
    except StorageError:
        return {}


def _entry(store: Store, message: StoredMessage, tags: dict[str, tuple[str, ...]]) -> tuple[IndexEntry, bool]:
    """The index entry for one stored message, and whether it could be read."""
    try:
        raw = store.read(message.ref, hint=message.relative_path)
    except ChronoMailError:
        entry = unreadable_entry(message.ref, message.relative_path, message.flags, message.size)
        return replace(entry, tags=tags.get(message.ref.store_id, ())), False
    entry = entry_from_raw(message.ref, message.relative_path, message.flags, message.size, raw, store.stored_at(message.ref, message.relative_path))
    return replace(entry, tags=tags.get(message.ref.store_id, ())), True


def rebuild_index(
    store: Store, index_path: Path, progress: Progress | None = None, stop: Callable[[], bool] | None = None,
    replacing: Callable[[], AbstractContextManager[object]] | None = None, receiver: Receiving | None = None,
) -> tuple[Index, RebuildReport]:
    """Read every message in the store and build a new index. The messages are counted first, so progress tells how many of how many.
    The old index stays in place until the new one is complete; if anything fails, or stop says the person aborted it
    (RebuildAborted), the new one is put away and the old index is untouched. replacing: held while the new file takes the old one's
    place (the engine waits until no command has the old one open)."""
    started = time.monotonic()
    messages = [message for folder in store.list_folders() for message in store.list_messages(folder.path)]  # only lists the files: quick
    total = len(messages)
    builder = IndexBuilder(index_path, receiver)  # receiver: the account that received each message (D-292)
    tags = _stored_tags(store)
    indexed = unreadable = 0
    try:
        if progress is not None:
            progress(0, total)
        batch: list[IndexEntry] = []
        for message in messages:
            entry, readable = _entry(store, message, tags)
            unreadable += 0 if readable else 1
            batch.append(entry)
            if len(batch) >= BATCH_SIZE:
                if stop is not None and stop():
                    raise RebuildAborted("Rebuilding the index was aborted")
                indexed += builder.add_many(batch)
                batch.clear()
                if progress is not None:
                    progress(indexed, total)
        if stop is not None and stop():
            raise RebuildAborted("Rebuilding the index was aborted")
        indexed += builder.add_many(batch)
        if progress is not None:
            progress(indexed, total)
        with (replacing or nullcontext)():
            index = builder.finish()
    except BaseException:
        builder.abandon()
        raise
    return index, RebuildReport(indexed, unreadable, time.monotonic() - started)


def reconcile_index(store: Store, index: Index) -> ReconcileReport:
    """Bring the index in line with the store after an interruption (a message stored but not indexed,
    deleted but still listed, renamed). The store is the truth; mail is never touched."""
    known = index.snapshot()
    tags = _stored_tags(store)
    present: set[str] = set()
    added = corrected = 0
    for folder in store.list_folders():
        for message in store.list_messages(folder.path):
            present.add(message.ref.store_id)
            current = known.get(message.ref.store_id)
            if current is None:
                entry, _ = _entry(store, message, tags)
                index.add(entry)
                added += 1
            elif current != (folder.path, message.relative_path):
                index.move(message.ref.store_id, folder.path, message.relative_path)
                index.update_flags(message.ref.store_id, message.flags, message.relative_path)
                corrected += 1
    stale = [store_id for store_id in known if store_id not in present]
    for store_id in stale:
        index.remove(store_id)
    for store_id in (set(tags) | index.tagged()) & present:  # the tags file is the truth for tags too
        indexed = index.get(store_id)
        if indexed is not None and indexed.tags != tags.get(store_id, ()):
            index.update_tags(store_id, tags.get(store_id, ()))
            corrected += 1
    return ReconcileReport(added, len(stale), corrected)
