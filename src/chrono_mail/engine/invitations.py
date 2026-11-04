"""What a calendar message in a mail asks or tells, for the card shown above its text (iTIP, RFC 5546; D-310)."""
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from html.parser import HTMLParser
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, override

from chrono_mail.core import ChronoMailError, CollectionInfo, CollectionKind, MessageFlag
from chrono_mail.engine.pimaccess import account_name, open_pim, pim_account_ids
from chrono_mail.engine.session import Engine
from chrono_mail.mime import Attachment
from chrono_mail.pim.ical import Attendee
from chrono_mail.pimstore import PimStore
from chrono_mail.pim.itip import BusyPeriod, CalendarMessage, added_already, added_dates, holds_occurrence, in_meeting, is_newer, occurrences_of, proposal_usable, read_calendar_message, text_items

# --- Version ------------------------------------------------------------
__version__ = "invitations.py 4.4.517"

# --- Version history ----------------------------------------------------
# v4.4.517: with_mark: an answer that left no copy is read from the mark on the mail (D-310)
# v4.4.516: your own line shows the answer in your copy (D-310)
# v4.4.515: no limit of 50 blocks: blocks_of gives the cards of a text step by step, so every item gets its block and a huge file stays fast (D-310)
# v4.4.514: a public calendar counts as holding your meeting again, a private one first; only one shared by someone else does not; your_calendars opens each store once per card (D-310)
# v4.4.512: only your own calendars count as holding a meeting: is_yours, a public or shared one no longer does; an ADD shows your answer to its dates (D-310)
# v4.4.509: calendar_text: a calendar part is read with the character set the mail states (D-310)
# v4.4.508: a text with several items has a card per item (parts), of every kind, and says what it holds: events, tasks, journal entries and
#           damaged items left out (D-310)
# v4.4.507: busy times have a card (kind freebusy, busy); card_of: the card of any calendar text, also of a file that asks nothing (plain,
#           items), D-310 step 4
# v4.4.506: a request for the latest version is never out of date (it has no SEQUENCE), D-310 step 3
# v4.4.505: occurrence_kept for Remove and Add; read-only calendars never searched, your own calendars before public ones; a copy of single
#           occurrences only offers no Add to calendar (D-310)
# v4.4.504: Initial invitation card: the calendar message of a mail, who you are in it, and whether its item is in one of your calendars

CALENDAR_TYPES: frozenset[str] = frozenset({"text/calendar", "application/ics"})
Version = Literal["new", "same", "newer", "older"]  # the message against the copy in your calendar ("new": there is none)
BLOCKS_AT_ONCE: int = 20  # blocks of a text read at a time: every item gets its block, the next ones when the person scrolls near them


@dataclass(frozen=True, slots=True)
class InvitationPerson:
    address: str
    name: str
    answer: str  # NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE, DELEGATED (an attendee's PARTSTAT)
    you: bool  # the address of one of your accounts


@dataclass(frozen=True, slots=True)
class InvitationInfo:
    position: int  # the calendar part's place among the message's attachments (message.show); -1: a file on this computer
    method: str  # REQUEST, REPLY, CANCEL, ADD, REFRESH, COUNTER, DECLINECOUNTER, PUBLISH
    kind: str  # event, task or freebusy
    uid: str
    title: str
    location: str
    start: date | datetime | None  # an event's start, a task's due moment, the start of the period of busy times; a date for a whole day
    end: date | datetime | None  # an event's end (a whole day: the day after the last, as in iCalendar), the end of the period of busy times
    all_day: bool
    repeats: bool
    organizer: InvitationPerson | None
    attendees: tuple[InvitationPerson, ...]
    stored_in: str  # the calendar or task list it is in, "Name (account)"; "": in none of yours
    version: Version
    your_answer: str  # your answer in the copy in your calendar ("" when there is none)
    text_shows_nothing: bool = False  # the mail's own text is empty (only layout): the card is all there is to show
    occurrence_kept: bool = False  # the item, or for one occurrence that occurrence, is in the copy (Remove from calendar can remove it)
    organized_by_you: bool = True  # the copy in your calendar names one of your addresses as organizer (a message to you as organizer)
    proposal_usable: bool = True  # a proposal changes something and its times can be used (Accept proposal)
    busy: tuple[BusyPeriod, ...] = ()  # busy times: the periods someone is busy
    plain: bool = False  # a calendar file that asks nothing (no METHOD): it is offered as PUBLISH
    items: int = 1  # how many items it holds
    item: int = -1  # the place of this item in a text with several items (the commands' `item`); -1: the text has one, or this is its whole card
    parts: tuple["InvitationInfo", ...] = ()  # a text with several items: the card of each, as far as asked for (blocks_of)
    events: int = 0  # the whole card of a text: how many events and tasks it holds
    tasks: int = 0
    journals: int = 0  # journal entries in the text: left out
    damaged: int = 0  # items that cannot be read: left out
    answered_on_mail: bool = False  # your answer is known from the mark on the mail: no copy of it says so (with_mark)


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


def calendar_text(attachment: Attachment) -> str:
    """The text of a calendar part, in the character set the mail states; UTF-8, as iCalendar's own, when it states none or an unknown one."""
    try:
        return attachment.data.decode(attachment.charset or "utf-8", "replace")
    except (LookupError, ValueError):  # an unknown name, or one Python refuses to use (hostile input)
        return attachment.data.decode("utf-8", "replace")


def calendar_part(attachments: tuple[Attachment, ...] | list[Attachment]) -> tuple[int, CalendarMessage] | None:
    """The first part of a message that is a calendar message (one with a method, RFC 6047), with its place, or None. A calendar file that asks
    nothing (no method) stays an ordinary attachment."""
    for position, attachment in enumerate(attachments):
        if attachment.content_type.lower() not in CALENDAR_TYPES:
            continue
        message = read_calendar_message(calendar_text(attachment))
        if message is not None and message.method and message.kind in ("event", "task", "freebusy"):
            return position, message
    return None


def _person(person: Attendee, addresses: frozenset[str]) -> InvitationPerson:
    return InvitationPerson(person.address, person.name, person.status, person.address.strip().lower() in addresses)


def _yours_as_kept(person: InvitationPerson, answer: str) -> InvitationPerson:
    """Your own line says your real answer, as your copy holds it, whatever the mail says."""
    return replace(person, answer=answer) if person.you and answer else person


def your_addresses(engine: Engine) -> frozenset[str]:
    """The addresses of the person's accounts, in lower case."""
    return frozenset(address for settings in engine.config().accounts if (address := settings.account.email_address.strip().lower()))


@dataclass(frozen=True, slots=True)
class _Kept:
    stored_in: str = ""
    version: Version = "new"
    answer: str = ""
    occurrence_kept: bool = False
    organized_by_you: bool = True
    proposal_usable: bool = True


def is_yours(info: CollectionInfo) -> bool:
    """Whether a calendar counts as holding your meeting: a private and a public one do, as if you own them. A read-only one holds someone else's
    copy, and one shared with you is theirs, even when you may write in it (D-310)."""
    return not info.read_only and info.server_value("shared") != "1"


@contextmanager
def your_calendars(engine: Engine, kind: CollectionKind) -> Iterator[list[tuple[str, PimStore, CollectionInfo]]]:
    """Every calendar that counts as yours, each store opened once, your private ones first: a public one only when none of those holds the item.
    An account whose calendars cannot be opened (removed meanwhile, no key, a file gone meanwhile) is skipped."""
    with ExitStack() as opened:
        found: list[tuple[str, PimStore, CollectionInfo]] = []
        for account_id in pim_account_ids(engine):
            try:
                store = opened.enter_context(open_pim(engine, account_id))
                found.extend((account_id, store, info) for info in store.collections(kind) if is_yours(info))
            except (ChronoMailError, OSError, sqlite3.Error):
                continue
        yield sorted(found, key=lambda place: place[2].server_value("public") == "1")


def _added_answer(text: str, message_text: str, addresses: frozenset[str]) -> str:
    """Your answer to the dates of an ADD: what the copy says on them, never the series' own; "": none of them is in the copy."""
    dates = (read_calendar_message(text, moment) for moment in added_dates(message_text))
    return next((person.status for date in dates if date is not None for person in date.attendees if person.address.strip().lower() in addresses), "")


def _stored(engine: Engine, places: list[tuple[str, PimStore, CollectionInfo]], message: CalendarMessage, message_text: str, addresses: frozenset[str]) -> _Kept:
    """Where the item of the message is kept here, the message against that copy, and your answer in it. A store that cannot be read is skipped."""
    for account_id, store, info in places:
        try:  # a damaged or unreadable item is skipped: the mail is always shown
            item = store.index.find_uid(info.collection_id, message.uid)
            text = store.get(info.collection_id, item.item_id) if item is not None else ""
        except (ChronoMailError, OSError, sqlite3.Error):  # also an index busy with a synchronisation
            continue
        first = read_calendar_message(text) if text else None  # the main item, or else the first changed occurrence it holds
        if first is None:
            continue
        main = read_calendar_message(text, None)  # None: the copy holds only changed occurrences (an invitation to one)
        kept = read_calendar_message(text, message.occurrence) if message.occurrence is not None else main
        base = kept or main or first  # an occurrence not changed in the copy has the version of its series (RFC 5546)
        # The copy's DTSTAMP is this computer's, rewritten at every change here. A REFRESH has no SEQUENCE: it is never out of date.
        newer = is_newer(message, base.sequence, None) if message.method != "REFRESH" else None
        version: Version = "same" if newer is None else ("newer" if newer else "older")
        answer = next((person.status for person in base.attendees if person.address.strip().lower() in addresses), "")
        if message.method == "ADD" and main is None:
            continue  # no series kept (single occurrences only): there is nothing to add dates to, ask for the series
        if message.method == "ADD":
            kept_here = not added_already(text, message_text)  # dates still to add
            answer = _added_answer(text, message_text, addresses)
        else:
            moments = occurrences_of(message_text)  # every occurrence the message is about (one or several)
            held = in_meeting if message.method in ("REPLY", "COUNTER") else holds_occurrence  # to you as organizer: only a date it really has
            kept_here = message.occurrence is None or any(held(text, moment) for moment in moments)
        yours = (main or first).organizer is not None and (main or first).organizer.address.strip().lower() in addresses  # type: ignore[union-attr]
        usable = message.method != "COUNTER" or proposal_usable(text, message_text)
        return _Kept(f"{info.name} ({account_name(engine, account_id)})", version, answer, kept_here, yours, usable)
    return _Kept()


def invitation_of(engine: Engine, attachments: tuple[Attachment, ...] | list[Attachment]) -> InvitationInfo | None:
    """The card of a message: its calendar message, or None when it has none."""
    found = calendar_part(attachments)
    if found is None:
        return None
    return card_of(engine, found[0], calendar_text(attachments[found[0]]))


def with_mark(card: InvitationInfo, flags: frozenset[MessageFlag]) -> InvitationInfo:
    """The card with the answer the mail itself remembers. A Decline leaves no copy, so its mark is what tells every computer; a copy that holds
    the item knows better. A text with several items has one mail for them all, so no mark can tell which one was meant."""
    answer = "DECLINED" if MessageFlag.INVITE_DECLINED in flags else "DELEGATED" if MessageFlag.INVITE_DELEGATED in flags else ""
    holds = bool(card.stored_in) and (not card.occurrence_kept if card.method == "ADD" else card.occurrence_kept)  # (ADD: occurrence_kept is dates still to add)
    if not answer or holds or card.items > 1 or card.method not in ("REQUEST", "ADD"):
        return card
    return replace(card, your_answer=answer, answered_on_mail=True, attendees=tuple(_yours_as_kept(person, answer) for person in card.attendees))


def blocks_of(engine: Engine, position: int, text: str, first: int, count: int) -> tuple[InvitationInfo, ...]:
    """The cards of `count` items of a text, from the place `first` on; fewer at its end."""
    addresses = your_addresses(engine)
    found = text_items(text)
    cards: list[InvitationInfo] = []
    with your_calendars(engine, CollectionKind.EVENTS) as calendars, your_calendars(engine, CollectionKind.TASKS) as task_lists:
        for place in range(first, len(found.texts)):
            if len(cards) >= count:
                break
            message = read_calendar_message(found.texts[place])
            if message is not None:
                card = _card_of_item(engine, task_lists if message.kind == "task" else calendars, position, message, found.texts[place], addresses)
                cards.append(replace(card, item=place if len(found.texts) > 1 else -1))
    return tuple(cards)


def card_of(engine: Engine, position: int, text: str) -> InvitationInfo | None:
    """The card of a calendar text, or None when it holds nothing to show. Several items: the first ones' cards in `parts`, the first one's
    details in the card itself, and what the text holds; blocks_of gives the rest."""
    found = text_items(text)
    cards = blocks_of(engine, position, text, 0, BLOCKS_AT_ONCE)
    if not cards:
        return None
    return replace(cards[0], item=-1, parts=cards if len(found.texts) > 1 else (), items=len(found.texts), events=found.kinds.count("event"),
                   tasks=found.kinds.count("task"), journals=found.journals, damaged=found.damaged)


def _card_of_item(engine: Engine, places: list[tuple[str, PimStore, CollectionInfo]], position: int, message: CalendarMessage, message_text: str, addresses: frozenset[str]) -> InvitationInfo:
    """The card of one item. A text that asks nothing (no METHOD) is offered as a shared item."""
    plain = not message.method
    message = replace(message, method="PUBLISH") if plain else message
    kept = _stored(engine, places, message, message_text, addresses) if message.uid and message.kind != "freebusy" else _Kept()
    event, task = message.event, message.task
    start = event.start if event is not None else (task.due if task is not None else message.period_start)
    end = event.end if event is not None else message.period_end
    all_day = start is not None and not isinstance(start, datetime)
    repeats = event.repeats if event is not None else bool(task is not None and task.rrule)
    return InvitationInfo(
        position, message.method, message.kind, message.uid, message.title, message.location, start, end, all_day, repeats,
        _person(message.organizer, addresses) if message.organizer is not None else None, tuple(_yours_as_kept(_person(each, addresses), kept.answer) for each in message.attendees),
        kept.stored_in, kept.version, kept.answer, occurrence_kept=kept.occurrence_kept, organized_by_you=kept.organized_by_you, proposal_usable=kept.proposal_usable,
        busy=message.busy, plain=plain,
    )
