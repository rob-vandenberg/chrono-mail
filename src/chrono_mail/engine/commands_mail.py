import base64
import mimetypes
import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Literal

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError, FolderRole, MessageFlag
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.commands_addressbook import AddressesParameters, contact_addresses
from chrono_mail.engine.commands_emclient import fingerprint_of_raw, known_messages
from chrono_mail.engine.commands_rules import apply_to_new
from chrono_mail.engine.recurring import RECURRING_FILE, Repeat, load_repeats, save_repeats
from chrono_mail.engine.schedule import SNOOZE_GROUPS, SNOOZED_FILE, SNOOZED_FOLDER, describe_when, load_snoozed, parse_when, snooze_group
from chrono_mail.engine.compose import ComposeKind, build_draft, message_filename, suggest_filename
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import IndexedMessage, SearchFilter, SortField
from chrono_mail.index.model import DateBounds, GroupCount
from chrono_mail.mime import Address, Attachment, OutgoingMessage, ParsedMessage, build_message, parse_message
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.sync import ImapState, ImapSyncOptions, Pop3State, fetch_pop3, send_and_file, sync_imap

# --- Version ------------------------------------------------------------
__version__ = "commands_mail.py 0.5.145"

# --- Version history ----------------------------------------------------
# v0.5.145: mail.fetch reports its steps per account (engine.activity): connecting, listing, downloading, rules, disconnected
# v0.5.144: The rules run before the imported-account flag is switched off; a failure to save it is reported, the check still counts
# v0.5.134: Tags: each listed message has its tags; message.list and message.search find messages by tag
# v0.5.132: The first check of an imported account records mail that is already here instead of storing it twice
# v0.4.120: The IMAP sync holds the account's link (no action changes its messages meanwhile); the Sent copy goes to the server at once
# v0.4.119: mail.fetch reports IMAP folders held back by a safety rule (blocked); mail.settle runs the sync with the person's answer for one
# v0.3.109: Sort orders unread, flagged, attachment, replied, contact (sorted here: the index does not know the contacts), folder, conversation and preview
# v0.2.100: Rows of the Snoozed folder carry the time they come back (snoozed_until); sort order "snoozed" with its groups (Today, Tomorrow, This week, Next week, Later)
# v0.2.94: Sorting by recipients (to); search results in any sort order; message.groups: the groups of a folder or a search for the group headers
# v0.0.86: Forward as attachment: the original message attached as an .eml file (attach_source_message)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.67: Messages that are sent again and again
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: replies are sent from the account the message was addressed to
# v0.0.43: Certificate exceptions
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
    account: str = ""  # only filled in the combined views (message.combined): the account the row belongs to
    snoozed_until: datetime | None = None  # in the Snoozed folder: when the message comes back
    tags: tuple[str, ...] = ()


def _rows(mailbox: Mailbox, messages: Iterable[IndexedMessage], snoozed: dict[str, int] | None = None) -> tuple[MessageRow, ...]:
    """Message rows with the size of their conversations and, for the Snoozed folder, the time they come back."""
    found = list(messages)
    sizes = mailbox.index.thread_sizes([message.thread_key for message in found])
    times = snoozed or {}
    return tuple(
        MessageRow(
            message.store_id, "/".join(message.folder), message.subject, message.sender, message.date, message.size, _flag_names(message.flags),
            message.has_attachments, message.preview, message.recipients, sizes.get(message.thread_key, 1),
            snoozed_until=datetime.fromtimestamp(times[message.store_id]).astimezone() if message.folder == SNOOZED and message.store_id in times else None,
            tags=message.tags,
        )
        for message in found
    )


SNOOZED: tuple[str, ...] = (SNOOZED_FOLDER,)
LATER: int = 2**62  # sorts a snoozed message without a record after all others


def _snoozed_times(engine: Engine, mailbox: Mailbox) -> dict[str, int]:
    """store id -> when the message comes back (seconds), from the account's record of snoozed messages."""
    return {store_id: int(str(entry["until"])) for store_id, entry in load_snoozed(engine.directories.account_directory(mailbox.account_id) / SNOOZED_FILE).items()}


def _by_snooze(messages: list[IndexedMessage], times: dict[str, int], soonest_first: bool) -> list[IndexedMessage]:
    """The messages of the Snoozed folder by the time they come back. The index does not know these times, so the whole
    folder is sorted here; the Snoozed folder holds what a person put away by hand, not hundreds of thousands of messages."""
    return sorted(messages, key=lambda message: times.get(message.store_id, LATER), reverse=not soonest_first)


CONTACT_SORT: str = "contact"
_RECIPIENT_ROLES: frozenset[FolderRole] = frozenset({FolderRole.SENT, FolderRole.DRAFTS})  # with Outbox: the folders where a row shows its recipients


def contact_address_set(engine: Engine) -> frozenset[str]:
    """Every e-mail address of every contact, in lower case."""
    return frozenset(entry.address for entry in contact_addresses(engine, AddressesParameters()))


def from_contacts(mailbox: Mailbox, messages: list[IndexedMessage], known: frozenset[str]) -> dict[str, bool]:
    """store id -> whether the message is from a contact (in Sent, Drafts and Outbox: to a contact, the first recipient),
    as the Contact column shows it."""
    to_folders = {folder.path for folder in mailbox.folders() if folder.role in _RECIPIENT_ROLES} | {OUTBOX}
    result: dict[str, bool] = {}
    for message in messages:
        found = getaddresses([message.recipients if message.folder in to_folders and message.recipients else message.sender])
        result[message.store_id] = bool(found) and found[0][1].strip().lower() in known
    return result


def _by_contact(engine: Engine, mailbox: Mailbox, newest_first: list[IndexedMessage], contacts_first: bool) -> list[IndexedMessage]:
    """The messages from a contact first or last, newest first within each part. The index does not know the contacts, so all messages are sorted here."""
    contact = from_contacts(mailbox, newest_first, contact_address_set(engine))
    return sorted(newest_first, key=lambda message: contact[message.store_id], reverse=contacts_first)  # a stable sort: newest first stays within each part


EVERYTHING: int = 2**62  # a limit that takes every message


def _snooze_sort(sort: str, folder: tuple[str, ...] | None) -> bool:
    """Sorting by the time a message comes back is only possible in the Snoozed folder; elsewhere it is sorting by date."""
    return sort == "snoozed" and folder == SNOOZED


# --- fetching and sending -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BlockedFolder:
    folder: str
    kind: str  # missing_here: most of its messages were deleted here; gone_from_server: most are gone from the server
    count: int


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
    blocked: tuple[BlockedFolder, ...] = ()  # IMAP folders a safety rule held back: the person decides (mail.settle)


def mail_fetch(engine: Engine, parameters: AccountParameters) -> FetchResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        settings = mailbox.settings
        if settings.account.kind is AccountKind.LOCAL:
            raise UsageError("Local Folders has no server: there is no mail to fetch")
        password = engine.passwords.get(settings.account.account_id, ServerRole.INCOMING)
        if password is None:
            raise ConfigurationError("No password is stored for this account")
        if settings.account.kind is AccountKind.IMAP:
            return _imap_fetch(engine, mailbox, password)
        state = Pop3State(engine.directories.pop3_state_file(mailbox.account_id), durable=engine.durable)
        inbox = mailbox.role_folder(FolderRole.INBOX)
        known = known_messages(mailbox) if settings.skip_known_mail else None  # an imported account: its mail may be here already

        def already_here(raw: bytes) -> str | None:
            key = fingerprint_of_raw(raw) if known is not None else None
            return None if key is None or known is None else known.get(key)

        account_id = settings.account.account_id

        def downloading(done: int, total: int) -> None:
            engine.progress("Downloading", done, total)
            engine.activity(account_id, "receive", "Downloading", done, total)

        engine.activity(account_id, "receive", "Connecting", None, None)
        with Pop3Client(settings.account.incoming, password, Pop3Limits(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES), engine.tls_context(settings)) as client:
            engine.activity(account_id, "receive", "Connected", None, None)
            report = fetch_pop3(
                client, mailbox.store, mailbox.index, state, inbox, settings.pop3, MAXIMUM_MESSAGE_BYTES, engine.clock,
                progress=downloading, already_here=already_here if known is not None else None,
                step=lambda text: engine.activity(account_id, "receive", text, None, None),
            )
        engine.activity(account_id, "receive", "Disconnected", None, None)
        if report.new_references:
            engine.activity(account_id, "receive", "Applying rules", None, None)
        ruled = apply_to_new(engine, mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])
        flag_failed: tuple[str, ...] = ()
        if settings.skip_known_mail and report.aborted is None:  # one complete check: from now on every new message is new
            try:
                engine.change_config(lambda config: config.with_account(replace(config.find(account_id), skip_known_mail=False)))
            except ConfigurationError as error:  # the flag stays on: the next check recognises the mail that is here once more, which does no harm
                flag_failed = (str(error),)
    return FetchResult(
        report.downloaded,
        report.already_known,
        (*(f"{item.unique_id}: {item.reason}" for item in report.failed), *ruled.failed, *flag_failed),
        report.deleted_on_server,
        report.deletion_blocked,
        report.index_problems,
        report.aborted,
        ruled.actions,
    )


def _imap_fetch(engine: Engine, mailbox: Mailbox, password: str, confirmed: tuple[tuple[tuple[str, ...], str], ...] = ()) -> FetchResult:
    """mail.fetch for an IMAP account: the two-way sync of store and server (confirmed: the person's answers for blocked folders)."""
    link = engine.imap_link(mailbox.account_id)
    with link.lock:  # the actions done on the server first wait until the sync is done, and the other way round
        link.folders = None  # the sync may make, renumber or forget folders
        link.validity.clear()
        state = ImapState(engine.directories.imap_state_file(mailbox.account_id), durable=engine.durable)
        account_id = mailbox.account_id

        def downloading(folder: str, done: int, total: int) -> None:
            engine.progress(f"Downloading {folder}", done, total)
            engine.activity(account_id, "receive", f"Downloading {folder}", done, total)

        engine.activity(account_id, "receive", "Connecting", None, None)
        with ImapClient(mailbox.settings.account.incoming, password, ssl_context=engine.tls_context(mailbox.settings)) as client:
            engine.activity(account_id, "receive", "Connected", None, None)
            report = sync_imap(
                client, mailbox.store, mailbox.index, state, ImapSyncOptions(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES, confirmed=confirmed),
                progress=downloading, step=lambda text: engine.activity(account_id, "receive", text, None, None),
            )
        engine.activity(account_id, "receive", "Disconnected", None, None)
    inbox = mailbox.role_folder(FolderRole.INBOX)
    if report.new_references:
        engine.activity(account_id, "receive", "Applying rules", None, None)
    ruled = apply_to_new(engine, mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])
    notes = (f"{item.folder}: {item.note}" for item in report.folders if item.note and item.blocked is None)  # a blocked folder is a question, not an error
    remarks = (*report.failed, *report.notes, *notes, *ruled.failed)
    blocked = tuple(BlockedFolder(item.folder, item.blocked, item.blocked_count) for item in report.folders if item.blocked is not None)
    return FetchResult(report.downloaded, 0, tuple(remarks), 0, None, 0, report.aborted, ruled.actions, blocked)


@dataclass(frozen=True, slots=True)
class SettleParameters:
    account: str = field(metadata=option("The IMAP account"))
    folder: str = field(metadata=option("The folder a safety rule held back (as mail.fetch reported it)"))
    choice: Literal["delete_on_server", "restore_here", "remove_here", "keep_here"] = field(
        metadata=option("Most messages missing here: delete_on_server or restore_here; most gone from the server: remove_here or keep_here")
    )


def mail_settle(engine: Engine, parameters: SettleParameters) -> FetchResult:
    """Synchronise an IMAP account once with the person's answer for a folder that a safety rule held back."""
    with open_mailbox(engine, parameters.account) as mailbox:
        if mailbox.settings.account.kind is not AccountKind.IMAP:
            raise UsageError("Only an IMAP account can have a folder held back")
        password = engine.passwords.get(mailbox.settings.account.account_id, ServerRole.INCOMING)
        if password is None:
            raise ConfigurationError("No password is stored for this account")
        return _imap_fetch(engine, mailbox, password, ((mailbox.folder(parameters.folder), parameters.choice),))


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
    attach_source_message: bool = field(default=False, metadata=option("Forward as attachment: attach the whole source message as an .eml file"))
    mark_replied: bool = field(default=False, metadata=option("Reply: mark the source message as replied to once sent"))
    keep_folder: str | None = field(default=None, metadata=option("The folder of a stored message (a draft) whose attachments are to be sent"))
    keep_message: str | None = field(default=None, metadata=option("The id of that stored message"))
    keep_attachments: tuple[str, ...] = field(default=(), metadata=option("The position of an attachment of that stored message to send (repeat for more)"))
    replaces_folder: str | None = field(default=None, metadata=option("The folder of a draft (or Outbox message) that this send replaces"))
    replaces_message: str | None = field(default=None, metadata=option("The id of a draft (or Outbox message) that is removed once this message was sent"))
    send_at: str | None = field(default=None, metadata=option("Send later: the local time, like 2026-10-11T09:00; the message waits in the Outbox"))
    repeat_months: int = field(default=0, metadata=option("With send_at: send the message again every this many months (with the weeks and days: one interval)"))
    repeat_weeks: int = field(default=0, metadata=option("With send_at: send the message again every this many weeks"))
    repeat_days: int = field(default=0, metadata=option("With send_at: send the message again every this many days"))
    repeat_until: str | None = field(default=None, metadata=option("With a repeat: the local time after which nothing is sent any more (default: never)"))


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
    if not parameters.include_source_attachments and not parameters.attach_source_message:
        return kept
    if not parameters.source_folder or not parameters.source_message:
        raise UsageError("Taking attachments along needs the source message")
    source = mailbox.reference(mailbox.folder(parameters.source_folder), _check_id(parameters.source_message))
    raw = mailbox.store.read(source, hint=mailbox.hint(source.store_id))
    parsed = parse_message(raw)
    if parameters.attach_source_message:
        return (*kept, Attachment(message_filename(parsed.subject), "message/rfc822", None, False, raw))
    return (*kept, *(attachment for attachment in parsed.attachments if not attachment.inline))


def _schedule(engine: Engine, mailbox: Mailbox, outgoing: OutgoingMessage, parameters: SendParameters) -> SendResult:
    """Keep the message in the Outbox, marked with the time it is to be sent (mail.send-due sends it then)."""
    when = parse_when(str(parameters.send_at), engine.clock())
    repeating = parameters.repeat_months or parameters.repeat_weeks or parameters.repeat_days
    if parameters.repeat_until and not repeating:
        raise UsageError("An end time needs an interval (repeat_days, repeat_weeks or repeat_months)")
    repeat = (
        Repeat(when, parse_when(parameters.repeat_until, engine.clock()) if parameters.repeat_until else None, parameters.repeat_months, parameters.repeat_weeks, parameters.repeat_days)
        if repeating else None
    )
    notes: list[tuple[str, str]] = [("X-Chrono-Mail-Kind", "recurring" if repeat else "scheduled")]
    if repeat is None:
        notes.append(("X-Chrono-Mail-Send-At", str(when)))
    if parameters.source_folder and parameters.source_message:
        notes.append(("X-Chrono-Mail-Source", f"{parameters.source_folder}/{_check_id(parameters.source_message)}"))
        if parameters.mark_replied:
            notes.append(("X-Chrono-Mail-Mark-Replied", "1"))
    scheduled = replace(outgoing, extra_headers=tuple(notes))
    reference = mailbox.deliver(mailbox.folder("Outbox"), build_message(scheduled, draft=True), frozenset({MessageFlag.SEEN}))
    if repeat is not None:  # the message stays in the Outbox as the pattern; the schedule is kept next to it
        path = engine.directories.account_directory(mailbox.account_id) / RECURRING_FILE
        repeats = load_repeats(path)
        repeats[reference.store_id] = repeat
        save_repeats(path, repeats)
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
        if (parameters.repeat_months or parameters.repeat_weeks or parameters.repeat_days or parameters.repeat_until) and not parameters.send_at:
            raise UsageError("Repeating needs the time of the first send (send_at)")
        if parameters.send_at:
            return _schedule(engine, mailbox, outgoing, parameters)
        outcome = send_and_file(mailbox.store, mailbox.index, settings.account.outgoing, password, outgoing, OUTBOX, mailbox.role_folder(FolderRole.SENT), ssl_context=engine.tls_context(settings, outgoing=True))
        if outcome.sent:
            mailbox.put_on_server(outcome.reference)  # IMAP: the Sent copy is in Sent on the server at once
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
    sort: Literal["date", "subject", "sender", "to", "size", "snoozed", "unread", "flagged", "attachment", "replied", "contact", "folder", "conversation", "preview"] = field(
        default="date", metadata=option("The sort order (snoozed: when it comes back, only in the Snoozed folder; unread ... contact: yes first unless oldest_first)")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many messages to skip"))
    limit: int = field(default=50, metadata=option("How many messages to show"))
    unread_only: bool = field(default=False, metadata=option("Only unread messages"))
    tag: str | None = field(default=None, metadata=option("Only messages with this tag"))


@dataclass(frozen=True, slots=True)
class ListResult:
    folder: str
    total: int
    unread: int
    messages: tuple[MessageRow, ...]


def message_list(engine: Engine, parameters: ListParameters) -> ListResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        total = mailbox.index.count(folder)
        times = _snoozed_times(engine, mailbox) if folder == SNOOZED else None
        if times is not None and _snooze_sort(parameters.sort, folder):
            everything = mailbox.index.page(folder, SortField.DATE, True, 0, total, parameters.unread_only)
            rows = _by_snooze(everything, times, parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.sort == CONTACT_SORT:
            everything = mailbox.index.page(folder, SortField.DATE, True, 0, total, parameters.unread_only)
            rows = _by_contact(engine, mailbox, everything, not parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.tag:
            only = SearchFilter(unread=True if parameters.unread_only else None, tag=parameters.tag)
            rows = mailbox.index.search("", folder, only, parameters.offset, parameters.limit, _sort_field(parameters.sort), not parameters.oldest_first)
        else:
            rows = mailbox.index.page(folder, _sort_field(parameters.sort), not parameters.oldest_first, parameters.offset, parameters.limit, parameters.unread_only)
        return ListResult("/".join(folder), total, mailbox.index.count(folder, unread_only=True), _rows(mailbox, rows, times))


def _sort_field(sort: str) -> SortField:
    return SortField.DATE if sort in ("snoozed", CONTACT_SORT) else SortField(sort)


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
    query: str = field(default="", metadata=option("Words to find; from:, to:, subject: and body: limit a word to one field; tag:name finds a tag"))
    folder: str | None = field(default=None, metadata=option("Only in this folder"))
    unread: bool | None = field(default=None, metadata=option("Only unread (true) or only read (false) messages"))
    flagged: bool | None = field(default=None, metadata=option("Only flagged (true) or only unflagged (false) messages"))
    has_attachments: bool | None = field(default=None, metadata=option("Only messages with (true) or without (false) attachments"))
    date_from: str | None = field(default=None, metadata=option("Only messages from this day on (YYYY-MM-DD, your local time)"))
    date_to: str | None = field(default=None, metadata=option("Only messages up to and including this day (YYYY-MM-DD)"))
    minimum_size: int | None = field(default=None, metadata=option("Only messages of at least this many bytes"))
    maximum_size: int | None = field(default=None, metadata=option("Only messages of at most this many bytes"))
    tag: str | None = field(default=None, metadata=option("Only messages with this tag (or type tag:name in the words)"))
    conversations: bool = field(default=False, metadata=option("Show only the newest message of each conversation (not together with words to find)"))
    sort: Literal["date", "subject", "sender", "to", "size", "snoozed", "unread", "flagged", "attachment", "replied", "contact", "folder", "conversation", "preview"] = field(
        default="date", metadata=option("The sort order (snoozed: when it comes back, only in the Snoozed folder; unread ... contact: yes first unless oldest_first)")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many results to skip (not for message.groups)"))
    limit: int = field(default=50, metadata=option("How many results to show (not for message.groups)"))


def date_bounds(now: datetime) -> DateBounds:
    """Where the date groups begin, in the local time of this moment: today, yesterday, this week (from Monday), last week, this month, last month."""
    today = now.astimezone().date()
    this_week = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    days = (today, today - timedelta(days=1), this_week, this_week - timedelta(days=7), this_month, last_month)
    starts = [datetime.combine(day, time.min).astimezone() for day in days]  # each day's own midnight, also across a change to or from summer time
    return DateBounds(starts[0], starts[1], starts[2], starts[3], starts[4], starts[5])


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


def _search_filter(parameters: SearchParameters) -> SearchFilter:
    return SearchFilter(
        unread=parameters.unread, flagged=parameters.flagged, has_attachments=parameters.has_attachments,
        date_from=_day(parameters.date_from, "date_from", end=False), date_to=_day(parameters.date_to, "date_to", end=True),
        minimum_size=parameters.minimum_size, maximum_size=parameters.maximum_size, newest_per_thread=parameters.conversations, tag=parameters.tag,
    )


def message_search(engine: Engine, parameters: SearchParameters) -> tuple[MessageRow, ...]:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        times = _snoozed_times(engine, mailbox) if folder in (SNOOZED, None) else None
        if times is not None and _snooze_sort(parameters.sort, folder) and folder is not None:
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters), 0, mailbox.index.count(folder), SortField.DATE, True)
            found = _by_snooze(everything, times, parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.sort == CONTACT_SORT:
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters), 0, EVERYTHING, SortField.DATE, True)
            found = _by_contact(engine, mailbox, everything, not parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        else:
            found = mailbox.index.search(parameters.query, folder, _search_filter(parameters), parameters.offset, parameters.limit, _sort_field(parameters.sort), not parameters.oldest_first)
        return _rows(mailbox, found, times)


def message_groups(engine: Engine, parameters: SearchParameters) -> tuple[GroupCount, ...]:
    """The groups of a folder or a search, in the order of message.search (or message.list) with the same options, each with its number of messages."""
    if parameters.sort == CONTACT_SORT:
        return ()  # a yes/no sort has no group headers
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        if folder is not None and _snooze_sort(parameters.sort, folder):
            times, now = _snoozed_times(engine, mailbox), datetime.fromtimestamp(engine.clock()).astimezone()
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters), 0, mailbox.index.count(folder), SortField.DATE, True)
            counts: dict[str, int] = {}
            for message in everything:
                key = snooze_group(times.get(message.store_id), now)
                counts[key] = counts.get(key, 0) + 1
            order = SNOOZE_GROUPS if parameters.oldest_first else tuple(reversed(SNOOZE_GROUPS))
            return tuple(GroupCount(key, name, counts[key]) for key, name in order if counts.get(key))
        found = mailbox.index.groups(date_bounds(datetime.now()), parameters.query, folder, _search_filter(parameters), _sort_field(parameters.sort), not parameters.oldest_first)
        return tuple(found)


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
    kind: ComposeKind = field(default="reply", metadata=option("reply, reply_all, forward or forward_attachment"))
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
    from_account: str | None = None  # the account the answer is sent from; None: the caller chooses


def reply_account(engine: Engine, opened: AccountSettings, parsed: ParsedMessage, raw: bytes) -> str | None:
    """The account a reply or a forward is sent from. The same rule wherever the mail is kept (its own folders or Local Folders):
    the account whose address the message was sent to (To, Cc, Delivered-To and the like); the account whose folders it is in
    wins when it is one of them or when none matches. None when nothing decides (the person's default account then)."""
    from chrono_mail.engine.rule_engine import header_values

    addressed = {address.address.lower() for address in (*parsed.to_addresses, *parsed.cc_addresses)}
    for name in ("Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To"):
        for value in header_values(raw, name):
            addressed.add(parseaddr(value)[1].lower())
    senders = [item for item in engine.config().accounts if item.account.kind is not AccountKind.LOCAL]
    matches = [item for item in senders if item.account.email_address.lower() in addressed]
    here = opened if opened.account.kind is not AccountKind.LOCAL and opened.storage == "own" else None
    if here is not None and (not matches or any(item.account.account_id == here.account.account_id for item in matches)):
        return here.account.account_id
    return matches[0].account.account_id if matches else None


def message_compose(engine: Engine, parameters: ComposeParameters) -> ComposeResult:
    """The starting point of a reply, a reply to all, or a forward (nothing is stored or sent)."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        from_account = reply_account(engine, mailbox.settings, parsed, raw)
        own = engine.resolve_account(from_account).account.email_address if from_account else mailbox.settings.account.email_address
        draft = build_draft(parsed, parameters.kind, own, parameters.quote)
        return ComposeResult(draft.kind, draft.to, draft.cc, draft.subject, draft.body, draft.in_reply_to, draft.references, draft.attachment_names, from_account)


def register(registry: CommandRegistry) -> None:
    registry.register("mail.fetch", "Download new messages from the POP3 server", AccountParameters, mail_fetch)
    registry.register("mail.settle", "Answer for an IMAP folder that a safety rule held back (may delete on the server or here)", SettleParameters, mail_settle, destructive=True)
    registry.register("mail.send", "Send a message (a copy stays in Outbox until it is sent, then moves to Sent)", SendParameters, mail_send)
    registry.register("message.list", "List the messages of a folder", ListParameters, message_list)
    registry.register("message.thread", "List the messages of the conversation a message belongs to", MessageParameters, message_thread)
    registry.register("message.show", "Show one message", MessageParameters, message_show)
    registry.register("message.search", "Search messages", SearchParameters, message_search)
    registry.register("message.groups", "The groups of a folder or a search (date, size, sender, recipients or subject, as sorted) with their number of messages", SearchParameters, message_groups)
    registry.register("message.attachment.save", "Save one attachment of a message to a file", AttachmentSaveParameters, message_attachment_save)
    registry.register("message.compose", "Prepare a reply, a reply to all, or a forward of a message", ComposeParameters, message_compose)
    registry.register("message.flag", "Change the read, flagged or replied marks of a message", FlagParameters, message_flag)
    registry.register("message.move", "Move a message to another folder", TransferParameters, message_move)
    registry.register("message.copy", "Copy a message to another folder", TransferParameters, message_copy)
    registry.register("message.delete", "Move a message to Trash, or delete it for good", DeleteParameters, message_delete, destructive=True)
