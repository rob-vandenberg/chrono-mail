import re
from email import policy
from email.headerregistry import Address as HeaderAddress
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from chrono_mail.core import MessageFormatError
from chrono_mail.mime.model import Address, Attachment, OutgoingMessage

# --- Version ------------------------------------------------------------
__version__ = "builder.py 0.0.16"

# --- Version history ----------------------------------------------------
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
    """Build the raw message. Bcc is never written into a message that is sent (see envelope_recipients);
    a draft keeps it, so that editing can go on where it stopped."""
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
