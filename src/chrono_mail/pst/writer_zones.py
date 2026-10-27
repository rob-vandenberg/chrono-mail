"""An event's time zone as the data file keeps it (MS-OXOCAL 2.2.1.39-2.2.1.41): the Windows name of the zone and its rules (offset,
summer-time offset and when each starts), worked out from the named zone. The mirror of zones.py."""

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chrono_mail.pst.zones import WINDOWS_ZONES

# --- Version ------------------------------------------------------------
__version__ = "writer_zones.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: the Windows name and the rules of a zone, as TZDEFINITION and TZSTRUCT

_BY_ZONE: dict[str, str] = {}
for _windows, _named in WINDOWS_ZONES.items():
    _BY_ZONE.setdefault(_named, _windows)
_BY_ZONE["UTC"] = _BY_ZONE["Etc/UTC"] = "UTC"


@dataclass(frozen=True, slots=True)
class ZoneRules:
    name: str
    bias: int  # minutes, UTC = local + bias
    daylight_bias: int
    standard: bytes  # SYSTEMTIME of the change to standard time (zeros: none)
    daylight: bytes


def _system_time(moment: datetime) -> bytes:
    """A yearly change as SYSTEMTIME: month, weekday, which one in the month (5: the last), and the wall-clock time."""
    week = (moment.day - 1) // 7 + 1
    if moment.day + 7 > ((moment.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day:
        week = 5
    return struct.pack("<8H", 0, moment.month, (moment.weekday() + 1) % 7, week, moment.hour, moment.minute, 0, 0)


def _signature(zone: tzinfo, year: int) -> tuple[timedelta | None, ...]:
    start = datetime(year, 1, 1, tzinfo=UTC)
    return tuple((start + timedelta(hours=6 * step)).astimezone(zone).utcoffset() for step in range(4 * 365))


@cache
def _windows_name(key: str, year: int) -> str:
    found = _BY_ZONE.get(key)
    if found is not None:
        return found
    try:
        zone = ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return ""
    wanted = _signature(zone, year)
    for windows, named in WINDOWS_ZONES.items():
        try:
            if _signature(ZoneInfo(named), year) == wanted:
                return windows
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ""


def rules_of(zone: tzinfo, year: int) -> ZoneRules:
    """The zone's rules in the given year, and its Windows name (its own name when Windows has none that fits)."""
    key = str(getattr(zone, "key", "") or ("UTC" if zone is UTC else ""))
    name = _windows_name(key, year) if key else ""
    moment = datetime(year, 1, 1, tzinfo=UTC)
    previous = moment.astimezone(zone)
    changes: list[tuple[datetime, datetime]] = []  # (wall clock just before, wall clock just after)
    for _ in range(366 * 24):
        moment += timedelta(hours=1)
        local = moment.astimezone(zone)
        if local.utcoffset() != previous.utcoffset():
            changes.append((moment.astimezone(timezone_of(previous)).replace(tzinfo=None), local))
        previous = local
    standard_offset = previous.utcoffset() or timedelta(0)
    if previous.dst():
        standard_offset -= previous.dst() or timedelta(0)
    bias = -int(standard_offset.total_seconds() // 60)
    if len(changes) != 2:
        return ZoneRules(name or key or "UTC", bias, 0, bytes(16), bytes(16))
    daylight_bias = 0
    to_standard = to_daylight = bytes(16)
    for before, after in changes:
        if after.dst():
            to_daylight = _system_time(before)
            daylight_bias = -int((after.dst() or timedelta(0)).total_seconds() // 60)
        else:
            to_standard = _system_time(before)
    return ZoneRules(name or key, bias, daylight_bias, to_standard, to_daylight)


def timezone_of(moment: datetime) -> tzinfo:
    return timezone(moment.utcoffset() or timedelta(0))


def definition(rules: ZoneRules, year: int) -> bytes:
    """A TZDEFINITION with the zone's name and one rule, in effect from the given year."""
    name = rules.name.encode("utf-16-le")
    header = struct.pack("<HH", 0x0002, len(rules.name)) + name + struct.pack("<H", 1)
    rule = struct.pack("<BBHHH", 0x02, 0x01, 0x003E, 0x0002, year) + bytes(14)
    rule += struct.pack("<iii", rules.bias, 0, rules.daylight_bias) + rules.standard + rules.daylight
    return struct.pack("<BBH", 0x02, 0x01, len(header)) + header + rule


def structure(rules: ZoneRules) -> bytes:
    """The older TZSTRUCT."""
    return struct.pack("<iiiH", rules.bias, 0, rules.daylight_bias, 0) + rules.standard + struct.pack("<H", 0) + rules.daylight
