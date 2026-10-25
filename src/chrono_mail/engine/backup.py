import json
import os
import secrets
import string
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

import pyzipper

from chrono_mail.config import AccountSettings, AppConfig, ServerRole, config_to_text
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, CryptoError
from chrono_mail.core.appinfo import program_version
from chrono_mail.crypto import DataKey, decrypt_message
from chrono_mail.crypto.cipher import MAGIC
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.store.atomic import fsync_directory
from chrono_mail.store.layout import parse_name
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "backup.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v3.3.343: what was downloaded (POP3) is taken before the messages are listed, so it never names a message the backup lacks (RV-61); a
#           message deleted while the backup runs no longer searches the whole mailbox again (RV-69)
# v2.4.324: one backup or restore at a time (JOB_LOCK); backup.cancel stops either (CANCEL); backup_running
# v2.4.322: Initial: Menu > File > Back up…: a .cmz file (a ZIP, AES-256 with a generated password when protected) with
#           the chosen parts of the chosen accounts, exactly as kept here but readable without this computer's key (D-244)

FORMAT_VERSION: int = 1
MANIFEST_ENTRY: str = "chrono-mail-backup.json"  # the contents page: what the backup holds (read first by a restore)
EXTENSION: str = ".cmz"
PASSWORD_LENGTH: int = 24
PASSWORD_ALPHABET: str = string.ascii_uppercase + string.ascii_lowercase + string.digits  # no signs: easy to copy, type and keep
LOCAL_MAIL: str = "local-folders"  # the backup's name for the mail of Local Folders
LOCAL_ITEMS: str = "this-computer"  # the backup's name for the calendars, contacts and tasks kept only on this computer
MAXIMUM_WINDOW_SETTINGS_CHARACTERS: int = 20_000_000
PROGRESS_EVERY: int = 100

MESSAGES: str = "messages"
RULES: str = "rules"
CALENDARS: str = "calendars"
CONTACTS: str = "contacts"
TASKS: str = "tasks"
SETTINGS: str = "settings"
PARTS: tuple[str, ...] = (MESSAGES, RULES, CALENDARS, CONTACTS, TASKS, SETTINGS)
ITEM_PARTS: dict[CollectionKind, str] = {CollectionKind.EVENTS: CALENDARS, CollectionKind.CONTACTS: CONTACTS, CollectionKind.TASKS: TASKS}

DOING_MESSAGES: str = "Backing up messages"  # the progress reports (the status line and the Back up window)
DOING_ITEMS: str = "Backing up calendars, contacts and tasks"
DOING_SETTINGS: str = "Backing up settings"

_LEFT_OUT_NAMES: frozenset[str] = frozenset({".deleted", "tmp"})  # removed folders and messages still being written
_INDEX_SUFFIXES: tuple[str, ...] = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal")  # rebuilt after a restore

CANCEL: threading.Event = threading.Event()  # set by backup.cancel: the backup or restore that runs stops
JOB_LOCK: threading.Lock = threading.Lock()  # one backup or restore at a time


class BackupCancelled(ChronoMailError):
    """The person cancelled the backup (or the program ended): no file was left behind."""


class _Archive(Protocol):
    """The part of pyzipper's ZIP file the backup uses (pyzipper has no type information of its own)."""

    def writestr(self, zinfo_or_arcname: str, data: bytes) -> None: ...

    def setpassword(self, pwd: bytes) -> None: ...

    def close(self) -> None: ...


def new_backup_password() -> str:
    """A password for a protected backup: 24 random capitals, lower-case letters and digits (about 143 bits), made by the
    program so a backup is never protected by a password that can be guessed."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def valid_backup_password(password: str) -> bool:
    return len(password) == PASSWORD_LENGTH and all(character in PASSWORD_ALPHABET for character in password)


def backup_running() -> bool:
    """Whether a backup or restore runs now (Quit asks first)."""
    return JOB_LOCK.locked()


def cancel_backup() -> bool:
    """Stop the backup or restore that runs (a backup's unfinished file is removed). Whether one was running."""
    running = JOB_LOCK.locked()
    if running:
        CANCEL.set()
    return running


@dataclass(frozen=True, slots=True)
class BackupChoice:
    """What the person chose to back up."""

    path: Path
    accounts: tuple[str, ...]  # account ids, LOCAL_MAIL and LOCAL_ITEMS
    parts: frozenset[str]
    passwords: bool = False
    password: str | None = None  # None: not protected
    window_settings: str = ""  # the window's own settings as the window wrote them (JSON); empty: none
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class BackupReport:
    path: Path
    size: int
    accounts: int
    messages: int
    items: int
    unreadable: int  # files kept exactly as they were because they could not be decrypted (damaged here already)


@dataclass(frozen=True, slots=True)
class BackupEntry:
    """One line of the Back up window's account list: an account (or Local Folders, or this computer's calendars) and what it has."""

    entry_id: str
    name: str
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _File:
    """A file to put in the backup: where it is now, its part, and for a message the store it is in (found again by its id when it moved)."""

    path: Path
    doing: str
    counted: bool = False  # a message, an item or a setting: what the counts and the progress tell
    store_root: Path | None = None
    data: bytes | None = None  # taken when the files were listed (what was downloaded, RV-61); None: read when it is written


@dataclass
class _Messages:
    """Per store: where each message is now (by its id) and which messages are in the backup already, so a message that moves while
    the backup runs is backed up exactly once."""

    places: dict[Path, dict[str, Path]] = field(default_factory=dict)
    done: dict[Path, set[str]] = field(default_factory=dict)


def backup_entries(engine: Engine) -> tuple[BackupEntry, ...]:
    """The accounts a backup can hold, in the order of the folder list (Local Folders and this computer's calendars last), each with
    the parts it has."""
    from chrono_mail.engine.commands_accounts import shown_name  # the account names of the window (D-184)

    config = engine.config()
    entries: list[BackupEntry] = []
    local: list[BackupEntry] = []
    for settings in config.accounts:
        account_id = settings.account.account_id
        item_parts = _item_parts(engine, account_id)
        if account_id == LOCAL_ACCOUNT_ID:
            if engine.directories.store_directory(account_id).is_dir():
                local.append(BackupEntry(LOCAL_MAIL, "Local Folders", (MESSAGES,)))
            if item_parts:
                local.append(BackupEntry(LOCAL_ITEMS, "On this computer", item_parts))
            continue
        mail: tuple[str, ...] = () if settings.account.kind is AccountKind.DAV else (MESSAGES,)
        entries.append(BackupEntry(account_id, shown_name(settings, config.accounts), mail + item_parts))
    return tuple(entries + local)


def _item_parts(engine: Engine, account_id: str) -> tuple[str, ...]:
    kinds = {ITEM_PARTS[kind] for kind in _collection_kinds(engine.directories.pim_directory(account_id)).values()}
    return tuple(part for part in (CALENDARS, CONTACTS, TASKS) if part in kinds)


def _collection_kinds(pim_root: Path) -> dict[str, CollectionKind]:
    """The calendars, task lists and address books kept in a place, by their folder name."""
    kinds: dict[str, CollectionKind] = {}
    base = pim_root / "collections"
    if not base.is_dir():
        return kinds
    for directory in sorted(base.iterdir()):
        try:
            description: object = load_json((directory / "collection.json").read_text("utf-8"))
            if isinstance(description, dict):
                kinds[directory.name] = CollectionKind(cast(dict[str, object], description).get("kind"))
        except (OSError, ValueError):
            continue  # a damaged collection is not readable here either
    return kinds


def write_backup(engine: Engine, choice: BackupChoice) -> BackupReport:
    """Write the backup. Nothing here or on a server changes (D-193): the backup only reads. The file is written under a temporary
    name and only renamed into place when it is complete, so a cancel or a failure leaves nothing behind."""
    target = _check(choice)
    if not JOB_LOCK.acquire(blocking=False):
        raise UsageError("A backup is being made or restored already")
    CANCEL.clear()
    temporary = target.with_name(target.name + ".part")
    try:
        report = _write(engine, choice, target, temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        CANCEL.clear()
        JOB_LOCK.release()
    return report


def _check(choice: BackupChoice) -> Path:
    if not choice.accounts:
        raise UsageError("Choose at least one account to back up")
    unknown = sorted(choice.parts - set(PARTS))
    if unknown or not choice.parts:
        raise UsageError(f"Choose what to back up from: {', '.join(PARTS)}")
    if choice.password is not None and not valid_backup_password(choice.password):
        raise UsageError(f"A backup password is {PASSWORD_LENGTH} capitals, lower-case letters and digits, made by Chrono Mail")
    if choice.passwords and choice.password is None:
        raise UsageError("A backup with the account passwords must be protected with a backup password")
    if len(choice.window_settings) > MAXIMUM_WINDOW_SETTINGS_CHARACTERS:
        raise UsageError("The window's settings are too large to back up")
    if choice.window_settings:
        try:
            if not isinstance(load_json(choice.window_settings), dict):
                raise ValueError
        except ValueError:
            raise UsageError("The window's settings are not readable") from None
    target = choice.path if choice.path.suffix.lower() == EXTENSION else choice.path.with_name(choice.path.name + EXTENSION)
    if target.exists() and not choice.overwrite:
        raise UsageError(f"{target} exists already")
    if not target.parent.is_dir():
        raise UsageError(f"The folder {target.parent} does not exist")
    return target


def _write(engine: Engine, choice: BackupChoice, target: Path, temporary: Path) -> BackupReport:
    config = engine.config()
    key = engine.data_key(config) if config.key_id is not None else None
    chosen = _chosen_accounts(config, choice.accounts)
    files = list(_files(engine, choice, chosen))
    sealed: list[str] = []
    unreadable: list[str] = []
    counts = {DOING_MESSAGES: 0, DOING_ITEMS: 0, DOING_SETTINGS: 0}
    totals = {doing: sum(1 for item in files if item.doing == doing and item.counted) for doing in counts}
    messages = _Messages()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private mail: readable by the person only
    with os.fdopen(descriptor, "wb") as stream:
        archive = _open_archive(stream, choice.password)
        try:
            for name, text in _config_entries(engine, config, choice, chosen):
                _add(archive, name, text.encode("utf-8"))
            for item in files:
                if CANCEL.is_set() or engine.stop_event.is_set():
                    raise BackupCancelled("The backup was cancelled; no file was left behind")
                written = _add_file(engine, archive, item, key, messages, sealed, unreadable)
                if written and item.counted:
                    counts[item.doing] += 1
                    if counts[item.doing] % PROGRESS_EVERY == 0 or counts[item.doing] == totals[item.doing]:
                        engine.progress(item.doing, counts[item.doing], totals[item.doing])
            _add(archive, MANIFEST_ENTRY, _manifest(engine, choice, chosen, sealed, unreadable, counts).encode("utf-8"))
        finally:
            archive.close()
        stream.flush()
        if engine.durable:
            os.fsync(stream.fileno())
    os.replace(temporary, target)
    if engine.durable:
        fsync_directory(target.parent)
    return BackupReport(target, target.stat().st_size, len(chosen), counts[DOING_MESSAGES], counts[DOING_ITEMS], len(unreadable))


def _open_archive(stream: object, password: str | None) -> _Archive:
    if password is None:
        return cast(_Archive, pyzipper.AESZipFile(stream, "w", compression=pyzipper.ZIP_DEFLATED, allowZip64=True))
    archive = cast(_Archive, pyzipper.AESZipFile(stream, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES, allowZip64=True))
    archive.setpassword(password.encode("ascii"))
    return archive


def _add(archive: _Archive, name: str, data: bytes) -> None:
    archive.writestr(name, data)  # compressed, dated now and readable by its owner only when unpacked (the ZIP module's way for a name)


@dataclass(frozen=True, slots=True)
class _Chosen:
    """An account in the backup, and which of its places: its mail, its calendars, contacts and tasks."""

    settings: AccountSettings
    mail: bool
    items: bool


def _chosen_accounts(config: AppConfig, entries: tuple[str, ...]) -> list[_Chosen]:
    known = {settings.account.account_id: settings for settings in config.accounts}
    chosen: dict[str, _Chosen] = {}
    for entry in dict.fromkeys(entries):
        account_id = LOCAL_ACCOUNT_ID if entry in (LOCAL_MAIL, LOCAL_ITEMS) else entry
        settings = known.get(account_id)
        if settings is None or entry == LOCAL_ACCOUNT_ID:
            raise UsageError(f"There is no account {entry} to back up")
        earlier = chosen.get(account_id)
        mail = entry != LOCAL_ITEMS or (earlier is not None and earlier.mail)
        items = entry != LOCAL_MAIL or (earlier is not None and earlier.items)
        chosen[account_id] = _Chosen(settings, mail, items)
    return [chosen[settings.account.account_id] for settings in config.accounts if settings.account.account_id in chosen]


def _files(engine: Engine, choice: BackupChoice, chosen: list[_Chosen]) -> Iterator[_File]:
    """Every file to back up, listed before the first is written (so the counts are known). Messages that arrive later wait for the
    next backup; messages that move meanwhile are found again by their id."""
    kinds = {kind for kind, part in ITEM_PARTS.items() if part in choice.parts}
    # what each account downloaded (POP3), taken before any mailbox is listed: it never names a message downloaded later, which the backup
    # lacks (a restore would then never download it again, or have it deleted on the server, RV-61)
    downloaded: dict[Path, bytes | None] = {}
    if MESSAGES in choice.parts:
        for item in chosen:
            if item.mail:
                path = engine.directories.pop3_state_file(item.settings.account.account_id)
                downloaded[path] = _read(path)
    for item in chosen:
        account_id = item.settings.account.account_id
        account_directory = engine.directories.account_directory(account_id)
        if item.mail and MESSAGES in choice.parts:
            store_root = engine.directories.store_directory(account_id)
            with engine.place_lock(account_id):  # the list of the mailbox's files at one moment, never halfway a change
                for path in _walk(store_root):
                    is_message = path.parent.name in ("cur", "new") and parse_name(path.name) is not None
                    yield _File(path, DOING_MESSAGES, is_message, store_root if is_message else None)
                for path in sorted(account_directory.iterdir()) if account_directory.is_dir() else ():
                    if path in downloaded:
                        taken = downloaded[path]
                        if taken is not None:  # none yet when it was taken: left out (what came later is downloaded again, never lost)
                            yield _File(path, DOING_MESSAGES, data=taken)
                    elif path.is_file() and not path.name.endswith(_INDEX_SUFFIXES):
                        yield _File(path, DOING_MESSAGES)  # what the server holds (IMAP)
        if item.items and kinds:
            pim_root = engine.directories.pim_directory(account_id)
            collections = _collection_kinds(pim_root)
            for path in _walk(pim_root):
                relative = path.relative_to(pim_root).parts
                if relative[0] == "collections":
                    if len(relative) > 1 and collections.get(relative[1]) in kinds:
                        yield _File(path, DOING_ITEMS, len(relative) == 4 and relative[2] == "items")
                elif not path.name.endswith(_INDEX_SUFFIXES):
                    yield _File(path, DOING_ITEMS)  # the description, the server's state, the sync conflicts
    if SETTINGS in choice.parts and engine.directories.junk_filter_file.is_file():
        yield _File(engine.directories.junk_filter_file, DOING_SETTINGS, True)


def _walk(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(folder for folder in folders if folder not in _LEFT_OUT_NAMES)
        for name in sorted(names):
            yield Path(directory) / name


def _add_file(engine: Engine, archive: _Archive, item: _File, key: DataKey | None, messages: _Messages, sealed: list[str], unreadable: list[str]) -> bool:
    """Put one file in the backup, readable without this computer's key. A message that moved is backed up where it is now; one that
    is gone (deleted meanwhile) or backed up already is left out. Whether it was written."""
    path = item.path
    parsed = parse_name(path.name) if item.store_root is not None else None
    store_id = parsed[0] if parsed is not None else None
    if item.store_root is not None and store_id is not None:
        done = messages.done.setdefault(item.store_root, set())
        if store_id in done:
            return False
    data: bytes | None = item.data if item.data is not None else _read(path)
    if data is None and item.store_root is not None and store_id is not None:
        path = _find_again(messages, item.store_root, store_id) or path
        data = _read(path)
    if data is None:
        return False  # deleted while the backup ran
    name = "data/" + path.relative_to(engine.directories.data_directory).as_posix()
    if key is not None and data.startswith(MAGIC):
        try:
            data = decrypt_message(data, key)
            sealed.append(name)
        except CryptoError:
            unreadable.append(name)  # damaged here already: kept exactly as it is
    _add(archive, name, data)
    if item.store_root is not None and store_id is not None:
        messages.done.setdefault(item.store_root, set()).add(store_id)
    return True


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ChronoMailError(f"{path} could not be read for the backup: {error.strerror}") from None


def _find_again(messages: _Messages, store_root: Path, store_id: str) -> Path | None:
    """Where a message is now that it is no longer where it was listed (moved, or a mark changed its name). The mailbox is searched again
    only when the message moved since the last search: one that search did not find was deleted (searching again for each deleted
    message could make a backup of a large mailbox hang, RV-69)."""
    places = messages.places.get(store_root)
    if places is not None:
        found = places.get(store_id)
        if found is None or found.exists():
            return found
    places = {}
    for path in _walk(store_root / "folders"):
        parsed = parse_name(path.name)
        if parsed is not None and path.parent.name in ("cur", "new"):
            places[parsed[0]] = path
    messages.places[store_root] = places
    return places.get(store_id)


def _config_entries(engine: Engine, config: AppConfig, choice: BackupChoice, chosen: list[_Chosen]) -> Iterator[tuple[str, str]]:
    """The settings files: the chosen accounts always (without this computer's key), the rules and the other settings when chosen,
    and the account passwords only in a protected backup."""
    directories = engine.directories
    yield "config/config.json", config_to_text(AppConfig(tuple(item.settings for item in chosen), None))
    if RULES in choice.parts and directories.rules_file.is_file():
        yield "config/rules.json", directories.rules_file.read_text("utf-8")
    if SETTINGS in choice.parts:
        for path in (directories.sidebar_file, directories.tags_file):
            if path.is_file():
                yield f"config/{path.name}", path.read_text("utf-8")
        if choice.window_settings:
            yield "config/window.json", choice.window_settings
    if choice.passwords and choice.password is not None:
        passwords: dict[str, dict[str, str]] = {}
        for item in chosen:
            account_id = item.settings.account.account_id
            known = {role.value: engine.passwords.get(account_id, role) for role in ServerRole}
            kept = {role: password for role, password in known.items() if password is not None}
            if kept:
                passwords[account_id] = kept
        yield "config/passwords.json", json.dumps(passwords, indent=2)


def _manifest(engine: Engine, choice: BackupChoice, chosen: list[_Chosen], sealed: list[str], unreadable: list[str], counts: dict[str, int]) -> str:
    entries = {entry.entry_id: entry.name for entry in backup_entries(engine)}
    return json.dumps(
        {
            "format": FORMAT_VERSION,
            "program": "Chrono Mail",
            "version": program_version(),
            "created": datetime.fromtimestamp(engine.clock()).astimezone().isoformat(timespec="seconds"),
            "parts": [part for part in PARTS if part in choice.parts],
            "accounts": [
                {
                    "id": item.settings.account.account_id,
                    "name": entries.get(item.settings.account.account_id, "Local Folders" if item.mail else "On this computer"),
                    "mail": item.mail and MESSAGES in choice.parts,
                    "items": item.items,
                }
                for item in chosen
            ],
            "passwords": choice.passwords,
            "protected": choice.password is not None,
            "messages": counts[DOING_MESSAGES],
            "items": counts[DOING_ITEMS],
            "sealed": sealed,  # files that are encrypted on a computer: a restore encrypts them again with that computer's key
            "unreadable": unreadable,
        },
        indent=1,
    )
