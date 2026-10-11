import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.pimstore.extract import Extract, tag_text
from chrono_mail.pimstore.schema import MINIMUM_SQLITE_VERSION, SCHEMA_VERSION, STATEMENTS

# --- Version ------------------------------------------------------------
__version__ = "index.py 0.0.62"

# --- Version history ----------------------------------------------------
# v0.0.62: The contacts section
# v0.0.60: Commands for collections, tags, contacts, events and tasks
# v0.0.59: Initial index of calendars, tasks and contacts

_WORD = re.compile(r"\w+", re.UNICODE)
FLOATING_MARGIN: int = 86_400  # wall-clock times are kept as if UTC: a range is widened by a day each way, the exact filtering is done on the real values


@dataclass(frozen=True, slots=True)
class IndexedItem:
    item_id: str
    collection: str
    kind: CollectionKind
    uid: str
    title: str
    subtitle: str
    sort_key: str
    start: int | None
    end: int | None
    due: int | None
    floating: bool
    repeats: bool
    until: int | None
    status: str
    done: bool
    priority: int
    percent: int
    tags: tuple[str, ...]
    size: int


_COLUMNS = "i.item_id, i.collection, i.kind, i.uid, i.title, i.subtitle, i.sort_key, i.start, i.end, i.due, i.floating, i.repeats, i.until, i.status, i.done, i.priority, i.percent, i.tags, i.size"


def _item(row: tuple[object, ...]) -> IndexedItem:
    tags = tuple(part for part in str(row[17]).split("\x1f") if part)
    return IndexedItem(
        str(row[0]), str(row[1]), CollectionKind(str(row[2])), str(row[3]), str(row[4]), str(row[5]), str(row[6]), _number(row[7]), _number(row[8]), _number(row[9]),
        bool(row[10]), bool(row[11]), _number(row[12]), str(row[13]), bool(row[14]), int(str(row[15])), int(str(row[16])), tags, int(str(row[18])),
    )


def _number(value: object) -> int | None:
    return int(str(value)) if value is not None else None


def match_expression(text: str) -> str:
    """A full-text expression for the words in a text: every word is a prefix (typing 'rey' finds Reynolds)."""
    words = _WORD.findall(text)
    return " ".join(f'"{word}"*' for word in words)


@dataclass(frozen=True, slots=True)
class ItemQuery:
    kind: CollectionKind | None = None
    collections: tuple[str, ...] = ()
    text: str = ""
    tag: str = ""
    start: int | None = None  # events that happen inside [start, end) (floating times need a margin: see the engine)
    end: int | None = None
    only_open: bool = False  # tasks that are not done
    only_done: bool = False
    due_before: int | None = None
    sort: str = "key"  # key: the sort key, due, start
    descending: bool = False
    limit: int = 1000
    offset: int = 0


class PimIndex:
    """The rebuildable index of the items of one account (SQLite). Never the source of truth."""

    def __init__(self, path: Path) -> None:
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
            raise StorageError(f"SQLite {sqlite3.sqlite_version} is too old; 3.43 or newer is needed")
        self.path: Path = path
        try:
            self._connection: sqlite3.Connection = sqlite3.connect(path, isolation_level=None, timeout=30)
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            if self._connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0:
                with self._transaction():
                    for statement in STATEMENTS:
                        self._connection.execute(statement)
                    self._connection.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            else:
                version = self._connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
                if version is None or int(version[0]) != SCHEMA_VERSION:
                    raise StorageError("The index has an unknown format; rebuild it")
        except sqlite3.DatabaseError:
            raise StorageError("The index is damaged; rebuild it") from None

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self) -> Generator[None]:
        self._connection.execute("BEGIN IMMEDIATE")  # the write lock at once: two commands changing items at the same time wait for each other (30 s) instead of failing as "locked"
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # --- changing ------------------------------------------------------------------------------

    def upsert(self, item_id: str, collection: str, kind: CollectionKind, extract: Extract, size: int) -> None:
        with self._transaction():
            self._remove(item_id)
            cursor = self._connection.execute(
                "INSERT INTO items (item_id, collection, kind, uid, title, subtitle, sort_key, start, end, due, floating, repeats, until, status, done, priority, percent, tags, tags_folded, size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id, collection, kind.value, extract.uid, extract.title, extract.subtitle, extract.sort_key, extract.start, extract.end, extract.due, int(extract.floating),
                    int(extract.repeats), extract.until, extract.status, int(extract.done), extract.priority, extract.percent, tag_text(extract.tags), tag_text(extract.tags, True), size,
                ),
            )
            self._connection.execute("INSERT INTO text (rowid, title, body) VALUES (?, ?, ?)", (cursor.lastrowid, extract.title, extract.text))

    def _remove(self, item_id: str) -> None:
        row = self._connection.execute("SELECT id FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if row is not None:
            self._connection.execute("DELETE FROM text WHERE rowid = ?", (row[0],))
            self._connection.execute("DELETE FROM items WHERE id = ?", (row[0],))

    def remove(self, item_id: str) -> None:
        with self._transaction():
            self._remove(item_id)

    def remove_collection(self, collection: str) -> None:
        with self._transaction():
            for (item_id,) in self._connection.execute("SELECT item_id FROM items WHERE collection = ?", (collection,)).fetchall():
                self._remove(str(item_id))

    def clear(self) -> None:
        with self._transaction():
            self._connection.execute("DELETE FROM text")
            self._connection.execute("DELETE FROM items")

    # --- asking --------------------------------------------------------------------------------

    def item_ids(self, collection: str | None = None) -> set[str]:
        if collection is None:
            rows = self._connection.execute("SELECT item_id FROM items").fetchall()
        else:
            rows = self._connection.execute("SELECT item_id FROM items WHERE collection = ?", (collection,)).fetchall()
        return {str(row[0]) for row in rows}

    def get(self, item_id: str) -> IndexedItem | None:
        row = self._connection.execute(f"SELECT {_COLUMNS} FROM items i WHERE i.item_id = ?", (item_id,)).fetchone()
        return _item(row) if row is not None else None

    def find_uid(self, collection: str, uid: str) -> IndexedItem | None:
        row = self._connection.execute(f"SELECT {_COLUMNS} FROM items i WHERE i.collection = ? AND i.uid = ?", (collection, uid)).fetchone()
        return _item(row) if row is not None else None

    def count(self, query: ItemQuery) -> int:
        statement, parameters = self._statement(query, "count(*)", with_order=False)
        return int(self._connection.execute(statement, parameters).fetchone()[0])

    def query(self, query: ItemQuery) -> list[IndexedItem]:
        statement, parameters = self._statement(query, _COLUMNS, with_order=True)
        return [_item(row) for row in self._connection.execute(statement, parameters).fetchall()]

    def _statement(self, query: ItemQuery, columns: str, with_order: bool) -> tuple[str, list[object]]:
        source = "items i"
        conditions: list[str] = []
        parameters: list[object] = []
        expression = match_expression(query.text)
        if expression:
            source = "text t JOIN items i ON i.id = t.rowid"
            conditions.append("t.text MATCH ?")
            parameters.append(expression)
        if query.kind is not None:
            conditions.append("i.kind = ?")
            parameters.append(query.kind.value)
        if query.collections:
            conditions.append("i.collection IN (" + ",".join("?" * len(query.collections)) + ")")
            parameters.extend(query.collections)
        if query.tag:
            conditions.append("i.tags_folded LIKE ? ESCAPE '\\'")
            escaped = query.tag.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.append(f"%\x1f{escaped}\x1f%")
        if query.only_open:
            conditions.append("i.done = 0")
        if query.only_done:
            conditions.append("i.done = 1")
        if query.due_before is not None:
            conditions.append("i.due IS NOT NULL AND i.due < ?")
            parameters.append(query.due_before + FLOATING_MARGIN)
        if query.start is not None and query.end is not None:
            conditions.append(
                "((i.repeats = 0 AND i.start < ? AND COALESCE(i.end, i.start) >= ?) OR (i.repeats = 1 AND i.start < ? AND (i.until IS NULL OR i.until >= ?)))"
            )
            parameters.extend([query.end + FLOATING_MARGIN, query.start - FLOATING_MARGIN, query.end + FLOATING_MARGIN, query.start - FLOATING_MARGIN])
        statement = f"SELECT {columns} FROM {source}" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        if with_order:
            column = {"key": "i.sort_key", "due": "i.due IS NULL, i.due", "start": "i.start"}.get(query.sort, "i.sort_key")
            direction = "DESC" if query.descending else "ASC"
            statement += f" ORDER BY {column} {direction}, i.id LIMIT ? OFFSET ?"
            parameters.extend([max(0, query.limit), max(0, query.offset)])
        return statement, parameters

    def tags(self, kind: CollectionKind | None = None) -> dict[str, tuple[str, int]]:
        """Every tag in use, by its lower-case name: as it was first written, and how many items carry it."""
        counts: dict[str, tuple[str, int]] = {}
        rows = self._connection.execute("SELECT tags FROM items WHERE tags != ''" + (" AND kind = ?" if kind is not None else ""), (kind.value,) if kind is not None else ()).fetchall()
        for (text,) in rows:
            for tag in str(text).split("\x1f"):
                if tag:
                    shown, number = counts.get(tag.casefold(), (tag, 0))
                    counts[tag.casefold()] = (shown, number + 1)
        return counts

    def check(self) -> bool:
        return self._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
