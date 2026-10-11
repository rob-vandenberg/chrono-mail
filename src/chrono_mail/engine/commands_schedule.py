import threading
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.commands_mail import MessageParameters, _check_id
from chrono_mail.engine.mailbox import Mailbox, open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.recurring import MAXIMUM_ATTEMPTS, RECURRING_FILE, Repeat, advance, describe_repeat, index_after, load_repeats, next_due, save_repeats
from chrono_mail.engine.schedule import describe_when, load_snoozed, parse_when, save_snoozed
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.mime import Address, OutgoingMessage, ParsedMessage, parse_message
from chrono_mail.sync import send_and_file

# --- Version ------------------------------------------------------------
__version__ = "commands_schedule.py 0.0.72"

# --- Version history ----------------------------------------------------
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.67: Messages that are sent again and again
# v0.0.45: accounts that share Local Folders: the sender is found by the From address
# v0.0.43: Certificate exceptions
# v0.0.34: Initial send later and snooze

SEND_AT: str = "x-chrono-mail-send-at"
SNOOZED_FOLDER: str = "Snoozed"
SNOOZED_FILE: str = "snoozed.json"
KIND: str = "x-chrono-mail-kind"
_REPEAT_LOCK = threading.Lock()  # two checks at the same moment must not send the same message twice


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
    repeat: str = ""  # "every 5 days, until 2027-01-01" for a message that is sent again and again
    paused: bool = False
    times_sent: int = 0
    last_error: str = ""


def _accounts(engine: Engine, wanted: str | None) -> list[str]:
    config = engine.config()
    if wanted:
        return [engine.resolve_account(wanted, config).account.account_id]
    return [item.account.account_id for item in config.accounts]


def _places(engine: Engine, wanted: str | None) -> list[list[AccountSettings]]:
    """The places where mail is kept (accounts that share Local Folders count as one), each with the accounts that can send from it."""
    config = engine.config()
    groups = engine.storage_groups(config)
    if wanted:
        place = engine.storage_settings(engine.resolve_account(wanted, config), config).account.account_id
        return [groups[place]]
    return list(groups.values())


def _sender(members: list[AccountSettings], parsed: ParsedMessage) -> AccountSettings | None:
    """The account a scheduled message is sent from: the one whose address is in its From, else the first that can send."""
    senders = [item for item in members if item.account.kind is not AccountKind.LOCAL]
    own = {address.address.lower() for address in parsed.from_addresses}
    return next((item for item in senders if item.account.email_address.lower() in own), senders[0] if senders else None)


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


def _patterns(mailbox: Mailbox) -> dict[str, tuple[MessageRef, ParsedMessage]]:
    """The messages in the Outbox that are sent again and again (the pattern; the schedule is in the file next to the store)."""
    found: dict[str, tuple[MessageRef, ParsedMessage]] = {}
    for message in mailbox.store.list_messages(mailbox.folder("Outbox")):
        try:
            parsed = parse_message(mailbox.store.read(message.ref, hint=mailbox.hint(message.ref.store_id)))
        except ChronoMailError:
            continue
        if _notes(parsed).get(KIND) == "recurring":
            found[message.ref.store_id] = (message.ref, parsed)
    return found


def _repeat_path(engine: Engine, mailbox: Mailbox) -> Path:
    return engine.directories.account_directory(mailbox.account_id) / RECURRING_FILE


def mail_scheduled(engine: Engine, parameters: AccountOptional) -> tuple[ScheduledInfo, ...]:
    """The messages waiting to be sent at a time, soonest first (a message that repeats shows its next send)."""
    found: list[ScheduledInfo] = []
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            for reference, parsed, due in _scheduled(mailbox):
                sender = _sender(members, parsed) or members[0]
                found.append(ScheduledInfo(sender.account.account_id, reference.store_id, describe_when(due), due, parsed.subject, tuple(a.address for a in parsed.to_addresses)))
            repeats = load_repeats(_repeat_path(engine, mailbox))
            for store_id, (reference, parsed) in _patterns(mailbox).items():
                repeat = repeats.get(store_id)
                moment = next_due(repeat) if repeat is not None else None
                if repeat is None or moment is None:
                    continue
                sender = _sender(members, parsed) or members[0]
                found.append(
                    ScheduledInfo(
                        sender.account.account_id, store_id, describe_when(moment), moment, parsed.subject, tuple(a.address for a in parsed.to_addresses),
                        describe_repeat(repeat), repeat.paused, repeat.sent, repeat.last_error,
                    )
                )
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


def _outgoing(sender: AccountSettings, parsed: ParsedMessage) -> OutgoingMessage:
    return OutgoingMessage(
        from_address=Address(sender.account.display_name, sender.account.email_address),
        to_addresses=parsed.to_addresses, cc_addresses=parsed.cc_addresses, bcc_addresses=parsed.bcc_addresses, subject=parsed.subject,
        text_body=parsed.text_body or "", html_body=parsed.html_body, attachments=parsed.attachments,
        in_reply_to=parsed.in_reply_to, references=parsed.references,
    )


def mail_send_due(engine: Engine, parameters: SendDueParameters) -> SendDueResult:
    """Send the scheduled messages whose time has come. The scheduled copy is removed once a verified copy is in the Outbox
    and before the server is asked, so a crash can leave a message unsent in the Outbox but never sends it twice."""
    now, sent, waiting = engine.clock(), 0, 0
    failed: list[str] = []
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            for reference, parsed, due in _scheduled(mailbox):
                if (parameters.message and parameters.message != reference.store_id) or (not parameters.message and due > now):
                    waiting += 1
                    continue
                sender = _sender(members, parsed)
                password = engine.passwords.get(sender.account.account_id, ServerRole.OUTGOING) if sender is not None else None
                if sender is None or password is None:
                    failed.append(f"{parsed.subject}: no password is stored for the account it is sent from")
                    continue
                try:
                    outcome = send_and_file(
                        mailbox.store, mailbox.index, sender.account.outgoing, password, _outgoing(sender, parsed), OUTBOX,
                        mailbox.role_folder(FolderRole.SENT), before_send=_remover(mailbox, reference), ssl_context=engine.tls_context(sender, outgoing=True),
                    )
                except ChronoMailError as error:
                    failed.append(f"{parsed.subject}: {error}")
                    continue
                if outcome.sent:
                    sent += 1
                    _mark_replied(mailbox, parsed)
                else:
                    failed.append(f"{parsed.subject}: {outcome.error} (it stays in the Outbox and is not sent again by itself)")
            more_sent, more_waiting = _send_repeats(engine, members, mailbox, parameters.message, now, failed)
            sent, waiting = sent + more_sent, waiting + more_waiting
    return SendDueResult(sent, tuple(failed), waiting)


def _send_repeats(engine: Engine, members: list[AccountSettings], mailbox: Mailbox, only: str | None, now: float, failed: list[str]) -> tuple[int, int]:
    """Send the messages that repeat and are due. The schedule moves on before the server is asked, so a crash can lose one send but never makes
    two; a send that failed for certain is tried again at the next check, up to three times."""
    path = _repeat_path(engine, mailbox)
    sent = waiting = 0
    with _REPEAT_LOCK:
        repeats = load_repeats(path)
        patterns = _patterns(mailbox)
        if not repeats:
            return 0, 0
        for store_id in [key for key in repeats if key not in patterns]:
            del repeats[store_id]  # the pattern is gone (deleted by hand): nothing to send
        for store_id, repeat in list(repeats.items()):
            reference, parsed = patterns[store_id]
            forced = only == store_id
            if only and not forced:
                waiting += 1
                continue
            due = next_due(repeat)
            if due is None and not forced:  # the series is over: the pattern has done its work (the sent copies are in the Sent folder)
                mailbox.delete(reference, permanent=True)
                del repeats[store_id]
                continue
            if not forced and (repeat.paused or due is None or due > now):
                waiting += 1
                continue
            sender = _sender(members, parsed)
            password = engine.passwords.get(sender.account.account_id, ServerRole.OUTGOING) if sender is not None else None
            if sender is None or password is None:
                failed.append(f"{parsed.subject}: no password is stored for the account it is sent from")
                continue
            moved = advance(repeat, now) if not forced else repeat
            if not forced:
                repeats[store_id] = moved
                save_repeats(path, repeats)
            error = ""
            try:
                outcome = send_and_file(
                    mailbox.store, mailbox.index, sender.account.outgoing, password, _outgoing(sender, parsed), OUTBOX, mailbox.role_folder(FolderRole.SENT),
                    ssl_context=engine.tls_context(sender, outgoing=True),
                )
                if not outcome.sent:
                    error = outcome.error or "the server refused the message"
            except ChronoMailError as problem:
                error = str(problem)
            if not error:
                sent += 1
                repeats[store_id] = replace(moved, sent=moved.sent + 1, last_sent=int(now), last_error="")
            else:
                failed.append(f"{parsed.subject}: {error}")
                if forced:
                    repeats[store_id] = replace(repeat, last_error=error)
                elif repeat.attempts + 1 < MAXIMUM_ATTEMPTS:
                    repeats[store_id] = replace(repeat, attempts=repeat.attempts + 1, last_error=error)  # the same send is tried again
                else:
                    repeats[store_id] = replace(moved, last_error=error)  # given up: on to the next send
            save_repeats(path, repeats)
        save_repeats(path, repeats)
    return sent, waiting


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
        recurring = _notes(parsed).get(KIND) == "recurring"
        if _due_time(parsed) is None and not recurring:
            raise UsageError("That message is not scheduled")
        if recurring:  # the schedule ends here; the draft is the pattern again
            with _REPEAT_LOCK:
                path = _repeat_path(engine, mailbox)
                repeats = load_repeats(path)
                repeats.pop(reference.store_id, None)
                save_repeats(path, repeats)
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


# --- messages that repeat ------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepeatState:
    message_id: str
    paused: bool
    next_send: str
    repeat: str


def _change_repeat(engine: Engine, parameters: MessageParameters, change: Callable[[Repeat], Repeat]) -> RepeatState:
    with open_mailbox(engine, parameters.account) as mailbox, _REPEAT_LOCK:
        path = _repeat_path(engine, mailbox)
        repeats = load_repeats(path)
        key = _check_id(parameters.message)
        if key not in repeats or key not in _patterns(mailbox):
            raise UsageError("That message is not sent again and again")
        repeats[key] = change(repeats[key])
        save_repeats(path, repeats)
        moment = next_due(repeats[key])
        return RepeatState(key, repeats[key].paused, describe_when(moment) if moment is not None else "never", describe_repeat(repeats[key]))


def repeat_pause(engine: Engine, parameters: MessageParameters) -> RepeatState:
    """Stop sending a repeating message until it is resumed."""
    return _change_repeat(engine, parameters, lambda repeat: replace(repeat, paused=True))


def repeat_resume(engine: Engine, parameters: MessageParameters) -> RepeatState:
    """Go on sending a repeating message; the sends that were missed meanwhile are skipped."""
    now = engine.clock()
    return _change_repeat(engine, parameters, lambda repeat: replace(repeat, paused=False, index=index_after(repeat, now), attempts=0))


@dataclass(frozen=True, slots=True)
class RepeatChange(MessageParameters):
    start: str | None = field(default=None, metadata=option("A new time of the first send, like 2026-10-11T09:00"))
    until: str | None = field(default=None, metadata=option("A new end, like 2027-01-01T09:00; 'never' removes the end"))
    months: int | None = field(default=None, metadata=option("A new number of months in the interval"))
    weeks: int | None = field(default=None, metadata=option("A new number of weeks in the interval"))
    days: int | None = field(default=None, metadata=option("A new number of days in the interval"))


def repeat_change(engine: Engine, parameters: RepeatChange) -> RepeatState:
    """Change the start, the end or the interval of a repeating message. The count starts again from the start; sends in the past are skipped."""
    now = engine.clock()

    def change(repeat: Repeat) -> Repeat:
        start = parse_when(parameters.start, now) if parameters.start else repeat.start
        end = repeat.end
        if parameters.until:
            end = None if parameters.until.strip().lower() == "never" else parse_when(parameters.until, now)
        months = repeat.months if parameters.months is None else parameters.months
        weeks = repeat.weeks if parameters.weeks is None else parameters.weeks
        days = repeat.days if parameters.days is None else parameters.days
        fresh = Repeat(start, end, months, weeks, days, 0, repeat.sent, repeat.paused, 0, repeat.last_error, repeat.last_sent)
        return replace(fresh, index=index_after(fresh, now))

    return _change_repeat(engine, parameters, change)


def repeat_cancel(engine: Engine, parameters: MessageParameters) -> RepeatState:
    """End a repeating message: the pattern is removed from the Outbox. What was sent stays in the Sent folder."""
    state = _change_repeat(engine, parameters, lambda repeat: replace(repeat, paused=True))
    with open_mailbox(engine, parameters.account) as mailbox, _REPEAT_LOCK:
        path = _repeat_path(engine, mailbox)
        repeats = load_repeats(path)
        repeats.pop(state.message_id, None)
        save_repeats(path, repeats)  # the schedule goes first: a pattern without a schedule is never sent
        mailbox.delete(mailbox.reference(mailbox.folder("Outbox"), state.message_id), permanent=True)
    return state


def register(registry: CommandRegistry) -> None:
    registry.register("mail.repeat-pause", "Stop sending a repeating message until it is resumed", MessageParameters, repeat_pause)
    registry.register("mail.repeat-resume", "Go on sending a repeating message", MessageParameters, repeat_resume)
    registry.register("mail.repeat-change", "Change the start, end or interval of a repeating message", RepeatChange, repeat_change)
    registry.register("mail.repeat-cancel", "End a repeating message and remove its pattern", MessageParameters, repeat_cancel, destructive=True)
    registry.register("mail.scheduled", "List the messages waiting to be sent at a time", AccountOptional, mail_scheduled)
    registry.register("mail.send-due", "Send the scheduled messages whose time has come", SendDueParameters, mail_send_due)
    registry.register("mail.unschedule", "Take a scheduled message back as a draft", MessageParameters, mail_unschedule)
    registry.register("message.snooze", "Put a message away until a time", SnoozeParameters, message_snooze)
    registry.register("message.snoozed", "List the snoozed messages", AccountOptional, message_snoozed)
    registry.register("message.wake", "Bring back snoozed messages whose time has come", WakeParameters, message_wake)
