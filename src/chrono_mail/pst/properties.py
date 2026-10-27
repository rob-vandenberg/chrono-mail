import codecs
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

# --- Version ------------------------------------------------------------
__version__ = "properties.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the values of a .pst's properties from their raw bytes (MS-PST 2.3.3, MS-OXCDATA 2.11.1), and named properties

MAXIMUM_MESSAGE_BYTES: int = 256 * 1024 * 1024  # one message with its attachments; more is refused (the same cap as the rest of the program)
MAXIMUM_VALUES: int = 100_000  # the values of one multi-value property

# property types (MS-OXCDATA 2.11.1)
INTEGER16 = 0x0002
INTEGER32 = 0x0003
FLOATING32 = 0x0004
FLOATING64 = 0x0005
CURRENCY = 0x0006
FLOATING_TIME = 0x0007
ERROR_CODE = 0x000A
BOOLEAN = 0x000B
INTEGER64 = 0x0014
STRING8 = 0x001E
UNICODE = 0x001F
TIME = 0x0040
GUID = 0x0048
BINARY = 0x0102
MULTIPLE_INTEGER32 = 0x1003
MULTIPLE_STRING8 = 0x101E
MULTIPLE_UNICODE = 0x101F
MULTIPLE_BINARY = 0x1102

_FILETIME_START = datetime(1601, 1, 1, tzinfo=UTC)
_NO_DATE_YEAR = 4500  # the program writes 4501-01-01 for "no date"

_CODECS: dict[int, str] = {
    65001: "utf-8", 20127: "ascii", 28591: "latin-1", 1200: "utf-16-le", 1201: "utf-16-be", 50220: "iso2022_jp", 50221: "iso2022_jp",
    50222: "iso2022_jp", 51932: "euc_jp", 20932: "euc_jp", 51949: "euc_kr", 936: "gbk", 54936: "gb18030", 52936: "hz", 950: "big5",
    20866: "koi8_r", 21866: "koi8_u", 65000: "utf-7", 10000: "mac_roman",
}


def codec_for(codepage: int) -> str:
    """The Python codec of a Windows code page; cp1252 (what the program wrote by default) for one Python does not know."""
    name = _CODECS.get(codepage)
    if name is None:
        name = f"iso8859_{codepage - 28590}" if 28592 <= codepage <= 28606 else f"cp{codepage}"
    try:
        codecs.lookup(name)
    except LookupError:
        return "cp1252"
    return name


def filetime(ticks: int) -> datetime | None:
    """A FILETIME (100 ns steps since 1601, UTC) as a moment; None for zero, the program's "no date" and what no date can hold."""
    if ticks <= 0:
        return None
    try:
        moment = _FILETIME_START + timedelta(microseconds=ticks // 10)
    except OverflowError:
        return None
    return None if moment.year >= _NO_DATE_YEAR else moment


def minutes_moment(minutes: int) -> datetime | None:
    """Minutes since 1601 (the recurrence blobs' unit) as a wall-clock time without a zone; None when out of range."""
    if minutes < 0:
        return None
    try:
        moment = datetime(1601, 1, 1) + timedelta(minutes=minutes)
    except OverflowError:
        return None
    return None if moment.year >= _NO_DATE_YEAR else moment


def nearest_date(moment: datetime) -> date:
    """The day of a date the program kept as a moment: some write midnight as UTC, some the UTC of local midnight (an hour or more
    either side); the nearest midnight is the day meant."""
    return (moment + timedelta(hours=12)).date()


def decode_integer(value_type: int, data: bytes) -> int | None:
    sizes = {INTEGER16: 2, INTEGER32: 4, ERROR_CODE: 4, INTEGER64: 8, CURRENCY: 8, BOOLEAN: 1}
    size = sizes.get(value_type)
    if size is None or not data:
        return None
    if value_type == BOOLEAN:
        return 1 if any(data[:4]) else 0
    if len(data) < size:
        return None
    return int.from_bytes(data[:size], "little", signed=value_type != ERROR_CODE)


def decode_float(value_type: int, data: bytes) -> float | None:
    if value_type == FLOATING32 and len(data) >= 4:
        return float(struct.unpack_from("<f", data)[0])
    if value_type in (FLOATING64, FLOATING_TIME) and len(data) >= 8:
        return float(struct.unpack_from("<d", data)[0])
    return None


def decode_time(value_type: int, data: bytes) -> datetime | None:
    if value_type != TIME or len(data) < 8:
        return None
    return filetime(int.from_bytes(data[:8], "little"))


def _clean(text: str) -> str:
    end = text.find("\x00")
    return text if end < 0 else text[:end]


def decode_string(value_type: int, data: bytes, codepage: int = 1252) -> str | None:
    if value_type == UNICODE:
        return _clean(data[: len(data) & ~1].decode("utf-16-le", errors="replace"))
    if value_type == STRING8:
        return _clean(data.decode(codec_for(codepage), errors="replace"))
    return None


def _pieces(data: bytes) -> list[bytes] | None:
    """The values of a variable-size multi-value property: a count, the offsets, then the values (MS-PST 2.3.3.4.2)."""
    if len(data) < 4:
        return None
    count = int.from_bytes(data[:4], "little")
    if count > MAXIMUM_VALUES or 4 + 4 * count > len(data):
        return None
    offsets = [int.from_bytes(data[4 + 4 * index : 8 + 4 * index], "little") for index in range(count)]
    pieces: list[bytes] = []
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < count else len(data)
        if not 4 + 4 * count <= offset <= end <= len(data):
            return None
        pieces.append(data[offset:end])
    return pieces


def decode_strings(value_type: int, data: bytes, codepage: int = 1252) -> tuple[str, ...] | None:
    pieces = _pieces(data) if value_type in (MULTIPLE_UNICODE, MULTIPLE_STRING8) else None
    if pieces is None:
        return None
    single = UNICODE if value_type == MULTIPLE_UNICODE else STRING8
    return tuple(text for piece in pieces for text in [decode_string(single, piece, codepage)] if text is not None)


def decode_binaries(value_type: int, data: bytes) -> tuple[bytes, ...] | None:
    if value_type != MULTIPLE_BINARY:
        return None
    pieces = _pieces(data)
    return tuple(pieces) if pieces is not None else None


def decode_integers(value_type: int, data: bytes) -> tuple[int, ...] | None:
    if value_type != MULTIPLE_INTEGER32:
        return None
    count = min(len(data) // 4, MAXIMUM_VALUES)
    return tuple(int.from_bytes(data[4 * index : 4 * index + 4], "little", signed=True) for index in range(count))


@dataclass(frozen=True, slots=True)
class NamedKey:
    """What a named property is: its property set and either its number (LID) or its name."""

    guid: bytes  # 16 bytes, as stored (little-endian GUID)
    lid: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class NameMap:
    """The data file's map of named properties to their property ids (0x8000 and up), both ways."""

    by_id: Mapping[int, NamedKey] = field(default_factory=dict[int, NamedKey])
    by_key: Mapping[NamedKey, int] = field(default_factory=dict[NamedKey, int])

    def id_of(self, guid: bytes, lid: int) -> int | None:
        return self.by_key.get(NamedKey(guid, lid, None))

    def id_of_name(self, guid: bytes, name: str) -> int | None:
        return self.by_key.get(NamedKey(guid, None, name))


@dataclass(frozen=True, slots=True)
class Properties:
    """The properties of one object (a message, a recipient, an attachment, a folder) as raw (type, bytes), read on demand."""

    values: Mapping[int, tuple[int, bytes]] = field(default_factory=dict[int, tuple[int, bytes]])
    names: NameMap = field(default_factory=NameMap)
    fallback_codepage: int = 1252  # a recipient's or attachment's: its message's; a folder's or a message's: the data file's

    @property
    def codepage(self) -> int:
        """The code page of this object's 8-bit strings: its own (PR_MESSAGE_CODEPAGE, then PR_INTERNET_CPID), else the one it was given."""
        for tag in (0x3FFD, 0x3FDE):
            found = self.values.get(tag)
            if found is not None:
                number = decode_integer(found[0], found[1])
                if number is not None and 0 < number < 70_000:
                    return number
        return self.fallback_codepage

    def named(self, guid: bytes, lid: int) -> int | None:
        return self.names.id_of(guid, lid)

    def named_string(self, guid: bytes, name: str) -> int | None:
        return self.names.id_of_name(guid, name)

    def has(self, tag: int | None) -> bool:
        return tag is not None and tag in self.values

    def raw(self, tag: int | None) -> tuple[int, bytes] | None:
        return None if tag is None else self.values.get(tag)

    def integer(self, tag: int | None) -> int | None:
        found = self.raw(tag)
        return None if found is None else decode_integer(*found)

    def boolean(self, tag: int | None) -> bool:
        return bool(self.integer(tag))

    def floating(self, tag: int | None) -> float | None:
        found = self.raw(tag)
        return None if found is None else decode_float(*found)

    def time(self, tag: int | None) -> datetime | None:
        found = self.raw(tag)
        return None if found is None else decode_time(*found)

    def text(self, tag: int | None) -> str:
        """A string property, or empty."""
        found = self.raw(tag)
        if found is None:
            return ""
        return (decode_string(found[0], found[1], self.codepage) or "").strip("\x00")

    def binary(self, tag: int | None) -> bytes | None:
        found = self.raw(tag)
        return found[1] if found is not None and found[0] == BINARY else None

    def strings(self, tag: int | None) -> tuple[str, ...]:
        found = self.raw(tag)
        if found is None:
            return ()
        if found[0] in (UNICODE, STRING8):
            single = decode_string(found[0], found[1], self.codepage)
            return (single,) if single else ()
        return decode_strings(found[0], found[1], self.codepage) or ()

    def binaries(self, tag: int | None) -> tuple[bytes, ...]:
        found = self.raw(tag)
        if found is None:
            return ()
        if found[0] == BINARY:
            return (found[1],)
        return decode_binaries(found[0], found[1]) or ()


@dataclass(frozen=True, slots=True)
class Attachment:
    props: Properties
    data: bytes | None = None  # the attached file (by value)
    embedded: "Message | None" = None  # an attached message


@dataclass(frozen=True, slots=True)
class Message:
    """One item of the data file as plain data: its properties, its recipients and its attachments."""

    props: Properties
    recipients: tuple[Properties, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    node: int = 0  # the data file's node id of the item
    unread_attachments: int = 0  # attachments the reading library could not give
