from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from typing import Literal

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.ical import Attendee, Event, EventSeries, Moment, Reminder, events_to_text, new_uid, occurrences, parse_events
from chrono_mail.pim.times import day_bounds, format_moment, local_zone, parse_moment, zone_named
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_calendar.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: The tasks section
# v0.0.60: Initial event commands

MAXIMUM_DAYS: int = 400
MAXIMUM_ROWS: int = 5_000
LIST_FIELDS: tuple[str, ...] = ("reminder", "attendee", "tag")


@dataclass(frozen=True, slots=True)
class OccurrenceInfo:
    """One time an event happens. For all-day events the end is the day after the last day (as in iCalendar)."""

    item: str
    account: str
    collection: str
    calendar: str
    colour: str
    uid: str
    summary: str
    start: str
    end: str
    all_day: bool
    location: str
    repeats: bool
    changed: bool  # this occurrence was changed on its own
    occurrence: str  # the original start of this occurrence of a repeating event: what to give to event.edit and event.delete to act on this one only
    status: str
    busy: bool
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttendeeInfo:
    address: str
    name: str
    role: str
    status: str
    rsvp: bool


@dataclass(frozen=True, slots=True)
class ReminderInfo:
    minutes_before: int | None
    at: str
    action: str
    relative_to_end: bool


@dataclass(frozen=True, slots=True)
class ChangedOccurrence:
    occurrence: str
    start: str
    end: str
    summary: str


@dataclass(frozen=True, slots=True)
class EventInfo:
    item: str
    account: str
    collection: str
    uid: str
    summary: str
    description: str
    location: str
    start: str
    end: str
    all_day: bool
    time_zone: str
    rrule: str
    exceptions: tuple[str, ...]
    status: str
    busy: bool
    classification: str
    tags: tuple[str, ...]
    organizer: AttendeeInfo | None
    attendees: tuple[AttendeeInfo, ...]
    reminders: tuple[ReminderInfo, ...]
    url: str
    changed_occurrences: tuple[ChangedOccurrence, ...]


def _zone_name(moment: Moment) -> str:
    if isinstance(moment, datetime) and moment.tzinfo is not None:
        return str(getattr(moment.tzinfo, "key", "") or ("UTC" if moment.utcoffset() == timedelta(0) else ""))
    return ""


def _person(person: Attendee) -> AttendeeInfo:
    return AttendeeInfo(person.address, person.name, person.role, person.status, person.rsvp)


def _info(item: str, account: str, collection: str, series: EventSeries) -> EventInfo:
    e = series.master
    return EventInfo(
        item, account, collection, e.uid, e.summary, e.description, e.location, format_moment(e.start), format_moment(e.end), e.all_day, _zone_name(e.start), e.rrule,
        tuple(format_moment(moment) for moment in e.exdates), e.status, e.transparency != "TRANSPARENT", e.classification, e.categories,
        _person(e.organizer) if e.organizer else None, tuple(_person(p) for p in e.attendees),
        tuple(ReminderInfo(r.minutes_before, format_moment(r.at), r.action, r.relative_to_end) for r in e.reminders), e.url,
        tuple(ChangedOccurrence(format_moment(o.recurrence_id), format_moment(o.start), format_moment(o.end), o.summary) for o in series.overrides),
    )


# --- reading ------------------------------------------------------------------------------------------------------------------------------


def _day(text: str, name: str) -> date:
    try:
        moment = parse_moment(text)
    except ValueError as error:
        raise UsageError(f"{name}: {error}") from None
    return moment.date() if isinstance(moment, datetime) else moment


@dataclass(frozen=True, slots=True)
class ListParameters:
    from_date: str = field(metadata=option("The first day (2026-10-12), in your local time"))
    to_date: str | None = field(default=None, metadata=option("The day after the last day (default: the day after from_date; at most 400 days)"))
    calendar: str | None = field(default=None, metadata=option("Only this calendar (default: all)"))
    text: str = field(default="", metadata=option("Only events with these words"))
    limit: int = field(default=1000, metadata=option("How many at most"))


def event_list(engine: Engine, parameters: ListParameters) -> tuple[OccurrenceInfo, ...]:
    """The events that happen on these days, repeating ones expanded, in order of time."""
    first = _day(parameters.from_date, "from_date")
    last = _day(parameters.to_date, "to_date") if parameters.to_date else first + timedelta(days=1)
    if last <= first:
        raise UsageError("to_date must be after from_date")
    if (last - first).days > MAXIMUM_DAYS:
        raise UsageError(f"At most {MAXIMUM_DAYS} days can be listed at once")
    start, _ = day_bounds(first)
    end, _ = day_bounds(last)
    zone = local_zone()
    found: list[tuple[datetime, OccurrenceInfo]] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            names = {info.collection_id: info for info in store.collections(CollectionKind.EVENTS)}
            if parameters.calendar is not None and parameters.calendar not in names:
                continue
            query = ItemQuery(CollectionKind.EVENTS, (parameters.calendar,) if parameters.calendar else tuple(names), parameters.text, start=int(start.timestamp()), end=int(end.timestamp()), limit=10**5)
            for item in store.index.query(query):
                info = names[item.collection]
                for occurrence in occurrences([store.get(item.collection, item.item_id)], start, end, zone):
                    e = occurrence.event
                    moment = occurrence.start
                    key = datetime(moment.year, moment.month, moment.day, tzinfo=zone) if not isinstance(moment, datetime) else (moment if moment.tzinfo else moment.replace(tzinfo=zone))
                    found.append((key, OccurrenceInfo(
                        item.item_id, account, item.collection, info.name, info.colour, e.uid, e.summary, format_moment(occurrence.start), format_moment(occurrence.end), e.all_day,
                        e.location, item.repeats, occurrence.changed,
                        (format_moment(e.recurrence_id) if e.recurrence_id is not None else format_moment(occurrence.start)) if item.repeats else "", e.status,
                        e.transparency != "TRANSPARENT", e.categories,
                    )))
    found.sort(key=lambda pair: (pair[0], pair[1].summary.casefold()))
    return tuple(row for _, row in found[: max(1, min(parameters.limit, MAXIMUM_ROWS))])


@dataclass(frozen=True, slots=True)
class ItemParameters:
    item: str = field(metadata=option("The event id (see event.list)"))


def _series(text: str) -> EventSeries:
    parsed = parse_events(text)
    if not parsed:
        raise UsageError("This event cannot be read (it is kept as it is)")
    return parsed[0]


def event_show(engine: Engine, parameters: ItemParameters) -> EventInfo:
    with located(engine, parameters.item, CollectionKind.EVENTS) as found:
        return _info(parameters.item, found.account, found.item.collection, _series(found.store.get(found.item.collection, parameters.item)))


# --- writing ------------------------------------------------------------------------------------------------------------------------------


def _reminder(text: str, zone_name: str) -> Reminder:
    """'15' minutes before; '0' at the start; '-30' after; 'at:2026-10-01T09:00' at a fixed time."""
    cleaned = text.strip()
    try:
        if cleaned.lower().startswith("at:"):
            moment = parse_moment(cleaned[3:], zone_named(zone_name) if zone_name else None)
            if not isinstance(moment, datetime):
                raise ValueError("A reminder at a fixed time needs a time")
            return Reminder(None, moment)
        return Reminder(int(cleaned))
    except ValueError:
        raise UsageError(f"A reminder is minutes before the start (15), or at:2026-10-01T09:00 (got '{text}')") from None


def _attendee(text: str) -> Attendee:
    name, address = parseaddr(text)
    if "@" not in address:
        raise UsageError(f"'{text}' is not an e-mail address")
    return Attendee(address, name)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventFields:
    """The fields of an event (for event.add and event.edit). A list given here replaces the list the event has."""

    summary: str | None = field(default=None, metadata=option("The title"))
    start: str | None = field(default=None, metadata=option("The start: 2026-10-12 (all day) or 2026-10-12T09:00 (in time_zone), or with an offset (+02:00 or Z)"))
    end: str | None = field(default=None, metadata=option("The end (for all-day events: the day after the last day). Default: an hour after the start, or the next day"))
    time_zone: str | None = field(default=None, metadata=option("The zone of times without an offset, like Europe/Amsterdam (default: this computer's)"))
    location: str | None = field(default=None, metadata=option("Where"))
    description: str | None = field(default=None, metadata=option("A description"))
    rrule: str | None = field(default=None, metadata=option("How it repeats, like FREQ=WEEKLY;BYDAY=MO (empty: not at all)"))
    reminder: tuple[str, ...] = field(default=(), metadata=option("Reminders: minutes before the start (15), or at:2026-10-01T09:00 (repeat)"))
    attendee: tuple[str, ...] = field(default=(), metadata=option("Attendees as addresses or 'Name <address>' (repeat)"))
    organizer: str | None = field(default=None, metadata=option("The organiser, as an address (empty: none)"))
    tag: tuple[str, ...] = field(default=(), metadata=option("Tags (repeat)"))
    status: Literal["confirmed", "tentative", "cancelled"] | None = field(default=None, metadata=option("The status"))
    busy: bool | None = field(default=None, metadata=option("false: the event does not block the time"))
    url: str | None = field(default=None, metadata=option("A web address"))


def _apply(event: Event, values: EventFields, creating: bool) -> Event:
    zone_name = values.time_zone or ""
    try:
        zone = zone_named(zone_name) if zone_name else None
    except ValueError as error:
        raise UsageError(str(error)) from None
    result = event
    try:
        if values.start is not None:
            start = parse_moment(values.start, zone)
            old_length = (event.end - event.start) if isinstance(event.start, datetime) and isinstance(event.end, datetime) else None
            result = replace(result, start=start)
            if values.end is None:
                if isinstance(start, datetime):
                    result = replace(result, end=start + (old_length if old_length is not None and not creating else timedelta(hours=1)))
                else:
                    result = replace(result, end=start + timedelta(days=1))
        if values.end is not None:
            end = parse_moment(values.end, zone, all_day=result.all_day)
            if isinstance(result.start, datetime) != isinstance(end, datetime):
                raise UsageError("The start and the end must both be dates or both be times")
            result = replace(result, end=end)
    except ValueError as error:
        raise UsageError(str(error)) from None
    if isinstance(result.start, datetime) and isinstance(result.end, datetime) and result.end < result.start:
        raise UsageError("An event cannot end before it starts")
    if not isinstance(result.start, datetime) and not isinstance(result.end, datetime) and result.end <= result.start:
        raise UsageError("An all-day event ends the day after its last day, so the end must be after the start")
    if values.summary is not None:
        result = replace(result, summary=values.summary.strip())
    if values.location is not None:
        result = replace(result, location=values.location.strip())
    if values.description is not None:
        result = replace(result, description=values.description)
    if values.rrule is not None:
        rule = values.rrule.strip().removeprefix("RRULE:")
        if rule and "FREQ=" not in rule.upper():
            raise UsageError("A rule starts with FREQ=, like FREQ=WEEKLY;BYDAY=MO")
        result = replace(result, rrule=rule)
    if values.reminder:
        result = replace(result, reminders=tuple(_reminder(text, zone_name) for text in values.reminder))
    if values.attendee:
        result = replace(result, attendees=tuple(_attendee(text) for text in values.attendee))
    if values.organizer is not None:
        result = replace(result, organizer=_attendee(values.organizer) if values.organizer.strip() else None)
    if values.tag:
        result = replace(result, categories=tuple(dict.fromkeys(" ".join(tag.split()) for tag in values.tag if tag.strip())))
    if values.status is not None:
        result = replace(result, status=values.status.upper())
    if values.busy is not None:
        result = replace(result, transparency="OPAQUE" if values.busy else "TRANSPARENT")
    if values.url is not None:
        result = replace(result, url=values.url.strip())
    return result


def _clear(event: Event, names: tuple[str, ...]) -> Event:
    result = event
    for name in names:
        if name == "reminder":
            result = replace(result, reminders=())
        elif name == "attendee":
            result = replace(result, attendees=())
        elif name == "tag":
            result = replace(result, categories=())
        else:
            raise UsageError(f"Only these can be cleared: {', '.join(LIST_FIELDS)}")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class AddParameters(EventFields):
    calendar: str = field(metadata=option("The calendar id (see collection.list)"))


def event_add(engine: Engine, parameters: AddParameters) -> EventInfo:
    if parameters.summary is None or parameters.start is None:
        raise UsageError("An event needs a summary and a start")
    event = _apply(Event(uid=new_uid()), parameters, creating=True)
    try:
        with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (account, store, info):
            if info.read_only:
                raise UsageError("This calendar is read-only")
            series = EventSeries(event)
            item = store.put(info.collection_id, events_to_text(series))
            return _info(item, account, info.collection_id, series)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditParameters(EventFields):
    item: str = field(metadata=option("The event id"))
    occurrence: str = field(default="", metadata=option("For a repeating event: the occurrence to change on its own (the 'occurrence' of event.list); empty changes the whole series"))
    clear: tuple[str, ...] = field(default=(), metadata=option("Lists to empty: reminder, attendee, tag (repeat)"))
    add_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to add (repeat)"))
    remove_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to remove (repeat)"))


def _in_event_zone(moment: Moment, start: Moment) -> Moment:
    """An occurrence given as text, in the zone of the event it belongs to (so that it is written with that zone's name, not with a bare offset)."""
    if isinstance(start, datetime) and start.tzinfo is not None:
        if isinstance(moment, datetime):
            return moment.astimezone(start.tzinfo) if moment.tzinfo is not None else moment.replace(tzinfo=start.tzinfo)
        return datetime(moment.year, moment.month, moment.day, tzinfo=start.tzinfo)
    if not isinstance(start, datetime):
        return moment.date() if isinstance(moment, datetime) else moment
    return moment


def _duration(start: Moment, end: Moment) -> timedelta:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return end - start
    if not isinstance(start, datetime) and not isinstance(end, datetime):
        return end - start
    return timedelta(hours=1)


def _occurrence_event(series: EventSeries, moment: Moment) -> Event:
    """The changed occurrence at this moment (or what it would be if it were changed)."""
    for override in series.overrides:
        if override.recurrence_id == moment:
            return override
    master = series.master
    if not master.repeats:
        raise UsageError("This event does not repeat")
    return replace(master, start=moment, end=moment + _duration(master.start, master.end), rrule="", exdates=(), rdates=(), recurrence_id=moment)


def event_edit(engine: Engine, parameters: EditParameters) -> EventInfo:
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            if parameters.occurrence:
                try:
                    moment = _in_event_zone(parse_moment(parameters.occurrence), series.master.start)
                except ValueError as error:
                    raise UsageError(f"occurrence: {error}") from None
                base = _occurrence_event(series, moment)
                if parameters.rrule:
                    raise UsageError("One occurrence cannot have its own rule")
                changed = _apply(_clear(base, parameters.clear), parameters, creating=False)
                others = tuple(o for o in series.overrides if o.recurrence_id != moment)
                series = EventSeries(series.master, (*others, changed))
            else:
                master = _apply(_clear(series.master, parameters.clear), parameters, creating=False)
                if parameters.add_tag or parameters.remove_tag:
                    master = replace(master, categories=changed_tags(master.categories, parameters.add_tag, parameters.remove_tag))
                series = EventSeries(master, series.overrides)
            found.store.put(found.item.collection, events_to_text(series, original), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, series)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    item: str = field(metadata=option("The event id"))
    occurrence: str = field(default="", metadata=option("For a repeating event: only this occurrence (the 'occurrence' of event.list); empty deletes the whole event"))


@dataclass(frozen=True, slots=True)
class DeleteResult:
    item: str
    deleted: bool  # false when only one occurrence was taken out of a series


def event_delete(engine: Engine, parameters: DeleteParameters) -> DeleteResult:
    """Delete an event, or one occurrence of a repeating event (it becomes an exception). The file is kept in .deleted for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            if not parameters.occurrence:
                found.store.delete(found.item.collection, parameters.item)
                return DeleteResult(parameters.item, True)
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            try:
                moment = _in_event_zone(parse_moment(parameters.occurrence), series.master.start)
            except ValueError as error:
                raise UsageError(f"occurrence: {error}") from None
            if not series.master.repeats:
                raise UsageError("This event does not repeat")
            master = replace(series.master, exdates=tuple(dict.fromkeys((*series.master.exdates, moment))))
            overrides = tuple(o for o in series.overrides if o.recurrence_id != moment)
            found.store.put(found.item.collection, events_to_text(EventSeries(master, overrides), original), parameters.item)
            return DeleteResult(parameters.item, False)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class MoveParameters:
    item: str = field(metadata=option("The event id"))
    calendar: str = field(metadata=option("The calendar to move it to"))


def event_move(engine: Engine, parameters: MoveParameters) -> EventInfo:
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (account, _, target):
                if account != found.account:
                    raise UsageError("An event can only be moved inside one account (copy it with export and import)")
                if target.read_only or found.collection.read_only:
                    raise UsageError("A read-only calendar cannot take part in a move")
                new_id = found.store.move(found.item.collection, parameters.item, target.collection_id)
                return _info(new_id, account, target.collection_id, _series(found.store.get(target.collection_id, new_id)))
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class ImportParameters:
    calendar: str = field(metadata=option("The calendar id"))
    text: str = field(metadata=option("The iCalendar text (.ics), with one or more events"))


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int
    updated: int
    skipped: int
    items: tuple[str, ...]


def event_import(engine: Engine, parameters: ImportParameters) -> ImportResult:
    """Add the events of an iCalendar text. An event whose uid is in the calendar already replaces that event."""
    imported = updated = skipped = 0
    items: list[str] = []
    try:
        with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (_, store, info):
            if info.read_only:
                raise UsageError("This calendar is read-only")
            parsed = parse_events(parameters.text)
            if not parsed:
                raise UsageError("No event could be read from this text")
            for series in parsed:
                known = store.index.find_uid(info.collection_id, series.master.uid)
                items.append(store.put(info.collection_id, events_to_text(series), known.item_id if known else None))
                if known:
                    updated += 1
                else:
                    imported += 1
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ImportResult(imported, updated, skipped, tuple(items))


@dataclass(frozen=True, slots=True)
class ExportParameters:
    item: str | None = field(default=None, metadata=option("The event id"))
    calendar: str | None = field(default=None, metadata=option("Or a whole calendar"))


@dataclass(frozen=True, slots=True)
class ExportResult:
    events: int
    text: str


def event_export(engine: Engine, parameters: ExportParameters) -> ExportResult:
    """An event (or every event of a calendar) as iCalendar text: one calendar object for each, one after the other."""
    if (parameters.item is None) == (parameters.calendar is None):
        raise UsageError("Give either an event or a calendar")
    texts: list[str] = []
    try:
        if parameters.item is not None:
            with located(engine, parameters.item, CollectionKind.EVENTS) as found:
                texts.append(found.store.get(found.item.collection, parameters.item))
        else:
            assert parameters.calendar is not None
            with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (_, store, info):
                texts.extend(store.get(info.collection_id, item) for item in store.item_ids(info.collection_id))
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ExportResult(len(texts), "".join(texts))


def register(registry: CommandRegistry) -> None:
    registry.register("event.list", "List the events of some days, repeating ones expanded", ListParameters, event_list)
    registry.register("event.show", "Show an event", ItemParameters, event_show)
    registry.register("event.add", "Add an event", AddParameters, event_add)
    registry.register("event.edit", "Change an event, or one occurrence of a repeating event", EditParameters, event_edit)
    registry.register("event.delete", "Delete an event, or one occurrence of a repeating event", DeleteParameters, event_delete, destructive=True)
    registry.register("event.move", "Move an event to another calendar of the same account", MoveParameters, event_move)
    registry.register("event.import", "Import events from iCalendar text", ImportParameters, event_import)
    registry.register("event.export", "Export an event or a calendar as iCalendar text", ExportParameters, event_export)
