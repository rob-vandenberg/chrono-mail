from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from chrono_mail.core import MessageFlag

# --- Version ------------------------------------------------------------
__version__ = "changes.py 0.5.155"

# --- Version history ----------------------------------------------------
# v0.5.155: Initial version: the facts the engine reports when mail changes (D-146), and the row a list shows of a message

_FLAG_NAMES: dict[MessageFlag, str] = {
    MessageFlag.SEEN: "seen",
    MessageFlag.REPLIED: "replied",
    MessageFlag.FLAGGED: "flagged",
    MessageFlag.TRASHED: "trashed",
    MessageFlag.DRAFT: "draft",
}


def flag_names(flags: frozenset[MessageFlag]) -> tuple[str, ...]:
    return tuple(sorted(_FLAG_NAMES[flag] for flag in flags))


@dataclass(frozen=True, slots=True)
class MessageRow:
    """What a list shows of one message."""

    message_id: str
    folder: str
    subject: str
    sender: str
    date: datetime | None
    size: int
    flags: tuple[str, ...]
    attachments: bool
    preview: str
    recipients: str
    thread_size: int = 1  # messages in the conversation, in all folders
    account: str = ""  # only filled in the combined views (message.combined): the account the row belongs to
    snoozed_until: datetime | None = None  # in the Snoozed folder: when the message comes back
    tags: tuple[str, ...] = ()


ChangeKind = Literal[
    "added",  # a message came into a folder (delivered, copied, saved as a draft)
    "removed",  # a message left the mailbox for good (deleted, or its file was gone)
    "moved",  # a message went from one folder to another (Trash, Archive, Junk, Snoozed, a folder chosen)
    "flags",  # the marks of a message changed (read, flagged, replied)
    "tags",  # the tags of a message changed
    "folder_read",  # every message of a folder was marked read
    "folder_emptied",  # every message of a folder was deleted for good
    "folder_made",
    "folder_renamed",
    "folder_deleted",
    "mailbox_changed",  # a big job changed this mailbox (a mail check, an import, a rebuilt index): views of it fetch again
]


@dataclass(frozen=True, slots=True)
class Change:
    """One fact about mail that changed, reported by the engine right after it happened (for IMAP: after the server
    confirmed). Every view and every count updates from these facts instead of fetching everything again (D-146)."""

    kind: ChangeKind
    mailbox: str  # the account whose store holds the mail (Local Folders for the accounts that keep their mail there)
    folder: str = ""  # where the message was, or the folder concerned ("Archive/2026")
    message_id: str = ""
    to_folder: str = ""  # moved: where it went; folder_renamed: the new name
    new_message_id: str = ""  # moved: its id there
    row: MessageRow | None = None  # added, moved: what a list shows of it (where it is now)
    was_unread: bool = False  # removed, moved, flags: unread before the change
    unread: bool = False  # added, moved, flags: unread after the change
    flags: tuple[str, ...] = ()  # flags: the marks after the change
    tags: tuple[str, ...] = ()  # tags: the tags after the change
    count: int = 0  # folder_read, folder_emptied: how many messages
