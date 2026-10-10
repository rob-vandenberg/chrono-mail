import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from chrono_mail.config import (
    AccountSettings,
    Pop3DeletePolicy,
    Pop3Options,
    ServerRole,
    new_account_id,
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
    DataKey,
    generate_data_key,
    key_from_recovery_code,
    recovery_code_for,
    seal_key_with_passphrase,
    unseal_key_with_passphrase,
)
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import STANDARD_FOLDERS, Engine
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from chrono_mail.protocols.tls import context_for, inspect_certificate, valid_fingerprint
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from chrono_mail.protocols.smtp import SmtpLimits, check_login
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "commands_accounts.py 0.0.45"

# --- Version history ----------------------------------------------------
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
    display_name: str = field(metadata=option("The name shown for the account"))
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
    if any(item.account.email_address.lower() == email.lower() for item in config.accounts):
        raise ConfigurationError("An account with this e-mail address already exists")
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
        trusted_certificates=_fingerprints(parameters.trusted_certificate), storage=parameters.storage,
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
        engine.save_config(new_config.with_account(settings))  # the commit point: until here nothing refers to the account
    except ChronoMailError:
        engine.passwords.delete(account_id, ServerRole.INCOMING)
        engine.passwords.delete(account_id, ServerRole.OUTGOING)
        shutil.rmtree(engine.directories.account_directory(account_id), ignore_errors=True)
        raise
    return AccountAddResult(account_id, key_created or local_key_created, tuple("/".join(path) for path, _ in STANDARD_FOLDERS))


@dataclass(frozen=True, slots=True)
class AccountCheckParameters:
    username: str = field(metadata=option("The user name for the incoming server (and the outgoing server unless given separately)"))
    password: str = field(metadata=option("The password", secret=True))
    incoming_host: str = field(metadata=option("The POP3 server"))
    outgoing_host: str = field(metadata=option("The SMTP server"))
    incoming_port: int = field(default=995, metadata=option("The POP3 port"))
    incoming_security: Security = field(default="tls", metadata=option("tls, starttls or none"))
    outgoing_port: int = field(default=587, metadata=option("The SMTP port"))
    outgoing_security: Security = field(default="starttls", metadata=option("tls, starttls or none"))
    outgoing_username: str | None = field(default=None, metadata=option("The SMTP user name, when it differs from the incoming one"))
    outgoing_password: str | None = field(default=None, metadata=option("The SMTP password, when it differs from the incoming one", secret=True))
    kind: Kind = field(default="pop3", metadata=option("pop3 or imap"))
    trusted_certificate: tuple[str, ...] = field(default=(), metadata=option("The SHA-256 fingerprint of a server certificate to accept (repeat for more)"))


@dataclass(frozen=True, slots=True)
class AccountCheckResult:
    incoming_ok: bool
    incoming_message: str
    outgoing_ok: bool
    outgoing_message: str


def account_check(engine: Engine, parameters: AccountCheckParameters) -> AccountCheckResult:
    """Try to connect and log in to both servers, before an account is created. Nothing is stored or sent."""
    context = context_for(frozenset(_fingerprints(parameters.trusted_certificate)))
    try:
        incoming = ServerSettings(parameters.incoming_host.strip(), parameters.incoming_port, _security(parameters.incoming_security), parameters.username)
        if parameters.kind == "imap":
            ImapClient(incoming, parameters.password, ImapLimits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        else:
            Pop3Client(incoming, parameters.password, Pop3Limits(connect_timeout_seconds=15, read_timeout_seconds=20), context).connect()
        incoming_ok, incoming_message = True, "Connected and logged in"
    except ChronoMailError as error:
        incoming_ok, incoming_message = False, str(error)
    try:
        outgoing = ServerSettings(parameters.outgoing_host.strip(), parameters.outgoing_port, _security(parameters.outgoing_security), parameters.outgoing_username or parameters.username)
        check_login(outgoing, parameters.outgoing_password or parameters.password, SmtpLimits(timeout_seconds=20), context)
        outgoing_ok, outgoing_message = True, "Connected and logged in"
    except ChronoMailError as error:
        outgoing_ok, outgoing_message = False, str(error)
    return AccountCheckResult(incoming_ok, incoming_message, outgoing_ok, outgoing_message)


@dataclass(frozen=True, slots=True)
class NoParameters:
    pass


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


def _summary(item: AccountSettings) -> AccountSummary:
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
    )


def account_list(engine: Engine, parameters: NoParameters) -> tuple[AccountSummary, ...]:
    return tuple(_summary(item) for item in engine.config().accounts)


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
    if settings.account.kind is AccountKind.LOCAL and any(item.storage == "local" for item in config.accounts):
        raise UsageError("Accounts still keep their mail in Local Folders: give them folders of their own first")
    engine.save_config(config.without_account(account_id))
    engine.passwords.delete(account_id, ServerRole.INCOMING)
    engine.passwords.delete(account_id, ServerRole.OUTGOING)
    return AccountRemoveResult(account_id, str(engine.directories.account_directory(account_id)))


@dataclass(frozen=True, slots=True)
class AccountPolicyParameters:
    account: str = field(metadata=option("The account"))
    policy: Policy = field(metadata=option("What happens to a message on the server after it was downloaded and verified"))
    days: int = field(default=30, metadata=option("Days before deleting, for delete_after_days"))


def account_policy(engine: Engine, parameters: AccountPolicyParameters) -> AccountSummary:
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    changed = replace(settings, pop3=Pop3Options(Pop3DeletePolicy(parameters.policy), parameters.days))
    engine.save_config(config.with_account(changed))
    return _summary(changed)


@dataclass(frozen=True, slots=True)
class AccountEditParameters:
    account: str = field(metadata=option("The account"))
    display_name: str | None = field(default=None, metadata=option("The name shown for the account"))
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


def account_edit(engine: Engine, parameters: AccountEditParameters) -> AccountSummary:
    """Change the settings of an account. What is not given stays as it was. The stored mail is not touched."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    account = settings.account
    email = account.email_address if parameters.email_address is None else parameters.email_address.strip()
    if email.count("@") != 1 or email.startswith("@") or email.endswith("@"):
        raise UsageError("The e-mail address is not valid")
    if any(item.account.account_id != account.account_id and item.account.email_address.lower() == email.lower() for item in config.accounts):
        raise ConfigurationError("Another account has this e-mail address already")
    name = account.display_name if parameters.display_name is None else (parameters.display_name.strip() or email)
    incoming = ServerSettings(
        account.incoming.host if parameters.incoming_host is None else parameters.incoming_host.strip(),
        account.incoming.port if parameters.incoming_port is None else parameters.incoming_port,
        account.incoming.security if parameters.incoming_security is None else _security(parameters.incoming_security),
        account.incoming.username if parameters.username is None else parameters.username.strip(),
    )
    outgoing = ServerSettings(
        account.outgoing.host if parameters.outgoing_host is None else parameters.outgoing_host.strip(),
        account.outgoing.port if parameters.outgoing_port is None else parameters.outgoing_port,
        account.outgoing.security if parameters.outgoing_security is None else _security(parameters.outgoing_security),
        account.outgoing.username if parameters.outgoing_username is None else parameters.outgoing_username.strip(),
    )
    if not incoming.username or not outgoing.username:
        raise UsageError("A user name must not be empty")
    pop3 = Pop3Options(
        settings.pop3.policy if parameters.delete_policy is None else Pop3DeletePolicy(parameters.delete_policy),
        settings.pop3.days if parameters.delete_days is None else parameters.delete_days,
    )
    changed = replace(
        settings,
        account=replace(account, display_name=name, email_address=email, incoming=incoming, outgoing=outgoing),
        pop3=pop3,
        signature=settings.signature if parameters.signature is None else parameters.signature.replace("\r\n", "\n").rstrip(),
        check_minutes=settings.check_minutes if parameters.check_minutes is None else parameters.check_minutes,
    )
    # Passwords first: a new password that cannot be stored must not leave settings behind that expect it
    if parameters.password:
        engine.passwords.set(account.account_id, ServerRole.INCOMING, parameters.password)
    if parameters.outgoing_password:
        engine.passwords.set(account.account_id, ServerRole.OUTGOING, parameters.outgoing_password)
    engine.save_config(config.with_account(changed))
    return _summary(changed)


# --- keys -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeyStatus:
    key_id: str | None
    in_credential_store: bool


def key_status(engine: Engine, parameters: NoParameters) -> KeyStatus:
    config = engine.config()
    if config.key_id is None:
        return KeyStatus(None, False)
    try:
        engine.keys.load(bytes.fromhex(config.key_id))
    except CryptoError:
        return KeyStatus(config.key_id, False)
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
    kind: Literal["pop3", "imap", "smtp"] = field(default="imap", metadata=option("Which kind of server it is: pop3, imap or smtp"))


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
    info = inspect_certificate(parameters.host.strip(), parameters.port, _security(parameters.security), parameters.kind)
    return CertificateResult(info.fingerprint, info.subject, info.issuer, info.names, info.not_before, info.not_after, info.expired, info.verified, info.problem)


@dataclass(frozen=True, slots=True)
class TrustParameters:
    account: str = field(metadata=option("The account"))
    fingerprint: str = field(metadata=option("The SHA-256 fingerprint of the certificate to accept, as shown by certificate.inspect"))


def account_trust_certificate(engine: Engine, parameters: TrustParameters) -> AccountSummary:
    """Accept one server certificate for an account, on the person's explicit say-so. Only that exact certificate is trusted."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    (new,) = _fingerprints((parameters.fingerprint,))
    if new in settings.trusted_certificates:
        return _summary(settings)
    changed = replace(settings, trusted_certificates=(*settings.trusted_certificates, new))
    engine.save_config(config.with_account(changed))
    return _summary(changed)


def account_forget_certificates(engine: Engine, parameters: AccountParameters) -> AccountSummary:
    """Take back every accepted certificate of an account: the usual checks apply again."""
    config = engine.config()
    settings = engine.resolve_account(parameters.account, config)
    changed = replace(settings, trusted_certificates=())
    engine.save_config(config.with_account(changed))
    return _summary(changed)


def register(registry: CommandRegistry) -> None:
    registry.register("certificate.inspect", "Look at the certificate of a server (nothing is accepted by looking)", CertificateParameters, certificate_inspect)
    registry.register("account.trust-certificate", "Accept a server certificate for an account (by its fingerprint)", TrustParameters, account_trust_certificate)
    registry.register("account.forget-certificates", "Forget the accepted certificates of an account", AccountParameters, account_forget_certificates)
    registry.register("account.add", "Add a POP3/SMTP account and create its (encrypted) message store", AccountAddParameters, account_add)
    registry.register("account.check", "Test the connection and login to both servers before creating an account", AccountCheckParameters, account_check)
    registry.register("account.edit", "Change the name, address, servers, passwords, signature or server-copy policy of an account", AccountEditParameters, account_edit)
    registry.register("account.list", "List the accounts", NoParameters, account_list)
    registry.register("account.remove", "Forget an account and its passwords (the mail on disk is kept)", AccountParameters, account_remove, destructive=True)
    registry.register("account.policy", "Set what happens to messages on the POP3 server after download", AccountPolicyParameters, account_policy)
    registry.register("key.status", "Show the data key id and whether the credential store has it", NoParameters, key_status)
    registry.register("key.export-file", "Write the data key, protected by a passphrase, to a recovery file", KeyExportParameters, key_export_file)
    registry.register("key.recovery-code", "Show the data key as a printable recovery code", NoParameters, key_recovery_code)
    registry.register("key.restore-file", "Put the data key back into the credential store from a recovery file", KeyRestoreFileParameters, key_restore_file)
    registry.register("key.restore-code", "Put the data key back into the credential store from a recovery code", KeyRestoreCodeParameters, key_restore_code)
