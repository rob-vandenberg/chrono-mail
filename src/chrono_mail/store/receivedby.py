import os
import re
import threading
from collections.abc import Iterable
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "receivedby.py 4.2.418"

# --- Version history ----------------------------------------------------
# v4.2.418: Initial: which account received each message of a mailbox, written once and never changed (D-292)

FILE_NAME: str = "received-by.log"  # in the store, beside "folders" and "properties": a backup takes it with the mailbox
MAXIMUM_FILE_BYTES: int = 512 * 1024 * 1024  # about 7 million lines; a larger file is not read (the messages are placed by the rule)
_ID: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")  # a message id and an account id alike
_WRITING: threading.Lock = threading.Lock()  # every write of every mailbox, so a message never gets two lines
_LOCK: threading.Lock = threading.Lock()  # the files read so far
_READ: dict[Path, tuple[tuple[int, int, int], dict[str, str]]] = {}  # a file -> (its identity, time and size when read, message id -> account id)


def read_records(data: bytes) -> dict[str, str]:
    """The lines of such a file (message id -> account id); a line that cannot be read is skipped. Never raises."""
    found: dict[str, str] = {}
    for line in data.decode("utf-8", "replace").split("\n"):
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "R" and _ID.fullmatch(parts[1]) and _ID.fullmatch(parts[2]):
            found.setdefault(parts[1], parts[2])  # the first line of a message is its account for good
    return found


def mapped_records(records: dict[str, str], accounts: dict[str, str]) -> dict[str, str]:
    """The records with each account named by its id here (accounts: id in the records -> id here); a record of an account that is not
    here is left out (the rule decides that message), never written with an id no account has."""
    return {store_id: accounts[account_id] for store_id, account_id in records.items() if account_id in accounts}


def records_text(records: dict[str, str]) -> bytes:
    return "".join(f"R\t{store_id}\t{account_id}\n" for store_id, account_id in records.items()).encode("utf-8")


class ReceivedBy:
    """Which of the person's accounts received each message of one mailbox (D-292): one line per message, "R <message> <account>",
    only ever added to. The first line of a message is its account for good: a later line for it is never written, and would be ignored.
    A line that cannot be read is skipped; it never stops the mail."""

    def __init__(self, root: Path, durable: bool = True) -> None:
        self.path: Path = root / FILE_NAME
        self._durable: bool = durable

    def _stamp(self) -> tuple[int, int, int] | None:
        try:
            status = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StorageError(f"The record of who received the mail cannot be read: {error.strerror}") from None
        return status.st_ino, status.st_mtime_ns, status.st_size  # a file put in its place (a restore) is another file

    def all(self) -> dict[str, str]:
        """Every message's account (message id -> account id), read again only when the file changed. Only looked up by the callers:
        it grows in place as records are added (record_many)."""
        stamp = self._stamp()
        if stamp is None:
            return {}
        with _LOCK:
            known = _READ.get(self.path)
            if known is not None and known[0] == stamp:
                return known[1]
        if stamp[2] > MAXIMUM_FILE_BYTES:
            raise StorageError("The record of who received the mail is far too large")
        try:
            found = read_records(self.path.read_bytes())
        except OSError as error:
            raise StorageError(f"The record of who received the mail cannot be read: {error.strerror}") from None
        with _LOCK:
            _READ[self.path] = (stamp, found)
        return found

    def of(self, store_id: str) -> str | None:
        return self.all().get(store_id)

    def record(self, store_id: str, account_id: str) -> str:
        """The account that received the message, written once: one that has an account keeps it. Returns the one kept."""
        return self.record_many([(store_id, account_id)]).get(store_id, account_id)

    def record_many(self, pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
        """Several at once, in one write: only the messages that have no account yet get one. Returns the account each has now."""
        wanted = list(pairs)
        for store_id, account_id in wanted:
            if _ID.fullmatch(store_id) is None or _ID.fullmatch(account_id) is None:
                raise StorageError("A message id and an account id are 32 lower-case hexadecimal characters")
        with _WRITING:
            known = self.all()
            new: dict[str, str] = {}
            for store_id, account_id in wanted:
                if store_id not in known and store_id not in new:
                    new[store_id] = account_id
            if new:
                self._append(records_text(new).decode("utf-8"))
                stamp = self._stamp()
                with _LOCK:  # what was read stays read and grows with what is written: a large file is never read again for each new line
                    known.update(new)
                    if stamp is not None:
                        _READ[self.path] = (stamp, known)
                    else:
                        _READ.pop(self.path, None)
        return {store_id: known.get(store_id) or new[store_id] for store_id, _ in wanted}

    def leave_out(self, account_ids: set[str]) -> None:
        """Take out the records that name these accounts (a restore that could not bring them back): never a record of an account that
        is not here. Written as a new file in one step."""
        with _WRITING:
            kept = {store_id: account_id for store_id, account_id in self.all().items() if account_id not in account_ids}
            if len(kept) == len(self.all()):
                return
            try:
                replace_file_atomically(self.path, records_text(kept), self._durable)
            except OSError as error:
                raise StorageError(f"Who received the mail could not be recorded: {error.strerror}") from None
            with _LOCK:
                _READ.pop(self.path, None)

    def copy(self, source: str, target: str) -> None:
        """The copy of a message was received by the same account."""
        account = self.of(source)
        if account is not None:
            self.record(target, account)

    def take_from(self, other: "ReceivedBy", store_id: str) -> None:
        """A message that comes from another mailbox keeps the account that received it."""
        account = other.of(store_id)
        if account is not None:
            self.record(store_id, account)

    def _append(self, text: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_newline = False
            if self.path.exists() and self.path.stat().st_size:
                with self.path.open("rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    needs_newline = existing.read(1) != b"\n"  # a line cut off by a crash stays a line of its own, unread
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, (("\n" if needs_newline else "") + text).encode("utf-8"))
                if self._durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StorageError(f"Who received the mail could not be recorded: {error.strerror}") from None
