"""The node database of a Unicode data file (MS-PST 2.2): the header, the blocks with their trailers, the data trees and subnode
trees, the two B-trees (BBT and NBT) and the allocation maps. Written once, from start to end: blocks go to the file as they come, the
B-trees, the maps and the header when the file is closed."""

import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# --- Version ------------------------------------------------------------
__version__ = "writer_ndb.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: the write-once node database of a Unicode data file (header, blocks, data and subnode trees, BBT, NBT,
#           AMap, PMap and FMap pages)

PAGE_SIZE: int = 512
BLOCK_LIMIT: int = 8192
BLOCK_TRAILER: int = 16
MAXIMUM_BLOCK_DATA: int = BLOCK_LIMIT - BLOCK_TRAILER  # 8176
FIRST_AMAP: int = 0x4400
AMAP_RANGE: int = 496 * 8 * 64  # 253,952 bytes per allocation map page
HEADER_SIZE: int = 564
CRYPT_NONE: int = 0x00

PAGE_BBT = 0x80
PAGE_NBT = 0x81
PAGE_FMAP = 0x82
PAGE_PMAP = 0x83
PAGE_AMAP = 0x84

_BIDS_PER_XBLOCK = (MAXIMUM_BLOCK_DATA - 8) // 8  # 1021
_SUBNODES_PER_BLOCK = (MAXIMUM_BLOCK_DATA - 8) // 24  # 340
_SIENTRIES_PER_BLOCK = (MAXIMUM_BLOCK_DATA - 8) // 16
_BT_BYTES = 488


def crc(data: bytes, start: int = 0) -> int:
    """The data file's CRC (MS-PST 5.3): the CRC-32 table without the inversions before and after."""
    return zlib.crc32(data, start ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def signature(ib: int, bid: int) -> int:
    """ComputeSig (MS-PST 5.5)."""
    value = (ib ^ bid) & 0xFFFFFFFF
    return ((value >> 16) ^ value) & 0xFFFF


def _range_start(index: int) -> int:
    return FIRST_AMAP + index * AMAP_RANGE


def _map_pages(index: int) -> int:
    """How many map pages open the allocation range of this index: its AMap, a PMap every 8 ranges, an FMap at 128 and every 496 after."""
    pages = 1
    if index % 8 == 0:
        pages += 1
    if index >= 128 and (index - 128) % 496 == 0:
        pages += 1
    return pages


@dataclass(frozen=True, slots=True)
class SubNode:
    nid: int
    data: int
    sub: int = 0


@dataclass(frozen=True, slots=True)
class _Node:
    nid: int
    data: int
    sub: int
    parent: int


class NodeDatabase:
    """The file being written: allocate, write blocks, keep the B-tree entries; close writes the rest and the header."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: BinaryIO = open(path, "w+b")  # noqa: SIM115 - open until close, which every caller reaches through a with block
        self.file.write(bytes(FIRST_AMAP))
        self.position = FIRST_AMAP + _map_pages(0) * PAGE_SIZE
        self.used: dict[int, list[tuple[int, int]]] = {0: [(0, _map_pages(0) * PAGE_SIZE)]}  # range -> (offset in range, size)
        self.next_bid = 4
        self.next_page_bid = 1 << 40
        self.blocks: list[tuple[int, int, int]] = []  # bid, ib, cb
        self.nodes: dict[int, _Node] = {}
        self.highest_nid: dict[int, int] = {}

    # --- space

    def _allocate(self, size: int, alignment: int) -> int:
        while True:
            index = (self.position - FIRST_AMAP) // AMAP_RANGE
            start = _range_start(index)
            floor = start + _map_pages(index) * PAGE_SIZE
            if self.position < floor:
                self.position = floor
            self.position = (self.position + alignment - 1) // alignment * alignment
            if self.position + size <= start + AMAP_RANGE:
                offset = self.position
                self.position += size
                if index not in self.used:
                    self.used[index] = [(0, _map_pages(index) * PAGE_SIZE)]
                self.used[index].append((offset - start, size))
                return offset
            self.position = _range_start(index + 1)

    def _write_at(self, offset: int, data: bytes) -> None:
        self.file.seek(offset)
        self.file.write(data)

    # --- blocks

    def _bid(self, internal: bool) -> int:
        bid = self.next_bid
        self.next_bid += 4
        return bid | (2 if internal else 0)

    def block(self, data: bytes, internal: bool = False) -> int:
        """One block (at most 8,176 bytes of data), padded to 64 bytes with its trailer; its bid."""
        if len(data) > MAXIMUM_BLOCK_DATA:
            raise ValueError("a block larger than the format allows")
        bid = self._bid(internal)
        size = (len(data) + BLOCK_TRAILER + 63) // 64 * 64
        ib = self._allocate(size, 64)
        trailer = struct.pack("<HHIQ", len(data), signature(ib, bid), crc(data), bid)
        self._write_at(ib, data + bytes(size - len(data) - BLOCK_TRAILER) + trailer)
        self.blocks.append((bid, ib, len(data)))
        return bid

    def data(self, pieces: list[bytes]) -> int:
        """A node's data as blocks (each piece one block): one block, or a data tree of XBLOCKs (and an XXBLOCK) above them."""
        if not pieces:
            return self.block(b"")
        bids = [self.block(piece) for piece in pieces]
        if len(bids) == 1:
            return bids[0]
        sizes = [len(piece) for piece in pieces]
        xblocks: list[tuple[int, int]] = []
        for start in range(0, len(bids), _BIDS_PER_XBLOCK):
            part = bids[start : start + _BIDS_PER_XBLOCK]
            total = sum(sizes[start : start + _BIDS_PER_XBLOCK])
            body = struct.pack("<BBHI", 0x01, 1, len(part), total) + b"".join(struct.pack("<Q", bid) for bid in part)
            xblocks.append((self.block(body, internal=True), total))
        if len(xblocks) == 1:
            return xblocks[0][0]
        if len(xblocks) > _BIDS_PER_XBLOCK:
            raise ValueError("data larger than the format allows")
        body = struct.pack("<BBHI", 0x01, 2, len(xblocks), sum(sizes)) + b"".join(struct.pack("<Q", bid) for bid, _ in xblocks)
        return self.block(body, internal=True)

    def bytes_data(self, data: bytes) -> int:
        return self.data([data[start : start + MAXIMUM_BLOCK_DATA] for start in range(0, len(data), MAXIMUM_BLOCK_DATA)] or [b""])

    def subnodes(self, entries: list[SubNode]) -> int:
        """A subnode tree (SLBLOCKs, an SIBLOCK above them when they are many); 0 for none."""
        if not entries:
            return 0
        ordered = sorted(entries, key=lambda entry: entry.nid)
        leaves: list[tuple[int, int]] = []
        for start in range(0, len(ordered), _SUBNODES_PER_BLOCK):
            part = ordered[start : start + _SUBNODES_PER_BLOCK]
            body = struct.pack("<BBHI", 0x02, 0, len(part), 0) + b"".join(struct.pack("<QQQ", entry.nid, entry.data, entry.sub) for entry in part)
            leaves.append((part[0].nid, self.block(body, internal=True)))
        if len(leaves) == 1:
            return leaves[0][1]
        if len(leaves) > _SIENTRIES_PER_BLOCK:
            raise ValueError("too many subnodes")
        body = struct.pack("<BBHI", 0x02, 1, len(leaves), 0) + b"".join(struct.pack("<QQ", nid, bid) for nid, bid in leaves)
        return self.block(body, internal=True)

    # --- nodes

    def node(self, nid: int, data: int, sub: int = 0, parent: int = 0) -> None:
        if nid in self.nodes:
            raise ValueError("a node written twice")
        self.nodes[nid] = _Node(nid, data, sub, parent)
        kind = nid & 0x1F
        self.highest_nid[kind] = max(self.highest_nid.get(kind, 0), nid >> 5)

    # --- closing

    def _tree(self, kind: int, entries: list[tuple[int, bytes]], entry_size: int) -> tuple[int, int]:
        """A B-tree of pages over the entries (key, packed entry); the root's (bid, ib)."""
        level: list[tuple[int, int, int]] = []  # first key, bid, ib
        depth = 0
        items = entries
        while True:
            size = entry_size if depth == 0 else 24
            per_page = _BT_BYTES // size
            level = []
            for start in range(0, max(1, len(items)), per_page):
                part = items[start : start + per_page]
                body = b"".join(packed for _, packed in part)
                bid = self.next_page_bid
                self.next_page_bid += 1
                ib = self._allocate(PAGE_SIZE, PAGE_SIZE)
                page = body + bytes(_BT_BYTES - len(body)) + struct.pack("<BBBBI", len(part), per_page, size, depth, 0)
                page += struct.pack("<BBHIQ", kind, kind, signature(ib, bid), crc(page), bid)
                self._write_at(ib, page)
                level.append((part[0][0] if part else 0, bid, ib))
            if len(level) == 1:
                return level[0][1], level[0][2]
            items = [(key, struct.pack("<QQQ", key, bid, ib)) for key, bid, ib in level]
            depth += 1

    def _bbt(self) -> tuple[int, int]:
        entries = sorted(self.blocks)
        return self._tree(PAGE_BBT, [(bid, struct.pack("<QQHHI", bid, ib, cb, 2, 0)) for bid, ib, cb in entries], 24)

    def _nbt(self) -> tuple[int, int]:
        entries = sorted(self.nodes.values(), key=lambda node: node.nid)
        return self._tree(PAGE_NBT, [(node.nid, struct.pack("<QQQII", node.nid, node.data, node.sub, node.parent, 0)) for node in entries], 32)

    def _maps(self, ranges: int) -> int:
        """Every allocation range's map pages; the free bytes of all AMaps."""
        free = 0
        for index in range(ranges):
            start = _range_start(index)
            bits = bytearray(496)
            for offset, size in self.used.get(index, [(0, _map_pages(index) * PAGE_SIZE)]):
                for slot in range(offset // 64, (offset + size + 63) // 64):
                    bits[slot // 8] |= 0x80 >> (slot % 8)
            free += sum(8 - bin(byte).count("1") for byte in bits) * 64
            pages = [(PAGE_AMAP, bytes(bits))]
            if index % 8 == 0:
                pages.append((PAGE_PMAP, b"\xff" * 496))
            if index >= 128 and (index - 128) % 496 == 0:
                pages.append((PAGE_FMAP, b"\xff" * 496))
            for number, (kind, body) in enumerate(pages):
                ib = start + number * PAGE_SIZE
                page = body + struct.pack("<BBHI", kind, kind, 0, crc(body)) + struct.pack("<Q", ib)
                self._write_at(ib, page)
        return free

    def close(self, crypt: int = CRYPT_NONE) -> None:
        """Write the B-trees, the maps and the header, and flush the file to disk."""
        nbt = self._nbt()
        bbt = self._bbt()
        ranges = (self.position - FIRST_AMAP + AMAP_RANGE - 1) // AMAP_RANGE
        end = _range_start(ranges)
        free = self._maps(ranges)
        self.file.truncate(end)
        rgnid = []
        for kind in range(32):
            minimum = 0x10000 if kind == 0x04 else 0x4000 if kind == 0x03 else 0x400
            rgnid.append(max(minimum, self.highest_nid.get(kind, 0) + 1))
        root = struct.pack("<IQQQQQQQQBBH", 0, end, _range_start(ranges - 1), free, 0, nbt[0], nbt[1], bbt[0], bbt[1], 0x02, 0, 0)
        header = bytearray(HEADER_SIZE)
        header[0:4] = b"!BDN"
        struct.pack_into("<2sHHBBII", header, 8, b"SM", 23, 19, 1, 1, 0, 0)
        struct.pack_into("<QQI", header, 24, 0, self.next_page_bid, 1)
        struct.pack_into("<32I", header, 44, *rgnid)
        header[180:252] = root
        header[256:384] = b"\xff" * 128
        header[384:512] = b"\xff" * 128
        header[512] = 0x80
        header[513] = crypt
        struct.pack_into("<Q", header, 516, self.next_bid)
        struct.pack_into("<I", header, 4, crc(bytes(header[8:479])))
        struct.pack_into("<I", header, 524, crc(bytes(header[8:524])))
        self._write_at(0, bytes(header))
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()

    def abandon(self) -> None:
        try:
            self.file.close()
        except OSError:
            pass
