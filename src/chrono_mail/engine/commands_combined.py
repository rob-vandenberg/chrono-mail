from dataclasses import dataclass, field, replace
import typing
from typing import Literal

from datetime import datetime
import string

from chrono_mail.core import Folder, FolderRole, shown_name
from chrono_mail.engine.commands_mail import CONTACT_SORT, LATER, timed_only, EVERYTHING, LocateResult, MessageRow, PlacesResult, leave_out, neighbours, not_heard, with_headings, _day, _rows, contact_address_set, date_bounds, from_contacts, group_of, RECIPIENT_VIEWS
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.schedule import SNOOZE_GROUPS, snooze_group
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.index import SearchFilter, SortField
from chrono_mail.index.model import DATE_GROUPS, SIZE_GROUPS, YES_NO_SORTS, GroupCount

# --- Version ------------------------------------------------------------
__version__ = "commands_combined.py 4.2.432"

# --- Version history ----------------------------------------------------
# v4.2.432: the Contact sort of a Favorites view judges every row by the person it shows (RECIPIENT_VIEWS)
# v4.2.430: to_shown merged by the row's own value
# v4.2.429: the sort order to_shown (From / To for mail one sent, D-295)
# v4.2.426: Favorites > Snoozed, Scheduled and Outbox carry when each message leaves (comes back, is sent) in their first rows too, and sort
#           and group by it (sort order snoozed), as their folders do
# v4.2.417: the sort orders of the people columns (From (name and email), From (email), To (name), To (email), D-291) and of Account (D-292)
# v4.2.413: the view everywhere: every folder of every account, for a search of All folders shown from a Favorites or Tags view (D-287)
# v4.2.408: the Favorites and Tags views take sent_from and sent_to, snoozed_from and snoozed_to, send_at_from and send_at_to (days,
#           D-288); the filters are made once per mailbox
# v3.6.368: message.combined-places and message.combined-locate read the rows and the groups of each mailbox together: an Undo between
#           them put messages under the wrong heading (a held Ctrl+Z)
# v3.6.354: Chrono Mail's own folders known by their role, their names written once (core OWN_FOLDERS) (CR-36)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.0.280: the sort order sent (the message's own Date) merges and groups like Received (B-16)
# v1.9.270: R-19: typed message.combined-places
# v1.6.260: gather(): each mailbox read once under its lock for the rows, the groups and the numbers of a view together; message.combined-places
#           also names the message each one comes before (before, before_mailboxes); the whole order of a view is message.combined-order (R-15)
# v1.1.242: The filters of a folder view in Favorites and Tags views too: unread, flagged, has_attachments, filter_tag, the days and sizes of
#           Advanced search; they narrow the view (R-03)
# v1.1.202: The view scheduled (every Scheduled folder), like snoozed
# v1.1.200: The Scheduled folder is, like the Outbox, never part of the Unread, Flagged, Unreplied or Tags views
# v1.1.191: The views snoozed (every Snoozed folder), unreplied (inbox mail without the Replied mark) and archive (every Archive folder)
# v0.5.166: a Favorites view sorted by sender sorts by the name the From column shows, as the index does
# v0.5.165: message.combined-places counts only the messages the asker has heard of (D-152)
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

ViewName = Literal["inboxes", "unread", "flagged", "outbox", "scheduled", "sent", "drafts", "snoozed", "unreplied", "archive", "junk", "trash", "tagged", "everywhere"]  # the Favorites and Tags views, and every folder
CombinedSort = Literal[
    "date", "sent", "subject", "sender", "to", "size", "snoozed", "unread", "flagged", "attachment", "replied", "contact", "folder", "conversation", "preview",
    "sender_full", "sender_address", "to_names", "to_addresses", "account", "to_shown",
]  # their sort orders (snoozed: when a message leaves Snoozed, Scheduled or the Outbox; any other message after those)
TIMED_SORT: str = "snoozed"
EVERYWHERE: str = "everywhere"  # every folder of every account, Junk and Trash too: a search of All folders from a Favorites view (D-287)
VIEWS: tuple[str, ...] = tuple(view for view in typing.get_args(ViewName) if view != EVERYWHERE)  # the standard favorites
_ROLE_OF_VIEW: dict[str, FolderRole] = {
    "inboxes": FolderRole.INBOX, "unreplied": FolderRole.INBOX, "sent": FolderRole.SENT, "drafts": FolderRole.DRAFTS, "archive": FolderRole.ARCHIVE,
    "junk": FolderRole.JUNK, "trash": FolderRole.TRASH, "outbox": FolderRole.OUTBOX, "snoozed": FolderRole.SNOOZED, "scheduled": FolderRole.SCHEDULED,
}


@dataclass(frozen=True, slots=True)
class CombinedParameters:
    view: ViewName = field(
        metadata=option("inboxes, unread, flagged, outbox, scheduled, sent, drafts, snoozed, unreplied (inbox mail without the Replied mark), archive, junk, trash, tagged (with tag), or everywhere (every folder)")
    )
    tag: str = field(default="", metadata=option("For the view tagged: the tag"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("Only the mailboxes of these accounts (default: all)"))
    query: str = field(default="", metadata=option("Words to find in these messages (as in message.search)"))
    unread: bool | None = field(default=None, metadata=option("Only unread (true) or only read (false) messages of the view"))
    flagged: bool | None = field(default=None, metadata=option("Only flagged (true) or only unflagged (false) messages of the view"))
    has_attachments: bool | None = field(default=None, metadata=option("Only messages with (true) or without (false) attachments"))
    filter_tag: str | None = field(default=None, metadata=option("Only messages of the view with this tag (for the view tagged: with this tag too)"))
    date_from: str | None = field(default=None, metadata=option("Only messages from this day on (YYYY-MM-DD, your local time)"))
    date_to: str | None = field(default=None, metadata=option("Only messages up to and including this day (YYYY-MM-DD)"))
    minimum_size: int | None = field(default=None, metadata=option("Only messages of at least this many bytes"))
    maximum_size: int | None = field(default=None, metadata=option("Only messages of at most this many bytes"))
    sent_from: str | None = field(default=None, metadata=option("Only messages sent from this day on, by their own date (YYYY-MM-DD)"))
    sent_to: str | None = field(default=None, metadata=option("Only messages sent up to and including this day, by their own date (YYYY-MM-DD)"))
    snoozed_from: str | None = field(default=None, metadata=option("Only snoozed messages that come back from this day on (YYYY-MM-DD)"))
    snoozed_to: str | None = field(default=None, metadata=option("Only snoozed messages that come back up to and including this day (YYYY-MM-DD)"))
    send_at_from: str | None = field(default=None, metadata=option("Only messages waiting to be sent from this day on (YYYY-MM-DD)"))
    send_at_to: str | None = field(default=None, metadata=option("Only messages waiting to be sent up to and including this day (YYYY-MM-DD)"))
    sort: CombinedSort = field(
        default="date", metadata=option("The sort order (unread ... contact: yes first unless oldest_first)")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many results to skip (not for message.combined-groups)"))
    limit: int = field(default=50, metadata=option("How many results to show (not for message.combined-groups)"))
    stay_messages: tuple[str, ...] = field(default=(), metadata=option("A message that stays although it is no longer unread, flagged or tagged: it was changed while shown (repeat for more)"))
    stay_mailboxes: tuple[str, ...] = field(default=(), metadata=option("The mailbox of each message that stays, in the same order (repeat for more)"))


_NO_CASE = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)  # as SQLite's NOCASE: only A to Z
_TEXT_OF_SORT: dict[str, str] = {
    "subject": "subject", "sender": "sender", "to": "recipients", "folder": "folder", "preview": "preview",
    "sender_full": "sender", "sender_address": "sender_address", "to_names": "recipient_names", "to_addresses": "recipient_addresses", "account": "received_by",  # D-291, D-292
}
_NEWEST_WITHIN: frozenset[str] = frozenset({"unread", "flagged", "attachment", "replied", CONTACT_SORT, "folder", "conversation", "preview", TIMED_SORT})  # same value: newest first, either way


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
    if sort == TIMED_SORT:  # as its folder orders it: by the moment, a message without one after all others
        return int(row.snoozed_until.timestamp()) if row.snoozed_until is not None else LATER
    return str(getattr(row, _TEXT_OF_SORT[sort])).translate(_NO_CASE)


def _row_key(row: MessageRow, sort: str) -> tuple[bool, float] | int | str:
    """The order of the index (message.search) for rows of several mailboxes, so that they can be merged."""
    if sort in ("date", "sent"):
        moment = row.date if sort == "date" else row.sent
        return (moment is not None, moment.timestamp() if moment is not None else 0.0)  # without a date: last when newest first
    if sort == "size":
        return row.size
    if sort == "sender":
        return shown_name(row.sender).translate(_NO_CASE)  # as the index sorts it: the name the From column shows
    if sort == "to_shown":
        return row.to_shown.translate(_NO_CASE)  # as the index sorts it: the person From / To shows (D-295)
    return str(getattr(row, _TEXT_OF_SORT[sort])).translate(_NO_CASE)


def _chosen(view: str, folder: Folder) -> bool:
    if view == EVERYWHERE:
        return True
    if view in _ROLE_OF_VIEW:
        return folder.role is _ROLE_OF_VIEW[view]
    return folder.role not in (FolderRole.TRASH, FolderRole.JUNK, FolderRole.OUTBOX, FolderRole.SCHEDULED)


def _narrowed(name: str, own: bool | None, chosen: bool | None) -> bool | None:
    """The view's own mark (all unread, all flagged) and the filter's on the same mark: both must hold."""
    if own is not None and chosen is not None and own != chosen:
        raise UsageError(f"This view holds only messages with {name} {'on' if own else 'off'}")
    return own if own is not None else chosen


def _filters(view: str, parameters: CombinedParameters, mailbox: Mailbox, account: str) -> SearchFilter:
    """What the view holds (its own marks or tag), narrowed by the filters chosen (R-03)."""
    if len(parameters.stay_mailboxes) != len(parameters.stay_messages):
        raise UsageError("Give the mailbox of each message that stays")
    stay = frozenset(message for mailbox, message in zip(parameters.stay_mailboxes, parameters.stay_messages, strict=True) if mailbox == account)
    own_tag = parameters.tag if view == "tagged" else None
    chosen_tag = (parameters.filter_tag or "").strip() or None
    tag, also_tag = (own_tag, chosen_tag) if own_tag else (chosen_tag, None)
    return SearchFilter(
        unread=_narrowed("unread", True if view == "unread" else None, parameters.unread),
        flagged=_narrowed("flagged", True if view == "flagged" else None, parameters.flagged),
        replied=False if view == "unreplied" else None, has_attachments=parameters.has_attachments,
        date_from=_day(parameters.date_from, "date_from", end=False), date_to=_day(parameters.date_to, "date_to", end=True),
        minimum_size=parameters.minimum_size, maximum_size=parameters.maximum_size, tag=tag, also_tag=also_tag, stay=stay,
        sent_from=_day(parameters.sent_from, "sent_from", end=False), sent_to=_day(parameters.sent_to, "sent_to", end=True),
        only=timed_only(mailbox, (parameters.snoozed_from, parameters.snoozed_to), (parameters.send_at_from, parameters.send_at_to)),
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


@dataclass
class Gathered:
    """What each mailbox of a Favorites view gave, each read under that mailbox's lock (D-151): its rows (as many as asked),
    its group counts (when asked), how many messages and unread messages it holds (when asked), and for the Contact sort
    which rows are from (or to) a contact."""

    rows: list[MessageRow] = field(default_factory=list)
    groups: dict[str, GroupCount] = field(default_factory=dict)
    total: int = 0
    unread: int = 0
    from_contact: dict[tuple[str, str], bool] = field(default_factory=dict)  # (account, store id) -> from (or to) a contact


def gather(engine: Engine, parameters: CombinedParameters, want: int | None, groups: bool = False, tally: bool = False) -> Gathered:
    """Read every mailbox of the view once (want: the first rows of each folder, None for none). The rows, the groups and the
    numbers of one mailbox are read together, so they describe the same moment (R-15)."""
    view = parameters.view
    if view == "tagged" and not parameters.tag.strip():
        raise UsageError("The view tagged needs a tag")
    contact, timed = parameters.sort == CONTACT_SORT, parameters.sort == TIMED_SORT
    sort, descending = SortField.DATE if contact or timed else SortField(parameters.sort), not parameters.oldest_first
    grouped = groups and not contact and not timed and sort not in YES_NO_SORTS  # the yes/no sorts have no group headers
    bounds = date_bounds(datetime.now())
    now = datetime.fromtimestamp(engine.clock()).astimezone()
    names = dict(SNOOZE_GROUPS)
    known = contact_address_set(engine) if contact and want is not None else frozenset[str]()
    found = Gathered()
    for account in _places(engine, parameters.accounts):
        with open_mailbox(engine, account) as mailbox, mailbox.reading():
            filters = _filters(view, parameters, mailbox, account)  # once per mailbox: the same for each of its folders
            for folder in mailbox.folders():
                if not _chosen(view, folder):
                    continue
                times = mailbox.timed(folder.path)  # Snoozed, Scheduled, the Outbox: when each message leaves it; None for any other folder
                rows = None
                if want is not None:
                    rows = mailbox.index.search(parameters.query, folder.path, filters, 0, EVERYTHING if contact or timed else want, sort, descending)
                    if contact:  # the index does not know the contacts: every message is looked at
                        found.from_contact.update(((account, store_id), yes) for store_id, yes in from_contacts(rows, known, view in RECIPIENT_VIEWS).items())
                    found.rows.extend(replace(row, account=account) for row in _rows(mailbox, rows, times))
                if groups and timed:  # the groups of its folder: when each message leaves, never in the index
                    everything = rows if rows is not None else mailbox.index.search(parameters.query, folder.path, filters, 0, EVERYTHING, SortField.DATE, True)
                    for message in everything:
                        key = snooze_group((times or {}).get(message.store_id), now)
                        before = found.groups.get(key)
                        found.groups[key] = GroupCount(key, names[key], 1 if before is None else before.count + 1)
                if grouped:
                    for group in mailbox.index.groups(bounds, parameters.query, folder.path, filters, sort, descending):
                        before = found.groups.get(group.key)
                        found.groups[group.key] = group if before is None else replace(before, count=before.count + group.count)
                if tally:
                    total, unread = mailbox.index.tally(parameters.query, folder.path, filters)
                    found.total += total
                    found.unread += unread
    return found


def in_order(found: Gathered, parameters: CombinedParameters) -> list[MessageRow]:
    """The rows of all mailboxes merged in the view's order, as the index orders one mailbox (message.search)."""
    rows = list(found.rows)
    descending = not parameters.oldest_first
    if parameters.sort in _NEWEST_WITHIN:
        rows.sort(key=_newest_key, reverse=True)
        if parameters.sort == CONTACT_SORT:
            rows.sort(key=lambda row: found.from_contact.get((row.account, row.message_id), False), reverse=descending)
        else:
            rows.sort(key=lambda row: _value_key(row, parameters.sort), reverse=descending)
    else:
        rows.sort(key=lambda row: _row_key(row, parameters.sort), reverse=descending)
    return rows


def groups_in_order(found: Gathered, parameters: CombinedParameters) -> tuple[GroupCount, ...]:
    """The groups of all mailboxes together, in the order of the merged rows."""
    if not found.groups:
        return ()
    if parameters.sort == TIMED_SORT:  # soonest first, as its folder
        order = SNOOZE_GROUPS if parameters.oldest_first else tuple(reversed(SNOOZE_GROUPS))
        return tuple(found.groups[key] for key, _ in order if key in found.groups)
    sort, descending = SortField(parameters.sort), not parameters.oldest_first
    if sort in (SortField.DATE, SortField.SENT):
        position = {key: number for number, (key, _) in enumerate(DATE_GROUPS)}
        no_date = len(DATE_GROUPS) - 1
        oldest = {key: -(no_date + 1) if number == no_date else -number for key, number in position.items()}  # oldest first: without a date at the top
        return tuple(sorted(found.groups.values(), key=lambda group: position[group.key] if descending else oldest[group.key]))
    if sort is SortField.SIZE:
        position = {key: number for number, (key, _, _) in enumerate(SIZE_GROUPS)}
        return tuple(sorted(found.groups.values(), key=lambda group: position[group.key], reverse=descending))
    return tuple(sorted(found.groups.values(), key=lambda group: group.key, reverse=descending))


def message_combined(engine: Engine, parameters: CombinedParameters) -> tuple[MessageRow, ...]:
    """The messages of one kind of folder in every mailbox (all inboxes, all unread, all flagged, all with a tag and so on), newest first unless another order is asked for."""
    want = parameters.offset + parameters.limit
    return tuple(in_order(gather(engine, parameters, want), parameters)[parameters.offset:want])


def message_combined_groups(engine: Engine, parameters: CombinedParameters) -> tuple[GroupCount, ...]:
    """The groups of a combined view, in the order of message.combined with the same options, each with its number of messages."""
    return groups_in_order(gather(engine, parameters, None, groups=True), parameters)


@dataclass(frozen=True, slots=True)
class CombinedLocateParameters(CombinedParameters):
    account: str = field(default="", metadata=option("The account (mailbox) the message is in"))
    message: str = field(default="", metadata=option("The message id"))


def message_combined_locate(engine: Engine, parameters: CombinedLocateParameters) -> LocateResult:
    """Where a message stands in a Favorites view, and its group heading (asked when a message comes into the view shown). The rows
    and the groups of each mailbox are read together: a change between them made the heading wrong."""
    found = gather(engine, parameters, EVERYTHING, groups=True)
    everything = in_order(found, parameters)
    wanted = engine.resolve_account(parameters.account).account.account_id if parameters.account else ""
    position = next((number for number, row in enumerate(everything) if row.message_id == parameters.message and (not wanted or row.account == wanted)), None)
    if position is None:
        return LocateResult(None)
    key, name = group_of(groups_in_order(found, parameters), position)
    return LocateResult(position, key, name)


@dataclass(frozen=True, slots=True)
class CombinedPlacesParameters(CombinedParameters):
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    mailboxes: tuple[str, ...] = field(default=(), metadata=option("The account (mailbox) of each message, in the same order (repeat for more)"))
    headings: bool = field(default=False, metadata=option("Also the group heading each message falls under"))
    heard: tuple[str, ...] = field(default=(), metadata=option("mailbox=number: the last fact the asker heard of a mailbox; messages that came in later are not counted (repeat for more)"))


def message_combined_places(engine: Engine, parameters: CombinedPlacesParameters) -> PlacesResult:
    """Where several messages stand in a Favorites view; the order is worked out once, however many messages are asked. The rows and
    the groups of each mailbox are read together: an Undo between them made the headings one off (a held Ctrl+Z)."""
    if len(parameters.mailboxes) != len(parameters.messages):
        raise UsageError("Give the mailbox of each message")
    found = gather(engine, parameters, EVERYTHING, groups=parameters.headings)
    everything = in_order(found, parameters)
    missing = not_heard(engine, parameters.heard, {row.account for row in everything})  # after the reading: a superset of what it counted
    if missing is None:
        return PlacesResult(tuple(None for _ in parameters.messages), known=False)
    places = {(row.account, row.message_id): number for number, row in reversed(list(enumerate(everything)))}  # the first place when one message is shown twice
    owners = {mailbox: engine.resolve_account(mailbox).account.account_id for mailbox in set(parameters.mailboxes)}
    keys = [(owners[mailbox], message_id) for mailbox, message_id in zip(parameters.mailboxes, parameters.messages, strict=True)]
    asked = set(keys)
    unheard = {(place, message_id) for (place, message_id) in places if message_id in missing.get(place, set()) and (place, message_id) not in asked}
    left = [places[key] for key in unheard]
    everywhere = [places.get(key) for key in keys]
    following = neighbours([(row.account, row.message_id) for row in everything], keys, unheard)
    before, before_mailboxes = tuple(found[1] if found else "" for found in following), tuple(found[0] if found else "" for found in following)
    positions = leave_out(everywhere, left)
    if not parameters.headings or all(position is None for position in positions):
        return PlacesResult(positions, before=before, before_mailboxes=before_mailboxes)
    return with_headings(positions, groups_in_order(found, parameters), tuple(everywhere), before, before_mailboxes)


MESSAGE_COMBINED_PLACES: TypedCommand[CombinedPlacesParameters, PlacesResult] = TypedCommand("message.combined-places", CombinedPlacesParameters, PlacesResult)


def register(registry: CommandRegistry) -> None:
    registry.register("message.combined", "List the messages of the same kind of folder in all mailboxes", CombinedParameters, message_combined, doing="Loading the messages")
    registry.register("message.combined-locate", "Where a message stands in a Favorites view, and its group heading", CombinedLocateParameters, message_combined_locate, doing="Finding a message in the list")
    registry.register(MESSAGE_COMBINED_PLACES.id, "Where several messages stand in a Favorites view (0 is the first)", CombinedPlacesParameters, message_combined_places, doing="Finding messages in the list")
    registry.register("message.combined-groups", "The groups of a combined view (as sorted) with their number of messages", CombinedParameters, message_combined_groups, doing="Loading the messages")
