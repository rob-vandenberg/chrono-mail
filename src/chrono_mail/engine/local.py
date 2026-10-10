import shutil

from chrono_mail.config import AccountSettings, AppConfig
from chrono_mail.core import (
    LOCAL_ACCOUNT_ID,
    Account,
    AccountKind,
    ChronoMailError,
    ConnectionSecurity,
    CryptoError,
    ServerSettings,
)
from chrono_mail.crypto import DataKey, generate_data_key
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "local.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial Local Folders mailbox

LOCAL_NAME: str = "Local Folders"
_NOWHERE = ServerSettings("localhost", 1, ConnectionSecurity.NONE, "-")  # Local Folders has no servers


def ensure_local_folders(engine: Engine, encrypted: bool = True) -> tuple[AppConfig, bool]:
    """Make the Local Folders mailbox if it does not exist yet. Returns the settings and whether a new encryption key was made.
    Local Folders is a mailbox without servers: POP3 accounts may keep their mail in it, all in one tree of folders."""
    config = engine.config()
    if config.local_folders() is not None:
        return config, False
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
    directory = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    try:
        store = Store.create(directory, LOCAL_ACCOUNT_ID, key, durable=engine.durable)
        for path, role in STANDARD_FOLDERS:
            store.create_folder(path, role)
        settings = AccountSettings(Account(LOCAL_ACCOUNT_ID, LOCAL_NAME, "", AccountKind.LOCAL, _NOWHERE, _NOWHERE), encrypted=encrypted)
        engine.save_config(config.with_account(settings))  # the commit point
    except ChronoMailError:
        shutil.rmtree(engine.directories.account_directory(LOCAL_ACCOUNT_ID), ignore_errors=True)
        raise
    return engine.config(), key_created
