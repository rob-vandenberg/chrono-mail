"""Pure data model: Account, Folder, MessageRef, Flags and error types. No I/O, apart from the thin door to the credential store (credentials.py, D-220)."""

from chrono_mail.core.account import LOCAL_ACCOUNT_ID, Account, AccountKind, ConnectionSecurity, ServerSettings
from chrono_mail.core.appinfo import program_version
from chrono_mail.core.credentials import SERVICE_NAME, CredentialBackend, CredentialEntries, CredentialFailure
from chrono_mail.core.errors import (
    AuthenticationError,
    ChronoMailError,
    ConfigurationError,
    CryptoError,
    MessageFormatError,
    ProtocolError,
    RebuildAborted,
    StorageError,
)
from chrono_mail.core.flags import MessageFlag, flags_from_maildir_suffix, flags_to_maildir_suffix
from chrono_mail.core.folder import Folder, FolderRole
from chrono_mail.core.message import MessageRef
from chrono_mail.core.names import displayed_address, shown_name, written_address
from chrono_mail.core.pim import (
    LOOK_FOLLOWS,
    LOOK_KEPT,
    LOOK_WAITING,
    OWN_COLOUR_ID,
    CollectionInfo,
    CollectionKind,
    CollectionLook,
    PaletteColour,
    is_colour,
    palette_of,
    palette_problem,
)

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 4.2.430"

# --- Version history ----------------------------------------------------
# v4.2.430: Export written_address
# v2.0.281: Export RebuildAborted
# v1.8.263: Export CredentialEntries, CredentialFailure and SERVICE_NAME (R-16)
# v1.1.221: Export CollectionLook and its states, is_colour, palette_problem
# v1.1.219: Export the palette of a calendar
# v0.5.166: Export shown_name
# v0.0.59: Export the collection kinds
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.26: Version number in the About dialog
# v0.0.10: Export AuthenticationError
# v0.0.9: Export CredentialBackend
# v0.0.4: Export the core data model
# v0.0.2: Initial skeleton

__all__ = [
    "CollectionInfo",
    "CollectionLook",
    "LOOK_FOLLOWS",
    "LOOK_KEPT",
    "LOOK_WAITING",
    "is_colour",
    "palette_problem",
    "OWN_COLOUR_ID",
    "PaletteColour",
    "palette_of",
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
    "CredentialEntries",
    "CredentialFailure",
    "SERVICE_NAME",
    "CryptoError",
    "Folder",
    "FolderRole",
    "MessageFlag",
    "MessageFormatError",
    "MessageRef",
    "shown_name",
    "written_address",
    "displayed_address",
    "ProtocolError",
    "ServerSettings",
    "RebuildAborted",
    "StorageError",
    "flags_from_maildir_suffix",
    "flags_to_maildir_suffix",
]
