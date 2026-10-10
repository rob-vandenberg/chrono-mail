# --- Version ------------------------------------------------------------
__version__ = "schema.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: Schema 4: the addresses seen in messages (for completing addresses)
# v0.0.14: Schema 3: previews are cleaned (older indexes are rebuilt)
# v0.0.13: Schema 2: a preview line per message and a partial index for unread counts
# v0.0.8: Initial index schema

SCHEMA_VERSION: int = 4
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
