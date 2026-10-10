"""Pure data model: Account, Folder, MessageRef, Flags and error types. No I/O."""

from chrono_mail.core.account import Account, AccountKind, ConnectionSecurity, ServerSettings
from chrono_mail.core.errors import (
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
__version__ = "__init__.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Export the core data model
# v0.0.2: Initial skeleton

__all__ = [
    "Account",
    "AccountKind",
    "ChronoMailError",
    "ConfigurationError",
    "ConnectionSecurity",
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
