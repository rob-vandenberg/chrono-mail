# --- Version ------------------------------------------------------------
__version__ = "schema.py 0.0.59"

# --- Version history ----------------------------------------------------
# v0.0.59: Initial index schema for calendars, tasks and contacts

SCHEMA_VERSION: int = 1
MINIMUM_SQLITE_VERSION: tuple[int, int, int] = (3, 43, 0)

STATEMENTS: tuple[str, ...] = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE items (
        id INTEGER PRIMARY KEY,
        item_id TEXT NOT NULL UNIQUE,
        collection TEXT NOT NULL,
        kind TEXT NOT NULL,
        uid TEXT NOT NULL,
        title TEXT NOT NULL,
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
        size INTEGER NOT NULL
    )""",
    "CREATE INDEX items_by_collection ON items (collection, kind)",
    "CREATE INDEX items_by_start ON items (kind, start)",
    "CREATE INDEX items_by_due ON items (kind, due) WHERE done = 0",
    "CREATE INDEX items_by_sort ON items (kind, sort_key)",
    "CREATE INDEX items_by_uid ON items (collection, uid)",
    """CREATE VIRTUAL TABLE text USING fts5 (
        title, body,
        content = '', contentless_delete = 1,
        tokenize = 'unicode61 remove_diacritics 2'
    )""",
)
