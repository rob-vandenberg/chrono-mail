import os
import sys
from dataclasses import dataclass
from pathlib import Path

from chrono_mail.core import StorageError

# --- Version ------------------------------------------------------------
__version__ = "pop3_state.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial POP3 state log

# What has been downloaded is kept in an append-only text file, one line per event:
#   D <TAB> unique id <TAB> store id <TAB> time of download      the message is safely stored locally
#   X <TAB> unique id <TAB> time                                  the server copy was deleted
# Appending (with fsync) cannot damage earlier lines; a line cut off by a crash is ignored. If the
# file is lost, messages still on the server are downloaded again: duplicates, never a loss.


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    unique_id: str
    store_id: str
    downloaded_at: int
    deleted_on_server: bool = False


class Pop3State:
    def __init__(self, path: Path, durable: bool = True) -> None:
        self.path: Path = path
        self._durable: bool = durable
        self.records: dict[str, DownloadRecord] = {}
        self.ignored_lines: int = 0
        self._needs_newline: bool = False
        self._load()

    def _load(self) -> None:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            raise StorageError(f"The POP3 state could not be read: {error.strerror}") from None
        self._needs_newline = bool(data) and not data.endswith(b"\n")
        for raw in data.decode("utf-8", "replace").split("\n"):
            parts = raw.split("\t")
            try:
                if parts[0] == "D" and len(parts) == 4 and parts[1] and len(parts[2]) == 32:
                    self.records[parts[1]] = DownloadRecord(parts[1], parts[2], int(parts[3]))
                elif parts[0] == "X" and len(parts) == 3 and parts[1] in self.records:
                    old = self.records[parts[1]]
                    self.records[parts[1]] = DownloadRecord(old.unique_id, old.store_id, old.downloaded_at, True)
                elif raw.strip():
                    self.ignored_lines += 1
            except ValueError:
                self.ignored_lines += 1

    def _append(self, line: str) -> None:
        text = ("\n" if self._needs_newline else "") + line + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, text.encode("utf-8"))
                if self._durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._needs_newline = False
        except OSError as error:
            raise StorageError(f"The POP3 state could not be saved: {error.strerror}") from None

    def record_download(self, unique_id: str, store_id: str, when: int) -> None:
        """Remember that this message is safely stored. Call before anything is deleted on the server."""
        self._append(f"D\t{unique_id}\t{store_id}\t{when}")
        self.records[unique_id] = DownloadRecord(unique_id, store_id, when)

    def record_server_deletion(self, unique_id: str, when: int) -> None:
        self._append(f"X\t{unique_id}\t{when}")
        old = self.records.get(unique_id)
        if old is not None:
            self.records[unique_id] = DownloadRecord(old.unique_id, old.store_id, old.downloaded_at, True)
