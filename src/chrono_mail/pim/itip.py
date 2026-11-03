"""Calendar messages sent by mail (iTIP, RFC 5546; iMIP, RFC 6047): what a text/calendar part asks or tells. No I/O."""
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal  # icalendar hands out untyped property objects

import icalendar

from chrono_mail.pim.ical import (  # noqa: PLC2701 - the module's own readers: one reading of iCalendar
    MAXIMUM_COMPONENTS, Attendee, Event, Moment, Task, _attendees, _event_from, _moment, _parse, _person, _stamp, _task_from, _text,
)

# --- Version ------------------------------------------------------------
__version__ = "itip.py 4.4.504"

# --- Version history ----------------------------------------------------
# v4.4.504: Initial calendar messages: the method, the kind, the item, its version (SEQUENCE, DTSTAMP), whether a newer one replaces it

MessageKind = Literal["event", "task", "freebusy", "journal"]
METHODS: frozenset[str] = frozenset({"PUBLISH", "REQUEST", "REPLY", "ADD", "CANCEL", "REFRESH", "COUNTER", "DECLINECOUNTER"})  # RFC 5546 section 1.4
_START_NEEDED: frozenset[str] = frozenset({"PUBLISH", "REQUEST", "ADD", "COUNTER", ""})  # these set a time: an event without one is damaged
_KINDS: dict[str, MessageKind] = {"VEVENT": "event", "VTODO": "task", "VFREEBUSY": "freebusy", "VJOURNAL": "journal"}


@dataclass(frozen=True, slots=True)
class CalendarMessage:
    """One calendar message: what it asks (method; "" for a calendar file that asks nothing) about which item, in which version."""

    method: str
    kind: MessageKind
    uid: str
    sequence: int  # the item's version: a higher one replaces every lower one (RFC 5546 section 2.1.5)
    stamp: datetime | None  # when it was written (DTSTAMP): the newer of two of the same version replaces the other
    occurrence: Moment | None = None  # RECURRENCE-ID: the message is about this one occurrence of a series; None: the whole item
    title: str = ""
    location: str = ""
    organizer: Attendee | None = None
    attendees: tuple[Attendee, ...] = ()
    event: Event | None = None  # the event, when it has a start (an answer or a request for the latest version may have none)
    task: Task | None = None


def read_calendar_message(text: str | bytes, occurrence: Moment | None | Literal["main"] = "main") -> CalendarMessage | None:
    """The calendar message in a text, or None when the text holds none that Chrono Mail can read (damaged, empty, or no item). Never raises.
    Only one kind of item is in one message (RFC 5546 section 1.4): the first one written decides. Of a series the main item is read (the one
    without RECURRENCE-ID), else the first readable one; with `occurrence` (a stored copy) the item of exactly that RECURRENCE-ID (None: the
    main item), or None when the text has no such item."""
    calendar = _parse(text)
    if calendar is None:
        return None
    method = _text(calendar, "METHOD").upper()
    method = method if method in METHODS else ""  # an unknown method asks nothing Chrono Mail can answer: a calendar file
    kind_name = next((each.name for each in calendar.subcomponents if each.name in _KINDS), None)
    if kind_name is None:
        return None
    kind = _KINDS[kind_name]
    items = [each for each in calendar.subcomponents if each.name == kind_name][:MAXIMUM_COMPONENTS]
    if occurrence == "main":
        items.sort(key=lambda each: each.get("RECURRENCE-ID") is not None)  # the main item first, wherever it is written
    for component in items:
        try:  # its uid, version and contents all come from this one item
            found = _message_of(component, method, kind)
        except Exception:  # noqa: BLE001 - one damaged item is skipped, whatever the library raises
            continue
        if found is None or (occurrence != "main" and not _same_moment(found.occurrence, occurrence)):
            continue
        return found
    return None


def _same_moment(first: Moment | None, second: Moment | None) -> bool:
    if first is None or second is None:
        return first is second
    try:
        return first == second
    except TypeError:  # one with a zone, one without
        return False


def _first(component: icalendar.Component, name: str) -> str:
    """A text property's value; written twice (hostile or damaged), the first counts."""
    value: Any = component.get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value is not None else ""


def _message_of(component: icalendar.Component, method: str, kind: MessageKind) -> CalendarMessage | None:
    try:
        sequence = max(0, int(str(component.get("SEQUENCE", 0))))
    except (ValueError, OverflowError):
        sequence = 0
    uid = _first(component, "UID")  # never made up: an item without one is found in no calendar
    organizer: Any = component.get("ORGANIZER")
    if isinstance(organizer, list):  # written twice (hostile or damaged): the first counts
        organizer = organizer[0] if organizer else None
    found = CalendarMessage(
        method, kind, uid, sequence, _stamp(component, "DTSTAMP"), _moment(component, "RECURRENCE-ID"), _first(component, "SUMMARY"),
        _first(component, "LOCATION"), _person(organizer) if organizer is not None and str(organizer).strip() else None, _attendees(component),
    )
    if kind == "event":
        event = _event_from(component)
        if event is None and (component.get("DTSTART") is not None or method in _START_NEEDED):
            return None  # a start that cannot be read is a damaged item; a message that sets a time needs one (RFC 5546 section 3.2)
        return replace(found, event=event)  # None: no start written, as an answer or a request for the latest version may leave it out
    if kind == "task":
        return replace(found, task=replace(_task_from(component), uid=uid))
    return found


def is_newer(message: CalendarMessage, sequence: int, stamp: datetime | None) -> bool | None:
    """Whether the message replaces a version kept with this SEQUENCE and DTSTAMP: True newer, False older, None the same version
    (RFC 5546 section 2.1.5: the higher SEQUENCE wins; of the same SEQUENCE the later DTSTAMP). Against the copy in a calendar of this
    computer pass stamp None: its DTSTAMP is rewritten at every change made here, so only the organizer's SEQUENCE tells it apart."""
    if message.sequence != sequence:
        return message.sequence > sequence
    if message.stamp is None or stamp is None or message.stamp == stamp:
        return None
    try:
        return message.stamp > stamp
    except TypeError:  # one with a zone, one without: they cannot be compared, so neither is taken for newer
        return None
