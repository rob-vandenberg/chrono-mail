import json
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from email.policy import compat32
from pathlib import Path
from typing import Protocol, cast

import pyzipper

from chrono_mail.config import AccountSettings, AppConfig, RuleSet, ServerRole, TagColours, parse_config, parse_rules, parse_sidebar, parse_tags, save_rules, save_tags
from chrono_mail.config.files import load_config
from chrono_mail.config.rules import load_rules
from chrono_mail.config.sidebar import save_sidebar
from chrono_mail.config.tags import load_tags
from chrono_mail.core import AccountKind, ChronoMailError, CollectionInfo, CollectionKind, CryptoError, FolderRole, MessageFlag, StorageError
from chrono_mail.crypto import DataKey, encrypt_message, generate_data_key
from chrono_mail.engine.backup import CANCEL, FORMAT_VERSION, JOB_LOCK, MANIFEST_ENTRY
from chrono_mail.engine.local import SETUP_LOCK, ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.pimstore.extract import extract
from chrono_mail.store import Store
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.store.layout import parse_name, split_info
from chrono_mail.sync.imap_state import ImapState

# --- Version ------------------------------------------------------------
__version__ = "restore.py 2.4.324"

# --- Version history ----------------------------------------------------
# v2.4.324: Initial: Menu > File > Restore…: a .cmz backup back on this computer: an account that is not here whole, under its own
#           identity; one that is here item by item, the server's data left to the server (D-193); "Remove all data on this computer
#           first" sets this computer's data aside and removes it only after the restore succeeded; "Replace items that already
#           exist" (D-244)

DOING_MESSAGES: str = "Restoring messages"  # the progress reports (the status line and the Restore window)
DOING_ITEMS: str = "Restoring calendars, contacts and tasks"
MAXIMUM_MANIFEST_BYTES: int = 200_000_000
_ITEM_KINDS: dict[str, CollectionKind] = {"calendars": CollectionKind.EVENTS, "contacts": CollectionKind.CONTACTS, "tasks": CollectionKind.TASKS}


class NotABackup(UsageError):
    """The file is not a backup of Chrono Mail (or it is damaged)."""


class BackupPasswordNeeded(UsageError):
    """The backup is protected: its backup password is needed."""


class WrongBackupPassword(UsageError):
    """The password given does not open the backup."""


class RestoreCancelled(ChronoMailError):
    """The person cancelled the restore: what was restored whole stays (all of it is put back when the data was set aside first)."""


class _Reading(Protocol):
    """The part of pyzipper's ZIP file a restore uses (pyzipper has no type information of its own)."""

    def namelist(self) -> list[str]: ...

    def getinfo(self, name: str) -> zipfile.ZipInfo: ...

    def read(self, name: str) -> bytes: ...

    def setpassword(self, pwd: bytes) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ContentsAccount:
    account_id: str
    name: str
    mail: bool
    items: bool
    here: bool  # the account exists on this computer: it is filled in item by item, never replaced whole


@dataclass(frozen=True, slots=True)
class BackupContents:
    """The backup's contents page, for the Restore window."""

    created: str
    version: str
    parts: tuple[str, ...]
    accounts: tuple[ContentsAccount, ...]
    messages: int
    items: int
    passwords: bool
    protected: bool


@dataclass(frozen=True, slots=True)
class RestoreChoice:
    path: Path
    password: str | None
    remove_first: bool = False
    replace_existing: bool = False


@dataclass
class RestoreReport:
    made: list[str] = field(default_factory=list[str])  # accounts that came back whole
    filled: list[str] = field(default_factory=list[str])  # accounts that were here and got what they lacked
    messages: int = 0
    items: int = 0
    skipped: int = 0  # messages and items that were here already (or that the server holds)
    window_settings: str = ""  # the window's own settings from the backup (the window puts them in place)
    notes: list[str] = field(default_factory=list[str])


@dataclass
class _Progress:
    engine: Engine
    totals: dict[str, int]
    done: dict[str, int] = field(default_factory=dict[str, int])

    def step(self, doing: str) -> None:
        if CANCEL.is_set() or self.engine.stop_event.is_set():
            raise RestoreCancelled("The restore was cancelled")
        done = self.done.get(doing, 0) + 1
        self.done[doing] = done
        if done % 100 == 0 or done == self.totals.get(doing, 0):
            self.engine.progress(doing, done, self.totals.get(doing, 0))


# --- the key of this computer -------------------------------------------------------------------------


def ensure_key(engine: Engine) -> bool:
    """Make this computer's encryption key when it has none yet (a fresh installation), before anything of a backup is read. Whether
    one was made: then the person is shown its recovery code before the backup is chosen."""
    with SETUP_LOCK:
        config = engine.config()
        if config.key_id is not None:
            return False
        key = generate_data_key()
        engine.keys.save(key)
        if engine.keys.load(key.key_id) != key:
            raise CryptoError("The credential store did not return the key that was just saved")
        engine.change_config(lambda fresh: fresh.with_key_id(key.key_id.hex()) if fresh.key_id is None else fresh)
        return True


# --- reading a backup -----------------------------------------------------------------------------------


def _open(path: Path, password: str | None) -> tuple[_Reading, dict[str, object], bool]:
    try:
        archive = cast(_Reading, pyzipper.AESZipFile(path))
    except FileNotFoundError:
        raise UsageError(f"{path} does not exist") from None
    except (OSError, zipfile.BadZipFile, pyzipper.BadZipFile, ValueError):
        raise NotABackup("This file is not a backup of Chrono Mail") from None
    try:
        if MANIFEST_ENTRY not in archive.namelist():
            raise NotABackup("This file is not a backup of Chrono Mail")
        protected = bool(archive.getinfo(MANIFEST_ENTRY).flag_bits & 0x1)
        if protected:
            if not password:
                raise BackupPasswordNeeded("This backup is protected with a backup password")
            archive.setpassword(password.encode("utf-8"))
        manifest = json.loads(_read(archive, MANIFEST_ENTRY))
        if not isinstance(manifest, dict):
            raise NotABackup("This file is not a backup of Chrono Mail")
        found = cast(dict[str, object], manifest)
        format_number = found.get("format")
        if not isinstance(format_number, int) or format_number < 1:
            raise NotABackup("This file is not a backup of Chrono Mail")
        if format_number > FORMAT_VERSION:
            raise UsageError(f"This backup was made by a newer Chrono Mail ({found.get('version', 'unknown')}); update Chrono Mail first")
        return archive, found, protected
    except BaseException:
        archive.close()
        raise


def _read(archive: _Reading, name: str) -> bytes:
    try:
        if archive.getinfo(name).file_size > MAXIMUM_MANIFEST_BYTES and name == MANIFEST_ENTRY:
            raise NotABackup("This file is not a backup of Chrono Mail")
        return archive.read(name)
    except RuntimeError:  # pyzipper: a wrong password
        raise WrongBackupPassword("This password does not open the backup") from None
    except (zipfile.BadZipFile, pyzipper.BadZipFile, OSError, ValueError, EOFError) as error:
        raise NotABackup(f"The backup is damaged: {error}") from None


def read_contents(engine: Engine, path: Path, password: str | None) -> BackupContents:
    """What a backup holds (its contents page), after the backup password when it is protected."""
    archive, manifest, protected = _open(path, password)
    try:
        backup_config = _backup_config(archive)
    finally:
        archive.close()
    config = engine.config()
    rows = _accounts(manifest)
    accounts = tuple(
        ContentsAccount(settings.account.account_id, rows.get(settings.account.account_id, ("", False, False))[0] or _plain_name(settings),
                        rows.get(settings.account.account_id, ("", False, False))[1], rows.get(settings.account.account_id, ("", False, False))[2],
                        _same_here(config, settings) is not None)
        for settings in backup_config.accounts
    )
    parts = manifest.get("parts")
    return BackupContents(
        str(manifest.get("created", "")), str(manifest.get("version", "")), tuple(str(part) for part in parts) if isinstance(parts, list) else (),
        accounts, _number(manifest.get("messages")), _number(manifest.get("items")), manifest.get("passwords") is True, protected,
    )


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _plain_name(settings: AccountSettings) -> str:
    return settings.account_name or settings.account.email_address or settings.account.incoming.username


def _accounts(manifest: dict[str, object]) -> dict[str, tuple[str, bool, bool]]:
    """Per account in the backup: its name, and whether its mail and its calendars, contacts and tasks are in it."""
    found: dict[str, tuple[str, bool, bool]] = {}
    rows = manifest.get("accounts")
    if isinstance(rows, list):
        for row in cast(list[object], rows):
            if isinstance(row, dict):
                data = cast(dict[str, object], row)
                found[str(data.get("id", ""))] = (str(data.get("name", "")), data.get("mail") is True, data.get("items") is True)
    return found


def _backup_config(archive: _Reading) -> AppConfig:
    try:
        return parse_config(_read(archive, "config/config.json").decode("utf-8"))
    except KeyError:
        raise NotABackup("The backup holds no accounts") from None
    except (ChronoMailError, UnicodeDecodeError) as error:
        if isinstance(error, (WrongBackupPassword, NotABackup)):
            raise
        raise NotABackup(f"The accounts in the backup cannot be read: {error}") from None


def _same_here(config: AppConfig, settings: AccountSettings) -> AccountSettings | None:
    """The account here that is this account of the backup: the same identity, or the same login on the same server with the same
    protocol (D-081)."""
    account = settings.account
    for item in config.accounts:
        if item.account.account_id == account.account_id:
            return item
    if account.kind in (AccountKind.LOCAL, AccountKind.DAV):
        return None
    for item in config.accounts:
        if (
            item.account.kind is account.kind and item.account.incoming.username.strip().lower() == account.incoming.username.strip().lower()
            and item.account.incoming.host.strip().lower() == account.incoming.host.strip().lower()
        ):
            return item
    return None


# --- the restore ----------------------------------------------------------------------------------------


def restore_backup(engine: Engine, choice: RestoreChoice) -> RestoreReport:
    """Put a backup back on this computer. Nothing is sent to a server here: what came back is synchronised by the next check, where the
    server wins (D-193). With remove_first this computer's data is set aside first and removed only when the restore succeeded; a failure
    or a cancel puts it back as it was."""
    if not JOB_LOCK.acquire(blocking=False):
        raise UsageError("A backup is being made or restored already")
    CANCEL.clear()
    try:
        archive, manifest, _ = _open(choice.path, choice.password)
        try:
            ensure_key(engine)
            key = engine.data_key(engine.config())
            engine.close_imap_links()
            aside: Path | None = _set_aside(engine) if choice.remove_first else None
            try:
                with engine.quietly():
                    report = _restore(engine, archive, manifest, key, choice)
            except BaseException:
                if aside is not None:
                    _put_back(engine, aside)
                raise
            if aside is not None:
                _let_go(engine, aside, report)
            return report
        finally:
            archive.close()
    finally:
        CANCEL.clear()
        JOB_LOCK.release()


def _restore(engine: Engine, archive: _Reading, manifest: dict[str, object], key: DataKey, choice: RestoreChoice) -> RestoreReport:
    names = archive.namelist()
    sealed = {name for name in cast(list[object], manifest.get("sealed")) if isinstance(name, str)} if isinstance(manifest.get("sealed"), list) else set[str]()
    raw_parts = manifest.get("parts")
    parts = {str(part) for part in cast(list[object], raw_parts)} if isinstance(raw_parts, list) else set[str]()
    kinds = {kind for part, kind in _ITEM_KINDS.items() if part in parts}
    backup_config = _backup_config(archive)
    passwords = _passwords(archive, names)
    report = RestoreReport()
    progress = _Progress(engine, _totals(names, backup_config))
    for settings in backup_config.accounts:  # Local Folders first: accounts may keep their mail in it
        if settings.account.kind is not AccountKind.LOCAL:
            continue
        _one_account(engine, archive, names, sealed, kinds, key, settings, choice, report, progress)
    for settings in backup_config.accounts:
        if settings.account.kind is AccountKind.LOCAL:
            continue
        _one_account(engine, archive, names, sealed, kinds, key, settings, choice, report, progress)
    if "rules" in parts:
        _restore_rules(engine, archive, names, choice.replace_existing)
    if "settings" in parts:
        _restore_settings(engine, archive, names, sealed, key, choice.replace_existing, report)
    _restore_passwords(engine, passwords, report, choice.replace_existing)
    return report


def _totals(names: list[str], backup_config: AppConfig) -> dict[str, int]:
    accounts = {settings.account.account_id for settings in backup_config.accounts}
    totals = {DOING_MESSAGES: 0, DOING_ITEMS: 0}
    for name in names:
        parts = name.split("/")
        if len(parts) < 4 or parts[0] != "data" or parts[1] != "accounts" or parts[2] not in accounts:
            continue
        if _is_message(parts[3:]):
            totals[DOING_MESSAGES] += 1
        elif _is_item(parts[3:]):
            totals[DOING_ITEMS] += 1
    return totals


def _is_message(relative: list[str]) -> bool:
    """store/folders/<folder>/cur|new/<message>"""
    return len(relative) == 5 and relative[0] == "store" and relative[1] == "folders" and relative[3] in ("cur", "new") and parse_name(relative[4]) is not None


def _is_item(relative: list[str]) -> bool:
    """pim/collections/<collection>/items/<item>"""
    return len(relative) == 5 and relative[0] == "pim" and relative[1] == "collections" and relative[3] == "items"


def _passwords(archive: _Reading, names: list[str]) -> dict[str, dict[str, str]]:
    if "config/passwords.json" not in names:
        return {}
    data: object = json.loads(_read(archive, "config/passwords.json"))
    found: dict[str, dict[str, str]] = {}
    if isinstance(data, dict):
        for account_id, roles in cast(dict[str, object], data).items():
            if isinstance(roles, dict):
                found[account_id] = {role: password for role, password in cast(dict[str, object], roles).items() if isinstance(password, str)}
    return found


def _one_account(
    engine: Engine, archive: _Reading, names: list[str], sealed: set[str], kinds: set[CollectionKind], key: DataKey, settings: AccountSettings,
    choice: RestoreChoice, report: RestoreReport, progress: _Progress,
) -> None:
    here = _same_here(engine.config(), settings)
    prefix = f"data/accounts/{settings.account.account_id}/"
    if here is None:
        _restore_whole(engine, archive, [name for name in names if name.startswith(prefix)], prefix, sealed, kinds, key, settings, report, progress)
        report.made.append(settings.account.account_id)
        return
    account_id = here.account.account_id
    if choice.replace_existing and account_id == settings.account.account_id and settings.account.kind is not AccountKind.LOCAL:
        engine.change_config(lambda fresh: fresh.with_account(settings))  # the account's own settings as they were in the backup
    _fill_mail(engine, archive, names, prefix, here, choice.replace_existing, report, progress)
    if kinds:
        _fill_items(engine, archive, names, prefix, here, kinds, choice.replace_existing, report, progress)
    report.filled.append(account_id)


# --- an account that is not here: back whole, under its own identity ------------------------------------------------


def _restore_whole(
    engine: Engine, archive: _Reading, names: list[str], prefix: str, sealed: set[str], kinds: set[CollectionKind], key: DataKey,
    settings: AccountSettings, report: RestoreReport, progress: _Progress,
) -> None:
    """The account's files are written in a folder of their own and that folder is renamed into place in one step, so the account comes
    back whole or not at all; only then is it added to the accounts."""
    account_id = settings.account.account_id
    directories = engine.directories
    target = directories.account_directory(account_id)
    building = target.with_name(f".restoring-{account_id}")
    shutil.rmtree(building, ignore_errors=True)
    collections_kept = _kept_collections(archive, names, prefix, kinds)
    try:
        for name in names:
            relative = name[len(prefix):].split("/")
            if not relative[-1] or not _wanted(relative, collections_kept):
                continue
            doing = DOING_MESSAGES if _is_message(relative) else DOING_ITEMS if _is_item(relative) else ""
            if doing:
                progress.step(doing)
            data = _read(archive, name)
            if name in sealed:
                data = encrypt_message(data, key)
            elif relative in (["store", "store.json"], ["pim", "pim.json"]):
                data = _with_key(data, key)
            elif relative == ["pim", "dav-state.json"]:
                data = _dav_state_for(data, collections_kept)
            path = building.joinpath(*relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_new_file(path, data, engine.durable)
            if doing == DOING_MESSAGES:
                report.messages += 1
            elif doing:
                report.items += 1
        _complete_layout(building)
        if settings.account.kind is not AccountKind.DAV and settings.storage == "own" and not (building / "store" / "store.json").exists():
            store = Store.create(building / "store", account_id, key if settings.encrypted else None, durable=engine.durable)
            for path_of_folder, role in STANDARD_FOLDERS:
                store.create_folder(path_of_folder, role)
        if target.exists():  # the files of an account removed earlier (its mail is kept on removal): kept beside it, never written over
            kept = target.with_name(f"{account_id}-kept-{time.strftime('%Y%m%d-%H%M%S')}")
            os.replace(target, kept)
            report.notes.append(f"Mail of an earlier account was kept in {kept}")
        building.parent.mkdir(parents=True, exist_ok=True)
        os.replace(building, target)
        if engine.durable:
            fsync_directory(target.parent)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    if settings.storage == "local":
        ensure_local_folders(engine)
    engine.change_config(lambda fresh: fresh.with_account(settings))


def _complete_layout(account: Path) -> None:
    """A backup keeps files, not empty folders: the folders a store needs, empty or not, are made again (every mail folder has tmp, new and
    cur; every calendar, task list and address book items and tmp; each store its folder of removed things)."""
    for root, inner in ((account / "store", "folders"), (account / "pim", "collections")):
        if not (root / f"{'store' if inner == 'folders' else 'pim'}.json").exists():
            continue
        (root / inner).mkdir(parents=True, exist_ok=True)
        (root / ".deleted").mkdir(exist_ok=True)
        for directory in (root / inner).iterdir():
            if directory.is_dir():
                for name in ("tmp", "new", "cur") if inner == "folders" else ("items", "tmp"):
                    (directory / name).mkdir(exist_ok=True)


def _kept_collections(archive: _Reading, names: list[str], prefix: str, kinds: set[CollectionKind]) -> set[str]:
    """The calendars, task lists and address books of the chosen kinds in the backup, by their folder name."""
    kept: set[str] = set()
    for name in names:
        relative = name[len(prefix):].split("/")
        if len(relative) == 4 and relative[:2] == ["pim", "collections"] and relative[3] == "collection.json":
            description = _json_object(_read(archive, name))
            try:
                if CollectionKind(description.get("kind")) in kinds:
                    kept.add(relative[2])
            except ValueError:
                continue
    return kept


def _wanted(relative: list[str], collections_kept: set[str]) -> bool:
    if relative[0] == "pim" and len(relative) > 2 and relative[1] == "collections":
        return relative[2] in collections_kept
    if relative[0] == "pim":
        return bool(collections_kept)  # the description and the server's state go with the collections
    return True


def _json_object(data: bytes) -> dict[str, object]:
    try:
        found: object = json.loads(data)
    except ValueError:
        return {}
    return cast(dict[str, object], found) if isinstance(found, dict) else {}


def _with_key(data: bytes, key: DataKey) -> bytes:
    """A store's description names the key of the computer it is on: this one's."""
    description = _json_object(data)
    if description.get("encrypted") is True:
        description["key_id"] = key.key_id.hex()
    return json.dumps(description, indent=2).encode("utf-8")


def _dav_state_for(data: bytes, collections_kept: set[str]) -> bytes:
    """The server's state only for the collections that came back (a state without its collection would mislead the next sync)."""
    state = _json_object(data)
    collections = state.get("collections")
    if isinstance(collections, dict):
        state["collections"] = {key: value for key, value in cast(dict[str, object], collections).items() if key in collections_kept}
    return json.dumps(state, indent=1).encode("utf-8")


# --- an account that is here: what it lacks, item by item -------------------------------------------------------------


def _message_id(raw: bytes) -> str:
    value = BytesHeaderParser(policy=compat32).parsebytes(raw[:200_000]).get("Message-ID")
    return _bare(value) if value else ""


def _bare(message_id: str) -> str:
    return message_id.strip().strip("<>").strip().lower()


def _fill_mail(
    engine: Engine, archive: _Reading, names: list[str], prefix: str, here: AccountSettings, replace_existing: bool, report: RestoreReport,
    progress: _Progress,
) -> None:
    """The messages of the backup that are not here. In an account on an IMAP server only the ones that were never on the server (made or
    moved offline): the rest is the server's to bring, or to have deleted since (D-193)."""
    folders: dict[str, tuple[tuple[str, ...], FolderRole]] = {}
    messages: list[tuple[str, list[str]]] = []
    for name in names:
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix):].split("/")
        if len(relative) == 4 and relative[:2] == ["store", "folders"] and relative[3] == "folder.json":
            description = _json_object(_read(archive, name))
            path = description.get("path")
            if isinstance(path, list):
                try:
                    folders[relative[2]] = (tuple(str(part) for part in cast(list[object], path)), FolderRole(description.get("role", "other")))
                except ValueError:
                    continue
        elif _is_message(relative):
            messages.append((name, relative))
    if not messages:
        return
    on_server = _on_server(archive, names, prefix) if here.account.kind is AccountKind.IMAP else set[str]()
    with open_mailbox(engine, here.account.account_id) as mailbox:
        local_ids = mailbox.store.all_store_ids()
        known: dict[tuple[tuple[str, ...], str], str] = {}
        for store_id, indexed in mailbox.index.messages(local_ids).items():
            if indexed.message_id:
                known[(indexed.folder, _bare(indexed.message_id))] = store_id
        for name, relative in messages:
            progress.step(DOING_MESSAGES)
            folder = folders.get(relative[2])
            parsed = parse_name(relative[4])
            if folder is None or parsed is None:
                report.skipped += 1
                continue
            store_id, info = parsed
            raw = _read(archive, name)
            match = store_id if store_id in local_ids else known.get((folder[0], _message_id(raw)))
            properties_name = f"{prefix}store/properties/{store_id}.json"
            if match is not None:
                if replace_existing and properties_name in names:
                    mailbox.store.properties.put_text(match, _read(archive, properties_name).decode("utf-8"), replace_existing=True)
                report.skipped += 1
                continue
            if store_id in on_server:
                report.skipped += 1
                continue
            flags: frozenset[MessageFlag] = split_info(info)[0] if info else frozenset()
            mailbox.ensure_folder(folder[0], folder[1])
            mailbox.restore_here(folder[0], raw, flags, store_id)
            if properties_name in names:
                mailbox.store.properties.put_text(store_id, _read(archive, properties_name).decode("utf-8"), replace_existing=True)
            report.messages += 1


def _on_server(archive: _Reading, names: list[str], prefix: str) -> set[str]:
    """The messages the server had when the backup was made (from the account's IMAP state in the backup)."""
    name = f"{prefix}imap-state.log"
    if name not in names:
        return set()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "imap-state.log"
        path.write_bytes(_read(archive, name))
        state = ImapState(path, durable=False)
        return {entry.store_id for entries in state.entries.values() for entry in entries.values()} | set(state.uploaded)


def _fill_items(
    engine: Engine, archive: _Reading, names: list[str], prefix: str, here: AccountSettings, kinds: set[CollectionKind], replace_existing: bool,
    report: RestoreReport, progress: _Progress,
) -> None:
    """The appointments, contacts and tasks of the backup that are not here, matched by their UID. In a collection on a server only the ones
    that never reached the server; a calendar of this computer that is not here is made again."""
    collections: dict[str, dict[str, object]] = {}
    items: dict[str, list[tuple[str, str]]] = {}
    for name in names:
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix):].split("/")
        if len(relative) == 4 and relative[:2] == ["pim", "collections"] and relative[3] == "collection.json":
            collections[relative[2]] = _json_object(_read(archive, name))
        elif _is_item(relative):
            items.setdefault(relative[2], []).append((name, relative[4]))
    if not collections:
        return
    linked = _linked_items(archive, names, prefix)
    with open_pim(engine, here.account.account_id, create=True) as store:
        local = store.collections()
        for collection_id, description in collections.items():
            try:
                kind = CollectionKind(description.get("kind"))
            except ValueError:
                continue
            if kind not in kinds:
                continue
            server = description.get("server")
            on_server = isinstance(server, dict) and bool(server)
            target = _matching(local, collection_id, kind, str(description.get("name", "")), cast(dict[str, object], server) if on_server else {})
            if target is None:
                if on_server:  # the server brings its collections itself
                    report.skipped += len(items.get(collection_id, []))
                    continue
                target = store.create_collection(kind, str(description.get("name", "Calendar")) or "Calendar", str(description.get("colour", "")), collection_id=collection_id)
                local.append(target)
            for name, item_id in items.get(collection_id, []):
                progress.step(DOING_ITEMS)
                text = _read(archive, name).decode("utf-8", "replace")
                uid = extract(kind, text).uid
                existing = store.index.find_uid(target.collection_id, uid) if uid else None
                if existing is not None:
                    if replace_existing and not target.server:
                        store.put(target.collection_id, text, existing.item_id)
                    report.skipped += 1
                    continue
                if on_server and item_id in linked.get(collection_id, set()):
                    report.skipped += 1  # it was on the server: the server's to bring
                    continue
                store.put(target.collection_id, text, item_id=None if item_id in set(store.item_ids(target.collection_id)) else item_id)
                report.items += 1


def _matching(local: list[CollectionInfo], collection_id: str, kind: CollectionKind, name: str, server: dict[str, object]) -> CollectionInfo | None:
    for info in local:
        if info.collection_id == collection_id:
            return info
    href = str(server.get("href", "")) if server else ""
    for info in local:
        if info.kind is not kind:
            continue
        if href and dict(info.server).get("href") == href:
            return info
        if not href and not info.server and info.name.casefold() == name.casefold():
            return info
    return None


def _linked_items(archive: _Reading, names: list[str], prefix: str) -> dict[str, set[str]]:
    """Per collection: the items that were on the server when the backup was made."""
    name = f"{prefix}pim/dav-state.json"
    if name not in names:
        return {}
    collections = _json_object(_read(archive, name)).get("collections")
    linked: dict[str, set[str]] = {}
    if isinstance(collections, dict):
        for collection_id, state in cast(dict[str, object], collections).items():
            links = cast(dict[str, object], state).get("links") if isinstance(state, dict) else None
            if isinstance(links, dict):
                linked[collection_id] = {str(cast(dict[str, object], link).get("id")) for link in cast(dict[str, object], links).values() if isinstance(link, dict)}
    return linked


# --- rules, settings and passwords ----------------------------------------------------------------------------------


def _restore_rules(engine: Engine, archive: _Reading, names: list[str], replace_existing: bool) -> None:
    if "config/rules.json" not in names:
        return
    backup = parse_rules(_read(archive, "config/rules.json").decode("utf-8"))
    path = engine.directories.rules_file
    here = load_rules(path) if path.exists() else RuleSet()
    by_id = {rule.rule_id: rule for rule in backup.rules}
    kept = tuple(by_id.pop(rule.rule_id) if replace_existing and rule.rule_id in by_id else rule for rule in here.rules)
    known = {rule.rule_id for rule in kept}
    save_rules(path, RuleSet(kept + tuple(rule for rule in backup.rules if rule.rule_id not in known)))


def _restore_settings(engine: Engine, archive: _Reading, names: list[str], sealed: set[str], key: DataKey, replace_existing: bool, report: RestoreReport) -> None:
    directories = engine.directories
    if "config/tags.json" in names:
        backup = parse_tags(_read(archive, "config/tags.json").decode("utf-8"))
        here = load_tags(directories.tags_file) if directories.tags_file.exists() else TagColours()
        by_key = {entry.key: entry for entry in backup.entries}
        kept = tuple(by_key.pop(entry.key) if replace_existing and entry.key in by_key else entry for entry in here.entries)
        known = {entry.key for entry in kept}
        save_tags(directories.tags_file, TagColours(kept + tuple(entry for entry in backup.entries if entry.key not in known)))
    if "config/sidebar.json" in names and (replace_existing or not directories.sidebar_file.exists()):
        sidebar = parse_sidebar(_read(archive, "config/sidebar.json").decode("utf-8"))
        with engine.sidebar_lock:
            save_sidebar(directories.sidebar_file, sidebar)
    junk = "data/junk-filter.bin"
    if junk in names and (replace_existing or not directories.junk_filter_file.exists()):
        data = _read(archive, junk)
        with engine.junk.lock:
            replace_file_atomically(directories.junk_filter_file, encrypt_message(data, key) if junk in sealed else data, engine.durable)
            engine.junk.words = None  # read again from the file that came back
    if "config/window.json" in names:
        report.window_settings = _read(archive, "config/window.json").decode("utf-8")


def _restore_passwords(engine: Engine, passwords: dict[str, dict[str, str]], report: RestoreReport, replace_existing: bool) -> None:
    roles = {role.value: role for role in ServerRole}
    config = engine.config()
    present = {settings.account.account_id for settings in config.accounts}
    for account_id, kept in passwords.items():
        if account_id not in present:
            continue
        made = account_id in report.made
        for role_name, password in kept.items():
            role = roles.get(role_name)
            if role is not None and (made or replace_existing or engine.passwords.get(account_id, role) is None):
                engine.passwords.set(account_id, role, password)


# --- this computer's data set aside (Remove all data on this computer first) ------------------------------------------


def _config_files(engine: Engine) -> tuple[Path, ...]:
    directories = engine.directories
    return directories.rules_file, directories.sidebar_file, directories.tags_file


def _set_aside(engine: Engine) -> Path:
    """Move this computer's mail, calendars, contacts, tasks, rules and settings aside (never the server's: nothing is sent). The
    accounts are taken out of the settings; this computer's key stays."""
    directories = engine.directories
    aside = directories.data_directory / f"restore-aside-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    (aside / "config").mkdir(parents=True)
    replace_file_atomically(aside / "config" / "config.json", directories.config_file.read_bytes() if directories.config_file.exists() else b"", engine.durable)
    with _undo_on_failure(lambda: _put_back(engine, aside)):
        for path in _config_files(engine):
            if path.exists():
                os.replace(path, aside / "config" / path.name)
        accounts = directories.data_directory / "accounts"
        if accounts.exists():
            os.replace(accounts, aside / "accounts")
        with engine.junk.lock:
            if directories.junk_filter_file.exists():
                os.replace(directories.junk_filter_file, aside / directories.junk_filter_file.name)
            engine.junk.words = None
        engine.change_config(lambda fresh: AppConfig((), fresh.key_id))
    return aside


@contextmanager
def _undo_on_failure(undo: Callable[[], None]) -> Generator[None]:
    try:
        yield
    except BaseException:
        undo()
        raise


def _put_back(engine: Engine, aside: Path) -> None:
    """Everything as it was before the restore: what the restore wrote goes, what was set aside comes back."""
    directories = engine.directories
    accounts = directories.data_directory / "accounts"
    restored = {settings.account.account_id for settings in engine.config().accounts}
    try:
        if (aside / "accounts").exists():
            shutil.rmtree(accounts, ignore_errors=True)
            os.replace(aside / "accounts", accounts)
        with engine.junk.lock:
            directories.junk_filter_file.unlink(missing_ok=True)
            if (aside / directories.junk_filter_file.name).exists():
                os.replace(aside / directories.junk_filter_file.name, directories.junk_filter_file)
            engine.junk.words = None
        for path in _config_files(engine):
            path.unlink(missing_ok=True)
            if (aside / "config" / path.name).exists():
                os.replace(aside / "config" / path.name, path)
        before = (aside / "config" / "config.json").read_text("utf-8")
        old = load_config(aside / "config" / "config.json") if before else AppConfig()
        engine.change_config(lambda fresh: AppConfig(old.accounts, fresh.key_id))
        for account_id in restored - {settings.account.account_id for settings in old.accounts}:
            for role in ServerRole:
                engine.passwords.delete(account_id, role)
    except (OSError, ChronoMailError) as error:
        raise StorageError(f"The data of this computer could not all be put back; what is missing is in {aside}: {error}") from None
    shutil.rmtree(aside, ignore_errors=True)


def _let_go(engine: Engine, aside: Path, report: RestoreReport) -> None:
    """The restore succeeded: the data set aside goes, and the passwords of the accounts that went with it."""
    restored = {settings.account.account_id for settings in engine.config().accounts}
    try:
        text = (aside / "config" / "config.json").read_text("utf-8")
        old = load_config(aside / "config" / "config.json") if text else AppConfig()
    except (OSError, ChronoMailError):
        old = AppConfig()
    for settings in old.accounts:
        if settings.account.account_id not in restored:
            for role in ServerRole:
                engine.passwords.delete(settings.account.account_id, role)
    try:
        shutil.rmtree(aside)
    except OSError as error:
        report.notes.append(f"The data set aside could not all be removed ({error.strerror}); it is in {aside}")
