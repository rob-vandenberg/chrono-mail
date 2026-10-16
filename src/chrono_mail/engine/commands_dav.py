import threading
import urllib.parse
from dataclasses import dataclass, field, replace

from chrono_mail.config import AccountSettings, ServerRole, fingerprints_for
from chrono_mail.core import AccountKind, ConfigurationError, LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_accounts import AccountSummary, _summary
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.dav import DavClient
from chrono_mail.sync.davsync import sync_dav

# --- Version ------------------------------------------------------------
__version__ = "commands_dav.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: The server address is saved on a fresh read of the settings, one command at a time (change_config)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: Initial commands for the calendars, task lists and address books on a CalDAV and CardDAV server

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(account_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(account_id, threading.Lock())


def _client(engine: Engine, settings: AccountSettings, url: str = "", username: str | None = None, password: str | None = None) -> DavClient:
    address = url or settings.dav_url
    if not address:
        raise UsageError("This account has no calendar and contacts server (see account.set-dav)")
    account_id = settings.account.account_id
    secret = password or engine.passwords.get(account_id, ServerRole.DAV) or engine.passwords.get(account_id, ServerRole.INCOMING)
    if secret is None:
        raise ConfigurationError("No password is stored for the calendar and contacts server")
    user = (username if username is not None else settings.dav_username) or settings.account.incoming.username
    parts = urllib.parse.urlsplit(address)
    return DavClient(address, user, secret, fingerprints_for(settings.trusted_certificates, parts.hostname or "", parts.port or 443))


@dataclass(frozen=True, slots=True)
class SetDavParameters:
    account: str = field(metadata=option("The account"))
    url: str = field(default="", metadata=option("The address of the CalDAV and CardDAV server (empty: stop using one)"))
    username: str = field(default="", metadata=option("The user name (empty: the same as for incoming mail)"))
    password: str | None = field(default=None, metadata=option("The password (omit: the one for incoming mail, or the one stored earlier)", secret=True))


def dav_set(engine: Engine, parameters: SetDavParameters) -> AccountSummary:
    """Set the address of the calendar and contacts server of an account."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    if settings.account.kind is AccountKind.LOCAL:
        raise UsageError("The Local Folders have no server")
    account_id = settings.account.account_id
    url, username = parameters.url.strip(), parameters.username.strip()
    config = engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(account_id), dav_url=url, dav_username=username)))
    updated = config.find(account_id)
    if parameters.password is not None:
        engine.passwords.set(account_id, ServerRole.DAV, parameters.password)
    elif not parameters.url.strip():
        engine.passwords.delete(account_id, ServerRole.DAV)
    return _summary(updated)


@dataclass(frozen=True, slots=True)
class DiscoverParameters:
    account: str = field(metadata=option("The account"))
    url: str = field(default="", metadata=option("Try this address instead of the stored one"))
    username: str | None = field(default=None, metadata=option("Try this user name instead of the stored one"))
    password: str | None = field(default=None, metadata=option("Try this password instead of the stored one", secret=True))


@dataclass(frozen=True, slots=True)
class DavCollectionSummary:
    name: str
    kind: str
    href: str
    read_only: bool


def dav_discover(engine: Engine, parameters: DiscoverParameters) -> tuple[DavCollectionSummary, ...]:
    """The calendars, task lists and address books that the server of an account offers."""
    settings = engine.resolve_account(parameters.account)
    client = _client(engine, settings, parameters.url, parameters.username, parameters.password)
    homes = client.homes()
    return tuple(DavCollectionSummary(item.name, item.kind, item.href, item.read_only) for item in client.collections(*homes))


@dataclass(frozen=True, slots=True)
class SyncParameters:
    account: str | None = field(default=None, metadata=option("The account (omit: every account that has a calendar and contacts server)"))


@dataclass(frozen=True, slots=True)
class DavSyncSummary:
    account: str
    collections: int
    created: int
    pulled: int
    pushed: int
    deleted_here: int
    deleted_on_server: int
    conflicts: int
    errors: tuple[str, ...]


def _sync_account(engine: Engine, settings: AccountSettings) -> DavSyncSummary:
    account_id = settings.account.account_id
    client = _client(engine, settings)
    with _lock_for(account_id):
        with open_pim(engine, account_id, create=True) as store:
            report = sync_dav(client, store, engine.directories.pim_directory(account_id) / "dav-state.json", engine.durable)
    return DavSyncSummary(
        account_id, report.collections, report.created, report.pulled, report.pushed, report.deleted_here, report.deleted_on_server, report.conflicts, tuple(report.errors)
    )


def dav_sync(engine: Engine, parameters: SyncParameters) -> tuple[DavSyncSummary, ...]:
    """Bring the calendars, task lists and address books of an account in line with its server, both ways."""
    config = engine.config()
    if parameters.account is not None:
        return (_sync_account(engine, engine.resolve_account(parameters.account, config)),)
    return tuple(_sync_account(engine, item) for item in config.accounts if item.dav_url and item.account.account_id != LOCAL_ACCOUNT_ID)


def register(registry: CommandRegistry) -> None:
    registry.register("account.set-dav", "Set the calendar and contacts server (CalDAV and CardDAV) of an account", SetDavParameters, dav_set)
    registry.register("dav.discover", "List what the calendar and contacts server of an account offers", DiscoverParameters, dav_discover)
    registry.register("dav.sync", "Synchronise calendars, task lists and address books with the server, both ways", SyncParameters, dav_sync)
