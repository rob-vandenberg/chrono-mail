"""A repeat pattern (RRULE) as the data file keeps it (MS-OXOCAL 2.2.1.44): the RecurrencePattern, and for an event the
AppointmentRecurrencePattern with its deleted and changed occurrences. The mirror of recurrence.py; a rule the format cannot hold gives
None (the event then stands alone)."""

import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil.rrule import rrulestr

from chrono_mail.pst.recurrence import (
    ARO_BUSYSTATUS, ARO_LOCATION, ARO_SUBJECT, DAILY, END_AFTER, END_BY_DATE, MONTHLY, PATTERN_DAY, PATTERN_MONTH, PATTERN_MONTH_NTH,
    PATTERN_WEEK, WEEKLY, YEARLY,
)

# --- Version ------------------------------------------------------------
__version__ = "writer_recurrence.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: an RRULE with its deleted and changed occurrences as the recurrence blob of an event or task

END_NEVER = 0x2023
NO_END_DATE = 0x5AE980DF
MAXIMUM_OCCURRENCES: int = 100_000
_DAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
_UNSUPPORTED = ("BYHOUR", "BYMINUTE", "BYSECOND", "BYWEEKNO", "BYYEARDAY")
_START = date(1601, 1, 1)


def minutes(day: date) -> int:
    return (day - _START).days * 1440


def wall_minutes(moment: datetime) -> int:
    return minutes(moment.date()) + moment.hour * 60 + moment.minute


@dataclass(frozen=True, slots=True)
class Change:
    """A changed occurrence: wall-clock times in the event's zone, and what differs from the series."""

    start: datetime
    end: datetime
    original: datetime
    subject: str | None = None
    location: str | None = None
    busy: int | None = None


@dataclass(frozen=True, slots=True)
class Pattern:
    frequency: int
    pattern_type: int
    period: int
    day_mask: int
    day_number: int
    end_type: int
    occurrences: int
    first_day_of_week: int
    start: date
    end: date | None
    kind: int  # PidLidRecurrenceType: 1 daily, 2 weekly, 3 monthly, 4 yearly


def _parts(rule: str) -> dict[str, str] | None:
    parts: dict[str, str] = {}
    for piece in rule.strip().removeprefix("RRULE:").split(";"):
        key, equals, value = piece.partition("=")
        if equals:
            parts[key.strip().upper()] = value.strip().upper()
    if not parts.get("FREQ") or any(key in parts for key in _UNSUPPORTED):
        return None
    return parts


def _mask(days: list[str]) -> int | None:
    mask = 0
    for day in days:
        name = day[-2:]
        if name not in _DAYS:
            return None
        mask |= 1 << _DAYS.index(name)
    return mask


def _nth(by_day: list[str], set_position: str) -> tuple[int, int] | None:
    """(day mask, which one: 1-4, 5 last) of BYDAY=2TU or BYDAY=TU;BYSETPOS=2."""
    if not by_day:
        return None
    numbers = {day[:-2] for day in by_day}
    if len(numbers) != 1:
        return None
    number = numbers.pop() or set_position
    try:
        which = int(number)
    except ValueError:
        return None
    mask = _mask(by_day)
    if mask is None or which == 0 or which < -1 or which > 5:
        return None
    return mask, 5 if which in (-1, 5) else which


def _occurrences(parts: dict[str, str], start: datetime) -> list[datetime]:
    """The occurrences the rule gives (wall clock), to find the last one; at most MAXIMUM_OCCURRENCES."""
    base = ";".join(f"{key}={value}" for key, value in parts.items() if key not in ("COUNT", "UNTIL"))
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    until: datetime | None = None
    if "UNTIL" in parts:
        text = parts["UNTIL"].rstrip("Z")
        until = datetime.strptime(text[:8], "%Y%m%d") + timedelta(days=1, seconds=-1) if len(text) == 8 else datetime.strptime(text, "%Y%m%dT%H%M%S")
    found: list[datetime] = []
    for moment in rrulestr(base, dtstart=start, cache=False):
        if (count is not None and len(found) >= count) or (until is not None and moment > until) or len(found) >= MAXIMUM_OCCURRENCES:
            break
        found.append(moment)
    return found


def pattern_of(rule: str, start: datetime) -> Pattern | None:
    """The pattern of an RRULE for a series whose first occurrence starts at start (wall clock)."""
    parts = _parts(rule)
    if parts is None:
        return None
    try:
        interval = max(1, int(parts.get("INTERVAL", "1")))
    except ValueError:
        return None
    by_day = [day for day in parts.get("BYDAY", "").split(",") if day]
    month_days = [day for day in parts.get("BYMONTHDAY", "").split(",") if day]
    frequency = parts["FREQ"]
    day_mask = day_number = 0
    if frequency == "DAILY" and not by_day:
        made = (DAILY, PATTERN_DAY, interval * 1440, 1)
    elif frequency in ("DAILY", "WEEKLY"):
        mask = _mask(by_day) if by_day else 1 << ((start.weekday() + 1) % 7)
        if mask is None or any(len(day) > 2 for day in by_day) or (frequency == "DAILY" and interval != 1):
            return None
        made, day_mask = (WEEKLY, PATTERN_WEEK, interval, 2), mask
    elif frequency in ("MONTHLY", "YEARLY"):
        yearly = frequency == "YEARLY"
        if yearly and parts.get("BYMONTH", str(start.month)) != str(start.month):
            return None
        period = interval * 12 if yearly else interval
        if by_day:
            nth = _nth(by_day, parts.get("BYSETPOS", ""))
            if nth is None or month_days:
                return None
            day_mask, day_number = nth
            made = (YEARLY if yearly else MONTHLY, PATTERN_MONTH_NTH, period, 4 if yearly else 3)
        else:
            if len(month_days) > 1:
                return None
            day = int(month_days[0]) if month_days and month_days[0].lstrip("-").isdigit() else start.day
            day_number = 31 if day == -1 else day
            if not 1 <= day_number <= 31:
                return None
            made = (YEARLY if yearly else MONTHLY, PATTERN_MONTH, period, 4 if yearly else 3)
    else:
        return None
    first_day = _DAYS.index(parts["WKST"]) if parts.get("WKST") in _DAYS else 1
    try:
        found = _occurrences(parts, start)
    except (ValueError, OverflowError):
        return None
    if not found:
        return None
    if "COUNT" in parts:
        end_type, occurrences, end = END_AFTER, len(found), found[-1].date()
    elif "UNTIL" in parts:
        end_type, occurrences, end = END_BY_DATE, len(found), found[-1].date()
    else:
        end_type, occurrences, end = END_NEVER, 10, None
    return Pattern(made[0], made[1], made[2], day_mask, day_number, end_type, occurrences, first_day, start.date(), end, made[3])


def _first_date_time(pattern: Pattern) -> int:
    start = minutes(pattern.start)
    if pattern.pattern_type == PATTERN_DAY:
        return start % pattern.period
    if pattern.pattern_type == PATTERN_WEEK:
        back = (pattern.start.weekday() + 1 - pattern.first_day_of_week) % 7
        return minutes(pattern.start - timedelta(days=back)) % (pattern.period * 7 * 1440)
    months = ((pattern.start.year - 1601) * 12 + pattern.start.month - 1) % pattern.period
    return minutes(date(1601 + months // 12, months % 12 + 1, 1))


def pattern_blob(pattern: Pattern, deleted: list[int], modified: list[int]) -> bytes:
    """The RecurrencePattern (MS-OXOCAL 2.2.1.44.1)."""
    blob = struct.pack("<HHHHHIII", 0x3004, 0x3004, pattern.frequency, pattern.pattern_type, 0, _first_date_time(pattern), pattern.period, 0)
    if pattern.pattern_type == PATTERN_WEEK:
        blob += struct.pack("<I", pattern.day_mask)
    elif pattern.pattern_type == PATTERN_MONTH:
        blob += struct.pack("<I", pattern.day_number)
    elif pattern.pattern_type == PATTERN_MONTH_NTH:
        blob += struct.pack("<II", pattern.day_mask, pattern.day_number)
    blob += struct.pack("<III", pattern.end_type, pattern.occurrences, pattern.first_day_of_week)
    blob += struct.pack(f"<I{len(deleted)}I", len(deleted), *sorted(deleted))
    blob += struct.pack(f"<I{len(modified)}I", len(modified), *sorted(modified))
    blob += struct.pack("<II", minutes(pattern.start), minutes(pattern.end) if pattern.end is not None else NO_END_DATE)
    return blob


def _short(value: str) -> bytes:
    data = value.encode("cp1252", errors="replace")[:0xFFFE]
    return struct.pack("<HH", len(data) + 1, len(data)) + data


def appointment_blob(pattern: Pattern, start_offset: int, length: int, deleted: list[date], changes: list[Change]) -> bytes:
    """The AppointmentRecurrencePattern (MS-OXOCAL 2.2.1.44.2): the pattern, the times of day and the changed occurrences."""
    ordered = sorted(changes, key=lambda change: change.start)
    deleted_minutes = sorted({minutes(day) for day in deleted} | {minutes(change.original.date()) for change in ordered})
    modified_minutes = sorted({minutes(change.start.date()) for change in ordered})
    blob = pattern_blob(pattern, deleted_minutes, modified_minutes)
    blob += struct.pack("<IIIIH", 0x3006, 0x3009, start_offset, start_offset + length, len(ordered))
    extended = b""
    for change in ordered:
        flags = (ARO_SUBJECT if change.subject is not None else 0) | (ARO_LOCATION if change.location is not None else 0)
        flags |= ARO_BUSYSTATUS if change.busy is not None else 0
        times = struct.pack("<III", wall_minutes(change.start), wall_minutes(change.end), wall_minutes(change.original))
        blob += times + struct.pack("<H", flags)
        if change.subject is not None:
            blob += _short(change.subject)
        if change.location is not None:
            blob += _short(change.location)
        if change.busy is not None:
            blob += struct.pack("<I", change.busy)
        extended += struct.pack("<II", 4, 0) + struct.pack("<I", 0)
        if flags & (ARO_SUBJECT | ARO_LOCATION):
            extended += times
            for value in (change.subject, change.location):
                if value is not None:
                    wide = value.encode("utf-16-le")[: 0xFFFF * 2]
                    extended += struct.pack("<H", len(wide) // 2) + wide
            extended += struct.pack("<I", 0)
    return blob + struct.pack("<I", 0) + extended + struct.pack("<I", 0)
