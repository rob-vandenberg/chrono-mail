from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from chrono_mail.core import MessageFlag

# --- Version ------------------------------------------------------------
__version__ = "model.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial index model

FLAG_BITS: dict[MessageFlag, int] = {
    MessageFlag.SEEN: 1,
    MessageFlag.REPLIED: 2,
    MessageFlag.FLAGGED: 4,
    MessageFlag.TRASHED: 8,
    MessageFlag.DRAFT: 16,
}
UNREADABLE_SUBJECT: str = "(damaged message)"


def flags_to_bits(flags: frozenset[MessageFlag]) -> int:
    return sum(FLAG_BITS[flag] for flag in flags)


def flags_from_bits(bits: int) -> frozenset[MessageFlag]:
    return frozenset(flag for flag, bit in FLAG_BITS.items() if bits & bit)


class SortField(Enum):
    DATE = "date"
    SUBJECT = "subject"
    SENDER = "sender"
    SIZE = "size"


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


@dataclass(frozen=True, slots=True)
class SearchFilter:
    unread: bool | None = None
    flagged: bool | None = None
    has_attachments: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    minimum_size: int | None = None
    maximum_size: int | None = None
