import threading
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.config import AccountSettings, AppConfig, ServerRole, fingerprints_for, new_account_id, server_pin
from chrono_mail.core import AccountKind, AuthenticationError, CertificateError, CertificateTrouble, ChronoMailError, CollectionLook, ConfigurationError, LOCAL_ACCOUNT_ID, ProtocolError
from chrono_mail.core.account import Account, ConnectionSecurity, ServerSettings
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_accounts import AccountAddResult, AccountSummary, _refuse_same_account, _summary
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.newkey import recovery_code_not_stored
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command, talks_to_servers
from chrono_mail.engine.walletlabels import label_account
from chrono_mail.protocols.dav import DavCertificateError, DavClient, DavError, RemoteCollection, canonical_key, folder_key
from chrono_mail.protocols.tls import inspect_certificate
from chrono_mail.sync.davsync import forget_collection, matching_collection, sync_dav, unsent_changes

# --- Version ------------------------------------------------------------
__version__ = "commands_dav.py 4.2.452"

# --- Version history ----------------------------------------------------
# v4.2.452: account.add-dav unchecked: an account taken over from another program is added without asking its server first (an import
#           makes no connection; the first fill reaches it, D-300) (UR-01)
# v4.2.449: dav.sync receive_only, the first fill of a new account (D-300): only downloads; nothing sent, nothing removed here
# v4.2.447: dav.find and dav.find-new fresh: a Find the person starts checks the certificate with the usual checks only (D-299)
# v4.2.441: every calendar server connection also takes the certificates accepted until Chrono Mail closes; dav.sync goes on with the other
#           accounts when one's certificate cannot be verified and names that server (certificate), as dav.find does when it looked by itself (D-299)
# v3.6.371: a stored calendar password gets a readable label in the wallet (B-23, D-275)
# v3.5.346: account.add-dav says whether the recovery code still has to be shown (RV-03)
# v3.0.339: a restore waits until this command has ended and holds it back meanwhile (talks_to_servers, RV-60)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.271: R-19: typed account.set-dav
# v1.9.270: R-19: typed dav.sync
# v1.1.236: The synchronisation's answer also says how many collections made here offline were made on the server (made_on_server, R-29)
# v1.1.234: The synchronisation's answer says how many collections the server confirmed deleted were taken away here (removed, R-25)
# v1.1.226: A synchronisation tells the window when conflicts were found or something waiting was sent (D-194)
# v1.1.223: Every client is closed when its work is done (it keeps its connection open, R-36)
# v1.1.221: The sync is given the account's kept names and colours of its server's collections and saves what became of them (keep_looks, D-192)
# v1.1.217: Folders left out compared in the one form of their address (canonical_key); dav.sync takes several accounts
# v1.1.212: dav.find-new and account.add-dav: an account of calendars, contacts and tasks only, saved only after its server answered with folders (D-185)
# v1.1.210: dav.find looks for the calendar server of an account by itself and lists every folder; dav.link ties the ticked ones (D-180 to D-183)
# v0.5.159: the sync with the server is a big job: one fact per account at its end (D-146 step 5)
# v0.5.144: The server address is saved on a fresh read of the settings, one command at a time (change_config)
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: Initial commands for the calendars, task lists and address books on a CalDAV and CardDAV server

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(account_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(account_id, threading.Lock())


def _client(
    engine: Engine, settings: AccountSettings, url: str = "", username: str | None = None, password: str | None = None, trusted: frozenset[str] | None = None,
) -> DavClient:
    address = url or settings.dav_url
    if not address:
        raise UsageError("This account has no calendar and contacts server (see account.set-dav)")
    account_id = settings.account.account_id
    secret = password or engine.passwords.get(account_id, ServerRole.DAV) or engine.passwords.get(account_id, ServerRole.INCOMING)
    if secret is None:
        raise ConfigurationError("No password is stored for the calendar and contacts server")
    user = (username if username is not None else settings.dav_username) or settings.account.incoming.username
    if trusted is None:
        trusted = _accepted_for(engine, settings, address)
    return DavClient(address, user, secret, trusted)


def _host_and_port(address: str) -> tuple[str, int]:
    parts = urllib.parse.urlsplit(address)
    return (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


def _accepted_for(engine: Engine, settings: AccountSettings, address: str) -> frozenset[str]:
    """The certificates accepted for this place: for good with the account, or until Chrono Mail closes (D-299)."""
    return fingerprints_for(engine.pins(settings.trusted_certificates), *_host_and_port(address))


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
    if settings.account.kind is AccountKind.DAV and not url:
        raise UsageError("An account of calendars, contacts and tasks only cannot be without its server (remove the account instead)")

    def change(fresh: AppConfig) -> AppConfig:
        current = fresh.find(account_id)
        left_out = current.dav_left_out if url == current.dav_url else ()  # what was left out belongs to the old server
        return fresh.with_account(replace(current, dav_url=url, dav_username=username, dav_left_out=left_out))

    config = engine.change_config(change)
    updated = config.find(account_id)
    if parameters.password is not None:
        engine.passwords.set(account_id, ServerRole.DAV, parameters.password)
        label_account(engine, account_id)  # B-23
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
    with _client(engine, settings, parameters.url, parameters.username, parameters.password) as client:
        homes = client.homes()
        return tuple(DavCollectionSummary(item.name, item.kind, item.href, item.read_only) for item in client.collections(*homes))


@dataclass(frozen=True, slots=True)
class SyncParameters:
    account: str | None = field(default=None, metadata=option("The account (omit: every account that has a calendar and contacts server)"))
    accounts: tuple[str, ...] = field(default=(), metadata=option("These accounts (repeat for more), each when it has a calendar and contacts server"))
    receive_only: bool = field(default=False, metadata=option("Only download (the first fill of a new account): nothing is sent to the server, nothing removed here"))


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
    removed: int = 0  # calendars, task lists and address books the server confirmed deleted, taken away here too (R-25)
    made_on_server: int = 0  # made here offline, now made on the server (R-29)
    certificate: CertificateTrouble | None = None  # its server's certificate could not be verified: nothing was synchronised (D-299)


def keep_looks(engine: Engine, account_id: str, before: Mapping[str, CollectionLook | None], after: Mapping[str, CollectionLook | None]) -> None:
    """Save what became of the kept names and colours of an account's server collections (D-192): each one only when it is still what it was when
    the work began, so a change made meanwhile is never overwritten."""
    if not after:
        return

    def change(fresh: AppConfig) -> AppConfig:
        current = fresh.find(account_id)
        looks = {look.key: look for look in current.collection_looks}
        for key, now in after.items():
            if looks.get(key) != before.get(key):
                continue
            if now is None:
                looks.pop(key, None)
            else:
                looks[key] = now
        return fresh.with_account(replace(current, collection_looks=tuple(looks.values())))

    engine.change_config(change)


def _sync_account(engine: Engine, settings: AccountSettings, receive_only: bool = False) -> DavSyncSummary:
    account_id = settings.account.account_id
    with _client(engine, settings) as client, _lock_for(account_id):
        with open_pim(engine, account_id, create=True) as store:
            fresh = engine.config().find(account_id)  # read inside the lock: a name or colour changed just before is in it
            looks = {look.key: look for look in fresh.collection_looks}
            report = sync_dav(
                client, store, _state_path(engine, account_id), engine.durable, push=not receive_only, left_out=frozenset(settings.dav_left_out), looks=looks,
            )
            keep_looks(engine, account_id, {key: looks.get(key) for key in report.looks}, report.looks)
    if report.conflicts:
        engine.report(Change("conflicts_changed", account_id))
    if report.pushed or report.deleted_on_server or report.made_on_server or report.conflicts or report.errors:
        engine.report(Change("waiting_changed", account_id))  # what waited may have gone (or still waits): the window asks again (D-194)
    return DavSyncSummary(
        account_id, report.collections, report.created, report.pulled, report.pushed, report.deleted_here, report.deleted_on_server, report.conflicts, tuple(report.errors),
        report.removed, report.made_on_server,
    )


def _sync_or_name(engine: Engine, settings: AccountSettings, receive_only: bool = False) -> DavSyncSummary:
    """One account's synchronisation; a certificate that could not be verified stops only this account and names its server (D-299)."""
    try:
        return _sync_account(engine, settings, receive_only)
    except CertificateError as error:
        account_id = settings.account.account_id
        return DavSyncSummary(account_id, 0, 0, 0, 0, 0, 0, 0, (str(error),), certificate=replace(error.trouble, account=account_id))


def _state_path(engine: Engine, account_id: str) -> Path:
    return engine.directories.pim_directory(account_id) / "dav-state.json"


@talks_to_servers  # a restore waits for it and holds it back meanwhile (RV-60)
def dav_sync(engine: Engine, parameters: SyncParameters) -> tuple[DavSyncSummary, ...]:
    """Bring the calendars, task lists and address books of an account in line with its server, both ways."""
    config = engine.config()
    only = parameters.receive_only
    if parameters.account is not None:
        return (_sync_or_name(engine, engine.resolve_account(parameters.account, config), only),)
    if parameters.accounts:
        chosen = [engine.resolve_account(name, config) for name in parameters.accounts]
        return tuple(_sync_or_name(engine, item, only) for item in chosen if item.dav_url and item.account.account_id != LOCAL_ACCOUNT_ID)
    return tuple(_sync_or_name(engine, item, only) for item in config.accounts if item.dav_url and item.account.account_id != LOCAL_ACCOUNT_ID)


# --- finding the server by itself (D-181) ----------------------------------------------------------------------------------------------------

WEB_PORT: int = 443  # where a calendar server is looked for (tests point it at their own server)
PUBLIC_WORD: str = "public"


@dataclass(frozen=True, slots=True)
class FindParameters:
    account: str = field(metadata=option("The account"))
    url: str = field(default="", metadata=option("Check this address (empty: look on the incoming mail server and on the domain of the address)"))
    username: str | None = field(default=None, metadata=option("Try this user name instead of the stored one"))
    password: str | None = field(default=None, metadata=option("Try this password instead of the stored one", secret=True))
    fresh: bool = field(default=False, metadata=option("Check the certificates with the usual checks only (a Find the person starts): none accepted before counts"))


@dataclass(frozen=True, slots=True)
class FoundFolder:
    key: str  # what the account remembers when this folder is left out (folder_key)
    name: str
    kind: str  # events, tasks or contacts
    public: bool  # one of the public folders next to the user's own
    read_only: bool
    ticked: bool  # how its tick starts: kept in step now, or (a server this account does not use yet) always


@dataclass(frozen=True, slots=True)
class FoundServer:
    url: str  # the address of the server that answered; empty: nothing was found
    pin: str  # host:port=fingerprint to accept with it: the certificate accepted for this account's mail (D-180); empty when not needed
    folders: tuple[FoundFolder, ...]
    problem: str  # when nothing was found: what went wrong at a place that looked like a calendar server (a certificate, the login); else empty
    certificate: CertificateTrouble | None = None  # when nothing was found: the first place whose certificate could not be verified (D-299)


def _places(settings: AccountSettings) -> list[str]:
    """Where to look, in order: the incoming mail server, then the domain of the address (RFC 6764's well-known addresses are tried at each)."""
    hosts = [settings.account.incoming.host.strip().lower(), settings.account.email_address.rpartition("@")[2].strip().lower()]
    unique = [host for position, host in enumerate(hosts) if host and host not in hosts[:position]]
    return [f"https://{host}/" if WEB_PORT == 443 else f"https://{host}:{WEB_PORT}/" for host in unique]


def _mail_fingerprints(settings: AccountSettings) -> frozenset[str]:
    """Every certificate the person accepted for this account's servers."""
    return frozenset(pin.rpartition("=")[2] for pin in settings.trusted_certificates)


def _answers_as_calendar_server(address: str, trusted: frozenset[str]) -> bool:
    """Ask the well-known addresses without logging in: a calendar server asks for the login (or answers); a web server without one does neither.
    So the password only goes to a place that is a calendar server."""
    with DavClient(address, "", "", trusted) as probe:
        for path in ("/.well-known/caldav", "/.well-known/carddav"):
            try:
                response = probe.request("PROPFIND", path, {"Depth": "0", "Content-Type": "application/xml; charset=utf-8"})
            except AuthenticationError:
                return True
            except DavError:
                continue
            if response.status == 207:
                return True
        return False


def _with_certificate(
    engine: Engine, settings: AccountSettings, address: str, attempt: Callable[[frozenset[str]], bool], fresh: bool = False,
) -> tuple[bool, str]:
    """Run an attempt at a place with the certificates accepted for it. When the certificate fails and it is exactly one the person accepted for this
    account's mail, it is accepted for this place too, without asking (D-180): (the attempt's answer, the pin to keep or "")."""
    if fresh:  # a Find the person starts: the usual checks only, the Verify certificate window asks about the rest (D-299)
        return attempt(frozenset()), ""
    trusted = _accepted_for(engine, settings, address)
    try:
        return attempt(trusted), ""
    except DavCertificateError:
        host, port = _host_and_port(address)
        shown = inspect_certificate(host, port, ConnectionSecurity.TLS, "https").fingerprint
        if shown not in _mail_fingerprints(settings) or shown in trusted:
            raise
        return attempt(frozenset({shown})), server_pin(host, port, shown)


def _found(settings: AccountSettings, address: str, collections: list[RemoteCollection]) -> tuple[FoundFolder, ...]:
    same_server = settings.dav_url.strip() == address
    left_out = {canonical_key(key) for key in settings.dav_left_out} if same_server else set[str]()
    return tuple(
        FoundFolder(folder_key(item), item.name, item.kind, item.public, item.read_only, not same_server or folder_key(item) not in left_out)
        for item in collections
    )


def dav_find(engine: Engine, parameters: FindParameters) -> FoundServer:
    """Find the calendars, task lists and address books of an account: at the address given, or by looking on its incoming mail server and on the
    domain of its address. Lists every folder (the public ones too), each with how its tick starts."""
    settings = engine.resolve_account(parameters.account)
    if settings.account.kind is AccountKind.LOCAL:
        raise UsageError("The Local Folders have no server")
    given = parameters.url.strip()
    problems: list[str] = []
    certificate: CertificateTrouble | None = None
    for address in [given] if given else _places(settings):
        found: list[RemoteCollection] = []

        def attempt(trusted: frozenset[str]) -> bool:
            if not given and not _answers_as_calendar_server(address, trusted):
                return False
            with _client(engine, settings, address, parameters.username, parameters.password, trusted) as client:
                found[:] = client.all_collections()
            return True

        try:
            answered, pin = _with_certificate(engine, settings, address, attempt, parameters.fresh)
        except ProtocolError as error:  # also a refused login or certificate
            if given:
                raise
            if isinstance(error, (DavCertificateError, AuthenticationError)):
                problems.append(f"{_host_and_port(address)[0]}: {error}")
            if isinstance(error, CertificateError) and certificate is None:
                certificate = replace(error.trouble, account=settings.account.account_id)
            continue
        if answered and found:
            return FoundServer(address, pin, _found(settings, address, found), "")
        if given:
            return FoundServer(address, pin, (), "The server was reached, but it offers no calendars, task lists or address books")
    return FoundServer("", "", (), "; ".join(problems), certificate)


@dataclass(frozen=True, slots=True)
class LinkParameters:
    account: str = field(metadata=option("The account"))
    url: str = field(metadata=option("The address of the calendar and contacts server (as dav.find gave it)"))
    left_out: tuple[str, ...] = field(default=(), metadata=option("The folders not to keep in step (their key, as dav.find gave it)"))
    pin: str = field(default="", metadata=option("The certificate to accept with it, as dav.find gave it (only one already accepted for this account)"))
    username: str = field(default="", metadata=option("The user name (empty: the same as for incoming mail)"))
    password: str | None = field(default=None, metadata=option("The password (omit: the one for incoming mail, or the one stored earlier)", secret=True))


def dav_link(engine: Engine, parameters: LinkParameters) -> AccountSummary:
    """Keep the folders of a calendar server in step with an account, except the ones left out. A folder that was kept in step and is now left out
    goes from here (to the deleted items, the server is not touched); refused while it has changes here that are not on the server yet (D-183)."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    if settings.account.kind is AccountKind.LOCAL:
        raise UsageError("The Local Folders have no server")
    account_id = settings.account.account_id
    url = parameters.url.strip()
    if not url:
        raise UsageError("Give the address of the calendar and contacts server")
    pin = parameters.pin.strip()
    if pin and pin.rpartition("=")[2] not in _mail_fingerprints(settings):
        raise UsageError("Only a certificate already accepted for this account is accepted with the calendar server")
    left_out = tuple(dict.fromkeys(canonical_key(item.strip()) for item in parameters.left_out if item.strip()))
    newly_left_out = set(left_out) - {canonical_key(key) for key in settings.dav_left_out} if url == settings.dav_url else set[str]()
    with _lock_for(account_id):
        if newly_left_out:
            with open_pim(engine, account_id, create=True) as store:
                going = [info for info in (matching_collection(store, key) for key in sorted(newly_left_out)) if info is not None]
                waiting = [info.name for info in going if unsent_changes(store, info, _state_path(engine, account_id))]
                if waiting:
                    raise UsageError(f"{', '.join(waiting)} has changes here that are not on the server yet; try again after the next synchronisation")
                for info in going:
                    store.delete_collection(info.collection_id)
                    forget_collection(_state_path(engine, account_id), info.collection_id, engine.durable)

        def change(fresh: AppConfig) -> AppConfig:
            current = fresh.find(account_id)
            pins = current.trusted_certificates if not pin or pin in current.trusted_certificates else (*current.trusted_certificates, pin)
            return fresh.with_account(replace(current, dav_url=url, dav_username=parameters.username.strip(), dav_left_out=left_out, trusted_certificates=pins))

        config = engine.change_config(change)
    if parameters.password is not None:
        engine.passwords.set(account_id, ServerRole.DAV, parameters.password)
        label_account(engine, account_id)  # B-23
    return _summary(config.find(account_id))


# --- an account of calendars, contacts and tasks only (D-185) -------------------------------------------------------------------------------------


def web_address(text: str) -> str:
    """The address as typed, with https:// in front when no scheme was typed (a host name alone is enough)."""
    address = text.strip()
    if address and "://" not in address:
        address = "https://" + address
    if address and urllib.parse.urlsplit(address).path == "":
        address += "/"
    return address


@dataclass(frozen=True, slots=True)
class FindNewParameters:
    url: str = field(metadata=option("The address of the calendar and contacts server (a host name is enough)"))
    username: str = field(metadata=option("The user name"))
    password: str = field(metadata=option("The password", secret=True))
    fingerprint: str = field(default="", metadata=option("The SHA-256 fingerprint of a certificate the person accepted for this server"))
    fresh: bool = field(default=False, metadata=option("Check the certificate with the usual checks only (a Find the person starts): none accepted before counts"))


def _new_client(engine: Engine, url: str, username: str, password: str, fingerprint: str, fresh: bool = False) -> DavClient:
    host, port = _host_and_port(url)
    if not host:
        raise UsageError("Type the address of the calendar and contacts server")
    if fresh:
        return DavClient(url, username, password)
    return DavClient(url, username, password, fingerprints_for(engine.pins((server_pin(host, port, fingerprint),) if fingerprint else ()), host, port))


def dav_find_new(engine: Engine, parameters: FindNewParameters) -> FoundServer:
    """What a calendar and contacts server offers to a login that is not an account yet: every folder, the public ones too, all ticked."""
    url = web_address(parameters.url)
    with _new_client(engine, url, parameters.username.strip(), parameters.password, parameters.fingerprint.strip(), parameters.fresh) as client:
        found = client.all_collections()
    pin = server_pin(*_host_and_port(url), parameters.fingerprint.strip()) if parameters.fingerprint.strip() else ""
    folders = tuple(FoundFolder(folder_key(item), item.name, item.kind, item.public, item.read_only, True) for item in found)
    return FoundServer(url, pin, folders, "" if folders else "The server was reached, but it offers no calendars, task lists or address books")


@dataclass(frozen=True, slots=True)
class AddDavParameters:
    url: str = field(metadata=option("The address of the calendar and contacts server (a host name is enough)"))
    username: str = field(metadata=option("The user name"))
    password: str = field(metadata=option("The password; read from standard input or asked for, never taken from the command line", secret=True))
    account_name: str = field(default="", metadata=option("How Chrono Mail names the account (empty: the user name)"))
    left_out: tuple[str, ...] = field(default=(), metadata=option("The folders not to keep in step (their key, as dav.find-new gave it)"))
    fingerprint: str = field(default="", metadata=option("The SHA-256 fingerprint of a certificate the person accepted for this server"))
    unchecked: bool = field(default=False, metadata=option("Add it without asking the server first (an account in use in another program, imported)"))


def account_add_dav(engine: Engine, parameters: AddDavParameters) -> AccountAddResult:
    """Add an account of calendars, contacts and tasks only. Its server is asked first: only a login that reaches folders becomes an account, so a
    wrong address or password leaves nothing behind."""
    url = web_address(parameters.url)
    username = parameters.username.strip()
    if not username:
        raise UsageError("Type the user name")
    fingerprint = parameters.fingerprint.strip()
    if not parameters.unchecked:
        with _new_client(engine, url, username, parameters.password, fingerprint) as client:
            offered = client.all_collections()
        if not offered:
            raise UsageError("The server was reached, but it offers no calendars, task lists or address books")
    host, port = _host_and_port(url)
    _refuse_same_account(engine.config().accounts, AccountKind.DAV, username, host, None)
    _, key_created = ensure_local_folders(engine, True)  # the data key the calendars are kept with exists from here on
    account_id = new_account_id()
    server = ServerSettings(host, port, ConnectionSecurity.TLS, username)
    account_name = parameters.account_name.strip()
    settings = AccountSettings(
        Account(account_id, account_name or username, "", AccountKind.DAV, server, server),
        trusted_certificates=(server_pin(host, port, fingerprint),) if fingerprint else (),
        dav_url=url, dav_left_out=tuple(dict.fromkeys(item.strip() for item in parameters.left_out if item.strip())), account_name=account_name,
    )
    engine.passwords.set(account_id, ServerRole.INCOMING, parameters.password)
    try:
        def add(fresh: AppConfig) -> AppConfig:
            _refuse_same_account(fresh.accounts, AccountKind.DAV, username, host, None)  # added meanwhile?
            return fresh.with_account(settings)

        engine.change_config(add)  # the commit point
    except ChronoMailError:
        engine.passwords.delete(account_id, ServerRole.INCOMING)
        raise
    label_account(engine, account_id)  # B-23
    return AccountAddResult(account_id, key_created, (), recovery_code_not_stored(engine))


DAV_FIND_NEW: TypedCommand[FindNewParameters, FoundServer] = TypedCommand("dav.find-new", FindNewParameters, FoundServer)
ACCOUNT_ADD_DAV: TypedCommand[AddDavParameters, AccountAddResult] = TypedCommand("account.add-dav", AddDavParameters, AccountAddResult)
DAV_FIND: TypedCommand[FindParameters, FoundServer] = TypedCommand("dav.find", FindParameters, FoundServer)
DAV_LINK: TypedCommand[LinkParameters, AccountSummary] = TypedCommand("dav.link", LinkParameters, AccountSummary)


DAV_SYNC: TypedCommand[SyncParameters, tuple[DavSyncSummary, ...]] = TypedCommand("dav.sync", SyncParameters, tuple)  # every element is a DavSyncSummary


ACCOUNT_SET_DAV: TypedCommand[SetDavParameters, AccountSummary] = TypedCommand("account.set-dav", SetDavParameters, AccountSummary)


def register(registry: CommandRegistry) -> None:
    registry.register(ACCOUNT_SET_DAV.id, "Set the calendar and contacts server (CalDAV and CardDAV) of an account", SetDavParameters, dav_set, doing="Saving the calendar and contacts server")
    registry.register("dav.discover", "List what the calendar and contacts server of an account offers", DiscoverParameters, dav_discover, doing="Asking the calendar server what it offers")
    registry.register(DAV_SYNC.id, "Synchronise calendars, task lists and address books with the server, both ways", SyncParameters, quiet_command(dav_sync), doing="Synchronising calendars, contacts and tasks")
    registry.register(DAV_FIND.id, "Find the calendars, task lists and address books of an account (at an address, or by looking for its server)", FindParameters, dav_find, doing="Looking for calendars, contacts and tasks")
    registry.register(DAV_FIND_NEW.id, "List what a calendar and contacts server offers to a login that is not an account yet", FindNewParameters, dav_find_new, doing="Looking for calendars, contacts and tasks")
    registry.register(ACCOUNT_ADD_DAV.id, "Add an account of calendars, contacts and tasks only (no mail)", AddDavParameters, account_add_dav, doing="Adding an account of calendars and contacts")
    registry.register(DAV_LINK.id, "Keep the folders of a calendar and contacts server in step with an account, except the ones left out", LinkParameters, dav_link, destructive=True, doing="Saving the calendars, contacts and tasks to keep in step")
