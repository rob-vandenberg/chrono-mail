from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from chrono_mail.core import MessageFlag, PaletteColour

# --- Version ------------------------------------------------------------
__version__ = "changes.py 4.2.417"

# --- Version history ----------------------------------------------------
# v4.2.417: MessageRow: the people columns (From (email), To (name), To (email), D-291) and the account that received the message (D-292)
# v2.2.314: "changed" also for a message in the Outbox whose waiting time changed (B-21)
# v2.0.281: RebuildStep: how far the index of one place where mail is kept is rebuilt or filled in (D-229)
# v2.0.280: MessageRow.sent: the message's own Date, for the Sent column (B-16)
# v1.9.265: A fact about a collection carries its palette and whether it is read only (palette, read_only): the sidebar takes them from the fact and
#           never asks for every collection again (R-44)
# v1.1.250: A fact about mail carries the new numbers of the folders it touched (numbers, FolderNumbers): the window shows them and counts nothing itself (R-10)
# v1.1.225: conflicts_changed and waiting_changed (D-194)
# v1.1.207: The fact changed: a message waiting in the Scheduled folder was changed in place (D-177)
# v0.5.165: sequence: every fact told is numbered per mailbox (an answer can then count only what the list has heard, D-152)
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
    sent: datetime | None = None  # the moment the message carries itself (its Date line): the Sent column
    sender_address: str = ""  # From (email): the sender's address, else the name (D-291)
    recipient_names: str = ""  # To (name): each recipient's name, else the address
    recipient_addresses: str = ""  # To (email): each recipient's address, else the name
    received_by: str = ""  # Account: the name of the account that received the message, as it is shown ("" when not known, D-292)


@dataclass(frozen=True, slots=True)
class RebuildStep:
    """How far the index of one place where mail is kept is rebuilt or filled in: account is the place's account (Local Folders
    for the accounts kept there); done of total messages; usable: the old index stays in use meanwhile (it may run in the
    background, an abort keeps it); ended once it is complete or aborted."""

    account: str
    done: int
    total: int
    usable: bool = False
    ended: bool = False


ChangeKind = Literal[
    "added",  # a message came into a folder (delivered, copied, saved as a draft)
    "removed",  # a message left the mailbox for good (deleted, or its file was gone)
    "moved",  # a message went from one folder to another (Trash, Archive, Junk, Snoozed, a folder chosen)
    "flags",  # the marks of a message changed (read, flagged, replied)
    "tags",  # the tags of a message changed
    "changed",  # a message waiting in the Scheduled folder was changed in place (edited, or a repeat moved on), or one in the Outbox may now be sent: row is what a list shows of it now
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
    "conflicts_changed",  # the sync conflicts of this account changed (found, brought up to date or settled, D-194): their list asks again
    "waiting_changed",  # what of this account waits for its server changed (D-194)
]


@dataclass(frozen=True, slots=True)
class FolderNumbers:
    """A folder's numbers right after a fact, as the index has them (R-10)."""

    folder: str  # "Archive/2026"
    messages: int
    unread: int


NUMBERED_KINDS: frozenset[str] = frozenset({"added", "removed", "moved", "flags", "changed", "folder_read", "folder_emptied"})  # facts that carry numbers


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
    row: MessageRow | None = None  # added, moved, changed: what a list shows of it (where it is now)
    was_unread: bool = False  # removed, moved, flags: unread before the change
    unread: bool = False  # added, moved, flags: unread after the change
    flags: tuple[str, ...] = ()  # flags: the marks after the change
    tags: tuple[str, ...] = ()  # tags: the tags after the change
    count: int = 0  # folder_read, folder_emptied: how many messages
    role: str = ""  # added, moved: the role of the folder the message is in now ("inbox", "trash", "other" …); folder_made: the folder's role; items and collections: "events", "tasks" or "contacts"
    name: str = ""  # collections: the name
    colour: str = ""  # collections: the colour
    palette: tuple[PaletteColour, ...] = ()  # calendars (collection_made, collection_edited): the palette after the change, its own colour first (D-191)
    read_only: bool = False  # collections (collection_made, collection_edited): read only on its server after the change
    sequence: int = 0  # the number of this fact among the facts of its mailbox, counted from 1 since the program started (D-152)
    numbers: tuple[FolderNumbers, ...] = ()  # mail facts (NUMBERED_KINDS): the numbers of folder (and to_folder) right after the change (R-10)
