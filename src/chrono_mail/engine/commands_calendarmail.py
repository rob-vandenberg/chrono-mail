from dataclasses import dataclass, field

from chrono_mail.core import CollectionInfo, CollectionKind
from chrono_mail.engine.commands_calendar import in_event_zone
from chrono_mail.engine.commands_drafts import DraftOpenParameters, draft_open
from chrono_mail.engine.commands_mail import SendParameters, SendResult, mail_send
from chrono_mail.engine.meetingmail import organizing_account, who_mails
from chrono_mail.engine.pimaccess import Located, located, open_collection
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import cancelled_on, parse_events
from chrono_mail.pim.times import parse_moment

# --- Version ------------------------------------------------------------
__version__ = "commands_calendarmail.py 4.4.552"

# --- Version history ----------------------------------------------------
# v4.4.552: event.mailing says when a meeting about to be deleted is Cancelled: nobody is asked about a mail then (D-310)
# v4.4.532: Initial: a calendar mail goes without the composer, with the person's message, and the window can ask who would mail (D-310)


@dataclass(frozen=True, slots=True)
class CalendarMailParameters:
    account: str = field(metadata=option("The account of the draft"))
    folder: str = field(metadata=option("The folder of the draft"))
    message: str = field(metadata=option("The id of the draft a calendar command made"))
    note: str = field(default="", metadata=option("The person's own message, above the text of the mail"))
    delay_seconds: int = field(default=0, metadata=option("Wait in the Outbox this many seconds before it may be sent"))
    hold: bool = field(default=False, metadata=option("Keep it in the Outbox until the Outbox is sent next"))


def calendar_mail_send(engine: Engine, parameters: CalendarMailParameters) -> SendResult:
    """Send a draft a calendar command made, as the composer would have: through the Outbox, so offline it goes later, and the mail it answers is marked."""
    draft = draft_open(engine, DraftOpenParameters(parameters.account, parameters.message, parameters.folder))
    note = parameters.note.strip()
    return mail_send(engine, SendParameters(
        account=parameters.account, to=draft.to, cc=draft.cc, bcc=draft.bcc, subject=draft.subject, body=f"{note}\n\n{draft.body}" if note else draft.body,
        in_reply_to=draft.in_reply_to, references=draft.references, source_folder=draft.source_folder, source_message=draft.source_message,
        mark_replied=draft.kind == "reply" and draft.source_message is not None, keep_folder=draft.folder, keep_message=draft.message_id,
        keep_attachments=tuple(str(attachment.position) for attachment in draft.attachments), replaces_folder=draft.folder, replaces_message=draft.message_id,
        delay_seconds=parameters.delay_seconds, hold=parameters.hold,
    ))


@dataclass(frozen=True, slots=True)
class MailingParameters:
    calendar: str = field(default="", metadata=option("A meeting about to be made: its calendar"))
    item: str = field(default="", metadata=option("A meeting that exists: its event id"))
    deleting: bool = field(default=False, metadata=option("The meeting is about to be deleted"))
    occurrence: str = field(default="", metadata=option("Deleting: only this date of it"))


@dataclass(frozen=True, slots=True)
class MailingResult:
    chrono: bool  # Chrono Mail would mail the attendees: the calendar's server does not
    cancelled: bool = False  # about to be deleted and Cancelled: Chrono Mail mails nothing, so nobody is asked (D-310)


def _chrono_mails(engine: Engine, account: str, info: CollectionInfo, gone: str) -> MailingResult:
    you = organizing_account(engine, account)
    address = you.account.email_address.strip().lower() if you is not None else ""
    return MailingResult(you is not None and who_mails(engine, (account, info), account, address, gone, answers=False).chrono)


def _deleting_cancelled(found: Located, parameters: MailingParameters) -> bool:
    if not parameters.deleting:
        return False
    parsed = parse_events(found.store.get(found.item.collection, parameters.item))
    if not parsed:
        return False
    try:
        return cancelled_on(parsed[0], in_event_zone(parse_moment(parameters.occurrence), parsed[0].master.start) if parameters.occurrence else None)
    except ValueError as error:
        raise UsageError(f"occurrence: {error}") from None


def event_mailing(engine: Engine, parameters: MailingParameters) -> MailingResult:
    """Who would tell the attendees of your meeting, asked before it is saved or deleted: the person can then still take the action back."""
    if parameters.item:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            if _deleting_cancelled(found, parameters):
                return MailingResult(False, True)
            return _chrono_mails(engine, found.account, found.collection, parameters.item if parameters.deleting and not parameters.occurrence else "")
    with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (account, _, info):
        return _chrono_mails(engine, account, info, "")


CALENDAR_MAIL_SEND: TypedCommand[CalendarMailParameters, SendResult] = TypedCommand("calendar.mail-send", CalendarMailParameters, SendResult)
EVENT_MAILING: TypedCommand[MailingParameters, MailingResult] = TypedCommand("event.mailing", MailingParameters, MailingResult)


def register(registry: CommandRegistry) -> None:
    registry.register(CALENDAR_MAIL_SEND.id, "Send a mail a calendar command made, with your own message above it", CalendarMailParameters, calendar_mail_send, doing="Sending the mail")
    registry.register(EVENT_MAILING.id, "Say whether Chrono Mail or the calendar's server would mail the attendees of a meeting", MailingParameters, event_mailing, doing="Reading the calendar")
