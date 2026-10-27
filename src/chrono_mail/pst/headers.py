import email.policy
import re
from datetime import datetime
from email.header import Header
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

from chrono_mail.core.flags import MessageFlag
from chrono_mail.pst.names import PS_PUBLIC_STRINGS
from chrono_mail.pst.properties import Message, Properties

# --- Version ------------------------------------------------------------
__version__ = "headers.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the property tags of a message, its flags and categories, and its header block (kept, completed or made)

POLICY = email.policy.SMTP.clone(cte_type="7bit")
UTF8_POLICY = POLICY.clone(utf8=True)  # for a header that only raw UTF-8 can carry (an internationalised address, RFC 6532)

# property tags (MS-OXPROPS)
MESSAGE_CLASS = 0x001A
SUBJECT = 0x0037
TRANSPORT_HEADERS = 0x007D
CLIENT_SUBMIT_TIME = 0x0039
DELIVERY_TIME = 0x0E06
CREATION_TIME = 0x3007
MESSAGE_FLAGS = 0x0E07
FLAG_STATUS = 0x1090
LAST_VERB = 0x1081
INTERNET_MESSAGE_ID = 0x1035
IN_REPLY_TO = 0x1042
REFERENCES = 0x1039
BODY = 0x1000
HTML = 0x1013
RTF_COMPRESSED = 0x1009
INTERNET_CPID = 0x3FDE
IMPORTANCE = 0x0017
DISPLAY_NAME = 0x3001
EMAIL_ADDRESS = 0x3003
ADDRESS_TYPE = 0x3002
SMTP_ADDRESS = 0x39FE
RECIPIENT_TYPE = 0x0C15
SENDER_NAME = 0x0C1A
SENDER_ADDRESS = 0x0C1F
SENDER_ADDRESS_TYPE = 0x0C1E
SENDER_SMTP = 0x5D01
REPRESENTING_NAME = 0x0042
REPRESENTING_ADDRESS = 0x0065
REPRESENTING_ADDRESS_TYPE = 0x0064
REPRESENTING_SMTP = 0x5D02
ATTACH_METHOD = 0x3705
ATTACH_DATA = 0x3701
ATTACH_LONG_FILENAME = 0x3707
ATTACH_FILENAME = 0x3704
ATTACH_DISPLAY_NAME = 0x3001
ATTACH_MIME_TAG = 0x370E
ATTACH_CONTENT_ID = 0x3712

BY_VALUE = 1
EMBEDDED = 5
RECIPIENT_KIND_MASK = 0x0F  # the type's high bits are flags (MAPI_SUBMITTED 0x80000000, MAPI_P1 0x10000000)
_READ = 0x0001
_UNSENT = 0x0008
_REPLY_VERBS = (102, 103)
_DROPPED_HEADERS = frozenset({"mime-version"})  # and every Content-* header: they described the old body
_COMPLETED_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")
_ADDRESS = re.compile(r"^[^@\s<>]+@[^@\s<>]+$")
_ADDRESS_IN_TEXT = re.compile(r"[^\s<>,;:\"]+@[^\s<>,;:\"]+")
_ADDRESS_HEADERS = frozenset({"from", "to", "cc", "bcc", "sender", "reply-to"})


def flags(props: Properties) -> frozenset[MessageFlag]:
    found: set[MessageFlag] = set()
    message_flags = props.integer(MESSAGE_FLAGS) or 0
    if message_flags & _READ:
        found.add(MessageFlag.SEEN)
    if message_flags & _UNSENT:
        found.add(MessageFlag.DRAFT)
    if props.integer(FLAG_STATUS) == 2:
        found.add(MessageFlag.FLAGGED)
    if props.integer(LAST_VERB) in _REPLY_VERBS:
        found.add(MessageFlag.REPLIED)
    return frozenset(found)


def categories(props: Properties) -> tuple[str, ...]:
    """The item's categories: the named property Keywords of the public strings."""
    tag = props.named_string(PS_PUBLIC_STRINGS, "Keywords")
    seen: list[str] = []
    for value in props.strings(tag):
        value = value.strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def one_line(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())


def recipient_kind(row: Properties) -> int:
    """1 To, 2 Cc, 3 Bcc (the flags in the high bits left out)."""
    return (row.integer(RECIPIENT_TYPE) or 1) & RECIPIENT_KIND_MASK


def smtp_address(props: Properties, smtp_tag: int, address_tag: int, type_tag: int) -> str:
    """An internet address: the SMTP one when kept, else the address when it is one (not an internal directory name)."""
    smtp = props.text(smtp_tag).strip()
    if _ADDRESS.match(smtp):
        return smtp
    address = props.text(address_tag).strip()
    return address if props.text(type_tag).strip().upper() in ("SMTP", "") and _ADDRESS.match(address) else ""


def person(name: str, address: str) -> str:
    """A header value for one person; with no internet address, the name alone as an empty group, so it still shows."""
    name = one_line(name)
    if address:
        if not name or name == address:
            return address
        try:
            return formataddr((name, address), charset="utf-8")
        except UnicodeError:  # an internationalised address (RFC 6532): written as it is, the header then goes out as UTF-8
            return f"{_display(name)} <{address}>"
    return f"{_display(name)}:;" if name else ""


def _display(name: str) -> str:
    if any(ord(character) > 127 for character in name):
        return Header(name, "utf-8").encode()
    return '"' + name.replace('"', "").replace("\\", "") + '"'


def recipients(message: Message) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"To": [], "Cc": [], "Bcc": []}
    for row in message.recipients:
        kind = {1: "To", 2: "Cc", 3: "Bcc"}.get(recipient_kind(row))
        if kind is None:
            continue
        value = person(row.text(DISPLAY_NAME), smtp_address(row, SMTP_ADDRESS, EMAIL_ADDRESS, ADDRESS_TYPE))
        if value:
            found[kind].append(value)
    return found


def sender(props: Properties) -> str:
    representing = person(props.text(REPRESENTING_NAME), smtp_address(props, REPRESENTING_SMTP, REPRESENTING_ADDRESS, REPRESENTING_ADDRESS_TYPE))
    return representing or person(props.text(SENDER_NAME), smtp_address(props, SENDER_SMTP, SENDER_ADDRESS, SENDER_ADDRESS_TYPE))


def _date(props: Properties) -> datetime | None:
    return props.time(CLIENT_SUBMIT_TIME) or props.time(DELIVERY_TIME) or props.time(CREATION_TIME)


def kept_headers(text: str) -> bytes:
    """The headers the message came with, without the old description of its body (the body is rebuilt)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    dropping = False
    for line in lines:
        if not line.strip():
            if kept:
                break  # the end of the headers (anything after a blank line is not a header)
            continue
        if line[0] in " \t":
            if not dropping and kept:
                kept.append(line)
            continue
        name, colon, _ = line.partition(":")
        if not colon or not name.strip() or " " in name.strip():
            dropping = True
            continue
        dropping = name.strip().lower() in _DROPPED_HEADERS or name.strip().lower().startswith("content-")
        if not dropping:
            kept.append(line)
    return "".join(line + "\r\n" for line in kept).encode("utf-8", errors="replace")


def header_line(name: str, value: str) -> bytes:
    """One header, folded and encoded on its own, so a value one way cannot carry cannot cost the others: plain ASCII with encoded words
    where it can, raw UTF-8 (RFC 6532) where only that can carry it (an internationalised address); empty when neither can."""
    value = one_line(value)
    if not value:
        return b""
    international = name.lower() in _ADDRESS_HEADERS and any(not address.isascii() for address in _ADDRESS_IN_TEXT.findall(value))
    for policy in (UTF8_POLICY,) if international else (POLICY, UTF8_POLICY):  # an address is never put in encoded words
        try:
            head = EmailMessage(policy=policy)
            head[name] = value
            return head.as_bytes(policy=policy).rstrip(b"\r\n") + b"\r\n"
        except Exception:  # noqa: BLE001 - the standard library refuses damaged values in its own ways; the next way is tried, then the header is left out
            continue
    return b""


def made_headers(message: Message) -> list[tuple[str, bytes]]:
    """The headers made from the message's properties, in the usual order."""
    props = message.props
    values: list[tuple[str, str]] = [("From", sender(props))]
    values += [(kind, ", ".join(people)) for kind, people in recipients(message).items()]
    values.append(("Subject", props.text(SUBJECT)))
    moment = _date(props)
    values.append(("Date", format_datetime(moment) if moment is not None else ""))
    values += [("Message-ID", props.text(INTERNET_MESSAGE_ID)), ("In-Reply-To", props.text(IN_REPLY_TO)), ("References", props.text(REFERENCES))]
    importance = props.integer(IMPORTANCE)
    if importance in (0, 2):
        values.append(("Importance", "low" if importance == 0 else "high"))
    return [(name, line) for name, value in values for line in [header_line(name, value)] if line]


def _names_in(block: bytes) -> set[str]:
    names: set[str] = set()
    for line in block.split(b"\r\n"):
        if line and line[:1] not in (b" ", b"\t"):
            names.add(line.split(b":", 1)[0].strip().decode("ascii", errors="replace").lower())
    return names


def header_block(message: Message) -> bytes:
    """The header block: the headers the message came with, completed with the usual ones they lack (a message kept with only its
    Received lines still shows who, what and when); made from the properties when none were kept."""
    transport = message.props.text(TRANSPORT_HEADERS)
    kept = kept_headers(transport) if transport.strip() else b""
    made = made_headers(message)
    if not kept:
        return b"".join(line for _, line in made)
    present = _names_in(kept)
    missing = [line for name, line in made if name in _COMPLETED_HEADERS and name.lower() not in present]
    return kept + b"".join(missing)
