import codecs
import re
from datetime import UTC, datetime
from email.headerregistry import AddressHeader, DateHeader
from email.message import Message, MIMEPart
from email.parser import BytesHeaderParser, BytesParser
from email.policy import default

from chrono_mail.mime.limits import (
    MAXIMUM_DEFECTS,
    MAXIMUM_HEADER_SECTION_BYTES,
    MAXIMUM_MIME_DEPTH,
    MAXIMUM_PART_BYTES,
    MAXIMUM_PARTS,
)
from chrono_mail.mime.model import Address, Attachment, ParsedMessage

# --- Version ------------------------------------------------------------
__version__ = "parser.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.5: Initial message parser

_ID_PATTERN: re.Pattern[str] = re.compile(r"<[^<>\s]+>")
_ADDRESS_HEADERS: tuple[str, ...] = ("from", "to", "cc", "reply-to")
_CAMEL_BOUNDARY: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")
_FOLDING: re.Pattern[str] = re.compile(r"\r?\n[ \t]")


def scrub(text: str) -> str:
    """Text that is always valid Unicode.

    Python's email parser hands raw 8-bit bytes in a header back as lone surrogate characters
    (surrogateescape). Those must not leave the parser: they cannot be stored or encoded. The original
    bytes are recovered and read as UTF-8, so a header sent as raw UTF-8 comes out as the real text;
    whatever is not valid becomes U+FFFD.
    """
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:  # a surrogate that is not one of the escaped bytes
        return "".join("\ufffd" if "\ud800" <= character <= "\udfff" else character for character in text)


class _Collector:
    """Gathers the defects of one parse, without duplicates and with a cap."""

    def __init__(self) -> None:
        self.defects: list[str] = []

    def add(self, defect: str) -> None:
        if defect not in self.defects and len(self.defects) < MAXIMUM_DEFECTS:
            self.defects.append(defect)


def _defect_name(defect: object) -> str:
    """Python's defect class name in kebab-case, for example MissingHeaderBodySeparatorDefect."""
    name = type(defect).__name__.removesuffix("Defect")
    return _CAMEL_BOUNDARY.sub("-", name).lower()


def parse_message(raw: bytes) -> ParsedMessage:
    """Parse a raw message. Never raises: whatever cannot be read becomes a defect."""
    collector = _Collector()
    try:
        return _parse(raw, collector)
    except RecursionError:
        collector.add("nesting-too-deep")
    except Exception as error:  # hostile input must never crash the caller (AGENTS.md section 4.2)
        collector.add(f"parse-failed-{type(error).__name__}")
    return _parse_headers_only(raw, collector)


def _limit_header_section(raw: bytes, collector: _Collector) -> bytes:
    """Cut a message whose header section exceeds the cap down to its headers."""
    boundary = len(raw)
    for separator in (b"\n\n", b"\r\n\r\n"):
        position = raw.find(separator, 0, MAXIMUM_HEADER_SECTION_BYTES + len(separator))
        if position != -1:
            boundary = min(boundary, position)
    if boundary <= MAXIMUM_HEADER_SECTION_BYTES:
        return raw
    collector.add("header-section-too-large")
    cut = raw.rfind(b"\n", 0, MAXIMUM_HEADER_SECTION_BYTES)
    return raw[: max(cut, 0)] + b"\n\n"


def _parse(raw: bytes, collector: _Collector) -> ParsedMessage:
    limited = _limit_header_section(raw, collector)
    root = BytesParser(policy=default).parsebytes(limited)
    return _extract(root, collector, with_body=True)


def _parse_headers_only(raw: bytes, collector: _Collector) -> ParsedMessage:
    try:
        limited = _limit_header_section(raw, collector)
        root = BytesHeaderParser(policy=default).parsebytes(limited)
        return _extract(root, collector, with_body=False)
    except Exception as error:
        collector.add(f"headers-failed-{type(error).__name__}")
        return ParsedMessage(defects=tuple(collector.defects))


def _header_text(part: MIMEPart, name: str, collector: _Collector) -> str | None:
    """The decoded text of a header; when decoding fails or loses everything, its raw text."""
    decoded: str | None = None
    try:
        value = part[name]
        decoded = None if value is None else scrub(str(value))
    except Exception:
        collector.add(f"header-unreadable-{name.lower()}")
    raw_text = _raw_header(part, name.lower(), collector)
    if raw_text is not None and not (decoded or "").strip() and raw_text.strip():
        collector.add(f"header-undecodable-{name.lower()}")
        return raw_text
    return decoded if decoded is not None else raw_text


def _raw_header(part: MIMEPart, name: str, collector: _Collector) -> str | None:
    """The header text exactly as received (unfolded), before any interpretation."""
    try:
        for raw_name, raw_value in part.raw_items():
            if raw_name.lower() == name:
                text = _FOLDING.sub(" ", str(raw_value))
                return scrub(text).strip()
    except Exception:
        collector.add(f"header-lost-{name}")
    return None


def _addresses(part: MIMEPart, name: str, collector: _Collector) -> tuple[Address, ...]:
    try:
        value = part[name]
        if value is None:
            return ()
        if getattr(value, "defects", ()):
            collector.add(f"invalid-address-header-{name.lower()}")
        if not isinstance(value, AddressHeader):
            collector.add(f"invalid-address-header-{name.lower()}")
            return ()
        found: list[Address] = []
        for entry in value.addresses:
            try:
                if entry.addr_spec and entry.addr_spec != "<>":
                    found.append(Address(scrub(entry.display_name), scrub(entry.addr_spec)))
            except Exception:
                collector.add(f"invalid-address-{name.lower()}")
        return tuple(found)
    except Exception:
        collector.add(f"header-unreadable-{name.lower()}")
        return ()


def _date(part: MIMEPart, collector: _Collector) -> datetime | None:
    try:
        value = part["Date"]
        if value is None:
            return None
        if not isinstance(value, DateHeader) or value.datetime is None:
            collector.add("invalid-date")
            return None
        moment = value.datetime
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    except Exception:
        collector.add("invalid-date")
        return None


def _decode_text(part: MIMEPart, data: bytes, collector: _Collector) -> str:
    try:
        charset = part.get_content_charset() or "utf-8"
        codecs.lookup(charset)
    except (LookupError, TypeError, ValueError):
        collector.add("unknown-charset")
        charset = "utf-8"
    try:
        text = data.decode(charset)
    except UnicodeDecodeError:  # before ValueError, which it is a subclass of
        collector.add("invalid-text-encoding")
        text = data.decode(charset, errors="replace")
    except (LookupError, TypeError, ValueError):  # for example a codec that is not a text encoding
        collector.add("unknown-charset")
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n")


def _filename(part: MIMEPart, collector: _Collector) -> str | None:
    try:
        filename = part.get_filename()
        return None if filename is None else scrub(filename)
    except Exception:
        collector.add("invalid-filename")
        return None


def _disposition(part: MIMEPart) -> str | None:
    try:
        return part.get_content_disposition()
    except Exception:
        return None


def _leaf_data(part: MIMEPart, collector: _Collector) -> bytes | None:
    """The decoded payload of a leaf part, or None when too large or undecodable."""
    try:
        encoded = part.get_payload()
        if isinstance(encoded, str) and len(encoded) > MAXIMUM_PART_BYTES:
            collector.add("part-too-large")
            return None
        decoded = part.get_payload(decode=True)
        return decoded if isinstance(decoded, bytes) else None
    except Exception:
        collector.add("part-undecodable")
        return None


def _embedded_message_data(part: MIMEPart, collector: _Collector) -> bytes:
    try:
        payload = part.get_payload()
        if isinstance(payload, list):
            return b"".join(item.as_bytes() for item in payload if isinstance(item, Message))
    except Exception:
        collector.add("embedded-message-unreadable")
    return b""


def _content_type(part: MIMEPart) -> str:
    try:
        return scrub(part.get_content_type())
    except Exception:
        return "application/octet-stream"


def _extract(root: MIMEPart, collector: _Collector, with_body: bool) -> ParsedMessage:
    for defect in root.defects:
        collector.add(_defect_name(defect))

    raw_headers: list[tuple[str, str]] = []
    for name in _ADDRESS_HEADERS:
        text = _raw_header(root, name, collector)
        if text is not None:
            raw_headers.append((name, text))

    in_reply_to_text = _header_text(root, "In-Reply-To", collector)
    in_reply_to_ids = _ID_PATTERN.findall(in_reply_to_text or "")
    references_text = _header_text(root, "References", collector)

    text_body: str | None = None
    html_body: str | None = None
    attachments: list[Attachment] = []
    if with_body:
        text_body, html_body, attachments = _extract_body(root, collector)

    message_id_text = _header_text(root, "Message-ID", collector)
    message_id = scrub(message_id_text or "").strip() or None
    return ParsedMessage(
        subject=_header_text(root, "Subject", collector) or "",
        from_addresses=_addresses(root, "From", collector),
        to_addresses=_addresses(root, "To", collector),
        cc_addresses=_addresses(root, "Cc", collector),
        reply_to_addresses=_addresses(root, "Reply-To", collector),
        raw_address_headers=tuple(raw_headers),
        date=_date(root, collector),
        message_id=message_id,
        in_reply_to=(in_reply_to_ids[0] if in_reply_to_ids else (in_reply_to_text or "").strip() or None),
        references=tuple(_ID_PATTERN.findall(references_text or "")),
        text_body=text_body,
        html_body=html_body,
        attachments=tuple(attachments),
        defects=tuple(collector.defects),
    )


def _extract_body(root: MIMEPart, collector: _Collector) -> tuple[str | None, str | None, list[Attachment]]:
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[Attachment] = []
    stack: list[tuple[MIMEPart, int]] = [(root, 1)]
    parts_seen = 0
    while stack:
        part, depth = stack.pop()
        parts_seen += 1
        if parts_seen > MAXIMUM_PARTS:
            collector.add("too-many-parts")
            break
        content_type = _content_type(part)
        for defect in part.defects:
            collector.add(_defect_name(defect))
        if content_type.startswith("message/") and part.is_multipart():
            attachments.append(
                Attachment(
                    _filename(part, collector),
                    content_type,
                    None,
                    False,
                    _embedded_message_data(part, collector),
                )
            )
            continue
        if part.is_multipart():
            if depth >= MAXIMUM_MIME_DEPTH:
                collector.add("nesting-too-deep")
                continue
            children = list(part.iter_parts())
            stack.extend((child, depth + 1) for child in reversed(children))
            continue
        data = _leaf_data(part, collector)
        if data is None:
            continue
        filename = _filename(part, collector)
        disposition = _disposition(part)
        is_body_candidate = disposition != "attachment" and filename is None
        if is_body_candidate and content_type == "text/plain" and text_body is None:
            text_body = _decode_text(part, data, collector)
        elif is_body_candidate and content_type == "text/html" and html_body is None:
            html_body = _decode_text(part, data, collector)
        else:
            content_id = part.get("Content-ID")
            attachments.append(
                Attachment(
                    filename,
                    content_type,
                    scrub(str(content_id)).strip() if content_id else None,
                    disposition == "inline" or is_body_candidate,
                    data,
                )
            )
    return text_body, html_body, attachments
