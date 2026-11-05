# --- Version ------------------------------------------------------------
__version__ = "schema.py 4.4.545"

# --- Version history ----------------------------------------------------
# v4.4.545: Schema 5: reminder_due also for tasks (D-316); 3 and 4 are upgraded in place
# v4.4.544: Schema 4: reminder_due, when the next reminder of an event comes due (D-316); 3 is upgraded in place
# v1.10.277: Schema 3: contact groups and their members (R-39)
# v1.8.263: MINIMUM_SQLITE_VERSION moved to index/database.py (R-16)
# v0.0.60: Schema 2: a second line for lists (organisation, place)
# v0.0.59: Initial index schema for calendars, tasks and contacts

SCHEMA_VERSION: int = 5
VERSION_WITHOUT_REMINDERS: int = 3  # upgraded in place, like the next: one transaction, so an interruption leaves the old format whole
VERSION_WITHOUT_TASK_REMINDERS: int = 4

REMINDER_INDEX: str = "CREATE INDEX items_by_reminder ON items (reminder_due) WHERE reminder_due IS NOT NULL"

STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE items (
        id INTEGER PRIMARY KEY,
        item_id TEXT NOT NULL UNIQUE,
        collection TEXT NOT NULL,
        kind TEXT NOT NULL,
        uid TEXT NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT NOT NULL,
        sort_key TEXT NOT NULL,
        start INTEGER,
        end INTEGER,
        due INTEGER,
        floating INTEGER NOT NULL,
        repeats INTEGER NOT NULL,
        until INTEGER,
        status TEXT NOT NULL,
        done INTEGER NOT NULL,
        priority INTEGER NOT NULL,
        percent INTEGER NOT NULL,
        tags TEXT NOT NULL,
        tags_folded TEXT NOT NULL,
        size INTEGER NOT NULL,
        is_group INTEGER NOT NULL,
        members TEXT NOT NULL,
        reminder_due INTEGER
    )""",
    "CREATE INDEX items_by_collection ON items (collection, kind)",
    "CREATE INDEX items_by_start ON items (kind, start)",
    "CREATE INDEX items_by_due ON items (kind, due) WHERE done = 0",
    "CREATE INDEX items_by_sort ON items (kind, sort_key)",
    "CREATE INDEX items_by_uid ON items (collection, uid)",
    REMINDER_INDEX,
    """CREATE VIRTUAL TABLE text USING fts5 (
        title, body,
        content = '', contentless_delete = 1,
        tokenize = 'unicode61 remove_diacritics 2'
    )""",
)
