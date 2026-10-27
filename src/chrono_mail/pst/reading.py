import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast

from chrono_mail.core.pim import CollectionKind
from chrono_mail.pst.contacts import email_slots
from chrono_mail.pst.convert import convert
from chrono_mail.pst.folders import DISPLAY_NAME, FolderNode, PlacedFolder, place
from chrono_mail.pst.headers import ATTACH_DATA, ATTACH_METHOD, BY_VALUE, EMBEDDED, MESSAGE_CLASS
from chrono_mail.pst.model import PstError, PstFailure, PstItem, PstStore
from chrono_mail.pst.names import NAME_ID_ENTRIES, NAME_ID_GUIDS, NAME_ID_STRINGS, parse_name_map
from chrono_mail.pst.properties import MAXIMUM_MESSAGE_BYTES, Attachment, Message, NameMap, Properties

# --- Version ------------------------------------------------------------
__version__ = "reading.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the data file read through libpff (pypff), in the walk order the import numbers its items by

MAXIMUM_ENTRIES: int = 20_000  # properties of one object
MAXIMUM_RECIPIENTS: int = 10_000
MAXIMUM_ATTACHMENTS: int = 1_000
MAXIMUM_EMBEDDING_DEPTH: int = 5
MAXIMUM_SUB_FOLDERS: int = 10_000
RECORD_KEY = 0x0FF9
MAXIMUM_CONTACTS: int = 500_000  # contacts of all folders together looked through for group members
CONTACT_INDEX_SECONDS: float = 600.0  # the longest the look through the contacts may take; past it the rest counts as missing
CONTACTS_PARTIAL: str = "not every contact could be looked through for the contact groups: some members may be missing"
ALIVE_EVERY: int = 200  # contacts looked through between two signs of life



# --- the reading library: pypff has no type information of its own; these are the parts this reader uses ----------------------------


class _Entry(Protocol):
    @property
    def entry_type(self) -> int: ...

    @property
    def value_type(self) -> int: ...

    @property
    def data(self) -> bytes | None: ...


class _RecordSet(Protocol):
    @property
    def number_of_entries(self) -> int: ...

    def get_entry(self, index: int) -> _Entry: ...


class _Item(Protocol):
    @property
    def identifier(self) -> int: ...

    @property
    def number_of_record_sets(self) -> int: ...

    def get_record_set(self, index: int) -> _RecordSet: ...

    @property
    def number_of_sub_items(self) -> int: ...

    def get_sub_item(self, index: int) -> "_Item": ...


class _Attachment(_Item, Protocol):
    @property
    def size(self) -> int: ...

    def read_buffer(self, size: int) -> bytes: ...


class _Message(_Item, Protocol):
    @property
    def recipients(self) -> _Item | None: ...

    @property
    def number_of_attachments(self) -> int: ...

    def get_attachment(self, index: int) -> _Attachment: ...


class _Folder(_Item, Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def number_of_sub_folders(self) -> int: ...

    def get_sub_folder(self, index: int) -> "_Folder": ...

    @property
    def number_of_sub_messages(self) -> int: ...

    def get_sub_message(self, index: int) -> _Message: ...


class _File(Protocol):
    def open(self, filename: str) -> None: ...

    def close(self) -> None: ...

    @property
    def root_folder(self) -> _Folder: ...

    @property
    def message_store(self) -> _Item: ...

    @property
    def name_to_id_map(self) -> _Item | None: ...

    @property
    def ascii_codepage(self) -> object: ...


# --- from the library's objects to plain data --------------------------------------------------------------------------------------------


def _raw(record_set: _RecordSet, skipped: frozenset[int] = frozenset()) -> dict[int, tuple[int, bytes]]:
    values: dict[int, tuple[int, bytes]] = {}
    count = min(record_set.number_of_entries, MAXIMUM_ENTRIES)
    for index in range(count):
        try:
            entry = record_set.get_entry(index)
            tag = entry.entry_type
            if tag in skipped or tag in values:
                continue  # never read: an attachment's data is read once, after its size is checked
            values[tag] = (entry.value_type, entry.data or b"")
        except (OSError, ValueError, TypeError, MemoryError):
            continue  # a property the library cannot give is left out; the rest of the object stands
    return values


def properties(item: _Item, names: NameMap, record_set: int = 0, codepage: int = 1252, skipped: frozenset[int] = frozenset()) -> Properties:
    """The properties of a record set; 8-bit strings without a code page of their own are read in the given one."""
    try:
        if item.number_of_record_sets <= record_set:
            return Properties({}, names, codepage)
        return Properties(_raw(item.get_record_set(record_set), skipped), names, codepage)
    except (OSError, ValueError, TypeError):
        return Properties({}, names, codepage)


_DATA = frozenset({ATTACH_DATA})
_KEPT_AS_LINK = (2, 3, 4, 7)  # by reference, by reference only, by reference resolved, by web reference: no contents in the file


def _attachment(attachment: _Attachment, names: NameMap, codepage: int, depth: int, budget: list[int]) -> Attachment:
    props = properties(attachment, names, 0, codepage, _DATA)
    method = props.integer(ATTACH_METHOD) or BY_VALUE
    if method == EMBEDDED:
        embedded: Message | None = None
        if depth < MAXIMUM_EMBEDDING_DEPTH and attachment.number_of_sub_items > 0:
            inner = attachment.get_sub_item(0)
            embedded = message_of(cast(_Message, inner), names, depth + 1, budget, codepage)
        if embedded is None:
            raise ValueError("an attached message the library does not give")  # counted, so the report says a part is missing
        return Attachment(props, None, embedded)
    if method in _KEPT_AS_LINK:
        return Attachment(props, None, None)
    size = attachment.size
    if size < 0 or size > budget[0]:
        raise MemoryError("a message larger than 256 MB")
    data = attachment.read_buffer(size) if size else b""
    budget[0] -= len(data)
    return Attachment(props, data, None)


def message_of(item: _Message, names: NameMap, depth: int = 0, budget: list[int] | None = None, codepage: int = 1252) -> Message:
    """One item of the data file as plain data; an attachment the library cannot give is counted, not fatal. Its recipients and
    attachments read 8-bit strings in the message's code page."""
    budget = budget if budget is not None else [MAXIMUM_MESSAGE_BYTES]
    props = properties(item, names, 0, codepage)
    own = props.codepage
    budget[0] -= sum(len(value) for _, value in props.values.values())
    if budget[0] < 0:
        raise MemoryError("a message larger than 256 MB")
    rows: list[Properties] = []
    try:
        table = item.recipients
        if table is not None:
            for index in range(min(table.number_of_record_sets, MAXIMUM_RECIPIENTS)):
                rows.append(properties(table, names, index, own))
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # a message whose recipients cannot be read keeps its headers
    attachments: list[Attachment] = []
    unread = 0
    try:
        count = item.number_of_attachments
    except (OSError, ValueError, TypeError, AttributeError):
        count = 0
    for index in range(min(count, MAXIMUM_ATTACHMENTS)):
        try:
            attachments.append(_attachment(item.get_attachment(index), names, own, depth, budget))
        except MemoryError:
            raise
        except (OSError, ValueError, TypeError, AttributeError):
            unread += 1
    unread += max(0, count - MAXIMUM_ATTACHMENTS)
    return Message(props, tuple(rows), tuple(attachments), _identifier(item), unread)


def _identifier(item: _Item) -> int:
    try:
        return item.identifier
    except (OSError, ValueError, TypeError):
        return 0


def _names(file: _File) -> NameMap:
    try:
        item = file.name_to_id_map
        if item is None:
            return NameMap()
        values = _raw(item.get_record_set(0))
    except (OSError, ValueError, TypeError, AttributeError):
        return NameMap()
    return parse_name_map(*(values.get(tag, (0, b""))[1] for tag in (NAME_ID_GUIDS, NAME_ID_ENTRIES, NAME_ID_STRINGS)))


def _folder_node(folder: _Folder, names: NameMap, codepage: int, depth: int = 0) -> FolderNode:
    props = properties(folder, names, 0, codepage)
    try:
        items = max(0, folder.number_of_sub_messages)
    except (OSError, ValueError, TypeError):
        items = 0
    name = props.text(DISPLAY_NAME) or (_folder_name(folder))
    node = FolderNode(_identifier(folder), name.strip(), props, items, folder)
    if depth < 100:
        try:
            count = min(folder.number_of_sub_folders, MAXIMUM_SUB_FOLDERS)
        except (OSError, ValueError, TypeError):
            count = 0
        for index in range(count):
            try:
                node.children.append(_folder_node(folder.get_sub_folder(index), names, codepage, depth + 1))
            except (OSError, ValueError, TypeError):
                continue  # a folder the library cannot open is left out
    return node


def _folder_name(folder: _Folder) -> str:
    try:
        return folder.name or ""
    except (OSError, ValueError, TypeError):
        return ""


def file_codepage(name: object) -> int:
    """The code page of an older (ANSI) data file's 8-bit strings, as the reading library names it (b"cp1252", "windows-1250"); cp1252
    when it names none."""
    text = name.decode("ascii", errors="replace") if isinstance(name, bytes) else name if isinstance(name, str) else ""
    digits = "".join(character for character in text if character.isdigit())
    return int(digits) if digits and 0 < int(digits) < 70_000 else 1252


class _Opened:
    def __init__(self, path: Path) -> None:
        import pypff  # type: ignore[missing-import]  # libpff's binding has no type information; imported here only, the rest runs without it

        if not path.exists():
            raise PstError(f"{path.name} does not exist")
        try:
            if not pypff.check_file_signature(str(path)):
                raise PstError(f"{path.name} is not a data file of Outlook (.pst)")
            self.file = cast(_File, pypff.file())
            self.file.open(str(path))
        except PstError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise PstError(f"{path.name} cannot be read: it is damaged or not a data file of Outlook") from error
        try:
            self.codepage = file_codepage(self.file.ascii_codepage)
        except (OSError, ValueError, TypeError, AttributeError):
            self.codepage = 1252
        self.names = _names(self.file)
        self.store = properties(self.file.message_store, self.names, 0, self.codepage)
        try:
            root = _folder_node(self.file.root_folder, self.names, self.codepage)
        except (OSError, ValueError, TypeError) as error:
            self.file.close()
            raise PstError(f"{path.name} cannot be read: its folders are damaged") from error
        self.placed = place(self.store, root)
        self.root = root

    def close(self) -> None:
        try:
            self.file.close()
        except (OSError, ValueError):
            pass


def read_store(path: Path) -> PstStore:
    """The data file's name and its folders; PstError with a plain-English reason when it is not a readable data file."""
    opened = _Opened(path)
    try:
        name = opened.store.text(DISPLAY_NAME).strip() or path.stem
        return PstStore(name, tuple(placed.folder for placed in opened.placed))
    finally:
        opened.close()


def walk_items(placed: list[PlacedFolder], start: int) -> Iterator[tuple[int, PlacedFolder, int]]:
    """The numbered positions of the items to import (number, folder, index in the folder), from start on: the readable folders in
    order, their items in order. Folders of kinds Chrono Mail has no place for count nothing."""
    number = 0
    for folder in placed:
        if not folder.readable:
            continue
        count = folder.folder.items
        if number + count <= start:
            number += count
            continue
        for index in range(count):
            if number >= start:
                yield number, folder, index
            number += 1


def total_items(placed: list[PlacedFolder]) -> int:
    return sum(folder.folder.items for folder in placed if folder.readable)


class Progress(Protocol):
    """What a reader tells the one that waits on it: how many items there are, and which one it is about to read."""

    def total(self, count: int) -> None: ...

    def at(self, number: int, folder: tuple[str, ...]) -> None: ...

    def alive(self) -> None:
        """Long work on one item goes on (so the one that waits does not take it for a hang)."""
        ...


class ContactsOfFile:
    """The contacts of the data file by node id, for group members kept as a pointer to one: found in the file itself (its contact
    folders), so a group comes out the same whatever was read before it, in this process or another."""

    def __init__(
        self, placed: list[PlacedFolder], names: NameMap, codepage: int, progress: Progress | None = None, limit: int | None = None,
        seconds: float | None = None,
    ) -> None:
        self.placed = placed
        self.limit = MAXIMUM_CONTACTS if limit is None else limit
        self.seconds = CONTACT_INDEX_SECONDS if seconds is None else seconds
        self.partial = False  # not every contact could be looked through (too many, or too slow): some members count as missing
        self.progress = progress
        self.names = names
        self.codepage = codepage
        self.index: dict[int, tuple[_Folder, int]] | None = None
        self.found: dict[int, tuple[str, tuple[str, ...]] | None] = {}

    def _build_index(self) -> dict[int, tuple[_Folder, int]]:
        index: dict[int, tuple[_Folder, int]] = {}
        deadline = time.monotonic() + self.seconds
        looked = 0
        for folder in self.placed:
            if folder.folder.kind != CollectionKind.CONTACTS:
                continue
            handle = cast(_Folder, folder.node.handle)
            for position in range(folder.folder.items):
                if looked >= self.limit or time.monotonic() > deadline:
                    self.partial = True
                    return index
                looked += 1
                if self.progress is not None and looked % ALIVE_EVERY == 0:
                    self.progress.alive()
                try:
                    index.setdefault(handle.get_sub_message(position).identifier, (handle, position))
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
        return index

    def __call__(self, node: int) -> tuple[str, tuple[str, ...]] | None:
        if node in self.found:
            return self.found[node]
        if self.index is None:
            self.index = self._build_index()
        place_of = self.index.get(node)
        result: tuple[str, tuple[str, ...]] | None = None
        if place_of is not None:
            try:
                props = properties(place_of[0].get_sub_message(place_of[1]), self.names, 0, self.codepage)
                if props.text(MESSAGE_CLASS).strip().upper().startswith("IPM.CONTACT"):
                    result = (props.text(DISPLAY_NAME).strip(), email_slots(props))
            except (OSError, ValueError, TypeError, AttributeError):
                result = None
        self.found[node] = result
        return result


def items_from(
    placed: list[PlacedFolder], names: NameMap, store_key: bytes, codepage: int, start: int, progress: Progress | None,
) -> Iterator[tuple[int, PstItem]]:
    """The items of the placed folders, numbered, from start on. Never raises for one item: what goes wrong is a failure that names the
    kind of fault, never what the item holds."""
    if progress is not None:
        progress.total(total_items(placed))
    contacts = ContactsOfFile(placed, names, codepage, progress)
    noted = False
    for number, folder, index in walk_items(placed, start):
        path_of = folder.folder.path
        if progress is not None:
            progress.at(number, path_of)
        try:
            handle = cast(_Folder, folder.node.handle)
            message = message_of(handle.get_sub_message(index), names, codepage=codepage)
        except MemoryError:
            yield number, PstFailure(path_of, "a message larger than 256 MB")
            continue
        except Exception as error:  # noqa: BLE001 - one item the library cannot give is a failure for that item only; its type, never its values
            yield number, PstFailure(path_of, f"an item that could not be read ({type(error).__name__})")
            continue
        for item in convert(message, path_of, store_key, contacts, number):
            yield number, item
        if contacts.partial and not noted:
            noted = True
            yield number, PstFailure(path_of, CONTACTS_PARTIAL)


def fingerprint(path: Path) -> bytes:
    """A key for a data file that keeps none of its own: its size and its first megabyte, the same on every import of that file."""
    digest = hashlib.sha256()
    try:
        digest.update(path.stat().st_size.to_bytes(8, "little"))
        with path.open("rb") as file:
            digest.update(file.read(1024 * 1024))
    except OSError:
        digest.update(path.name.encode("utf-8", errors="replace"))
    return b"file:" + digest.digest()


def read_items(path: Path, start: int = 0, progress: Progress | None = None) -> Iterator[tuple[int, PstItem]]:
    """Every item to import, numbered in the walk order from 0, from start on; progress is told the total first and the number and
    folder before each item is read (so a reader in another process knows which one it was on when it died). Never raises for one item."""
    opened = _Opened(path)
    try:
        store_key = opened.store.binary(RECORD_KEY) or fingerprint(path)
        yield from items_from(opened.placed, opened.names, store_key, opened.codepage, start, progress)
    finally:
        opened.close()
