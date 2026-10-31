import os
import shutil
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime
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
    LOCAL_ACCOUNT_ID,
    Account,
    AccountKind,
    CertificateError,
    CertificateTrouble,
    ChronoMailError,
    ConfigurationError,
    ConnectionSecurity,
    CryptoError,
    ServerSettings,
    StorageError,
)
from chrono_mail.crypto import (
    CredentialStoreError,
    DataKey,
    key_from_recovery_code,
    recovery_code_for,
    seal_key_with_passphrase,
    unseal_key_with_passphrase,
)
from chrono_mail.engine.local import SETUP_LOCK, ensure_local_folders, take_back
from chrono_mail.engine.newkey import kept_local_key, kept_other_key, make_data_key, recovery_code_not_stored, recovery_code_stored, usable
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine, let_go_kept_indexes
from chrono_mail.engine.walletlabels import label_account, label_key, label_wallet
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from chrono_mail.protocols.tls import context_for, inspect_certificate, valid_fingerprint
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.protocols.smtp import SmtpLimits, check_login
from chrono_mail.store import Store
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "commands_accounts.py 4.2.451"

# --- Version history ----------------------------------------------------
# v4.2.451: in_send_receive, the tick "Include this account in Send and Receive", replaces check_minutes, the per-account interval (D-059)
# v4.2.447: account.check fresh (a test the person starts: the usual checks only); certificate.inspect tells whether the certificate is kept
#           with an account (kept); account.forget-certificate takes one back (D-299)
# v4.2.441: account.check names a server whose certificate could not be verified (incoming_certificate, outgoing_certificate) and takes the
#           certificates accepted until Chrono Mail closes; certificate.trust-for-run; security tls-unchecked, the weak choice (D-299)
# v3.6.371: the passwords and the key get readable labels in the wallet when stored or the account is renamed; wallet.label at start (B-23, D-275)
# v3.6.353: the kept index connections are let go before the mail kept on this computer is set aside (D-266)
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
# v3.6.350: key.status names the key of Local Folders kept here when the settings name none (kept), so the start asks for it;
#           key.start-without-kept-mail sets that mail aside (never deleted) when its key is gone for good, so a new key can be made
# v3.6.349: a recovery code or file brings back the key of Local Folders kept on this computer when the settings are gone (RV-177)
# v3.5.346: key.code-stored: the person confirmed the recovery code is stored; account.add says whether the code still has to be shown
#           (the key is made in one place, which remembers it until then; RV-03)
# v3.0.340: Local Folders can never be removed, only hidden (the owner, D-255)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
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

Security = Literal["tls", "starttls", "none", "tls-unchecked"]  # tls-unchecked: encrypted, the certificate not checked (weak; the person's own choice, D-299)
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
    show_recovery_code: bool = False  # the recovery code of this computer's key was never confirmed as stored: the window shows it (RV-03)


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
            if not host.strip() or not digits(port) or not 1 <= int(port) <= 65535:
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
    if parameters.encrypted:
        with SETUP_LOCK:  # one key for this computer: another command making one at the same moment waits and then uses this one (RV-70)
            current = engine.config()
            if current.key_id is None:
                made = make_data_key(engine)  # never an encrypted store whose key cannot be read back
                noted = engine.change_config(lambda fresh: fresh.with_key_id(made.key_id.hex()) if fresh.key_id is None else fresh)
                key, key_created = (made, True) if noted.key_id == made.key_id.hex() else (engine.data_key(noted), False)  # another key came first
            else:
                key = engine.data_key(current)
    store_directory = engine.directories.store_directory(account_id)
    engine.passwords.set(account_id, ServerRole.INCOMING, parameters.password)
    engine.passwords.set(account_id, ServerRole.OUTGOING, parameters.outgoing_password or parameters.password)
    try:
        if parameters.storage == "own":
            store = Store.create(store_directory, account_id, key, durable=engine.durable)
            for path, role in STANDARD_FOLDERS:
                store.create_folder(path, role)

        def add(fresh: AppConfig) -> AppConfig:
            _refuse_same_account(fresh.accounts, settings.account.kind, settings.account.incoming.username, settings.account.incoming.host, None)  # added meanwhile?
            return fresh.with_account(settings)

        engine.change_config(add)  # the commit point: until here nothing refers to the account
    except ChronoMailError:
        engine.passwords.delete(account_id, ServerRole.INCOMING)
        engine.passwords.delete(account_id, ServerRole.OUTGOING)
        shutil.rmtree(engine.directories.account_directory(account_id), ignore_errors=True)
        raise
    label_account(engine, account_id)  # its passwords named in the wallet as the account is (B-23)
    return AccountAddResult(account_id, key_created or local_key_created, tuple("/".join(path) for path, _ in STANDARD_FOLDERS), recovery_code_not_stored(engine))


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
    fresh: bool = field(default=False, metadata=option("Check the certificates with the usual checks only (a test the person starts): none accepted before counts"))


@dataclass(frozen=True, slots=True)
class AccountCheckResult:
    incoming_ok: bool
    incoming_message: str
    outgoing_ok: bool
    outgoing_message: str
    incoming_certificate: CertificateTrouble | None = None  # the incoming server's certificate could not be verified: the window asks (D-299)
    outgoing_certificate: CertificateTrouble | None = None


def account_check(engine: Engine, parameters: AccountCheckParameters) -> AccountCheckResult:
    """Try to connect and log in to both servers, before an account is created or with the changes of one being edited. Nothing is stored or
    sent. For an account that exists a password not typed is the stored one: a password typed once is never asked for again (R-05)."""
    pins = () if parameters.fresh else engine.pins(_pins(parameters.trusted_certificate))  # also those accepted until closing; a fresh test: none (D-299)
    password, outgoing_password = parameters.password, parameters.outgoing_password
    account_id = ""
    if parameters.account:
        account_id = engine.resolve_account(parameters.account).account.account_id
        password = password or engine.passwords.get(account_id, ServerRole.INCOMING) or ""
        if not outgoing_password:  # the same login: the incoming password (a new one included); else the stored outgoing one
            outgoing_password = (engine.passwords.get(account_id, ServerRole.OUTGOING) or password) if parameters.outgoing_username is not None else password
    if not password:
        raise ConfigurationError("No password was typed, and none is stored for this account")
    try:
        incoming = ServerSettings(parameters.incoming_host.strip(), parameters.incoming_port, _security(parameters.incoming_security), parameters.username)
        context = context_for(fingerprints_for(pins, incoming.host, incoming.port), incoming.security, parameters.kind)
        if parameters.kind == "imap":
            ImapClient(incoming, password, ImapLimits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        else:
            Pop3Client(incoming, password, Pop3Limits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        incoming_ok, incoming_message, incoming_certificate = True, "Connected and logged in", None
    except CertificateError as error:
        incoming_ok, incoming_message, incoming_certificate = False, str(error), replace(error.trouble, account=account_id)
    except ChronoMailError as error:
        incoming_ok, incoming_message, incoming_certificate = False, str(error), None
    try:
        outgoing = ServerSettings(parameters.outgoing_host.strip(), parameters.outgoing_port, _security(parameters.outgoing_security), parameters.outgoing_username or parameters.username)
        check_login(outgoing, outgoing_password or password, SmtpLimits(timeout_seconds=20), context_for(fingerprints_for(pins, outgoing.host, outgoing.port), outgoing.security, "smtp"))
        outgoing_ok, outgoing_message, outgoing_certificate = True, "Connected and logged in", None
    except CertificateError as error:
        outgoing_ok, outgoing_message, outgoing_certificate = False, str(error), replace(error.trouble, account=account_id)
    except ChronoMailError as error:
        outgoing_ok, outgoing_message, outgoing_certificate = False, str(error), None
    return AccountCheckResult(incoming_ok, incoming_message, outgoing_ok, outgoing_message, incoming_certificate, outgoing_certificate)


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
    in_send_receive: bool = True  # unticked: left out of every refresh but its own (D-059)
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
        item.in_send_receive,
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
    if settings.account.kind is AccountKind.LOCAL:  # the mail, calendars, contacts and tasks of this computer: it can be hidden, never removed (D-255)
        raise UsageError("Local Folders cannot be removed. It can be hidden in Settings > Sidebar.")

    def remove(fresh: AppConfig) -> AppConfig:
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
    in_send_receive: bool | None = field(default=None, metadata=option("Include this account in Send and Receive (off: only its own refresh reaches it)"))
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
    in_send_receive = settings.in_send_receive if parameters.in_send_receive is None else parameters.in_send_receive
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
            in_send_receive=in_send_receive,
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
    label_account(engine, account.account_id)  # a new name or address, or a password stored again: its labels follow (B-23)
    return _summary(config.find(account.account_id))


# --- keys -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeyStatus:
    key_id: str | None
    in_credential_store: bool
    problem: str = ""  # why the key could not be had, in the credential store's own words ("" when it is there)
    store_answered: bool = True  # False: the store could not be asked at all (locked, not running yet); the key may well be there
    kept: bool = False  # the key of Local Folders' mail kept on this computer while the settings name none (they were lost or reset)
    kept_other: bool = False  # Local Folders' mail kept here was written with an older key of this computer that the store no longer has
    take_back: bool = False  # ... with an older key the store has: it is taken back (written again with this computer's key, local.take-back)


def key_status(engine: Engine, parameters: NoParameters) -> KeyStatus:
    config = engine.config()
    key_id = config.key_id
    kept = False
    older = kept_other_key(engine)
    try:
        other = older is not None and not usable(engine, older)
        back = older is not None and not other  # an older key the store has: Local Folders is taken back with it (local.take-back)
    except CredentialStoreError:
        other = back = False  # the store cannot be asked now: nothing is said about the older key
    if key_id is None:
        key_id = kept_local_key(engine)  # the settings were lost or reset, but Local Folders' mail is still here: its key is this computer's
        if key_id is None:
            return KeyStatus(None, False)
        kept = True
    try:
        engine.keys.load(bytes.fromhex(key_id))
    except CredentialStoreError as error:
        return KeyStatus(key_id, False, str(error), store_answered=False, kept=kept, kept_other=other)
    except (CryptoError, ValueError) as error:  # not there, or there but damaged: the store did answer
        return KeyStatus(key_id, False, str(error), kept=kept, kept_other=other)
    return KeyStatus(key_id, True, kept=kept, kept_other=other, take_back=back)


def local_take_back(engine: Engine, parameters: NoParameters) -> KeyStatus:
    """Local Folders kept on this computer, written with an older key of this computer that the password store has: written again with this
    computer's key and taken into use; the old files are kept aside, never deleted. Files that could not be read are said in problem."""
    said = take_back(engine)
    return replace(key_status(engine, parameters), problem=said)


@dataclass(frozen=True, slots=True)
class KeptSetAside:
    folder: str  # where the mail kept on this computer now is (never deleted)


def key_start_without_kept_mail(engine: Engine, parameters: NoParameters) -> KeptSetAside:
    """Start without Local Folders' mail, calendars, contacts and tasks kept on this computer that cannot be opened: their key is gone for
    good (no recovery code or file), whether the settings name no key or an older version made another key for this computer. They are set
    aside in a folder of their own, never deleted, so Local Folders (and a key) can be made again. Refused while their key is in the password
    store, or the store cannot be asked (a locked wallet is no lost key)."""
    with SETUP_LOCK:
        kept, older = kept_local_key(engine), kept_other_key(engine)
        if kept is None and older is None:
            raise UsageError("No mail kept on this computer waits for its key (Local Folders is in use): nothing was set aside")
        if kept is not None and usable(engine, kept):  # its key is here: it opens as it is (an older key it was written with may still fail
            # to be taken back, for want of room: then it may be set aside)
            raise UsageError("The key of the mail kept on this computer is in the password store: it is taken back, nothing was set aside")
        source = engine.directories.account_directory(LOCAL_ACCOUNT_ID)
        target = engine.directories.data_directory / f"set-aside-local-folders-{datetime.now():%Y%m%d-%H%M%S}"
        let_go_kept_indexes()  # the kept index connections are closed first: Windows refuses to move an open file (D-266)
        try:
            source.rename(target)
        except OSError as error:
            raise StorageError(f"The mail kept on this computer could not be set aside ({error.strerror or error}); nothing was changed") from None
        return KeptSetAside(str(target))


@dataclass(frozen=True, slots=True)
class KeyExportParameters:
    path: str = field(metadata=option("Where to write the recovery file"))
    passphrase: str = field(metadata=option("The passphrase that protects the recovery file", secret=True))
    overwrite: bool = field(default=False, metadata=option("Replace an existing file"))


@dataclass(frozen=True, slots=True)
class KeyExportResult:
    key_id: str
    path: str


def _this_computers_key(engine: Engine) -> DataKey:
    """The data key: the one the settings name, or, when they name none (they were lost or reset), the one the mail kept here was written
    with (the review of 3.6.350: its code could not be shown)."""
    config = engine.config()
    kept = kept_local_key(engine) if config.key_id is None else None
    return engine.data_key(config.with_key_id(kept) if kept is not None else config)


def key_export_file(engine: Engine, parameters: KeyExportParameters) -> KeyExportResult:
    """Write the data key, sealed with a passphrase, to a file. Keep it somewhere other than this computer."""
    key = _this_computers_key(engine)
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
    key = _this_computers_key(engine)
    return RecoveryCode(key.key_id.hex(), recovery_code_for(key))


def key_code_stored(engine: Engine, parameters: NoParameters) -> KeyStatus:
    """The person confirmed that the recovery code (or a recovery file) of this computer's key is stored in a safe place."""
    recovery_code_stored(engine)
    return key_status(engine, parameters)


@dataclass(frozen=True, slots=True)
class KeyRestoreFileParameters:
    path: str = field(metadata=option("The recovery file"))
    passphrase: str = field(metadata=option("The passphrase of the recovery file", secret=True))


@dataclass(frozen=True, slots=True)
class KeyRestoreCodeParameters:
    code: str = field(metadata=option("The recovery code", secret=True))


def _restore(engine: Engine, key: DataKey) -> KeyStatus:
    config = engine.config()
    wanted = config.key_id if config.key_id is not None else kept_local_key(engine)  # settings lost: the key the kept mail was written with
    if config.key_id is not None and key.key_id.hex() == kept_other_key(engine):  # the older key the kept Local Folders was written with
        engine.keys.save(key)  # only stored here: taking Local Folders back is its own step (local.take-back), which the key window runs next
        label_key(engine, key.key_id)
        if engine.keys.load(key.key_id) != key:
            raise CryptoError("The credential store did not return the key that was just saved")
        return key_status(engine, NoParameters())
    if wanted is None:
        raise ConfigurationError("No account uses a data key yet")
    if key.key_id.hex() != wanted:
        raise CryptoError("This is not the key of this installation's accounts")
    engine.keys.save(key)
    label_key(engine, key.key_id)
    if engine.keys.load(key.key_id) != key:
        raise CryptoError("The credential store did not return the key that was just saved")
    if config.key_id is None:  # the kept Local Folders can be taken into use again with it (RV-177); one key at a time (SETUP_LOCK)
        with SETUP_LOCK:
            if engine.change_config(lambda fresh: fresh.with_key_id(wanted) if fresh.key_id is None else fresh).key_id != wanted:
                raise CryptoError("Another key was made on this computer meanwhile; nothing was changed")
            ensure_local_folders(engine)  # and is shown at once, with its mail
    return key_status(engine, NoParameters())  # also says when Local Folders still waits for an older key (the review of 3.6.350)


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
    account: str = field(default="", metadata=option("The account to tell whether this certificate is kept with it for good (kept)"))


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
    accepted_for_run: bool = False  # the person accepted exactly this certificate meanwhile (until Chrono Mail closes, or for good): nothing to ask
    kept: bool = False  # kept for good with the account named (Don't ask me again): the window's tick starts ticked (D-299)


def certificate_inspect(engine: Engine, parameters: CertificateParameters) -> CertificateResult:
    """Look at the certificate a server presents, without logging in. Nothing is trusted by looking."""
    if parameters.kind == "https" and parameters.security != "tls":
        raise UsageError("A calendar and contacts server (https) is reached with tls only")
    host = parameters.host.strip()
    info = inspect_certificate(host, parameters.port, _security(parameters.security), parameters.kind)
    accepted = info.fingerprint in fingerprints_for(engine.pins(), host, parameters.port)
    kept = False
    if parameters.account:
        try:
            kept = info.fingerprint in fingerprints_for(engine.resolve_account(parameters.account).trusted_certificates, host, parameters.port)
        except ConfigurationError:
            kept = False  # the account is gone meanwhile: nothing is kept with it
    return CertificateResult(
        info.fingerprint, info.subject, info.issuer, info.names, info.not_before, info.not_after, info.expired, info.verified, info.problem, accepted, kept,
    )


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


@dataclass(frozen=True, slots=True)
class RunTrustParameters:
    host: str = field(metadata=option("The server the certificate belongs to"))
    port: int = field(metadata=option("The port of that server"))
    fingerprint: str = field(metadata=option("The SHA-256 fingerprint of the certificate to accept, as shown by certificate.inspect"))


@dataclass(frozen=True, slots=True)
class RunTrustResult:
    pin: str  # host:port=fingerprint, accepted until Chrono Mail closes


def certificate_trust_for_run(engine: Engine, parameters: RunTrustParameters) -> RunTrustResult:
    """Accept one server certificate until Chrono Mail closes, on the person's explicit say-so (Connect, D-299). Kept in memory only, never
    saved: the next start asks again. Only that exact certificate is trusted."""
    (pin,) = _pins((f"{parameters.host.strip()}:{parameters.port}={parameters.fingerprint}",))
    engine.trust_for_run(pin)
    return RunTrustResult(pin)


def account_forget_certificate(engine: Engine, parameters: TrustParameters) -> AccountSummary:
    """Take back one accepted certificate of an account (Connect without Don't ask me again, D-299): the usual checks apply to it again
    after Chrono Mail closes. Nothing else is changed."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    (gone,) = _pins((f"{parameters.host}:{parameters.port}={parameters.fingerprint}",))
    account_id = settings.account.account_id
    config = engine.change_config(lambda fresh: fresh.with_account(replace(
        fresh.find(account_id), trusted_certificates=tuple(pin for pin in fresh.find(account_id).trusted_certificates if pin != gone),
    )))
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
ACCOUNT_FORGET_CERTIFICATE: TypedCommand[TrustParameters, AccountSummary] = TypedCommand("account.forget-certificate", TrustParameters, AccountSummary)
CERTIFICATE_TRUST_FOR_RUN: TypedCommand[RunTrustParameters, RunTrustResult] = TypedCommand("certificate.trust-for-run", RunTrustParameters, RunTrustResult)
ACCOUNT_ADD: TypedCommand[AccountAddParameters, AccountAddResult] = TypedCommand("account.add", AccountAddParameters, AccountAddResult)
ACCOUNT_CHECK: TypedCommand[AccountCheckParameters, AccountCheckResult] = TypedCommand("account.check", AccountCheckParameters, AccountCheckResult)
ACCOUNT_EDIT: TypedCommand[AccountEditParameters, AccountSummary] = TypedCommand("account.edit", AccountEditParameters, AccountSummary)
ACCOUNT_LIST: TypedCommand[NoParameters, tuple[AccountSummary, ...]] = TypedCommand("account.list", NoParameters, tuple)  # every element is a AccountSummary
ACCOUNT_REMOVE: TypedCommand[AccountParameters, AccountRemoveResult] = TypedCommand("account.remove", AccountParameters, AccountRemoveResult)
KEY_STATUS: TypedCommand[NoParameters, KeyStatus] = TypedCommand("key.status", NoParameters, KeyStatus)
KEY_EXPORT_FILE: TypedCommand[KeyExportParameters, KeyExportResult] = TypedCommand("key.export-file", KeyExportParameters, KeyExportResult)
KEY_RECOVERY_CODE: TypedCommand[NoParameters, RecoveryCode] = TypedCommand("key.recovery-code", NoParameters, RecoveryCode)
KEY_CODE_STORED: TypedCommand[NoParameters, KeyStatus] = TypedCommand("key.code-stored", NoParameters, KeyStatus)
KEY_RESTORE_FILE: TypedCommand[KeyRestoreFileParameters, KeyStatus] = TypedCommand("key.restore-file", KeyRestoreFileParameters, KeyStatus)
KEY_RESTORE_CODE: TypedCommand[KeyRestoreCodeParameters, KeyStatus] = TypedCommand("key.restore-code", KeyRestoreCodeParameters, KeyStatus)
KEY_START_WITHOUT_KEPT: TypedCommand[NoParameters, KeptSetAside] = TypedCommand("key.start-without-kept-mail", NoParameters, KeptSetAside)
LOCAL_TAKE_BACK: TypedCommand[NoParameters, KeyStatus] = TypedCommand("local.take-back", NoParameters, KeyStatus)


@dataclass(frozen=True, slots=True)
class WalletLabelResult:
    accounts: int  # how many accounts' entries were looked at


def wallet_label(engine: Engine, parameters: NoParameters) -> WalletLabelResult:
    """At start: Chrono Mail's entries in the wallet get labels a person can read (B-23, D-275); only the labels change."""
    older = kept_other_key(engine)  # the older key Local Folders' kept mail was written with (read from a file: may be no key id at all)
    try:
        other_keys = (bytes.fromhex(older),) if older is not None else ()
    except ValueError:
        other_keys = ()
    return WalletLabelResult(label_wallet(engine, other_keys))


WALLET_LABEL: TypedCommand[NoParameters, WalletLabelResult] = TypedCommand("wallet.label", NoParameters, WalletLabelResult)


def register(registry: CommandRegistry) -> None:
    registry.register(WALLET_LABEL.id, "Give Chrono Mail's passwords and key a readable name in the wallet (only the names change)", NoParameters, wallet_label, doing="Naming Chrono Mail's passwords in your wallet")
    registry.register(CERTIFICATE_INSPECT.id, "Look at the certificate of a server (nothing is accepted by looking)", CertificateParameters, certificate_inspect, doing="Reading the server's certificate")
    registry.register(ACCOUNT_TRUST_CERTIFICATE.id, "Accept a server certificate for an account (by its fingerprint)", TrustParameters, account_trust_certificate, doing="Accepting a certificate")
    registry.register(ACCOUNT_FORGET_CERTIFICATE.id, "Forget one accepted certificate of an account (by its fingerprint)", TrustParameters, account_forget_certificate, doing="Forgetting a certificate")
    registry.register(CERTIFICATE_TRUST_FOR_RUN.id, "Accept a server certificate until Chrono Mail closes (by its fingerprint; never saved)", RunTrustParameters, certificate_trust_for_run, doing="Accepting a certificate")
    registry.register(ACCOUNT_FORGET_CERTIFICATES.id, "Forget the accepted certificates of an account", AccountParameters, account_forget_certificates, doing="Forgetting the accepted certificates")
    registry.register(ACCOUNT_ADD.id, "Add a POP3/SMTP account and create its (encrypted) message store", AccountAddParameters, account_add, doing="Adding an account")
    registry.register(ACCOUNT_CHECK.id, "Test the connection and login to both servers before creating an account", AccountCheckParameters, account_check, doing="Testing the connection to the servers")
    registry.register(ACCOUNT_EDIT.id, "Change the name, address, servers, passwords, signature or server-copy policy of an account", AccountEditParameters, account_edit, doing="Saving the account's settings")
    registry.register(ACCOUNT_LIST.id, "List the accounts", NoParameters, account_list, doing="Reading the accounts")
    registry.register(ACCOUNT_REMOVE.id, "Forget an account and its passwords (the mail on disk is kept)", AccountParameters, account_remove, destructive=True, doing="Removing an account")
    registry.register("account.policy", "Set what happens to messages on the POP3 server after download", AccountPolicyParameters, account_policy, doing="Saving what happens to mail on the server")
    registry.register(KEY_STATUS.id, "Show the data key id and whether the credential store has it", NoParameters, key_status, doing="Checking the encryption key")
    registry.register(KEY_EXPORT_FILE.id, "Write the data key, protected by a passphrase, to a recovery file", KeyExportParameters, key_export_file, doing="Writing the recovery file")
    registry.register(KEY_RECOVERY_CODE.id, "Show the data key as a printable recovery code", NoParameters, key_recovery_code, doing="Reading the recovery code")
    registry.register(KEY_CODE_STORED.id, "Note that the recovery code of the data key is stored in a safe place", NoParameters, key_code_stored, doing="Noting the recovery code as stored")
    registry.register(KEY_RESTORE_FILE.id, "Put the data key back into the credential store from a recovery file", KeyRestoreFileParameters, key_restore_file, doing="Restoring the key from the recovery file")
    registry.register(KEY_RESTORE_CODE.id, "Put the data key back into the credential store from a recovery code", KeyRestoreCodeParameters, key_restore_code, doing="Restoring the key from the recovery code")
    registry.register(
        KEY_START_WITHOUT_KEPT.id, "Set aside Local Folders' mail kept here whose key is gone for good (never deleted), so a new key can be made", NoParameters,
        key_start_without_kept_mail, destructive=True, doing="Setting the mail kept on this computer aside",
    )
    registry.register(
        LOCAL_TAKE_BACK.id, "Take back Local Folders kept here with an older key of this computer (written again with this computer's key)",
        NoParameters, local_take_back, doing="Taking Local Folders back",
    )
