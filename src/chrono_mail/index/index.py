import os
import re
import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from chrono_mail.core import MessageFlag, StorageError
from chrono_mail.index.model import (
    IndexedMessage,
    IndexEntry,
    SearchFilter,
    SortField,
    flags_from_bits,
    flags_to_bits,
)
from chrono_mail.index.schema import FOLDER_SEPARATOR, MINIMUM_SQLITE_VERSION, SCHEMA_VERSION, STATEMENTS

# --- Version ------------------------------------------------------------
__version__ = "index.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial message index

_COLUMNS: str = "m.store_id, m.folder, m.path, m.flags, m.date, m.size, m.subject, m.sender, m.recipients, m.message_id, m.thread_key, m.has_attachments"
_SORT_COLUMN: dict[SortField, str] = {
    SortField.DATE: "m.date",
    SortField.SUBJECT: "m.subject COLLATE NOCASE",
    SortField.SENDER: "m.sender COLLATE NOCASE",
    SortField.SIZE: "m.size",
}
_FIELD_COLUMN: dict[str, str] = {"from": "sender", "to": "recipients", "subject": "subject", "body": "body"}
_TERM: re.Pattern[str] = re.compile(r'(?:(\w+):)?("[^"]*"?|\S+)')


def _folder_key(path: tuple[str, ...]) -> str:
    return FOLDER_SEPARATOR.join(path)


def _folder_path(key: str) -> tuple[str, ...]:
    return tuple(key.split(FOLDER_SEPARATOR))


def _epoch(moment: datetime | None) -> int | None:
    return None if moment is None else int(moment.astimezone(UTC).timestamp())


def _moment(epoch: int | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC)


def _row(values: tuple[object, ...]) -> IndexedMessage:
    store_id, folder, path, flags, date, size, subject, sender, recipients, message_id, thread_key, attachments = values
    return IndexedMessage(
        str(store_id),
        _folder_path(str(folder)),
        str(path),
        flags_from_bits(int(str(flags))),
        _moment(None if date is None else int(str(date))),
        int(str(size)),
        str(subject),
        str(sender),
        str(recipients),
        None if message_id is None else str(message_id),
        str(thread_key),
        bool(attachments),
    )


def match_expression(query: str) -> str:
    """A safe FTS5 expression for what a person typed. Words match as prefixes, quotes make phrases,
    and from:, to:, subject: and body: limit a term to one field."""
    terms: list[str] = []
    for field, text in _TERM.findall(query.replace("\x00", " ")):
        column = _FIELD_COLUMN.get(field.lower()) if field else None
        if field and column is None:
            text = f"{field}:{text}"  # not a field we know: ordinary text
        phrase = text.startswith('"')
        words = text.strip('"').replace('"', '""').strip()
        if not words:
            continue
        expression = f'"{words}"' if phrase else f'"{words}"*'
        terms.append(f"{column} : {expression}" if column else expression)
    return " ".join(terms)


class Index:
    """The rebuildable list and search index of one account (SQLite). Never the source of truth."""

    def __init__(self, path: Path) -> None:
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
            raise StorageError(f"SQLite {sqlite3.sqlite_version} is too old; 3.43 or newer is needed")
        self.path: Path = path
        try:
            self._connection: sqlite3.Connection = sqlite3.connect(path, isolation_level=None)
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA temp_store = MEMORY")
            if self._connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0:
                self._create_schema()
            else:
                version = self._connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
                if version is None or int(version[0]) != SCHEMA_VERSION:
                    raise StorageError("The index has an unknown format; rebuild it")
        except sqlite3.DatabaseError:
            raise StorageError("The index is damaged; rebuild it") from None

    def _create_schema(self) -> None:
        with self._transaction():
            for statement in STATEMENTS:
                self._connection.execute(statement)
            self._connection.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self) -> Generator[None]:
        self._connection.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # --- changing the index ----------------------------------------------------------------

    def _insert(self, entry: IndexEntry) -> None:
        self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE store_id = ?)", (entry.store_id,))
        self._connection.execute("DELETE FROM messages WHERE store_id = ?", (entry.store_id,))
        cursor = self._connection.execute(
            "INSERT INTO messages (store_id, folder, path, flags, date, size, subject, sender, recipients, message_id, thread_key, has_attachments)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.store_id,
                _folder_key(entry.folder),
                entry.path,
                flags_to_bits(entry.flags),
                _epoch(entry.date),
                entry.size,
                entry.subject,
                entry.sender,
                entry.recipients,
                entry.message_id,
                entry.thread_key,
                int(entry.has_attachments),
            ),
        )
        self._connection.execute(
            "INSERT INTO text (rowid, subject, sender, recipients, body) VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, entry.subject, entry.sender, entry.recipients, entry.body),
        )

    def add_many(self, entries: Iterable[IndexEntry]) -> int:
        """Add (or replace) messages in one transaction. Returns how many."""
        count = 0
        try:
            with self._transaction():
                for entry in entries:
                    self._insert(entry)
                    count += 1
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None
        return count

    def add(self, entry: IndexEntry) -> None:
        self.add_many([entry])

    def remove(self, store_id: str) -> None:
        try:
            with self._transaction():
                self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE store_id = ?)", (store_id,))
                self._connection.execute("DELETE FROM messages WHERE store_id = ?", (store_id,))
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def remove_folder(self, folder: tuple[str, ...]) -> int:
        """Forget every message of a folder (the folder itself has been removed from the store)."""
        try:
            with self._transaction():
                self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE folder = ?)", (_folder_key(folder),))
                cursor = self._connection.execute("DELETE FROM messages WHERE folder = ?", (_folder_key(folder),))
                return cursor.rowcount
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def update_flags(self, store_id: str, flags: frozenset[MessageFlag], path: str) -> None:
        self._update("UPDATE messages SET flags = ?, path = ? WHERE store_id = ?", (flags_to_bits(flags), path, store_id))

    def move(self, store_id: str, folder: tuple[str, ...], path: str) -> None:
        self._update("UPDATE messages SET folder = ?, path = ? WHERE store_id = ?", (_folder_key(folder), path, store_id))

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        try:
            with self._transaction():
                for (key,) in self._connection.execute("SELECT DISTINCT folder FROM messages").fetchall():
                    path = _folder_path(key)
                    if path[: len(old)] == old:
                        self._connection.execute(
                            "UPDATE messages SET folder = ? WHERE folder = ?", (_folder_key(new + path[len(old) :]), key)
                        )
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def _update(self, statement: str, parameters: tuple[object, ...]) -> None:
        try:
            with self._transaction():
                self._connection.execute(statement, parameters)
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    # --- asking the index -------------------------------------------------------------------

    def message_count(self) -> int:
        return int(self._connection.execute("SELECT count(*) FROM messages").fetchone()[0])

    def all_store_ids(self) -> set[str]:
        return {str(row[0]) for row in self._connection.execute("SELECT store_id FROM messages")}

    def snapshot(self) -> dict[str, tuple[tuple[str, ...], str]]:
        """store id -> (folder, relative path) for every message, to compare with the store."""
        return {
            str(store_id): (_folder_path(str(folder)), str(path))
            for store_id, folder, path in self._connection.execute("SELECT store_id, folder, path FROM messages")
        }

    def get(self, store_id: str) -> IndexedMessage | None:
        row = self._connection.execute(f"SELECT {_COLUMNS} FROM messages m WHERE m.store_id = ?", (store_id,)).fetchone()
        return None if row is None else _row(row)

    def count(self, folder: tuple[str, ...], unread_only: bool = False) -> int:
        sql = "SELECT count(*) FROM messages m WHERE m.folder = ?" + (" AND (m.flags & 1) = 0" if unread_only else "")
        return int(self._connection.execute(sql, (_folder_key(folder),)).fetchone()[0])

    def page(
        self,
        folder: tuple[str, ...],
        sort: SortField = SortField.DATE,
        descending: bool = True,
        offset: int = 0,
        limit: int = 100,
        unread_only: bool = False,
    ) -> list[IndexedMessage]:
        """One screenful of a folder, in the chosen order."""
        direction = "DESC" if descending else "ASC"
        sql = (
            f"SELECT {_COLUMNS} FROM messages m WHERE m.folder = ?"
            + (" AND (m.flags & 1) = 0" if unread_only else "")
            + f" ORDER BY {_SORT_COLUMN[sort]} {direction}, m.id {direction} LIMIT ? OFFSET ?"
        )
        rows = self._connection.execute(sql, (_folder_key(folder), max(0, limit), max(0, offset))).fetchall()
        return [_row(row) for row in rows]

    def thread_members(self, thread_key: str) -> list[IndexedMessage]:
        rows = self._connection.execute(
            f"SELECT {_COLUMNS} FROM messages m WHERE m.thread_key = ? ORDER BY m.date, m.id", (thread_key,)
        ).fetchall()
        return [_row(row) for row in rows]

    def search(
        self,
        query: str,
        folder: tuple[str, ...] | None = None,
        filters: SearchFilter = SearchFilter(),
        offset: int = 0,
        limit: int = 100,
    ) -> list[IndexedMessage]:
        """Messages matching the words and the filters, newest first. An empty query lists by filters only."""
        conditions: list[str] = []
        parameters: list[object] = []
        source = "messages m"
        expression = match_expression(query)
        if expression:
            source = "text t JOIN messages m ON m.id = t.rowid"
            conditions.append("t.text MATCH ?")
            parameters.append(expression)
        # With a text query the words must be looked up first. A bare "m.folder = ?" or "m.date >= ?" lets SQLite walk
        # the folder's date index instead and probe the full-text table once per row (measured: 4 seconds instead of
        # 0.1 second with 300,000 hits). The unary plus on a column stops SQLite from using an index for that condition.
        steer = "+" if expression else ""
        if folder is not None:
            conditions.append(f"{steer}m.folder = ?")
            parameters.append(_folder_key(folder))
        if filters.unread is not None:
            conditions.append("(m.flags & 1) = 0" if filters.unread else "(m.flags & 1) != 0")
        if filters.flagged is not None:
            conditions.append("(m.flags & 4) != 0" if filters.flagged else "(m.flags & 4) = 0")
        if filters.has_attachments is not None:
            conditions.append("m.has_attachments = ?")
            parameters.append(int(filters.has_attachments))
        if filters.date_from is not None:
            conditions.append(f"{steer}m.date >= ?")
            parameters.append(_epoch(filters.date_from))
        if filters.date_to is not None:
            conditions.append(f"{steer}m.date <= ?")
            parameters.append(_epoch(filters.date_to))
        if filters.minimum_size is not None:
            conditions.append("m.size >= ?")
            parameters.append(filters.minimum_size)
        if filters.maximum_size is not None:
            conditions.append("m.size <= ?")
            parameters.append(filters.maximum_size)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT {_COLUMNS} FROM {source}{where} ORDER BY m.date DESC, m.id DESC LIMIT ? OFFSET ?"
        parameters.extend([max(0, limit), max(0, offset)])
        try:
            return [_row(row) for row in self._connection.execute(sql, parameters).fetchall()]
        except sqlite3.OperationalError as error:
            raise StorageError(f"The search could not be run: {error}") from None

    def check(self) -> bool:
        """Run SQLite's integrity check (slow on a large index)."""
        return self._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


class IndexBuilder:
    """Builds a complete new index next to the old one and swaps it in with one rename."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self._building: Path = path.with_name(path.name + ".new")
        for leftover in (self._building, Path(f"{self._building}-wal"), Path(f"{self._building}-shm")):
            leftover.unlink(missing_ok=True)
        self.index: Index = Index(self._building)

    def add_many(self, entries: Iterable[IndexEntry]) -> int:
        return self.index.add_many(entries)

    def finish(self) -> Index:
        """Make the new index the real one. The old index is replaced in a single step."""
        self.index._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # noqa: SLF001
        self.index.close()
        for suffix in ("-wal", "-shm"):
            Path(f"{self._building}{suffix}").unlink(missing_ok=True)
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        os.replace(self._building, self.path)
        return Index(self.path)

    def abandon(self) -> None:
        self.index.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self._building}{suffix}").unlink(missing_ok=True)
