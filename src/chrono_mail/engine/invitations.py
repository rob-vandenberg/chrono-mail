"""What a calendar message in a mail asks or tells, for the card shown above its text (iTIP, RFC 5546; D-310)."""
import sqlite3
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, override

from chrono_mail.core import ChronoMailError, CollectionKind
from chrono_mail.engine.pimaccess import account_name, open_pim, pim_account_ids
from chrono_mail.engine.session import Engine
from chrono_mail.mime import Attachment
from chrono_mail.pim.ical import Attendee
from chrono_mail.pim.itip import CalendarMessage, is_newer, read_calendar_message

# --- Version ------------------------------------------------------------
__version__ = "invitations.py 4.4.504"

# --- Version history ----------------------------------------------------
# v4.4.504: Initial invitation card: the calendar message of a mail, who you are in it, and whether its item is in one of your calendars

CALENDAR_TYPES: frozenset[str] = frozenset({"text/calendar", "application/ics"})
Version = Literal["new", "same", "newer", "older"]  # the message against the copy in your calendar ("new": there is none)


@dataclass(frozen=True, slots=True)
class InvitationPerson:
    address: str
    name: str
    answer: str  # NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE, DELEGATED (an attendee's PARTSTAT)
    you: bool  # the address of one of your accounts


@dataclass(frozen=True, slots=True)
class InvitationInfo:
    position: int  # the calendar part's place among the message's attachments (message.show)
    method: str  # REQUEST, REPLY, CANCEL, ADD, REFRESH, COUNTER, DECLINECOUNTER, PUBLISH
    kind: str  # event or task
    uid: str
    title: str
    location: str
    start: date | datetime | None  # an event's start, a task's due moment; a date for a whole day
    end: date | datetime | None  # an event's end (a whole day: the day after the last, as in iCalendar)
    all_day: bool
    repeats: bool
    organizer: InvitationPerson | None
    attendees: tuple[InvitationPerson, ...]
    stored_in: str  # the calendar or task list it is in, "Name (account)"; "": in none of yours
    version: Version
    your_answer: str  # your answer in the copy in your calendar ("" when there is none)
    text_shows_nothing: bool = False  # the mail's own text is empty (only layout): the card is all there is to show


class _VisibleText(HTMLParser):
    """Whether an HTML text shows any text: every piece of text counts, wherever it stands, except in a script or a style. It errs toward
    showing: the index's own reading skips what follows an unclosed <head> or <title>, which would hide a real text behind the card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.found = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "title"):  # a page's title is never shown in the page
            self.hidden_depth += 1
        elif tag in ("img", "picture", "svg", "video", "object", "embed", "iframe", "input", "button", "textarea", "select"):
            self.found = True  # a picture or a field shows something
        if any(name == "background" and value or name == "style" and value and "url(" in value.lower() for name, value in attrs):
            self.found = True  # a background picture, remote or cid: (the remote content line asks for a remote one)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "title") and self.hidden_depth:
            self.hidden_depth -= 1

    @override
    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.translate(INVISIBLE).strip():
            self.found = True
        elif self.hidden_depth and "url(" in data.lower() and self.lasttag == "style":
            self.found = True  # a style block with a picture (a page's background)


INVISIBLE: dict[int, None] = dict.fromkeys(map(ord, "\u00ad\u034f\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff"))  # they show nothing


def shows_text(html_text: str | None, plain_text: str | None) -> tuple[bool, bool]:
    """Whether the HTML text and the plain text show anything. Never raises."""
    plain = bool((plain_text or "").translate(INVISIBLE).strip())
    if not html_text:
        return False, plain
    reader = _VisibleText()
    try:
        reader.feed(html_text)
        reader.close()
    except Exception:  # noqa: BLE001 - a text the parser cannot read is taken as showing something: never hidden
        return True, plain
    return reader.found, plain


def calendar_part(attachments: tuple[Attachment, ...] | list[Attachment]) -> tuple[int, CalendarMessage] | None:
    """The first part of a message that is a calendar message (one with a method, RFC 6047), with its place, or None. A calendar file that asks
    nothing (no method) stays an ordinary attachment."""
    for position, attachment in enumerate(attachments):
        if attachment.content_type.lower() not in CALENDAR_TYPES:
            continue
        message = read_calendar_message(attachment.data)
        if message is not None and message.method and message.kind in ("event", "task"):
            return position, message
    return None


def _person(person: Attendee, addresses: frozenset[str]) -> InvitationPerson:
    return InvitationPerson(person.address, person.name, person.status, person.address.strip().lower() in addresses)


def your_addresses(engine: Engine) -> frozenset[str]:
    """The addresses of the person's accounts, in lower case."""
    return frozenset(address for settings in engine.config().accounts if (address := settings.account.email_address.strip().lower()))


def _stored(engine: Engine, message: CalendarMessage, addresses: frozenset[str]) -> tuple[str, Version, str]:
    """Where the item of the message is kept here, the message against that copy, and your answer in it. A store that cannot be read is skipped."""
    kind = CollectionKind.EVENTS if message.kind == "event" else CollectionKind.TASKS
    for account_id in pim_account_ids(engine):
        try:
            with open_pim(engine, account_id) as store:
                for info in store.collections(kind):
                    try:  # a damaged or unreadable item is skipped: the mail is always shown
                        item = store.index.find_uid(info.collection_id, message.uid)
                        text = store.get(info.collection_id, item.item_id) if item is not None else ""
                    except (ChronoMailError, sqlite3.Error):  # also an index busy with a synchronisation
                        continue
                    first = read_calendar_message(text) if text else None  # the main item, or else the first changed occurrence it holds
                    if first is None:
                        continue
                    main = read_calendar_message(text, None)  # None: the copy holds only changed occurrences (an invitation to one)
                    kept = read_calendar_message(text, message.occurrence) if message.occurrence is not None else main
                    base = kept or main or first  # an occurrence not changed in the copy has the version of its series (RFC 5546)
                    newer = is_newer(message, base.sequence, None)  # the copy's DTSTAMP is this computer's, rewritten at every change here
                    version: Version = "same" if newer is None else ("newer" if newer else "older")
                    answer = next((person.status for person in base.attendees if person.address.strip().lower() in addresses), "")
                    return f"{info.name} ({account_name(engine, account_id)})", version, answer
        except (ChronoMailError, OSError, sqlite3.Error):  # an account whose calendars cannot be opened (removed meanwhile, no key, a file gone meanwhile)
            continue
    return "", "new", ""


def invitation_of(engine: Engine, attachments: tuple[Attachment, ...] | list[Attachment]) -> InvitationInfo | None:
    """The card of a message: its calendar message, or None when it has none."""
    found = calendar_part(attachments)
    if found is None:
        return None
    position, message = found
    addresses = your_addresses(engine)
    stored_in, version, answer = _stored(engine, message, addresses) if message.uid else ("", "new", "")
    event, task = message.event, message.task
    start = event.start if event is not None else (task.due if task is not None else None)
    end = event.end if event is not None else None
    all_day = start is not None and not isinstance(start, datetime)
    repeats = event.repeats if event is not None else bool(task is not None and task.rrule)
    return InvitationInfo(
        position, message.method, message.kind, message.uid, message.title, message.location, start, end, all_day, repeats,
        _person(message.organizer, addresses) if message.organizer is not None else None, tuple(_person(each, addresses) for each in message.attendees),
        stored_in, version, answer,
    )
