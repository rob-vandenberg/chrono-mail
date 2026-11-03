"""Calendar messages sent by mail (iTIP, RFC 5546; iMIP, RFC 6047): what a text/calendar part asks or tells. No I/O."""
import copy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal  # icalendar hands out untyped property objects

import icalendar

from chrono_mail.pim.ical import (  # noqa: PLC2701 - the module's own readers: one reading of iCalendar
    MAXIMUM_COMPONENTS, Attendee, Event, Moment, Task, _attendees, _event_from, _moment, _parse, _person, _stamp, _task_from, _text,
)

# --- Version ------------------------------------------------------------
__version__ = "itip.py 4.4.505"

# --- Version history ----------------------------------------------------
# v4.4.505: answering (your copy with your answer, the REPLY), following the organizer (updates keeping your answers, cancellations,
#           added dates, REFRESH), delegating (REQUEST to the delegate, REPLY DELEGATED); times compared as moments (D-310)
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


# --- answering (D-310) -------------------------------------------------------------------------------------------------------------------

ANSWERS: frozenset[str] = frozenset({"ACCEPTED", "TENTATIVE", "DECLINED", "DELEGATED"})


def _address_of(prop: Any) -> str:  # noqa: ANN401 - icalendar's property objects are untyped
    text = str(prop).strip()
    return (text[7:] if text.lower().startswith("mailto:") else text).strip().lower()


def _items_of(calendar: icalendar.Calendar) -> list[icalendar.Component]:
    return [each for each in calendar.subcomponents if each.name in ("VEVENT", "VTODO")]


def _set_answer(component: icalendar.Component, address: str, answer: str, name: str = "") -> None:
    """Your attendee line with your answer (PARTSTAT), the question for an answer (RSVP) gone; a line of your own when you were not on the list
    (an invitation passed on to you): RFC 5546 lets you answer it."""
    props: Any = component.get("ATTENDEE")
    people = props if isinstance(props, list) else ([props] if props is not None else [])
    for person in people:
        if _address_of(person) == address:
            person.params["PARTSTAT"] = answer
            person.params.pop("RSVP", None)
            return
    line = icalendar.vCalAddress(f"mailto:{address}")
    line.params["PARTSTAT"] = answer
    if name:
        line.params["CN"] = name
    component.add("ATTENDEE", line)


def answered_copy(text: str, address: str, answer: str, name: str = "") -> str | None:
    """The calendar copy of a calendar message with your answer: without its METHOD (a stored item has none, RFC 4791), your attendee line set
    in every item of it. None when the text holds nothing to keep."""
    calendar = _parse(text)
    if calendar is None or not _items_of(calendar):
        return None
    calendar.pop("METHOD", None)
    for component in _items_of(calendar):
        _set_answer(component, address.strip().lower(), answer, name)
    return calendar.to_ical().decode("utf-8")


def merged_answer(stored: str, message_text: str, address: str, answer: str, occurrence: Moment | None, take_message: bool, name: str = "") -> str | None:
    """Your copy after answering, when one is kept already. take_message: the message is a newer version, so its items replace the kept
    ones (for one occurrence: only that occurrence); else the kept copy stays as it is (its own changes, reminders, colour) with your answer set
    in it (for one occurrence: in that occurrence, taken from the message when the copy has no changed version of it)."""
    kept = _parse(stored)
    message = _parse(message_text)
    if kept is None or message is None:
        return None
    kept.pop("METHOD", None)
    lower = address.strip().lower()

    if occurrence is None and take_message:
        _keep_own_answers(kept, message, lower)
        old_main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
        series_answer = _your_answer(old_main, lower) if old_main is not None else ""
        before = {str(_moment(each, "RECURRENCE-ID")): each for each in _items_of(kept) if each.get("RECURRENCE-ID") is not None}
        for component in _items_of(message):
            if component.get("RECURRENCE-ID") is None:
                _set_answer(component, lower, answer, name)  # your answer to the series
                continue
            old = before.get(str(_moment(component, "RECURRENCE-ID")))
            own = _your_answer(old, lower) if old is not None else ""
            if old is not None and own and own != series_answer and _when(old) == _when(component):
                _set_answer(component, lower, own)  # a date you answered otherwise, resent at the same time, keeps that (as updated_copy)
            else:
                _set_answer(component, lower, answer, name)  # a date that followed the series takes your new answer, whatever the organizer recorded
        message.pop("METHOD", None)
        return message.to_ical().decode("utf-8")
    if occurrence is None:
        main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
        series_answer = _your_answer(main, lower) if main is not None else ""
        for component in _items_of(kept):  # the series and the occurrences that followed it; one you answered otherwise keeps that
            if component.get("RECURRENCE-ID") is None or _your_answer(component, lower) in ("", "NEEDS-ACTION", series_answer):
                _set_answer(component, lower, answer, name)
        return kept.to_ical().decode("utf-8")
    # A message about occurrences only (one or several): each of them into the copy, with your answer.
    incoming = [each for each in _items_of(message) if each.get("RECURRENCE-ID") is not None]
    if not incoming:
        return None
    for component in incoming:
        moment = _moment(component, "RECURRENCE-ID")
        present = [each for each in _items_of(kept) if _same_moment(_moment(each, "RECURRENCE-ID"), moment)]
        if present and not take_message:
            for each in present:
                _set_answer(each, lower, answer, name)
            continue
        kept.subcomponents = [each for each in kept.subcomponents if not (each.name in ("VEVENT", "VTODO") and _same_moment(_moment(each, "RECURRENCE-ID"), moment))]
        _set_answer(component, lower, answer, name)
        kept.add_component(component)
    _copy_zones(message, kept)
    return kept.to_ical().decode("utf-8")


def reply_text(message_text: str, address: str, answer: str, now: datetime, name: str = "", copy_text: str | None = None) -> str | None:
    """Your answer to the organizer (METHOD:REPLY, RFC 5546 section 3.2.3): per item its UID, the SEQUENCE of the request, DTSTAMP now, the
    organizer, your attendee line with your answer, the RECURRENCE-ID of one occurrence, and its title and time to read. None when the text
    holds nothing to answer."""
    message = _parse(message_text)
    if message is None or not _items_of(message):
        return None
    reply = icalendar.Calendar()
    reply.add("PRODID", "-//Chrono Mail//Chrono Mail//EN")
    reply.add("VERSION", "2.0")
    reply.add("METHOD", "REPLY")
    lower = address.strip().lower()
    kept = _parse(copy_text) if copy_text else None  # your copy as just written: a date you answered otherwise is answered as it says
    kept_answers = {str(_moment(each, "RECURRENCE-ID")): _your_answer(each, lower) for each in _items_of(kept)} if kept is not None else {}
    for zone in message.walk("VTIMEZONE"):
        reply.add_component(zone)
    for item in _items_of(message):
        answer_item = icalendar.Event() if item.name == "VEVENT" else icalendar.Todo()
        for key in ("UID", "SEQUENCE", "RECURRENCE-ID", "ORGANIZER", "SUMMARY", "DTSTART", "DTEND", "DUE"):
            value = item.get(key)
            if value is not None and not isinstance(value, list):
                answer_item[key] = value
        answer_item.add("DTSTAMP", now)
        own = kept_answers.get(str(_moment(item, "RECURRENCE-ID")), "")
        _set_answer(answer_item, lower, own if own and own != "NEEDS-ACTION" else answer, name)
        reply.add_component(answer_item)
    return reply.to_ical().decode("utf-8")


# --- following the organizer (D-310) -----------------------------------------------------------------------------------------------------

_TIME_KEYS: tuple[str, ...] = ("DTSTART", "DTEND", "DURATION", "DUE", "RRULE", "RDATE", "EXDATE")  # a change of these asks a new answer (RFC 5546 2.1.4)


def _moment_key(value: object) -> str:
    """A moment as one text for comparing: an aware time in UTC, a floating time or a day as written."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        try:
            return value.astimezone(UTC).isoformat()
        except (OverflowError, ValueError):
            return value.isoformat()
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _when(component: icalendar.Component) -> tuple[str, ...]:
    """The item's time as moments, not as written: the same moment in another zone or another way of writing is the same time; the same clock
    time in another zone is another moment. Lists of dates (RDATE, EXDATE) compare as sets, however they are spread over lines."""
    found: list[str] = []
    for key in ("DTSTART", "DTEND", "DUE"):
        found.append(_moment_key(_moment(component, key)))
    duration: Any = component.get("DURATION")
    found.append(str(getattr(duration, "dt", "")) if duration is not None and not isinstance(duration, list) else "")
    rule: Any = component.get("RRULE")
    found.append(rule.to_ical().decode("utf-8", "replace") if hasattr(rule, "to_ical") else "")
    for key in ("RDATE", "EXDATE"):
        props: Any = component.get(key)
        moments: set[str] = set()
        for prop in props if isinstance(props, list) else ([props] if props is not None else []):
            for value in getattr(prop, "dts", []):
                moments.add(_moment_key(getattr(value, "dt", value)))
        found.append(",".join(sorted(moments)))
    return tuple(found)


def _your_answer(component: icalendar.Component, address: str) -> str:
    props: Any = component.get("ATTENDEE")
    people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
    return next((str(person.params.get("PARTSTAT", "NEEDS-ACTION")).upper() for person in people if _address_of(person) == address), "")


def updated_copy(stored: str, message_text: str, address: str) -> str | None:
    """The copy after a newer version from the organizer: the message's items replace the kept ones (one occurrence: only that one); your
    answer stays, unless its time changed, then it is asked again (NEEDS-ACTION, RFC 5546 section 2.1.4)."""
    kept = _parse(stored)
    message = _parse(message_text)
    if kept is None or message is None or not _items_of(message):
        return None
    lower = address.strip().lower()
    before = {str(_moment(each, "RECURRENCE-ID")): each for each in _items_of(kept)}
    for component in _items_of(message):
        same = before.get(str(_moment(component, "RECURRENCE-ID")))
        if same is not None:  # the same item (or occurrence) kept: a new time asks the answer again (RFC 5546 section 2.1.4)
            answer = _your_answer(same, lower)
            if answer and _when(same) != _when(component):
                answer = "NEEDS-ACTION"
        else:  # an occurrence the copy has not changed: it takes the series' answer, unless the organizer moved it (a new time asks again)
            series = before.get("None")
            answer = _your_answer(series, lower) if series is not None else ""
            occurrence, start = _moment(component, "RECURRENCE-ID"), _moment(component, "DTSTART")
            if answer and occurrence is not None and start is not None and _moment_key(occurrence) != _moment_key(start):
                answer = "NEEDS-ACTION"
        if answer:
            _set_answer(component, lower, answer)
    if any(_moment(each, "RECURRENCE-ID") is None for each in _items_of(message)):  # the whole series (with its changed occurrences): it replaces the copy
        _keep_own_answers(kept, message, lower)
        message.pop("METHOD", None)
        return message.to_ical().decode("utf-8")
    for component in _items_of(message):
        moment = _moment(component, "RECURRENCE-ID")
        kept.subcomponents = [each for each in kept.subcomponents if not (each.name in ("VEVENT", "VTODO") and _same_moment(_moment(each, "RECURRENCE-ID"), moment))]
        kept.add_component(component)
    _copy_zones(message, kept)
    kept.pop("METHOD", None)
    return kept.to_ical().decode("utf-8")


def _copy_zones(source: icalendar.Calendar, target: icalendar.Calendar) -> None:
    """The time zones the message brings that the copy lacks (a new TZID must never be left undefined)."""
    for zone in source.walk("VTIMEZONE"):
        if not any(_text(each, "TZID") == _text(zone, "TZID") for each in target.walk("VTIMEZONE")):
            target.subcomponents.insert(0, zone)


def without_occurrence(stored: str, occurrence: Moment) -> str | None:
    """The copy without one occurrence (a cancellation of it): an EXDATE on the main item, its changed version gone. "" when that occurrence was
    all the copy held (the item goes); None when it cannot be read or holds other occurrences only."""
    kept = _parse(stored)
    if kept is None:
        return None
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    if main is None and not any(_same_moment(_moment(each, "RECURRENCE-ID"), occurrence) for each in _items_of(kept)):
        return None  # a copy of other occurrences only: this one is not in it
    kept.subcomponents = [each for each in kept.subcomponents if not (each.name in ("VEVENT", "VTODO") and _same_moment(_moment(each, "RECURRENCE-ID"), occurrence))]
    if main is not None and not holds_occurrence(stored, occurrence, main_only=True):
        pass  # excluded already: never written twice
    elif main is not None:
        main.add("EXDATE", occurrence)
    elif not _items_of(kept):
        return ""  # that occurrence was all the copy held: the item goes
    return kept.to_ical().decode("utf-8")


def with_added(stored: str, message_text: str) -> str | None:
    """The copy with the occurrences an ADD brings (RFC 5546 section 3.2.4): each as an extra date (RDATE) of the series, with its own item."""
    kept = _parse(stored)
    message = _parse(message_text)
    if kept is None or message is None:
        return None
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    if main is None:
        return None
    present = {str(_moment(each, "RECURRENCE-ID")) for each in _items_of(kept)}
    series: Any = main.get("ATTENDEE")
    series_people: list[Any] = series if isinstance(series, list) else ([series] if series is not None else [])
    answers = {_address_of(person): str(person.params.get("PARTSTAT", "")) for person in series_people}
    for component in _items_of(message):
        props: Any = component.get("ATTENDEE")
        for person in props if isinstance(props, list) else ([props] if props is not None else []):
            answer = answers.get(_address_of(person), "")
            if answer:  # an added date takes the answer each attendee gave the series (yours among them)
                person.params["PARTSTAT"] = answer
                person.params.pop("RSVP", None)
        start = _moment(component, "DTSTART")
        if start is None or str(start) in present:
            continue  # an occurrence added already: never twice (a second click, the same message again)
        present.add(str(start))
        main.add("RDATE", start)
        component.pop("RECURRENCE-ID", None)
        component.add("RECURRENCE-ID", start)
        kept.add_component(component)
    _copy_zones(message, kept)
    return kept.to_ical().decode("utf-8")


def refresh_text(message_text: str, address: str, now: datetime) -> str | None:
    """A request to the organizer for the latest version (METHOD:REFRESH, RFC 5546 section 3.2.6): UID, organizer, your attendee line."""
    message = _parse(message_text)
    if message is None or not _items_of(message):
        return None
    refresh = icalendar.Calendar()
    refresh.add("PRODID", "-//Chrono Mail//Chrono Mail//EN")
    refresh.add("VERSION", "2.0")
    refresh.add("METHOD", "REFRESH")
    item = _items_of(message)[0]
    asked = icalendar.Event() if item.name == "VEVENT" else icalendar.Todo()
    for key in ("UID", "ORGANIZER"):
        value = item.get(key)
        if value is not None and not isinstance(value, list):
            asked[key] = value
    asked.add("DTSTAMP", now)
    asked.add("ATTENDEE", icalendar.vCalAddress(f"mailto:{address.strip().lower()}"))
    refresh.add_component(asked)
    return refresh.to_ical().decode("utf-8")


def delegated_texts(message_text: str, address: str, delegate: str, now: datetime, name: str = "") -> tuple[str, str] | None:
    """Delegating an invitation (RFC 5546 section 3.2.2.3): the request passed on to the delegate (your line DELEGATED with DELEGATED-TO, a line
    for the delegate with DELEGATED-FROM and RSVP), and the answer to the organizer (your line DELEGATED, DELEGATED-TO). None: nothing to pass on."""
    message = _parse(message_text)
    if message is None or not _items_of(message):
        return None
    you, other = address.strip().lower(), delegate.strip().lower()
    for item in _items_of(message):
        _set_answer(item, you, "DELEGATED", name)
        props: Any = item.get("ATTENDEE")
        people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
        role = "REQ-PARTICIPANT"
        for person in people:
            if _address_of(person) == you:
                person.params["DELEGATED-TO"] = f"mailto:{other}"
                role = str(person.params.get("ROLE", role))
        line = icalendar.vCalAddress(f"mailto:{other}")
        line.params.update({"DELEGATED-FROM": f"mailto:{you}", "RSVP": "TRUE", "PARTSTAT": "NEEDS-ACTION", "ROLE": role})
        item.add("ATTENDEE", line)
    message.pop("METHOD", None)
    message.add("METHOD", "REQUEST")
    reply = reply_text(message_text, address, "DELEGATED", now, name)
    if reply is None:
        return None
    answered = _parse(reply)
    if answered is None:
        return None
    for item in _items_of(answered):
        props = item.get("ATTENDEE")
        for person in props if isinstance(props, list) else [props]:
            if person is not None and _address_of(person) == you:
                person.params["DELEGATED-TO"] = f"mailto:{other}"
        line = icalendar.vCalAddress(f"mailto:{other}")  # the delegate on the answer too (RFC 5546 section 3.2.2.3)
        line.params.update({"DELEGATED-FROM": f"mailto:{you}", "PARTSTAT": "NEEDS-ACTION"})
        item.add("ATTENDEE", line)
    return message.to_ical().decode("utf-8"), answered.to_ical().decode("utf-8")


def published_copy(text: str) -> str | None:
    """The calendar copy of a shared item (PUBLISH): as it was sent, without its METHOD; no attendee line of yours is made up."""
    calendar = _parse(text)
    if calendar is None or not _items_of(calendar):
        return None
    calendar.pop("METHOD", None)
    return calendar.to_ical().decode("utf-8")


def with_delegate(text: str, address: str, delegate: str, occurrences: list[Moment]) -> str | None:
    """Your copy naming who goes in your place (RFC 5546 section 3.2.2.3): DELEGATED-TO on your line, a line for the delegate with DELEGATED-FROM,
    in the items the delegation is about (one occurrence, or all)."""
    calendar = _parse(text)
    if calendar is None:
        return None
    you, other = address.strip().lower(), delegate.strip().lower()
    for item in _items_of(calendar):
        moment = _moment(item, "RECURRENCE-ID")
        if occurrences:  # this delegation is about these occurrences only
            if not any(_same_moment(moment, each) for each in occurrences):
                continue
        elif moment is not None and _your_answer(item, you) != "DELEGATED":
            continue  # the whole series: the series, and the dates that followed it into DELEGATED now
        props: Any = item.get("ATTENDEE")
        mine = [person for person in (props if isinstance(props, list) else ([props] if props is not None else [])) if _address_of(person) == you]
        if not occurrences and moment is not None and any(person.params.get("DELEGATED-TO") for person in mine):
            continue  # a date delegated earlier to someone else stays theirs
        people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
        if any(_address_of(person) == other for person in people):
            continue
        for person in people:
            if _address_of(person) == you:
                person.params["DELEGATED-TO"] = f"mailto:{other}"
        line = icalendar.vCalAddress(f"mailto:{other}")
        line.params.update({"DELEGATED-FROM": f"mailto:{you}", "PARTSTAT": "NEEDS-ACTION", "RSVP": "TRUE"})
        item.add("ATTENDEE", line)
    return calendar.to_ical().decode("utf-8")


def _excluded(component: icalendar.Component) -> list[Moment]:
    props: Any = component.get("EXDATE")
    found: list[Moment] = []
    for prop in props if isinstance(props, list) else ([props] if props is not None else []):
        for value in getattr(prop, "dts", []):
            moment = getattr(value, "dt", None)
            if isinstance(moment, (date, datetime)):
                found.append(moment)
    return found


def holds_occurrence(text: str, occurrence: Moment, main_only: bool = False) -> bool:
    """Whether the copy holds this occurrence: a changed version of it, or (main_only: only this) a main item that does not exclude it."""
    calendar = _parse(text)
    if calendar is None:
        return False
    if not main_only and any(_same_moment(_moment(each, "RECURRENCE-ID"), occurrence) for each in _items_of(calendar)):
        return True
    main = next((each for each in _items_of(calendar) if each.get("RECURRENCE-ID") is None), None)
    return main is not None and not any(_same_moment(each, occurrence) for each in _excluded(main))


def added_already(text: str, message_text: str) -> bool:
    """Whether every date an ADD brings is in the copy already."""
    kept = _parse(text)
    message = _parse(message_text)
    if kept is None or message is None:
        return False
    present = {str(_moment(each, "RECURRENCE-ID")) for each in _items_of(kept)}
    return all(str(_moment(each, "DTSTART")) in present for each in _items_of(message))


def _keep_own_answers(kept: icalendar.Calendar, message: icalendar.Calendar, address: str) -> None:
    """A new version of the whole series keeps the occurrences you answered otherwise than the series (a declined date): each becomes a changed
    occurrence of the new version with your answer, unless the series' time changed (then every answer is asked again, RFC 5546 2.1.4)."""
    old_main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    new_main = next((each for each in _items_of(message) if each.get("RECURRENCE-ID") is None), None)
    if old_main is None or new_main is None or _when(old_main) != _when(new_main):
        return
    series_answer = _your_answer(old_main, address)
    present = {str(_moment(each, "RECURRENCE-ID")) for each in _items_of(message)}
    start, end = _moment(new_main, "DTSTART"), _moment(new_main, "DTEND")
    for old in _items_of(kept):
        moment = _moment(old, "RECURRENCE-ID")
        answer = _your_answer(old, address)
        if moment is None or str(moment) in present or not answer or answer == series_answer:
            continue
        occurrence = copy.deepcopy(new_main)
        due = _moment(new_main, "DUE")
        for key in ("RRULE", "RDATE", "EXDATE", "DTSTART", "DTEND", "DUE", "RECURRENCE-ID"):
            occurrence.pop(key, None)
        occurrence.add("RECURRENCE-ID", moment)
        occurrence.add("DTSTART", moment)
        for key, until in (("DTEND", end), ("DUE", due)):  # the end, or a task's due moment, the same length after the occurrence's start
            if start is not None and until is not None and type(start) is type(moment):
                try:
                    occurrence.add(key, moment + (until - start))  # type: ignore[operator]
                except TypeError:
                    pass
        _set_answer(occurrence, address, answer)
        message.add_component(occurrence)


def occurrences_of(text: str) -> list[Moment]:
    """The occurrences a message is about (each item's RECURRENCE-ID), in order; empty for one about the whole item."""
    calendar = _parse(text)
    if calendar is None:
        return []
    return [moment for each in _items_of(calendar) if (moment := _moment(each, "RECURRENCE-ID")) is not None]
