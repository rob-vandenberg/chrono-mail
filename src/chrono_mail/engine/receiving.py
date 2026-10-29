import threading
from collections.abc import Callable, Collection, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from chrono_mail.config import AccountSettings
from chrono_mail.core import AccountKind, FolderRole, StorageError
from chrono_mail.index import IndexEntry
from chrono_mail.index.model import Receiving
from chrono_mail.store import Store
from chrono_mail.sync import Pop3State

WAITING_ROLES: frozenset[FolderRole] = frozenset({FolderRole.DRAFTS, FolderRole.OUTBOX, FolderRole.SCHEDULED})  # not sent yet: their From can change

# --- Version ------------------------------------------------------------
__version__ = "receiving.py 4.2.428"

# --- Version history ----------------------------------------------------
# v4.2.428: the owner: an account is fixed only when a message is retrieved from a server (the account a download gives) or sent (send.py);
#           every other message is shown by the rule and nothing is written (a draft wherever it is, imported mail); the record file is read
#           before the write lock; holding: one write for a whole download (at most every 500 messages); a POP3 account's own folders read its
#           download records too
# v4.2.418: the account is recorded with the message (the mailbox's received-by.log), once, and never changed; the record comes first, the rule
#           only decides a message that has none; Drafts, the Outbox and Scheduled are not recorded (their From can change: the account
#           in it is shown); every POP3 account's download records count
# v4.2.417: Initial: which of the person's accounts received a message, one rule for Reply's account and the Account column (D-292)


def own_place(settings: AccountSettings) -> AccountSettings | None:
    """The account whose own folders these are; None for Local Folders, which holds the mail of several accounts."""
    return settings if settings.account.kind is not AccountKind.LOCAL and settings.storage == "own" else None


def receiving_account(accounts: Sequence[AccountSettings], here: AccountSettings | None, addressed: Collection[str]) -> AccountSettings | None:
    """The account a message was sent to: the account whose own folders hold it (here) when it is one of the accounts whose address the
    message was sent to (To, Cc, the delivery lines; addressed: lower case) or when none is; else the first of those. None when nothing
    decides. The rule of Reply's account (its From) and of the Account column (D-292)."""
    senders = [item for item in accounts if item.account.kind not in (AccountKind.LOCAL, AccountKind.DAV)]
    matches = [item for item in senders if item.account.email_address.lower() in addressed]
    if here is not None and (not matches or any(item.account.account_id == here.account.account_id for item in matches)):
        return here
    return matches[0] if matches else None


HELD_BATCH: int = 500  # while a download holds the records back, they are written at the latest every so many messages

_RECORDS_LOCK: threading.Lock = threading.Lock()
_RECORDS: dict[Path, tuple[tuple[int, int], dict[str, str]]] = {}  # a POP3 state file -> (its time and size when read, store id -> account)


def _downloaded_by(path: Path, account_id: str) -> dict[str, str]:
    """The messages (store ids) this POP3 account downloaded, by its state file; read again only when the file changed (a send or a
    draft in Local Folders asks for it, and the file can be large)."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return {}
    stamp = (status.st_mtime_ns, status.st_size)
    with _RECORDS_LOCK:
        known = _RECORDS.get(path)
        if known is not None and known[0] == stamp:
            return known[1]
    found = {record.store_id: account_id for record in Pop3State(path, durable=False).records.values()}
    with _RECORDS_LOCK:
        _RECORDS[path] = (stamp, found)
    return found


class Receiver(Receiving):
    """The account that received each message of one place where mail is kept, for its index (D-292). A message's record (the mailbox's
    received-by.log) comes first and never changes. The account a download gives, or a POP3 download record names, is recorded now:
    retrieved from a server, it is fixed (a send records its own, send.py). Any other message is only shown: the account whose own folders hold it, else in Local Folders the
    POP3 account whose download record names it, else by receiving_account, else the account whose address it was sent from; in Drafts,
    the Outbox and Scheduled the account in its From. "" when nothing decides (imported mail of an address that is no account here)."""

    def __init__(self, place: AccountSettings, accounts: Sequence[AccountSettings], state_file: Callable[[str], Path], store: Store | None = None) -> None:
        self._here: AccountSettings | None = own_place(place)
        self._accounts: tuple[AccountSettings, ...] = tuple(accounts)
        self._known: frozenset[str] = frozenset(item.account.account_id for item in self._accounts)
        self._state_file: Callable[[str], Path] = state_file
        self._store: Store | None = store  # where the records are kept; None: nothing is recorded (a check only)
        self._downloaded: dict[str, str] | None = None  # store id -> the POP3 account that downloaded it; read when first needed
        self._waiting: set[tuple[str, ...]] | None = None  # the folders of messages not sent yet
        self._pending: dict[str, str] = {}  # decided, to be recorded at flush (one write for many messages)
        self._held: int = 0  # holding: flush waits until the hold ends

    def _downloaders(self) -> dict[str, str]:
        """The messages a POP3 account retrieved (store id -> account), by its download records: in Local Folders every POP3 account's (one
        moved into Local Folders brings its records along; the first named wins), in a POP3 account's own folders its own."""
        if self._downloaded is None:
            found: dict[str, str] = {}
            places = reversed(self._accounts) if self._here is None else [self._here]
            for item in places:
                if item.account.kind is AccountKind.POP3:
                    try:
                        found.update(_downloaded_by(self._state_file(item.account.account_id), item.account.account_id))
                    except (OSError, StorageError):
                        continue  # an unreadable record: its mail is shown by the rule
            self._downloaded = found
        return self._downloaded

    def _waiting_folders(self) -> set[tuple[str, ...]]:
        if self._waiting is None:
            try:
                self._waiting = {folder.path for folder in self._store.list_folders() if folder.role in WAITING_ROLES} if self._store is not None else set()
            except StorageError:
                self._waiting = set()
        return self._waiting

    def prepare(self) -> None:
        """Read the download records, the folders and the records of who received the mail now (outside the index's write lock)."""
        self._downloaders()
        self._waiting_folders()
        if self._store is not None:
            try:
                self._store.received_by.all()  # read once, kept: the write lock never waits for it
            except StorageError:
                pass  # read and told where it is used

    def _by_address(self, entry: IndexEntry, sender_first: bool) -> str:
        addressed = {*entry.delivered_to, *(address.strip().lower() for address in entry.recipient_addresses.split(","))} - {""}  # every recipient, as Reply reads them
        sender = {entry.sender_address.strip().lower()} - {""}
        found = receiving_account(self._accounts, None, sender) if sender_first else None
        if found is None:
            found = receiving_account(self._accounts, self._here, addressed)
        if found is None:
            found = receiving_account(self._accounts, None, sender)  # sent from one of the person's accounts
        return found.account.account_id if found is not None else ""

    def __call__(self, entry: IndexEntry) -> str:
        record = self._store.received_by if self._store is not None else None
        if record is not None:
            try:
                recorded = record.of(entry.store_id)
            except StorageError:
                recorded = None  # the record cannot be read now: shown by the rule, nothing written
                record = None
            if recorded is None:
                recorded = self._pending.get(entry.store_id)
            if recorded is not None and recorded in self._known:
                return recorded
            if recorded is not None:
                record = None  # it names an account that is not here (a restore that left it out): decided by the rule, never written over
        waiting = entry.folder in self._waiting_folders()
        if entry.received_by and not waiting:  # retrieved from a server just now: that account, fixed at this moment (the owner)
            if record is not None:
                self._pending[entry.store_id] = entry.received_by  # written at flush, once the index let go of its write lock
                record.hold(entry.store_id, entry.received_by)  # known at once: a move or a copy meanwhile takes it along
            return entry.received_by
        if waiting:
            return self._by_address(entry, sender_first=True)  # not sent yet: its From shows the account
        downloaded = self._downloaders().get(entry.store_id)
        if downloaded:  # a POP3 download record says which account retrieved it (mail kept from before 4.2.418): that retrieval fixes it
            if record is not None:
                self._pending[entry.store_id] = downloaded
            return downloaded
        account = self._here.account.account_id if self._here is not None else ""
        return account or self._by_address(entry, sender_first=False)  # shown by the rule, never written: only a retrieval or a send fixes it

    @contextmanager
    def holding(self) -> Generator[None]:
        """What is decided meanwhile is recorded in one write when the hold ends (a whole download, never one write per message)."""
        self._held += 1
        try:
            yield
        finally:
            self._held -= 1
            self.flush()

    def flush(self) -> None:
        """Record what was decided since the last flush, in one write; a message recorded meanwhile keeps its own (D-292)."""
        if self._held and len(self._pending) < HELD_BATCH:
            return  # the hold writes it when it ends, or once a batch is full (a crash halfway loses at most one batch)
        pending = self._pending
        self._pending = {}
        if pending and self._store is not None:
            try:
                self._store.received_by.record_many(pending.items())
            except StorageError:
                pass  # recorded at the next filling in or rebuild; shown now
