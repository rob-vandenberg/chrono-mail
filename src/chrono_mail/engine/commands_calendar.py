from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from typing import Literal

from chrono_mail.core import OWN_COLOUR_ID, CollectionInfo, CollectionKind, PaletteColour, StorageError
from chrono_mail.core.colours import css_colour, css_name_nearest
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, palette_for, pim_account_ids
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.ical import Attendee, Event, EventSeries, Moment, Reminder, MAXIMUM_OCCURRENCES, events_to_text, new_uid, occurrences, parse_events
from chrono_mail.pim.times import day_bounds, format_moment, local_zone, parse_moment, zone_named
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_calendar.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: a series whose time of day moves moves its skipped and changed dates by as much on their own day; when its first date or
#           its rule changes they stay as they were (RV-185); dates the series names itself (RDATE) stay (RV-187); a changed occurrence
#           never moved in time moves with the series (RV-188); an edit of one date of an object of only changed occurrences changes
#           exactly that date (RV-186)
# v3.2.342: a whole series changed to a new start takes its skipped and changed occurrences along; they go when it no longer repeats (RV-93)
# v3.0.339: an attendee who stays keeps their name, role and answer when the attendees are given again (RV-17)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.273: R-19: typed event.show, -add, -edit, -delete
# v1.1.225: Every change of an item goes through item_writer: on its server first when it has one (R-23, D-194)
# v1.1.221: The colours of an appointment come from its account's palette (D-191)
# v1.1.219: An event's colour: event.add and event.edit take a colour of its calendar's palette (colour); event.list gives each occurrence its colour (D-191)
# v1.1.218: The cap of occurrences counts for the whole range, not per event (the Windows computer's report)
# v1.1.205: event.list gives every event of the days asked for (no cut of 5,000), for several calendars (calendars), and says when a limit against hostile calendars was reached (EventList.cut, R-02); typed (EVENT_LIST)
# v0.5.159: event.list for one event (item): only its occurrences (the calendar replaces only the blocks of an event that changed); importing events is a big job
# v0.0.63: The tasks section
# v0.0.60: Initial event commands

MAXIMUM_DAYS: int = 400
MAXIMUM_EVENTS: int = 100_000  # events in the days of one event.list (only a hostile calendar has more)
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
    colour_id: str = ""  # the colour of its calendar's palette it has (empty: the calendar's own); `colour` is what that is now
    other_colour: str = ""  # a colour another program gave it (RFC 7986 COLOR), when it has no colour of the palette


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
    colour_id: str = ""  # the colour of its calendar's palette (empty: the calendar's own)
    other_colour: str = ""  # a colour another program gave it (RFC 7986 COLOR), when it has no colour of the palette


def colour_of(palette: tuple[PaletteColour, ...], colour_id: str, other: str) -> str:
    """The colour an event is drawn in: its colour of its calendar's palette (the calendar's own first, then its account's colours), else a colour
    another program gave it, else the calendar's own (also when its colour was deleted from the palette)."""
    if colour_id:
        chosen = next((entry.colour for entry in palette if entry.colour_id == colour_id), "")
        if chosen:
            return chosen
    return other or palette[0].colour


def _with_colour(event: Event, palette: tuple[PaletteColour, ...], colour_id: str) -> Event:
    """An event given a colour of its calendar's palette (empty or the calendar's own: none of its own): the id it keeps, and the CSS name
    nearest to that colour for other programs."""
    if not colour_id or colour_id == OWN_COLOUR_ID:
        return replace(event, colour_id="", css_colour="")
    chosen = next((entry for entry in palette if entry.colour_id == colour_id), None)
    if chosen is None:
        raise UsageError("This calendar has no such colour")
    return replace(event, colour_id=colour_id, css_colour=css_name_nearest(chosen.colour))


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
        e.colour_id, css_colour(e.css_colour) if not e.colour_id else "",
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
    calendars: tuple[str, ...] = field(default=(), metadata=option("Only these calendars (repeat; default: all)"))
    text: str = field(default="", metadata=option("Only events with these words"))
    limit: int = field(default=0, metadata=option("How many at most (0: every one)"))
    item: str = field(default="", metadata=option("Only this event (the calendar replaces only the blocks of an event that changed)"))


@dataclass(frozen=True, slots=True)
class EventList:
    events: tuple[OccurrenceInfo, ...]
    cut: bool  # a limit was reached (the limit asked for, MAXIMUM_EVENTS, or an event that happens MAXIMUM_OCCURRENCES times or more): there may be more


def event_list(engine: Engine, parameters: ListParameters) -> EventList:
    """The events that happen on these days, repeating ones expanded, in order of time. Every one, unless a limit is
    reached; the answer says so then (the days are the limit, at most MAXIMUM_DAYS; the other limits only stop hostile
    calendars)."""
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
    cut = False
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            names = {info.collection_id: info for info in store.collections(CollectionKind.EVENTS)}
            settings = engine.config().find(account)
            palettes = {identifier: palette_for(settings, info) for identifier, info in names.items()}
            if parameters.calendar is not None:
                chosen = (parameters.calendar,) if parameters.calendar in names else ()
            else:
                chosen = tuple(collection for collection in names if not parameters.calendars or collection in parameters.calendars)
            if not chosen:
                continue
            query = ItemQuery(CollectionKind.EVENTS, chosen, parameters.text, start=int(start.timestamp()), end=int(end.timestamp()), limit=MAXIMUM_EVENTS + 1)
            items = store.index.query(query)
            if len(items) > MAXIMUM_EVENTS:
                cut, items = True, items[:MAXIMUM_EVENTS]
            for item in items:
                if parameters.item and item.item_id != parameters.item:
                    continue
                info = names[item.collection]
                room = MAXIMUM_OCCURRENCES - len(found)  # the cap is for the whole range, not per event
                if room <= 0:
                    cut = True
                    break
                times = occurrences([store.get(item.collection, item.item_id)], start, end, zone, limit=room)
                cut = cut or len(times) >= room
                for occurrence in times:
                    e = occurrence.event
                    moment = occurrence.start
                    key = datetime(moment.year, moment.month, moment.day, tzinfo=zone) if not isinstance(moment, datetime) else (moment if moment.tzinfo else moment.replace(tzinfo=zone))
                    other = css_colour(e.css_colour) if not e.colour_id else ""
                    found.append((key, OccurrenceInfo(
                        item.item_id, account, item.collection, info.name, colour_of(palettes[item.collection], e.colour_id, other), e.uid, e.summary, format_moment(occurrence.start),
                        format_moment(occurrence.end), e.all_day, e.location, item.repeats, occurrence.changed,
                        (format_moment(e.recurrence_id) if e.recurrence_id is not None else format_moment(occurrence.start)) if item.repeats else "", e.status,
                        e.transparency != "TRANSPARENT", e.categories, e.colour_id, other,
                    )))
    found.sort(key=lambda pair: (pair[0], pair[1].summary.casefold()))
    if parameters.limit and len(found) > parameters.limit:
        cut, found = True, found[: parameters.limit]
    return EventList(tuple(row for _, row in found), cut)


EVENT_LIST: TypedCommand[ListParameters, EventList] = TypedCommand("event.list", ListParameters, EventList)


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


def _following_the_start(series: EventSeries, master: Event) -> EventSeries:
    """The whole series changed: its skipped occurrences (EXDATE) and changed ones (RECURRENCE-ID) follow it, and go when it no longer
    repeats. When the rule stayed and the first occurrence stayed on its day, each of them moves on the wall clock by exactly as much as
    the start moved, from the old zone into the new one: a new time of day, a new zone, also for a rule of several times a day (RV-93),
    and a changed occurrence whose own time was never moved moves with it (RV-188). When the first day or the rule changed, they stay
    exactly as they were: a date that is still in the series keeps being skipped or changed, and two of them can never land on one date
    (RV-185). Dates the series names itself (RDATE) never move (RV-187)."""
    before = series.master
    if not series.own_master or master.start == before.start and master.rrule == before.rrule and master.repeats == before.repeats:  # the same
        # moment: an edit of the title only (the window gives the start back in this computer's zone) moves nothing
        return replace(series, master=master)  # (a stand-in master names no occurrences of its own)
    if not master.repeats:
        return replace(series, master=replace(master, exdates=()), overrides=())
    length = _length(master)
    if before.rrule and not master.rrule:  # only its named dates are left (and its first one): the exceptions of the rule go with it
        named = {*master.rdates, master.start}

        def first(moment: Moment) -> Moment:  # an exception of the first occurrence goes with the start when that moved
            return master.start if moment == before.start else moment

        return replace(
            series, master=replace(master, exdates=tuple(first(moment) for moment in master.exdates if first(moment) in named)),
            overrides=tuple(
                _moved_override(override, first(override.recurrence_id), before, length)
                for override in series.overrides if override.recurrence_id is not None and first(override.recurrence_id) in named
            ),
        )
    if master.rrule != before.rrule or not _same_first_day(before.start, master.start) or _rule_sets_the_times(master.rrule):
        return replace(series, master=master)  # (a rule that sets its own hours keeps its occurrences where they are)
    fixed = set(before.rdates)

    def follows(moment: Moment) -> Moment:
        return moment if moment in fixed else _moved_like_the_start(moment, before.start, master.start)

    return replace(
        series, master=replace(master, exdates=tuple(follows(moment) for moment in master.exdates)),
        overrides=tuple(
            _moved_override(override, follows(override.recurrence_id), before, length) if override.recurrence_id is not None else override
            for override in series.overrides
        ),
    )


def _rule_sets_the_times(rule: str) -> bool:
    """A rule that names the hours, minutes or positions itself (BYHOUR=9,15): its occurrences do not move with the start's time."""
    parts = {part.split("=", 1)[0].strip().upper() for part in rule.split(";")}
    return bool(parts & {"BYHOUR", "BYMINUTE", "BYSECOND", "BYSETPOS"})


def _day_of(moment: Moment) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def _same_first_day(before: Moment, after: Moment) -> bool:
    """The first occurrence stays on its day, each in its own zone: only its time of day, its zone, or all-day or not changed. A floating
    time and a zoned one are never compared."""
    if isinstance(before, datetime) and isinstance(after, datetime) and (before.tzinfo is None) != (after.tzinfo is None):
        return False
    return _day_of(before) == _day_of(after)


def _wall(moment: Moment) -> datetime:
    """The moment on its own wall clock, without a zone (a day: its midnight)."""
    return moment.replace(tzinfo=None) if isinstance(moment, datetime) else datetime(moment.year, moment.month, moment.day)


def _moved_like_the_start(moment: Moment, before: Moment, after: Moment) -> Moment:
    """A skipped or changed occurrence moved on the wall clock by as much as the start: read in the series' old zone, moved, and given the
    new zone (a day stays a day, a time a time, as the new start). One that cannot be read that way (a floating time in a zoned series,
    the edge of year 9999) stays as it is."""
    if isinstance(moment, datetime) != isinstance(before, datetime):
        return moment
    try:
        if isinstance(moment, datetime) and isinstance(before, datetime):
            if (moment.tzinfo is None) != (before.tzinfo is None):
                return moment
            moment = moment.astimezone(before.tzinfo) if before.tzinfo is not None else moment
        moved = _wall(moment) + (_wall(after) - _wall(before))
        if isinstance(after, datetime):
            return moved.replace(tzinfo=after.tzinfo)
        return moved.date()
    except (OverflowError, ValueError):
        return moment


def _moved_override(override: Event, moment: Moment, before: Event, length: timedelta | None) -> Event:
    """A changed occurrence at its new place; when its own time was the series' (only its title or people changed), it keeps the
    series' time there too (RV-188). At the edge of the years it only changes its place."""
    kept_time = override.start == override.recurrence_id and _length(override) == _length(before)
    if kept_time and length is not None:  # also across a change between all-day and timed: it takes the series' new kind
        try:
            return replace(override, recurrence_id=moment, start=moment, end=moment + length)
        except OverflowError:
            pass
    return replace(override, recurrence_id=moment)


def _length(event: Event) -> timedelta | None:
    """How long it lasts; None when its start and end cannot be compared (a day and a time, a floating and a zoned time)."""
    start, end = event.start, event.end
    if isinstance(start, datetime) and isinstance(end, datetime):
        return end - start if (start.tzinfo is None) == (end.tzinfo is None) else None
    if not isinstance(start, datetime) and not isinstance(end, datetime):
        return end - start
    return None


def _attendees_kept(before: tuple[Attendee, ...], texts: tuple[str, ...]) -> tuple[Attendee, ...]:
    """The attendees as given, each one who was there already with everything known of them: name, role and answer (accepted,
    declined). The editor shows only the addresses; a Save that resent them reset every answer, on the server too (RV-17)."""
    known = {person.address.lower(): person for person in before}
    result: list[Attendee] = []
    for text in texts:
        given = _attendee(text)
        earlier = known.get(given.address.lower())
        result.append(given if earlier is None else replace(earlier, name=given.name or earlier.name))
    return tuple(result)


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
    colour: str | None = field(default=None, metadata=option("The colour: the id of a colour of the calendar's palette (empty: the calendar's own colour)"))


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
        result = replace(result, attendees=_attendees_kept(result.attendees, values.attendee))
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
            if parameters.colour is not None:
                event = _with_colour(event, palette_for(engine.config().find(account), info), parameters.colour.strip())
            series = EventSeries(event)
            with item_writer(engine, account, store) as writer:
                item = writer.put(info, events_to_text(series))
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
                if parameters.colour is not None:
                    changed = _with_colour(changed, palette_for(engine.config().find(found.account), found.collection), parameters.colour.strip())
                known = any(o.recurrence_id == moment for o in series.overrides)  # in its own place: the order says which one stands for
                overrides = tuple(changed if o.recurrence_id == moment else o for o in series.overrides) if known else (*series.overrides, changed)
                series = replace(series, overrides=overrides)  # the event in an object of only changed occurrences (RV-186)
                if not series.own_master and overrides[0].recurrence_id == moment:
                    series = replace(series, master=replace(changed, recurrence_id=None))  # the occurrence that stands for the event changed
            else:
                master = _apply(_clear(series.master, parameters.clear), parameters, creating=False)
                if parameters.add_tag or parameters.remove_tag:
                    master = replace(master, categories=changed_tags(master.categories, parameters.add_tag, parameters.remove_tag))
                if parameters.colour is not None:
                    master = _with_colour(master, palette_for(engine.config().find(found.account), found.collection), parameters.colour.strip())
                series = _following_the_start(series, master)
            with item_writer(engine, found.account, found.store) as writer:
                writer.put(found.collection, events_to_text(series, original), parameters.item)
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
                with item_writer(engine, found.account, found.store) as writer:
                    writer.delete(found.collection, parameters.item)
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
            with item_writer(engine, found.account, found.store) as writer:
                writer.put(found.collection, events_to_text(replace(series, master=master, overrides=overrides), original), parameters.item)
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
                with item_writer(engine, account, found.store) as writer:
                    new_id = writer.move(found.collection, parameters.item, target)
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
        with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (account, store, info):
            if info.read_only:
                raise UsageError("This calendar is read-only")
            parsed = parse_events(parameters.text)
            if not parsed:
                raise UsageError("No event could be read from this text")
            with item_writer(engine, account, store) as writer:
                for series in parsed:
                    known = store.index.find_uid(info.collection_id, series.master.uid)
                    items.append(writer.put(info, events_to_text(series), known.item_id if known else None))
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


EVENT_SHOW: TypedCommand[ItemParameters, EventInfo] = TypedCommand("event.show", ItemParameters, EventInfo)
EVENT_ADD: TypedCommand[AddParameters, EventInfo] = TypedCommand("event.add", AddParameters, EventInfo)
EVENT_EDIT: TypedCommand[EditParameters, EventInfo] = TypedCommand("event.edit", EditParameters, EventInfo)
EVENT_DELETE: TypedCommand[DeleteParameters, DeleteResult] = TypedCommand("event.delete", DeleteParameters, DeleteResult)


def register(registry: CommandRegistry) -> None:
    registry.register(EVENT_LIST.id, "List the events of some days, repeating ones expanded", ListParameters, event_list, doing="Loading the calendar")
    registry.register(EVENT_SHOW.id, "Show an event", ItemParameters, event_show, doing="Loading an event")
    registry.register(EVENT_ADD.id, "Add an event", AddParameters, event_add, doing="Adding an event")
    registry.register(EVENT_EDIT.id, "Change an event, or one occurrence of a repeating event", EditParameters, event_edit, doing="Saving an event")
    registry.register(EVENT_DELETE.id, "Delete an event, or one occurrence of a repeating event", DeleteParameters, event_delete, destructive=True, doing="Deleting an event")
    registry.register("event.move", "Move an event to another calendar of the same account", MoveParameters, event_move, doing="Moving an event")
    registry.register("event.import", "Import events from iCalendar text", ImportParameters, quiet_command(event_import), doing="Importing events")
    registry.register("event.export", "Export an event or a calendar as iCalendar text", ExportParameters, event_export, doing="Exporting events")
