from dataclasses import dataclass, field, replace
from typing import Literal

from datetime import datetime
import string

from chrono_mail.core import Folder, FolderRole
from chrono_mail.engine.commands_mail import CONTACT_SORT, EVERYTHING, LocateResult, MessageRow, PlacesResult, with_headings, _rows, contact_address_set, date_bounds, from_contacts, group_of
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import SearchFilter, SortField
from chrono_mail.index.model import DATE_GROUPS, SIZE_GROUPS, YES_NO_SORTS, GroupCount

# --- Version ------------------------------------------------------------
__version__ = "commands_combined.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: a Favorites view and its groups read each mailbox's index under its place lock (D-151)
# v0.5.163: message.combined-places can give the group heading of each message (headings)
# v0.5.162: stay_messages and stay_mailboxes: messages a Favorites view keeps after they were read, unflagged or untagged in it
# v0.5.160: message.combined-places: where several messages stand in a Favorites view, the order worked out once
# v0.5.156: message.combined-locate: where a message stands in a Favorites view, and its group heading
# v0.5.135: The view tagged: all mail with one tag (the Tags in the folder list)
# v0.3.109: Sort orders unread, flagged, attachment, replied, contact, folder, conversation and preview (newest first within the same value)
# v0.2.94: Combined views in any sort order; message.combined-groups: their groups for the group headers
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Initial combined views over the mailboxes of all accounts (Favorites)

VIEWS: tuple[str, ...] = ("inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash", "tagged")
_ROLE_OF_VIEW: dict[str, FolderRole] = {"inboxes": FolderRole.INBOX, "sent": FolderRole.SENT, "drafts": FolderRole.DRAFTS, "junk": FolderRole.JUNK, "trash": FolderRole.TRASH}


@dataclass(frozen=True, slots=True)
class CombinedParameters:
    view: Literal["inboxes", "unread", "flagged", "outbox", "sent", "drafts", "junk", "trash", "tagged"] = field(metadata=option("inboxes, unread, flagged, outbox, sent, drafts, junk, trash, or tagged (with tag)"))
    tag: str = field(default="", metadata=option("For the view tagged: the tag"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("Only the mailboxes of these accounts (default: all)"))
    query: str = field(default="", metadata=option("Words to find in these messages (as in message.search)"))
    sort: Literal["date", "subject", "sender", "to", "size", "unread", "flagged", "attachment", "replied", "contact", "folder", "conversation", "preview"] = field(
        default="date", metadata=option("The sort order (unread ... contact: yes first unless oldest_first)")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many results to skip (not for message.combined-groups)"))
    limit: int = field(default=50, metadata=option("How many results to show (not for message.combined-groups)"))
    stay_messages: tuple[str, ...] = field(default=(), metadata=option("A message that stays although it is no longer unread, flagged or tagged: it was changed while shown (repeat for more)"))
    stay_mailboxes: tuple[str, ...] = field(default=(), metadata=option("The mailbox of each message that stays, in the same order (repeat for more)"))


_NO_CASE = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)  # as SQLite's NOCASE: only A to Z
_TEXT_OF_SORT: dict[str, str] = {"subject": "subject", "sender": "sender", "to": "recipients", "folder": "folder", "preview": "preview"}
_NEWEST_WITHIN: frozenset[str] = frozenset({"unread", "flagged", "attachment", "replied", CONTACT_SORT, "folder", "conversation", "preview"})  # same value: newest first, either way


def _newest_key(row: MessageRow) -> tuple[bool, float]:
    return (row.date is not None, row.date.timestamp() if row.date is not None else 0.0)


def _value_key(row: MessageRow, sort: str) -> int | str:
    """The value the new sort orders compare (yes/no as 1 and 0), as the index orders it."""
    if sort == "unread":
        return int("seen" not in row.flags)
    if sort in ("flagged", "replied"):
        return int(sort in row.flags)
    if sort == "attachment":
        return int(row.attachments)
    if sort == "conversation":
        return row.thread_size
    return str(getattr(row, _TEXT_OF_SORT[sort])).translate(_NO_CASE)


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


def _filters(view: str, parameters: CombinedParameters, account: str) -> SearchFilter:
    if len(parameters.stay_mailboxes) != len(parameters.stay_messages):
        raise UsageError("Give the mailbox of each message that stays")
    stay = frozenset(message for mailbox, message in zip(parameters.stay_mailboxes, parameters.stay_messages, strict=True) if mailbox == account)
    return SearchFilter(
        unread=True if view == "unread" else None, flagged=True if view == "flagged" else None, tag=parameters.tag if view == "tagged" else None, stay=stay,
    )


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
    """The messages of one kind of folder in every mailbox (all inboxes, all unread, all flagged, all with a tag and so on), newest first unless another order is asked for."""
    view = parameters.view
    if view == "tagged" and not parameters.tag.strip():
        raise UsageError("The view tagged needs a tag")
    want = parameters.offset + parameters.limit
    contact = parameters.sort == CONTACT_SORT
    sort, descending = SortField.DATE if contact else SortField(parameters.sort), not parameters.oldest_first
    found: list[MessageRow] = []
    from_contact: dict[tuple[str, str], bool] = {}  # (account, store id) -> from (or to) a contact
    known = contact_address_set(engine) if contact else frozenset[str]()
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox, mailbox.reading():
            for folder in mailbox.folders():
                if _chosen(view, folder):
                    rows = mailbox.index.search(parameters.query, folder.path, _filters(view, parameters, account), 0, EVERYTHING if contact else want, sort, descending)
                    if contact:  # the index does not know the contacts: every message is looked at
                        from_contact.update(((account, store_id), yes) for store_id, yes in from_contacts(mailbox, rows, known).items())
                    found.extend(replace(row, account=account) for row in _rows(mailbox, rows))
    if parameters.sort in _NEWEST_WITHIN:
        found.sort(key=_newest_key, reverse=True)
        if contact:
            found.sort(key=lambda row: from_contact.get((row.account, row.message_id), False), reverse=descending)
        else:
            found.sort(key=lambda row: _value_key(row, parameters.sort), reverse=descending)
    else:
        found.sort(key=lambda row: _row_key(row, parameters.sort), reverse=descending)
    return tuple(found[parameters.offset:want])


def message_combined_groups(engine: Engine, parameters: CombinedParameters) -> tuple[GroupCount, ...]:
    """The groups of a combined view, in the order of message.combined with the same options, each with its number of messages."""
    view = parameters.view
    if parameters.sort == CONTACT_SORT or SortField(parameters.sort) in YES_NO_SORTS:
        return ()  # the yes/no sorts have no group headers
    sort, descending = SortField(parameters.sort), not parameters.oldest_first
    bounds = date_bounds(datetime.now())
    counts: dict[str, GroupCount] = {}
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox, mailbox.reading():
            for folder in mailbox.folders():
                if _chosen(view, folder):
                    for group in mailbox.index.groups(bounds, parameters.query, folder.path, _filters(view, parameters, account), sort, descending):
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


@dataclass(frozen=True, slots=True)
class CombinedLocateParameters(CombinedParameters):
    account: str = field(default="", metadata=option("The account (mailbox) the message is in"))
    message: str = field(default="", metadata=option("The message id"))


def message_combined_locate(engine: Engine, parameters: CombinedLocateParameters) -> LocateResult:
    """Where a message stands in a Favorites view, and its group heading (asked when a message comes into the view shown)."""
    everything = message_combined(engine, replace(parameters, offset=0, limit=EVERYTHING))
    wanted = engine.resolve_account(parameters.account).account.account_id if parameters.account else ""
    position = next((number for number, row in enumerate(everything) if row.message_id == parameters.message and (not wanted or row.account == wanted)), None)
    if position is None:
        return LocateResult(None)
    key, name = group_of(message_combined_groups(engine, parameters), position)
    return LocateResult(position, key, name)


@dataclass(frozen=True, slots=True)
class CombinedPlacesParameters(CombinedParameters):
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    mailboxes: tuple[str, ...] = field(default=(), metadata=option("The account (mailbox) of each message, in the same order (repeat for more)"))
    headings: bool = field(default=False, metadata=option("Also the group heading each message falls under"))


def message_combined_places(engine: Engine, parameters: CombinedPlacesParameters) -> PlacesResult:
    """Where several messages stand in a Favorites view; the order is worked out once, however many messages are asked."""
    if len(parameters.mailboxes) != len(parameters.messages):
        raise UsageError("Give the mailbox of each message")
    everything = message_combined(engine, replace(parameters, offset=0, limit=EVERYTHING))
    places = {(row.account, row.message_id): number for number, row in reversed(list(enumerate(everything)))}  # the first place when one message is shown twice
    owners = {mailbox: engine.resolve_account(mailbox).account.account_id for mailbox in set(parameters.mailboxes)}
    positions = tuple(places.get((owners[mailbox], message_id)) for mailbox, message_id in zip(parameters.mailboxes, parameters.messages, strict=True))
    if not parameters.headings or all(position is None for position in positions):
        return PlacesResult(positions)
    return with_headings(positions, message_combined_groups(engine, parameters))


def register(registry: CommandRegistry) -> None:
    registry.register("message.combined", "List the messages of the same kind of folder in all mailboxes", CombinedParameters, message_combined)
    registry.register("message.combined-locate", "Where a message stands in a Favorites view, and its group heading", CombinedLocateParameters, message_combined_locate)
    registry.register("message.combined-places", "Where several messages stand in a Favorites view (0 is the first)", CombinedPlacesParameters, message_combined_places)
    registry.register("message.combined-groups", "The groups of a combined view (as sorted) with their number of messages", CombinedParameters, message_combined_groups)
