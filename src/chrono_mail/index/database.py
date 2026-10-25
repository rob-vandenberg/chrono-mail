import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from chrono_mail.core import StorageError

# --- Version ------------------------------------------------------------
__version__ = "database.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: writes: how many changes this program wrote to an index file that were not told at once (a big quiet job's commit, a rebuilt
#           file put in place); told_at_once marks the others (a single change of the mailbox: the list hears it as a fact)
# v2.5.335: _format: the format a file holds, read again under the write lock before anything is made or upgraded (D-247)
# v1.8.263: R-16: the one copy of opening an index file and of its transactions (was in index/index.py and pimstore/index.py)

MINIMUM_SQLITE_VERSION: tuple[int, int, int] = (3, 43, 0)  # contentless FTS5 tables that allow deletes
_DAMAGED: str = "The index is damaged; rebuild it"
_WRITES: dict[str, int] = {}  # index file -> changes this program wrote to it, not told at once (one copy runs per person, D-137)
_WRITES_LOCK = threading.Lock()
_TOLD = threading.local()  # .on: the writes of this thread are told the moment they are made; .pending: index files so written, not told yet


def writes(path: Path) -> int:
    """How many changes this program wrote to this index file so far that were not told at once (a big job tells its changes only at its end,
    a rebuilt file none): two reads with the same number had no such write between them. The changes told at once the list hears as facts,
    and judges them itself (a message read while a large view loads changes nothing there: the window's places not known yet, RV-206)."""
    with _WRITES_LOCK:
        return _WRITES.get(os.path.realpath(path), 0)


def _count(key: str) -> None:
    with _WRITES_LOCK:
        _WRITES[key] = _WRITES.get(key, 0) + 1


def _pending() -> set[str]:
    pending = getattr(_TOLD, "pending", None)
    if not isinstance(pending, set):
        pending = set[str]()
        _TOLD.pending = pending
    return pending


def wrote(path: Path, told: bool = True) -> None:
    """One more change written to this index file (a commit, or with told False a rebuilt file put in its place, never told). Inside
    told_at_once it waits for its fact: told (facts_told), it is not counted; lost (facts_lost), it is."""
    key = os.path.realpath(path)
    if told and getattr(_TOLD, "on", False):
        _pending().add(key)
        return
    _count(key)


@contextmanager
def told_at_once(flush: bool = True) -> Generator[None]:
    """The index writes of this thread inside are told the moment they are made (a single change of the mailbox, under its place lock,
    D-151): they are counted by writes only when their fact is not told after all. flush: the fact is told inside, so whatever is not told
    by its end counts; else the caller says it (facts_told, facts_lost)."""
    before = getattr(_TOLD, "on", False)
    _TOLD.on = True
    try:
        yield
    finally:
        _TOLD.on = before
        if flush:
            facts_lost()


def facts_told() -> None:
    """The facts of this thread's pending writes were told: the list hears them, and they are not counted."""
    _pending().clear()


def facts_lost() -> None:
    """This thread's pending writes were not told (a step failed between the write and its fact): they count, so no list trusts its places."""
    pending = _pending()
    for key in pending:
        _count(key)
    pending.clear()


class IndexDatabase:
    """One rebuildable index file (SQLite). Each kind of index adds its own layout and format check in _prepare."""

    def __init__(self, path: Path) -> None:
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
            raise StorageError(f"SQLite {sqlite3.sqlite_version} is too old; 3.43 or newer is needed")
        self.path: Path = path
        try:
            self._connection: sqlite3.Connection = sqlite3.connect(path, isolation_level=None, timeout=30)  # waits for another writer
        except sqlite3.DatabaseError:
            raise StorageError(_DAMAGED) from None
        try:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._prepare()
        except BaseException as error:
            # Closed before the error goes on: the rebuild that follows deletes the -wal file, and Windows cannot delete
            # a file that is still open (WinError 32); the connection would only be freed by the garbage collector.
            self._connection.close()
            if isinstance(error, sqlite3.DatabaseError):
                raise StorageError(_DAMAGED) from None
            raise

    def _prepare(self) -> None:
        """Make the layout of a new file, or check (and upgrade) the format of an existing one. StorageError when it cannot be used.
        Anything it writes is decided under the write lock: another connection may make or upgrade the same file at the same moment (D-247)."""

    def _format(self) -> str | None:
        """The format the file holds: None for a new, empty file; "" when it has tables but no format number."""
        if self._connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0:
            return None
        version = self._connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        return "" if version is None else str(version[0])

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self) -> Generator[None]:
        # The write lock at once (D-107): a deferred BEGIN fails at once with "database is locked" when another connection wrote
        # in between; this way two changes at the same time wait for each other (30 s) instead.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        wrote(self.path)  # before and after the commit: a read during it gets a count of its own, so a read that saw the old data and one
        self._connection.execute("COMMIT")  # that sees the new never carry the same count (a count for a failed commit only makes it
        wrote(self.path)  # stricter)

    def check(self) -> bool:
        """Run SQLite's integrity check (slow on a large index)."""
        return self._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
