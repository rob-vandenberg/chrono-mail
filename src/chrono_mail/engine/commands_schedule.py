import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.engine.commands_drafts import DraftSaveParameters, DraftSaveResult, draft_save
from chrono_mail.engine.commands_mail import MessageParameters, SendParameters, _check_id, outgoing_message
from chrono_mail.engine.mailbox import Mailbox, open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.recurring import MAXIMUM_ATTEMPTS, Repeat, advance, describe_repeat, index_after, next_due, repeat_from_text, repeat_text
from chrono_mail.engine.schedule import SCHEDULED_FOLDER, SNOOZED_FOLDER, SNOOZED_LOCK, describe_when, parse_when
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.mime import REPEAT_HEADER, SEND_AT_HEADER, Address, OutgoingMessage, ParsedMessage, build_message, parse_message, send_time, with_extra_headers
from chrono_mail.store import MessageNotFoundError
from chrono_mail.sync import send_and_file

# --- Version ------------------------------------------------------------
__version__ = "commands_schedule.py 1.9.270"

# --- Version history ----------------------------------------------------
# v1.9.270: R-19: typed mail.unschedule (its answer is the draft it became)
# v1.9.268: R-21: Send now, Pause and Resume answer with the message's line as it is now (None: it no longer waits), so the Scheduled page
#           changes only that line; mail.scheduled, mail.send-due, mail.repeat-pause, mail.repeat-resume, message.snoozed and message.wake typed
# v1.1.247: snooze_one: one message's step, also for many at once (R-08)
# v1.1.208: mail.unschedule writes the draft from the account in the message's From, also when Local Folders is named (a drop on Drafts) (D-178)
# v1.1.207: mail.edit-scheduled (typed): a scheduled message is changed in place under its own id, its time and repeat kept or changed, a repeat's count of sends kept; ScheduledInfo carries the interval, start and end
# v1.1.206: A repeating message carries its timetable in itself, so it is listed and sent wherever its mail is kept (it was never sent from an account that keeps its mail in Local Folders); snooze times come from the messages' properties files, per mailbox (D-176)
# v1.1.203: mail.unschedule is described as moving a scheduled message back to drafts
# v1.1.200: Scheduled and repeating messages wait in the Scheduled folder (made when first used); those still in the Outbox move there once,
#           at the first check (the same store id, one atomic rename each)
# v1.1.196: Scheduled messages are found in the index (only those due are opened), repeating ones by their own id; a message that cannot be
#           read is told once per run and never sent; a repeat schedule is only dropped when its message is truly gone (R-01)
# v0.5.158: a scheduled or repeating message that was sent is reported where it is stored (D-146 step 4)
# v0.5.123: The Snoozed folder and the folder a message wakes up in are made on the server first in an IMAP account (D-118)
# v0.4.120: The Sent copy of a scheduled or repeating message goes to the server at once (IMAP)
# v0.2.101: Snooze and wake change the record one at a time and make folders safely (no second Snoozed folder); waking a message that has no record (Cancel snooze) brings it to the Inbox
# v0.2.100: The name of the Snoozed folder and its record file live in schedule.py (message.list needs them too)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.67: Messages that are sent again and again
# v0.0.45: accounts that share Local Folders: the sender is found by the From address
# v0.0.43: Certificate exceptions
# v0.0.34: Initial send later and snooze

SEND_AT: str = SEND_AT_HEADER
KIND: str = "x-chrono-mail-kind"
_REPEAT_LOCK = threading.Lock()  # two checks at the same moment must not send the same message twice


def _notes(parsed: ParsedMessage) -> dict[str, str]:
    return dict(parsed.chrono_headers)


def _due_time(parsed: ParsedMessage) -> int | None:
    return send_time(parsed)


def _repeat_of(parsed: ParsedMessage) -> Repeat | None:
    """The timetable a repeating message carries in itself, or None for any other message (or a damaged timetable)."""
    notes = _notes(parsed)
    if notes.get(KIND) != "recurring" or REPEAT_HEADER not in notes:
        return None
    return repeat_from_text(notes[REPEAT_HEADER])


def _look_at(repeat: Repeat, now: float) -> int:
    """The send time a repeating message carries (the index copies it): its next send; when the series is over, now, so the
    next check removes its pattern."""
    moment = next_due(repeat)
    return moment if moment is not None else int(now)


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
    months: int = 0  # a repeating message: its interval, its first send and its end (the time window shows them when it is edited)
    weeks: int = 0
    days: int = 0
    start: int = 0
    end: int | None = None


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


SCHEDULED: tuple[str, ...] = (SCHEDULED_FOLDER,)


def _in_outbox(folder: tuple[str, ...]) -> bool:
    return tuple(part.lower() for part in folder) == tuple(part.lower() for part in OUTBOX)  # standard names in any letter case


def _unreadable(engine: Engine, mailbox: Mailbox, store_id: str, error: ChronoMailError, failed: list[str]) -> None:
    """A scheduled message (or one that may be) cannot be read: it is not sent and stays where it is; said once per run."""
    if engine.first_time(f"unreadable scheduled message {mailbox.account_id} {store_id}"):
        failed.append(f"a scheduled message cannot be read ({error}); it is not sent and stays where it is.")


def _into_scheduled(engine: Engine, mailbox: Mailbox, failed: list[str]) -> None:
    """Before 1.1.200 scheduled and repeating messages waited in the Outbox: they move to the Scheduled folder, once, each
    in one atomic rename that keeps its store id (so the schedule file still names it). A message that could not be read
    when it was indexed is not moved (it may be a send that failed); it is told once. A repeating message carries its next
    send like any other since 1.1.206."""
    waiting: list[MessageRef] = []
    for store_id, folder, moment in mailbox.index.scheduled():
        if _in_outbox(folder) and moment > 0:
            waiting.append(mailbox.reference(folder, store_id))
        elif _in_outbox(folder):
            _unreadable(engine, mailbox, store_id, ChronoMailError("it could not be read when it was indexed"), failed)
    if not waiting:
        return
    target, _ = mailbox.ensure_folder(SCHEDULED)
    for reference in waiting:
        try:
            mailbox.move(reference, target.path)
        except ChronoMailError as error:  # stays in the Outbox and is tried again at the next check; never lost
            failed.append(f"a scheduled message could not be moved to the Scheduled folder: {error}")


def _scheduled(mailbox: Mailbox) -> list[tuple[MessageRef, int]]:
    """The messages in the Scheduled folder that have a send time, soonest first, from the index: no message is opened
    here. A message that could not be read when it was indexed has time 0, so it is looked at (and reported) at once."""
    return [(mailbox.reference(folder, store_id), moment) for store_id, folder, moment in mailbox.index.scheduled() if folder == SCHEDULED]


def _read(mailbox: Mailbox, reference: MessageRef) -> ParsedMessage:
    return parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))


def _waiting_place(mailbox: Mailbox, store_id: str) -> MessageRef:
    """Where a scheduled or repeating message is: the Scheduled folder, or the Outbox before it moved there. Asked of the
    store itself, never of the index (the index may be behind; a schedule must never end because of it). Raises
    MessageNotFoundError when it is in neither."""
    known = {folder.path for folder in mailbox.store.list_folders()}
    for folder in (SCHEDULED, OUTBOX):
        if folder not in known:
            continue  # Scheduled is made when first used
        reference = mailbox.reference(folder, store_id)
        try:
            mailbox.store.locate(reference)  # any other error of the store goes on: it never counts as gone
            return reference
        except MessageNotFoundError:
            continue
    raise MessageNotFoundError("Message not found")


def _record(mailbox: Mailbox, reference: MessageRef, repeat: Repeat, now: float) -> None:
    """Write a repeating message's timetable and its next send into the message itself (one safe replace under the same id)."""
    raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
    changed = with_extra_headers(raw, {"X-Chrono-Mail-Repeat": repeat_text(repeat), "X-Chrono-Mail-Send-At": str(_look_at(repeat, now))})
    mailbox.replace_waiting(reference, changed)


def _info(members: list[AccountSettings], reference: MessageRef, parsed: ParsedMessage) -> ScheduledInfo | None:
    """What a list shows of a waiting message, or None when there is nothing to show (not scheduled, or a series that is over)."""
    sender = _sender(members, parsed) or members[0]
    to = tuple(a.address for a in parsed.to_addresses)
    repeat = _repeat_of(parsed)
    if repeat is not None:
        moment = next_due(repeat)
        if moment is None:
            return None  # the next check removes its pattern
        return ScheduledInfo(
            sender.account.account_id, reference.store_id, describe_when(moment), moment, parsed.subject, to, describe_repeat(repeat), repeat.paused, repeat.sent,
            repeat.last_error, repeat.months, repeat.weeks, repeat.days, repeat.start, repeat.end,
        )
    due = _due_time(parsed)
    return None if due is None else ScheduledInfo(sender.account.account_id, reference.store_id, describe_when(due), due, parsed.subject, to)


def mail_scheduled(engine: Engine, parameters: AccountOptional) -> tuple[ScheduledInfo, ...]:
    """The messages waiting to be sent at a time, soonest first (a message that repeats shows its next send)."""
    found: list[ScheduledInfo] = []
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            _into_scheduled(engine, mailbox, [])  # told by the minute check, not by a list
            for reference, _ in _scheduled(mailbox):
                try:
                    parsed = _read(mailbox, reference)
                except ChronoMailError:
                    continue  # the minute check tells it (once); there is nothing here to show of it
                info = _info(members, reference, parsed)
                if info is not None:
                    found.append(info)
    return tuple(sorted(found, key=lambda item: item.seconds))


MAIL_SCHEDULED: TypedCommand[AccountOptional, tuple[ScheduledInfo, ...]] = TypedCommand("mail.scheduled", AccountOptional, tuple)  # every element is a ScheduledInfo


def _line(members: list[AccountSettings], mailbox: Mailbox, store_id: str) -> ScheduledInfo | None:
    """What a list shows of one waiting message as it is now, or None when it no longer waits: sent, moved, a series that is
    over, or not readable (mail.scheduled leaves such a message out too)."""
    try:
        reference = _waiting_place(mailbox, store_id)
        return _info(members, reference, _read(mailbox, reference))
    except ChronoMailError:
        return None


@dataclass(frozen=True, slots=True)
class ScheduledEdit(SendParameters):
    message: str = field(default="", metadata=option("The id of the scheduled message to change; it keeps its id"))
    keep_time: bool = field(default=False, metadata=option("Keep its time and repeat; without it, send_at (and repeat_*) give the new ones"))


def mail_edit_scheduled(engine: Engine, parameters: ScheduledEdit) -> ScheduledInfo:
    """Change a message that waits in the Scheduled folder: its content, and its time and repeat unless they are kept. It stays
    scheduled the whole time and keeps its id; a repeating message keeps how often it was sent. The message is replaced in one
    safe step (never a draft in between)."""
    if not (parameters.to or parameters.cc or parameters.bcc):
        raise UsageError("There is no recipient")
    store_id = _check_id(parameters.message)
    now = engine.clock()
    (members,) = _places(engine, parameters.account)
    with open_mailbox(engine, parameters.account) as mailbox, _REPEAT_LOCK:
        reference = mailbox.reference(SCHEDULED, store_id)
        try:
            parsed = _read(mailbox, reference)
        except MessageNotFoundError:
            raise UsageError("That message no longer waits in the Scheduled folder (it was sent or moved meanwhile)") from None
        old_repeat, old_due = _repeat_of(parsed), _due_time(parsed)
        if old_repeat is None and old_due is None:
            raise UsageError("That message is not scheduled")
        written = replace(parameters, keep_folder="/".join(SCHEDULED), keep_message=store_id) if parameters.keep_attachments else parameters
        outgoing = outgoing_message(mailbox, written)
        repeat: Repeat | None
        if parameters.keep_time:
            repeat, when = old_repeat, old_due
        else:
            if not parameters.send_at:
                raise UsageError("Give the new time (send_at), or keep the time (keep_time)")
            when = parse_when(parameters.send_at, now)
            interval = (parameters.repeat_months, parameters.repeat_weeks, parameters.repeat_days)
            if parameters.repeat_until and not any(interval):
                raise UsageError("An end time needs an interval (repeat_days, repeat_weeks or repeat_months)")
            repeat = None
            if any(interval):  # counted from the new first send; how often it was sent and whether it is paused stay
                end = parse_when(parameters.repeat_until, now) if parameters.repeat_until else None
                sent, paused, last_sent = (old_repeat.sent, old_repeat.paused, old_repeat.last_sent) if old_repeat is not None else (0, False, 0)
                repeat = Repeat(when, end, *interval, sent=sent, paused=paused, last_sent=last_sent)
        notes: list[tuple[str, str]]
        if repeat is not None:
            notes = [("X-Chrono-Mail-Kind", "recurring"), ("X-Chrono-Mail-Send-At", str(_look_at(repeat, now))), ("X-Chrono-Mail-Repeat", repeat_text(repeat))]
        else:
            notes = [("X-Chrono-Mail-Kind", "scheduled"), ("X-Chrono-Mail-Send-At", str(when))]
        old = _notes(parsed)
        notes += [(name, old[name.lower()]) for name in ("X-Chrono-Mail-Source", "X-Chrono-Mail-Mark-Replied") if name.lower() in old]  # a reply stays a reply
        raw = build_message(replace(outgoing, extra_headers=tuple(notes)), draft=True)
        mailbox.replace_waiting(reference, raw)
        info = _info(members, reference, parse_message(raw))
    if info is None:
        raise UsageError("That repeating message has no send left; give it a new time or end")
    return info


MAIL_EDIT_SCHEDULED: TypedCommand[ScheduledEdit, ScheduledInfo] = TypedCommand("mail.edit-scheduled", ScheduledEdit, ScheduledInfo)


@dataclass(frozen=True, slots=True)
class SendDueParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))
    message: str | None = field(default=None, metadata=option("Send this scheduled message now, whatever its time"))


@dataclass(frozen=True, slots=True)
class SendDueResult:
    sent: int
    failed: tuple[str, ...]
    waiting: int
    line: ScheduledInfo | None = None  # a message named: its line as it is now (a repeating one waits for its next send); None: it no longer waits


def _outgoing(sender: AccountSettings, parsed: ParsedMessage) -> OutgoingMessage:
    return OutgoingMessage(
        from_address=Address(sender.account.display_name, sender.account.email_address),
        to_addresses=parsed.to_addresses, cc_addresses=parsed.cc_addresses, bcc_addresses=parsed.bcc_addresses, subject=parsed.subject,
        text_body=parsed.text_body or "", html_body=parsed.html_body, attachments=parsed.attachments,
        in_reply_to=parsed.in_reply_to, references=parsed.references,
    )


def mail_send_due(engine: Engine, parameters: SendDueParameters) -> SendDueResult:
    """Send the scheduled messages whose time has come (they wait in the Scheduled folder). The scheduled copy is removed once
    a verified copy is in the Outbox and before the server is asked, so a crash can leave a message unsent in the Outbox but
    never sends it twice. A repeating message is found the same way: it carries its next send in itself."""
    now, sent, waiting = engine.clock(), 0, 0
    failed: list[str] = []
    line: ScheduledInfo | None = None
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            _into_scheduled(engine, mailbox, failed)
            named = False
            for reference, listed in _scheduled(mailbox):
                if (parameters.message and parameters.message != reference.store_id) or (not parameters.message and listed > now):
                    waiting += 1  # not opened: its time (from the index) has not come
                    continue
                named = named or parameters.message == reference.store_id
                try:
                    parsed = _read(mailbox, reference)
                except ChronoMailError as error:
                    _unreadable(engine, mailbox, reference.store_id, error, failed)
                    continue
                if _notes(parsed).get(KIND) == "recurring":
                    more_sent, more_waiting = _send_repeat(engine, members, mailbox, reference, parameters.message is not None, now, failed)
                    sent, waiting = sent + more_sent, waiting + more_waiting
                    continue
                due = _due_time(parsed)  # the message itself is the truth
                if due is None:
                    continue  # not scheduled
                if not parameters.message and due > now:
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
                mailbox.report_added(outcome.reference)  # stored past the mailbox's operations: in Sent, or in the Outbox when it failed
                if outcome.sent:
                    sent += 1
                    mailbox.put_on_server(outcome.reference)  # IMAP: the Sent copy is in Sent on the server at once
                    _mark_replied(mailbox, parsed)
                else:
                    failed.append(f"{parsed.subject}: {outcome.error} (it stays in the Outbox and is not sent again by itself)")
            if named and parameters.message:
                line = _line(members, mailbox, parameters.message)
    return SendDueResult(sent, tuple(failed), waiting, line)


MAIL_SEND_DUE: TypedCommand[SendDueParameters, SendDueResult] = TypedCommand("mail.send-due", SendDueParameters, SendDueResult)


def _send_repeat(engine: Engine, members: list[AccountSettings], mailbox: Mailbox, reference: MessageRef, forced: bool, now: float, failed: list[str]) -> tuple[int, int]:
    """Send a repeating message whose time has come (or now, when forced), as (sent, waiting). The pattern stays in the Scheduled
    folder and records in itself how far the series is: the timetable moves on before the server is asked, so a crash can lose
    one send but never makes two; a send that failed for certain is tried again at the next check, up to three times."""
    with _REPEAT_LOCK:
        try:
            parsed = _read(mailbox, reference)  # again, under the lock: another check may have moved it on meanwhile
        except ChronoMailError as error:
            _unreadable(engine, mailbox, reference.store_id, error, failed)  # it stays: a read that failed never ends a series
            return 0, 0
        repeat = _repeat_of(parsed)
        if repeat is None:
            _unreadable(engine, mailbox, reference.store_id, ChronoMailError("its timetable is damaged"), failed)
            return 0, 0
        due = next_due(repeat)
        if due is None and not forced:  # the series is over: the pattern has done its work (the sent copies are in the Sent folder)
            mailbox.delete(reference, permanent=True)
            return 0, 0
        if not forced and (repeat.paused or due is None or due > now):
            return 0, 1
        sender = _sender(members, parsed)
        password = engine.passwords.get(sender.account.account_id, ServerRole.OUTGOING) if sender is not None else None
        if sender is None or password is None:
            failed.append(f"{parsed.subject}: no password is stored for the account it is sent from")
            return 0, 0
        moved = advance(repeat, now) if not forced else repeat
        if not forced:
            try:
                _record(mailbox, reference, moved, now)
            except ChronoMailError as problem:  # without its progress written first a crash could send it twice: not sent now
                failed.append(f"{parsed.subject}: it is not sent now, because its progress could not be written: {problem}")
                return 0, 0
        error = ""
        try:
            outcome = send_and_file(
                mailbox.store, mailbox.index, sender.account.outgoing, password, _outgoing(sender, parsed), OUTBOX, mailbox.role_folder(FolderRole.SENT),
                ssl_context=engine.tls_context(sender, outgoing=True),
            )
            mailbox.report_added(outcome.reference)  # stored past the mailbox's operations: in Sent, or in the Outbox when it failed
            if not outcome.sent:
                error = outcome.error or "the server refused the message"
            else:
                mailbox.put_on_server(outcome.reference)  # IMAP: the Sent copy is in Sent on the server at once
        except ChronoMailError as problem:
            error = str(problem)
        if not error:
            after = replace(moved, sent=moved.sent + 1, last_sent=int(now), last_error="")
        else:
            failed.append(f"{parsed.subject}: {error}")
            if forced:
                after = replace(repeat, last_error=error)
            elif repeat.attempts + 1 < MAXIMUM_ATTEMPTS:
                after = replace(repeat, attempts=repeat.attempts + 1, last_error=error)  # the same send is tried again
            else:
                after = replace(moved, last_error=error)  # given up: on to the next send
        try:
            _record(mailbox, reference, after, now)
        except ChronoMailError as problem:  # the timetable already moved on before the send: nothing is sent twice
            failed.append(f"{parsed.subject}: the count of its sends could not be written: {problem}")
        return (0, 0) if error else (1, 0)


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


def mail_unschedule(engine: Engine, parameters: MessageParameters) -> DraftSaveResult:
    """Take a scheduled message back: it becomes a draft again, written from the account in its From (the place where the mail
    is kept may be Local Folders, which sends nothing itself: a drop on Drafts names it)."""
    (members,) = _places(engine, parameters.account)
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = _waiting_place(mailbox, _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        if _due_time(parsed) is None and _notes(parsed).get(KIND) != "recurring":
            raise UsageError("That message is not scheduled")  # a repeating message's timetable is in itself: it ends with it
    sender = _sender(members, parsed)
    return draft_save(
        engine,
        DraftSaveParameters(
            account=sender.account.account_id if sender is not None else parameters.account, to=tuple(_shown(parsed.to_addresses)), cc=tuple(_shown(parsed.cc_addresses)), bcc=tuple(_shown(parsed.bcc_addresses)),
            subject=parsed.subject, body=parsed.text_body or "", html=parsed.html_body, keep_attachments=tuple(str(i) for i in range(len(parsed.attachments))),
            in_reply_to=parsed.in_reply_to, references=parsed.references, replace_folder="/".join(reference.folder_path), replace_message=reference.store_id,
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
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        mailbox.ensure_folder((SNOOZED_FOLDER,))
        return snooze_one(mailbox, reference, until)


def snooze_one(mailbox: Mailbox, reference: MessageRef, until: int) -> SnoozeResult:
    """One message into the Snoozed folder (which exists), its record written first (also for many messages at once, R-08)."""
    target = (SNOOZED_FOLDER,)
    if reference.folder_path == target:
        raise UsageError("The message is snoozed already")
    origin = "/".join(reference.folder_path)
    with SNOOZED_LOCK:  # the record first: a message in Snoozed without a record would never come back
        mailbox.store.properties.change(reference.store_id, lambda current: replace(current, snoozed_until=until, snoozed_from=origin))
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
    """The snoozed messages, soonest back first: the messages in each Snoozed folder, with what their properties files say."""
    found: list[SnoozedInfo] = []
    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            for store_id, until in mailbox.snoozed().items():
                origin = mailbox.store.properties.of(store_id).snoozed_from or "Inbox"
                found.append(SnoozedInfo(mailbox.store.account_id, store_id, describe_when(until), until, origin))
    return tuple(sorted(found, key=lambda item: item.seconds))


MESSAGE_SNOOZED: TypedCommand[AccountOptional, tuple[SnoozedInfo, ...]] = TypedCommand("message.snoozed", AccountOptional, tuple)  # every element is a SnoozedInfo


@dataclass(frozen=True, slots=True)
class WakeParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))
    message: str | None = field(default=None, metadata=option("Bring this snoozed message back now"))


@dataclass(frozen=True, slots=True)
class WakeResult:
    woken: int
    waiting: int


def message_wake(engine: Engine, parameters: WakeParameters) -> WakeResult:
    """Bring back the snoozed messages whose time has come (or one of them now). They return unread. A message named
    here that is in Snoozed without a record (its folder of origin is unknown) goes to the Inbox."""
    now, woken, waiting = engine.clock(), 0, 0
    with SNOOZED_LOCK:
        for members in _places(engine, parameters.account):
            found = _wake_place(engine, members[0].account.account_id, parameters.message, now)
            woken, waiting = woken + found[0], waiting + found[1]
    return WakeResult(woken, waiting)


MESSAGE_WAKE: TypedCommand[WakeParameters, WakeResult] = TypedCommand("message.wake", WakeParameters, WakeResult)


def _wake_place(engine: Engine, account: str, wanted: str | None, now: float) -> tuple[int, int]:
    woken, waiting = 0, 0
    with open_mailbox(engine, account) as mailbox:
        try:
            here = {message.ref.store_id: message.ref for message in mailbox.store.list_messages((SNOOZED_FOLDER,))}
        except StorageError:
            return 0, 0  # there is no Snoozed folder
        records = mailbox.store.properties.readable(here)
        for store_id, reference in here.items():
            record = records.get(store_id)
            until = record.snoozed_until if record is not None else None
            if wanted and wanted != store_id:
                waiting += 1 if until is not None else 0
                continue
            if until is None:
                if wanted == store_id:
                    woken += _back_to_inbox(mailbox, reference)  # Cancel snooze of a message whose record is lost
                continue
            if not wanted and until > now:
                waiting += 1
                continue
            try:
                target = parse_folder(record.snoozed_from or "Inbox") if record is not None else ("Inbox",)
                mailbox.ensure_folder(target)
                moved = mailbox.move(reference, target)
                mailbox.set_flags(moved, frozenset(mailbox.current_flags(moved) - {MessageFlag.SEEN}))
            except ChronoMailError:
                continue  # try again next time; the record stays
            try:
                mailbox.store.properties.change(store_id, lambda current: replace(current, snoozed_until=None, snoozed_from=None))
            except StorageError:
                pass  # the message is back; a record outside the Snoozed folder is never read, and a new snooze writes it anew
            woken += 1
    return woken, waiting


def _back_to_inbox(mailbox: Mailbox, reference: MessageRef) -> int:
    """A message in Snoozed without a record of where it came from: to the Inbox, unread."""
    inbox = next((folder.path for folder in mailbox.folders() if folder.role is FolderRole.INBOX), ("Inbox",))
    mailbox.ensure_folder(inbox, FolderRole.INBOX)
    moved = mailbox.move(reference, inbox)
    mailbox.set_flags(moved, frozenset(mailbox.current_flags(moved) - {MessageFlag.SEEN}))
    return 1


# --- messages that repeat ------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepeatState:
    message_id: str
    paused: bool
    next_send: str
    repeat: str
    line: ScheduledInfo | None = None  # pause, resume, change: its line as it is now; None: it no longer waits


def _repeating(mailbox: Mailbox, store_id: str) -> tuple[MessageRef, Repeat]:
    """A repeating message and the timetable it carries; a usage error when it is not one (or is gone)."""
    try:
        reference = _waiting_place(mailbox, store_id)
    except MessageNotFoundError:
        raise UsageError("That message is not sent again and again") from None
    repeat = _repeat_of(_read(mailbox, reference))
    if repeat is None:
        raise UsageError("That message is not sent again and again")
    return reference, repeat


def _state(store_id: str, repeat: Repeat) -> RepeatState:
    moment = next_due(repeat)
    return RepeatState(store_id, repeat.paused, describe_when(moment) if moment is not None else "never", describe_repeat(repeat))


def _change_repeat(engine: Engine, parameters: MessageParameters, change: Callable[[Repeat], Repeat]) -> RepeatState:
    (members,) = _places(engine, parameters.account)
    with open_mailbox(engine, parameters.account) as mailbox, _REPEAT_LOCK:
        key = _check_id(parameters.message)
        reference, repeat = _repeating(mailbox, key)
        changed = change(repeat)
        _record(mailbox, reference, changed, engine.clock())  # the timetable is in the message itself
        return replace(_state(key, changed), line=_line(members, mailbox, key))


def repeat_pause(engine: Engine, parameters: MessageParameters) -> RepeatState:
    """Stop sending a repeating message until it is resumed."""
    return _change_repeat(engine, parameters, lambda repeat: replace(repeat, paused=True))


def repeat_resume(engine: Engine, parameters: MessageParameters) -> RepeatState:
    """Go on sending a repeating message; the sends that were missed meanwhile are skipped."""
    now = engine.clock()
    return _change_repeat(engine, parameters, lambda repeat: replace(repeat, paused=False, index=index_after(repeat, now), attempts=0))


MAIL_REPEAT_PAUSE: TypedCommand[MessageParameters, RepeatState] = TypedCommand("mail.repeat-pause", MessageParameters, RepeatState)
MAIL_REPEAT_RESUME: TypedCommand[MessageParameters, RepeatState] = TypedCommand("mail.repeat-resume", MessageParameters, RepeatState)


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
    """End a repeating message: the pattern is removed from the Scheduled folder, and its timetable with it. What was sent stays in the Sent folder."""
    with open_mailbox(engine, parameters.account) as mailbox, _REPEAT_LOCK:
        key = _check_id(parameters.message)
        reference, repeat = _repeating(mailbox, key)
        mailbox.delete(reference, permanent=True)
        return _state(key, replace(repeat, paused=True))


MAIL_UNSCHEDULE: TypedCommand[MessageParameters, DraftSaveResult] = TypedCommand("mail.unschedule", MessageParameters, DraftSaveResult)


def register(registry: CommandRegistry) -> None:
    registry.register(MAIL_REPEAT_PAUSE.id, "Stop sending a repeating message until it is resumed", MessageParameters, repeat_pause)
    registry.register(MAIL_REPEAT_RESUME.id, "Go on sending a repeating message", MessageParameters, repeat_resume)
    registry.register("mail.repeat-change", "Change the start, end or interval of a repeating message", RepeatChange, repeat_change)
    registry.register("mail.repeat-cancel", "End a repeating message and remove its pattern", MessageParameters, repeat_cancel, destructive=True)
    registry.register(MAIL_SCHEDULED.id, "List the messages waiting to be sent at a time", AccountOptional, mail_scheduled)
    registry.register(MAIL_SEND_DUE.id, "Send the scheduled messages whose time has come", SendDueParameters, mail_send_due)
    registry.register(MAIL_EDIT_SCHEDULED.id, "Change a scheduled message: its content, and its time and repeat unless kept; it keeps its id", ScheduledEdit, mail_edit_scheduled)
    registry.register(MAIL_UNSCHEDULE.id, "Move a scheduled message back to drafts", MessageParameters, mail_unschedule)
    registry.register("message.snooze", "Put a message away until a time", SnoozeParameters, message_snooze)
    registry.register(MESSAGE_SNOOZED.id, "List the snoozed messages", AccountOptional, message_snoozed)
    registry.register(MESSAGE_WAKE.id, "Bring back snoozed messages whose time has come", WakeParameters, message_wake)
