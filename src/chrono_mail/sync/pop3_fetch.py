import time
from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.config import Pop3DeletePolicy, Pop3Options
from chrono_mail.core import ChronoMailError, MessageRef, ProtocolError
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Refused
from chrono_mail.store import Store
from chrono_mail.sync.pop3_state import DownloadRecord, Pop3State

# --- Version ------------------------------------------------------------
__version__ = "pop3_fetch.py 0.5.132"

# --- Version history ----------------------------------------------------
# v0.5.132: already_here: a message that is already in the store (imported) is recorded as downloaded, not stored twice
# v0.0.33: the new messages are listed in the report
# v0.0.10: Initial POP3 fetch

SECONDS_PER_DAY: int = 86_400
MISSING_RATIO_LIMIT: float = 0.5
MISSING_RATIO_MINIMUM_COUNT: int = 10
Checkpoint = Callable[[str], None]
Progress = Callable[[int, int], None]
AlreadyHere = Callable[[bytes], str | None]  # the store id of the same message when it is already in the store


def _no_checkpoint(name: str) -> None:
    """Crash points for tests."""


@dataclass(frozen=True, slots=True)
class FailedMessage:
    unique_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class FetchReport:
    downloaded: int = 0
    already_known: int = 0
    failed: tuple[FailedMessage, ...] = ()
    deleted_on_server: int = 0
    deletion_blocked: str | None = None
    index_problems: int = 0
    aborted: str | None = None  # the connection failed part-way; what was done up to then is kept
    new_references: tuple[MessageRef, ...] = ()  # the messages stored by this run
    already_here: int = 0  # downloaded, but already in the store (imported mail): recorded, not stored twice


@dataclass
class _Run:
    downloaded: int = 0
    already_known: int = 0
    failed: list[FailedMessage] = field(default_factory=list)
    to_delete: dict[str, int] = field(default_factory=dict)  # unique id -> message number
    deleted: int = 0
    blocked: str | None = None
    index_problems: int = 0
    aborted: str | None = None
    new_references: list[MessageRef] = field(default_factory=list)
    already_here: int = 0


def fetch_pop3(
    client: Pop3Client,
    store: Store,
    index: Index | None,
    state: Pop3State,
    inbox: tuple[str, ...],
    options: Pop3Options,
    maximum_message_bytes: int,
    now: Callable[[], float] = time.time,
    checkpoint: Checkpoint = _no_checkpoint,
    progress: Progress | None = None,
    already_here: AlreadyHere | None = None,
) -> FetchReport:
    """Download new messages through a connected client.

    The order protects the mail: a message is deleted on the server only after it has been stored
    and verified locally (the store reads it back and compares it), and after the download is
    recorded in the state log. A failure with one message is reported and the message stays on
    the server; a failure of the connection ends the run and keeps everything done so far.
    With already_here, a message that is already in the store is recorded with the store id of that copy instead of
    being stored again; the account's delete policy then treats it like any downloaded message.
    """
    run = _Run()
    try:
        numbers = client.uidl()
        sizes = client.list_sizes()
        on_server = {unique_id: number for number, unique_id in numbers}
        new = [(number, unique_id) for number, unique_id in numbers if unique_id not in state.records]
        run.already_known = len(on_server) - len({unique_id for _, unique_id in new})
        seen: set[str] = set()
        for position, (number, unique_id) in enumerate(new, 1):
            if unique_id in seen:
                continue  # a server that lists the same unique id twice
            seen.add(unique_id)
            if progress is not None:
                progress(position, len(new))
            _download_one(client, store, index, state, inbox, options, maximum_message_bytes, now, checkpoint, run, number, unique_id, sizes.get(number), already_here)
        _apply_policy(store, state, options, now, run, on_server)
        if run.to_delete:
            _delete_on_server(client, state, now, checkpoint, run)
    except ProtocolError as error:
        run.aborted = str(error)
    return FetchReport(
        run.downloaded, run.already_known, tuple(run.failed), run.deleted, run.blocked, run.index_problems, run.aborted, tuple(run.new_references), run.already_here
    )


def _download_one(
    client: Pop3Client,
    store: Store,
    index: Index | None,
    state: Pop3State,
    inbox: tuple[str, ...],
    options: Pop3Options,
    maximum_message_bytes: int,
    now: Callable[[], float],
    checkpoint: Checkpoint,
    run: _Run,
    number: int,
    unique_id: str,
    size: int | None,
    already_here: AlreadyHere | None = None,
) -> None:
    if size is not None and size > maximum_message_bytes:
        run.failed.append(FailedMessage(unique_id, "the message is larger than the limit"))
        return
    try:
        raw = client.retr(number)
    except Pop3Refused as error:
        run.failed.append(FailedMessage(unique_id, f"the server would not send it: {error}"))
        return
    checkpoint("fetch:after-retr")
    present = already_here(raw) if already_here is not None else None
    if present is not None:
        state.record_download(unique_id, present, int(now()))  # the delete policy checks this copy before the server copy is touched
        run.already_here += 1
        return
    try:
        reference = store.deliver(inbox, raw, verify=True)
    except ChronoMailError as error:
        run.failed.append(FailedMessage(unique_id, f"it could not be stored safely: {error}"))
        return
    checkpoint("fetch:after-deliver")
    state.record_download(unique_id, reference.store_id, int(now()))
    checkpoint("fetch:after-state")
    run.downloaded += 1
    run.new_references.append(reference)
    if index is not None:
        try:
            index.add(entry_from_raw(reference, f"new/{reference.store_id}", frozenset(), len(raw), raw))
        except ChronoMailError:
            run.index_problems += 1  # the message is safe in the store; reconcile_index() will add it
    if options.policy is Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD:
        run.to_delete[unique_id] = number  # verified when it was stored; deleted together with the others below


def _verified_locally(store: Store, record: DownloadRecord, locations: dict[str, tuple[tuple[str, ...], str]]) -> bool:
    """Is the downloaded copy still in the store and does it still decrypt?"""
    where = locations.get(record.store_id)
    if where is None:
        return False
    try:
        store.verify(MessageRef(store.account_id, where[0], record.store_id), hint=where[1])
    except ChronoMailError:
        return False
    return True


def _apply_policy(store: Store, state: Pop3State, options: Pop3Options, now: Callable[[], float], run: _Run, on_server: dict[str, int]) -> None:
    """Delete older messages on the server according to the account's policy, but only ones whose
    local copy is verified, and never when the store looks wrong."""
    if options.policy is Pop3DeletePolicy.KEEP:
        return
    waiting = [state.records[unique_id] for unique_id in on_server if unique_id in state.records and not state.records[unique_id].deleted_on_server and unique_id not in run.to_delete]
    if not waiting:
        return
    if store.damaged_folders or not store.list_folders():
        run.blocked = "the store looks damaged or empty; nothing was deleted on the server"
        return
    locations = store.locate_all()
    candidates: list[DownloadRecord] = []
    if options.policy is Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD:
        candidates = [record for record in waiting if _verified_locally(store, record, locations)]
    elif options.policy is Pop3DeletePolicy.DELETE_AFTER_DAYS:
        limit = now() - options.days * SECONDS_PER_DAY
        candidates = [record for record in waiting if record.downloaded_at <= limit and _verified_locally(store, record, locations)]
    elif options.policy is Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY:
        missing = [record for record in waiting if record.store_id not in locations]
        if len(waiting) >= MISSING_RATIO_MINIMUM_COUNT and len(missing) / len(waiting) > MISSING_RATIO_LIMIT:
            run.blocked = "most downloaded messages are missing from the store; nothing was deleted on the server"
            return
        candidates = missing
    for record in candidates:
        run.to_delete[record.unique_id] = on_server[record.unique_id]


def _delete_on_server(client: Pop3Client, state: Pop3State, now: Callable[[], float], checkpoint: Checkpoint, run: _Run) -> None:
    for number in run.to_delete.values():
        client.dele(number)
    checkpoint("fetch:after-dele")
    checkpoint("fetch:before-quit")
    client.quit()
    checkpoint("fetch:after-quit")
    for unique_id in run.to_delete:
        state.record_server_deletion(unique_id, int(now()))
    run.deleted = len(run.to_delete)
