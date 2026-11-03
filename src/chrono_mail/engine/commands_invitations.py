"""Answering a calendar message in a mail (D-310): Accept, Tentative and Decline, the calendar it goes into, and who mails the answer."""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import formataddr
from typing import Literal

from chrono_mail.config import AccountSettings
from chrono_mail.core import ChronoMailError, CollectionInfo, CollectionKind, StorageError
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.commands_mail import MessageParameters, _check_id, reply_account
from chrono_mail.engine.invitations import calendar_part
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.pimaccess import account_name, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.mime import ParsedMessage, parse_message
from chrono_mail.pim.itip import (
    CalendarMessage, answered_copy, delegated_texts, holds_occurrence, is_newer, occurrences_of, published_copy, with_delegate, merged_answer, read_calendar_message, refresh_text, reply_text, updated_copy, with_added, without_occurrence,
)

# --- Version ------------------------------------------------------------
__version__ = "commands_invitations.py 4.4.505"

# --- Version history ----------------------------------------------------
# v4.4.505: invitation.delegate: the invitation passed on to someone else, and the answer DELEGATED to the organizer, both as drafts
# v4.4.505: invitation.apply: a newer version, a cancellation or an added occurrence from the organizer into your calendar, server first; an
#           added occurrence of a series you do not have asks the organizer for the latest version (a draft)
# v4.4.505: Initial: invitation.choices (every calendar, the one to start on) and invitation.answer (Accept, Tentative, Decline: the copy
#           in a calendar server first, the answer mailed by the server or as a draft to send), D-310

Answer = Literal["ACCEPTED", "TENTATIVE", "DECLINED"]
_ANSWERABLE: frozenset[str] = frozenset({"REQUEST", "PUBLISH"})  # what asks for an answer, or offers an item to keep (an ADD is added first)
_SUBJECT_WORD: dict[str, str] = {"ACCEPTED": "Accepted", "TENTATIVE": "Tentative", "DECLINED": "Declined"}
_ANSWER_WORD: dict[str, str] = {"ACCEPTED": "accepted", "TENTATIVE": "tentatively accepted", "DECLINED": "declined"}


@dataclass(frozen=True, slots=True)
class CalendarChoice:
    collection: str
    name: str
    account: str
    account_name: str
    read_only: bool
    public: bool  # one of the public folders a server keeps next to the account's own (D-182)


@dataclass(frozen=True, slots=True)
class ChoicesResult:
    calendars: tuple[CalendarChoice, ...]  # every calendar (for a task: every task list) of every account, in the sidebar's order
    chosen: str  # the one the window starts on: the first own, writable one of the account that received the mail; "": none
    stored: str  # the one that holds the item already ("": none): an answer changes the copy there


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerParameters(MessageParameters):
    answer: Answer = field(metadata=option("ACCEPTED, TENTATIVE or DECLINED"))
    calendar: str = field(default="", metadata=option("Accept or Tentative of an item not kept yet: the calendar (task list) to keep it in"))
    mail: bool = field(default=True, metadata=option("When Chrono Mail mails the answer: make it (a draft to send); false: no answer mail"))


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str
    stored_in: str  # the calendar that holds the item now, "Name (account)"; "": none (declined)
    server_answers: bool  # the calendar's server mails the answer itself (D-310)
    draft_folder: str | None  # the answer mail to send, when Chrono Mail mails it
    draft_message: str | None
    draft_account: str | None = None  # the account the answer mail is a draft of


@dataclass(frozen=True, slots=True)
class _Opened:
    parsed: ParsedMessage
    message: CalendarMessage
    text: str  # the calendar part's text
    receiving: AccountSettings | None  # the account the mail was sent to (D-292)


def _opened(engine: Engine, parameters: MessageParameters) -> _Opened:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        parsed = parse_message(raw)
        found = calendar_part(parsed.attachments)
        if found is None:
            raise UsageError("This message holds no calendar message")
        receiving = reply_account(engine, mailbox.settings, parsed, raw)
    position, message = found
    settings = engine.config().find(receiving) if receiving else None
    return _Opened(parsed, message, parsed.attachments[position].data.decode("utf-8", "replace"), settings)


def _kind(message: CalendarMessage) -> CollectionKind:
    return CollectionKind.TASKS if message.kind == "task" else CollectionKind.EVENTS


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
        raise UsageError("You are the organizer of this meeting: there is nothing to answer")


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
    for public_round in (False, True):  # your own calendars first; a public one only when none of yours holds it (where you put it yourself)
        for account in pim_account_ids(engine):
            try:
                with open_pim(engine, account) as store:
                    for info in store.collections(kind):
                        if info.read_only or (info.server_value("public") == "1") != public_round:
                            continue  # a read-only calendar holds someone else's copy: never changed for your answer
                        item = store.index.find_uid(info.collection_id, uid)
                        if item is not None:
                            return _Stored(account, info, item.item_id, store.get(info.collection_id, item.item_id))
            except ChronoMailError:
                continue
    return None


def _label(engine: Engine, account: str, info: CollectionInfo) -> str:
    return f"{info.name} ({account_name(engine, account)})"


def invitation_choices(engine: Engine, parameters: MessageParameters) -> ChoicesResult:
    opened = _opened(engine, parameters)
    kind = _kind(opened.message)
    calendars: list[CalendarChoice] = []
    chosen = ""
    receiving = opened.receiving.account.account_id if opened.receiving is not None else ""
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                for info in store.collections(kind):
                    public = info.server_value("public") == "1"
                    calendars.append(CalendarChoice(info.collection_id, info.name, account, account_name(engine, account), info.read_only, public))
                    if not chosen and account == receiving and not info.read_only and not public:
                        chosen = info.collection_id
        except ChronoMailError:
            continue
    stored = _stored(engine, kind, opened.message.uid)
    return ChoicesResult(tuple(calendars), chosen, stored.info.collection_id if stored is not None else "")


def _server_answers(engine: Engine, account: str, info: CollectionInfo, address: str) -> bool:
    """Whether the calendar's server mails the answer: its account says so (D-310), the calendar is on the server, and the answering address is
    that account's own (a server answers only for its own user)."""
    try:
        settings = engine.config().find(account)
    except ChronoMailError:
        return False
    own = settings.account.email_address.strip().lower()
    return settings.server_answers and info.server_value("href") != "" and (not own or own == address)  # a calendar-only account has no address


def invitation_answer(engine: Engine, parameters: AnswerParameters) -> AnswerResult:
    opened = _opened(engine, parameters)
    message = opened.message
    if message.method not in _ANSWERABLE:
        raise UsageError("This calendar message asks for no answer")
    if parameters.answer not in _SUBJECT_WORD:
        raise UsageError("The answer is ACCEPTED, TENTATIVE or DECLINED")
    if message.method == "REQUEST":
        _not_yours(engine, opened)
    you = _you(engine, opened) if message.method != "PUBLISH" else _You("", "", "")  # a shared item asks no address
    kind = _kind(message)
    stored = _stored(engine, kind, message.uid)
    # Everything is worked out and checked before anything is written: a step that fails leaves nothing half done.
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else None
        base = same or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
        if base is not None and is_newer(message, base.sequence, None) is False:
            raise UsageError("Your calendar has a newer version of this meeting: this invitation is out of date")  # OR-4: the command checks it
    text: str | None = None
    if parameters.answer == "DECLINED":
        holds = stored is not None and (message.occurrence is None or any(holds_occurrence(stored.text, moment) for moment in occurrences_of(opened.text)))
        # (not only dates removed already: the card looks at every date the message names, and so does Decline)
        target = (stored.account, stored.info) if stored is not None and holds else None  # an occurrence not in the copy: Decline adds nothing
        item: str | None = stored.item if stored is not None and holds else None
        if stored is not None and holds and message.occurrence is not None:
            same = read_calendar_message(stored.text, message.occurrence)
            take = same is None or bool(is_newer(message, same.sequence, None))  # a newer version of the date: it replaces the kept one (as Accept)
            text = merged_answer(stored.text, opened.text, you.address, "DECLINED", message.occurrence, take_message=take, name=you.name)
            if text is None:
                raise UsageError("The occurrence could not be marked as declined")
    else:
        if stored is not None and message.method == "PUBLISH":  # a shared item kept already: a newer version as sent, nothing made up
            if not is_newer(message, (read_calendar_message(stored.text) or message).sequence, None):
                raise UsageError("This item is in your calendar already")
            text, target, item = published_copy(opened.text), (stored.account, stored.info), stored.item
        elif stored is not None:
            kept = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else read_calendar_message(stored.text, None)
            take = kept is None or bool(is_newer(message, kept.sequence, None))
            text = merged_answer(stored.text, opened.text, you.address, parameters.answer, message.occurrence, take_message=take, name=you.name)
            target, item = (stored.account, stored.info), stored.item
        else:
            shared = message.method == "PUBLISH"  # kept as sent: no attendee line of yours is made up
            text = published_copy(opened.text) if shared else answered_copy(opened.text, you.address, parameters.answer, you.name)
            target, item = _collection(engine, kind, parameters.calendar), None
        if text is None:
            raise UsageError("The invitation could not be read")
        if target[1].read_only:
            raise UsageError("This calendar is read-only")
    server = target is not None and _server_answers(engine, target[0], target[1], you.address)
    mailing = not server and parameters.mail and message.method != "PUBLISH"  # a published item asks no answer
    reply = _answer_text(opened, you, parameters.answer, text) if mailing else None
    try:
        if target is not None:
            with open_pim(engine, target[0]) as store, item_writer(engine, target[0], store) as writer:
                if text is None and item is not None:
                    writer.delete(target[1], item)  # declined: the copy goes; a server that answers mails the decline itself (D-310)
                elif text is not None:
                    writer.put(target[1], text, item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    stored_in = _label(engine, target[0], target[1]) if target is not None and parameters.answer != "DECLINED" else ""
    draft_folder = draft_message = draft_account = None
    if reply is not None:
        draft_folder, draft_message = _save_mail(engine, opened, you.account, _SUBJECT_WORD[parameters.answer],
                                                 f"{you.name or you.address} has {_ANSWER_WORD[parameters.answer]} this invitation.\n", reply)
        draft_account = you.account
    return AnswerResult(parameters.answer, stored_in, server, draft_folder, draft_message, draft_account)


def _answer_text(opened: _Opened, you: _You, answer: str, copy_text: str | None = None) -> str:
    """The calendar answer for the organizer (METHOD:REPLY), checked before anything is written."""
    organizer = opened.message.organizer
    if organizer is None or not organizer.address:
        raise UsageError("This invitation names no organizer to answer")
    reply = reply_text(opened.text, you.address, answer, datetime.now(UTC), you.name, copy_text)  # each date as your copy says
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
    saved = draft_save(engine, DraftSaveParameters(
        account=account, to=(to,), subject=f"{word}: {opened.message.title or '(no title)'}", body=body, kind="reply",
        in_reply_to=opened.parsed.message_id, references=(opened.parsed.message_id,) if opened.parsed.message_id else (), calendar=calendar,
    ))
    return saved.folder, saved.message_id


def _collection(engine: Engine, kind: CollectionKind, collection: str) -> tuple[str, CollectionInfo]:
    if not collection:
        raise UsageError("Choose the calendar to keep it in")
    for account in pim_account_ids(engine):
        try:
            with open_pim(engine, account) as store:
                for info in store.collections(kind):
                    if info.collection_id == collection:
                        return account, info
        except ChronoMailError:  # an account whose calendars cannot be opened is skipped
            continue
    raise UsageError("There is no such calendar")


@dataclass(frozen=True, slots=True)
class ApplyResult:
    done: str  # updated, removed, added, asked (a request for the latest version is ready to send)
    stored_in: str
    draft_folder: str | None = None
    draft_message: str | None = None
    draft_account: str | None = None


def invitation_apply(engine: Engine, parameters: MessageParameters) -> ApplyResult:
    """What the organizer changed, into your calendar: a newer version (your answer kept unless its time changed), a cancellation (the item,
    or the one occurrence, removed), added occurrences. Server first (item_writer). A message older than your copy changes nothing (OR-4:
    the command checks it, not only the window)."""
    opened = _opened(engine, parameters)
    message = opened.message
    kind = _kind(message)
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
        folder, draft = _save_mail(engine, opened, you.account, "Refresh", "Please send me the latest version of this meeting.\n", refresh)
        return ApplyResult("asked", "", folder, draft, you.account)
    if stored is None:
        raise UsageError("This meeting is not in your calendar")
    occurrence = message.occurrence
    kept = read_calendar_message(stored.text, occurrence) if occurrence is not None else read_calendar_message(stored.text, None)
    base = kept or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
    if base is not None and is_newer(message, base.sequence, None) is False:
        raise UsageError("Your calendar has a newer version of this meeting: this message is out of date")  # OR-4: the command checks it
    remove_all = False
    if message.method == "CANCEL":
        text = stored.text if occurrence is not None else ""
        for moment in occurrences_of(opened.text) if occurrence is not None else ():  # every occurrence the cancellation names
            if text == "":
                break
            if not holds_occurrence(text, moment):
                continue
            text = without_occurrence(text, moment)
            if text is None:
                raise UsageError("The calendar message could not be read")
        if text == stored.text:
            raise UsageError("This occurrence is not in your calendar")
        remove_all = text == ""  # the whole item, or every occurrence it held
        done = "removed"
    elif message.method == "ADD":
        text, done = with_added(stored.text, opened.text), "added"
    elif message.method in ("REQUEST", "PUBLISH"):
        if base is not None and is_newer(message, base.sequence, None) is not True:
            raise UsageError("Your calendar has this version of the meeting already, or a newer one")
        text, done = updated_copy(stored.text, opened.text, you.address), "updated"
    else:
        raise UsageError("This calendar message changes nothing in your calendar")
    if not remove_all and text is None:
        raise UsageError("The calendar message could not be read")
    try:
        with open_pim(engine, stored.account) as store, item_writer(engine, stored.account, store) as writer:
            if remove_all:
                writer.delete(stored.info, stored.item)
                return ApplyResult("removed", "")
            assert text is not None  # checked above
            writer.put(stored.info, text, stored.item)
            return ApplyResult(done, _label(engine, stored.account, stored.info))
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegateParameters(MessageParameters):
    to: str = field(metadata=option("The address of the person who goes in your place"))


@dataclass(frozen=True, slots=True)
class DelegateResult:
    account: str  # the drafts are of this account
    folder: str
    request_message: str  # the invitation passed on to the delegate
    answer_message: str  # the answer DELEGATED to the organizer; "": the calendar's server mails it


def invitation_delegate(engine: Engine, parameters: DelegateParameters) -> DelegateResult:
    """Someone else goes in your place (RFC 5546 section 3.2.2.3): two drafts to send, and your answer DELEGATED in your copy, server first.
    Everything is checked before anything is written."""
    opened = _opened(engine, parameters)
    message = opened.message
    if message.method != "REQUEST":
        raise UsageError("Only an invitation can be passed on")
    _not_yours(engine, opened)
    delegate = parameters.to.strip()
    if "@" not in delegate or any(character.isspace() or character in "<>,;\"" for character in delegate) or len(delegate) > 254:
        raise UsageError("Write the e-mail address of the person who goes in your place")
    you = _you(engine, opened)
    if delegate.lower() == you.address:
        raise UsageError("You cannot pass the invitation on to yourself")
    organizer = message.organizer
    if organizer is None or not organizer.address:
        raise UsageError("This invitation names no organizer to answer")
    texts = delegated_texts(opened.text, you.address, delegate, datetime.now(UTC), you.name)
    if texts is None:
        raise UsageError("The invitation could not be read")
    request, answer = texts
    stored = _stored(engine, _kind(message), message.uid)
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else None
        base = same or read_calendar_message(stored.text, None) or read_calendar_message(stored.text)
        if base is not None and is_newer(message, base.sequence, None) is False:
            raise UsageError("Your calendar has a newer version of this meeting: this invitation is out of date")  # OR-4: the command checks it
    take = False
    if stored is not None:
        same = read_calendar_message(stored.text, message.occurrence) if message.occurrence is not None else read_calendar_message(stored.text, None)
        take = same is None or bool(is_newer(message, same.sequence, None))  # a newer invitation: its version into the copy (as Accept)
    text = merged_answer(stored.text, opened.text, you.address, "DELEGATED", message.occurrence, take_message=take, name=you.name) if stored is not None else None
    moments = occurrences_of(opened.text) if message.occurrence is not None else []  # a whole series (also with changed dates): all of it
    text = with_delegate(text, you.address, delegate, moments) if text is not None else None  # a server answering itself names them
    server = stored is not None and text is not None and _server_answers(engine, stored.account, stored.info, you.address)
    if stored is not None and text is not None:  # the calendar first: a refusal leaves no drafts behind
        try:
            with open_pim(engine, stored.account) as store, item_writer(engine, stored.account, store) as writer:
                writer.put(stored.info, text, stored.item)
        except StorageError as error:
            raise UsageError(str(error)) from None
    who = you.name or you.address
    passed = _save_mail(engine, opened, you.account, "Invitation", f"{who} asks you to go to this meeting in their place.\n", request, to=delegate)
    if server:  # the server mails your answer DELEGATED itself (D-310)
        return DelegateResult(you.account, passed[0], passed[1], "")
    answered = _save_mail(engine, opened, you.account, "Delegated", f"{who} has asked {delegate} to go to this meeting in their place.\n", answer)
    return DelegateResult(you.account, passed[0], passed[1], answered[1])


INVITATION_CHOICES: TypedCommand[MessageParameters, ChoicesResult] = TypedCommand("invitation.choices", MessageParameters, ChoicesResult)
INVITATION_ANSWER: TypedCommand[AnswerParameters, AnswerResult] = TypedCommand("invitation.answer", AnswerParameters, AnswerResult)
INVITATION_APPLY: TypedCommand[MessageParameters, ApplyResult] = TypedCommand("invitation.apply", MessageParameters, ApplyResult)
INVITATION_DELEGATE: TypedCommand[DelegateParameters, DelegateResult] = TypedCommand("invitation.delegate", DelegateParameters, DelegateResult)


def register(registry: CommandRegistry) -> None:
    registry.register(INVITATION_CHOICES.id, "The calendars an invitation can go into, and the one to start on", MessageParameters, invitation_choices,
                      doing="Reading the invitation")
    registry.register(INVITATION_APPLY.id, "Take the organizer's change into your calendar: a newer version, a cancellation, added occurrences",
                      MessageParameters, invitation_apply, doing="Changing your calendar")
    registry.register(INVITATION_DELEGATE.id, "Pass an invitation on to someone who goes in your place", DelegateParameters, invitation_delegate,
                      doing="Passing the invitation on")
    registry.register(INVITATION_ANSWER.id, "Accept, accept tentatively or decline an invitation", AnswerParameters, invitation_answer,
                      doing="Answering the invitation")
