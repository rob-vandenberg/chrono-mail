from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from email.utils import parseaddr
from typing import Literal

from chrono_mail.config import AccountSettings
from chrono_mail.core import OWN_COLOUR_ID, CollectionInfo, CollectionKind, PaletteColour, StorageError
from chrono_mail.core.colours import css_colour, css_name_nearest
from chrono_mail.engine.invitations import take_mark_off
from chrono_mail.engine.meetingmail import Mailing, MeetingDraft, copy_for, delete_copy, meeting_mail, news_of_status, organizer_from, organizing_account, quiet, settings_of, who_mails, your_addresses_lower
from chrono_mail.engine.pimaccess import Located, located, open_collection, open_pim, palette_for, pim_account_ids
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.ical import Attendee, Event, EventSeries, Moment, Reminder, MAXIMUM_OCCURRENCES, cancelled_on, events_to_text, given_parts, kept_from, new_uid, occurrences, parse_events
from chrono_mail.pim.itip import attendee_addresses, cancelled_copy, cancelled_in, holds_occurrence, restored_copy, with_organizer, you_declined
from chrono_mail.pim.times import day_bounds, format_moment, local_zone, moved_like_the_start, parse_moment, zone_named
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_calendar.py 4.4.571"

# --- Version history ----------------------------------------------------
# v4.4.571: event.list takes a tag: only the events with that tag (D-327)
# v4.4.558: A row no longer says answerable and whole: the menus go by invited, and an invitation with no organizer you declined counts as invited
# v4.4.557: event.cancel and event.restore, server first, with a real cancellation or a new version for your meeting and silent on someone else's; a Cancelled event takes no edit or move until it is Restored (D-310)
# v4.4.553: A date in an object of only changed dates has its own row date and is deleted as that date; its last date takes the object along (D-310)
# v4.4.552: Every row says invited, your answer and read-only; hidden means declined, in a ticked calendar; a deleted date shows nowhere and has no way back (event.restore gone);
#           Delete of someone else's meeting never mails from here and goes the silent way; Delete of a Cancelled meeting mails nothing (D-310)
# v4.4.550: OccurrenceInfo.cancelled: a cancelled event or date, drawn grey (D-316)
# v4.4.547: An e-mail reminder can be at a fixed time; a task uses the address and the info of a reminder too (D-316)
# v4.4.536: Reminders by e-mail, at most 5 given, and keep: for one that stays as it is (round 4)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.521: a declined date of a series is left out as a skipped date; hidden: deleted and declined dates of a series, shown greyed when asked for (D-310)
# v4.4.518: A meeting you make names you as organizer and invites its attendees, a change or cancellation tells them when you say so (D-310)
# v4.4.513: a series that picks its days by position (BYSETPOS) takes its skipped and changed dates along to another hour (D-310)
# v4.4.506: moved_like_the_start lives in pim/times.py: a proposal for a whole series moves its dates the same way (D-310)
# v4.2.385: event.import builds the text afresh as before and keeps from the text given only the time zones the event uses (VTIMEZONE), its
#           X- properties and its CREATED (pim.ical.kept_from): a zone of its own was lost, so its times became floating after a restart
# v3.6.351: an event with floating times keeps them floating on an edit without a zone (it got this computer's zone, its skipped dates not);
#           an occurrence of a floating series is named floating too (its changed or deleted occurrence got a zone and never matched)
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
MOST_REMINDERS: int = 5  # the owner's number (round 4)


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
    occurrence: str  # the original start of this occurrence of a repeating event, or of a date in an object of only changed dates: what to give to event.edit and event.delete to act on this one only
    status: str
    busy: bool
    tags: tuple[str, ...]
    colour_id: str = ""  # the colour of its calendar's palette it has (empty: the calendar's own); `colour` is what that is now
    other_colour: str = ""  # a colour another program gave it (RFC 7986 COLOR), when it has no colour of the palette
    hidden: bool = False  # a meeting or a date you declined: shown greyed only when asked for
    invited: bool = False  # someone else organizes it and lists you, or an invitation with no organizer you declined; anything else is self-created
    answer: str = ""  # invited: your answer on this occurrence (NEEDS-ACTION: none yet); it says nothing of `status`, a fact of its own
    read_only: bool = False  # its calendar takes no changes

    @property
    def cancelled(self) -> bool:
        return self.status == "CANCELLED"


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


def reminder_info(reminder: Reminder) -> ReminderInfo:
    return ReminderInfo(reminder.minutes_before, format_moment(reminder.at), reminder.action, reminder.relative_to_end)


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
    yours: bool = False  # event.show: a meeting you organize, so its attendees can be told of a change
    draft: MeetingDraft = MeetingDraft()  # the mail to the attendees, ready to send
    cancel_draft: MeetingDraft = MeetingDraft()  # the cancellation to the attendees taken off, ready to send


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
        tuple(reminder_info(r) for r in e.reminders), e.url,
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
    tag: str = field(default="", metadata=option("Only events with this tag"))
    limit: int = field(default=0, metadata=option("How many at most (0: every one)"))
    hidden: bool = field(default=False, metadata=option("Also what you declined: meetings and dates of a series"))
    item: str = field(default="", metadata=option("Only this event (the calendar replaces only the blocks of an event that changed)"))


@dataclass(frozen=True, slots=True)
class EventList:
    events: tuple[OccurrenceInfo, ...]
    cut: bool  # a limit was reached (the limit asked for, MAXIMUM_EVENTS, or an event that happens MAXIMUM_OCCURRENCES times or more): there may be more


def _key(moment: Moment, zone: tzinfo) -> datetime:
    """A moment to sort and compare by: a whole day from its midnight, a floating time in `zone`."""
    return datetime(moment.year, moment.month, moment.day, tzinfo=zone) if not isinstance(moment, datetime) else (moment if moment.tzinfo else moment.replace(tzinfo=zone))


def _declined(event: Event, yours: Collection[str]) -> bool:
    """A meeting or a date you declined: it stays, hidden, and counts for nothing (D-310). A deleted single event simply no longer exists."""
    return any(person.status == "DECLINED" and person.address.strip().lower() in yours for person in event.attendees)


def _your_lines(event: Event, yours: Collection[str]) -> list[Attendee]:
    return [person for person in event.attendees if person.address.strip().lower() in yours]


def _others_meeting(event: Event, yours: Collection[str]) -> bool:
    """Someone else organizes it, whether or not you are listed: nothing you do to your copy should mail them unless you answer."""
    return event.organizer is not None and event.organizer.address.strip().lower() not in yours


def _invited(event: Event, yours: Collection[str]) -> bool:
    """Someone else organizes it and lists you. An event that names another organizer without listing you is self-created. An invitation with no organizer
    that you declined is still one: it can be answered again."""
    return bool(_your_lines(event, yours)) and (_others_meeting(event, yours) or (event.organizer is None and _declined(event, yours)))


def _your_answer(event: Event, yours: Collection[str]) -> str:
    """Your answer on an invited event or date, as `_declined` reads it when two of your addresses are listed."""
    if not _invited(event, yours):
        return ""
    return "DECLINED" if _declined(event, yours) else _your_lines(event, yours)[0].status


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
    yours = your_addresses_lower(engine)
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
            query = ItemQuery(CollectionKind.EVENTS, chosen, parameters.text, parameters.tag, start=int(start.timestamp()), end=int(end.timestamp()), limit=MAXIMUM_EVENTS + 1)
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
                text = store.get(item.collection, item.item_id)
                times = occurrences([text], start, end, zone, limit=room)
                cut = cut or len(times) >= room
                for occurrence in times:
                    e = occurrence.event
                    key = _key(occurrence.start, zone)
                    hidden = _declined(e, yours)
                    if hidden and not parameters.hidden:
                        continue
                    other = css_colour(e.css_colour) if not e.colour_id else ""
                    found.append((key, OccurrenceInfo(
                        item.item_id, account, item.collection, info.name, colour_of(palettes[item.collection], e.colour_id, other), e.uid, e.summary, format_moment(occurrence.start),
                        format_moment(occurrence.end), e.all_day, e.location, item.repeats, occurrence.changed,
                        (format_moment(e.recurrence_id) if e.recurrence_id is not None else format_moment(occurrence.start)) if item.repeats or occurrence.changed else "", e.status,
                        e.transparency != "TRANSPARENT", e.categories, e.colour_id, other, hidden, _invited(e, yours), _your_answer(e, yours), info.read_only,
                    )))
    found.sort(key=lambda pair: (pair[0], pair[1].summary.casefold()))
    if parameters.limit and len(found) > parameters.limit:
        cut, found = True, found[: parameters.limit]
    return EventList(tuple(row for _, row in found), cut)


EVENT_LIST: TypedCommand[ListParameters, EventList] = TypedCommand("event.list", ListParameters, EventList)


@dataclass(frozen=True, slots=True)
class ItemParameters:
    item: str = field(metadata=option("The event id (see event.list)"))


def _organizes(engine: Engine, event: Event) -> bool:
    """A meeting you organize: it has attendees, and its organizer is you or nobody yet (made here before a meeting named its organizer)."""
    return bool(event.attendees) and (event.organizer is None or event.organizer.address.strip().lower() in your_addresses_lower(engine))


def _series(text: str) -> EventSeries:
    parsed = parse_events(text)
    if not parsed:
        raise UsageError("This event cannot be read (it is kept as it is)")
    return parsed[0]


def event_show(engine: Engine, parameters: ItemParameters) -> EventInfo:
    with located(engine, parameters.item, CollectionKind.EVENTS) as found:
        series = _series(found.store.get(found.item.collection, parameters.item))
        return replace(_info(parameters.item, found.account, found.item.collection, series), yours=_organizes(engine, series.master))


# --- writing ------------------------------------------------------------------------------------------------------------------------------


def _reminder(text: str, zone_name: str, before: tuple[Reminder, ...] = ()) -> Reminder:
    """'15' minutes before; '0' at the start; '-30' after; 'at:2026-10-01T09:00' at a fixed time; 'email:15' and 'email:at:2026-10-01T09:00' an e-mail
    reminder; 'keep:0' the first reminder the event has, exactly as it is."""
    cleaned = text.strip()
    try:
        if cleaned.lower().startswith("keep:"):
            return before[int(cleaned[5:])]
        if cleaned.lower().startswith("email:"):
            return replace(_reminder(cleaned[6:], zone_name), action="EMAIL")
        if cleaned.lower().startswith("at:"):
            moment = parse_moment(cleaned[3:], zone_named(zone_name) if zone_name else None)
            if not isinstance(moment, datetime):
                raise ValueError("A reminder at a fixed time needs a time")
            return Reminder(None, moment)
        return Reminder(int(cleaned))
    except (ValueError, IndexError):
        raise UsageError(f"A reminder is minutes before the start (15), email:15, or at:2026-10-01T09:00 (got '{text}')") from None


def addressed(engine: Engine, account: str, reminders: tuple[Reminder, ...]) -> tuple[Reminder, ...]:
    """An e-mail reminder made here is for your own address: the one of the account, as the standard wants it in the alarm."""
    if not any(reminder.action == "EMAIL" and not reminder.to for reminder in reminders):
        return reminders
    own = organizing_account(engine, account)
    address = own.account.email_address if own is not None else ""
    return tuple(replace(reminder, to=address) if reminder.action == "EMAIL" and not reminder.to else reminder for reminder in reminders)


def _addressed(engine: Engine, account: str, event: Event) -> Event:
    return replace(event, reminders=addressed(engine, account, event.reminders))


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
        return moment if moment in fixed else moved_like_the_start(moment, before.start, master.start)

    return replace(
        series, master=replace(master, exdates=tuple(follows(moment) for moment in master.exdates)),
        overrides=tuple(
            _moved_override(override, follows(override.recurrence_id), before, length) if override.recurrence_id is not None else override
            for override in series.overrides
        ),
    )


def _rule_sets_the_times(rule: str) -> bool:
    """A rule that names the hours or minutes itself (BYHOUR=9,15): its occurrences do not move with the start's time. BYSETPOS only picks
    among the dates (the second Tuesday): the time stays the start's."""
    parts = {part.split("=", 1)[0].strip().upper() for part in rule.split(";")}
    return bool(parts & {"BYHOUR", "BYMINUTE", "BYSECOND"})


def _day_of(moment: Moment) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def _same_first_day(before: Moment, after: Moment) -> bool:
    """The first occurrence stays on its day, each in its own zone: only its time of day, its zone, or all-day or not changed. A floating
    time and a zoned one are never compared."""
    if isinstance(before, datetime) and isinstance(after, datetime) and (before.tzinfo is None) != (after.tzinfo is None):
        return False
    return _day_of(before) == _day_of(after)


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
    reminder: tuple[str, ...] = field(default=(), metadata=option("Reminders: minutes before the start (15), email:15, or at:2026-10-01T09:00 (repeat)"))
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
    floating = not creating and isinstance(event.start, datetime) and event.start.tzinfo is None  # a time without a zone stays one (RV-93)
    try:
        if values.start is not None:
            start = parse_moment(values.start, zone, floating=floating)
            old_length = (event.end - event.start) if isinstance(event.start, datetime) and isinstance(event.end, datetime) else None
            result = replace(result, start=start)
            if values.end is None:
                if isinstance(start, datetime):
                    result = replace(result, end=start + (old_length if old_length is not None and not creating else timedelta(hours=1)))
                else:
                    result = replace(result, end=start + timedelta(days=1))
        if values.end is not None:
            end = parse_moment(values.end, zone, all_day=result.all_day, floating=floating)
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
        if len(values.reminder) > max(MOST_REMINDERS, len(result.reminders)):  # an event from elsewhere keeps what it has
            raise UsageError(f"An event has at most {MOST_REMINDERS} reminders")
        result = replace(result, reminders=tuple(_reminder(text, zone_name, event.reminders) for text in values.reminder))
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
    tell: bool = field(default=True, metadata=option("A meeting: invite its attendees; false: nobody is told"))


def _with_organizer(event: Event, settings: AccountSettings | None) -> Event:
    return replace(event, organizer=organizer_from(settings)) if event.organizer is None and settings is not None else event


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
            event = _addressed(engine, account, event)
            you = organizing_account(engine, account) if _organizes(engine, event) else None
            series = EventSeries(_with_organizer(event, you))
            text = events_to_text(series)
            mails = who_mails(engine, (account, info), account, you.account.email_address.strip().lower(), answers=False) if you is not None else None
            mails = quiet(mails) if mails is not None and not parameters.tell else mails
            with item_writer(engine, account, store) as writer:
                item = writer.put(info, copy_for(text, mails) if mails is not None else text)
            draft = MeetingDraft()
            if you is not None and mails is not None and mails.chrono:
                draft = meeting_mail(engine, you, text, event.summary, "REQUEST", "Invitation", f"You are invited to {event.summary or 'a meeting'}.\n")
            return replace(_info(item, account, info.collection_id, series), draft=draft)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditParameters(EventFields):
    item: str = field(metadata=option("The event id"))
    occurrence: str = field(default="", metadata=option("For a repeating event: the occurrence to change on its own (the 'occurrence' of event.list); empty changes the whole series"))
    clear: tuple[str, ...] = field(default=(), metadata=option("Lists to empty: reminder, attendee, tag (repeat)"))
    add_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to add (repeat)"))
    remove_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to remove (repeat)"))
    tell: bool = field(default=False, metadata=option("A meeting you organize: tell the attendees of this change"))


def in_event_zone(moment: Moment, start: Moment) -> Moment:
    """An occurrence given as text, in the zone of the event it belongs to (so that it is written with that zone's name, not with a bare offset)."""
    if isinstance(start, datetime) and start.tzinfo is not None:
        if isinstance(moment, datetime):
            return moment.astimezone(start.tzinfo) if moment.tzinfo is not None else moment.replace(tzinfo=start.tzinfo)
        return datetime(moment.year, moment.month, moment.day, tzinfo=start.tzinfo)
    if not isinstance(start, datetime):
        return moment.date() if isinstance(moment, datetime) else moment
    if isinstance(moment, datetime) and moment.tzinfo is not None:  # a floating series: its occurrences are floating too, on this wall clock
        return moment.astimezone(local_zone()).replace(tzinfo=None)  # (a zoned one never matches its skipped or changed dates)
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


def _moment_of(text: str, series: EventSeries) -> Moment:
    try:
        return in_event_zone(parse_moment(text), series.master.start)
    except ValueError as error:
        raise UsageError(f"occurrence: {error}") from None


def _not_cancelled(series: EventSeries, moment: Moment | None) -> None:
    """A Cancelled event takes no change until it is Restored; only Restore and Delete act on it (OR-4, D-310)."""
    if cancelled_on(series, moment):
        raise UsageError("This event is cancelled: restore it first")


def _taken_off_mail(engine: Engine, you: AccountSettings, before: EventSeries, original: str, text: str, dates: tuple[Moment, ...]) -> MeetingDraft:
    """The cancellation for the attendees taken off your meeting (RFC 5546 section 3.2.5): the meeting as they knew it, one version on, so their programs take it."""
    gone = frozenset(address.lower() for address in attendee_addresses(original)) - frozenset(address.lower() for address in attendee_addresses(text))
    if not gone:
        return MeetingDraft()
    newer = replace(before, master=replace(before.master, sequence=before.master.sequence + 1), overrides=tuple(replace(each, sequence=each.sequence + 1) for each in before.overrides))
    title = before.master.summary
    return meeting_mail(engine, you, events_to_text(newer, original), title, "CANCEL", "Cancelled", f"You are no longer on the list for {title or 'this meeting'}.\n", dates, only=gone)


def event_edit(engine: Engine, parameters: EditParameters) -> EventInfo:
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            before = series
            moment: Moment | None = None
            if parameters.status == "cancelled":
                raise UsageError("Cancelled is set with event.cancel, which tells the attendees the right thing")
            if parameters.occurrence:
                moment = _moment_of(parameters.occurrence, series)
                _not_cancelled(series, moment)
                base = _occurrence_event(series, moment)
                if parameters.rrule:
                    raise UsageError("One occurrence cannot have its own rule")
                changed = _addressed(engine, found.account, _apply(_clear(base, parameters.clear), parameters, creating=False))
                if parameters.colour is not None:
                    changed = _with_colour(changed, palette_for(engine.config().find(found.account), found.collection), parameters.colour.strip())
                known = any(o.recurrence_id == moment for o in series.overrides)  # in its own place: the order says which one stands for
                overrides = tuple(changed if o.recurrence_id == moment else o for o in series.overrides) if known else (*series.overrides, changed)
                series = replace(series, overrides=overrides)  # the event in an object of only changed occurrences (RV-186)
                if not series.own_master and overrides[0].recurrence_id == moment:
                    series = replace(series, master=replace(changed, recurrence_id=None))  # the occurrence that stands for the event changed
            else:
                _not_cancelled(series, None)
                master = _addressed(engine, found.account, _apply(_clear(series.master, parameters.clear), parameters, creating=False))
                if parameters.add_tag or parameters.remove_tag:
                    master = replace(master, categories=changed_tags(master.categories, parameters.add_tag, parameters.remove_tag))
                if parameters.colour is not None:
                    master = _with_colour(master, palette_for(engine.config().find(found.account), found.collection), parameters.colour.strip())
                series = _following_the_start(series, master)
            if not _organizes(engine, series.master) and not _organizes(engine, before.master):  # (the last attendee taken off still hears of it)
                with item_writer(engine, found.account, found.store) as writer:
                    writer.put(found.collection, events_to_text(series, original), parameters.item)
                return _info(parameters.item, found.account, found.item.collection, series)
            you = organizing_account(engine, found.account)
            series = replace(series, master=_with_organizer(series.master, you))
            if parameters.tell and moment is not None:
                series = replace(series, overrides=tuple(replace(o, sequence=o.sequence + 1) if o.recurrence_id == moment else o for o in series.overrides))
            elif parameters.tell:  # a higher number: the attendees' programs take it as the newer version (RFC 5546)
                series = replace(series, master=replace(series.master, sequence=series.master.sequence + 1))
            text = events_to_text(series, original)
            address = you.account.email_address.strip().lower() if you is not None else ""
            mails = who_mails(engine, (found.account, found.collection), found.account, address, answers=False)
            mails = mails if parameters.tell else quiet(mails)
            with item_writer(engine, found.account, found.store) as writer:
                writer.put(found.collection, copy_for(text, mails), parameters.item)
            draft = MeetingDraft()
            dates = (moment,) if moment is not None else ()
            if you is not None and mails.chrono:
                draft = meeting_mail(engine, you, text, series.master.summary, "REQUEST", "Changed", f"{series.master.summary or 'The meeting'} has changed.\n", dates)
            cancel = _taken_off_mail(engine, you, before, original, text, dates) if you is not None and mails.chrono else MeetingDraft()
            return replace(_info(parameters.item, found.account, found.item.collection, series), draft=draft, cancel_draft=cancel)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    item: str = field(metadata=option("The event id"))
    occurrence: str = field(default="", metadata=option("For a repeating event: only this occurrence (the 'occurrence' of event.list); empty deletes the whole event"))
    tell: bool = field(default=False, metadata=option("A meeting you organize: tell the attendees it is cancelled"))


@dataclass(frozen=True, slots=True)
class DeleteResult:
    item: str
    deleted: bool  # false when only one occurrence was taken out of a series
    draft: MeetingDraft = MeetingDraft()  # the cancellation to the attendees, ready to send


def _silent(engine: Engine, found: Located, gone: str = "") -> Mailing:
    """Your copy of someone else's meeting changes without a word from here, and the server is kept silent as far as it can be (D-310)."""
    settings = settings_of(engine, found.account)
    address = settings.account.email_address.strip().lower() if settings is not None else ""
    return quiet(who_mails(engine, (found.account, found.collection), found.account, address, gone))


def _news_of_delete(mails: Mailing, tell: bool, cancelled: bool) -> Mailing:
    """Who hears of the delete of your meeting. A Cancelled meeting: Chrono Mail mails nothing and a server that listens stays silent, as Cancel was the
    moment to tell them; a server that mails whatever it is told is followed (D-310)."""
    if cancelled:
        return replace(mails, server=mails.server and not mails.told, chrono=False)
    return mails if tell else quiet(mails)


def _delete_whole(engine: Engine, parameters: DeleteParameters, found: Located) -> DeleteResult:
    """The whole event goes; a damaged one too, which nothing can be said about. Your meeting: the attendees hear of it as the person said. Someone
    else's meeting: nothing is mailed, the server stays silent too (D-310)."""
    try:
        original = found.store.get(found.item.collection, parameters.item)
        parsed = parse_events(original)
    except StorageError:
        original, parsed = "", list[EventSeries]()
    you = organizing_account(engine, found.account) if parsed and _organizes(engine, parsed[0].master) else None
    if you is None:
        others = bool(parsed) and _others_meeting(parsed[0].master, your_addresses_lower(engine))
        with item_writer(engine, found.account, found.store) as writer:
            delete_copy(writer, found.collection, parameters.item, original, _silent(engine, found, parameters.item) if others else Mailing())
        take_mark_off(engine, original)
        return DeleteResult(parameters.item, True)
    mails = who_mails(engine, (found.account, found.collection), found.account, you.account.email_address.strip().lower(), parameters.item, answers=False)
    mails = _news_of_delete(mails, parameters.tell, cancelled_on(parsed[0], None))
    with item_writer(engine, found.account, found.store) as writer:
        delete_copy(writer, found.collection, parameters.item, original, mails)
    draft = MeetingDraft()
    if mails.chrono:
        title = parsed[0].master.summary
        draft = meeting_mail(engine, you, original, title, "CANCEL", "Cancelled", f"{title or 'The meeting'} is cancelled.\n")
    return DeleteResult(parameters.item, True, draft)


def event_delete(engine: Engine, parameters: DeleteParameters) -> DeleteResult:
    """Delete an event, or one occurrence of a repeating event: its date goes on the series' list of removed dates, for good. The file of a whole event is
    kept in .deleted for 30 days. For one date of someone else's meeting no silent way is known on a server that mails whatever it is told: that is followed."""
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            if not parameters.occurrence:
                return _delete_whole(engine, parameters, found)
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            moment = _moment_of(parameters.occurrence, series)
            if series.own_master and not series.master.repeats:
                raise UsageError("This event does not repeat")
            overrides = tuple(o for o in series.overrides if o.recurrence_id != moment)
            if series.own_master:
                changed = replace(series, master=replace(series.master, exdates=tuple(dict.fromkeys((*series.master.exdates, moment)))), overrides=overrides)
            elif len(overrides) == len(series.overrides):
                raise UsageError("This date is not in this event")
            elif not overrides:
                return _delete_whole(engine, replace(parameters, occurrence=""), found)  # its last date
            else:
                changed = replace(series, master=replace(overrides[0], recurrence_id=None), overrides=overrides)  # an object of only changed dates: no list of removed dates (RV-186)
            text = events_to_text(changed, original)
            you = organizing_account(engine, found.account) if _organizes(engine, series.master) else None
            if you is None:
                others = _others_meeting(series.master, your_addresses_lower(engine))
                with item_writer(engine, found.account, found.store) as writer:
                    writer.put(found.collection, copy_for(text, _silent(engine, found)) if others else text, parameters.item)
                return DeleteResult(parameters.item, False)
            mails = who_mails(engine, (found.account, found.collection), found.account, you.account.email_address.strip().lower(), answers=False)
            mails = _news_of_delete(mails, parameters.tell, cancelled_on(series, moment))
            with item_writer(engine, found.account, found.store) as writer:
                writer.put(found.collection, copy_for(text, mails), parameters.item)
            draft = MeetingDraft()
            if mails.chrono:
                draft = meeting_mail(engine, you, original, series.master.summary, "CANCEL", "Cancelled", f"One date of {series.master.summary or 'the meeting'} is cancelled.\n", (moment,))
            return DeleteResult(parameters.item, False, draft)
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
                parsed = parse_events(found.store.get(found.item.collection, parameters.item))
                if parsed:  # (a damaged event can still be moved)
                    _not_cancelled(parsed[0], None)
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
                given = given_parts(parameters.text, "VEVENT")  # read once for the whole text
                for series in parsed:
                    known = store.index.find_uid(info.collection_id, series.master.uid)
                    text = kept_from(events_to_text(series), given, series.master.uid, "VEVENT")  # its own time zones, X- and CREATED kept
                    items.append(writer.put(info, text, known.item_id if known else None))
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


@dataclass(frozen=True, slots=True)
class StatusParameters:
    item: str = field(metadata=option("The event id"))
    occurrence: str = field(default="", metadata=option("For a repeating event: only this occurrence (the 'occurrence' of event.list); empty is the whole event"))
    tell: bool = field(default=False, metadata=option("A meeting you organize: tell the attendees"))


def _dates_for_status(parameters: StatusParameters, series: EventSeries, text: str) -> tuple[Moment, ...]:
    if not parameters.occurrence:
        return ()
    moment = _moment_of(parameters.occurrence, series)
    _occurrence_event(series, moment)  # (a single event has no dates to act on)
    if not holds_occurrence(text, moment):
        raise UsageError("This date is not in this event")
    return (moment,)


def _next_version(series: EventSeries, dates: tuple[Moment, ...]) -> int:
    """One above what the attendees know of what changes (RFC 5546): their programs then take it as the newer version."""
    changing = tuple(_occurrence_event(series, moment) for moment in dates) or (series.master, *series.overrides)
    return max(each.sequence for each in changing) + 1


def _status_written(engine: Engine, parameters: StatusParameters, found: Located, original: str, dates: tuple[Moment, ...], cancelling: bool) -> EventInfo:
    """Server first: the status is written, and only then a mail is made. Your own meeting: the attendees hear of it as the person said. Someone else's
    meeting: your own copy only, same version, nothing is mailed and the server stays silent; the organizer's next version replaces it (D-310)."""
    series = _series(original)
    changed = cancelled_copy if cancelling else restored_copy
    you = organizing_account(engine, found.account) if _organizes(engine, series.master) else None
    if you is None:
        text = changed(original, dates)
        if text is None:
            raise UsageError("This event could not be read")
        mails = _silent(engine, found) if _others_meeting(series.master, your_addresses_lower(engine)) else Mailing()
        with item_writer(engine, found.account, found.store) as writer:
            writer.put(found.collection, copy_for(text, mails), parameters.item)
        return _info(parameters.item, found.account, found.item.collection, _series(text))
    address = you.account.email_address.strip().lower()
    mails = news_of_status(who_mails(engine, (found.account, found.collection), found.account, address, answers=False), parameters.tell)
    named = original if series.master.organizer is not None else with_organizer(original, address) or original
    version = _next_version(series, dates) if parameters.tell else None
    text = cancelled_copy(named, dates, version) if cancelling else restored_copy(named, dates, version, your_addresses_lower(engine))
    if text is None:
        raise UsageError("This event could not be read")
    with item_writer(engine, found.account, found.store) as writer:
        writer.put(found.collection, copy_for(text, mails), parameters.item)
    draft = MeetingDraft()
    if mails.chrono:
        title = series.master.summary
        about = f"One date of {title or 'the meeting'}" if dates else title or "The meeting"
        draft = meeting_mail(engine, you, text, title, "CANCEL", "Cancelled", f"{about} is cancelled.\n", dates) if cancelling else meeting_mail(engine, you, text, title, "REQUEST", "Restored", f"{about} is on again.\n", dates)
    return replace(_info(parameters.item, found.account, found.item.collection, _series(text)), draft=draft)


def event_cancel(engine: Engine, parameters: StatusParameters) -> EventInfo:
    """Cancelled, on the whole event or one date: it still exists, inert (D-316). All of it also covers a changed date with no status of its own; a date with
    its own status keeps it."""
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            dates = _dates_for_status(parameters, series, original)
            if cancelled_in(original, dates):
                raise UsageError("This event is cancelled already")
            yours = your_addresses_lower(engine)
            if _declined(_occurrence_event(series, dates[0]), yours) if dates else you_declined(original, yours):
                raise UsageError("A declined event cannot be cancelled")
            return _status_written(engine, parameters, found, original, dates, cancelling=True)
    except StorageError as error:
        raise UsageError(str(error)) from None


def event_restore(engine: Engine, parameters: StatusParameters) -> EventInfo:
    """Cancelled taken off, leaving no status, on the whole event or one date. All of it takes it off every date that has it. Your own meeting told of it: a
    new version, and the attendees answer again (D-310)."""
    try:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if found.collection.read_only:
                raise UsageError("This calendar is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            series = _series(original)
            dates = _dates_for_status(parameters, series, original)
            cancelled = cancelled_in(original, dates) if dates else any(each.status == "CANCELLED" for each in (series.master, *series.overrides))
            if not cancelled:
                raise UsageError("This event is not cancelled")
            return _status_written(engine, parameters, found, original, dates, cancelling=False)
    except StorageError as error:
        raise UsageError(str(error)) from None


EVENT_CANCEL: TypedCommand[StatusParameters, EventInfo] = TypedCommand("event.cancel", StatusParameters, EventInfo)
EVENT_RESTORE: TypedCommand[StatusParameters, EventInfo] = TypedCommand("event.restore", StatusParameters, EventInfo)


def register(registry: CommandRegistry) -> None:
    registry.register(EVENT_LIST.id, "List the events of some days, repeating ones expanded", ListParameters, event_list, doing="Loading the calendar")
    registry.register(EVENT_SHOW.id, "Show an event", ItemParameters, event_show, doing="Loading an event")
    registry.register(EVENT_ADD.id, "Add an event", AddParameters, event_add, doing="Adding an event")
    registry.register(EVENT_EDIT.id, "Change an event, or one occurrence of a repeating event", EditParameters, event_edit, doing="Saving an event")
    registry.register(EVENT_DELETE.id, "Delete an event, or one occurrence of a repeating event", DeleteParameters, event_delete, destructive=True, doing="Deleting an event")
    registry.register(EVENT_CANCEL.id, "Cancel an event, or one occurrence of a repeating event: it stays, greyed", StatusParameters, event_cancel, doing="Cancelling an event")
    registry.register(EVENT_RESTORE.id, "Take Cancelled off an event, or one occurrence of a repeating event", StatusParameters, event_restore, doing="Restoring an event")
    registry.register("event.move", "Move an event to another calendar of the same account", MoveParameters, event_move, doing="Moving an event")
    registry.register("event.import", "Import events from iCalendar text", ImportParameters, quiet_command(event_import), doing="Importing events")
    registry.register("event.export", "Export an event or a calendar as iCalendar text", ExportParameters, event_export, doing="Exporting events")
