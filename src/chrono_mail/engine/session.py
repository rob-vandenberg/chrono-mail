import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.config import (
    AccountSettings,
    AppConfig,
    AppDirectories,
    PasswordStore,
    fingerprints_for,
    load_config,
    save_config,
)
from chrono_mail.core import ConfigurationError, CryptoError, FolderRole, StorageError
from chrono_mail.crypto import DataKey, KeyStore
from chrono_mail.index import Index
from chrono_mail.protocols.tls import context_for
from chrono_mail.store import Store
from chrono_mail.sync import rebuild_index

# --- Version ------------------------------------------------------------
__version__ = "session.py 0.2.101"

# --- Version history ----------------------------------------------------
# v0.2.101: A store with two folders of the same path is repaired when it is opened
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.43: tls_context for accepted certificates
# v0.0.32: stop_event for long waits
# v0.0.13: key cache, safe for concurrent commands
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

    An Engine can run commands from several threads at once (a UI does): it keeps no per-command
    state except the key cache, which is locked. Every command receives it, and everything a
    command does goes through it, so a test (or an agent) can run the whole application
    without a screen, with in-memory key and password stores.
    """

    directories: AppDirectories
    passwords: PasswordStore
    keys: KeyStore
    clock: Callable[[], float] = time.time
    durable: bool = True
    progress: Progress = field(default=_no_progress)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)  # set when the program is ending: long waits stop
    _cached_keys: dict[bytes, DataKey] = field(default_factory=dict, init=False, repr=False)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def tls_context(self, settings: AccountSettings, outgoing: bool = False) -> ssl.SSLContext | None:
        """The TLS context for the incoming (or outgoing) server of an account; None means the usual checks apply to it,
        which is so unless a certificate was accepted for that very server."""
        server = settings.account.outgoing if outgoing else settings.account.incoming
        return context_for(fingerprints_for(settings.trusted_certificates, server.host, server.port))

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
        """The data key. It is asked from the credential store once and then kept for the session."""
        if config.key_id is None:
            raise CryptoError("No data key has been created yet")
        key_id = bytes.fromhex(config.key_id)
        with self._cache_lock:
            cached = self._cached_keys.get(key_id)
        if cached is not None:
            return cached
        key = self.keys.load(key_id)
        with self._cache_lock:
            self._cached_keys[key_id] = key
        return key

    def forget_keys(self) -> None:
        """Drop the cached key (it is fetched from the credential store again when it is next needed)."""
        with self._cache_lock:
            self._cached_keys.clear()

    # --- opening an account --------------------------------------------------------------------

    def store_directory(self, settings: AccountSettings) -> Path:
        return self.directories.store_directory(settings.account.account_id)

    def storage_settings(self, settings: AccountSettings, config: AppConfig | None = None) -> AccountSettings:
        """Where an account's mail is kept: the account itself, or Local Folders for a POP3 account that shares them."""
        if settings.storage != "local":
            return settings
        local = (config if config is not None else self.config()).local_folders()
        if local is None:
            raise ConfigurationError("This account keeps its mail in Local Folders, but there is no Local Folders mailbox")
        return local

    def storage_groups(self, config: AppConfig | None = None) -> dict[str, list[AccountSettings]]:
        """The accounts that share a place for their mail, by the id of that place: every account on its own, and the POP3
        accounts that keep their mail in Local Folders together with Local Folders itself."""
        current = config if config is not None else self.config()
        groups: dict[str, list[AccountSettings]] = {}
        for item in current.accounts:
            try:
                place = self.storage_settings(item, current).account.account_id
            except ConfigurationError:
                place = item.account.account_id  # Local Folders is missing: the account is shown on its own (and says so when opened)
            groups.setdefault(place, []).append(item)
        return groups

    def open_store(self, settings: AccountSettings, config: AppConfig | None = None) -> Store:
        current = config if config is not None else self.config()
        key = self.data_key(current) if settings.encrypted else None
        store = Store.open(self.store_directory(settings), key, durable=self.durable)
        if store.damaged_folders:
            store.merge_duplicate_folders()  # folders made twice by commands that ran at the same time (before 0.2.101)
        return store

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
