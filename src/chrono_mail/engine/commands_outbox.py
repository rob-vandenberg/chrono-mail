from dataclasses import dataclass, field, replace

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.commands_drafts import DraftSaveParameters, draft_save
from chrono_mail.engine.commands_mail import _check_id
from chrono_mail.engine.commands_schedule import KIND, _due_time, _mark_replied, _notes, _places, _sender, _shown
from chrono_mail.engine.junkfilter import learn
from chrono_mail.engine.mailbox import SentAlready, Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, OUTBOX, talks_to_servers
from chrono_mail.mime import ParsedMessage, parse_message
from chrono_mail.store import MessageNotFoundError, Properties, StoredMessage
from chrono_mail.sync import file_sent_again, send_stored

# --- Version ------------------------------------------------------------
__version__ = "commands_outbox.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: a message the server took is known by any of its marks (the letter written even on a full disk, sent_at, this run's memory): never sent again
# v3.6.349: a message of the Outbox the server took whose move to Sent failed keeps its "sent" mark, so it is never sent again (RV-178);
#           such a message is never made a draft (SentAlready, RV-181), and is filed in Sent without a countdown, recipient or password
# v3.1.341: a message marked as sent (sent_at) is only moved to Sent, never sent again (RV-50); Cancel of a reply keeps its link to the
#           original (RV-121)
# v3.0.339: a restore waits until this command has ended and holds it back meanwhile (talks_to_servers, RV-60)
# v2.2.314: a message whose waiting time has not come is left in the Outbox; message: only that one, now (Send now, the end of a countdown);
#           one message at a time is checked and sent (never twice); a reply marks its original once sent; mail.cancel-send, mail.end-countdown (B-21)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.270: R-19: typed mail.send-outbox
# v0.5.158: a message Send All sent is reported as moved from the Outbox to Sent (D-146 step 4)
# v0.5.145: Send All reports its steps per sending account (engine.activity), only for accounts with mail waiting; failures name their account
# v0.4.120: The Sent copy goes to the server at once (IMAP)
# v0.0.86: Initial Send All: send what waits in the Outbox


@dataclass(frozen=True, slots=True)
class SendOutboxParameters:
    account: str | None = field(default=None, metadata=option("Only this account (default: all accounts)"))
    message: str | None = field(default=None, metadata=option("Only this message, now, also before its waiting time is over (Send now, the end of a countdown)"))
    now: bool = field(default=False, metadata=option("Also the messages whose waiting time is not over (Send now on Quit)"))


@dataclass(frozen=True, slots=True)
class SendOutboxResult:
    sent: int
    failed: tuple[str, ...]
    failed_accounts: tuple[str, ...] = ()  # per failure, the id of the account it was sent from ("" when not known)


def _recipients(parsed_to: tuple[str, ...]) -> tuple[str, ...]:
    """Each address once, in the order given."""
    seen: set[str] = set()
    unique: list[str] = []
    for address in parsed_to:
        if address.lower() not in seen:
            seen.add(address.lower())
            unique.append(address)
    return tuple(unique)


@talks_to_servers  # a restore waits for it and holds it back meanwhile (RV-60)
def mail_send_outbox(engine: Engine, parameters: SendOutboxParameters) -> SendOutboxResult:
    """Send the messages that wait in the Outbox (an earlier send failed, or a delayed send waits for it, B-21): as they are stored, to
    their To, Cc and Bcc, without the Bcc header. Scheduled and repeating messages wait for their time and are left alone, and so does a
    message whose waiting time has not come (unless it is the one named). A message is only moved to Sent after the server accepted it;
    one that fails stays in the Outbox and is tried again at the next send of the Outbox. One message at a time is checked and sent, so
    no message is ever sent twice."""
    sent = 0
    failed: list[str] = []
    failed_accounts: list[str] = []

    def fail(account_id: str, text: str) -> None:
        failed.append(text)
        failed_accounts.append(account_id)

    for members in _places(engine, parameters.account):
        with open_mailbox(engine, members[0].account.account_id) as mailbox:
            waiting: list[tuple[StoredMessage, str | None, bytes, ParsedMessage, tuple[str, ...], AccountSettings, str]] = []
            for message in mailbox.store.list_messages(mailbox.folder("Outbox")):
                if parameters.message is not None and message.ref.store_id != parameters.message:
                    continue
                hint = mailbox.hint(message.ref.store_id)
                sent_already = mailbox.sent_already(message.ref, hint)  # only its move to Sent is left (the review of RV-178)
                if not sent_already and parameters.message is None and not parameters.now and _held_back(mailbox, message.ref, engine.clock()):
                    continue  # its countdown runs: it may be cancelled still
                try:
                    raw = mailbox.store.read(message.ref, hint=hint)
                    parsed = parse_message(raw)
                except ChronoMailError as error:
                    fail("", f"a message in the Outbox cannot be read: {error}")
                    continue
                if _due_time(parsed) is not None or _notes(parsed).get(KIND) == "recurring":
                    continue
                recipients = _recipients(tuple(address.address for address in (*parsed.to_addresses, *parsed.cc_addresses, *parsed.bcc_addresses)))
                if sent_already:  # nothing goes to a server: no recipient or password is needed to file it in Sent
                    known = _sender(members, parsed) or members[0]
                    waiting.append((message, hint, raw, parsed, recipients, known, ""))
                    continue
                if not recipients:
                    fail("", f"{parsed.subject}: there is no recipient")
                    continue
                sender = _sender(members, parsed)
                password = engine.passwords.get(sender.account.account_id, ServerRole.OUTGOING) if sender is not None else None
                if sender is None or password is None:
                    fail(sender.account.account_id if sender is not None else "", f"{parsed.subject}: no password is stored for the account it is sent from")
                    continue
                waiting.append((message, hint, raw, parsed, recipients, sender, password))
            totals: dict[str, int] = {}
            for *_, sender, _ in waiting:
                totals[sender.account.account_id] = totals.get(sender.account.account_id, 0) + 1
            counted: dict[str, int] = {}
            for message, hint, raw, parsed, recipients, sender, password in waiting:
                account_id = sender.account.account_id
                counted[account_id] = counted.get(account_id, 0) + 1
                engine.activity(account_id, "send", "Sending", counted[account_id], totals[account_id])
                outcome = None
                replies_to: str | None = None
                sent_before = False
                with engine.outbox_lock:  # another send of the Outbox, or a Cancel, may have taken it meanwhile
                    sent_before = _still_waiting(mailbox, message.ref, hint) and mailbox.sent_already(message.ref, hint)
                    if _still_waiting(mailbox, message.ref, hint) and not (
                        parameters.message is None and not parameters.now and not sent_before and _held_back(mailbox, message.ref, engine.clock())
                    ):  # a message the server took is filed in Sent whatever its countdown said
                        replies_to = _properties(mailbox, message.ref).replies_to
                        try:
                            if sent_before:  # the server accepted it before and only its move to Sent failed: never sent again (RV-50)
                                outcome = file_sent_again(mailbox.store, mailbox.index, message.ref, hint, mailbox.role_folder(FolderRole.SENT))
                            else:
                                outcome = send_stored(
                                    mailbox.store, mailbox.index, sender.account.outgoing, password, message.ref, hint, raw, sender.account.email_address,
                                    recipients, mailbox.role_folder(FolderRole.SENT), ssl_context=engine.tls_context(sender, outgoing=True),
                                )
                        except ChronoMailError as error:
                            fail(account_id, f"{parsed.subject}: {error}")
                        if outcome is not None and outcome.sent and outcome.reference.folder_path == message.ref.folder_path:  # waits in the Outbox
                            engine.sent_unfiled.add(message.ref.store_id)  # marked as sent: never sent again in this run, also with no mark on the
                            # disk (full); noted before the lock goes, so nothing that waits for it can take it for unsent
                if outcome is not None and outcome.sent:
                    sent += 0 if sent_before else 1
                    if outcome.reference.folder_path != message.ref.folder_path:  # in Sent now; the marks it carried in the Outbox go (RV-178:
                        _forget_waiting(mailbox, outcome.reference)  # not before, or the next refresh sends it again)
                        engine.sent_unfiled.discard(message.ref.store_id)
                        mailbox.report_moved(message.ref, outcome.reference)
                        mailbox.put_on_server(outcome.reference)  # IMAP: the Sent copy is in Sent on the server at once
                    _mark_replied(mailbox, parsed)
                    _mark_answered(mailbox, replies_to)
                    if outcome.warning:
                        fail(account_id, f"{parsed.subject}: {outcome.warning}")
                elif outcome is not None:
                    fail(account_id, f"{parsed.subject}: {outcome.error} (it stays in the Outbox)")
                if counted[account_id] == totals[account_id]:
                    engine.activity(account_id, "send", "Disconnected", None, None)
    return SendOutboxResult(sent, tuple(failed), tuple(failed_accounts))


def _properties(mailbox: Mailbox, reference: MessageRef) -> Properties:
    try:
        return mailbox.store.properties.of(reference.store_id)
    except ChronoMailError:
        return Properties()  # a damaged properties file (kept aside): the message is sent as any other


def _held_back(mailbox: Mailbox, reference: MessageRef, now: float) -> bool:
    """A delayed send whose countdown still runs (B-21)."""
    moment = _properties(mailbox, reference).send_not_before
    return moment is not None and moment > now


def _still_waiting(mailbox: Mailbox, reference: MessageRef, hint: str | None) -> bool:
    try:
        mailbox.store.locate(reference, hint)
    except MessageNotFoundError:
        return False
    return True


def _forget_waiting(mailbox: Mailbox, reference: MessageRef) -> None:
    """A message sent: what it noted while it waited goes (it is in Sent now), also the letter of a send whose move failed before."""
    try:
        mailbox.store.properties.change(reference.store_id, lambda current: replace(current, send_not_before=None, replies_to=None, sent_at=None))
    except ChronoMailError:
        pass  # only read in the Outbox
    mailbox.forget_sent_letter(reference)


def _mark_answered(mailbox: Mailbox, source: str | None) -> None:
    """A reply that waited in the Outbox was sent: the message it answers is marked as replied (and as good mail, D-238)."""
    if not source or "/" not in source:
        return
    folder, _, message_id = source.rpartition("/")
    try:
        reference = mailbox.reference(mailbox.folder(folder), _check_id(message_id))
        mailbox.set_flags(reference, frozenset({*mailbox.current_flags(reference), MessageFlag.REPLIED}))
        learn(mailbox, [reference], junk=False)
    except ChronoMailError:
        pass  # the reply was sent; the original was moved or deleted meanwhile


@dataclass(frozen=True, slots=True)
class OutboxMessageParameters:
    account: str = field(metadata=option("The account whose Outbox holds the message"))
    message: str = field(metadata=option("The message id"))


@dataclass(frozen=True, slots=True)
class CancelSendResult:
    account: str  # the account it is written from
    folder: str  # where it is now (Drafts)
    message_id: str


def mail_cancel_send(engine: Engine, parameters: OutboxMessageParameters) -> CancelSendResult:
    """Cancel a delayed send (B-21): the message leaves the Outbox and becomes a draft again, written from the account in its From, to be
    opened in the message window. Refused when it was sent meanwhile."""
    (members,) = _places(engine, parameters.account)
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(OUTBOX, _check_id(parameters.message))
        with engine.outbox_lock:  # never while it is being sent; the mailbox before the lock, as every send takes them (the other order could
            # wait for an index rebuild that waits for a send: a standstill). draft_save takes it out of the Outbox in its own locked step.
            try:
                parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
            except MessageNotFoundError:
                raise UsageError("The message has been sent already") from None
            if mailbox.sent_already(reference):  # RV-181
                raise SentAlready("This message was sent already: it goes to Sent at the next Send and Receive")
        sender = _sender(members, parsed)
        account = sender.account.account_id if sender is not None else parameters.account
        answers = _properties(mailbox, reference).replies_to  # a reply stays a reply: its original is marked once it is really sent (RV-121)
        source_folder, _, source_message = answers.rpartition("/") if answers and "/" in answers else (None, "", None)
        saved = draft_save(
            engine,
            DraftSaveParameters(
                account=account, to=tuple(_shown(parsed.to_addresses)), cc=tuple(_shown(parsed.cc_addresses)), bcc=tuple(_shown(parsed.bcc_addresses)),
                subject=parsed.subject, body=parsed.text_body or "", html=parsed.html_body, keep_attachments=tuple(str(i) for i in range(len(parsed.attachments))),
                in_reply_to=parsed.in_reply_to, references=parsed.references, replace_folder="/".join(OUTBOX), replace_message=reference.store_id,
                kind="reply" if answers else "new", source_folder=source_folder or None, source_message=source_message or None,
            ),
        )
    return CancelSendResult(account, saved.folder, saved.message_id)


def mail_end_countdown(engine: Engine, parameters: OutboxMessageParameters) -> OutboxMessageParameters:
    """The countdown of a delayed send is over and it waits for the next send of the Outbox: its waiting time goes (B-21)."""
    with open_mailbox(engine, parameters.account) as mailbox, engine.outbox_lock:  # the mailbox before the lock, as every send takes them
        reference = mailbox.reference(OUTBOX, _check_id(parameters.message))
        if _still_waiting(mailbox, reference, mailbox.hint(reference.store_id)):
            mailbox.store.properties.change(reference.store_id, lambda current: replace(current, send_not_before=None))
            mailbox.tell_changed(reference)
    return parameters


MAIL_SEND_OUTBOX: TypedCommand[SendOutboxParameters, SendOutboxResult] = TypedCommand("mail.send-outbox", SendOutboxParameters, SendOutboxResult)
MAIL_CANCEL_SEND: TypedCommand[OutboxMessageParameters, CancelSendResult] = TypedCommand("mail.cancel-send", OutboxMessageParameters, CancelSendResult)
MAIL_END_COUNTDOWN: TypedCommand[OutboxMessageParameters, OutboxMessageParameters] = TypedCommand("mail.end-countdown", OutboxMessageParameters, OutboxMessageParameters)


def register(registry: CommandRegistry) -> None:
    registry.register(MAIL_SEND_OUTBOX.id, "Send the messages that wait in the Outbox (not the scheduled ones)", SendOutboxParameters, mail_send_outbox, doing="Sending the Outbox")
    registry.register(MAIL_CANCEL_SEND.id, "Cancel a delayed send: the message leaves the Outbox and becomes a draft again", OutboxMessageParameters, mail_cancel_send, doing="Cancelling a send")
    registry.register(MAIL_END_COUNTDOWN.id, "End the countdown of a delayed send: it waits for the next send of the Outbox", OutboxMessageParameters, mail_end_countdown, doing="Keeping a message in the Outbox")
