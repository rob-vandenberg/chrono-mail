from dataclasses import dataclass
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "folder.py 0.0.4"

# --- Version history ----------------------------------------------------
# v0.0.4: Initial folder model


class FolderRole(Enum):
    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    TRASH = "trash"
    JUNK = "junk"
    ARCHIVE = "archive"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Folder:
    """A mail folder. The path is a tuple of names, so no server delimiter leaks into the model."""

    account_id: str
    path: tuple[str, ...]
    role: FolderRole

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Folder path must not be empty")
        if any(not segment.strip() for segment in self.path):
            raise ValueError("Folder path segments must not be empty")
