import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chrono_mail.core import CryptoError, Folder, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.crypto import DataKey, decrypt_message, encrypt_message
from chrono_mail.crypto.cipher import OVERHEAD
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.store.layout import build_name, join_info, new_store_id, parse_name, split_info
from chrono_mail.store.properties import MessageProperties
from chrono_mail.core.folder import OWN_FOLDERS, own_role
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "store.py 4.2.403"

# --- Version history ----------------------------------------------------
# v4.2.403: make_parents_real: a folder that is only the parent of others becomes a folder of its own (D-285); a rename takes the
#           folder lock, refuses a move into itself, and (noted: mail kept here) keeps RENAME_NOTE while its folders move:
#           finish_rename_under_way finishes an interrupted one at the next opening; RenameRefused: refused before anything was written
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) carry their role: given when made, once to the ones made
#           before, and by a rename to the folder that takes their place; one of them that is only the parent of others is made a folder (CR-36)
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash; folder_description: one way to read
#           a folder.json, also for a restore; a description nested without end is damaged, never a crash; has_letter and set_letter: a flag
#           letter the store does not model, put on or taken off by a rename (needs no free space), for the mark of a message the server
#           took (SENT_LETTER)
# v2.5.336: deliver_placed, copy_placed and move_placed give back where they put the message (Placed): nothing reads a whole folder to find it again (D-248, W13)
# v1.9.276: stored_at: when a message's file was written here, the last resort of the Received column (D-226)
# v1.1.227: Private files: encrypted texts the synchronisation keeps beside the mail (its sync conflicts, D-195)
# v1.1.206: properties: one small file per message for what cannot be kept in the message (tags, snooze, archive origin; tags.json taken over
#           once); replace: a message's content replaced under the same id, verified before it takes the old one's place (D-176)
# v0.5.164: one lock per message file around finding and using it (read, flags, copy, rename, delete): a change of flags renames the file, so a read could find a name that was gone a moment later
# v0.5.161: flags: the flags of one message from the name of its file
# v0.5.134: tags: a copy gets the tags of the original, a message deleted for good loses them
# v0.5.133: written_together: a bulk import makes a whole batch safe on disk at once instead of every message
# v0.3.110: MessageNotFoundError: the file of a message is not in the store
# v0.2.101: Creating a folder re-reads the folders under a lock (no second folder with the same path when commands run at the same time); ensure_folder; merge_duplicate_folders repairs stores where that happened
# v0.0.45: deliver can keep a message's identity
# v0.0.7: Initial encrypted Maildir store


def folder_description(text: str) -> tuple[tuple[str, ...], FolderRole]:
    """A mail folder's path and role from the text of its folder.json, as the store reads it (a restore judges a backup's by the same
    rules, RV-196). Raises ValueError, KeyError or TypeError when it cannot be used."""
    description = load_json(text)  # nested without end: a ValueError
    path, role = tuple(str(segment) for segment in description["path"]), FolderRole(description["role"])
    Folder("check", path, role)  # the store refuses an empty path or an empty name in it (ValueError), as Store.refresh does
    return path, role


RECOVERED_PATH: tuple[str, ...] = OWN_FOLDERS[FolderRole.RECOVERED]
SENT_LETTER: str = "P"  # Maildir's "passed" on a message of the Outbox: the server took it, only its move to Sent is left (never sent again)


FORMAT_VERSION: int = 1
TEMPORARY_FILE_MAXIMUM_AGE_SECONDS: float = 36 * 3600  # the Maildir convention
Checkpoint = Callable[[str], None]


_FOLDER_LOCKS: dict[str, threading.RLock] = {}
_FOLDER_LOCKS_GUARD = threading.Lock()


class RenameRefused(StorageError):
    """A folder rename refused before anything was written (no such folder, the new path taken, into itself)."""


RENAME_NOTE: str = "folder-rename"  # a private note: the rename of mail kept here that is under way (old and new path), D-285

def _folder_lock(root: Path) -> threading.RLock:
    """One lock per store for changes to its list of folders: commands of this program that run at the same time take turns."""
    with _FOLDER_LOCKS_GUARD:
        return _FOLDER_LOCKS.setdefault(str(root.resolve()), threading.RLock())


def _no_checkpoint(name: str) -> None:
    """Crash points: tests replace this to simulate a crash at a named step."""


def _make_safe(files: list[Path]) -> None:
    """Make written files and their renames safe on disk: one sync of everything where the system has it, else each file and folder."""
    sync = getattr(os, "sync", None)
    if sync is not None:
        sync()
        return
    for path in files:  # Windows: no sync of everything; FlushFileBuffers per file (os.fsync needs write access there)
        try:
            descriptor = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except OSError:
            continue  # moved or deleted meanwhile: nothing to make safe
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in {path.parent for path in files}:
        fsync_directory(directory)


_MESSAGE_LOCKS: tuple[threading.RLock, ...] = tuple(threading.RLock() for _ in range(256))


class MessageNotFoundError(StorageError):
    """The file of a message is not in the store (any more)."""


@dataclass(frozen=True, slots=True)
class StoredMessage:
    ref: MessageRef
    flags: frozenset[MessageFlag]
    extra_letters: str  # flag letters we do not model; preserved on every rename
    relative_path: str  # "new/<name>" or "cur/<name>" inside the folder directory
    size: int  # size of the raw message in bytes


@dataclass(frozen=True, slots=True)
class Placed:
    """A message just stored, copied or moved, and where its file is now: "new/<name>" or "cur/<name>" inside its folder (D-248)."""

    ref: MessageRef
    place: str


def _place(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    temporary_files_removed: int
    abandoned_folders_removed: int
    damaged_folders: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FolderEntry:
    directory: str
    folder: Folder


class Store:
    """The message store of one account: encrypted Maildir folders in random directories."""

    def __init__(
        self,
        root: Path,
        account_id: str,
        key: DataKey | None,
        durable: bool = True,
        checkpoint: Checkpoint = _no_checkpoint,
    ) -> None:
        self.root: Path = root
        self.account_id: str = account_id
        self._key: DataKey | None = key
        self._durable: bool = durable
        self._unsynced: list[Path] | None = None  # inside written_together: the files still to be made safe
        self.properties: MessageProperties = MessageProperties(root, durable)  # what cannot be kept in a message: its tags, snooze, archive origin
        self.properties.take_over_tags_file(root)  # before 1.1.206: one tags file for the whole mailbox
        self._checkpoint: Checkpoint = checkpoint
        self._folders: dict[tuple[str, ...], _FolderEntry] = {}
        self.damaged_folders: list[str] = []
        self.refresh()

    # --- opening ------------------------------------------------------------------------------

    @classmethod
    def create(
        cls, root: Path, account_id: str, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint
    ) -> "Store":
        if (root / "store.json").exists():
            raise StorageError("A store already exists here")
        try:
            (root / "folders").mkdir(parents=True, exist_ok=True)
            (root / ".deleted").mkdir(exist_ok=True)
            metadata = {
                "format": FORMAT_VERSION,
                "account_id": account_id,
                "encrypted": key is not None,
                "key_id": key.key_id.hex() if key is not None else None,
            }
            replace_file_atomically(root / "store.json", json.dumps(metadata, indent=2).encode(), durable)
        except OSError as error:
            raise StorageError(f"The store could not be created: {error.strerror}") from None
        return cls(root, account_id, key, durable, checkpoint)

    @classmethod
    def open(cls, root: Path, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint) -> "Store":
        try:
            metadata = load_json((root / "store.json").read_text("utf-8"))
            encrypted, key_id, account_id = bool(metadata["encrypted"]), metadata["key_id"], str(metadata["account_id"])
            if metadata["format"] != FORMAT_VERSION:
                raise StorageError("Unsupported store format")
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageError("The store description (store.json) is missing or damaged") from None
        if encrypted and key is None:
            raise StorageError("This store is encrypted and needs its key")
        if not encrypted and key is not None:
            raise StorageError("This store is not encrypted; open it without a key")
        if key is not None and key.key_id.hex() != key_id:
            raise StorageError("This is not the key of this store")
        return cls(root, account_id, key, durable, checkpoint)

    @property
    def encrypted(self) -> bool:
        return self._key is not None

    # --- folders ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Read the folder list from disk."""
        self._folders = {}
        self.damaged_folders = []
        folders_root = self.root / "folders"
        if not folders_root.is_dir():
            return
        for directory in sorted(folders_root.iterdir()):
            try:
                path, role = folder_description((directory / "folder.json").read_text("utf-8"))
                folder = Folder(self.account_id, path, role)
            except (OSError, ValueError, KeyError, TypeError):
                self.damaged_folders.append(directory.name)
                continue
            if path in self._folders:
                self.damaged_folders.append(directory.name)
                continue
            own = own_role(path)
            if own is not None and role is FolderRole.OTHER:  # one of Chrono Mail's own folders, made before they had a role: it gets it once
                folder = Folder(self.account_id, path, own)
                try:
                    self._write_description(directory, folder)
                except OSError:
                    pass  # written at the next start; the role holds in this run all the same
            self._folders[path] = _FolderEntry(directory.name, folder)

    def make_own_parents(self) -> None:
        """Recovered, the one of Chrono Mail's own folders that holds others, when it is only their parent (Recovered/<folder> made before
        3.6.354): a folder of its own, with its role, so it is known as what it is (CR-36)."""
        if RECOVERED_PATH in self.virtual_parent_paths():
            self.ensure_folder(RECOVERED_PATH)

    def _rename_note_left(self) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """The rename named by RENAME_NOTE (old, new); None when there is none. A note that cannot be read is dropped: it names
        nothing that can be finished, and the folders stay exactly as they are."""
        try:
            data = json.loads(self.read_private(RENAME_NOTE) or "null")
            if data is None:
                return None
            old, new = tuple(str(part) for part in data["old"]), tuple(str(part) for part in data["new"])
            if old and new:
                return old, new
        except (StorageError, ValueError, KeyError, TypeError):
            pass
        self._drop_rename_note()
        return None

    def _drop_rename_note(self) -> None:
        try:
            self.drop_private(RENAME_NOTE)
        except StorageError:
            pass  # left: finishing it again later changes nothing

    def finish_rename_under_way(self) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """A rename that was interrupted (the program stopped halfway) is finished; returns it (old, new) so the index can follow, None
        when none was under way or it could not be finished now. Refused (its folder gone meanwhile): forgotten, nothing moves. Another
        failure (a full disk): the note stays and it is tried again at the next opening (D-285)."""
        with _folder_lock(self.root):
            under_way = self._rename_note_left()
            if under_way is None:
                return None
            self.refresh()
            try:
                self._move_folders(self._rename_moves(*under_way))
            except RenameRefused:
                self._drop_rename_note()
                return None
            except StorageError:
                return None
            self._drop_rename_note()
            return under_way

    def make_parents_real(self) -> None:
        """Every folder that is only the parent of others (made by an import, or by a folder made inside it before 4.2.403) becomes
        a folder of its own, empty, so it is a normal folder like every other: it holds mail, is renamed and deleted (D-285).
        Only for mail kept on this computer: in an IMAP account the server decides which folders exist (D-193). Each folder is
        made in one atomic step; an interruption or a failure (a full disk) leaves the rest for the next opening, and never stops
        the account from opening."""
        with _folder_lock(self.root):  # never from a stale view of the folders while a rename runs
            self.refresh()
            if self._private_path(RENAME_NOTE).exists():
                return  # a rename under way leaves its new parent implied until it is finished (finish_rename_under_way)
            try:
                for path in sorted(self.virtual_parent_paths(), key=len):
                    self.ensure_folder(path)
            except StorageError:
                pass  # tried again at the next opening

    def list_folders(self) -> list[Folder]:
        return sorted((entry.folder for entry in self._folders.values()), key=lambda folder: folder.path)

    def virtual_parent_paths(self) -> set[tuple[str, ...]]:
        """Parents that have no folder of their own (left over from an interrupted rename)."""
        return {
            path[:length]
            for path in self._folders
            for length in range(1, len(path))
            if path[:length] not in self._folders
        }

    def _write_description(self, directory: Path, folder: Folder) -> None:
        description = {"version": 1, "path": list(folder.path), "role": folder.role.value}
        replace_file_atomically(directory / "folder.json", json.dumps(description).encode(), self._durable)

    def create_folder(self, path: tuple[str, ...], role: FolderRole = FolderRole.OTHER) -> Folder:
        folder = Folder(self.account_id, path, own_role(path) or role)  # Chrono Mail's own folder takes its role wherever it is made
        with _folder_lock(self.root):
            self.refresh()  # another command may have made it a moment ago
            if path in self._folders:
                raise StorageError("A folder with this path already exists")
            if len(path) > 1 and path[:1] == RECOVERED_PATH and RECOVERED_PATH not in self._folders:
                self.create_folder(RECOVERED_PATH)  # Recovered/<folder>: Recovered itself a folder with its role, never only a parent (CR-36)
            directory = self.root / "folders" / f"f-{os.urandom(6).hex()}"
            try:
                for name in ("tmp", "new", "cur"):
                    (directory / name).mkdir(parents=True)
                self._checkpoint("create-folder:before-description")
                self._write_description(directory, folder)
            except OSError as error:
                raise StorageError(f"The folder could not be created: {error.strerror}") from None
            self._folders[path] = _FolderEntry(directory.name, folder)
            return folder

    def ensure_folder(self, path: tuple[str, ...], role: FolderRole = FolderRole.OTHER) -> tuple[Folder, bool]:
        """The folder with this path, created when there is none (and whether it was created). Commands that run at the
        same time get the same folder."""
        with _folder_lock(self.root):
            self.refresh()
            entry = self._folders.get(path)
            if entry is not None:
                return entry.folder, False
            return self.create_folder(path, role), True

    def merge_duplicate_folders(self) -> int:
        """Repair a store with two folders of the same path (made by commands that ran at the same time before 0.2.101):
        the messages of the extra folders move into the first one (one rename each, same store id), then each emptied
        extra folder goes to .deleted. Nothing is erased; repeating it after an interruption finishes the job. Returns the
        number of messages moved."""
        moved = 0
        with _folder_lock(self.root):
            self.refresh()
            folders_root = self.root / "folders"
            for name in list(self.damaged_folders):
                extra = folders_root / name
                try:
                    path, _ = folder_description((extra / "folder.json").read_text("utf-8"))
                except (OSError, ValueError, KeyError, TypeError):
                    continue  # really damaged: left alone
                entry = self._folders.get(path)
                if entry is None or entry.directory == name:
                    continue
                primary = folders_root / entry.directory
                complete = True
                for part in ("new", "cur"):
                    source = extra / part
                    if not source.is_dir():
                        continue
                    for message in sorted(source.iterdir()):
                        target = primary / part / message.name
                        if target.exists():
                            complete = False  # never overwrite: left where it is
                            continue
                        self._checkpoint("merge-folders:before-message")
                        try:
                            os.rename(message, target)
                        except OSError as error:
                            raise StorageError(f"A message could not be moved: {error.strerror}") from None
                        moved += 1
                    if self._durable:
                        fsync_directory(primary / part)
                        fsync_directory(source)
                if not complete:
                    continue
                self._checkpoint("merge-folders:before-retire")
                try:
                    (self.root / ".deleted").mkdir(exist_ok=True)
                    os.rename(extra, self.root / ".deleted" / name)
                    if self._durable:
                        fsync_directory(folders_root)
                        fsync_directory(self.root / ".deleted")
                except OSError as error:
                    raise StorageError(f"The extra folder could not be set aside: {error.strerror}") from None
            self.refresh()
        return moved

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...], noted: bool = False) -> None:
        """Rename a folder and everything below it. Safe to repeat after an interruption. Noted (mail kept on this computer, D-285):
        RENAME_NOTE is written after the checks and dropped when every folder has its new path, so an interrupted rename is finished
        at the next opening; refused while an earlier one could not be finished yet. An IMAP account's renames are followed by the
        synchronisation instead (D-193)."""
        Folder(self.account_id, new, FolderRole.OTHER)  # validates the new path
        if new[: len(old)] == old:
            raise RenameRefused("A folder cannot be moved into itself")
        with _folder_lock(self.root):  # one rename at a time, and another command's opening never makes the new parent while it is only implied here
            self.refresh()
            if noted:
                self._earlier_rename_first()  # its note is never replaced
            moves = self._rename_moves(old, new)  # every refusal before anything is written
            if not moves:
                return  # an interrupted rename that had in fact finished: repeating it is harmless
            if noted:
                self.write_private(RENAME_NOTE, json.dumps({"old": list(old), "new": list(new)}))
            self._move_folders(moves)
            if noted:
                self._drop_rename_note()

    def _earlier_rename_first(self) -> None:
        """Before a noted rename: an earlier one that could not be finished yet is tried once more. Refused (its folder gone): forgotten.
        Still failing: refused with the real cause. Finished here: refused all the same, as the index has yet to follow at the next
        opening (finish_rename_under_way names it once more and moves nothing)."""
        earlier = self._rename_note_left()
        if earlier is None:
            return
        try:
            self._move_folders(self._rename_moves(*earlier))
        except RenameRefused:
            self._drop_rename_note()
            return
        except StorageError as error:
            raise StorageError(f"An earlier rename of a folder could not be finished: {error}") from None
        raise StorageError("An earlier rename of a folder was just finished: try again")

    def _rename_moves(self, old: tuple[str, ...], new: tuple[str, ...]) -> dict[tuple[str, ...], tuple[str, ...]]:
        """Each folder of the rename with its new path; empty when it is done already. Refused before anything is written."""
        affected = sorted(path for path in self._folders if path[: len(old)] == old)
        if not affected:
            if any(path[: len(new)] == new for path in self._folders):
                return {}
            raise RenameRefused("Folder not found")
        moves = {path: new + path[len(old) :] for path in affected}
        for target in moves.values():
            if target in self._folders and target not in moves:
                raise RenameRefused("A folder with the new path already exists")
        return moves

    def _move_folders(self, moves: dict[tuple[str, ...], tuple[str, ...]]) -> None:
        for source, target in sorted(moves.items(), key=lambda item: len(item[0]), reverse=True):
            entry = self._folders[source]
            kept = FolderRole.OTHER if entry.folder.role in OWN_FOLDERS else entry.folder.role  # the role goes with the place for Chrono Mail's own
            renamed = Folder(self.account_id, target, own_role(target) or kept)  # a rename the server made (D-193) is always followed
            self._checkpoint("rename-folder:before-folder")
            try:
                self._write_description(self.root / "folders" / entry.directory, renamed)
            except OSError as error:
                raise StorageError(f"The folder could not be renamed: {error.strerror}") from None
            del self._folders[source]
            self._folders[target] = _FolderEntry(entry.directory, renamed)
            self._checkpoint("rename-folder:after-folder")

    def delete_folder(self, path: tuple[str, ...], require_empty: bool = True) -> None:
        """Take a folder out of the store. Its files stay in .deleted until purge_deleted_folders()."""
        entry = self._entry(path)
        if require_empty and self.list_messages(path):
            raise StorageError("The folder is not empty")
        self._checkpoint("delete-folder:before-move")
        try:
            os.rename(self.root / "folders" / entry.directory, self.root / ".deleted" / entry.directory)
            if self._durable:
                fsync_directory(self.root / "folders")
                fsync_directory(self.root / ".deleted")
        except OSError as error:
            raise StorageError(f"The folder could not be deleted: {error.strerror}") from None
        del self._folders[path]
        self._checkpoint("delete-folder:after-move")

    def purge_deleted_folders(self) -> int:
        """Remove for good what delete_folder() set aside. Returns the number of folders removed."""
        removed = 0
        for directory in sorted((self.root / ".deleted").iterdir()):
            self._checkpoint("purge:before-remove")
            shutil.rmtree(directory)
            removed += 1
        return removed

    def _entry(self, path: tuple[str, ...]) -> _FolderEntry:
        entry = self._folders.get(path)
        if entry is None:
            self.refresh()
            entry = self._folders.get(path)
        if entry is None:
            raise StorageError("Folder not found")
        return entry

    def _folder_directory(self, path: tuple[str, ...]) -> Path:
        return self.root / "folders" / self._entry(path).directory

    # --- messages -----------------------------------------------------------------------------

    def _encode(self, raw: bytes) -> bytes:
        return encrypt_message(raw, self._key) if self._key is not None else raw

    def _decode(self, blob: bytes) -> bytes:
        return decrypt_message(blob, self._key) if self._key is not None else blob

    def _write_blob(self, folder_directory: Path, store_id: str, blob: bytes, flags: frozenset[MessageFlag], extra: str) -> Path:
        temporary = folder_directory / "tmp" / store_id
        info = join_info(flags, extra) if (flags or extra) else None
        destination = folder_directory / ("new" if info is None else "cur") / build_name(store_id, info)
        try:
            self._checkpoint("write:before-temporary")
            write_new_file(temporary, blob, self._durable)
            self._checkpoint("write:after-temporary")
            os.rename(temporary, destination)
            self._checkpoint("write:after-rename")
            if self._durable:
                fsync_directory(destination.parent)
            elif self._unsynced is not None:
                self._unsynced.append(destination)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StorageError(f"The message could not be written: {error.strerror}") from None
        return destination

    def deliver(
        self, folder_path: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag] = frozenset(), verify: bool = True, store_id: str | None = None
    ) -> MessageRef:
        return self.deliver_placed(folder_path, raw, flags, verify, store_id).ref

    def deliver_placed(
        self, folder_path: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag] = frozenset(), verify: bool = True, store_id: str | None = None
    ) -> Placed:
        """Store a complete raw message. With verify, it is read back and compared before this returns.
        A store_id is given when a message is moved here from another store and keeps its identity."""
        directory = self._folder_directory(folder_path)
        if store_id is None:
            store_id = new_store_id()
        elif len(store_id) != 32 or any(character not in "0123456789abcdef" for character in store_id):
            raise StorageError("A message id is 32 lower-case hexadecimal characters")
        destination = self._write_blob(directory, store_id, self._encode(raw), flags, "")
        if verify:
            try:
                stored = self._decode(destination.read_bytes())
            except Exception as error:
                destination.unlink(missing_ok=True)
                raise StorageError(f"The stored message could not be verified: {type(error).__name__}") from None
            if stored != raw:
                destination.unlink(missing_ok=True)
                raise StorageError("The stored message differs from the original")
        self._checkpoint("deliver:done")
        return Placed(MessageRef(self.account_id, folder_path, store_id), _place(destination))

    @contextmanager
    def written_together(self) -> Generator[None]:
        """For a bulk import: the messages delivered inside are written without making each one safe on disk, and all of
        them are made safe at once when this ends (also after an error). Until then a crash can lose or damage them, so
        the caller records them as done only afterwards. A store that is not durable stays as it is."""
        if not self._durable or self._unsynced is not None:
            yield
            return
        self._durable = False
        self._unsynced = []
        try:
            yield
        finally:
            written, self._unsynced = self._unsynced, None
            self._durable = True
            _make_safe(written)

    def _message_lock(self, store_id: str) -> threading.RLock:
        """The lock of one message's file: finding a file and using it (read, rename, delete) is one step, because a
        change of flags renames the file. A fixed set of locks shared by every Store of this process (a store is opened
        per command); two messages may share one, which only makes one wait a moment."""
        return _MESSAGE_LOCKS[hash((str(self.root), store_id)) % len(_MESSAGE_LOCKS)]

    def _locate(self, directory: Path, store_id: str, hint: str | None = None) -> Path:
        if hint is not None and hint.split("/", 1)[0] in ("new", "cur"):
            candidate = directory / hint
            parsed = parse_name(candidate.name)
            if parsed is not None and parsed[0] == store_id and candidate.exists():
                return candidate
        for _ in range(2):  # a concurrent flag change may move the file from new to cur
            candidate = directory / "new" / store_id
            if candidate.exists():
                return candidate
            with os.scandir(directory / "cur") as entries:
                for entry in entries:
                    parsed = parse_name(entry.name)
                    if parsed is not None and parsed[0] == store_id:
                        return Path(entry.path)
        raise MessageNotFoundError("Message not found")

    def read(self, ref: MessageRef, hint: str | None = None) -> bytes:
        """The raw message. Raises CryptoError when the file is damaged."""
        with self._message_lock(ref.store_id):  # a change of flags is a rename: finding and reading are one step
            path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
            try:
                blob = path.read_bytes()
            except OSError as error:
                raise StorageError(f"The message could not be read: {error.strerror}") from None
        return self._decode(blob)

    def locate(self, ref: MessageRef, hint: str | None = None) -> str:
        """The current relative path ("new/..." or "cur/...") of a message inside its folder."""
        return _place(self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint))

    def stored_at(self, ref: MessageRef, hint: str | None = None) -> datetime | None:
        """When a message's file was written here (its modification time: a rename for a flag or a move keeps it); None when it cannot be told."""
        try:
            return datetime.fromtimestamp(self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint).stat().st_mtime, UTC)
        except (OSError, StorageError, ValueError, OverflowError):
            return None

    def flags(self, ref: MessageRef, hint: str | None = None) -> frozenset[MessageFlag]:
        """The flags of one message, from the name of its file (the folder is not listed)."""
        with self._message_lock(ref.store_id):
            path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
        parsed = parse_name(path.name)
        if parsed is None:
            raise MessageNotFoundError("Message not found")
        return split_info(parsed[1] or "")[0]

    def verify(self, ref: MessageRef, hint: str | None = None) -> None:
        """Decrypt and authenticate one message. Raises StorageError or CryptoError when it is damaged."""
        self.read(ref, hint)

    def list_messages(self, folder_path: tuple[str, ...]) -> list[StoredMessage]:
        directory = self._folder_directory(folder_path)
        found: list[StoredMessage] = []
        for subdirectory in ("new", "cur"):
            with os.scandir(directory / subdirectory) as entries:
                for entry in entries:
                    parsed = parse_name(entry.name)
                    if parsed is None:
                        continue
                    store_id, info = parsed
                    flags, extra = split_info(info or "")
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        continue  # removed while listing
                    found.append(
                        StoredMessage(
                            MessageRef(self.account_id, folder_path, store_id),
                            flags,
                            extra,
                            f"{subdirectory}/{entry.name}",
                            max(0, size - OVERHEAD) if self.encrypted else size,
                        )
                    )
        return found

    def locate_all(self) -> dict[str, tuple[tuple[str, ...], str]]:
        """store id -> (folder path, relative path) for every message file (directory listings only)."""
        found: dict[str, tuple[tuple[str, ...], str]] = {}
        for path, entry in self._folders.items():
            for subdirectory in ("new", "cur"):
                with os.scandir(self.root / "folders" / entry.directory / subdirectory) as entries:
                    for item in entries:
                        parsed = parse_name(item.name)
                        if parsed is not None:
                            found[parsed[0]] = (path, f"{subdirectory}/{item.name}")
        return found

    def all_store_ids(self) -> set[str]:
        """The identity of every message file in every folder (directory listings only)."""
        return set(self.locate_all())

    def set_flags(self, ref: MessageRef, flags: frozenset[MessageFlag], hint: str | None = None) -> str:
        """Change the flags (a rename). Letters we do not model are kept. Returns the new relative path."""
        return self._rename_info(ref, hint, lambda _flags, extra: join_info(flags, extra))

    def has_letter(self, ref: MessageRef, letter: str, hint: str | None = None) -> bool:
        """Whether the message's file name carries this flag letter the store does not model."""
        parsed = parse_name(self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint).name)
        return parsed is not None and letter in split_info(parsed[1] or "")[1]

    def set_letter(self, ref: MessageRef, letter: str, present: bool, hint: str | None = None) -> str:
        """Put a flag letter the store does not model on the message's file name, or take it off: a rename, so it needs no free space on
        the disk. The flags and the other letters stay. Returns the new relative path."""
        if len(letter) != 1 or letter in join_info(frozenset(MessageFlag), ""):
            raise ValueError(f"{letter!r} is not a letter the store leaves alone")

        def changed(flags: frozenset[MessageFlag], extra: str) -> str:
            kept = extra.replace(letter, "")
            return join_info(flags, kept + letter if present else kept)

        return self._rename_info(ref, hint, changed)

    def _rename_info(self, ref: MessageRef, hint: str | None, change: Callable[[frozenset[MessageFlag], str], str]) -> str:
        """Give the message's file name the flag part change makes of its flags and other letters (a rename)."""
        directory = self._folder_directory(ref.folder_path)
        with self._message_lock(ref.store_id):  # no read of this message happens during the rename
            for _ in range(3):
                path = self._locate(directory, ref.store_id, hint)
                hint = None
                parsed = parse_name(path.name)
                assert parsed is not None
                flags, extra = split_info(parsed[1] or "")
                info = change(flags, extra)
                if info == "" and path.parent.name == "new":
                    return f"new/{path.name}"
                target = directory / "cur" / build_name(ref.store_id, info)
                if target == path:
                    return f"cur/{path.name}"
                try:
                    self._checkpoint("flags:before-rename")
                    os.rename(path, target)
                    self._checkpoint("flags:after-rename")
                    if self._durable:
                        fsync_directory(target.parent)
                        if path.parent != target.parent:
                            fsync_directory(path.parent)
                except FileNotFoundError:
                    continue  # someone else changed the file first: look again
                except OSError as error:
                    raise StorageError(f"The flags could not be changed: {error.strerror}") from None
                return f"cur/{target.name}"
            raise StorageError("The message kept changing while its flags were being set")

    def move(self, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> MessageRef:
        return self.move_placed(ref, target_folder, hint).ref

    def move_placed(self, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        """Move a message to another folder in one atomic rename. The store id stays the same."""
        source_directory = self._folder_directory(ref.folder_path)
        target_directory = self._folder_directory(target_folder)
        with self._message_lock(ref.store_id):
            path = self._locate(source_directory, ref.store_id, hint)
            destination = target_directory / path.parent.name / path.name
            if destination.exists():
                raise StorageError("The target folder already holds this message")
            try:
                self._checkpoint("move:before-rename")
                os.rename(path, destination)
                self._checkpoint("move:after-rename")
                if self._durable:
                    fsync_directory(destination.parent)
                    fsync_directory(path.parent)
            except OSError as error:
                raise StorageError(f"The message could not be moved: {error.strerror}") from None
            return Placed(MessageRef(self.account_id, target_folder, ref.store_id), _place(destination))

    def copy(self, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> MessageRef:
        return self.copy_placed(ref, target_folder, hint).ref

    def copy_placed(self, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        """Copy a message (same flags, new identity). The copy is verified before this returns."""
        with self._message_lock(ref.store_id):
            path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
            parsed = parse_name(path.name)
            assert parsed is not None
            flags, extra = split_info(parsed[1] or "")
            try:
                blob = path.read_bytes()
            except OSError as error:
                raise StorageError(f"The message could not be read: {error.strerror}") from None
        store_id = new_store_id()
        destination = self._write_blob(self._folder_directory(target_folder), store_id, blob, flags, extra)
        try:
            if destination.read_bytes() != blob:
                raise StorageError("The copy differs from the original")
        except OSError as error:
            destination.unlink(missing_ok=True)
            raise StorageError(f"The copy could not be verified: {error.strerror}") from None
        try:
            self.properties.copy(ref.store_id, store_id)
        except StorageError:
            pass  # damaged properties never stop a copy; the file keeps its .bad copy for repair
        return Placed(MessageRef(self.account_id, target_folder, store_id), _place(destination))

    def replace(self, ref: MessageRef, raw: bytes, hint: str | None = None) -> None:
        """Replace a message's content, keeping its id, its folder and its flags. Only for the program's own messages that wait to
        be sent (a repeating message records its progress in itself); received mail is never changed. The new version is written
        aside and read back first, and only then takes the old one's place in one rename: an interruption leaves the old version
        or the new one, never neither."""
        directory = self._folder_directory(ref.folder_path)
        blob = self._encode(raw)
        with self._message_lock(ref.store_id):
            path = self._locate(directory, ref.store_id, hint)
            temporary = directory / "tmp" / f"{ref.store_id}.{os.urandom(4).hex()}"
            try:
                self._checkpoint("replace:before-temporary")
                write_new_file(temporary, blob, self._durable)
                self._checkpoint("replace:after-temporary")
                try:
                    stored = self._decode(temporary.read_bytes())
                except Exception as error:
                    raise StorageError(f"The new version of the message could not be verified: {type(error).__name__}") from None
                if stored != raw:
                    raise StorageError("The new version of the message differs from what was written")
                os.replace(temporary, path)
                self._checkpoint("replace:after-rename")
                if self._durable:
                    fsync_directory(path.parent)
            except OSError as error:
                raise StorageError(f"The message could not be replaced: {error.strerror}") from None
            finally:
                temporary.unlink(missing_ok=True)

    def delete(self, ref: MessageRef, hint: str | None = None) -> None:
        """Remove a message file for good."""
        with self._message_lock(ref.store_id):
            path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
            try:
                self._checkpoint("delete:before-unlink")
                path.unlink()
                if self._durable:
                    fsync_directory(path.parent)
            except OSError as error:
                raise StorageError(f"The message could not be deleted: {error.strerror}") from None
        try:
            self.properties.forget(ref.store_id)
        except StorageError:
            pass  # a properties file left for a message that is gone does no harm

    # --- recovery -----------------------------------------------------------------------------

    # --- private files (D-195) ------------------------------------------------------------------

    def _private_path(self, name: str) -> Path:
        if not 0 < len(name) <= 120 or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name):
            raise StorageError("A private file is named with lower-case letters, digits and hyphens")
        return self.root / "private" / name

    def write_private(self, name: str, text: str) -> None:
        """Keep a text beside the mail, encrypted like the messages, written atomically (the old version or the new one, never a mix)."""
        path = self._private_path(name)
        try:
            path.parent.mkdir(exist_ok=True)
            replace_file_atomically(path, self._encode(text.encode("utf-8")), self._durable)
        except OSError as error:
            raise StorageError(f"A file of the synchronisation could not be written: {error.strerror}") from None

    def read_private(self, name: str) -> str | None:
        """A text kept beside the mail, or None when there is none. A damaged one raises (it is never taken for empty)."""
        path = self._private_path(name)
        try:
            return self._decode(path.read_bytes()).decode("utf-8", "replace")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StorageError(f"A file of the synchronisation could not be read: {error.strerror}") from None
        except CryptoError as error:
            raise StorageError(f"A file of the synchronisation could not be read: {error}") from None

    def drop_private(self, name: str) -> None:
        try:
            self._private_path(name).unlink(missing_ok=True)
        except OSError as error:
            raise StorageError(f"A file of the synchronisation could not be removed: {error.strerror}") from None

    def recover(self, temporary_file_maximum_age: float = TEMPORARY_FILE_MAXIMUM_AGE_SECONDS) -> RecoveryReport:
        """Clean up after an interrupted run. Never touches a message file in new or cur."""
        removed = 0
        now = time.time()
        for entry in self._folders.values():
            temporary_directory = self.root / "folders" / entry.directory / "tmp"
            with os.scandir(temporary_directory) as entries:
                for item in list(entries):
                    try:
                        if now - item.stat().st_mtime >= temporary_file_maximum_age:
                            os.unlink(item.path)
                            removed += 1
                    except OSError:
                        continue
        abandoned = 0
        for directory_name in list(self.damaged_folders):
            directory = self.root / "folders" / directory_name
            if (directory / "folder.json").exists():
                continue  # damaged but holds a description: leave it for a person to look at
            if all(not any((directory / name).iterdir()) for name in ("tmp", "new", "cur") if (directory / name).is_dir()):
                shutil.rmtree(directory, ignore_errors=True)
                self.damaged_folders.remove(directory_name)
                abandoned += 1
        return RecoveryReport(removed, abandoned, tuple(self.damaged_folders))
