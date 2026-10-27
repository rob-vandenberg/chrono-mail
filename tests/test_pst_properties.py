import struct
import uuid
from datetime import UTC, date, datetime

from chrono_mail.pst.names import PS_MAPI, PS_PUBLIC_STRINGS, PSETID_APPOINTMENT, parse_name_map
from chrono_mail.pst.properties import (
    BINARY, BOOLEAN, ERROR_CODE, FLOATING64, INTEGER16, INTEGER32, INTEGER64, MULTIPLE_BINARY, MULTIPLE_INTEGER32, MULTIPLE_STRING8,
    MULTIPLE_UNICODE, STRING8, TIME, UNICODE, Properties, codec_for, decode_binaries, decode_float, decode_integer, decode_integers,
    decode_string, decode_strings, decode_time, filetime, nearest_date,
)
from tests.test_pst_helpers import filetime as ticks
from tests.test_pst_helpers import multi

# --- Version ------------------------------------------------------------
__version__ = "test_pst_properties.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: property values from raw bytes, and the map of named properties


def test_integers_and_booleans() -> None:
    assert decode_integer(INTEGER16, struct.pack("<h", -2)) == -2
    assert decode_integer(INTEGER32, struct.pack("<i", 123456)) == 123456
    assert decode_integer(INTEGER64, struct.pack("<q", 2**40)) == 2**40
    assert decode_integer(ERROR_CODE, struct.pack("<I", 0x8004010F)) == 0x8004010F
    assert decode_integer(BOOLEAN, b"\x01") == 1
    assert decode_integer(BOOLEAN, b"\x00\x00\x00\x00") == 0
    assert decode_integer(INTEGER32, b"\x01\x02") is None  # too short: never read past the value
    assert decode_integer(BINARY, b"\x01\x02\x03\x04") is None
    assert decode_float(FLOATING64, struct.pack("<d", 0.25)) == 0.25


def test_times() -> None:
    moment = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)
    assert decode_time(TIME, struct.pack("<Q", ticks(moment))) == moment
    assert filetime(0) is None
    assert filetime(ticks(datetime(4501, 1, 1, tzinfo=UTC))) is None  # the program's "no date"
    assert filetime(2**63 - 1) is None
    assert decode_time(TIME, b"\x00" * 4) is None
    assert nearest_date(datetime(2026, 5, 3, 22, 0)) == date(2026, 5, 4)  # local midnight east of UTC, kept as UTC
    assert nearest_date(datetime(2026, 5, 4, 5, 0)) == date(2026, 5, 4)


def test_strings() -> None:
    assert decode_string(UNICODE, "Grüße\x00".encode("utf-16-le")) == "Grüße"
    assert decode_string(UNICODE, b"A\x00B") == "A"  # an odd last byte is left out
    assert decode_string(STRING8, "café".encode("cp1252")) == "café"
    assert decode_string(STRING8, "Привет".encode("koi8_r"), 20866) == "Привет"
    assert codec_for(28592) == "iso8859_2"
    assert codec_for(99999) == "cp1252"


def test_multiple_values() -> None:
    data = multi(["one".encode("utf-16-le"), "two".encode("utf-16-le")])
    assert decode_strings(MULTIPLE_UNICODE, data) == ("one", "two")
    assert decode_strings(MULTIPLE_STRING8, multi([b"a", b"b"])) == ("a", "b")
    assert decode_binaries(MULTIPLE_BINARY, multi([b"\x01", b"", b"\x02\x03"])) == (b"\x01", b"", b"\x02\x03")
    assert decode_integers(MULTIPLE_INTEGER32, struct.pack("<ii", 1, -1)) == (1, -1)


def test_damaged_multiple_values_are_refused() -> None:
    assert decode_strings(MULTIPLE_UNICODE, struct.pack("<I", 1_000_000)) is None  # a count larger than the data
    assert decode_binaries(MULTIPLE_BINARY, struct.pack("<II", 1, 999)) is None  # an offset past the end
    assert decode_binaries(MULTIPLE_BINARY, struct.pack("<III", 2, 12, 10) + b"xx") is None  # offsets going backwards
    assert decode_binaries(MULTIPLE_BINARY, b"\x01") is None


def test_properties_read_on_demand() -> None:
    props = Properties({0x0037: (STRING8, "Łódź".encode("cp1250")), 0x3FFD: (INTEGER32, struct.pack("<i", 1250))})
    assert props.text(0x0037) == "Łódź"
    assert props.text(0x1000) == ""
    assert props.integer(None) is None
    assert props.strings(0x0037) == ("Łódź",)


def _entry(number: int, guid_index: int, string: bool, index: int) -> bytes:
    return struct.pack("<IHH", number, (guid_index << 1) | int(string), index)


def test_name_map() -> None:
    own_guid = uuid.UUID("11111111-2222-3333-4444-555555555555").bytes_le
    guids = PSETID_APPOINTMENT + own_guid
    name = "Keywords".encode("utf-16-le")
    strings = struct.pack("<I", len(name)) + name + b"\x00" * 0  # the name at offset 0
    entries = (
        _entry(0x8216, 3, False, 0)  # PSETID_Appointment (the first GUID of the stream), LID 0x8216 -> 0x8000
        + _entry(0, 2, True, 1)  # PS_PUBLIC_STRINGS, the name at offset 0 -> 0x8001
        + _entry(0x1234, 1, False, 2)  # PS_MAPI -> 0x8002
        + _entry(7, 4, False, 3)  # the second GUID of the stream -> 0x8003
        + _entry(1, 9, False, 4)  # a GUID index past the stream: left out
        + _entry(9999, 2, True, 5)  # a name offset past the stream: left out
    )
    names = parse_name_map(guids, entries, strings)
    assert names.id_of(PSETID_APPOINTMENT, 0x8216) == 0x8000
    assert names.id_of_name(PS_PUBLIC_STRINGS, "Keywords") == 0x8001
    assert names.id_of(PS_MAPI, 0x1234) == 0x8002
    assert names.id_of(own_guid, 7) == 0x8003
    assert 0x8004 not in names.by_id and 0x8005 not in names.by_id


def test_name_map_survives_garbage() -> None:
    names = parse_name_map(b"\xff" * 5, b"\xff" * 37, b"\xff" * 3)
    assert names.id_of(PS_MAPI, 0) is None
