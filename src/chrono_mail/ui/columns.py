from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses

from chrono_mail.core import FolderRole
from chrono_mail.core.folder import OWN_FOLDERS
from chrono_mail.pim.times import local_time
from chrono_mail.engine.changes import MessageRow
from chrono_mail.ui.formatting import coming_date, display_name, file_size, short_date

# --- Version ------------------------------------------------------------
__version__ = "columns.py 4.2.421"

# --- Version history ----------------------------------------------------
# v4.2.421: one From and one To column, shown one way each (D-291): every way is called From or To; a saved list keeps at most one way of
#           each, the leftmost; people_group and people_shown tell the way of From or To a list shows
# v4.2.417: the people columns From (name and email), From (email), To (name), To (email), each sorting by what it shows (D-291), and
#           Account, the account that received the message (D-292); PEOPLE_COLUMNS: the columns the header's From and To submenus hold
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) known by their role, no longer by their name (CR-36)
# v3.6.349: the dates of the list on this computer's clock through the named zone (Windows, RV-201)
# v2.2.314: SEND_AT_FOLDERS: the Scheduled folder and the Outbox (a delayed send, B-21)
# v2.0.280: the Sent column (the moment the message carries itself, sorts by sent; "Saved" in Drafts); the standard columns are those of incoming mail, with From (B-16); the pixel widths of versions before 0.1.89 are no longer taken over
# v1.9.274: R-19 round 4: the rows are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.1.200: SCHEDULED_FOLDER_PATH: the Snoozed until column is Send at there
# v0.5.150: The standard order starts with Flag, Unread, Attachment
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
    Column("from", "From", 200, "sender"),  # the name, else the address
    Column("from_full", "From", 260, "sender_full"),  # the From column shown as name and email (D-291)
    Column("from_address", "From", 200, "sender_address"),  # the From column shown as the address only
    Column("to", "To", 200, "to"),  # names and addresses, as written
    Column("to_names", "To", 200, "to_names"),  # the To column shown as the names only
    Column("to_addresses", "To", 200, "to_addresses"),  # the To column shown as the addresses only
    Column("account", "Account", 140, "account"),
    Column("subject", "Subject", 300, "subject"),
    Column("tag", "Tag", 60, symbol=True),  # a narrow column of its own width, as the icon columns: Subject keeps its room
    Column("date", "Received", 110, "date"),
    Column("sent", "Sent", 110, "sent"),  # the message's own Date line; called Saved in Drafts (SAVED_LABEL)
    Column("size", "Size", 64, "size", right=True),
    Column("snoozed", "Snoozed until", 110, "snoozed"),
    Column("folder", "Folder", 110, "folder"),
    Column("conversation", "Messages in conversation", 60, "conversation", right=True),
    Column("preview", "Preview", 320, "preview"),
)
DEFAULT_COLUMNS: tuple[str, ...] = ("flag", "status", "attachment", "from", "subject", "date", "size", "contact", "trash")  # those of incoming mail (columnsetup.STARTING)
NEW_COLUMNS: tuple[str, ...] = ("contact", "trash")  # added once to a set of columns saved before they existed
SNOOZED_COLUMN: str = "snoozed"  # shown in the Snoozed folder only; not in the column lists of Settings and the Message list window
SNOOZED_FOLDER_PATH: str = OWN_FOLDERS[FolderRole.SNOOZED][0]
SCHEDULED_FOLDER_PATH: str = OWN_FOLDERS[FolderRole.SCHEDULED][0]  # its messages carry when they are sent in the same field: the column is called Send at there
SEND_AT_LABEL: str = "Send at"
OUTBOX_FOLDER_PATH: str = OWN_FOLDERS[FolderRole.OUTBOX][0]
SEND_AT_FOLDERS: tuple[str, ...] = (SCHEDULED_FOLDER_PATH, OUTBOX_FOLDER_PATH)  # the column says when each message is sent (the Outbox: a delayed send, B-21)
SAVED_LABEL: str = "Saved"  # the Sent column in Drafts: a draft carries when it was last saved
BY_ID: dict[str, Column] = {column.column_id: column for column in COLUMNS}
PEOPLE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {  # From and To: one column each, shown one of these ways (the column of each way, its name), D-291
    "from": (("from_full", "Name and email"), ("from", "Name only"), ("from_address", "Email only")),
    "to": (("to", "Name and email"), ("to_names", "Name only"), ("to_addresses", "Email only")),
}
SYMBOLS: dict[str, str] = {"status": "●", "flag": "⚑", "attachment": "📎", "replied": "↩", "contact": "", "trash": "", "tag": ""}  # Contact and Delete draw icons, their headers stay empty
READ_MARK: str = "○"  # the dot of an unread message turns into an empty circle when it is read


def people_group(column_id: str) -> str | None:
    """The column a way of showing people belongs to: "from" or "to"; None for every other column."""
    return next((group for group, ways in PEOPLE_COLUMNS.items() if any(way == column_id for way, _ in ways)), None)


def people_shown(columns: tuple[str, ...], group: str) -> str | None:
    """The way the From or To column is shown in these columns (the column of that way); None while it is not shown."""
    return next((column_id for column_id in columns if people_group(column_id) == group), None)


def valid_columns(wanted: list[str]) -> tuple[str, ...]:
    """The known columns of a saved list, each once, and From and To each one way only (the leftmost, D-291); the default when
    nothing usable is left."""
    seen: list[str] = []
    for column_id in wanted:
        group = people_group(column_id)
        if column_id in BY_ID and column_id not in seen and (group is None or people_shown(tuple(seen), group) is None):
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


def person_address(row: MessageRow, recipients_first: bool) -> str:
    """The e-mail address (lower case) of the person the row shows: the sender, or in Sent, Drafts and Outbox the first recipient."""
    recipients = row.recipients
    found = getaddresses([recipients if recipients_first and recipients else row.sender])
    return found[0][1].strip().lower() if found else ""


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
        return local_time(moment).strftime("%d %b %Y %H:%M")
    if style == "iso":
        return local_time(moment).strftime("%Y-%m-%d %H:%M")
    return short_date(moment)


def cell_text(column_id: str, row: MessageRow, recipients_first: bool, date_style: str = "short") -> str:
    """What a column shows for a message row."""
    flags: tuple[str, ...] = row.flags
    sender, recipients = row.sender, row.recipients
    if column_id == "status":
        return SYMBOLS["status"] if "seen" not in flags else READ_MARK
    if column_id == "flag":
        return SYMBOLS["flag"] if "flagged" in flags else ""
    if column_id == "attachment":
        return SYMBOLS["attachment"] if row.attachments else ""
    if column_id == "replied":
        return SYMBOLS["replied"] if "replied" in flags else ""
    if column_id == "from_to":
        return display_name(_first_address(recipients) if recipients_first and recipients else sender) or "(unknown)"
    if column_id == "from":
        return display_name(sender) or "(unknown)"
    if column_id == "from_full":
        return sender or "(unknown)"
    if column_id == "from_address":
        return row.sender_address or "(unknown)"
    if column_id == "to":
        return recipients
    if column_id == "to_names":
        return row.recipient_names
    if column_id == "to_addresses":
        return row.recipient_addresses
    if column_id == "account":
        return row.received_by
    if column_id == "subject":
        return row.subject or "(no subject)"
    if column_id == "date":
        return date_text(row.date, date_style)
    if column_id == "sent":
        return date_text(row.sent, date_style)
    if column_id == "size":
        return file_size(row.size)
    if column_id == SNOOZED_COLUMN:
        until = row.snoozed_until
        return coming_date(until) if date_style == "short" else date_text(until, date_style)
    if column_id == "folder":
        return row.folder
    if column_id == "conversation":
        size = row.thread_size
        return str(size) if size > 1 else ""
    if column_id == "preview":
        return row.preview
    if column_id == "tag":
        return ", ".join(row.tags)
    return ""
