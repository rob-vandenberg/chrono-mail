import ssl
from collections.abc import Callable
from dataclasses import dataclass

from chrono_mail.core import ChronoMailError, MessageFlag, MessageRef, ProtocolError, ServerSettings
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import OutgoingMessage, build_message, envelope_recipients, without_bcc
from chrono_mail.protocols.smtp import Refusal, SmtpLimits, send_message
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "send.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: The copy in the Outbox and in Sent keeps the Bcc recipients (only what goes to the server is without them); send_stored sends a message that waits in the Outbox
# v0.0.35: Send later and snooze
# v0.0.34: before_send hook
# v0.0.10: Initial send and file

Checkpoint = Callable[[str], None]


def _no_checkpoint(name: str) -> None:
    """Crash points for tests."""


@dataclass(frozen=True, slots=True)
class SendOutcome:
    sent: bool
    reference: MessageRef  # where the message is now: in Sent after a send, in the Outbox otherwise
    refused: tuple[Refusal, ...] = ()
    error: str | None = None  # why it was not sent (the message is still in the Outbox)
    warning: str | None = None  # it was sent, but could not be filed away completely


def send_and_file(
    store: Store,
    index: Index | None,
    settings: ServerSettings,
    password: str,
    outgoing: OutgoingMessage,
    outbox: tuple[str, ...],
    sent: tuple[str, ...],
    limits: SmtpLimits = SmtpLimits(),
    ssl_context: ssl.SSLContext | None = None,
    checkpoint: Checkpoint = _no_checkpoint,
    before_send: Callable[[], None] = lambda: None,
) -> SendOutcome:
    """Build the message, keep a verified copy in the Outbox, send it, then move it to Sent. The stored copy keeps
    the Bcc recipients; what goes to the server is the same message without them.

    A message that fails to send stays in the Outbox: it is never lost and never sent again by
    itself (a crash after the server accepted it would otherwise send it twice). MessageFormatError
    is raised, before anything is stored, for a message that cannot be built.
    """
    raw = build_message(outgoing, draft=True)  # the Bcc header stays in our copy (Outbox, then Sent)
    recipients = envelope_recipients(outgoing)
    reference = store.deliver(outbox, raw, flags=frozenset({MessageFlag.SEEN}), verify=True)
    path = f"cur/{reference.store_id}!2,S"
    _index(index, reference, path, raw)
    checkpoint("send:after-outbox")
    before_send()  # for example: remove the scheduled message this one replaces, now that a safe copy is in the Outbox
    try:
        result = send_message(settings, password, outgoing.from_address.address, recipients, without_bcc(raw), limits, ssl_context)
    except ProtocolError as error:
        return SendOutcome(False, reference, error=str(error))
    checkpoint("send:after-smtp")
    return _file_as_sent(store, index, reference, path, sent, result.refused, checkpoint)


def send_stored(
    store: Store,
    index: Index | None,
    settings: ServerSettings,
    password: str,
    reference: MessageRef,
    path: str | None,
    raw: bytes,
    sender: str,
    recipients: tuple[str, ...],
    sent: tuple[str, ...],
    limits: SmtpLimits = SmtpLimits(),
    ssl_context: ssl.SSLContext | None = None,
    checkpoint: Checkpoint = _no_checkpoint,
) -> SendOutcome:
    """Send a message that waits in the Outbox, exactly as it is stored but without its Bcc header, then move it to Sent.
    It is not stored again: until the server has accepted it, the copy in the Outbox is the only one and it stays there."""
    try:
        result = send_message(settings, password, sender, recipients, without_bcc(raw), limits, ssl_context)
    except ProtocolError as error:
        return SendOutcome(False, reference, error=str(error))
    checkpoint("send:after-smtp")
    return _file_as_sent(store, index, reference, path, sent, result.refused, checkpoint)


def _file_as_sent(store: Store, index: Index | None, reference: MessageRef, path: str | None, sent: tuple[str, ...], refused: tuple[Refusal, ...], checkpoint: Checkpoint) -> SendOutcome:
    """Move a message the server accepted from the Outbox to Sent; if that fails it was still sent, and stays in the Outbox."""
    try:
        moved = store.move(reference, sent, hint=path)
        if index is not None:
            index.move(moved.store_id, sent, path if path is not None else store.locate(moved))
    except ChronoMailError as error:
        return SendOutcome(True, reference, refused, warning=f"it was sent but could not be moved to Sent: {error}")
    checkpoint("send:after-move")
    return SendOutcome(True, moved, refused)


def _index(index: Index | None, reference: MessageRef, path: str, raw: bytes) -> None:
    if index is None:
        return
    try:
        index.add(entry_from_raw(reference, path, frozenset({MessageFlag.SEEN}), len(raw), raw))
    except ChronoMailError:
        pass  # the message is safe in the store; reconcile_index() adds it later
