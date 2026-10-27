"""A stored message (RFC 5322 bytes) as the properties of an item in a data file: its headers as they came, who sent it and to whom,
its dates, its plain and HTML text, its attachments (an attached message as an .eml file), its marks and its categories."""

import email
import email.policy
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage, Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from chrono_mail.core.flags import MessageFlag
from chrono_mail.pst.names import PS_PUBLIC_STRINGS
from chrono_mail.pst.writer import Attachment
from chrono_mail.pst.writer_ltp import Prop, binary, boolean, integer, text, texts, time
from chrono_mail.pst.writer_names import NameRegistry

# --- Version ------------------------------------------------------------
__version__ = "writer_mail.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: a stored message as the properties, recipients and attachments of an item in a data file

MAXIMUM_PARTS: int = 1_000
_READ = 0x01
_UNSENT = 0x08
_HAS_ATTACHMENT = 0x10
_REPLIED_VERB = 102
_MESSAGE_OPEN_ICON = 0x105  # replied
_ONE_OFF_PROVIDER = bytes.fromhex("812B1FA4BEA310199D6E00DD010F5402")


@dataclass(frozen=True, slots=True)
class Item:
    props: list[Prop]
    recipients: list[list[Prop]]
    attachments: list[Attachment]


def one_off(name: str, address: str) -> bytes:
    """A one-off entry id for an internet address (MS-OXCDATA 2.2.5.1), in Unicode."""
    def piece(value: str) -> bytes:
        return value.replace("\x00", "").encode("utf-16-le") + b"\x00\x00"

    return bytes(4) + _ONE_OFF_PROVIDER + b"\x00\x00" + (0x9001).to_bytes(2, "little") + piece(name or address) + piece("SMTP") + piece(address)


def categories(names: NameRegistry, values: tuple[str, ...]) -> list[Prop]:
    if not values:
        return []
    return [texts(names.id_of(PS_PUBLIC_STRINGS, "Keywords"), list(values))]


def recipient(name: str, address: str, kind: int, extra: list[Prop] | None = None) -> list[Prop]:
    """One row of a recipient table: 1 To, 2 Cc, 3 Bcc."""
    shown = name or address
    row = [
        integer(0x0C15, kind), boolean(0x0E0F, True), integer(0x0FFE, 6), binary(0x0FFF, one_off(name, address)), text(0x3001, shown),
        text(0x3002, "SMTP"), text(0x3003, address), binary(0x300B, b"SMTP:" + address.upper().encode("utf-8", "replace") + b"\x00"),
        integer(0x3900, 0), text(0x39FE, address), text(0x3A20, shown), boolean(0x3A40, False), text(0x5FF6, shown),
        binary(0x5FF7, one_off(name, address)), integer(0x5FFD, 1), integer(0x5FFF, 0),
    ]
    return row + (extra or [])


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _attachment(data: bytes, name: str, media: str, content_id: str, inline: bool) -> Attachment:
    extension = name[name.rfind(".") :] if "." in name else ""
    props = [
        integer(0x3705, 1), binary(0x3701, data), integer(0x0E20, len(data)), text(0x3707, name), text(0x3704, name), text(0x3001, name),
        text(0x370E, media), integer(0x370B, -1), integer(0x0FFE, 7),
    ]
    if extension:
        props.append(text(0x3703, extension))
    if content_id:
        props.append(text(0x3712, content_id))
    if inline:
        props += [boolean(0x7FFE, True), integer(0x3714, 4)]
    return Attachment(props)


class _Parts:
    def __init__(self) -> None:
        self.plain: str | None = None
        self.html: str | None = None
        self.attachments: list[Attachment] = []
        self.count = 0

    def visit(self, part: Message) -> None:
        self.count += 1
        if self.count > MAXIMUM_PARTS:
            return
        media = part.get_content_type()
        if part.is_multipart() and media != "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list):
                for child in payload:
                    if isinstance(child, Message):
                        self.visit(child)
            return
        disposition = (part.get_content_disposition() or "").lower()
        name = part.get_filename() or ""
        if media == "message/rfc822":
            inner = part.get_payload()
            raw = inner[0].as_bytes(policy=email.policy.SMTP) if isinstance(inner, list) and inner and isinstance(inner[0], Message) else b""
            subject = inner[0].get("Subject", "") if isinstance(inner, list) and inner and isinstance(inner[0], Message) else ""
            self.attachments.append(_attachment(raw, name or f"{str(subject).strip()[:60] or 'message'}.eml", "message/rfc822", "", False))
            return
        if disposition != "attachment" and not name:
            if media == "text/plain" and self.plain is None:
                self.plain = _text_of(part)
                return
            if media == "text/html" and self.html is None:
                self.html = _text_of(part)
                return
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            data = b""
        content_id = (part.get("Content-ID", "") or "").strip().strip("<>")
        self.attachments.append(_attachment(data, name or "attachment", media, content_id, disposition == "inline" and bool(content_id)))


def _header_text(raw: bytes) -> str:
    end = raw.find(b"\r\n\r\n")
    if end < 0:
        end = raw.find(b"\n\n")
    head = raw[: end if end >= 0 else len(raw)]
    return head.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"


def _date(message: EmailMessage) -> datetime | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _people(message: EmailMessage, header: str) -> list[tuple[str, str]]:
    values = [str(value) for value in message.get_all(header, [])]
    return [(name, address) for name, address in getaddresses(values) if address]


def mail_item(
    raw: bytes, flags: frozenset[MessageFlag], tags: tuple[str, ...], received: datetime | None, names: NameRegistry,
) -> Item:
    """The item for one stored message."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    parts = _Parts()
    parts.visit(message)
    sent = _date(message)
    when = received or sent or datetime.now(UTC)
    subject = " ".join(str(message.get("Subject", "")).split())
    sender_name, sender_address = parseaddr(str(message.get("From", "")))
    sender_shown = sender_name or sender_address
    to = _people(message, "To")
    cc = _people(message, "Cc")
    bcc = _people(message, "Bcc")
    message_flags = (_READ if MessageFlag.SEEN in flags else 0) | (_UNSENT if MessageFlag.DRAFT in flags else 0)
    if parts.attachments:
        message_flags |= _HAS_ATTACHMENT
    props: list[Prop] = [
        text(0x001A, "IPM.Note"), text(0x0037, subject), text(0x0070, subject), text(0x0E1D, subject), integer(0x0E07, message_flags),
        integer(0x0E08, len(raw)), integer(0x0017, 1), integer(0x0036, 0), time(0x0E06, when), time(0x3007, when), time(0x3008, when),
        text(0x007D, _header_text(raw)), boolean(0x0E1B, bool(parts.attachments)), integer(0x3FDE, 65001),
        text(0x0E04, "; ".join(name or address for name, address in to)), text(0x0E03, "; ".join(name or address for name, address in cc)),
        text(0x0E02, "; ".join(name or address for name, address in bcc)),
    ]
    if sent is not None:
        props.append(time(0x0039, sent))
    if sender_address:
        for name_tag, type_tag, address_tag, smtp_tag, entry_tag in ((0x0C1A, 0x0C1E, 0x0C1F, 0x5D01, 0x0C19), (0x0042, 0x0064, 0x0065, 0x5D02, 0x0041)):
            props += [text(name_tag, sender_shown), text(type_tag, "SMTP"), text(address_tag, sender_address), text(smtp_tag, sender_address),
                      binary(entry_tag, one_off(sender_name, sender_address))]
    for tag, header in ((0x1035, "Message-ID"), (0x1042, "In-Reply-To"), (0x1039, "References")):
        value = " ".join(str(message.get(header, "")).split())
        if value:
            props.append(text(tag, value))
    importance = str(message.get("Importance", "") or message.get("X-Priority", "")).strip().lower()
    if importance in ("high", "1", "2") or importance.startswith(("1 ", "2 ")):
        props.append(integer(0x0017, 2))
    elif importance in ("low", "4", "5") or importance.startswith(("4 ", "5 ")):
        props.append(integer(0x0017, 0))
    if parts.plain is not None or parts.html is None:
        props.append(text(0x1000, parts.plain or ""))
    if parts.html is not None:
        props.append(binary(0x1013, parts.html.encode("utf-8")))
    if MessageFlag.FLAGGED in flags:
        props += [integer(0x1090, 2), integer(0x1095, 6)]
    if MessageFlag.REPLIED in flags:
        props += [integer(0x1081, _REPLIED_VERB), time(0x1082, when), integer(0x1080, _MESSAGE_OPEN_ICON)]
    else:
        props.append(integer(0x1080, 0x100 if MessageFlag.SEEN in flags else 0x101))
    props += categories(names, tags)
    recipients = [recipient(name, address, kind) for kind, people in ((1, to), (2, cc), (3, bcc)) for name, address in people]
    return Item(props, recipients, parts.attachments)

