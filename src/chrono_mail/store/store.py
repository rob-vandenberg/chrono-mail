import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from chrono_mail.core import Folder, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.crypto import DataKey, decrypt_message, encrypt_message
from chrono_mail.crypto.cipher import OVERHEAD
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.store.layout import build_name, join_info, new_store_id, parse_name, split_info
from chrono_mail.store.tags import MessageTags

# --- Version ------------------------------------------------------------
__version__ = "store.py 0.5.134"

# --- Version history ----------------------------------------------------
# v0.5.134: tags: a copy gets the tags of the original, a message deleted for good loses them
# v0.5.133: written_together: a bulk import makes a whole batch safe on disk at once instead of every message
# v0.3.110: MessageNotFoundError: the file of a message is not in the store
# v0.2.101: Creating a folder re-reads the folders under a lock (no second folder with the same path when commands run at the same time); ensure_folder; merge_duplicate_folders repairs stores where that happened
# v0.0.45: deliver can keep a message's identity
# v0.0.7: Initial encrypted Maildir store

FORMAT_VERSION: int = 1
TEMPORARY_FILE_MAXIMUM_AGE_SECONDS: float = 36 * 3600  # the Maildir convention
Checkpoint = Callable[[str], None]


_FOLDER_LOCKS: dict[str, threading.RLock] = {}
_FOLDER_LOCKS_GUARD = threading.Lock()


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
        self.tags: MessageTags = MessageTags(root, durable)  # which messages carry which tags
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
            metadata = json.loads((root / "store.json").read_text("utf-8"))
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
                description = json.loads((directory / "folder.json").read_text("utf-8"))
                path = tuple(str(segment) for segment in description["path"])
                folder = Folder(self.account_id, path, FolderRole(description["role"]))
            except (OSError, ValueError, KeyError, TypeError):
                self.damaged_folders.append(directory.name)
                continue
            if path in self._folders:
                self.damaged_folders.append(directory.name)
                continue
            self._folders[path] = _FolderEntry(directory.name, folder)

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
        folder = Folder(self.account_id, path, role)
        with _folder_lock(self.root):
            self.refresh()  # another command may have made it a moment ago
            if path in self._folders:
                raise StorageError("A folder with this path already exists")
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
                    description = json.loads((extra / "folder.json").read_text("utf-8"))
                    path = tuple(str(segment) for segment in description["path"])
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

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        """Rename a folder and everything below it. Safe to repeat after an interruption."""
        Folder(self.account_id, new, FolderRole.OTHER)  # validates the new path
        affected = sorted(path for path in self._folders if path[: len(old)] == old)
        if not affected:
            if any(path[: len(new)] == new for path in self._folders):
                return  # an interrupted rename that had in fact finished: repeating it is harmless
            raise StorageError("Folder not found")
        moves = {path: new + path[len(old) :] for path in affected}
        for target in moves.values():
            if target in self._folders and target not in moves:
                raise StorageError("A folder with the new path already exists")
        for source, target in sorted(moves.items(), key=lambda item: len(item[0]), reverse=True):
            entry = self._folders[source]
            renamed = Folder(self.account_id, target, entry.folder.role)
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
        return MessageRef(self.account_id, folder_path, store_id)

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
        path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
        try:
            return self._decode(path.read_bytes())
        except OSError as error:
            raise StorageError(f"The message could not be read: {error.strerror}") from None

    def locate(self, ref: MessageRef, hint: str | None = None) -> str:
        """The current relative path ("new/..." or "cur/...") of a message inside its folder."""
        path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
        return f"{path.parent.name}/{path.name}"

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
        directory = self._folder_directory(ref.folder_path)
        for _ in range(3):
            path = self._locate(directory, ref.store_id, hint)
            hint = None
            parsed = parse_name(path.name)
            assert parsed is not None
            _, extra = split_info(parsed[1] or "")
            info = join_info(flags, extra)
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
        """Move a message to another folder in one atomic rename. The store id stays the same."""
        source_directory = self._folder_directory(ref.folder_path)
        target_directory = self._folder_directory(target_folder)
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
        return MessageRef(self.account_id, target_folder, ref.store_id)

    def copy(self, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> MessageRef:
        """Copy a message (same flags, new identity). The copy is verified before this returns."""
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
            self.tags.copy(ref.store_id, store_id)
        except StorageError:
            pass  # damaged tags never stop a copy; the tags file keeps its .bad copy for repair
        return MessageRef(self.account_id, target_folder, store_id)

    def delete(self, ref: MessageRef, hint: str | None = None) -> None:
        """Remove a message file for good."""
        path = self._locate(self._folder_directory(ref.folder_path), ref.store_id, hint)
        try:
            self._checkpoint("delete:before-unlink")
            path.unlink()
            if self._durable:
                fsync_directory(path.parent)
        except OSError as error:
            raise StorageError(f"The message could not be deleted: {error.strerror}") from None
        try:
            self.tags.forget(ref.store_id)
        except StorageError:
            pass  # a line left for a message that is gone does no harm

    # --- recovery -----------------------------------------------------------------------------

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
