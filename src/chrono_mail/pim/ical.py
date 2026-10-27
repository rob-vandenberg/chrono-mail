import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any  # icalendar hands out untyped property objects; every value is checked before it is used

import icalendar
import recurring_ical_events

# --- Version ------------------------------------------------------------
__version__ = "ical.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: kept_from: a text built afresh for an import keeps from the text it came in only the time zones it uses (VTIMEZONE), its own
#           X- properties (CREATED it carries already); never anything a server would refuse; given_parts reads the imported text once for all
#           its items; a changed occurrence is matched by the moment of its RECURRENCE-ID, in any written form
# v3.6.356: an event kept with a length (DURATION), or with no end written at all, whose start alone changed keeps its end: the end is
#           written whenever it was not written as an end (CR-58)
# v3.6.351: a change of the attendee list keeps each attendee's own line (CUTYPE, DELEGATED-*, SCHEDULE-*), writing afresh only
#           the changed fields and the new attendees (RV-17)
# v3.2.342: an object with only changed occurrences gets no made-up main event when it is written (RV-146)
# v1.1.219: An event's colour: the id of a colour of its calendar's palette (X-CHRONO-MAIL-COLOR-ID) and the CSS name of RFC 7986 COLOR (D-191)
# v1.1.218: occurrences stops expanding at the end of the range or the cap (an event every second no longer freezes a view); limit (the Windows computer's report)
# v0.0.60: Commands for collections, tags, contacts, events and tasks
# v0.0.58: Initial events and tasks from and to iCalendar, reminders, attendees, recurrence

MAXIMUM_TEXT_CHARACTERS: int = 16 * 1024 * 1024
MAXIMUM_COMPONENTS: int = 20_000
MAXIMUM_ATTENDEES: int = 500
MAXIMUM_REMINDERS: int = 20
MAXIMUM_DATES: int = 5_000  # exceptions and extra dates of one repeating item
MAXIMUM_OCCURRENCES: int = 5_000  # one range of a calendar view
COLOUR_ID_PROPERTY: str = "X-CHRONO-MAIL-COLOR-ID"  # which colour of its calendar's palette an event has (D-191)

# The library reads a damaged value of an event as a "broken" property and goes on; for alarms and tasks it raises and loses the whole calendar. Servers
# and old programs send damaged values often enough that one bad alarm must not hide an event, so the tolerant reading is switched on for them too.
for _tolerant in (icalendar.Alarm, icalendar.Todo):
    setattr(_tolerant, "ignore_exceptions", True)

PRODUCT_ID: str = "-//Chrono Mail//Chrono Mail//EN"

Moment = date | datetime  # a date is an all-day value; a datetime is aware, or floating (no zone: the wall-clock time wherever it is shown)


@dataclass(frozen=True, slots=True)
class Reminder:
    """An alarm: minutes before the start of an event (or the due time of a task; negative: after), or at a fixed time."""

    minutes_before: int | None = 15
    at: datetime | None = None
    action: str = "DISPLAY"
    relative_to_end: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class Attendee:
    address: str
    name: str = ""
    role: str = "REQ-PARTICIPANT"
    status: str = "NEEDS-ACTION"
    rsvp: bool = False


@dataclass(frozen=True, slots=True)
class Event:
    uid: str = ""
    summary: str = ""
    start: Moment = date(2000, 1, 1)
    end: Moment = date(2000, 1, 2)
    description: str = ""
    location: str = ""
    rrule: str = ""
    exdates: tuple[Moment, ...] = ()
    rdates: tuple[Moment, ...] = ()
    recurrence_id: Moment | None = None  # set on a changed occurrence of a repeating event
    status: str = ""  # CONFIRMED, TENTATIVE, CANCELLED or empty
    transparency: str = "OPAQUE"  # TRANSPARENT: does not block the time
    classification: str = ""
    categories: tuple[str, ...] = ()
    organizer: Attendee | None = None
    attendees: tuple[Attendee, ...] = ()
    reminders: tuple[Reminder, ...] = ()
    sequence: int = 0
    url: str = ""
    created: datetime | None = None
    modified: datetime | None = None
    colour_id: str = ""  # a colour of its calendar's palette (X-CHRONO-MAIL-COLOR-ID); empty: the calendar's own colour
    css_colour: str = ""  # RFC 7986 COLOR: a CSS colour name (what other programs read; Chrono Mail writes the nearest one to its colour)

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    @property
    def repeats(self) -> bool:
        return bool(self.rrule or self.rdates)


@dataclass(frozen=True, slots=True)
class EventSeries:
    """What one calendar object holds: an event, and the occurrences of it that were changed."""

    master: Event
    overrides: tuple[Event, ...] = ()
    own_master: bool = True  # False: the object holds only changed occurrences (an invitation to one occurrence of someone else's series);
    # its master is then a stand-in made from the first of them and is never written as an event of its own (RV-146)


@dataclass(frozen=True, slots=True)
class Task:
    uid: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    start: Moment | None = None
    due: Moment | None = None
    completed: datetime | None = None
    percent: int = 0
    status: str = "NEEDS-ACTION"  # NEEDS-ACTION, IN-PROCESS, COMPLETED, CANCELLED
    priority: int = 0  # 0 none, 1 highest ... 9 lowest
    rrule: str = ""
    categories: tuple[str, ...] = ()
    reminders: tuple[Reminder, ...] = ()
    parent: str = ""
    sequence: int = 0
    url: str = ""
    created: datetime | None = None
    modified: datetime | None = None

    @property
    def done(self) -> bool:
        return self.status == "COMPLETED" or self.completed is not None


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One time an event happens, for a view of a range of days."""

    event: Event
    start: Moment
    end: Moment
    changed: bool = False  # this occurrence was changed or moved on its own


def new_uid() -> str:
    return f"{uuid.uuid4()}@chrono-mail"


# --- reading properties ---------------------------------------------------------------------------------------------------------------


def _parse(text: str | bytes) -> icalendar.Calendar | None:
    """The calendar in a text, or None for anything that is not one. The library can raise many kinds of error on damaged text; none gets out."""
    try:
        if len(text) > MAXIMUM_TEXT_CHARACTERS:
            return None
        parsed = icalendar.Calendar.from_ical(text.decode("utf-8", "replace") if isinstance(text, bytes) else text)
    except Exception:  # noqa: BLE001 - damaged input must never stop the program, whatever the library raises
        return None
    return parsed if isinstance(parsed, icalendar.Calendar) else None


def _text(component: icalendar.Component, name: str) -> str:
    value = component.get(name)
    return str(value).strip() if value is not None else ""


def _number(component: icalendar.Component, name: str) -> int:
    try:
        return int(str(component.get(name, 0)))
    except (ValueError, OverflowError):
        return 0


def _value_of(prop: object) -> object:
    """The value of a date or time property, or None when it is damaged (the library raises on reading a broken one)."""
    try:
        return getattr(prop, "dt", None)
    except Exception:  # noqa: BLE001 - a damaged property is a missing property
        return None


def _moment(component: icalendar.Component, name: str) -> Moment | None:
    value = _value_of(component.get(name))
    if isinstance(value, datetime):
        return value
    return value if isinstance(value, date) else None


def _stamp(component: icalendar.Component, name: str) -> datetime | None:
    value = _moment(component, name)
    return value if isinstance(value, datetime) else None


def _moments(component: icalendar.Component, name: str) -> tuple[Moment, ...]:
    """The values of a property that may be written several times and as lists (EXDATE, RDATE)."""
    props: Any = component.get(name)
    found: list[Moment] = []
    for prop in props if isinstance(props, list) else ([props] if props is not None else []):
        try:
            entries = list(getattr(prop, "dts", []))
        except Exception:  # noqa: BLE001 - a damaged list is an empty one
            entries = list[object]()
        for entry in entries:
            value = _value_of(entry)
            if isinstance(value, datetime) or isinstance(value, date):
                found.append(value)
            if len(found) >= MAXIMUM_DATES:
                return tuple(found)
    return tuple(found)


def _categories(component: icalendar.Component) -> tuple[str, ...]:
    props: Any = component.get("CATEGORIES")
    names: list[str] = []
    for prop in props if isinstance(props, list) else ([props] if props is not None else []):
        names.extend(str(category).strip() for category in getattr(prop, "cats", []))
    return tuple(dict.fromkeys(name for name in names if name))


def _rrule(component: icalendar.Component) -> str:
    prop = component.get("RRULE")
    if prop is None:
        return ""
    try:
        return prop.to_ical().decode("utf-8")
    except Exception:  # noqa: BLE001 - an odd rule is dropped, the event stays
        return ""


def _address(prop: Any) -> str:  # noqa: ANN401
    text = str(prop).strip()
    return text[7:] if text.lower().startswith("mailto:") else text


def _person(prop: Any) -> Attendee:  # noqa: ANN401
    params = getattr(prop, "params", {})
    return Attendee(
        _address(prop), str(params.get("CN", "")), str(params.get("ROLE", "REQ-PARTICIPANT")).upper(), str(params.get("PARTSTAT", "NEEDS-ACTION")).upper(),
        str(params.get("RSVP", "")).upper() == "TRUE",
    )


def _attendees(component: icalendar.Component) -> tuple[Attendee, ...]:
    props: Any = component.get("ATTENDEE")
    found = [_person(prop) for prop in (props if isinstance(props, list) else ([props] if props is not None else []))]
    return tuple(found[:MAXIMUM_ATTENDEES])


def _reminders(component: icalendar.Component) -> tuple[Reminder, ...]:
    """The alarms of an item. The text of an alarm that is the title of the item itself is not kept: it is what is written when there is none."""
    summary = _text(component, "SUMMARY")
    found: list[Reminder] = []
    for alarm in component.subcomponents:
        if alarm.name != "VALARM" or len(found) >= MAXIMUM_REMINDERS:
            continue
        try:
            trigger = alarm.get("TRIGGER")
            value = _value_of(trigger)
            action = _text(alarm, "ACTION").upper() or "DISPLAY"
            description = _text(alarm, "DESCRIPTION")
            if description == summary:
                description = ""
            if isinstance(value, timedelta):
                related_end = str(getattr(trigger, "params", {}).get("RELATED", "")).upper() == "END"
                found.append(Reminder(-int(value.total_seconds() // 60), None, action, related_end, description))
            elif isinstance(value, datetime):
                found.append(Reminder(None, value, action, False, description))
        except Exception:  # noqa: BLE001 - a damaged alarm is left out, the item stays
            continue
    return tuple(found)


def _event_from(component: icalendar.Component) -> Event | None:
    start = _moment(component, "DTSTART")
    if start is None:
        return None
    end = _moment(component, "DTEND")
    if end is None:
        duration = component.get("DURATION")
        delta = getattr(duration, "dt", None)
        end = start + delta if isinstance(delta, timedelta) else (start + timedelta(days=1) if not isinstance(start, datetime) else start)
    try:
        sequence = _number(component, "SEQUENCE")
    except OverflowError:
        sequence = 0
    organizer = component.get("ORGANIZER")
    return Event(
        _text(component, "UID"), _text(component, "SUMMARY"), start, end, str(component.get("DESCRIPTION", "")).strip(), _text(component, "LOCATION"),
        _rrule(component), _moments(component, "EXDATE"), _moments(component, "RDATE"), _moment(component, "RECURRENCE-ID"),
        _text(component, "STATUS").upper(), _text(component, "TRANSP").upper() or "OPAQUE", _text(component, "CLASS").upper(), _categories(component),
        _person(organizer) if organizer is not None else None, _attendees(component), _reminders(component), sequence, _text(component, "URL"),
        _stamp(component, "CREATED"), _stamp(component, "LAST-MODIFIED"), _text(component, COLOUR_ID_PROPERTY), _text(component, "COLOR"),
    )


def parse_events(text: str | bytes) -> list[EventSeries]:
    """The events in a text, one series per UID (an event and its changed occurrences). Never raises."""
    calendar = _parse(text)
    if calendar is None:
        return []
    masters: dict[str, Event] = {}
    overrides: dict[str, list[Event]] = {}
    order: list[str] = []
    for component in list(calendar.walk("VEVENT"))[:MAXIMUM_COMPONENTS]:
        try:
            event = _event_from(component)
        except Exception:  # noqa: BLE001 - one damaged event is skipped, the others are read
            continue
        if event is None:
            continue
        uid = event.uid or new_uid()
        event = replace(event, uid=uid)
        if event.recurrence_id is not None:
            overrides.setdefault(uid, []).append(event)
        elif uid not in masters:
            masters[uid] = event
        if uid not in order:
            order.append(uid)
    series: list[EventSeries] = []
    for uid in order:
        changed = overrides.get(uid, [])
        master = masters.get(uid) or (replace(changed[0], recurrence_id=None) if changed else None)
        if master is not None:
            series.append(EventSeries(master, tuple(changed), own_master=uid in masters))
    return series


def _task_from(component: icalendar.Component) -> Task:
    status = _text(component, "STATUS").upper() or "NEEDS-ACTION"
    completed = _stamp(component, "COMPLETED")
    percent = max(0, min(100, _number(component, "PERCENT-COMPLETE")))
    return Task(
        _text(component, "UID") or new_uid(), _text(component, "SUMMARY"), str(component.get("DESCRIPTION", "")).strip(), _text(component, "LOCATION"),
        _moment(component, "DTSTART"), _moment(component, "DUE"), completed, percent, status, max(0, min(9, _number(component, "PRIORITY"))),
        _rrule(component), _categories(component), _reminders(component), _text(component, "RELATED-TO"), _number(component, "SEQUENCE"), _text(component, "URL"),
        _stamp(component, "CREATED"), _stamp(component, "LAST-MODIFIED"),
    )


def parse_tasks(text: str | bytes) -> list[Task]:
    """The tasks (VTODO) in a text. Never raises."""
    calendar = _parse(text)
    if calendar is None:
        return []
    tasks: list[Task] = []
    for component in list(calendar.walk("VTODO"))[:MAXIMUM_COMPONENTS]:
        try:
            tasks.append(_task_from(component))
        except Exception:  # noqa: BLE001 - one damaged task is skipped
            continue
    return tasks


# --- writing --------------------------------------------------------------------------------------------------------------------------


def _zone_of(value: Moment | None) -> tzinfo | None:
    return value.tzinfo if isinstance(value, datetime) else None


def _timezone_components(values: Iterable[Moment | None], present: set[str]) -> list[icalendar.Timezone]:
    """VTIMEZONE components for the zones of these values, except for UTC and the zones the calendar has already."""
    made: list[icalendar.Timezone] = []
    for value in values:
        zone = _zone_of(value)
        if zone is None or zone.utcoffset(None) == timedelta(0) and getattr(zone, "key", "UTC") in ("UTC", "Etc/UTC"):
            continue
        key = getattr(zone, "key", None) or getattr(zone, "zone", None)
        if not isinstance(key, str) or key in present or key == "UTC":
            continue
        try:
            made.append(icalendar.Timezone.from_tzinfo(zone))
        except Exception:  # noqa: BLE001 - a zone that cannot be described is written as UTC instead of failing the save
            continue
        present.add(key)
    return made


def _set(component: icalendar.Component, name: str, value: str | int | Moment | None, old: object) -> None:
    """Write a property when it differs from what is there (so that what was not edited stays exactly as it was); remove it when empty."""
    if value == old:
        return
    component.pop(name, None)
    if value is None or value == "":
        return
    component.add(name, value)


def _set_list(component: icalendar.Component, name: str, values: tuple[Moment, ...], old: tuple[Moment, ...]) -> None:
    if values == old:
        return
    component.pop(name, None)
    for value in values:
        component.add(name, value)


def _set_categories(component: icalendar.Component, values: tuple[str, ...], old: tuple[str, ...]) -> None:
    if values == old:
        return
    component.pop("CATEGORIES", None)
    if values:
        component.add("CATEGORIES", list(values))


def _set_rrule(component: icalendar.Component, text: str, old: str) -> None:
    if text == old:
        return
    component.pop("RRULE", None)
    if text:
        component.add("RRULE", icalendar.vRecur.from_ical(text))


def _person_prop(person: Attendee, organizer: bool) -> icalendar.vCalAddress:
    prop = icalendar.vCalAddress(f"mailto:{person.address}")
    if person.name:
        prop.params["CN"] = person.name
    if not organizer:
        prop.params["ROLE"] = person.role
        prop.params["PARTSTAT"] = person.status
        if person.rsvp:
            prop.params["RSVP"] = "TRUE"
    return prop


def _kept_person_prop(person: Attendee, earlier: Any) -> Any:  # noqa: ANN401 - icalendar's property objects are untyped
    """An attendee's line: the one the item had for this address, with all it says that this program does not show (CUTYPE=ROOM,
    DELEGATED-TO, SCHEDULE-STATUS, ...), and the name, role, answer and RSVP as now; a new attendee's line written afresh (RV-17)."""
    if earlier is None:
        return _person_prop(person, False)
    if _person(earlier) == person:
        return earlier  # unchanged: exactly as it was
    prop = icalendar.vCalAddress(str(earlier))
    prop.params.update(getattr(earlier, "params", {}))
    for key, value in (("CN", person.name), ("ROLE", person.role), ("PARTSTAT", person.status)):
        if value:
            prop.params[key] = value
        else:
            prop.params.pop(key, None)
    if person.rsvp:
        prop.params["RSVP"] = "TRUE"
    else:
        prop.params.pop("RSVP", None)
    return prop


def _set_people(component: icalendar.Component, event: Event, old: Event | None) -> None:
    if old is None or event.organizer != old.organizer:
        component.pop("ORGANIZER", None)
        if event.organizer is not None:
            component.add("ORGANIZER", _person_prop(event.organizer, True))
    if old is None or event.attendees != old.attendees:
        props: Any = component.get("ATTENDEE")
        written = {_address(prop).lower(): prop for prop in (props if isinstance(props, list) else ([props] if props is not None else []))}
        component.pop("ATTENDEE", None)
        for person in event.attendees:
            component.add("ATTENDEE", _kept_person_prop(person, written.get(person.address.lower())))


def _set_reminders(component: icalendar.Component, reminders: tuple[Reminder, ...], old: tuple[Reminder, ...], summary: str) -> None:
    if reminders == old:
        return
    component.subcomponents[:] = [part for part in component.subcomponents if part.name != "VALARM"]
    for reminder in reminders[:MAXIMUM_REMINDERS]:
        alarm = icalendar.Alarm()
        alarm.add("ACTION", reminder.action)
        alarm.add("DESCRIPTION", reminder.description or summary or "Reminder")
        if reminder.at is not None:
            alarm.add("TRIGGER", reminder.at.astimezone(UTC), parameters={"VALUE": "DATE-TIME"})
        else:
            parameters = {"RELATED": "END"} if reminder.relative_to_end else {}
            alarm.add("TRIGGER", timedelta(minutes=-(reminder.minutes_before or 0)), parameters=parameters)
        component.add_component(alarm)


def _write_event(component: icalendar.Component, event: Event, old: Event | None) -> None:
    now = datetime.now(UTC)
    _set(component, "UID", event.uid, old.uid if old else None)
    _set(component, "SUMMARY", event.summary, old.summary if old else None)
    _set(component, "DESCRIPTION", event.description, old.description if old else None)
    _set(component, "LOCATION", event.location, old.location if old else None)
    _set(component, "DTSTART", event.start, old.start if old else None)
    timed_anew = old is None or event.start != old.start or event.end != old.end
    end_unwritten = timed_anew and ("DURATION" in component or "DTEND" not in component)  # the end kept as a length, or only implied: written whatever it was (CR-58)
    if timed_anew:
        component.pop("DURATION", None)  # the end is written as an end; a duration left behind would contradict it
    _set(component, "DTEND", event.end, None if end_unwritten else (old.end if old else None))
    _set(component, "RECURRENCE-ID", event.recurrence_id, old.recurrence_id if old else None)
    _set_rrule(component, event.rrule, old.rrule if old else "")
    _set_list(component, "EXDATE", event.exdates, old.exdates if old else ())
    _set_list(component, "RDATE", event.rdates, old.rdates if old else ())
    _set(component, "STATUS", event.status, old.status if old else None)
    _set(component, "TRANSP", event.transparency if event.transparency != "OPAQUE" else "", old.transparency if old and old.transparency != "OPAQUE" else None)
    _set(component, "CLASS", event.classification, old.classification if old else None)
    _set(component, "URL", event.url, old.url if old else None)
    _set(component, COLOUR_ID_PROPERTY, event.colour_id, old.colour_id if old else None)
    _set(component, "COLOR", event.css_colour, old.css_colour if old else None)
    _set_categories(component, event.categories, old.categories if old else ())
    _set_people(component, event, old)
    _set_reminders(component, event.reminders, old.reminders if old else (), event.summary)
    _set(component, "SEQUENCE", event.sequence or "", old.sequence or None if old else None)
    if old is None:
        component.add("DTSTAMP", now)
        component.add("CREATED", event.created or now)
    elif event != old:
        component.pop("DTSTAMP", None)
        component.add("DTSTAMP", now)
        component.pop("LAST-MODIFIED", None)
        component.add("LAST-MODIFIED", now)


def _new_calendar() -> icalendar.Calendar:
    calendar = icalendar.Calendar()
    calendar.add("PRODID", PRODUCT_ID)
    calendar.add("VERSION", "2.0")
    return calendar


def _add_zones(calendar: icalendar.Calendar, values: Iterable[Moment | None]) -> None:
    present = {_text(zone, "TZID") for zone in calendar.walk("VTIMEZONE")}
    for zone in _timezone_components(values, present):
        calendar.subcomponents.insert(0, zone)


def events_to_text(series: EventSeries, original: str | None = None) -> str:
    """The calendar text for an event and its changed occurrences. With the original text, only what differs from it is written: the properties
    and the alarms that were not edited, and anything this program does not know, stay exactly as they were."""
    calendar = _parse(original) if original else None
    if calendar is None:
        calendar = _new_calendar()
    components = [part for part in calendar.walk("VEVENT")]
    old_master_component = next((part for part in components if part.get("RECURRENCE-ID") is None), None)
    old_master = _event_from(old_master_component) if old_master_component is not None else None
    if not series.own_master and series.overrides:
        # only changed occurrences and no event of its own: a change of "the event" is a change of that occurrence; a made-up main event beside
        # it showed it twice everywhere (RV-146)
        first = series.overrides[0]
        if series.master != replace(first, recurrence_id=None):
            series = replace(series, overrides=(replace(series.master, recurrence_id=first.recurrence_id, rrule=first.rrule), *series.overrides[1:]))
    else:
        if old_master_component is None:
            old_master_component = icalendar.Event()
            calendar.add_component(old_master_component)
        _write_event(old_master_component, series.master, old_master)
    old_overrides = {event.recurrence_id: (event, part) for part in components if part.get("RECURRENCE-ID") is not None for event in [_event_from(part)] if event is not None}
    wanted = {event.recurrence_id for event in series.overrides}
    for part in components:  # occurrences that are no longer changed
        if part.get("RECURRENCE-ID") is not None and _moment(part, "RECURRENCE-ID") not in wanted:
            calendar.subcomponents.remove(part)
    for event in series.overrides:
        known = old_overrides.get(event.recurrence_id)
        if known is None:
            part = icalendar.Event()
            calendar.add_component(part)
            _write_event(part, replace(event, uid=series.master.uid), None)
        else:
            _write_event(known[1], replace(event, uid=series.master.uid), known[0])
    _add_zones(calendar, [series.master.start, series.master.end, *series.master.exdates, *series.master.rdates, *[m for e in series.overrides for m in (e.start, e.end, e.recurrence_id)]])
    return calendar.to_ical().decode("utf-8")


def _key_of(component: icalendar.Component) -> str:
    """Which component of a series this is: the main one ("") or a changed occurrence, by the moment of its RECURRENCE-ID (the same instant
    in any written form: UTC or a named zone)."""
    if component.get("RECURRENCE-ID") is None:
        return ""
    moment = _moment(component, "RECURRENCE-ID")
    if isinstance(moment, datetime):
        return moment.astimezone(UTC).isoformat() if moment.tzinfo is not None else moment.isoformat()
    return moment.isoformat() if moment is not None else "unreadable"


def _zones_used(calendar: icalendar.Calendar) -> set[str]:
    used: set[str] = set()
    for component in calendar.walk():
        for value in component.values():
            for each in value if isinstance(value, list) else [value]:
                params = getattr(each, "params", None)
                zone = params.get("TZID") if params is not None else None
                if isinstance(zone, str) and zone:
                    used.add(zone)
    return used


@dataclass(frozen=True, slots=True)
class GivenParts:
    """What an imported text may hand on to the items built from it, read once for the whole text: per UID its components that can be read
    (by occurrence), and its time zones by TZID."""

    components: dict[str, dict[str, Any]] = field(default_factory=dict)  # icalendar components (untyped)
    zones: dict[str, Any] = field(default_factory=dict)


def given_parts(text: str, kind: str) -> GivenParts:
    """The parts of an imported text that kept_from may take over. kind: VEVENT or VTODO. Never raises."""
    parts = GivenParts()
    try:
        source = _parse(text)
        if source is None:
            return parts
        reader = _event_from if kind == "VEVENT" else _task_from
        for component in list(source.walk(kind))[:MAXIMUM_COMPONENTS]:
            try:
                reader(component)
            except Exception:  # noqa: BLE001 - a component that cannot be read gives nothing
                continue
            parts.components.setdefault(str(component.get("UID", "")).strip(), {}).setdefault(_key_of(component), component)
        for zone in source.walk("VTIMEZONE"):
            parts.zones.setdefault(str(zone.get("TZID", "")), zone)
    except Exception:  # noqa: BLE001 - nothing to hand on
        return GivenParts()
    return parts


def kept_from(fresh: str, given: GivenParts, uid: str, kind: str) -> str:
    """The text built afresh (fresh) for an item of an imported text: with the given text's VTIMEZONE components that the item uses (a zone of
    its own, which the program cannot write itself), and, from the given component of the same UID and occurrence that can be read, its X-
    properties (CREATED the fresh text carries already). Nothing else is taken over (no METHOD, no other components, no second main component). kind: VEVENT or
    VTODO. Never raises: when anything goes wrong, the fresh text."""
    try:
        sources = given.components.get(uid, {})
        if not sources and not given.zones:
            return fresh
        built = _parse(fresh)
        if built is None:
            return fresh
        for component in built.walk(kind):
            found = sources.get(_key_of(component))
            if found is None:
                continue
            for name, value in found.items():
                if name.upper().startswith("X-") and name not in component:
                    component[name] = value
        used = _zones_used(built)
        zones = [zone for name, zone in given.zones.items() if name in used]
        if zones:
            names = {str(part.get("TZID", "")) for part in zones}
            built.subcomponents = [*zones, *(part for part in built.subcomponents if not (part.name == "VTIMEZONE" and str(part.get("TZID", "")) in names))]
        return built.to_ical().decode("utf-8")
    except Exception:  # noqa: BLE001 - a text that cannot be combined is stored as built afresh
        return fresh


def tasks_to_text(task: Task, original: str | None = None) -> str:
    """The calendar text for a task; as for events, only what differs from the original is written."""
    calendar = _parse(original) if original else None
    if calendar is None:
        calendar = _new_calendar()
    component = next(iter(calendar.walk("VTODO")), None)
    old = _task_from(component) if component is not None else None
    if component is None:
        component = icalendar.Todo()
        calendar.add_component(component)
    now = datetime.now(UTC)
    _set(component, "UID", task.uid, old.uid if old else None)
    _set(component, "SUMMARY", task.summary, old.summary if old else None)
    _set(component, "DESCRIPTION", task.description, old.description if old else None)
    _set(component, "LOCATION", task.location, old.location if old else None)
    _set(component, "DTSTART", task.start, old.start if old else None)
    _set(component, "DUE", task.due, old.due if old else None)
    _set(component, "COMPLETED", task.completed.astimezone(UTC) if task.completed else None, old.completed if old else None)
    _set(component, "PERCENT-COMPLETE", task.percent if task.percent else "", (old.percent or None) if old else None)
    _set(component, "STATUS", task.status if task.status != "NEEDS-ACTION" or old is not None else "", old.status if old else None)
    _set(component, "PRIORITY", task.priority if task.priority else "", (old.priority or None) if old else None)
    _set_rrule(component, task.rrule, old.rrule if old else "")
    _set_categories(component, task.categories, old.categories if old else ())
    _set_reminders(component, task.reminders, old.reminders if old else (), task.summary)
    _set(component, "RELATED-TO", task.parent, old.parent if old else None)
    _set(component, "URL", task.url, old.url if old else None)
    _set(component, "SEQUENCE", task.sequence or "", (old.sequence or None) if old else None)
    if old is None:
        component.add("DTSTAMP", now)
        component.add("CREATED", task.created or now)
    elif task != old:
        component.pop("DTSTAMP", None)
        component.add("DTSTAMP", now)
        component.pop("LAST-MODIFIED", None)
        component.add("LAST-MODIFIED", now)
    _add_zones(calendar, [task.start, task.due])
    return calendar.to_ical().decode("utf-8")


def complete_task(task: Task, when: datetime | None = None) -> Task:
    """The task marked completed now (or at this time); a repeating task is the caller's matter."""
    return replace(task, status="COMPLETED", completed=when or datetime.now(UTC), percent=100)


def advance_task(task: Task) -> Task | None:
    """The next time of a repeating task: its dates moved to the next time its rule gives, open again. None when the task does not repeat, or the rule has no next time."""
    from dateutil.rrule import rrulestr

    anchor = task.due or task.start
    if not task.rrule or anchor is None:
        return None
    base = anchor if isinstance(anchor, datetime) else datetime(anchor.year, anchor.month, anchor.day)
    try:
        following = rrulestr(task.rrule, dtstart=base).after(base)
    except (ValueError, TypeError, OverflowError):
        return None
    if not isinstance(following, datetime):
        return None
    delta = following - base

    def moved(moment: Moment | None) -> Moment | None:
        if moment is None:
            return None
        return moment + delta

    return replace(task, start=moved(task.start), due=moved(task.due), status="NEEDS-ACTION", completed=None, percent=0)


def reopen_task(task: Task) -> Task:
    return replace(task, status="NEEDS-ACTION", completed=None, percent=0)


# --- repeating items over a range ----------------------------------------------------------------------------------------------------------


def _as_aware(value: Moment, zone: tzinfo) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=zone)
    return datetime(value.year, value.month, value.day, tzinfo=zone)


def occurrences(texts: Iterable[str], start: datetime, end: datetime, zone: tzinfo | None = None, limit: int = MAXIMUM_OCCURRENCES) -> list[Occurrence]:
    """The events of these calendar texts that happen between two moments, repeating ones expanded, exceptions and moved occurrences honoured,
    sorted by start; at most `limit`. Floating and all-day times are taken in `zone` (the local zone when not given). Occurrences are made one by
    one in the order of their start, and making stops at the end of the range or at the limit, so a rule like every second costs no more than
    the occurrences it gives."""
    limit = min(limit, MAXIMUM_OCCURRENCES)
    if end <= start or limit <= 0:
        return []
    local = zone or datetime.now().astimezone().tzinfo or UTC
    found: list[Occurrence] = []
    for text in texts:
        calendar = _parse(text)
        if calendar is None:
            continue
        try:
            changed_ids = {(series.master.uid, change.recurrence_id) for series in parse_events(text) for change in series.overrides}
            for component in recurring_ical_events.of(calendar, components=["VEVENT"]).after(start):  # in the order of their start
                try:
                    event = _event_from(component)
                except Exception:  # noqa: BLE001 - a damaged occurrence is left out
                    continue
                if event is None:
                    continue
                if _as_aware(event.start, local) >= end:
                    break  # this one and every later one start after the range
                found.append(Occurrence(event, event.start, event.end, (event.uid, event.recurrence_id) in changed_ids))
                if len(found) >= limit:
                    break
        except Exception:  # noqa: BLE001 - one damaged item must not empty the whole view (what it gave before the fault is kept)
            pass
        if len(found) >= limit:
            break
    found.sort(key=lambda item: (_as_aware(item.start, local), _as_aware(item.end, local)))
    return found[:limit]


def due_tasks_on(tasks: Iterable[Task], day: date) -> list[Task]:
    """Tasks that are due on this day (dates and date-times alike), for the agenda."""
    return [task for task in tasks if task.due is not None and (task.due.date() if isinstance(task.due, datetime) else task.due) == day]
