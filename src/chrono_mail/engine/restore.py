import dataclasses
import json
import os
import shutil
import tempfile
import time
import zipfile
import zlib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from email.policy import compat32
from pathlib import Path
from typing import Protocol, cast

import pyzipper

from chrono_mail.config import AccountSettings, AppConfig, Rule, RuleSet, ServerRole, TagColours, parse_config, parse_rules, parse_sidebar, parse_tags, save_rules, save_tags
from chrono_mail.config.files import load_config
from chrono_mail.config.rules import load_rules
from chrono_mail.config.sidebar import save_sidebar
from chrono_mail.config.tags import load_tags
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionInfo, CollectionKind, ConfigurationError, CryptoError, FolderRole, MessageFlag, StorageError
from chrono_mail.crypto import DataKey, encrypt_message, generate_data_key
from chrono_mail.engine.backup import CANCEL, FORMAT_VERSION, JOB_LOCK, LOCAL_ITEMS, LOCAL_MAIL, MANIFEST_ENTRY
from chrono_mail.engine.commands_schedule import SCHEDULED, paused_if_overdue
from chrono_mail.engine.local import SETUP_LOCK, ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import OUTBOX, STANDARD_FOLDERS, Engine
from chrono_mail.pimstore.extract import extract
from chrono_mail.store import Store
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.store.layout import parse_name, split_info
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.sync.pop3_state import Pop3State
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "restore.py 3.3.343"

# --- Version history ----------------------------------------------------
# v3.3.343: what was downloaded (POP3) never names a message that did not come back, so it is never deleted on the server (RV-61); every
#           file of a backup has a size limit (RV-63); a damaged description of a calendar, a folder, a message's details or a setting is
#           left out and told, the restore goes on (RV-64, RV-65); Replace keeps where an account's mail is kept and its encryption
#           (RV-67); a failed restore puts the data back whole or not at all (RV-68); a Calendars-and-contacts account is found by its server
#           and login (RV-73); the items of a collection are read once (RV-75)
# v3.0.339: a name in a backup that leads out of its account's folder is never written (RV-59); the restore waits until the checks and sends
#           that run have ended and holds new ones back (RV-60)
# v2.4.334: the report names where each damaged message or item was: its folder or calendar and its account (the Windows computer's note)
# v2.4.328: every backup restores (FIXES-2.4.326.md phase 2): an account without files comes back (its folder is made first, W1); a damaged
#           message or item is left out and told, the restore goes on, and the data set aside is kept then (W5); a damaged entry is named,
#           never its bytes, and a cut-off backup is "damaged or incomplete" (W4); only the rules of the accounts that came back (R2)
# v2.4.327: nothing that comes back is sent by itself: the Outbox's messages go to Drafts, a scheduled message whose time passed is paused
#           (D-245, the Windows computer's W6)
# v2.4.326: the contents per account (Local Folders and this computer apart, with their parts and counts); a restore of the chosen
#           accounts, parts and passwords only (D-244)
# v2.4.324: Initial: Menu > File > Restore…: a .cmz backup back on this computer: an account that is not here whole, under its own
#           identity; one that is here item by item, the server's data left to the server (D-193); "Remove all data on this computer
#           first" sets this computer's data aside and removes it only after the restore succeeded; "Replace items that already
#           exist" (D-244)

DOING_MESSAGES: str = "Restoring messages"  # the progress reports (the status line and the Restore window)
DOING_ITEMS: str = "Restoring calendars, contacts and tasks"
MAXIMUM_MANIFEST_BYTES: int = 200_000_000
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024  # a message in a backup: the same limit as a download
MAXIMUM_ENTRY_BYTES: int = 200_000_000  # any other file in a backup (a crafted small file can unpack to far more: RV-63)
_SETTING_WORDS: dict[str, str] = {  # the settings files of a backup, as the report names one that could not be read
    "config/rules.json": "the rules", "config/tags.json": "the tag colours", "config/sidebar.json": "the order of the folder list",
    "data/junk-filter.bin": "what the junk filter learned", "config/window.json": "the window settings", "config/passwords.json": "the passwords",
}
DRAFTS: tuple[str, ...] = ("Drafts",)  # where the Outbox's messages go when the store has no folder for drafts yet
_ITEM_KINDS: dict[str, CollectionKind] = {"calendars": CollectionKind.EVENTS, "contacts": CollectionKind.CONTACTS, "tasks": CollectionKind.TASKS}


class NotABackup(UsageError):
    """The file is not a backup of Chrono Mail (or it is damaged)."""


class DamagedEntry(NotABackup):
    """One file in the backup cannot be read back (damaged): a message or an item is left out and told, the restore goes on (W5)."""


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
class ContentsEntry:
    """One row of the Restore window's account list: an account of the backup (Local Folders and this computer's calendars, contacts and
    tasks apart, as in the Backup window), the parts it has and how many messages and items."""

    entry_id: str  # the account's id, LOCAL_MAIL or LOCAL_ITEMS
    name: str
    parts: tuple[str, ...]
    messages: int
    items: int
    here: bool  # the account exists on this computer: it is filled in item by item, never replaced whole


@dataclass(frozen=True, slots=True)
class BackupContents:
    """The backup's contents page, for the Restore window."""

    created: str
    version: str
    parts: tuple[str, ...]
    accounts: tuple[ContentsEntry, ...]
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
    entries: tuple[str, ...] | None = None  # the accounts to restore (ids, LOCAL_MAIL, LOCAL_ITEMS); None: every one in the backup
    parts: frozenset[str] | None = None  # the parts to restore; None: every one in the backup
    passwords: bool = True  # the account passwords, when the backup holds them


@dataclass
class RestoreReport:
    made: list[str] = field(default_factory=list[str])  # accounts that came back whole
    filled: list[str] = field(default_factory=list[str])  # accounts that were here and got what they lacked
    messages: int = 0
    items: int = 0
    skipped: int = 0  # messages and items that were here already (or that the server holds)
    to_drafts: int = 0  # messages that waited in the Outbox: in Drafts now, never sent by themselves (D-245)
    paused: int = 0  # scheduled messages whose time had passed: paused (D-245)
    damaged: list[str] = field(default_factory=list[str])  # messages and items of the backup that could not be read: left out (W5)
    damaged_places: list[str] = field(default_factory=list[str])  # where they were, for the person: "1 message in Inbox of Work"
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
        raise NotABackup(_unreadable_words(path)) from None
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


def _unreadable_words(path: Path) -> str:
    """What a file that does not open as a backup is: a ZIP file that is cut off or damaged, or no backup at all."""
    try:
        with path.open("rb") as file:
            start = file.read(4)
    except OSError:
        start = b""
    return "This backup is damaged or incomplete" if start == b"PK\x03\x04" else "This file is not a backup of Chrono Mail"


def _read(archive: _Reading, name: str) -> bytes:
    try:
        size = archive.getinfo(name).file_size
        if name == MANIFEST_ENTRY and size > MAXIMUM_MANIFEST_BYTES:
            raise NotABackup("This file is not a backup of Chrono Mail")
        if size > (MAXIMUM_MESSAGE_BYTES if "/cur/" in name or "/new/" in name else MAXIMUM_ENTRY_BYTES):
            raise DamagedEntry(f"The backup is damaged: {name} is too large")  # never read: a crafted file can unpack to far more (RV-63)
        return archive.read(name)
    except RuntimeError:  # pyzipper: a wrong password
        raise WrongBackupPassword("This password does not open the backup") from None
    except (zipfile.BadZipFile, pyzipper.BadZipFile, OSError, ValueError, EOFError, zlib.error):  # zlib: damaged compressed data
        raise DamagedEntry(f"The backup is damaged: {name} cannot be read") from None  # never the error's text: it may hold the entry's raw bytes (W4)


def read_contents(engine: Engine, path: Path, password: str | None) -> BackupContents:
    """What a backup holds (its contents page), after the backup password when it is protected."""
    archive, manifest, protected = _open(path, password)
    try:
        backup_config = _backup_config(archive)
        accounts = _entries(engine.config(), archive, manifest, backup_config)
    finally:
        archive.close()
    parts = manifest.get("parts")
    return BackupContents(
        str(manifest.get("created", "")), str(manifest.get("version", "")), tuple(str(part) for part in parts) if isinstance(parts, list) else (),
        accounts, _number(manifest.get("messages")), _number(manifest.get("items")), manifest.get("passwords") is True, protected,
    )


def _entries(config: AppConfig, archive: _Reading, manifest: dict[str, object], backup_config: AppConfig) -> tuple[ContentsEntry, ...]:
    """The rows of the account list, in the order of the Backup window (Local Folders and this computer last); the counts come from the
    backup's list of files, nothing is decrypted for them."""
    names = archive.namelist()
    rows = _accounts(manifest)
    raw_parts = manifest.get("parts")
    in_backup = {str(part) for part in cast(list[object], raw_parts)} if isinstance(raw_parts, list) else set[str]()
    entries: list[ContentsEntry] = []
    local: list[ContentsEntry] = []
    for settings in backup_config.accounts:
        account_id = settings.account.account_id
        prefix = f"data/accounts/{account_id}/"
        name, mail, items = rows.get(account_id, ("", True, True))
        relatives = [name_in[len(prefix):].split("/") for name_in in names if name_in.startswith(prefix)]
        messages = sum(1 for relative in relatives if _is_message(relative))
        kinds = set(_collection_kinds(archive, names, prefix).values()) if items else set[CollectionKind]()
        item_count = sum(1 for relative in relatives if _is_item(relative)) if items else 0
        item_parts = tuple(part for part, kind in _ITEM_KINDS.items() if kind in kinds and part in in_backup)
        mail_parts: tuple[str, ...] = ("messages",) if mail and "messages" in in_backup and settings.account.kind is not AccountKind.DAV else ()
        here = _same_here(config, settings) is not None
        if settings.account.kind is AccountKind.LOCAL:
            if mail_parts:
                local.append(ContentsEntry(LOCAL_MAIL, "Local Folders", mail_parts, messages, 0, here))
            if item_parts:
                local.append(ContentsEntry(LOCAL_ITEMS, "On this computer", item_parts, 0, item_count, here))
            continue
        entries.append(ContentsEntry(account_id, name or _plain_name(settings), mail_parts + item_parts, messages, item_count, here))
    return tuple(entries + local)


def _collection_kinds(archive: _Reading, names: list[str], prefix: str) -> dict[str, CollectionKind]:
    """The calendars, task lists and address books of an account in the backup, by their folder name."""
    kinds: dict[str, CollectionKind] = {}
    for name in names:
        relative = name[len(prefix):].split("/") if name.startswith(prefix) else list[str]()
        if len(relative) == 4 and relative[:2] == ["pim", "collections"] and relative[3] == "collection.json":
            try:
                kinds[relative[2]] = CollectionKind(_json_object(_read(archive, name)).get("kind"))
            except (ValueError, DamagedEntry):  # a damaged description: that collection is left out and told, the rest opens (RV-64)
                continue
    return kinds


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
    if account.kind is AccountKind.LOCAL:
        return None
    if account.kind is AccountKind.DAV:  # the same calendar server and login (RV-73)
        login = _dav_login(settings)
        return next((item for item in config.accounts if item.account.kind is AccountKind.DAV and login[0] and _dav_login(item) == login), None)
    for item in config.accounts:
        if (
            item.account.kind is account.kind and item.account.incoming.username.strip().lower() == account.incoming.username.strip().lower()
            and item.account.incoming.host.strip().lower() == account.incoming.host.strip().lower()
        ):
            return item
    return None


def _dav_login(settings: AccountSettings) -> tuple[str, str]:
    """A Calendars-and-contacts account's server and login, written the same way whatever the spelling."""
    return settings.dav_url.strip().rstrip("/").lower(), (settings.dav_username or settings.account.incoming.username).strip().lower()


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
            with engine.server_work.paused(CANCEL.is_set):  # the checks and sends that run end first, new ones wait: never a POP3 delete
                # while this computer's mail is set aside (RV-60)
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
                if aside is not None and report.damaged:  # never lose mail: what was here stays until the person removes it
                    report.notes.append(f"Because part of the backup was damaged, the data that was on this computer before is kept in {aside}")
                elif aside is not None:
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
    if choice.parts is not None:
        parts &= choice.parts
    kinds = {kind for part, kind in _ITEM_KINDS.items() if part in parts}
    backup_config = _backup_config(archive)
    chosen = [(settings, *_chosen(choice.entries, settings)) for settings in backup_config.accounts]
    chosen = [(settings, "messages" in parts and mail, bool(kinds) and items) for settings, mail, items in chosen if mail or items]
    report = RestoreReport()
    passwords = dict[str, dict[str, str]]()
    if choice.passwords:
        with _left_out_when_damaged("config/passwords.json", report):
            passwords = _passwords(archive, names)
    progress = _Progress(engine, _totals(archive, names, [(settings.account.account_id, mail, items) for settings, mail, items in chosen], kinds))
    local_mail = any(mail and settings.account.kind is AccountKind.LOCAL for settings, mail, _ in chosen)  # Local Folders' mail comes back too
    for settings, mail, items in chosen:
        if items:
            report.damaged.extend(_damaged_collections(archive, names, f"data/accounts/{settings.account.account_id}/"))
    for settings, mail, items in sorted(chosen, key=lambda row: row[0].account.kind is not AccountKind.LOCAL):  # Local Folders first: accounts may keep their mail in it
        _one_account(engine, archive, names, sealed, kinds if items else set[CollectionKind](), mail, local_mail, key, settings, choice, report, progress)
    if "rules" in parts:
        with _left_out_when_damaged("config/rules.json", report):
            _restore_rules(engine, archive, names, choice.replace_existing, _ids_here(engine.config(), [settings for settings, _, _ in chosen]))
    if "settings" in parts:
        _restore_settings(engine, archive, names, sealed, key, choice.replace_existing, report)
    restored = {settings.account.account_id for settings, _, _ in chosen}
    _restore_passwords(engine, {account_id: kept for account_id, kept in passwords.items() if account_id in restored}, report, choice.replace_existing)
    report.damaged_places = _damaged_places(archive, names, report.damaged, backup_config)
    return report


def _damaged_collections(archive: _Reading, names: list[str], prefix: str) -> list[str]:
    """The items of the calendars, task lists and address books whose description cannot be read: they are left out and told (RV-64)."""
    damaged: list[str] = []
    for name in names:
        relative = name[len(prefix):].split("/") if name.startswith(prefix) else list[str]()
        if len(relative) == 4 and relative[:2] == ["pim", "collections"] and relative[3] == "collection.json":
            try:
                _read(archive, name)
            except DamagedEntry:
                inside = f"{prefix}pim/collections/{relative[2]}/items/"
                damaged.extend([item for item in names if item.startswith(inside)] or [name])
    return damaged


@contextmanager
def _left_out_when_damaged(name: str, report: RestoreReport) -> Generator[None]:
    """A settings file of the backup that cannot be read is left out and told; the restore goes on (RV-65)."""
    try:
        yield
    except (DamagedEntry, ConfigurationError, UnicodeDecodeError, ValueError):
        report.damaged.append(name)
        report.notes.append(f"Part of the backup could not be read and was left out: {_SETTING_WORDS.get(name, name)}")


def _damaged_places(archive: _Reading, names: list[str], damaged: list[str], backup_config: AppConfig) -> list[str]:
    """Where the damaged messages and items were, so the person can tell what is missing: "2 messages in Inbox of Work", "1 item in
    Birthdays of On this computer" (a damaged file's own subject cannot be read)."""
    accounts = {settings.account.account_id: settings for settings in backup_config.accounts}
    counts: dict[str, int] = {}
    for name in damaged:
        relative = name.split("/")
        if len(relative) < 8 or relative[:2] != ["data", "accounts"]:
            continue
        account_id, kind, folder = relative[2], relative[4], relative[5]
        settings = accounts.get(account_id)
        is_mail = relative[3:5] == ["store", "folders"]
        owner = ("Local Folders" if is_mail else "On this computer") if settings is not None and settings.account.kind is AccountKind.LOCAL else (
            _plain_name(settings) if settings is not None else "an account")
        description = f"data/accounts/{account_id}/{relative[3]}/{kind}/{folder}/{'folder.json' if is_mail else 'collection.json'}"
        place = folder
        if description in names:
            try:
                found = _json_object(_read(archive, description))
                path, title = found.get("path"), found.get("name")
                place = "/".join(str(part) for part in cast(list[object], path)) if is_mail and isinstance(path, list) else title if isinstance(title, str) else folder
            except DamagedEntry:
                pass
        key = f"{'message' if is_mail else 'item'}\t{place} of {owner}"
        counts[key] = counts.get(key, 0) + 1
    return [f"{counted(count, word)} in {where}" for key, count in counts.items() for word, where in [key.split("\t", 1)]]


def _chosen(entries: tuple[str, ...] | None, settings: AccountSettings) -> tuple[bool, bool]:
    """Whether the person chose this account's mail and its calendars, contacts and tasks (Local Folders and this computer apart)."""
    account_id = settings.account.account_id
    if entries is None:
        return True, True
    if settings.account.kind is AccountKind.LOCAL:
        return LOCAL_MAIL in entries, LOCAL_ITEMS in entries
    return account_id in entries, account_id in entries


def _totals(archive: _Reading, names: list[str], chosen: list[tuple[str, bool, bool]], kinds: set[CollectionKind]) -> dict[str, int]:
    """How many messages and items the restore goes through (the progress counts them)."""
    totals = {DOING_MESSAGES: 0, DOING_ITEMS: 0}
    for account_id, mail, items in chosen:
        prefix = f"data/accounts/{account_id}/"
        collections = {folder for folder, kind in _collection_kinds(archive, names, prefix).items() if kind in kinds} if items else set[str]()
        for name in names:
            if name.startswith(prefix):
                relative = name[len(prefix):].split("/")
                if mail and _is_message(relative):
                    totals[DOING_MESSAGES] += 1
                elif _is_item(relative) and relative[2] in collections:
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
    engine: Engine, archive: _Reading, names: list[str], sealed: set[str], kinds: set[CollectionKind], mail: bool, local_mail: bool, key: DataKey,
    settings: AccountSettings, choice: RestoreChoice, report: RestoreReport, progress: _Progress,
) -> None:
    """One account of the backup: its mail when mail, its calendars, contacts and tasks of these kinds; its definition always."""
    here = _same_here(engine.config(), settings)
    prefix = f"data/accounts/{settings.account.account_id}/"
    if here is None:
        _restore_whole(
            engine, archive, [name for name in names if name.startswith(prefix)], prefix, sealed, kinds, mail, local_mail, key, settings, report, progress,
        )
        report.made.append(settings.account.account_id)
        return
    account_id = here.account.account_id
    if choice.replace_existing and account_id == settings.account.account_id and settings.account.kind is not AccountKind.LOCAL:
        # the account's own settings as they were in the backup; where its mail is kept and its encryption stay as here (RV-67)
        kept = dataclasses.replace(settings, storage=here.storage, encrypted=here.encrypted)
        engine.change_config(lambda fresh: fresh.with_account(kept))
    if mail:
        _fill_mail(engine, archive, names, prefix, here, choice.replace_existing, report, progress)
    if kinds:
        _fill_items(engine, archive, names, prefix, here, kinds, choice.replace_existing, report, progress)
    report.filled.append(account_id)


# --- an account that is not here: back whole, under its own identity ------------------------------------------------


def _restore_whole(
    engine: Engine, archive: _Reading, names: list[str], prefix: str, sealed: set[str], kinds: set[CollectionKind], mail: bool, local_mail: bool,
    key: DataKey, settings: AccountSettings, report: RestoreReport, progress: _Progress,
) -> None:
    """The account's files are written in a folder of their own and that folder is renamed into place in one step, so the account comes
    back whole or not at all; only then is it added to the accounts."""
    account_id = settings.account.account_id
    directories = engine.directories
    target = directories.account_directory(account_id)
    building = target.with_name(f".restoring-{account_id}")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)  # made here, not by the first file written: an account may have no files at all (a POP3 account not fetched yet, W1)
    collections_kept = _kept_collections(archive, names, prefix, kinds)
    lost_folders = _damaged_folders(archive, names, prefix) if mail else set[str]()
    try:
        for name in names:
            relative = name[len(prefix):].split("/")
            if not relative[-1] or not _wanted(relative, collections_kept, mail):
                continue
            if not _inside(building, relative):  # a name that leads out of the account's folder ("..", a drive): never written (RV-59)
                report.damaged.append(name)
                continue
            doing = DOING_MESSAGES if _is_message(relative) else DOING_ITEMS if _is_item(relative) else ""
            if doing:
                progress.step(doing)
            if len(relative) > 3 and relative[:2] == ["store", "folders"] and relative[2] in lost_folders:
                if doing:  # a folder whose description cannot be read: its messages are left out and told (RV-65)
                    report.damaged.append(name)
                continue
            try:
                data = _read(archive, name)
            except DamagedEntry:
                if relative[:2] == ["store", "properties"]:  # a message's tags and notes: it comes back without them (RV-65)
                    report.damaged.append(name)
                    _note_without_details(report)
                    continue
                if not doing:
                    raise  # the account's own files (its store, its state): without them it cannot come back whole
                report.damaged.append(name)
                continue
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
        description = building / "store" / "store.json"
        if mail and description.exists():
            sealed_store = _json_object(description.read_bytes()).get("encrypted") is True
            _hold_back(Store.open(building / "store", key if sealed_store else None, durable=engine.durable), engine.clock(), report)
        if (building / "pop3-state.log").exists():
            _downloaded_that_came_back(engine, building, settings, local_mail, key, report)
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


def _downloaded_that_came_back(engine: Engine, building: Path, settings: AccountSettings, local_mail: bool, key: DataKey, report: RestoreReport) -> None:
    """What was downloaded (POP3) names only messages that came back, or that the person deleted here before the backup: a message it
    names that is not here would be deleted on the server ("delete from the server when deleted here"), or never downloaded again (RV-61).
    Left out: the messages of the backup that were damaged, and, for an account that keeps its mail in Local Folders while their mail
    does not come back with it, every message that is not in Local Folders here. A message left out is downloaded again: twice at worst,
    never lost."""
    path = building / "pop3-state.log"
    state = Pop3State(path, durable=False)
    damaged = {parsed[0] for name in report.damaged if (parsed := parse_name(name.rsplit("/", 1)[-1])) is not None}
    present: set[str] | None = None
    if settings.storage == "local" and not local_mail:
        try:
            with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
                present = mailbox.store.all_store_ids()
        except ChronoMailError:
            present = set()
    kept = [
        record for record in state.records.values()
        if record.deleted_on_server or (record.store_id not in damaged and (present is None or record.store_id in present))
    ]
    if len(kept) == len(state.records):
        return
    lines = [f"D\t{record.unique_id}\t{record.store_id}\t{record.downloaded_at}" for record in kept]
    lines += [f"X\t{record.unique_id}\t{record.downloaded_at}" for record in kept if record.deleted_on_server]
    replace_file_atomically(path, "".join(f"{line}\n" for line in lines).encode("utf-8"), engine.durable)


def _damaged_folders(archive: _Reading, names: list[str], prefix: str) -> set[str]:
    """The mail folders of an account in the backup whose description cannot be read, by their folder name (RV-65)."""
    damaged: set[str] = set()
    for name in names:
        relative = name[len(prefix):].split("/") if name.startswith(prefix) else list[str]()
        if len(relative) == 4 and relative[:2] == ["store", "folders"] and relative[3] == "folder.json":
            try:
                _read(archive, name)
            except DamagedEntry:
                damaged.add(relative[2])
    return damaged


def _hold_back(store: Store, now: float, report: RestoreReport) -> None:
    """Mail that comes back from a backup is never sent by itself (D-245): the Outbox's messages go to Drafts, for the person to send; a
    scheduled message whose time has passed is paused (Settings > Scheduled messages > Resume)."""
    folders = store.list_folders()
    if any(folder.path == OUTBOX for folder in folders):
        waiting = store.list_messages(OUTBOX)
        if waiting:
            drafts = next((folder.path for folder in folders if folder.role is FolderRole.DRAFTS), None) or store.ensure_folder(DRAFTS, FolderRole.DRAFTS)[0].path
            for message in waiting:
                store.move(message.ref, drafts)
                report.to_drafts += 1
    if any(folder.path == SCHEDULED for folder in folders):
        for message in store.list_messages(SCHEDULED):
            try:
                paused = paused_if_overdue(store.read(message.ref), now)
            except ChronoMailError:
                continue  # an unreadable scheduled message is never sent (the minute check tells it)
            if paused is not None:
                store.replace(message.ref, paused)
                report.paused += 1


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


def _inside(folder: Path, relative: list[str]) -> bool:
    """Does this name from a backup stay inside the folder it is written to? A crafted backup could otherwise write files anywhere the
    person may write (".." parts, a drive or a backslash on Windows), for example a program that starts with the desktop (RV-59)."""
    if any(part in ("", ".", "..") or "\\" in part or ":" in part or "\0" in part for part in relative):
        return False
    return folder.resolve().joinpath(*relative).resolve().is_relative_to(folder.resolve())


def _kept_collections(archive: _Reading, names: list[str], prefix: str, kinds: set[CollectionKind]) -> set[str]:
    """The calendars, task lists and address books of the chosen kinds in the backup, by their folder name."""
    return {folder for folder, kind in _collection_kinds(archive, names, prefix).items() if kind in kinds}


def _wanted(relative: list[str], collections_kept: set[str], mail: bool) -> bool:
    if relative[0] == "pim" and len(relative) > 2 and relative[1] == "collections":
        return relative[2] in collections_kept
    if relative[0] == "pim":
        return bool(collections_kept)  # the description and the server's state go with the collections
    return mail  # the messages, and what the account knows of its server's mail (POP3 and IMAP state)


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
    damaged_folders: set[str] = set()
    messages: list[tuple[str, list[str]]] = []
    for name in names:
        if not name.startswith(prefix):
            continue
        relative = name[len(prefix):].split("/")
        if len(relative) == 4 and relative[:2] == ["store", "folders"] and relative[3] == "folder.json":
            try:
                description = _json_object(_read(archive, name))
            except DamagedEntry:  # its messages are left out and told; the rest comes back (RV-65)
                damaged_folders.add(relative[2])
                continue
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
            if relative[2] in damaged_folders:
                report.damaged.append(name)
                continue
            if folder is None or parsed is None:
                report.skipped += 1
                continue
            store_id, info = parsed
            try:
                raw = _read(archive, name)
            except DamagedEntry:
                report.damaged.append(name)
                continue
            match = store_id if store_id in local_ids else known.get((folder[0], _message_id(raw)))
            properties_name = f"{prefix}store/properties/{store_id}.json"
            if match is not None:
                if replace_existing and properties_name in names:
                    _put_properties(mailbox.store, match, archive, properties_name, report)
                report.skipped += 1
                continue
            if store_id in on_server:
                report.skipped += 1
                continue
            flags: frozenset[MessageFlag] = split_info(info)[0] if info else frozenset()
            target, role = folder
            if target == OUTBOX:  # never sent by itself (D-245): the person sends it from Drafts
                try:
                    target = mailbox.role_folder(FolderRole.DRAFTS)
                except StorageError:
                    target = DRAFTS
                role = FolderRole.DRAFTS
                report.to_drafts += 1
            elif target == SCHEDULED and (paused := paused_if_overdue(raw, engine.clock())) is not None:
                raw = paused
                report.paused += 1
            mailbox.ensure_folder(target, role)
            mailbox.restore_here(target, raw, flags, store_id)
            if properties_name in names:
                _put_properties(mailbox.store, store_id, archive, properties_name, report)
            report.messages += 1


def _put_properties(store: Store, store_id: str, archive: _Reading, name: str, report: RestoreReport) -> None:
    """A message's tags and notes from the backup; damaged ones are left out, the message comes back without them (RV-65)."""
    try:
        text = _read(archive, name).decode("utf-8")
    except (DamagedEntry, UnicodeDecodeError):
        report.damaged.append(name)
        _note_without_details(report)
        return
    store.properties.put_text(store_id, text, replace_existing=True)


def _note_without_details(report: RestoreReport) -> None:
    words = "Some messages came back without their tags and notes: their details in the backup could not be read"
    if words not in report.notes:
        report.notes.append(words)


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
            try:
                collections[relative[2]] = _json_object(_read(archive, name))
            except DamagedEntry:
                continue  # its items are told as damaged (RV-64)
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
            taken = set(store.item_ids(target.collection_id))  # read once per collection, not once per item (RV-75)
            for name, item_id in items.get(collection_id, []):
                progress.step(DOING_ITEMS)
                try:
                    text = _read(archive, name).decode("utf-8", "replace")
                except DamagedEntry:
                    report.damaged.append(name)
                    continue
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
                taken.add(store.put(target.collection_id, text, item_id=None if item_id in taken else item_id))
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


def _ids_here(config: AppConfig, chosen: list[AccountSettings]) -> dict[str, str]:
    """The id each chosen account of the backup has here now (its own, or that of the same account that was here already)."""
    found: dict[str, str] = {}
    for settings in chosen:
        here = _same_here(config, settings)
        if here is not None:
            found[settings.account.account_id] = here.account.account_id
    return found


def _rules_for(rules: RuleSet, ids_here: dict[str, str]) -> RuleSet:
    """The rules of the accounts that came back, naming them by their ids here; a rule for every account stays; a rule only for accounts
    that were not chosen stays out (it would name accounts that are not here)."""
    kept: list[Rule] = []
    for rule in rules.rules:
        if not rule.accounts:
            kept.append(rule)
            continue
        accounts = tuple(dict.fromkeys(ids_here[account] for account in rule.accounts if account in ids_here))
        if accounts:
            kept.append(dataclasses.replace(rule, accounts=accounts))
    return RuleSet(tuple(kept))


def _restore_rules(engine: Engine, archive: _Reading, names: list[str], replace_existing: bool, ids_here: dict[str, str]) -> None:
    if "config/rules.json" not in names:
        return
    backup = _rules_for(parse_rules(_read(archive, "config/rules.json").decode("utf-8")), ids_here)
    path = engine.directories.rules_file
    here = load_rules(path) if path.exists() else RuleSet()
    by_id = {rule.rule_id: rule for rule in backup.rules}
    kept = tuple(by_id.pop(rule.rule_id) if replace_existing and rule.rule_id in by_id else rule for rule in here.rules)
    known = {rule.rule_id for rule in kept}
    save_rules(path, RuleSet(kept + tuple(rule for rule in backup.rules if rule.rule_id not in known)))


def _restore_settings(engine: Engine, archive: _Reading, names: list[str], sealed: set[str], key: DataKey, replace_existing: bool, report: RestoreReport) -> None:
    directories = engine.directories
    backup: TagColours | None = None
    if "config/tags.json" in names:
        with _left_out_when_damaged("config/tags.json", report):
            backup = parse_tags(_read(archive, "config/tags.json").decode("utf-8"))
    if backup is not None:
        here = load_tags(directories.tags_file) if directories.tags_file.exists() else TagColours()
        by_key = {entry.key: entry for entry in backup.entries}
        kept = tuple(by_key.pop(entry.key) if replace_existing and entry.key in by_key else entry for entry in here.entries)
        known = {entry.key for entry in kept}
        save_tags(directories.tags_file, TagColours(kept + tuple(entry for entry in backup.entries if entry.key not in known)))
    if "config/sidebar.json" in names and (replace_existing or not directories.sidebar_file.exists()):
        with _left_out_when_damaged("config/sidebar.json", report):
            sidebar = parse_sidebar(_read(archive, "config/sidebar.json").decode("utf-8"))
            with engine.sidebar_lock:
                save_sidebar(directories.sidebar_file, sidebar)
    junk = "data/junk-filter.bin"
    if junk in names and (replace_existing or not directories.junk_filter_file.exists()):
        with _left_out_when_damaged(junk, report):
            data = _read(archive, junk)
            with engine.junk.lock:
                replace_file_atomically(directories.junk_filter_file, encrypt_message(data, key) if junk in sealed else data, engine.durable)
                engine.junk.words = None  # read again from the file that came back
    if "config/window.json" in names:
        with _left_out_when_damaged("config/window.json", report):
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
        if accounts.exists():  # what the restore wrote goes aside in one step (removed with the rest below): never half of it (RV-68)
            os.replace(accounts, aside / "restored")
        if (aside / "accounts").exists():
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
