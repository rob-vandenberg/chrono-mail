import json
import shutil
import threading
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
from chrono_mail.crypto import DataKey, generate_data_key
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "local.py 3.0.339"

# --- Version history ----------------------------------------------------
# v3.0.339: Local Folders that is missing from the settings while its mail is still here is taken into use again, never removed;
#           a failed first setup removes only what it made (RV-62)
# v0.5.144: Local Folders is entered in a fresh read of the settings, one command at a time (change_config)
# v0.0.65: The CalDAV and CardDAV client; first-time setup of the local calendars is serialised
# v0.0.45: Initial Local Folders mailbox

LOCAL_NAME: str = "Local Folders"
SETUP_LOCK = threading.RLock()  # two commands that need Local Folders for the first time at once (three sections shown quickly) must not both make it
_NOWHERE = ServerSettings("localhost", 1, ConnectionSecurity.NONE, "-")  # Local Folders has no servers


def ensure_local_folders(engine: Engine, encrypted: bool = True) -> tuple[AppConfig, bool]:
    """Make the Local Folders mailbox if it does not exist yet. Returns the settings and whether a new encryption key was made.
    Local Folders is a mailbox without servers: POP3 accounts may keep their mail in it, all in one tree of folders."""
    with SETUP_LOCK:
        return _ensure_local_folders(engine, encrypted)


def _ensure_local_folders(engine: Engine, encrypted: bool) -> tuple[AppConfig, bool]:
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
            key = generate_data_key()
            engine.keys.save(key)
            if engine.keys.load(key.key_id) != key:
                raise CryptoError("The credential store did not return the key that was just saved")
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
        metadata = json.loads((directory / "store.json").read_text("utf-8"))
        encrypted, kept_key = bool(metadata["encrypted"]), metadata["key_id"]
    except (OSError, ValueError, KeyError, TypeError):
        raise StorageError(f"The mail of Local Folders is still in {directory}, but its description (store.json) cannot be read; nothing was changed") from None
    config = engine.config()
    if encrypted and config.key_id is not None and config.key_id != kept_key:
        raise CryptoError(f"The mail of Local Folders in {directory} was written with another key than this computer's; nothing was changed")
    key = engine.data_key(config.with_key_id(str(kept_key))) if encrypted else None  # its own key, from the credential store
    store = Store.open(directory, key, durable=engine.durable)
    for path, role in STANDARD_FOLDERS:
        store.ensure_folder(path, role)
    settings = AccountSettings(Account(LOCAL_ACCOUNT_ID, LOCAL_NAME, "", AccountKind.LOCAL, _NOWHERE, _NOWHERE), encrypted=encrypted)
    engine.change_config(lambda fresh: (fresh.with_key_id(str(kept_key)) if encrypted and fresh.key_id is None else fresh).with_account(settings))
    return engine.config()
