"""Three versions of a calendar item or a contact compared field by field (D-194): the one both sides last agreed on, Chrono Mail's and the server's.
A field changed on one side only is taken from that side; the same field changed on both sides to the same value is no clash; changed differently
it is a clash, which the person settles (Settings > Sync conflicts). What a field is (the owner, 2026-10-20): each property; each changed occurrence
of a repeating event is an item of its own; lists (skipped dates, tags, phone numbers, e-mail addresses, attendees …) are merged entry by entry;
all alarms of an item together are one field; a text is one field; a property of another program is a field by its name and always kept; the time
stamps a program sets itself are not compared. Works on the text lines themselves, so nothing a program wrote is lost. Never raises."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from chrono_mail.pim.vcard import fold, unfold

# --- Version ------------------------------------------------------------
__version__ = "merge.py 1.10.277"

# --- Version history ----------------------------------------------------
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
ALARMS: str = "VALARM"
SEPARATOR: str = "\x1f"
LABELS: dict[str, str] = {
    "SUMMARY": "Title", "LOCATION": "Location", "DESCRIPTION": "Description", "DTSTART": "Starts", "DTEND": "Ends", "DUE": "Due", "DURATION": "Duration",
    "RRULE": "Repeat", "EXDATE": "Skipped date", "RDATE": "Extra date", "CATEGORIES": "Tag", "ATTENDEE": "Attendee", "ORGANIZER": "Organizer",
    "STATUS": "Status", "PRIORITY": "Priority", "PERCENT-COMPLETE": "Done (%)", "COMPLETED": "Completed", "TRANSP": "Show as", "CLASS": "Private",
    "COLOR": "Colour (other programs)", "X-CHRONO-MAIL-COLOR-ID": "Colour", "URL": "Web address", "VALARM": "Reminder",
    "FN": "Name", "N": "Name parts", "ORG": "Company", "TITLE": "Job title", "TEL": "Phone", "EMAIL": "E-mail", "ADR": "Address", "BDAY": "Birthday",
    "NOTE": "Note", "NICKNAME": "Nickname", "PHOTO": "Photo", "ROLE": "Role", "MEMBER": "Group member", "X-ADDRESSBOOKSERVER-MEMBER": "Group member",
    "KIND": "Group", "X-ADDRESSBOOKSERVER-KIND": "Group", "item": "The whole item",
}


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
    recurrence = next((line for name, line in component.lines if name == "RECURRENCE-ID"), "")
    return f"{component.name}{SEPARATOR}{_head_and_value(recurrence)[1].strip() if recurrence else ''}"


def _fields(unit: _Component) -> dict[str, str]:
    """A unit's fields: key -> the text that stands for it (a whole line, a value of a comma list, or all alarms together)."""
    counts: dict[str, int] = {}
    for name, _ in unit.lines:
        counts[name] = counts.get(name, 0) + 1
    found: dict[str, str] = {}
    for name, line in unit.lines:
        if name in STAMPS:
            continue
        head, value = _head_and_value(line)
        if name in COMMA_LISTS:
            for part in _split_commas(value):
                found[f"{name}{SEPARATOR}{head}{SEPARATOR}{part.strip()}"] = part.strip()
        elif name in LISTS or counts[name] > 1:
            found[f"{name}{SEPARATOR}{value.strip().lower()}"] = line
        else:
            found[name] = line
    alarms = [child for child in unit.children if child.name == ALARMS]
    if alarms:
        found[ALARMS] = "\n".join("\n".join(_serialise(alarm)) for alarm in alarms)
    others = [child for child in unit.children if child.name != ALARMS]
    for child in others:  # rare (a VLOCATION, for one): kept whole, as one field each
        found[f"{child.name}{SEPARATOR}" + "\n".join(_serialise(child))] = "\n".join(_serialise(child))
    return found


def _label(key: str, unit_key: str) -> str:
    name = key.split(SEPARATOR, 1)[0]
    words = LABELS.get(name, name.title() if not name.startswith("X-") else name)
    recurrence = unit_key.split(SEPARATOR, 1)[1] if SEPARATOR in unit_key else ""
    return f"{words} (the occurrence of {recurrence})" if recurrence else words


def _shown(text: str | None) -> str:
    """A field's text as the person reads it: the value of a line ("" when absent)."""
    if text is None:
        return ""
    if "\n" in text:  # alarms: their triggers
        return "; ".join(_head_and_value(line)[1] for line in text.split("\n") if _name(line) == "TRIGGER") or "a reminder"
    return _head_and_value(text)[1] if ":" in text and not text.startswith(":") else text


def _choose(key: str, base: str | None, local: str | None, server: str | None, choices: Mapping[str, str], clashes: list[Clash], label: str) -> str | None:
    if local == server:
        return local
    if local == base:
        return server
    if server == base:
        return local
    choice = choices.get(key)
    if choice == LOCAL:
        return local
    if choice == SERVER:
        return server
    clashes.append(Clash(key, label, _shown(local), _shown(server)))
    return local


def _rebuild(name: str, fields: dict[str, str | None], order: list[str], stamps: list[str], children: list[str]) -> list[str]:
    """A unit written again from its merged fields: in Chrono Mail's order, then what only the server had; a comma list again on one line per head."""
    out = [f"BEGIN:{name}"]
    out.extend(stamps)
    grouped: dict[str, list[str]] = {}
    emitted: set[str] = set()
    for key in order:
        value = fields.get(key)
        if value is None or key in emitted:
            continue
        emitted.add(key)
        parts = key.split(SEPARATOR)
        if parts[0] in COMMA_LISTS and len(parts) == 3:
            head = parts[1]
            if head not in grouped:
                grouped[head] = []
                out.append(SEPARATOR + head)  # where the line goes; filled below
            grouped[head].append(value)
        elif parts[0] == ALARMS or (len(parts) > 1 and "\n" in (value or "") and parts[0] not in COMMA_LISTS):
            children.append(value)
        else:
            out.append(value)
    out = [f"{line[1:]}:{','.join(grouped[line[1:]])}" if line.startswith(SEPARATOR) else line for line in out]
    for child in children:
        out.extend(child.split("\n"))
    out.append(f"END:{name}")
    return out


def _merge_unit(unit_key: str, base: _Component | None, local: _Component | None, server: _Component | None, choices: Mapping[str, str],
                clashes: list[Clash]) -> list[str] | None:
    """One unit (an event, a changed occurrence, a task, a contact) merged: its lines, or None when it is gone."""
    unit_choice = choices.get(f"unit{SEPARATOR}{unit_key}")
    if local is None or server is None:
        present = local if local is not None else server
        assert present is not None
        if base is None:
            return _serialise(present)  # new on one side: taken
        if _fields(present) == _fields(base):
            return None  # removed on the other side, unchanged here: removed
        side = LOCAL if local is not None else SERVER
        if unit_choice in (LOCAL, SERVER):
            return _serialise(present) if unit_choice == side else None
        what = "removed on the server, changed here" if local is not None else "removed here, changed on the server"
        clashes.append(Clash(f"unit{SEPARATOR}{unit_key}", _label("item", unit_key) + f" ({what})", "kept" if local is not None else "removed",
                             "kept" if server is not None else "removed"))
        return _serialise(local) if local is not None else None
    base_fields = _fields(base) if base is not None else None
    local_fields, server_fields = _fields(local), _fields(server)
    merged: dict[str, str | None] = {}
    for key in dict.fromkeys([*local_fields, *server_fields, *(base_fields or {})]):
        before = base_fields.get(key) if base_fields is not None else None
        if base_fields is None and key not in local_fields and key not in server_fields:
            continue
        if base_fields is None and local_fields.get(key) != server_fields.get(key):  # no agreed version: every difference is a clash
            choice = choices.get(key)
            if choice in (LOCAL, SERVER):
                merged[key] = local_fields.get(key) if choice == LOCAL else server_fields.get(key)
            else:
                clashes.append(Clash(key, _label(key, unit_key), _shown(local_fields.get(key)), _shown(server_fields.get(key))))
                merged[key] = local_fields.get(key)
            continue
        merged[key] = _choose(key, before, local_fields.get(key), server_fields.get(key), choices, clashes, _label(key, unit_key))
    order = [*local_fields, *(key for key in server_fields if key not in local_fields)]
    stamps = [line for name, line in local.lines if name in STAMPS]
    return _rebuild(local.name, merged, order, stamps, [])


def _units(root: _Component) -> tuple[list[str], dict[str, _Component], list[_Component]]:
    """(the wrapper's own lines, the units by key, the time zones) of a parsed text."""
    wrapper = root.children[0]
    if wrapper.name in UNITS:  # a vCard has no wrapper
        return [], {_unit_key(wrapper): wrapper}, []
    units = {_unit_key(child): child for child in wrapper.children if child.name in UNITS}
    zones = [child for child in wrapper.children if child.name not in UNITS]
    return [line for _, line in wrapper.lines], units, zones


def merge(base: str | None, local: str, server: str, choices: Mapping[str, str] | None = None) -> Merged:
    """Merge Chrono Mail's and the server's version of one item against the version both last agreed on (None: not known, so every difference is a
    clash). `choices` settle clashes by their key: LOCAL or SERVER. The merged text, or the clashes still open."""
    wanted = choices or {}
    clashes: list[Clash] = []
    local_root, server_root = _parse(local), _parse(server)
    base_root = _parse(base) if base is not None else None
    if local_root is None or server_root is None or (base is not None and base_root is None):  # not readable: the whole item is one field
        if local == server:
            return Merged(local)
        chosen = _choose("item", base, local, server, wanted, clashes, LABELS["item"])
        return Merged(chosen if not clashes else None, tuple(clashes))
    local_wrapper, local_units, local_zones = _units(local_root)
    _, server_units, server_zones = _units(server_root)
    base_units: dict[str, _Component] = _units(base_root)[1] if base_root is not None else {}
    lines: list[str] = []
    for unit_key in dict.fromkeys([*local_units, *server_units, *base_units]):
        if unit_key not in local_units and unit_key not in server_units:
            continue
        merged = _merge_unit(
            unit_key, base_units.get(unit_key) if base_root is not None else None, local_units.get(unit_key), server_units.get(unit_key), wanted, clashes,
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
