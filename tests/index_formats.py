"""Turning a current index back into an older format, for the tests of the upgrades."""
import sqlite3

from chrono_mail.index.schema import FOLDER_NUMBER_STATEMENTS, FOLDER_SEPARATOR, MESSAGES_TABLE, SORT_INDEX_PREFIX

# --- Version ------------------------------------------------------------
__version__ = "index_formats.py 4.4.562"

# --- Version history ----------------------------------------------------
# v4.4.562: the sort indexes of formats 19 and 20 kept here, as format 21 has others (D-320)
# v4.4.539: as_format_19: an index as it was before a folder had a row of its own (D-314)

# The sort indexes of formats 19 and 20 (format 19 by the folder's name, without the prefix), as SORT_INDEXES held them before format 21.
_FORMAT_20_SORTED_BY: tuple[tuple[str, str], ...] = (
    ("messages_by_date", "folder_id, date DESC, id DESC"),
    ("messages_by_sent", "folder_id, sent DESC, id DESC"),
    ("messages_by_subject", "folder_id, subject COLLATE NOCASE, id"),
    ("messages_by_sender_name", "folder_id, sender_name COLLATE NOCASE, id"),
    ("messages_by_size", "folder_id, size, id"),
    ("messages_by_to_shown", "folder_id, to_shown COLLATE NOCASE, id"),
    ("messages_by_unread", "folder_id, (flags & 1), date DESC, id DESC"),
    ("messages_by_flagged", "folder_id, ((flags & 4) <> 0), date, id"),
    ("messages_by_replied", "folder_id, ((flags & 2) <> 0), date, id"),
    ("messages_by_attachment", "folder_id, has_attachments, date, id"),
    ("messages_by_preview", "folder_id, preview COLLATE NOCASE, date, id"),
    ("messages_by_sender", "folder_id, sender COLLATE NOCASE, id"),
    ("messages_by_sender_address", "folder_id, sender_address COLLATE NOCASE, id"),
    ("messages_by_recipient_names", "folder_id, recipient_names COLLATE NOCASE, id"),
    ("messages_by_recipient_addresses", "folder_id, recipient_addresses COLLATE NOCASE, id"),
)
_FORMAT_20_MARKS: frozenset[str] = frozenset({"messages_by_unread", "messages_by_flagged", "messages_by_replied"})
FORMAT_20_SORT_INDEXES: tuple[str, ...] = (
    *(f"CREATE INDEX {SORT_INDEX_PREFIX}{name} ON messages ({columns}, store_id{', flags' if name in _FORMAT_20_MARKS else ''})" for name, columns in _FORMAT_20_SORTED_BY),
    *(
        f"CREATE INDEX {SORT_INDEX_PREFIX}{name}_unread ON messages ({columns}, store_id, flags) WHERE (flags & 1) = 0"
        for name, columns in _FORMAT_20_SORTED_BY
        if name not in ("messages_by_date", "messages_by_unread")
    ),
)


def as_format_19(connection: sqlite3.Connection) -> None:
    """Every message holds its folder's name again, and every index has the name and the columns of format 19."""
    paths: dict[int, str] = {}
    rows = {int(row[0]): (int(row[1]), str(row[2])) for row in connection.execute("SELECT id, parent, name FROM folders")}
    for folder_id in rows:
        parts: list[str] = []
        at = folder_id
        while at in rows:
            parts.insert(0, rows[at][1])
            at = rows[at][0]
        paths[folder_id] = FOLDER_SEPARATOR.join(parts)
    for name in ("folder_numbers_added", "folder_numbers_removed", "folder_numbers_changed"):
        connection.execute(f"DROP TRIGGER {name}")
    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'messages' AND sql IS NOT NULL").fetchall():
        connection.execute(f"DROP INDEX {name}")
    connection.execute("ALTER TABLE messages RENAME TO messages_20")
    connection.execute(MESSAGES_TABLE.replace("folder_id INTEGER NOT NULL", "folder TEXT NOT NULL"))
    columns = ", ".join(str(row[1]) for row in connection.execute("PRAGMA table_info(messages_20)") if row[1] != "folder_id")
    connection.execute("CREATE TEMP TABLE keys_20 (id INTEGER PRIMARY KEY, key TEXT NOT NULL)")
    connection.executemany("INSERT INTO temp.keys_20 VALUES (?, ?)", list(paths.items()))
    connection.execute(f"INSERT INTO messages (folder, {columns}) SELECT (SELECT key FROM temp.keys_20 k WHERE k.id = o.folder_id), {columns} FROM messages_20 o")
    numbers = connection.execute("SELECT key, messages, unread FROM folder_numbers n JOIN temp.keys_20 k ON k.id = n.folder_id").fetchall()
    for statement in ("DROP TABLE messages_20", "DROP TABLE temp.keys_20", "DROP TABLE folder_numbers", "DROP TABLE folders"):
        connection.execute(statement)
    for statement in FOLDER_NUMBER_STATEMENTS:
        connection.execute(statement.replace("folder_id INTEGER PRIMARY KEY", "folder TEXT PRIMARY KEY").replace("unread INTEGER NOT NULL)", "unread INTEGER NOT NULL) WITHOUT ROWID").replace("folder_id", "folder"))
    connection.executemany("INSERT INTO folder_numbers VALUES (?, ?, ?)", numbers)
    for statement in FORMAT_20_SORT_INDEXES:
        connection.execute(statement.replace(SORT_INDEX_PREFIX, "", 1).replace("folder_id", "folder"))
    connection.execute("CREATE INDEX messages_by_send_at ON messages (send_at) WHERE send_at IS NOT NULL")
    connection.execute("CREATE INDEX messages_by_thread ON messages (thread_key)")
    connection.execute("CREATE INDEX messages_by_message_id ON messages (message_id)")
    connection.execute("UPDATE meta SET value = '19' WHERE key = 'schema'")
