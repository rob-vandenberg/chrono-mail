"""Import from Outlook's data files (.pst): mail, calendars, contacts with their groups, tasks and categories.

After the import everything looks as it did in Outlook. A data file named after the address of a POP3 account here fills that
account's folders (the standard folders by their role); one named after an address without an account here goes into Local
Folders under that name; Outlook's own data file goes into Local Folders itself. Calendars, contacts and tasks always go to
Local Folders. The data file is read in a separate process (chrono_mail.pst.isolated), item by item; the journal of each file
remembers how far it came, so a stopped import continues where it stopped, and what is here already is never stored twice."""

import hashlib
import json
import re
import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.config import load_tags
from chrono_mail.core.jsontext import load_json
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, FolderRole, MessageRef, StorageError
from chrono_mail.core.folder import OWN_FOLDERS
from chrono_mail.engine import commands_addressbook, commands_calendar, commands_tasklist
from chrono_mail.engine.commands_addressbook import AddressesParameters, contact_addresses, contact_import
from chrono_mail.engine.commands_calendar import event_import
from chrono_mail.engine.commands_pim import CreateParameters, KindName, collection_create
from chrono_mail.engine.commands_tags import give_colours
from chrono_mail.engine.commands_tasklist import task_import
from chrono_mail.engine.groups import groups_of_book
from chrono_mail.engine.import_common import MAXIMUM_MESSAGE_BYTES, MAXIMUM_REPORTED, Journal, fingerprint_of_raw, known_copies, still_here
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.outlook_settings import ProfileImport, ProfileScan, import_profile, scan_profile
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.contact import Contact, GroupForm, contact_to_text, member_reference, parse_contacts
from chrono_mail.pim.ical import parse_events, parse_tasks, events_to_text, tasks_to_text
from chrono_mail.pst.model import PstContact, PstError, PstEvent, PstFailure, PstFolder, PstGroup, PstItem, PstMail, PstStore, PstTask
from chrono_mail.store import clean_tags

# --- Version ------------------------------------------------------------
__version__ = "commands_outlook.py 4.2.388"

# --- Version history ----------------------------------------------------
# v4.2.388: the scan says whether an earlier import went through a data file to the end, and which kinds it left out then; again: start
#           over for the chosen files (their journals are discarded; what is here already is not added twice, what was deleted since comes back)
# v4.2.386: Accounts, rules and signatures from Outlook's profile on the Windows computer where it keeps them (outlook_settings); the scan
#           reads the profile and its data files when asked; a data file of a POP3 account the import makes goes into that account
# v4.2.385: Initial version: import.outlook-scan and import.outlook (mail where Outlook had it, calendars, contacts, groups, tasks, categories;
#           in batches, resumable through a journal per data file, nothing stored twice); the review: never into Chrono Mail's own folders,
#           a blank folder name gets one, one bad item fails only itself, an item number given twice keeps what was stored, one journal per
#           file whatever is chosen (what was left out is imported when chosen later), a message of two files is stored once in one run as
#           after each other, the journal only once the batch is safe on disk, empty folders of notes are not mentioned; Tags ticked in a later
#           run gives what was imported before without them its categories, once (each at most once per import, given up when its place
#           is gone, one note per batch); a batch that could not be made safe records what it stored for the retry; the reader's note that
#           not every contact was looked through is told once per file, not once per batch

JOURNAL_FOLDER: str = "import-outlook"  # one journal per data file, in the data directory
PROGRESS_TEXT: str = "Importing from the data files"
DATA_FILE_SUFFIX: str = ".pst"
OFFLINE_FILE_SUFFIX: str = ".ost"  # the copy Outlook keeps of a server's mail: that mail comes from the server itself
_KIND_NAMES: dict[CollectionKind, KindName] = {CollectionKind.EVENTS: "events", CollectionKind.TASKS: "tasks", CollectionKind.CONTACTS: "contacts"}
_ADDRESS = re.compile(r"^[^\s@]+@[^\s@]+$")
_HAS_UID = re.compile(r"^UID[;:]", re.MULTILINE | re.IGNORECASE)
UNNAMED_FOLDER: str = "Unnamed folder"  # a folder whose name is only spaces
OWN_NAME_SUFFIX: str = " (Outlook)"  # a folder of the data file named as one of Chrono Mail's own folders gets a name of its own
_OWN_NAMES: frozenset[str] = frozenset(path[0].casefold() for path in OWN_FOLDERS.values())
_MAPPED_ROLES: frozenset[FolderRole] = frozenset({FolderRole.INBOX, FolderRole.SENT, FolderRole.DRAFTS, FolderRole.TRASH, FolderRole.JUNK, FolderRole.ARCHIVE, FolderRole.OUTBOX})
UNTAGGED: str = "untagged:"  # journal value: an item imported while Tags was off, and where it is, to get its categories when Tags is chosen
STORED: str = "stored:"  # journal value: what a batch that could not be made safe stored for an item (checked on disk before it is used)
NOTED_CONTACTS_PARTIAL: str = "noted: contacts partial"  # journal phase: the file's note that not every contact was looked through was told
SKIPPED: str = "skipped:"  # an item of a kind not chosen: done when that kind is chosen later (SKIPPED + its kind)


# --- the reader (a separate process; the tests put their own data file in its place) --------------------------------------------------


def read_store(path: Path) -> PstStore:
    """The data file's name and folders, read in a separate process (a damaged file never takes the program down)."""
    from chrono_mail.pst import isolated  # loaded when a data file is read: the reader and its library never weigh on the start

    return isolated.store_in_child(path)


def read_items(path: Path, start: int) -> Iterator[tuple[int, PstItem]]:
    """The items of the data file from item number start on, in a stable order, read in a separate process."""
    from chrono_mail.pst import isolated

    return isolated.items_in_child(path, start)


# --- parameters and results -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanParameters:
    files: tuple[str, ...] = field(default=(), metadata=option("A data file (.pst) to import from (repeat)"))
    profile: bool = field(default=False, metadata=option("Also read the accounts, rules, signatures and data files of Outlook's profile on this computer (Windows)"))


@dataclass(frozen=True, slots=True)
class FileScan:
    file: str
    name: str = ""  # the data file's name as Outlook shows it
    problem: str = ""  # why nothing of this file can be imported ("" when it can)
    place: str = ""  # where its mail goes, in words ("" when its mail is not imported)
    mail: int = 0
    events: int = 0
    contacts: int = 0  # contacts and groups (the folders count them together; the import counts them apart)
    tasks: int = 0
    left_out: tuple[str, ...] = ()  # folders of a kind Chrono Mail has no place for, with their counts
    items: int = 0  # everything the file holds (for the progress)
    done: int = 0  # items done by an earlier import of this file
    finished: bool = False  # an earlier import of this very file went through it to the end
    left_out_kinds: tuple[str, ...] = ()  # the kinds (mail, events, contacts, tasks) that earlier import left out: imported when chosen now


@dataclass(frozen=True, slots=True)
class ScanResult:
    files: tuple[FileScan, ...]
    profile: ProfileScan = ProfileScan()

    @property
    def items(self) -> int:
        return sum(scan.items for scan in self.files)

    @property
    def done(self) -> int:
        return sum(scan.done for scan in self.files)


@dataclass(frozen=True, slots=True)
class ImportParameters:
    files: tuple[str, ...] = field(default=(), metadata=option("A data file (.pst) to import from (repeat)"))
    mail: bool = field(default=True, metadata=option("Import the mail"))
    calendar: bool = field(default=True, metadata=option("Import the calendars into Local Folders"))
    contacts: bool = field(default=True, metadata=option("Import the contacts and their groups into Local Folders"))
    tasks: bool = field(default=True, metadata=option("Import the tasks into Local Folders"))
    tags: bool = field(default=True, metadata=option("Import the categories as tags"))
    batch: int = field(default=0, metadata=option("Items per run (0: all); run again to continue"))
    accounts: bool = field(default=False, metadata=option("Import the POP3 and IMAP accounts of Outlook's profile on this computer (Windows), with their passwords"))
    rules: bool = field(default=False, metadata=option("Import Outlook's rules and blocked senders (Windows, while Outlook runs)"))
    signatures: bool = field(default=False, metadata=option("Give the accounts here their signature from Outlook's profile (Windows)"))
    again: bool = field(default=False, metadata=option("Start over for these data files: go through everything again (what is here already is not added twice)"))


@dataclass(frozen=True, slots=True)
class ImportResult:
    mail_imported: int = 0
    mail_existing: int = 0
    mail_failed: int = 0
    events_imported: int = 0
    events_existing: int = 0
    events_failed: int = 0
    contacts_imported: int = 0
    contacts_existing: int = 0
    contacts_failed: int = 0
    groups_imported: int = 0
    groups_existing: int = 0
    groups_failed: int = 0
    tasks_imported: int = 0
    tasks_existing: int = 0
    tasks_failed: int = 0
    unreadable: int = 0  # items the data file could not give
    failures: tuple[str, ...] = ()  # where and why, never the contents; at most MAXIMUM_REPORTED per run
    tags_added: int = 0
    items_tagged: int = 0  # imported earlier while Tags was off; given their categories now
    done: int = 0  # items this run went through
    left: int = 0  # about how many items are still to do (0: the import is complete)
    notes: tuple[str, ...] = ()
    profile: ProfileImport = ProfileImport()


# --- where things go ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Place:
    """Where a data file's mail goes: a mailbox (an account, or Local Folders), the folder at its top, and whether the standard folders go
    onto the mailbox's own standard folders."""

    account: str
    top: tuple[str, ...]
    by_role: bool
    words: str
    mail: bool = True  # False: the mail of an account that keeps it on its server is never imported from a file


def _place(engine: Engine, store: PstStore) -> _Place:
    name = store.display_name.strip()
    if not _ADDRESS.match(name):
        return _Place(LOCAL_ACCOUNT_ID, (), True, "Local Folders")
    for settings in engine.config().accounts:
        account = settings.account
        if account.email_address.strip().lower() != name.lower():
            continue
        if account.kind is AccountKind.POP3:
            return _Place(account.account_id, (), True, f"the account {account.email_address}")
        if account.kind is AccountKind.IMAP:
            return _Place(account.account_id, (), True, "", mail=False)
    return _Place(LOCAL_ACCOUNT_ID, (name,), False, f"Local Folders > {name}")


def _not_own(path: tuple[str, ...]) -> tuple[str, ...]:
    """Imported mail never goes into Chrono Mail's own folders (what is in the Outbox is sent): a folder of the data file at the top with the
    name of one of them gets a name of its own."""
    if path and path[0].casefold() in _OWN_NAMES:
        return (path[0] + OWN_NAME_SUFFIX,) + path[1:]
    return path


class _Folders:
    """Where a mail folder of the data file goes in the mailbox: below a standard folder onto the mailbox's folder with that role (never by
    name, OR-26), the rest by its path; made where missing. The Outbox goes into Drafts: an import never sends."""

    def __init__(self, mailbox: Mailbox, place: _Place, store: PstStore) -> None:
        self.mailbox = mailbox
        self.place = place
        self.roles: dict[tuple[str, ...], FolderRole] = {folder.path: folder.role for folder in store.folders if folder.role in _MAPPED_ROLES}
        self.made: set[tuple[str, ...]] = set()

    def target(self, folder: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(part.strip() or UNNAMED_FOLDER for part in folder)
        if not self.place.by_role:
            path = _not_own(self.place.top + names)
        else:
            length, role = 0, FolderRole.INBOX if not names else None
            for size in range(len(names), 0, -1):
                found = self.roles.get(folder[:size])
                if found is not None:
                    length, role = size, found
                    break
            base: tuple[str, ...] | None = None
            if role is not None:
                try:
                    base = self.mailbox.role_folder(FolderRole.DRAFTS if role is FolderRole.OUTBOX else role)
                except StorageError:
                    pass  # no folder with that role here (Archive): a folder of its own name
            path = base + names[length:] if base is not None else _not_own(names)
        if path not in self.made:
            self.mailbox.ensure_folder(path)
            self.made.add(path)
        return path


# --- the files and the journals ------------------------------------------------------------------


def _file_key(path: Path) -> str:
    status = path.stat()
    return f"{path.resolve()}|{status.st_size}|{status.st_mtime_ns}"


def _journal_path(engine: Engine, path: Path) -> Path:
    name = hashlib.sha256(str(path.resolve()).encode("utf-8", "surrogateescape")).hexdigest()[:32]
    return engine.directories.data_directory / JOURNAL_FOLDER / f"{name}.log"


def _chosen_kinds(parameters: ImportParameters) -> frozenset[str]:
    """The kinds of item chosen, as their skipped outcomes name them."""
    chosen = (("mail", parameters.mail), ("events", parameters.calendar), ("contacts", parameters.contacts), ("tasks", parameters.tasks))
    return frozenset(name for name, on in chosen if on)


def _rank(outcome: str) -> int:
    """Which of two outcomes of one item number counts (the reader may give a number twice: an item, then a failure of a part of it)."""
    if outcome == "failed":
        return 0
    if outcome.startswith(SKIPPED):
        return 1
    if outcome in ("duplicate", "imported"):
        return 2
    return 3  # a store id: what was stored is never forgotten


def _better(earlier: str | None, later: str) -> str:
    return later if earlier is None or _rank(later) >= _rank(earlier) else earlier


def _earlier_journal(engine: Engine, path: Path) -> Journal | None:
    """The journal of an earlier import of this very file, never a new one: a scan changes nothing."""
    journal_path = _journal_path(engine, path)
    try:
        first = load_json(journal_path.read_text("utf-8").split("\n", 1)[0])
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not (isinstance(first, list) and len(first) == 2 and first[0] == "source" and isinstance(first[1], str) and first[1] == _file_key(path)):
        return None
    try:
        return Journal(journal_path, first[1])
    except OSError:
        return None


def _next_number(journal: Journal) -> int:
    return max((int(number) for number in journal.done if number.isdigit()), default=-1) + 1


def _checked_path(text: str) -> Path:
    path = Path(text.strip()).expanduser()
    if path.suffix.lower() == OFFLINE_FILE_SUFFIX:
        raise UsageError("This file holds a copy of the mail of an account that keeps its mail on its server; Chrono Mail takes that mail from the server itself")
    if not path.is_file():
        raise UsageError("The data file is not there")
    return path


def _left_out(store: PstStore) -> tuple[str, ...]:
    return tuple(f"{' > '.join(folder.path)}: {folder.items:,} {folder.unsupported}" for folder in store.folders if folder.unsupported and folder.items > 0)


# --- the scan -------------------------------------------------------------------------------------


def _scan_file(engine: Engine, text: str, coming: frozenset[str] = frozenset()) -> FileScan:
    try:
        path = _checked_path(text)
        store = read_store(path)
    except (UsageError, PstError) as error:
        return FileScan(text, problem=str(error))
    place = _place(engine, store)
    if place.account == LOCAL_ACCOUNT_ID and place.top and place.top[0].lower() in coming:
        place = replace(place, words=f"the account {place.top[0]} when Accounts is chosen (else {place.words})")
    counts: dict[CollectionKind | None, int] = {}
    for folder in store.folders:
        if not folder.unsupported:
            counts[folder.kind] = counts.get(folder.kind, 0) + folder.items
    journal = _earlier_journal(engine, path)
    return FileScan(
        text, store.display_name, "", place.words, counts.get(None, 0) if place.mail else 0, counts.get(CollectionKind.EVENTS, 0), counts.get(CollectionKind.CONTACTS, 0),
        counts.get(CollectionKind.TASKS, 0), _left_out(store), sum(folder.items for folder in store.folders), len(journal.done) if journal is not None else 0,
        journal is not None and "finished" in journal.phases,
        tuple(sorted({outcome[len(SKIPPED):] for outcome in journal.done.values() if outcome.startswith(SKIPPED)})) if journal is not None else (),
    )


def scan(engine: Engine, parameters: ScanParameters) -> ScanResult:
    """What an import of these data files would bring in and where it would go; changes nothing."""
    files = tuple(text for text in parameters.files if text.strip())
    profile = scan_profile(engine) if parameters.profile else ProfileScan()
    if not files:
        files = profile.files
    if not files and not profile.available:
        raise UsageError("Choose a data file (.pst) to import from")
    coming = frozenset(address.lower() for address in profile.coming)
    return ScanResult(tuple(_scan_file(engine, text, coming) for text in files), profile)


# --- one import, kept between its batches ----------------------------------------------------------


@dataclass
class _File:
    path: Path
    key: str
    store: PstStore
    place: _Place


@dataclass
class _Run:
    """What one import keeps between its batches: the data files' folders, the mail that was here before the import, the addresses of the
    contacts, the collections it fills and the files that could not be read this time."""

    key: tuple[str, ...]
    files: list[_File]
    unreadable: dict[str, str]  # file -> why (tried again by a later import)
    known: dict[str, dict[str, list[tuple[str, str]]]] = field(default_factory=dict)  # mailbox -> fingerprint -> (store id, the file it came from or "")
    addresses: set[str] | None = None
    defaults: dict[CollectionKind, str] = field(default_factory=dict)  # Local Folders' own calendar, task list and address book when the import began
    collections: dict[tuple[CollectionKind, str], str] = field(default_factory=dict)  # (kind, name) -> the collection made or found for it
    tags_tried: set[tuple[str, str]] = field(default_factory=set)  # (journal, record) of tags tried by this import: each at most once


_RUN: list[_Run] = []  # the one import that runs (at most one entry)
_RUN_LOCK = threading.Lock()


def _run_for(engine: Engine, texts: tuple[str, ...]) -> _Run:
    paths = [_checked_path(text) for text in texts]
    key = (str(engine.directories.data_directory), *(_file_key(path) for path in paths))
    with _RUN_LOCK:
        if _RUN and _RUN[0].key == key:
            return _RUN[0]
    files: list[_File] = []
    unreadable: dict[str, str] = {}
    for path in paths:
        try:
            store = read_store(path)
        except PstError as error:
            unreadable[str(path)] = str(error)
            continue
        files.append(_File(path, _file_key(path), store, _place(engine, store)))
    run = _Run(key, files, unreadable)
    with _RUN_LOCK:
        _RUN[:] = [run]
    return run


def finished_run() -> None:
    """Forget what the import kept between its batches (it is complete, or the program starts again)."""
    with _RUN_LOCK:
        _RUN.clear()


# --- calendars, contacts and tasks ----------------------------------------------------------------


def _stable_uid(*parts: str) -> str:
    """A uid for an item without one, the same at every import of it: no second copy when it is imported again."""
    return "pst-" + hashlib.sha256("\x00".join(parts).encode("utf-8", "surrogateescape")).hexdigest()[:32]


def _with_categories(own: tuple[str, ...], given: tuple[str, ...]) -> tuple[str, ...]:
    return clean_tags([*own, *given]) if given else own


class _Batch:
    """One run of the import: the counts, the failures and the tags met."""

    def __init__(self, engine: Engine, run: _Run, parameters: ImportParameters) -> None:
        self.engine = engine
        self.run = run
        self.parameters = parameters
        self.counts: dict[str, int] = {}
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.tags: dict[str, None] = {}
        self.number = ""  # the item number being imported
        self.untagged: list[tuple[str, str]] = []  # (journal key, where the item is) of items imported without their categories

    def count(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def failed(self, kind: str, folder: tuple[str, ...], why: str) -> str:
        self.count(f"{kind}_failed" if kind else "unreadable")
        if len(self.failures) < MAXIMUM_REPORTED:
            self.failures.append(f"{' > '.join(folder) or 'the top'}: {why}")
        return "failed"

    def remember_untagged(self, names: tuple[str, ...], part: int, *where: str, always: bool = False) -> None:
        """An item stored while Tags is off (or whose tags could not be given) that has categories: a later run with Tags gives them, once.
        part: which event, task or card of the item (one item may hold several)."""
        if names and (always or not self.parameters.tags):
            self.untagged.append((f"{UNTAGGED}{self.number}:{part}", json.dumps([*where, list(names)])))

    def categories(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """The categories an item gets (none when Tags is not chosen); each becomes a tag at the end of the run."""
        if not self.parameters.tags:
            return ()
        cleaned = clean_tags(names)
        self.tags.update(dict.fromkeys(cleaned))
        return cleaned

    # --- the collections

    def default(self, journal: Journal, kind: CollectionKind) -> str:
        """Local Folders' own calendar, task list or address book: the one the data file's own folder of that kind goes into."""
        remembered = journal.values.get(f"default-{kind.value}")
        with open_pim(self.engine, LOCAL_ACCOUNT_ID) as store:
            present = {info.collection_id: info for info in store.collections(kind) if not info.read_only}
        if remembered is not None and remembered in present:
            return remembered
        chosen = self.run.defaults.get(kind)
        if chosen is None or chosen not in present:
            if not present:
                raise UsageError("Local Folders has no place for this kind of item")
            chosen = next(iter(present))
            self.run.defaults[kind] = chosen
        journal.remember(f"default-{kind.value}", chosen)
        return chosen

    def collection(self, journal: Journal, file: _File, kind: CollectionKind, folder: tuple[str, ...]) -> str:
        """The data file's own folder of a kind goes into Local Folders' own collection; any other into a collection named as the folder,
        made once and found again by its name."""
        record = next((item for item in file.store.folders if item.path == folder), None)
        name = record.path[-1].strip() if record is not None and record.path else ""
        if record is None or record.kind is not kind or record.main or not name:
            return self.default(journal, kind)
        key = (kind, name.casefold())
        known = self.run.collections.get(key)
        if known is not None:
            return known
        with open_pim(self.engine, LOCAL_ACCOUNT_ID) as store:
            found = next((info.collection_id for info in store.collections(kind) if info.name.casefold() == name.casefold() and not info.read_only), None)
        if found is None:
            found = collection_create(self.engine, CreateParameters(kind=_KIND_NAMES[kind], name=name)).collection
        self.run.collections[key] = found
        return found

    def _uid_here(self, collection: str, uid: str) -> bool:
        with open_pim(self.engine, LOCAL_ACCOUNT_ID) as store:
            return store.index.find_uid(collection, uid) is not None

    # --- the items

    def event(self, journal: Journal, file: _File, item: PstEvent) -> str:
        collection = self.collection(journal, file, CollectionKind.EVENTS, item.folder)
        parsed = parse_events(item.ical)
        if not parsed:
            return self.failed("events", item.folder, "an appointment that could not be read")
        own_uid = _HAS_UID.search(item.ical) is not None
        categories = self.categories(item.categories)
        new = 0
        for number, series in enumerate(parsed):
            uid = series.master.uid if own_uid else _stable_uid(item.ical, str(number))
            if self._uid_here(collection, uid):
                continue
            wanted = replace(series, master=replace(series.master, uid=uid, categories=_with_categories(series.master.categories, categories)),
                             overrides=tuple(replace(event, uid=uid) for event in series.overrides))
            single = len(parsed) == 1
            text = item.ical if single and wanted == series else events_to_text(wanted, item.ical if single else None)
            event_import(self.engine, commands_calendar.ImportParameters(collection, text))
            self.remember_untagged(item.categories, number, "events", collection, uid)
            new += 1
        self.count("events_imported" if new else "events_existing")
        return "imported" if new else "duplicate"

    def task(self, journal: Journal, file: _File, item: PstTask) -> str:
        collection = self.collection(journal, file, CollectionKind.TASKS, item.folder)
        parsed = parse_tasks(item.ical)
        if not parsed:
            return self.failed("tasks", item.folder, "a task that could not be read")
        own_uid = _HAS_UID.search(item.ical) is not None
        categories = self.categories(item.categories)
        new = 0
        for number, task in enumerate(parsed):
            uid = task.uid if own_uid else _stable_uid(item.ical, str(number))
            if self._uid_here(collection, uid):
                continue
            wanted = replace(task, uid=uid, categories=_with_categories(task.categories, categories))
            single = len(parsed) == 1
            text = item.ical if single and wanted == task else tasks_to_text(wanted, item.ical if single else None)
            task_import(self.engine, commands_tasklist.ImportParameters(collection, text))
            self.remember_untagged(item.categories, number, "tasks", collection, uid)
            new += 1
        self.count("tasks_imported" if new else "tasks_existing")
        return "imported" if new else "duplicate"

    def _addresses(self) -> set[str]:
        if self.run.addresses is None:
            self.run.addresses = {found.address for found in contact_addresses(self.engine, AddressesParameters())}
        return self.run.addresses

    def contact(self, journal: Journal, file: _File, item: PstContact) -> str:
        """A contact whose address is here already is that contact (as the other import does); one without an address by its uid."""
        book = self.collection(journal, file, CollectionKind.CONTACTS, item.folder)
        parsed = parse_contacts(item.vcard)
        if not parsed:
            return self.failed("contacts", item.folder, "a contact that could not be read")
        known = self._addresses()
        categories = self.categories(item.categories)
        new = 0
        for number, contact in enumerate(parsed):
            emails = {email.value.strip().lower() for email in contact.emails if email.value.strip()}
            uid = contact.uid or _stable_uid(item.vcard, str(number))
            if (emails & known) or self._uid_here(book, uid):
                continue
            if not contact.full_name():
                return self.failed("contacts", item.folder, "a contact without a name")
            wanted = replace(contact, uid=uid, categories=_with_categories(contact.categories, categories))
            result = contact_import(self.engine, commands_addressbook.ImportParameters(book, contact_to_text(wanted)))
            if result.imported + result.updated == 0:
                return self.failed("contacts", item.folder, "a contact that could not be stored")
            known.update(emails)
            self.remember_untagged(item.categories, number, "contacts", book, uid)
            new += 1
        self.count("contacts_imported" if new else "contacts_existing")
        return "imported" if new else "duplicate"

    def group(self, journal: Journal, file: _File, item: PstGroup) -> str:
        """A distribution list becomes a contact group of the address book: members who are its contacts by their card, others by address."""
        name = " ".join(item.name.split())
        if not name:
            return self.failed("groups", item.folder, "a group without a name")
        book = self.collection(journal, file, CollectionKind.CONTACTS, item.folder)
        uid = _stable_uid("group", name, *(address for _, address in item.members))
        items = {found.address: found.item for found in contact_addresses(self.engine, AddressesParameters()) if found.collection == book}
        with open_pim(self.engine, LOCAL_ACCOUNT_ID) as store:
            if any(group.title.casefold() == name.casefold() for group in groups_of_book(store, book)) or store.index.find_uid(book, uid) is not None:
                self.count("groups_existing")
                return "duplicate"
            references: list[str] = []
            for _, address in item.members:
                clean = address.strip().lower()
                if not clean:
                    continue
                indexed = store.index.get(items[clean]) if clean in items else None
                references.append(member_reference(indexed.uid) if indexed is not None and indexed.uid.strip() else f"mailto:{address.strip()}")
        card = Contact(uid=uid, display_name=name, family_name=name, group_form=GroupForm.APPLE, members=tuple(dict.fromkeys(references)))
        contact_import(self.engine, commands_addressbook.ImportParameters(book, contact_to_text(card)))
        self.count("groups_imported")
        return "imported"

    # --- the mail

    def mail(self, mailboxes: "_Mailboxes", journal: Journal, file: _File, item: PstMail) -> str:
        if len(item.raw) > MAXIMUM_MESSAGE_BYTES:
            return self.failed("mail", item.folder, "a message larger than the limit")
        mailbox, folders, known = mailboxes.of(file)
        key = fingerprint_of_raw(item.raw)
        copies = known.get(key, []) if key is not None else []
        stored = journal.values.get(STORED + self.number, "")
        if stored and still_here(mailbox, stored):  # stored by a batch that could not be made safe, and here after all: that copy is this item
            if key is not None and (stored, file.key) not in copies:
                copies.append((stored, file.key))
                known[key] = copies
            self.count("mail_imported")
            return stored
        if any(source != file.key and still_here(mailbox, store_id) for store_id, source in copies):
            self.count("mail_existing")  # here before, or from another data file: never twice; a copy in another folder of this file is kept, as Outlook had it
            return "duplicate"
        reference = mailbox.deliver(folders.target(item.folder), item.raw, item.flags)
        if key is not None:
            copies.append((reference.store_id, file.key))
            known[key] = copies
        tags = self.categories(item.categories)
        if tags:
            try:
                mailbox.set_tags(reference, tags)
            except Exception:  # noqa: BLE001 - the message is stored: it stays imported, and its tags come with the next import
                self.count("tags_later")
                self.remember_untagged(item.categories, 0, "mail", file.place.account, reference.store_id, always=True)
        self.remember_untagged(item.categories, 0, "mail", file.place.account, reference.store_id)
        self.count("mail_imported")
        return reference.store_id


    # --- categories for what was imported before without them

    def give_tags(self, journal: Journal, room: int | None, step: Callable[[], None]) -> int:
        """Items this file brought in while Tags was off get their categories now (the categories as tags, as at the import); each once.
        At most room of them (None: all), each counted by step as an item of the batch, and each tried at most once by one import (one that
        fails is tried again by a later import; one whose place is gone is given up). Returns how many are still waiting in this import."""
        waiting = [
            (key, value) for key, value in journal.values.items()
            if key.startswith(UNTAGGED) and value and (str(journal.path), key) not in self.run.tags_tried
        ]
        for number, (key, value) in enumerate(waiting):
            if room is not None and number >= room:
                return len(waiting) - number
            step()
            self.run.tags_tried.add((str(journal.path), key))
            try:
                where = load_json(value)
                if not (isinstance(where, list) and len(where) == 4 and all(isinstance(part, str) for part in where[:3]) and isinstance(where[3], list)):
                    journal.remember(key, "")  # not readable: nothing to give
                    continue
                names = self.categories(tuple(str(name) for name in where[3]))
                given = self._give(str(where[0]), str(where[1]), str(where[2]), names)
            except Exception:  # noqa: BLE001 - one item that cannot get its tags never stops the import; tried again by a later import
                self.count("tags_failed")
                continue
            journal.remember(key, "")
            self.count("items_tagged" if given else "tags_gone")
        return 0

    def _give(self, kind: str, place: str, ident: str, names: tuple[str, ...]) -> bool:
        """Give an imported item its categories. False: the item, its account or its collection is no longer here (nothing to give, ever)."""
        if kind == "mail":
            if place != LOCAL_ACCOUNT_ID and not any(settings.account.account_id == place for settings in self.engine.config().accounts):
                return False  # the account was removed
            with open_mailbox(self.engine, place) as mailbox:
                row = mailbox.index.get(ident)
                if row is None:
                    return False  # deleted since
                current = mailbox.tags_of(ident)
                wanted = clean_tags([*current, *names])
                if wanted != current:
                    mailbox.set_tags(MessageRef(mailbox.store.account_id, row.folder, ident), wanted)
            return True
        with open_pim(self.engine, LOCAL_ACCOUNT_ID) as store:
            if place not in {info.collection_id for info in store.collections()}:
                return False  # the calendar, task list or address book was deleted
            row = store.index.find_uid(place, ident)
            text = store.get(place, row.item_id) if row is not None else None
        if text is None:
            return False  # deleted since
        if kind == "events":
            parsed = parse_events(text)
            if parsed:
                series = parsed[0]
                changed = replace(series, master=replace(series.master, categories=_with_categories(series.master.categories, names)))
                if changed != series:
                    event_import(self.engine, commands_calendar.ImportParameters(place, events_to_text(changed, text)))
        elif kind == "tasks":
            tasks = parse_tasks(text)
            if tasks and _with_categories(tasks[0].categories, names) != tasks[0].categories:
                changed_task = replace(tasks[0], categories=_with_categories(tasks[0].categories, names))
                task_import(self.engine, commands_tasklist.ImportParameters(place, tasks_to_text(changed_task, text)))
        elif kind == "contacts":
            cards = parse_contacts(text)
            if cards and _with_categories(cards[0].categories, names) != cards[0].categories:
                changed_card = replace(cards[0], categories=_with_categories(cards[0].categories, names))
                contact_import(self.engine, commands_addressbook.ImportParameters(place, contact_to_text(changed_card)))
        return True


class _Mailboxes:
    """The mailboxes a run stores mail in, opened when first needed and kept open (and written together) until the run ends."""

    def __init__(self, engine: Engine, run: _Run, stack: ExitStack, owners: dict[str, str]) -> None:
        self.engine, self.run, self.stack, self.owners = engine, run, stack, owners  # owners: store id -> the data file this import stored it from
        self.open: dict[str, Mailbox] = {}
        self.folders: dict[str, _Folders] = {}

    def of(self, file: _File) -> tuple[Mailbox, _Folders, dict[str, list[tuple[str, str]]]]:
        account = file.place.account
        mailbox = self.open.get(account)
        if mailbox is None:
            mailbox = self.stack.enter_context(open_mailbox(self.engine, account))
            self.stack.enter_context(mailbox.store.written_together())
            self.open[account] = mailbox
        if account not in self.run.known:
            copies = known_copies(mailbox)
            self.run.known[account] = {key: [(store_id, self.owners.get(store_id, "")) for store_id in ids] for key, ids in copies.items()}
        folders = self.folders.get(file.key)
        if folders is None:
            folders = self.folders[file.key] = _Folders(mailbox, file.place, file.store)
        return mailbox, folders, self.run.known[account]


# --- the import -----------------------------------------------------------------------------------


def _one_item(batch: _Batch, mailboxes: _Mailboxes, journal: Journal, file: _File, item: PstItem) -> str:
    """One item imported; whatever goes wrong with it fails that item only (AGENTS.md 4.2.1)."""
    parameters = batch.parameters
    try:
        match item:
            case PstFailure():
                from chrono_mail.pst.reading import CONTACTS_PARTIAL  # loaded with the reader, as read_items does

                if item.what == CONTACTS_PARTIAL:  # a note about the whole file, given by every reading: told once per file
                    if NOTED_CONTACTS_PARTIAL in journal.phases:
                        return "failed"  # the lowest outcome: never takes the place of the item of the same number
                    journal.phase(NOTED_CONTACTS_PARTIAL)
                return batch.failed("", item.folder, item.what)
            case PstMail():
                if not file.place.mail:
                    return SKIPPED + "server"  # never: that mail comes from the account's server
                return batch.mail(mailboxes, journal, file, item) if parameters.mail else SKIPPED + "mail"
            case PstEvent():
                return batch.event(journal, file, item) if parameters.calendar else SKIPPED + "events"
            case PstTask():
                return batch.task(journal, file, item) if parameters.tasks else SKIPPED + "tasks"
            case PstContact():
                return batch.contact(journal, file, item) if parameters.contacts else SKIPPED + "contacts"
            case PstGroup():
                return batch.group(journal, file, item) if parameters.contacts else SKIPPED + "contacts"
    except Exception as error:  # noqa: BLE001 - a bad item never stops the import; only the kind of fault is told, never what the item holds
        kind = {"PstMail": "mail", "PstEvent": "events", "PstTask": "tasks", "PstContact": "contacts", "PstGroup": "groups"}.get(type(item).__name__, "")
        why = str(error) if isinstance(error, (ChronoMailError, OSError)) else f"it could not be imported ({type(error).__name__})"
        return batch.failed(kind, item.folder, why)
    return "failed"


def _close(items: Iterator[tuple[int, PstItem]]) -> None:
    """Ends the reading process of a file read only in part."""
    if isinstance(items, Generator):
        items.close()


def run_import(engine: Engine, parameters: ImportParameters) -> ImportResult:
    """Import from the data files, up to batch items per run (0: all); run again until nothing is left."""
    texts = tuple(text for text in parameters.files if text.strip())
    from_profile = parameters.accounts or parameters.rules or parameters.signatures
    if not texts and not from_profile:
        raise UsageError("Choose a data file (.pst) to import from")
    if not (parameters.mail or parameters.calendar or parameters.contacts or parameters.tasks or from_profile):
        raise UsageError("Choose what to import")
    profile = ProfileImport()
    if from_profile:
        profile = import_profile(engine, parameters.accounts, parameters.rules, parameters.signatures)
        if profile.accounts_added:
            finished_run()  # a data file named after an account made now goes into that account
    if not texts or not (parameters.mail or parameters.calendar or parameters.contacts or parameters.tasks):
        return ImportResult(profile=profile)
    if parameters.again:  # start over: every chosen file is gone through again (the duplicate checks keep what is here from being added twice)
        finished_run()
        for text in texts:
            _journal_path(engine, _checked_path(text)).unlink(missing_ok=True)
    run = _run_for(engine, texts)
    try:
        return replace(_import_run(engine, parameters, run), profile=profile)
    except BaseException:
        finished_run()  # a batch that failed (it may not be safe on disk): Import again rebuilds the mail here from the index and the journals
        raise


def _import_run(engine: Engine, parameters: ImportParameters, run: _Run) -> ImportResult:
    ensure_local_folders(engine)
    if parameters.calendar or parameters.contacts or parameters.tasks:
        ensure_local_pim(engine)
    batch = _Batch(engine, run, parameters)
    journals = {file.key: Journal(_journal_path(engine, file.path), file.key) for file in run.files}  # one per file, whatever is chosen
    chosen = _chosen_kinds(parameters)

    def waiting(outcome: str) -> bool:
        """An item left out earlier whose kind is chosen now."""
        return outcome.startswith(SKIPPED) and outcome[len(SKIPPED):] in chosen

    def final(journal: Journal) -> int:
        return sum(1 for outcome in journal.done.values() if not waiting(outcome))

    total = sum(folder.items for file in run.files for folder in file.store.folders)
    done_before = sum(final(journal) for journal in journals.values())
    owners = {store_id: file.key for file in run.files for store_id in journals[file.key].imported_ids()}
    owners.update((value, file.key) for file in run.files for key, value in journals[file.key].values.items() if key.startswith(STORED) and value)
    room = parameters.batch if parameters.batch > 0 else None
    handled = 0
    left = 0
    for why in run.unreadable.values():
        batch.notes.append(why)
    def step() -> None:
        nonlocal handled
        handled += 1
        engine.progress(PROGRESS_TEXT, done_before + handled, max(total, done_before + handled))

    if parameters.tags:
        for journal in journals.values():
            left += batch.give_tags(journal, None if room is None else max(0, room - handled), step)
    for file in run.files:
        journal = journals[file.key]
        file_items = sum(folder.items for folder in file.store.folders)
        if "noted" not in journal.phases:
            if not file.place.mail and parameters.mail:
                batch.notes.append(f"{file.store.display_name}: its mail comes from its server, so it is taken from there, not from this file")
            batch.notes.extend(f"Not imported (Chrono Mail has no place for them): {text}" for text in _left_out(file.store))
            journal.phase("noted")
        pending = sorted(int(number) for number, outcome in journal.done.items() if number.isdigit() and waiting(outcome))
        if "finished" in journal.phases and not pending:
            continue
        if str(file.path) in run.unreadable:
            continue  # tried again by a later import, not by the next batch of this one
        if room is not None and handled >= room:
            left += max(1, file_items - final(journal))
            continue
        start = min([_next_number(journal), *pending[:1]])
        outcomes: dict[str, str] = {}
        complete = False
        reading_error: PstError | None = None
        try:
            with ExitStack() as stack:
                mailboxes = _Mailboxes(engine, run, stack, owners)
                current: int | None = None
                try:
                    items = read_items(file.path, start)
                    try:
                        for number, item in items:
                            key = str(number)
                            if number != current:
                                earlier = journal.done.get(key)
                                if earlier is not None and key not in outcomes and not waiting(earlier):
                                    continue  # done by an earlier run
                                if room is not None and handled >= room:
                                    break  # the batch ends between two items, never between the parts of one
                                current = number
                                handled += 1
                                batch.number = key
                            outcomes[key] = _better(outcomes.get(key), _one_item(batch, mailboxes, journal, file, item))
                            engine.progress(PROGRESS_TEXT, done_before + handled, max(total, done_before + handled))
                        else:
                            complete = True
                    finally:
                        _close(items)
                except PstError as error:
                    reading_error = error
        except BaseException:
            # the batch could not be made safe on disk: nothing is recorded as done, only what it stored (an item whose copy is here after all
            # is that copy at the retry, checked on disk then) and the tags those copies wait for
            for key, outcome in outcomes.items():
                if _rank(outcome) == 3:
                    journal.remember(STORED + key, outcome)
            for key, where in batch.untagged:
                journal.remember(key, where)
            raise
        # only here, once the mailboxes made the batch safe on disk (an error there leaves the journal as it was: those items are done again)
        journal.messages([(key, _better(journal.done.get(key), outcome)) for key, outcome in outcomes.items()])
        for key, where in batch.untagged:
            journal.remember(key, where)
        batch.untagged.clear()
        journal.finish()
        if reading_error is not None:
            batch.notes.append(f"{file.store.display_name}: {reading_error}")
            run.unreadable[str(file.path)] = str(reading_error)  # not finished: a later import continues from where it stopped
        elif complete:
            journal.phase("finished")
        else:
            left += max(1, file_items - final(journal))
    if batch.tags:
        before = len(load_tags(engine.directories.tags_file).entries)
        give_colours(engine, tuple(batch.tags))
        tags_added = len(load_tags(engine.directories.tags_file).entries) - before
    else:
        tags_added = 0
    counts = batch.counts
    for count, text in (
        ("tags_later", "{} messages are imported; their tags come with the next import"),
        ("tags_failed", "{} imported items could not get their tags yet; a later import tries again"),
        ("tags_gone", "{} imported items are no longer here (or their account or list was removed), so they get no tags"),
    ):
        if counts.get(count, 0):
            batch.notes.append(text.format(counts[count]))
    if left == 0:
        finished_run()
    return ImportResult(
        counts.get("mail_imported", 0), counts.get("mail_existing", 0), counts.get("mail_failed", 0),
        counts.get("events_imported", 0), counts.get("events_existing", 0), counts.get("events_failed", 0),
        counts.get("contacts_imported", 0), counts.get("contacts_existing", 0), counts.get("contacts_failed", 0),
        counts.get("groups_imported", 0), counts.get("groups_existing", 0), counts.get("groups_failed", 0),
        counts.get("tasks_imported", 0), counts.get("tasks_existing", 0), counts.get("tasks_failed", 0),
        counts.get("unreadable", 0), tuple(batch.failures), tags_added, counts.get("items_tagged", 0), handled, left, tuple(batch.notes),
    )


IMPORT_OUTLOOK_SCAN: TypedCommand[ScanParameters, ScanResult] = TypedCommand("import.outlook-scan", ScanParameters, ScanResult)
IMPORT_OUTLOOK: TypedCommand[ImportParameters, ImportResult] = TypedCommand("import.outlook", ImportParameters, ImportResult)


def register(registry: CommandRegistry) -> None:
    registry.register(IMPORT_OUTLOOK_SCAN.id, "What an import from Outlook's data files (.pst) would bring in, and where it would go (changes nothing)", ScanParameters, scan, doing="Reading the data files")
    registry.register(IMPORT_OUTLOOK.id, "Import mail, calendars, contacts, tasks and categories from Outlook's data files (.pst)", ImportParameters, quiet_command(run_import), doing="Importing from the data files")
