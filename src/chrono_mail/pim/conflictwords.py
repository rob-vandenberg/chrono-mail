"""Every field of both versions of an item in a sync conflict, in words a person reads (D-308): dates in the window's own words, a repeat as
"Every week on Monday", a reminder as "1 hour before the start, message on screen", never a raw calendar or card value. Only the fields a person
enters or sees (merge.VISIBLE). Never raises: a value that cannot be read is shown as it is written."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from chrono_mail.core.words import counted, digits
from chrono_mail.pim.merge import ALARMS, LOCAL, MONTHS, SEPARATOR, SERVER, choice_key, copy_of, jar, merge, number, repeats, same, units_together, visible
from chrono_mail.pim.times import in_local_zone, zone_named

# --- Version ------------------------------------------------------------
__version__ = "conflictwords.py 4.2.497"

# --- Version history ----------------------------------------------------
# v4.2.497: a field of an occurrence the person keeps or leaves out is taken by nothing; a jar's entry taken out on a side shows as going, as merge does; a row differs and has its choice exactly as merge reads the item (one notion of a difference): a photo, an attendee's answer,
#           reminders in another order, the same moment in another zone; an occurrence on one side only without an agreed version is a
#           choice; Role and Anniversary are shown; a comma list's value is a line of its own (the owner); another zone differs in an item
#           that repeats, as merge reads it; a number longer than nine digits is shown as written, never a ValueError (OR-3); every kind of
#           an entry shown (TYPE=work;TYPE=cell is "(work, cell)"); the units' keys as merge reads them together; a text that cannot be read
#           is one row, the whole item; a "\;" inside a part of a name, company or address stays; a choice never reads the same on both sides;
#           a second copy of a changed occurrence shows its own start and is named so, a second copy of the item too; values that differ
#           only in spaces are shown in quotes
# v4.2.496: an entry of a jar (members, tags) and the fields of an occurrence on one side only are shown without a choice; such an
#           occurrence has one choice, keep or leave out, where it clashes; a strange digit in a repeat never raises (the owner)
# v4.2.492: an entry of a list only here and one only on the server, of the same kind, are one row (the owner, D-308)
# v4.2.481: Initial: the rows of the window of one sync conflict, every field in words (the owner, D-308)

ABSENT: str = "(empty)"
ORDER: tuple[str, ...] = (
    "SUMMARY", "N", "FN", "NICKNAME", "ORG", "TITLE", "ROLE", "X-CHRONO-MAIL-COLOR-ID", "PRIORITY", "STATUS", "PERCENT-COMPLETE", "LOCATION",
    "DTSTART", "DTEND", "DUE", "DURATION", "RRULE", "EXDATE", "RDATE", ALARMS, "TRANSP", "ATTENDEE", "EMAIL", "TEL", "URL", "ADR", "BDAY",
    "ANNIVERSARY", "CATEGORIES", "DESCRIPTION", "NOTE", "PHOTO", "MEMBER", "X-ADDRESSBOOKSERVER-MEMBER",
)
LABELS: dict[str, str] = {
    "SUMMARY": "Title", "N": "Name", "FN": "Display name", "NICKNAME": "Nickname", "ORG": "Company", "TITLE": "Job title", "ROLE": "Role",
    "X-CHRONO-MAIL-COLOR-ID": "Colour", "PRIORITY": "Priority", "STATUS": "Status", "PERCENT-COMPLETE": "Done", "LOCATION": "Location",
    "DTSTART": "Starts", "DTEND": "Ends", "DUE": "Due", "DURATION": "Duration", "RRULE": "Repeat", "EXDATE": "Skipped date", "RDATE": "Extra date",
    ALARMS: "Reminder", "TRANSP": "Show as", "ATTENDEE": "Attendee", "EMAIL": "E-mail", "TEL": "Phone", "URL": "Web address", "ADR": "Address",
    "BDAY": "Birthday", "ANNIVERSARY": "Anniversary", "CATEGORIES": "Tag", "DESCRIPTION": "Description", "NOTE": "Note", "PHOTO": "Photo",
    "MEMBER": "Group member", "X-ADDRESSBOOKSERVER-MEMBER": "Group member",
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
MOMENTS: frozenset[str] = frozenset({"DTSTART", "DTEND", "DUE", "EXDATE", "RDATE"})  # a moment, whose zone tells it apart
PAIRED: frozenset[str] = frozenset({"NICKNAME", "EMAIL", "TEL", "URL", "ADR", "ATTENDEE"})  # lists whose entries pair up (D-308)


@dataclass(frozen=True, slots=True)
class Words:
    """What the window gives: a moment in its own words (a date alone when it is one), and a calendar colour's name by its id."""

    when: Callable[[date | datetime], str]
    colour: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class Row:
    """One field of the item: what a choice names, its label, both values in words, whether they differ, for a difference that is no clash
    the side that is taken by itself (LOCAL or SERVER; "" when it clashes or is the same), every entry its choice settles (two for a pair), and
    whether the person chooses it (a difference that is no choice is taken by itself: a jar's entry, a field of an occurrence on one side only)."""

    key: str
    label: str
    local: str
    server: str
    differs: bool
    taken: str
    keys: tuple[str, ...]
    choice: bool


def _structured(value: str) -> list[str]:
    """The parts of a name, a company or an address: split at each ";" that is no "\\;" (a semicolon inside a part), each part in words."""
    parts: list[str] = []
    current, escaped = "", False
    for character in value:
        if escaped:
            current += "\\" + character
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ";":
            parts.append(current)
            current = ""
        else:
            current += character
    parts.append(current + ("\\" if escaped else ""))
    return [_unescaped(part) for part in parts]


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
    """A line's parameters by name; every kind (each TYPE=, and a kind written without TYPE= as vCard 2.1 does) gathered in TYPE, as merge
    reads them."""
    found: dict[str, str] = {}
    kinds: list[str] = []
    for part in head.split(";")[1:]:
        name, equals, value = part.partition("=")
        if name.strip().upper() == "TYPE" or (name.strip() and not equals):
            kinds.extend(kind.strip() for kind in (value.strip().strip('"').split(",") if equals else [name]) if kind.strip())
        else:
            found[name.strip().upper()] = value.strip().strip('"')
    if kinds:
        found["TYPE"] = ",".join(dict.fromkeys(kinds))
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
    written = ""
    in_time = False
    for character in text[1:]:
        if character == "T":
            in_time = True
        elif character.isascii() and character.isdigit():
            written += character
        elif character in units and written:
            unit = "minute" if character == "M" and in_time else units[character]
            amount = number(written)
            if (character == "M" and not in_time) or amount is None:
                return "", False  # months are not a duration; a number too long to read is shown as written (OR-3)
            if amount:
                parts.append(counted(amount, unit))
            written = ""
        else:
            return "", False
    return (" ".join(parts) or "0 minutes"), negative


def _day_words(day: str) -> str:
    code = day[-2:].upper()
    name = DAYS.get(code, day)
    number = day[:-2]
    if len(number) <= 4 and digits(number[1:] if number[:1] in ("+", "-") else number):  # only 0 to 9, one sign at most: "²" or "+-1" is shown as written (OR-3)
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
    if unit is None or any(digits(parts.get(name, "")) and number(parts[name]) is None for name in ("INTERVAL", "COUNT")):
        return value  # a number too long to read: the rule as written (OR-3)
    interval = number(parts.get("INTERVAL", "")) or 1
    text = f"Every {interval} {unit}s" if interval > 1 else f"Every {unit}"
    if parts.get("BYDAY"):
        text += " on " + ", ".join(_day_words(day) for day in parts["BYDAY"].split(",") if day)
    if parts.get("BYMONTHDAY"):
        text += " on day " + ", ".join(parts["BYMONTHDAY"].split(","))
    if parts.get("BYMONTH"):
        months = [MONTHS[month - 1] for written in parts["BYMONTH"].split(",") if (month := number(written)) is not None and 1 <= month <= 12]
        text += " in " + ", ".join(months)
    count = number(parts.get("COUNT", ""))
    if count is not None:
        text += ", " + counted(count, "time")
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
    level = number(value)
    if level is None:
        return value.strip() if digits(value.strip()) else "None"  # a number too long to read: as written (OR-3)
    if level == 0:
        return "None"
    return "High" if level <= 4 else "Medium" if level == 5 else "Low"


def _birthday(value: str, parameters: dict[str, str], words: Words) -> str:
    text = value.strip()
    if text.startswith("--"):  # no year
        month_day = text[2:].replace("-", "")
        if len(month_day) == 4 and digits(month_day) and 1 <= int(month_day[:2]) <= 12:
            return f"{int(month_day[2:])} {MONTHS[int(month_day[:2]) - 1]}"
        return value
    return _when(text[:10], parameters, words)


def value_words(name: str, field: str, key: str, task: bool, words: Words) -> str:
    """One field's text (a whole line, a value of a comma list as a line of its own, or all reminders) in words."""
    if name == ALARMS:
        return reminder_words(field, task, words)
    head, value = _head_and_value(field)
    parameters = _parameters(head)
    if name in ("DTSTART", "DTEND", "DUE", "EXDATE", "RDATE"):
        return _when(value, parameters, words)
    if name in ("BDAY", "ANNIVERSARY"):
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
        parts = (_structured(value) + ["", "", "", "", ""])[:5]
        return " ".join(part for part in (parts[3], parts[1], parts[2], parts[0], parts[4]) if part) or ABSENT
    if name in ("ORG", "ADR"):
        return ", ".join(part for part in _structured(value) if part) or ABSENT
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


def _told_apart(name: str, local: str | None, server: str | None, row: Row) -> Row:
    """A choice whose two sides read the same in words gets what differs (D-308): another zone of a moment, the parameters that are not the
    same, or else the values as written; a photo stays "A photo" (never its raw text)."""
    if local is None or server is None or name in (ALARMS, "PHOTO") or "\n" in local or "\n" in server:
        return row
    (local_head, local_value), (server_head, server_value) = _head_and_value(local), _head_and_value(server)
    ours, theirs = _parameters(local_head), _parameters(server_head)
    if name == "ATTENDEE":  # an answer is not compared (D-308): never what tells them apart
        ours, theirs = ({key: value for key, value in found.items() if key not in ("PARTSTAT", "RSVP")} for found in (ours, theirs))
    mine, yours = row.local, row.server
    if name in MOMENTS and ours.get("TZID") != theirs.get("TZID"):
        mine += f" ({_zone_words(local_value, ours)})"
        yours += f" ({_zone_words(server_value, theirs)})"
    else:
        only_ours = sorted(f"{key}={value}" if value else key for key, value in ours.items() if theirs.get(key) != value)
        only_theirs = sorted(f"{key}={value}" if value else key for key, value in theirs.items() if ours.get(key) != value)
        mine += f" ({', '.join(only_ours)})" if only_ours else ""
        yours += f" ({', '.join(only_theirs)})" if only_theirs else ""
        if mine == yours and local_value.strip() != server_value.strip():
            mine, yours = f"{row.local} ({local_value.strip()})", f"{row.server} ({server_value.strip()})"
        elif mine == yours:  # only spaces differ: the values in quotes, so the person sees them
            mine, yours = f'{row.local} ("{local_value}")', f'{row.server} ("{server_value}")'
    return Row(row.key, row.label, mine, yours, row.differs, row.taken, row.keys, row.choice) if mine != yours else row


def _zone_words(value: str, parameters: dict[str, str]) -> str:
    return parameters.get("TZID") or ("UTC" if value.strip().upper().endswith("Z") else "no time zone")


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
            out[partner] = Row(ours.key, ours.label, ours.local, theirs.server, True, "", ours.keys + theirs.keys, True)
            out[position] = None
    return [row for row in out if row is not None]


def rows(base: str | None, local: str, server: str, words: Words) -> list[Row]:
    """Every field a person enters or sees, of the main item and each changed occurrence, with both versions in words. A field differs as merge
    reads it, and is the person's choice exactly where merge finds a clash (one notion of a difference, D-308); one that differs without a
    clash is taken by itself (`taken`). The entries of a list that differ are paired (_paired)."""
    base_units, local_units, server_units = units_together(base, local, server)  # their keys as merge reads them
    clashing = {clash.key for clash in merge(base, local, server).clashes}  # what the person chooses: exactly what merge asks
    if "item" in clashing:  # a text that cannot be read: the whole item is the one choice, as merge asks it
        return [Row("item", "The whole item", "Chrono Mail's version", "The server's version", True, "", ("item",), True)]
    found: list[Row] = []
    for unit_key in dict.fromkeys([*local_units, *server_units]):
        unit_name = (local_units.get(unit_key) or server_units[unit_key])[0]
        task = unit_name == "VTODO"
        ours = local_units[unit_key][1] if unit_key in local_units else dict[str, str]()
        theirs = server_units[unit_key][1] if unit_key in server_units else dict[str, str]()
        before = base_units[unit_key][1] if unit_key in base_units else None
        repeating = repeats(ours, theirs, before)  # another zone is another moment in an item that repeats, as merge reads it
        keys = [key for key in dict.fromkeys([*ours, *theirs]) if visible(unit_name, key)]
        keys.sort(key=lambda key: ORDER.index(key.split(SEPARATOR, 1)[0]) if key.split(SEPARATOR, 1)[0] in ORDER else len(ORDER))
        recurrence, copy = copy_of(unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else "")  # its start without " #2" (OR-2)
        occurrence = _when(recurrence, {}, words) + (f" ({copy})" if copy else "")
        suffix = f" (the occurrence of {occurrence})" if recurrence else f" ({copy})" if copy else ""
        alone = (unit_key in local_units) != (unit_key in server_units)  # an occurrence on one side only: kept or left out as a whole
        whole = f"unit{SEPARATOR}{unit_key}"
        if alone and whole in clashing:  # the person keeps it or leaves it out
            mine, yours = ("Kept", "Left out") if unit_key in local_units else ("Left out", "Kept")
            title = f"The occurrence of {occurrence}" if recurrence else "The whole item" + suffix
            found.append(Row(whole, title, mine, yours, True, "", (whole,), True))
        entries: list[tuple[Row, str, str, tuple[str, ...]]] = []  # each row with, for an entry of a list on one side only, that side and its kind
        written: dict[str, tuple[str, str | None, str | None]] = {}  # a choice's key: the field's name and both lines as written
        for key in keys:
            name = key.split(SEPARATOR, 1)[0]
            mine = value_words(name, ours[key], key, task, words) if key in ours else ABSENT
            yours = value_words(name, theirs[key], key, task, words) if key in theirs else ABSENT
            differs = not same(ours.get(key), theirs.get(key), repeating)  # as merge reads it: a photo, an answer, another zone (D-308)
            chosen = choice_key(key, unit_key)
            choice = differs and chosen in clashing
            taken = ""
            if differs and not choice:
                if alone and whole in clashing:  # its fate is the person's keep or leave out: nothing taken by itself
                    taken = ""
                elif alone:  # new on its side: taken; else removed with it
                    taken = (LOCAL if unit_key in local_units else SERVER) if before is None else (SERVER if unit_key in local_units else LOCAL)
                elif before is None:  # not known since when (a jar's entry): it stays where either side has it
                    taken = LOCAL if key in ours else SERVER
                elif jar(key) and key in before and (key not in ours or key not in theirs):  # taken out on a side: out (D-194, as merge)
                    taken = LOCAL if key not in ours else SERVER
                elif same(before.get(key), ours.get(key), repeating):
                    taken = SERVER
                elif same(before.get(key), theirs.get(key), repeating) or jar(key):
                    taken = LOCAL
            label = (TASK_LABELS.get(name) if task else None) or LABELS.get(name, name.title())
            side = "" if name not in PAIRED or (key in ours) == (key in theirs) else LOCAL if key in ours else SERVER
            written[chosen] = (name, ours.get(key), theirs.get(key))
            entries.append((Row(chosen, label + suffix, mine, yours, differs, taken, (chosen,), choice), side if choice else "", name,
                            _kind(name, ours.get(key) or theirs[key])))
        for row in _paired(entries):  # a choice that reads the same on both sides gets what differs (D-308)
            same_words = row.choice and row.local == row.server and row.keys[0] in written and row.keys[-1] in written
            found.append(_told_apart(written[row.keys[0]][0], written[row.keys[0]][1], written[row.keys[-1]][2], row) if same_words else row)
    return found
