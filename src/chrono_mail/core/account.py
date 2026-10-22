from dataclasses import dataclass
from enum import Enum

from chrono_mail.core.errors import ConfigurationError

# --- Version ------------------------------------------------------------
__version__ = "account.py 1.1.212"

# --- Version history ----------------------------------------------------
# v1.1.212: An account of calendars, contacts and tasks only, without mail (D-185)
# v0.0.45: Local Folders
# v0.0.31: IMAP accounts
# v0.0.4: Initial account model

MAXIMUM_PORT: int = 65535
LOCAL_ACCOUNT_ID: str = "00000000000000000000000000000001"  # the one Local Folders mailbox


class AccountKind(Enum):
    POP3 = "pop3"
    IMAP = "imap"
    LOCAL = "local"  # Local Folders: a mailbox without servers that POP3 accounts may share (D-060)
    DAV = "dav"  # calendars, contacts and tasks only, on a CalDAV and CardDAV server: no mail (D-185)


class ConnectionSecurity(Enum):
    NONE = "none"
    STARTTLS = "starttls"  # also STLS for POP3
    TLS = "tls"  # implicit TLS


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Where and how to connect to one server. The password lives in the credential store."""

    host: str
    port: int
    security: ConnectionSecurity
    username: str

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ConfigurationError("Server host must not be empty")
        if not 1 <= self.port <= MAXIMUM_PORT:
            raise ConfigurationError(f"Server port must be between 1 and {MAXIMUM_PORT}, got {self.port}")


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    display_name: str
    email_address: str
    kind: AccountKind
    incoming: ServerSettings
    outgoing: ServerSettings
