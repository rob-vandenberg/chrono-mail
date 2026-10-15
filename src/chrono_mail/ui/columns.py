import json
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses

from chrono_mail.ui.formatting import coming_date, display_name, file_size, short_date

# --- Version ------------------------------------------------------------
__version__ = "columns.py 0.5.135"

# --- Version history ----------------------------------------------------
# v0.5.135: The Tag column (narrow; switched on in Settings > Message list or the menu of the column headers)
# v0.3.109: Every column but Delete sorts: Unread, Flagged, Attachment, Replied, Contact, Folder, Messages in conversation, Preview
# v0.2.100: Snoozed until: a column of the Snoozed folder only (sorts by the time a message comes back), added once after Received
# v0.2.94: A click on the To column sorts by To
# v0.1.89: Icon columns 24 px; Contact and Delete columns, added once to a saved set of columns; the address of the person a row shows
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.41: Initial columns of the message list

CARD: str = "card"  # the single column of the card layout


@dataclass(frozen=True, slots=True)
class Column:
    column_id: str
    label: str  # in the column editor and the header
    width: int
    sort: str | None = None  # the sort order a click on the header gives (date, subject, sender, to, size, snoozed, unread ...), if any
    right: bool = False
    symbol: bool = False  # a narrow column holding a symbol; its header shows the symbol


COLUMNS: tuple[Column, ...] = (
    Column("status", "Unread", 24, "unread", symbol=True),
    Column("flag", "Flagged", 24, "flagged", symbol=True),
    Column("attachment", "Attachment", 24, "attachment", symbol=True),
    Column("replied", "Replied", 24, "replied", symbol=True),
    Column("contact", "Contact", 24, "contact", symbol=True),
    Column("trash", "Delete", 24, symbol=True),
    Column("from_to", "From / To", 170, "sender"),
    Column("from", "From", 200, "sender"),
    Column("to", "To", 200, "to"),
    Column("subject", "Subject", 300, "subject"),
    Column("tag", "Tag", 60, symbol=True),  # a narrow column of its own width, as the icon columns: Subject keeps its room
    Column("date", "Received", 110, "date"),
    Column("size", "Size", 64, "size", right=True),
    Column("snoozed", "Snoozed until", 110, "snoozed"),
    Column("folder", "Folder", 110, "folder"),
    Column("conversation", "Messages in conversation", 60, "conversation", right=True),
    Column("preview", "Preview", 320, "preview"),
)
DEFAULT_COLUMNS: tuple[str, ...] = ("status", "flag", "attachment", "from_to", "subject", "date", "size", "contact", "trash")
NEW_COLUMNS: tuple[str, ...] = ("contact", "trash")  # added once to a set of columns saved before they existed
SNOOZED_COLUMN: str = "snoozed"  # shown in the Snoozed folder only; not in the column lists of Settings and the Message list window
SNOOZED_FOLDER_PATH: str = "Snoozed"
BY_ID: dict[str, Column] = {column.column_id: column for column in COLUMNS}
SYMBOLS: dict[str, str] = {"status": "●", "flag": "⚑", "attachment": "📎", "replied": "↩", "contact": "", "trash": "", "tag": ""}  # Contact and Delete draw icons, their headers stay empty
READ_MARK: str = "○"  # the dot of an unread message turns into an empty circle when it is read


def valid_columns(wanted: list[str]) -> tuple[str, ...]:
    """The known columns of a saved list, each once; the default when nothing usable is left."""
    seen: list[str] = []
    for column_id in wanted:
        if column_id in BY_ID and column_id not in seen:
            seen.append(column_id)
    return tuple(seen) or DEFAULT_COLUMNS


def with_new_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    """A saved set of columns with the columns that are new since it was saved added at the right."""
    return columns + tuple(column_id for column_id in NEW_COLUMNS if column_id not in columns)


def with_snoozed_column(columns: tuple[str, ...]) -> tuple[str, ...]:
    """Snoozed until added after Received (at the end without Received), once."""
    if SNOOZED_COLUMN in columns:
        return columns
    if "date" in columns:
        place = columns.index("date") + 1
        return columns[:place] + (SNOOZED_COLUMN,) + columns[place:]
    return columns + (SNOOZED_COLUMN,)


def keep_snoozed_column(chosen: tuple[str, ...], before: tuple[str, ...]) -> tuple[str, ...]:
    """A set of columns chosen where Snoozed until is not offered, with Snoozed until kept where it was (after the column it
    followed, else at the start) when it was in the set before."""
    if SNOOZED_COLUMN not in before or SNOOZED_COLUMN in chosen:
        return chosen
    place = before.index(SNOOZED_COLUMN)
    after = next((column_id for column_id in reversed(before[:place]) if column_id in chosen), None)
    position = chosen.index(after) + 1 if after is not None else 0
    return chosen[:position] + (SNOOZED_COLUMN,) + chosen[position:]


def without_snoozed_column(columns: tuple[str, ...]) -> tuple[str, ...]:
    shown = tuple(column_id for column_id in columns if column_id != SNOOZED_COLUMN)
    return shown or DEFAULT_COLUMNS


def person_address(row: object, recipients_first: bool) -> str:
    """The e-mail address (lower case) of the person the row shows: the sender, or in Sent, Drafts and Outbox the first recipient."""
    recipients = str(getattr(row, "recipients"))
    found = getaddresses([recipients if recipients_first and recipients else str(getattr(row, "sender"))])
    return found[0][1].strip().lower() if found else ""


def column_widths(text: str) -> dict[str, int]:
    """Saved widths ({"subject": 400, ...}); anything unusable is ignored."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in BY_ID and isinstance(value, int) and not isinstance(value, bool) and 16 <= value <= 4000}


def _first_address(text: str) -> str:
    """The first of several addresses 'Name <address>, other@example.org'."""
    found = getaddresses([text])
    if not found:
        return text
    name, address = found[0]
    return f"{name} <{address}>" if name else address


def date_text(moment: datetime | None, style: str) -> str:
    """The date in the chosen style: short (time today, weekday, date), full (date and time) or iso."""
    if moment is None:
        return ""
    if style == "full":
        return moment.astimezone().strftime("%d %b %Y %H:%M")
    if style == "iso":
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    return short_date(moment)


def cell_text(column_id: str, row: object, recipients_first: bool, date_style: str = "short") -> str:
    """What a column shows for a message row."""
    flags: tuple[str, ...] = getattr(row, "flags")
    sender, recipients = str(getattr(row, "sender")), str(getattr(row, "recipients"))
    if column_id == "status":
        return SYMBOLS["status"] if "seen" not in flags else READ_MARK
    if column_id == "flag":
        return SYMBOLS["flag"] if "flagged" in flags else ""
    if column_id == "attachment":
        return SYMBOLS["attachment"] if getattr(row, "attachments") else ""
    if column_id == "replied":
        return SYMBOLS["replied"] if "replied" in flags else ""
    if column_id == "from_to":
        return display_name(_first_address(recipients) if recipients_first and recipients else sender) or "(unknown)"
    if column_id == "from":
        return display_name(sender) or "(unknown)"
    if column_id == "to":
        return recipients
    if column_id == "subject":
        return str(getattr(row, "subject")) or "(no subject)"
    if column_id == "date":
        return date_text(getattr(row, "date"), date_style)
    if column_id == "size":
        return file_size(int(getattr(row, "size")))
    if column_id == SNOOZED_COLUMN:
        until = getattr(row, "snoozed_until", None)
        return coming_date(until) if date_style == "short" else date_text(until, date_style)
    if column_id == "folder":
        return str(getattr(row, "folder"))
    if column_id == "conversation":
        size = int(getattr(row, "thread_size", 1))
        return str(size) if size > 1 else ""
    if column_id == "preview":
        return str(getattr(row, "preview"))
    if column_id == "tag":
        return ", ".join(getattr(row, "tags", ()))
    return ""
