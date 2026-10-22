from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from chrono_mail.core import MessageFlag

# --- Version ------------------------------------------------------------
__version__ = "model.py 2.0.280"

# --- Version history ----------------------------------------------------
# v2.0.280: SortField.SENT and the sent moment of IndexEntry and IndexedMessage: the message's own Date (the Sent column, B-16)
# v1.1.242: SearchFilter.also_tag: a second tag the messages must carry too (Filter > Tagged with in a Tags view, R-03)
# v1.1.196: IndexEntry.send_at: when a message kept in the Outbox is to be sent (0: it could not be read)
# v1.1.191: SearchFilter.replied (the Unreplied view)
# v0.5.162: SearchFilter.stay: messages that stay in a view filtered by unread, flagged or a tag after they stopped matching there
# v0.5.134: Tags in index entries, indexed messages and search filters
# v0.3.109: Sorting by unread, flagged, attachment, replied, folder, messages in conversation and preview
# v0.2.94: Sorting by recipients (to); group headers of the message list (GroupCount, DATE_GROUPS, SIZE_GROUPS)
# v0.0.23: people in index entries, KnownAddress
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.13: preview
# v0.0.8: Initial index model

FLAG_BITS: dict[MessageFlag, int] = {
    MessageFlag.SEEN: 1,
    MessageFlag.REPLIED: 2,
    MessageFlag.FLAGGED: 4,
    MessageFlag.TRASHED: 8,
    MessageFlag.DRAFT: 16,
}
UNREADABLE_SUBJECT: str = "(damaged message)"
UNKNOWN_SEND_TIME: int = 0  # the send time of a message that could not be read: it is due at once, so it is looked at (and reported)
PREVIEW_CHARACTERS: int = 160


def flags_to_bits(flags: frozenset[MessageFlag]) -> int:
    return sum(FLAG_BITS[flag] for flag in flags)


def flags_from_bits(bits: int) -> frozenset[MessageFlag]:
    return frozenset(flag for flag, bit in FLAG_BITS.items() if bits & bit)


class SortField(Enum):
    DATE = "date"
    SENT = "sent"  # the moment the message carries itself (its Date line)
    SUBJECT = "subject"
    SENDER = "sender"
    SIZE = "size"
    RECIPIENTS = "to"
    UNREAD = "unread"
    FLAGGED = "flagged"
    ATTACHMENT = "attachment"
    REPLIED = "replied"
    FOLDER = "folder"
    CONVERSATION = "conversation"
    PREVIEW = "preview"


YES_NO_SORTS: frozenset[SortField] = frozenset({SortField.UNREAD, SortField.FLAGGED, SortField.ATTACHMENT, SortField.REPLIED})  # no group headers


DATE_GROUPS: tuple[tuple[str, str], ...] = (  # key, name; newest first. The boundaries are given by the caller (local time)
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("this_week", "Earlier this week"),
    ("last_week", "Last week"),
    ("this_month", "Earlier this month"),
    ("last_month", "Last month"),
    ("older", "Older"),
    ("no_date", "No date"),
)
SIZE_GROUPS: tuple[tuple[str, str, int | None], ...] = (  # key, name, upper limit in bytes (not included); smallest first
    ("tiny", "Tiny", 10 * 1024),
    ("small", "Small", 100 * 1024),
    ("medium", "Medium", 1024 * 1024),
    ("large", "Large", 10 * 1024 * 1024),
    ("huge", "Huge", None),
)


@dataclass(frozen=True, slots=True)
class DateBounds:
    """Where the date groups begin, newest first: today, yesterday, this week (Monday), last week, this month, last month."""

    today: datetime
    yesterday: datetime
    this_week: datetime
    last_week: datetime
    this_month: datetime
    last_month: datetime

    def starts(self) -> tuple[datetime, ...]:
        return (self.today, self.yesterday, self.this_week, self.last_week, self.this_month, self.last_month)


@dataclass(frozen=True, slots=True)
class GroupCount:
    """One group of the message list, in list order: what it is (key: a date or size group, or the sorted text in
    lower case), the text it shows (the date or size group's name, or the sender, subject or recipients as stored), and
    how many messages it holds."""

    key: str
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """Everything the index keeps about one message. The body is only used for full-text search."""

    store_id: str
    folder: tuple[str, ...]
    path: str  # relative path hint inside the folder directory, for example "cur/<id>!2,S"
    flags: frozenset[MessageFlag]
    date: datetime | None
    size: int
    subject: str
    sender: str
    recipients: str
    message_id: str | None
    thread_key: str
    has_attachments: bool
    body: str
    preview: str = ""  # the first characters of the body, for the message list
    people: tuple[tuple[str, str, bool], ...] = ()  # (name, address, is a recipient) for the address list
    tags: tuple[str, ...] = ()  # as written; the store's tags file is the truth
    send_at: int | None = None  # a scheduled message: when it is to be sent (its own header is the truth); 0: not readable, so not known
    sent: datetime | None = None  # the moment the message carries itself (its Date line): the Sent column


@dataclass(frozen=True, slots=True)
class KnownAddress:
    """An address seen in the mail, for completing what a person types."""

    name: str
    address: str
    as_sender: int
    as_recipient: int


@dataclass(frozen=True, slots=True)
class IndexedMessage:
    store_id: str
    folder: tuple[str, ...]
    path: str
    flags: frozenset[MessageFlag]
    date: datetime | None
    size: int
    subject: str
    sender: str
    recipients: str
    message_id: str | None
    thread_key: str
    has_attachments: bool
    preview: str = ""
    tags: tuple[str, ...] = ()
    sent: datetime | None = None  # the message's own Date line


@dataclass(frozen=True, slots=True)
class SearchFilter:
    unread: bool | None = None
    flagged: bool | None = None
    replied: bool | None = None  # True: only messages with the Replied mark, False: only those without
    has_attachments: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    minimum_size: int | None = None
    maximum_size: int | None = None
    newest_per_thread: bool = False  # only the newest message of each conversation in its folder (ignored with a text query)
    tag: str | None = None  # only messages with this tag (compared without regard to case)
    also_tag: str | None = None  # ... and with this one too (Filter > Tagged with in a Tags view)
    stay: frozenset[str] = frozenset()  # messages that are found although they no longer match unread, flagged or tag (changed while a view showed them)
