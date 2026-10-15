import re
from dataclasses import dataclass
from enum import Enum

from chrono_mail.core import LOCAL_ACCOUNT_ID, Account, AccountKind, ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "model.py 0.5.132"

# --- Version history ----------------------------------------------------
# v0.5.132: skip_known_mail: an imported account records mail that is already here on its first check
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.66: address and user name of the calendar and contacts server
# v0.0.45: storage of an account (own folders or Local Folders)
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.43: accepted certificates per account
# v0.0.20: signature per account
# v0.0.9: Initial configuration model

ACCOUNT_ID_PATTERN: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")  # used in directory names: nothing else may pass
MAXIMUM_DELETE_AFTER_DAYS: int = 3650
MAXIMUM_SIGNATURE_CHARACTERS: int = 4000
MAXIMUM_TRUSTED_CERTIFICATES: int = 10
_HEXADECIMAL = "0123456789abcdef"


def server_pin(host: str, port: int, fingerprint: str) -> str:
    """An accepted certificate for one server: host:port=fingerprint (host in lower case)."""
    return f"{host.strip().lower()}:{port}={fingerprint}"


def valid_pin(text: str) -> bool:
    """A bare SHA-256 fingerprint (an older entry, for every server of the account) or a server pin in exactly the form server_pin makes."""
    if "=" not in text:
        return len(text) == 64 and all(character in _HEXADECIMAL for character in text)
    server, _, fingerprint = text.rpartition("=")
    host, _, port = server.rpartition(":")
    return bool(host) and port.isdigit() and 1 <= int(port) <= 65535 and valid_pin(fingerprint) and "=" not in fingerprint and text == server_pin(host, int(port), fingerprint)


def fingerprints_for(pins: tuple[str, ...], host: str, port: int) -> frozenset[str]:
    """The fingerprints accepted for one server: the ones pinned to its host and port and the bare ones of older entries."""
    wanted = f"{host.strip().lower()}:{port}="
    return frozenset(pin.removeprefix(wanted) if pin.startswith(wanted) else pin for pin in pins if "=" not in pin or pin.startswith(wanted))
MAXIMUM_CHECK_MINUTES: int = 240


class Pop3DeletePolicy(Enum):
    """What happens to a message on the POP3 server after it has been downloaded and verified."""

    KEEP = "keep"
    DELETE_AFTER_DOWNLOAD = "delete_after_download"
    DELETE_AFTER_DAYS = "delete_after_days"
    DELETE_WHEN_DELETED_LOCALLY = "delete_when_deleted_locally"


@dataclass(frozen=True, slots=True)
class Pop3Options:
    policy: Pop3DeletePolicy = Pop3DeletePolicy.KEEP  # the safe default: the server copy is never removed
    days: int = 30  # only used with DELETE_AFTER_DAYS

    def __post_init__(self) -> None:
        if not 1 <= self.days <= MAXIMUM_DELETE_AFTER_DAYS:
            raise ConfigurationError(f"The number of days must be between 1 and {MAXIMUM_DELETE_AFTER_DAYS}")


@dataclass(frozen=True, slots=True)
class AccountSettings:
    account: Account
    pop3: Pop3Options = Pop3Options()
    encrypted: bool = True
    signature: str = ""  # plain text, added to new messages
    trusted_certificates: tuple[str, ...] = ()  # SHA-256 fingerprints of server certificates the person accepted
    check_minutes: int = 0  # how often to check this account for mail; 0 follows the general setting
    dav_url: str = ""  # the address of the CalDAV and CardDAV server of this account; empty: no calendars, tasks and contacts from a server
    dav_username: str = ""  # the user name for it; empty: the same as for incoming mail
    storage: str = "own"  # where a POP3 account's mail is kept: its own folders (own) or the shared Local Folders (local)
    skip_known_mail: bool = False  # an imported account: on its first check, mail that is already here is recorded as downloaded, not stored twice

    def __post_init__(self) -> None:
        if len(self.signature) > MAXIMUM_SIGNATURE_CHARACTERS:
            raise ConfigurationError(f"A signature may have at most {MAXIMUM_SIGNATURE_CHARACTERS} characters")
        if self.storage not in ("own", "local"):
            raise ConfigurationError("The storage of an account is 'own' (its own folders) or 'local' (Local Folders)")
        if self.storage == "local" and self.account.kind is not AccountKind.POP3:
            raise ConfigurationError("Only a POP3 account can keep its mail in Local Folders")
        if self.dav_url and not self.dav_url.startswith(("https://", "http://")):
            raise ConfigurationError("The address of the calendar and contacts server starts with https:// (or http:// for this computer)")
        if len(self.dav_url) > 500 or len(self.dav_username) > 200:
            raise ConfigurationError("The address or user name of the calendar and contacts server is too long")
        if not 0 <= self.check_minutes <= MAXIMUM_CHECK_MINUTES:
            raise ConfigurationError(f"The number of minutes between checks must be between 0 and {MAXIMUM_CHECK_MINUTES}")
        if len(self.trusted_certificates) > MAXIMUM_TRUSTED_CERTIFICATES or len(set(self.trusted_certificates)) != len(self.trusted_certificates):
            raise ConfigurationError("Too many accepted certificates (or one twice)")
        if not all(valid_pin(item) for item in self.trusted_certificates):
            raise ConfigurationError("An accepted certificate is a server (host:port) and a SHA-256 fingerprint of 64 lower-case hexadecimal characters")


@dataclass(frozen=True, slots=True)
class AppConfig:
    accounts: tuple[AccountSettings, ...] = ()
    key_id: str | None = None  # id of the data key in the credential store; the key itself is never stored here

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for settings in self.accounts:
            identifier = settings.account.account_id
            if ACCOUNT_ID_PATTERN.fullmatch(identifier) is None:
                raise ConfigurationError("An account id must be 32 lower-case hexadecimal characters")
            if identifier in seen:
                raise ConfigurationError("Two accounts have the same id")
            seen.add(identifier)
        if self.key_id is not None and re.fullmatch(r"[0-9a-f]{16}", self.key_id) is None:
            raise ConfigurationError("The key id must be 16 lower-case hexadecimal characters")

    def local_folders(self) -> AccountSettings | None:
        """The Local Folders mailbox, if it exists."""
        return next((item for item in self.accounts if item.account.account_id == LOCAL_ACCOUNT_ID), None)

    def find(self, account_id: str) -> AccountSettings:
        for settings in self.accounts:
            if settings.account.account_id == account_id:
                return settings
        raise ConfigurationError("Unknown account")

    def with_account(self, settings: AccountSettings) -> "AppConfig":
        """A copy with this account added, or replacing the account with the same id."""
        if any(item.account.account_id == settings.account.account_id for item in self.accounts):  # the same place in the list
            return AppConfig(tuple(settings if item.account.account_id == settings.account.account_id else item for item in self.accounts), self.key_id)
        return AppConfig((*self.accounts, settings), self.key_id)

    def without_account(self, account_id: str) -> "AppConfig":
        self.find(account_id)
        return AppConfig(tuple(item for item in self.accounts if item.account.account_id != account_id), self.key_id)

    def with_key_id(self, key_id: str | None) -> "AppConfig":
        return AppConfig(self.accounts, key_id)
