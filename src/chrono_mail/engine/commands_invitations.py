"""Answering a calendar message in a mail or a file (D-310): Accept, Tentative and Decline, the calendar it goes into, and who mails the answer."""
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from email.utils import formataddr
from pathlib import Path
import sqlite3
from typing import Literal

from chrono_mail.config import AccountSettings
from chrono_mail.core import ANSWER_MARKS, LOCAL_ACCOUNT_ID, ChronoMailError, CollectionInfo, CollectionKind, MessageFlag, StorageError
from chrono_mail.engine.commands_calendar import MAXIMUM_EVENTS, in_event_zone
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.commands_mail import MessageParameters, _check_id, reply_account
from chrono_mail.engine.invitations import InvitationInfo, answering, asks_nothing, writing, calendar_part, calendar_text, blocks_of, card_of, copy_holds, is_yours, items_left, kept_items, own_mail, take_mark_off, with_mark, with_writers, your_addresses, your_calendars
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.meetingmail import Mailing, copy_for, delete_copy, quiet, settings_of, who_mails
from chrono_mail.engine.pim_server import ItemWriter, item_writer
from chrono_mail.engine.calendarnames import CollectionNamer
from chrono_mail.engine.pimaccess import DEFAULT_COLLECTIONS, account_name, ensure_local_pim, located, open_pim, pim_account_ids, shown_collection_name
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.index import IndexedMessage
from chrono_mail.mime import parse_message
from chrono_mail.pim.ical import MAXIMUM_OCCURRENCES, Occurrence, _as_aware, occurrences, parse_events
from chrono_mail.pim.itip import (
    keeps_busy,
    answer_given, asked_again, cancelled_copy, cancelled_in, declined_copy, moved_answer, organizer_of, with_kept_mail, with_organizer, you_declined,
    BusyPeriod, CalendarMessage, added_dates, answered_added, answered_copy, attendee_addresses, busy_periods, busy_reply, countered, declinecounter_text, delegated_texts, holds_occurrence, in_meeting, meeting_in_mail, proposal_usable, request_of, shared_text, text_items, with_attendee_answer, is_newer, occurrences_of, published_copy, with_delegate, merged_answer, read_calendar_message, refresh_text, reply_text, told_silent, updated_copy, with_added, with_silence, without_dates, BEFORE_BARE, BEFORE_LOUD, BEFORE_SILENT, ready_to_delete,
)
from chrono_mail.pim.times import local_time, local_zone, parse_moment
from chrono_mail.pimstore import ItemQuery
from chrono_mail.sync.davsync import DavState

# --- Version ------------------------------------------------------------
__version__ = "commands_invitations.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: a calendar named only by an address is shown by a name (shown_collection_name, D-346)
# v4.4.558: From the calendar an answer is refused in a read-only calendar, on a Cancelled event and when given already; an answer on one date of a series declined in Local Folders moves the whole series as one record (D-310)
# v4.4.557: A Cancelled event takes no answer and no delegate until it is Restored, a newer version of the organizer does (D-310)
# v4.4.556: An answer from the calendar changes the event right-clicked, says only what the organizer's mail says, and marks that mail (D-310)
# v4.4.555: A cancellation sets Cancelled; an invitation with no organizer stores what the person says, silently when told so, also at Accept after a Decline; a later answer the server mails goes as it is; a file with no organizer asks nothing (D-310)
# v4.4.554: A declined event stays hidden in the calendar it is in, Change my answer leaves it there, the mark only where no copy tells it, Delegate from Local Folders picks a calendar, Accept on a declined copy warns of clashes (D-310)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.531: A calendar file opened from your own Sent or Drafts is your own: no buttons (D-310)
# v4.4.530: Every block of a mail with several items knows who wrote it (D-310)
# v4.4.529: The clash warning on a series names how many and when; invitations and .ics files up to 1 MB (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.527: invitation.choices says what you have at that time (clashes); busy times with no cut in the text or the period; an attachment too large says so (D-310)
# v4.4.526: an answer says when it waits for the connection; a delegation says whether the server tells the organizer (D-310)
# v4.4.525: Add all skips what any of your calendars holds, the choice to replace is gone; invitation.blocks says how many items are left (D-310)
# v4.4.520: an answer's draft names the mail it answers, so sending it sets the Replied mark; a server that mails sets it at once; the card knows a draft waits (D-310)
# v4.4.519: the choice of who wrote moved to invitations.py, for the card too; a declined proposal and a sent latest version are marked on the mail and not made twice (D-310)
# v4.4.518: Who mails moved to meetingmail.py; the box of the account that got the invite decides, and it covers only answers; Accept proposal follows what the person said (D-310)
# v4.4.517: A Decline or Delegate leaves its mark on the mail, Change my answer and a new answer take it off (D-310)
# v4.4.516: invitation.reopen: Change my answer (D-310)
# v4.4.515: invitation.blocks: the cards of a big text step by step; a Decline that waits for the connection says what the server's copy needs first; refused twice, the meeting stays here with a sync conflict (D-310)
# v4.4.514: Add all puts each item in as it was written, so an export comes back identical; a public calendar counts as yours again, a private one first (D-310)
# v4.4.512: an added date is answered like an invitation: Accept and Tentative add it with your answer, Decline adds nothing, Delegate passes it on; invitation.apply only asks for a series you do not have (D-310)
# v4.4.512: only your own calendars count as holding a meeting; a calendar that holds it already is marked and refused (D-310)
# v4.4.510: who mails: the account decides, Chrono Mail sends when the server cannot, the copy tells the server whether to mail, also before a delete (D-310)
# v4.4.509: Take their answer takes only the line of the attendee who wrote it; a calendar part in the character set the mail states (D-310)
# v4.4.508: a text with several items: every command works on one item (`item`), of whatever kind; invitation.add: all items that ask no
#           answer, events into the chosen calendar, tasks into the chosen task list, those already there replaced or skipped (D-310)
# v4.4.507: every command also takes an attachment or a file (SourceParameters), invitation.card shows one; several items of a shared text
#           are imported, each on its own; invitation.busy: your busy times answered from the calendars you tick; a task says task and
#           task list, never meeting and calendar (D-310 step 4)
# v4.4.506: invitation.organize: you are the organizer: an attendee's answer into your meeting, a proposal accepted (the meeting moved,
#           the attendees asked again) or declined, the latest version sent to whoever asks (D-310 step 3)
# v4.4.505: invitation.delegate: the invitation passed on to someone else, and the answer DELEGATED to the organizer, both as drafts
# v4.4.505: invitation.apply: a newer version, a cancellation or an added occurrence from the organizer into your calendar, server first; an
#           added occurrence of a series you do not have asks the organizer for the latest version (a draft)
# v4.4.505: Initial: invitation.choices (every calendar, the one to start on) and invitation.answer (Accept, Tentative, Decline: the copy
#           in a calendar server first, the answer mailed by the server or as a draft to send), D-310

MAILS_LOOKED_AT: int = 500  # how many of the organizer's newest mails an answer from the calendar reads to find its invitation, so the wait stays short
MAXIMUM_INVITATION_BYTES: int = 1024 * 1024  # an invitation or an .ics file: 1 MB, the owner's choice, as the import limit of a large calendar service (the backup copies the stores and has no limit)
TOO_LARGE: str = "This calendar file is larger than 1 MB, so it cannot be read here"

Answer = Literal["ACCEPTED", "TENTATIVE", "DECLINED"]
_ANSWERABLE: frozenset[str] = frozenset({"REQUEST", "PUBLISH", "ADD"})  # what asks for an answer, or offers an item to keep
_SUBJECT_WORD: dict[str, str] = {"ACCEPTED": "Accepted", "TENTATIVE": "Tentative", "DECLINED": "Declined"}
_ANSWER_WORD: dict[str, str] = {"ACCEPTED": "accepted", "TENTATIVE": "tentatively accepted", "DECLINED": "declined"}
_MOST_BUSY_ITEMS: int = 200_000  # occurrences looked at for one answer (only a hostile calendar has more)


@dataclass(frozen=True, slots=True)
class CalendarChoice:
    collection: str
    name: str
    account: str
    account_name: str
    read_only: bool
    public: bool  # one of the public folders a server keeps next to the account's own (D-182)
    shared: bool = False  # someone else's calendar you may use: it never starts ticked and never counts as holding your meeting (D-310)
    holds: bool = False  # the item is in this calendar already: a second copy cannot go there


@dataclass(frozen=True, slots=True)
class SeriesClash:
    title: str
    start: str  # when your appointment starts


@dataclass(frozen=True, slots=True)
class ChoicesResult:
    calendars: tuple[CalendarChoice, ...]  # every calendar (for a task: every task list) of every account, in the sidebar's order
    chosen: str  # the one the window starts on: the first own, writable one of the account that received the mail; "": none
    stored: str  # the one that holds the item already ("": none): an answer changes the copy there
    receiving: str = ""  # the account that received the mail ("": none, a file): the busy times window starts on its own calendars
    clashes: tuple[str, ...] = ()  # an invitation not kept yet: what you have at its time, each once, by title
    series_clashes: tuple[SeriesClash, ...] = ()  # a series not kept yet: each appointment of yours it meets in the coming year, in order of time
    declined_here: bool = False  # the copy is one you declined in Local Folders: Delegate too asks for a calendar


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceParameters(MessageParameters):
    """Where a calendar message is: the mail's own (its calendar part), one of its attachments, or a file (then the mail's fields stay empty)."""

    part: int = field(default=-1, metadata=option("An attachment's position in the message; -1: the message's own calendar message"))
    file: str = field(default="", metadata=option("Or a calendar file (.ics) on this computer"))
    event: str = field(default="", metadata=option("Or a hidden event in a calendar, answered again (see event.list)"))
    occurrence: str = field(default="", metadata=option("With event: the date of the series that is meant"))
    item: int = field(default=-1, metadata=option("In a text with several items: the place of the one meant; -1: the text has one"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ChoicesParameters(SourceParameters):
    kind: Literal["", "event", "task"] = field(default="", metadata=option("event or task: the calendars or task lists for all items of that kind; empty: for the one item"))


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerParameters(SourceParameters):
    answer: Answer = field(metadata=option("ACCEPTED, TENTATIVE or DECLINED"))
    calendar: str = field(default="", metadata=option("Accept or Tentative of an item not kept yet: the calendar (task list) to keep it in"))
    mail: bool = field(default=True, metadata=option("When Chrono Mail mails the answer: make it (a draft to send); false: no answer mail"))
    to: str = field(default="", metadata=option("An invitation that names no organizer: the address the answer goes to"))
    dry: bool = field(default=False, metadata=option("Change nothing: only check the answer and say who would mail it"))


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str
    stored_in: str  # the calendar that holds the item now, "Name (account)"; "": none (declined)
    server_answers: bool  # the calendar's server mails the answer itself (D-310)
    draft_folder: str | None  # the answer mail to send, when Chrono Mail mails it
    draft_message: str | None
    draft_account: str | None = None  # the account the answer mail is a draft of
    waiting: bool = False  # the server could not be reached: the change goes when the connection is back
    chrono: bool = False  # dry: Chrono Mail would mail this answer
    no_organizer: bool = False  # dry: the invitation names nobody to answer
    sender: str = ""  # dry: who sent the mail it came in; "": a file
    uid: str = ""  # of the item: every open card of it shows what your calendar holds now


@dataclass(frozen=True, slots=True)
class _Opened:
    message_id: str | None  # the mail's, for an answer to follow it; a file has none
    senders: tuple[str, ...]  # who sent the mail
    message: CalendarMessage
    text: str  # the calendar part's text
    receiving: AccountSettings | None  # the account the mail was sent to (D-292)
    plain: bool = False  # a calendar text that asks nothing (no METHOD): offered as PUBLISH
    several: bool = False  # a text with several items and none chosen: only the card and Add all work on all of them
    whole: str = ""  # the whole text, also of what is left out: the card says what it holds
    marks: frozenset[MessageFlag] | None = None  # the marks of the mail this is the one invitation of; None: no mark can speak for it (a file, another attachment, several items)
    source: tuple[str, str] | None = None  # the folder and id of the mail: an answer to it marks it as replied once it is sent; a file has none
    own: str = ""  # the mail lies in your Sent ("sent") or Drafts ("draft"): what it carries is your own and asks nothing
    account: str = ""  # the account of the mail
    kept: bool = False  # answered from the calendar: the text is your own copy
    meeting: str = ""  # kept: the organizer's own text, from the mail it came in; "": no such mail
    title: str = ""  # kept: the organizer's own title, from that mail


def _file_text(path: str) -> bytes:
    try:
        file = Path(path)
        if file.stat().st_size > MAXIMUM_INVITATION_BYTES:
            raise UsageError(TOO_LARGE)
        return file.read_bytes()
    except OSError:
        raise UsageError(f"The file {path} could not be read") from None


def _kept_request(engine: Engine, parameters: SourceParameters) -> tuple[bytes, str | None]:
    """An event of the calendar as the invitation it was, and the account of its calendar (Local Folders receives no mail)."""
    with located(engine, parameters.event, CollectionKind.EVENTS) as found:
        text = found.store.get(found.item.collection, parameters.event)
        account = found.account
    series = parse_events(text)
    try:
        moment = in_event_zone(parse_moment(parameters.occurrence), series[0].master.start) if parameters.occurrence and series else None
    except ValueError as error:
        raise UsageError(f"occurrence: {error}") from None
    request = asked_again(text, moment)
    if request is None:
        raise UsageError("This holds no calendar item that can be shown")
    return request.encode("utf-8"), None if account == LOCAL_ACCOUNT_ID else account


def _opened(engine: Engine, parameters: SourceParameters, busy: bool = False, whole: bool = False) -> _Opened:
    """The calendar message of a mail, an attachment or a file, or the one item of it that is meant. busy: the caller also takes busy times;
    everything else has nothing to do with them. whole: the caller also takes a text with several items, with no item chosen."""
    message_id: str | None = None
    senders: tuple[str, ...] = ()
    source: tuple[str, str] | None = None
    own = ""
    receiving: str | None = None
    own_part = False
    flags = frozenset[MessageFlag]()
    if parameters.event:
        data, receiving = _kept_request(engine, parameters)
    elif parameters.file:
        data = _file_text(parameters.file)
    else:
        with open_mailbox(engine, parameters.account) as mailbox:
            reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
            raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
            parsed = parse_message(raw)
            if parameters.part >= len(parsed.attachments):
                raise UsageError("This message has no such attachment")
            found = calendar_part(parsed.attachments)
            if parameters.part < 0 and found is None:
                raise UsageError("This message holds no calendar message")
            position = found[0] if found is not None and parameters.part < 0 else parameters.part
            data = calendar_text(parsed.attachments[position]).encode("utf-8")
            if len(data) > MAXIMUM_INVITATION_BYTES:
                raise UsageError(TOO_LARGE)
            own_part = found is not None and found[0] == position
            flags = mailbox.current_flags(reference)
            message_id, senders = parsed.message_id, tuple(address.address for address in parsed.from_addresses)
            receiving = reply_account(engine, mailbox.settings, parsed, raw)
            source = (parameters.folder, parameters.message)
            own = own_mail(mailbox, mailbox.folder(parameters.folder))
    data = data.removeprefix(b"\xef\xbb\xbf")  # a byte order mark, as some programs write in front of a file
    text = data.decode("utf-8", "replace")
    items = text_items(text)
    if parameters.item >= len(items.texts):
        raise UsageError("This text has no such item" if items.texts else "This holds no calendar item that can be shown")
    several = parameters.item < 0 and len(items.texts) > 1
    if several and not whole:
        raise UsageError("Choose one of these items")
    message = read_calendar_message(items.texts[max(parameters.item, 0)]) if items.texts else None
    if message is None:
        raise UsageError("This holds no calendar item that can be shown")
    if message.kind == "freebusy" and not busy:
        raise UsageError("This message holds busy times: there is nothing to answer or to keep")
    whole_text = text
    text = text if several else items.texts[max(parameters.item, 0)]
    plain = asks_nothing(message, bool(parameters.file))
    settings = engine.config().find(receiving) if receiving else None
    marks = flags if own_part and len(items.texts) == 1 else None
    return _Opened(message_id, senders, replace(message, method="PUBLISH") if plain else message, text, settings, plain, several, whole_text, marks, source, own, parameters.account, bool(parameters.event))


def _kind(message: CalendarMessage) -> CollectionKind:
    return CollectionKind.TASKS if message.kind == "task" else CollectionKind.EVENTS


def _item(message: CalendarMessage) -> str:
    """What the person calls the item."""
    return "task" if message.kind == "task" else "meeting"


def _home(kind: CollectionKind) -> str:
    """What the person calls the place it is kept."""
    return "task list" if kind == CollectionKind.TASKS else "calendar"


@dataclass(frozen=True, slots=True)
class _You:
    address: str  # lower case
    name: str
    account: str  # the mail account of that address: the answer is sent from it


def _not_yours(engine: Engine, opened: _Opened) -> None:
    """Refuse when you organize the meeting yourself: your answer would change your own meeting, and its server would mail every attendee."""
    organizer = opened.message.organizer
    addresses = {settings.account.email_address.strip().lower() for settings in engine.config().accounts if settings.account.email_address}
    if organizer is not None and organizer.address.strip().lower() in addresses:
        raise UsageError(f"You are the organizer of this {_item(opened.message)}: there is nothing to answer")


def _you(engine: Engine, opened: _Opened) -> _You:
    """You in the item: the receiving account's address when it is on the list, else any of your mail accounts' on it, else the receiving
    account's (an invitation passed on to you). The answer is sent from the account of that address (RFC 6047: the sender is the attendee)."""
    listed = {person.address.strip().lower() for person in opened.message.attendees}
    receiving = opened.receiving
    if receiving is not None and receiving.account.email_address.strip().lower() in listed:
        return _You(receiving.account.email_address.strip().lower(), receiving.account.display_name, receiving.account.account_id)
    for settings in engine.config().accounts:
        address = settings.account.email_address.strip().lower()
        if address and address in listed:
            return _You(address, settings.account.display_name, settings.account.account_id)
    if receiving is None or not receiving.account.email_address:
        raise UsageError("None of your accounts received this invitation: there is no address to answer with")
    return _You(receiving.account.email_address.strip().lower(), receiving.account.display_name, receiving.account.account_id)


@dataclass(frozen=True, slots=True)
class _Stored:
    account: str
    info: CollectionInfo
    item: str
    text: str


def _stored(engine: Engine, kind: CollectionKind, uid: str) -> _Stored | None:
    if not uid:
        return None
    with your_calendars(engine, kind) as places:  # someone else's copy is never changed for your answer
        for account, store, info in places:
            try:
                item = store.index.find_uid(info.collection_id, uid)
                if item is not None:
                    return _Stored(account, info, item.item_id, store.get(info.collection_id, item.item_id))
            except ChronoMailError:
                continue
    return None


def _copy_meant(engine: Engine, kind: CollectionKind, parameters: SourceParameters, uid: str) -> _Stored | None:
    """The copy an answer changes: the event right-clicked when it is in a calendar of yours, else the one your calendars hold of that item (D-310)."""
    if parameters.event and uid:
        with located(engine, parameters.event, CollectionKind.EVENTS) as found:
            if is_yours(found.collection):
                return _Stored(found.account, found.collection, found.item.item_id, found.store.get(found.item.collection, parameters.event))
    return _stored(engine, kind, uid)


def _kept_declined(engine: Engine, stored: _Stored | None) -> _Stored | None:
    """The copy in Local Folders of an invitation you declined (D-310): it holds your answer, but it is no appointment. An answer that keeps the
    invitation goes to a calendar the person picks, and this copy goes. A declined copy in a server calendar simply stays there, and is answered in place."""
    return stored if stored is not None and stored.account == LOCAL_ACCOUNT_ID and you_declined(stored.text, your_addresses(engine)) else None


def _keep_declined(engine: Engine, text: str) -> None:
    """An invitation you declined with no copy anywhere has no calendar: its copy goes, without asking, into the calendar of Local Folders, on this computer only
    (the owner's exception to D-193)."""
    ensure_local_pim(engine)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store, item_writer(engine, LOCAL_ACCOUNT_ID, store) as writer:
        info = next(iter(store.collections(CollectionKind.EVENTS)), None)
        if info is None:
            _, name, colour = next(each for each in DEFAULT_COLLECTIONS if each[0] == CollectionKind.EVENTS)
            info = store.create_collection(CollectionKind.EVENTS, name, colour)
        writer.put(info, text, None)


def _holds(engine: Engine, calendar: tuple[str, CollectionInfo], uid: str) -> bool:
    """Whether a calendar that does not count as yours holds the item already (the window greys it; OR-4: the command checks it too)."""
    with open_pim(engine, calendar[0]) as store:
        return bool(uid) and store.index.find_uid(calendar[1].collection_id, uid) is not None


def _series_kept(stored: _Stored | None, home: str) -> None:
    """A date added to a series can only be answered when your calendar has that series (RFC 5546 section 3.2.4)."""
    if stored is None or read_calendar_message(stored.text, None) is None:
        raise UsageError(f"The series of this date is not in your {home}: ask the organizer to send it again")


def _label(engine: Engine, account: str, info: CollectionInfo) -> str:
    return f"{shown_collection_name(engine, account, info.name)} ({account_name(engine, account)})"


def _clashes(engine: Engine, opened: _Opened) -> tuple[tuple[str, ...], tuple[SeriesClash, ...]]:
    """What you already have at the time of an invitation, in your own calendars, for the warning before Accept: by title, and for a series each date too.
    A series counts for the coming year. What does not block its time is no clash, and a calendar that cannot be read is skipped: the warning never stands in the way of an answer."""
    message, zone = opened.message, local_zone()
    event = message.event
    start = _moment_of(event.start, zone) if event is not None else None
    if event is None or start is None:
        return (), ()
    end = _moment_of(event.end, zone) or start
    start = max(start, datetime.now(zone)) if event.repeats else start
    last = start + timedelta(days=366) if event.repeats else max(end, start + timedelta(minutes=1))
    wanted = [(_as_aware(each.start, zone), _as_aware(each.end, zone)) for each in occurrences([opened.text], start, last, zone)] or [(start, end)]
    addresses = your_addresses(engine)
    found: dict[str, None] = {}
    dated: dict[tuple[datetime, str], None] = {}
    with your_calendars(engine, CollectionKind.EVENTS) as places:
        for _, store, info in places:
            try:
                items = store.index.query(ItemQuery(CollectionKind.EVENTS, (info.collection_id,), "", start=int(start.timestamp()), end=int(last.timestamp()), limit=MAXIMUM_EVENTS))
                for item in items:
                    if item.uid != message.uid:
                        for each in occurrences([store.get(item.collection, item.item_id)], start, last, zone):
                            begins, ends = _as_aware(each.start, zone), _as_aware(each.end, zone)
                            if keeps_busy(each.event, addresses) and any(begins < until and since < ends for since, until in wanted):
                                found[item.title or "(no title)"] = None
                                dated[(begins, item.title or "(no title)")] = None
            except (ChronoMailError, OSError, sqlite3.Error):
                continue
    return tuple(found), tuple(SeriesClash(title, begins.isoformat()) for begins, title in sorted(dated)) if event.repeats else ()


def invitation_choices(engine: Engine, parameters: ChoicesParameters) -> ChoicesResult:
    opened = _opened(engine, parameters, busy=True, whole=bool(parameters.kind))
    kind = {"event": CollectionKind.EVENTS, "task": CollectionKind.TASKS}.get(parameters.kind, _kind(opened.message))
    calendars: list[CalendarChoice] = []
    chosen = ""
    receiving = opened.receiving.account.account_id if opened.receiving is not None else ""
    uid = opened.message.uid if not parameters.kind and opened.message.kind != "freebusy" else ""
    stored = _copy_meant(engine, kind, parameters, uid)
    declined = _kept_declined(engine, stored)
    stored = None if declined is not None else stored
    namer = CollectionNamer(engine)  # the contacts read at most once for the whole choice (D-346)
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                for info in store.collections(kind):
                    public, shared = info.server_value("public") == "1", info.server_value("shared") == "1"
                    holds = uid != "" and store.index.find_uid(info.collection_id, uid) is not None
                    if holds and declined is not None and declined.info.collection_id == info.collection_id:
                        holds = False  # a declined copy is no appointment: an answer may land here
                    calendars.append(CalendarChoice(info.collection_id, namer(account, info.name), account, account_name(engine, account), info.read_only, public, shared, holds))
                    if not chosen and account == receiving and is_yours(info) and not public and not holds:
                        chosen = info.collection_id
        except ChronoMailError:
            continue
    blocking_already = stored is not None and opened.message.method != "ADD" and not you_declined(stored.text, your_addresses(engine))  # what you declined blocks nothing until you answer
    clashes, series_clashes = _clashes(engine, opened) if not parameters.kind and opened.message.method in ("REQUEST", "ADD") and not blocking_already else ((), ())
    return ChoicesResult(tuple(calendars), chosen, stored.info.collection_id if stored is not None else "", receiving, clashes, series_clashes, declined is not None and opened.message.method != "ADD")


def _invitation_in(mailbox: Mailbox, row: IndexedMessage, opened: _Opened, stored: str, one_date: bool) -> _Opened | None:
    """`opened` with this mail, when it is the invitation of the copy answered."""
    reference = mailbox.reference(row.folder, row.store_id)
    parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
    found = calendar_part(parsed.attachments)
    data = calendar_text(parsed.attachments[found[0]]) if found is not None else ""
    if not data or len(data) > MAXIMUM_INVITATION_BYTES:
        return None
    items = text_items(data)
    for uid, text in zip(items.uids, items.texts, strict=True):
        meeting = meeting_in_mail(text, stored, opened.message, one_date) if uid == opened.message.uid else None
        said = read_calendar_message(meeting) if meeting is not None else None
        if meeting is not None and said is not None:
            senders = tuple(address.address for address in parsed.from_addresses)
            marks = mailbox.current_flags(reference) if len(items.texts) == 1 else None
            return replace(opened, message_id=parsed.message_id, senders=senders, marks=marks, source=("/".join(row.folder), row.store_id), account=mailbox.settings.account.account_id, meeting=meeting, title=said.title)
    return None


def _with_its_mail(engine: Engine, opened: _Opened, you: _You, stored: _Stored | None, one_date: bool) -> _Opened:
    """An answer from the calendar with the mail the invitation came in, when the account that answers still has it: the organizer's own text goes out, nothing you
    changed in your copy, and that mail gets the Replied mark (D-310). Only the newest mail of the organizer is read, so an answer never waits for a large mailbox."""
    organizer = opened.message.organizer
    if not opened.kept or organizer is None or not organizer.address or not you.account:
        return opened
    copy_text = stored.text if stored is not None else opened.text
    try:
        with open_mailbox(engine, you.account) as mailbox:
            for row in mailbox.index.sent_by(organizer.address, MAILS_LOOKED_AT):
                try:
                    found = _invitation_in(mailbox, row, opened, copy_text, one_date)
                except (ChronoMailError, OSError):  # a mail that cannot be read is not the one
                    continue
                if found is not None:
                    return found
    except (ChronoMailError, OSError, sqlite3.Error):
        return opened
    return opened


def _remember(engine: Engine, opened: _Opened, mark: MessageFlag | None, replied: bool = False) -> None:
    """Keep your answer on the mail, as a mark any computer reads (D-310): a Decline with no copy in a server calendar has nothing else to tell it. An answer or
    Change my answer takes an older mark off. The answer stands whatever happens here: a refused mark leaves the card asking, and the person can answer again. `replied`: the
    server mails the answer, so nothing is sent here to set the Replied mark."""
    if opened.marks is None or opened.source is None or opened.message.method not in ("REQUEST", "ADD", "COUNTER", "REFRESH"):
        return
    wanted = (opened.marks - ANSWER_MARKS) | ({mark} if mark is not None else frozenset[MessageFlag]()) | ({MessageFlag.REPLIED} if replied else frozenset[MessageFlag]())
    if wanted != opened.marks:
        try:
            with open_mailbox(engine, opened.account) as mailbox:
                mailbox.set_flags(mailbox.reference(mailbox.folder(opened.source[0]), _check_id(opened.source[1])), wanted)
        except ChronoMailError:
            return


def _organizer_named(message: CalendarMessage, copy: _Stored | None, you: _You) -> str:
    """Who a mail that names no organizer goes to once you answered it: the copy says what you decided. An unanswered copy decides nothing, whatever a
    server wrote in it by itself: the person is asked (D-310). "": the message names its own organizer, or nobody knows."""
    if copy is None or (message.organizer is not None and message.organizer.address):
        return ""
    item = read_calendar_message(copy.text)
    answered = item is not None and any(person.address.strip().lower() == you.address and person.status != "NEEDS-ACTION" for person in item.attendees)
    return organizer_of(copy.text) if answered else ""


def _leave_local_folders(engine: Engine, declined: _Stored, item: str | None) -> None:
    """A copy you declined in Local Folders became an appointment: the old copy goes, unless `item` is that copy, and the mark goes with it."""
    if item != declined.item:
        with open_pim(engine, declined.account) as store, item_writer(engine, declined.account, store) as writer:
            writer.delete(declined.info, declined.item)
    take_mark_off(engine, declined.text)


def _not_cancelled(opened: _Opened, stored: _Stored | None) -> None:
    """A Cancelled event takes no answer until it is Restored (OR-4, D-310); a newer version from the organizer replaces it, so that can be answered."""
    message = opened.message
    if stored is None or stored.info.kind != CollectionKind.EVENTS or not cancelled_in(stored.text, occurrences_of(opened.text) if message.occurrence is not None else ()):
        return
    same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else None
    base = same or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
    if base is None or not is_newer(message, base.sequence, None):
        raise UsageError("This event is cancelled: restore it first")


def _answerable_there(engine: Engine, parameters: SourceParameters, opened: _Opened, answer: str) -> None:
    """What the menu of an event greys, the command refuses too (OR-4, D-310): any answer in a read-only calendar or on a Cancelled event, and the answer it
    has already, on that date or on all of it."""
    if not parameters.event:
        return
    with located(engine, parameters.event, CollectionKind.EVENTS) as found:
        if found.collection.read_only:
            raise UsageError("This calendar is read-only")
        text = found.store.get(found.item.collection, parameters.event)
    moments = occurrences_of(opened.text) if opened.message.occurrence is not None else ()
    if cancelled_in(text, moments):
        raise UsageError("This event is cancelled: restore it first")
    if answer_given(text, your_addresses(engine), answer, moments):
        raise UsageError("You gave this answer already")


def _one_date_moved(stored: _Stored, opened: _Opened, you: _You, answer: str) -> str | None:
    """An answer on one date of a series declined as a whole in Local Folders: the whole series, as one record, for the calendar picked (D-310)."""
    message = opened.message
    if message.occurrence is None:
        return None
    kept = read_calendar_message(stored.text, message.occurrence)
    text = moved_answer(stored.text, opened.text, you.address, answer, message.occurrence, kept is None or bool(is_newer(message, kept.sequence, None)), you.name)
    if text is None:
        raise UsageError("The invitation could not be read")  # the declined copy stays: the rest of the series is never lost
    return text


def invitation_answer(engine: Engine, parameters: AnswerParameters) -> AnswerResult:
    opened = _opened(engine, parameters)
    _answerable_there(engine, parameters, opened, parameters.answer)
    message = opened.message
    if message.method not in _ANSWERABLE:
        raise UsageError("This calendar message asks for no answer")
    if parameters.answer not in _SUBJECT_WORD:
        raise UsageError("The answer is ACCEPTED, TENTATIVE or DECLINED")
    if message.method != "PUBLISH":
        _not_yours(engine, opened)
    you = _you(engine, opened) if message.method != "PUBLISH" else _You("", "", "")  # a shared item asks no address
    kind = _kind(message)
    item_word, home = _item(message), _home(kind)
    stored = _copy_meant(engine, kind, parameters, message.uid)
    _not_cancelled(opened, stored)
    opened = opened if parameters.dry else _with_its_mail(engine, opened, you, stored, bool(parameters.occurrence))
    declined = _kept_declined(engine, stored) if parameters.answer != "DECLINED" else None
    stored = None if declined is not None else stored
    adding = message.method == "ADD"
    if adding:
        _series_kept(stored, home)
    # Everything is worked out and checked before anything is written: a step that fails leaves nothing half done.
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else None
        base = same or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
        if base is not None and is_newer(message, base.sequence, None) is False:
            raise UsageError(f"Your {home} has a newer version of this {item_word}: this invitation is out of date")  # OR-4: the command checks it
    text: str | None = None
    keeping: str | None = None  # the copy Local Folders gets of an invitation you declined with no copy anywhere
    if parameters.answer == "DECLINED" and adding and stored is not None:  # nothing is added; a date that is there already is marked
        text = answered_added(stored.text, opened.text, you.address, "DECLINED", you.name)
        target, item = ((stored.account, stored.info), stored.item) if text is not None else (None, None)
    elif parameters.answer == "DECLINED":
        holds = stored is not None and (message.occurrence is None or any(holds_occurrence(stored.text, moment) for moment in occurrences_of(opened.text)))
        # (not only dates removed already: the card looks at every date the message names, and so does Decline)
        target = (stored.account, stored.info) if stored is not None and holds else None  # an occurrence not in the copy: Decline adds nothing
        item: str | None = stored.item if stored is not None and holds else None
        if stored is not None and holds and (message.occurrence is not None or (kind == CollectionKind.EVENTS and message.method != "PUBLISH")):  # a declined event stays where it is, hidden
            same = read_calendar_message(stored.text, message.occurrence)
            take = same is None or bool(is_newer(message, same.sequence, None))  # a newer version: it replaces the kept one (as Accept)
            text = merged_answer(stored.text, opened.text, you.address, "DECLINED", message.occurrence, take_message=take, name=you.name)
            if text is None:
                raise UsageError("The occurrence could not be marked as declined")
            if stored.account == LOCAL_ACCOUNT_ID and message.occurrence is None and opened.marks is not None:
                text = with_kept_mail(text, opened.account, opened.message_id)  # its mark goes when the copy goes
        if kind == CollectionKind.EVENTS and message.method != "PUBLISH" and stored is None:
            keeping = declined_copy(opened.text, you.address, you.name, opened.account, opened.message_id)
    else:
        if stored is not None and message.method == "PUBLISH":  # a shared item kept already: a newer version as sent, nothing made up
            if not is_newer(message, (read_calendar_message(stored.text) or message).sequence, None):
                raise UsageError(f"This item is in your {home} already")
            text, target, item = published_copy(opened.text), (stored.account, stored.info), stored.item
        elif stored is not None and adding:  # the new dates into the series, your answer on them; the series keeps its own
            grown = with_added(stored.text, opened.text)
            text = answered_added(grown, opened.text, you.address, parameters.answer, you.name) if grown is not None else None
            target, item = (stored.account, stored.info), stored.item
        elif stored is not None:
            kept = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else read_calendar_message(stored.text, None)
            take = kept is None or bool(is_newer(message, kept.sequence, None))
            text = merged_answer(stored.text, opened.text, you.address, parameters.answer, message.occurrence, take_message=take, name=you.name)
            target, item = (stored.account, stored.info), stored.item
        else:
            shared = message.method == "PUBLISH"  # kept as sent: no attendee line of yours is made up
            moved = _one_date_moved(declined, opened, you, parameters.answer) if declined is not None else None
            text = moved or (published_copy(opened.text) if shared else answered_copy(opened.text, you.address, parameters.answer, you.name))
            target, item = _collection(engine, kind, parameters.calendar), None
            if declined is not None and declined.info.collection_id == target[1].collection_id:
                item = declined.item  # the person chose the calendar it is in: it becomes an appointment there
            elif _holds(engine, target, message.uid):
                raise UsageError(f"This {item_word} is in that {home} already")
        if text is None:
            raise UsageError("The invitation could not be read")
        if target[1].read_only:
            raise UsageError(f"This {home} is read-only")
    mails = who_mails(engine, target, you.account, you.address, item if text is None and item is not None else "")
    named = _organizer_named(message, stored or declined, you)
    missing = message.method != "PUBLISH" and not named and (message.organizer is None or not message.organizer.address)
    if named and declined is not None and text is not None:
        text = with_organizer(text, named)  # (the new copy comes from the mail, which names nobody: what you decided is in the copy you declined)
    if parameters.dry:  # (the person says who the organizer is, whoever mails)
        chrono = (mails.chrono or (missing and mails.server)) and message.method != "PUBLISH"
        return AnswerResult(parameters.answer, "", mails.server, None, None, chrono=chrono, no_organizer=missing, sender=next(iter(opened.senders), "") if opened.source else "")
    to = parameters.to.strip()
    if to:
        _check_address(to, "Write the e-mail address of the organizer")
    organizer_last = False
    if missing:
        if parameters.mail and not to and (mails.server or mails.chrono):
            raise UsageError("This invitation names no organizer to answer")
        if to or not parameters.mail:  # the person's word: the address, also with no mail, or empty for an appointment of your own
            text = with_organizer(text, to) if text is not None else None
            keeping = with_organizer(keeping, to) if keeping is not None else None
        mails = mails if parameters.mail else quiet(mails)
        organizer_last = bool(to) and mails.bare and text is not None
    mailing = mails.chrono and parameters.mail and message.method != "PUBLISH"  # a published item asks no answer
    mail_to = to if missing else named
    reply = _answer_text(opened, you, parameters.answer, text, mail_to) if mailing else None
    waiting = False
    try:
        if target is not None:
            with open_pim(engine, target[0]) as store, item_writer(engine, target[0], store) as writer:
                if text is None and item is not None and stored is not None:
                    delete_copy(writer, target[1], item, stored.text, mails)  # a declined task or shared item: the copy goes; a server that answers mails the decline itself (D-310)
                elif organizer_last and text is not None:
                    writer.put_organizer_last(target[1], text, item)  # told nothing, a server that mails whatever it is told stays silent only this way (D-310)
                elif text is not None:
                    writer.put(target[1], copy_for(text, mails), item, mailed=mails.server and parameters.mail)
                waiting = writer.waiting > 0
        if keeping is not None:
            _keep_declined(engine, keeping)
        if declined is not None:
            _leave_local_folders(engine, declined, item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    stored_in = _label(engine, target[0], target[1]) if target is not None and parameters.answer != "DECLINED" else ""
    draft_folder = draft_message = draft_account = None
    if reply is not None:
        draft_folder, draft_message = _save_mail(engine, opened, you.account, _SUBJECT_WORD[parameters.answer],
                                                 f"{you.name or you.address} has {_ANSWER_WORD[parameters.answer]} this invitation.\n", reply, mail_to or None)
        draft_account = you.account
    copy_tells = text is not None and target is not None and (target[0] != LOCAL_ACCOUNT_ID or message.occurrence is not None or adding)  # only a whole copy in Local Folders knows its mail, and can take the mark off when it goes
    _remember(engine, opened, MessageFlag.INVITE_DECLINED if parameters.answer == "DECLINED" and not copy_tells else None, mails.server and message.method != "PUBLISH")
    return AnswerResult(parameters.answer, stored_in, mails.server, draft_folder, draft_message, draft_account, waiting, uid=message.uid)


@dataclass(frozen=True, slots=True, kw_only=True)
class AddParameters(SourceParameters):
    calendar: str = field(default="", metadata=option("The calendar the events go into"))
    task_list: str = field(default="", metadata=option("The task list the tasks go into"))


@dataclass(frozen=True, slots=True)
class AddResult:
    added: int
    skipped: int  # items one of your calendars holds already


def invitation_add(engine: Engine, parameters: AddParameters) -> AddResult:
    """Add all (D-310): the items of a text that ask no answer, each its own object: events in the chosen calendar, tasks in the chosen task
    list, read once. An item one of your calendars holds is handled, so it is skipped, whichever calendar was chosen."""
    opened = _opened(engine, parameters, busy=True, whole=True)
    if opened.message.method != "PUBLISH":
        raise UsageError("These items ask for an answer: each has its own buttons")
    items = text_items(opened.text)
    chosen = {"event": parameters.calendar, "task": parameters.task_list}
    places = {kind: [position for position, each in enumerate(items.kinds) if each == kind] for kind in chosen}
    for kind, collection_kind in (("event", CollectionKind.EVENTS), ("task", CollectionKind.TASKS)):
        if places[kind] and _collection(engine, collection_kind, chosen[kind])[1].read_only:
            raise UsageError(f"This {_home(collection_kind)} is read-only")
    if not places["event"] and not places["task"]:
        raise UsageError("This holds nothing to add")
    skipped = kept_items(engine, opened.text)
    added = 0
    for kind, collection_kind in (("event", CollectionKind.EVENTS), ("task", CollectionKind.TASKS)):
        wanted = [(items.uids[position], items.texts[position]) for position in places[kind] if position not in skipped]
        if wanted:
            added += _put_items(engine, collection_kind, chosen[kind], wanted)
    return AddResult(added, len(skipped))


def _put_items(engine: Engine, kind: CollectionKind, collection: str, items: list[tuple[str, str]]) -> int:
    """Items (their uid and text) into a calendar or task list, each as it was written (what a file holds comes back identical), server first:
    how many went in."""
    account, info = _collection(engine, kind, collection)
    try:
        with open_pim(engine, account) as store, item_writer(engine, account, store) as writer:
            for uid, text in items:
                known = store.index.find_uid(info.collection_id, uid)
                writer.put(info, published_copy(text) or text, known.item_id if known else None)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return len(items)


def _check_address(address: str, wanted: str) -> None:
    if "@" not in address or any(character.isspace() or character in "<>,;\"" for character in address) or len(address) > 254:
        raise UsageError(wanted)


def _answer_text(opened: _Opened, you: _You, answer: str, copy_text: str | None = None, to: str = "") -> str:
    """The calendar answer for the organizer (METHOD:REPLY), checked before anything is written. to: the organizer the person named for an item that names none."""
    organizer = opened.message.organizer
    if not to and (organizer is None or not organizer.address):
        raise UsageError("This invitation names no organizer to answer")
    reply = reply_text(opened.meeting or opened.text, you.address, answer, datetime.now(UTC), you.name, copy_text, to, bare=opened.kept and not opened.meeting)  # each date as your copy says
    if reply is None:
        raise UsageError("The invitation could not be read")
    return reply


def _save_mail(engine: Engine, opened: _Opened, account: str, word: str, body: str, calendar: str, to: str | None = None) -> tuple[str, str]:
    """A draft of `account` to the organizer (or `to`): readable text and the calendar message (RFC 6047), sent only when the person presses Send."""
    organizer = opened.message.organizer
    if to is None:
        if organizer is None or not organizer.address:
            raise UsageError("This calendar message names no organizer to write to")
        to = formataddr((organizer.name, organizer.address))  # quoted: "Doe, John" stays one name
    subject = word if opened.message.kind == "freebusy" or (opened.kept and not opened.meeting) else f"{word}: {(opened.title if opened.kept else opened.message.title) or '(no title)'}"
    saved = draft_save(engine, DraftSaveParameters(
        account=account, to=(to,), subject=subject, body=body, kind="reply", source_folder=opened.source[0] if opened.source else None,
        source_message=opened.source[1] if opened.source else None,
        in_reply_to=opened.message_id, references=(opened.message_id,) if opened.message_id else (), calendar=calendar,
    ))
    return saved.folder, saved.message_id


def _collection(engine: Engine, kind: CollectionKind, collection: str) -> tuple[str, CollectionInfo]:
    if not collection:
        raise UsageError(f"Choose the {_home(kind)} to keep it in")
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                for info in store.collections(kind):
                    if info.collection_id == collection:
                        return account, info
        except ChronoMailError:  # an account whose calendars cannot be opened is skipped
            continue
    raise UsageError(f"There is no such {_home(kind)}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyParameters(SourceParameters):
    dry: bool = field(default=False, metadata=option("Change nothing: only say whether this makes a mail (done: asked)"))


@dataclass(frozen=True, slots=True)
class ApplyResult:
    done: str  # updated, cancelled, asked (a request for the original invite is ready to send)
    stored_in: str
    draft_folder: str | None = None
    draft_message: str | None = None
    draft_account: str | None = None


def invitation_apply(engine: Engine, parameters: ApplyParameters) -> ApplyResult:
    """What the organizer changed, into your calendar, when the person says so: a newer version copied into your copy (your answer kept unless its time
    changed), a cancellation (the item, or the one occurrence, Cancelled: it still exists). Added dates of a series you do not have: a request to send it again (of a series you have: an answer). Server first (item_writer). A message older than your copy changes nothing (OR-4:
    the command checks it, not only the window)."""
    opened = _opened(engine, parameters)
    message = opened.message
    kind = _kind(message)
    item_word, home = _item(message), _home(kind)
    stored = _stored(engine, kind, message.uid)
    try:
        you = _you(engine, opened)
    except UsageError:  # mail to an alias: only a request for the latest version needs your address
        you = _You("", "", "")
    if message.method == "ADD" and (stored is None or read_calendar_message(stored.text, None) is None):  # no series kept: ask for it (RFC 5546 3.2.4)
        if not you.address:
            raise UsageError("None of your accounts received this message: there is no address to ask from")  # a series you do not have: ask the organizer for it (RFC 5546 section 3.2.4)
        refresh = refresh_text(opened.text, you.address, datetime.now(UTC))
        if refresh is None:
            raise UsageError("The calendar message could not be read")
        if parameters.dry:
            return ApplyResult("asked", "")
        folder, draft = _save_mail(engine, opened, you.account, "Refresh", f"Please send me the latest version of this {item_word}.\n", refresh)
        return ApplyResult("asked", "", folder, draft, you.account)
    if parameters.dry:
        return ApplyResult("", "")
    if stored is None:
        raise UsageError(f"This {item_word} is not in your {home}")
    occurrence = message.occurrence
    kept = read_calendar_message(stored.text, occurrence) if occurrence is not None else read_calendar_message(stored.text, None)
    base = kept or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
    if base is not None and is_newer(message, base.sequence, None) is False:
        raise UsageError(f"Your {home} has a newer version of this {item_word}: this message is out of date")  # OR-4: the command checks it
    if message.method == "CANCEL":
        moments = [moment for moment in occurrences_of(opened.text) if holds_occurrence(stored.text, moment)] if occurrence is not None else list[date | datetime]()  # every occurrence the cancellation names
        if occurrence is not None and not moments:
            raise UsageError(f"This occurrence is not in your {home}")
        if cancelled_in(stored.text, moments):
            raise UsageError(f"This {item_word} is cancelled in your {home} already")
        text, done = cancelled_copy(stored.text, moments, message.sequence), "cancelled"
    elif message.method in ("REQUEST", "PUBLISH"):
        if base is not None and is_newer(message, base.sequence, None) is not True:
            raise UsageError(f"Your {home} has this version of the {item_word} already, or a newer one")
        text, done = updated_copy(stored.text, opened.text, you.address), "updated"
    else:
        raise UsageError(f"This calendar message changes nothing in your {home}")
    if text is None:
        raise UsageError("The calendar message could not be read")
    mails = who_mails(engine, (stored.account, stored.info), you.account, you.address) if you.address else Mailing()
    try:
        with open_pim(engine, stored.account) as store, item_writer(engine, stored.account, store) as writer:
            writer.put(stored.info, copy_for(text, mails), stored.item)
            return ApplyResult(done, _label(engine, stored.account, stored.info))
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegateParameters(SourceParameters):
    to: str = field(metadata=option("The address of the person who goes in your place"))
    mail: bool = field(default=True, metadata=option("Make the mails (drafts to send); false: none, your answer only in your calendar"))
    calendar: str = field(default="", metadata=option("A copy you declined in Local Folders: the calendar it goes into"))


@dataclass(frozen=True, slots=True)
class DelegateResult:
    account: str  # the drafts are of this account
    folder: str
    request_message: str  # the invitation passed on to the delegate
    answer_message: str  # the answer DELEGATED to the organizer; "": the calendar's server mails it, or nobody does
    server_answers: bool = False  # the calendar's server tells the organizer
    uid: str = ""  # of the item: every open card of it shows what your calendar holds now


def invitation_delegate(engine: Engine, parameters: DelegateParameters) -> DelegateResult:
    """Someone else goes in your place (RFC 5546 section 3.2.2.3): two drafts to send, and your answer DELEGATED in your copy, server first.
    Everything is checked before anything is written."""
    opened = _opened(engine, parameters)
    _answerable_there(engine, parameters, opened, "DELEGATED")
    message = opened.message
    if message.method not in ("REQUEST", "ADD"):
        raise UsageError("Only an invitation can be passed on")
    _not_yours(engine, opened)
    delegate = parameters.to.strip()
    _check_address(delegate, "Write the e-mail address of the person who goes in your place")
    you = _you(engine, opened)
    if delegate.lower() == you.address:
        raise UsageError("You cannot pass the invitation on to yourself")
    organizer = message.organizer
    if organizer is None or not organizer.address:
        raise UsageError("This invitation names no organizer to answer")
    stored = _copy_meant(engine, _kind(message), parameters, message.uid)
    _not_cancelled(opened, stored)
    opened = _with_its_mail(engine, opened, you, stored, bool(parameters.occurrence))
    meeting = opened.meeting or (shared_text(opened.text) if opened.kept else opened.text)
    texts = delegated_texts(meeting, you.address, delegate, datetime.now(UTC), you.name, bare=opened.kept and not opened.meeting) if meeting is not None else None
    if texts is None:
        raise UsageError("The invitation could not be read")
    request, answer = texts
    adding = message.method == "ADD"
    declined = _kept_declined(engine, stored) if not adding else None
    stored = None if declined is not None else stored
    if adding:
        _series_kept(stored, _home(_kind(message)))
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else None
        base = same or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
        if base is not None and is_newer(message, base.sequence, None) is False:
            raise UsageError(f"Your {_home(_kind(message))} has a newer version of this {_item(message)}: this invitation is out of date")  # OR-4: the command checks it
    take = False
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else read_calendar_message(stored.text, None)
        take = same is None or bool(is_newer(message, same.sequence, None))  # a newer invitation: its version into the copy (as Accept)
    text = merged_answer(stored.text, opened.text, you.address, "DELEGATED", message.occurrence, take_message=take, name=you.name) if stored is not None else None
    target, item = ((stored.account, stored.info), stored.item) if stored is not None else (None, None)
    if declined is not None:  # as Accept and Tentative: it goes to the calendar the person picks
        target = _collection(engine, _kind(message), parameters.calendar)
        if target[1].read_only:
            raise UsageError(f"This {_home(_kind(message))} is read-only")
        if declined.info.collection_id == target[1].collection_id:
            item = declined.item  # the person chose the calendar it is in: it becomes an appointment there
        elif _holds(engine, target, message.uid):
            raise UsageError(f"This {_item(message)} is in that {_home(_kind(message))} already")
        text = _one_date_moved(declined, opened, you, "DELEGATED") or answered_copy(opened.text, you.address, "DELEGATED", you.name)
    moments = added_dates(opened.text) if adding else occurrences_of(opened.text) if message.occurrence is not None else []  # a whole series (also with changed dates): all of it
    if adding and stored is not None:  # only the new dates, and as with Decline they are not added: one that is there already is marked
        text = answered_added(stored.text, opened.text, you.address, "DELEGATED", you.name)
    text = with_delegate(text, you.address, delegate, moments) if text is not None else None  # a server answering itself names them
    mails = who_mails(engine, target if text is not None else None, you.account, you.address)
    mails = mails if parameters.mail else quiet(mails)
    if target is not None and text is not None:  # the calendar first: a refusal leaves no drafts behind
        try:
            with open_pim(engine, target[0]) as store, item_writer(engine, target[0], store) as writer:
                writer.put(target[1], copy_for(text, mails), item)
            if declined is not None:
                _leave_local_folders(engine, declined, item)
        except StorageError as error:
            raise UsageError(str(error)) from None
    who = you.name or you.address
    task = message.kind == "task"
    asked = f"{who} asks you to do this task in their place.\n" if task else f"{who} asks you to go to this meeting in their place.\n"
    if not parameters.mail:
        _remember(engine, opened, MessageFlag.INVITE_DELEGATED)
        return DelegateResult(you.account, "", "", "", uid=message.uid)
    passed = _save_mail(engine, opened, you.account, "Invitation", asked, request, to=delegate)
    _remember(engine, opened, MessageFlag.INVITE_DELEGATED)
    if not mails.chrono:  # the server mails your answer DELEGATED itself, or you want no such mails (D-310)
        return DelegateResult(you.account, passed[0], passed[1], "", mails.server, uid=message.uid)
    told = f"{who} has asked {delegate} to do this task in their place.\n" if task else f"{who} has asked {delegate} to go to this meeting in their place.\n"
    answered = _save_mail(engine, opened, you.account, "Delegated", told, answer)
    return DelegateResult(you.account, passed[0], passed[1], answered[1], uid=message.uid)


Organize = Literal["take", "accept_proposal", "decline_proposal", "send_latest"]
_ORGANIZE_FOR: dict[str, str] = {"take": "REPLY", "accept_proposal": "COUNTER", "decline_proposal": "COUNTER", "send_latest": "REFRESH"}


@dataclass(frozen=True, slots=True, kw_only=True)
class OrganizeParameters(SourceParameters):
    action: Organize = field(metadata=option("take (an attendee's answer), accept_proposal, decline_proposal, or send_latest (to whoever asks)"))
    mail: bool = field(default=True, metadata=option("When Chrono Mail mails: make the mail (a draft to send); false: none"))
    dry: bool = field(default=False, metadata=option("Change nothing: only check the action and say whether Chrono Mail would mail"))


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    done: str  # taken, moved, declined, sent
    stored_in: str
    server_mails: bool  # the calendar's server mails the attendees itself
    draft_account: str | None = None
    draft_folder: str | None = None
    draft_message: str | None = None
    chrono: bool = False  # dry: Chrono Mail would mail


def _sender(opened: _Opened, yours: Iterable[str]) -> str:
    """The attendee who wrote the message, chosen as the card does; never you."""
    found = writing([person.address for person in opened.message.attendees], opened.senders, set(yours))
    if found is None:
        raise UsageError("This calendar message names no attendee")
    return found


def _answering(opened: _Opened) -> str:
    """The attendee whose answer this is, chosen as the card does."""
    found = answering([person.address for person in opened.message.attendees if person.address], opened.senders)
    if found is None:
        raise UsageError("This answer does not show whose it is")
    return found


def invitation_organize(engine: Engine, parameters: OrganizeParameters) -> OrganizeResult:
    """You are the organizer: an attendee's answer, proposal or request for the latest version, into your meeting or answered (D-310 step 3).
    Everything is checked before anything is written; your meeting is written server first."""
    opened = _opened(engine, parameters)
    message = opened.message
    item_word, home = _item(message), _home(_kind(message))
    if message.method != _ORGANIZE_FOR.get(parameters.action, ""):
        raise UsageError("This calendar message asks for something else")
    organizer = message.organizer
    yours = {settings.account.email_address.strip().lower(): settings for settings in engine.config().accounts if settings.account.email_address}
    own = yours.get(organizer.address.strip().lower()) if organizer is not None else None
    if own is None:
        raise UsageError(f"You are not the organizer of this {item_word}")
    if message.method in ("COUNTER", "REFRESH") and opened.marks is not None and opened.marks & ANSWER_MARKS:
        raise UsageError("You have answered this message already")  # OR-4: the card greys its buttons, the command checks it
    stored = _stored(engine, _kind(message), message.uid)
    if stored is None:
        if parameters.action != "send_latest":
            raise UsageError(f"This {item_word} is not in your {home}")
    elif stored.info.read_only:
        raise UsageError(f"This {home} is read-only")
    attendee = _answering(opened) if parameters.action == "take" else _sender(opened, yours)  # (an answer may come from another account of yours)
    kept = (read_calendar_message(stored.text, None) or read_calendar_message(stored.text)) if stored is not None else None
    same = read_calendar_message(stored.text, message.occurrence) if stored is not None and message.occurrence is not None else None
    base = same or kept  # a date is compared with its own version when it has one, else with its series (as invitation.answer and the card)
    if base is not None and message.method in ("REPLY", "COUNTER") and is_newer(message, base.sequence, None) is False:
        raise UsageError(f"This message is about an older version of the {item_word}: it has changed since")  # OR-4: the command checks it
    if base is not None and message.method == "COUNTER" and is_newer(message, base.sequence, None):
        raise UsageError(f"This proposal is about a version of the {item_word} you do not have")  # (a proposal is about the version you sent, never a later one)
    if kept is not None and (kept.organizer is None or kept.organizer.address.strip().lower() not in yours):
        raise UsageError(f"You are not the organizer of the {item_word} in your {home}")
    moments = tuple(occurrences_of(opened.text)) if message.occurrence is not None else ()
    if stored is not None and moments and message.method != "REFRESH" and not any(in_meeting(stored.text, moment) for moment in moments):
        raise UsageError(f"This date is not in your {item_word} any more")  # OR-4: the card greys its buttons, the command checks it
    if stored is not None and parameters.action == "accept_proposal" and not proposal_usable(stored.text, opened.text):
        raise UsageError(f"This proposal changes nothing in your {item_word}, or its times cannot be used")
    now = datetime.now(UTC)
    text: str | None = None
    calendar: str | None = None
    to: tuple[str, ...] = ()
    word = ""
    if parameters.action == "take":
        text = with_attendee_answer(stored.text, opened.text, attendee) if stored is not None else None
        done = "taken"
    elif parameters.action == "accept_proposal":
        text = countered(stored.text, opened.text, organizer.address if organizer is not None else "") if stored is not None else None
        done = "moved"
        calendar = request_of(text, now, occurrences=moments) if text is not None else None
        to = tuple(sorted(address for address in attendee_addresses(calendar or "") if address not in yours))
        word = "Changed"
    elif parameters.action == "decline_proposal":
        calendar, to, word, done = declinecounter_text(opened.text, now), (attendee,), "Proposal declined", "declined"
    else:  # send_latest: the meeting as it is now (RFC 5546 section 3.2.6); one no longer in your calendar has nothing to send
        calendar = request_of(stored.text, now) if stored is not None else None
        if calendar is None:
            raise UsageError(f"This {item_word} is not in your {home} any more: there is no latest version to send")
        to, word, done = (attendee,), "Invitation", "sent"
    if parameters.action in ("take", "accept_proposal") and text is None:
        raise UsageError("The calendar message could not be read")
    mails = who_mails(engine, (stored.account, stored.info) if stored is not None else None, own.account.account_id, own.account.email_address.strip().lower(), answers=False)
    server = mails.server
    # A refusal of a proposal and the latest version exist only as mails: always made. A moved meeting: what the person said (D-310).
    mailing = calendar is not None and parameters.mail and (parameters.action in ("decline_proposal", "send_latest") or mails.chrono)
    if mailing and not to:
        raise UsageError(f"This {item_word} has no attendees to write to")
    if parameters.dry:
        return OrganizeResult("", "", server, chrono=mailing)
    if parameters.action == "accept_proposal" and not parameters.mail:
        mails, server = quiet(mails), False  # nobody is told, as the person said
    if stored is not None and text is not None:
        try:
            with open_pim(engine, stored.account) as store, item_writer(engine, stored.account, store) as writer:
                writer.put(stored.info, copy_for(text, mails), stored.item)
        except StorageError as error:
            raise UsageError(str(error)) from None
    stored_in = _label(engine, stored.account, stored.info) if stored is not None else ""
    if not mailing or calendar is None:
        if parameters.action == "decline_proposal":
            _remember(engine, opened, MessageFlag.INVITE_DECLINED)  # refused without a mail: handled all the same
        return OrganizeResult(done, stored_in, server)
    title = (base.title if base is not None else "") or message.title or "(no title)"  # your meeting's: a request for the latest version has none
    body = {"Changed": f"The {item_word} has changed.\n", "Proposal declined": f"Thank you for your proposal; the {item_word} stays as it is.\n",
            "Invitation": f"The latest version of this {item_word}.\n"}[word]
    saved = draft_save(engine, DraftSaveParameters(
        account=own.account.account_id, to=to, subject=f"{word}: {title}", body=body, kind="reply", source_folder=opened.source[0] if opened.source else None,
        source_message=opened.source[1] if opened.source else None, in_reply_to=opened.message_id, references=(opened.message_id,) if opened.message_id else (), calendar=calendar,
    ))
    if parameters.action != "accept_proposal":  # (an accepted proposal shows in the meeting itself)
        _remember(engine, opened, MessageFlag.INVITE_DECLINED if parameters.action == "decline_proposal" else MessageFlag.INVITE_SENT)
    return OrganizeResult(done, stored_in, server, own.account.account_id, saved.folder, saved.message_id)


@dataclass(frozen=True, slots=True)
class ReopenResult:
    removed: bool  # false: it was not in your calendar any more
    stays: bool = False  # a declined event stays where it is, nothing changed: only the four answers come back


def invitation_reopen(engine: Engine, parameters: SourceParameters) -> ReopenResult:
    """Change my answer: your copy goes without a word to the organizer, so the invitation can be answered anew (D-310). A date of a series, or
    dates added to it: only those dates go. Decline never deletes: a declined event stays where it is and nothing changes, the window only brings back the four answers."""
    opened = _opened(engine, parameters)
    message = opened.message
    _not_yours(engine, opened)
    card = card_of(engine, parameters.part, opened.text)
    if card is not None and message.kind == "event" and card.your_answer == "DECLINED" and copy_holds(card):
        return ReopenResult(False, True)
    you = _you(engine, opened)
    stored = _stored(engine, _kind(message), message.uid)
    _remember(engine, opened, None)
    if stored is None:
        return ReopenResult(False)
    text = ""  # the whole copy goes
    if message.occurrence is not None or message.method == "ADD":
        text = without_dates(stored.text, added_dates(opened.text) if message.method == "ADD" else occurrences_of(opened.text))
        if text is None:
            raise UsageError("Your copy could not be read")
    mails = who_mails(engine, (stored.account, stored.info), you.account, you.address, "" if text else stored.item)
    silent = quiet(mails)  # nobody is told: a new answer follows
    try:
        with open_pim(engine, stored.account) as store, item_writer(engine, stored.account, store) as writer:
            if text:
                writer.put(stored.info, copy_for(text, silent), stored.item)
            else:
                delete_copy(writer, stored.info, stored.item, stored.text, silent)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ReopenResult(True)


@dataclass(frozen=True, slots=True, kw_only=True)
class BlocksParameters(SourceParameters):
    first: int = field(metadata=option("The place of the first item wanted"))
    count: int = field(metadata=option("How many items"))


@dataclass(frozen=True, slots=True)
class BlocksResult:
    parts: tuple[InvitationInfo, ...]
    left: int  # how many items of the text are in none of your calendars yet


def invitation_blocks(engine: Engine, parameters: BlocksParameters) -> BlocksResult:
    """The cards of some items of a text with several items: the next ones as the person scrolls, or those that changed."""
    if parameters.first < 0 or parameters.count < 1:
        raise UsageError("Name a place and how many items")
    opened = _opened(engine, replace(parameters, item=-1), busy=True, whole=True)
    parts = tuple(with_writers(part, opened.senders) for part in blocks_of(engine, parameters.part, opened.whole, parameters.first, parameters.count, bool(parameters.file)))
    return BlocksResult(parts, items_left(engine, opened.whole))


def invitation_card(engine: Engine, parameters: SourceParameters) -> InvitationInfo:
    """The card of an attachment or a file, for a window of its own: the mail's own card comes with message.show."""
    opened = _opened(engine, parameters, busy=True, whole=True)
    card = card_of(engine, parameters.part, opened.whole, bool(parameters.file))
    if card is None:
        raise UsageError("This holds no calendar item that can be shown")
    card = replace(card, own_mail=opened.own)
    return with_mark(card, opened.marks, opened.senders) if opened.marks is not None else with_writers(card, opened.senders)


@dataclass(frozen=True, slots=True, kw_only=True)
class BusyParameters(SourceParameters):
    calendars: tuple[str, ...] = field(default=(), metadata=option("A calendar whose events count (repeat for more)"))


@dataclass(frozen=True, slots=True)
class BusyResult:
    account: str  # the answer is a draft of this account
    folder: str
    message: str
    periods: int  # how many busy times it holds


def _moment_of(value: date | datetime | None, zone: tzinfo) -> datetime | None:
    """A moment of the period asked for, as a time with a zone; a day or a time without a zone (against RFC 5546) counts in this computer's."""
    if value is None:
        return None
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


def _period_asked(message: CalendarMessage) -> tuple[datetime, datetime]:
    """The period a request asks about, as long as it asks: a period with too much in it stops the answer (_busy_occurrences), and says so."""
    zone = local_zone()
    first, last = _moment_of(message.period_start, zone), _moment_of(message.period_end, zone)
    if first is None or last is None:
        raise UsageError("This request names no period")
    try:
        if last <= first:
            raise UsageError("This request names no period")
        return first.astimezone(UTC), last.astimezone(UTC)
    except (OverflowError, ValueError):
        raise UsageError("The period of this request cannot be used") from None


def _busy_occurrences(engine: Engine, calendars: tuple[str, ...], first: datetime, last: datetime) -> list[Occurrence]:
    """Every occurrence in these calendars in the period. A calendar that cannot be read, or a period with too much in it, stops the answer:
    a time left out would tell the requester that you are free."""
    found: list[Occurrence] = []
    missing = set(calendars)
    zone = local_zone()
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                chosen = tuple(info.collection_id for info in store.collections(CollectionKind.EVENTS) if info.collection_id in missing)
                if not chosen:
                    continue
                items = store.index.query(ItemQuery(CollectionKind.EVENTS, chosen, "", start=int(first.timestamp()), end=int(last.timestamp()), limit=MAXIMUM_EVENTS + 1))
                if len(items) > MAXIMUM_EVENTS:
                    raise UsageError("Your calendars hold too much in this period to answer")
                for item in items:
                    times = occurrences([store.get(item.collection, item.item_id)], first, last, zone)
                    found.extend(times)
                    if len(times) >= MAXIMUM_OCCURRENCES or len(found) > _MOST_BUSY_ITEMS:
                        raise UsageError("Your calendars hold too much in this period to answer")
                missing.difference_update(chosen)
        except ChronoMailError as error:
            if isinstance(error, UsageError):
                raise
            continue
    if missing:
        raise UsageError("One of the calendars could not be read")
    return found


def _busy_text(who: str, first: datetime, last: datetime, periods: tuple[BusyPeriod, ...]) -> str:
    """The text of the answer: the period and the busy times to read, in this computer's time; only times, never what the items are."""
    def moment(value: datetime) -> str:
        return local_time(value).strftime("%A %d %B %Y, %H:%M")

    lines = [f"{moment(period.start)} - {moment(period.end)}{' (tentative)' if period.kind == 'BUSY-TENTATIVE' else ''}" for period in periods]
    heading = f"The busy times of {who} from {moment(first)} to {moment(last)}"
    return f"{heading}:\n\n" + "\n".join(lines) + "\n" if lines else f"{heading}: none.\n"


def invitation_busy(engine: Engine, parameters: BusyParameters) -> BusyResult:
    """Your answer to a request for your busy times, from the calendars you ticked: a draft to the requester, only sent with Send (D-310 step 4)."""
    opened = _opened(engine, parameters, busy=True)
    message = opened.message
    if message.kind != "freebusy" or message.method != "REQUEST":
        raise UsageError("This message does not ask for your busy times")
    if message.organizer is None or not message.organizer.address:
        raise UsageError("This request names no one to answer")
    if message.organizer.address.strip().lower() in your_addresses(engine):
        raise UsageError("You asked for these busy times yourself: there is nothing to answer")
    if not parameters.calendars:
        raise UsageError("Choose at least one calendar")
    you = _you(engine, opened)
    first, last = _period_asked(message)
    periods = busy_periods(_busy_occurrences(engine, parameters.calendars, first, last), first, last, your_addresses(engine), local_zone())
    calendar = busy_reply(message, you.address, you.name, first, last, periods, datetime.now(UTC))
    folder, draft = _save_mail(engine, opened, you.account, "Busy times", _busy_text(you.name or you.address, first, last, periods), calendar)
    return BusyResult(you.account, folder, draft, len(periods))


INVITATION_CARD: TypedCommand[SourceParameters, InvitationInfo] = TypedCommand("invitation.card", SourceParameters, InvitationInfo)
INVITATION_REOPEN: TypedCommand[SourceParameters, ReopenResult] = TypedCommand("invitation.reopen", SourceParameters, ReopenResult)
INVITATION_BLOCKS: TypedCommand[BlocksParameters, BlocksResult] = TypedCommand("invitation.blocks", BlocksParameters, BlocksResult)
INVITATION_BUSY: TypedCommand[BusyParameters, BusyResult] = TypedCommand("invitation.busy", BusyParameters, BusyResult)
INVITATION_CHOICES: TypedCommand[ChoicesParameters, ChoicesResult] = TypedCommand("invitation.choices", ChoicesParameters, ChoicesResult)
INVITATION_ADD: TypedCommand[AddParameters, AddResult] = TypedCommand("invitation.add", AddParameters, AddResult)
INVITATION_ANSWER: TypedCommand[AnswerParameters, AnswerResult] = TypedCommand("invitation.answer", AnswerParameters, AnswerResult)
INVITATION_APPLY: TypedCommand[ApplyParameters, ApplyResult] = TypedCommand("invitation.apply", ApplyParameters, ApplyResult)
INVITATION_DELEGATE: TypedCommand[DelegateParameters, DelegateResult] = TypedCommand("invitation.delegate", DelegateParameters, DelegateResult)
INVITATION_ORGANIZE: TypedCommand[OrganizeParameters, OrganizeResult] = TypedCommand("invitation.organize", OrganizeParameters, OrganizeResult)


def register(registry: CommandRegistry) -> None:
    registry.register(INVITATION_CARD.id, "The card of a calendar attachment or file", SourceParameters, invitation_card, doing="Reading the calendar file")
    registry.register(INVITATION_REOPEN.id, "Change my answer: your copy goes, without a word to the organizer", SourceParameters, invitation_reopen, doing="Changing your answer")
    registry.register(INVITATION_BLOCKS.id, "The cards of some items of a calendar text with several items", BlocksParameters, invitation_blocks, doing="Reading the calendar file")
    registry.register(INVITATION_BUSY.id, "Answer a request for your busy times from these calendars: a draft to send", BusyParameters, invitation_busy,
                      doing="Working out your busy times")
    registry.register(INVITATION_CHOICES.id, "The calendars an invitation can go into, and the one to start on", ChoicesParameters, invitation_choices,
                      doing="Reading the invitation")
    registry.register(INVITATION_ADD.id, "Add all items of a calendar text that ask no answer: events to a calendar, tasks to a task list", AddParameters, invitation_add,
                      doing="Adding the items")
    registry.register(INVITATION_APPLY.id, "Take the organizer's change into your calendar: a newer version, a cancellation, added occurrences",
                      ApplyParameters, invitation_apply, doing="Changing your calendar")
    registry.register(INVITATION_DELEGATE.id, "Pass an invitation on to someone who goes in your place", DelegateParameters, invitation_delegate,
                      doing="Passing the invitation on")
    registry.register(INVITATION_ORGANIZE.id, "Your meeting: take an attendee's answer, accept or decline a proposal, send the latest version",
                      OrganizeParameters, invitation_organize, doing="Changing your meeting")
    registry.register(INVITATION_ANSWER.id, "Accept, accept tentatively or decline an invitation", AnswerParameters, invitation_answer,
                      doing="Answering the invitation")
