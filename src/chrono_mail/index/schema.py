# --- Version ------------------------------------------------------------
__version__ = "schema.py 4.4.539"

# --- Version history ----------------------------------------------------
# v4.4.539: format 20: a folder is a row of its own (FOLDERS_TABLE), each message holds its id, so a rename changes one row (D-314); every
#           index has a new name, so an upgrade makes what it needs beside the old ones (FIRST_INDEXES, OLD_MESSAGES)
# v4.2.471: format 19: every sort index also holds each message's id (SORT_INDEXES), so a sort reads only its index (D-306);
#           18 and the formats upgraded in place get them remade in the background
# v4.2.469: format 18: every sort index has a copy that holds only unread mail (UNREAD_INDEXES), so an unread view reads only its unread
#           mail, whatever the sort (D-305); 17 and the formats upgraded in place get them in the background
# v4.2.430: format 17: to_shown filled in again from the headers (16, 15 and 14), from each message's own addresses
# v4.2.429: format 16: to_shown, the person the From / To column shows for mail one sent, with its sort index; 15 and 14 are filled in from
#           the headers only (FILL_HEADERS_KEY)
# v4.2.418: format 15: an index of format 14 is filled in once more (every message's receiving account recorded in its properties, D-292)
# v4.2.417: format 14: the people columns (sender_address, recipient_names, recipient_addresses, D-291) and received_by, the account that
#           received each message (D-292), with sort indexes; 13, 12 and 11 are upgraded in place and filled in afterwards
# v2.0.301: format 13: has_attachments follows the new meaning of inline (D-235); 12 and 11 are upgraded in place and filled in afterwards
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

SCHEMA_VERSION: int = 20  # 20: a folder is a row of its own and each message holds its id, so a rename changes one row (D-314; 19 to 11 upgraded in place: the rows copied at once, the sort indexes made and the old rows dropped afterwards); 19: every sort index also holds each message's id (SORT_INDEXES, D-306; 18 to 11 upgraded in place, the indexes remade afterwards); 18: every sort index has a copy holding only unread mail (UNREAD_INDEXES, D-305; 17 to 11 upgraded in place, the copies made afterwards); 17: to_shown filled in again from each message's own addresses (16 read them back from the joined recipients, which a name that looks like an address breaks); 16: to_shown, the person the From / To column shows for mail one sent (its first recipient, else the sender), sorted by (D-295; 15 to 11 upgraded in place, filled in afterwards); 15: nothing new, but every message is filled in once more, so its receiving account is recorded with it (D-292); 14: the people columns and the receiving account (D-291, D-292; 13, 12 and 11 upgraded in place, filled in afterwards); 13: has_attachments by what the text shows (D-235; 12 and 11 upgraded in place, filled in afterwards); 12: sent, the message's own Date (the Sent column, B-16; 11 is upgraded in place, D-229); 11: date is when the mail server received the message (D-226): an older index is rebuilt
FOLDER_SEPARATOR: str = "\x1f"
SENT_FILL_KEY: str = "sent_filled_to"  # meta, after an upgrade in place: the row id up to which sent, has_attachments, the people columns and received_by were read from the messages; gone when all are done
UPGRADE_FROM_11: tuple[str, ...] = (  # format 12 only adds the sent moment: the old index stays usable, sent is filled in afterwards (index.fill-sent)
    "ALTER TABLE messages ADD COLUMN sent INTEGER",
    "CREATE INDEX messages_by_sent ON messages (folder, sent DESC, id DESC)",
)

PEOPLE_INDEXES: tuple[str, ...] = (  # the people columns sort like From (D-291); received_by sorts by the account's name, which no index can hold
    "CREATE INDEX messages_by_sender ON messages (folder, sender COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_sender_address ON messages (folder, sender_address COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_recipient_names ON messages (folder, recipient_names COLLATE NOCASE, id)",
    "CREATE INDEX messages_by_recipient_addresses ON messages (folder, recipient_addresses COLLATE NOCASE, id)",
)
UPGRADE_FROM_15: tuple[str, ...] = (  # format 16 only adds the person From / To shows for sent mail: filled in afterwards, from the headers only
    "ALTER TABLE messages ADD COLUMN to_shown TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX messages_by_to_shown ON messages (folder, to_shown COLLATE NOCASE, id)",
)
FILL_HEADERS_KEY: str = "fill_from_headers"  # meta, while a filling in runs that needs only the headers of each message (an upgrade from 14, 15 or 16)
UPGRADE_FROM_13: tuple[str, ...] = (  # format 14 only adds columns: the old index stays usable, they are filled in afterwards (index.fill-sent)
    "ALTER TABLE messages ADD COLUMN sender_address TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE messages ADD COLUMN recipient_names TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE messages ADD COLUMN recipient_addresses TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE messages ADD COLUMN received_by TEXT NOT NULL DEFAULT ''",
    *PEOPLE_INDEXES,
)

# A copy of every sort index that holds only unread mail (D-305): an unread view (Favorites > Unread, Filter > Unread) sorted by any column
# reads only its unread messages; through the full sort index it would read every message of every folder to find them (21 s on a spinning
# disk for 678 unread of 140,465). messages_by_unread needs none: it starts with the unread mark, and serves the date sort of unread mail too. The condition is written exactly as the
# search writes it, or SQLite does not use them. As format 18 had them: since 19 they are part of SORT_INDEXES (an old format's tests use these).
UNREAD_INDEXES: tuple[str, ...] = tuple(
    f"CREATE INDEX IF NOT EXISTS {name}_unread ON messages ({columns}) WHERE (flags & 1) = 0"
    for name, columns in (
        ("messages_by_sent", "folder, sent DESC, id DESC"),
        ("messages_by_subject", "folder, subject COLLATE NOCASE, id"),
        ("messages_by_sender_name", "folder, sender_name COLLATE NOCASE, id"),
        ("messages_by_size", "folder, size, id"),
        ("messages_by_to_shown", "folder, to_shown COLLATE NOCASE, id"),
        ("messages_by_flagged", "folder, ((flags & 4) <> 0), date, id"),
        ("messages_by_replied", "folder, ((flags & 2) <> 0), date, id"),
        ("messages_by_attachment", "folder, has_attachments, date, id"),
        ("messages_by_preview", "folder, preview COLLATE NOCASE, date, id"),
        ("messages_by_sender", "folder, sender COLLATE NOCASE, id"),
        ("messages_by_sender_address", "folder, sender_address COLLATE NOCASE, id"),
        ("messages_by_recipient_names", "folder, recipient_names COLLATE NOCASE, id"),
        ("messages_by_recipient_addresses", "folder, recipient_addresses COLLATE NOCASE, id"),
    )
)

# Every sort index (D-306): the folder and the sort's columns, then each message's id, so a sort and the order of a view read only the
# index, never the rows; the read mark is not in them (a mark change would rewrite every one): the unread ones come from the Unread index (one row read per message: 1.7 s on a spinning disk for a folder of 16,000, 0.07 s from the index).
# The unread copies of D-305 the same. Every index has a name no format before 20 had: the upgrade makes the ones it needs at once beside the
# old ones, whose removal reads all their pages (74 s on a cold disk); the rest follow afterwards, one at a time (Index.make_sort_index).
_SORTED_BY: tuple[tuple[str, str], ...] = (
    ("messages_by_date", "folder_id, date DESC, id DESC"),
    ("messages_by_sent", "folder_id, sent DESC, id DESC"),
    ("messages_by_subject", "folder_id, subject COLLATE NOCASE, id"),
    ("messages_by_sender_name", "folder_id, sender_name COLLATE NOCASE, id"),  # the sender as the From column shows it
    ("messages_by_size", "folder_id, size, id"),
    ("messages_by_to_shown", "folder_id, to_shown COLLATE NOCASE, id"),  # From / To where it shows the recipients (D-295)
    # The sort keys of the yes/no sorts are written exactly like _SORT_COLUMN in index.py, or SQLite does not use these indexes.
    ("messages_by_unread", "folder_id, (flags & 1), date DESC, id DESC"),  # also serves unread counts: (flags & 1) = 0 is a seek
    ("messages_by_flagged", "folder_id, ((flags & 4) <> 0), date, id"),
    ("messages_by_replied", "folder_id, ((flags & 2) <> 0), date, id"),
    ("messages_by_attachment", "folder_id, has_attachments, date, id"),
    ("messages_by_preview", "folder_id, preview COLLATE NOCASE, date, id"),
    ("messages_by_sender", "folder_id, sender COLLATE NOCASE, id"),  # the people columns sort like From (D-291)
    ("messages_by_sender_address", "folder_id, sender_address COLLATE NOCASE, id"),
    ("messages_by_recipient_names", "folder_id, recipient_names COLLATE NOCASE, id"),
    ("messages_by_recipient_addresses", "folder_id, recipient_addresses COLLATE NOCASE, id"),
)
# A sort by a mark computes its key from flags: those indexes hold flags too, or SQLite reads every row for it (they change with that mark anyway).
_MARKS: dict[str, str] = {"messages_by_unread": ", flags", "messages_by_flagged": ", flags", "messages_by_replied": ", flags"}
SORT_INDEX_PREFIX: str = "folder_"  # a sort index's name is the name it had before format 20 after this
SORT_INDEXES: tuple[str, ...] = (
    *(f"CREATE INDEX {SORT_INDEX_PREFIX}{name} ON messages ({columns}, store_id{_MARKS.get(name, '')})" for name, columns in _SORTED_BY),
    *(
        f"CREATE INDEX {SORT_INDEX_PREFIX}{name}_unread ON messages ({columns}, store_id, flags) WHERE (flags & 1) = 0"  # flags: the condition is read from it
        for name, columns in _SORTED_BY
        if name not in ("messages_by_date", "messages_by_unread")  # the Unread index serves the date sort of unread mail
    ),
)

TAG_STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE message_tags (store_id TEXT NOT NULL, tag TEXT NOT NULL)",  # tag: the name folded (casefold), to find messages by tag
    "CREATE INDEX message_tags_by_tag ON message_tags (tag, store_id)",
    "CREATE INDEX message_tags_by_message ON message_tags (store_id)",
)

# A folder is a row of its own (D-314): its id, the id of the folder it is in (TOP_FOLDER: none) and its display name. Each message holds the
# id, so a rename or a move of a folder, however large its tree, changes this one row. A row stays when its mail has left, like its numbers
# below, and a rebuild gives every folder a new id: no id is ever kept outside the index.
TOP_FOLDER: int = 0
FOLDERS_TABLE: str = "CREATE TABLE folders (id INTEGER PRIMARY KEY, parent INTEGER NOT NULL, name TEXT NOT NULL, UNIQUE (parent, name))"

# The numbers of every folder, kept by SQLite itself in the transaction of every change of a message (R-10): reading them is one lookup, and
# they can never differ from the messages table. Bit 1 of flags is Seen: (flags & 1) = 0 is unread. A row may stay at 0 after its folder emptied.
FOLDER_NUMBERS_TABLE: str = "CREATE TABLE folder_numbers (folder_id INTEGER PRIMARY KEY, messages INTEGER NOT NULL, unread INTEGER NOT NULL)"
FOLDER_NUMBER_TRIGGERS: tuple[str, ...] = (
    """CREATE TRIGGER folder_numbers_added AFTER INSERT ON messages BEGIN
        INSERT INTO folder_numbers VALUES (NEW.folder_id, 1, (NEW.flags & 1) = 0)
        ON CONFLICT (folder_id) DO UPDATE SET messages = messages + 1, unread = unread + excluded.unread;
    END""",
    """CREATE TRIGGER folder_numbers_removed AFTER DELETE ON messages BEGIN
        UPDATE folder_numbers SET messages = messages - 1, unread = unread - ((OLD.flags & 1) = 0) WHERE folder_id = OLD.folder_id;
    END""",
    """CREATE TRIGGER folder_numbers_changed AFTER UPDATE OF folder_id, flags ON messages
    WHEN OLD.folder_id <> NEW.folder_id OR (OLD.flags & 1) <> (NEW.flags & 1) BEGIN
        UPDATE folder_numbers SET messages = messages - 1, unread = unread - ((OLD.flags & 1) = 0) WHERE folder_id = OLD.folder_id;
        INSERT INTO folder_numbers VALUES (NEW.folder_id, 1, (NEW.flags & 1) = 0)
        ON CONFLICT (folder_id) DO UPDATE SET messages = messages + 1, unread = unread + excluded.unread;
    END""",
)
FOLDER_NUMBER_STATEMENTS: tuple[str, ...] = (FOLDER_NUMBERS_TABLE, *FOLDER_NUMBER_TRIGGERS)

# Only scheduled messages (and those that could not be read) have a send time: the minute check finds them without a scan.
SEND_AT_INDEX: str = "CREATE INDEX scheduled_messages ON messages (send_at) WHERE send_at IS NOT NULL"
THREAD_INDEX: str = "CREATE INDEX messages_of_thread ON messages (thread_key)"
MESSAGE_ID_INDEX: str = "CREATE INDEX messages_with_message_id ON messages (message_id)"
# What an upgraded index needs at once: the list by date, the unread ones, the conversations and the scheduled mail; the other sort indexes follow.
FIRST_INDEXES: tuple[str, ...] = (*SORT_INDEXES[:1], SORT_INDEXES[6], SEND_AT_INDEX, THREAD_INDEX, MESSAGE_ID_INDEX)

OLD_MESSAGES: str = "messages_old"  # an upgraded index's rows as they were, with their indexes, until they are dropped afterwards (Index.make_sort_index)
MESSAGES_TABLE: str = """CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    store_id TEXT NOT NULL UNIQUE,
    folder_id INTEGER NOT NULL,
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
    sent INTEGER,
    sender_address TEXT NOT NULL DEFAULT '',
    recipient_names TEXT NOT NULL DEFAULT '',
    recipient_addresses TEXT NOT NULL DEFAULT '',
    received_by TEXT NOT NULL DEFAULT '',
    to_shown TEXT NOT NULL DEFAULT ''
)"""
# Every column of a message but its folder: the same in every format the upgrade starts from.
MESSAGE_COLUMNS: tuple[str, ...] = (
    "id", "store_id", "path", "flags", "date", "size", "subject", "sender", "recipients", "message_id", "thread_key", "has_attachments", "preview", "tags",
    "sender_name", "send_at", "sent", "sender_address", "recipient_names", "recipient_addresses", "received_by", "to_shown",
)

STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    FOLDERS_TABLE,
    MESSAGES_TABLE,
    SEND_AT_INDEX,
    *SORT_INDEXES,
    THREAD_INDEX,
    MESSAGE_ID_INDEX,
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
