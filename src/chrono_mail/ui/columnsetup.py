import json
from dataclasses import dataclass
from typing import Literal, get_args

from chrono_mail.ui.columns import BY_ID, PEOPLE_COLUMNS, SNOOZED_COLUMN, people_shown, valid_columns
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "columnsetup.py 4.2.436"

# --- Version history ----------------------------------------------------
# v4.2.436: KIND_NAMES: how the hover texts of Apply to all, Default and Reset name each kind of folder
# v4.2.426: the From / To column has a sort of its own (from_to), never shared with From (the owner: a column sorts by itself); a saved
#           setup saved before (no format 2) sorted by sender that shows From / To and no From is sorted by From / To (saved_sort)
# v4.2.421: a sort of From or To follows the way the column shows (sort_of_way_shown), also in a saved setup (D-291)
# v4.2.417: the sort orders of the people columns and of Account (D-291, D-292)
# v4.2.414: a search of several folders is known by the view itself (CurrentView.several_folders)
# v4.2.413: a search of All folders from a Favorites or Tags view is a search of several folders too (D-287)
# v4.2.408: a search of several folders is a kind of its own: it starts with From, To and Folder beside the incoming columns (D-288)
# v4.2.406: a search of a folder and its subfolders has the setup of a search in all folders: it shows several folders (D-287)
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) known by their role, no longer by their name (CR-36)
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v2.2.314: an Outbox starts with the Send at column (a delayed send shows when it goes, B-21)
# v2.0.280: Columns per folder (B-16): the six kinds of folder, the columns and sort each kind starts with, which setup a view
#           uses, the sort a folder falls back to when its sorted column goes, the setups kept as text

FolderType = Literal["incoming", "sent", "drafts", "outbox", "scheduled", "snoozed", "search"]
FOLDER_TYPES: tuple[FolderType, ...] = get_args(FolderType)
SEARCH_KEY: str = "search"  # a search in all folders of an account, or of a folder and its subfolders: one setup of its own


@dataclass(frozen=True, slots=True)
class ColumnSetup:
    """How one folder or view shows its messages: the columns shown in their order, their widths (a column layout as text,
    "" for the standard widths), the sort order and its direction."""

    columns: tuple[str, ...]
    sort: str
    oldest_first: bool = False
    widths: str = ""


# The setups a kind of folder starts with (the owner, 2026-10-22): what a list of that kind needs to show, and its natural order
STARTING: dict[FolderType, ColumnSetup] = {
    "incoming": ColumnSetup(("flag", "status", "attachment", "from", "subject", "date", "size", "contact", "trash"), "date"),
    "sent": ColumnSetup(("flag", "attachment", "to", "subject", "sent", "size", "contact", "trash"), "sent"),
    "drafts": ColumnSetup(("attachment", "to", "subject", "sent", "size", "trash"), "sent"),
    "outbox": ColumnSetup(("attachment", "to", "subject", SNOOZED_COLUMN, "sent", "size", "trash"), "sent"),  # Send at: a delayed send (B-21)
    "scheduled": ColumnSetup(("attachment", "to", "subject", SNOOZED_COLUMN, "size", "trash"), "snoozed", oldest_first=True),  # the next to go first
    "snoozed": ColumnSetup(("flag", "attachment", "from_to", "subject", SNOOZED_COLUMN, "size", "trash"), "snoozed", oldest_first=True),
    # a search of several folders: sent and received mail together, so both From and To, and where each message is (D-288)
    "search": ColumnSetup(("flag", "status", "attachment", "from", "to", "subject", "folder", "date", "size", "contact", "trash"), "date"),
}
# How the hover texts of Apply to all, Default and Reset name the folders of each kind (the owner, 4.2.436)
KIND_NAMES: dict[FolderType, str] = {
    "incoming": "received mail folders", "sent": "Sent folders", "drafts": "Drafts folders", "outbox": "Outbox folders",
    "scheduled": "Scheduled folders", "snoozed": "Snoozed folders", "search": "search folders",
}
_OUTGOING_FAVORITES: dict[str, FolderType] = {"sent": "sent", "drafts": "drafts", "outbox": "outbox", "scheduled": "scheduled", "snoozed": "snoozed"}
_LOCAL_FOLDERS: dict[str, FolderType] = {"outbox": "outbox", "scheduled": "scheduled", "snoozed": "snoozed"}  # by the folder's role (CR-36)

SETUP_FORMAT: int = 2  # a setup saved since every column has a sort of its own (4.2.426); without it, saved_sort reads the sort of before
FROM_TO_SORT: str = "from_to"  # the From / To column's own sort: by what it shows (the engine is asked for the sender or the recipients)

# The column that shows each sort order: one each, so a sort belongs to the column it was chosen by
SORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "sender": ("from",), FROM_TO_SORT: ("from_to",), "subject": ("subject",), "date": ("date",), "sent": ("sent",), "size": ("size",), "to": ("to",),
    "snoozed": (SNOOZED_COLUMN,), "unread": ("status",), "flagged": ("flag",), "attachment": ("attachment",), "replied": ("replied",),
    "contact": ("contact",), "folder": ("folder",), "conversation": ("conversation",), "preview": ("preview",),
    "sender_full": ("from_full",), "sender_address": ("from_address",), "to_names": ("to_names",), "to_addresses": ("to_addresses",), "account": ("account",),
}
FALLBACK_SORTS: tuple[str, ...] = ("date", "sent", "subject", "to", "sender", FROM_TO_SORT, "size")  # Received, Sent, Subject, To, From, From / To, Size


def folder_type(view: CurrentView) -> FolderType:
    """The kind of folder a view shows: an outgoing folder (or its Favorites entry, or a pin of it) is a kind of its own, the rest is incoming."""
    if view.several_folders:
        return "search"
    if view.combined is not None:
        return _OUTGOING_FAVORITES.get(view.combined, "incoming") if view.key_kind is EntryKind.FAVORITE else "incoming"
    if view.kind is not EntryKind.FOLDER or view.folder is None:
        return "incoming"
    if view.folder_role in ("sent", "drafts"):
        return "sent" if view.folder_role == "sent" else "drafts"
    return _LOCAL_FOLDERS.get(view.folder_role or "", "incoming")


def setup_key(view: CurrentView) -> str:
    """The name a view's own setup is kept under: a folder (also opened through its pin) by account and path, an account's Unread
    and Flagged by account, a Favorites, group or Tags entry by its entry, a search of several folders under one name."""
    if view.several_folders:
        return SEARCH_KEY
    if view.combined is None and view.kind is EntryKind.FOLDER:
        return folder_key(view.account or "", view.folder or "")
    if view.combined is None:
        return f"{view.kind.value}\x1f{view.account or ''}"
    if view.key_kind is EntryKind.GROUPINBOX:
        return f"{EntryKind.GROUPINBOX.value}\x1f{','.join(view.combined_accounts)}"
    return f"{view.key_kind.value}\x1f{view.path}"


def folder_key(account: str, path: str) -> str:
    return f"folder\x1f{account}\x1f{path}"


def renamed_key(key: str, account: str, old: str, new: str) -> str | None:
    """The key of a folder setup after the folder (or a folder above it) was renamed; None when it is not about that folder."""
    start = folder_key(account, old)
    if key == start:
        return folder_key(account, new)
    if key.startswith(start + "/"):
        return folder_key(account, new) + key[len(start):]
    return None


def sort_shown(columns: tuple[str, ...], sort: str) -> bool:
    return any(column_id in columns for column_id in SORT_COLUMNS.get(sort, ()))


PEOPLE_SORTS: dict[str, str] = {
    sort: group for group, ways in PEOPLE_COLUMNS.items() for way, _ in ways if (sort := BY_ID[way].sort) is not None
}  # the sort of each way of showing From or To, and its column


def sort_of_way_shown(columns: tuple[str, ...], sort: str) -> str:
    """A sort of From or To follows the way the column shows (D-291): sorted by a way these columns do not show while they show
    another way of that column, they sort by the way shown. Every other sort stays."""
    group = PEOPLE_SORTS.get(sort)
    if group is None or sort_shown(columns, sort):
        return sort
    shown = people_shown(columns, group)
    found = BY_ID[shown].sort if shown is not None else None
    return found or sort


def saved_sort(columns: tuple[str, ...], sort: str, before_own_sorts: bool = True) -> str:
    """The sort of a saved setup: of From or To, the way shown (D-291). One saved before every column had a sort of its own (4.2.426) by
    sender belonged to the first column that showed it, From / To or From (Name only): it stays with that column. A sort chosen since is
    never changed."""
    if before_own_sorts and sort == "sender" and FROM_TO_SORT in columns:
        if "from" not in columns or columns.index(FROM_TO_SORT) < columns.index("from"):
            return FROM_TO_SORT  # where the arrow was
    return sort_of_way_shown(columns, sort)


def fallback_sort(columns: tuple[str, ...], sort: str) -> str:
    """The sort order of a folder whose columns are these: its own while a column shows it (of From or To: the way shown), else the first shown of Received,
    Sent, Subject, To, From, Size, else the first text column that sorts (never an icon column)."""
    sort = sort_of_way_shown(columns, sort)
    if sort_shown(columns, sort):
        return sort
    for candidate in FALLBACK_SORTS:
        if sort_shown(columns, candidate):
            return candidate
    for column_id in columns:
        column = BY_ID.get(column_id)
        if column is not None and column.sort is not None and not column.symbol:
            return column.sort
    return sort


def _before_own_sorts(found: object) -> bool:
    """A setup saved before every column had a sort of its own: no format, or one older than SETUP_FORMAT (a newer one is never converted)."""
    return not isinstance(found, int) or isinstance(found, bool) or found < SETUP_FORMAT


def _setup_from(data: object) -> tuple[ColumnSetup, FolderType] | None:
    if not isinstance(data, dict):
        return None
    columns, sort, oldest, widths, kind = data.get("columns"), data.get("sort"), data.get("oldest_first"), data.get("widths", ""), data.get("type")
    if not isinstance(columns, list) or not isinstance(sort, str) or sort not in SORT_COLUMNS or not isinstance(oldest, bool) or not isinstance(widths, str):
        return None
    if kind not in FOLDER_TYPES:
        return None
    chosen = valid_columns([str(column) for column in columns])
    return ColumnSetup(chosen, saved_sort(chosen, sort, _before_own_sorts(data.get("format"))), oldest, widths), kind


def read_setups(text: str) -> dict[str, tuple[ColumnSetup, FolderType]]:
    """Kept setups by name, each with the kind of folder it belongs to; anything unusable is left out."""
    try:
        data: object = load_json(text) if text else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    found: dict[str, tuple[ColumnSetup, FolderType]] = {}
    for key, value in data.items():
        setup = _setup_from(value)
        if isinstance(key, str) and setup is not None:
            found[key] = setup
    return found


def setups_text(setups: dict[str, tuple[ColumnSetup, FolderType]]) -> str:
    return json.dumps({
        key: {"type": kind, "columns": list(setup.columns), "sort": setup.sort, "oldest_first": setup.oldest_first, "widths": setup.widths, "format": SETUP_FORMAT}
        for key, (setup, kind) in setups.items()
    })
