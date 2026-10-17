from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from chrono_mail.core import MessageFlag

# --- Version ------------------------------------------------------------
__version__ = "changes.py 0.5.159"

# --- Version history ----------------------------------------------------
# v0.5.159: facts of calendars, task lists and address books: items added, changed, removed, moved; collections made, edited, removed; collection_changed after a big job (D-146 step 5)
# v0.5.156: role: the role of the folder the message is in after the change (a Favorites view decides by it whether the message belongs in it)
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
    "item_added",  # calendars, task lists, address books: mailbox is the account, folder the collection, message_id the item, role its kind
    "item_changed",
    "item_removed",
    "item_moved",  # to_folder: the collection it went to, new_message_id: its id there
    "collection_made",  # name and colour: of the collection
    "collection_edited",
    "collection_removed",
    "collection_changed",  # a big job (a sync with the server, an import, a tag renamed) changed the collections of this account: views of them fetch again
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
    role: str = ""  # added, moved: the role of the folder the message is in now ("inbox", "trash", "other" …); folder_made: the folder's role; items and collections: "events", "tasks" or "contacts"
    name: str = ""  # collections: the name
    colour: str = ""  # collections: the colour
