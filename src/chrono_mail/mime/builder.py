import re
from collections.abc import Mapping
from email import message_from_bytes, policy
from email.headerregistry import Address as HeaderAddress
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from chrono_mail.core import MessageFormatError
from chrono_mail.mime.model import Address, Attachment, OutgoingMessage

# --- Version ------------------------------------------------------------
__version__ = "builder.py 4.2.428"

# --- Version history ----------------------------------------------------
# v4.2.428: as_it_goes_out: the stored message as it goes to the server, without its Bcc and without the program's own X-Chrono-Mail notes; also
#           for a message with plain LF line ends
# v1.1.206: with_extra_headers: set or remove X-Chrono-Mail headers of a stored message, every other byte kept (a repeating message records its progress in itself, D-176)
# v0.0.86: Bcc can be taken out of a stored message just before it is sent; an attached e-mail (message/rfc822) is added as it is, not in base64
# v0.0.16: drafts keep Bcc and the program's own X-Chrono-Mail headers
# v0.0.5: Initial message builder


_EXTRA_NAME: re.Pattern[str] = re.compile(r"X-Chrono-Mail-[A-Za-z0-9-]{1,40}")
_MAXIMUM_EXTRA_VALUE: int = 500


def _check_extra_header(name: str, value: str) -> None:
    """Only the program's own notes may be added, and only as plain printable text."""
    if _EXTRA_NAME.fullmatch(name) is None:
        raise MessageFormatError("Only X-Chrono-Mail headers can be added")
    if len(value) > _MAXIMUM_EXTRA_VALUE or not value.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MessageFormatError("A header value must be short printable ASCII text")


def _header_address(address: Address) -> HeaderAddress:
    if not address.address.isascii() or not address.address.strip():
        raise MessageFormatError("Address must be non-empty ASCII (internationalised addresses are not supported)")
    return HeaderAddress(display_name=address.name, addr_spec=address.address)


def _split_content_type(content_type: str) -> tuple[str, str]:
    main_type, separator, subtype = content_type.partition("/")
    if not separator or not main_type.strip() or not subtype.strip():
        return "application", "octet-stream"
    return main_type.strip(), subtype.strip()


def _add_attachment(message: EmailMessage, attachment: Attachment) -> None:
    main_type, subtype = _split_content_type(attachment.content_type)
    if (main_type.lower(), subtype.lower()) == ("message", "rfc822"):  # an e-mail as an attachment may not be base64 (RFC 2046 section 5.2.1)
        attached = message_from_bytes(attachment.data, policy=policy.default)
        message.add_attachment(attached, filename=attachment.filename)
        return
    if attachment.inline:
        message.add_attachment(
            attachment.data,
            maintype=main_type,
            subtype=subtype,
            disposition="inline",
            filename=attachment.filename,
            cid=attachment.content_id,
        )
    else:
        message.add_attachment(
            attachment.data,
            maintype=main_type,
            subtype=subtype,
            filename=attachment.filename,
            cid=attachment.content_id,
        )


def as_it_goes_out(raw: bytes) -> bytes:
    """The stored message as it goes to the server: without its Bcc header and without the program's own X-Chrono-Mail notes (a draft moved
    into the Outbox still carries them; they are this computer's only). Every other byte stays as it is."""
    return _without_headers(raw, (b"bcc:", b"x-chrono-mail-"))


def without_bcc(raw: bytes) -> bytes:
    """The stored message without its Bcc header (and the lines that continue it).
    Only the header block is looked at; the body and everything else stay exactly as they are."""
    return _without_headers(raw, (b"bcc:",))


def _without_headers(raw: bytes, names: tuple[bytes, ...]) -> bytes:
    """The message without these headers (and the lines that continue them). The header block ends at the first empty line, whatever the
    line ends (CRLF, or LF as some imported messages have); a message of headers only is all header. Every other byte stays as it is."""
    kept: list[bytes] = []
    position = 0
    skipping = False
    for line in raw.split(b"\n"):
        line += b"\n" if position + len(line) < len(raw) else b""  # its own line end back (only the last line may have none)
        if line in (b"\n", b"\r\n"):
            return b"".join(kept) + raw[position:]  # the body, from its empty line on, exactly as it was
        position += len(line)
        if line[:1] in (b" ", b"\t"):
            if not skipping:
                kept.append(line)
            continue
        skipping = line.lower().startswith(names)
        if not skipping:
            kept.append(line)
    return b"".join(kept)


def with_extra_headers(raw: bytes, changes: Mapping[str, str | None]) -> bytes:
    """The same message with these X-Chrono-Mail headers set (a value) or removed (None); every other byte stays as it was.
    For the program's own messages that wait to be sent (a repeating message records its progress in itself)."""
    wanted = {name.lower(): value for name, value in changes.items()}
    for name, value in changes.items():
        if value is not None:
            _check_extra_header(name, value)
    end = raw.find(b"\r\n\r\n")
    newline = b"\r\n"
    if end < 0 or (0 <= raw.find(b"\n\n") < end):
        end, newline = raw.find(b"\n\n"), b"\n"
    if end < 0:
        raise MessageFormatError("The message has no end to its headers")
    head, body = raw[:end], raw[end:]
    kept: list[bytes] = []
    dropping = False
    for line in head.split(newline):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        name = line.split(b":", 1)[0].strip().decode("ascii", "replace").lower()
        dropping = name in wanted
        if not dropping:
            kept.append(line)
    for name, value in changes.items():
        if value is not None:
            kept.append(f"{name}: {value}".encode("ascii"))
    return newline.join(kept) + body


def envelope_recipients(outgoing: OutgoingMessage) -> tuple[str, ...]:
    """Every recipient address for the SMTP envelope (To, Cc and Bcc), without duplicates."""
    seen: set[str] = set()
    recipients: list[str] = []
    for address in (*outgoing.to_addresses, *outgoing.cc_addresses, *outgoing.bcc_addresses):
        key = address.address.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(address.address)
    return tuple(recipients)


def build_message(outgoing: OutgoingMessage, draft: bool = False) -> bytes:
    """Build the raw message. With draft (a draft, or the copy kept in the Outbox and Sent) the Bcc header is written,
    so that no information is lost; what goes to the server never has it (see without_bcc and envelope_recipients)."""
    try:
        message = EmailMessage()
        sender = _header_address(outgoing.from_address)
        message["From"] = sender
        if outgoing.to_addresses:
            message["To"] = [_header_address(address) for address in outgoing.to_addresses]
        if outgoing.cc_addresses:
            message["Cc"] = [_header_address(address) for address in outgoing.cc_addresses]
        if draft and outgoing.bcc_addresses:
            message["Bcc"] = [_header_address(address) for address in outgoing.bcc_addresses]
        for name, value in outgoing.extra_headers:
            _check_extra_header(name, value)
            message[name] = value
        message["Subject"] = outgoing.subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=sender.domain or None)
        if outgoing.in_reply_to:
            message["In-Reply-To"] = outgoing.in_reply_to
        if outgoing.references:
            message["References"] = " ".join(outgoing.references)
        message.set_content(outgoing.text_body)
        if outgoing.html_body is not None:
            message.add_alternative(outgoing.html_body, subtype="html")
        for attachment in outgoing.attachments:
            _add_attachment(message, attachment)
        return message.as_bytes(policy=policy.SMTP)
    except MessageFormatError:
        raise
    except Exception as error:
        # Only the error type is reported: the message may contain private text (AGENTS.md section 4.6).
        raise MessageFormatError(f"Message cannot be built: {type(error).__name__}") from None
