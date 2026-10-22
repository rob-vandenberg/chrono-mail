# --- Version ------------------------------------------------------------
__version__ = "schema.py 2.0.281"

# --- Version history ----------------------------------------------------
# v2.0.281: format 11 is upgraded in place to 12 (the empty sent column and its index; SENT_FILL_KEY says how far sent was filled in), never rebuilt (D-229)
# v2.0.280: format 12: sent, the moment the message itself carries (its Date line), with a sort index (the Sent column, B-16); format 11 is rebuilt
# v1.9.276: format 11: the date column is the server's receive time (D-226); format 10 is rebuilt
# v1.8.263: MINIMUM_SQLITE_VERSION moved to index/database.py (R-16)
# v1.1.250: Schema 10: the numbers of every folder (messages, unread), kept by the database in the same step as every change of a message
#           (folder_numbers and its triggers), so a folder's numbers are one lookup and never differ from the index (R-10); schema 9 is upgraded in place
# v1.1.196: Schema 9: send_at, when a scheduled message is to be sent (the minute check asks the index, not every Outbox message); schema 8 is upgraded in place
# v0.5.166: Schema 8: sender_name, the sender as the From column shows it (sorting and grouping by sender use it); schema 7 is upgraded in place
# v0.5.134: Schema 7: the tags of each message (a column to show them, a table to find them); schema 6 is upgraded in place
# v0.4.117: Schema 6: the partial index for unread counts is gone (the index of the Unread sort serves them)
# v0.3.109: Schema 5: indexes for sorting by unread, flagged, replied, attachment and preview
# v0.0.23: Schema 4: the addresses seen in messages (for completing addresses)
# v0.0.14: Schema 3: previews are cleaned (older indexes are rebuilt)
# v0.0.13: Schema 2: a preview line per message and a partial index for unread counts
# v0.0.8: Initial index schema

SCHEMA_VERSION: int = 12  # 12: sent, the message's own Date (the Sent column, B-16; 11 is upgraded in place, D-229); 11: date is when the mail server received the message (D-226): an older index is rebuilt
FOLDER_SEPARATOR: str = "\x1f"
SENT_FILL_KEY: str = "sent_filled_to"  # meta, after the upgrade from format 11: the row id up to which sent was read from the messages; gone when all are done
UPGRADE_FROM_11: tuple[str, ...] = (  # format 12 only adds the sent moment: the old index stays usable, sent is filled in afterwards (index.fill-sent)
    "ALTER TABLE messages ADD COLUMN sent INTEGER",
    "CREATE INDEX messages_by_sent ON messages (folder, sent DESC, id DESC)",
)

TAG_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE message_tags (store_id TEXT NOT NULL, tag TEXT NOT NULL)",  # tag: the name folded (casefold), to find messages by tag
    "CREATE INDEX message_tags_by_tag ON message_tags (tag, store_id)",
    "CREATE INDEX message_tags_by_message ON message_tags (store_id)",
)

# The numbers of every folder, kept by SQLite itself in the transaction of every change of a message (R-10): reading them is one lookup, and
# they can never differ from the messages table. Bit 1 of flags is Seen: (flags & 1) = 0 is unread. A row may stay at 0 after its folder emptied.
FOLDER_NUMBER_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE folder_numbers (folder TEXT PRIMARY KEY, messages INTEGER NOT NULL, unread INTEGER NOT NULL) WITHOUT ROWID",
    """CREATE TRIGGER folder_numbers_added AFTER INSERT ON messages BEGIN
        INSERT INTO folder_numbers VALUES (NEW.folder, 1, (NEW.flags & 1) = 0)
        ON CONFLICT (folder) DO UPDATE SET messages = messages + 1, unread = unread + excluded.unread;
    END""",
    """CREATE TRIGGER folder_numbers_removed AFTER DELETE ON messages BEGIN
        UPDATE folder_numbers SET messages = messages - 1, unread = unread - ((OLD.flags & 1) = 0) WHERE folder = OLD.folder;
    END""",
    """CREATE TRIGGER folder_numbers_changed AFTER UPDATE OF folder, flags ON messages
    WHEN OLD.folder <> NEW.folder OR (OLD.flags & 1) <> (NEW.flags & 1) BEGIN
        UPDATE folder_numbers SET messages = messages - 1, unread = unread - ((OLD.flags & 1) = 0) WHERE folder = OLD.folder;
        INSERT INTO folder_numbers VALUES (NEW.folder, 1, (NEW.flags & 1) = 0)
        ON CONFLICT (folder) DO UPDATE SET messages = messages + 1, unread = unread + excluded.unread;
    END""",
)

# Only scheduled messages (and those that could not be read) have a send time: the minute check finds them without a scan.
SEND_AT_INDEX: str = "CREATE INDEX messages_by_send_at ON messages (send_at) WHERE send_at IS NOT NULL"

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
        preview TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        sender_name TEXT NOT NULL DEFAULT '',
        send_at INTEGER,
        sent INTEGER
    )""",
    SEND_AT_INDEX,
    "CREATE INDEX messages_by_date ON messages (folder, date DESC, id DESC)",
    "CREATE INDEX messages_by_sent ON messages (folder, sent DESC, id DESC)",
    "CREATE INDEX messages_by_subject ON messages (folder, subject COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_sender_name ON messages (folder, sender_name COLLATE NOCASE, id)",  # the sender as the From column shows it
    "CREATE INDEX messages_by_size ON messages (folder, size, id)",
    # The sort keys of the yes/no sorts are written exactly like _SORT_COLUMN in index.py, or SQLite does not use these indexes.
    "CREATE INDEX messages_by_unread ON messages (folder, (flags & 1), date DESC, id DESC)",  # also serves unread counts: (flags & 1) = 0 is a seek
    "CREATE INDEX messages_by_flagged ON messages (folder, ((flags & 4) <> 0), date, id)",
    "CREATE INDEX messages_by_replied ON messages (folder, ((flags & 2) <> 0), date, id)",
    "CREATE INDEX messages_by_attachment ON messages (folder, has_attachments, date, id)",
    "CREATE INDEX messages_by_preview ON messages (folder, preview COLLATE NOCASE, date, id)",
    "CREATE INDEX messages_by_thread ON messages (thread_key)",
    "CREATE INDEX messages_by_message_id ON messages (message_id)",
    *TAG_STATEMENTS,
    *FOLDER_NUMBER_STATEMENTS,
    # Every address seen in a From, To or Cc, with how often (counts only grow; a rebuild recounts exactly).
    """CREATE TABLE addresses (
        address TEXT PRIMARY KEY COLLATE NOCASE,
        name TEXT NOT NULL,
        as_sender INTEGER NOT NULL,
        as_recipient INTEGER NOT NULL,
        last INTEGER
    )""",
    """CREATE VIRTUAL TABLE text USING fts5 (
        subject, sender, recipients, body,
        content = '', contentless_delete = 1,
        tokenize = 'unicode61 remove_diacritics 2'
    )""",
)
