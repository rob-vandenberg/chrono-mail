"""Pure data model: Account, Folder, MessageRef, Flags and error types. No I/O."""

from chrono_mail.core.account import Account, AccountKind, ConnectionSecurity, ServerSettings
from chrono_mail.core.credentials import CredentialBackend
from chrono_mail.core.errors import (
    AuthenticationError,
    ChronoMailError,
    ConfigurationError,
    CryptoError,
    MessageFormatError,
    ProtocolError,
    StorageError,
)
from chrono_mail.core.flags import MessageFlag, flags_from_maildir_suffix, flags_to_maildir_suffix
from chrono_mail.core.folder import Folder, FolderRole
from chrono_mail.core.message import MessageRef

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Export AuthenticationError
# v0.0.9: Export CredentialBackend
# v0.0.4: Export the core data model
# v0.0.2: Initial skeleton

__all__ = [
    "Account",
    "AccountKind",
    "AuthenticationError",
    "ChronoMailError",
    "ConfigurationError",
    "ConnectionSecurity",
    "CredentialBackend",
    "CryptoError",
    "Folder",
    "FolderRole",
    "MessageFlag",
    "MessageFormatError",
    "MessageRef",
    "ProtocolError",
    "ServerSettings",
    "StorageError",
    "flags_from_maildir_suffix",
    "flags_to_maildir_suffix",
]
