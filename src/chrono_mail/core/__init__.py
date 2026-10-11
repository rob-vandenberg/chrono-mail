"""Pure data model: Account, Folder, MessageRef, Flags and error types. No I/O."""

from chrono_mail.core.account import LOCAL_ACCOUNT_ID, Account, AccountKind, ConnectionSecurity, ServerSettings
from chrono_mail.core.appinfo import program_version
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
from chrono_mail.core.pim import CollectionInfo, CollectionKind

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.59"

# --- Version history ----------------------------------------------------
# v0.0.59: Export the collection kinds
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.26: Version number in the About dialog
# v0.0.10: Export AuthenticationError
# v0.0.9: Export CredentialBackend
# v0.0.4: Export the core data model
# v0.0.2: Initial skeleton

__all__ = [
    "CollectionInfo",
    "CollectionKind",
    "LOCAL_ACCOUNT_ID",
    "program_version",
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
