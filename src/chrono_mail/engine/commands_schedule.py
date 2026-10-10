from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.config import ServerRole
from chrono_mail.core import ChronoMailError, ConfigurationError, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.commands_mail import MessageParameters, _check_id
from chrono_mail.engine.mailbox import Mailbox, open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.schedule import describe_when, load_snoozed, parse_when, save_snoozed
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.mime import Address, OutgoingMessage, ParsedMessage, parse_message
from chrono_mail.sync import send_and_file

# --- Version ------------------------------------------------------------
__version__ = "commands_schedule.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial send later and snooze

SEND_AT: str = "x-chrono-mail-send-at"
SNOOZED_FOLDER: str = "Snoozed"
SNOOZED_FILE: str = "snoozed.json"


def _notes(parsed: ParsedMessage) -> dict[str, str]:
    return dict(parsed.chrono_headers)


def _due_time(parsed: ParsedMessage) -> int | None:
    text = _notes(parsed).get(SEND_AT, "")
    return int(text) if text.isdigit() else None


@dataclass(frozen=True, slots=True)
class AccountOptional:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))


@dataclass(frozen=True, slots=True)
class ScheduledInfo:
    account: str
    message_id: str
    send_at: str
    seconds: int
    subject: str
    to: tuple[str, ...]


def _accounts(engine: Engine, wanted: str | None) -> list[str]:
    config = engine.config()
    if wanted:
        return [engine.resolve_account(wanted, config).account.account_id]
    return [item.account.account_id for item in config.accounts]


def _scheduled(mailbox: Mailbox) -> list[tuple[MessageRef, ParsedMessage, int]]:
    found: list[tuple[MessageRef, ParsedMessage, int]] = []
    outbox = mailbox.folder("Outbox")
    for message in mailbox.store.list_messages(outbox):
        try:
            parsed = parse_message(mailbox.store.read(message.ref, hint=mailbox.hint(message.ref.store_id)))
        except ChronoMailError:
            continue
        due = _due_time(parsed)
        if due is not None:
            found.append((message.ref, parsed, due))
    return found


def mail_scheduled(engine: Engine, parameters: AccountOptional) -> tuple[ScheduledInfo, ...]:
    """The messages waiting to be sent at a time, soonest first."""
    found: list[ScheduledInfo] = []
    for account in _accounts(engine, parameters.account):
        with open_mailbox(engine, account) as mailbox:
            for reference, parsed, due in _scheduled(mailbox):
                found.append(ScheduledInfo(account, reference.store_id, describe_when(due), due, parsed.subject, tuple(a.address for a in parsed.to_addresses)))
    return tuple(sorted(found, key=lambda item: item.seconds))


@dataclass(frozen=True, slots=True)
class SendDueParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))
    message: str | None = field(default=None, metadata=option("Send this scheduled message now, whatever its time"))


@dataclass(frozen=True, slots=True)
class SendDueResult:
    sent: int
    failed: tuple[str, ...]
    waiting: int


def _outgoing(mailbox: Mailbox, parsed: ParsedMessage) -> OutgoingMessage:
    settings = mailbox.settings
    return OutgoingMessage(
        from_address=Address(settings.account.display_name, settings.account.email_address),
        to_addresses=parsed.to_addresses, cc_addresses=parsed.cc_addresses, bcc_addresses=parsed.bcc_addresses, subject=parsed.subject,
        text_body=parsed.text_body or "", html_body=parsed.html_body, attachments=parsed.attachments,
        in_reply_to=parsed.in_reply_to, references=parsed.references,
    )


def mail_send_due(engine: Engine, parameters: SendDueParameters) -> SendDueResult:
    """Send the scheduled messages whose time has come. The scheduled copy is removed once a verified copy is in the Outbox
    and before the server is asked, so a crash can leave a message unsent in the Outbox but never sends it twice."""
    now, sent, waiting = engine.clock(), 0, 0
    failed: list[str] = []
    for account in _accounts(engine, parameters.account):
        with open_mailbox(engine, account) as mailbox:
            password = engine.passwords.get(account, ServerRole.OUTGOING)
            for reference, parsed, due in _scheduled(mailbox):
                if (parameters.message and parameters.message != reference.store_id) or (not parameters.message and due > now):
                    waiting += 1
                    continue
                if password is None:
                    failed.append(f"{parsed.subject}: no password is stored for the account")
                    continue
                try:
                    outcome = send_and_file(
                        mailbox.store, mailbox.index, mailbox.settings.account.outgoing, password, _outgoing(mailbox, parsed), OUTBOX,
                        mailbox.role_folder(FolderRole.SENT), before_send=_remover(mailbox, reference),
                    )
                except ChronoMailError as error:
                    failed.append(f"{parsed.subject}: {error}")
                    continue
                if outcome.sent:
                    sent += 1
                    _mark_replied(mailbox, parsed)
                else:
                    failed.append(f"{parsed.subject}: {outcome.error} (it stays in the Outbox and is not sent again by itself)")
    return SendDueResult(sent, tuple(failed), waiting)


def _remover(mailbox: Mailbox, reference: MessageRef) -> Callable[[], None]:
    def remove() -> None:
        mailbox.delete(reference, permanent=True)

    return remove


def _mark_replied(mailbox: Mailbox, parsed: ParsedMessage) -> None:
    notes = _notes(parsed)
    source = notes.get("x-chrono-mail-source", "")
    if notes.get("x-chrono-mail-mark-replied") != "1" or "/" not in source:
        return
    folder, _, message_id = source.rpartition("/")
    try:
        reference = mailbox.reference(mailbox.folder(folder), _check_id(message_id))
        mailbox.set_flags(reference, frozenset({*mailbox.current_flags(reference), MessageFlag.REPLIED}))
    except ChronoMailError:
        pass


def mail_unschedule(engine: Engine, parameters: MessageParameters) -> object:
    """Take a scheduled message back: it becomes a draft again."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder("Outbox"), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        if _due_time(parsed) is None:
            raise UsageError("That message is not scheduled")
    return draft_save(
        engine,
        DraftSaveParameters(
            account=parameters.account, to=tuple(_shown(parsed.to_addresses)), cc=tuple(_shown(parsed.cc_addresses)), bcc=tuple(_shown(parsed.bcc_addresses)),
            subject=parsed.subject, body=parsed.text_body or "", html=parsed.html_body, keep_attachments=tuple(str(i) for i in range(len(parsed.attachments))),
            in_reply_to=parsed.in_reply_to, references=parsed.references, replace_folder="Outbox", replace_message=reference.store_id,
        ),
    )


def _shown(addresses: tuple[Address, ...]) -> list[str]:
    return [f"{a.name} <{a.address}>" if a.name else a.address for a in addresses]


# --- snooze ------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnoozeParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    until: str = field(metadata=option("When it comes back, a local time like 2026-10-11T09:00"))


@dataclass(frozen=True, slots=True)
class SnoozeResult:
    message_id: str
    until: str


def message_snooze(engine: Engine, parameters: SnoozeParameters) -> SnoozeResult:
    """Put a message away until a time: it waits in the Snoozed folder and then comes back, unread, to where it was."""
    until = parse_when(parameters.until, engine.clock())
    with open_mailbox(engine, parameters.account) as mailbox:
        source = mailbox.folder(parameters.folder)
        reference = mailbox.reference(source, _check_id(parameters.message))
        target = (SNOOZED_FOLDER,)
        if source == target:
            raise UsageError("The message is snoozed already")
        if target not in {folder.path for folder in mailbox.folders()}:
            mailbox.store.create_folder(target)
        path = engine.directories.account_directory(mailbox.account_id) / SNOOZED_FILE
        entries = load_snoozed(path)
        entries[reference.store_id] = {"until": until, "folder": "/".join(source)}
        save_snoozed(path, entries)  # the record first: a message in Snoozed without a record would never come back
        mailbox.move(reference, target)
        return SnoozeResult(reference.store_id, describe_when(until))


@dataclass(frozen=True, slots=True)
class SnoozedInfo:
    account: str
    message_id: str
    until: str
    seconds: int
    comes_back_to: str


def message_snoozed(engine: Engine, parameters: AccountOptional) -> tuple[SnoozedInfo, ...]:
    found: list[SnoozedInfo] = []
    for account in _accounts(engine, parameters.account):
        for store_id, entry in load_snoozed(engine.directories.account_directory(account) / SNOOZED_FILE).items():
            found.append(SnoozedInfo(account, store_id, describe_when(int(str(entry["until"]))), int(str(entry["until"])), str(entry["folder"])))
    return tuple(sorted(found, key=lambda item: item.seconds))


@dataclass(frozen=True, slots=True)
class WakeParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))
    message: str | None = field(default=None, metadata=option("Bring this snoozed message back now"))


@dataclass(frozen=True, slots=True)
class WakeResult:
    woken: int
    waiting: int


def message_wake(engine: Engine, parameters: WakeParameters) -> WakeResult:
    """Bring back the snoozed messages whose time has come (or one of them now). They return unread."""
    now, woken, waiting = engine.clock(), 0, 0
    for account in _accounts(engine, parameters.account):
        path = engine.directories.account_directory(account) / SNOOZED_FILE
        entries = load_snoozed(path)
        if not entries:
            continue
        with open_mailbox(engine, account) as mailbox:
            here: dict[str, MessageRef] = {}
            if (SNOOZED_FOLDER,) in {f.path for f in mailbox.folders()}:
                here = {message.ref.store_id: message.ref for message in mailbox.store.list_messages((SNOOZED_FOLDER,))}
            for store_id, entry in list(entries.items()):
                if parameters.message and parameters.message != store_id:
                    waiting += 1
                    continue
                if not parameters.message and int(str(entry["until"])) > now:
                    waiting += 1
                    continue
                reference = here.get(store_id)
                if reference is not None:
                    try:
                        target = parse_folder(str(entry["folder"]))
                        if target not in {f.path for f in mailbox.folders()}:
                            mailbox.store.create_folder(target)
                        moved = mailbox.move(reference, target)
                        mailbox.set_flags(moved, frozenset(mailbox.current_flags(moved) - {MessageFlag.SEEN}))
                    except ChronoMailError:
                        continue  # try again next time; the record stays
                    woken += 1
                del entries[store_id]  # also when the message is gone: nothing is left to wait for
            save_snoozed(path, entries)
    return WakeResult(woken, waiting)


def register(registry: CommandRegistry) -> None:
    registry.register("mail.scheduled", "List the messages waiting to be sent at a time", AccountOptional, mail_scheduled)
    registry.register("mail.send-due", "Send the scheduled messages whose time has come", SendDueParameters, mail_send_due)
    registry.register("mail.unschedule", "Take a scheduled message back as a draft", MessageParameters, mail_unschedule)
    registry.register("message.snooze", "Put a message away until a time", SnoozeParameters, message_snooze)
    registry.register("message.snoozed", "List the snoozed messages", AccountOptional, message_snoozed)
    registry.register("message.wake", "Bring back snoozed messages whose time has come", WakeParameters, message_wake)
