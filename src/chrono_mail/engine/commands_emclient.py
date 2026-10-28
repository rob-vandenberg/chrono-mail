"""Import from eM Client: accounts with their passwords, rules, contacts and all mail, from its three exports.

The mail goes into Local Folders in batches; a list of the files done (the journal) lets a stopped import continue
where it stopped. A message that was already in Local Folders before the import is not stored twice, and the first
check of an imported account records mail that is already here instead of downloading it again (skip_known_mail)."""

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.config import Rule, ServerRole, load_rules, load_tags, new_rule_id, save_rules, save_tags
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.engine.commands_accounts import AccountAddParameters, Policy, account_add
from chrono_mail.engine.commands_tags import give_colours
from chrono_mail.engine.commands_addressbook import AddParameters, AddressesParameters, contact_add, contact_addresses
from chrono_mail.engine.emclient_source import (
    MAXIMUM_MESSAGE_BYTES,
    BackupData,
    MessageFile,
    SourceCategory,
    SettingsExport,
    SourceAccount,
    message_files,
    message_keys,
    read_backup,
    read_settings,
    standard_roles,
)
from chrono_mail.engine.import_common import EVERYTHING, MAXIMUM_REPORTED, PROGRESS_STEP, Journal, contents_fingerprint, fingerprint, fingerprint_of_raw, known_messages, still_here  # EVERYTHING and fingerprint: their names here stay
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.engine.walletlabels import label_account
from chrono_mail.store import clean_tags
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "commands_emclient.py 4.2.395"

# --- Version history ----------------------------------------------------
# v4.2.395: the count of the mail goes to the window every 100 messages and at the last of each batch (the owner)
# v4.2.391: the contents fingerprint of the exports is computed once per import run and kept with the run (not again for every batch);
#           a scan that finds newer exports in the same place forgets the run of the older ones
# v4.2.388: the journal belongs to the exports by their contents (names, sizes and times of their files), not by the folder alone: a newer
#           export in the same place is a new import; the scan says which parts an earlier import of these very exports finished; again:
#           start over (the journal is discarded; what is here already is not added twice, what was deleted since comes back)
# v4.2.387: the export's standard folders are known by the language of all its folder names (emclient_source.standard_roles)
# v4.2.385: the journal is written only when the batch was made safe on disk (an error there no longer records the messages as done), and
#           a failed batch forgets the run, so Import again finds the messages it stored as duplicates; a message whose tags could not be
#           given stays imported (not failed) and gets its tags with a later import with Tags (tried once per import, not per batch);
#           the fingerprint, the mail already here, the journal and the check of a copy on disk moved to import_common (shared with the
#           import from a data file); their names here stay as they were
# v3.6.371: the passwords stored again get readable labels in the wallet (B-23, D-275)
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.271: R-19: typed import.emclient-scan, import.emclient
# v1.2.254: The names of the export's standard folders come from one table (emclient_source.STANDARD_FOLDERS), shared with the rules
# v0.5.155: the import is a big job: one "mailbox changed" at the end of each batch instead of a fact per message (D-146)
# v0.5.144: The imported-account flag is set on a fresh read of the settings, one command at a time (change_config)
# v0.5.143: The progress of the mail counts over the whole import (earlier batches and imports included), not per batch
# v0.5.141: import.emclient-passwords: the passwords of accounts that are here already, from the settings export
# v0.5.137: Tags: the categories become tags (colour, what they are for), imported mail gets its categories as tags, and mail imported before gets them too (once)
# v0.5.133: An account the password store refused is reported and made by the next run, the rest goes on; twice as fast: the duplicate check reads the header only, a batch is made safe on disk at once, the export's lists stay in memory between batches; a duplicate counts only when its copy here can be read
# v0.5.132: Initial version: import.emclient-scan and import.emclient


JOURNAL_FILE: str = "import-emclient.log"
_Journal = Journal  # the journal, the fingerprint and the check of a copy on disk are shared by every importer (import_common)
_still_here = still_here
FINISHED: str = "finished:"  # journal phase: a part (PARTS) an import of these exports went through to the end
PARTS: tuple[str, ...] = ("accounts", "rules", "mail", "contacts", "tags")


# --- parameters and results -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanParameters:
    settings_file: str | None = field(default=None, metadata=option("The settings export (.xml, exported with passwords)"))
    mail_folder: str | None = field(default=None, metadata=option("The folder of the mail export (.eml files)"))
    backup_file: str | None = field(default=None, metadata=option("The backup (.zip): read, flagged and replied marks and the contacts"))


@dataclass(frozen=True, slots=True)
class ImportParameters:
    settings_file: str | None = field(default=None, metadata=option("The settings export (.xml, exported with passwords)"))
    mail_folder: str | None = field(default=None, metadata=option("The folder of the mail export (.eml files)"))
    backup_file: str | None = field(default=None, metadata=option("The backup (.zip): read, flagged and replied marks and the contacts"))
    accounts: bool = field(default=True, metadata=option("Import the accounts and their passwords (needs the settings export)"))
    rules: bool = field(default=True, metadata=option("Import the rules (needs the settings export)"))
    mail: bool = field(default=True, metadata=option("Import the mail into Local Folders (needs the mail export)"))
    contacts: bool = field(default=True, metadata=option("Import the contacts (needs the backup)"))
    tags: bool = field(default=True, metadata=option("Import the categories as tags, and the tags of the mail (the settings export and the backup)"))
    batch: int = field(default=0, metadata=option("Messages per run (0: all); run again to continue"))
    again: bool = field(default=False, metadata=option("Start over: go through everything again (what is here already is not added twice)"))


@dataclass(frozen=True, slots=True)
class ScanResult:
    accounts: int
    accounts_existing: int
    accounts_not_pop3: int
    passwords_missing: tuple[str, ...]  # accounts whose password cannot be read from the settings file
    rules: int
    rules_not_imported: tuple[str, ...]  # "name: reason"
    folders: int
    messages: int
    messages_done: int  # by an earlier run of this import
    marks: int  # messages with read, flagged and replied marks in the backup
    contacts: int
    tags: int = 0  # categories in the settings export
    tagged: int = 0  # messages with categories in the backup
    finished: tuple[str, ...] = ()  # the parts (PARTS) an earlier import of these very exports went through to the end


@dataclass(frozen=True, slots=True)
class ImportResult:
    accounts_added: int = 0
    accounts_existing: int = 0
    accounts_without_password: tuple[str, ...] = ()
    rules_added: int = 0
    rules_existing: int = 0
    rules_not_imported: tuple[str, ...] = ()
    contacts_added: int = 0
    contacts_existing: int = 0
    messages_imported: int = 0
    messages_duplicate: int = 0
    messages_failed: tuple[str, ...] = ()
    messages_left: int = 0
    tags_added: int = 0
    messages_tagged: int = 0
    notes: tuple[str, ...] = ()


def _path(text: str | None, what: str) -> Path | None:
    if text is None or not text.strip():
        return None
    path = Path(text.strip()).expanduser()
    if not path.exists():
        raise UsageError(f"The {what} does not exist")
    return path


def _same_account(engine: Engine, source: SourceAccount) -> str | None:
    """The id of the Chrono Mail account for the same mailbox (D-081: the same login, server and protocol)."""
    for settings in engine.config().accounts:
        account = settings.account
        if account.kind is AccountKind.POP3 and account.incoming.username.lower() == source.incoming.username.lower() and account.incoming.host.lower() == source.incoming.host.lower():
            return account.account_id
    return None


def scan(engine: Engine, parameters: ScanParameters) -> ScanResult:
    """What an import of these exports would do; changes nothing."""
    settings_path = _path(parameters.settings_file, "settings export")
    mail_path = _path(parameters.mail_folder, "mail export folder")
    backup_path = _path(parameters.backup_file, "backup")
    if settings_path is None and mail_path is None and backup_path is None:
        raise UsageError("Choose at least one export to import from")
    export = read_settings(settings_path) if settings_path is not None else SettingsExport((), (), False)
    pop3 = [account for account in export.accounts if account.kind == "pop3"]
    existing = sum(1 for account in pop3 if _same_account(engine, account) is not None)
    missing = tuple(account.email_address for account in pop3 if account.incoming.password is None and _same_account(engine, account) is None)
    not_imported = tuple(f"{rule.name}: {rule.problem}" for rule in export.rules if rule.problem is not None)
    folders: set[tuple[str, ...]] = set()
    messages = done = 0
    source = _journal_source(settings_path, mail_path, backup_path)
    places = _places(engine, settings_path, mail_path, backup_path)
    with _RUN_LOCK:
        if _RUN and _RUN[0].places == places and _RUN[0].key[1] != source:  # newer exports in the same place: the run of the older ones is gone
            _RUN.clear()
    journal = _Journal(engine.directories.data_directory / JOURNAL_FILE, source) if _journal_matches(engine, source) else None
    if mail_path is not None:
        for item in message_files(mail_path):
            messages += 1
            folders.add(item.folder)
            if journal is not None and item.relative in journal.done:
                done += 1
    backup = read_backup(backup_path, engine.directories.data_directory) if backup_path is not None else BackupData()
    return ScanResult(
        len(pop3), existing, len(export.accounts) - len(pop3), missing, len(export.rules) - len(not_imported), not_imported,
        len(folders), messages, done, sum(len(found) for found in backup.marks.values()), len(backup.contacts), len(export.categories), len(backup.tagged),
        tuple(part for part in PARTS if journal is not None and FINISHED + part in journal.phases),
    )


def _journal_source(settings_path: Path | None, mail_path: Path | None, backup_path: Path | None) -> str:
    """What the journal belongs to: these exports as they are now (a newer export written into the same place is another source)."""
    return "exports:" + contents_fingerprint((settings_path, mail_path, backup_path))


def _places(engine: Engine, settings_path: Path | None, mail_path: Path | None, backup_path: Path | None) -> tuple[str, ...]:
    """Where the exports are (not what they hold): finds the run of an import of these places."""
    return (str(engine.directories.data_directory), *(str(path.resolve()) if path is not None else "" for path in (settings_path, mail_path, backup_path)))


def _source_of_run(engine: Engine, settings_path: Path | None, mail_path: Path | None, backup_path: Path | None) -> str:
    """The contents fingerprint of the exports, computed once per import run: the batches after the first reuse it."""
    places = _places(engine, settings_path, mail_path, backup_path)
    with _RUN_LOCK:
        if _RUN and _RUN[0].places == places:
            return _RUN[0].key[1]
    return _journal_source(settings_path, mail_path, backup_path)


def _journal_matches(engine: Engine, source: str) -> bool:
    """Is there a journal of an earlier import of these exports? (Only then is it opened, so a scan never starts a new one.)"""
    try:
        first = (engine.directories.data_directory / JOURNAL_FILE).read_text("utf-8").split("\n", 1)[0]
        return bool(load_json(first) == ["source", source])
    except (OSError, UnicodeDecodeError, ValueError):
        return False


# --- the import -----------------------------------------------------------------------------------


def _policy(account: SourceAccount) -> tuple[Policy, int]:
    if not account.leave_on_server:
        return "delete_after_download", 30
    if account.delete_after_days > 0:
        return "delete_after_days", min(account.delete_after_days, 3650)
    if account.remove_when_deleted:
        return "delete_when_deleted_locally", 30
    return "keep", 30


def _import_accounts(engine: Engine, export: SettingsExport, add: bool, notes: list[str]) -> tuple[dict[str, str], int, int, list[str]]:
    """Make the POP3 accounts that are not here yet (their mail in Local Folders). Returns uid -> account id, added, existing, without password."""
    found: dict[str, str] = {}
    added = existing = 0
    without_password: list[str] = []
    for source in export.accounts:
        if source.kind != "pop3":
            notes.append(f"{source.email_address or source.name}: only POP3 accounts are imported")
            continue
        same = _same_account(engine, source)
        if same is not None:
            found[source.uid] = same
            existing += 1
            continue
        if not add:
            continue
        email = source.email_address or (source.incoming.username if "@" in source.incoming.username else "")
        policy, days = _policy(source)
        incoming, outgoing = source.incoming, source.outgoing
        different_login = outgoing.username and outgoing.username != incoming.username
        try:
            result = account_add(
                engine,
                AccountAddParameters(
                    display_name=source.person_name or source.name, email_address=email, username=incoming.username,
                    password=incoming.password or "", incoming_host=incoming.host, outgoing_host=outgoing.host,
                    incoming_port=incoming.port or 110, incoming_security=incoming.security, outgoing_port=outgoing.port or 25,
                    outgoing_security=outgoing.security, outgoing_username=outgoing.username if different_login else None,
                    outgoing_password=outgoing.password if outgoing.password != incoming.password else None,
                    delete_policy=policy, delete_days=days, kind="pop3", storage="local",
                ),
            )
        except ChronoMailError as error:
            notes.append(f"{email or source.name}: the account was not made (Import again makes it): {error}")
            continue
        account_id = result.account_id
        engine.change_config(lambda config: config.with_account(replace(config.find(account_id), skip_known_mail=True)))
        found[source.uid] = result.account_id
        added += 1
        if incoming.password is None or outgoing.password is None:
            without_password.append(email)
    return found, added, existing, without_password


def _same_rule(first: Rule, second: Rule) -> bool:
    return (first.name, first.conditions, first.actions, first.match, first.accounts) == (second.name, second.conditions, second.actions, second.match, second.accounts)


def _import_rules(engine: Engine, export: SettingsExport, accounts: dict[str, str], notes: list[str]) -> tuple[int, int, list[str]]:
    rules = load_rules(engine.directories.rules_file)
    added = existing = 0
    not_imported: list[str] = []
    for source in export.rules:
        if source.problem is not None:
            not_imported.append(f"{source.name}: {source.problem}")
            continue
        for_accounts: tuple[str, ...] = ()
        if source.account_uid is not None:
            account = accounts.get(source.account_uid)
            if account is None:
                not_imported.append(f"{source.name}: its account was not imported")
                continue
            for_accounts = (account,)
        try:
            rule = Rule(new_rule_id(), source.name, source.conditions, source.actions, source.match, source.enabled, for_accounts)
            if any(_same_rule(rule, known) for known in rules.rules):
                existing += 1
                continue
            rules = rules.with_rule(rule)
        except ChronoMailError as error:
            not_imported.append(f"{source.name}: {error}")
            continue
        added += 1
        if source.left_out:
            notes.append(f"Rule {source.name}: not imported: {', '.join(source.left_out)}")
    save_rules(engine.directories.rules_file, rules)
    return added, existing, not_imported


def _import_categories(engine: Engine, categories: tuple[SourceCategory, ...], names_in_mail: tuple[str, ...]) -> int:
    """The categories become tags with their colour and what they are for; a tag of the same name keeps what it has.
    Names the mail carries that are no category get the default colour. Returns how many tags were made."""
    try:
        tags = load_tags(engine.directories.tags_file)
        made = 0
        for category in categories:
            if tags.entry(category.name) is not None:
                continue
            tags = tags.with_colour(category.name, category.colour).with_details(category.name, "", category.kinds)
            made += 1
        save_tags(engine.directories.tags_file, tags)
    except ChronoMailError as error:
        raise UsageError(f"The categories could not be made into tags: {error}") from None
    before = len(load_tags(engine.directories.tags_file).entries)
    give_colours(engine, names_in_mail)
    return made + len(load_tags(engine.directories.tags_file).entries) - before


def _tag_present_mail(engine: Engine, backup: BackupData) -> tuple[int, int]:
    """Mail imported before tags existed (or by an earlier run) gets the categories it had: found in Local Folders by its
    Message-ID. Returns (tagged, not found)."""
    tagged = missing = 0
    ensure_local_folders(engine)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        known = known_messages(mailbox)
        for message_id, _, tags in backup.tagged:
            store_id = known.get(f"id:{message_id}") if message_id else None
            if store_id is None:
                missing += 1
                continue
            current = mailbox.tags_of(store_id)
            wanted = clean_tags([*current, *tags])
            if wanted != current:
                row = mailbox.index.get(store_id)
                if row is None:
                    missing += 1
                    continue
                mailbox.set_tags(MessageRef(mailbox.store.account_id, row.folder, store_id), wanted)
            tagged += 1
    return tagged, missing


def _import_contacts(engine: Engine, backup: BackupData, notes: list[str]) -> tuple[int, int]:
    ensure_local_pim(engine)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        books = store.collections(CollectionKind.CONTACTS)
    if not books:
        notes.append("Contacts: Local Folders has no address book")
        return 0, 0
    known = {item.address for item in contact_addresses(engine, AddressesParameters())}
    added = existing = 0
    for source in backup.contacts:
        emails = tuple(email.lower() for email in source.emails)
        if any(email in known for email in emails):
            existing += 1
            continue
        try:
            contact_add(
                engine,
                AddParameters(
                    collection=books[0].collection_id, name=source.name or None, given=source.given or None, additional=source.additional or None,
                    family=source.family or None, nickname=(source.nickname,) if source.nickname else (), email=source.emails, phone=source.phones,
                    organization=source.organization or None, department=source.department or None, title=source.title or None,
                    note=source.note or None, birthday=source.birthday or None,
                ),
            )
        except ChronoMailError as error:
            notes.append(f"A contact was not imported: {error}")
            continue
        known.update(emails)
        added += 1
    return added, existing


class _Folders:
    """Where a folder of the export goes in Local Folders: the standard folders onto their roles, the rest by name."""

    def __init__(self, mailbox: Mailbox, names: Sequence[str]) -> None:
        self.mailbox = mailbox
        self.made: set[tuple[str, ...]] = set()
        tops = list(dict.fromkeys(name.strip() for name in names))  # the export's top folders, once each, in order: one language for all
        self.roles: dict[str, FolderRole] = {tops[index]: role for index, role in standard_roles(tops).items()}

    def target(self, folder: tuple[str, ...]) -> tuple[str, ...]:
        if not folder:
            path = self.mailbox.role_folder(FolderRole.INBOX)
        else:
            role = self.roles.get(folder[0].strip())
            base = (folder[0].strip(),)
            if role is not None:
                try:
                    base = self.mailbox.role_folder(role)
                except StorageError:
                    pass  # no folder with that role (Archive): a folder of its own name
            path = base + tuple(part.strip() for part in folder[1:])
        if path not in self.made:
            self.mailbox.ensure_folder(path)
            self.made.add(path)
        return path


@dataclass
class _Run:
    """What one import keeps between its batches, so that each batch does not read the export, the backup and the
    mail that is already here again: the files of the export, the marks of the backup (each used once) and the
    fingerprints of the mail that was here before the import."""

    key: tuple[str, ...]
    files: list[MessageFile]
    backup: BackupData | None
    places: tuple[str, ...] = ()  # where the exports are (_places): the batches find this run, and its fingerprint (key[1]), by them
    known: dict[str, str] | None = None
    with_tags: bool = True  # Tags is chosen: imported mail gets its categories as tags
    tags_given: bool = False  # the tags waiting from earlier imports were tried by this import (once per import, not per batch)


_RUN: list[_Run] = []  # the one import that runs (at most one entry)
_RUN_LOCK = threading.Lock()


def _file_key(path: Path | None) -> str:
    if path is None:
        return ""
    status = path.stat()
    return f"{path.resolve()}|{status.st_size}|{status.st_mtime_ns}"


def _run_for(engine: Engine, mail_path: Path, backup_path: Path | None, journal_source: str, places: tuple[str, ...] = ()) -> _Run:
    key = (str(engine.directories.data_directory), journal_source, str(mail_path.resolve()), _file_key(backup_path))
    with _RUN_LOCK:
        if _RUN and _RUN[0].key == key:
            return _RUN[0]
        run = _Run(key, list(message_files(mail_path)), read_backup(backup_path, engine.directories.data_directory) if backup_path is not None else None, places)
        _RUN[:] = [run]
        return run


def _finished_run() -> None:
    with _RUN_LOCK:
        _RUN.clear()


UNTAGGED: str = "untagged:"  # journal value: a message imported whose tags could not be given; a later run with Tags gives them


def _give_waiting_tags(engine: Engine, journal: _Journal) -> tuple[int, int]:
    """Messages imported whose tags could not be given then get them now (once). Returns (given, still waiting)."""
    waiting = [(key, value) for key, value in journal.values.items() if key.startswith(UNTAGGED) and value]
    if not waiting:
        return 0, 0
    given = failed = 0
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        for key, value in waiting:
            try:
                found = load_json(value)
                if isinstance(found, list) and len(found) == 2 and isinstance(found[0], str) and isinstance(found[1], list):
                    row = mailbox.index.get(found[0])
                    if row is not None:  # deleted since: nothing to give
                        current = mailbox.tags_of(found[0])
                        wanted = clean_tags([*current, *(str(tag) for tag in found[1])])
                        if wanted != current:
                            mailbox.set_tags(MessageRef(mailbox.store.account_id, row.folder, found[0]), wanted)
                        given += 1
            except Exception:  # noqa: BLE001 - tried again by the next import
                failed += 1
                continue
            journal.remember(key, "")
    return given, failed


def _import_mail(engine: Engine, mail_path: Path, run: _Run, journal: _Journal, batch: int) -> tuple[int, int, list[str], int, int]:
    """Up to batch messages (0: all) that are not done yet. Returns imported, duplicates, failures, how many are left and how many messages
    are imported whose tags could not be given (they get them with a later run). The batch is made safe on disk at once at its end, and only
    then written in the journal as done."""
    ensure_local_folders(engine)
    files = [item for item in run.files if item.relative not in journal.done]
    imported = duplicates = 0
    failed: list[str] = []
    outcomes: list[tuple[str, str]] = []
    untagged: list[tuple[str, str]] = []
    work = files if batch <= 0 else files[:batch]
    done_before = len(run.files) - len(files)  # by earlier batches and earlier imports: the count runs on over the whole import
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox, mailbox.store.written_together():
        if run.known is None:
            run.known = known_messages(mailbox, journal.imported_ids())
        known = run.known
        folders = _Folders(mailbox, [item.folder[0] for item in run.files if item.folder])
        for number, item in enumerate(work, 1):
            if (done_before + number) % PROGRESS_STEP == 0 or number == len(work):
                engine.progress("Importing mail", done_before + number, len(run.files))
            try:
                path = mail_path / item.relative
                if path.stat().st_size > MAXIMUM_MESSAGE_BYTES:
                    raise UsageError("the message is larger than the limit")
                raw = path.read_bytes()
                message_id, moment = message_keys(raw)
                found = run.backup.take(item.folder, message_id, moment) if run.backup is not None else None
                marks, tags = found if found is not None else (None, ())
                key = fingerprint_of_raw(raw)
                present = known.get(key) if key is not None else None
                if present is not None and _still_here(mailbox, present):
                    outcomes.append((item.relative, "duplicate"))
                    duplicates += 1
                    continue
                reference = mailbox.deliver(folders.target(item.folder), raw, marks if marks is not None else frozenset({MessageFlag.SEEN}))
                if tags and run.with_tags:
                    try:
                        mailbox.set_tags(reference, tags)
                    except Exception:  # noqa: BLE001 - the message is stored: it stays imported, its tags come with a later run
                        untagged.append((UNTAGGED + item.relative, json.dumps([reference.store_id, list(tags)])))
            except (ChronoMailError, OSError) as error:
                outcomes.append((item.relative, "failed"))
                if len(failed) < MAXIMUM_REPORTED:
                    failed.append(f"{item.relative}: {error}")
                continue
            outcomes.append((item.relative, reference.store_id))
            imported += 1
    # only here, once written_together made the batch safe on disk without an error: an error there leaves the journal as it was (those
    # messages are found again as duplicates, never recorded as done while not safe)
    journal.messages(outcomes)
    for key, value in untagged:
        journal.remember(key, value)
    journal.finish()
    left = len(files) - len(work)
    if left == 0:
        _finished_run()
    return imported, duplicates, failed, left, len(untagged)


def run_import(engine: Engine, parameters: ImportParameters) -> ImportResult:
    """Import from the exports. The accounts, rules and contacts are done once; the mail in batches (run again until nothing is left)."""
    settings_path = _path(parameters.settings_file, "settings export")
    mail_path = _path(parameters.mail_folder, "mail export folder")
    backup_path = _path(parameters.backup_file, "backup")
    if (parameters.accounts or parameters.rules) and settings_path is None:
        raise UsageError("Accounts and rules come from the settings export: choose it, or leave them out")
    if parameters.mail and mail_path is None:
        raise UsageError("The mail comes from the mail export folder: choose it, or leave the mail out")
    if parameters.contacts and backup_path is None:
        raise UsageError("The contacts come from the backup: choose it, or leave them out")
    if not (parameters.accounts or parameters.rules or parameters.mail or parameters.contacts or parameters.tags):  # tags without their exports: nothing to do
        raise UsageError("Choose what to import")
    journal_path = engine.directories.data_directory / JOURNAL_FILE
    if parameters.again:  # start over: everything is gone through again (the duplicate checks keep what is here from being added twice)
        _finished_run()
        journal_path.unlink(missing_ok=True)
    source = _source_of_run(engine, settings_path, mail_path, backup_path)  # once per run: the batches after the first reuse it
    journal = _Journal(journal_path, source)
    notes: list[str] = []
    result = ImportResult()
    export = read_settings(settings_path) if settings_path is not None else SettingsExport((), (), False)
    if (parameters.accounts or parameters.rules) and "settings" not in journal.phases:
        before = len(notes)
        accounts, added, existing, without = _import_accounts(engine, export, parameters.accounts, notes)
        not_made = any("the account was not made" in note for note in notes[before:])
        result = replace(result, accounts_added=added, accounts_existing=existing, accounts_without_password=tuple(without))
        if parameters.rules:
            rules_added, rules_existing, not_imported = _import_rules(engine, export, accounts, notes)
            result = replace(result, rules_added=rules_added, rules_existing=rules_existing, rules_not_imported=tuple(not_imported))
        if not not_made:  # an account that could not be made (the password store did not answer) is tried again by the next run
            journal.phase("settings")
    run = _run_for(engine, mail_path, backup_path, source, _places(engine, settings_path, mail_path, backup_path)) if mail_path is not None else None
    backup = run.backup if run is not None else (read_backup(backup_path, engine.directories.data_directory) if backup_path is not None else None)
    if parameters.contacts and backup is not None and "contacts" not in journal.phases:
        contacts_added, contacts_existing = _import_contacts(engine, backup, notes)
        result = replace(result, contacts_added=contacts_added, contacts_existing=contacts_existing)
        journal.phase("contacts")
    if parameters.tags and "tags" not in journal.phases and (export.categories or (backup is not None and backup.tagged)):
        names_in_mail = tuple(dict.fromkeys(tag for _, _, tags in (backup.tagged if backup is not None else ()) for tag in tags))
        added = _import_categories(engine, export.categories, names_in_mail)
        tagged = missing = 0
        if backup is not None and backup.tagged:
            tagged, missing = _tag_present_mail(engine, backup)
        if missing and parameters.mail:
            notes.append(f"Tags: {missing} tagged messages are not here yet; they get their tags when they are imported")
        result = replace(result, tags_added=added, messages_tagged=tagged)
        journal.phase("tags")
    if parameters.tags and (run is None or not run.tags_given):
        if run is not None:
            run.tags_given = True  # a record that fails is tried again by a later import, not by the next batch
        given, waiting = _give_waiting_tags(engine, journal)
        result = replace(result, messages_tagged=result.messages_tagged + given)
        if waiting:
            notes.append(f"Tags: {waiting} imported messages could not get their tags yet; the next import tries again")
    if run is not None:
        run.with_tags = parameters.tags
    if parameters.mail and mail_path is not None and run is not None:
        try:
            imported, duplicates, failed, left, tags_later = _import_mail(engine, mail_path, run, journal, parameters.batch)
        except BaseException:
            _finished_run()  # a batch that failed (it may not be safe on disk): Import again finds the mail here again from the index
            raise
        result = replace(result, messages_imported=imported, messages_duplicate=duplicates, messages_failed=tuple(failed), messages_left=left)
        if tags_later:
            notes.append(f"Tags: {tags_later} messages are imported; their tags come with the next import")
    if result.messages_left == 0:  # the parts chosen went through to the end (an account not made yet is tried again: not finished)
        chosen = {"accounts": parameters.accounts, "rules": parameters.rules, "mail": parameters.mail, "contacts": parameters.contacts, "tags": parameters.tags}
        for part in PARTS:
            if chosen[part] and (part not in ("accounts", "rules") or "settings" in journal.phases) and FINISHED + part not in journal.phases:
                journal.phase(FINISHED + part)
    return replace(result, notes=tuple(notes))


@dataclass(frozen=True, slots=True)
class PasswordsParameters:
    settings_file: str = field(metadata=option("The settings export (.xml, exported with passwords)"))


@dataclass(frozen=True, slots=True)
class PasswordsResult:
    updated: tuple[str, ...]  # the accounts whose passwords were stored again
    not_decodable: tuple[str, ...]  # accounts here whose password in the export cannot be read: left as they are
    not_here: int  # accounts of the export that are not here: nothing done


def update_passwords(engine: Engine, parameters: PasswordsParameters) -> PasswordsResult:
    """Store again, from the settings export, the passwords of the accounts that are here already (the same login and
    server). A repair after the key of the export was corrected; never shows a password."""
    path = _path(parameters.settings_file, "settings export")
    if path is None:
        raise UsageError("Choose the settings export")
    export = read_settings(path)
    updated: list[str] = []
    not_decodable: list[str] = []
    not_here = 0
    for source in export.accounts:
        account_id = _same_account(engine, source)
        if account_id is None:
            not_here += 1
            continue
        name = source.email_address or source.name
        if source.incoming.password is None:
            not_decodable.append(name)
            continue
        engine.passwords.set(account_id, ServerRole.INCOMING, source.incoming.password)
        engine.passwords.set(account_id, ServerRole.OUTGOING, source.outgoing.password or source.incoming.password)
        label_account(engine, account_id)  # B-23
        updated.append(name)
    return PasswordsResult(tuple(updated), tuple(not_decodable), not_here)


IMPORT_EMCLIENT_SCAN: TypedCommand[ScanParameters, ScanResult] = TypedCommand("import.emclient-scan", ScanParameters, ScanResult)
IMPORT_EMCLIENT: TypedCommand[ImportParameters, ImportResult] = TypedCommand("import.emclient", ImportParameters, ImportResult)


def register(registry: CommandRegistry) -> None:
    registry.register(IMPORT_EMCLIENT_SCAN.id, "What an import from eM Client's exports would bring in (changes nothing)", ScanParameters, scan, doing="Reading the exports to import")
    registry.register("import.emclient-passwords", "Store again the passwords of accounts that are here already, from eM Client's settings export", PasswordsParameters, update_passwords, doing="Importing passwords")
    registry.register(IMPORT_EMCLIENT.id, "Import accounts, passwords, rules, contacts and mail from eM Client's exports", ImportParameters, quiet_command(run_import), doing="Importing mail and settings")
