import json
import re
import shutil
import threading
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "tags.py 0.5.134"

# --- Version history ----------------------------------------------------
# v0.5.134: Initial version: which messages of a mailbox carry which tags (tags.json next to the folders)

FILE_NAME: str = "tags.json"
FORMAT_VERSION: int = 1
MAXIMUM_NAME: int = 100
MAXIMUM_TAGS_PER_MESSAGE: int = 50
MAXIMUM_FILE_BYTES: int = 64 * 1024 * 1024
_STORE_ID: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    """One lock per tags file, shared by every Store object of the same mailbox in this program."""
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def clean_tags(tags: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Names as written, with spaces tidied, each once (compared without regard to case), in the order given."""
    found: dict[str, str] = {}
    for tag in tags:
        name = " ".join(tag.split())
        if not name:
            continue
        if len(name) > MAXIMUM_NAME:
            raise StorageError("A tag has at most 100 characters")
        found.setdefault(name.casefold(), name)
    if len(found) > MAXIMUM_TAGS_PER_MESSAGE:
        raise StorageError(f"A message has at most {MAXIMUM_TAGS_PER_MESSAGE} tags")
    return tuple(found.values())


class MessageTags:
    """Which messages of a mailbox carry which tags: one small file next to the folders (the message files stay as
    they are). The file is the truth for tags; the index only copies it. Every change re-reads the file under a lock
    and writes it in one atomic step, so changes made at the same time are never lost."""

    def __init__(self, root: Path, durable: bool = True) -> None:
        self.path: Path = root / FILE_NAME
        self._durable: bool = durable

    def _read(self) -> dict[str, tuple[str, ...]]:
        try:
            if not self.path.exists():
                return {}
            if self.path.stat().st_size > MAXIMUM_FILE_BYTES:
                raise StorageError("The tags of this mailbox are far too large")
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, dict) or data.get("format") != FORMAT_VERSION or not isinstance(data.get("messages"), dict):
                raise ValueError("not a tags file")
            found: dict[str, tuple[str, ...]] = {}
            for store_id, tags in data["messages"].items():
                if not isinstance(store_id, str) or _STORE_ID.fullmatch(store_id) is None or not isinstance(tags, list):
                    raise ValueError("not a tags file")
                cleaned = clean_tags([tag for tag in tags if isinstance(tag, str)])
                if cleaned:
                    found[store_id] = cleaned
            return found
        except (OSError, UnicodeDecodeError) as error:
            raise StorageError(f"The tags of this mailbox cannot be read: {error}") from None
        except (ValueError, StorageError):
            try:
                shutil.copyfile(self.path, self.path.with_name(FILE_NAME + ".bad"))
            except OSError:
                pass  # the damaged file stays where it is; it is never overwritten silently below
            raise StorageError(f"The tags of this mailbox are damaged; the file was copied to {FILE_NAME}.bad") from None

    def _write(self, tags: dict[str, tuple[str, ...]]) -> None:
        text = json.dumps({"format": FORMAT_VERSION, "messages": {store_id: list(names) for store_id, names in sorted(tags.items())}}, ensure_ascii=False)
        try:
            replace_file_atomically(self.path, text.encode("utf-8"), self._durable)
        except OSError as error:
            raise StorageError(f"The tags could not be saved: {error.strerror}") from None

    def all(self) -> dict[str, tuple[str, ...]]:
        """store id -> its tags, for every message that has tags."""
        with _lock(self.path):
            return self._read()

    def of(self, store_id: str) -> tuple[str, ...]:
        return self.all().get(store_id, ())

    def set(self, store_id: str, tags: tuple[str, ...]) -> tuple[str, ...]:
        """Give a message exactly these tags (none removes them). Returns them as kept."""
        if _STORE_ID.fullmatch(store_id) is None:
            raise StorageError("A message id is 32 lower-case hexadecimal characters")
        cleaned = clean_tags(tags)
        with _lock(self.path):
            current = self._read()
            if current.get(store_id, ()) == cleaned:
                return cleaned
            if cleaned:
                current[store_id] = cleaned
            else:
                current.pop(store_id, None)
            self._write(current)
        return cleaned

    def copy(self, source: str, target: str) -> tuple[str, ...]:
        """The copy of a message gets the tags of the original."""
        tags = self.of(source)
        return self.set(target, tags) if tags else ()

    def forget(self, store_id: str) -> None:
        """A message deleted for good loses its tags."""
        with _lock(self.path):
            current = self._read()
            if current.pop(store_id, None) is not None:
                self._write(current)

    def rename(self, old: str, new: str | None) -> list[str]:
        """Rename a tag in every message (None removes it). Returns the store ids that changed."""
        wanted = old.casefold()
        changed: list[str] = []
        with _lock(self.path):
            current = self._read()
            for store_id, tags in list(current.items()):
                if not any(tag.casefold() == wanted for tag in tags):
                    continue
                renamed: list[str] = []
                for tag in tags:
                    if tag.casefold() != wanted:
                        renamed.append(tag)
                    elif new is not None:
                        renamed.append(new)
                replaced = clean_tags(renamed)
                if replaced:
                    current[store_id] = replaced
                else:
                    del current[store_id]
                changed.append(store_id)
            if changed:
                self._write(current)
        return changed
