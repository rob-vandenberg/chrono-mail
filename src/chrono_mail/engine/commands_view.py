from dataclasses import dataclass, field, replace

from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.commands_combined import CombinedParameters, gather, groups_in_order, in_order
from chrono_mail.engine.commands_mail import CONTACT_SORT, EVERYTHING, TIMED, SearchParameters, _rows, _search_filter, _snooze_sort, _sort_field, message_groups, message_search
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.index import SearchFilter
from chrono_mail.index.model import GroupCount

# --- Version ------------------------------------------------------------
__version__ = "commands_view.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.6.260: Initial: message.order and message.combined-order (the whole order of a view: every message's id, folder and unread mark, the
#           headings, the first rows), message.rows (rows asked by the message, never by a place) (R-15)

FIRST_ROWS: int = 100  # the rows of the start of a view that come with its order


@dataclass(frozen=True, slots=True)
class ViewOrder:
    """The order of a view: every message it holds, or only the first ones (complete False: a large view shows its start
    before its whole order is read). Everything in it was read at one moment per mailbox."""

    messages: tuple[str, ...]  # the message ids in the view's order
    mailboxes: tuple[str, ...]  # a Favorites view: the mailbox of each message; empty for a view of one mailbox
    folders: tuple[str, ...]  # the folder of each message ("Inbox", "Archive/2026")
    unread: tuple[bool, ...]  # whether each message is unread
    total: int  # how many messages the view holds (also when only the first ones are given)
    unread_total: int
    complete: bool  # every message of the view is given
    groups: tuple[GroupCount, ...]  # the group headings in order with their numbers of messages (asked with headings)
    rows: tuple[MessageRow, ...]  # the rows of the first messages and of the messages named


@dataclass(frozen=True, slots=True)
class OrderParameters(SearchParameters):
    """A folder or a search of one mailbox, as message.search takes it (offset and limit are not used)."""

    whole: bool = field(default=True, metadata=option("Every message of the view; otherwise only the first ones (with the numbers of all)"))
    first: int = field(default=FIRST_ROWS, metadata=option("How many messages of the start come with their rows"))
    rows_of: tuple[str, ...] = field(default=(), metadata=option("Also the row of this message (repeat for more)"))
    headings: bool = field(default=False, metadata=option("Also the group headings with their numbers of messages"))


@dataclass(frozen=True, slots=True)
class CombinedOrderParameters(CombinedParameters):
    """A Favorites or Tags view, as message.combined takes it (offset and limit are not used)."""

    whole: bool = field(default=True, metadata=option("Every message of the view; otherwise only the first ones (with the numbers of all)"))
    first: int = field(default=FIRST_ROWS, metadata=option("How many messages of the start come with their rows"))
    rows_of: tuple[str, ...] = field(default=(), metadata=option("Also the row of this message (repeat for more)"))
    rows_of_mailboxes: tuple[str, ...] = field(default=(), metadata=option("The mailbox of each message of rows_of, in the same order (repeat for more)"))
    headings: bool = field(default=False, metadata=option("Also the group headings with their numbers of messages"))


def _shared(text: str, seen: dict[str, str]) -> str:
    """One string object per folder name: half a million messages of one folder hold one name, not half a million copies."""
    return seen.setdefault(text, text)


def message_order(engine: Engine, parameters: OrderParameters) -> ViewOrder:
    """The order of a folder or a search of one mailbox, read at one moment: what the list holds of a view (R-15)."""
    first = max(0, parameters.first)
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        groups = message_groups(engine, parameters) if parameters.headings else ()  # inside the same reading: the same moment
        names: dict[str, str] = {}
        if parameters.sort == CONTACT_SORT or _snooze_sort(parameters.sort, folder):  # worked out here, over every message: always whole
            everything = message_search(engine, replace(parameters, offset=0, limit=EVERYTHING))
            wanted = {row.message_id for row in everything[:first]} | set(parameters.rows_of)
            return ViewOrder(
                tuple(row.message_id for row in everything), (), tuple(_shared(row.folder, names) for row in everything), tuple("seen" not in row.flags for row in everything),
                len(everything), sum(1 for row in everything if "seen" not in row.flags), True, groups, tuple(row for row in everything if row.message_id in wanted),
            )
        filters = _search_filter(parameters)
        found = mailbox.index.ordered(parameters.query, folder, filters, _sort_field(parameters.sort), not parameters.oldest_first, None if parameters.whole else first)
        if parameters.whole:
            total, unread_total = len(found), sum(1 for _, _, unread in found if unread)
        elif folder is not None and not parameters.query and filters == SearchFilter():
            total, unread_total = mailbox.index.numbers(folder)  # a plain folder: the numbers the index keeps (D-208)
        else:
            total, unread_total = mailbox.index.tally(parameters.query, folder, filters)
        wanted_ids = [store_id for store_id, _, _ in found[:first]] + [message_id for message_id in parameters.rows_of]
        indexed = mailbox.index.messages(wanted_ids)
        rows = _rows(mailbox, [indexed[store_id] for store_id in dict.fromkeys(wanted_ids) if store_id in indexed], mailbox.timed(folder))
        return ViewOrder(
            tuple(store_id for store_id, _, _ in found), (), tuple(_shared("/".join(path), names) for _, path, _ in found), tuple(unread for _, _, unread in found),
            total, unread_total, parameters.whole or total <= len(found), groups, rows,
        )


def message_combined_order(engine: Engine, parameters: CombinedOrderParameters) -> ViewOrder:
    """The order of a Favorites or Tags view (every mailbox, or those of a group), each mailbox read at one moment (R-15)."""
    if len(parameters.rows_of_mailboxes) != len(parameters.rows_of):
        raise UsageError("Give the mailbox of each message of rows_of")
    first = max(0, parameters.first)
    whole = parameters.whole or parameters.sort == CONTACT_SORT  # the Contact sort looks at every message anyway
    found = gather(engine, parameters, EVERYTHING if whole else first, groups=parameters.headings, tally=not whole)
    ordered = in_order(found, parameters)
    if whole:
        total, unread_total = len(ordered), sum(1 for row in ordered if "seen" not in row.flags)
    else:
        ordered = ordered[:first]
        total, unread_total = found.total, found.unread
    owners = {mailbox: engine.resolve_account(mailbox).account.account_id for mailbox in set(parameters.rows_of_mailboxes)}
    named = {(owners[mailbox], message_id) for mailbox, message_id in zip(parameters.rows_of_mailboxes, parameters.rows_of, strict=True)}
    names: dict[str, str] = {}
    return ViewOrder(
        tuple(row.message_id for row in ordered), tuple(_shared(row.account, names) for row in ordered), tuple(_shared(row.folder, names) for row in ordered),
        tuple("seen" not in row.flags for row in ordered), total, unread_total, whole or total <= len(ordered), groups_in_order(found, parameters),
        tuple(row for number, row in enumerate(ordered) if number < first or (row.account, row.message_id) in named),
    )


@dataclass(frozen=True, slots=True)
class RowsParameters:
    account: str = field(metadata=option("The account (mailbox) of the messages"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    combined: bool = field(default=False, metadata=option("The rows say their mailbox, as in a Favorites view"))


@dataclass(frozen=True, slots=True)
class RowsAnswer:
    rows: tuple[MessageRow, ...]  # the rows of the messages asked that are still there, in no particular order


def message_rows(engine: Engine, parameters: RowsParameters) -> RowsAnswer:
    """The rows of these messages as a list shows them (asked by the message, never by a place, so a message that moved in
    the list meanwhile still gets its own row; one that is gone is left out)."""
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        indexed = list(mailbox.index.messages(parameters.messages).values())
        rows = _rows(mailbox, indexed, mailbox.timed(None) if any(message.folder in TIMED for message in indexed) else None)  # Snoozed until, Send at
        place = mailbox.store.account_id
    return RowsAnswer(tuple(replace(row, account=place) for row in rows) if parameters.combined else rows)


MESSAGE_ORDER: TypedCommand[OrderParameters, ViewOrder] = TypedCommand("message.order", OrderParameters, ViewOrder)
MESSAGE_COMBINED_ORDER: TypedCommand[CombinedOrderParameters, ViewOrder] = TypedCommand("message.combined-order", CombinedOrderParameters, ViewOrder)
MESSAGE_ROWS: TypedCommand[RowsParameters, RowsAnswer] = TypedCommand("message.rows", RowsParameters, RowsAnswer)


def register(registry: CommandRegistry) -> None:
    registry.register(MESSAGE_ORDER.id, "The order of a folder or a search: every message's id, folder and unread mark, with the first rows", OrderParameters, message_order, doing="Loading the messages")
    registry.register(MESSAGE_COMBINED_ORDER.id, "The order of a Favorites view: every message's mailbox, id, folder and unread mark, with the first rows", CombinedOrderParameters, message_combined_order, doing="Loading the messages")
    registry.register(MESSAGE_ROWS.id, "The rows of named messages, as the message list shows them", RowsParameters, message_rows, doing="Loading the messages")
