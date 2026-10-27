from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, tzinfo

from chrono_mail.pst.properties import minutes_moment

# --- Version ------------------------------------------------------------
__version__ = "recurrence.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the repeat pattern of an event or task (MS-OXOCAL 2.2.1.44) and its RRULE

MAXIMUM_DATES: int = 5_000  # deleted or changed occurrences of one series
MAXIMUM_LENGTH_MINUTES: int = 5 * 366 * 1440  # one occurrence (an occurrence may last days; years means a damaged pattern)

DAILY = 0x200A
WEEKLY = 0x200B
MONTHLY = 0x200C
YEARLY = 0x200D
PATTERN_DAY = 0x0
PATTERN_WEEK = 0x1
PATTERN_MONTH = 0x2
PATTERN_MONTH_END = 0x3
PATTERN_MONTH_NTH = 0x4
END_BY_DATE = 0x2021
END_AFTER = 0x2022
_DAY_NAMES = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
# default, Gregorian, US English, Japanese era, Taiwan, Korean, Thai, Middle East French, Arabic, transliterated English and French: all
# count months and days as the Gregorian calendar does
_GREGORIAN = frozenset({0, 1, 2, 3, 4, 5, 7, 9, 10, 11, 12})

# what an exception overrides (ExceptionInfo.OverrideFlags)
ARO_SUBJECT = 0x0001
ARO_MEETINGTYPE = 0x0002
ARO_REMINDERDELTA = 0x0004
ARO_REMINDER = 0x0008
ARO_LOCATION = 0x0010
ARO_BUSYSTATUS = 0x0020
ARO_ATTACHMENT = 0x0040
ARO_SUBTYPE = 0x0080
ARO_APPTCOLOR = 0x0100


class RecurrenceError(ValueError):
    """A repeat pattern that is damaged or of a kind that cannot be written as an RRULE."""


@dataclass(frozen=True, slots=True)
class ChangedOccurrence:
    """One changed occurrence of a series: times are wall-clock minutes since 1601 in the event's own zone."""

    start: int
    end: int
    original_start: int
    subject: str | None = None
    location: str | None = None
    busy_status: int | None = None
    reminder_minutes: int | None = None
    reminder_set: bool | None = None


@dataclass(frozen=True, slots=True)
class Recurrence:
    frequency: int
    pattern_type: int
    calendar_type: int
    period: int
    day_mask: int  # weekly and nth-weekday patterns: bit 0 Sunday ... bit 6 Saturday
    day_of_month: int  # monthly pattern; for the nth-weekday pattern, which one (1-4, 5 = last)
    end_type: int
    occurrences: int
    first_day_of_week: int
    deleted: tuple[int, ...]  # minutes since 1601 of the day (midnight) of each deleted or changed occurrence
    modified: tuple[int, ...]
    start_date: int
    end_date: int
    start_offset: int | None = None  # an event's start and end in minutes after midnight
    end_offset: int | None = None
    changes: tuple[ChangedOccurrence, ...] = ()


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.data):
            raise RecurrenceError("cut off")
        piece = self.data[self.position : self.position + size]
        self.position += size
        return piece

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def dates(self) -> tuple[int, ...]:
        count = self.u32()
        if count > MAXIMUM_DATES:
            raise RecurrenceError("too many dates")
        return tuple(self.u32() for _ in range(count))

    def skip_block(self) -> None:
        self.take(self.u32())


def parse(data: bytes, appointment: bool) -> Recurrence:
    """The RecurrencePattern, and for an event the AppointmentRecurrencePattern after it with its changed occurrences."""
    reader = _Reader(data)
    reader.u16()  # reader version
    reader.u16()  # writer version
    frequency = reader.u16()
    pattern_type = reader.u16()
    calendar_type = reader.u16()
    reader.u32()  # FirstDateTime
    period = reader.u32()
    reader.u32()  # SlidingFlag
    day_mask = day_of_month = 0
    if pattern_type == PATTERN_WEEK:
        day_mask = reader.u32()
    elif pattern_type in (PATTERN_MONTH, PATTERN_MONTH_END, 0xA, 0xC):
        day_of_month = reader.u32()
    elif pattern_type in (PATTERN_MONTH_NTH, 0xB):
        day_mask = reader.u32()
        day_of_month = reader.u32()
    elif pattern_type != PATTERN_DAY:
        raise RecurrenceError("unknown pattern")
    end_type = reader.u32()
    occurrences = reader.u32()
    first_day_of_week = reader.u32()
    deleted = reader.dates()
    modified = reader.dates()
    start_date = reader.u32()
    end_date = reader.u32()
    if not appointment:
        return Recurrence(frequency, pattern_type, calendar_type, period, day_mask, day_of_month, end_type, occurrences, first_day_of_week, deleted, modified, start_date, end_date)
    reader.u32()  # ReaderVersion2
    writer_version = reader.u32()
    start_offset = reader.u32()
    end_offset = reader.u32()
    if start_offset >= 1440 or not start_offset <= end_offset <= start_offset + MAXIMUM_LENGTH_MINUTES:
        raise RecurrenceError("a start outside the day or a length out of range")
    count = reader.u16()
    if count > MAXIMUM_DATES:
        raise RecurrenceError("too many changes")
    changes = [_exception(reader) for _ in range(count)]
    try:
        reader.skip_block()  # ReservedBlock1
        changes = [_extended(reader, change, flags, writer_version) for change, flags in changes]
    except RecurrenceError:
        pass  # the wide-character texts are an extra; the 8-bit ones stand
    return Recurrence(
        frequency, pattern_type, calendar_type, period, day_mask, day_of_month, end_type, occurrences, first_day_of_week, deleted, modified,
        start_date, end_date, start_offset, end_offset, tuple(change for change, _ in changes),
    )


def _short_text(reader: _Reader) -> str:
    reader.u16()  # length with the terminator
    size = reader.u16()
    return reader.take(size).decode("cp1252", errors="replace")


def _exception(reader: _Reader) -> tuple[ChangedOccurrence, int]:
    start = reader.u32()
    end = reader.u32()
    original = reader.u32()
    flags = reader.u16()
    subject = location = None
    busy = reminder_minutes = None
    reminder_set: bool | None = None
    if flags & ARO_SUBJECT:
        subject = _short_text(reader)
    if flags & ARO_MEETINGTYPE:
        reader.u32()
    if flags & ARO_REMINDERDELTA:
        reminder_minutes = reader.u32()
    if flags & ARO_REMINDER:
        reminder_set = bool(reader.u32())
    if flags & ARO_LOCATION:
        location = _short_text(reader)
    if flags & ARO_BUSYSTATUS:
        busy = reader.u32()
    if flags & ARO_ATTACHMENT:
        reader.u32()
    if flags & ARO_SUBTYPE:
        reader.u32()
    if flags & ARO_APPTCOLOR:
        reader.u32()
    return ChangedOccurrence(start, end, original, subject, location, busy, reminder_minutes, reminder_set), flags


def _extended(reader: _Reader, change: ChangedOccurrence, flags: int, writer_version: int) -> tuple[ChangedOccurrence, int]:
    """The ExtendedException of a changed occurrence: its subject and location in full Unicode."""
    if writer_version >= 0x3009:
        reader.skip_block()  # ChangeHighlight
    reader.skip_block()  # ReservedBlockEE1
    subject, location = change.subject, change.location
    if flags & (ARO_SUBJECT | ARO_LOCATION):
        reader.take(12)  # the start, end and original start again
        if flags & ARO_SUBJECT:
            subject = reader.take(2 * reader.u16()).decode("utf-16-le", errors="replace")
        if flags & ARO_LOCATION:
            location = reader.take(2 * reader.u16()).decode("utf-16-le", errors="replace")
        reader.skip_block()  # ReservedBlockEE2
    return replace(change, subject=subject, location=location), flags


def _days(mask: int) -> str:
    return ",".join(name for bit, name in enumerate(_DAY_NAMES) if mask & (1 << bit))


def local_moment(minutes: int, zone: tzinfo | None) -> datetime:
    """Wall-clock minutes since 1601 in the event's zone as an aware moment (UTC when the zone is not known)."""
    moment = minutes_moment(minutes)
    if moment is None:
        raise RecurrenceError("a date out of range")
    return moment.replace(tzinfo=zone or UTC)


def rrule(recurrence: Recurrence, start: date | datetime, zone: tzinfo | None) -> str:
    """The RRULE text of a pattern for a series that starts at start (a date for all-day items)."""
    if recurrence.calendar_type not in _GREGORIAN:
        raise RecurrenceError("a calendar other than the Gregorian one")
    parts: list[str] = []
    pattern = recurrence.pattern_type
    period = recurrence.period
    if pattern == PATTERN_DAY:
        parts += ["FREQ=DAILY", f"INTERVAL={max(1, period // 1440)}"]
    elif pattern == PATTERN_WEEK:
        if not recurrence.day_mask & 0x7F:
            raise RecurrenceError("no days")
        if recurrence.frequency == DAILY:
            parts += ["FREQ=DAILY", f"BYDAY={_days(recurrence.day_mask)}"]
        else:
            parts += ["FREQ=WEEKLY", f"INTERVAL={max(1, period)}", f"BYDAY={_days(recurrence.day_mask)}"]
    elif pattern in (PATTERN_MONTH, PATTERN_MONTH_END, PATTERN_MONTH_NTH):
        if recurrence.frequency == YEARLY:
            parts += ["FREQ=YEARLY", f"INTERVAL={max(1, period // 12)}", f"BYMONTH={start.month}"]
        else:
            parts += ["FREQ=MONTHLY", f"INTERVAL={max(1, period)}"]
        if pattern == PATTERN_MONTH:
            day = recurrence.day_of_month if 1 <= recurrence.day_of_month <= 31 else start.day
            if day >= 29:  # a month without that day has the occurrence on its last day
                parts += ["BYMONTHDAY=" + ",".join(str(value) for value in range(28, day + 1)), "BYSETPOS=-1"]
            else:
                parts.append(f"BYMONTHDAY={day}")
        elif pattern == PATTERN_MONTH_END:
            parts.append("BYMONTHDAY=-1")
        else:
            if not recurrence.day_mask & 0x7F:
                raise RecurrenceError("no days")
            which = recurrence.day_of_month
            parts += [f"BYDAY={_days(recurrence.day_mask)}", f"BYSETPOS={-1 if which >= 5 or which < 1 else which}"]
    else:
        raise RecurrenceError("a pattern of another calendar")
    if recurrence.end_type == END_AFTER and 0 < recurrence.occurrences < 1_000_000:
        parts.append(f"COUNT={recurrence.occurrences}")
    elif recurrence.end_type == END_BY_DATE:
        last = minutes_moment(recurrence.end_date)
        if last is not None:
            if isinstance(start, datetime):
                if recurrence.start_offset is not None:
                    until = local_moment(recurrence.end_date + recurrence.start_offset, zone)
                else:
                    until = datetime.combine(last.date(), start.time()).replace(tzinfo=start.tzinfo or zone or UTC)
                parts.append("UNTIL=" + until.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"))
            else:
                parts.append("UNTIL=" + last.strftime("%Y%m%d"))
    if 0 <= recurrence.first_day_of_week <= 6 and recurrence.first_day_of_week != 1:
        parts.append(f"WKST={_DAY_NAMES[recurrence.first_day_of_week]}")
    return ";".join(parts)


def deleted_days(recurrence: Recurrence) -> list[date]:
    """The days of occurrences that were deleted: the deleted list holds the changed ones too, which stay as changed occurrences."""
    changed = {moment.date() for change in recurrence.changes for moment in [minutes_moment(change.original_start)] if moment is not None}
    days: list[date] = []
    for minutes in recurrence.deleted:
        moment = minutes_moment(minutes)
        if moment is not None and moment.date() not in changed:
            days.append(moment.date())
    return days


def offset_after_midnight(recurrence: Recurrence) -> timedelta:
    return timedelta(minutes=recurrence.start_offset or 0)
