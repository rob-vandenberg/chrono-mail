import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Literal

from chrono_mail.config import ServerRole
from chrono_mail.core import ConfigurationError, FolderRole, MessageFlag
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import IndexedMessage, SearchFilter, SortField
from chrono_mail.mime import Address, Attachment, OutgoingMessage, parse_message
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.sync import Pop3State, fetch_pop3, send_and_file

# --- Version ------------------------------------------------------------
__version__ = "commands_mail.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial message commands

MAXIMUM_ATTACHMENT_BYTES: int = 100 * 1024 * 1024
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024
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


def _row(message: IndexedMessage) -> MessageRow:
    return MessageRow(message.store_id, "/".join(message.folder), message.subject, message.sender, message.date, message.size, _flag_names(message.flags), message.has_attachments)


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


def mail_fetch(engine: Engine, parameters: AccountParameters) -> FetchResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        settings = mailbox.settings
        password = engine.passwords.get(settings.account.account_id, ServerRole.INCOMING)
        if password is None:
            raise ConfigurationError("No password is stored for this account")
        state = Pop3State(engine.directories.pop3_state_file(mailbox.account_id), durable=engine.durable)
        inbox = mailbox.role_folder(FolderRole.INBOX)
        with Pop3Client(settings.account.incoming, password, Pop3Limits(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES)) as client:
            report = fetch_pop3(
                client, mailbox.store, mailbox.index, state, inbox, settings.pop3, MAXIMUM_MESSAGE_BYTES, engine.clock,
                progress=lambda done, total: engine.progress("Downloading", done, total),
            )
    return FetchResult(
        report.downloaded,
        report.already_known,
        tuple(f"{item.unique_id}: {item.reason}" for item in report.failed),
        report.deleted_on_server,
        report.deletion_blocked,
        report.index_problems,
        report.aborted,
    )


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
    to: tuple[str, ...] = field(metadata=option("A recipient (repeat for more)"))
    subject: str = field(default="", metadata=option("The subject"))
    body: str = field(default="", metadata=option("The text of the message"))
    cc: tuple[str, ...] = field(default=(), metadata=option("A copy recipient (repeat for more)"))
    bcc: tuple[str, ...] = field(default=(), metadata=option("A blind copy recipient (repeat for more)"))
    html: str | None = field(default=None, metadata=option("An HTML version of the body"))
    attach: tuple[str, ...] = field(default=(), metadata=option("A file to attach (repeat for more)"))
    in_reply_to: str | None = field(default=None, metadata=option("The Message-ID this is a reply to"))


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: bool
    message_id: str
    folder: str
    refused: tuple[str, ...]
    error: str | None
    warning: str | None


def mail_send(engine: Engine, parameters: SendParameters) -> SendResult:
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
            attachments=tuple(_attachment(path) for path in parameters.attach),
            in_reply_to=parameters.in_reply_to,
            references=(parameters.in_reply_to,) if parameters.in_reply_to else (),
        )
        outcome = send_and_file(mailbox.store, mailbox.index, settings.account.outgoing, password, outgoing, OUTBOX, mailbox.role_folder(FolderRole.SENT))
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
        return ListResult("/".join(folder), mailbox.index.count(folder), mailbox.index.count(folder, unread_only=True), tuple(_row(row) for row in rows))


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


def _check_id(message_id: str) -> str:
    if len(message_id) != 32 or any(character not in "0123456789abcdef" for character in message_id):
        raise UsageError("A message id is 32 lower-case hexadecimal characters")
    return message_id


def _shown(addresses: tuple[Address, ...]) -> tuple[str, ...]:
    return tuple(f"{a.name} <{a.address}>" if a.name else a.address for a in addresses)


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
            tuple(AttachmentInfo(a.filename, a.content_type, len(a.data), a.inline) for a in parsed.attachments),
            parsed.defects,
        )


@dataclass(frozen=True, slots=True)
class SearchParameters:
    account: str = field(metadata=option("The account"))
    query: str = field(default="", metadata=option("Words to find; from:, to:, subject: and body: limit a word to one field"))
    folder: str | None = field(default=None, metadata=option("Only in this folder"))
    unread: bool | None = field(default=None, metadata=option("Only unread (true) or only read (false) messages"))
    flagged: bool | None = field(default=None, metadata=option("Only flagged (true) or only unflagged (false) messages"))
    has_attachments: bool | None = field(default=None, metadata=option("Only messages with (true) or without (false) attachments"))
    offset: int = field(default=0, metadata=option("How many results to skip"))
    limit: int = field(default=50, metadata=option("How many results to show"))


def message_search(engine: Engine, parameters: SearchParameters) -> tuple[MessageRow, ...]:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        filters = SearchFilter(unread=parameters.unread, flagged=parameters.flagged, has_attachments=parameters.has_attachments)
        return tuple(_row(row) for row in mailbox.index.search(parameters.query, folder, filters, parameters.offset, parameters.limit))


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


def register(registry: CommandRegistry) -> None:
    registry.register("mail.fetch", "Download new messages from the POP3 server", AccountParameters, mail_fetch)
    registry.register("mail.send", "Send a message (a copy stays in Outbox until it is sent, then moves to Sent)", SendParameters, mail_send)
    registry.register("message.list", "List the messages of a folder", ListParameters, message_list)
    registry.register("message.show", "Show one message", MessageParameters, message_show)
    registry.register("message.search", "Search messages", SearchParameters, message_search)
    registry.register("message.flag", "Change the read, flagged or replied marks of a message", FlagParameters, message_flag)
    registry.register("message.move", "Move a message to another folder", TransferParameters, message_move)
    registry.register("message.copy", "Copy a message to another folder", TransferParameters, message_copy)
    registry.register("message.delete", "Move a message to Trash, or delete it for good", DeleteParameters, message_delete, destructive=True)
