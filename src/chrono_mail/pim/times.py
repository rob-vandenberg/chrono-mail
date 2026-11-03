from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal

from chrono_mail.pim.ical import Moment

# --- Version ------------------------------------------------------------
__version__ = "times.py 4.4.506"

# --- Version history ----------------------------------------------------
# v4.4.506: moved_like_the_start and wall_clock, from commands_calendar: a proposal for a whole series moves its dates the same way (D-310)
# v3.6.351: parse_moment floating: a time without a zone stays one, for an item that had none
# v3.6.349: a zone the system cannot name falls back to its offset of now, never UTC (the review of RV-201); local_time and moment_at: a moment on this computer's clock without the platform's conversion, which Windows refuses before 1970 and after 3000 (RV-201, RV-204)
# v0.0.60: Initial time input and output for commands

_local: tzinfo | None = None


def local_zone() -> tzinfo:
    """The time zone of this computer as a named zone (Europe/Amsterdam), on Windows too; UTC when the system names none, so a time written
    to a server is always exact (a zone without a name has no definition a server could read)."""
    global _local
    if _local is None:
        try:
            _local = tzlocal.get_localzone()
        except Exception:  # noqa: BLE001 - a system without a usable zone setting is read as UTC, not as a failure
            _local = UTC
    return _local


def local_time(moment: datetime) -> datetime:
    """A moment on this computer's clock, through the named zone (the one typed times are read back in, so a list and an editor agree):
    Windows' own conversion refuses moments before 1970 and after 3000, which mail may carry (RV-201). A moment no conversion can show
    (the edges of year 1 and 9999) stays as it is. A floating time stays as it is."""
    if moment.tzinfo is None:
        return moment
    try:
        return moment.astimezone(local_zone())
    except (OverflowError, ValueError):
        return moment


def in_local_zone(moment: datetime) -> datetime:
    """A moment in the zone the engine reads typed times back in (what an editor shows is what it saves); at the edges of the years, as
    it is."""
    if moment.tzinfo is None:
        return moment
    try:
        return moment.astimezone(local_zone())
    except (OverflowError, ValueError):
        return moment


def moment_at(epoch: int) -> datetime:
    """Seconds since 1970 as a moment on this computer's clock, without the platform (Windows refuses times past 3000, RV-204); a number
    beyond the years a date can have is the first or last moment there is."""
    try:
        moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=epoch)
    except OverflowError:
        moment = datetime.max.replace(tzinfo=UTC) if epoch > 0 else datetime.min.replace(tzinfo=UTC)
    return local_time(moment)


def zone_named(name: str) -> tzinfo:
    """A zone by its name (Europe/Amsterdam, UTC); raises ValueError for an unknown one."""
    cleaned = name.strip()
    if cleaned.upper() in ("UTC", "Z"):
        return UTC
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"Unknown time zone '{name}' (use a name like Europe/Amsterdam)") from None


def parse_moment(text: str, zone: tzinfo | None = None, all_day: bool = False, floating: bool = False) -> Moment:
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
    if parsed.tzinfo is None and not (floating and zone is None):  # floating: a time without a zone stays one (the item had none)
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


def wall_clock(moment: Moment) -> datetime:
    """The moment on its own wall clock, without a zone (a day: its midnight)."""
    return moment.replace(tzinfo=None) if isinstance(moment, datetime) else datetime(moment.year, moment.month, moment.day)


def moved_like_the_start(moment: Moment, before: Moment, after: Moment) -> Moment:
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
        moved = wall_clock(moment) + (wall_clock(after) - wall_clock(before))
        if isinstance(after, datetime):
            return moved.replace(tzinfo=after.tzinfo)
        return moved.date()
    except (OverflowError, ValueError):
        return moment
