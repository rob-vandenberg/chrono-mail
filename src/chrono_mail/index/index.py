import os
import re
import sqlite3
from dataclasses import dataclass
from email.utils import getaddresses
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

from chrono_mail.core import MessageFlag, StorageError, shown_name
from chrono_mail.index.database import IndexDatabase
from chrono_mail.index.model import (
    DATE_GROUPS,
    SIZE_GROUPS,
    DateBounds,
    GroupCount,
    IndexedMessage,
    IndexEntry,
    KnownAddress,
    SearchFilter,
    YES_NO_SORTS,
    SortField,
    flags_from_bits,
    flags_to_bits,
)
from chrono_mail.index.schema import (
    FOLDER_SEPARATOR,
    SCHEMA_VERSION,
    SENT_FILL_KEY,
    STATEMENTS,
    UPGRADE_FROM_11,
)

# --- Version ------------------------------------------------------------
__version__ = "index.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: a stored moment before 1970 is read by adding seconds (Windows refused a negative time, so its folder could not be listed, RV-36);
#           moments are counted without converting them (RV-34)
# v2.5.335: a new file is made, and an old format upgraded, only after the format is read again under the write lock: a second
#           connection opening the same file at that moment took it for damaged (D-247)
# v2.1.308: wrote_to: whether a message of a folder (Sent) names an address among its recipients (the junk filter's trusted senders, D-238)
# v2.0.301: format 12 is upgraded in place too (format 13, D-235): the filling in reads has_attachments with sent (fill_sent)
# v2.0.281: format 11 is upgraded in place (sent added, filled in afterwards: sent_filling, sent_to_fill, fill_sent); when_closed: what an index
#           opened by the engine lets go when it closes (D-229)
# v2.0.280: sent, the message's own Date: kept, read with every row, sorted by with the date groups (the Sent column, B-16); format 11 is rebuilt
# v1.9.276: format 10 is no longer current: its date column held the sender's Date, so it is rebuilt (D-226)
# v1.8.263: R-16: opening the file and the transactions come from IndexDatabase (index/database.py), shared with the calendar index
# v1.6.260: ordered(): every message of a folder or a search in its order (id, folder, unread), without reading the rows; tally(): how many
#           and how many unread; messages(): the rows of named messages (R-15)
# v1.1.250: numbers(): a folder's messages and unread messages, one lookup in the table the database keeps (schema 10; format 9 is upgraded in place, R-10)
# v1.1.242: also_tag: a second tag, a mark like the first (a message untagged while shown stays, R-03)
# v1.1.196: Schema 9: the send time of each scheduled message (scheduled(), due()); format 8 is upgraded in place and the engine fills in the Outbox (set_send_times)
# v1.1.191: the replied filter (a reply marks it, so it stays while the view shows it, as unread and flagged do)
# v0.5.166: sorting and grouping by sender use the name the From column shows (sender_name); format 7 is upgraded in place (D-153)
# v0.5.162: a search filter can name messages that stay although they no longer match unread, flagged or the tag
# v0.5.160: positions: where several messages stand in a folder in the chosen order, in one pass
# v0.5.153: position: where a message stands in a folder in the chosen order
# v0.5.134: Tags: kept per message, found with a filter or tag: in a search; an index of format 6 is upgraded in place
# v0.5.130: A failed open closes its connection (Windows could not delete the -wal file for the rebuild); an unreadable format number counts as an unknown format
# v0.3.110: A change takes the write lock at its start (BEGIN IMMEDIATE), so changes made at the same time wait for each other instead of failing
# v0.3.109: Sorting by unread, flagged, attachment, replied, folder, messages in conversation and preview (newest first within the same value); groups by folder, conversation size and preview
# v0.2.94: Sorting by recipients; a search in any sort order; the groups of a folder or a search (group headers)
# v0.0.23: addresses seen in messages, suggest
# v0.0.19: OR and NOT (and a minus) in search words
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.13: preview, unread index, busy timeout
# v0.0.8: Initial message index

_COLUMNS: str = "m.store_id, m.folder, m.path, m.flags, m.date, m.size, m.subject, m.sender, m.recipients, m.message_id, m.thread_key, m.has_attachments, m.preview, m.tags, m.sent"
_SORT_COLUMN: dict[SortField, str] = {
    SortField.DATE: "m.date",
    SortField.SENT: "m.sent",
    SortField.SUBJECT: "m.subject COLLATE NOCASE",
    SortField.SENDER: "m.sender_name COLLATE NOCASE",  # the sender as the From column shows it
    SortField.SIZE: "m.size",
    SortField.RECIPIENTS: "m.recipients COLLATE NOCASE",
    SortField.UNREAD: "(m.flags & 1)",  # 0 for unread, so that SQLite seeks it for unread counts too; _order turns its direction round
    SortField.FLAGGED: "((m.flags & 4) <> 0)",  # the other yes/no keys are 1 for yes: the newest-first direction puts yes on top
    SortField.REPLIED: "((m.flags & 2) <> 0)",
    SortField.ATTACHMENT: "m.has_attachments",
    SortField.FOLDER: "m.folder COLLATE NOCASE",
    SortField.CONVERSATION: "(SELECT count(*) FROM messages c WHERE c.thread_key = m.thread_key)",
    SortField.PREVIEW: "m.preview COLLATE NOCASE",
}
_NEWEST_WITHIN: frozenset[SortField] = YES_NO_SORTS | {SortField.FOLDER, SortField.CONVERSATION, SortField.PREVIEW}  # messages with the same value: newest first, either way
_TEXT_GROUP_COLUMN: dict[SortField, str] = {
    SortField.SUBJECT: "m.subject", SortField.SENDER: "m.sender_name", SortField.RECIPIENTS: "m.recipients", SortField.FOLDER: "m.folder", SortField.PREVIEW: "m.preview",
}
_FIELD_COLUMN: dict[str, str] = {"from": "sender", "to": "recipients", "subject": "subject", "body": "body"}
_TERM: re.Pattern[str] = re.compile(r'(-?)(?:(\w+):)?("[^"]*"?|\S+)')
_TAG_TERM: re.Pattern[str] = re.compile(r'(?i)(?<!\S)(-?)tag:("[^"]*"?|\S+)')
TAG_SEPARATOR: str = "\x1f"


def _tags_text(tags: tuple[str, ...]) -> str:
    return TAG_SEPARATOR.join(tags)


def split_tag_terms(query: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """tag:name, tag:"two words" and -tag:name taken out of what a person typed: (the rest, wanted tags, excluded tags)."""
    wanted: list[str] = []
    excluded: list[str] = []
    for minus, name in _TAG_TERM.findall(query):
        cleaned = " ".join(name.strip('"').split())
        if cleaned:
            (excluded if minus else wanted).append(cleaned)
    return _TAG_TERM.sub(" ", query), tuple(wanted), tuple(excluded)


def _order(sort: SortField, descending: bool) -> str:
    """The ORDER BY of a list or a search."""
    direction = "DESC" if descending != (sort is SortField.UNREAD) else "ASC"
    if sort in _NEWEST_WITHIN:
        return f"{_SORT_COLUMN[sort]} {direction}, m.date DESC, m.id DESC"
    return f"{_SORT_COLUMN[sort]} {direction}, m.id {direction}"


def _folder_key(path: tuple[str, ...]) -> str:
    return FOLDER_SEPARATOR.join(path)


def _folder_path(key: str) -> tuple[str, ...]:
    return tuple(key.split(FOLDER_SEPARATOR))


_EPOCH: datetime = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch(moment: datetime | None) -> int | None:
    return None if moment is None else (moment - _EPOCH) // timedelta(seconds=1)


def _moment(epoch: int | None) -> datetime | None:
    """A stored moment; before 1970 too (fromtimestamp refuses negative values on Windows, RV-36); None when out of reach."""
    if epoch is None:
        return None
    try:
        return _EPOCH + timedelta(seconds=epoch)
    except OverflowError:
        return None


def _row(values: tuple[object, ...]) -> IndexedMessage:
    store_id, folder, path, flags, date, size, subject, sender, recipients, message_id, thread_key, attachments, preview, tags, sent = values
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
        str(preview),
        tuple(tag for tag in str(tags).split(TAG_SEPARATOR) if tag),
        _moment(None if sent is None else int(str(sent))),
    )


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """What a person typed, as two full-text expressions: what must match and what must not."""

    positive: str = ""
    negative: str = ""


def parse_query(query: str) -> ParsedQuery:
    """A safe pair of FTS5 expressions for what a person typed.

    Words match as prefixes, quotes make phrases, and from:, to:, subject: and body: limit a term to one field.
    OR (capitals, or |) between terms gives a choice; terms next to each other all have to match, and that binds
    tighter than OR: "a b OR c" is (a and b) or c. NOT before a term, or a minus in front of it ("-spam",
    "-from:bob", -"a phrase"), excludes the messages that contain it; exclusions apply to the whole search.
    """
    groups: list[list[str]] = [[]]
    excluded: list[str] = []
    negate_next = False
    for minus, field, text in _TERM.findall(query.replace("\x00", " ")):
        if not field and text in ("OR", "|"):
            if groups[-1]:
                groups.append([])
            negate_next = False
            continue
        if not field and text == "NOT":
            negate_next = True
            continue
        negated = negate_next or bool(minus)
        negate_next = False
        column = _FIELD_COLUMN.get(field.lower()) if field else None
        if field and column is None:
            text = f"{field}:{text}"  # not a field we know: ordinary text
        phrase = text.startswith('"')
        words = text.strip('"').replace('"', '""').strip()
        if not words:
            continue
        expression = f'"{words}"' if phrase else f'"{words}"*'
        term = f"{column} : {expression}" if column else expression
        if negated:
            excluded.append(term)
        else:
            groups[-1].append(term)
    positive = " OR ".join(" ".join(group) for group in groups if group)
    return ParsedQuery(positive, " OR ".join(excluded))


def match_expression(query: str) -> str:
    """The expression for the words that must match (see parse_query)."""
    return parse_query(query).positive


class Index(IndexDatabase):
    """The rebuildable list and search index of one account (SQLite). Never the source of truth."""

    _released: Callable[[], None] | None = None  # let go of when closed (when_closed)

    @override
    def _prepare(self) -> None:
        self._connection.execute("PRAGMA temp_store = MEMORY")
        if self._format() == str(SCHEMA_VERSION):
            return  # the usual case: nothing to make, no lock taken
        with self._transaction():  # looked at again under the write lock: another connection may have made or upgraded it meanwhile (D-247)
            found = self._format()
            if found is None:
                self._create_schema()
            elif found in ("11", "12"):
                self._upgrade_in_place(found)
            elif found != str(SCHEMA_VERSION):
                # An older format is rebuilt from the messages (format 11 changed what date holds for every message, D-226), a newer one too
                raise StorageError("The index has an unknown format; rebuild it")

    def _upgrade_in_place(self, version: str) -> None:
        """Format 12 added the moment each message carries (sent), format 13 the attachments by what the text shows (D-235): the index
        stays usable at once, both are filled in afterwards (sent_to_fill, fill_sent), from where it stopped (D-229). Inside _prepare's transaction."""
        for statement in UPGRADE_FROM_11 if version == "11" else ():
            self._connection.execute(statement)
        self._connection.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (str(SCHEMA_VERSION),))
        self._connection.execute("INSERT INTO meta VALUES (?, '0')", (SENT_FILL_KEY,))

    def when_closed(self, released: Callable[[], None]) -> None:
        """Something to let go of when this index is closed (the engine's hold on the file, so it is never replaced while open)."""
        self._released = released

    @override
    def close(self) -> None:
        super().close()
        released, self._released = self._released, None
        if released is not None:
            released()

    def sent_filling(self) -> tuple[int, int] | None:
        """After an upgrade in place: how many messages have their sent moment and attachments read, and how many there are; None when all have."""
        filled_to = self._connection.execute("SELECT value FROM meta WHERE key = ?", (SENT_FILL_KEY,)).fetchone()
        if filled_to is None:
            return None
        done, total = self._connection.execute("SELECT count(*) FILTER (WHERE id <= ?), count(*) FROM messages", (int(str(filled_to[0])),)).fetchone()
        return int(done), int(total)

    def sent_to_fill(self, limit: int) -> list[tuple[int, str, tuple[str, ...], str]]:
        """The next messages whose sent moment and attachments are still to be read: (row id, store id, folder, path), in the order of their row ids."""
        filled_to = self._connection.execute("SELECT value FROM meta WHERE key = ?", (SENT_FILL_KEY,)).fetchone()
        if filled_to is None:
            return []
        rows = self._connection.execute(
            "SELECT id, store_id, folder, path FROM messages WHERE id > ? ORDER BY id LIMIT ?", (int(str(filled_to[0])), limit)
        ).fetchall()
        return [(int(row_id), str(store_id), _folder_path(str(folder)), str(path)) for row_id, store_id, folder, path in rows]

    def fill_sent(self, moments: Sequence[tuple[int, datetime | None, bool | None]], filled_to: int, finished: bool) -> None:
        """The sent moments and attachments of these rows (None: the message could not be read, its attachments stay as they were), and how
        far the filling has come (finished: every message has them now), in one step."""
        with self._transaction():
            self._connection.executemany(
                "UPDATE messages SET sent = ?, has_attachments = COALESCE(?, has_attachments) WHERE id = ?",
                [(_epoch(moment), None if attachments is None else int(attachments), row_id) for row_id, moment, attachments in moments],
            )
            if finished:
                self._connection.execute("DELETE FROM meta WHERE key = ?", (SENT_FILL_KEY,))
            else:
                self._connection.execute("UPDATE meta SET value = ? WHERE key = ?", (str(filled_to), SENT_FILL_KEY))

    def _create_schema(self) -> None:
        """Inside _prepare's transaction."""
        for statement in STATEMENTS:
            self._connection.execute(statement)
        self._connection.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))

    # --- changing the index ----------------------------------------------------------------

    def _insert(self, entry: IndexEntry) -> None:
        self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE store_id = ?)", (entry.store_id,))
        replaced = self._connection.execute("DELETE FROM messages WHERE store_id = ?", (entry.store_id,)).rowcount
        cursor = self._connection.execute(
            "INSERT INTO messages (store_id, folder, path, flags, date, size, subject, sender, recipients, message_id, thread_key, has_attachments, preview, tags, sender_name, send_at, sent)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                entry.preview,
                _tags_text(entry.tags),
                shown_name(entry.sender),
                entry.send_at,
                _epoch(entry.sent),
            ),
        )
        self._write_tags(entry.store_id, entry.tags)
        self._connection.execute(
            "INSERT INTO text (rowid, subject, sender, recipients, body) VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, entry.subject, entry.sender, entry.recipients, entry.body),
        )
        if not replaced:  # a message that is added again must not count twice
            for name, address, recipient in entry.people:
                self._connection.execute(
                    "INSERT INTO addresses (address, name, as_sender, as_recipient, last) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (address) DO UPDATE SET"
                    " name = CASE WHEN excluded.name != '' AND (addresses.name = '' OR COALESCE(excluded.last, 0) >= COALESCE(addresses.last, 0)) THEN excluded.name ELSE addresses.name END,"
                    " as_sender = addresses.as_sender + excluded.as_sender, as_recipient = addresses.as_recipient + excluded.as_recipient,"
                    " last = MAX(COALESCE(addresses.last, 0), COALESCE(excluded.last, 0))",
                    (address, name, 0 if recipient else 1, 1 if recipient else 0, _epoch(entry.date)),
                )

    def _write_tags(self, store_id: str, tags: tuple[str, ...]) -> None:
        self._connection.execute("DELETE FROM message_tags WHERE store_id = ?", (store_id,))
        self._connection.executemany("INSERT INTO message_tags (store_id, tag) VALUES (?, ?)", [(store_id, tag.casefold()) for tag in tags])

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

    # --- scheduled messages ----------------------------------------------------------------

    def set_send_times(self, times: Mapping[str, int | None]) -> None:
        """The send time of these messages (store id -> moment, None: not scheduled), all in one step."""
        try:
            with self._transaction():
                self._connection.executemany("UPDATE messages SET send_at = ? WHERE store_id = ?", [(moment, store_id) for store_id, moment in times.items()])
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def scheduled(self) -> list[tuple[str, tuple[str, ...], int]]:
        """Every message that has a send time: (store id, folder, moment), soonest first. A message that could not be read
        has moment 0 (UNKNOWN_SEND_TIME). Only these rows are read (a partial index holds them), never the whole folder."""
        rows = self._connection.execute("SELECT store_id, folder, send_at FROM messages WHERE send_at IS NOT NULL ORDER BY send_at, id").fetchall()
        return [(str(store_id), _folder_path(str(folder)), int(moment)) for store_id, folder, moment in rows]

    def remove(self, store_id: str) -> None:
        try:
            with self._transaction():
                self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE store_id = ?)", (store_id,))
                self._connection.execute("DELETE FROM messages WHERE store_id = ?", (store_id,))
                self._connection.execute("DELETE FROM message_tags WHERE store_id = ?", (store_id,))
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def remove_folder(self, folder: tuple[str, ...]) -> int:
        """Forget every message of a folder (the folder itself has been removed from the store)."""
        try:
            with self._transaction():
                self._connection.execute("DELETE FROM text WHERE rowid IN (SELECT id FROM messages WHERE folder = ?)", (_folder_key(folder),))
                self._connection.execute("DELETE FROM message_tags WHERE store_id IN (SELECT store_id FROM messages WHERE folder = ?)", (_folder_key(folder),))
                cursor = self._connection.execute("DELETE FROM messages WHERE folder = ?", (_folder_key(folder),))
                return cursor.rowcount
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def update_flags(self, store_id: str, flags: frozenset[MessageFlag], path: str) -> None:
        self._update("UPDATE messages SET flags = ?, path = ? WHERE store_id = ?", (flags_to_bits(flags), path, store_id))

    def update_tags(self, store_id: str, tags: tuple[str, ...]) -> None:
        try:
            with self._transaction():
                self._connection.execute("UPDATE messages SET tags = ? WHERE store_id = ?", (_tags_text(tags), store_id))
                self._write_tags(store_id, tags)
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be updated: {type(error).__name__}") from None

    def tagged(self) -> set[str]:
        """The store ids of the messages that have tags here."""
        return {str(row[0]) for row in self._connection.execute("SELECT DISTINCT store_id FROM message_tags")}

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

    def numbers(self, folder: tuple[str, ...]) -> tuple[int, int]:
        """A folder's messages and unread messages: one lookup in the numbers the database keeps with every change (R-10)."""
        try:
            found = self._connection.execute("SELECT messages, unread FROM folder_numbers WHERE folder = ?", (_folder_key(folder),)).fetchone()
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be read: {error}") from None
        return (0, 0) if found is None else (max(0, int(found[0])), max(0, int(found[1])))

    def count(self, folder: tuple[str, ...], unread_only: bool = False) -> int:
        sql = "SELECT count(*) FROM messages m WHERE m.folder = ?" + (" AND (m.flags & 1) = 0" if unread_only else "")
        return int(self._connection.execute(sql, (_folder_key(folder),)).fetchone()[0])

    def position(self, folder: tuple[str, ...], store_id: str, sort: SortField = SortField.DATE, descending: bool = True, unread_only: bool = False) -> int | None:
        """Where a message stands in a folder in the chosen order, as page() lists it (0 is the first), or None when it is not there."""
        sql = (
            f"SELECT place FROM (SELECT m.store_id AS id, ROW_NUMBER() OVER (ORDER BY {_order(sort, descending)}) - 1 AS place FROM messages m WHERE m.folder = ?"
            + (" AND (m.flags & 1) = 0" if unread_only else "")
            + ") WHERE id = ?"
        )
        found = self._connection.execute(sql, (_folder_key(folder), store_id)).fetchone()
        return None if found is None else int(found[0])

    def positions(self, folder: tuple[str, ...], store_ids: Iterable[str], sort: SortField = SortField.DATE, descending: bool = True) -> dict[str, int]:
        """Where each of these messages stands in a folder in the chosen order, as page() lists it (0 is the first), worked
        out in one pass over the folder (one per 500 messages asked); a message that is not there is left out."""
        wanted = list(dict.fromkeys(store_ids))
        found: dict[str, int] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            sql = (
                f"SELECT id, place FROM (SELECT m.store_id AS id, ROW_NUMBER() OVER (ORDER BY {_order(sort, descending)}) - 1 AS place FROM messages m WHERE m.folder = ?)"
                f" WHERE id IN ({', '.join('?' * len(chunk))})"
            )
            for store_id, place in self._connection.execute(sql, (_folder_key(folder), *chunk)):
                found[str(store_id)] = int(place)
        return found

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
        sql = (
            f"SELECT {_COLUMNS} FROM messages m WHERE m.folder = ?"
            + (" AND (m.flags & 1) = 0" if unread_only else "")
            + f" ORDER BY {_order(sort, descending)} LIMIT ? OFFSET ?"
        )
        rows = self._connection.execute(sql, (_folder_key(folder), max(0, limit), max(0, offset))).fetchall()
        return [_row(row) for row in rows]

    def thread_members(self, thread_key: str) -> list[IndexedMessage]:
        rows = self._connection.execute(
            f"SELECT {_COLUMNS} FROM messages m WHERE m.thread_key = ? ORDER BY m.date, m.id", (thread_key,)
        ).fetchall()
        return [_row(row) for row in rows]

    def suggest(self, text: str, limit: int = 8) -> list[KnownAddress]:
        """Addresses whose name or address contains the text, the ones written to most often first (a start of a word first of all)."""
        needle = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not needle:
            return []
        rows = self._connection.execute(
            "SELECT name, address, as_sender, as_recipient FROM addresses"
            " WHERE address LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\'"
            " ORDER BY CASE WHEN address LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,"
            " as_recipient * 3 + as_sender DESC, last DESC, address LIMIT ?",
            (f"%{needle}%", f"%{needle}%", f"{needle}%", f"{needle}%", f"% {needle}%", max(0, limit)),
        ).fetchall()
        return [KnownAddress(str(name), str(address), int(sender), int(recipient)) for name, address, sender, recipient in rows]

    def wrote_to(self, folder: tuple[str, ...], address: str) -> bool:
        """Whether a message of this folder (the Sent folder) names this address among its recipients, exactly."""
        wanted = address.strip().casefold()
        if not wanted:
            return False
        needle = wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            rows = self._connection.execute(
                "SELECT recipients FROM messages WHERE folder = ? AND recipients LIKE ? ESCAPE '\\' LIMIT 50", (_folder_key(folder), f"%{needle}%"),
            ).fetchall()
        except sqlite3.Error as error:
            raise StorageError(f"The index could not be read: {error}") from None
        return any(found.casefold() == wanted for (text,) in rows for _, found in getaddresses([str(text)]))

    def thread_sizes(self, thread_keys: Collection[str]) -> dict[str, int]:
        """How many messages (in all folders) each of these conversations has."""
        sizes: dict[str, int] = {}
        keys = sorted(set(thread_keys))
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for key, count in self._connection.execute(f"SELECT thread_key, count(*) FROM messages WHERE thread_key IN ({marks}) GROUP BY thread_key", chunk):
                sizes[str(key)] = int(count)
        return sizes

    def search(
        self,
        query: str,
        folder: tuple[str, ...] | None = None,
        filters: SearchFilter = SearchFilter(),
        offset: int = 0,
        limit: int = 100,
        sort: SortField = SortField.DATE,
        descending: bool = True,
    ) -> list[IndexedMessage]:
        """Messages matching the words and the filters, newest first unless another order is asked for. An empty query lists by filters only."""
        source, where, parameters = self._search_parts(query, folder, filters)
        sql = f"SELECT {_COLUMNS} FROM {source}{where} ORDER BY {_order(sort, descending)} LIMIT ? OFFSET ?"
        parameters.extend([max(0, limit), max(0, offset)])
        try:
            return [_row(row) for row in self._connection.execute(sql, parameters).fetchall()]
        except sqlite3.OperationalError as error:
            raise StorageError(f"The search could not be run: {error}") from None

    def ordered(
        self, query: str = "", folder: tuple[str, ...] | None = None, filters: SearchFilter = SearchFilter(), sort: SortField = SortField.DATE, descending: bool = True,
        limit: int | None = None,
    ) -> list[tuple[str, tuple[str, ...], bool]]:
        """Every message of a folder or a search in the order search() lists them (or the first limit of them), as (store id,
        folder, unread) only: the order of a view without reading its rows (R-15). Each folder is one shared tuple."""
        source, where, parameters = self._search_parts(query, folder, filters)
        order = _order(sort, descending)
        tail = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            parameters.append(max(0, limit))
        try:
            if folder is not None:  # one folder: its name is not read for every message
                return [(str(store_id), folder, bool(unread)) for store_id, unread in self._connection.execute(f"SELECT m.store_id, (m.flags & 1) = 0 FROM {source}{where} ORDER BY {order}{tail}", parameters)]
            paths: dict[str, tuple[str, ...]] = {}
            sql = f"SELECT m.store_id, m.folder, (m.flags & 1) = 0 FROM {source}{where} ORDER BY {order}{tail}"
            return [(str(store_id), paths.get(key) or paths.setdefault(key, _folder_path(str(key))), bool(unread)) for store_id, key, unread in self._connection.execute(sql, parameters)]
        except sqlite3.OperationalError as error:
            raise StorageError(f"The search could not be run: {error}") from None

    def tally(self, query: str = "", folder: tuple[str, ...] | None = None, filters: SearchFilter = SearchFilter()) -> tuple[int, int]:
        """How many messages a folder or a search holds, and how many of them are unread (what ordered() would list)."""
        source, where, parameters = self._search_parts(query, folder, filters)
        try:
            found = self._connection.execute(f"SELECT count(*), coalesce(sum((m.flags & 1) = 0), 0) FROM {source}{where}", parameters).fetchone()
        except sqlite3.OperationalError as error:
            raise StorageError(f"The search could not be run: {error}") from None
        return int(found[0]), int(found[1])

    def messages(self, store_ids: Iterable[str]) -> dict[str, IndexedMessage]:
        """The index rows of these messages (one question per 500); a message the index does not have is left out."""
        wanted = list(dict.fromkeys(store_ids))
        found: dict[str, IndexedMessage] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            for values in self._connection.execute(f"SELECT {_COLUMNS} FROM messages m WHERE m.store_id IN ({', '.join('?' * len(chunk))})", chunk):
                message = _row(values)
                found[message.store_id] = message
        return found

    def groups(
        self,
        bounds: DateBounds,
        query: str = "",
        folder: tuple[str, ...] | None = None,
        filters: SearchFilter = SearchFilter(),
        sort: SortField = SortField.DATE,
        descending: bool = True,
    ) -> list[GroupCount]:
        """The groups of a folder or a search in the order the messages are listed (search with the same arguments), each
        with its number of messages: date groups when sorted by date, size groups by size, one group per number of messages
        in the conversation, none for the yes/no sorts (unread, flagged ...), one group per text otherwise."""
        if sort in YES_NO_SORTS:
            return []
        source, where, parameters = self._search_parts(query, folder, filters)
        if sort in (SortField.DATE, SortField.SENT):
            moment = _SORT_COLUMN[sort]  # Received or Sent: the same date groups
            cases = " ".join(f"WHEN {moment} >= ? THEN {position}" for position in range(len(bounds.starts())))
            key = f"CASE WHEN {moment} IS NULL THEN {len(DATE_GROUPS) - 1} {cases} ELSE {len(DATE_GROUPS) - 2} END"
            parameters = [_epoch(start) for start in bounds.starts()] + parameters
            no_date = len(DATE_GROUPS) - 1  # messages without a date come last when the newest are first, and first otherwise
            order = "g ASC" if descending else f"CASE WHEN g = {no_date} THEN {no_date + 1} ELSE g END DESC"
            sql = f"SELECT {key} AS g, count(*) FROM {source}{where} GROUP BY g ORDER BY {order}"
            names = DATE_GROUPS
        elif sort is SortField.SIZE:
            cases = " ".join(f"WHEN m.size < {limit} THEN {position}" for position, (_, _, limit) in enumerate(SIZE_GROUPS) if limit is not None)
            key = f"CASE {cases} ELSE {len(SIZE_GROUPS) - 1} END"
            sql = f"SELECT {key} AS g, count(*) FROM {source}{where} GROUP BY g ORDER BY g {'DESC' if descending else 'ASC'}"
            names = tuple((group_key, name) for group_key, name, _ in SIZE_GROUPS)
        elif sort is SortField.CONVERSATION:
            sql = f"SELECT {_SORT_COLUMN[sort]} AS g, count(*) FROM {source}{where} GROUP BY g ORDER BY g {'DESC' if descending else 'ASC'}"
            try:
                return [GroupCount(f"{int(size):09d}", str(int(size)), int(count)) for size, count in self._connection.execute(sql, parameters).fetchall()]
            except sqlite3.OperationalError as error:
                raise StorageError(f"The search could not be run: {error}") from None
        else:
            column = _TEXT_GROUP_COLUMN[sort]
            sql = (
                f"SELECT lower({column}), {column}, count(*) FROM {source}{where}"
                f" GROUP BY {column} COLLATE NOCASE ORDER BY {column} COLLATE NOCASE {'DESC' if descending else 'ASC'}"
            )
            try:
                found_text = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.OperationalError as error:
                raise StorageError(f"The search could not be run: {error}") from None
            if sort is SortField.FOLDER:  # named as the Folder column shows it
                return [GroupCount(str(key), "/".join(_folder_path(str(value))), int(count)) for key, value, count in found_text]
            return [GroupCount(str(key), str(value), int(count)) for key, value, count in found_text]
        try:
            found = self._connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as error:
            raise StorageError(f"The search could not be run: {error}") from None
        return [GroupCount(names[int(position)][0], names[int(position)][1], int(count)) for position, count in found]

    def _search_parts(self, query: str, folder: tuple[str, ...] | None, filters: SearchFilter) -> tuple[str, str, list[object]]:
        """The FROM and WHERE of a search, and the values for its question marks."""
        conditions: list[str] = []
        parameters: list[object] = []
        source = "messages m"
        query, wanted_tags, excluded_tags = split_tag_terms(query)
        parsed = parse_query(query)
        expression = parsed.positive
        if expression:
            source = "text t JOIN messages m ON m.id = t.rowid"
            conditions.append("t.text MATCH ?")
            parameters.append(expression)
        # With a text query the words must be looked up first. A bare "m.folder = ?" or "m.date >= ?" lets SQLite walk
        # the folder's date index instead and probe the full-text table once per row (measured: 4 seconds instead of
        # 0.1 second with 300,000 hits). The unary plus on a column stops SQLite from using an index for that condition.
        steer = "+" if expression else ""
        if parsed.negative:
            conditions.append("m.id NOT IN (SELECT rowid FROM text WHERE text MATCH ?)")
            parameters.append(parsed.negative)
        if folder is not None:
            conditions.append(f"{steer}m.folder = ?")
            parameters.append(_folder_key(folder))
        marks: list[str] = []  # the conditions on what a person changes while a view shows the message
        if filters.unread is not None:
            marks.append("(m.flags & 1) = 0" if filters.unread else "(m.flags & 1) != 0")
        if filters.flagged is not None:
            marks.append("(m.flags & 4) != 0" if filters.flagged else "(m.flags & 4) = 0")
        if filters.replied is not None:
            marks.append("(m.flags & 2) != 0" if filters.replied else "(m.flags & 2) = 0")
        mark_values: list[object] = []
        for tag in (filters.tag, filters.also_tag):
            if tag:
                marks.append("m.store_id IN (SELECT store_id FROM message_tags WHERE tag = ?)")
                mark_values.append(" ".join(tag.split()).casefold())
        if marks and filters.stay:  # read, unflagged or untagged while shown: it stays until the view is left
            stay = sorted(filters.stay)
            conditions.append(f"(({' AND '.join(marks)}) OR m.store_id IN ({', '.join('?' * len(stay))}))")
            parameters.extend((*mark_values, *stay))
        else:
            conditions.extend(marks)
            parameters.extend(mark_values)
        if filters.has_attachments is not None:
            conditions.append("m.has_attachments = ?")
            parameters.append(int(filters.has_attachments))
        if filters.date_from is not None:
            conditions.append(f"{steer}m.date >= ?")
            parameters.append(_epoch(filters.date_from))
        if filters.date_to is not None:
            conditions.append(f"{steer}m.date <= ?")
            parameters.append(_epoch(filters.date_to))
        if filters.newest_per_thread and not expression:
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM messages n WHERE n.thread_key = m.thread_key AND n.folder = m.folder AND n.id != m.id"
                " AND (COALESCE(n.date, -4611686018427387904) > COALESCE(m.date, -4611686018427387904)"
                " OR (COALESCE(n.date, -4611686018427387904) = COALESCE(m.date, -4611686018427387904) AND n.id > m.id)))"
            )
        for tag in wanted_tags:
            conditions.append("m.store_id IN (SELECT store_id FROM message_tags WHERE tag = ?)")
            parameters.append(" ".join(tag.split()).casefold())
        for tag in excluded_tags:
            conditions.append("m.store_id NOT IN (SELECT store_id FROM message_tags WHERE tag = ?)")
            parameters.append(tag.casefold())
        if filters.minimum_size is not None:
            conditions.append("m.size >= ?")
            parameters.append(filters.minimum_size)
        if filters.maximum_size is not None:
            conditions.append("m.size <= ?")
            parameters.append(filters.maximum_size)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return source, where, parameters

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
