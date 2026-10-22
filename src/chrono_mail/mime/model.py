from dataclasses import dataclass, field
from datetime import datetime

# --- Version ------------------------------------------------------------
__version__ = "model.py 1.9.276"

# --- Version history ----------------------------------------------------
# v1.9.276: received: when the mail server received the message (D-226)
# v1.1.206: REPEAT_HEADER: a repeating message carries its timetable in itself (D-176)
# v1.1.203: send_time's comment names the Scheduled folder
# v1.1.196: SEND_AT_HEADER and send_time(): the moment a message kept in the Outbox is to be sent
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
    received: datetime | None = None  # when the mail server received it: the topmost Received line, else Delivery-date (D-226)
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


SEND_AT_HEADER: str = "x-chrono-mail-send-at"  # a message waiting in the Scheduled folder: when it is sent (a repeating one: its next send), seconds since 1970 (UTC)
REPEAT_HEADER: str = "x-chrono-mail-repeat"  # a repeating message: its whole timetable and how far the series is (engine/recurring.py writes and reads it)


def send_time(message: ParsedMessage) -> int | None:
    """The moment a scheduled message (in the Scheduled folder) is to be sent, or None when it is not scheduled (or the value is not a number)."""
    text = dict(message.chrono_headers).get(SEND_AT_HEADER, "")
    return int(text) if text.isdigit() else None
