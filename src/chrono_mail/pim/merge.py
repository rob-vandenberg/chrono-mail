"""Three versions of a calendar item or a contact compared field by field (D-194): the one both sides last agreed on, Chrono Mail's and the server's.
A field changed on one side only is taken from that side; the same field changed on both sides to the same value is no clash; changed differently
it is a clash, which the person settles (Settings > Sync conflicts). What a field is (the owner, 2026-10-20): each property; each changed occurrence
of a repeating event is an item of its own; lists (skipped dates, tags, phone numbers, e-mail addresses, attendees …) are merged entry by entry;
all alarms of an item together are one field; a text is one field; a property of another program is a field by its name and always kept; the time
stamps a program sets itself are not compared. Works on the text lines themselves, so nothing a program wrote is lost. Never raises."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from chrono_mail.core.words import digits as plain_digits
from chrono_mail.pim.times import zone_named
from chrono_mail.pim.vcard import fold, unfold

# --- Version ------------------------------------------------------------
__version__ = "merge.py 4.2.497"

# --- Version history ----------------------------------------------------
# v4.2.497: a jar's entry taken out on one side stays out, also when the other side only rewrote it;
#           changed on both sides is judged for the whole item, its occurrences together; without an agreed version an occurrence on one
#           side only is a choice; an attendee's answer is not compared; reminders by when and how everywhere; the same value written another
#           valid way (parameter order, a default VALUE, the names' capitals, the same moment in another zone) is the same, capitals in values
#           and "\;" in a name, company or address count; Role and Anniversary are visible; one value with two kinds stays twice; a comma
#           list's entry is shown whole (the owner); an occurrence on one side only is unchanged by what a person sees, as it reads; a line
#           of an attendee taken from here keeps the server's answer; another zone is the same moment only in an item that does not repeat;
#           kinds (TYPE=work,cell) read the same in any order and capitals; two lines of one version with the same value and kinds both stay;
#           a reminder's time by its length (-PT30M is -P0DT0H30M0S) or its moment; a date with hyphens reads as without them; whether a unit
#           repeats is decided once for all three versions; two changed occurrences of one start in one version both stay; a parameter
#           that names one of a few settings (ROLE=chair) reads the same in any capitals; a reminder without ACTION is a message on screen;
#           the second copy of a changed occurrence, or of the item itself, is named so in its label; a line that starts with the format's
#           own separator never stops a merge; an empty value (N:;;;;, NOTE:) reads as no line; a copy's mark is one no value can carry
# v4.2.496: group members and tags are a jar: what each side put in or took out stays, never a conflict (the owner); a strange digit in a
#           changed occurrence's date never raises (core.words.digits)
# v4.2.490: fields are compared as they read: the format's backslashes (\\,) taken out first (the owner)
# v4.2.481: Only what a person enters or sees can clash (D-308): any other field, and reminders that differ only in what is not shown, take
#           the server's version; changed on both sides, every visible difference is a choice; a changed occurrence's fields are chosen apart from the main event's; the occurrence in a label in words
# v1.10.277: A group's members and kind have labels for Sync conflicts (R-39)
# v1.1.225: Initial: the three-way comparison field by field of an iCalendar item or a vCard (R-23, R-24, D-194)

LOCAL: str = "local"
SERVER: str = "server"
STAMPS: frozenset[str] = frozenset({"DTSTAMP", "LAST-MODIFIED", "SEQUENCE", "REV", "PRODID", "X-MOZ-GENERATION"})  # set by programs: never compared
COMMA_LISTS: frozenset[str] = frozenset({"CATEGORIES", "EXDATE", "RDATE", "RESOURCES"})  # several values on one line, merged value by value
LISTS: frozenset[str] = frozenset({
    "ATTENDEE", "TEL", "EMAIL", "ADR", "URL", "IMPP", "RELATED", "MEMBER", "X-ADDRESSBOOKSERVER-MEMBER", "ATTACH", "COMMENT", "CONTACT", "NICKNAME",
})  # several lines of one name, merged line by line (an entry is known by its value)
UNITS: frozenset[str] = frozenset({"VEVENT", "VTODO", "VJOURNAL", "VCARD"})
JARS: frozenset[str] = frozenset({"MEMBER", "X-ADDRESSBOOKSERVER-MEMBER", "CATEGORIES"})  # a group's members, tags: each entry by itself (the owner)
ALARMS: str = "VALARM"
SEPARATOR: str = "\x1f"
LABELS: dict[str, str] = {
    "SUMMARY": "Title", "LOCATION": "Location", "DESCRIPTION": "Description", "DTSTART": "Starts", "DTEND": "Ends", "DUE": "Due", "DURATION": "Duration",
    "RRULE": "Repeat", "EXDATE": "Skipped date", "RDATE": "Extra date", "CATEGORIES": "Tag", "ATTENDEE": "Attendee", "ORGANIZER": "Organizer",
    "STATUS": "Status", "PRIORITY": "Priority", "PERCENT-COMPLETE": "Done (%)", "COMPLETED": "Completed", "TRANSP": "Show as", "CLASS": "Private",
    "COLOR": "Colour (other programs)", "X-CHRONO-MAIL-COLOR-ID": "Colour", "URL": "Web address", "VALARM": "Reminder",
    "FN": "Name", "N": "Name parts", "ORG": "Company", "TITLE": "Job title", "TEL": "Phone", "EMAIL": "E-mail", "ADR": "Address", "BDAY": "Birthday",
    "NOTE": "Note", "NICKNAME": "Nickname", "PHOTO": "Photo", "ROLE": "Role", "MEMBER": "Group member", "X-ADDRESSBOOKSERVER-MEMBER": "Group member",
    "KIND": "Group", "X-ADDRESSBOOKSERVER-KIND": "Group", "ANNIVERSARY": "Anniversary", "item": "The whole item",
}
VISIBLE: dict[str, frozenset[str]] = {  # what Chrono Mail's own windows let a person enter or see: only these can clash (D-308)
    "VEVENT": frozenset({
        "SUMMARY", "X-CHRONO-MAIL-COLOR-ID", "LOCATION", "DTSTART", "DTEND", "DURATION", "RRULE", "EXDATE", "RDATE", ALARMS, "TRANSP", "ATTENDEE",
        "CATEGORIES", "DESCRIPTION",
    }),
    "VTODO": frozenset({"SUMMARY", "PRIORITY", "STATUS", "PERCENT-COMPLETE", "DTSTART", "DUE", "RRULE", ALARMS, "LOCATION", "CATEGORIES", "DESCRIPTION"}),
    "VCARD": frozenset({
        "N", "FN", "NICKNAME", "ORG", "TITLE", "ROLE", "EMAIL", "TEL", "URL", "ADR", "BDAY", "ANNIVERSARY", "NOTE", "CATEGORIES", "PHOTO", "MEMBER",
        "X-ADDRESSBOOKSERVER-MEMBER",
    }),
}
# a moment: the same one in another zone is the same, only in a unit that does not repeat (there a zone moves later occurrences at summer time)
ZONED: frozenset[str] = frozenset({"DTSTART", "DTEND", "DUE", "RECURRENCE-ID", "EXDATE", "RDATE"})
REPEATS: frozenset[str] = frozenset({"RRULE", "RDATE"})  # a unit with one of these repeats
COPY_MARK: str = SEPARATOR + "#"  # after a unit's key: its second copy (#2), a mark no value read into a key can carry (OR-2)
LONGEST_NUMBER: int = 9  # digits of a number read from a calendar or a card: a longer one is shown as written (OR-3)
DURATION_UNITS: dict[str, int] = {"W": 604800, "D": 86400, "H": 3600, "M": 60, "S": 1}  # seconds in each part of a duration
DATED: frozenset[str] = ZONED | {"COMPLETED", "BDAY", "ANNIVERSARY"}  # a date or a moment: VALUE=DATE-TIME, or VALUE=DATE of a date, is as without it
STRUCTURED: frozenset[str] = frozenset({"N", "ORG", "ADR"})  # parts split by ";": a "\;" inside a part is another value than a ";" between parts
ANSWERS: frozenset[str] = frozenset({"PARTSTAT", "RSVP"})  # an attendee's answer: never compared, the server's taken (D-308)
SETTINGS: frozenset[str] = frozenset({  # parameters whose value is one of a few settings: the same in any capitals (free text such as CN is not)
    "ROLE", "CUTYPE", "PARTSTAT", "RSVP", "VALUE", "RELATED", "ENCODING", "FBTYPE", "RANGE", "RELTYPE", "SCHEDULE-AGENT", "SCHEDULE-FORCE-SEND",
    "DISPLAY", "FEATURE",
})
PART_MARK: str = "\x1e"  # in a field as it reads: between the parts of a structured value, and between a line's name and its parameters
VALUE_MARK: str = "\x1d"  # in a field as it reads: between a line's name with its parameters and its value
MONTHS: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


@dataclass(frozen=True, slots=True)
class Clash:
    """One field changed differently on both sides: its key (what a choice names), a label for the person, and both values as text ("" = absent)."""

    key: str
    label: str
    local: str
    server: str


@dataclass(frozen=True, slots=True)
class Merged:
    """The outcome: the merged text when nothing clashes (or every clash was chosen), else None and the clashes."""

    text: str | None
    clashes: tuple[Clash, ...] = ()


@dataclass(slots=True)
class _Component:
    name: str
    lines: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])  # (property name, the whole line), in order
    children: list["_Component"] = field(default_factory=list["_Component"])


def _parse(text: str) -> _Component | None:
    """The components of a text (BEGIN … END nested), or None when it is not one."""
    root = _Component("")
    stack = [root]
    for line in unfold(text):
        if not line.strip():
            continue
        name = _name(line)
        value = line.partition(":")[2].strip().upper()
        if name == "BEGIN":
            child = _Component(value)
            stack[-1].children.append(child)
            stack.append(child)
        elif name == "END":
            if len(stack) == 1 or stack[-1].name != value:
                return None
            stack.pop()
        else:
            stack[-1].lines.append((name, line))
    return root if len(stack) == 1 and root.children else None


def _name(line: str) -> str:
    head = line.split(":", 1)[0]
    return head.split(";", 1)[0].strip().upper()


def _head_and_value(line: str) -> tuple[str, str]:
    """A line's name with its parameters, and its value. The parameters may hold a quoted ':' (a quoted value), the value never starts inside quotes."""
    quoted = False
    for position, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            return line[:position], line[position + 1 :]
    return line, ""


def _split_commas(value: str) -> list[str]:
    parts: list[str] = []
    current = ""
    escaped = False
    for character in value:
        if escaped:
            current += character
            escaped = False
        elif character == "\\":
            current += character
            escaped = True
        elif character == ",":
            parts.append(current)
            current = ""
        else:
            current += character
    parts.append(current)
    return [part for part in parts if part.strip()]


def _serialise(component: _Component) -> list[str]:
    out = [f"BEGIN:{component.name}"]
    out.extend(line for _, line in component.lines)
    for child in component.children:
        out.extend(_serialise(child))
    out.append(f"END:{component.name}")
    return out


def _unit_key(component: _Component) -> str:
    """A unit's key: its name and, of a changed occurrence, its original start as it reads (the same moment in another zone is the same one)."""
    recurrence = next((line for name, line in component.lines if name == "RECURRENCE-ID"), "")
    start = _reading(recurrence)[1].strip().replace(SEPARATOR, "") if recurrence else ""  # never the separator of a copy's mark
    return f"{component.name}{SEPARATOR}{start}"


def _written_key(component: _Component) -> str:
    """A unit's key with the original start as it is written (for its label: the time in the occurrence's own zone)."""
    recurrence = next((line for name, line in component.lines if name == "RECURRENCE-ID"), "")
    return f"{component.name}{SEPARATOR}{_head_and_value(recurrence)[1].strip().replace(SEPARATOR, '') if recurrence else ''}"


def _pieces(head: str) -> list[str]:
    """A line's name and its parameters, split at each ";" outside quotes."""
    pieces: list[str] = []
    current = ""
    quoted = False
    for character in head:
        if character == '"':
            quoted = not quoted
        if character == ";" and not quoted:
            pieces.append(current)
            current = ""
        else:
            current += character
    pieces.append(current)
    return pieces


def _moment(text: str, zone: str) -> datetime | None:
    """A moment with a zone (20261028T090000Z, or 20261028T100000 in the zone named) as UTC; None when it has none or cannot be read."""
    value = text.strip()
    utc = value[-1:] in ("Z", "z")
    if len(value) != (16 if utc else 15) or value[8] != "T" or not plain_digits(value[:8]) or not plain_digits(value[9:15]) or not (utc or zone):
        return None
    try:
        moment = datetime(int(value[:4]), int(value[4:6]), int(value[6:8]), int(value[9:11]), int(value[11:13]), int(value[13:15]),
                          tzinfo=UTC if utc else zone_named(zone))
        return moment.astimezone(UTC)
    except (ValueError, OverflowError):  # a day or a zone that does not exist: as written
        return None


def _reading(line: str, repeating: bool = False) -> tuple[str, str]:
    """A line as it reads: its name and parameters (the names in capitals, in order, an attendee's answer and a default VALUE left out, quotes
    taken off) and its value (the format's backslashes taken out; a moment with a zone as UTC). Another valid way of writing the same reads the
    same (the owner); capitals in a value count, except in kinds (TYPE=work,cell), which read as the entry's key has them. `repeating`: the line
    is of a unit that repeats, where a moment in another zone is another moment."""
    head, value = _head_and_value(line)
    name, *pieces = _pieces(head)
    name = name.strip().upper()
    parameters: list[tuple[str, str]] = []
    kinds: set[str] = set()
    for piece in pieces:
        key, equals, setting = piece.partition("=")
        key = key.strip().upper()
        if key == "TYPE" or (key and not equals):  # kinds, also without TYPE= (vCard 2.1): in any order and capitals, as _entry_key reads them
            kinds.update(kind.strip().lower() for kind in (setting.replace('"', "").split(",") if equals else [key]) if kind.strip())
        elif key and not (name == "ATTENDEE" and key in ANSWERS):
            setting = setting.strip().replace('"', "")
            parameters.append((key, setting.upper() if key in SETTINGS else setting))
    if kinds:
        parameters.append(("TYPE", ",".join(sorted(kinds))))
    if name in DATED:  # 1980-01-01 is 19800101, 2026-10-28T10:00:00 is 20261028T100000 (a text such as "circa 1980" as written)
        value = ",".join(part.replace("-", "").replace(":", "") if part.strip() and set(part.strip()) <= set("0123456789-:TZtz+") else part
                         for part in value.split(","))
    if name in ZONED and not repeating:
        zone = next((setting for key, setting in parameters if key == "TZID"), "")
        moments = [_moment(part, zone) for part in value.split(",")]
        if moments and all(moment is not None for moment in moments) and (zone or value.strip()[-1:] in ("Z", "z")):
            value = ",".join(moment.strftime("%Y%m%dT%H%M%SZ") for moment in moments if moment is not None)
            parameters = [(key, setting) for key, setting in parameters if key != "TZID"]
    if name in DATED:
        dates = all(len(part.strip().replace("-", "")) == 8 and plain_digits(part.strip().replace("-", "")) for part in value.split(","))
        parameters = [(key, setting) for key, setting in parameters
                      if not (key == "VALUE" and (setting.upper() == "DATE-TIME" or (setting.upper() == "DATE" and dates)))]
    plain, escaped = "", False
    for character in value:
        if escaped:
            plain += "\n" if character in "nN" else character
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ";" and name in STRUCTURED:
            plain += PART_MARK  # between two parts: never the same as a ";" inside one
        else:
            plain += character
    return PART_MARK.join([name, *(f"{key}={setting}" for key, setting in sorted(parameters))]), plain


def _entry_key(name: str, head: str, value: str) -> str:
    """The key of an entry of a list: its value, and its kinds (work, cell) when it has any, so one value of two kinds is two entries."""
    kinds: set[str] = set()
    for piece in _pieces(head)[1:]:
        key, equals, setting = piece.partition("=")
        if not equals:
            kinds.add(key.strip().lower())  # a kind written without TYPE= (vCard 2.1)
        elif key.strip().upper() == "TYPE":
            kinds.update(kind.strip().lower() for kind in setting.replace('"', "").split(",") if kind.strip())
    entry = f"{name}{SEPARATOR}{value.strip().lower()}"
    return f"{entry}{SEPARATOR}{','.join(sorted(kinds))}" if kinds else entry


def _repeats(unit: _Component | None) -> bool:
    return unit is not None and any(name in REPEATS for name, _ in unit.lines)


def _fields(unit: _Component, repeating: bool | None = None) -> dict[str, str]:
    """A unit's fields: key -> the text that stands for it (a whole line, a value of a comma list, or all alarms together). `repeating`: whether
    the unit repeats in any of its versions (decided once for all three, so their keys agree); None: by its own lines. Two lines of one list
    with the same key are told apart by how their parameters read, then their value as written, then their place: no line is ever lost."""
    counts: dict[str, int] = {}
    for name, _ in unit.lines:
        counts[name] = counts.get(name, 0) + 1
    found: dict[str, str] = {}
    if repeating is None:
        repeating = _repeats(unit)
    entries: dict[str, list[str]] = {}
    for name, line in unit.lines:
        if name not in STAMPS and name not in COMMA_LISTS and (name in LISTS or counts[name] > 1):
            entries.setdefault(_entry_key(name, *_head_and_value(line)), []).append(line)
    seen_keys: dict[str, int] = {}
    for name, line in unit.lines:
        if name in STAMPS:
            continue
        head, value = _head_and_value(line)
        if name in COMMA_LISTS:  # each value a line of its own: known by how it reads, so a head written another way is the same entry
            for part in _split_commas(value):
                entry = f"{head}:{part.strip()}"
                heading, reads = _reading(entry, repeating)
                found[f"{name}{SEPARATOR}{heading}{SEPARATOR}{reads}"] = entry
        elif name in LISTS or counts[name] > 1:
            key = _entry_key(name, head, value)
            if len(entries[key]) > 1:  # another line of this version has the same key: told apart, never one of them lost (OR-2)
                key = f"{key}{SEPARATOR}{_reading(line)[0]}{SEPARATOR}{value.strip()}"  # an attendee's answer left out
                seen_keys[key] = seen_keys.get(key, 0) + 1
                if seen_keys[key] > 1:
                    key = f"{key}{SEPARATOR}{seen_keys[key]}"
            found[key] = line
        else:
            found[name] = line
    alarms = [child for child in unit.children if child.name == ALARMS]
    if alarms:
        found[ALARMS] = "\n".join("\n".join(_serialise(alarm)) for alarm in alarms)
    others = [child for child in unit.children if child.name != ALARMS]
    for child in others:  # rare (a VLOCATION, for one): kept whole, as one field each
        found[f"{child.name}{SEPARATOR}" + "\n".join(_serialise(child))] = "\n".join(_serialise(child))
    return found


def occurrence_words(value: str) -> str:
    """The original start of a changed occurrence as a person reads it ("12 August 2026", "12 August 2026, 09:00"); as written when unreadable."""
    digits = value.strip()
    if len(digits) < 8 or not plain_digits(digits[:8]) or not 1 <= int(digits[4:6]) <= 12:  # only 0 to 9: "²" is no digit here (OR-3)
        return value
    words = f"{int(digits[6:8])} {MONTHS[int(digits[4:6]) - 1]} {digits[:4]}"
    if len(digits) >= 13 and digits[8] == "T" and plain_digits(digits[9:13]):
        words += f", {digits[9:11]}:{digits[11:13]}"
    return words


COPIES: dict[int, str] = {2: "second copy", 3: "third copy", 4: "fourth copy"}


def copy_of(recurrence: str) -> tuple[str, str]:
    """A changed occurrence's original start without the mark of a second copy of it (COPY_MARK, _units), and that copy in words ("" for the
    first)."""
    start, mark, count = recurrence.rpartition(COPY_MARK)
    if not mark or not plain_digits(count) or len(count) > LONGEST_NUMBER:
        return recurrence, ""
    return start, COPIES.get(int(count), f"copy {count}")


def _label(key: str, unit_key: str) -> str:
    name = key.split(SEPARATOR, 1)[0]
    words = LABELS.get(name, name.title() if not name.startswith("X-") else name)
    recurrence, copy = copy_of(unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else "")
    copy_words = f" ({copy})" if copy else ""  # two changed occurrences of one start, or two of the item itself: told apart (OR-2)
    return f"{words} (the occurrence of {occurrence_words(recurrence)}{copy_words})" if recurrence else words + copy_words


def choice_key(key: str, unit_key: str) -> str:
    """What a choice names: the field's key, of a changed occurrence with its original start, so it is chosen apart from the main event's."""
    recurrence = unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else ""
    return f"{key}{SEPARATOR}@{recurrence}" if recurrence else key


def visible(unit_name: str, key: str) -> bool:
    """Whether a field is something a person enters or sees in Chrono Mail's own windows (D-308)."""
    return key.split(SEPARATOR, 1)[0] in VISIBLE.get(unit_name, frozenset[str]())


def reminders_seen(text: str | None) -> tuple[tuple[str, str], ...]:
    """An item's reminders as a person sees them: when each goes off and how; their ids and other programs' markers left out."""
    found: list[tuple[str, str]] = []
    trigger, action = "", ""
    for line in (text or "").split("\n"):
        name = _name(line)
        head, value = _head_and_value(line)
        if name == "BEGIN":
            trigger, action = "", ""
        elif name == "TRIGGER":
            parameters = sorted(part.strip().upper() for part in head.split(";")[1:]
                                if part.strip().upper() not in ("VALUE=DURATION", "RELATED=START", "VALUE=DATE-TIME"))
            trigger = ";".join([*parameters, _trigger_reads(value)])
        elif name == "ACTION":
            action = value.strip().upper()
        elif name == "END":
            found.append((trigger, action or "DISPLAY"))  # without ACTION: a message on screen, as Chrono Mail shows it
    return tuple(sorted(found))


def number(text: str) -> int | None:
    """A number written in a calendar or a card: only the digits 0 to 9, at most LONGEST_NUMBER of them; None otherwise (shown as written, OR-3)."""
    value = text.strip()
    return int(value) if plain_digits(value) and len(value) <= LONGEST_NUMBER else None


def seconds_of(text: str) -> int | None:
    """An ISO duration (-PT30M, P0DT0H30M0S, P1W) as its length in seconds; None when it cannot be read."""
    value = text.strip().upper()
    sign = -1 if value.startswith("-") else 1
    value = value.lstrip("+-")
    if not value.startswith("P") or len(value) < 3:
        return None
    total, digits, in_time = 0, "", False
    for character in value[1:]:
        if character == "T" and not in_time and not digits:
            in_time = True
        elif character.isascii() and character.isdigit():
            digits += character
        elif character in DURATION_UNITS and digits and (in_time or character in "WD") and (not in_time or character in "HMS"):
            amount = number(digits)
            if amount is None:
                return None
            total += amount * DURATION_UNITS[character]
            digits = ""
        else:
            return None
    return sign * total if not digits else None


def _trigger_reads(value: str) -> str:
    """When a reminder goes off as it reads: a duration by its length, a moment as UTC; else as written."""
    length = seconds_of(value)
    if length is not None:
        return f"{length}s"
    moment = _moment(value.replace("-", "").replace(":", ""), "")
    return moment.strftime("%Y%m%dT%H%M%SZ") if moment is not None else value.strip().upper()


def _said(text: str | None, repeating: bool = False) -> str | None:
    """A field as it reads (_reading), so another valid way of writing the same is no difference (the owner, 4.2.490); all reminders together
    by when and how (reminders_seen); another part of an item (a VLOCATION) as it is."""
    if text is not None and text.startswith(f"BEGIN:{ALARMS}"):
        return "\n".join(PART_MARK.join(reminder) for reminder in reminders_seen(text))
    if text is None or "\n" in text or ":" not in text:  # a text that is no line: as it is
        return text
    heading, value = _reading(text, repeating)
    if not value.replace(PART_MARK, "").strip():  # an empty value (N:;;;;, NOTE:) reads as no line at all
        return None
    return VALUE_MARK.join((heading, value))


def same(first: str | None, second: str | None, repeating: bool = False) -> bool:
    """Whether two versions of a field read the same: the one notion of a difference, here and in the window (D-308). `repeating`: of a unit
    that repeats (repeats), where another zone is another moment."""
    return _said(first, repeating) == _said(second, repeating)


def repeats(*fields: Mapping[str, str] | None) -> bool:
    """Whether a unit repeats (a repeat rule or extra dates) in any of its versions."""
    return any(key.split(SEPARATOR, 1)[0] in REPEATS for version in fields if version is not None for key in version)


def jar(key: str) -> bool:
    """Whether a field is an entry of a jar (a group's member, a tag): put in or taken out by itself, never compared with the others (the owner)."""
    return key.split(SEPARATOR, 1)[0] in JARS


def _seen(unit_name: str, fields: Mapping[str, str] | None, repeating: bool) -> dict[str, str | None] | None:
    """A unit's fields as a person sees them: only those Chrono Mail's windows show, as they read; the entries of a jar left out (what each side
    put in or took out is no change that clashes). None for a unit that is not there."""
    if fields is None:
        return None
    said = {key: _said(value, repeating) for key, value in fields.items() if visible(unit_name, key) and not jar(key)}
    return {key: value for key, value in said.items() if value is not None}  # an empty value is as no line


def _jarred(before: str | None, local: str | None, server: str | None, known: bool, repeating: bool) -> str | None:
    """An entry of a jar: what the side that changed it did; not known since when, it stays where either side has it (nothing is lost)."""
    if same(local, server, repeating):
        return local
    if not known:
        return local if local is not None else server
    if before is not None and (local is None or server is None):  # taken out on one side: out, also when the other only rewrote it (D-194)
        return None
    if same(local, before, repeating):
        return server
    return local


def _quietly(before: str | None, local: str | None, server: str | None, known: bool, repeating: bool) -> str | None:
    """A field not seen: the side that changed it, and where both did, the server's (D-193, D-308)."""
    if known and same(local, before, repeating):
        return server
    if known and same(server, before, repeating):
        return local
    return server


def _shown(text: str | None) -> str:
    """A field's text as the person reads it: the value of a line ("" when absent)."""
    if text is None:
        return ""
    if "\n" in text:  # alarms: their triggers
        return "; ".join(_head_and_value(line)[1] for line in text.split("\n") if _name(line) == "TRIGGER") or "a reminder"
    return _head_and_value(text)[1] if ":" in text and not text.startswith(":") else text


def _choose(key: str, base: str | None, local: str | None, server: str | None, choices: Mapping[str, str], clashes: list[Clash], label: str,
            repeating: bool = False) -> str | None:
    if same(local, server, repeating):
        return server  # what reads the same: the server's way of writing it (D-308)
    if choices.get(key) in (LOCAL, SERVER):  # the person's choice in the window counts for every field that differs (D-308)
        return local if choices.get(key) == LOCAL else server
    if same(local, base, repeating):
        return server
    if same(server, base, repeating):
        return local
    choice = choices.get(key)
    if choice == LOCAL:
        return local
    if choice == SERVER:
        return server
    clashes.append(Clash(key, label, _shown(local), _shown(server)))
    return local


def _answered(line: str, server: str) -> str:
    """An attendee's line taken from here with the server's answer in it (PARTSTAT, RSVP): the server's answer is taken (D-308)."""
    head, value = _head_and_value(line)
    name, *pieces = _pieces(head)
    ours = [piece for piece in pieces if piece.partition("=")[0].strip().upper() not in ANSWERS]
    theirs = [piece for piece in _pieces(_head_and_value(server)[0])[1:] if piece.partition("=")[0].strip().upper() in ANSWERS]
    return ";".join([name, *ours, *theirs]) + ":" + value


def _rebuild(name: str, fields: dict[str, str | None], order: list[str], stamps: list[str], children: list[str]) -> list[str]:
    """A unit written again from its merged fields: in Chrono Mail's order, then what only the server had; a comma list again on one line per head."""
    out = [f"BEGIN:{name}"]
    out.extend(stamps)
    grouped: dict[str, list[str]] = {}
    places: dict[str, int] = {}  # where each comma list's line goes: by its place, never a mark an input line could carry too (OR-3)
    emitted: set[str] = set()
    for key in order:
        value = fields.get(key)
        if value is None or key in emitted:
            continue
        emitted.add(key)
        parts = key.split(SEPARATOR)
        if parts[0] in COMMA_LISTS and len(parts) == 3:  # a value of a comma list: back on the line of its head as written
            head, value = _head_and_value(value)
            if head not in grouped:
                grouped[head] = []
                places[head] = len(out)
                out.append("")  # where the line goes; filled below
            grouped[head].append(value)
        elif parts[0] == ALARMS or (len(parts) > 1 and "\n" in (value or "") and parts[0] not in COMMA_LISTS):
            children.append(value)
        else:
            out.append(value)
    for head, place in places.items():
        out[place] = f"{head}:{','.join(grouped[head])}"
    for child in children:
        out.extend(child.split("\n"))
    out.append(f"END:{name}")
    return out


def _merge_unit(unit_key: str, base: _Component | None, local: _Component | None, server: _Component | None, known: bool, both: bool,
                choices: Mapping[str, str], clashes: list[Clash]) -> list[str] | None:
    """One unit (an event, a changed occurrence, a task, a contact) merged: its lines, or None when it is gone. `known`: the agreed version of the
    item is known; `both`: the item's visible fields changed here and on the server, so every visible difference is the person's (D-194)."""
    unit_choice = choices.get(f"unit{SEPARATOR}{unit_key}")
    some = local if local is not None else server if server is not None else base
    written = _written_key(some) if some is not None else unit_key  # its label: the start as it is written, and which copy of it
    if copy_of(unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else "")[1]:
        written += COPY_MARK + unit_key.rpartition(COPY_MARK)[2]
    if local is None or server is None:
        present = local if local is not None else server
        assert present is not None
        repeating = _repeats(present) or _repeats(base)  # decided once for the unit's versions
        present_fields, base_fields = _fields(present, repeating), _fields(base, repeating) if base is not None else None
        unchanged = _seen(present.name, present_fields, repeating) == _seen(present.name, base_fields, repeating)  # as a person sees it (D-308)
        if base is None and known and not both:
            return _serialise(present)  # new on one side: taken
        if base is not None and not both and unchanged:
            return None  # removed on the other side, unchanged here as it reads: removed
        side = LOCAL if local is not None else SERVER
        if unit_choice in (LOCAL, SERVER):
            return _serialise(present) if unit_choice == side else None
        if base is None:  # not known since when, or new on one side of an item changed on both: the person keeps it or leaves it out
            what = "only here" if local is not None else "only on the server"
        elif unchanged:
            what = "removed on the server" if local is not None else "removed here"
        else:
            what = "removed on the server, changed here" if local is not None else "removed here, changed on the server"
        clashes.append(Clash(f"unit{SEPARATOR}{unit_key}", _label("item", written) + f" ({what})", "kept" if local is not None else "removed",
                             "kept" if server is not None else "removed"))
        return _serialise(local) if local is not None else None
    repeating = _repeats(local) or _repeats(server) or _repeats(base)  # once for all three: another zone is another moment in a unit that repeats
    base_fields = _fields(base, repeating) if base is not None else None
    local_fields, server_fields = _fields(local, repeating), _fields(server, repeating)
    merged: dict[str, str | None] = {}
    for key in dict.fromkeys([*local_fields, *server_fields, *(base_fields or {})]):
        before = base_fields.get(key) if base_fields is not None else None
        if base_fields is None and key not in local_fields and key not in server_fields:
            continue
        if same(local_fields.get(key), server_fields.get(key), repeating):  # reads the same (an attendee's answer, another way of writing): the server's
            merged[key] = server_fields.get(key) if key in server_fields else local_fields.get(key)
            continue
        if not visible(local.name, key):
            merged[key] = _quietly(before, local_fields.get(key), server_fields.get(key), base_fields is not None, repeating)
            continue
        if jar(key):  # put in or taken out on one side: so it is (the owner)
            merged[key] = _jarred(before, local_fields.get(key), server_fields.get(key), base_fields is not None, repeating)
            continue
        named = choice_key(key, unit_key)
        if (base_fields is None or both) and not same(local_fields.get(key), server_fields.get(key), repeating):  # every difference is the person's choice
            choice = choices.get(named)
            if choice in (LOCAL, SERVER):
                merged[key] = local_fields.get(key) if choice == LOCAL else server_fields.get(key)
            else:
                clashes.append(Clash(named, _label(key, written), _shown(local_fields.get(key)), _shown(server_fields.get(key))))
                merged[key] = local_fields.get(key)
            continue
        merged[key] = _choose(named, before, local_fields.get(key), server_fields.get(key), choices, clashes, _label(key, written), repeating)
    for key, value in merged.items():  # an attendee's line taken from here: with the server's answer (D-308)
        theirs = server_fields.get(key)
        if key.split(SEPARATOR, 1)[0] == "ATTENDEE" and value is not None and theirs is not None and value == local_fields.get(key):
            merged[key] = _answered(value, theirs)
    order = [*local_fields, *(key for key in server_fields if key not in local_fields)]
    stamps = [line for name, line in local.lines if name in STAMPS]
    return _rebuild(local.name, merged, order, stamps, [])


def _changed(keys: list[str], base: dict[str, _Component], side: dict[str, _Component], repeating_units: Mapping[str, bool]) -> bool:
    """Whether a side changed what a person sees of the item since the agreed version: in its main unit or any occurrence, one put in or taken out
    too."""
    for key in keys:
        name = (side.get(key) or base[key]).name if key in side or key in base else ""
        before, now = base.get(key), side.get(key)
        repeating = repeating_units.get(key, False)
        before_fields = _fields(before, repeating) if before is not None else None
        now_fields = _fields(now, repeating) if now is not None else None
        if _seen(name, before_fields, repeating) != _seen(name, now_fields, repeating):
            return True
    return False


def units_together(*texts: str | None) -> list[dict[str, tuple[str, dict[str, str]]]]:
    """The units of the versions of one item (units_of), whether a unit repeats decided once for all of them, as merge reads them."""
    roots = [_parse(text) if text else None for text in texts]
    parsed = [_units(root)[1] if root is not None else dict[str, _Component]() for root in roots]
    repeating = {key: any(_repeats(units.get(key)) for units in parsed) for units in parsed for key in units}
    return [{key: (unit.name, _fields(unit, repeating[key])) for key, unit in units.items()} for units in parsed]


def units_of(text: str | None) -> dict[str, tuple[str, dict[str, str]]]:
    """An item's units by key (the main one, each changed occurrence): its name and its fields, as compared here; empty when unreadable."""
    root = _parse(text) if text else None
    if root is None:
        return {}
    return {key: (unit.name, _fields(unit)) for key, unit in _units(root)[1].items()}


def _units(root: _Component) -> tuple[list[str], dict[str, _Component], list[_Component]]:
    """(the wrapper's own lines, the units by key, the time zones) of a parsed text."""
    wrapper = root.children[0]
    if wrapper.name in UNITS:  # a vCard has no wrapper
        return [], {_unit_key(wrapper): wrapper}, []
    units: dict[str, _Component] = {}
    for child in wrapper.children:
        if child.name in UNITS:
            key, count = _unit_key(child), 1
            while (f"{key}{COPY_MARK}{count}" if count > 1 else key) in units:  # one start written twice (or in two zones): both stay (OR-2)
                count += 1
            units[f"{key}{COPY_MARK}{count}" if count > 1 else key] = child
    zones = [child for child in wrapper.children if child.name not in UNITS]
    return [line for _, line in wrapper.lines], units, zones


def merge(base: str | None, local: str, server: str, choices: Mapping[str, str] | None = None) -> Merged:
    """Merge Chrono Mail's and the server's version of one item against the version both last agreed on (None: not known, so every difference is a
    clash). `choices` settle clashes by their key: LOCAL or SERVER. The merged text, or the clashes still open."""
    wanted = choices or {}
    clashes: list[Clash] = []
    local_root, server_root = _parse(local), _parse(server)
    base_root = _parse(base) if base is not None else None
    if base is not None and base_root is None and local_root is not None and server_root is not None:
        base = None  # an agreed version that cannot be read, of two that can: none, every difference the person's choice
    if local_root is None or server_root is None:  # not readable: the whole item is one field
        if local == server:
            return Merged(local)
        chosen = _choose("item", base, local, server, wanted, clashes, LABELS["item"])
        return Merged(chosen if not clashes else None, tuple(clashes))
    local_wrapper, local_units, local_zones = _units(local_root)
    _, server_units, server_zones = _units(server_root)
    base_units: dict[str, _Component] = _units(base_root)[1] if base_root is not None else {}
    known = base_root is not None
    keys = list(dict.fromkeys([*local_units, *server_units, *base_units]))
    repeating = {key: any(_repeats(units.get(key)) for units in (base_units, local_units, server_units)) for key in keys}
    both = known and _changed(keys, base_units, local_units, repeating) and _changed(keys, base_units, server_units, repeating)  # (D-194)
    lines: list[str] = []
    for unit_key in keys:
        if unit_key not in local_units and unit_key not in server_units:
            continue
        merged = _merge_unit(
            unit_key, base_units.get(unit_key) if known else None, local_units.get(unit_key), server_units.get(unit_key), known, both, wanted, clashes,
        )
        if merged is not None:
            lines.extend(merged)
    if clashes:
        return Merged(None, tuple(clashes))
    if local_root.children[0].name not in UNITS:
        zone_ids = {next((line for name, line in zone.lines if name == "TZID"), "") for zone in local_zones}
        zones = [*local_zones, *(zone for zone in server_zones if next((line for name, line in zone.lines if name == "TZID"), "") not in zone_ids)]
        wrapper_name = local_root.children[0].name
        lines = [f"BEGIN:{wrapper_name}", *local_wrapper, *(line for zone in zones for line in _serialise(zone)), *lines, f"END:{wrapper_name}"]
    return Merged("\r\n".join(fold(line) for line in lines) + "\r\n")
