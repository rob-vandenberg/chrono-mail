import json
import os
import shutil
import threading
import time
from pathlib import Path

from chrono_mail.config import AccountSettings, AppConfig
from chrono_mail.core import (
    LOCAL_ACCOUNT_ID,
    Account,
    AccountKind,
    ChronoMailError,
    ConnectionSecurity,
    CryptoError,
    ServerSettings,
    StorageError,
)
from chrono_mail.core.words import counted
from chrono_mail.crypto import CredentialStoreError, DataKey, decrypt_message, encrypt_message, read_key_id
from chrono_mail.engine.junkwords import JunkWords, load_words
from chrono_mail.engine.newkey import kept_other_key, make_data_key, usable
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.store import Store
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "local.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash; the messages about kept Local
#           Folders name Start without this mail, the way out when its key is gone; kept Local Folders written with an older key of this
#           computer is taken back with it, written again with this computer's key (the old files kept aside); an interrupted rewrite is
#           finished or undone at the next setup
# v3.6.349: kept Local Folders whose key is gone names File > Use a recovery code, and that code brings it back (newkey.kept_local_key, RV-177)
# v3.5.346: The data key is made in the one place that remembers its recovery code was not stored yet (RV-03)
# v3.0.339: Local Folders that is missing from the settings while its mail is still here is taken into use again, never removed;
#           a failed first setup removes only what it made (RV-62)
# v0.5.144: Local Folders is entered in a fresh read of the settings, one command at a time (change_config)
# v0.0.65: The CalDAV and CardDAV client; first-time setup of the local calendars is serialised
# v0.0.45: Initial Local Folders mailbox

LOCAL_NAME: str = "Local Folders"
REKEYING: str = ".rekeying-" + LOCAL_ACCOUNT_ID  # Local Folders written again with this computer's key, before it takes the old one's place
DISK_BLOCK: int = 4096  # the smallest piece of a disk a file or folder takes
ROOM_TO_SPARE: int = 200_000_000  # free space left over after the copy of Local Folders (the disk must not be filled to the last byte)
REKEYED_MARK: str = ".rekeyed"  # in REKEYING once every file is written: from then on it takes the old one's place, also after a crash
SETUP_LOCK = threading.RLock()  # two commands that need Local Folders for the first time at once (three sections shown quickly) must not both make it
_NOWHERE = ServerSettings("localhost", 1, ConnectionSecurity.NONE, "-")  # Local Folders has no servers


def ensure_local_folders(engine: Engine, encrypted: bool = True) -> tuple[AppConfig, bool]:
    """Make the Local Folders mailbox if it does not exist yet. Returns the settings and whether a new encryption key was made.
    Local Folders is a mailbox without servers: POP3 accounts may keep their mail in it, all in one tree of folders."""
    with SETUP_LOCK:
        return _ensure_local_folders(engine, encrypted)


def _ensure_local_folders(engine: Engine, encrypted: bool) -> tuple[AppConfig, bool]:
    _finish_rekeying(engine)
    config = engine.config()
    if config.local_folders() is not None:
        return config, False
    directory = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    if (directory / "store.json").exists():
        return _take_kept_local_folders(engine, directory), False
    key: DataKey | None = None
    key_created = False
    if encrypted:
        if config.key_id is None:
            key = make_data_key(engine)
            key_created = True
            config = config.with_key_id(key.key_id.hex())
        else:
            key = engine.data_key(config)
    made = not engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists()
    try:
        store = Store.create(directory, LOCAL_ACCOUNT_ID, key, durable=engine.durable)
        for path, role in STANDARD_FOLDERS:
            store.create_folder(path, role)
        settings = AccountSettings(Account(LOCAL_ACCOUNT_ID, LOCAL_NAME, "", AccountKind.LOCAL, _NOWHERE, _NOWHERE), encrypted=encrypted)
        key_id = config.key_id
        engine.change_config(lambda fresh: (fresh.with_key_id(key_id) if fresh.key_id is None else fresh).with_account(settings))  # the commit point
    except ChronoMailError:
        if made:  # only what this call made is taken away again: never a folder that was there before (the calendars of this computer, RV-62)
            shutil.rmtree(engine.directories.account_directory(LOCAL_ACCOUNT_ID), ignore_errors=True)
        raise
    return engine.config(), key_created


def _take_kept_local_folders(engine: Engine, directory: Path) -> AppConfig:
    """Local Folders is not in the settings, but its mail is still on this computer (settings that were lost or reset): that mail is taken
    into use again as it is, with the key it was written with; it is never removed or made again (RV-62, never lose mail)."""
    try:
        metadata = load_json((directory / "store.json").read_text("utf-8"))
        encrypted, kept_key = bool(metadata["encrypted"]), metadata["key_id"]
    except (OSError, ValueError, KeyError, TypeError):
        raise StorageError(f"The mail of Local Folders is still in {directory}, but its description (store.json) cannot be read; nothing was changed") from None
    config = engine.config()
    if encrypted and config.key_id is not None and config.key_id != kept_key:  # an older version made a new key after the settings were lost
        if not usable(engine, str(kept_key)):
            raise CryptoError(
                f"The mail of Local Folders in {directory} was written with an older key of this computer, which its password store no longer "
                "has. Use File > Use a recovery code with that key's recovery code, or Start without this mail there (it is not deleted); "
                "nothing was changed"
            )
        raise CryptoError(  # written again only by its own step (take_back), which the key window runs with a word on the screen
            "Local Folders was written with an older key of this computer and is taken back first: choose File > Use a recovery code, and "
            "the window does it (no code is needed); nothing was changed"
        )
    try:
        key = engine.data_key(config.with_key_id(str(kept_key))) if encrypted else None  # its own key, from the credential store
    except CredentialStoreError:
        raise
    except CryptoError:  # not there, or damaged
        raise CryptoError(
            f"The mail of Local Folders is still in {directory}, but its key is not in this computer's password store. Use File > Use a "
            "recovery code to bring the key back (or Start without this mail there, which sets it aside); nothing was changed."
        ) from None
    store = Store.open(directory, key, durable=engine.durable)
    for path, role in STANDARD_FOLDERS:
        store.ensure_folder(path, role)
    settings = AccountSettings(Account(LOCAL_ACCOUNT_ID, LOCAL_NAME, "", AccountKind.LOCAL, _NOWHERE, _NOWHERE), encrypted=encrypted)
    engine.change_config(lambda fresh: (fresh.with_key_id(str(kept_key)) if encrypted and fresh.key_id is None else fresh).with_account(settings))
    return engine.config()


def take_back(engine: Engine) -> str:
    """Local Folders kept on this computer, written with an older key of this computer that the password store has: written again with this
    computer's key (_rekey) and taken into use. Its own step (local.take-back), so it never runs hidden inside another command. What could
    not be read is said in the answer ("" when everything came along); it stays in the old files, which are kept aside."""
    with SETUP_LOCK:
        _finish_rekeying(engine)
        config = engine.config()
        older = kept_other_key(engine)
        said = ""
        if older is not None:
            if not usable(engine, older):
                raise CryptoError("The older key of Local Folders is not in this computer's password store: use its recovery code")
            skipped = _rekey(engine, engine.data_key(config.with_key_id(older)), engine.data_key(config))
            if skipped:
                said = f"{counted(len(skipped), 'file')} could not be read; they stay in the old files, kept in {engine.directories.data_directory}."
        _rekey_junk(engine)  # also after a rewrite whose last step was finished later
        if engine.config().local_folders() is None and (engine.directories.store_directory(LOCAL_ACCOUNT_ID) / "store.json").exists():
            _ensure_local_folders(engine, True)  # taken into use, also when an earlier rewrite was only finished now
        return said


def _rekey_junk(engine: Engine) -> None:
    """What the junk filter learned, when it is still written with an older key of this computer that the store has (also a copy set aside
    as .bad, which takes the main file's place when there is none): written again with this computer's key."""
    config = engine.config()
    if config.key_id is None:
        return
    current = engine.data_key(config)
    main = engine.directories.junk_filter_file
    bad = main.with_name(main.name + ".bad")
    with engine.junk.lock:
        for junk in (main, bad):
            try:
                data = junk.read_bytes()
                older = read_key_id(data).hex()
                if older != config.key_id and usable(engine, older):
                    replace_file_atomically(junk, encrypt_message(decrypt_message(data, engine.data_key(config.with_key_id(older))), current), engine.durable)
                    if junk == bad and (not main.exists() or _learned(load_words(bad, current)) > _learned(load_words(main, current))):
                        # the learning kept aside only because it could not be read is in use again when it knows more than what was learned
                        # since (the two cannot be added up: a message both learned would count twice); the other copy is kept aside
                        if main.exists():
                            os.replace(main, main.with_name(f"{main.name}.since-{time.strftime('%Y%m%d-%H%M%S')}"))
                        os.replace(bad, main)
            except (OSError, CryptoError, ValueError, StorageError):
                pass  # not there, damaged, or written with this computer's key already
        engine.junk.words = None  # read again when needed


def _rekey(engine: Engine, old: DataKey, new: DataKey) -> list[str]:
    """Local Folders kept on this computer, written with an older key of this computer, written again with this computer's key: every file
    into a folder of its own, then put in place in two renames; the old files stay aside, never deleted. A crash at any point leaves either
    the old Local Folders in place (an unfinished copy is thrown away at the next setup) or the new one ready to take its place (finished
    then): never neither (AGENTS.md 4.1.4). A file that cannot be read is left out of the copy (one bad file never stops it, AGENTS.md
    4.2.1) and named in the answer; it stays in the old files. It needs room for a second copy: without it nothing is done."""
    source = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    building = source.with_name(REKEYING)
    shutil.rmtree(building, ignore_errors=True)
    needed = sum(_on_disk(path) for path in source.rglob("*")) + ROOM_TO_SPARE
    free = shutil.disk_usage(source.parent).free
    if free < needed:
        raise StorageError(
            f"Taking Local Folders back needs about {_size(needed)} free on this disk, and {_size(free)} is free. Make room and try again; "
            "nothing was changed"
        )
    skipped: list[str] = []
    try:
        building.mkdir(parents=True)

        def unopened(error: OSError) -> None:  # a folder that cannot be opened: named, and left in the old files like a file
            skipped.append(Path(error.filename).relative_to(source).as_posix() + "/" if error.filename else "a folder")

        for root, folders, files in os.walk(source, onerror=unopened):
            folders.sort()
            here = Path(root)
            (building / here.relative_to(source)).mkdir(parents=True, exist_ok=True)
            for name in sorted(files):
                path = here / name
                target = building / path.relative_to(source)
                try:
                    data = path.read_bytes()
                    times = path.stat()
                except OSError:
                    skipped.append(path.relative_to(source).as_posix())  # it cannot be read here; it stays in the old files
                    continue
                write_new_file(target, _rekeyed(path, data, old, new, source), False)
                os.utime(target, ns=(times.st_atime_ns, times.st_mtime_ns))  # its time stays: the last resort of the Received column (D-226)
                if engine.durable:  # its contents and its time on the disk together
                    descriptor = os.open(target, os.O_RDWR | getattr(os, "O_BINARY", 0))
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
        essential = [name for name in skipped if not _one_item(name)]
        if essential:  # only a single message or item may stay behind: a folder or a description left out would hide all it holds
            raise OSError(5, f"{essential[0]} could not be read")
        if engine.durable:  # every folder of the copy on the disk before the mark says it is whole (AGENTS.md 4.1.1)
            for directory in sorted((path for path in building.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
                fsync_directory(directory)
            fsync_directory(building)
        write_new_file(building / REKEYED_MARK, new.key_id.hex().encode("utf-8"), engine.durable)
        if engine.durable:
            fsync_directory(building)
    except OSError as error:
        shutil.rmtree(building, ignore_errors=True)
        raise StorageError(f"Local Folders could not be written with this computer's key ({error.strerror or error}); nothing was changed") from None
    _finish_rekeying(engine)
    return skipped


def _one_item(name: str) -> bool:
    """Whether a path of Local Folders is one message or one calendar, contact or task item: only those may stay behind in a take-back."""
    parts = name.split("/")
    return (len(parts) == 5 and parts[:2] == ["store", "folders"] and parts[3] in ("cur", "new")) or (
        len(parts) == 5 and parts[:2] == ["pim", "collections"] and parts[3] == "items"
    )


def _learned(words: JunkWords) -> int:
    return words.junk_messages + words.good_messages


def _on_disk(path: Path) -> int:
    """What a file or folder takes on the disk: its blocks (a small message takes a whole block), at least a block each."""
    try:
        found = path.stat()
    except OSError:
        return 0
    blocks = getattr(found, "st_blocks", None)  # not on Windows: its size rounded up to a cluster
    used = blocks * 512 if isinstance(blocks, int) else found.st_size
    return max(DISK_BLOCK, -(-used // DISK_BLOCK) * DISK_BLOCK)


def _size(count: int) -> str:
    return f"{count / 1_000_000_000:.1f} GB" if count >= 100_000_000 else f"{count / 1_000_000:.0f} MB"


def _rekeyed(path: Path, data: bytes, old: DataKey, new: DataKey, root: Path) -> bytes:
    """A file of Local Folders as it is with this computer's key: an encrypted file written again, a description naming the new key."""
    try:
        written_with = read_key_id(data)
    except CryptoError:
        written_with = None  # not an encrypted file
    if written_with is not None:
        try:
            return encrypt_message(decrypt_message(data, old), new) if written_with == old.key_id else data
        except CryptoError:
            return data  # damaged here already: kept exactly as it is
    if path.name in ("store.json", "pim.json") and path.parent.parent == root:
        try:
            description = load_json(data)
        except (ValueError, RecursionError):
            return data
        if isinstance(description, dict) and description.get("key_id") == old.key_id.hex():
            return json.dumps({**description, "key_id": new.key_id.hex()}).encode("utf-8")
    return data


def _finish_rekeying(engine: Engine) -> None:
    """A rewrite of Local Folders that was cut off: once every file was written (the mark) it takes the old one's place, the old one kept
    aside; before that the unfinished copy is thrown away and the old Local Folders stays."""
    source = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
    building = source.with_name(REKEYING)
    if not building.exists():
        return
    if not (building / REKEYED_MARK).exists():
        if source.exists():
            shutil.rmtree(building, ignore_errors=True)
        return
    if engine.config().local_folders() is not None:  # Local Folders came back another way meanwhile (a restore): this copy is not used
        try:
            os.replace(building, engine.directories.data_directory / f"set-aside-local-folders-unused-{time.strftime('%Y%m%d-%H%M%S')}")
        except OSError:
            pass  # tried again at the next setup; it never takes the place of a Local Folders in use
        return
    try:
        if source.exists():
            os.replace(source, engine.directories.data_directory / f"set-aside-local-folders-older-key-{time.strftime('%Y%m%d-%H%M%S')}")
            if engine.durable:
                fsync_directory(engine.directories.data_directory)  # the old files' new place is on the disk before anything else
        os.replace(building, source)
        (source / REKEYED_MARK).unlink(missing_ok=True)
        if engine.durable:
            fsync_directory(source.parent)
    except OSError as error:
        raise StorageError(f"Local Folders could not take its place with this computer's key ({error.strerror or error}); try again") from None
