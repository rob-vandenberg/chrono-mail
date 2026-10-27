"""The lists, tables and properties layer of a data file (MS-PST 2.3): the heap on a node, its B-trees, property contexts and table
contexts, built as the blocks of one node plus the subnodes their large values need."""

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime

from chrono_mail.pst.writer_ndb import MAXIMUM_BLOCK_DATA, NodeDatabase, SubNode

# --- Version ------------------------------------------------------------
__version__ = "writer_ltp.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: heap on node, BTH, property contexts and table contexts for the write-once data file

MAXIMUM_ALLOCATION: int = 3580
SIGNATURE_PC: int = 0xBC
SIGNATURE_TC: int = 0x7C
SIGNATURE_BTH: int = 0xB5
NID_TYPE_LTP: int = 0x1F

# property types
INTEGER16 = 0x0002
INTEGER32 = 0x0003
FLOATING64 = 0x0005
BOOLEAN = 0x000B
OBJECT = 0x000D
INTEGER64 = 0x0014
UNICODE = 0x001F
TIME = 0x0040
BINARY = 0x0102
MULTIPLE_INTEGER32 = 0x1003
MULTIPLE_UNICODE = 0x101F
MULTIPLE_BINARY = 0x1102

_FIXED_SIZE = {INTEGER16: 2, INTEGER32: 4, 0x0004: 4, FLOATING64: 8, 0x0006: 8, 0x0007: 8, 0x000A: 4, BOOLEAN: 1, INTEGER64: 8, TIME: 8}
_FILETIME_START = datetime(1601, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Prop:
    """One property: its tag (id << 16 | type) and its value as stored."""

    tag: int
    data: bytes

    @property
    def kind(self) -> int:
        return self.tag & 0xFFFF


# --- values ---------------------------------------------------------------------------------------


def filetime(moment: datetime) -> int:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    delta = aware - _FILETIME_START
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def text(tag_id: int, value: str) -> Prop:
    return Prop(tag_id << 16 | UNICODE, value.replace("\x00", "").encode("utf-16-le"))


def integer(tag_id: int, value: int) -> Prop:
    return Prop(tag_id << 16 | INTEGER32, struct.pack("<i", value) if value < 2**31 else struct.pack("<I", value & 0xFFFFFFFF))


def boolean(tag_id: int, value: bool) -> Prop:
    return Prop(tag_id << 16 | BOOLEAN, b"\x01" if value else b"\x00")


def time(tag_id: int, moment: datetime) -> Prop:
    return Prop(tag_id << 16 | TIME, struct.pack("<Q", max(0, filetime(moment))))


def floating(tag_id: int, value: float) -> Prop:
    return Prop(tag_id << 16 | FLOATING64, struct.pack("<d", value))


def binary(tag_id: int, value: bytes) -> Prop:
    return Prop(tag_id << 16 | BINARY, value)


def _multiple(pieces: list[bytes]) -> bytes:
    offsets: list[int] = []
    position = 4 + 4 * len(pieces)
    for piece in pieces:
        offsets.append(position)
        position += len(piece)
    return struct.pack(f"<I{len(pieces)}I", len(pieces), *offsets) + b"".join(pieces)


def texts(tag_id: int, values: list[str]) -> Prop:
    return Prop(tag_id << 16 | MULTIPLE_UNICODE, _multiple([value.encode("utf-16-le") for value in values]))


def binaries(tag_id: int, values: list[bytes]) -> Prop:
    return Prop(tag_id << 16 | MULTIPLE_BINARY, _multiple(values))


def integers(tag_id: int, values: list[int]) -> Prop:
    return Prop(tag_id << 16 | MULTIPLE_INTEGER32, b"".join(struct.pack("<i", value) for value in values))


# --- the heap -------------------------------------------------------------------------------------


def _fill_level(free: int) -> int:
    for level, minimum in enumerate((3584, 2560, 2048, 1792, 1536, 1280, 1024, 768, 512, 256, 128, 64, 32, 16, 8)):
        if free >= minimum:
            return level
    return 15


def _header_size(page: int) -> int:
    if page == 0:
        return 12
    return 66 if page % 128 == 8 else 2


@dataclass
class Heap:
    """A heap on a node: allocations of at most 3,580 bytes in pages of one block each."""

    client: int
    pages: list[list[bytes]] = field(default_factory=lambda: [list[bytes]()])
    root: int = 0

    def _room(self, page: int, extra: int) -> bool:
        allocations = self.pages[page]
        used = _header_size(page) + sum(len(item) for item in allocations) + extra
        used += used & 1
        return used + 4 + 2 * (len(allocations) + 2) <= MAXIMUM_BLOCK_DATA

    def allocate(self, data: bytes) -> int:
        if len(data) > MAXIMUM_ALLOCATION:
            raise ValueError("an allocation larger than the heap allows")
        if not self._room(len(self.pages) - 1, len(data)):
            self.pages.append([])
        page = len(self.pages) - 1
        self.pages[page].append(data)
        return page << 16 | len(self.pages[page]) << 5

    def blocks(self) -> list[bytes]:
        built: list[bytes] = []
        free_levels: list[int] = []
        bodies: list[bytearray] = []
        for number, allocations in enumerate(self.pages):
            body = bytearray(_header_size(number))
            starts: list[int] = []
            for item in allocations:
                starts.append(len(body))
                body += item
            starts.append(len(body))
            if len(body) & 1:
                body += b"\x00"
            map_offset = len(body)
            body += struct.pack(f"<HH{len(starts)}H", len(allocations), 0, *starts)
            struct.pack_into("<H", body, 0, map_offset)
            free_levels.append(_fill_level(MAXIMUM_BLOCK_DATA - len(body)))
            bodies.append(body)
        for number, body in enumerate(bodies):
            if number == 0:
                levels = free_levels[:8] + [0] * (8 - len(free_levels[:8]))
                struct.pack_into("<BBI", body, 2, 0xEC, self.client, self.root)
                struct.pack_into("<I", body, 8, sum(level << (4 * index) for index, level in enumerate(levels)))
            elif number % 128 == 8:
                levels = free_levels[number : number + 128]
                packed = bytearray(64)
                for index, level in enumerate(levels):
                    packed[index // 2] |= level << (4 * (index % 2))
                body[2:66] = packed
            built.append(bytes(body))
        return built


def bth(heap: Heap, key_size: int, entry_size: int, records: list[tuple[bytes, bytes]]) -> int:
    """A B-tree on the heap over the records (key, data), sorted by key; the hid of its header."""
    ordered = sorted(records, key=lambda record: record[0][::-1])  # keys compare as little-endian numbers
    levels = 0
    root = 0
    if ordered:
        size = key_size + entry_size
        level = [(key, key + data) for key, data in ordered]
        while True:
            per = MAXIMUM_ALLOCATION // size
            made: list[tuple[bytes, int]] = []
            for start in range(0, len(level), per):
                part = level[start : start + per]
                made.append((part[0][0], heap.allocate(b"".join(packed for _, packed in part))))
            if len(made) == 1:
                root = made[0][1]
                break
            level = [(key, key + struct.pack("<I", hid)) for key, hid in made]
            size = key_size + 4
            levels += 1
    return heap.allocate(struct.pack("<BBBBI", SIGNATURE_BTH, key_size, entry_size, levels, root))


# --- what a node is built of ----------------------------------------------------------------------


@dataclass
class NodeParts:
    """The blocks of a node and the subnodes it owns."""

    database: NodeDatabase
    subnodes: list[SubNode] = field(default_factory=list[SubNode])
    next_index: int = 1

    def subnode_nid(self, kind: int = NID_TYPE_LTP) -> int:
        nid = self.next_index << 5 | kind
        self.next_index += 1
        return nid

    def value(self, heap: Heap, data: bytes) -> int:
        """An HNID for a variable-size value: a heap allocation, or a subnode when it is larger."""
        if len(data) <= MAXIMUM_ALLOCATION:
            return heap.allocate(data)
        nid = self.subnode_nid()
        self.subnodes.append(SubNode(nid, self.database.bytes_data(data)))
        return nid

    def finish(self, heap: Heap) -> tuple[int, int]:
        """The node's data (the heap) and its subnode tree: (bidData, bidSub)."""
        return self.database.data(heap.blocks()), self.database.subnodes(self.subnodes)


def property_context(parts: NodeParts, props: list[Prop]) -> Heap:
    """A property context over the properties (the last of one tag wins)."""
    heap = Heap(SIGNATURE_PC)
    unique: dict[int, Prop] = {prop.tag >> 16: prop for prop in props}
    records: list[tuple[bytes, bytes]] = []
    for prop_id, prop in unique.items():
        size = _FIXED_SIZE.get(prop.kind)
        if size is not None and size <= 4:
            value = int.from_bytes(prop.data[:size].ljust(4, b"\x00"), "little")
        else:
            value = parts.value(heap, prop.data)
        records.append((struct.pack("<H", prop_id), struct.pack("<HI", prop.kind, value)))
    heap.root = bth(heap, 2, 6, records)
    return heap


@dataclass(frozen=True, slots=True)
class Column:
    tag: int
    offset: int
    size: int
    bit: int


ROW_ID = 0x67F20003
ROW_VERSION = 0x67F30003


def _column_size(tag: int) -> int:
    return _FIXED_SIZE.get(tag & 0xFFFF, 4)


def layout(tags: list[int]) -> tuple[list[Column], tuple[int, int, int, int]]:
    """Where each column sits in a row: 8- and 4-byte values first (the row id and version at the very start), then 2-, then 1-byte
    values, then the cell existence bitmap."""
    rest = [tag for tag in dict.fromkeys(tags) if tag not in (ROW_ID, ROW_VERSION)]
    ordered = [ROW_ID, ROW_VERSION] + sorted((tag for tag in rest if _column_size(tag) >= 4), key=lambda tag: -_column_size(tag))
    ordered += [tag for tag in rest if _column_size(tag) == 2] + [tag for tag in rest if _column_size(tag) == 1]
    position = 0
    placed: dict[int, tuple[int, int]] = {}
    ends = [0, 0, 0]
    for tag in ordered:
        size = _column_size(tag)
        placed[tag] = (position, size)
        position += size
        group = 0 if size >= 4 else 1 if size == 2 else 2
        for later in range(group, 3):
            ends[later] = position
    columns = [Column(tag, placed[tag][0], placed[tag][1], bit) for bit, tag in enumerate(ordered)]
    bitmap = (len(ordered) + 7) // 8
    return columns, (ends[0], ends[1], ends[2], position + bitmap)


def table_context(parts: NodeParts, tags: list[int], rows: list[tuple[int, dict[int, bytes]]]) -> Heap:
    """A table context with these columns and rows (row id, values by tag); a value for a column the table does not have is left out."""
    heap = Heap(SIGNATURE_TC)
    columns, ends = layout(tags)
    by_tag = {column.tag: column for column in columns}
    row_size = ends[3]
    matrix: list[bytes] = []
    for version, (row_id, values) in enumerate(rows, start=1):
        row = bytearray(row_size)
        cells = {ROW_ID: struct.pack("<I", row_id), ROW_VERSION: struct.pack("<I", version), **values}
        for tag, data in cells.items():
            column = by_tag.get(tag)
            if column is None:
                continue
            if tag & 0xFFFF in _FIXED_SIZE:
                row[column.offset : column.offset + column.size] = data[: column.size].ljust(column.size, b"\x00")
            else:
                if not data:
                    continue
                struct.pack_into("<I", row, column.offset, parts.value(heap, data))
            row[ends[2] + column.bit // 8] |= 0x80 >> (column.bit % 8)
        matrix.append(bytes(row))
    index = bth(heap, 4, 4, [(struct.pack("<I", row_id), struct.pack("<I", number)) for number, (row_id, _) in enumerate(rows)])
    rows_hnid = 0
    if matrix:
        total = b"".join(matrix)
        if len(total) <= MAXIMUM_ALLOCATION:
            rows_hnid = heap.allocate(total)
        else:
            per_block = MAXIMUM_BLOCK_DATA // row_size
            pieces = [b"".join(matrix[start : start + per_block]) for start in range(0, len(matrix), per_block)]
            pieces = [piece.ljust(MAXIMUM_BLOCK_DATA, b"\x00") if number < len(pieces) - 1 else piece for number, piece in enumerate(pieces)]
            rows_hnid = parts.subnode_nid()
            parts.subnodes.append(SubNode(rows_hnid, parts.database.data(pieces)))
    descriptions = b"".join(struct.pack("<IHBB", column.tag, column.offset, column.size, column.bit) for column in sorted(columns, key=lambda column: column.tag))
    info = struct.pack("<BB4HIII", SIGNATURE_TC, len(columns), *ends, index, rows_hnid, 0) + descriptions
    heap.root = heap.allocate(info)
    return heap
