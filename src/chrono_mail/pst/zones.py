import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --- Version ------------------------------------------------------------
__version__ = "zones.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: an event's time zone from what the data file keeps: the Windows zone name (CLDR's table), else the zone's
#           own rules (TZDEFINITION, TZSTRUCT) as a zone with a VTIMEZONE of yearly rules

# CLDR windowsZones, territory 001: every Windows zone name and its named zone
WINDOWS_ZONES: dict[str, str] = {
    "Dateline Standard Time": "Etc/GMT+12", "UTC-11": "Etc/GMT+11", "Aleutian Standard Time": "America/Adak",
    "Hawaiian Standard Time": "Pacific/Honolulu", "Marquesas Standard Time": "Pacific/Marquesas", "Alaskan Standard Time": "America/Anchorage",
    "UTC-09": "Etc/GMT+9", "Pacific Standard Time (Mexico)": "America/Tijuana", "UTC-08": "Etc/GMT+8", "Pacific Standard Time": "America/Los_Angeles",
    "US Mountain Standard Time": "America/Phoenix", "Mountain Standard Time (Mexico)": "America/Mazatlan", "Mountain Standard Time": "America/Denver",
    "Yukon Standard Time": "America/Whitehorse", "Central America Standard Time": "America/Guatemala", "Central Standard Time": "America/Chicago",
    "Easter Island Standard Time": "Pacific/Easter", "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina", "SA Pacific Standard Time": "America/Bogota", "Eastern Standard Time (Mexico)": "America/Cancun",
    "Eastern Standard Time": "America/New_York", "Haiti Standard Time": "America/Port-au-Prince", "Cuba Standard Time": "America/Havana",
    "US Eastern Standard Time": "America/Indianapolis", "Turks And Caicos Standard Time": "America/Grand_Turk",
    "Paraguay Standard Time": "America/Asuncion", "Atlantic Standard Time": "America/Halifax", "Venezuela Standard Time": "America/Caracas",
    "Central Brazilian Standard Time": "America/Cuiaba", "SA Western Standard Time": "America/La_Paz", "Pacific SA Standard Time": "America/Santiago",
    "Newfoundland Standard Time": "America/St_Johns", "Tocantins Standard Time": "America/Araguaina",
    "E. South America Standard Time": "America/Sao_Paulo", "SA Eastern Standard Time": "America/Cayenne",
    "Argentina Standard Time": "America/Buenos_Aires", "Greenland Standard Time": "America/Godthab", "Montevideo Standard Time": "America/Montevideo",
    "Magallanes Standard Time": "America/Punta_Arenas", "Saint Pierre Standard Time": "America/Miquelon", "Bahia Standard Time": "America/Bahia",
    "UTC-02": "Etc/GMT+2", "Mid-Atlantic Standard Time": "Etc/GMT+2", "Azores Standard Time": "Atlantic/Azores",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde", "UTC": "Etc/UTC", "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "Sao Tome Standard Time": "Africa/Sao_Tome", "Morocco Standard Time": "Africa/Casablanca",
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest", "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw", "W. Central Africa Standard Time": "Africa/Lagos", "Jordan Standard Time": "Asia/Amman",
    "GTB Standard Time": "Europe/Bucharest", "Middle East Standard Time": "Asia/Beirut", "Egypt Standard Time": "Africa/Cairo",
    "E. Europe Standard Time": "Europe/Chisinau", "Syria Standard Time": "Asia/Damascus", "West Bank Standard Time": "Asia/Hebron",
    "South Africa Standard Time": "Africa/Johannesburg", "FLE Standard Time": "Europe/Kiev", "Israel Standard Time": "Asia/Jerusalem",
    "South Sudan Standard Time": "Africa/Juba", "Kaliningrad Standard Time": "Europe/Kaliningrad", "Sudan Standard Time": "Africa/Khartoum",
    "Libya Standard Time": "Africa/Tripoli", "Namibia Standard Time": "Africa/Windhoek", "Arabic Standard Time": "Asia/Baghdad",
    "Turkey Standard Time": "Europe/Istanbul", "Arab Standard Time": "Asia/Riyadh", "Belarus Standard Time": "Europe/Minsk",
    "Russian Standard Time": "Europe/Moscow", "E. Africa Standard Time": "Africa/Nairobi", "Volgograd Standard Time": "Europe/Volgograd",
    "Iran Standard Time": "Asia/Tehran", "Arabian Standard Time": "Asia/Dubai", "Astrakhan Standard Time": "Europe/Astrakhan",
    "Azerbaijan Standard Time": "Asia/Baku", "Russia Time Zone 3": "Europe/Samara", "Mauritius Standard Time": "Indian/Mauritius",
    "Saratov Standard Time": "Europe/Saratov", "Georgian Standard Time": "Asia/Tbilisi", "Caucasus Standard Time": "Asia/Yerevan",
    "Armenian Standard Time": "Asia/Yerevan", "Afghanistan Standard Time": "Asia/Kabul", "West Asia Standard Time": "Asia/Tashkent",
    "Qyzylorda Standard Time": "Asia/Qyzylorda", "Ekaterinburg Standard Time": "Asia/Yekaterinburg", "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Calcutta", "Sri Lanka Standard Time": "Asia/Colombo", "Nepal Standard Time": "Asia/Katmandu",
    "Central Asia Standard Time": "Asia/Bishkek", "Bangladesh Standard Time": "Asia/Dhaka", "Omsk Standard Time": "Asia/Omsk",
    "Myanmar Standard Time": "Asia/Rangoon", "SE Asia Standard Time": "Asia/Bangkok", "Altai Standard Time": "Asia/Barnaul",
    "W. Mongolia Standard Time": "Asia/Hovd", "North Asia Standard Time": "Asia/Krasnoyarsk", "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Tomsk Standard Time": "Asia/Tomsk", "China Standard Time": "Asia/Shanghai", "North Asia East Standard Time": "Asia/Irkutsk",
    "Singapore Standard Time": "Asia/Singapore", "W. Australia Standard Time": "Australia/Perth", "Taipei Standard Time": "Asia/Taipei",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar", "Aus Central W. Standard Time": "Australia/Eucla", "Transbaikal Standard Time": "Asia/Chita",
    "Tokyo Standard Time": "Asia/Tokyo", "North Korea Standard Time": "Asia/Pyongyang", "Korea Standard Time": "Asia/Seoul",
    "Yakutsk Standard Time": "Asia/Yakutsk", "Cen. Australia Standard Time": "Australia/Adelaide", "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane", "AUS Eastern Standard Time": "Australia/Sydney",
    "West Pacific Standard Time": "Pacific/Port_Moresby", "Tasmania Standard Time": "Australia/Hobart", "Vladivostok Standard Time": "Asia/Vladivostok",
    "Lord Howe Standard Time": "Australia/Lord_Howe", "Bougainville Standard Time": "Pacific/Bougainville", "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Magadan Standard Time": "Asia/Magadan", "Norfolk Standard Time": "Pacific/Norfolk", "Sakhalin Standard Time": "Asia/Sakhalin",
    "Central Pacific Standard Time": "Pacific/Guadalcanal", "Russia Time Zone 11": "Asia/Kamchatka", "Kamchatka Standard Time": "Asia/Kamchatka",
    "New Zealand Standard Time": "Pacific/Auckland", "UTC+12": "Etc/GMT-12", "Fiji Standard Time": "Pacific/Fiji",
    "Chatham Islands Standard Time": "Pacific/Chatham", "UTC+13": "Etc/GMT-13", "Tonga Standard Time": "Pacific/Tongatapu",
    "Samoa Standard Time": "Pacific/Apia", "Line Islands Standard Time": "Pacific/Kiritimati", "Mexico Standard Time": "America/Mexico_City",
    "Mexico Standard Time 2": "America/Chihuahua",
}
_TZRULE_SIZE = 66
_EFFECTIVE_RULE = 0x0002
_DAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")


def _zone(name: str) -> tzinfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Change:
    """When a zone changes to or from summer time (a SYSTEMTIME of the rule): the day-th (5 = last) weekday of a month, at a time."""

    month: int
    weekday: int  # 0 Sunday
    day: int
    hour: int
    minute: int

    def on(self, year: int) -> datetime:
        first = datetime(year, self.month, 1)
        offset = (self.weekday - (first.weekday() + 1) % 7) % 7
        moment = first + timedelta(days=offset + 7 * (max(1, min(self.day, 5)) - 1))
        while moment.month != self.month:
            moment -= timedelta(days=7)  # the fifth that is not there: the last
        return moment + timedelta(hours=min(self.hour, 23), minutes=min(self.minute, 59))

    def byday(self) -> str:
        return f"{-1 if self.day >= 5 else max(1, self.day)}{_DAYS[self.weekday]}"


def _change(data: bytes) -> Change | None:
    _, month, weekday, day, hour, minute = struct.unpack_from("<6H", data)
    if not 1 <= month <= 12 or not 0 <= weekday <= 6:
        return None
    return Change(month, weekday, day, hour, minute)


class RuleZone(tzinfo):
    """A zone made from the rules the data file keeps (offset, summer-time offset and when each starts), for a zone no name can be found
    for. Its key names it in the calendar text; its VTIMEZONE says the same rules as yearly RRULEs."""

    def __init__(self, key: str, bias: int, standard_bias: int, daylight_bias: int, standard: Change | None, daylight: Change | None) -> None:
        self.key = key
        self.standard_offset = timedelta(minutes=-(bias + standard_bias))
        self.daylight_offset = timedelta(minutes=-(bias + daylight_bias))
        self.standard = standard
        self.daylight = daylight

    def _summer(self, moment: datetime | None) -> bool:
        if moment is None or self.standard is None or self.daylight is None:
            return False
        local = moment.replace(tzinfo=None)
        try:
            to_daylight = self.daylight.on(local.year)
            to_standard = self.standard.on(local.year)
        except (ValueError, OverflowError):
            return False
        if to_daylight < to_standard:
            return to_daylight <= local < to_standard
        return not to_standard <= local < to_daylight  # the southern half: summer across the turn of the year

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self.daylight_offset if self._summer(dt) else self.standard_offset

    def dst(self, dt: datetime | None) -> timedelta:
        return self.daylight_offset - self.standard_offset if self._summer(dt) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return self.key

    def __repr__(self) -> str:
        return f"RuleZone({self.key!r})"

    def vtimezone(self) -> str:
        """The zone as a VTIMEZONE with yearly rules."""

        def offset(value: timedelta) -> str:
            minutes = int(value.total_seconds() // 60)
            return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"

        lines = ["BEGIN:VTIMEZONE", f"TZID:{self.key}"]
        if self.standard is None or self.daylight is None:
            lines += ["BEGIN:STANDARD", "DTSTART:19700101T000000", f"TZOFFSETFROM:{offset(self.standard_offset)}",
                      f"TZOFFSETTO:{offset(self.standard_offset)}", "END:STANDARD"]
        else:
            for kind, change, before, after in (
                ("STANDARD", self.standard, self.daylight_offset, self.standard_offset),
                ("DAYLIGHT", self.daylight, self.standard_offset, self.daylight_offset),
            ):
                lines += [f"BEGIN:{kind}", f"DTSTART:{change.on(1970).strftime('%Y%m%dT%H%M%S')}", f"TZOFFSETFROM:{offset(before)}",
                          f"TZOFFSETTO:{offset(after)}", f"RRULE:FREQ=YEARLY;BYMONTH={change.month};BYDAY={change.byday()}", f"END:{kind}"]
        lines.append("END:VTIMEZONE")
        return "\r\n".join(lines) + "\r\n"


def _rule_zone(key: str, offsets: bytes, standard_change: bytes, daylight_change: bytes) -> tzinfo | None:
    """A zone from lBias, lStandardBias and lDaylightBias and the two SYSTEMTIMEs of the changes."""
    bias, standard_bias, daylight_bias = struct.unpack_from("<iii", offsets)
    if not all(-24 * 60 <= value <= 24 * 60 for value in (bias, standard_bias, daylight_bias)):
        return None
    standard = _change(standard_change)
    daylight = _change(daylight_change)
    if standard is None or daylight is None:
        total = bias + standard_bias
        if total % 60 == 0 and -14 * 60 <= total <= 12 * 60:
            hours = total // 60
            return _zone("Etc/UTC" if hours == 0 else f"Etc/GMT{'+' if hours > 0 else '-'}{abs(hours)}")
        return RuleZone(key, bias, standard_bias, daylight_bias, None, None)
    return RuleZone(key, bias, standard_bias, daylight_bias, standard, daylight)


def _key(name: str, bias: int) -> str:
    if name:
        return name
    minutes = -bias
    return f"UTC{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d} (from the data file)"


def from_windows_name(name: str) -> tzinfo | None:
    """The named zone for a Windows zone name; None for one not in the table."""
    iana = WINDOWS_ZONES.get(name.strip())
    return _zone(iana) if iana is not None else None


def from_definition(blob: bytes) -> tzinfo | None:
    """A zone from a TZDEFINITION blob (MS-OXOCAL 2.2.1.41): by its Windows name when known, else from its effective rule."""
    if len(blob) < 8 or blob[0] != 2:
        return None
    header_size = int.from_bytes(blob[2:4], "little")
    characters = int.from_bytes(blob[6:8], "little")
    if 8 + 2 * characters > len(blob) or characters > 260:
        return None
    name = blob[8 : 8 + 2 * characters].decode("utf-16-le", errors="replace").strip()
    named = from_windows_name(name)
    if named is not None:
        return named
    position = 4 + header_size
    if position + 2 > len(blob):
        return None
    count = int.from_bytes(blob[position - 2 : position], "little")
    rules = [blob[position + index * _TZRULE_SIZE : position + (index + 1) * _TZRULE_SIZE] for index in range(min(count, 1000))]
    rules = [rule for rule in rules if len(rule) == _TZRULE_SIZE]
    if not rules:
        return None
    effective = next((rule for rule in rules if int.from_bytes(rule[4:6], "little") & _EFFECTIVE_RULE), rules[-1])
    offsets = effective[22:]  # after versions, reserved, flags, year and the 14 bytes of X: the biases, then the two SYSTEMTIMEs
    return _rule_zone(_key(name, int.from_bytes(offsets[0:4], "little", signed=True)), offsets[:12], offsets[12:28], offsets[28:44])


def from_structure(blob: bytes) -> tzinfo | None:
    """A zone from the old TZSTRUCT (MS-OXOCAL 2.2.1.39): its offset and its summer-time rule; a whole-hour zone without summer time is
    the named fixed zone of its offset."""
    if len(blob) < 48:
        return None  # the biases, the standard year and change, the daylight year and change
    return _rule_zone(_key("", int.from_bytes(blob[0:4], "little", signed=True)), blob[:12], blob[14:30], blob[32:48])


def vtimezone_of(zone: tzinfo) -> str | None:
    return zone.vtimezone() if isinstance(zone, RuleZone) else None

