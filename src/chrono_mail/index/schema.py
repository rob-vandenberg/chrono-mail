# --- Version ------------------------------------------------------------
__version__ = "schema.py 0.3.109"

# --- Version history ----------------------------------------------------
# v0.3.109: Schema 5: indexes for sorting by unread, flagged, replied, attachment and preview
# v0.0.23: Schema 4: the addresses seen in messages (for completing addresses)
# v0.0.14: Schema 3: previews are cleaned (older indexes are rebuilt)
# v0.0.13: Schema 2: a preview line per message and a partial index for unread counts
# v0.0.8: Initial index schema

SCHEMA_VERSION: int = 5
MINIMUM_SQLITE_VERSION: tuple[int, int, int] = (3, 43, 0)  # contentless FTS5 tables that allow deletes
FOLDER_SEPARATOR: str = "\x1f"

STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        store_id TEXT NOT NULL UNIQUE,
        folder TEXT NOT NULL,
        path TEXT NOT NULL,
        flags INTEGER NOT NULL,
        date INTEGER,
        size INTEGER NOT NULL,
        subject TEXT NOT NULL,
        sender TEXT NOT NULL,
        recipients TEXT NOT NULL,
        message_id TEXT,
        thread_key TEXT NOT NULL,
        has_attachments INTEGER NOT NULL,
        preview TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX messages_by_date ON messages (folder, date DESC, id DESC)",
    "CREATE INDEX messages_by_subject ON messages (folder, subject COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_sender ON messages (folder, sender COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_size ON messages (folder, size, id)",
    # The sort keys of the yes/no sorts are written exactly like _SORT_COLUMN in index.py, or SQLite does not use these indexes.
    "CREATE INDEX messages_by_unread ON messages (folder, (flags & 1), date DESC, id DESC)",  # also serves unread counts: (flags & 1) = 0 is a seek
    "CREATE INDEX messages_by_flagged ON messages (folder, ((flags & 4) <> 0), date, id)",
    "CREATE INDEX messages_by_replied ON messages (folder, ((flags & 2) <> 0), date, id)",
    "CREATE INDEX messages_by_attachment ON messages (folder, has_attachments, date, id)",
    "CREATE INDEX messages_by_preview ON messages (folder, preview COLLATE NOCASE, date, id)",
    "CREATE INDEX messages_by_thread ON messages (thread_key)",
    "CREATE INDEX messages_by_message_id ON messages (message_id)",
    # Every address seen in a From, To or Cc, with how often (counts only grow; a rebuild recounts exactly).
    """CREATE TABLE addresses (
        address TEXT PRIMARY KEY COLLATE NOCASE,
        name TEXT NOT NULL,
        as_sender INTEGER NOT NULL,
        as_recipient INTEGER NOT NULL,
        last INTEGER
    )""",
    # Unread counts per folder without scanning every message: the WHERE must be written exactly like the queries (D-030).
    "CREATE INDEX messages_unread ON messages (folder) WHERE (flags & 1) = 0",
    """CREATE VIRTUAL TABLE text USING fts5 (
        subject, sender, recipients, body,
        content = '', contentless_delete = 1,
        tokenize = 'unicode61 remove_diacritics 2'
    )""",
)
