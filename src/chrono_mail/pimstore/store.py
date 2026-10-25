import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from chrono_mail.core import CollectionInfo, CollectionKind, CryptoError, PaletteColour, StorageError, is_colour
from chrono_mail.crypto import DataKey, decrypt_message, encrypt_message
from chrono_mail.pimstore.extract import extract
from chrono_mail.pimstore.index import PimIndex
from chrono_mail.store.atomic import fsync_directory, replace_file_atomically, write_new_file
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "store.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v1.1.225: Private files: encrypted texts the synchronisation keeps beside the items (the agreed version of an item changed here, the sync
#           conflicts), written atomically (D-194)
# v1.1.221: The palette belongs to the account (D-191): a calendar keeps none; old_palettes and forget_old_palette take over what 1.1.219 kept
# v1.1.219: A calendar's palette is kept in its collection.json and changed with update_collection (D-191)
# v0.5.159: reporter: every change of an item or a collection is told right after it succeeded (D-146 step 5); a move is told once, as a move
# v0.0.59: Initial encrypted item store for calendars, tasks and contacts

FORMAT_VERSION: int = 1
HEX = frozenset("0123456789abcdef")
MAXIMUM_ITEM_BYTES: int = 32 * 1024 * 1024
Checkpoint = Callable[[str], None]
Reporter = Callable[[str, str, str, str, str, CollectionInfo | None], None]  # kind, collection, item, target collection, new item, the collection


def _no_checkpoint(point: str) -> None:
    return None


def new_id() -> str:
    return os.urandom(16).hex()


def _check_id(value: str, what: str) -> str:
    if len(value) != 32 or any(character not in HEX for character in value):
        raise StorageError(f"A {what} is 32 lower-case hexadecimal characters")
    return value


@dataclass(frozen=True, slots=True)
class OldPalette:
    """The palette a calendar kept in its collection.json before it belonged to the account (1.1.219, 1.1.220): taken over once."""

    collection_id: str
    name: str
    palette: tuple[PaletteColour, ...] | None
    own_name: str


class PimStore:
    """The collections (calendars, task lists, address books) of one account and their items: encrypted files, written atomically, with a rebuildable index.

    Every item is one file holding the text of a calendar object or a vCard. A deleted item is moved to `.deleted`, not erased."""

    reporter: "Reporter | None" = None  # told every change right after it succeeded (the engine turns it into a fact, D-146)
    _hushed: int = 0  # inside a move: its copy and its removal are told as one move

    def _tell(self, kind: str, collection_id: str, item_id: str = "", to_collection: str = "", new_item: str = "", info: CollectionInfo | None = None) -> None:
        if self.reporter is not None and not self._hushed:
            self.reporter(kind, collection_id, item_id, to_collection, new_item, info)

    def __init__(self, root: Path, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint) -> None:
        self.root: Path = root
        self._key: DataKey | None = key
        self._durable: bool = durable
        self._checkpoint: Checkpoint = checkpoint
        self.damaged_collections: list[str] = []
        self.index: PimIndex = self._open_index()
        self.reconcile()

    # --- opening ------------------------------------------------------------------------------

    @classmethod
    def create(cls, root: Path, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint) -> "PimStore":
        if (root / "pim.json").exists():
            raise StorageError("A store for calendars and contacts already exists here")
        try:
            (root / "collections").mkdir(parents=True, exist_ok=True)
            (root / ".deleted").mkdir(exist_ok=True)
            metadata = {"format": FORMAT_VERSION, "encrypted": key is not None, "key_id": key.key_id.hex() if key is not None else None}
            replace_file_atomically(root / "pim.json", json.dumps(metadata, indent=2).encode(), durable)
        except OSError as error:
            raise StorageError(f"The store could not be created: {error.strerror}") from None
        return cls(root, key, durable, checkpoint)

    @classmethod
    def open(cls, root: Path, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint) -> "PimStore":
        try:
            metadata = load_json((root / "pim.json").read_text("utf-8"))
            encrypted, key_id = bool(metadata["encrypted"]), metadata["key_id"]
            if metadata["format"] != FORMAT_VERSION:
                raise StorageError("Unsupported format of the store for calendars and contacts")
        except (OSError, ValueError, KeyError, TypeError):
            raise StorageError("The description of the store for calendars and contacts (pim.json) is missing or damaged") from None
        if encrypted and key is None:
            raise StorageError("This store is encrypted and needs its key")
        if not encrypted and key is not None:
            raise StorageError("This store is not encrypted; open it without a key")
        if key is not None and key.key_id.hex() != key_id:
            raise StorageError("This is not the key of this store")
        return cls(root, key, durable, checkpoint)

    @classmethod
    def open_or_create(cls, root: Path, key: DataKey | None, durable: bool = True, checkpoint: Checkpoint = _no_checkpoint) -> "PimStore":
        return cls.open(root, key, durable, checkpoint) if (root / "pim.json").exists() else cls.create(root, key, durable, checkpoint)

    @property
    def encrypted(self) -> bool:
        return self._key is not None

    def close(self) -> None:
        self.index.close()

    def _open_index(self) -> PimIndex:
        path = self.root / "pim.sqlite"
        try:
            return PimIndex(path)
        except StorageError:  # damaged or of another version: it is never the truth, so it is made again
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            return PimIndex(path)

    def _encode(self, text: str) -> bytes:
        raw = text.encode("utf-8")
        return encrypt_message(raw, self._key) if self._key is not None else raw

    def _decode(self, blob: bytes) -> str:
        raw = decrypt_message(blob, self._key) if self._key is not None else blob
        return raw.decode("utf-8", "replace")

    # --- collections --------------------------------------------------------------------------

    def _collection_directory(self, collection_id: str) -> Path:
        return self.root / "collections" / _check_id(collection_id, "collection id")

    def collections(self, kind: CollectionKind | None = None) -> list[CollectionInfo]:
        found: list[CollectionInfo] = []
        self.damaged_collections = []
        base = self.root / "collections"
        if not base.is_dir():
            return found
        for directory in sorted(base.iterdir()):
            try:
                description = load_json((directory / "collection.json").read_text("utf-8"))
                info = CollectionInfo(
                    directory.name, CollectionKind(description["kind"]), str(description["name"]), str(description.get("colour", "")),
                    bool(description.get("read_only", False)), tuple((str(key), str(value)) for key, value in description.get("server", {}).items()),
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.damaged_collections.append(directory.name)
                continue
            if kind is None or info.kind is kind:
                found.append(info)
        found.sort(key=lambda info: (info.name.casefold(), info.collection_id))
        return found

    def get_collection(self, collection_id: str) -> CollectionInfo:
        _check_id(collection_id, "collection id")
        for info in self.collections():
            if info.collection_id == collection_id:
                return info
        raise StorageError("There is no such calendar, task list or address book")

    def _write_collection(self, info: CollectionInfo) -> None:
        description: dict[str, object] = {"kind": info.kind.value, "name": info.name, "colour": info.colour, "read_only": info.read_only, "server": dict(info.server)}
        replace_file_atomically(self._collection_directory(info.collection_id) / "collection.json", json.dumps(description, indent=2).encode(), self._durable)

    def create_collection(
        self, kind: CollectionKind, name: str, colour: str = "", read_only: bool = False, server: dict[str, str] | None = None, collection_id: str | None = None
    ) -> CollectionInfo:
        name = " ".join(name.split())
        if not name or len(name) > 200:
            raise StorageError("A name of up to 200 characters is needed")
        if colour and not is_colour(colour):
            raise StorageError("A colour is written #rrggbb")
        identifier = _check_id(collection_id, "collection id") if collection_id is not None else new_id()
        directory = self._collection_directory(identifier)
        if directory.exists():
            raise StorageError("That collection exists already")
        try:
            (directory / "items").mkdir(parents=True)
            (directory / "tmp").mkdir()
            info = CollectionInfo(identifier, kind, name, colour.lower(), read_only, tuple((server or {}).items()))
            self._write_collection(info)
            if self._durable:
                fsync_directory(directory.parent)
        except OSError as error:
            shutil.rmtree(directory, ignore_errors=True)
            raise StorageError(f"The collection could not be made: {error.strerror}") from None
        self._tell("collection_made", info.collection_id, info=info)
        return info

    def update_collection(
        self, collection_id: str, name: str | None = None, colour: str | None = None, read_only: bool | None = None, server: dict[str, str] | None = None,
    ) -> CollectionInfo:
        info = self.get_collection(collection_id)
        new_name = " ".join(name.split()) if name is not None else info.name
        if not new_name or len(new_name) > 200:
            raise StorageError("A name of up to 200 characters is needed")
        new_colour = colour if colour is not None else info.colour
        if new_colour and not is_colour(new_colour):
            raise StorageError("A colour is written #rrggbb")
        changed = CollectionInfo(
            info.collection_id, info.kind, new_name, new_colour.lower(), info.read_only if read_only is None else read_only,
            tuple(server.items()) if server is not None else info.server,
        )
        try:
            self._write_collection(changed)
        except OSError as error:
            raise StorageError(f"The collection could not be changed: {error.strerror}") from None
        self._tell("collection_edited", changed.collection_id, info=changed)
        return changed

    # --- private files (D-194) ------------------------------------------------------------------

    def _private_path(self, name: str) -> Path:
        if not 0 < len(name) <= 120 or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name):
            raise StorageError("A private file is named with lower-case letters, digits and hyphens")
        return self.root / "private" / name

    def write_private(self, name: str, text: str) -> None:
        """Keep a text beside the items, encrypted like them, written atomically (the old version or the new one, never a mix)."""
        path = self._private_path(name)
        try:
            path.parent.mkdir(exist_ok=True)
            replace_file_atomically(path, self._encode(text), self._durable)
        except OSError as error:
            raise StorageError(f"A file of the synchronisation could not be written: {error.strerror}") from None

    def read_private(self, name: str) -> str | None:
        """A text kept beside the items, or None when there is none. A damaged one raises (it is never taken for empty)."""
        path = self._private_path(name)
        try:
            return self._decode(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, CryptoError) as error:
            raise StorageError(f"A file of the synchronisation could not be read: {error}") from None

    def drop_private(self, name: str) -> None:
        try:
            self._private_path(name).unlink(missing_ok=True)
        except OSError as error:
            raise StorageError(f"A file of the synchronisation could not be removed: {error.strerror}") from None

    def old_palettes(self) -> list[OldPalette]:
        """The palettes calendars kept themselves before the palette belonged to the account (D-191), in the order of the calendars. A damaged one
        is left out."""
        found: list[OldPalette] = []
        for info in self.collections(CollectionKind.EVENTS):
            try:
                kept = load_json((self._collection_directory(info.collection_id) / "collection.json").read_text("utf-8")).get("palette")
                if kept is None:
                    continue
                colours = kept.get("colours")
                palette = None if colours is None else tuple(PaletteColour(str(entry[0]), str(entry[1]), str(entry[2])) for entry in colours)
                found.append(OldPalette(info.collection_id, info.name, palette, str(kept.get("own_name", ""))))
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
                continue
        return found

    def forget_old_palette(self, collection_id: str) -> None:
        """Write a calendar's collection.json again without the palette it kept (taken over by its account). Told as nothing: nothing shown changes."""
        try:
            self._write_collection(self.get_collection(collection_id))
        except OSError as error:
            raise StorageError(f"The collection could not be changed: {error.strerror}") from None

    def delete_collection(self, collection_id: str) -> None:
        """Take a collection away with its items. The files go to `.deleted`, not away."""
        directory = self._collection_directory(collection_id)
        if not directory.is_dir():
            raise StorageError("There is no such calendar, task list or address book")
        info = next((known for known in self.collections() if known.collection_id == collection_id), None)
        target = self.root / ".deleted" / f"{collection_id}.{int(time.time())}"
        try:
            os.rename(directory, target)
            if self._durable:
                fsync_directory(directory.parent)
        except OSError as error:
            raise StorageError(f"The collection could not be deleted: {error.strerror}") from None
        self.index.remove_collection(collection_id)
        self._tell("collection_removed", collection_id, info=info)

    # --- items --------------------------------------------------------------------------------

    def _item_path(self, collection_id: str, item_id: str) -> Path:
        return self._collection_directory(collection_id) / "items" / _check_id(item_id, "item id")

    def put(self, collection_id: str, text: str, item_id: str | None = None, verify: bool = True) -> str:
        """Store an item (a new one, or a replacement of the item with this id) and index it. The old version is replaced in one step: a crash
        leaves the old or the new item, never a mix. With verify the file is read back and compared before this returns."""
        info = self.get_collection(collection_id)
        if len(text) > MAXIMUM_ITEM_BYTES:
            raise StorageError("The item is far too large")
        identifier = _check_id(item_id, "item id") if item_id is not None else new_id()
        destination = self._item_path(collection_id, identifier)
        replacing = destination.exists()
        directory = destination.parent.parent
        temporary = directory / "tmp" / identifier
        blob = self._encode(text)
        try:
            temporary.unlink(missing_ok=True)
            self._checkpoint("put:before-temporary")
            write_new_file(temporary, blob, self._durable)
            self._checkpoint("put:after-temporary")
            os.replace(temporary, destination)
            self._checkpoint("put:after-rename")
            if self._durable:
                fsync_directory(destination.parent)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StorageError(f"The item could not be written: {error.strerror}") from None
        if verify:
            try:
                stored = self._decode(destination.read_bytes())
            except (OSError, CryptoError) as error:
                raise StorageError(f"The stored item could not be verified: {type(error).__name__}") from None
            if stored != text.encode("utf-8").decode("utf-8", "replace"):
                raise StorageError("The stored item differs from the original")
        self.index.upsert(identifier, collection_id, info.kind, extract(info.kind, text), len(blob))
        self._checkpoint("put:done")
        self._tell("item_changed" if replacing else "item_added", collection_id, identifier, info=info)
        return identifier

    def get(self, collection_id: str, item_id: str) -> str:
        path = self._item_path(collection_id, item_id)
        try:
            return self._decode(path.read_bytes())
        except FileNotFoundError:
            raise StorageError("There is no such item") from None
        except OSError as error:
            raise StorageError(f"The item could not be read: {error.strerror}") from None

    def delete(self, collection_id: str, item_id: str) -> None:
        """Take an item away: its file is moved to `.deleted`, and it is no longer in the index."""
        path = self._item_path(collection_id, item_id)
        if not path.exists():
            raise StorageError("There is no such item")
        target_directory = self.root / ".deleted" / collection_id
        try:
            target_directory.mkdir(parents=True, exist_ok=True)
            os.rename(path, target_directory / f"{item_id}.{int(time.time() * 1000)}")
            if self._durable:
                fsync_directory(path.parent)
        except OSError as error:
            raise StorageError(f"The item could not be deleted: {error.strerror}") from None
        self._checkpoint("delete:after-rename")
        self.index.remove(item_id)
        self._tell("item_removed", collection_id, item_id, info=next((known for known in self.collections() if known.collection_id == collection_id), None))

    def move(self, collection_id: str, item_id: str, target_collection: str) -> str:
        """Put an item in another collection of the same kind and return its id there (a new one). The copy is stored and checked before the original
        goes; when this was interrupted after the copy, doing it again finds the copy and only finishes the job."""
        source, destination = self.get_collection(collection_id), self.get_collection(target_collection)
        if source.kind is not destination.kind:
            raise StorageError("An item can only go to a collection of the same kind")
        text = self.get(collection_id, item_id)
        self._hushed += 1
        try:
            new_id_here = self._move_quietly(collection_id, item_id, target_collection, source.kind, text)
        finally:
            self._hushed -= 1
        self._tell("item_moved", collection_id, item_id, target_collection, new_id_here, info=destination)
        return new_id_here

    def _move_quietly(self, collection_id: str, item_id: str, target_collection: str, kind: CollectionKind, text: str) -> str:
        source_kind = kind
        earlier = self.index.find_uid(target_collection, extract(source_kind, text).uid) if extract(source_kind, text).uid else None
        if earlier is not None and self.get(target_collection, earlier.item_id) == text:
            new_id_here = earlier.item_id
        else:
            new_id_here = self.put(target_collection, text)
        self._checkpoint("move:after-copy")
        self.delete(collection_id, item_id)
        return new_id_here

    def item_ids(self, collection_id: str) -> list[str]:
        """The ids of the items on disk (not from the index)."""
        directory = self._collection_directory(collection_id) / "items"
        try:
            return sorted(entry.name for entry in os.scandir(directory) if entry.is_file() and len(entry.name) == 32 and all(c in HEX for c in entry.name))
        except FileNotFoundError:
            return []
        except OSError as error:
            raise StorageError(f"The collection could not be listed: {error.strerror}") from None

    def verify(self, collection_id: str | None = None) -> list[tuple[str, str]]:
        """Decrypt every item and say which ones are damaged: a list of (collection id, item id)."""
        damaged: list[tuple[str, str]] = []
        for info in self.collections():
            if collection_id is not None and info.collection_id != collection_id:
                continue
            for item in self.item_ids(info.collection_id):
                try:
                    self._decode(self._item_path(info.collection_id, item).read_bytes())
                except (OSError, CryptoError):
                    damaged.append((info.collection_id, item))
        return damaged

    # --- the index ----------------------------------------------------------------------------

    def reconcile(self) -> tuple[int, int]:
        """Make the index agree with the files: items that are on disk and not in the index are added, items in the index that are gone are removed.
        Returns (added, removed). Damaged items are left out of the index and stay on disk."""
        added = removed = 0
        known = self.index.item_ids()
        seen: set[str] = set()
        for info in self.collections():
            for item in self.item_ids(info.collection_id):
                seen.add(item)
                if item in known:
                    continue
                try:
                    text = self.get(info.collection_id, item)
                except (StorageError, CryptoError):
                    continue
                self.index.upsert(item, info.collection_id, info.kind, extract(info.kind, text), self._item_path(info.collection_id, item).stat().st_size)
                added += 1
        for item in known - seen:
            self.index.remove(item)
            removed += 1
        return added, removed

    def rebuild_index(self) -> int:
        """Throw the index away and read every item again. Returns the number of items indexed."""
        self.index.clear()
        count = 0
        for info in self.collections():
            for item in self.item_ids(info.collection_id):
                try:
                    text = self.get(info.collection_id, item)
                except (StorageError, CryptoError):
                    continue
                self.index.upsert(item, info.collection_id, info.kind, extract(info.kind, text), self._item_path(info.collection_id, item).stat().st_size)
                count += 1
        return count

    def purge_deleted(self, older_than_days: float = 30.0) -> int:
        """Erase what was deleted more than this many days ago. Returns the number of files and directories erased."""
        limit = time.time() - older_than_days * 86400
        erased = 0
        base = self.root / ".deleted"
        for path in list(base.iterdir()) if base.is_dir() else []:
            try:
                if path.is_dir() and "." in path.name:  # a collection that was taken away: <id>.<seconds>
                    if int(path.name.rsplit(".", 1)[1]) < limit:
                        shutil.rmtree(path)
                        erased += 1
                elif path.is_dir():  # the items deleted from one collection: <id>.<milliseconds>
                    for item in list(path.iterdir()):
                        if int(item.name.rsplit(".", 1)[1]) / 1000 < limit:
                            item.unlink()
                            erased += 1
                    if not any(path.iterdir()):
                        path.rmdir()
            except (OSError, ValueError, IndexError):
                continue
        return erased
