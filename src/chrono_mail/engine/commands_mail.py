import bisect
import base64
import mimetypes
import os
from collections.abc import Collection, Generator, Iterable, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Literal

from chrono_mail.pim.times import local_zone, moment_at
from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, written_address, CertificateTrouble, ChronoMailError, ConfigurationError, Folder, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.commands_addressbook import AddressesParameters, contact_addresses
from chrono_mail.engine.import_common import fingerprint_of_raw, known_messages
from chrono_mail.engine.changes import Change, MessageRow, flag_names
from chrono_mail.engine.commands_rules import apply_to_new
from chrono_mail.engine.recurring import Repeat, repeat_from_text, repeat_text
from chrono_mail.engine.schedule import REPEAT_LOCK, SCHEDULED_FOLDER, SNOOZE_GROUPS, TOLERANCE_SECONDS, TIMED_FOLDERS, describe_when, parse_when, snooze_group
from chrono_mail.engine.compose import ComposeKind, build_draft, message_filename, suggest_filename
from chrono_mail.engine.imap_online import waiting_marker
from chrono_mail.engine.junkfilter import filter_new, learn, learn_from_move
from chrono_mail.engine.mailbox import Mailbox, SentAlready, open_mailbox
from chrono_mail.engine.receiving import own_place, receiving_account
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, OUTBOX, talks_to_servers
from chrono_mail.index import IndexedMessage, SearchFilter, SortField
from chrono_mail.index.model import DateBounds, GroupCount
from chrono_mail.mime import REPEAT_HEADER, Address, Attachment, OutgoingMessage, ParsedMessage, build_message, delivery_addresses, parse_message
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.sync import ImapState, ImapSyncOptions, Pop3State, fetch_pop3, send_and_file, sync_imap
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "commands_mail.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: mail.send names the outgoing server when its certificate could not be verified (certificate), so the window asks (D-299)
# v4.2.432: the Contact sort judges every row by the person the list shows (shows_recipients), one rule for the whole list (D-295)
# v4.2.430: addresses shown to the window written so they can be read back (written_address); a row carries to_shown; the Contact sort takes the first address from the address columns, never the names read back
# v4.2.429: the sort order to_shown: From / To for mail one sent, exactly as it shows (D-295)
# v4.2.418: the account a message is sent from, and the account that downloads a message, are recorded with it (D-292)
# v4.2.417: Reply's account by the one rule of engine/receiving.py; a POP3 check tells the index which account downloaded each message (D-292)
# v4.2.408: the searches take sent_from and sent_to (the message's own Date), snoozed_from and snoozed_to, send_at_from and
#           send_at_to (days, D-288)
# v4.2.406: message.search, message.groups and message.order take subfolders: a folder's search also looks in the folders inside it (D-287)
# v4.2.385: the fingerprint of the mail already here comes from import_common (shared by the importers)
# v3.6.368: message.places and message.locate read the order and the headings under one hold of the mailbox's lock: an Undo between them put
#           messages under the wrong heading (a held Ctrl+Z)
# v3.6.358: each_kept_attachment says per position what is kept, so a draft save can tell where each attachment went (CR-03)
# v3.6.354: Chrono Mail's own folders known by their role, their names written once (core OWN_FOLDERS) (CR-36)
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError; what follows a send the server took never makes it fail (told_after_send: a warning)
# v3.6.350: a send that replaces an Outbox message the server took, or one gone meanwhile, is refused (never sent twice); it goes in the
#           same locked step that stores or sends what replaces it; a message sent whose move to Sent failed is remembered for the run
#           (never sent again, also on a full disk); a message of Scheduled it replaces goes in that locked step too; a delayed send writes
#           its waiting time before what it replaces goes, and a failure of either leaves only what was there before
# v3.6.349: a snooze or send time in the list without the platform's conversion (Windows refuses before 1970 and after 3000, RV-201, RV-204); mail.settle waits for a restore and is held back by it, like a check (RV-171)
# v3.1.341: Send puts its copy in the Outbox and sends it under the Outbox lock (a refresh never sends it too, RV-109); a failed send
#           replaces its draft too: the message waits in the Outbox (RV-155)
# v3.0.339: a restore waits until this command has ended and holds it back meanwhile (talks_to_servers, RV-60)
# v2.2.314: mail.send with delay_seconds or hold puts the message in the Outbox and sends nothing (B-21); a reply waiting in the Outbox
#           notes the message it answers, marked as replied once it is really sent (also after a failed send)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.1.308: the junk filter: new Inbox mail goes through it before the rules (FetchResult.junked); a move into or out of Junk and a
#           sent reply teach it (D-238)
# v2.0.302: InlineImage.position: the picture's place among the attachments, to open or save it from a right-click (D-236)
# v2.0.301: message.show gives the pictures the text does not show (pictures), to be shown below it (B-11, D-235)
# v2.0.280: the sort order sent (the message's own Date, with the date groups) and each row's sent moment (B-16)
# v1.9.273: R-19: typed message.search
# v1.9.270: R-19: typed message.show, message.thread, message.attachment.save, message.compose, mail.send, message.delete, mail.fetch, mail.settle, message.places, message.move
# v1.6.260: message.places also names, for each message, the one it comes before among those the asker holds (neighbours, R-15); the order of a
#           folder is read without its rows (Index.ordered)
# v1.1.247: flags_wanted: a message's marks with the asked changes, also for many at once (R-08)
# v1.1.239: The sync conflicts before and after a check are asked of the mailbox (R-37)
# v1.1.237: A folder the check renamed (the server renamed it) is told as renamed, never as deleted (R-30)
# v1.1.227: An IMAP check tells the window when its sync conflicts changed, and when changes waited for the server (D-195)
# v1.1.207: outgoing_message: the message as written, shared by mail.send and mail.edit-scheduled
# v1.1.206: A repeating message carries its timetable and its next send in itself, never in a file beside it (D-176)
# v1.1.203: The help of send_at says Schedule and the Scheduled folder
# v1.1.200: A scheduled message waits in the Scheduled folder (made when first used); Scheduled lists, sorts and groups by when each message is sent, as Snoozed by when it comes back
# v0.5.165: message.places counts only the messages the asker has heard of (heard; known), D-152
# v0.5.164: a mail check tells each change the moment it makes it (MailboxAnnouncer); list, search, groups and places read the index under the mailbox's place lock (D-151)
# v0.5.163: a mail check tells each change as a fact (new, removed, marks, tags, kept in Recovered, folders made or gone) instead of one "mailbox changed"; message.places can give the group heading of each message (headings)
# v0.5.162: stay: messages a view keeps although they no longer match its unread, flagged or tag filter (read, unflagged or untagged while it showed them)
# v0.5.160: message.places: where several messages stand in a folder or a search, the order worked out once (a new sort keeps the selected messages)
# v0.5.158: a sent message is reported where it is stored (Sent, or the Outbox when it failed); a mail check that made folders reports its mailbox as changed
# v0.5.156: message.locate: where a message stands in a folder or a search, and under which group heading (a list puts a message that came in at its place)
# v0.5.155: MessageRow lives in changes.py (the engine reports changes with it); a mail check reports its mailbox as changed, quietly (D-146)
# v0.5.153: message.position: where a message stands in a folder in a sort order (the list finds it again after a sort)
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

ListSort = Literal[  # the sort orders of a folder or a search
    "date", "sent", "subject", "sender", "to", "size", "snoozed", "unread", "flagged", "attachment", "replied", "contact", "folder", "conversation", "preview",
    "sender_full", "sender_address", "to_names", "to_addresses", "account",  # the people columns (D-291) and Account (D-292)
    "to_shown",  # From / To where it shows the recipients: its first recipient, else the sender (D-295)
]
MAXIMUM_ATTACHMENT_BYTES: int = 100 * 1024 * 1024
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024
MAXIMUM_INLINE_IMAGE_BYTES: int = 5 * 1024 * 1024
MAXIMUM_INLINE_IMAGES_TOTAL_BYTES: int = 20 * 1024 * 1024
SHOWN_PICTURE_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})  # shown below the text (B-11)
def _rows(mailbox: Mailbox, messages: Iterable[IndexedMessage], snoozed: dict[str, int] | None = None) -> tuple[MessageRow, ...]:
    """Message rows with the size of their conversations and, in the Snoozed and the Scheduled folder, the time they leave it."""
    found = list(messages)
    sizes = mailbox.index.thread_sizes([message.thread_key for message in found])
    times = snoozed or {}
    return tuple(
        MessageRow(
            message.store_id, "/".join(message.folder), message.subject, message.sender, message.date, message.size, flag_names(message.flags),
            message.has_attachments, message.preview, message.recipients, sizes.get(message.thread_key, 1),
            snoozed_until=moment_at(times[message.store_id]) if message.folder in TIMED and message.store_id in times else None,
            tags=message.tags, sent=message.sent, sender_address=message.sender_address, recipient_names=message.recipient_names,
            recipient_addresses=message.recipient_addresses, received_by=message.received_by, to_shown=message.to_shown,
        )
        for message in found
    )


SCHEDULED: tuple[str, ...] = (SCHEDULED_FOLDER,)
TIMED: tuple[tuple[str, ...], ...] = tuple((name,) for name in TIMED_FOLDERS)  # folders whose messages leave at a time (Mailbox.timed)
LATER: int = 2**62  # sorts a message without a known time after all others


def _by_snooze(messages: list[IndexedMessage], times: dict[str, int], soonest_first: bool) -> list[IndexedMessage]:
    """The messages of the Snoozed or the Scheduled folder by the time they leave it. The whole folder is sorted here; these
    folders hold what a person put away or scheduled by hand, not hundreds of thousands of messages."""
    return sorted(messages, key=lambda message: times.get(message.store_id, LATER), reverse=not soonest_first)


CONTACT_SORT: str = "contact"
_RECIPIENT_ROLES: frozenset[FolderRole] = frozenset({FolderRole.SENT, FolderRole.DRAFTS, FolderRole.OUTBOX, FolderRole.SCHEDULED})  # the folders where a row shows its recipients
RECIPIENT_VIEWS: tuple[str, ...] = ("sent", "drafts", "outbox", "scheduled")  # ... and the Favorites entries of those folders


def contact_address_set(engine: Engine) -> frozenset[str]:
    """Every e-mail address of every contact, in lower case."""
    return frozenset(entry.address for entry in contact_addresses(engine, AddressesParameters()))


def contact_address(recipients_first: bool, recipient_addresses: str, recipients: str, sender_address: str, sender: str) -> str:
    """The address (lower case) of the person a row shows, as the Contact column and the Contact sort take it: in Sent, Drafts, the Outbox and
    Scheduled the first recipient, else (or without a recipient) the sender; from the address columns, and only while a row of an upgraded
    index is not filled in yet from its text, as before."""
    if recipients_first and (recipient_addresses or recipients):
        return first_address(recipient_addresses) if recipient_addresses else _first_parsed(recipients)
    return first_address(sender_address) if sender_address else _first_parsed(sender)


def _first_parsed(text: str) -> str:
    found = getaddresses([text])
    return found[0][1].strip().lower() if found else ""


def first_address(addresses: str) -> str:
    """The first of the addresses an index row keeps joined by ", " (an address never holds one), in lower case; never read back as one
    text of names and addresses (a name that looks like an address made that unreadable)."""
    return addresses.split(", ", 1)[0].strip().lower()


def shows_recipients(mailbox: Mailbox, folder: tuple[str, ...] | None) -> bool:
    """Whether a list of this one folder shows who its messages went to: Sent, Drafts, the Outbox and Scheduled; a list of several
    folders (a search of a folder and its subfolders, of every folder) shows the senders. The window's From / To column and Contact
    mark follow the same rule, so the Contact sort judges every row by the person the row shows."""
    return folder is not None and any(found.path == folder and found.role in _RECIPIENT_ROLES for found in mailbox.folders())


def from_contacts(messages: list[IndexedMessage], known: frozenset[str], recipients_first: bool) -> dict[str, bool]:
    """store id -> whether the person the row shows is a contact, as the Contact column shows it: the first recipient in a list that
    shows the recipients (shows_recipients), else the sender; one rule for the whole list, never by each message's own folder."""
    result: dict[str, bool] = {}
    for message in messages:
        address = contact_address(recipients_first, message.recipient_addresses, message.recipients, message.sender_address, message.sender)
        result[message.store_id] = bool(address) and address in known
    return result


def _by_contact(engine: Engine, newest_first: list[IndexedMessage], contacts_first: bool, recipients_first: bool) -> list[IndexedMessage]:
    """The messages from a contact first or last, newest first within each part. The index does not know the contacts, so all messages are sorted here."""
    contact = from_contacts(newest_first, contact_address_set(engine), recipients_first)
    return sorted(newest_first, key=lambda message: contact[message.store_id], reverse=contacts_first)  # a stable sort: newest first stays within each part


EVERYTHING: int = 2**62  # a limit that takes every message


def _snooze_sort(sort: str, folder: tuple[str, ...] | None) -> bool:
    """Sorting by the time a message leaves its folder is only possible in Snoozed and Scheduled; elsewhere it is by date."""
    return sort == "snoozed" and folder in TIMED


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
    junked: int = 0  # new messages the junk filter moved to the Junk folder (D-238)


@talks_to_servers  # a restore waits for it and holds it back meanwhile (RV-60)
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
                step=lambda text: engine.activity(account_id, "receive", text, None, None), announcer=MailboxAnnouncer(mailbox),
                receiving=account_id,  # the account that received each new message, recorded with it (D-292)
            )
        engine.activity(account_id, "receive", "Disconnected", None, None)
        if report.new_references:
            engine.activity(account_id, "receive", "Applying rules", None, None)
        kept, junked = filter_new(mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])  # junk first (D-238)
        ruled = apply_to_new(engine, mailbox, kept)
        flag_failed: tuple[str, ...] = ()
        if settings.skip_known_mail and report.aborted is None:  # one complete check: from now on every new message is new
            try:
                engine.change_config(lambda config: config.with_account(replace(config.find(account_id), skip_known_mail=False)))
            except ConfigurationError as error:  # the flag stays on: the next check recognises the mail that is here once more, which does no harm
                flag_failed = (str(error),)
    return FetchResult(
        report.downloaded,
        report.already_known,
        (*(f"{item.unique_id}: {item.reason}" for item in report.failed), *junked.failed, *ruled.failed, *flag_failed),
        report.deleted_on_server,
        report.deletion_blocked,
        report.index_problems,
        report.aborted,
        ruled.actions,
        junked=junked.moved,
    )


def _imap_fetch(engine: Engine, mailbox: Mailbox, password: str, confirmed: tuple[tuple[tuple[str, ...], str], ...] = ()) -> FetchResult:
    """mail.fetch for an IMAP account: the two-way sync of store and server (confirmed: the person's answers for blocked folders)."""
    link = engine.imap_link(mailbox.account_id)
    with link.lock:  # the actions done on the server first wait until the sync is done, and the other way round
        link.folders = None  # the sync may make, renumber or forget folders
        link.validity.clear()
        state = ImapState(engine.directories.imap_state_file(mailbox.account_id), durable=engine.durable)
        account_id = mailbox.account_id
        folders_before = {folder.path: folder for folder in mailbox.folders()}
        conflicts_before = [c.conflict_id for c in mailbox.sync_conflicts(kept_too=True)]
        announcer = MailboxAnnouncer(mailbox)

        def downloading(folder: str, done: int, total: int) -> None:
            engine.progress(f"Downloading {folder}", done, total)
            engine.activity(account_id, "receive", f"Downloading {folder}", done, total)

        engine.activity(account_id, "receive", "Connecting", None, None)
        with ImapClient(mailbox.settings.account.incoming, password, ssl_context=engine.tls_context(mailbox.settings)) as client:
            engine.activity(account_id, "receive", "Connected", None, None)
            report = sync_imap(
                client, mailbox.store, mailbox.index, state, ImapSyncOptions(maximum_message_bytes=MAXIMUM_MESSAGE_BYTES, confirmed=confirmed),
                progress=downloading, step=lambda text: engine.activity(account_id, "receive", text, None, None), announcer=announcer,
            )
        engine.activity(account_id, "receive", "Disconnected", None, None)
    _report_sync(mailbox, folders_before, tuple(announcer.renamed))
    if [c.conflict_id for c in mailbox.sync_conflicts(kept_too=True)] != conflicts_before:
        engine.report(Change("conflicts_changed", account_id))  # a refusal became a sync conflict (D-195)
    if waiting_marker(engine, account_id).exists():
        engine.report(Change("waiting_changed", account_id))  # what waited may have gone: the window asks again
    inbox = mailbox.role_folder(FolderRole.INBOX)
    if report.new_references:
        engine.activity(account_id, "receive", "Applying rules", None, None)
    kept, junked = filter_new(mailbox, [reference for reference in report.new_references if reference.folder_path == inbox])  # junk first (D-238)
    ruled = apply_to_new(engine, mailbox, kept)
    notes = (f"{item.folder}: {item.note}" for item in report.folders if item.note and item.blocked is None)  # a blocked folder is a question, not an error
    remarks = (*report.failed, *report.notes, *notes, *junked.failed, *ruled.failed)
    blocked = tuple(BlockedFolder(item.folder, item.blocked, item.blocked_count) for item in report.folders if item.blocked is not None)
    return FetchResult(report.downloaded, 0, tuple(remarks), 0, None, 0, report.aborted, ruled.actions, blocked, junked.moved)


class MailboxAnnouncer:
    """A mail check tells each change through the mailbox the moment it makes it, holding the mailbox's place lock around
    the index write and the fact (D-150, D-151)."""

    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox: Mailbox = mailbox
        self.renamed: list[tuple[str, ...]] = []  # the old paths of folders renamed by the check (told as renamed, not as deleted)

    def changing(self) -> AbstractContextManager[object]:
        return self.mailbox.changing()

    def added(self, reference: MessageRef) -> None:
        self.mailbox.report_added(reference)

    def removed(self, reference: MessageRef, was_unread: bool) -> None:
        self.mailbox.report_removed(reference, was_unread)

    def flags(self, reference: MessageRef, flags: frozenset[MessageFlag], was_unread: bool) -> None:
        self.mailbox.report_flags(reference, flags, was_unread)

    def tags(self, reference: MessageRef, tags: tuple[str, ...]) -> None:
        self.mailbox.report_tags(reference, tags)

    def moved(self, before: MessageRef, now: MessageRef) -> None:
        self.mailbox.report_moved(before, now)

    def folder_made(self, folder: Folder) -> None:
        self.mailbox.report_folder_made(folder)

    def folder_renamed(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        self.renamed.append(old)
        self.mailbox.report_folder_renamed(old, new)


def _report_sync(mailbox: Mailbox, folders_before: dict[tuple[str, ...], Folder], renamed: tuple[tuple[str, ...], ...] = ()) -> None:
    """Folders the sync took away here (the server no longer has them) are told at its end; everything else was told the
    moment it happened. A folder that was renamed (and those inside it) was told then and is no deletion."""
    folders_after = {folder.path for folder in mailbox.folders()}
    for path in folders_before:
        if path not in folders_after and not any(path[: len(old)] == old for old in renamed):
            mailbox.report_folder_deleted(path)


@dataclass(frozen=True, slots=True)
class SettleParameters:
    account: str = field(metadata=option("The IMAP account"))
    folder: str = field(metadata=option("The folder a safety rule held back (as mail.fetch reported it)"))
    choice: Literal["delete_on_server", "restore_here", "remove_here", "keep_here"] = field(
        metadata=option("Most messages missing here: delete_on_server or restore_here; most gone from the server: remove_here or keep_here")
    )


@talks_to_servers  # a full sync like a check: a restore waits for it and holds it back meanwhile (RV-171)
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
    send_at: str | None = field(default=None, metadata=option("Schedule: the local time, like 2026-10-11T09:00; the message waits in the Scheduled folder"))
    repeat_months: int = field(default=0, metadata=option("With send_at: send the message again every this many months (with the weeks and days: one interval)"))
    repeat_weeks: int = field(default=0, metadata=option("With send_at: send the message again every this many weeks"))
    repeat_days: int = field(default=0, metadata=option("With send_at: send the message again every this many days"))
    repeat_until: str | None = field(default=None, metadata=option("With a repeat: the local time after which nothing is sent any more (default: never)"))
    delay_seconds: int = field(default=0, metadata=option("Wait in the Outbox this many seconds before it may be sent (Send now, or mail.send-outbox afterwards, sends it)"))
    hold: bool = field(default=False, metadata=option("Keep it in the Outbox until the Outbox is sent next (Send and Receive, Send All)"))


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: bool
    message_id: str
    folder: str
    refused: tuple[str, ...]
    error: str | None
    warning: str | None
    scheduled: str | None = None  # when it will be sent, if it was not sent now
    waiting: bool = False  # it waits in the Outbox (a delayed send): nothing was sent yet
    certificate: CertificateTrouble | None = None  # not sent: the outgoing server's certificate could not be verified; the window asks (D-299)


def kept_attachments(raw: bytes | None, positions: tuple[str, ...]) -> tuple[Attachment, ...]:
    """The attachments at these positions (as listed by message.show) of a stored message."""
    return tuple(attachment for attachment in each_kept_attachment(raw, positions) if attachment is not None)


def each_kept_attachment(raw: bytes | None, positions: tuple[str, ...]) -> tuple[Attachment | None, ...]:
    """For each position, in order, the attachment kept from the stored message; None for a part the text shows (inline), which is not kept."""
    if not positions:
        return ()
    if raw is None:
        raise UsageError("Keeping attachments needs the stored message they are in")
    parsed = parse_message(raw)
    kept: list[Attachment | None] = []
    for text in positions:
        if not digits(text) or int(text) >= len(parsed.attachments):
            raise UsageError("The stored message has no such attachment")
        attachment = parsed.attachments[int(text)]
        kept.append(None if attachment.inline else attachment)
    return tuple(kept)


def _source_attachments(mailbox: Mailbox, parameters: "SendParameters") -> tuple[Attachment, ...]:
    """The attachments of the message being forwarded and those kept from a stored draft, when asked for."""
    kept: tuple[Attachment, ...] = ()
    if parameters.keep_attachments:
        if not parameters.keep_folder or not parameters.keep_message:
            raise UsageError("Keeping attachments needs the stored message they are in")
        if any(not digits(position) for position in parameters.keep_attachments):
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


def outgoing_message(mailbox: Mailbox, parameters: SendParameters) -> OutgoingMessage:
    """The message as written: from the account's own address, with the files and kept attachments it names."""
    settings = mailbox.settings
    return OutgoingMessage(
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


MAXIMUM_DELAY_SECONDS: int = 999


def _replies_to(parameters: SendParameters) -> str | None:
    """A reply: the message it answers ("Inbox/<id>"), to be marked as replied once the reply is sent."""
    if parameters.mark_replied and parameters.source_folder and parameters.source_message:
        return f"{parameters.source_folder}/{_check_id(parameters.source_message)}"
    return None


def _note_replies_to(mailbox: Mailbox, reference: MessageRef, source: str | None) -> None:
    try:
        mailbox.store.properties.change(reference.store_id, lambda current: replace(current, replies_to=source))
    except ChronoMailError:
        pass  # the message is safe in the Outbox; only the replied mark of its original would be missed


def _refuse_a_time_sent(mailbox: Mailbox, replaced: MessageRef | None, when: int) -> None:
    """Hold the send locks. A repeating message stays in Scheduled after each send: what replaces it may not start at a time it sent a moment
    ago (a check sent it while it was being changed; the new one would send that time again, D-262)."""
    if replaced is None or replaced.folder_path != SCHEDULED:
        return
    try:
        notes = dict(parse_message(mailbox.store.read(replaced, hint=mailbox.hint(replaced.store_id))).chrono_headers)
    except ChronoMailError:
        return  # gone or unreadable: taking it out says so (_settle)
    repeat = repeat_from_text(notes[REPEAT_HEADER]) if notes.get("x-chrono-mail-kind") == "recurring" and REPEAT_HEADER in notes else None
    if repeat is not None and repeat.last_sent and when <= repeat.last_sent + TOLERANCE_SECONDS:
        raise UsageError("This repeating message was sent at its time a moment ago: nothing was changed. Open it again to change the next sends")


@contextmanager
def _send_locks(engine: Engine, replaced: MessageRef | None) -> Generator[None]:
    """The Outbox lock; first the repeat lock too when what is stored replaces a scheduled message: a repeating send holds it from writing
    its timetable to its send, so it never sends a pattern that was replaced meanwhile (D-262)."""
    if replaced is not None and replaced.folder_path == SCHEDULED:
        with REPEAT_LOCK, engine.outbox_lock:
            yield
    else:
        with engine.outbox_lock:
            yield


def _settle(mailbox: Mailbox, replaced: MessageRef | None) -> None:
    """Hold engine.outbox_lock, after what replaces it is stored: an Outbox message it replaces goes now (refused when a send took it); for a
    draft it replaces, whether a send took that meanwhile is asked once more (dragged to Drafts while it was being sent)."""
    if replaced is None:
        return
    if replaced.folder_path in (OUTBOX, SCHEDULED):  # a send may take it: it goes in this locked step
        mailbox.take_out_of_outbox(replaced)
    else:
        mailbox.refuse_if_sent(replaced)


def _put_in_outbox(engine: Engine, mailbox: Mailbox, outgoing: OutgoingMessage, parameters: SendParameters, replaced: MessageRef | None) -> SendResult:
    """A delayed send (B-21): a verified copy waits in the Outbox, with the moment before which it is not sent; nothing is sent here.
    Send now or the end of its countdown sends it (mail.send-outbox with the message), else the next send of the Outbox does."""
    if not 0 <= parameters.delay_seconds <= MAXIMUM_DELAY_SECONDS:
        raise UsageError(f"A delay is 0 to {MAXIMUM_DELAY_SECONDS} seconds")
    not_before = int(engine.clock()) + parameters.delay_seconds if parameters.delay_seconds else None
    source = _replies_to(parameters)
    with _send_locks(engine, replaced):  # no send of the Outbox sees the message before its waiting time is written
        reference = mailbox.deliver(OUTBOX, build_message(outgoing, draft=True), frozenset({MessageFlag.SEEN}))
        try:  # its waiting time first, then what it replaces goes: a failure of either leaves only what was there before
            if not_before is not None or source is not None:
                mailbox.store.properties.change(reference.store_id, lambda current: replace(current, send_not_before=not_before, replies_to=source))
            _settle(mailbox, replaced)  # after the new one is safe, in the same locked step: the two never wait together
        except ChronoMailError as error:
            mailbox.drop_replacement(reference, error)  # without its waiting time it would go at the next refresh, beside a Send pressed again
            raise
    if not_before is not None:
        mailbox.tell_changed(reference)  # the row shows when it may go
    if parameters.replaces_folder and parameters.replaces_message and (replaced is None or replaced.folder_path not in (OUTBOX, SCHEDULED)):
        try:
            mailbox.delete(mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message)), permanent=True)
        except ChronoMailError:
            pass  # the Outbox copy holds the content now
    return SendResult(False, reference.store_id, "/".join(OUTBOX), (), None, None, waiting=True)


def _schedule(engine: Engine, mailbox: Mailbox, outgoing: OutgoingMessage, parameters: SendParameters, replaced: MessageRef | None) -> SendResult:
    """Keep the message in the Scheduled folder, marked with the time it is to be sent (mail.send-due sends it then)."""
    when = parse_when(str(parameters.send_at), engine.clock())
    repeating = parameters.repeat_months or parameters.repeat_weeks or parameters.repeat_days
    if parameters.repeat_until and not repeating:
        raise UsageError("An end time needs an interval (repeat_days, repeat_weeks or repeat_months)")
    repeat = (
        Repeat(when, parse_when(parameters.repeat_until, engine.clock()) if parameters.repeat_until else None, parameters.repeat_months, parameters.repeat_weeks, parameters.repeat_days)
        if repeating else None
    )
    notes: list[tuple[str, str]] = [("X-Chrono-Mail-Kind", "recurring" if repeat else "scheduled"), ("X-Chrono-Mail-Send-At", str(when))]
    if repeat is not None:  # the message carries its whole timetable: it can never be separated from it
        notes.append(("X-Chrono-Mail-Repeat", repeat_text(repeat)))
    if parameters.source_folder and parameters.source_message:
        notes.append(("X-Chrono-Mail-Source", f"{parameters.source_folder}/{_check_id(parameters.source_message)}"))
        if parameters.mark_replied:
            notes.append(("X-Chrono-Mail-Mark-Replied", "1"))
    scheduled = replace(outgoing, extra_headers=tuple(notes))
    waiting, _ = mailbox.ensure_folder(SCHEDULED)  # made the first time it is used, like Snoozed and Archive; local only
    with _send_locks(engine, replaced):  # the mailbox before the locks, as every send takes them; a due send waits for it too (it takes them)
        _refuse_a_time_sent(mailbox, replaced, when)
        reference = mailbox.deliver(waiting.path, build_message(scheduled, draft=True), frozenset({MessageFlag.SEEN}))  # never on a server
        try:
            _settle(mailbox, replaced)  # after the new one is safe, in the same locked step: the two never wait together
        except ChronoMailError as error:
            mailbox.drop_replacement(reference, error)
            raise
    if parameters.replaces_folder and parameters.replaces_message and (replaced is None or replaced.folder_path not in (OUTBOX, SCHEDULED)):
        try:
            mailbox.delete(mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message)), permanent=True)
        except ChronoMailError:
            pass  # the scheduled message holds the content now
    return SendResult(False, reference.store_id, SCHEDULED_FOLDER, (), None, None, describe_when(when))


@talks_to_servers  # a restore waits for it and holds it back meanwhile (RV-60)
def mail_send(engine: Engine, parameters: SendParameters) -> SendResult:
    if not (parameters.to or parameters.cc or parameters.bcc):
        raise UsageError("There is no recipient")
    with open_mailbox(engine, parameters.account) as mailbox:
        replaced = _replaced(mailbox, parameters)
        if replaced is not None and mailbox.sent_already(replaced):  # the server took it: what replaces it would be sent a second time
            raise SentAlready("The message this replaces was sent already; nothing was sent")  # never "try again" in the window
        return _send(engine, mailbox, parameters, replaced)


def _replaced(mailbox: Mailbox, parameters: SendParameters) -> MessageRef | None:
    """The draft or Outbox message a send replaces (the folder as the mailbox reads it, in any letter case); None when there is none."""
    if not (parameters.replaces_folder and parameters.replaces_message):
        return None
    try:
        return mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message))
    except ChronoMailError:
        return None  # its folder is gone: nothing is replaced


def _send(engine: Engine, mailbox: Mailbox, parameters: SendParameters, replaced: MessageRef | None) -> SendResult:
    """Send, schedule or put in the Outbox, in the open mailbox. An Outbox message this replaces: under the Outbox lock the
    new one is stored first, then the old one goes (Mailbox.take_out_of_outbox), and when a send took it meanwhile the new one goes again
    and nothing is sent (D-262): never sent twice, and never the old one lost before the new one is safe (its kept attachments with it).
    replaced: the draft or Outbox message this replaces; whether a send took it meanwhile is asked again under the lock (_settle)."""
    settings = mailbox.settings
    password = engine.passwords.get(settings.account.account_id, ServerRole.OUTGOING)
    if password is None:
        raise ConfigurationError("No password is stored for this account")
    outgoing = outgoing_message(mailbox, parameters)
    if (parameters.repeat_months or parameters.repeat_weeks or parameters.repeat_days or parameters.repeat_until) and not parameters.send_at:
        raise UsageError("Repeating needs the time of the first send (send_at)")
    if parameters.send_at:
        return _schedule(engine, mailbox, outgoing, parameters, replaced)
    if parameters.delay_seconds or parameters.hold:
        return _put_in_outbox(engine, mailbox, outgoing, parameters, replaced)
    with _send_locks(engine, replaced):  # its copy in the Outbox is sent by this send only: a refresh meanwhile waits, never sends it too (RV-109)
        outcome = send_and_file(mailbox.store, mailbox.index, settings.account.outgoing, password, outgoing, OUTBOX, mailbox.role_folder(FolderRole.SENT),
            ssl_context=engine.tls_context(settings, outgoing=True),
            before_send=lambda: _settle(mailbox, replaced),  # after the new copy
            account=settings.account.account_id,  # recorded with it in Sent (D-292)
        )  # is stored, before the server is asked, all under the lock: the old one never goes before the new one is safe (as RV-127)
        if outcome.sent and outcome.reference.folder_path == OUTBOX:
            engine.sent_unfiled.add(outcome.reference.store_id)  # a failed move to Sent: never sent again in this run, also without a mark on the disk
        if not outcome.sent and _replies_to(parameters) is not None:  # sent again later from the Outbox: the original is marked then
            _note_replies_to(mailbox, outcome.reference, _replies_to(parameters))
    told = mailbox.told_after_send(outcome.reference, outcome.sent)  # never raises: a send the server took is never told as failed
    if outcome.sent and parameters.mark_replied and parameters.source_folder and parameters.source_message:
        try:
            source = mailbox.reference(mailbox.folder(parameters.source_folder), _check_id(parameters.source_message))
            mailbox.set_flags(source, frozenset({*mailbox.current_flags(source), MessageFlag.REPLIED}))
            learn(mailbox, [source], junk=False)  # mail one replies to is good mail (D-238)
        except ChronoMailError:
            pass  # the reply was sent; failing to mark the original is not worth an error
    if parameters.replaces_folder and parameters.replaces_message and (replaced is None or replaced.folder_path not in (OUTBOX, SCHEDULED)):  # sent, or kept in the Outbox to be sent later:
        try:  # the draft became that message (an Outbox message it replaces went before it was sent)
            old = mailbox.reference(mailbox.folder(parameters.replaces_folder), _check_id(parameters.replaces_message))
            mailbox.delete(old, permanent=True)
        except ChronoMailError:
            pass
    return SendResult(
        outcome.sent,
        outcome.reference.store_id,
        "/".join(outcome.reference.folder_path),
        tuple(f"{item.address}: {item.code} {item.text}" for item in outcome.refused),
        outcome.error,
        "; ".join(text for text in (outcome.warning, told) if text),
        certificate=None if outcome.certificate is None else replace(outcome.certificate, account=settings.account.account_id),
    )


# --- reading ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(default="Inbox", metadata=option("The folder, for example Inbox or Archive/2026"))
    sort: ListSort = field(
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
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        folder = mailbox.folder(parameters.folder)
        total = mailbox.index.count(folder)
        times = mailbox.timed(folder)
        if times is not None and _snooze_sort(parameters.sort, folder):
            everything = mailbox.index.page(folder, SortField.DATE, True, 0, total, parameters.unread_only)
            rows = _by_snooze(everything, times, parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.sort == CONTACT_SORT:
            everything = mailbox.index.page(folder, SortField.DATE, True, 0, total, parameters.unread_only)
            rows = _by_contact(engine, everything, not parameters.oldest_first, shows_recipients(mailbox, folder))[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.tag:
            only = SearchFilter(unread=True if parameters.unread_only else None, tag=parameters.tag)
            rows = mailbox.index.search("", folder, only, parameters.offset, parameters.limit, _sort_field(parameters.sort), not parameters.oldest_first)
        else:
            rows = mailbox.index.page(folder, _sort_field(parameters.sort), not parameters.oldest_first, parameters.offset, parameters.limit, parameters.unread_only)
        return ListResult("/".join(folder), total, mailbox.index.count(folder, unread_only=True), _rows(mailbox, rows, times))


@dataclass(frozen=True, slots=True)
class PositionParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder, for example Inbox or Archive/2026"))
    message: str = field(metadata=option("The message id (as shown by message.list)"))
    sort: ListSort = field(
        default="date", metadata=option("The sort order, as for message.list")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))


@dataclass(frozen=True, slots=True)
class PositionResult:
    position: int | None  # 0 is the first message of the folder in that order; None when the message is not in the folder


def message_position(engine: Engine, parameters: PositionParameters) -> PositionResult:
    """Where a message stands in a folder in the order message.list gives it."""
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        folder = mailbox.folder(parameters.folder)
        if _special_order(parameters.sort, folder):
            ordered = _ordered_ids(engine, mailbox, folder, parameters.sort, parameters.oldest_first)
            return PositionResult(next((number for number, message_id in enumerate(ordered) if message_id == parameters.message), None))
        return PositionResult(mailbox.index.position(folder, parameters.message, _sort_field(parameters.sort), not parameters.oldest_first))


def _special_order(sort: str, folder: tuple[str, ...]) -> bool:
    """Whether a folder's order in this sort is worked out here (snoozed until, contact) rather than by the index."""
    return _snooze_sort(sort, folder) or sort == CONTACT_SORT


def _ordered_ids(engine: Engine, mailbox: Mailbox, folder: tuple[str, ...], sort: str, oldest_first: bool) -> list[str]:
    """The ids of every message of a folder in the order message.list gives them."""
    if not _special_order(sort, folder):
        return [store_id for store_id, _, _ in mailbox.index.ordered(folder=folder, sort=_sort_field(sort), descending=not oldest_first)]
    times = mailbox.timed(folder)
    everything = mailbox.index.page(folder, SortField.DATE, True, 0, mailbox.index.count(folder))
    ordered = _by_snooze(everything, times, oldest_first) if times is not None and sort != CONTACT_SORT else _by_contact(engine, everything, not oldest_first, shows_recipients(mailbox, folder))
    return [message.store_id for message in ordered]


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
    position: int = -1  # its place in message.show's attachments (to open or save it), -1: unknown


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
    pictures: tuple[InlineImage, ...] = ()  # attached pictures the text does not show, in the message's order: shown below it (B-11)


def _check_id(message_id: str) -> str:
    if len(message_id) != 32 or any(character not in "0123456789abcdef" for character in message_id):
        raise UsageError("A message id is 32 lower-case hexadecimal characters")
    return message_id


def _shown(addresses: tuple[Address, ...]) -> tuple[str, ...]:
    return tuple(written_address(a.name, a.address) for a in addresses)  # read back by the window: a name with a comma or an @ in quotes


def _inline_images(parsed: ParsedMessage) -> tuple[tuple[InlineImage, ...], tuple[InlineImage, ...]]:
    """The pictures to embed in the page as data, small enough, together within one limit: those the HTML refers to with cid:, and the
    attached pictures the text does not show, in the message's order, to be shown below it (B-11); a larger one stays an attachment only."""
    referenced: list[InlineImage] = []
    below: list[InlineImage] = []
    total = 0
    for position, attachment in enumerate(parsed.attachments):
        shown_below = not attachment.inline and attachment.content_type in SHOWN_PICTURE_TYPES
        if not (attachment.content_id or shown_below) or not attachment.content_type.startswith("image/") or len(attachment.data) > MAXIMUM_INLINE_IMAGE_BYTES:
            continue
        total += len(attachment.data)
        if total > MAXIMUM_INLINE_IMAGES_TOTAL_BYTES:
            break
        data = base64.b64encode(attachment.data).decode("ascii")
        if attachment.content_id:
            referenced.append(InlineImage(attachment.content_id.strip("<>"), attachment.content_type, data, position))
        if shown_below:
            below.append(InlineImage((attachment.content_id or "").strip("<>"), attachment.content_type, data, position))
    return tuple(referenced), tuple(below)


def message_show(engine: Engine, parameters: MessageParameters) -> ShowResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        reference = mailbox.reference(folder, _check_id(parameters.message))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        parsed = parse_message(raw)
        referenced, below = _inline_images(parsed)
        return ShowResult(
            reference.store_id,
            "/".join(folder),
            flag_names(mailbox.current_flags(reference)),
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
            referenced,
            _shown(parsed.reply_to_addresses),
            below,
        )


@dataclass(frozen=True, slots=True)
class SearchParameters:
    account: str = field(metadata=option("The account"))
    query: str = field(default="", metadata=option("Words to find; from:, to:, subject: and body: limit a word to one field; tag:name finds a tag"))
    folder: str | None = field(default=None, metadata=option("Only in this folder"))
    subfolders: bool = field(default=False, metadata=option("With folder: also in every folder inside it"))
    unread: bool | None = field(default=None, metadata=option("Only unread (true) or only read (false) messages"))
    flagged: bool | None = field(default=None, metadata=option("Only flagged (true) or only unflagged (false) messages"))
    has_attachments: bool | None = field(default=None, metadata=option("Only messages with (true) or without (false) attachments"))
    date_from: str | None = field(default=None, metadata=option("Only messages from this day on (YYYY-MM-DD, your local time)"))
    date_to: str | None = field(default=None, metadata=option("Only messages up to and including this day (YYYY-MM-DD)"))
    minimum_size: int | None = field(default=None, metadata=option("Only messages of at least this many bytes"))
    maximum_size: int | None = field(default=None, metadata=option("Only messages of at most this many bytes"))
    sent_from: str | None = field(default=None, metadata=option("Only messages sent from this day on, by their own date (YYYY-MM-DD)"))
    sent_to: str | None = field(default=None, metadata=option("Only messages sent up to and including this day, by their own date (YYYY-MM-DD)"))
    snoozed_from: str | None = field(default=None, metadata=option("Only snoozed messages that come back from this day on (YYYY-MM-DD)"))
    snoozed_to: str | None = field(default=None, metadata=option("Only snoozed messages that come back up to and including this day (YYYY-MM-DD)"))
    send_at_from: str | None = field(default=None, metadata=option("Only messages waiting to be sent from this day on (YYYY-MM-DD)"))
    send_at_to: str | None = field(default=None, metadata=option("Only messages waiting to be sent up to and including this day (YYYY-MM-DD)"))
    tag: str | None = field(default=None, metadata=option("Only messages with this tag (or type tag:name in the words)"))
    conversations: bool = field(default=False, metadata=option("Show only the newest message of each conversation (not together with words to find)"))
    sort: ListSort = field(
        default="date", metadata=option("The sort order (snoozed: when it comes back, only in the Snoozed folder; unread ... contact: yes first unless oldest_first)")
    )
    oldest_first: bool = field(default=False, metadata=option("Oldest (or smallest, or A first) at the top"))
    offset: int = field(default=0, metadata=option("How many results to skip (not for message.groups)"))
    limit: int = field(default=50, metadata=option("How many results to show (not for message.groups)"))
    stay: tuple[str, ...] = field(default=(), metadata=option("A message that stays although it no longer matches unread, flagged or tag: it was changed while shown (repeat for more)"))


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
    moment = datetime.combine(day, time.max if end else time.min, tzinfo=local_zone())  # the named zone: never the platform's conversion
    try:  # (Windows refuses one before 1970); a day at the edge of the calendar has no moment in every zone
        moment.timestamp()
    except (OverflowError, ValueError, OSError):
        raise UsageError(f"{name} must be a date between 0002-01-01 and 9998-12-31") from None
    return moment


def timed_only(
    mailbox: Mailbox, snoozed: tuple[str | None, str | None], send_at: tuple[str | None, str | None],
) -> frozenset[str] | None:
    """The messages that come back (snoozed) or are to be sent (Scheduled, a delayed send in the Outbox) within these days, or None
    when no such days are asked; both asked: those that match both (D-288)."""
    found: frozenset[str] | None = None
    for (first, last), name, times in (
        (snoozed, "snoozed", lambda: mailbox.snoozed()),
        (send_at, "send_at", lambda: {**(mailbox.timed((SCHEDULED_FOLDER,)) or {}), **(mailbox.timed(OUTBOX) or {})}),
    ):
        start, end = _day(first, f"{name}_from", end=False), _day(last, f"{name}_to", end=True)
        if start is None and end is None:
            continue
        low, high = start.timestamp() if start else float("-inf"), end.timestamp() if end else float("inf")
        within = frozenset(store_id for store_id, moment in times().items() if low <= moment <= high)
        found = within if found is None else found & within
    return found


def _search_filter(parameters: SearchParameters, mailbox: Mailbox) -> SearchFilter:
    return SearchFilter(
        unread=parameters.unread, flagged=parameters.flagged, has_attachments=parameters.has_attachments,
        date_from=_day(parameters.date_from, "date_from", end=False), date_to=_day(parameters.date_to, "date_to", end=True),
        minimum_size=parameters.minimum_size, maximum_size=parameters.maximum_size, newest_per_thread=parameters.conversations, tag=parameters.tag,
        stay=frozenset(parameters.stay), subfolders=parameters.subfolders and parameters.folder is not None,
        sent_from=_day(parameters.sent_from, "sent_from", end=False), sent_to=_day(parameters.sent_to, "sent_to", end=True),
        only=timed_only(mailbox, (parameters.snoozed_from, parameters.snoozed_to), (parameters.send_at_from, parameters.send_at_to)),
    )


def message_search(engine: Engine, parameters: SearchParameters) -> tuple[MessageRow, ...]:
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        times = mailbox.timed(folder)
        if times is not None and _snooze_sort(parameters.sort, folder) and folder is not None:
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters, mailbox), 0, EVERYTHING if parameters.subfolders else mailbox.index.count(folder), SortField.DATE, True)
            found = _by_snooze(everything, times, parameters.oldest_first)[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        elif parameters.sort == CONTACT_SORT:
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters, mailbox), 0, EVERYTHING, SortField.DATE, True)
            found = _by_contact(engine, everything, not parameters.oldest_first, shows_recipients(mailbox, None if parameters.subfolders else folder))[max(0, parameters.offset):max(0, parameters.offset) + max(0, parameters.limit)]
        else:
            found = mailbox.index.search(parameters.query, folder, _search_filter(parameters, mailbox), parameters.offset, parameters.limit, _sort_field(parameters.sort), not parameters.oldest_first)
        return _rows(mailbox, found, times)


def message_groups(engine: Engine, parameters: SearchParameters) -> tuple[GroupCount, ...]:
    """The groups of a folder or a search, in the order of message.search (or message.list) with the same options, each with its number of messages."""
    if parameters.sort == CONTACT_SORT:
        return ()  # a yes/no sort has no group headers
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        folder = mailbox.folder(parameters.folder) if parameters.folder else None
        if folder is not None and _snooze_sort(parameters.sort, folder):
            times, now = mailbox.timed(folder) or {}, datetime.fromtimestamp(engine.clock()).astimezone()
            everything = mailbox.index.search(parameters.query, folder, _search_filter(parameters, mailbox), 0, EVERYTHING if parameters.subfolders else mailbox.index.count(folder), SortField.DATE, True)
            counts: dict[str, int] = {}
            for message in everything:
                key = snooze_group(times.get(message.store_id), now)
                counts[key] = counts.get(key, 0) + 1
            order = SNOOZE_GROUPS if parameters.oldest_first else tuple(reversed(SNOOZE_GROUPS))
            return tuple(GroupCount(key, name, counts[key]) for key, name in order if counts.get(key))
        found = mailbox.index.groups(date_bounds(datetime.now()), parameters.query, folder, _search_filter(parameters, mailbox), _sort_field(parameters.sort), not parameters.oldest_first)
        return tuple(found)


@dataclass(frozen=True, slots=True)
class LocateResult:
    position: int | None  # where the message stands in the view's order (0 is the first); None when it is not in the view
    group_key: str = ""  # the group heading it falls under ("" when the view has no headings in this order)
    group_name: str = ""


def group_of(groups: tuple[GroupCount, ...], position: int) -> tuple[str, str]:
    """The group heading of the message at this position, from the groups in list order with their counts."""
    start = 0
    for group in groups:
        if position < start + group.count:
            return group.key, group.value
        start += group.count
    return ("", "")


@dataclass(frozen=True, slots=True)
class LocateParameters(SearchParameters):
    message: str = field(default="", metadata=option("The message id"))


def message_locate(engine: Engine, parameters: LocateParameters) -> LocateResult:
    """Where a message stands in a folder or a search (as message.list and message.search order it), and its group
    heading. Asked when a message comes into the view a person looks at, so the list can put it at its place. The place and the
    headings are read under one hold of the mailbox's lock: a change between them made the heading wrong."""
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        plain = not parameters.query and parameters.folder and _search_filter(parameters, mailbox) == SearchFilter() and parameters.sort not in ("snoozed", CONTACT_SORT)
        if plain and parameters.folder is not None:
            position = message_position(engine, PositionParameters(parameters.account, parameters.folder, parameters.message, parameters.sort, parameters.oldest_first)).position
        else:
            everything = message_search(engine, replace(parameters, offset=0, limit=EVERYTHING))
            position = next((number for number, row in enumerate(everything) if row.message_id == parameters.message), None)
        if position is None:
            return LocateResult(None)
        key, name = group_of(message_groups(engine, parameters), position)
    return LocateResult(position, key, name)


@dataclass(frozen=True, slots=True)
class PlacesParameters(SearchParameters):
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    headings: bool = field(default=False, metadata=option("Also the group heading each message falls under"))
    heard: tuple[str, ...] = field(default=(), metadata=option("mailbox=number: the last fact the asker heard of a mailbox; messages that came in later are not counted (repeat for more)"))


@dataclass(frozen=True, slots=True)
class PlacesResult:
    positions: tuple[int | None, ...]  # for each message asked, in the same order: where it stands (0 is the first), None when it is not in the view
    group_keys: tuple[str, ...] = ()  # with headings: for each message, the key of its group heading ("" when it has none)
    group_names: tuple[str, ...] = ()
    known: bool = True  # False: what came in after the facts heard is no longer known one by one (the asker fetches the view again)
    before: tuple[str, ...] = ()  # for each message: the message it comes before among those the asker holds ("" at the end, or not in the view)
    before_mailboxes: tuple[str, ...] = ()  # a Favorites view: the mailbox of each of those


def neighbours[K](ordered: Sequence[K], asked: Sequence[K], left_out: Collection[K]) -> list[K | None]:
    """For each message asked: the first message after it in the order that the asker holds (one it asked about itself, or
    one it has not heard of, it does not hold yet), None at the end or when the message is not in the view. A list puts a
    message that came in before that one, so rows that moved meanwhile never make the place wrong (R-15)."""
    wanted = set(asked)
    places = {key: number for number, key in enumerate(ordered) if key in wanted}
    skipped = wanted | set(left_out)
    found: list[K | None] = []
    for key in asked:
        number = places.get(key)
        if number is None:
            found.append(None)
            continue
        number += 1
        while number < len(ordered) and ordered[number] in skipped:
            number += 1
        found.append(ordered[number] if number < len(ordered) else None)
    return found


def with_headings(
    positions: tuple[int | None, ...], groups: tuple[GroupCount, ...], counted: tuple[int | None, ...] | None = None, before: tuple[str, ...] = (), before_mailboxes: tuple[str, ...] = (),
) -> PlacesResult:
    """The places with the group heading of each, worked out from the groups in list order with their counts (counted: the
    places among everything, when the places given leave out messages the asker has not heard of)."""
    found = [group_of(groups, position) if position is not None else ("", "") for position in (positions if counted is None else counted)]
    return PlacesResult(positions, tuple(key for key, _ in found), tuple(name for _, name in found), before=before, before_mailboxes=before_mailboxes)


def not_heard(engine: Engine, given: tuple[str, ...], places: set[str]) -> dict[str, set[str]] | None:
    """For each of these mailboxes, the messages that came in after the last fact the asker heard of it (it cannot have
    them yet), or None when that is not known any more. Without heard everything counts; with it, a mailbox it names
    nothing of was heard up to 0."""
    if not given:
        return {place: set() for place in places}
    heard: dict[str, int] = {}
    for item in given:
        mailbox, _, number = item.rpartition("=")
        if not mailbox or not digits(number):
            raise UsageError(f"'{item}' is not mailbox=number")
        heard[mailbox] = int(number)
    found: dict[str, set[str]] = {}
    for place in places:
        came = engine.arrived_after(place, heard.get(place, 0))
        if came is None:
            return None
        found[place] = came
    return found


def leave_out(ordered: list[int | None], left_out: list[int]) -> tuple[int | None, ...]:
    """Places among everything, made places among what the asker has: each place less the left-out messages before it."""
    before = sorted(left_out)
    return tuple(None if place is None else place - bisect.bisect_left(before, place) for place in ordered)


def message_places(engine: Engine, parameters: PlacesParameters) -> PlacesResult:
    """Where several messages stand in a folder or a search (as message.list and message.search order them), and for each
    the message it comes before among those the asker holds; the order is worked out once, however many messages are
    asked. Asked when messages come into the view a person looks at. The order and the headings are read under one hold of the
    mailbox's lock: an Undo between them made the headings one off (a held Ctrl+Z put messages under the wrong heading)."""
    with open_mailbox(engine, parameters.account) as mailbox, mailbox.reading():
        place = mailbox.store.account_id
        if parameters.folder is not None and not parameters.query and _search_filter(parameters, mailbox) == SearchFilter():
            ordered = _ordered_ids(engine, mailbox, mailbox.folder(parameters.folder), parameters.sort, parameters.oldest_first)
        else:
            ordered = [row.message_id for row in message_search(engine, replace(parameters, offset=0, limit=EVERYTHING))]
        shown = set(ordered)
        groups = message_groups(engine, parameters) if parameters.headings and any(message in shown for message in parameters.messages) else ()
    missing = not_heard(engine, parameters.heard, {place})  # after the reading: a superset of what it counted
    if missing is None:
        return PlacesResult(tuple(None for _ in parameters.messages), known=False)
    places = {message_id: number for number, message_id in enumerate(ordered)}
    unheard = missing[place] - set(parameters.messages)
    left = [places[message_id] for message_id in unheard if message_id in places]
    everywhere = [places.get(message_id) for message_id in parameters.messages]
    before = tuple(found or "" for found in neighbours(ordered, parameters.messages, unheard))
    positions = leave_out(everywhere, left)
    if not parameters.headings or all(position is None for position in positions):
        return PlacesResult(positions, before=before)
    return with_headings(positions, groups, tuple(everywhere), before)


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
        wanted = flags_wanted(mailbox.current_flags(reference), parameters.seen, parameters.flagged, parameters.replied)
        return FlagResult(reference.store_id, flag_names(mailbox.set_flags(reference, wanted)))


def flags_wanted(current: frozenset[MessageFlag], seen: bool | None, flagged: bool | None, replied: bool | None) -> frozenset[MessageFlag]:
    """A message's marks with these changed (None: as they are)."""
    flags = set(current)
    for flag, wanted in ((MessageFlag.SEEN, seen), (MessageFlag.FLAGGED, flagged), (MessageFlag.REPLIED, replied)):
        if wanted is True:
            flags.add(flag)
        elif wanted is False:
            flags.discard(flag)
    return frozenset(flags)


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
        learn_from_move(mailbox, reference.folder_path, [moved])  # into or out of Junk teaches the junk filter (D-238)
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
    addressed = {address.address.lower() for address in (*parsed.to_addresses, *parsed.cc_addresses)} | set(delivery_addresses(raw))
    found = receiving_account(engine.config().accounts, own_place(opened), addressed)
    return found.account.account_id if found is not None else None


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


MESSAGE_SHOW: TypedCommand[MessageParameters, ShowResult] = TypedCommand("message.show", MessageParameters, ShowResult)
MESSAGE_THREAD: TypedCommand[MessageParameters, tuple[MessageRow, ...]] = TypedCommand("message.thread", MessageParameters, tuple)  # every element is a MessageRow
MESSAGE_ATTACHMENT_SAVE: TypedCommand[AttachmentSaveParameters, AttachmentSaveResult] = TypedCommand("message.attachment.save", AttachmentSaveParameters, AttachmentSaveResult)
MESSAGE_COMPOSE: TypedCommand[ComposeParameters, ComposeResult] = TypedCommand("message.compose", ComposeParameters, ComposeResult)
MAIL_SEND: TypedCommand[SendParameters, SendResult] = TypedCommand("mail.send", SendParameters, SendResult)
MESSAGE_DELETE: TypedCommand[DeleteParameters, DeleteResult] = TypedCommand("message.delete", DeleteParameters, DeleteResult)
MAIL_FETCH: TypedCommand[AccountParameters, FetchResult] = TypedCommand("mail.fetch", AccountParameters, FetchResult)
MAIL_SETTLE: TypedCommand[SettleParameters, FetchResult] = TypedCommand("mail.settle", SettleParameters, FetchResult)
MESSAGE_PLACES: TypedCommand[PlacesParameters, PlacesResult] = TypedCommand("message.places", PlacesParameters, PlacesResult)
MESSAGE_MOVE: TypedCommand[TransferParameters, TransferResult] = TypedCommand("message.move", TransferParameters, TransferResult)


MESSAGE_SEARCH: TypedCommand[SearchParameters, tuple[MessageRow, ...]] = TypedCommand("message.search", SearchParameters, tuple)  # every element is a MessageRow


def register(registry: CommandRegistry) -> None:
    registry.register(MAIL_FETCH.id, "Download new messages from the server (POP3), or synchronise with it (IMAP)", AccountParameters, mail_fetch, doing="Checking for new mail")  # each change is told as a fact (D-150)
    registry.register(MAIL_SETTLE.id, "Answer for an IMAP folder that a safety rule held back (may delete on the server or here)", SettleParameters, mail_settle, destructive=True, doing="Synchronising a folder")
    registry.register(MAIL_SEND.id, "Send a message (a copy stays in Outbox until it is sent, then moves to Sent)", SendParameters, mail_send, doing="Sending a message")
    registry.register("message.list", "List the messages of a folder", ListParameters, message_list, doing="Loading the messages")
    registry.register("message.position", "Where a message stands in a folder in a sort order (0 is the first)", PositionParameters, message_position, doing="Finding a message in the list")
    registry.register("message.locate", "Where a message stands in a folder or a search, and its group heading", LocateParameters, message_locate, doing="Finding a message in the list")
    registry.register(MESSAGE_PLACES.id, "Where several messages stand in a folder or a search (0 is the first)", PlacesParameters, message_places, doing="Finding messages in the list")
    registry.register(MESSAGE_THREAD.id, "List the messages of the conversation a message belongs to", MessageParameters, message_thread, doing="Loading a conversation")
    registry.register(MESSAGE_SHOW.id, "Show one message", MessageParameters, message_show, doing="Opening a message")
    registry.register(MESSAGE_SEARCH.id, "Search messages", SearchParameters, message_search, doing="Searching")
    registry.register("message.groups", "The groups of a folder or a search (date, size, sender, recipients or subject, as sorted) with their number of messages", SearchParameters, message_groups, doing="Loading the messages")
    registry.register(MESSAGE_ATTACHMENT_SAVE.id, "Save one attachment of a message to a file", AttachmentSaveParameters, message_attachment_save, doing="Saving an attachment")
    registry.register(MESSAGE_COMPOSE.id, "Prepare a reply, a reply to all, or a forward of a message", ComposeParameters, message_compose, doing="Preparing a reply or forward")
    registry.register("message.flag", "Change the read, flagged or replied marks of a message", FlagParameters, message_flag, doing="Changing a message's marks")
    registry.register(MESSAGE_MOVE.id, "Move a message to another folder", TransferParameters, message_move, doing="Moving a message")
    registry.register("message.copy", "Copy a message to another folder", TransferParameters, message_copy, doing="Copying a message")
    registry.register(MESSAGE_DELETE.id, "Move a message to Trash, or delete it for good", DeleteParameters, message_delete, destructive=True, doing="Deleting a message")
