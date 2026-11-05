"""Calendar messages sent by mail (iTIP, RFC 5546; iMIP, RFC 6047): what a text/calendar part asks or tells. No I/O."""
import copy
import hashlib
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from functools import lru_cache
from itertools import groupby, islice
from typing import Any, Literal  # icalendar hands out untyped property objects

import icalendar
from dateutil.rrule import rrulestr

from chrono_mail.pim.ical import (  # noqa: PLC2701 - the module's own readers: one reading of iCalendar
    COLOUR_ID_PROPERTY, MAXIMUM_ATTENDEES, MAXIMUM_COMPONENTS, MAXIMUM_DATES, Attendee, Event, Moment, Occurrence, Task, _as_aware, _attendees, _event_from, _moment, _moments, _parse, _person, _stamp, _task_from, _text, calendars_in, new_uid, zones_used,
    occurrences, occurrences_from,
)
from chrono_mail.pim.times import moved_like_the_start, wall_clock

# --- Version ------------------------------------------------------------
__version__ = "itip.py 4.4.557"

# --- Version history ----------------------------------------------------
# v4.4.557: restored_copy takes Cancelled off, with your own meeting's attendees asked again; cancelled_in: a changed date goes by what it says itself (D-310)
# v4.4.556: What goes out of an answer from the calendar: meeting_in_mail, the organizer's own text from the mail; shared_text; reply_text can leave out title and time; no server mark in a mail (D-310)
# v4.4.555: An update is copied into the event you keep, also at Accept; a date it writes itself keeps its time; removed dates are capped and compared as sets; your deleted dates are no change of time; a cancellation sets Cancelled, cancelled_in as it leaves it; Accept of a copy a server reset takes its free mark off; with_organizer (D-310)
# v4.4.554: A declined event stays in its copy: merged_answer answers every date on all of it, what you declined and now attend blocks your time again; with_kept_mail (D-310)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.527: more_attendees: attendees past those read are counted; keeps_busy: what blocks its time, for the warning too (D-310)
# v4.4.523: description and comment of a calendar message (D-310)
# v4.4.518: request_of: a date of a series that has no changed version of its own is made an item, so a mail about it never speaks for the series
# v4.4.516: without_dates: dates out of a copy until they are answered again; an answered date comes back (D-310)
# v4.4.515: text_items remembers the last text it read; ready_to_delete: a copy as the server should hold it before its delete, also when that delete waits for the connection (D-310)
# v4.4.514: text_items reads every calendar of a text, an item takes along every time zone used anywhere inside it, as its own calendar defines it; a delegator's answer adds whoever they name as not answered yet (D-310)
# v4.4.512: an ADD can be answered: answered_added, added_dates, and the answer and the delegate's invitation name the date as an occurrence (D-310)
# v4.4.510: a delegate's answer carries DELEGATED-FROM; told_silent, with_silence, without_organizer (D-310)
# v4.4.509: with_attendee_answer takes only the line of the attendee who answered (D-310)
# v4.4.508: text_items: a calendar text split into its items, of every kind, each a text of its own (items_text: some of them together);
#           journal entries and damaged items counted; a shared item without a UID gets a lasting one; item_count is gone (D-310)
# v4.4.507: busy times (VFREEBUSY): read from a message (BusyPeriod), made from your calendars (busy_periods) and written as an answer
#           (busy_reply); item_count: how many items a calendar text holds (D-310 step 4)
# v4.4.506: you are the organizer: an attendee's answer into your meeting, a proposal accepted (new time, SEQUENCE + 1, attendees
#           asked again), your meeting as a REQUEST to send, DECLINECOUNTER (D-310 step 3)
# v4.4.505: answering (your copy with your answer, the REPLY), following the organizer (updates keeping your answers, cancellations,
#           added dates, REFRESH), delegating (REQUEST to the delegate, REPLY DELEGATED); times compared as moments (D-310)
# v4.4.504: Initial calendar messages: the method, the kind, the item, its version (SEQUENCE, DTSTAMP), whether a newer one replaces it

MessageKind = Literal["event", "task", "freebusy", "journal"]
METHODS: frozenset[str] = frozenset({"PUBLISH", "REQUEST", "REPLY", "ADD", "CANCEL", "REFRESH", "COUNTER", "DECLINECOUNTER"})  # RFC 5546 section 1.4
_START_NEEDED: frozenset[str] = frozenset({"PUBLISH", "REQUEST", "ADD", "COUNTER", ""})  # these set a time: an event without one is damaged
_KINDS: dict[str, MessageKind] = {"VEVENT": "event", "VTODO": "task", "VFREEBUSY": "freebusy", "VJOURNAL": "journal"}
_MOST_STEPS: int = 100_000  # how many repeats of a rule a date from a message may lie from its start (in_meeting): about a quarter of a second of work
_ONE_DAY: int = 86400
_LONGEST_PROPOSAL: timedelta = timedelta(days=366)  # a proposal longer than this is no meeting, and the calendar would lay it out on every day
_MEETING_RULES: frozenset[str] = frozenset({"FREQ=DAILY", "FREQ=WEEKLY", "FREQ=MONTHLY", "FREQ=YEARLY"})
_RULE_SECONDS: dict[str, int] = {"SECONDLY": 1, "MINUTELY": 60, "HOURLY": 3600, "DAILY": 86400, "WEEKLY": 604800, "MONTHLY": 2678400, "YEARLY": 31622400}
_PARTSTATS: frozenset[str] = frozenset({"NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE", "DELEGATED", "COMPLETED", "IN-PROCESS"})  # RFC 5545 section 3.2.12
_RULE_TIMES: frozenset[str] = frozenset({"BYHOUR", "BYMINUTE", "BYSECOND"})  # a rule with these sets the times itself
_RULE_DAYS: frozenset[str] = frozenset({"BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH", "BYSETPOS"})  # a rule with these sets the days itself
_BUSY_KINDS: frozenset[str] = frozenset({"BUSY", "BUSY-TENTATIVE", "BUSY-UNAVAILABLE"})  # RFC 5545 section 3.2.9


@dataclass(frozen=True, slots=True)
class BusyPeriod:
    """A time someone is busy (a FREEBUSY period, RFC 5545 section 3.8.2.6)."""

    start: datetime
    end: datetime
    kind: str = "BUSY"  # BUSY, BUSY-TENTATIVE or BUSY-UNAVAILABLE


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
    more_attendees: int = 0  # attendees past MAXIMUM_ATTENDEES: counted, not read
    event: Event | None = None  # the event, when it has a start (an answer or a request for the latest version may have none)
    task: Task | None = None
    busy: tuple[BusyPeriod, ...] = ()  # busy times: what is busy in the period below
    period_start: Moment | None = None  # busy times: the period they are about (a request: the period asked for)
    period_end: Moment | None = None
    description: str = ""  # the item's own text
    comment: str = ""  # what the one who wrote added (COMMENT), in an answer or a proposal


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


def _attendees_past(component: icalendar.Component) -> int:
    props: Any = component.get("ATTENDEE")
    return max(0, len(props) - MAXIMUM_ATTENDEES) if isinstance(props, list) else 0


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
        description=_first(component, "DESCRIPTION"), comment=_first(component, "COMMENT"), more_attendees=_attendees_past(component),
    )
    if kind == "event":
        event = _event_from(component)
        if event is None and (component.get("DTSTART") is not None or method in _START_NEEDED):
            return None  # a start that cannot be read is a damaged item; a message that sets a time needs one (RFC 5546 section 3.2)
        return replace(found, event=event)  # None: no start written, as an answer or a request for the latest version may leave it out
    if kind == "task":
        return replace(found, task=replace(_task_from(component), uid=uid))
    if kind == "freebusy":
        return replace(found, busy=_busy_of(component), period_start=_moment(component, "DTSTART"), period_end=_moment(component, "DTEND"))
    return found


def _busy_of(component: icalendar.Component) -> tuple[BusyPeriod, ...]:
    """The busy periods of a VFREEBUSY, in order of time, at most MAXIMUM_DATES; free ones and ones that cannot be read are left out."""
    props: Any = component.get("FREEBUSY")
    found: list[BusyPeriod] = []
    for prop in props if isinstance(props, list) else ([props] if props is not None else []):
        for period in prop if isinstance(prop, list) else [prop]:
            start, end = getattr(period, "start", None), getattr(period, "end", None)
            kind = str(getattr(period, "params", {}).get("FBTYPE", "BUSY")).upper()
            if not isinstance(start, datetime) or not isinstance(end, datetime) or kind == "FREE":
                continue
            try:
                if end > start and len(found) < MAXIMUM_DATES:
                    found.append(BusyPeriod(start, end, kind if kind in _BUSY_KINDS else "BUSY"))  # another kind counts as busy (RFC 5545 3.2.9)
            except TypeError:  # one with a zone, one without
                continue
    return tuple(sorted(found, key=lambda each: _moment_key(each.start)))


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

_KEPT_MAIL = "X-CHRONO-MAIL-INVITE"  # on a declined copy in Local Folders: the mail it came in (that copy stays on this computer)
_SERVER_FREE = "X-MICROSOFT-CDO-BUSYSTATUS"  # how a server marks a copy free, beside TRANSP (measured on Kerio)
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


def _busy_again(component: icalendar.Component) -> None:
    """You attend after all: the item blocks your time again. A server may have marked it free when you declined, and keeps that by itself (D-310)."""
    for key in ("TRANSP", _SERVER_FREE):
        component.pop(key, None)
    component.add("TRANSP", "OPAQUE")


def _meant_free(calendar: icalendar.Calendar) -> bool:
    return any(_first(each, "TRANSP").upper() == "TRANSPARENT" for each in _items_of(calendar))


def _answer_item(component: icalendar.Component, address: str, answer: str, name: str, before: str, meant_free: bool = True) -> None:
    """Your answer on an item; `before` is what you answered until now: what you declined and now attend blocks your time again. So does a copy a server
    reset to unanswered with its own free mark of your Decline still on it (measured on Kerio): meant_free False, the organizer's message says nothing of free."""
    free_mark = before == "NEEDS-ACTION" and not meant_free and _first(component, _SERVER_FREE).upper() == "FREE"
    _set_answer(component, address, answer, name)
    if (before == "DECLINED" or free_mark) and answer in ("ACCEPTED", "TENTATIVE"):
        _busy_again(component)


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


def with_kept_mail(text: str, account: str, message_id: str | None) -> str:
    """A copy in Local Folders that you declined, with the mail it came in, whose mark tells other computers: when the copy goes, so does the mark (D-310)."""
    calendar = _parse(text) if message_id else None
    if calendar is None:
        return text
    calendar.pop(_KEPT_MAIL, None)
    calendar.add(_KEPT_MAIL, f"{account} {message_id}")
    return calendar.to_ical().decode("utf-8")


def declined_copy(text: str, address: str, name: str, account: str, message_id: str | None) -> str | None:
    """The copy of an invitation you declined with no copy anywhere, for Local Folders (D-310)."""
    copy_text = answered_copy(text, address, "DECLINED", name)
    return with_kept_mail(copy_text, account, message_id) if copy_text is not None else None


def kept_mail(text: str) -> tuple[str, str] | None:
    """The account and Message-ID of the mail a declined copy came in; None: it has none."""
    calendar = _parse(text)
    account, _, message_id = str(calendar.get(_KEPT_MAIL, "") if calendar is not None else "").partition(" ")
    return (account, message_id) if message_id else None


def you_declined(text: str, addresses: Collection[str]) -> bool:
    """Whether you declined the whole item (not one date of it)."""
    calendar = _parse(text)
    main = next((each for each in _items_of(calendar) if each.get("RECURRENCE-ID") is None), None) if calendar is not None else None
    return main is not None and any(_your_answer(main, address) == "DECLINED" for address in addresses)


def asked_again(stored: str, occurrence: Moment | None) -> str | None:
    """A kept item as the invitation it was, to answer it from the calendar: the whole item, or (occurrence) that one date, also one taken out of the series.
    It is your copy, with what you changed in it: what goes out comes from meeting_in_mail or shared_text."""
    calendar = _parse(stored)
    main = next((each for each in _items_of(calendar) if each.get("RECURRENCE-ID") is None), None) if calendar is not None else None
    if calendar is None:
        return None
    calendar.pop(_KEPT_MAIL, None)
    calendar.pop("METHOD", None)
    calendar.add("METHOD", "REQUEST")
    if occurrence is not None:
        date_item = next((each for each in _items_of(calendar) if _same_moment(_moment(each, "RECURRENCE-ID"), occurrence)), None)
        if date_item is None and main is None:
            return None
        if date_item is None and main is not None:
            date_item = copy.deepcopy(main)
            for name in ("RRULE", "RDATE", "EXDATE"):
                date_item.pop(name, None)
            start, end = _moment(main, "DTSTART"), _moment(main, "DTEND")
            date_item.pop("DTSTART", None)
            date_item.add("DTSTART", occurrence)
            if start is not None and end is not None and "DTEND" in date_item:
                date_item.pop("DTEND", None)
                date_item.add("DTEND", moved_like_the_start(end, start, occurrence))
            date_item.add("RECURRENCE-ID", occurrence)
        calendar.subcomponents = [*(each for each in calendar.subcomponents if each.name == "VTIMEZONE"), date_item]
    return calendar.to_ical().decode("utf-8")


def _organizers_date(mail_text: str, stored: str, occurrence: Moment) -> Moment:
    """A date of your copy as a date of the organizer's series: a series you moved yourself has its dates at your time."""
    sent, kept = _parse(mail_text), _parse(stored)
    series, own = (_main_of(sent) if sent is not None else None), (_main_of(kept) if kept is not None else None)
    theirs, yours = (_moment(series, "DTSTART") if series is not None else None), (_moment(own, "DTSTART") if own is not None else None)
    return moved_like_the_start(occurrence, yours, theirs) if theirs is not None and yours is not None else occurrence


def meeting_in_mail(mail_text: str, stored: str, asked: CalendarMessage, one_date: bool) -> str | None:
    """The organizer's own text of what you answer from the calendar (`asked`, from your copy `stored`), in an item of a mail: an invitation of the same
    version from the same organizer, about the same dates. Nothing you changed in your copy is in it (D-310). None: this mail is not the one."""
    sent = read_calendar_message(mail_text)
    if sent is None or sent.method != "REQUEST" or sent.organizer is None or asked.organizer is None or sent.organizer.address.strip().lower() != asked.organizer.address.strip().lower():
        return None
    moment = _organizers_date(mail_text, stored, asked.occurrence) if one_date and asked.occurrence is not None else None
    if moment is not None and not holds_occurrence(mail_text, moment):
        return None
    meeting = asked_again(mail_text, moment)
    found = read_calendar_message(meeting) if meeting is not None else None
    if meeting is None or found is None or found.sequence != asked.sequence or (moment is None and not _same_moment(found.occurrence, asked.occurrence)):
        return None
    if moment is None and asked.occurrence is not None and sorted(map(str, occurrences_of(meeting))) != sorted(map(str, occurrences_of(stored))):
        return None  # a copy of only some dates, answered as a whole: a mail speaks for it only when it names them all
    return meeting


def shared_text(text: str) -> str | None:
    """Your copy without what is yours alone: reminders, tags, colour, free or busy and every private mark. All that is left of a meeting whose mail is gone."""
    calendar = _parse(text)
    if calendar is None:
        return None
    for item in _items_of(calendar):
        item.subcomponents = [each for each in item.subcomponents if each.name != "VALARM"]
        for key in [each for each in item if each.upper() in ("CATEGORIES", "COLOR", "TRANSP") or each.upper().startswith("X-")]:
            item.pop(key)
    return calendar.to_ical().decode("utf-8")


def merged_answer(stored: str, message_text: str, address: str, answer: str, occurrence: Moment | None, take_message: bool, name: str = "") -> str | None:
    """Your copy after answering, when one is kept already. take_message: the message is a newer version, so what it changed is copied into the
    copy (_follow; for one occurrence: into that occurrence); else the kept copy stays as it is with your answer set in it (for one occurrence: in
    that occurrence, taken from the message when the copy has no changed version of it). An answer on all of it is on every date, whatever you
    answered on one (D-310); only a delegation leaves a date you answered otherwise."""
    kept = _parse(stored)
    message = _parse(message_text)
    if kept is None or message is None:
        return None
    kept.pop("METHOD", None)
    lower = address.strip().lower()
    series = _main_of(kept)
    free = _meant_free(message)
    if occurrence is not None and series is not None and answer != "DECLINED":
        _without_moment(series, "EXDATE", occurrence)  # a date taken out by Change my answer comes back with its answer

    if occurrence is None:
        every_date = answer != "DELEGATED"
        series_answer = _your_answer(series, lower) if series is not None else ""
        before = {id(each): _your_answer(each, lower) for each in _items_of(kept)}
        if take_message:
            _follow(kept, message, lower)
        for component in _items_of(kept):  # (a delegation: the series and the occurrences that followed it; one you answered otherwise keeps that)
            if every_date or component.get("RECURRENCE-ID") is None or _your_answer(component, lower) in ("", "NEEDS-ACTION", series_answer):
                _answer_item(component, lower, answer, name, before.get(id(component), ""), free)
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
                _answer_item(each, lower, answer, name, _your_answer(each, lower), free)
            continue
        was = present[0] if present else series
        before_answer = _your_answer(was, lower) if was is not None else ""
        if present:
            _take_fields(present[0], component)
        _answer_item(present[0] if present else _new_date(kept, series, component), lower, answer, name, before_answer, free)
    _copy_zones(message, kept)
    return kept.to_ical().decode("utf-8")


def _delegated_from(component: icalendar.Component, address: str) -> str:
    props: Any = component.get("ATTENDEE")
    people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
    return next((str(person.params["DELEGATED-FROM"]) for person in people if _address_of(person) == address and person.params.get("DELEGATED-FROM")), "")


def _no_server_marks(calendar: icalendar.Calendar) -> None:
    """What your copy tells its calendar server (RFC 6638) means nothing in a mail."""
    for line in _scheduled_lines(calendar):
        for key in ("SCHEDULE-AGENT", "SCHEDULE-STATUS", "SCHEDULE-FORCE-SEND"):
            line.params.pop(key, None)


def reply_text(message_text: str, address: str, answer: str, now: datetime, name: str = "", copy_text: str | None = None, organizer: str = "", bare: bool = False) -> str | None:
    """Your answer to the organizer (METHOD:REPLY, RFC 5546 section 3.2.3): per item its UID, the SEQUENCE of the request, DTSTAMP now, the
    organizer, your attendee line with your answer, the RECURRENCE-ID of one occurrence, and its title and time to read. None when the text
    holds nothing to answer. organizer: who it goes to, for an item that names nobody. bare: the text is your own copy, whose title and time may be
    your own: they stay out, as the RFC allows."""
    message = _parse(message_text)
    if message is None or not _items_of(message):
        return None
    _name_added_dates(message)
    _no_server_marks(message)
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
        for key in ("UID", "SEQUENCE", "RECURRENCE-ID", "ORGANIZER", *(() if bare else ("SUMMARY", "DTSTART", "DTEND", "DUE"))):
            value = item.get(key)
            if value is not None and not isinstance(value, list):
                answer_item[key] = value
        if organizer:
            answer_item.pop("ORGANIZER", None)  # (a line with a name and no address reaches nobody)
            answer_item.add("ORGANIZER", f"mailto:{organizer}")
        answer_item.add("DTSTAMP", now)
        own = kept_answers.get(str(_moment(item, "RECURRENCE-ID")), "")
        _set_answer(answer_item, lower, own if own and own != "NEEDS-ACTION" else answer, name)
        replaced = _delegated_from(item, lower)
        if replaced:
            answer_item["ATTENDEE"].params["DELEGATED-FROM"] = replaced  # who you go in the place of (RFC 5546 section 3.2.2.3)
        reply.add_component(answer_item)
    return reply.to_ical().decode("utf-8")


# --- a calendar server that mails by itself (D-310) ---------------------------------------------------------------------------------------

def _scheduled_lines(calendar: icalendar.Calendar) -> list[Any]:
    lines: list[Any] = []
    for item in _items_of(calendar):
        for key in ("ORGANIZER", "ATTENDEE"):
            props: Any = item.get(key)
            lines.extend(props if isinstance(props, list) else ([props] if props is not None else []))
    return lines


def told_silent(text: str) -> bool:
    """Whether a copy tells a calendar server not to mail about it (SCHEDULE-AGENT=CLIENT, RFC 6638)."""
    calendar = _parse(text)
    return calendar is not None and any(str(line.params.get("SCHEDULE-AGENT", "")).upper() == "CLIENT" for line in _scheduled_lines(calendar))


def with_silence(text: str, silent: bool) -> str | None:
    """The copy with its organizer and attendee lines marked SCHEDULE-AGENT=CLIENT, so a server that mails by itself leaves them alone (the
    organizer's line for an attendee's copy, the attendees' for an organizer's, RFC 6638), or with that mark taken off. None: not readable."""
    calendar = _parse(text)
    if calendar is None:
        return None
    for line in _scheduled_lines(calendar):
        line.params.pop("SCHEDULE-FORCE-SEND", None)
        if silent:
            line.params["SCHEDULE-AGENT"] = "CLIENT"
        else:
            line.params.pop("SCHEDULE-AGENT", None)
    return calendar.to_ical().decode("utf-8")


def without_organizer(text: str) -> str | None:
    """The copy as an item of your own, with no organizer and no attendees: a server that mails whatever it is told mails nothing when this one
    is deleted (tested). None: not readable."""
    calendar = _parse(text)
    if calendar is None:
        return None
    for item in _items_of(calendar):
        for key in ("ORGANIZER", "ATTENDEE"):
            item.pop(key, None)
    return calendar.to_ical().decode("utf-8")


BEFORE_SILENT: str = "silent"  # what a copy's delete needs first: the server told to stay silent,
BEFORE_LOUD: str = "loud"  # told to mail,
BEFORE_BARE: str = "bare"  # or the organizer taken off


def ready_to_delete(text: str, before: str) -> str | None:
    """The copy as the server should hold it when it is deleted: a server mails about a delete from what it holds. None: not readable."""
    return without_organizer(text) if before == BEFORE_BARE else with_silence(text, before == BEFORE_SILENT)


# --- following the organizer (D-310) -----------------------------------------------------------------------------------------------------

_YOUR_FIELDS: frozenset[str] = frozenset({"UID", "RECURRENCE-ID", "EXDATE", "CATEGORIES", "COLOR", COLOUR_ID_PROPERTY})  # yours: an update never writes these (D-310)
_SERIES_TIME: frozenset[str] = frozenset({"DTSTART", "DTEND", "DURATION", "DUE", "RRULE", "RDATE"})  # the time of a series: it reaches a changed date by moving it
_ORGANIZER_FIELDS: frozenset[str] = _SERIES_TIME | frozenset({"SUMMARY", "DESCRIPTION", "LOCATION", "ATTENDEE", "STATUS", "URL", "ATTACH", "CLASS", "PRIORITY", "GEO", "RESOURCES", "CONTACT", "COMMENT", "CONFERENCE"})
# (what a new version takes away by no longer writing it; anything else it does not write stays. Also the organizer: a mail that names none does not take back who it was)


def _moment_key(value: object) -> str:
    """A moment as one text for comparing: an aware time in UTC, a floating time or a day as written."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        try:
            return value.astimezone(UTC).isoformat()
        except (OverflowError, ValueError):
            return value.isoformat()
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _rule_text(component: icalendar.Component) -> str:
    rule: Any = component.get("RRULE")
    return rule.to_ical().decode("utf-8", "replace") if hasattr(rule, "to_ical") else ""


def _when(component: icalendar.Component) -> tuple[str, ...]:
    """The item's time as moments, not as written: the same moment in another zone or another way of writing is the same time; the same clock
    time in another zone is another moment. Extra dates (RDATE) compare as a set, however they are spread over lines. The list of removed dates
    (EXDATE) is no part of it: your own deleted dates are no change of time (D-310)."""
    found: list[str] = []
    for key in ("DTSTART", "DTEND", "DUE"):
        found.append(_moment_key(_moment(component, key)))
    duration: Any = component.get("DURATION")
    found.append(str(getattr(duration, "dt", "")) if duration is not None and not isinstance(duration, list) else "")
    found.append(_rule_text(component))
    found.append(",".join(sorted(_moment_key(each) for each in _moments(component, "RDATE"))))
    return tuple(found)


def _your_answer(component: icalendar.Component, address: str) -> str:
    props: Any = component.get("ATTENDEE")
    people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
    return next((str(person.params.get("PARTSTAT", "NEEDS-ACTION")).upper() for person in people if _address_of(person) == address), "")


def _main_of(calendar: icalendar.Calendar) -> icalendar.Component | None:
    return next((each for each in _items_of(calendar) if each.get("RECURRENCE-ID") is None), None)


def _copy_field(target: icalendar.Component, source: icalendar.Component, key: str) -> None:
    """One field as the organizer's new version has it."""
    if key in source:
        target[key] = copy.deepcopy(source[key])
    elif key in _ORGANIZER_FIELDS:
        target.pop(key, None)


def _take_fields(target: icalendar.Component, source: icalendar.Component) -> None:
    """The organizer's fields of a new version into an item you keep; yours stay, its reminders with them (D-310)."""
    for key in (set(source.keys()) | _ORGANIZER_FIELDS) - _YOUR_FIELDS:
        _copy_field(target, source, key)


def _field_text(component: icalendar.Component, key: str) -> list[bytes]:
    value: Any = component.get(key)
    return [each.to_ical() if hasattr(each, "to_ical") else str(each).encode("utf-8") for each in (value if isinstance(value, list) else [value])]


def _changed_fields(old: icalendar.Component, new: icalendar.Component) -> list[str]:
    """What a new version of a series changes, besides its time: what a changed date of it gets as well."""
    return [key for key in (set(old.keys()) | set(new.keys())) - _YOUR_FIELDS - _SERIES_TIME if _field_text(old, key) != _field_text(new, key)]


def _new_date(kept: icalendar.Calendar, main: icalendar.Component | None, component: icalendar.Component) -> icalendar.Component:
    """A date the organizer gives an item of its own, added to the copy: until now the series was your reminders and tags for that date, so it keeps them."""
    target = copy.deepcopy(component)
    if main is not None:
        target.subcomponents = copy.deepcopy([each for each in main.subcomponents if each.name == "VALARM"])
        for key in _YOUR_FIELDS & set(main.keys()) - {"UID", "RECURRENCE-ID", "EXDATE"}:
            target[key] = copy.deepcopy(main[key])
    kept.add_component(target)
    return target


def _on_date(override: icalendar.Component, moment: Moment) -> None:
    override.pop("RECURRENCE-ID", None)
    override.add("RECURRENCE-ID", moment)


def _moved_date(override: icalendar.Component, main: icalendar.Component, moment: Moment) -> None:
    """A changed date at the time of its series now, on its new date; it keeps what else is its own (D-310)."""
    fresh = _occurrence_of(main, moment)
    _on_date(override, moment)
    for key in ("DTSTART", "DTEND", "DURATION", "DUE"):
        _copy_field(override, fresh, key)


def _series_only(kept: icalendar.Calendar, main: icalendar.Component) -> str:
    """The copy as the series alone, to ask which dates it really has."""
    alone = copy.deepcopy(kept)
    alone.subcomponents = [each for each in alone.subcomponents if each.name not in ("VEVENT", "VTODO")]
    alone.add_component(copy.deepcopy(main))
    return alone.to_ical().decode("utf-8")


def _dates_of_series(series: str, main: icalendar.Component, dates: list[Moment]) -> set[str]:
    """Of these dates the ones the series really has, as in_meeting asks it of one date: the series is walked once, however many dates are asked."""
    near = [each for each in dates if _within_reach(main, each)]
    wanted = {_moment_key(each) for each in near}
    if main.name == "VTODO":
        return wanted - {_moment_key(each) for each in _excluded(main)}
    margin = timedelta(days=2)  # (the same moment read in any other zone)
    clocks = [clock for clock in map(wall_clock, near) if datetime.min + margin < clock < datetime.max - margin]  # the edge of the years: no date, as in in_meeting
    found: set[str] = set()
    if not clocks:
        return found
    first, last = min(clocks).replace(tzinfo=UTC) - margin, max(clocks) + margin
    for each in islice(occurrences_from(series, first), _MOST_STEPS):
        if wall_clock(each.start) > last:
            break
        if _moment_key(each.start) in wanted:
            found.add(_moment_key(each.start))
    return found


def _follow(kept: icalendar.Calendar, message: icalendar.Calendar, address: str) -> None:
    """What a newer version from the organizer changes, copied into the record you keep: a series is one record and a deleted date a note on it, so a
    date you deleted stays deleted, also when the series moves, and everything else that is yours stays (D-310). Your answer stands, unless the time
    changed, then it is asked again (RFC 5546 section 2.1.4). A new date or time also reaches a changed date, which keeps what else is its own; a
    changed date that is no date of the series any more goes."""
    main, new = _main_of(kept), _main_of(message)
    overrides = [each for each in _items_of(kept) if _moment(each, "RECURRENCE-ID") is not None]
    incoming = {_moment_key(_moment(each, "RECURRENCE-ID")): each for each in _items_of(message) if each.get("RECURRENCE-ID") is not None}
    _copy_zones(message, kept)
    moved = False
    changed: list[str] = []
    before: Moment | None = None
    if new is not None and main is None:
        main = copy.deepcopy(new)
        kept.add_component(main)
    elif new is not None and main is not None:
        yours = {_moment_key(each) for each in _excluded(main)}
        moved = _when(main) != _when(new) or any(_moment_key(each) not in yours for each in _excluded(new))
        changed = _changed_fields(main, new)
        answer, before = _your_answer(main, address), _moment(main, "DTSTART")
        _take_fields(main, new)
        if answer:
            _set_answer(main, address, "NEEDS-ACTION" if moved else answer)
    after = _moment(main, "DTSTART") if main is not None else None
    named = {_moment_key(each) for each in _moments(main, "RDATE")} if main is not None else set[str]()

    def follows(moment: Moment) -> Moment:
        return moment if not moved or before is None or after is None or _moment_key(moment) in named else moved_like_the_start(moment, before, after)

    if new is not None and main is not None:
        skipped = {_moment_key(each): each for each in [*map(follows, _excluded(main)), *_excluded(new)]}
        main.pop("EXDATE", None)
        for moment in skipped.values():
            main.add("EXDATE", moment)
    plain: icalendar.Component | None = None  # the series to move a date by: without its removed dates, which would be copied for every date
    dates_left: set[str] = set()
    if moved and overrides and main is not None:
        dates_left = _dates_of_series(_series_only(kept, main), main, [follows(moment) for each in overrides if (moment := _moment(each, "RECURRENCE-ID")) is not None])
        plain = copy.deepcopy(main)
        plain.pop("EXDATE", None)
    gone: set[int] = set()
    for override in overrides:
        moment = _moment(override, "RECURRENCE-ID")
        assert moment is not None  # (only those are in the list)
        date = follows(moment)
        theirs = incoming.pop(_moment_key(date), None)
        if theirs is None and plain is not None and _moment_key(date) not in dates_left:
            gone.add(id(override))
            continue
        answer = _your_answer(override, address)
        again = moved or (theirs is not None and _when(override) != _when(theirs))
        if theirs is not None:
            _take_fields(override, theirs)
        elif new is not None:
            for key in changed:
                _copy_field(override, new, key)
        if plain is not None and theirs is None:
            _moved_date(override, plain, date)
        elif moved:  # the message wrote this date itself: its time stands
            _on_date(override, date)
        if answer:
            _set_answer(override, address, "NEEDS-ACTION" if again else answer)
    kept.subcomponents = [each for each in kept.subcomponents if id(each) not in gone]
    series_answer = _your_answer(main, address) if main is not None else ""
    deleted = {_moment_key(each) for each in _excluded(main)} if main is not None else set[str]()
    for component in incoming.values():
        moment, start = _moment(component, "RECURRENCE-ID"), _moment(component, "DTSTART")
        if moment is not None and _moment_key(moment) in deleted:
            continue  # a date you deleted stays deleted
        target = _new_date(kept, main, component)
        if series_answer:  # an occurrence the copy has not changed: it takes the series' answer, unless the organizer moved it
            _set_answer(target, address, "NEEDS-ACTION" if moment is not None and start is not None and _moment_key(moment) != _moment_key(start) else series_answer)


def updated_copy(stored: str, message_text: str, address: str) -> str | None:
    """The copy after you took a newer version from the organizer: what it changed copied in (_follow)."""
    kept = _parse(stored)
    message = _parse(message_text)
    if kept is None or message is None or not _items_of(message):
        return None
    _follow(kept, message, address.strip().lower())
    kept.pop("METHOD", None)
    return kept.to_ical().decode("utf-8")


def cancelled_copy(stored: str, moments: Iterable[Moment] = (), sequence: int | None = None) -> str | None:
    """The copy Cancelled, and nothing else about it changed (D-316: it still exists): those dates, or with none all of it: then also a changed date with no
    status of its own, a date with its own status keeps it. sequence: the version the cancellation has. None: not readable."""
    kept = _parse(stored)
    if kept is None:
        return None
    main = _main_of(kept)
    dates = list(moments)
    found = [item for moment in dates if (item := _date_item(kept, main, stored, moment)) is not None] if dates else _items_of(kept)
    for item in found:
        if dates or item is main or item.get("STATUS") is None:
            item.pop("STATUS", None)
            item.add("STATUS", "CANCELLED")
        if sequence is not None:
            item.pop("SEQUENCE", None)
            item.add("SEQUENCE", sequence)
    return kept.to_ical().decode("utf-8")


def restored_copy(stored: str, moments: Iterable[Moment] = (), sequence: int | None = None, yours: Collection[str] = ()) -> str | None:
    """The copy with Cancelled taken off and no status left, whatever it had before (D-310): those dates, or with none every item that is Cancelled. sequence:
    the version of your own meeting told of it: every other attendee answers again; None: your answers and the version stay. None: not readable."""
    kept = _parse(stored)
    if kept is None:
        return None
    main = _main_of(kept)
    dates = list(moments)
    found = [item for moment in dates if (item := _date_item(kept, main, stored, moment)) is not None] if dates else _items_of(kept)
    for item in found:
        if _first(item, "STATUS").upper() == "CANCELLED":
            item.pop("STATUS", None)
        if sequence is not None:
            item.pop("SEQUENCE", None)
            item.add("SEQUENCE", sequence)
            _asked_again(item, yours)
    return kept.to_ical().decode("utf-8")


def cancelled_in(text: str, moments: Iterable[Moment] = ()) -> bool:
    """Whether the copy is Cancelled: those dates (each by its own status, else by the series'), or with none all of it: as cancelled_copy leaves it,
    the series and every changed date with no status of its own."""
    calendar = _parse(text)
    if calendar is None:
        return False
    items = _items_of(calendar)
    main = _main_of(calendar)
    dates = list(moments)
    if not dates:
        found = items if main is None else [each for each in items if each is main or not _first(each, "STATUS")]
        return bool(found) and all(_first(each, "STATUS").upper() == "CANCELLED" for each in found)

    def status(moment: Moment) -> str:
        own = next((each for each in items if _same_moment(_moment(each, "RECURRENCE-ID"), moment)), main)  # a changed date goes by what it says itself, as the calendar shows it
        return _first(own, "STATUS").upper() if own is not None else ""

    return all(status(moment) == "CANCELLED" for moment in dates)


def with_organizer(text: str, address: str) -> str | None:
    """The copy with this address as its organizer; with none, no organizer: an invitation that names none gets what the person says (D-310). None: not readable."""
    calendar = _parse(text)
    if calendar is None:
        return None
    for item in _items_of(calendar):
        item.pop("ORGANIZER", None)
        if address:
            item.add("ORGANIZER", icalendar.vCalAddress(f"mailto:{address}"))
    return calendar.to_ical().decode("utf-8")


def organizer_of(text: str) -> str:
    """The address of the copy's organizer; "": it names none."""
    calendar = _parse(text)
    item = next(iter(_items_of(calendar)), None) if calendar is not None else None
    organizer: Any = item.get("ORGANIZER") if item is not None else None
    return _address_of(organizer) if organizer is not None and not isinstance(organizer, list) else ""


def _copy_zones(source: icalendar.Calendar, target: icalendar.Calendar) -> None:
    """The time zones the message brings that the copy lacks (a new TZID must never be left undefined)."""
    for zone in source.walk("VTIMEZONE"):
        if not any(_text(each, "TZID") == _text(zone, "TZID") for each in target.walk("VTIMEZONE")):
            target.subcomponents.insert(0, zone)


def _without_moment(item: icalendar.Component, key: str, moment: Moment) -> None:
    """One date out of a list of dates of an item (EXDATE, RDATE)."""
    dates = _moments(item, key)
    if any(_same_moment(each, moment) for each in dates):
        item.pop(key, None)
        for each in dates:
            if not _same_moment(each, moment):
                item.add(key, each)


def without_dates(stored: str, moments: Iterable[Moment]) -> str | None:
    """The copy without these dates, for as long as they are not answered (Change my answer): an extra date goes from the list of extra dates, a
    date of the rule is skipped, and their own items go. "" when nothing is left (the item goes); None when it cannot be read."""
    kept = _parse(stored)
    if kept is None:
        return None
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    for moment in moments:
        kept.subcomponents = [each for each in kept.subcomponents if not (each.name in ("VEVENT", "VTODO") and _same_moment(_moment(each, "RECURRENCE-ID"), moment))]
        if main is None:
            continue
        if any(_same_moment(each, moment) for each in _moments(main, "RDATE")):
            _without_moment(main, "RDATE", moment)
        elif not any(_same_moment(each, moment) for each in _moments(main, "EXDATE")):
            main.add("EXDATE", moment)
    return kept.to_ical().decode("utf-8") if _items_of(kept) else ""


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


def _name_added_dates(message: icalendar.Calendar) -> None:
    """An ADD names its new dates by their start only; everything said about one of them names it as an occurrence, or it would be about the series."""
    if str(message.get("METHOD", "")).upper() != "ADD":
        return
    for item in _items_of(message):
        start = _moment(item, "DTSTART")
        if start is not None and item.get("RECURRENCE-ID") is None:
            item.add("RECURRENCE-ID", start)


def added_dates(message_text: str) -> list[Moment]:
    """The dates an ADD brings."""
    message = _parse(message_text)
    return [start for start in (_moment(each, "DTSTART") for each in _items_of(message)) if start is not None] if message is not None else []


def answered_added(text: str, message_text: str, address: str, answer: str, name: str = "") -> str | None:
    """The copy with your answer on the dates of an ADD it holds; the series itself keeps its own answer. None: it holds none of them."""
    kept = _parse(text)
    dates = added_dates(message_text)
    if kept is None:
        return None
    found = [each for each in _items_of(kept) if any(_same_moment(_moment(each, "RECURRENCE-ID"), date) for date in dates)]
    for item in found:
        _set_answer(item, address.strip().lower(), answer, name)
    return kept.to_ical().decode("utf-8") if found else None


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


def delegated_texts(message_text: str, address: str, delegate: str, now: datetime, name: str = "", bare: bool = False) -> tuple[str, str] | None:
    """Delegating an invitation (RFC 5546 section 3.2.2.3): the request passed on to the delegate (your line DELEGATED with DELEGATED-TO, a line
    for the delegate with DELEGATED-FROM and RSVP), and the answer to the organizer (your line DELEGATED, DELEGATED-TO). None: nothing to pass on.
    bare: as reply_text."""
    message = _parse(message_text)
    if message is None or not _items_of(message):
        return None
    _name_added_dates(message)
    _no_server_marks(message)
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
    reply = reply_text(message_text, address, "DELEGATED", now, name, bare=bare)
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
    return found[:MAXIMUM_DATES]


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


def occurrences_of(text: str) -> list[Moment]:
    """The occurrences a message is about (each item's RECURRENCE-ID), in order; empty for one about the whole item."""
    calendar = _parse(text)
    if calendar is None:
        return []
    return [moment for each in _items_of(calendar) if (moment := _moment(each, "RECURRENCE-ID")) is not None]


# --- you are the organizer (D-310 step 3) ------------------------------------------------------------------------------------------------


def _about(meeting: icalendar.Calendar, message: icalendar.Calendar) -> list[icalendar.Component]:
    """The items of a message about this meeting: another UID in the same text is another item, never this one."""
    uids = {_first(each, "UID") for each in _items_of(meeting)}
    return [each for each in _items_of(message) if _first(each, "UID") in uids]


def _within_reach(main: icalendar.Component, moment: Moment) -> bool:
    """Whether a date lies within what is looked at for this series: a date from a message is hostile input, and walking a rule to year 9999 takes
    seconds at every display. The reach is _MOST_STEPS repeats of the rule (a daily series: over 270 years; an hourly one: 11)."""
    start = _moment(main, "DTSTART")
    rule = _single(main, "RRULE")
    if start is None or rule is None:
        return True
    try:
        frequency = str((rule.get("FREQ") or [""])[0]).upper()
        interval = max(1, int((rule.get("INTERVAL") or [1])[0]))
        distance = (wall_clock(moment) - wall_clock(start)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return False
    return -_ONE_DAY <= distance <= _MOST_STEPS * _RULE_SECONDS.get(frequency, 1) * interval  # (a day before: the same moment read in another zone)


def in_meeting(text: str, moment: Moment) -> bool:
    """Whether this date is one of the meeting's: a changed version of it, or a date its series really has (not excluded, made by its rule or named
    by it; the new place of a changed date is not one). A task's repeats are not worked out here: any date its main item does not exclude.
    Never raises: a date that cannot be looked at (the edge of the years, out of reach) is not one."""
    calendar = _parse(text)
    if calendar is None:
        return False
    if any(_same_moment(_moment(each, "RECURRENCE-ID"), moment) for each in _items_of(calendar)):
        return True
    main = next((each for each in _items_of(calendar) if each.get("RECURRENCE-ID") is None), None)
    if main is None or not _within_reach(main, moment):
        return False
    if main.name == "VTODO":
        return holds_occurrence(text, moment, main_only=True)
    try:
        day = moment if isinstance(moment, datetime) else datetime(moment.year, moment.month, moment.day, tzinfo=UTC)
        day = day if day.tzinfo is not None else day.replace(tzinfo=UTC)
        found = occurrences([text], day - timedelta(minutes=1), day + timedelta(minutes=1), UTC)
    except (OverflowError, ValueError):
        return False
    return any(_same_moment(each.start, moment) and not each.changed for each in found)


def _partstat(person: Any) -> str:  # noqa: ANN401 - icalendar's property objects are untyped
    """An attendee's answer from a message, when it is one (a value written twice comes as a list); "" otherwise."""
    value: Any = person.params.get("PARTSTAT", "NEEDS-ACTION")
    answer = value.upper() if isinstance(value, str) else ""
    return answer if answer in _PARTSTATS else ""


def _repeats(main: icalendar.Component) -> bool:
    return main.get("RRULE") is not None or main.get("RDATE") is not None


def _date_item(kept: icalendar.Calendar, main: icalendar.Component | None, stored: str, moment: Moment) -> icalendar.Component | None:
    """The item of one date of your meeting: its changed version; the meeting itself when it does not repeat (that date is all it has); else that date
    made an item of its own. None when it is no date of the meeting."""
    target = next((each for each in _items_of(kept) if _same_moment(_moment(each, "RECURRENCE-ID"), moment)), None)
    if target is not None or main is None or not in_meeting(stored, moment):
        return target
    if not _repeats(main):
        return main
    target = _occurrence_of(main, moment)
    kept.add_component(target)
    return target


def _take_line(target: icalendar.Component, person: Any) -> None:  # noqa: ANN401 - icalendar's property objects are untyped
    """An attendee's line of an answer into one item of your meeting; a line not on your list is added, as written (not every attendee is a mail address)."""
    address = _address_of(person)
    mine: Any = target.get("ATTENDEE")
    lines: list[Any] = mine if isinstance(mine, list) else ([mine] if mine is not None else [])
    line = next((each for each in lines if _address_of(each) == address), None)
    if line is None:
        line = icalendar.vCalAddress(str(person).strip())
        for key in ("CN", "DELEGATED-FROM"):
            if person.params.get(key):
                line.params[key] = person.params[key]
        target.add("ATTENDEE", line)
    line.params["PARTSTAT"] = _partstat(person)
    line.params.pop("RSVP", None)
    line.params.pop("DELEGATED-TO", None)  # an earlier delegation does not outlive a newer answer
    if person.params.get("DELEGATED-TO"):
        line.params["DELEGATED-TO"] = person.params["DELEGATED-TO"]


def _delegate_lines(target: icalendar.Component, person: Any, reply_lines: list[Any]) -> None:  # noqa: ANN401 - icalendar's property objects are untyped
    """Whoever an attendee delegated to joins the meeting as not answered yet, when not on it: their answer comes only from their own mail."""
    named: Any = person.params.get("DELEGATED-TO")
    if _partstat(person) != "DELEGATED" or not named:
        return
    mine: Any = target.get("ATTENDEE")
    known = {_address_of(each) for each in (mine if isinstance(mine, list) else ([mine] if mine is not None else []))}
    for delegate in named if isinstance(named, list) else [named]:
        address = str(delegate).strip()
        lower = _address_of(icalendar.vCalAddress(address))
        if not address or any(character.isspace() for character in address) or lower in known:
            continue
        known.add(lower)
        line = icalendar.vCalAddress(address)
        name = next((each.params.get("CN") for each in reply_lines if _address_of(each) == lower), None)
        if name:
            line.params["CN"] = name
        line.params["PARTSTAT"] = "NEEDS-ACTION"
        line.params["DELEGATED-FROM"] = str(person).strip()
        target.add("ATTENDEE", line)


def with_attendee_answer(stored: str, reply: str, who: str) -> str | None:
    """Your meeting with an attendee's answer (a REPLY, RFC 5546 section 3.2.3): per item of the answer (one occurrence, or the meeting), each of
    the line of `who` (the attendee who answered: nobody answers for someone else) into yours: their PARTSTAT and DELEGATED-TO; a line not on your list (someone it was passed on to) added, and whoever they delegated to, as not answered yet. An
    answer to the series goes onto the dates that followed it too; one they answered otherwise keeps that. None when the meeting holds nothing it is about."""
    kept = _parse(stored)
    answer = _parse(reply)
    if kept is None or answer is None:
        return None
    taken = False
    for item in _about(kept, answer):
        moment = _moment(item, "RECURRENCE-ID")
        main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
        target = _date_item(kept, main, stored, moment) if moment is not None else main
        targets = [target] if target is not None else (_items_of(kept) if moment is None else list[icalendar.Component]())  # (a whole answer, a copy of single dates: all of them)
        taken = taken or bool(targets)
        props: Any = item.get("ATTENDEE")
        people: list[Any] = props if isinstance(props, list) else ([props] if props is not None else [])
        for person in people:
            address = _address_of(person)
            if address.strip().lower() != who.strip().lower() or any(character.isspace() for character in address) or not _partstat(person):
                continue  # someone else's line, or a damaged one; an answer that is none changes nothing
            following: list[icalendar.Component] = []
            if moment is None and main is not None:
                series_answer = _your_answer(main, address)
                following = [each for each in _items_of(kept) if each.get("RECURRENCE-ID") is not None and _your_answer(each, address) in ("NEEDS-ACTION", series_answer)]
            for each in [*targets, *following]:
                _take_line(each, person)
                _delegate_lines(each, person, people)
    return kept.to_ical().decode("utf-8") if taken else None


def _occurrence_of(main: icalendar.Component, moment: Moment) -> icalendar.Component:
    """One date of a series as a changed occurrence of its own: the series' item at that date, its length kept."""
    occurrence = copy.deepcopy(main)
    start, end, due = _moment(main, "DTSTART"), _moment(main, "DTEND"), _moment(main, "DUE")
    for key in ("RRULE", "RDATE", "EXDATE", "DTSTART", "DTEND", "DUE", "RECURRENCE-ID"):
        occurrence.pop(key, None)
    occurrence.add("RECURRENCE-ID", moment)
    occurrence.add("DTSTART", moment)
    for key, until in (("DTEND", end), ("DUE", due)):
        if start is not None and until is not None and type(start) is type(moment):
            try:
                occurrence.add(key, moment + (until - start))  # type: ignore[operator]
            except (TypeError, OverflowError):  # also the edge of the years: it ends at its start
                pass
    return occurrence


def _single(component: icalendar.Component, key: str) -> Any:  # noqa: ANN401 - icalendar's property objects are untyped
    """A property written once; written twice (hostile or damaged) it counts as not written."""
    value: Any = component.get(key)
    return None if isinstance(value, list) else value


def _length_of(component: icalendar.Component) -> timedelta | None:
    """How long the item lasts; None when it does not say, or its start and end cannot be compared."""
    start, end = _moment(component, "DTSTART"), _moment(component, "DTEND")
    duration: Any = getattr(_single(component, "DURATION"), "dt", None)
    if isinstance(duration, timedelta):
        return duration
    if start is None or end is None or isinstance(start, datetime) != isinstance(end, datetime):
        return None
    try:
        return end - start  # type: ignore[operator]
    except TypeError:  # one with a zone, one without
        return None


def _take_time(target: icalendar.Component, proposal: icalendar.Component) -> None:
    """The proposed time and place into an item. A proposal that names no end keeps the item's length; a task's DUE moves like a start."""
    start, length = _moment(target, "DTSTART"), _length_of(target)
    place = _single(proposal, "LOCATION")
    if place is not None:
        target["LOCATION"] = place
    zone = start.tzinfo if isinstance(start, datetime) else None

    def take(key: str, value: Any) -> None:  # noqa: ANN401 - icalendar's property objects are untyped
        """In the item's own zone: a series on Tuesdays stays on Tuesdays when the proposal is written in UTC."""
        moment: Any = getattr(value, "dt", None)
        if zone is not None and isinstance(moment, datetime) and moment.tzinfo is not None:
            target.pop(key, None)
            target.add(key, moment.astimezone(zone))
        else:
            target[key] = value

    for key in ("DTSTART", "DUE"):
        value = _single(proposal, key)
        if value is not None:
            take(key, value)
    ends = [(key, value) for key in ("DTEND", "DURATION") if (value := _single(proposal, key)) is not None]
    if ends:
        target.pop("DTEND", None)
        target.pop("DURATION", None)
        for key, value in ends:
            take(key, value)
    elif target.get("DTEND") is not None:
        target.pop("DTEND", None)
        moved = _moment(target, "DTSTART")
        if moved is not None and length is not None and isinstance(start, datetime) == isinstance(moved, datetime):
            try:
                target.add("DTEND", moved + length)
            except OverflowError:  # the edge of the years: the item ends at its start
                pass


def _same_rule(first: str, second: str) -> bool:
    """Whether two rules repeat alike, however they are written: the order of their parts, a written INTERVAL=1 and WKST make no difference, nor
    their last moment (it moves with the series, _until_moved)."""
    def meaning(rule: str) -> frozenset[tuple[str, frozenset[str]]]:
        parts = (part.split("=", 1) for part in rule.upper().split(";") if "=" in part)
        return frozenset((key, frozenset(value.split(","))) for key, value in parts if key not in ("WKST", "UNTIL") and (key, value) != ("INTERVAL", "1"))

    return meaning(first) == meaning(second)


def _until_moved(main: icalendar.Component, before: Moment, after: Moment) -> None:
    """A rule's last moment moves with its series, or its last date would be lost; a time in a zone is written in UTC (RFC 5545 section 3.3.10)."""
    rule = _single(main, "RRULE")
    until: Any = rule.get("UNTIL") if isinstance(rule, icalendar.vRecur) else None  # (a rule the library cannot read has none to move)
    if not until:
        return
    moved = until[0]
    try:
        if isinstance(moved, datetime) and isinstance(before, datetime) and (moved.tzinfo is None) != (before.tzinfo is None):
            moved = moved + (wall_clock(after) - wall_clock(before))  # (not as RFC 5545 asks, but written: a floating start with a last moment in UTC)
        elif isinstance(moved, datetime) or not isinstance(before, datetime):
            moved = moved_like_the_start(moved, before, after)
        else:  # a day as the last moment of a timed series (not as RFC 5545 asks, but written): it moves by whole days
            moved = moved + (_day_of(after) - _day_of(before))
        rule["UNTIL"] = [moved.astimezone(UTC) if isinstance(moved, datetime) and moved.tzinfo is not None else moved]
    except OverflowError:  # the edge of the years: it stays
        return


def _series_moved(kept: icalendar.Calendar, main: icalendar.Component, proposal: icalendar.Component) -> None:
    """A proposal for a whole series, the way the calendar moves one (commands_calendar._following_the_start): its skipped dates (EXDATE) and
    changed dates (RECURRENCE-ID) and its last moment (UNTIL) move on the wall clock with the start, so a cancelled date
    never comes back, a changed one never stays behind and the last one is never lost; a changed date with its own time keeps that time. Dates the series names itself (RDATE) never move. They stay where they are when
    the rule sets the hours, or the day moved and the rule changed or sets the days."""
    before, rule, length = _moment(main, "DTSTART"), _rule_text(main), _length_of(main)
    _take_time(main, proposal)
    new_rule = _single(proposal, "RRULE")
    if new_rule is not None and not _same_rule(_rule_text(proposal), rule):
        main["RRULE"] = new_rule
    after = _moment(main, "DTSTART")
    if before is None or after is None:
        return
    parts = {part.split("=", 1)[0].strip().upper() for part in rule.split(";")}
    day_moved = _day_of(before) != _day_of(after)
    if parts & _RULE_TIMES or (day_moved and (_rule_text(main) != rule or parts & _RULE_DAYS)):
        return
    _until_moved(main, before, after)
    named = _moments(main, "RDATE")

    def follows(moment: Moment) -> Moment:
        return moment if any(_same_moment(moment, each) for each in named) else moved_like_the_start(moment, before, after)

    skipped = _excluded(main)
    main.pop("EXDATE", None)
    for moment in skipped:
        main.add("EXDATE", follows(moment))
    new_length = _length_of(main)
    for override in _items_of(kept):
        moment = _moment(override, "RECURRENCE-ID")
        if moment is None:
            continue
        moved = follows(moment)
        series_time = _same_moment(_moment(override, "DTSTART"), moment) and _length_of(override) == length
        override.pop("RECURRENCE-ID", None)
        override.add("RECURRENCE-ID", moved)
        if series_time:
            override.pop("DTSTART", None)
            override.add("DTSTART", moved)
            override.pop("DTEND", None)
            override.pop("DURATION", None)
            if new_length is not None:
                try:
                    override.add("DTEND", moved + new_length)
                except OverflowError:  # the edge of the years: it ends at its start
                    pass


def _day_of(moment: Moment) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def _sound_values(item: icalendar.Component) -> bool:
    """Whether every time of a proposal can be read as what it should be: a proposal never puts a value into your meeting that would damage it."""
    for key in ("DTSTART", "DTEND", "DUE"):
        if item.get(key) is not None and _moment(item, key) is None:
            return False
    if item.get("DURATION") is not None:
        duration: Any = getattr(_single(item, "DURATION"), "dt", None)
        if item.get("DTEND") is not None or not isinstance(duration, timedelta) or duration < timedelta(0):
            return False
    rule = item.get("RRULE")
    if rule is None:
        return True
    start = _moment(item, "DTSTART")
    if not isinstance(_single(item, "RRULE"), icalendar.vRecur) or start is None:
        return False
    text = _rule_text(item)
    if not any(part.upper() in _MEETING_RULES for part in text.split(";")):
        return False  # an attendee never proposes a meeting every second
    interval: Any = _single(item, "RRULE").get("INTERVAL")
    if interval and int(interval[0]) < 1:
        return False  # (INTERVAL=0 never gets to a next date)
    try:
        return next(iter(rrulestr(text, dtstart=wall_clock(start), ignoretz=True)), None) is not None  # (a rule that makes no date at all: BYHOUR=25)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return False


def _due_after_start(item: icalendar.Component) -> bool:
    """Whether a task's due moment is not before its start (RFC 5545 section 3.8.2.3); an item with only one of them has nothing to compare."""
    start, due = _moment(item, "DTSTART"), _moment(item, "DUE")
    if start is None or due is None:
        return True
    try:
        return isinstance(start, datetime) == isinstance(due, datetime) and due >= start  # type: ignore[operator]
    except TypeError:  # one with a zone, one without
        return False


def proposal_usable(stored: str, counter: str) -> bool:
    """Whether Accept proposal has something to do: the proposal changes the time or the place of what it is about, its times can be used (an end after
    its start, of the same kind), and it does not move a series off the days its rule sets. Never raises: a time at the edge of the years cannot be used."""
    try:
        return _proposal_changes(stored, counter)
    except (OverflowError, ValueError):
        return False


def _proposal_changes(stored: str, counter: str) -> bool:
    kept = _parse(stored)
    proposal = _parse(counter)
    if kept is None or proposal is None:
        return False
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    changes = False
    for item in _about(kept, proposal):
        if not _sound_values(item):
            return False
        start, end = _moment(item, "DTSTART"), _moment(item, "DTEND")
        try:
            if start is not None and end is not None and (isinstance(start, datetime) != isinstance(end, datetime) or end < start):  # type: ignore[operator]
                return False
        except TypeError:  # one with a zone, one without
            return False
        moment = _moment(item, "RECURRENCE-ID")
        target = main if moment is None else next((each for each in _items_of(kept) if _same_moment(_moment(each, "RECURRENCE-ID"), moment)), None)
        if target is None and moment is not None and main is not None and in_meeting(stored, moment):
            target = _occurrence_of(main, moment) if _repeats(main) else main
        if target is None:
            continue
        rule = _rule_text(target)
        if moment is None and start is not None and rule and (not _rule_text(item) or _same_rule(_rule_text(item), rule)):
            old = _moment(target, "DTSTART")
            parts = {part.split("=", 1)[0].strip().upper() for part in rule.split(";")}
            in_zone = start.astimezone(old.tzinfo) if isinstance(old, datetime) and isinstance(start, datetime) and old.tzinfo is not None and start.tzinfo is not None else start
            if old is not None and _day_of(old) != _day_of(in_zone) and parts & _RULE_DAYS:
                return False
        moved = copy.deepcopy(target)
        _take_time(moved, item)
        if (length := _length_of(moved)) is not None and not timedelta(0) <= length <= _LONGEST_PROPOSAL:
            return False
        if not _due_after_start(moved):
            return False
        changes = changes or _when(moved) != _when(target) or _length_of(moved) != _length_of(target) or _first(moved, "LOCATION") != _first(target, "LOCATION")
        changes = changes or (moment is None and bool(_rule_text(item)) and not _same_rule(_rule_text(item), rule))
    if not changes or main is None or not any(each.get("RECURRENCE-ID") is None for each in _about(kept, proposal)):
        return changes
    moved_text = _moved_meeting(stored, counter, "")  # a series moved must still have its first date: not past its end, not on a skipped date
    first = read_calendar_message(moved_text, None) if moved_text is not None else None
    begins = first.event.start if first is not None and first.event is not None else None
    return moved_text is not None and (begins is None or in_meeting(moved_text, begins))


def countered(stored: str, counter: str, organizer: str) -> str | None:
    """Your meeting as an attendee proposed (a COUNTER accepted, RFC 5546 section 3.2.7): the time and place the card shows, of the whole series with its
    dates, or only of the dates the proposal is about; SEQUENCE one higher, and every attendee but you asked again (NEEDS-ACTION, RSVP); who passed it
    on stays so. None when there is nothing to take (proposal_usable)."""
    return _moved_meeting(stored, counter, organizer) if proposal_usable(stored, counter) else None


def _asked_again(target: icalendar.Component, yours: Collection[str]) -> None:
    """Every attendee of this item but you answers again; someone who passed it on has no answer to give."""
    props: Any = target.get("ATTENDEE")
    for person in props if isinstance(props, list) else ([props] if props is not None else []):
        if _address_of(person) not in yours and str(person.params.get("PARTSTAT", "")).upper() != "DELEGATED":
            person.params["PARTSTAT"] = "NEEDS-ACTION"
            person.params["RSVP"] = "TRUE"


def _moved_meeting(stored: str, counter: str, organizer: str) -> str | None:
    kept = _parse(stored)
    proposal = _parse(counter)
    if kept is None or proposal is None:
        return None
    items = _about(kept, proposal)
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    whole = next((each for each in items if each.get("RECURRENCE-ID") is None), None)
    if whole is not None and main is None:
        return None
    targets: list[icalendar.Component] = []
    for item in items:  # the dates first: a series moved after them takes them along, each keeping the time proposed for it
        moment = _moment(item, "RECURRENCE-ID")
        target = _date_item(kept, main, stored, moment) if moment is not None else None
        if target is not None:
            _take_time(target, item)
            targets.append(target)
    if whole is not None and main is not None:
        _series_moved(kept, main, whole)
        targets = _items_of(kept)
    if not targets:
        return None
    lower = organizer.strip().lower()
    for target in targets:
        try:
            sequence = max(0, int(str(target.get("SEQUENCE", 0))))
        except (ValueError, OverflowError):
            sequence = 0
        target.pop("SEQUENCE", None)
        target.add("SEQUENCE", sequence + 1)
        _asked_again(target, (lower,))
    _copy_zones(proposal, kept)
    return kept.to_ical().decode("utf-8")


def request_of(stored: str, now: datetime, method: str = "REQUEST", occurrences: tuple[Moment, ...] = ()) -> str | None:
    """Your meeting as a calendar message to send (REQUEST, or CANCEL), with DTSTAMP now (RFC 5546 sections 3.2.2, 3.2.5); with `occurrences`
    only those dates, so an attendee's copy of the series is not taken for an older version of the same one."""
    kept = _parse(stored)
    if kept is None or not _items_of(kept):
        return None
    main = next((each for each in _items_of(kept) if each.get("RECURRENCE-ID") is None), None)
    chosen = [each for each in (_date_item(kept, main, stored, moment) for moment in occurrences) if each is not None and each is not main]
    if chosen:  # (none: a meeting that does not repeat, where that date is the meeting)
        kept.subcomponents = [each for each in kept.subcomponents if each.name not in ("VEVENT", "VTODO") or any(each is other for other in chosen)]
    kept.pop("METHOD", None)
    kept.add("METHOD", method)
    for item in _items_of(kept):
        item.pop("DTSTAMP", None)
        item.add("DTSTAMP", now)
    return kept.to_ical().decode("utf-8")


def attendee_addresses(text: str) -> list[str]:
    """The mail addresses of every attendee of every item, in order, each once; a line that is no mail address, or a damaged one, has none to write to."""
    calendar = _parse(text)
    found: dict[str, None] = {}
    for item in _items_of(calendar) if calendar is not None else []:
        props: Any = item.get("ATTENDEE")
        for person in props if isinstance(props, list) else ([props] if props is not None else []):
            address = _address_of(person)
            if address.isascii() and address.count("@") == 1 and not any(character.isspace() or character in '<>,;"' for character in address):
                found[address] = None
    return list(found)


def declinecounter_text(counter: str, now: datetime) -> str | None:
    """No to an attendee's proposal (METHOD:DECLINECOUNTER, RFC 5546 section 3.2.8): UID, SEQUENCE, organizer, the attendee who proposed, the
    occurrence, DTSTAMP now."""
    proposal = _parse(counter)
    if proposal is None or not _items_of(proposal):
        return None
    message = icalendar.Calendar()
    message.add("PRODID", "-//Chrono Mail//Chrono Mail//EN")
    message.add("VERSION", "2.0")
    message.add("METHOD", "DECLINECOUNTER")
    for item in _items_of(proposal):
        answer = icalendar.Event() if item.name == "VEVENT" else icalendar.Todo()
        for key in ("UID", "SEQUENCE", "RECURRENCE-ID", "ORGANIZER", "ATTENDEE"):
            value = item.get(key)
            if value is not None:
                answer[key] = value
        answer.add("DTSTAMP", now)
        message.add_component(answer)
    return message.to_ical().decode("utf-8")


@dataclass(frozen=True, slots=True)
class TextItems:
    """What a calendar text holds, item by item: an event with its changed dates is one item, an item without a UID is one by itself."""

    texts: tuple[str, ...] = ()  # each item as a calendar text of its own, with the text's METHOD and the time zones it uses
    kinds: tuple[MessageKind, ...] = ()
    uids: tuple[str, ...] = ()
    journals: int = 0  # journal entries: left out (B-32)
    damaged: int = 0  # items that cannot be read: left out


def _method_of(calendar: icalendar.Calendar) -> str:
    method = _text(calendar, "METHOD").upper()
    return method if method in METHODS else ""  # an unknown method asks nothing Chrono Mail can answer: a calendar file


def _readable(component: icalendar.Component, method: str, kind: MessageKind) -> bool:
    try:
        return _message_of(component, method, kind) is not None
    except Exception:  # noqa: BLE001 - one damaged item is left out, whatever the library raises
        return False


def _items_in(calendars: list[icalendar.Calendar]) -> tuple[list[tuple[icalendar.Calendar, list[icalendar.Component]]], int, int]:
    """The items of the calendars of a text in the order written, each with the calendar it stands in, how many journal entries and how many
    damaged items they hold. A shared item without a UID gets one made from its contents: reading the text again finds the same item, and it can
    be found in a calendar."""
    method = _method_of(calendars[0])
    found: dict[tuple[str, str], tuple[icalendar.Calendar, list[icalendar.Component]]] = {}
    journals = position = 0
    for calendar in calendars:
        for component in calendar.subcomponents:
            position += 1
            if component.name == "VJOURNAL":
                journals += 1
            elif component.name in _KINDS:
                uid = _first(component, "UID")
                if not uid and method in ("", "PUBLISH"):
                    uid = f"{hashlib.sha256(component.to_ical()).hexdigest()[:32]}@chrono-mail"
                    component["UID"] = icalendar.vText(uid)
                found.setdefault((component.name, uid or f"\0{position}"), (calendar, []))[1].append(component)
    items = [item for item in found.values() if any(_readable(each, method, _KINDS[str(each.name)]) for each in item[1])]
    return items, journals, len(found) - len(items)


def _zones_of(calendar: icalendar.Calendar) -> dict[str, icalendar.Component]:
    """The time zones a calendar defines, by name; the first of a name counts."""
    zones: dict[str, icalendar.Component] = {}
    for each in calendar.subcomponents:
        if each.name == "VTIMEZONE":
            zones.setdefault(_text(each, "TZID"), each)
    return zones


def _calendar_of(calendar: icalendar.Calendar, components: list[icalendar.Component], own: dict[str, icalendar.Component], anywhere: dict[str, icalendar.Component]) -> str:
    """These components in a calendar text of their own: the properties of their calendar, and every time zone used anywhere inside them, their
    reminders included: as their own calendar defines it (two calendars of a text may use one name for two zones), else as another one does."""
    used = sorted({zone for component in components for zone in zones_used(component)})
    made = copy.copy(calendar)
    made.subcomponents = [*(zone for name in used if (zone := own.get(name, anywhere.get(name))) is not None), *components]
    return made.to_ical().decode("utf-8")


@lru_cache(maxsize=1)  # the blocks of a big file come in steps, and every button works on one item of it: read once, not each time
def text_items(text: str | bytes) -> TextItems:
    """The items of a calendar text, read once: every event, task and busy time, of whatever kind is written first or later, in every calendar of
    the text. Never raises."""
    calendars = calendars_in(text)
    if not calendars:
        return TextItems()
    every_uid = all(_first(each, "UID") for calendar in calendars for each in calendar.subcomponents if each.name in _KINDS)
    items, journals, damaged = _items_in(calendars)
    if len(items) == 1 and len(calendars) == 1 and every_uid and not journals and not damaged:  # one item and nothing else: as it was written
        texts: list[str] = [text.decode("utf-8", "replace") if isinstance(text, bytes) else text]
    else:
        zones = {id(calendar): _zones_of(calendar) for calendar in calendars}  # read once: a text may hold very many calendars
        anywhere = {name: zone for each in reversed(list(zones.values())) for name, zone in each.items()}
        texts = [_calendar_of(calendar, item, zones[id(calendar)], anywhere) for calendar, item in items]
    return TextItems(tuple(texts), tuple(_KINDS[str(item[0].name)] for _, item in items), tuple(_first(item[0], "UID") for _, item in items), journals, damaged)


# --- busy times (D-310 step 4) -----------------------------------------------------------------------------------------------------------


def keeps_busy(event: Event, addresses: frozenset[str]) -> bool:
    """Whether an event blocks its time: not when it does not block time (TRANSPARENT), is cancelled, or you declined it."""
    yours = next((person.status.upper() for person in event.attendees if person.address.strip().lower() in addresses), "")
    return not (event.transparency.upper() == "TRANSPARENT" or event.status.upper() == "CANCELLED" or yours == "DECLINED")


def busy_periods(found: Iterable[Occurrence], first: datetime, last: datetime, addresses: frozenset[str], zone: tzinfo) -> tuple[BusyPeriod, ...]:
    """The times between two moments that these occurrences keep you busy, merged, in UTC, in order: only times, never what the item is. An
    occurrence that does not block the time (TRANSPARENT), a cancelled one and one you declined are left out; a tentative one (its own status,
    or your answer) is BUSY-TENTATIVE unless something else makes the same time BUSY."""
    marks: list[tuple[datetime, int, int]] = []  # a moment, and how many busy and tentative items begin (1) or end (-1) there
    for occurrence in found:
        event = occurrence.event
        yours = next((person.status.upper() for person in event.attendees if person.address.strip().lower() in addresses), "")
        if not keeps_busy(event, addresses):
            continue
        start, end = max(first, _as_aware(occurrence.start, zone)), min(last, _as_aware(occurrence.end, zone))
        if end <= start:
            continue
        tentative = event.status.upper() == "TENTATIVE" or yours == "TENTATIVE"
        marks.append((start.astimezone(UTC), 0 if tentative else 1, 1 if tentative else 0))
        marks.append((end.astimezone(UTC), 0 if tentative else -1, -1 if tentative else 0))
    periods: list[BusyPeriod] = []
    busy = tentative_now = 0
    since: datetime | None = None
    shown = ""
    for moment, changes in groupby(sorted(marks, key=lambda mark: mark[0]), key=lambda mark: mark[0]):
        for _, busy_change, tentative_change in changes:
            busy += busy_change
            tentative_now += tentative_change
        kind = "BUSY" if busy else ("BUSY-TENTATIVE" if tentative_now else "")
        if kind != shown:
            if since is not None and shown:
                periods.append(BusyPeriod(since, moment, shown))
            since, shown = moment, kind
    return tuple(periods)


def busy_reply(request: CalendarMessage, address: str, name: str, first: datetime, last: datetime, periods: Iterable[BusyPeriod], now: datetime) -> str:
    """Your answer to a request for your busy times (METHOD:REPLY, RFC 5546 section 3.3.3): the UID and the organizer of the request, you as
    attendee, the period answered and its busy times, all in UTC."""
    reply = icalendar.Calendar()
    reply.add("PRODID", "-//Chrono Mail//Chrono Mail//EN")
    reply.add("VERSION", "2.0")
    reply.add("METHOD", "REPLY")
    item = icalendar.FreeBusy()
    item.add("UID", request.uid or new_uid())
    item.add("DTSTAMP", now)
    item.add("DTSTART", first.astimezone(UTC))
    item.add("DTEND", last.astimezone(UTC))
    if request.organizer is not None:
        organizer = icalendar.vCalAddress(f"mailto:{request.organizer.address}")
        if request.organizer.name:
            organizer.params["CN"] = request.organizer.name
        item.add("ORGANIZER", organizer)
    attendee = icalendar.vCalAddress(f"mailto:{address}")
    if name:
        attendee.params["CN"] = name
    item.add("ATTENDEE", attendee)
    for period in periods:
        line = icalendar.vPeriod((period.start, period.end))
        line.params["FBTYPE"] = period.kind
        item.add("FREEBUSY", line, encode=False)
    reply.add_component(item)
    return reply.to_ical().decode("utf-8")
