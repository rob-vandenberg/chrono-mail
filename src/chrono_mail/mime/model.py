from dataclasses import dataclass, field
from datetime import datetime

# --- Version ------------------------------------------------------------
__version__ = "model.py 0.0.16"

# --- Version history ----------------------------------------------------
# v0.0.16: Bcc and X-Chrono-Mail headers for drafts
# v0.0.5: Initial message model


@dataclass(frozen=True, slots=True)
class Address:
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str | None
    content_type: str
    content_id: str | None
    inline: bool
    data: bytes


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """What the parser found in a raw message. Fields that are missing or unreadable are empty."""

    subject: str = ""
    from_addresses: tuple[Address, ...] = ()
    to_addresses: tuple[Address, ...] = ()
    cc_addresses: tuple[Address, ...] = ()
    reply_to_addresses: tuple[Address, ...] = ()
    bcc_addresses: tuple[Address, ...] = ()  # only present in a draft; a sent message never carries them
    chrono_headers: tuple[tuple[str, str], ...] = ()  # (lower-case name, value) of the X-Chrono-Mail-* headers
    raw_address_headers: tuple[tuple[str, str], ...] = ()  # (lower-case header name, original text)
    date: datetime | None = None  # timezone-aware; a date without a time zone is taken as UTC
    message_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    text_body: str | None = None
    html_body: str | None = None
    attachments: tuple[Attachment, ...] = ()
    defects: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    from_address: Address
    to_addresses: tuple[Address, ...] = ()
    cc_addresses: tuple[Address, ...] = ()
    bcc_addresses: tuple[Address, ...] = ()
    subject: str = ""
    text_body: str = ""
    html_body: str | None = None
    attachments: tuple[Attachment, ...] = ()
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    extra_headers: tuple[tuple[str, str], ...] = ()  # X-Chrono-Mail-* only: the program's own notes in a draft
