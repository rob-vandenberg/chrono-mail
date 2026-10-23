import os
import shutil
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from chrono_mail.config import (
    load_sidebar,
    save_sidebar,
    AccountSettings,
    AppConfig,
    Pop3DeletePolicy,
    Pop3Options,
    ServerRole,
    fingerprints_for,
    new_account_id,
    server_pin,
)
from chrono_mail.core import (
    Account,
    AccountKind,
    ChronoMailError,
    ConfigurationError,
    ConnectionSecurity,
    CryptoError,
    ServerSettings,
)
from chrono_mail.crypto import (
    CredentialStoreError,
    DataKey,
    generate_data_key,
    key_from_recovery_code,
    recovery_code_for,
    seal_key_with_passphrase,
    unseal_key_with_passphrase,
)
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from chrono_mail.protocols.tls import context_for, inspect_certificate, valid_fingerprint
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.protocols.smtp import SmtpLimits, check_login
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "commands_accounts.py 2.1.308"

# --- Version history ----------------------------------------------------
# v2.1.308: junk_filter in the summary and in account.edit (D-238)
# v1.9.272: certificate.inspect takes kind https (a calendar and contacts server, tls only): Look at the server's certificate in the sections' Add account… always failed (since 1.1.212)
# v1.9.271: R-19: typed account.add, -check, -edit, -list, -remove, -trust-certificate, -forget-certificates, certificate.inspect, key.status, -export-file, -recovery-code, -restore-file, -restore-code
# v1.1.252: removing an account changes the sidebar file under the engine's sidebar lock (R-41)
# v1.1.243: account.check with account: a password not typed is the stored one, so testing a stored account never asks for it again (R-05)
# v1.1.213: outgoing_same_login: while on, the outgoing login follows the incoming one, a new incoming password included (D-186)
# v1.1.212: Accounts of calendars and contacts only: named by their account name or user name, their server from the calendar address; edit keeps their empty address; remove forgets the calendar password too (D-185)
# v1.1.211: The account name: what Chrono Mail shows for an account when it is set (shown_name, account_label); display_name is "Your name" (D-184)
# v1.1.197: key.status says why the key could not be had: the store's own reason, and whether the store answered at all
# v0.5.144: Every change of the settings is made on a fresh read, one command at a time (change_config): changes at the same moment are all kept
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.69: An account is the same account only with the same user name, incoming server and protocol (not the same e-mail address); labels that tell accounts apart
# v0.0.66: Address and user name of the calendar and contacts server in the summary
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.45: storage of an account (own folders or Local Folders)
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.43: Certificate exceptions
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.20: Settings window, account editing, signatures
# v0.0.31: IMAP accounts
# v0.0.13: account.check
# v0.0.12: Initial account and key commands

Security = Literal["tls", "starttls", "none"]
Kind = Literal["pop3", "imap"]
Policy = Literal["keep", "delete_after_download", "delete_after_days", "delete_when_deleted_locally"]


@dataclass(frozen=True, slots=True)
class AccountAddParameters:
    display_name: str = field(metadata=option("Your name: what the people you write to see, with the address"))
    email_address: str = field(metadata=option("The e-mail address of the account"))
    username: str = field(metadata=option("The user name for the incoming server (and the outgoing server unless given separately)"))
    password: str = field(metadata=option("The password; read from standard input or asked for, never taken from the command line", secret=True))
    incoming_host: str = field(metadata=option("The POP3 server"))
    outgoing_host: str = field(metadata=option("The SMTP server"))
    incoming_port: int = field(default=995, metadata=option("The POP3 port"))
    incoming_security: Security = field(default="tls", metadata=option("tls (implicit TLS), starttls (STLS) or none"))
    outgoing_port: int = field(default=587, metadata=option("The SMTP port"))
    outgoing_security: Security = field(default="starttls", metadata=option("tls, starttls or none"))
    outgoing_username: str | None = field(default=None, metadata=option("The SMTP user name, when it differs from the incoming one"))
    outgoing_password: str | None = field(default=None, metadata=option("The SMTP password, when it differs from the incoming one", secret=True))
    encrypted: bool = field(default=True, metadata=option("Encrypt the stored messages on disk (the default)"))
    delete_policy: Policy = field(default="keep", metadata=option("What happens to a message on the server after it was downloaded and verified"))
    delete_days: int = field(default=30, metadata=option("Days before deleting, for delete_after_days"))
    kind: Kind = field(default="pop3", metadata=option("pop3 (mail is downloaded) or imap (the mail stays on the server and is kept in step)"))
    trusted_certificate: tuple[str, ...] = field(default=(), metadata=option("The SHA-256 fingerprint of a server certificate to accept (repeat for more); see certificate.inspect"))
    storage: Literal["own", "local"] = field(default="own", metadata=option("POP3 only: own (the account has folders of its own) or local (its mail goes into the shared Local Folders)"))
    account_name: str = field(default="", metadata=option("How Chrono Mail names the account in its own lists, never in mail (empty: the e-mail address)"))


@dataclass(frozen=True, slots=True)
class AccountAddResult:
    account_id: str
    key_created: bool
    folders: tuple[str, ...]


def _fingerprints(values: tuple[str, ...]) -> tuple[str, ...]:
    """Fingerprints as typed (with or without colons, any case), checked and without repeats."""
    found: list[str] = []
    for value in values:
        text = value.replace(":", "").replace(" ", "").lower()
        if not valid_fingerprint(text):
            raise UsageError("A certificate fingerprint is the SHA-256 value: 64 hexadecimal characters")
        if text not in found:
            found.append(text)
    return tuple(found)


def _pins(values: tuple[str, ...]) -> tuple[str, ...]:
    """Accepted certificates as typed: a fingerprint (for every server of the account) or host:port=fingerprint (for that server only)."""
    found: list[str] = []
    for value in values:
        server, separator, fingerprint = value.rpartition("=")
        (checked,) = _fingerprints((fingerprint if separator else value,))
        if separator:
            host, _, port = server.rpartition(":")
            if not host.strip() or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise UsageError("An accepted certificate for one server is written host:port=fingerprint")
            checked = server_pin(host, int(port), checked)
        if checked not in found:
            found.append(checked)
    return tuple(found)


def _security(value: str) -> ConnectionSecurity:
    return ConnectionSecurity(value)


def account_add(engine: Engine, parameters: AccountAddParameters) -> AccountAddResult:
    if parameters.storage == "local":
        if parameters.kind != "pop3":
            raise UsageError("Only a POP3 account can keep its mail in Local Folders")
        _, local_key_created = ensure_local_folders(engine, parameters.encrypted)
    else:
        local_key_created = False
    config = engine.config()
    email = parameters.email_address.strip()
    if email.count("@") != 1 or email.startswith("@") or email.endswith("@"):
        raise UsageError("The e-mail address is not valid")
    _refuse_same_account(config.accounts, AccountKind(parameters.kind), parameters.username, parameters.incoming_host, None)
    account_id = new_account_id()
    account = Account(
        account_id,
        parameters.display_name.strip(),
        email,
        AccountKind(parameters.kind),
        ServerSettings(parameters.incoming_host.strip(), parameters.incoming_port, _security(parameters.incoming_security), parameters.username),
        ServerSettings(parameters.outgoing_host.strip(), parameters.outgoing_port, _security(parameters.outgoing_security), parameters.outgoing_username or parameters.username),
    )
    settings = AccountSettings(
        account, Pop3Options(Pop3DeletePolicy(parameters.delete_policy), parameters.delete_days), parameters.encrypted,
        trusted_certificates=_pins(parameters.trusted_certificate), storage=parameters.storage, account_name=parameters.account_name.strip(),
        outgoing_same_login=parameters.outgoing_username in (None, parameters.username) and parameters.outgoing_password in (None, parameters.password),
    )
    key: DataKey | None = None
    key_created = False
    new_config = config
    if parameters.encrypted:
        if config.key_id is None:
            key = generate_data_key()
            engine.keys.save(key)
            if engine.keys.load(key.key_id) != key:  # never create an encrypted store whose key cannot be read back
                raise CryptoError("The credential store did not return the key that was just saved")
            key_created = True
            new_config = config.with_key_id(key.key_id.hex())
        else:
            key = engine.data_key(config)
    store_directory = engine.directories.store_directory(account_id)
    engine.passwords.set(account_id, ServerRole.INCOMING, parameters.password)
    engine.passwords.set(account_id, ServerRole.OUTGOING, parameters.outgoing_password or parameters.password)
    try:
        if parameters.storage == "own":
            store = Store.create(store_directory, account_id, key, durable=engine.durable)
            for path, role in STANDARD_FOLDERS:
                store.create_folder(path, role)
        key_id = new_config.key_id

        def add(fresh: AppConfig) -> AppConfig:
            _refuse_same_account(fresh.accounts, settings.account.kind, settings.account.incoming.username, settings.account.incoming.host, None)  # added meanwhile?
            return (fresh.with_key_id(key_id) if fresh.key_id is None else fresh).with_account(settings)

        engine.change_config(add)  # the commit point: until here nothing refers to the account
    except ChronoMailError:
        engine.passwords.delete(account_id, ServerRole.INCOMING)
        engine.passwords.delete(account_id, ServerRole.OUTGOING)
        shutil.rmtree(engine.directories.account_directory(account_id), ignore_errors=True)
        raise
    return AccountAddResult(account_id, key_created or local_key_created, tuple("/".join(path) for path, _ in STANDARD_FOLDERS))


@dataclass(frozen=True, slots=True)
class AccountCheckParameters:
    username: str = field(metadata=option("The user name for the incoming server (and the outgoing server unless given separately)"))
    incoming_host: str = field(metadata=option("The POP3 server"))
    outgoing_host: str = field(metadata=option("The SMTP server"))
    password: str = field(default="", metadata=option("The password (for an account given with account: empty means the stored one)", secret=True))
    incoming_port: int = field(default=995, metadata=option("The POP3 port"))
    incoming_security: Security = field(default="tls", metadata=option("tls, starttls or none"))
    outgoing_port: int = field(default=587, metadata=option("The SMTP port"))
    outgoing_security: Security = field(default="starttls", metadata=option("tls, starttls or none"))
    outgoing_username: str | None = field(default=None, metadata=option("The SMTP user name, when it differs from the incoming one"))
    outgoing_password: str | None = field(default=None, metadata=option("The SMTP password, when it differs from the incoming one", secret=True))
    kind: Kind = field(default="pop3", metadata=option("pop3 or imap"))
    trusted_certificate: tuple[str, ...] = field(default=(), metadata=option("The SHA-256 fingerprint of a server certificate to accept (repeat for more)"))
    account: str = field(default="", metadata=option("An account that exists: a password not given is its stored one (nothing is changed)"))


@dataclass(frozen=True, slots=True)
class AccountCheckResult:
    incoming_ok: bool
    incoming_message: str
    outgoing_ok: bool
    outgoing_message: str


def account_check(engine: Engine, parameters: AccountCheckParameters) -> AccountCheckResult:
    """Try to connect and log in to both servers, before an account is created or with the changes of one being edited. Nothing is stored or
    sent. For an account that exists a password not typed is the stored one: a password typed once is never asked for again (R-05)."""
    pins = _pins(parameters.trusted_certificate)
    password, outgoing_password = parameters.password, parameters.outgoing_password
    if parameters.account:
        account_id = engine.resolve_account(parameters.account).account.account_id
        password = password or engine.passwords.get(account_id, ServerRole.INCOMING) or ""
        if not outgoing_password:  # the same login: the incoming password (a new one included); else the stored outgoing one
            outgoing_password = (engine.passwords.get(account_id, ServerRole.OUTGOING) or password) if parameters.outgoing_username is not None else password
    if not password:
        raise ConfigurationError("No password was typed, and none is stored for this account")
    try:
        incoming = ServerSettings(parameters.incoming_host.strip(), parameters.incoming_port, _security(parameters.incoming_security), parameters.username)
        context = context_for(fingerprints_for(pins, incoming.host, incoming.port))
        if parameters.kind == "imap":
            ImapClient(incoming, password, ImapLimits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        else:
            Pop3Client(incoming, password, Pop3Limits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        incoming_ok, incoming_message = True, "Connected and logged in"
    except ChronoMailError as error:
        incoming_ok, incoming_message = False, str(error)
    try:
        outgoing = ServerSettings(parameters.outgoing_host.strip(), parameters.outgoing_port, _security(parameters.outgoing_security), parameters.outgoing_username or parameters.username)
        check_login(outgoing, outgoing_password or password, SmtpLimits(timeout_seconds=20), context_for(fingerprints_for(pins, outgoing.host, outgoing.port)))
        outgoing_ok, outgoing_message = True, "Connected and logged in"
    except ChronoMailError as error:
        outgoing_ok, outgoing_message = False, str(error)
    return AccountCheckResult(incoming_ok, incoming_message, outgoing_ok, outgoing_message)


@dataclass(frozen=True, slots=True)
class NoParameters:
    pass


def _refuse_same_account(accounts: tuple[AccountSettings, ...], kind: AccountKind, username: str, host: str, own_id: str | None) -> None:
    """One mailbox is one account: the same user name on the same incoming server with the same protocol cannot be added twice. The e-mail address
    and the port do not matter: the same address on different servers, or the same mailbox through POP3 and through IMAP, are different accounts."""
    for item in accounts:
        incoming = item.account.incoming
        if (
            item.account.account_id != own_id and item.account.kind is kind and incoming.username.strip().lower() == username.strip().lower()
            and incoming.host.strip().lower() == host.strip().lower()
        ):
            raise ConfigurationError("An account with this user name on this incoming mail server and with this protocol exists already")


def server_label(item: AccountSettings) -> str:
    """The incoming server and the protocol, like mail.example.com (POP3); for calendars and contacts only, the calendar server."""
    if item.account.kind is AccountKind.LOCAL:
        return ""
    if item.account.kind is AccountKind.DAV:
        return f"{urllib.parse.urlsplit(item.dav_url).hostname or item.account.incoming.host} ({DAV_KIND_WORDS})"
    return f"{item.account.incoming.host} ({item.account.kind.value.upper()})"


DAV_KIND_WORDS: str = "Calendars and contacts"


def _own_name(item: AccountSettings) -> str:
    """The account name the person chose, else the e-mail address (an account without mail: its user name)."""
    return item.account_name or item.account.email_address or item.account.incoming.username


def account_label(item: AccountSettings, accounts: tuple[AccountSettings, ...] = ()) -> str:
    """How an account is named where it must be told apart from the others: its account name (with server and protocol when another account has the
    same name), else address - server (protocol)."""
    if item.account.kind is AccountKind.LOCAL:
        return item.account.display_name
    if item.account_name:
        return shown_name(item, accounts)
    return f"{_own_name(item)} - {server_label(item)}"


def shown_name(item: AccountSettings, accounts: tuple[AccountSettings, ...]) -> str:
    """The account name, else the e-mail address; with the server and protocol as well when another account would show the same (D-184)."""
    if item.account.kind is AccountKind.LOCAL:
        return item.account.display_name
    own = _own_name(item)
    shared = any(
        other.account.account_id != item.account.account_id and other.account.kind is not AccountKind.LOCAL and _own_name(other).lower() == own.lower()
        for other in accounts
    )
    return f"{own} - {server_label(item)}" if shared else own


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account_id: str
    display_name: str
    email_address: str
    kind: str
    encrypted: bool
    incoming: str
    outgoing: str
    delete_policy: str
    delete_days: int = 30
    signature: str = ""
    incoming_host: str = ""
    incoming_port: int = 0
    incoming_security: str = ""
    incoming_username: str = ""
    outgoing_host: str = ""
    outgoing_port: int = 0
    outgoing_security: str = ""
    outgoing_username: str = ""
    trusted_certificates: tuple[str, ...] = ()
    check_minutes: int = 0
    storage: str = "own"
    dav_url: str = ""
    dav_username: str = ""
    label: str = ""  # address - incoming server (protocol): tells accounts with the same address apart
    server_label: str = ""
    shown_name: str = ""  # the account name, else the address; with server and protocol when another account would show the same
    account_name: str = ""  # how Chrono Mail names the account (empty: the address); display_name is "Your name"
    outgoing_same_login: bool = True  # the outgoing server uses the login of the incoming one
    junk_filter: bool = True  # new mail goes through the junk filter (D-238)


def _summary(item: AccountSettings, accounts: tuple[AccountSettings, ...] = ()) -> AccountSummary:
    return AccountSummary(
        item.account.account_id,
        item.account.display_name,
        item.account.email_address,
        item.account.kind.value,
        item.encrypted,
        f"{item.account.incoming.host}:{item.account.incoming.port} ({item.account.incoming.security.value})",
        f"{item.account.outgoing.host}:{item.account.outgoing.port} ({item.account.outgoing.security.value})",
        item.pop3.policy.value,
        item.pop3.days,
        item.signature,
        item.account.incoming.host,
        item.account.incoming.port,
        item.account.incoming.security.value,
        item.account.incoming.username,
        item.account.outgoing.host,
        item.account.outgoing.port,
        item.account.outgoing.security.value,
        item.account.outgoing.username,
        item.trusted_certificates,
        item.check_minutes,
        item.storage,
        item.dav_url,
        item.dav_username,
        account_label(item, accounts),
        server_label(item),
        shown_name(item, accounts),
        item.account_name,
        item.outgoing_same_login,
        item.junk_filter,
    )


def account_list(engine: Engine, parameters: NoParameters) -> tuple[AccountSummary, ...]:
    accounts = engine.config().accounts
    return tuple(_summary(item, accounts) for item in accounts)


@dataclass(frozen=True, slots=True)
class AccountParameters:
    account: str = field(metadata=option("The account: its id, the start of its id, or its e-mail address"))


@dataclass(frozen=True, slots=True)
class AccountRemoveResult:
    account_id: str
    data_kept_in: str


def account_remove(engine: Engine, parameters: AccountParameters) -> AccountRemoveResult:
    """Forget the account and its passwords. The mail on disk is NOT deleted: it stays where it is."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    account_id = settings.account.account_id

    def remove(fresh: AppConfig) -> AppConfig:
        if settings.account.kind is AccountKind.LOCAL and any(item.storage == "local" for item in fresh.accounts):
            raise UsageError("Accounts still keep their mail in Local Folders: give them folders of their own first")
        return fresh.without_account(account_id)

    engine.change_config(remove)
    try:  # pinned folders and group memberships of the account are forgotten; a damaged sidebar file is reported when it is used
        with engine.sidebar_lock:
            sidebar = load_sidebar(engine.directories.sidebar_file)
            if any(item.account == account_id for item in sidebar.pins) or any(account_id in item.accounts for item in sidebar.groups):
                save_sidebar(engine.directories.sidebar_file, sidebar.without_account(account_id))
    except ConfigurationError:
        pass
    engine.passwords.delete(account_id, ServerRole.INCOMING)
    engine.passwords.delete(account_id, ServerRole.OUTGOING)
    engine.passwords.delete(account_id, ServerRole.DAV)
    return AccountRemoveResult(account_id, str(engine.directories.account_directory(account_id)))


@dataclass(frozen=True, slots=True)
class AccountPolicyParameters:
    account: str = field(metadata=option("The account"))
    policy: Policy = field(metadata=option("What happens to a message on the server after it was downloaded and verified"))
    days: int = field(default=30, metadata=option("Days before deleting, for delete_after_days"))


def account_policy(engine: Engine, parameters: AccountPolicyParameters) -> AccountSummary:
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    account_id = settings.account.account_id
    pop3 = Pop3Options(Pop3DeletePolicy(parameters.policy), parameters.days)
    config = engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(account_id), pop3=pop3)))
    return _summary(config.find(account_id))


@dataclass(frozen=True, slots=True)
class AccountEditParameters:
    account: str = field(metadata=option("The account"))
    display_name: str | None = field(default=None, metadata=option("Your name: what the people you write to see, with the address"))
    account_name: str | None = field(default=None, metadata=option("How Chrono Mail names the account in its own lists, never in mail (empty: the e-mail address)"))
    email_address: str | None = field(default=None, metadata=option("The e-mail address of the account"))
    signature: str | None = field(default=None, metadata=option("The signature added to new messages (empty removes it)"))
    username: str | None = field(default=None, metadata=option("The user name for the incoming server"))
    password: str | None = field(default=None, metadata=option("A new password for the incoming server", secret=True))
    incoming_host: str | None = field(default=None, metadata=option("The POP3 server"))
    incoming_port: int | None = field(default=None, metadata=option("The POP3 port"))
    incoming_security: Security | None = field(default=None, metadata=option("tls, starttls or none"))
    outgoing_username: str | None = field(default=None, metadata=option("The SMTP user name"))
    outgoing_password: str | None = field(default=None, metadata=option("A new password for the outgoing server", secret=True))
    outgoing_host: str | None = field(default=None, metadata=option("The SMTP server"))
    outgoing_port: int | None = field(default=None, metadata=option("The SMTP port"))
    outgoing_security: Security | None = field(default=None, metadata=option("tls, starttls or none"))
    delete_policy: Policy | None = field(default=None, metadata=option("What happens to a message on the server after it was downloaded and verified"))
    delete_days: int | None = field(default=None, metadata=option("Days before deleting, for delete_after_days"))
    check_minutes: int | None = field(default=None, metadata=option("Minutes between automatic checks of this account (0: as set in the general settings)"))
    outgoing_same_login: bool | None = field(default=None, metadata=option("The outgoing server uses the user name and password of the incoming one"))
    junk_filter: bool | None = field(default=None, metadata=option("New mail goes through the junk filter: junk to the Junk folder before the rules run"))


def account_edit(engine: Engine, parameters: AccountEditParameters) -> AccountSummary:
    """Change the settings of an account. What is not given stays as it was. The stored mail is not touched."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    account = settings.account
    email = account.email_address if parameters.email_address is None else parameters.email_address.strip()
    if account.kind is AccountKind.DAV:
        email = ""  # calendars and contacts only: no address
    elif email.count("@") != 1 or email.startswith("@") or email.endswith("@"):
        raise UsageError("The e-mail address is not valid")
    name = account.display_name if parameters.display_name is None else (parameters.display_name.strip() or email)
    incoming = ServerSettings(
        account.incoming.host if parameters.incoming_host is None else parameters.incoming_host.strip(),
        account.incoming.port if parameters.incoming_port is None else parameters.incoming_port,
        account.incoming.security if parameters.incoming_security is None else _security(parameters.incoming_security),
        account.incoming.username if parameters.username is None else parameters.username.strip(),
    )
    differs = (parameters.outgoing_username is not None and parameters.outgoing_username.strip() != incoming.username) or (
        bool(parameters.outgoing_password) and parameters.outgoing_password != parameters.password
    )  # an outgoing login of its own, given without the choice: it is no longer the same (never ignored without a word)
    same_login = (settings.outgoing_same_login and not differs) if parameters.outgoing_same_login is None else parameters.outgoing_same_login
    outgoing = ServerSettings(
        account.outgoing.host if parameters.outgoing_host is None else parameters.outgoing_host.strip(),
        account.outgoing.port if parameters.outgoing_port is None else parameters.outgoing_port,
        account.outgoing.security if parameters.outgoing_security is None else _security(parameters.outgoing_security),
        incoming.username if same_login else account.outgoing.username if parameters.outgoing_username is None else parameters.outgoing_username.strip(),
    )
    if not incoming.username or not outgoing.username:
        raise UsageError("A user name must not be empty")
    _refuse_same_account(config.accounts, account.kind, incoming.username, incoming.host, account.account_id)
    pop3 = Pop3Options(
        settings.pop3.policy if parameters.delete_policy is None else Pop3DeletePolicy(parameters.delete_policy),
        settings.pop3.days if parameters.delete_days is None else parameters.delete_days,
    )
    signature = settings.signature if parameters.signature is None else parameters.signature.replace("\r\n", "\n").rstrip()
    check_minutes = settings.check_minutes if parameters.check_minutes is None else parameters.check_minutes
    account_name = settings.account_name if parameters.account_name is None else parameters.account_name.strip()
    junk_filter = settings.junk_filter if parameters.junk_filter is None else parameters.junk_filter

    def edit(fresh: AppConfig) -> AppConfig:  # the edited parts on the account as it is now: what other commands changed meanwhile stays
        _refuse_same_account(fresh.accounts, account.kind, incoming.username, incoming.host, account.account_id)
        current = fresh.find(account.account_id)
        return fresh.with_account(replace(
            current,
            account=replace(current.account, display_name=name, email_address=email, incoming=incoming, outgoing=outgoing),
            pop3=pop3,
            signature=signature,
            check_minutes=check_minutes,
            account_name=account_name,
            outgoing_same_login=same_login,
            junk_filter=junk_filter,
        ))
    # Passwords first: a new password that cannot be stored must not leave settings behind that expect it
    if parameters.password:
        engine.passwords.set(account.account_id, ServerRole.INCOMING, parameters.password)
    if same_login:  # the outgoing password follows the incoming one: a new one, or the stored one when the tick was just set
        follow = parameters.password or (None if settings.outgoing_same_login else engine.passwords.get(account.account_id, ServerRole.INCOMING))
        if follow:
            engine.passwords.set(account.account_id, ServerRole.OUTGOING, follow)
    elif parameters.outgoing_password:
        engine.passwords.set(account.account_id, ServerRole.OUTGOING, parameters.outgoing_password)
    config = engine.change_config(edit)
    return _summary(config.find(account.account_id))


# --- keys -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeyStatus:
    key_id: str | None
    in_credential_store: bool
    problem: str = ""  # why the key could not be had, in the credential store's own words ("" when it is there)
    store_answered: bool = True  # False: the store could not be asked at all (locked, not running yet); the key may well be there


def key_status(engine: Engine, parameters: NoParameters) -> KeyStatus:
    config = engine.config()
    if config.key_id is None:
        return KeyStatus(None, False)
    try:
        engine.keys.load(bytes.fromhex(config.key_id))
    except CredentialStoreError as error:
        return KeyStatus(config.key_id, False, str(error), store_answered=False)
    except CryptoError as error:  # not there, or there but damaged: the store did answer
        return KeyStatus(config.key_id, False, str(error))
    return KeyStatus(config.key_id, True)


@dataclass(frozen=True, slots=True)
class KeyExportParameters:
    path: str = field(metadata=option("Where to write the recovery file"))
    passphrase: str = field(metadata=option("The passphrase that protects the recovery file", secret=True))
    overwrite: bool = field(default=False, metadata=option("Replace an existing file"))


@dataclass(frozen=True, slots=True)
class KeyExportResult:
    key_id: str
    path: str


def key_export_file(engine: Engine, parameters: KeyExportParameters) -> KeyExportResult:
    """Write the data key, sealed with a passphrase, to a file. Keep it somewhere other than this computer."""
    key = engine.data_key(engine.config())
    target = Path(parameters.path)
    if target.exists() and not parameters.overwrite:
        raise UsageError("The file exists; use overwrite to replace it")
    blob = seal_key_with_passphrase(key, parameters.passphrase)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(descriptor, blob)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(target)
    if unseal_key_with_passphrase(target.read_bytes(), parameters.passphrase) != key:  # the file is only good if it can be read back
        raise CryptoError("The recovery file could not be verified")
    return KeyExportResult(key.key_id.hex(), str(target))


@dataclass(frozen=True, slots=True)
class RecoveryCode:
    key_id: str
    code: str


def key_recovery_code(engine: Engine, parameters: NoParameters) -> RecoveryCode:
    """The data key as a printable code. Anyone who has this code can read your mail: store it like a password."""
    key = engine.data_key(engine.config())
    return RecoveryCode(key.key_id.hex(), recovery_code_for(key))


@dataclass(frozen=True, slots=True)
class KeyRestoreFileParameters:
    path: str = field(metadata=option("The recovery file"))
    passphrase: str = field(metadata=option("The passphrase of the recovery file", secret=True))


@dataclass(frozen=True, slots=True)
class KeyRestoreCodeParameters:
    code: str = field(metadata=option("The recovery code", secret=True))


def _restore(engine: Engine, key: DataKey) -> KeyStatus:
    config = engine.config()
    if config.key_id is None:
        raise ConfigurationError("No account uses a data key yet")
    if key.key_id.hex() != config.key_id:
        raise CryptoError("This is not the key of this installation's accounts")
    engine.keys.save(key)
    if engine.keys.load(key.key_id) != key:
        raise CryptoError("The credential store did not return the key that was just saved")
    return KeyStatus(config.key_id, True)


def key_restore_file(engine: Engine, parameters: KeyRestoreFileParameters) -> KeyStatus:
    try:
        blob = Path(parameters.path).read_bytes()
    except OSError as error:
        raise UsageError(f"The recovery file cannot be read: {error.strerror}") from None
    return _restore(engine, unseal_key_with_passphrase(blob, parameters.passphrase))


def key_restore_code(engine: Engine, parameters: KeyRestoreCodeParameters) -> KeyStatus:
    return _restore(engine, key_from_recovery_code(parameters.code))


@dataclass(frozen=True, slots=True)
class CertificateParameters:
    host: str = field(metadata=option("The server"))
    port: int = field(metadata=option("Its port"))
    security: Security = field(default="tls", metadata=option("tls (implicit TLS) or starttls"))
    kind: Literal["pop3", "imap", "smtp", "https"] = field(default="imap", metadata=option("Which kind of server it is: pop3, imap, smtp, or https (a calendar and contacts server)"))


@dataclass(frozen=True, slots=True)
class CertificateResult:
    fingerprint: str
    subject: str
    issuer: str
    names: tuple[str, ...]
    not_before: str
    not_after: str
    expired: bool
    accepted_by_default: bool  # would the usual checks accept it? (then there is nothing to accept)
    problem: str | None


def certificate_inspect(engine: Engine, parameters: CertificateParameters) -> CertificateResult:
    """Look at the certificate a server presents, without logging in. Nothing is trusted by looking."""
    if parameters.kind == "https" and parameters.security != "tls":
        raise UsageError("A calendar and contacts server (https) is reached with tls only")
    info = inspect_certificate(parameters.host.strip(), parameters.port, _security(parameters.security), parameters.kind)
    return CertificateResult(info.fingerprint, info.subject, info.issuer, info.names, info.not_before, info.not_after, info.expired, info.verified, info.problem)


@dataclass(frozen=True, slots=True)
class TrustParameters:
    account: str = field(metadata=option("The account"))
    host: str = field(metadata=option("The server the certificate belongs to"))
    port: int = field(metadata=option("The port of that server"))
    fingerprint: str = field(metadata=option("The SHA-256 fingerprint of the certificate to accept, as shown by certificate.inspect"))


def account_trust_certificate(engine: Engine, parameters: TrustParameters) -> AccountSummary:
    """Accept one server certificate for an account, on the person's explicit say-so. Only that exact certificate is trusted."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    (new,) = _pins((f"{parameters.host}:{parameters.port}={parameters.fingerprint}",))
    if new in settings.trusted_certificates:
        return _summary(settings)
    account_id = settings.account.account_id

    def trust(fresh: AppConfig) -> AppConfig:
        current = fresh.find(account_id)
        if new in current.trusted_certificates:
            return fresh
        return fresh.with_account(replace(current, trusted_certificates=(*current.trusted_certificates, new)))

    config = engine.change_config(trust)
    return _summary(config.find(account_id))


def account_forget_certificates(engine: Engine, parameters: AccountParameters) -> AccountSummary:
    """Take back every accepted certificate of an account: the usual checks apply again."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    account_id = settings.account.account_id
    config = engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(account_id), trusted_certificates=())))
    return _summary(config.find(account_id))


CERTIFICATE_INSPECT: TypedCommand[CertificateParameters, CertificateResult] = TypedCommand("certificate.inspect", CertificateParameters, CertificateResult)
ACCOUNT_TRUST_CERTIFICATE: TypedCommand[TrustParameters, AccountSummary] = TypedCommand("account.trust-certificate", TrustParameters, AccountSummary)
ACCOUNT_FORGET_CERTIFICATES: TypedCommand[AccountParameters, AccountSummary] = TypedCommand("account.forget-certificates", AccountParameters, AccountSummary)
ACCOUNT_ADD: TypedCommand[AccountAddParameters, AccountAddResult] = TypedCommand("account.add", AccountAddParameters, AccountAddResult)
ACCOUNT_CHECK: TypedCommand[AccountCheckParameters, AccountCheckResult] = TypedCommand("account.check", AccountCheckParameters, AccountCheckResult)
ACCOUNT_EDIT: TypedCommand[AccountEditParameters, AccountSummary] = TypedCommand("account.edit", AccountEditParameters, AccountSummary)
ACCOUNT_LIST: TypedCommand[NoParameters, tuple[AccountSummary, ...]] = TypedCommand("account.list", NoParameters, tuple)  # every element is a AccountSummary
ACCOUNT_REMOVE: TypedCommand[AccountParameters, AccountRemoveResult] = TypedCommand("account.remove", AccountParameters, AccountRemoveResult)
KEY_STATUS: TypedCommand[NoParameters, KeyStatus] = TypedCommand("key.status", NoParameters, KeyStatus)
KEY_EXPORT_FILE: TypedCommand[KeyExportParameters, KeyExportResult] = TypedCommand("key.export-file", KeyExportParameters, KeyExportResult)
KEY_RECOVERY_CODE: TypedCommand[NoParameters, RecoveryCode] = TypedCommand("key.recovery-code", NoParameters, RecoveryCode)
KEY_RESTORE_FILE: TypedCommand[KeyRestoreFileParameters, KeyStatus] = TypedCommand("key.restore-file", KeyRestoreFileParameters, KeyStatus)
KEY_RESTORE_CODE: TypedCommand[KeyRestoreCodeParameters, KeyStatus] = TypedCommand("key.restore-code", KeyRestoreCodeParameters, KeyStatus)


def register(registry: CommandRegistry) -> None:
    registry.register(CERTIFICATE_INSPECT.id, "Look at the certificate of a server (nothing is accepted by looking)", CertificateParameters, certificate_inspect)
    registry.register(ACCOUNT_TRUST_CERTIFICATE.id, "Accept a server certificate for an account (by its fingerprint)", TrustParameters, account_trust_certificate)
    registry.register(ACCOUNT_FORGET_CERTIFICATES.id, "Forget the accepted certificates of an account", AccountParameters, account_forget_certificates)
    registry.register(ACCOUNT_ADD.id, "Add a POP3/SMTP account and create its (encrypted) message store", AccountAddParameters, account_add)
    registry.register(ACCOUNT_CHECK.id, "Test the connection and login to both servers before creating an account", AccountCheckParameters, account_check)
    registry.register(ACCOUNT_EDIT.id, "Change the name, address, servers, passwords, signature or server-copy policy of an account", AccountEditParameters, account_edit)
    registry.register(ACCOUNT_LIST.id, "List the accounts", NoParameters, account_list)
    registry.register(ACCOUNT_REMOVE.id, "Forget an account and its passwords (the mail on disk is kept)", AccountParameters, account_remove, destructive=True)
    registry.register("account.policy", "Set what happens to messages on the POP3 server after download", AccountPolicyParameters, account_policy)
    registry.register(KEY_STATUS.id, "Show the data key id and whether the credential store has it", NoParameters, key_status)
    registry.register(KEY_EXPORT_FILE.id, "Write the data key, protected by a passphrase, to a recovery file", KeyExportParameters, key_export_file)
    registry.register(KEY_RECOVERY_CODE.id, "Show the data key as a printable recovery code", NoParameters, key_recovery_code)
    registry.register(KEY_RESTORE_FILE.id, "Put the data key back into the credential store from a recovery file", KeyRestoreFileParameters, key_restore_file)
    registry.register(KEY_RESTORE_CODE.id, "Put the data key back into the credential store from a recovery code", KeyRestoreCodeParameters, key_restore_code)
