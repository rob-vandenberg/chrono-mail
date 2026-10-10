from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index.model import PREVIEW_CHARACTERS, UNREADABLE_SUBJECT, IndexEntry
from chrono_mail.index.text import clean_preview, searchable_body
from chrono_mail.mime import Address, parse_message

# --- Version ------------------------------------------------------------
__version__ = "entry.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: the preview is cleaned of web addresses and decoration
# v0.0.13: preview
# v0.0.8: Initial index entries

_ALLOWED_CONTROL: frozenset[str] = frozenset("\t\n")


def _clean(text: str) -> str:
    """Remove characters that make no sense in a list (NUL and other controls)."""
    return "".join(character for character in text if character >= " " or character in _ALLOWED_CONTROL)


def _display(address: Address) -> str:
    return f"{address.name} <{address.address}>" if address.name else address.address


def entry_from_raw(
    reference: MessageRef, path: str, flags: frozenset[MessageFlag], size: int, raw: bytes
) -> IndexEntry:
    """Build the index entry for a raw message. Never raises: a bad message still gets an entry."""
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
        date=message.date,
        size=size,
        subject=_clean(message.subject),
        sender=_clean(sender),
        recipients=_clean(recipients),
        message_id=own_id,
        thread_key=thread_key,
        has_attachments=any(not attachment.inline for attachment in message.attachments),
        body=body,
        preview=clean_preview(body, PREVIEW_CHARACTERS),
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
    )
