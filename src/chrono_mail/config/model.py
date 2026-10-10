import re
from dataclasses import dataclass
from enum import Enum

from chrono_mail.core import Account, ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "model.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: Initial configuration model

ACCOUNT_ID_PATTERN: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")  # used in directory names: nothing else may pass
MAXIMUM_DELETE_AFTER_DAYS: int = 3650


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

    def find(self, account_id: str) -> AccountSettings:
        for settings in self.accounts:
            if settings.account.account_id == account_id:
                return settings
        raise ConfigurationError("Unknown account")

    def with_account(self, settings: AccountSettings) -> "AppConfig":
        """A copy with this account added, or replacing the account with the same id."""
        others = tuple(item for item in self.accounts if item.account.account_id != settings.account.account_id)
        return AppConfig((*others, settings), self.key_id)

    def without_account(self, account_id: str) -> "AppConfig":
        self.find(account_id)
        return AppConfig(tuple(item for item in self.accounts if item.account.account_id != account_id), self.key_id)

    def with_key_id(self, key_id: str | None) -> "AppConfig":
        return AppConfig(self.accounts, key_id)
