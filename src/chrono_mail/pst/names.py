import uuid

from chrono_mail.pst.properties import NamedKey, NameMap

# --- Version ------------------------------------------------------------
__version__ = "names.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the map of named properties (MS-PST 2.4.7) and the property sets this import reads


def _guid(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


PS_MAPI: bytes = _guid("00020328-0000-0000-C000-000000000046")
PS_PUBLIC_STRINGS: bytes = _guid("00020329-0000-0000-C000-000000000046")
PSETID_APPOINTMENT: bytes = _guid("00062002-0000-0000-C000-000000000046")
PSETID_TASK: bytes = _guid("00062003-0000-0000-C000-000000000046")
PSETID_ADDRESS: bytes = _guid("00062004-0000-0000-C000-000000000046")
PSETID_COMMON: bytes = _guid("00062008-0000-0000-C000-000000000046")
PSETID_MEETING: bytes = _guid("6ED8DA90-450B-101B-98DA-00AA003F1305")

NAME_ID_GUIDS: int = 0x0002  # PidTagNameidStreamGuid
NAME_ID_ENTRIES: int = 0x0003  # PidTagNameidStreamEntry
NAME_ID_STRINGS: int = 0x0004  # PidTagNameidStreamString
_ENTRY_SIZE = 8
_MAXIMUM_NAME_BYTES = 1024


def parse_name_map(guids: bytes, entries: bytes, strings: bytes) -> NameMap:
    """The named properties of the data file from its three streams. Each entry is 8 bytes: the number (LID) or the offset of the name
    in the string stream, 16 bits of GUID index and string flag, and the property's index above 0x8000. GUID index 1 is PS_MAPI, 2 is
    PS_PUBLIC_STRINGS, from 3 on the GUID stream. A damaged entry is left out, never the map."""
    by_id: dict[int, NamedKey] = {}
    by_key: dict[NamedKey, int] = {}
    for start in range(0, len(entries) - _ENTRY_SIZE + 1, _ENTRY_SIZE):
        number = int.from_bytes(entries[start : start + 4], "little")
        guid_and_kind = int.from_bytes(entries[start + 4 : start + 6], "little")
        index = int.from_bytes(entries[start + 6 : start + 8], "little")
        property_id = 0x8000 + index
        if property_id > 0xFFFE:
            continue
        guid_index = guid_and_kind >> 1
        if guid_index == 1:
            guid = PS_MAPI
        elif guid_index == 2:
            guid = PS_PUBLIC_STRINGS
        elif guid_index >= 3 and (guid_index - 3) * 16 + 16 <= len(guids):
            guid = guids[(guid_index - 3) * 16 : (guid_index - 3) * 16 + 16]
        else:
            continue
        if guid_and_kind & 1:
            if number + 4 > len(strings):
                continue
            size = int.from_bytes(strings[number : number + 4], "little")
            if size > _MAXIMUM_NAME_BYTES or number + 4 + size > len(strings):
                continue
            key = NamedKey(guid, None, strings[number + 4 : number + 4 + size].decode("utf-16-le", errors="replace"))
        else:
            key = NamedKey(guid, number, None)
        by_id[property_id] = key
        by_key.setdefault(key, property_id)
    return NameMap(by_id, by_key)
