import ssl
from collections.abc import Callable
from dataclasses import dataclass

from chrono_mail.core import ChronoMailError, MessageFlag, MessageRef, ProtocolError, ServerSettings
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import OutgoingMessage, build_message, envelope_recipients
from chrono_mail.protocols.smtp import Refusal, SmtpLimits, send_message
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "send.py 0.0.35"

# --- Version history ----------------------------------------------------
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
    """Build the message, keep a verified copy in the Outbox, send it, then move it to Sent.

    A message that fails to send stays in the Outbox: it is never lost and never sent again by
    itself (a crash after the server accepted it would otherwise send it twice). MessageFormatError
    is raised, before anything is stored, for a message that cannot be built.
    """
    raw = build_message(outgoing)
    recipients = envelope_recipients(outgoing)
    reference = store.deliver(outbox, raw, flags=frozenset({MessageFlag.SEEN}), verify=True)
    path = f"cur/{reference.store_id}!2,S"
    _index(index, reference, path, raw)
    checkpoint("send:after-outbox")
    before_send()  # for example: remove the scheduled message this one replaces, now that a safe copy is in the Outbox
    try:
        result = send_message(settings, password, outgoing.from_address.address, recipients, raw, limits, ssl_context)
    except ProtocolError as error:
        return SendOutcome(False, reference, error=str(error))
    checkpoint("send:after-smtp")
    try:
        moved = store.move(reference, sent, hint=path)
        if index is not None:
            index.move(moved.store_id, sent, path)
    except ChronoMailError as error:
        return SendOutcome(True, reference, result.refused, warning=f"it was sent but could not be moved to Sent: {error}")
    checkpoint("send:after-move")
    return SendOutcome(True, moved, result.refused)


def _index(index: Index | None, reference: MessageRef, path: str, raw: bytes) -> None:
    if index is None:
        return
    try:
        index.add(entry_from_raw(reference, path, frozenset({MessageFlag.SEEN}), len(raw), raw))
    except ChronoMailError:
        pass  # the message is safe in the store; reconcile_index() adds it later
