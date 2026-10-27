from datetime import UTC, datetime

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index.model import PREVIEW_CHARACTERS, UNKNOWN_SEND_TIME, UNREADABLE_SUBJECT, IndexEntry
from chrono_mail.index.text import clean_preview, searchable_body
from chrono_mail.mime import Address, ParsedMessage, parse_message, send_time

# --- Version ------------------------------------------------------------
__version__ = "entry.py 4.1.384"

# --- Version history ----------------------------------------------------
# v4.1.384: no other mail program named in a docstring (OR-10)
# v2.0.280: sent: the moment the message carries itself (its Date line), for the Sent column (B-16)
# v1.9.276: date is when the mail server received the message (D-226): the topmost Received line, else Delivery-date, else Date, else when it was stored here
# v1.1.196: the moment a scheduled message is to be sent; a message that cannot be read gets 0 (not known: looked at every time)
# v0.0.23: the addresses of a message
# v0.0.14: the preview is cleaned of web addresses and decoration
# v0.0.13: preview
# v0.0.8: Initial index entries

_ALLOWED_CONTROL: frozenset[str] = frozenset("\t\n")


def _clean(text: str) -> str:
    """Remove characters that make no sense in a list (NUL and other controls)."""
    return "".join(character for character in text if character >= " " or character in _ALLOWED_CONTROL)


def _display(address: Address) -> str:
    return f"{address.name} <{address.address}>" if address.name else address.address


MAXIMUM_PEOPLE: int = 60
MAXIMUM_ADDRESS_CHARACTERS: int = 254
MAXIMUM_NAME_CHARACTERS: int = 200


def _people(message: ParsedMessage) -> tuple[tuple[str, str, bool], ...]:
    """The addresses of a message worth remembering: From (False) and To and Cc (True). Anything odd is skipped."""
    found: list[tuple[str, str, bool]] = []
    for addresses, recipient in ((message.from_addresses, False), (message.to_addresses, True), (message.cc_addresses, True)):
        for address in addresses:
            text = _clean(address.address).strip()
            if not text or len(text) > MAXIMUM_ADDRESS_CHARACTERS or text.count("@") != 1 or text.startswith("@") or text.endswith("@") or any(c.isspace() or c in '<>",;' for c in text):
                continue
            found.append((_clean(address.name).strip()[:MAXIMUM_NAME_CHARACTERS], text, recipient))
            if len(found) >= MAXIMUM_PEOPLE:
                return tuple(found)
    return tuple(found)


def received_moment(message: ParsedMessage, stored: datetime | None) -> datetime | None:
    """The moment the Received column shows (D-226, as a widely used open-source mail program does): when the mail server received the message (the topmost
    Received line, else Delivery-date); for a message no server received (own mail) the sender's Date; else when it was stored here."""
    return message.received or message.date or stored


def entry_from_raw(
    reference: MessageRef, path: str, flags: frozenset[MessageFlag], size: int, raw: bytes, stored: datetime | None = None
) -> IndexEntry:
    """Build the index entry for a raw message. Never raises: a bad message still gets an entry. stored: when its file was written
    here (None: now, as it is being stored), the last resort of the Received column."""
    message = parse_message(raw)
    body = searchable_body(message.text_body, message.html_body)
    recipients = ", ".join(_display(address) for address in (*message.to_addresses, *message.cc_addresses))
    sender = _display(message.from_addresses[0]) if message.from_addresses else ""
    own_id = message.message_id or None
    if message.references:
        thread_key = message.references[0]
    elif message.in_reply_to:
        thread_key = message.in_reply_to
    elif own_id:
        thread_key = own_id
    else:
        thread_key = f"id:{reference.store_id}"
    return IndexEntry(
        store_id=reference.store_id,
        folder=reference.folder_path,
        path=path,
        flags=flags,
        date=received_moment(message, stored if stored is not None else datetime.now(UTC)),
        size=size,
        subject=_clean(message.subject),
        sender=_clean(sender),
        recipients=_clean(recipients),
        message_id=own_id,
        thread_key=thread_key,
        has_attachments=any(not attachment.inline for attachment in message.attachments),
        body=body,
        preview=clean_preview(body, PREVIEW_CHARACTERS),
        people=_people(message),
        send_at=send_time(message),
        sent=message.date,
    )


def unreadable_entry(reference: MessageRef, path: str, flags: frozenset[MessageFlag], size: int) -> IndexEntry:
    """A placeholder for a message that cannot be read, so it stays visible instead of vanishing."""
    return IndexEntry(
        store_id=reference.store_id,
        folder=reference.folder_path,
        path=path,
        flags=flags,
        date=None,
        size=size,
        subject=UNREADABLE_SUBJECT,
        sender="",
        recipients="",
        message_id=None,
        thread_key=f"id:{reference.store_id}",
        has_attachments=False,
        body="",
        send_at=UNKNOWN_SEND_TIME,  # it may be a scheduled message: the minute check looks at it and says it cannot be read
    )
