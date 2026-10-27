"""Export to Outlook: the mirror of the import. One data file (.pst) per POP3 account, named after its address, with that account's
folders (the standard folders on Outlook's standard folders, by their role); one data file for Local Folders with its mail and every
local calendar, address book and task list (the first of each kind as Outlook's own Calendar, Contacts and Tasks, the others as folders
of their own); IMAP accounts as accounts only (their mail comes from the server). The accounts go into a profile file (.prf) Outlook
imports; the rules are made in Outlook itself (Windows, while Outlook runs). Each data file is written beside its place and moved into
place when complete; a stored item that cannot be read is left out and reported without its contents."""

import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, FolderRole, MessageRef
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.contact import Contact, bare_uid, member_address, member_uid, parse_contacts
from chrono_mail.pim.ical import EventSeries, parse_events, parse_tasks
from chrono_mail.pst.writer import CALENDAR, CONTACTS, DRAFTS, INBOX, JUNK, OUTBOX, SENT, TASKS, TRASH, PstWriter
from chrono_mail.pst.writer_calendar import event_item, task_item
from chrono_mail.pst.writer_contacts import contact_item, group_item
from chrono_mail.pst.writer_mail import Item, mail_item

# --- Version ------------------------------------------------------------
__version__ = "commands_outlook_export.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: export.outlook-choices, export.outlook and export.outlook-stop: data files per POP3 account and for Local
#           Folders written by our own writer, the accounts as a profile file, the rules into Outlook

PROGRESS_TEXT: str = "Exporting to Outlook"
LOCAL_FILE_NAME: str = "Local Folders"
PROFILE_FILE_NAME: str = "Chrono Mail accounts.prf"
MAXIMUM_REPORTED: int = 200
PAGE: int = 500  # messages read from the index at a time: a large mailbox is never held in memory
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024
RULES_HERE: bool = sys.platform == "win32"
RULES_ELSEWHERE: str = "Rules can only be exported on a Windows computer with Outlook open"
_ROLES: dict[FolderRole, str] = {
    FolderRole.INBOX: INBOX, FolderRole.SENT: SENT, FolderRole.DRAFTS: DRAFTS, FolderRole.TRASH: TRASH, FolderRole.JUNK: JUNK, FolderRole.OUTBOX: OUTBOX,
}
_CONTAINERS: dict[CollectionKind, tuple[str, str]] = {
    CollectionKind.EVENTS: ("IPF.Appointment", CALENDAR), CollectionKind.CONTACTS: ("IPF.Contact", CONTACTS), CollectionKind.TASKS: ("IPF.Task", TASKS),
}
_UNSAFE = str.maketrans({character: "_" for character in '<>:"/\\|?*'})

_stop = threading.Event()


# --- parameters and results -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChoicesParameters:
    pass


@dataclass(frozen=True, slots=True)
class AccountChoice:
    account_id: str
    name: str
    kind: str  # pop3, imap or local


@dataclass(frozen=True, slots=True)
class ExportChoices:
    accounts: tuple[AccountChoice, ...]
    rules_available: bool
    rules_reason: str


@dataclass(frozen=True, slots=True)
class ExportParameters:
    folder: str = field(metadata=option("The folder to write the data files (.pst) and the profile file (.prf) in"))
    mail: bool = field(default=True, metadata=option("Export the mail"))
    calendar: bool = field(default=True, metadata=option("Export the calendars of Local Folders"))
    contacts: bool = field(default=True, metadata=option("Export the address books of Local Folders with their groups"))
    tasks: bool = field(default=True, metadata=option("Export the task lists of Local Folders"))
    tags: bool = field(default=True, metadata=option("Export the tags as categories"))
    accounts: bool = field(default=False, metadata=option("Write the POP3 and IMAP accounts to a profile file Outlook imports"))
    rules: bool = field(default=False, metadata=option("Make the rules in Outlook (Windows, while Outlook runs)"))
    account_ids: tuple[str, ...] = field(default=(), metadata=option("An account to export (repeat; none: every account)"))


@dataclass(frozen=True, slots=True)
class ExportResult:
    files: tuple[str, ...] = ()
    messages: int = 0
    events: int = 0
    contacts: int = 0
    groups: int = 0
    tasks: int = 0
    failed: int = 0
    failures: tuple[str, ...] = ()  # where and why, never the contents
    accounts: int = 0
    rules: int = 0
    notes: tuple[str, ...] = ()
    stopped: bool = False


@dataclass(frozen=True, slots=True)
class StopParameters:
    pass


@dataclass(frozen=True, slots=True)
class StopResult:
    stopping: bool = True


class _Stopped(Exception):
    """The person stopped the export: the data file being written is left out."""


@dataclass
class _Counts:
    engine: Engine
    total: int
    done: int = 0
    counted: dict[str, int] = field(default_factory=dict[str, int])
    failures: list[str] = field(default_factory=list[str])
    notes: list[str] = field(default_factory=list[str])

    def step(self, kind: str = "") -> None:
        if _stop.is_set():
            raise _Stopped
        if kind:
            self.counted[kind] = self.counted.get(kind, 0) + 1
        self.done += 1
        if self.done % 50 == 0 or self.done == self.total:
            self.engine.progress(PROGRESS_TEXT, self.done, max(self.total, self.done))

    def failed(self, where: str, what: str) -> None:
        self.counted["failed"] = self.counted.get("failed", 0) + 1
        if len(self.failures) < MAXIMUM_REPORTED:
            self.failures.append(f"{where}: {what}")


# --- the choices ----------------------------------------------------------------------------------


def choices(engine: Engine, parameters: ChoicesParameters) -> ExportChoices:
    """The accounts that can be exported, and whether the rules can be made in Outlook here."""
    ensure_local_folders(engine)
    found: list[AccountChoice] = []
    for settings in engine.config().accounts:
        account = settings.account
        if account.kind in (AccountKind.POP3, AccountKind.IMAP, AccountKind.LOCAL):
            name = "Local Folders" if account.kind is AccountKind.LOCAL else settings.account_name or account.email_address
            found.append(AccountChoice(account.account_id, name, account.kind.value))
    return ExportChoices(tuple(found), RULES_HERE, "" if RULES_HERE else RULES_ELSEWHERE)


# --- writing one data file ------------------------------------------------------------------------


def _file_name(name: str) -> str:
    cleaned = "".join(character for character in name.translate(_UNSAFE) if ord(character) >= 32).strip(" .")
    return (cleaned or "Outlook") + ".pst"


def _written(target: Path, display_name: str, fill: Callable[[PstWriter], None]) -> None:
    """Write a data file beside its place and move it there when complete (never a half-written file under its own name)."""
    temporary = target.with_name(f".{target.name}.{os.urandom(4).hex()}.part")
    writer = PstWriter(temporary, display_name)
    try:
        fill(writer)
        writer.close()
        os.replace(temporary, target)
    except BaseException:
        writer.abandon()
        raise


def _put(writer: PstWriter, folder: int, item: Item) -> None:
    writer.add_item(folder, item.props, item.recipients, item.attachments)


def _mail(engine: Engine, account_id: str, writer: PstWriter, tags: bool, counts: _Counts) -> None:
    """Every folder of the mailbox with its messages, read a page at a time."""
    with open_mailbox(engine, account_id) as mailbox:
        folders = sorted(mailbox.folders(), key=lambda folder: (len(folder.path), folder.path))
        made: dict[tuple[str, ...], int] = {}
        for folder in folders:
            parent = made.get(folder.path[:-1], writer.top) if len(folder.path) > 1 else writer.top
            made[folder.path] = writer.folder(parent, folder.path[-1], "IPF.Note", _ROLES.get(folder.role, ""))
        for folder in folders:
            where = "/".join(folder.path)
            offset = 0
            while True:
                rows = mailbox.index.page(folder.path, offset=offset, limit=PAGE)
                if not rows:
                    break
                offset += len(rows)
                for row in rows:
                    counts.step()
                    try:
                        raw = mailbox.store.read(MessageRef(mailbox.store.account_id, folder.path, row.store_id), hint=row.path)
                        if len(raw) > MAXIMUM_MESSAGE_BYTES:
                            counts.failed(where, "a message larger than 256 MB")
                            continue
                        item = mail_item(raw, row.flags, row.tags if tags else (), row.date, writer.names)
                        _put(writer, made[folder.path], item)
                        counts.counted["messages"] = counts.counted.get("messages", 0) + 1
                    except (ChronoMailError, ValueError, LookupError, UnicodeError, TypeError, AttributeError, IndexError) as error:
                        counts.failed(where, f"a message that could not be exported ({type(error).__name__})")


def _members(contacts: list[Contact], group: Contact) -> list[tuple[str, str]]:
    by_uid = {bare_uid(contact.uid): contact for contact in contacts if contact.uid}
    found: list[tuple[str, str]] = []
    for reference in group.members:
        address = member_address(reference)
        if address:
            found.append(("", address))
            continue
        contact = by_uid.get(bare_uid(member_uid(reference)))
        if contact is not None and contact.emails:
            found.append((contact.full_name(), contact.emails[0].value.strip()))
    return found


def _collections(engine: Engine, writer: PstWriter, parameters: ExportParameters, counts: _Counts) -> None:
    """The calendars, address books and task lists of Local Folders: the first of each kind as Outlook's own folder of that kind."""
    wanted = {CollectionKind.EVENTS: parameters.calendar, CollectionKind.CONTACTS: parameters.contacts, CollectionKind.TASKS: parameters.tasks}
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        for kind, chosen in wanted.items():
            if not chosen:
                continue
            container, role = _CONTAINERS[kind]
            for number, info in enumerate(store.collections(kind)):
                folder = writer.folder(writer.top, info.name.strip() or "Unnamed", container, role if number == 0 else "")
                texts: list[str] = []
                for item_id in store.item_ids(info.collection_id):
                    try:
                        texts.append(store.get(info.collection_id, item_id))
                    except ChronoMailError:
                        counts.step()
                        counts.failed(info.name, "an item that could not be read")
                if kind is CollectionKind.CONTACTS:
                    _contacts(writer, folder, info.name, texts, parameters.tags, counts)
                    continue
                for text in texts:
                    counts.step()
                    try:
                        if kind is CollectionKind.EVENTS:
                            for series in parse_events(text):
                                item, note = event_item(series if parameters.tags else _without_categories(series), writer.names)
                                _put(writer, folder, item)
                                counts.counted["events"] = counts.counted.get("events", 0) + 1
                                if note and note not in counts.notes:
                                    counts.notes.append(note)
                        else:
                            for task in parse_tasks(text):
                                if not parameters.tags:
                                    task = replace(task, categories=())
                                _put(writer, folder, task_item(task, writer.names))
                                counts.counted["tasks"] = counts.counted.get("tasks", 0) + 1
                    except (ChronoMailError, ValueError, LookupError, TypeError, AttributeError, OverflowError) as error:
                        counts.failed(info.name, f"an item that could not be exported ({type(error).__name__})")


def _without_categories(series: EventSeries) -> EventSeries:
    return replace(series, master=replace(series.master, categories=()))


def _contacts(writer: PstWriter, folder: int, name: str, texts: list[str], tags: bool, counts: _Counts) -> None:
    cards: list[Contact] = []
    for text in texts:
        try:
            cards += parse_contacts(text)
        except (ValueError, LookupError, TypeError) as error:
            counts.step()
            counts.failed(name, f"a contact that could not be read ({type(error).__name__})")
    people = [card for card in cards if card.group_form is None]
    for card in cards:
        counts.step()
        try:
            if card.group_form is not None:
                _put(writer, folder, group_item(card.full_name() or card.display_name or "Group", _members(people, card), writer.names))
                counts.counted["groups"] = counts.counted.get("groups", 0) + 1
            else:
                _put(writer, folder, contact_item(card if tags else replace(card, categories=()), writer.names))
                counts.counted["contacts"] = counts.counted.get("contacts", 0) + 1
        except (ValueError, LookupError, TypeError, AttributeError, OverflowError) as error:
            counts.failed(name, f"a contact that could not be exported ({type(error).__name__})")


# --- the export -----------------------------------------------------------------------------------


def _count(engine: Engine, accounts: list[str], parameters: ExportParameters) -> int:
    total = 0
    if parameters.mail:
        for account_id in accounts:
            with open_mailbox(engine, account_id) as mailbox:
                total += sum(mailbox.index.numbers(folder.path)[0] for folder in mailbox.folders())
    if LOCAL_ACCOUNT_ID in accounts and (parameters.calendar or parameters.contacts or parameters.tasks):
        with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
            for info in store.collections():
                total += len(store.item_ids(info.collection_id))
    return total


def _chosen(engine: Engine, parameters: ExportParameters) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(every chosen account, those whose mail goes into a data file, (id, data file name) of each of those)."""
    ensure_local_folders(engine)
    every = [settings for settings in engine.config().accounts if settings.account.kind in (AccountKind.POP3, AccountKind.IMAP, AccountKind.LOCAL)]
    wanted = set(parameters.account_ids)
    chosen = [settings for settings in every if not wanted or settings.account.account_id in wanted]
    files: list[tuple[str, str]] = []
    for settings in chosen:
        account = settings.account
        if account.kind is AccountKind.LOCAL:
            files.append((account.account_id, LOCAL_FILE_NAME))
        elif account.kind is AccountKind.POP3:
            files.append((account.account_id, account.email_address))
    return [settings.account.account_id for settings in chosen], [account_id for account_id, _ in files], files


def run_export(engine: Engine, parameters: ExportParameters) -> ExportResult:
    """Write the chosen accounts as data files, and the accounts and rules when chosen."""
    folder = Path(parameters.folder).expanduser()
    if not folder.is_dir():
        raise UsageError("The folder to export to is not there")
    _stop.clear()
    every, with_files, files = _chosen(engine, parameters)
    counts = _Counts(engine, _count(engine, with_files, parameters))
    written: list[str] = []
    stopped = False
    pim = parameters.calendar or parameters.contacts or parameters.tasks
    try:
        for account_id, name in files:
            local = account_id == LOCAL_ACCOUNT_ID
            if not parameters.mail and not (local and pim):
                continue
            target = folder / _file_name(name)

            def fill(writer: PstWriter, account_id: str = account_id, local: bool = local) -> None:
                if parameters.mail:
                    _mail(engine, account_id, writer, parameters.tags, counts)
                if local and pim:
                    _collections(engine, writer, parameters, counts)

            _written(target, name, fill)
            written.append(str(target))
    except _Stopped:
        stopped = True
    except OSError as error:
        raise UsageError(f"The data file could not be written: {error.strerror}") from None
    finally:
        _stop.clear()
    accounts_done, rules_done = 0, 0
    if parameters.accounts and not stopped:
        accounts_done = _accounts(engine, every, folder / PROFILE_FILE_NAME, counts, written)
    if parameters.rules and not stopped:
        rules_done = _rules(engine, counts)
    engine.progress(PROGRESS_TEXT, counts.done, max(counts.total, counts.done))
    found = counts.counted
    return ExportResult(
        tuple(written), found.get("messages", 0), found.get("events", 0), found.get("contacts", 0), found.get("groups", 0), found.get("tasks", 0),
        found.get("failed", 0), tuple(counts.failures), accounts_done, rules_done, tuple(counts.notes), stopped,
    )


def _accounts(engine: Engine, every: list[str], path: Path, counts: _Counts, written: list[str]) -> int:
    servers = [account_id for account_id in every if account_id != LOCAL_ACCOUNT_ID]
    if not servers:
        counts.notes.append("No POP3 or IMAP account was chosen: no profile file was written")
        return 0
    try:
        from chrono_mail.engine import outlook_profile  # loaded when chosen: the profile's code is only needed for this
    except ImportError:
        counts.notes.append("The accounts cannot be exported by this version")
        return 0
    try:
        counts.notes.extend(outlook_profile.export_accounts_prf(engine, servers, path))
    except ChronoMailError as error:
        counts.notes.append(str(error))
        return 0
    written.append(str(path))
    return len(servers)


def _rules(engine: Engine, counts: _Counts) -> int:
    if not RULES_HERE:
        counts.notes.append(RULES_ELSEWHERE)
        return 0
    try:
        from chrono_mail.engine import outlook_profile
    except ImportError:
        counts.notes.append("The rules cannot be exported by this version")
        return 0
    try:
        made, notes = outlook_profile.export_rules_to_outlook(engine, None)
    except ChronoMailError as error:
        counts.notes.append(str(error))
        return 0
    counts.notes.extend(notes)
    return made


def stop_export(engine: Engine, parameters: StopParameters) -> StopResult:
    """Stop the export that runs: the data file being written is left out; the ones written stay."""
    _stop.set()
    return StopResult()


EXPORT_OUTLOOK_CHOICES: TypedCommand[ChoicesParameters, ExportChoices] = TypedCommand("export.outlook-choices", ChoicesParameters, ExportChoices)
EXPORT_OUTLOOK: TypedCommand[ExportParameters, ExportResult] = TypedCommand("export.outlook", ExportParameters, ExportResult)
EXPORT_OUTLOOK_STOP: TypedCommand[StopParameters, StopResult] = TypedCommand("export.outlook-stop", StopParameters, StopResult)


def register(registry: CommandRegistry) -> None:
    registry.register(EXPORT_OUTLOOK_CHOICES.id, "The accounts that can be exported to Outlook, and whether its rules can be made here", ChoicesParameters, choices, doing="Reading the accounts")
    registry.register(EXPORT_OUTLOOK.id, "Export mail, calendars, contacts, tasks, tags, accounts and rules to Outlook (.pst and .prf files)", ExportParameters, run_export, doing=PROGRESS_TEXT)
    registry.register(EXPORT_OUTLOOK_STOP.id, "Stop the export to Outlook that runs", StopParameters, stop_export, doing="Stopping the export")
