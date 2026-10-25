from dataclasses import dataclass
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "folder.py 3.6.354"

# --- Version history ----------------------------------------------------
# v3.6.354: The roles of Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered); OWN_FOLDERS, the one place their names are
#           written; own_role; a standard folder is one with a role (CR-36)
# v0.0.4: Initial folder model


class FolderRole(Enum):
    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    TRASH = "trash"
    JUNK = "junk"
    ARCHIVE = "archive"
    OUTBOX = "outbox"  # the roles below are Chrono Mail's own: folders it makes itself, at the top of an account (OWN_FOLDERS)
    SCHEDULED = "scheduled"
    SNOOZED = "snoozed"
    RECOVERED = "recovered"
    OTHER = "other"  # a folder the person made: the only kind that is not a standard folder


OWN_FOLDERS: dict[FolderRole, tuple[str, ...]] = {
    FolderRole.OUTBOX: ("Outbox",), FolderRole.SCHEDULED: ("Scheduled",), FolderRole.SNOOZED: ("Snoozed",), FolderRole.RECOVERED: ("Recovered",),
}  # where Chrono Mail makes its own folders: the one place their names are written; a folder there takes its role when it is made


def own_role(path: tuple[str, ...]) -> FolderRole | None:
    """The role of the folder Chrono Mail keeps at this path, or None when none of its own folders is there."""
    return next((role for role, own in OWN_FOLDERS.items() if own == path), None)


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
