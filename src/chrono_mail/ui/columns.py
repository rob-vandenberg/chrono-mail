import json
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses

from chrono_mail.ui.formatting import display_name, file_size, short_date

# --- Version ------------------------------------------------------------
__version__ = "columns.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial columns of the message list

CARD: str = "card"  # the single column of the card layout


@dataclass(frozen=True, slots=True)
class Column:
    column_id: str
    label: str  # in the column editor and the header
    width: int
    sort: str | None = None  # the sort order a click on the header gives (date, subject, sender, size), if any
    right: bool = False
    symbol: bool = False  # a narrow column holding a symbol; its header shows the symbol


COLUMNS: tuple[Column, ...] = (
    Column("status", "Unread", 26, symbol=True),
    Column("flag", "Flagged", 26, symbol=True),
    Column("attachment", "Attachment", 26, symbol=True),
    Column("replied", "Replied", 26, symbol=True),
    Column("from_to", "From / To", 170, "sender"),
    Column("from", "From", 200, "sender"),
    Column("to", "To", 200),
    Column("subject", "Subject", 300, "subject"),
    Column("date", "Date", 110, "date"),
    Column("size", "Size", 64, "size", right=True),
    Column("folder", "Folder", 110),
    Column("conversation", "Messages in conversation", 60, right=True),
    Column("preview", "Preview", 320),
)
DEFAULT_COLUMNS: tuple[str, ...] = ("status", "flag", "attachment", "from_to", "subject", "date", "size")
BY_ID: dict[str, Column] = {column.column_id: column for column in COLUMNS}
SYMBOLS: dict[str, str] = {"status": "●", "flag": "⚑", "attachment": "📎", "replied": "↩"}


def valid_columns(wanted: list[str]) -> tuple[str, ...]:
    """The known columns of a saved list, each once; the default when nothing usable is left."""
    seen: list[str] = []
    for column_id in wanted:
        if column_id in BY_ID and column_id not in seen:
            seen.append(column_id)
    return tuple(seen) or DEFAULT_COLUMNS


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
        return SYMBOLS["status"] if "seen" not in flags else ""
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
    if column_id == "folder":
        return str(getattr(row, "folder"))
    if column_id == "conversation":
        size = int(getattr(row, "thread_size", 1))
        return str(size) if size > 1 else ""
    if column_id == "preview":
        return str(getattr(row, "preview"))
    return ""
