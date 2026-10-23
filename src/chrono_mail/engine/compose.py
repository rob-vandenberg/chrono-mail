import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from chrono_mail.index import html_to_text
from chrono_mail.mime import Address, ParsedMessage

# --- Version ------------------------------------------------------------
__version__ = "compose.py 2.4.331"

# --- Version history ----------------------------------------------------
# v2.4.331: suggest_filename takes out the marks that turn text around and puts "_" before a Windows device name (W11)
# v0.0.86: Forward as attachment: the original goes along as an .eml file
# v0.0.42: Settings window with pages for everything
# v0.0.13: Initial reply and forward drafts

ComposeKind = Literal["reply", "reply_all", "forward", "forward_attachment"]
_REPLY_PREFIX: re.Pattern[str] = re.compile(r"^\s*(?:re|aw|antw|sv|vs)\s*:\s*", re.IGNORECASE)
_FORWARD_PREFIX: re.Pattern[str] = re.compile(r"^\s*(?:fwd?|wg|tr|vs)\s*:\s*", re.IGNORECASE)
_UNSAFE_NAME: re.Pattern[str] = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
_DIRECTION_MARKS: re.Pattern[str] = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")  # they turn "gpj.exe" into what reads "exe.jpg"
_DEVICE_NAME: re.Pattern[str] = re.compile(r"con|prn|aux|nul|com[1-9]|lpt[1-9]", re.IGNORECASE)  # Windows devices, whatever the extension


def suggest_filename(filename: str | None, content_type: str, position: int) -> str:
    """A safe file name for saving an attachment: no path, no control or reserved characters, no marks that turn the text around (the
    name reads as it is), never a Windows device name (CON, NUL, COM1…: the data went to the device, W11), not too long."""
    bare = _DIRECTION_MARKS.sub("", (filename or "").replace("\\", "/").rsplit("/", 1)[-1])
    cleaned = _UNSAFE_NAME.sub("_", bare).strip(" .")[:150]
    if _DEVICE_NAME.fullmatch(cleaned.split(".", 1)[0].rstrip(" ")):  # the part before the first dot decides, spaces after it ignored
        cleaned = f"_{cleaned}"
    if cleaned:
        return cleaned
    extension = mimetypes.guess_extension(content_type) or ".bin"
    return f"attachment-{position + 1}{extension}"


@dataclass(frozen=True, slots=True)
class Draft:
    kind: ComposeKind
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    in_reply_to: str | None
    references: tuple[str, ...]
    attachment_names: tuple[str, ...]  # what a forward would carry along


def _format(address: Address) -> str:
    return f"{address.name} <{address.address}>" if address.name else address.address


def _unique(addresses: list[Address], exclude: set[str]) -> tuple[str, ...]:
    seen = set(exclude)
    found: list[str] = []
    for address in addresses:
        key = address.address.lower()
        if key not in seen:
            seen.add(key)
            found.append(_format(address))
    return tuple(found)


def _text_of(message: ParsedMessage) -> str:
    if message.text_body:
        return message.text_body
    return html_to_text(message.html_body, keep_lines=True) if message.html_body else ""


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.rstrip("\n").splitlines())


def _when(moment: datetime | None) -> str:
    return moment.strftime("%a, %d %b %Y %H:%M") if moment is not None else "an unknown date"


def subject_for(subject: str, kind: ComposeKind) -> str:
    """Re: or Fwd: in front of the subject, never twice."""
    if kind in ("forward", "forward_attachment"):
        return subject if _FORWARD_PREFIX.match(subject) else f"Fwd: {subject}"
    return subject if _REPLY_PREFIX.match(subject) else f"Re: {subject}"


def message_filename(subject: str) -> str:
    """The file name of a message sent along as an attachment: its subject, made safe, with .eml."""
    cleaned = _UNSAFE_NAME.sub("_", subject).strip(" .")[:100]
    return f"{cleaned or 'message'}.eml"


def build_draft(message: ParsedMessage, kind: ComposeKind, own_address: str, quote: str = "above") -> Draft:
    """The starting point of a reply, a reply to all, or a forward of this message."""
    own = own_address.lower()
    sender = message.from_addresses[0] if message.from_addresses else None
    sender_text = _format(sender) if sender is not None else "an unknown sender"
    in_reply_to = message.message_id
    references = (*message.references, *((in_reply_to,) if in_reply_to else ()))
    if kind == "forward_attachment":  # no quoted text: the whole original is attached
        return Draft("forward_attachment", (), (), subject_for(message.subject, kind), "\n\n", None, (), (message_filename(message.subject),))
    if kind == "forward":
        header = [
            "---------- Forwarded message ----------",
            f"From: {sender_text}",
            f"Date: {_when(message.date)}",
            f"Subject: {message.subject}",
            f"To: {', '.join(_format(a) for a in message.to_addresses)}",
        ]
        if message.cc_addresses:
            header.append(f"Cc: {', '.join(_format(a) for a in message.cc_addresses)}")
        return Draft(
            "forward", (), (), subject_for(message.subject, kind), "\n\n" + "\n".join(header) + "\n\n" + _text_of(message),
            None, (), tuple(suggest_filename(attachment.filename, attachment.content_type, position) for position, attachment in enumerate(message.attachments) if not attachment.inline),
        )
    from_me = sender is not None and sender.address.lower() == own
    if from_me and message.to_addresses:  # answering a message of mine goes to the people I wrote to
        main = list(message.to_addresses)
    else:
        main = list(message.reply_to_addresses or message.from_addresses)
    to = _unique(main, {own})
    cc: tuple[str, ...] = ()
    if kind == "reply_all":
        cc = _unique([*message.to_addresses, *message.cc_addresses], {own, *(a.address.lower() for a in main)})
    intro = f"On {_when(message.date)}, {sender_text} wrote:"
    if quote == "none":
        body = "\n\n"
    elif quote == "below":
        body = f"{intro}\n{_quote(_text_of(message))}\n\n\n"  # the answer goes under the quote
    else:
        body = f"\n\n{intro}\n{_quote(_text_of(message))}\n"
    return Draft(kind, to, cc, subject_for(message.subject, kind), body, in_reply_to, references, ())
