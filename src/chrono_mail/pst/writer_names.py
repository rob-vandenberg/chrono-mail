"""The map of named properties a written data file uses (MS-PST 2.4.7): each named property gets its id from 0x8000 on as it is first
asked for; the map is written with its streams and hash buckets when the file is closed."""

import struct

from chrono_mail.pst.names import PS_MAPI, PS_PUBLIC_STRINGS
from chrono_mail.pst.writer_ltp import Prop, binary, integer
from chrono_mail.pst.writer_ndb import crc

# --- Version ------------------------------------------------------------
__version__ = "writer_names.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: the named properties of a written data file and their map

BUCKETS: int = 251


class NameRegistry:
    """Named property -> property id, given out in the order asked."""

    def __init__(self) -> None:
        self.guids: list[bytes] = []
        self.entries: list[tuple[bytes, int | str]] = []
        self.ids: dict[tuple[bytes, int | str], int] = {}

    def id_of(self, guid: bytes, key: int | str) -> int:
        found = self.ids.get((guid, key))
        if found is None:
            found = 0x8000 + len(self.entries)
            self.entries.append((guid, key))
            self.ids[(guid, key)] = found
            if guid not in (PS_MAPI, PS_PUBLIC_STRINGS) and guid not in self.guids:
                self.guids.append(guid)
        return found

    def tag(self, guid: bytes, key: int | str, kind: int) -> int:
        """The property id of a named property, as the id half of a tag (shift it by 16 and add the type)."""
        return self.id_of(guid, key) << 16 | kind

    def _guid_index(self, guid: bytes) -> int:
        if guid == PS_MAPI:
            return 1
        if guid == PS_PUBLIC_STRINGS:
            return 2
        return 3 + self.guids.index(guid)

    def props(self) -> list[Prop]:
        """The properties of the name-to-id map node."""
        strings = bytearray()
        entries = bytearray()
        buckets: list[list[bytes]] = [[] for _ in range(BUCKETS)]
        for index, (guid, key) in enumerate(self.entries):
            guid_index = self._guid_index(guid)
            if isinstance(key, str):
                name = key.encode("utf-16-le")
                offset = len(strings)
                strings += struct.pack("<I", len(name)) + name
                strings += bytes(-len(strings) % 4)
                flags = guid_index << 1 | 1
                entries += struct.pack("<IHH", offset, flags, index)
                hashed = crc(name)
                buckets[(hashed ^ flags) % BUCKETS].append(struct.pack("<IHH", hashed, flags, index))
            else:
                flags = guid_index << 1
                entries += struct.pack("<IHH", key, flags, index)
                buckets[(key ^ flags) % BUCKETS].append(struct.pack("<IHH", key, flags, index))
        props = [integer(0x0001, BUCKETS), binary(0x0002, b"".join(self.guids)), binary(0x0003, bytes(entries)), binary(0x0004, bytes(strings))]
        props += [binary(0x1000 + number, b"".join(bucket)) for number, bucket in enumerate(buckets)]
        return props
