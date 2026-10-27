"""What every import from another program needs: telling the same message apart (the fingerprint), the mail that is here
already, the journal that lets a stopped import continue where it stopped, and the check that a copy is really on disk."""

import hashlib
import json
import os
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from chrono_mail.core import ChronoMailError, MessageRef
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.mailbox import Mailbox
from chrono_mail.mime import parse_message

# --- Version ------------------------------------------------------------
__version__ = "import_common.py 4.2.388"

# --- Version history ----------------------------------------------------
# v4.2.388: contents_fingerprint: what tells one export from a newer one in the same place (names, sizes and times, nothing read)
# v4.2.385: Initial version: the fingerprint, the mail already here, the journal and the check of a copy on disk, moved unchanged out of the
#           first importer so that every importer uses the same; the journal can also remember a value (which collection an import fills);
#           known_copies: every copy of a message, for an import that tells copies of its own source apart from mail that was here

EVERYTHING: int = 2**62
MAXIMUM_HEADER_BYTES: int = 256 * 1024  # how much of a message is read for its fingerprint
MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024  # the same limit as a download
MAXIMUM_REPORTED: int = 200  # failures listed per run; the rest are counted


# --- telling the same message apart -----------------------------------------------------------------


def _plain(text: str) -> str:
    return "".join(character for character in text if character >= " ").strip().lower()


def _address(sender: str) -> str:
    """'Name <a@example.com>' or 'a@example.com' -> 'a@example.com'."""
    found = re.search(r"<([^<>]*)>\s*$", sender)
    return (found.group(1) if found else sender).strip().lower()


def fingerprint(message_id: str | None, date: datetime | None, sender: str, subject: str) -> str | None:
    """What makes two copies the same message: the Message-ID, or without one the date, the sender and the subject."""
    if message_id and message_id.strip("<> "):
        return "id:" + message_id.strip().strip("<>").lower()
    if date is None:
        return None
    return f"m:{int(date.timestamp())}|{_address(sender)}|{_plain(subject)}"


def fingerprint_of_raw(raw: bytes) -> str | None:
    """The fingerprint of a raw message, from its header only (the same parser as the index, without the body)."""
    end = re.search(rb"\r?\n\r?\n", raw[:MAXIMUM_HEADER_BYTES])
    parsed = parse_message(raw[: end.end()] if end is not None else raw)
    sender = parsed.from_addresses[0].address if parsed.from_addresses else ""
    return fingerprint(parsed.message_id, parsed.date, sender, parsed.subject)


def known_messages(mailbox: Mailbox, leave_out: frozenset[str] = frozenset()) -> dict[str, str]:
    """fingerprint -> store id of every message in the mailbox (from its index), except the store ids left out."""
    known: dict[str, str] = {}
    for folder in mailbox.store.list_folders():
        for message in mailbox.index.page(folder.path, limit=EVERYTHING):
            if message.store_id in leave_out:
                continue
            key = fingerprint(message.message_id, message.date, message.sender, message.subject)
            if key is not None:
                known.setdefault(key, message.store_id)
    return known


def known_copies(mailbox: Mailbox) -> dict[str, list[str]]:
    """fingerprint -> the store ids of every copy of that message in the mailbox (from its index)."""
    known: dict[str, list[str]] = {}
    for folder in mailbox.store.list_folders():
        for message in mailbox.index.page(folder.path, limit=EVERYTHING):
            key = fingerprint(message.message_id, message.date, message.sender, message.subject)
            if key is not None:
                known.setdefault(key, []).append(message.store_id)
    return known


def still_here(mailbox: Mailbox, store_id: str) -> bool:
    """Is the copy that makes a message a duplicate really here and readable? (After a crash the index may name a copy
    that was never made safe on disk; then the message is imported again.)"""
    row = mailbox.index.get(store_id)
    if row is None:
        return False
    try:
        mailbox.store.verify(MessageRef(mailbox.store.account_id, row.folder, store_id), hint=row.path)
    except ChronoMailError:
        return False
    return True


# --- telling one export apart from a newer one in the same place -------------------------------------


def contents_fingerprint(paths: Sequence[Path | None]) -> str:
    """The paths and what they hold, by name, size and time of every file (a folder: every file below it); nothing is read. A newer export
    written into the same place gives another fingerprint."""
    digest = hashlib.sha256()

    def add(text: str) -> None:
        digest.update(text.encode("utf-8", "surrogateescape") + b"\0")

    def add_file(path: Path, name: str) -> None:
        try:
            status = path.stat()
        except OSError:
            add(f"{name}|gone")
            return
        add(f"{name}|{status.st_size}|{status.st_mtime_ns}")

    for path in paths:
        if path is None:
            add("-")
            continue
        add(str(path.resolve()))
        if not path.is_dir():
            add_file(path, "")
            continue
        for directory, folders, files in os.walk(path):
            folders.sort()
            here = Path(directory).relative_to(path).as_posix()
            for name in sorted(files):
                add_file(Path(directory) / name, f"{here}/{name}")
    return digest.hexdigest()


# --- the journal ----------------------------------------------------------------------------------


class Journal:
    """What an import has done, one JSON line per step, appended. It belongs to one source (an export, a data file):
    another source starts a new journal. A last line cut off by a crash is ignored."""

    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.phases: set[str] = set()
        self.done: dict[str, str] = {}  # item (a relative path, an item number) -> store id, "duplicate" or "failed"
        self.values: dict[str, str] = {}  # what the import remembered (remember)
        lines: list[object] = []
        try:
            for line in path.read_text("utf-8").splitlines():
                try:
                    lines.append(load_json(line))
                except ValueError:
                    continue
        except (OSError, UnicodeDecodeError):
            lines = []
        if not lines or lines[0] != ["source", source]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(["source", source]) + "\n", "utf-8")
            return
        for entry in lines[1:]:
            if isinstance(entry, list) and len(entry) == 2 and entry[0] == "phase" and isinstance(entry[1], str):
                self.phases.add(entry[1])
            elif isinstance(entry, list) and len(entry) == 3 and entry[0] == "mail" and isinstance(entry[1], str) and isinstance(entry[2], str):
                self.done[entry[1]] = entry[2]
            elif isinstance(entry, list) and len(entry) == 3 and entry[0] == "value" and isinstance(entry[1], str) and isinstance(entry[2], str):
                self.values[entry[1]] = entry[2]

    def imported_ids(self) -> frozenset[str]:
        return frozenset(value for value in self.done.values() if value not in ("duplicate", "failed", "imported") and not value.startswith("skipped:"))

    def _append(self, entry: list[str]) -> None:
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    def phase(self, name: str) -> None:
        self.phases.add(name)
        self._append(["phase", name])

    def remember(self, key: str, value: str) -> None:
        self.values[key] = value
        self._append(["value", key, value])

    def messages(self, outcomes: list[tuple[str, str]]) -> None:
        """Several items done, in one write."""
        if not outcomes:
            return
        with open(self.path, "a", encoding="utf-8") as stream:
            for relative, outcome in outcomes:
                self.done[relative] = outcome
                stream.write(json.dumps(["mail", relative, outcome]) + "\n")

    def finish(self) -> None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass  # a lost line only means that message is found again as a duplicate
