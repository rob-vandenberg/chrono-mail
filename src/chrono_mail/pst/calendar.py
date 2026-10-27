from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import icalendar

from chrono_mail.pim.ical import Attendee, Event, EventSeries, Moment, Reminder, Task, events_to_text, tasks_to_text
from chrono_mail.pst import recurrence
from chrono_mail.pst.headers import (
    ADDRESS_TYPE, DISPLAY_NAME, EMAIL_ADDRESS, SENDER_ADDRESS, SENDER_ADDRESS_TYPE, SENDER_NAME, SENDER_SMTP, SMTP_ADDRESS, SUBJECT, categories,
    recipient_kind, smtp_address,
)
from chrono_mail.pst.mail import bodies
from chrono_mail.pst.names import PSETID_APPOINTMENT, PSETID_COMMON, PSETID_TASK
from chrono_mail.pst.properties import Message, Properties, minutes_moment, nearest_date
from chrono_mail.pst.recurrence import Recurrence, RecurrenceError
from chrono_mail.pst.zones import from_definition, from_structure, vtimezone_of

# --- Version ------------------------------------------------------------
__version__ = "calendar.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: events (with their repeat pattern and changed occurrences) and tasks as iCalendar

SENSITIVITY = 0x0036
IMPORTANCE = 0x0017
CREATION_TIME = 0x3007
RECIPIENT_FLAGS = 0x5FFD
_ORGANIZER_ROW = 0x0002

# PSETID_Appointment
LOCATION = 0x8208
START_WHOLE = 0x820D
END_WHOLE = 0x820E
ALL_DAY = 0x8215
RECURRENCE = 0x8216
BUSY_STATUS = 0x8205
TIME_ZONE_STRUCT = 0x8233
TIME_ZONE_START = 0x825E
# PSETID_Common
REMINDER_MINUTES = 0x8501
REMINDER_TIME = 0x8502
REMINDER_SET = 0x8503
# PSETID_Task
TASK_STATUS = 0x8101
TASK_PERCENT = 0x8102
TASK_START = 0x8104
TASK_DUE = 0x8105
TASK_COMPLETED_DATE = 0x810F
TASK_RECURRENCE = 0x8116
TASK_COMPLETE = 0x811C
TASK_RECURRING = 0x8126

_TASK_STATUS = {0: "NEEDS-ACTION", 1: "IN-PROCESS", 2: "COMPLETED", 3: "NEEDS-ACTION", 4: "NEEDS-ACTION"}


class CalendarError(ValueError):
    """An event or task that cannot be made; the text never holds its contents."""


def zone_of(props: Properties) -> tzinfo | None:
    """The event's own zone where it can be named; None when it cannot."""
    definition = props.binary(props.named(PSETID_APPOINTMENT, TIME_ZONE_START))
    zone = from_definition(definition) if definition else None
    if zone is None:
        structure = props.binary(props.named(PSETID_APPOINTMENT, TIME_ZONE_STRUCT))
        zone = from_structure(structure) if structure else None
    return zone


def _with_own_zone(text: str, zone: tzinfo | None) -> str:
    """A zone made from the data file's rules gets its VTIMEZONE as yearly rules (in place of a list of changes the library would make)."""
    own = vtimezone_of(zone) if zone is not None else None
    if own is None or zone is None:
        return text
    key = zone.tzname(None)
    calendar = icalendar.Calendar.from_ical(text)
    if not isinstance(calendar, icalendar.Calendar):
        return text
    calendar.subcomponents = [part for part in calendar.subcomponents if not (part.name == "VTIMEZONE" and str(part.get("TZID", "")) == key)]
    made = icalendar.Timezone.from_ical(own)
    if isinstance(made, icalendar.Timezone):
        calendar.subcomponents.insert(0, made)
    return calendar.to_ical().decode("utf-8")


def _attendees(message: Message) -> tuple[Attendee | None, tuple[Attendee, ...]]:
    attendees: list[Attendee] = []
    for row in message.recipients:
        if (row.integer(RECIPIENT_FLAGS) or 0) & _ORGANIZER_ROW:
            continue
        address = smtp_address(row, SMTP_ADDRESS, EMAIL_ADDRESS, ADDRESS_TYPE)
        if not address:
            continue
        kind = recipient_kind(row)
        attendees.append(Attendee(address=address, name=row.text(DISPLAY_NAME), role="OPT-PARTICIPANT" if kind == 2 else "REQ-PARTICIPANT"))
    if not attendees:
        return None, ()
    props = message.props
    address = smtp_address(props, SENDER_SMTP, SENDER_ADDRESS, SENDER_ADDRESS_TYPE)
    organizer = Attendee(address=address, name=props.text(SENDER_NAME)) if address else None
    return organizer, tuple(attendees)


def _description(props: Properties) -> str:
    plain, _ = bodies(props)
    return (plain or "").strip()


def _reminders(props: Properties) -> tuple[Reminder, ...]:
    if not props.boolean(props.named(PSETID_COMMON, REMINDER_SET)):
        return ()
    minutes = props.integer(props.named(PSETID_COMMON, REMINDER_MINUTES))
    return (Reminder(minutes_before=minutes if minutes is not None and 0 <= minutes <= 10_000_000 else 15),)


def _shift_zone(start_utc: datetime, rule: Recurrence | None) -> tzinfo:
    """For an event whose zone cannot be named: a fixed offset from the first occurrence's wall-clock start, so the changed occurrences'
    wall-clock times land right."""
    if rule is not None and rule.start_offset is not None:
        local = minutes_moment(rule.start_date + rule.start_offset)
        if local is not None:
            offset = local - start_utc.replace(tzinfo=None)
            minutes = round(offset.total_seconds() / 60)
            if abs(minutes) <= 14 * 60:
                return timezone(timedelta(minutes=minutes))
    return UTC


def build_event(message: Message, uid: str) -> str:
    """The event as one VCALENDAR: the series with its RRULE and deleted occurrences, and every changed occurrence."""
    props = message.props
    start_utc = props.time(props.named(PSETID_APPOINTMENT, START_WHOLE))
    end_utc = props.time(props.named(PSETID_APPOINTMENT, END_WHOLE))
    if start_utc is None:
        raise CalendarError("an event without a start")
    if end_utc is None or end_utc < start_utc:
        end_utc = start_utc
    zone = zone_of(props)
    rule: Recurrence | None = None
    blob = props.binary(props.named(PSETID_APPOINTMENT, RECURRENCE))
    if blob:
        try:
            rule = recurrence.parse(blob, appointment=True)
        except RecurrenceError:
            rule = None
    all_day = props.boolean(props.named(PSETID_APPOINTMENT, ALL_DAY))
    local_zone = zone or _shift_zone(start_utc, rule)
    start: Moment
    end: Moment
    if all_day:
        start = start_utc.astimezone(zone).date() if zone else nearest_date(start_utc.astimezone(local_zone).replace(tzinfo=None))
        end = end_utc.astimezone(zone).date() if zone else nearest_date(end_utc.astimezone(local_zone).replace(tzinfo=None))
        if end <= start:
            end = start + timedelta(days=1)
    else:
        start = start_utc.astimezone(zone) if zone else start_utc.astimezone(UTC)
        end = end_utc.astimezone(zone) if zone else end_utc.astimezone(UTC)
    organizer, attendees = _attendees(message)
    busy = props.integer(props.named(PSETID_APPOINTMENT, BUSY_STATUS))
    master = Event(
        uid=uid, summary=props.text(SUBJECT), start=start, end=end, description=_description(props),
        location=props.text(props.named(PSETID_APPOINTMENT, LOCATION)), transparency="TRANSPARENT" if busy == 0 else "OPAQUE",
        classification="PRIVATE" if props.integer(SENSITIVITY) == 2 else "", categories=categories(props), organizer=organizer,
        attendees=attendees, reminders=_reminders(props), created=props.time(CREATION_TIME),
    )
    overrides: list[Event] = []
    if rule is not None:
        try:
            master, overrides = _repeat(master, rule, local_zone, all_day, zone is not None)
        except (RecurrenceError, OverflowError, ValueError):
            pass  # a pattern that cannot be written: the first occurrence stands alone
    return _with_own_zone(events_to_text(EventSeries(master=master, overrides=tuple(overrides))), zone)


def _at(minutes: int, zone: tzinfo, all_day: bool, named: bool) -> Moment:
    """Wall-clock minutes of the blob as a value: in the event's named zone, else in UTC."""
    local = recurrence.local_moment(minutes, zone)
    if all_day:
        return local.date()
    return local if named else local.astimezone(UTC)


def _repeat(master: Event, rule: Recurrence, zone: tzinfo, all_day: bool, named: bool) -> tuple[Event, list[Event]]:
    rrule_text = recurrence.rrule(rule, master.start, zone)
    offset = recurrence.offset_after_midnight(rule)
    exdates: list[Moment] = []
    for day in recurrence.deleted_days(rule)[: recurrence.MAXIMUM_DATES]:
        if all_day:
            exdates.append(day)
        else:
            moment = datetime.combine(day, datetime.min.time()).replace(tzinfo=zone) + offset
            exdates.append(moment if named else moment.astimezone(UTC))
    master = replace(master, rrule=rrule_text, exdates=tuple(exdates))
    overrides: list[Event] = []
    for change in rule.changes:
        start = _at(change.start, zone, all_day, named)
        end = _at(change.end, zone, all_day, named)
        if all_day:
            original = recurrence.local_moment(change.original_start, zone).date()
        else:
            original = _at(change.original_start, zone, False, named)
        if not isinstance(start, datetime) and not isinstance(end, datetime) and end <= start:
            end = start + timedelta(days=1)
        reminders = master.reminders
        if change.reminder_set is False:
            reminders = ()
        elif change.reminder_minutes is not None and 0 <= change.reminder_minutes <= 10_000_000:
            reminders = (Reminder(minutes_before=change.reminder_minutes),)
        overrides.append(replace(
            master, rrule="", exdates=(), recurrence_id=original, start=start, end=end,
            summary=change.subject if change.subject is not None else master.summary,
            location=change.location if change.location is not None else master.location,
            transparency=("TRANSPARENT" if change.busy_status == 0 else "OPAQUE") if change.busy_status is not None else master.transparency,
            reminders=reminders,
        ))
    return master, overrides


def build_task(message: Message, uid: str) -> str:
    """The task as one VCALENDAR with a VTODO."""
    props = message.props
    start_moment = props.time(props.named(PSETID_TASK, TASK_START))
    due_moment = props.time(props.named(PSETID_TASK, TASK_DUE))
    start = nearest_date(start_moment) if start_moment is not None else None
    due = nearest_date(due_moment) if due_moment is not None else None
    if start is not None and due is not None and due < start:
        start = due
    status = _TASK_STATUS.get(props.integer(props.named(PSETID_TASK, TASK_STATUS)) or 0, "NEEDS-ACTION")
    completed = props.time(props.named(PSETID_TASK, TASK_COMPLETED_DATE))
    if props.boolean(props.named(PSETID_TASK, TASK_COMPLETE)):
        status = "COMPLETED"
    percent_value = props.floating(props.named(PSETID_TASK, TASK_PERCENT))
    percent = round(percent_value * 100) if percent_value is not None and 0.0 <= percent_value <= 1.0 else 0
    if status == "COMPLETED":
        percent = 100
        completed = completed or due_moment
    else:
        completed = None
    importance = props.integer(IMPORTANCE)
    reminders: tuple[Reminder, ...] = ()
    if props.boolean(props.named(PSETID_COMMON, REMINDER_SET)):
        moment = props.time(props.named(PSETID_COMMON, REMINDER_TIME))
        if moment is not None:
            reminders = (Reminder(minutes_before=None, at=moment),)
    rrule_text = ""
    blob = props.binary(props.named(PSETID_TASK, TASK_RECURRENCE))
    if blob and props.boolean(props.named(PSETID_TASK, TASK_RECURRING)):
        anchor = start or due
        if anchor is not None:
            try:
                rrule_text = recurrence.rrule(recurrence.parse(blob, appointment=False), anchor, None)
                start = anchor
            except (RecurrenceError, OverflowError, ValueError):
                rrule_text = ""
    task = Task(
        uid=uid, summary=props.text(SUBJECT), description=_description(props), start=start, due=due, completed=completed, percent=percent,
        status=status, priority={0: 9, 2: 1}.get(importance if importance is not None else 1, 0), rrule=rrule_text, categories=categories(props),
        reminders=reminders, created=props.time(CREATION_TIME),
    )
    return tasks_to_text(task)
