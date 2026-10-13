from dataclasses import dataclass, field, replace
from typing import Literal

from datetime import datetime
import string

from chrono_mail.core import Folder, FolderRole
from chrono_mail.engine.commands_mail import MessageRow, _rows, date_bounds
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import SearchFilter, SortField
from chrono_mail.index.model import DATE_GROUPS, SIZE_GROUPS, GroupCount

# --- Version ------------------------------------------------------------
__version__ = "commands_combined.py 0.2.94"

# --- Version history ----------------------------------------------------
# v0.2.94: Combined views in any sort order; message.combined-groups: their groups for the group headers
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Initial combined views over the mailboxes of all accounts (Favorites)

VIEWS: tuple[str, ...] = ("inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash")
_ROLE_OF_VIEW: dict[str, FolderRole] = {"inboxes": FolderRole.INBOX, "sent": FolderRole.SENT, "drafts": FolderRole.DRAFTS, "junk": FolderRole.JUNK, "trash": FolderRole.TRASH}


@dataclass(frozen=True, slots=True)
class CombinedParameters:
    view: Literal["inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash"] = field(metadata=option("inboxes, unread, flagged, outbox, sent, drafts, junk or trash"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("Only the mailboxes of these accounts (default: all)"))
    query: str = field(default="", metadata=option("Words to find in these messages (as in message.search)"))
    sort: Literal["date", "subject", "sender", "to", "size"] = field(default="date", metadata=option("The sort order"))
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many results to skip (not for message.combined-groups)"))
    limit: int = field(default=50, metadata=option("How many results to show (not for message.combined-groups)"))


_NO_CASE = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)  # as SQLite's NOCASE: only A to Z
_TEXT_OF_SORT: dict[str, str] = {"subject": "subject", "sender": "sender", "to": "recipients"}


def _row_key(row: MessageRow, sort: str) -> tuple[bool, float] | int | str:
    """The order of the index (message.search) for rows of several mailboxes, so that they can be merged."""
    if sort == "date":
        return (row.date is not None, row.date.timestamp() if row.date is not None else 0.0)  # without a date: last when newest first
    if sort == "size":
        return row.size
    return str(getattr(row, _TEXT_OF_SORT[sort])).translate(_NO_CASE)


def _chosen(view: str, folder: Folder) -> bool:
    if view in _ROLE_OF_VIEW:
        return folder.role is _ROLE_OF_VIEW[view]
    if view == "outbox":
        return folder.path == OUTBOX
    return folder.role not in (FolderRole.TRASH, FolderRole.JUNK) and folder.path != OUTBOX


def _filters(view: str) -> SearchFilter:
    return SearchFilter(unread=True if view == "unread" else None, flagged=True if view == "flagged" else None)


def _places(engine: Engine, accounts: tuple[str, ...]) -> list[str]:
    """One account id for every place where mail is kept: accounts that share Local Folders count once."""
    config = engine.config()
    wanted = {engine.resolve_account(name, config).account.account_id for name in accounts}
    places: list[str] = []
    for place, members in engine.storage_groups(config).items():
        ids = {item.account.account_id for item in members} | {place}
        if wanted and not ids & wanted:
            continue
        places.append(place if place in ids else members[0].account.account_id)
    return places


def message_combined(engine: Engine, parameters: CombinedParameters) -> tuple[MessageRow, ...]:
    """The messages of one kind of folder in every mailbox (all inboxes, all unread, all flagged and so on), newest first unless another order is asked for."""
    view = parameters.view
    want = parameters.offset + parameters.limit
    sort, descending = SortField(parameters.sort), not parameters.oldest_first
    found: list[MessageRow] = []
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox:
            for folder in mailbox.folders():
                if _chosen(view, folder):
                    rows = mailbox.index.search(parameters.query, folder.path, _filters(view), 0, want, sort, descending)
                    found.extend(replace(row, account=account) for row in _rows(mailbox, rows))
    found.sort(key=lambda row: _row_key(row, parameters.sort), reverse=descending)
    return tuple(found[parameters.offset:want])


def message_combined_groups(engine: Engine, parameters: CombinedParameters) -> tuple[GroupCount, ...]:
    """The groups of a combined view, in the order of message.combined with the same options, each with its number of messages."""
    view = parameters.view
    sort, descending = SortField(parameters.sort), not parameters.oldest_first
    bounds = date_bounds(datetime.now())
    counts: dict[str, GroupCount] = {}
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox:
            for folder in mailbox.folders():
                if _chosen(view, folder):
                    for group in mailbox.index.groups(bounds, parameters.query, folder.path, _filters(view), sort, descending):
                        known = counts.get(group.key)
                        counts[group.key] = group if known is None else replace(known, count=known.count + group.count)
    if sort is SortField.DATE:
        position = {key: number for number, (key, _) in enumerate(DATE_GROUPS)}
        no_date = len(DATE_GROUPS) - 1
        oldest = {key: -(no_date + 1) if number == no_date else -number for key, number in position.items()}  # oldest first: without a date at the top
        order = sorted(counts.values(), key=lambda group: position[group.key] if descending else oldest[group.key])
    elif sort is SortField.SIZE:
        position = {key: number for number, (key, _, _) in enumerate(SIZE_GROUPS)}
        order = sorted(counts.values(), key=lambda group: position[group.key], reverse=descending)
    else:
        order = sorted(counts.values(), key=lambda group: group.key, reverse=descending)
    return tuple(order)


def register(registry: CommandRegistry) -> None:
    registry.register("message.combined", "List the messages of the same kind of folder in all mailboxes", CombinedParameters, message_combined)
    registry.register("message.combined-groups", "The groups of a combined view (as sorted) with their number of messages", CombinedParameters, message_combined_groups)
