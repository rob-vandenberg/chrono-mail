"""Made-up data for the tests of the .pst reader: properties from plain values, the way the data file keeps them."""

import struct
from datetime import UTC, datetime

from chrono_mail.pst.names import PS_PUBLIC_STRINGS, PSETID_ADDRESS, PSETID_APPOINTMENT, PSETID_COMMON, PSETID_MEETING, PSETID_TASK
from chrono_mail.pst.properties import (
    BINARY, BOOLEAN, FLOATING64, INTEGER32, MULTIPLE_BINARY, MULTIPLE_UNICODE, TIME, UNICODE, NamedKey, NameMap, Properties,
)

# --- Version ------------------------------------------------------------
__version__ = "test_pst_helpers.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial helpers for the .pst reader's tests

type Plain = int | bool | float | str | bytes | datetime | tuple[str, ...] | tuple[bytes, ...]

NAMED: list[NamedKey] = [
    NamedKey(PS_PUBLIC_STRINGS, None, "Keywords"),
    *(NamedKey(PSETID_APPOINTMENT, lid) for lid in (0x8205, 0x8208, 0x820D, 0x820E, 0x8215, 0x8216, 0x8233, 0x825E)),
    *(NamedKey(PSETID_COMMON, lid) for lid in (0x8501, 0x8502, 0x8503)),
    *(NamedKey(PSETID_TASK, lid) for lid in (0x8101, 0x8102, 0x8104, 0x8105, 0x810F, 0x8116, 0x811C, 0x8126)),
    *(NamedKey(PSETID_ADDRESS, lid) for lid in (0x8005, 0x802B, 0x8045, 0x8046, 0x8082, 0x8083, 0x8084, 0x8092, 0x8093, 0x8094, 0x80A3, 0x8054, 0x8055)),
    NamedKey(PSETID_MEETING, 0x0003),
]
NAMES: NameMap = NameMap({0x8000 + index: key for index, key in enumerate(NAMED)}, {key: 0x8000 + index for index, key in enumerate(NAMED)})


def named(guid: bytes, lid: int) -> int:
    found = NAMES.id_of(guid, lid)
    assert found is not None
    return found


KEYWORDS: int = NAMES.id_of_name(PS_PUBLIC_STRINGS, "Keywords") or 0


def filetime(moment: datetime) -> int:
    return round((moment - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000)


def multi(pieces: list[bytes]) -> bytes:
    """A variable-size multi-value: the count, the offsets, the values."""
    offset = 4 + 4 * len(pieces)
    head = struct.pack("<I", len(pieces))
    for piece in pieces:
        head += struct.pack("<I", offset)
        offset += len(piece)
    return head + b"".join(pieces)


def encode(value: Plain) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return BOOLEAN, b"\x01" if value else b"\x00"
    if isinstance(value, int):
        return INTEGER32, struct.pack("<i", value)
    if isinstance(value, float):
        return FLOATING64, struct.pack("<d", value)
    if isinstance(value, str):
        return UNICODE, value.encode("utf-16-le")
    if isinstance(value, bytes):
        return BINARY, value
    if isinstance(value, datetime):
        return TIME, struct.pack("<Q", filetime(value))
    if all(isinstance(item, str) for item in value):
        return MULTIPLE_UNICODE, multi([str(item).encode("utf-16-le") for item in value])
    return MULTIPLE_BINARY, multi([item if isinstance(item, bytes) else b"" for item in value])


def props(values: dict[int, Plain]) -> Properties:
    return Properties({tag: encode(value) for tag, value in values.items()}, NAMES)
