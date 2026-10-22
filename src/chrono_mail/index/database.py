import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from chrono_mail.core import StorageError

# --- Version ------------------------------------------------------------
__version__ = "database.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: R-16: the one copy of opening an index file and of its transactions (was in index/index.py and pimstore/index.py)

MINIMUM_SQLITE_VERSION: tuple[int, int, int] = (3, 43, 0)  # contentless FTS5 tables that allow deletes
_DAMAGED: str = "The index is damaged; rebuild it"


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
        """Make the layout of a new file, or check (and upgrade) the format of an existing one. StorageError when it cannot be used."""

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
        self._connection.execute("COMMIT")

    def check(self) -> bool:
        """Run SQLite's integrity check (slow on a large index)."""
        return self._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
