from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from chrono_mail.core import ChronoMailError, CollectionKind
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import occurrences_from
from chrono_mail.pim.times import local_zone
from chrono_mail.pimstore import IndexedItem, ItemQuery, PimStore
from chrono_mail.pimstore.extract import epoch

# --- Version ------------------------------------------------------------
__version__ = "commands_tagoverview.py 4.4.572"

# --- Version history ----------------------------------------------------
# v4.4.572: Initial tag.overview: the contacts, events and tasks of one tag, from the indexes alone (D-328)

EVERY: int = 10**6  # every item of one kind of one account with the tag
DAY: int = 86_400


@dataclass(frozen=True, slots=True)
class OverviewParameters:
    tag: str = field(metadata=option("The tag"))
    text: str = field(default="", metadata=option("Only items with these words"))


@dataclass(frozen=True, slots=True)
class TaggedItem:
    """An item of the overview, from the index: enough for its row; the item itself is opened by its own section's commands."""

    item: str
    account: str
    collection: str
    collection_name: str  # the calendar, task list or address book
    title: str
    subtitle: str  # a contact's organisation or address, an event's or a task's place
    start: int | None  # seconds since 1970; floating: the wall-clock time as if it were UTC
    end: int | None
    due: int | None
    floating: bool
    all_day: bool  # an event of whole days: its dates only
    repeats: bool
    later: bool  # an event still to come (or a series that goes on); a task not done; every contact. False: an earlier event, a finished task


@dataclass(frozen=True, slots=True)
class TagOverview:
    contacts: tuple[TaggedItem, ...]  # by name
    events: tuple[TaggedItem, ...]  # the ones still to come first, soonest first; then the earlier ones, latest first
    tasks: tuple[TaggedItem, ...]  # the open ones first, by due date (none last); then the finished ones
    unreadable: tuple[str, ...] = ()  # the accounts whose calendars, task lists or address books could not be read


def _now_wall() -> int:
    """This moment as the wall clock shows it, counted as if it were UTC: what a floating time is compared with."""
    now = datetime.now(local_zone())
    return int(now.replace(tzinfo=UTC).timestamp())


def _later(item: IndexedItem, now: int, now_wall: int) -> bool:
    if item.kind is CollectionKind.CONTACTS:
        return True  # a contact has no time: never put apart
    if item.kind is CollectionKind.TASKS:
        return not item.done
    moment = now_wall if item.floating else now
    if item.repeats:
        return item.until is None or item.until >= moment
    last = item.end if item.end is not None else item.start
    return last is not None and last >= moment


def _all_day(item: IndexedItem) -> bool:
    if item.kind is not CollectionKind.EVENTS or not item.floating or item.start is None or item.start % DAY:
        return False
    return item.end is None or (item.end - item.start) % DAY == 0


def tag_overview(engine: Engine, parameters: OverviewParameters) -> TagOverview:
    """Every contact, event and task with this tag, in every account, from the indexes alone (a tag is matched whole, without regard to
    capitals). An account whose items cannot be read is left out and named; the others still come (D-328)."""
    tag = parameters.tag.strip()
    if not tag:
        raise UsageError("Give a tag")
    now, now_wall = int(datetime.now(UTC).timestamp()), _now_wall()
    found: dict[CollectionKind, list[tuple[tuple[object, ...], TaggedItem]]] = {kind: [] for kind in (CollectionKind.CONTACTS, CollectionKind.EVENTS, CollectionKind.TASKS)}
    unreadable: list[str] = []
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                for kind, rows in found.items():
                    try:
                        names = {info.collection_id: info.name for info in store.collections(kind)}
                        items = store.index.query(ItemQuery(kind, tuple(names), parameters.text, tag, limit=EVERY, groups=False if kind is CollectionKind.CONTACTS else None)) if names else []
                    except ChronoMailError:
                        if account not in unreadable:
                            unreadable.append(account)  # its other kinds still come
                        continue
                    for item in items:
                        later = _later(item, now, now_wall)
                        if later and item.repeats and item.kind is CollectionKind.EVENTS:
                            item = _next_time(store, item)  # a series that goes on: its next date, not its first
                        tagged = TaggedItem(
                            item.item_id, account, item.collection, names.get(item.collection, ""), item.title, item.subtitle, item.start, item.end, item.due,
                            item.floating, _all_day(item), item.repeats, later,
                        )
                        rows.append((_order(item, later), tagged))
        except ChronoMailError:
            if account not in unreadable:
                unreadable.append(account)
    contacts, events, tasks = (tuple(tagged for _, tagged in sorted(found[kind], key=lambda entry: entry[0])) for kind in (CollectionKind.CONTACTS, CollectionKind.EVENTS, CollectionKind.TASKS))
    return TagOverview(contacts, events, tasks, tuple(unreadable))


def _next_time(store: PimStore, item: IndexedItem) -> IndexedItem:
    """A repeating event with its next occurrence's start and end in place of its first; as it was when its text cannot be read or has none."""
    try:
        coming = next(occurrences_from(store.get(item.collection, item.item_id), datetime.now(UTC)), None)
    except ChronoMailError:
        return item
    if coming is None:
        return item
    start, floating = epoch(coming.start)
    end, _ = epoch(coming.end)
    return replace(item, start=start, end=end, floating=floating)


def _order(item: IndexedItem, later: bool) -> tuple[object, ...]:
    """The place of an item in its group: contacts by name; events to come soonest first, then earlier ones latest first; tasks open first by
    due date (none last), then finished ones the same way."""
    if item.kind is CollectionKind.CONTACTS:
        return (item.sort_key, item.title.casefold(), item.item_id)
    if item.kind is CollectionKind.EVENTS:
        start = item.start or 0
        return (0, start, item.title.casefold(), item.item_id) if later else (1, -start, item.title.casefold(), item.item_id)
    return (0 if later else 1, item.due is None, item.due or 0, item.title.casefold(), item.item_id)


TAG_OVERVIEW: TypedCommand[OverviewParameters, TagOverview] = TypedCommand("tag.overview", OverviewParameters, TagOverview)


def register(registry: CommandRegistry) -> None:
    registry.register(TAG_OVERVIEW.id, "Every contact, event and task with a tag (the mail of a tag: message.combined-order with view tagged)", OverviewParameters, tag_overview, doing="Loading everything with a tag")
