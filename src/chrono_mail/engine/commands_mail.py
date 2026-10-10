import base64
import mimetypes
import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from email.utils import parseaddr
from pathlib import Path
from typing import Literal

from chrono_mail.config import ServerRole
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError, FolderRole, MessageFlag
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.commands_rules import apply_to_new
from chrono_mail.engine.schedule import describe_when, parse_when
from chrono_mail.engine.compose import ComposeKind, build_draft, suggest_filename
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import IndexedMessage, SearchFilter, SortField
from chrono_mail.mime import Address, Attachment, OutgoingMessage, ParsedMessage, build_message, parse_message
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.sync import ImapState, ImapSyncOptions, Pop3State, fetch_pop3, send_and_file, sync_imap

# --- Version ------------------------------------------------------------
__version__ = "commands_mail.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Settings window with pages for everything
# v0.0.35: Send later and snooze
# v0.0.33: Rules
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.16: a sent message can replace a draft
# v0.0.13: previews, inline images, attachment save, compose, reply marks
# v0.0.12: Initial message commands

MAXIMUM_ATTACHMENT_BYTES: int = 100 * 1024 * 1024
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024
MAXIMUM_INLINE_IMAGE_BYTES: int = 5 * 1024 * 1024
MAXIMUM_INLINE_IMAGES_TOTAL_BYTES: int = 20 * 1024 * 1024
_FLAG_NAMES: dict[MessageFlag, str] = {
    MessageFlag.SEEN: "seen",
    MessageFlag.REPLIED: "replied",
    MessageFlag.FLAGGED: "flagged",
    MessageFlag.TRASHED: "trashed",
    MessageFlag.DRAFT: "draft",
}


def _flag_names(flags: frozenset[MessageFlag]) -> tuple[str, ...]:
    return tuple(sorted(_FLAG_NAMES[flag] for flag in flags))


@dataclass(frozen=True, slots=True)
class MessageRow:
    message_id: str
    folder: str
    subject: str
    sender: str
    date: datetime | None
    size: int
    flags: tuple[str, ...]
    attachments: bool
    preview: str
    recipients: str
    thread_size: int = 1  # messages in the conversation, in all folders


def _rows(mailbox: Mailbox, messages: Iterable[IndexedMessage]) -> tuple[MessageRow, ...]:
    """Message rows with the size of their conversations."""
    found = list(messages)
    sizes = mailbox.index.thread_sizes([message.thread_key for message in found])
    return tuple(
        MessageRow(
            message.store_id, "/".join(message.folder), message.subject, message.sender, message.date, message.size, _flag_names(message.flags),
            message.has_attachments, message.preview, message.recipients, sizes.get(message.thread_key, 1),
        )
        for message in found
    )


# --- fetching and sending -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchResult:
    downloaded: int
    already_known: int
    failed: tuple[str, ...]
    deleted_on_server: int
    deletion_blocked: str | None
    index_problems: int
    aborted: str | None
    rule_actions: int = 0  # what the rules did to the new messages


def mail_fetch(engine: Engine, parameters: AccountParameters) -> FetchResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        settings = mailbox.settings
        password = engine.passwords.get(settings.account.account_id, ServerRole.INCOMING)
        if password is None:
            raise ConfigurationError("No password is stored for this account")
        if settings.account.kind is AccountKind.IMAP:
            return _imap_fetch(engine, mailbox, password)
        state = Pop3State(engine.directories.pop3_state_file(mailbox.account_id), durable=engine.durable)
        inbox = mailbox.role_folder(FolderRole.INBOX)
        with Pop3Client(settings.account.incoming, password, Pop3Limits(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES)) as client:
            report = fetch_pop3(
                client, mailbox.store, mailbox.index, state, inbox, settings.pop3, MAXIMUM_MESSAGE_BYTES, engine.clock,
                progress=lambda done, total: engine.progress("Downloading", done, total),
            )
        ruled = apply_to_new(engine, mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])
    return FetchResult(
        report.downloaded,
        report.already_known,
        (*(f"{item.unique_id}: {item.reason}" for item in report.failed), *ruled.failed),
        report.deleted_on_server,
        report.deletion_blocked,
        report.index_problems,
        report.aborted,
        ruled.actions,
    )


def _imap_fetch(engine: Engine, mailbox: Mailbox, password: str) -> FetchResult:
    """mail.fetch for an IMAP account: the two-way sync of store and server."""
    state = ImapState(engine.directories.imap_state_file(mailbox.account_id), durable=engine.durable)
    with ImapClient(mailbox.settings.account.incoming, password) as client:
        report = sync_imap(
            client, mailbox.store, mailbox.index, state, ImapSyncOptions(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES),
            progress=lambda folder, done, total: engine.progress(f"Downloading {folder}", done, total),
        )
    inbox = mailbox.role_folder(FolderRole.INBOX)
    ruled = apply_to_new(engine, mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])
    remarks = (*report.failed, *report.notes, *(f"{item.folder}: {item.note}" for item in report.folders if item.note), *ruled.failed)
    return FetchResult(report.downloaded, 0, tuple(remarks), 0, None, 0, report.aborted, ruled.actions)


def _addresses(values: tuple[str, ...]) -> tuple[Address, ...]:
    found: list[Address] = []
    for value in values:
        name, address = parseaddr(value)
        if address.count("@") != 1 or address.startswith("@") or address.endswith("@") or any(character.isspace() or character in '<>",;' for character in address):
            raise UsageError(f"'{value}' is not an e-mail address")
        found.append(Address(name, address))
    return tuple(found)


def _attachment(path_text: str) -> Attachment:
    path = Path(path_text)
    try:
        if path.stat().st_size > MAXIMUM_ATTACHMENT_BYTES:
            raise UsageError(f"{path.name} is larger than the attachment limit")
        data = path.read_bytes()
    except OSError as error:
        raise UsageError(f"{path_text} cannot be read: {error.strerror}") from None
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Attachment(path.name, content_type, None, False, data)


@dataclass(frozen=True, slots=True)
class SendParameters:
    account: str = field(metadata=option("The account to send from"))
    to: tuple[str, ...] = field(default=(), metadata=option("A recipient (repeat for more)"))
    subject: str = field(default="", metadata=option("The subject"))
    body: str = field(default="", metadata=option("The text of the message"))
    cc: tuple[str, ...] = field(default=(), metadata=option("A copy recipient (repeat for more)"))
    bcc: tuple[str, ...] = field(default=(), metadata=option("A blind copy recipient (repeat for more)"))
    html: str | None = field(default=None, metadata=option("An HTML version of the body"))
    attach: tuple[str, ...] = field(default=(), metadata=option("A file to attach (repeat for more)"))
    in_reply_to: str | None = field(default=None, metadata=option("The Message-ID this is a reply to"))
    references: tuple[str, ...] = field(default=(), metadata=option("A Message-ID of the conversation (repeat for more)"))
    source_folder: str | None = field(default=None, metadata=option("The folder of the message this answers or forwards"))
    source_message: str | None = field(default=None, metadata=option("The id of the message this answers or forwards"))
    include_source_attachments: bool = field(default=False, metadata=option("Forward: take the attachments of the source message along"))
    mark_replied: bool = field(default=False, metadata=option("Reply: mark the source message as replied to once sent"))
    keep_folder: str | None = field(default=None, metadata=option("The folder of a stored message (a draft) whose attachments are to be sent"))
    keep_message: str | None = field(default=None, metadata=option("The id of that stored message"))
    keep_attachments: tuple[str, ...] = field(default=(), metadata=option("The position of an attachment of that stored message to send (repeat for more)"))
    replaces_folder: str | None = field(default=None, metadata=option("The folder of a draft (or Outbox message) that this send replaces"))
    replaces_message: str | None = field(default=None, metadata=option("The id of a draft (or Outbox message) that is removed once this message was sent"))
    send_at: str | None = field(default=None, metadata=option("Send later: the local time, like 2026-10-11T09:00; the message waits in the Outbox"))


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: bool
    message_id: str
    folder: str
    refused: tuple[str, ...]
    error: str | None
    warning: str | None
    scheduled: str | None = None  # when it will be sent, if it was not sent now


def kept_attachments(raw: bytes | None, positions: tuple[str, ...]) -> tuple[Attachment, ...]:
    """The attachments at these positions (as listed by message.show) of a stored message."""
    if not positions:
        return ()
    if raw is None:
        raise UsageError("Keeping attachments needs the stored message they are in")
    parsed = parse_message(raw)
    kept: list[Attachment] = []
    for text in positions:
        if not text.isdigit() or int(text) >= len(parsed.attachments):
            raise UsageError("The stored message has no such attachment")
        attachment = parsed.attachments[int(text)]
        if not attachment.inline:
            kept.append(attachment)
    return tuple(kept)


def _source_attachments(mailbox: Mailbox, parameters: "SendParameters") -> tuple[Attachment, ...]:
    """The attachments of the message being forwarded and those kept from a stored draft, when asked for."""
    kept: tuple[Attachment, ...] = ()
    if parameters.keep_attachments:
        if not parameters.keep_folder or not parameters.keep_message:
            raise UsageError("Keeping attachments needs the stored message they are in")
        if any(not position.isdigit() for position in parameters.keep_attachments):
            raise UsageError("An attachment position is a whole number, starting at 0")
        stored = mailbox.reference(mailbox.folder(parameters.keep_folder), _check_id(parameters.keep_message))
        kept = kept_attachments(mailbox.store.read(stored, hint=mailbox.hint(stored.store_id)), parameters.keep_attachments)
    if not parameters.include_source_attachments:
        return kept
    if not parameters.source_folder or not parameters.source_message:
        raise UsageError("Taking attachments along needs the source message")
    source = mailbox.reference(mailbox.folder(parameters.source_folder), _check_id(parameters.source_message))
    parsed = parse_message(mailbox.store.read(source, hint=mailbox.hint(source.store_id)))
    return (*kept, *(attachment for attachment in parsed.attachments if not attachment.inline))


def _schedule(engine: Engine, mailbox: Mailbox, outgoing: OutgoingMessage, parameters: SendParameters) -> SendResult:
    """Keep the message in the Outbox, marked with the time it is to be sent (mail.send-due sends it then)."""
    when = parse_when(str(parameters.send_at), engine.clock())
    notes: list[tuple[str, str]] = [("X-Chrono-Mail-Kind", "scheduled"), ("X-Chrono-Mail-Send-At", str(when))]
    if parameters.source_folder and parameters.source_message:
        notes.append(("X-Chrono-Mail-Source", f"{parameters.source_folder}/{_check_id(parameters.source_message)}"))
        if parameters.mark_replied:
            notes.append(("X-Chrono-Mail-Mark-Replied", "1"))
    scheduled = replace(outgoing, extra_headers=tuple(notes))
    reference = mailbox.deliver(mailbox.folder("Outbox"), build_message(scheduled, draft=True), frozenset({MessageFlag.SEEN}))
    if parameters.replaces_folder and parameters.replaces_message:
        try:
            mailbox.delete(mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message)), permanent=True)
        except ChronoMailError:
            pass  # the scheduled message holds the content now
    return SendResult(False, reference.store_id, "Outbox", (), None, None, describe_when(when))


def mail_send(engine: Engine, parameters: SendParameters) -> SendResult:
    if not (parameters.to or parameters.cc or parameters.bcc):
        raise UsageError("There is no recipient")
    with open_mailbox(engine, parameters.account) as mailbox:
        settings = mailbox.settings
        password = engine.passwords.get(settings.account.account_id, ServerRole.OUTGOING)
        if password is None:
            raise ConfigurationError("No password is stored for this account")
        outgoing = OutgoingMessage(
            from_address=Address(settings.account.display_name, settings.account.email_address),
            to_addresses=_addresses(parameters.to),
            cc_addresses=_addresses(parameters.cc),
            bcc_addresses=_addresses(parameters.bcc),
            subject=parameters.subject,
            text_body=parameters.body,
            html_body=parameters.html,
            attachments=(*(_attachment(path) for path in parameters.attach), *_source_attachments(mailbox, parameters)),
            in_reply_to=parameters.in_reply_to,
            references=parameters.references or ((parameters.in_reply_to,) if parameters.in_reply_to else ()),
        )
        if parameters.send_at:
            return _schedule(engine, mailbox, outgoing, parameters)
        outcome = send_and_file(mailbox.store, mailbox.index, settings.account.outgoing, password, outgoing, OUTBOX, mailbox.role_folder(FolderRole.SENT))
        if outcome.sent and parameters.mark_replied and parameters.source_folder and parameters.source_message:
            try:
                source = mailbox.reference(mailbox.folder(parameters.source_folder), _check_id(parameters.source_message))
                mailbox.set_flags(source, frozenset({*mailbox.current_flags(source), MessageFlag.REPLIED}))
            except ChronoMailError:
                pass  # the reply was sent; failing to mark the original is not worth an error
        if outcome.sent and parameters.replaces_folder and parameters.replaces_message:
            try:
                old = mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message))
                mailbox.delete(old, permanent=True)  # the draft has become the sent message
            except ChronoMailError:
                pass
        return SendResult(
            outcome.sent,
            outcome.reference.store_id,
            "/".join(outcome.reference.folder_path),
            tuple(f"{item.address}: {item.code} {item.text}" for item in outcome.refused),
            outcome.error,
            outcome.warning,
        )


# --- reading ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(default="Inbox", metadata=option("The folder, for example Inbox or Archive/2026"))
    sort: Literal["date", "subject", "sender", "size"] = field(default="date", metadata=option("The sort order"))
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many messages to skip"))
    limit: int = field(default=50, metadata=option("How many messages to show"))
    unread_only: bool = field(default=False, metadata=option("Only unread messages"))


@dataclass(frozen=True, slots=True)
class ListResult:
    folder: str
    total: int
    unread: int
    messages: tuple[MessageRow, ...]


def message_list(engine: Engine, parameters: ListParameters) -> ListResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        rows = mailbox.index.page(folder, SortField(parameters.sort), not parameters.oldest_first, parameters.offset, parameters.limit, parameters.unread_only)
        return ListResult("/".join(folder), mailbox.index.count(folder), mailbox.index.count(folder, unread_only=True), _rows(mailbox, rows))


@dataclass(frozen=True, slots=True)
class MessageParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id (as shown by message.list)"))


@dataclass(frozen=True, slots=True)
class AttachmentInfo:
    filename: str | None
    content_type: str
    size: int
    inline: bool
    suggested_name: str


@dataclass(frozen=True, slots=True)
class InlineImage:
    content_id: str  # without the angle brackets, as referenced by cid: in the HTML
    content_type: str
    data_base64: str


@dataclass(frozen=True, slots=True)
class ShowResult:
    message_id: str
    folder: str
    flags: tuple[str, ...]
    subject: str
    sender: tuple[str, ...]
    to: tuple[str, ...]
    cc: tuple[str, ...]
    date: datetime | None
    header_message_id: str | None
    text_body: str | None
    html_body: str | None
    attachments: tuple[AttachmentInfo, ...]
    problems: tuple[str, ...]
    inline_images: tuple[InlineImage, ...]
    reply_to: tuple[str, ...]


def _check_id(message_id: str) -> str:
    if len(message_id) != 32 or any(character not in "0123456789abcdef" for character in message_id):
        raise UsageError("A message id is 32 lower-case hexadecimal characters")
    return message_id


def _shown(addresses: tuple[Address, ...]) -> tuple[str, ...]:
    return tuple(f"{a.name} <{a.address}>" if a.name else a.address for a in addresses)


def _inline_images(parsed: ParsedMessage) -> tuple[InlineImage, ...]:
    """Images the HTML refers to with cid:, small enough to be embedded in the page as data."""
    found: list[InlineImage] = []
    total = 0
    for attachment in parsed.attachments:
        if not attachment.content_id or not attachment.content_type.startswith("image/") or len(attachment.data) > MAXIMUM_INLINE_IMAGE_BYTES:
            continue
        total += len(attachment.data)
        if total > MAXIMUM_INLINE_IMAGES_TOTAL_BYTES:
            break
        found.append(InlineImage(attachment.content_id.strip("<>"), attachment.content_type, base64.b64encode(attachment.data).decode("ascii")))
    return tuple(found)


def message_show(engine: Engine, parameters: MessageParameters) -> ShowResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        reference = mailbox.reference(folder, _check_id(parameters.message))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        parsed = parse_message(raw)
        return ShowResult(
            reference.store_id,
            "/".join(folder),
            _flag_names(mailbox.current_flags(reference)),
            parsed.subject,
            _shown(parsed.from_addresses),
            _shown(parsed.to_addresses),
            _shown(parsed.cc_addresses),
            parsed.date,
            parsed.message_id,
            parsed.text_body,
            parsed.html_body,
            tuple(AttachmentInfo(a.filename, a.content_type, len(a.data), a.inline, suggest_filename(a.filename, a.content_type, position)) for position, a in enumerate(parsed.attachments)),
            parsed.defects,
            _inline_images(parsed),
            _shown(parsed.reply_to_addresses),
        )


@dataclass(frozen=True, slots=True)
class SearchParameters:
    account: str = field(metadata=option("The account"))
    query: str = field(default="", metadata=option("Words to find; from:, to:, subject: and body: limit a word to one field"))
    folder: str | None = field(default=None, metadata=option("Only in this folder"))
    unread: bool | None = field(default=None, metadata=option("Only unread (true) or only read (false) messages"))
    flagged: bool | None = field(default=None, metadata=option("Only flagged (true) or only unflagged (false) messages"))
    has_attachments: bool | None = field(default=None, metadata=option("Only messages with (true) or without (false) attachments"))
    date_from: str | None = field(default=None, metadata=option("Only messages from this day on (YYYY-MM-DD, your local time)"))
    date_to: str | None = field(default=None, metadata=option("Only messages up to and including this day (YYYY-MM-DD)"))
    minimum_size: int | None = field(default=None, metadata=option("Only messages of at least this many bytes"))
    maximum_size: int | None = field(default=None, metadata=option("Only messages of at most this many bytes"))
    conversations: bool = field(default=False, metadata=option("Show only the newest message of each conversation (not together with words to find)"))
    offset: int = field(default=0, metadata=option("How many results to skip"))
    limit: int = field(default=50, metadata=option("How many results to show"))


def _day(text: str | None, name: str, end: bool) -> datetime | None:
    """The start (or the last moment) of a day given as YYYY-MM-DD, in the local time zone."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise UsageError(f"{name} must be a date like 2026-05-31") from None
    moment = datetime.combine(day, time.max if end else time.min)
    return moment.astimezone()


def message_search(engine: Engine, parameters: SearchParameters) -> tuple[MessageRow, ...]:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        filters = SearchFilter(
            unread=parameters.unread, flagged=parameters.flagged, has_attachments=parameters.has_attachments,
            date_from=_day(parameters.date_from, "date_from", end=False), date_to=_day(parameters.date_to, "date_to", end=True),
            minimum_size=parameters.minimum_size, maximum_size=parameters.maximum_size, newest_per_thread=parameters.conversations,
        )
        return _rows(mailbox, mailbox.index.search(parameters.query, folder, filters, parameters.offset, parameters.limit))


def message_thread(engine: Engine, parameters: MessageParameters) -> tuple[MessageRow, ...]:
    """The conversation a message belongs to, oldest first, with the messages of all folders."""
    with open_mailbox(engine, parameters.account) as mailbox:
        indexed = mailbox.index.get(_check_id(parameters.message))
        if indexed is None:
            raise UsageError("Message not found")
        members = mailbox.index.thread_members(indexed.thread_key)
        return _rows(mailbox, members)


# --- changing ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlagParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    seen: bool | None = field(default=None, metadata=option("Mark read (true) or unread (false)"))
    flagged: bool | None = field(default=None, metadata=option("Flag (true) or unflag (false)"))
    replied: bool | None = field(default=None, metadata=option("Mark as replied to (true) or not (false)"))


@dataclass(frozen=True, slots=True)
class FlagResult:
    message_id: str
    flags: tuple[str, ...]


def message_flag(engine: Engine, parameters: FlagParameters) -> FlagResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        flags = set(mailbox.current_flags(reference))
        for flag, wanted in ((MessageFlag.SEEN, parameters.seen), (MessageFlag.FLAGGED, parameters.flagged), (MessageFlag.REPLIED, parameters.replied)):
            if wanted is True:
                flags.add(flag)
            elif wanted is False:
                flags.discard(flag)
        return FlagResult(reference.store_id, _flag_names(mailbox.set_flags(reference, frozenset(flags))))


@dataclass(frozen=True, slots=True)
class TransferParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    target: str = field(metadata=option("The folder to move or copy it to"))


@dataclass(frozen=True, slots=True)
class TransferResult:
    message_id: str
    folder: str


def message_move(engine: Engine, parameters: TransferParameters) -> TransferResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        moved = mailbox.move(reference, mailbox.folder(parameters.target))
        return TransferResult(moved.store_id, "/".join(moved.folder_path))


def message_copy(engine: Engine, parameters: TransferParameters) -> TransferResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        copied = mailbox.copy(reference, mailbox.folder(parameters.target))
        return TransferResult(copied.store_id, "/".join(copied.folder_path))


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    permanent: bool = field(default=False, metadata=option("Remove for good instead of moving to Trash"))


@dataclass(frozen=True, slots=True)
class DeleteResult:
    message_id: str
    outcome: str


def message_delete(engine: Engine, parameters: DeleteParameters) -> DeleteResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        return DeleteResult(reference.store_id, mailbox.delete(reference, parameters.permanent))


@dataclass(frozen=True, slots=True)
class AttachmentSaveParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    index: int = field(metadata=option("Which attachment: its position in the list shown by message.show, starting at 0"))
    path: str = field(metadata=option("Where to save it"))
    overwrite: bool = field(default=False, metadata=option("Replace an existing file"))


@dataclass(frozen=True, slots=True)
class AttachmentSaveResult:
    path: str
    size: int
    content_type: str


def message_attachment_save(engine: Engine, parameters: AttachmentSaveParameters) -> AttachmentSaveResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        if not 0 <= parameters.index < len(parsed.attachments):
            raise UsageError("The message has no such attachment")
        attachment = parsed.attachments[parameters.index]
        target = Path(parameters.path)
        if target.exists() and not parameters.overwrite:
            raise UsageError("The file exists; use overwrite to replace it")
        temporary = target.with_name(f".{target.name}.{os.urandom(4).hex()}.part")
        try:
            temporary.write_bytes(attachment.data)
            temporary.replace(target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise UsageError(f"The file cannot be written: {error.strerror}") from None
        return AttachmentSaveResult(str(target), len(attachment.data), attachment.content_type)


@dataclass(frozen=True, slots=True)
class ComposeParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message to reply to or forward"))
    kind: ComposeKind = field(default="reply", metadata=option("reply, reply_all or forward"))
    quote: Literal["above", "below", "none"] = field(default="above", metadata=option("Replies: my text above the quote, below it, or no quote at all"))


@dataclass(frozen=True, slots=True)
class ComposeResult:
    kind: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    in_reply_to: str | None
    references: tuple[str, ...]
    attachment_names: tuple[str, ...]


def message_compose(engine: Engine, parameters: ComposeParameters) -> ComposeResult:
    """The starting point of a reply, a reply to all, or a forward (nothing is stored or sent)."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        draft = build_draft(parsed, parameters.kind, mailbox.settings.account.email_address, parameters.quote)
        return ComposeResult(draft.kind, draft.to, draft.cc, draft.subject, draft.body, draft.in_reply_to, draft.references, draft.attachment_names)


def register(registry: CommandRegistry) -> None:
    registry.register("mail.fetch", "Download new messages from the POP3 server", AccountParameters, mail_fetch)
    registry.register("mail.send", "Send a message (a copy stays in Outbox until it is sent, then moves to Sent)", SendParameters, mail_send)
    registry.register("message.list", "List the messages of a folder", ListParameters, message_list)
    registry.register("message.thread", "List the messages of the conversation a message belongs to", MessageParameters, message_thread)
    registry.register("message.show", "Show one message", MessageParameters, message_show)
    registry.register("message.search", "Search messages", SearchParameters, message_search)
    registry.register("message.attachment.save", "Save one attachment of a message to a file", AttachmentSaveParameters, message_attachment_save)
    registry.register("message.compose", "Prepare a reply, a reply to all, or a forward of a message", ComposeParameters, message_compose)
    registry.register("message.flag", "Change the read, flagged or replied marks of a message", FlagParameters, message_flag)
    registry.register("message.move", "Move a message to another folder", TransferParameters, message_move)
    registry.register("message.copy", "Copy a message to another folder", TransferParameters, message_copy)
    registry.register("message.delete", "Move a message to Trash, or delete it for good", DeleteParameters, message_delete, destructive=True)
