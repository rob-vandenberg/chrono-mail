from dataclasses import dataclass, field

from chrono_mail.core import CollectionInfo, CollectionKind
from chrono_mail.engine.commands_drafts import DraftOpenParameters, draft_open
from chrono_mail.engine.commands_mail import SendParameters, SendResult, mail_send
from chrono_mail.engine.meetingmail import organizing_account, who_mails
from chrono_mail.engine.pimaccess import located, open_collection
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_calendarmail.py 4.4.532"

# --- Version history ----------------------------------------------------
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
    deleting: bool = field(default=False, metadata=option("The whole meeting is about to be deleted"))


@dataclass(frozen=True, slots=True)
class MailingResult:
    chrono: bool  # Chrono Mail would mail the attendees: the calendar's server does not


def _chrono_mails(engine: Engine, account: str, info: CollectionInfo, gone: str) -> MailingResult:
    you = organizing_account(engine, account)
    address = you.account.email_address.strip().lower() if you is not None else ""
    return MailingResult(you is not None and who_mails(engine, (account, info), account, address, gone, answers=False).chrono)


def event_mailing(engine: Engine, parameters: MailingParameters) -> MailingResult:
    """Who would tell the attendees of your meeting, asked before it is saved or deleted: the person can then still take the action back."""
    if parameters.item:
        with located(engine, parameters.item, CollectionKind.EVENTS) as found:
            return _chrono_mails(engine, found.account, found.collection, parameters.item if parameters.deleting else "")
    with open_collection(engine, parameters.calendar, CollectionKind.EVENTS) as (account, _, info):
        return _chrono_mails(engine, account, info, "")


CALENDAR_MAIL_SEND: TypedCommand[CalendarMailParameters, SendResult] = TypedCommand("calendar.mail-send", CalendarMailParameters, SendResult)
EVENT_MAILING: TypedCommand[MailingParameters, MailingResult] = TypedCommand("event.mailing", MailingParameters, MailingResult)


def register(registry: CommandRegistry) -> None:
    registry.register(CALENDAR_MAIL_SEND.id, "Send a mail a calendar command made, with your own message above it", CalendarMailParameters, calendar_mail_send, doing="Sending the mail")
    registry.register(EVENT_MAILING.id, "Say whether Chrono Mail or the calendar's server would mail the attendees of a meeting", MailingParameters, event_mailing, doing="Reading the calendar")
