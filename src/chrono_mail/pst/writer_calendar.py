"""Events and tasks as items of a data file: times, the zone, the repeat pattern with its deleted and changed occurrences, the
reminder, the attendees and the categories. The mirror of calendar.py."""

from datetime import UTC, date, datetime, time, timedelta, tzinfo

from chrono_mail.pim.ical import Event, EventSeries, Moment, Task
from chrono_mail.pim.times import local_zone
from chrono_mail.pst.names import PSETID_APPOINTMENT, PSETID_COMMON, PSETID_TASK
from chrono_mail.pst.writer_ltp import BINARY, BOOLEAN, FLOATING64, INTEGER32, TIME, UNICODE, Prop, binary, boolean, floating, integer, text
from chrono_mail.pst.writer_ltp import time as time_prop
from chrono_mail.pst.writer_mail import Item, categories, recipient
from chrono_mail.pst.writer_names import NameRegistry
from chrono_mail.pst.writer_recurrence import Change, appointment_blob, pattern_blob, pattern_of
from chrono_mail.pst.writer_zones import definition, rules_of, structure

# --- Version ------------------------------------------------------------
__version__ = "writer_calendar.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: events (with their zone, repeat pattern and changed occurrences) and tasks as items of a data file

_ORGANIZER = 0x0003  # sendable and the organizer


class _Named:
    """Named properties of one property set, made as they are used."""

    def __init__(self, names: NameRegistry, guid: bytes) -> None:
        self.names = names
        self.guid = guid

    def id(self, lid: int) -> int:
        return self.names.id_of(self.guid, lid)


def _zone(moment: Moment) -> tzinfo:
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            return local_zone()
        if getattr(moment.tzinfo, "key", None):
            return moment.tzinfo
        return UTC
    return local_zone()


def _instant(moment: Moment, zone: tzinfo) -> datetime:
    """The moment as UTC: a day starts at midnight in the zone, a time without a zone is in the zone."""
    if isinstance(moment, datetime):
        aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)
        return aware.astimezone(UTC)
    return datetime.combine(moment, time(0), tzinfo=zone).astimezone(UTC)


def _wall(moment: Moment, zone: tzinfo) -> datetime:
    """The moment as wall-clock time in the zone (no zone attached)."""
    return _instant(moment, zone).astimezone(zone).replace(tzinfo=None)


def _reminder(event_reminders: tuple[object, ...]) -> int | None:
    for reminder in event_reminders:
        before = getattr(reminder, "minutes_before", None)
        if isinstance(before, int) and before >= 0:
            return before
    return None


def event_item(series: EventSeries, names: NameRegistry) -> tuple[Item, str]:
    """The item of an event series, and a note (empty, or what of it the data file cannot hold)."""
    master: Event = series.master
    appointment = _Named(names, PSETID_APPOINTMENT)
    common = _Named(names, PSETID_COMMON)
    zone = _zone(master.start)
    all_day = not isinstance(master.start, datetime)
    start = _instant(master.start, zone)
    end = _instant(master.end, zone)
    if end < start:
        end = start
    length = int((end - start).total_seconds() // 60)
    rules = rules_of(zone, _wall(master.start, zone).year)
    zone_definition = definition(rules, _wall(master.start, zone).year)
    note = ""
    props: list[Prop] = [
        text(0x001A, "IPM.Appointment"), text(0x0037, master.summary), text(0x0070, master.summary), integer(0x0E07, 1), integer(0x0017, 1),
        integer(0x0036, 2 if master.classification.upper() in ("PRIVATE", "CONFIDENTIAL") else 0),
        time_prop(appointment.id(0x820D), start),
        time_prop(appointment.id(0x820E), end), time_prop(common.id(0x8516), start),
        time_prop(common.id(0x8517), end), integer(appointment.id(0x8213), length),
        boolean(appointment.id(0x8215), all_day),
        integer(appointment.id(0x8205), 0 if master.transparency.upper() == "TRANSPARENT" else 2),
        text(appointment.id(0x8208), master.location),
        text(appointment.id(0x8234), rules.name),
        binary(appointment.id(0x8233), structure(rules)), binary(appointment.id(0x825E), zone_definition),
        binary(appointment.id(0x825F), zone_definition), time_prop(appointment.id(0x8235), start),
    ]
    if master.description:
        props.append(text(0x1000, master.description))
    if master.created is not None:
        props.append(time_prop(0x3007, master.created))
    if master.modified is not None:
        props.append(time_prop(0x3008, master.modified))
    minutes_before = _reminder(master.reminders)
    props.append(boolean(common.id(0x8503), minutes_before is not None))
    if minutes_before is not None:
        props += [integer(common.id(0x8501), minutes_before), time_prop(common.id(0x8502), start),
                  time_prop(common.id(0x8560), start - timedelta(minutes=minutes_before))]
    recurring = False
    clip_end = end
    if master.rrule:
        wall_start = _wall(master.start, zone)
        pattern = pattern_of(master.rrule, wall_start)
        if pattern is None:
            note = "an event whose repeat pattern a data file cannot hold: only its first occurrence was written"
        else:
            changes = [
                Change(_wall(override.start, zone), _wall(override.end, zone), _wall(override.recurrence_id, zone),
                       override.summary if override.summary != master.summary else None,
                       override.location if override.location != master.location else None,
                       (0 if override.transparency.upper() == "TRANSPARENT" else 2) if override.transparency != master.transparency else None)
                for override in series.overrides if override.recurrence_id is not None
            ]
            deleted = [_wall(day, zone).date() for day in master.exdates]
            offset = 0 if all_day else wall_start.hour * 60 + wall_start.minute
            blob = appointment_blob(pattern, offset, length, deleted, changes)
            recurring = True
            props += [
                binary(appointment.id(0x8216), blob), binary(appointment.id(0x8260), zone_definition),
                integer(appointment.id(0x8231), pattern.kind),
            ]
            last = pattern.end or date(4500, 8, 31)
            clip_end = datetime.combine(last, time(0), tzinfo=zone).astimezone(UTC)
    props += [boolean(appointment.id(0x8223), recurring), time_prop(appointment.id(0x8236), clip_end)]
    rows: list[list[Prop]] = []
    if master.attendees:
        if master.organizer is not None and master.organizer.address:
            rows.append(recipient(master.organizer.name, master.organizer.address, 1, [integer(0x5FFD, _ORGANIZER)]))
            props += [text(0x0C1A, master.organizer.name or master.organizer.address), text(0x0C1E, "SMTP"), text(0x0C1F, master.organizer.address),
                      text(0x5D01, master.organizer.address)]
        for attendee in master.attendees:
            rows.append(recipient(attendee.name, attendee.address, 2 if attendee.role.upper() == "OPT-PARTICIPANT" else 1))
        props += [integer(appointment.id(0x8217), 1), integer(appointment.id(0x8218), 1)]
    props.append(integer(0x1080, (0x402 if master.attendees else 0x401 if recurring else 0x400)))
    props += categories(names, master.categories)
    return Item(props, rows, []), note


def _date_of(moment: Moment | None) -> datetime | None:
    """A task's date as the data file keeps it: the day at midnight UTC."""
    if moment is None:
        return None
    day = moment.astimezone(local_zone()).date() if isinstance(moment, datetime) and moment.tzinfo is not None else (
        moment.date() if isinstance(moment, datetime) else moment)
    return datetime.combine(day, time(0), tzinfo=UTC)


def task_item(task: Task, names: NameRegistry) -> Item:
    """The item of a task."""
    named = _Named(names, PSETID_TASK)
    common = _Named(names, PSETID_COMMON)
    done = task.status.upper() == "COMPLETED"
    status = 2 if done else 1 if task.status.upper() == "IN-PROCESS" else 0
    importance = 2 if 1 <= task.priority <= 4 else 0 if task.priority >= 6 else 1
    props: list[Prop] = [
        text(0x001A, "IPM.Task"), text(0x0037, task.summary), text(0x0070, task.summary), integer(0x0E07, 1), integer(0x0017, importance),
        integer(named.id(0x8101), status),
        floating(named.id(0x8102), 1.0 if done else max(0, min(100, task.percent)) / 100),
        boolean(named.id(0x811C), done), integer(named.id(0x8113), 1),
        integer(0x1080, 0x500),
    ]
    if task.description:
        props.append(text(0x1000, task.description))
    start = _date_of(task.start)
    due = _date_of(task.due)
    if start is not None:
        props += [time_prop(named.id(0x8104), start), time_prop(common.id(0x8516), start)]
    if due is not None:
        props += [time_prop(named.id(0x8105), due), time_prop(common.id(0x8517), due)]
    if done:
        props.append(time_prop(named.id(0x810F), task.completed or due or datetime.now(UTC)))
    reminder = next((reminder for reminder in task.reminders if reminder.at is not None), None)
    props.append(boolean(common.id(0x8503), reminder is not None))
    if reminder is not None and reminder.at is not None:
        props.append(time_prop(common.id(0x8502), reminder.at))
    anchor = start or due
    if task.rrule and anchor is not None:
        pattern = pattern_of(task.rrule, anchor.replace(tzinfo=None))
        if pattern is not None:
            props += [binary(named.id(0x8116), pattern_blob(pattern, [], [])), boolean(named.id(0x8126), True)]
    if task.created is not None:
        props.append(time_prop(0x3007, task.created))
    props += categories(names, task.categories)
    return Item(props, [], [])
