import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.config import (
    AccountSettings,
    AppConfig,
    AppDirectories,
    PasswordStore,
    load_config,
    save_config,
)
from chrono_mail.core import ConfigurationError, CryptoError, FolderRole, StorageError
from chrono_mail.crypto import DataKey, KeyStore
from chrono_mail.index import Index
from chrono_mail.store import Store
from chrono_mail.sync import rebuild_index

# --- Version ------------------------------------------------------------
__version__ = "session.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial engine session

OUTBOX: tuple[str, ...] = ("Outbox",)
STANDARD_FOLDERS: tuple[tuple[tuple[str, ...], FolderRole], ...] = (
    (("Inbox",), FolderRole.INBOX),
    (("Sent",), FolderRole.SENT),
    (("Drafts",), FolderRole.DRAFTS),
    (("Trash",), FolderRole.TRASH),
    (("Junk",), FolderRole.JUNK),
    (OUTBOX, FolderRole.OTHER),
)
Progress = Callable[[str, int, int | None], None]


def _no_progress(what: str, done: int, total: int | None) -> None:
    """Progress reports are optional."""


@dataclass
class Engine:
    """One session of the application: where its files are, how secrets are kept, and the clock.

    An Engine is used from one thread at a time. Every command receives it, and everything a
    command does goes through it, so a test (or an agent) can run the whole application
    without a screen, with in-memory key and password stores.
    """

    directories: AppDirectories
    passwords: PasswordStore
    keys: KeyStore
    clock: Callable[[], float] = time.time
    durable: bool = True
    progress: Progress = field(default=_no_progress)

    # --- configuration -------------------------------------------------------------------------

    def config(self) -> AppConfig:
        return load_config(self.directories.config_file)

    def save_config(self, config: AppConfig) -> None:
        save_config(self.directories.config_file, config)

    def resolve_account(self, identifier: str, config: AppConfig | None = None) -> AccountSettings:
        """An account by its id, by the start of its id (at least 6 characters), or by its e-mail address."""
        current = config if config is not None else self.config()
        wanted = identifier.strip().lower()
        matches = [
            settings
            for settings in current.accounts
            if settings.account.account_id == wanted
            or (len(wanted) >= 6 and settings.account.account_id.startswith(wanted))
            or settings.account.email_address.lower() == wanted
        ]
        if len(matches) != 1:
            raise ConfigurationError("Unknown account" if not matches else "More than one account matches; use the account id")
        return matches[0]

    # --- keys ----------------------------------------------------------------------------------

    def data_key(self, config: AppConfig) -> DataKey:
        if config.key_id is None:
            raise CryptoError("No data key has been created yet")
        return self.keys.load(bytes.fromhex(config.key_id))

    # --- opening an account --------------------------------------------------------------------

    def store_directory(self, settings: AccountSettings) -> Path:
        return self.directories.store_directory(settings.account.account_id)

    def open_store(self, settings: AccountSettings, config: AppConfig | None = None) -> Store:
        current = config if config is not None else self.config()
        key = self.data_key(current) if settings.encrypted else None
        return Store.open(self.store_directory(settings), key, durable=self.durable)

    def open_index(self, settings: AccountSettings, store: Store) -> Index:
        """The account's index. A missing or damaged index is rebuilt from the store: it is never the truth."""
        path = self.directories.index_file(settings.account.account_id)
        try:
            if path.exists():
                return Index(path)
        except StorageError:
            pass
        self.progress("Rebuilding the index", 0, None)
        index, _ = rebuild_index(store, path, lambda done: self.progress("Rebuilding the index", done, None))
        return index
