import json
import os
import re
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "properties.py 3.2.342"

# --- Version history ----------------------------------------------------
# v3.2.342: tags_and_unreadable: the messages whose tags cannot be read are told apart from those without tags (RV-48)
# v3.1.341: sent_at: a message of the Outbox the server accepted, whose move to Sent failed; it is never sent again (RV-50)
# v2.4.324: put_text: the properties of a message from a backup (D-244)
# v2.2.314: send_not_before (a message in the Outbox may not be sent before this moment) and replies_to (the message a reply answers,
#           marked as replied once it is really sent)
# v1.1.248: readable and tags take the listing of the properties folder made earlier (present), so a check lists it once (R-09)
# v1.1.206: Initial version: a message's properties that cannot be kept in the message itself (its tags, when a snoozed message comes
#           back and from where, where an archived message came from) in one small file per message, properties/<store id>.json in its
#           mailbox; it replaces tags.json (taken over once), snoozed.json and archived.log (D-176)

FOLDER_NAME: str = "properties"  # inside the store, beside "folders"; a move of a message never touches its properties file
OLD_TAGS_FILE: str = "tags.json"  # before 1.1.206: the tags of every message of the mailbox in one file
MAXIMUM_NAME: int = 100
MAXIMUM_TAGS_PER_MESSAGE: int = 50
MAXIMUM_FILE_BYTES: int = 64 * 1024  # a properties file is small; a larger one is damaged
MAXIMUM_OLD_TAGS_FILE_BYTES: int = 64 * 1024 * 1024
MAXIMUM_FOLDER_TEXT: int = 4000  # a folder path written in a properties file
_STORE_ID: re.Pattern[str] = re.compile(r"[0-9a-f]{32}")
_SUFFIX: str = ".json"
_BAD_SUFFIX: str = ".bad"

_LOCKS: tuple[threading.RLock, ...] = tuple(threading.RLock() for _ in range(256))


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


@dataclass(frozen=True, slots=True)
class Properties:
    """What is known of a message beyond the message itself. Received mail is kept exactly as it came, so this cannot be
    written into it."""

    tags: tuple[str, ...] = ()
    snoozed_until: int | None = None  # a snoozed message comes back at this moment (seconds since 1970)
    snoozed_from: str | None = None  # ... to this folder ("Inbox", "Projects/2026")
    archived_from: str | None = None  # an archived message came from this folder
    send_not_before: int | None = None  # a message in the Outbox is not sent before this moment (seconds since 1970): its countdown runs
    replies_to: str | None = None  # a reply waiting in the Outbox: the message it answers ("Inbox/<store id>"), marked as replied once it is sent
    sent_at: int | None = None  # the server accepted this message of the Outbox at this moment; only its move to Sent is left: never sent again (RV-50)

    def empty(self) -> bool:
        return (
            not self.tags and self.snoozed_until is None and self.snoozed_from is None and self.archived_from is None and self.send_not_before is None
            and self.replies_to is None and self.sent_at is None
        )


def _folder_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAXIMUM_FOLDER_TEXT:
        raise ValueError("not a folder")
    return value


def _from_data(data: object) -> tuple[Properties, dict[str, object]]:
    """The properties in a file, and the whole of it (names this version does not know are kept when it is written again)."""
    if not isinstance(data, dict):
        raise ValueError("not a properties file")
    whole: dict[str, object] = {str(key): value for key, value in data.items()}
    tags = whole.get("tags", [])
    until = whole.get("snoozed_until")
    not_before = whole.get("send_not_before")
    sent_at = whole.get("sent_at")
    if not isinstance(tags, list) or not all(moment is None or (isinstance(moment, int) and not isinstance(moment, bool)) for moment in (until, not_before, sent_at)):
        raise ValueError("not a properties file")
    properties = Properties(
        clean_tags([tag for tag in tags if isinstance(tag, str)]), until if isinstance(until, int) else None, _folder_text(whole.get("snoozed_from")),
        _folder_text(whole.get("archived_from")), not_before if isinstance(not_before, int) else None, _folder_text(whole.get("replies_to")),
        sent_at if isinstance(sent_at, int) else None,
    )
    return properties, whole


def _to_data(properties: Properties, whole: dict[str, object]) -> dict[str, object]:
    known = ("tags", "snoozed_until", "snoozed_from", "archived_from", "send_not_before", "replies_to", "sent_at")
    data = {key: value for key, value in whole.items() if key not in known}
    if properties.tags:
        data["tags"] = list(properties.tags)
    for key, value in (
        ("snoozed_until", properties.snoozed_until), ("snoozed_from", properties.snoozed_from), ("archived_from", properties.archived_from),
        ("send_not_before", properties.send_not_before), ("replies_to", properties.replies_to), ("sent_at", properties.sent_at),
    ):
        if value is not None:
            data[key] = value
    return data


def _unless_tagged(tags: tuple[str, ...]) -> Callable[[Properties], Properties]:
    """These tags, unless the message has tags already."""
    return lambda current: current if current.tags else replace(current, tags=tags)


class MessageProperties:
    """The properties files of one mailbox: properties/<store id>.json, at most one per message, made only when a message has
    a property. The store id never changes when a message moves, so a move never touches this file. Every change re-reads
    the file under the message's lock and writes it in one atomic step. A damaged file is copied aside (.bad) and reported
    for that message alone; it is never written over silently."""

    def __init__(self, root: Path, durable: bool = True) -> None:
        self.directory: Path = root / FOLDER_NAME
        self._durable: bool = durable

    def _lock(self, store_id: str) -> threading.RLock:
        """Shared by every Store object of this program (a store is opened per command); two messages may share one."""
        return _LOCKS[hash((str(self.directory), store_id)) % len(_LOCKS)]

    def _path(self, store_id: str) -> Path:
        if _STORE_ID.fullmatch(store_id) is None:
            raise StorageError("A message id is 32 lower-case hexadecimal characters")
        return self.directory / f"{store_id}{_SUFFIX}"

    def _read(self, store_id: str) -> tuple[Properties, dict[str, object]]:
        path = self._path(store_id)
        try:
            if path.stat().st_size > MAXIMUM_FILE_BYTES:
                raise ValueError("far too large")
            return _from_data(json.loads(path.read_text("utf-8")))
        except FileNotFoundError:
            return Properties(), {}
        except (OSError, UnicodeDecodeError) as error:
            raise StorageError(f"The properties of this message cannot be read: {error}") from None
        except (ValueError, StorageError):
            try:
                bad = path.with_name(path.name + _BAD_SUFFIX)
                if not bad.exists():
                    bad.write_bytes(path.read_bytes())
            except OSError:
                pass  # the damaged file stays where it is; it is never written over below
            raise StorageError(f"The properties of this message are damaged; the file was copied to {path.name}{_BAD_SUFFIX}") from None

    def _write(self, store_id: str, properties: Properties, whole: dict[str, object]) -> None:
        path = self._path(store_id)
        data = _to_data(properties, whole)
        try:
            if not data:
                path.unlink(missing_ok=True)
                if self._durable:
                    fsync_directory(path.parent)
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            replace_file_atomically(path, json.dumps(data, ensure_ascii=False).encode("utf-8"), self._durable)
        except OSError as error:
            raise StorageError(f"The properties of the message could not be saved: {error.strerror}") from None

    def of(self, store_id: str) -> Properties:
        with self._lock(store_id):
            return self._read(store_id)[0]

    def change(self, store_id: str, update: Callable[[Properties], Properties]) -> Properties:
        """Read, change and write one message's properties as one step. Returns them as kept."""
        with self._lock(store_id):
            current, whole = self._read(store_id)
            changed = update(current)
            changed = replace(changed, tags=clean_tags(changed.tags))
            if changed != current:
                self._write(store_id, changed, whole)
            return changed

    def forget(self, store_id: str) -> None:
        """A message deleted for good loses its properties (a damaged file is kept aside as .bad first)."""
        with self._lock(store_id):
            path = self._path(store_id)
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise StorageError(f"The properties of the message could not be removed: {error.strerror}") from None

    def copy(self, source: str, target: str) -> tuple[str, ...]:
        """The copy of a message gets the tags of the original (it is neither snoozed nor archived by being copied)."""
        tags = self.of(source).tags
        return self.set_tags(target, tags) if tags else ()

    def take_from(self, other: "MessageProperties", store_id: str) -> None:
        """A message moved to this mailbox from another one, keeping its id (a change of storage): its properties come along.
        Properties already here are kept (an interrupted earlier run may have brought them). A damaged file there is not
        brought (it stays there as .bad); the message itself still moves."""
        try:
            found, whole = other._read(store_id)
        except StorageError:
            return
        if found.empty() and not whole:
            return
        with self._lock(store_id):
            current, here = self._read(store_id)
            if current.empty() and not here:
                self._write(store_id, found, whole)

    def put_text(self, store_id: str, text: str, replace_existing: bool) -> bool:
        """The properties of a message from a backup, as the backup kept them; existing ones are kept unless replace_existing.
        Whether they were written. A file that cannot be read is refused (StorageError), never written."""
        try:
            found, whole = _from_data(json.loads(text))
        except ValueError:
            raise StorageError("The properties of a message in the backup are damaged") from None
        with self._lock(store_id):
            current, here = self._read(store_id)
            if not replace_existing and not (current.empty() and not here):
                return False
            self._write(store_id, found, whole)
            return True

    def stored_ids(self) -> set[str]:
        """The ids of the messages that have a properties file (one listing of the folder, nothing is read)."""
        try:
            with os.scandir(self.directory) as entries:
                return {entry.name[: -len(_SUFFIX)] for entry in entries if entry.name.endswith(_SUFFIX) and _STORE_ID.fullmatch(entry.name[: -len(_SUFFIX)])}
        except FileNotFoundError:
            return set()
        except OSError as error:
            raise StorageError(f"The properties of this mailbox cannot be listed: {error.strerror}") from None

    def readable(self, store_ids: Collection[str] | None = None, present: set[str] | None = None) -> dict[str, Properties]:
        """The properties of these messages (None: of every message that has any). A damaged file is left out (it was copied
        aside as .bad): the other messages never suffer from it. present: the ids stored_ids gave earlier (None: the folder is listed now)."""
        present = self.stored_ids() if present is None else present
        wanted = present if store_ids is None else present.intersection(store_ids)
        found: dict[str, Properties] = {}
        for store_id in wanted:
            try:
                properties = self.of(store_id)
            except StorageError:
                continue
            if not properties.empty():
                found[store_id] = properties
        return found

    # --- tags -----------------------------------------------------------------------------------------------------------------------------

    def tags(self, store_ids: Collection[str] | None = None, present: set[str] | None = None) -> dict[str, tuple[str, ...]]:
        """store id -> its tags, for these messages (None: every message) that have tags (present: as for readable)."""
        return {store_id: found.tags for store_id, found in self.readable(store_ids, present).items() if found.tags}

    def tags_and_unreadable(self, store_ids: Collection[str], present: set[str]) -> tuple[dict[str, tuple[str, ...]], set[str]]:
        """The tags of these messages (as tags gives them) and the messages whose properties file cannot be read: their tags are not
        known, which is not the same as having none (RV-48)."""
        found: dict[str, tuple[str, ...]] = {}
        unreadable: set[str] = set()
        for store_id in present.intersection(store_ids):
            try:
                properties = self.of(store_id)
            except StorageError:
                unreadable.add(store_id)
                continue
            if properties.tags:
                found[store_id] = properties.tags
        return found, unreadable

    def tags_of(self, store_id: str) -> tuple[str, ...]:
        return self.of(store_id).tags

    def set_tags(self, store_id: str, tags: tuple[str, ...]) -> tuple[str, ...]:
        """Give a message exactly these tags (none removes them). Returns them as kept."""
        cleaned = clean_tags(tags)
        return self.change(store_id, lambda current: replace(current, tags=cleaned)).tags

    def rename_tag(self, old: str, new: str | None) -> list[str]:
        """Rename a tag in every message (None removes it). Returns the store ids that changed."""
        wanted = old.casefold()
        changed: list[str] = []
        for store_id, tags in self.tags().items():
            if not any(tag.casefold() == wanted for tag in tags):
                continue

            def rename(current: Properties) -> Properties:
                renamed = [new if tag.casefold() == wanted else tag for tag in current.tags]
                return replace(current, tags=clean_tags([tag for tag in renamed if tag is not None]))

            self.change(store_id, rename)
            changed.append(store_id)
        return changed

    # --- the file of before 1.1.206 -------------------------------------------------------------------------------------------------------

    def take_over_tags_file(self, root: Path) -> None:
        """Before 1.1.206 the tags of a mailbox were in one file, tags.json: each message's tags move into its own properties
        file, and only then is the old file removed. Tags already in a properties file are kept (an interrupted earlier run
        wrote them, or they changed since). A damaged old file is renamed to tags.json.bad, never removed."""
        old = root / OLD_TAGS_FILE
        if not old.exists():
            return
        try:
            if old.stat().st_size > MAXIMUM_OLD_TAGS_FILE_BYTES:
                raise ValueError("far too large")
            data = json.loads(old.read_text("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("messages"), dict):
                raise ValueError("not a tags file")
            entries = {
                store_id: clean_tags([tag for tag in tags if isinstance(tag, str)])
                for store_id, tags in data["messages"].items()
                if isinstance(store_id, str) and _STORE_ID.fullmatch(store_id) and isinstance(tags, list)
            }
        except (OSError, UnicodeDecodeError, ValueError, StorageError):
            try:
                os.replace(old, old.with_name(OLD_TAGS_FILE + _BAD_SUFFIX))
            except OSError:
                pass
            return
        for store_id, tags in entries.items():
            if tags:
                try:
                    self.change(store_id, _unless_tagged(tags))
                except StorageError:
                    return  # the old file stays and is taken over at the next opening
        try:
            old.unlink()
        except OSError:
            pass  # taken over again at the next opening: the tags already there are kept
