from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal

from chrono_mail.pim.ical import Moment

# --- Version ------------------------------------------------------------
__version__ = "times.py 0.0.60"

# --- Version history ----------------------------------------------------
# v0.0.60: Initial time input and output for commands

_local: tzinfo | None = None


def local_zone() -> tzinfo:
    """The time zone of this computer as a named zone (Europe/Amsterdam), on Windows too; a fixed offset only when the system names none."""
    global _local
    if _local is None:
        try:
            _local = tzlocal.get_localzone()
        except Exception:  # noqa: BLE001 - a system without a usable zone setting is read as UTC, not as a failure
            _local = UTC
    return _local


def zone_named(name: str) -> tzinfo:
    """A zone by its name (Europe/Amsterdam, UTC); raises ValueError for an unknown one."""
    cleaned = name.strip()
    if cleaned.upper() in ("UTC", "Z"):
        return UTC
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"Unknown time zone '{name}' (use a name like Europe/Amsterdam)") from None


def parse_moment(text: str, zone: tzinfo | None = None, all_day: bool = False) -> Moment:
    """A date or a time as people write it. '2026-10-12' is a date (an all-day value); '2026-10-12T09:00' or '2026-10-12 09:00' is a time in `zone`
    (the local zone when not given); a trailing Z or an offset (+02:00) makes it exact. Raises ValueError with a readable message."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("A date or time is needed")
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00") if cleaned.endswith(("Z", "z")) else cleaned)
    except ValueError:
        raise ValueError(f"'{text}' is not a date or time (write 2026-10-12 or 2026-10-12T09:00, or with an offset such as +02:00)") from None
    only_date = len(cleaned) <= 10 and "T" not in cleaned.upper() and ":" not in cleaned
    if only_date or all_day:
        return parsed.date()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or local_zone())
    return parsed


def format_moment(moment: Moment | None) -> str:
    """A date as 2026-10-12, a time as 2026-10-12T09:00:00+02:00 (floating times without an offset)."""
    if moment is None:
        return ""
    return moment.isoformat() if isinstance(moment, (datetime, date)) else str(moment)


def in_zone(moment: Moment, zone: tzinfo | None = None) -> Moment:
    """A time shown in a zone (the local one by default); dates and floating times stay as they are."""
    if isinstance(moment, datetime) and moment.tzinfo is not None:
        return moment.astimezone(zone or local_zone())
    return moment


def day_bounds(day: date, zone: tzinfo | None = None) -> tuple[datetime, datetime]:
    """The start of a day and the start of the next, in a zone."""
    zone = zone or local_zone()
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    return start, datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
