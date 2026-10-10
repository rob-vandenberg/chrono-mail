import json
import os
from dataclasses import dataclass
from pathlib import Path

from chrono_mail.core import StorageError

# --- Version ------------------------------------------------------------
__version__ = "imap_state.py 0.0.30"

# --- Version history ----------------------------------------------------
# v0.0.30: uploaded messages
# v0.0.29: Initial IMAP state log

# What is known about an IMAP account is kept in an append-only file, one JSON object per line:
#   {"e": "v", "f": [folder path], "v": UIDVALIDITY}                    the folder's UIDVALIDITY; forgets the folder's messages
#   {"e": "m", "f": [...], "u": UID, "s": store id, "g": [flags]}        UID <-> store id, with the server flags last seen
#   {"e": "g", "f": [...], "u": UID, "g": [flags]}                       the server flags changed
#   {"e": "x", "f": [...], "u": UID}                                     the UID is gone from the server (and from the store)
#   {"e": "a", "s": store id}                                           the message was sent to the server (so that it is not sent twice)
# Appending (with fsync) cannot damage earlier lines; a line cut off by a crash is ignored; unknown events are ignored (later
# versions add more, for example the changes waiting to be sent). If the file is lost the next sync downloads again and
# matches by Message-ID: duplicates are avoided where possible, never a loss. The file is rewritten compactly when it has
# grown to many times the size of what it describes.

COMPACT_FACTOR: int = 8
COMPACT_MINIMUM_LINES: int = 5000


@dataclass(frozen=True, slots=True)
class ImapEntry:
    store_id: str
    flags: frozenset[str]  # the server flags last seen, limited to the ones we keep


class ImapState:
    def __init__(self, path: Path, durable: bool = True) -> None:
        self.path: Path = path
        self._durable: bool = durable
        self.validity: dict[tuple[str, ...], int] = {}
        self.entries: dict[tuple[str, ...], dict[int, ImapEntry]] = {}
        self.uploaded: set[str] = set()
        self.ignored_lines: int = 0
        self._lines: int = 0
        self._needs_newline: bool = False
        self._load()

    def _load(self) -> None:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            raise StorageError(f"The IMAP state could not be read: {error.strerror}") from None
        self._needs_newline = bool(data) and not data.endswith(b"\n")
        for raw in data.decode("utf-8", "replace").split("\n"):
            if not raw.strip():
                continue
            self._lines += 1
            try:
                self._apply(json.loads(raw))
            except (ValueError, KeyError, TypeError, AttributeError):
                self.ignored_lines += 1

    def _apply(self, event: dict[str, object]) -> None:
        kind = event["e"]
        if kind == "a":
            stored = event["s"]
            if not isinstance(stored, str) or len(stored) != 32:
                raise ValueError("bad store id")
            self.uploaded.add(stored)
            return
        raw_folder = event["f"]
        if not isinstance(raw_folder, list) or not all(isinstance(part, str) for part in raw_folder) or not raw_folder:
            raise ValueError("bad folder")
        folder = tuple(str(part) for part in raw_folder)
        if kind == "v":
            value = event["v"]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("bad validity")
            self.validity[folder] = value
            self.entries[folder] = {}
            return
        uid = event["u"]
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise ValueError("bad uid")
        if kind == "m":
            store_id, flags = event["s"], event["g"]
            if not isinstance(store_id, str) or len(store_id) != 32 or not isinstance(flags, list):
                raise ValueError("bad entry")
            self.entries.setdefault(folder, {})[uid] = ImapEntry(store_id, frozenset(str(flag) for flag in flags))
        elif kind == "g":
            flags = event["g"]
            current = self.entries.get(folder, {}).get(uid)
            if not isinstance(flags, list):
                raise ValueError("bad flags")
            if current is not None:
                self.entries[folder][uid] = ImapEntry(current.store_id, frozenset(str(flag) for flag in flags))
        elif kind == "x":
            self.entries.get(folder, {}).pop(uid, None)

    # --- writing ------------------------------------------------------------------------------------------------

    def _append(self, event: dict[str, object]) -> None:
        text = ("\n" if self._needs_newline else "") + json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
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
            self._lines += 1
        except OSError as error:
            raise StorageError(f"The IMAP state could not be saved: {error.strerror}") from None

    def mark_uploaded(self, store_id: str) -> None:
        """Remember that a local message was sent to the server. Call right after the server accepted it."""
        self._append({"e": "a", "s": store_id})
        self.uploaded.add(store_id)

    def set_validity(self, folder: tuple[str, ...], validity: int) -> None:
        """The folder's UIDVALIDITY. A new value forgets every UID of the folder (they mean something else now)."""
        self._append({"e": "v", "f": list(folder), "v": validity})
        self.validity[folder] = validity
        self.entries[folder] = {}

    def map(self, folder: tuple[str, ...], uid: int, store_id: str, flags: frozenset[str]) -> None:
        """Remember that this message is safely stored. Call before anything is changed on the server."""
        self._append({"e": "m", "f": list(folder), "u": uid, "s": store_id, "g": sorted(flags)})
        self.entries.setdefault(folder, {})[uid] = ImapEntry(store_id, flags)

    def set_flags(self, folder: tuple[str, ...], uid: int, flags: frozenset[str]) -> None:
        current = self.entries.get(folder, {}).get(uid)
        if current is None:
            return
        self._append({"e": "g", "f": list(folder), "u": uid, "g": sorted(flags)})
        self.entries[folder][uid] = ImapEntry(current.store_id, flags)

    def unmap(self, folder: tuple[str, ...], uid: int) -> None:
        if uid in self.entries.get(folder, {}):
            self._append({"e": "x", "f": list(folder), "u": uid})
            del self.entries[folder][uid]

    # --- reading ------------------------------------------------------------------------------------------------

    def folder_entries(self, folder: tuple[str, ...]) -> dict[int, ImapEntry]:
        return dict(self.entries.get(folder, {}))

    def find(self, store_id: str) -> tuple[tuple[str, ...], int] | None:
        """Where on the server a stored message is, if it is known."""
        for folder, mapped in self.entries.items():
            for uid, entry in mapped.items():
                if entry.store_id == store_id:
                    return folder, uid
        return None

    def count(self) -> int:
        return sum(len(mapped) for mapped in self.entries.values())

    def compact_if_needed(self) -> bool:
        """Rewrite the file as a snapshot when it holds far more lines than facts. Atomic: a crash keeps the old file."""
        facts = self.count() + len(self.validity)
        if self._lines < COMPACT_MINIMUM_LINES or self._lines < COMPACT_FACTOR * max(facts, 1):
            return False
        lines: list[str] = []
        for folder, validity in self.validity.items():
            lines.append(json.dumps({"e": "v", "f": list(folder), "v": validity}, separators=(",", ":"), ensure_ascii=False))
            for uid, entry in self.entries.get(folder, {}).items():
                lines.append(json.dumps({"e": "m", "f": list(folder), "u": uid, "s": entry.store_id, "g": sorted(entry.flags)}, separators=(",", ":"), ensure_ascii=False))
        lines.extend(json.dumps({"e": "a", "s": store_id}, separators=(",", ":")) for store_id in sorted(self.uploaded))
        temporary = self.path.with_name(self.path.name + ".new")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, ("\n".join(lines) + "\n").encode("utf-8"))
                if self._durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            temporary.replace(self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StorageError(f"The IMAP state could not be compacted: {error.strerror}") from None
        self._lines = len(lines)
        return True
