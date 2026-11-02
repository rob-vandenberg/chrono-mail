"""Every field of both versions of an item in a sync conflict, in words a person reads (D-308): dates in the window's own words, a repeat as
"Every week on Monday", a reminder as "1 hour before the start, message on screen", never a raw calendar or card value. Only the fields a person
enters or sees (merge.VISIBLE). Never raises: a value that cannot be read is shown as it is written."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from chrono_mail.core.words import counted, digits
from chrono_mail.pim.merge import ALARMS, LOCAL, MONTHS, SEPARATOR, SERVER, choice_key, units_of, visible
from chrono_mail.pim.times import in_local_zone, zone_named

# --- Version ------------------------------------------------------------
__version__ = "conflictwords.py 4.2.492"

# --- Version history ----------------------------------------------------
# v4.2.492: an entry of a list only here and one only on the server, of the same kind, are one row (the owner, D-308)
# v4.2.481: Initial: the rows of the window of one sync conflict, every field in words (the owner, D-308)

ABSENT: str = "(empty)"
ORDER: tuple[str, ...] = (
    "SUMMARY", "N", "FN", "NICKNAME", "ORG", "TITLE", "X-CHRONO-MAIL-COLOR-ID", "PRIORITY", "STATUS", "PERCENT-COMPLETE", "LOCATION", "DTSTART",
    "DTEND", "DUE", "DURATION", "RRULE", "EXDATE", "RDATE", ALARMS, "TRANSP", "ATTENDEE", "EMAIL", "TEL", "URL", "ADR", "BDAY", "CATEGORIES",
    "DESCRIPTION", "NOTE", "PHOTO", "MEMBER", "X-ADDRESSBOOKSERVER-MEMBER",
)
LABELS: dict[str, str] = {
    "SUMMARY": "Title", "N": "Name", "FN": "Display name", "NICKNAME": "Nickname", "ORG": "Company", "TITLE": "Job title",
    "X-CHRONO-MAIL-COLOR-ID": "Colour", "PRIORITY": "Priority", "STATUS": "Status", "PERCENT-COMPLETE": "Done", "LOCATION": "Location",
    "DTSTART": "Starts", "DTEND": "Ends", "DUE": "Due", "DURATION": "Duration", "RRULE": "Repeat", "EXDATE": "Skipped date", "RDATE": "Extra date",
    ALARMS: "Reminder", "TRANSP": "Show as", "ATTENDEE": "Attendee", "EMAIL": "E-mail", "TEL": "Phone", "URL": "Web address", "ADR": "Address",
    "BDAY": "Birthday", "CATEGORIES": "Tag", "DESCRIPTION": "Description", "NOTE": "Note", "PHOTO": "Photo", "MEMBER": "Group member",
    "X-ADDRESSBOOKSERVER-MEMBER": "Group member",
}
TASK_LABELS: dict[str, str] = {"SUMMARY": "Summary", "DESCRIPTION": "Note", "DTSTART": "Start"}  # as the task window names them
DAYS: dict[str, str] = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday", "FR": "Friday", "SA": "Saturday", "SU": "Sunday"}
ORDINALS: dict[int, str] = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last", -2: "second last"}
FREQUENCIES: dict[str, str] = {"YEARLY": "year", "MONTHLY": "month", "WEEKLY": "week", "DAILY": "day", "HOURLY": "hour", "MINUTELY": "minute",
                               "SECONDLY": "second"}
RULE_PARTS_IN_WORDS: frozenset[str] = frozenset({"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "COUNT", "UNTIL", "WKST"})
ACTIONS: dict[str, str] = {"DISPLAY": "message on screen", "EMAIL": "e-mail", "AUDIO": "sound", "NONE": "no alert"}
TASK_STATES: dict[str, str] = {"NEEDS-ACTION": "To do", "IN-PROCESS": "In progress", "COMPLETED": "Done", "CANCELLED": "Cancelled"}
EVENT_STATES: dict[str, str] = {"CONFIRMED": "Confirmed", "TENTATIVE": "Tentative", "CANCELLED": "Cancelled"}
TYPES_LEFT_OUT: frozenset[str] = frozenset({"PREF", "INTERNET", "VOICE", "X400"})
PAIRED: frozenset[str] = frozenset({"NICKNAME", "EMAIL", "TEL", "URL", "ADR", "CATEGORIES", "ATTENDEE"})  # lists whose entries pair up (D-308)


@dataclass(frozen=True, slots=True)
class Words:
    """What the window gives: a moment in its own words (a date alone when it is one), and a calendar colour's name by its id."""

    when: Callable[[date | datetime], str]
    colour: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class Row:
    """One field of the item: what a choice names, its label, both values in words, whether they differ, for a difference that is no clash
    the side that is taken by itself (LOCAL or SERVER; "" when it clashes or is the same), and every entry its choice settles (two for a pair)."""

    key: str
    label: str
    local: str
    server: str
    differs: bool
    taken: str
    keys: tuple[str, ...]


def _unescaped(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _head_and_value(line: str) -> tuple[str, str]:
    quoted = False
    for position, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            return line[:position], line[position + 1 :]
    return line, ""


def _parameters(head: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in head.split(";")[1:]:
        name, _, value = part.partition("=")
        found[name.strip().upper()] = value.strip().strip('"')
    return found


def moment_of(value: str, parameters: dict[str, str]) -> date | datetime | None:
    """A date or a moment as written in a calendar or a card (20260812, 20260812T090000Z, with its TZID); None when unreadable."""
    text = value.strip().replace("-", "").replace(":", "")
    try:
        if len(text) == 8 and digits(text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        if len(text) >= 15 and text[8] == "T" and digits(text[:8]) and digits(text[9:15]):
            moment = datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), int(text[9:11]), int(text[11:13]), int(text[13:15]))
            if text.endswith("Z"):
                return in_local_zone(moment.replace(tzinfo=UTC))
            if parameters.get("TZID"):
                return in_local_zone(moment.replace(tzinfo=zone_named(parameters["TZID"])))
            return moment
    except ValueError:
        return None
    return None


def _when(value: str, parameters: dict[str, str], words: Words) -> str:
    moment = moment_of(value, parameters)
    return words.when(moment) if moment is not None else value


def duration_words(value: str) -> tuple[str, bool]:
    """An ISO duration (-PT1H, P1DT2H, P2W) in words, and whether it is negative; ("", False) when unreadable, ("0 minutes", …) for zero."""
    text = value.strip().upper()
    negative = text.startswith("-")
    text = text.lstrip("+-")
    if not text.startswith("P"):
        return "", False
    units = {"W": "week", "D": "day", "H": "hour", "M": "minute", "S": "second"}
    parts: list[str] = []
    number = ""
    in_time = False
    for character in text[1:]:
        if character == "T":
            in_time = True
        elif character.isascii() and character.isdigit():
            number += character
        elif character in units and number:
            unit = "minute" if character == "M" and in_time else units[character]
            if character == "M" and not in_time:
                return "", False  # months are not a duration
            if int(number):
                parts.append(counted(int(number), unit))
            number = ""
        else:
            return "", False
    return (" ".join(parts) or "0 minutes"), negative


def _day_words(day: str) -> str:
    code = day[-2:].upper()
    name = DAYS.get(code, day)
    number = day[:-2]
    if number.lstrip("+-").isdigit() and number:
        return f"the {ORDINALS.get(int(number), number)} {name}"
    return name


def rule_words(value: str, words: Words) -> str:
    """A repeat rule (FREQ=WEEKLY;BYDAY=MO) in words: "Every week on Monday", "Every 2 months on day 12, 10 times"."""
    parts: dict[str, str] = {}
    for part in value.split(";"):
        name, _, setting = part.partition("=")
        if name.strip():
            parts[name.strip().upper()] = setting.strip()
    unit = FREQUENCIES.get(parts.get("FREQ", "").upper())
    if unit is None:
        return value
    interval = int(parts["INTERVAL"]) if digits(parts.get("INTERVAL", "")) and int(parts["INTERVAL"]) > 1 else 1
    text = f"Every {interval} {unit}s" if interval > 1 else f"Every {unit}"
    if parts.get("BYDAY"):
        text += " on " + ", ".join(_day_words(day) for day in parts["BYDAY"].split(",") if day)
    if parts.get("BYMONTHDAY"):
        text += " on day " + ", ".join(parts["BYMONTHDAY"].split(","))
    if parts.get("BYMONTH"):
        months = [MONTHS[int(month) - 1] for month in parts["BYMONTH"].split(",") if digits(month) and 1 <= int(month) <= 12]
        text += " in " + ", ".join(months)
    if digits(parts.get("COUNT", "")):
        text += ", " + counted(int(parts["COUNT"]), "time")
    if parts.get("UNTIL"):
        text += ", until " + _when(parts["UNTIL"], {}, words)
    if set(parts) - RULE_PARTS_IN_WORDS:
        text += " (with further rules)"
    return text


def reminder_words(text: str, task: bool, words: Words) -> str:
    """All reminders of an item in words, one after another: "1 hour before the start, message on screen"."""
    found: list[str] = []
    trigger, parameters, action = "", dict[str, str](), ""
    for line in text.split("\n"):
        head, value = _head_and_value(line)
        name = head.split(";", 1)[0].strip().upper()
        if name == "BEGIN":
            trigger, parameters, action = "", dict[str, str](), ""
        elif name == "TRIGGER":
            trigger, parameters = value.strip(), _parameters(head)
        elif name == "ACTION":
            action = value.strip().upper()
        elif name == "END":
            found.append(f"{_trigger_words(trigger, parameters, task, words)}, {ACTIONS.get(action, action.lower() or 'message on screen')}")
    return "; ".join(found) or ABSENT


def _trigger_words(trigger: str, parameters: dict[str, str], task: bool, words: Words) -> str:
    if parameters.get("VALUE", "").upper() == "DATE-TIME" or (trigger[:1].isdigit()):
        return "On " + _when(trigger, parameters, words)
    span, negative = duration_words(trigger)
    if not span:
        return trigger
    anchor = ("the due date" if task else "the end") if parameters.get("RELATED", "").upper() == "END" else "the start"
    if span == "0 minutes":
        return f"At {anchor}"
    return f"{span} {'before' if negative else 'after'} {anchor}"


def _kinds(parameters: dict[str, str]) -> list[str]:
    return [kind.lower() for kind in parameters.get("TYPE", "").split(",") if kind and kind.upper() not in TYPES_LEFT_OUT]


def _entry(value: str, parameters: dict[str, str]) -> str:
    kinds = _kinds(parameters)
    text = _unescaped(value.removeprefix("mailto:").removeprefix("MAILTO:").removeprefix("tel:"))
    return f"{text} ({', '.join(kinds)})" if kinds else text


def _priority(value: str) -> str:
    if not digits(value.strip()) or int(value.strip()) == 0:
        return "None"
    number = int(value.strip())
    return "High" if number <= 4 else "Medium" if number == 5 else "Low"


def _birthday(value: str, parameters: dict[str, str], words: Words) -> str:
    text = value.strip()
    if text.startswith("--"):  # no year
        month_day = text[2:].replace("-", "")
        if len(month_day) == 4 and digits(month_day) and 1 <= int(month_day[:2]) <= 12:
            return f"{int(month_day[2:])} {MONTHS[int(month_day[:2]) - 1]}"
        return value
    return _when(text[:10], parameters, words)


def value_words(name: str, field: str, key: str, task: bool, words: Words) -> str:
    """One field's text (a whole line, a value of a comma list, or all reminders) in words."""
    if name == ALARMS:
        return reminder_words(field, task, words)
    if SEPARATOR in key and key.count(SEPARATOR) == 2:  # a value of a comma list: its line's parameters are in the key
        parameters = _parameters(key.split(SEPARATOR)[1])
        return _when(field, parameters, words) if name in ("EXDATE", "RDATE") else _unescaped(field)
    head, value = _head_and_value(field)
    parameters = _parameters(head)
    if name in ("DTSTART", "DTEND", "DUE"):
        return _when(value, parameters, words)
    if name == "BDAY":
        return _birthday(value, parameters, words)
    if name == "DURATION":
        return duration_words(value)[0] or value
    if name == "RRULE":
        return rule_words(value, words)
    if name == "TRANSP":
        return "Free" if value.strip().upper() == "TRANSPARENT" else "Busy"
    if name == "ATTENDEE":
        address = value.strip().removeprefix("mailto:").removeprefix("MAILTO:")
        return f"{parameters['CN']} <{address}>" if parameters.get("CN") else address
    if name == "X-CHRONO-MAIL-COLOR-ID":
        return words.colour(value.strip())
    if name == "PRIORITY":
        return _priority(value)
    if name == "STATUS":
        return (TASK_STATES if task else EVENT_STATES).get(value.strip().upper(), value.strip().title())
    if name == "PERCENT-COMPLETE":
        return f"{value.strip()} %"
    if name == "N":  # family; given; additional; prefix; suffix
        parts = [_unescaped(part) for part in (value.split(";") + ["", "", "", "", ""])[:5]]
        return " ".join(part for part in (parts[3], parts[1], parts[2], parts[0], parts[4]) if part) or ABSENT
    if name in ("ORG", "ADR"):
        return ", ".join(part for part in (_unescaped(piece) for piece in value.split(";")) if part) or ABSENT
    if name in ("EMAIL", "TEL", "URL"):
        return _entry(value, parameters)
    if name == "PHOTO":
        return "A photo"
    if name in ("MEMBER", "X-ADDRESSBOOKSERVER-MEMBER"):
        return "A member"
    return _unescaped(value) or ABSENT


def _kind(name: str, field: str) -> tuple[str, ...]:
    """The kinds an entry shows (work, cell), the same however written; none for a tag."""
    return () if name == "CATEGORIES" else tuple(sorted(set(_kinds(_parameters(_head_and_value(field)[0])))))


def _paired(entries: list[tuple[Row, str, str, tuple[str, ...]]]) -> list[Row]:
    """An entry only here and one only on the server, of the same field and kind, in their order, are one row: its choice keeps that side's
    entry and leaves out the other's. An entry without a partner keeps its own row (the owner, D-308)."""
    out: list[Row | None] = [row for row, _, _, _ in entries]
    waiting: dict[tuple[str, tuple[str, ...]], list[int]] = {}
    for position, (_, side, name, kind) in enumerate(entries):
        if side == LOCAL:
            waiting.setdefault((name, kind), []).append(position)
    for position, (theirs, side, name, kind) in enumerate(entries):
        if side == SERVER and waiting.get((name, kind)):
            partner = waiting[(name, kind)].pop(0)
            ours = entries[partner][0]
            out[partner] = Row(ours.key, ours.label, ours.local, theirs.server, True, "", ours.keys + theirs.keys)
            out[position] = None
    return [row for row in out if row is not None]


def rows(base: str | None, local: str, server: str, words: Words) -> list[Row]:
    """Every field a person enters or sees, of the main item and each changed occurrence, with both versions in words. A field that differs
    where only one side changed it is taken from that side by itself (`taken`); where both did, it is a clash, chosen by the person. The
    entries of a list that differ are paired (_paired)."""
    base_units, local_units, server_units = units_of(base), units_of(local), units_of(server)
    found: list[Row] = []
    for unit_key in dict.fromkeys([*local_units, *server_units]):
        unit_name = (local_units.get(unit_key) or server_units[unit_key])[0]
        task = unit_name == "VTODO"
        ours = local_units[unit_key][1] if unit_key in local_units else dict[str, str]()
        theirs = server_units[unit_key][1] if unit_key in server_units else dict[str, str]()
        before = base_units[unit_key][1] if unit_key in base_units else None
        keys = [key for key in dict.fromkeys([*ours, *theirs]) if visible(unit_name, key)]
        keys.sort(key=lambda key: ORDER.index(key.split(SEPARATOR, 1)[0]) if key.split(SEPARATOR, 1)[0] in ORDER else len(ORDER))
        recurrence = unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else ""
        suffix = f" (the occurrence of {_when(recurrence, {}, words)})" if recurrence else ""
        entries: list[tuple[Row, str, str, tuple[str, ...]]] = []  # each row with, for an entry of a list on one side only, that side and its kind
        for key in keys:
            name = key.split(SEPARATOR, 1)[0]
            mine = value_words(name, ours[key], key, task, words) if key in ours else ABSENT
            yours = value_words(name, theirs[key], key, task, words) if key in theirs else ABSENT
            differs = mine != yours
            taken = ""
            if differs and before is not None:
                if before.get(key) == ours.get(key):
                    taken = SERVER
                elif before.get(key) == theirs.get(key):
                    taken = LOCAL
            label = (TASK_LABELS.get(name) if task else None) or LABELS.get(name, name.title())
            chosen = choice_key(key, unit_key)
            side = "" if name not in PAIRED or (key in ours) == (key in theirs) else LOCAL if key in ours else SERVER
            entries.append((Row(chosen, label + suffix, mine, yours, differs, taken, (chosen,)), side, name, _kind(name, ours.get(key) or theirs[key])))
        found.extend(_paired(entries))
    return found
