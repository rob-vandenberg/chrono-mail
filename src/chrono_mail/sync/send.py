import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from chrono_mail.core import ChronoMailError, MessageFlag, MessageRef, ProtocolError, ServerSettings, StorageError
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import OutgoingMessage, build_message, envelope_recipients, without_bcc
from chrono_mail.protocols.smtp import Refusal, SmtpLimits, send_message
from chrono_mail.store import SENT_LETTER, Store

# --- Version ------------------------------------------------------------
__version__ = "send.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: sent but not moved to Sent: marked first by a letter in its file name (a rename needs no free space, so a full disk cannot
#           stop it), then in its properties; moved to Sent but the index could not follow: it is in Sent, told as such (with a warning)
# v3.1.341: sent but not moved to Sent: marked as sent, never sent again (file_sent_again, RV-50); when the message it replaces cannot be
#           removed, the new copy in the Outbox is removed again and nothing is sent (RV-127)
# v2.5.336: the move to Sent takes the message's place from the store (D-248)
# v2.2.314: a failed send is tried again at the next send of the Outbox (the owner: every refresh sends and receives, D-028)
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

    A message that fails to send stays in the Outbox: it is never lost, and it is tried again at the
    next send of the Outbox (Send and Receive, D-028). MessageFormatError is raised, before anything
    is stored, for a message that cannot be built.
    """
    raw = build_message(outgoing, draft=True)  # the Bcc header stays in our copy (Outbox, then Sent)
    recipients = envelope_recipients(outgoing)
    reference = store.deliver(outbox, raw, flags=frozenset({MessageFlag.SEEN}), verify=True)
    path = f"cur/{reference.store_id}!2,S"
    _index(index, reference, path, raw)
    checkpoint("send:after-outbox")
    try:
        before_send()  # for example: remove the scheduled message this one replaces, now that a safe copy is in the Outbox
    except Exception as error:  # the message it replaces stays and is sent from there: this copy must not be sent as well (RV-127), whatever
        try:  # went wrong (also an index that could not be read)
            store.delete(reference, hint=path)
        except ChronoMailError as also:  # perhaps both are in the Outbox now: the person is told, never left to find two sends
            raise StorageError(f"{error}. The new version may not have been removed again ({also}): look in the Outbox before the next Send and Receive") from None
        if index is not None:
            index.remove(reference.store_id)
        raise
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
    """Move a message the server accepted from the Outbox to Sent; if that fails it was still sent, and stays in the Outbox marked as sent,
    so no later send of the Outbox sends it again: they only try the move again (RV-50). The mark is a letter in its file name first (a
    rename: it needs no free space, so a full disk does not stop it), then sent_at in its properties; the caller also remembers it for the
    rest of the run, in case neither could be written."""
    try:
        placed = store.move_placed(reference, sent, hint=path)
    except ChronoMailError as error:
        marks: list[str] = []
        try:
            renamed = store.set_letter(reference, SENT_LETTER, True, hint=path)
        except ChronoMailError as not_marked:
            marks.append(str(not_marked))
        else:
            try:
                if index is not None:
                    index.update_flags(reference.store_id, store.flags(reference, hint=renamed), renamed)
            except ChronoMailError:
                pass  # the mark is in the file name; the index finds the file again by looking
        try:
            store.properties.change(reference.store_id, lambda current: replace(current, sent_at=int(time.time())))
        except ChronoMailError as not_noted:
            marks.append(str(not_noted))
        if len(marks) == 2:
            return SendOutcome(True, reference, refused, warning=f"it was sent but could not be moved to Sent ({error}) or marked as sent ({marks[1]})")
        return SendOutcome(True, reference, refused, warning=f"it was sent but could not be moved to Sent: {error}; it waits in the Outbox as sent")
    try:
        if index is not None:
            index.move(placed.ref.store_id, sent, placed.place)
    except ChronoMailError as error:  # it is in Sent: the list follows once the index is repaired (Settings > Repair the search index)
        return SendOutcome(True, placed.ref, refused, warning=f"it was sent and moved to Sent, but the search index could not follow: {error}")
    checkpoint("send:after-move")
    return SendOutcome(True, placed.ref, refused)


def file_sent_again(store: Store, index: Index | None, reference: MessageRef, path: str | None, sent: tuple[str, ...]) -> SendOutcome:
    """A message of the Outbox the server accepted before, whose move to Sent failed then (sent_at): only the move is tried again."""
    return _file_as_sent(store, index, reference, path, sent, (), _no_checkpoint)


def _index(index: Index | None, reference: MessageRef, path: str, raw: bytes) -> None:
    if index is None:
        return
    try:
        index.add(entry_from_raw(reference, path, frozenset({MessageFlag.SEEN}), len(raw), raw))
    except ChronoMailError:
        pass  # the message is safe in the store; reconcile_index() adds it later
