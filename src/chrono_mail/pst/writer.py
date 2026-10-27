"""A write-once Unicode data file of the office suite's mail program (MS-PST): the messaging layer on top of the node database and
the property layer. Folders are made first (or as needed), items are written to the file as they come (only a short row per item stays
in memory for its folder's contents table), and close writes the folders, their tables, the message store, the map of named
properties and the nodes every data file must have (MS-PST 2.7.1)."""

import os
import struct
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.pst.names import PS_PUBLIC_STRINGS, PSETID_COMMON
from chrono_mail.pst.writer_ltp import (
    ROW_ID, ROW_VERSION, Heap, NodeParts, Prop, binaries, binary, boolean, integer, property_context,
    table_context, text,
)
from chrono_mail.pst.writer_names import NameRegistry
from chrono_mail.pst.writer_ndb import NodeDatabase, SubNode

# --- Version ------------------------------------------------------------
__version__ = "writer.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: the messaging layer of the write-once data file: the store, folders with their roles, items with recipients
#           and attachments, the tables and the mandatory nodes

NID_MESSAGE_STORE = 0x21
NID_NAME_TO_ID_MAP = 0x61
NID_ROOT_FOLDER = 0x122
NID_IPM_SUBTREE = 0x8022
NID_SEARCH_ROOT = 0x8042
NID_SPAM_FOLDER = 0x8062
NID_DELETED_ITEMS = 0x8082
NID_RECIPIENT_TABLE = 0x692
NID_ATTACHMENT_TABLE = 0x671
_EMPTY_NODES = (0x1E1, 0x201, 0x261, 0x281, 0x2A1, 0x321)  # the search queues and lists: empty in a new file
_FIRST_FOLDER_INDEX = 0x405
_FIRST_MESSAGE_INDEX = 0x10000

# the roles the reader finds by the file's own pointers (pst/folders.py)
INBOX, OUTBOX, TRASH, SENT, DRAFTS, JUNK, CALENDAR, CONTACTS, TASKS = "inbox", "outbox", "trash", "sent", "drafts", "junk", "calendar", "contacts", "tasks"

HIERARCHY_COLUMNS = [0x0E300003, 0x0E330014, 0x0E340102, 0x0E380003, 0x3001001F, 0x36020003, 0x36030003, 0x360A000B, 0x3613001F, 0x66350003, 0x66360003, ROW_ID, ROW_VERSION]
CONTENTS_COLUMNS = [
    0x00170003, 0x001A001F, 0x00360003, 0x0037001F, 0x00390040, 0x0042001F, 0x0057000B, 0x0058000B, 0x0070001F, 0x00710102, 0x0E03001F,
    0x0E04001F, 0x0E060040, 0x0E070003, 0x0E080003, 0x0E170003, 0x0E1B000B, 0x0E300003, 0x0E330014, 0x0E340102, 0x0E380003, 0x0E3C0102,
    0x0E3D0102, 0x10800003, 0x10900003, 0x10970003, 0x30080040, 0x65C60003, ROW_ID, ROW_VERSION,
]
FAI_COLUMNS = [0x001A001F, 0x0037001F, 0x0E070003, 0x0E170003, 0x0E300003, 0x0E330014, 0x0E380003, 0x30080040, 0x7C040102, 0x7D010003, ROW_ID, ROW_VERSION]
RECIPIENT_COLUMNS = [
    0x0C150003, 0x0E0F000B, 0x0FF90102, 0x0FFE0003, 0x0FFF0102, 0x3001001F, 0x3002001F, 0x3003001F, 0x300B0102, 0x39000003, 0x39FE001F,
    0x3A20001F, 0x3A40000B, 0x5FF6001F, 0x5FF70102, 0x5FFB0040, 0x5FFD0003, 0x5FFF0003, ROW_ID, ROW_VERSION,
]
ATTACHMENT_COLUMNS = [0x0E200003, 0x3704001F, 0x37050003, 0x370B0003, ROW_ID, ROW_VERSION]

_READ = 0x01


@dataclass
class Attachment:
    props: list[Prop]


@dataclass
class _Folder:
    nid: int
    parent: int
    name: str
    container: str
    hidden: bool = False
    children: list[int] = field(default_factory=list[int])
    rows: list[tuple[int, dict[int, bytes]]] = field(default_factory=list[tuple[int, dict[int, bytes]]])
    unread: int = 0
    extra: list[Prop] = field(default_factory=list[Prop])


def _row(props: list[Prop], columns: list[int]) -> dict[int, bytes]:
    wanted = set(columns)
    return {prop.tag: prop.data for prop in props if prop.tag in wanted}


class PstWriter:
    """Write one data file: make folders, add items, then close (or abandon, which removes the file)."""

    def __init__(self, path: Path, display_name: str) -> None:
        self.path = path
        self.display_name = display_name
        self.database = NodeDatabase(path)
        self.names = NameRegistry()
        self.names.id_of(PS_PUBLIC_STRINGS, "Keywords")  # a map without entries or property sets is refused by readers
        self.names.id_of(PSETID_COMMON, 0x8503)
        self.record_key = uuid.uuid4().bytes
        self.folders: dict[int, _Folder] = {}
        self.roles: dict[str, int] = {}
        self.next_folder = _FIRST_FOLDER_INDEX
        self.next_message = _FIRST_MESSAGE_INDEX
        self.items = 0
        self.folders[NID_ROOT_FOLDER] = _Folder(NID_ROOT_FOLDER, NID_ROOT_FOLDER, "", "")
        self._add(NID_IPM_SUBTREE, NID_ROOT_FOLDER, "Top of Personal Folders", "")
        self._add(NID_SEARCH_ROOT, NID_ROOT_FOLDER, "Search Root", "", hidden=False)
        self._add(NID_SPAM_FOLDER, NID_ROOT_FOLDER, "SPAM Search Folder 2", "")

    @property
    def top(self) -> int:
        """The folder the person sees as the top of the data file."""
        return NID_IPM_SUBTREE

    def _add(self, nid: int, parent: int, name: str, container: str, hidden: bool = False) -> int:
        self.folders[nid] = _Folder(nid, parent, name, container, hidden)
        self.folders[parent].children.append(nid)
        return nid

    def entry_id(self, nid: int) -> bytes:
        return bytes(4) + self.record_key + struct.pack("<I", nid)

    def folder(self, parent: int, name: str, container: str = "IPF.Note", role: str = "") -> int:
        """A new folder below parent; a role makes it the data file's standard folder of that role (the first one asked)."""
        if role == TRASH and TRASH not in self.roles and NID_DELETED_ITEMS not in self.folders:
            nid = NID_DELETED_ITEMS
        else:
            nid = self.next_folder << 5 | 0x02
            self.next_folder += 1
        self._add(nid, parent, name, container)
        if role and role not in self.roles:
            self.roles[role] = nid
        return nid

    def add_item(self, folder: int, props: list[Prop], recipients: Sequence[list[Prop]] = (), attachments: Sequence[Attachment] = ()) -> None:
        """One item, written to the file at once: its properties, its recipients and its attachments."""
        nid = self.next_message << 5 | 0x04
        self.next_message += 1
        database = self.database
        parts = NodeParts(database)
        recipient_parts = NodeParts(database)
        rows = [(number, {prop.tag: prop.data for prop in row}) for number, row in enumerate(recipients)]
        heap = table_context(recipient_parts, RECIPIENT_COLUMNS, rows)
        data, sub = recipient_parts.finish(heap)
        parts.subnodes.append(SubNode(NID_RECIPIENT_TABLE, data, sub))
        if attachments:
            table_rows: list[tuple[int, dict[int, bytes]]] = []
            for number, attachment in enumerate(attachments):
                attachment_nid = (0x400 + number) << 5 | 0x05
                attachment_parts = NodeParts(database)
                attachment_heap = property_context(attachment_parts, attachment.props)
                data, sub = attachment_parts.finish(attachment_heap)
                parts.subnodes.append(SubNode(attachment_nid, data, sub))
                table_rows.append((attachment_nid, _row(attachment.props, ATTACHMENT_COLUMNS)))
            table_parts = NodeParts(database)
            data, sub = table_parts.finish(table_context(table_parts, ATTACHMENT_COLUMNS, table_rows))
            parts.subnodes.append(SubNode(NID_ATTACHMENT_TABLE, data, sub))
        heap = property_context(parts, props)
        data, sub = parts.finish(heap)
        database.node(nid, data, sub, folder)
        record = self.folders[folder]
        record.rows.append((nid, _row(props, CONTENTS_COLUMNS)))
        flags = next((int.from_bytes(prop.data[:4], "little") for prop in props if prop.tag == 0x0E070003), 0)
        if not flags & _READ:
            record.unread += 1
        self.items += 1

    # --- closing

    def _table(self, nid: int, columns: list[int], rows: list[tuple[int, dict[int, bytes]]]) -> None:
        parts = NodeParts(self.database)
        heap = table_context(parts, columns, rows)
        data, sub = parts.finish(heap)
        self.database.node(nid, data, sub)

    def _context(self, nid: int, props: list[Prop], parent: int = 0) -> None:
        parts = NodeParts(self.database)
        heap: Heap = property_context(parts, props)
        data, sub = parts.finish(heap)
        self.database.node(nid, data, sub, parent)

    def _folder_props(self, record: _Folder) -> list[Prop]:
        props = [
            text(0x3001, record.name), integer(0x3602, len(record.rows)), integer(0x3603, record.unread), boolean(0x360A, bool(record.children)),
        ]
        if record.container:
            props.append(text(0x3613, record.container))
        if record.hidden:
            props.append(boolean(0x10F4, True))
        return props + record.extra

    def _pointers(self) -> list[Prop]:
        """The pointers to the standard folders of other kinds, kept on the root and on the Inbox."""
        props: list[Prop] = []
        for role, tag in ((CALENDAR, 0x36D0), (CONTACTS, 0x36D1), (TASKS, 0x36D4), (DRAFTS, 0x36D7)):
            if role in self.roles:
                props.append(binary(tag, self.entry_id(self.roles[role])))
        if JUNK in self.roles:
            props.append(binaries(0x36D8, [b"", b"", b"", b"", self.entry_id(self.roles[JUNK])]))
        return props

    def _store(self) -> list[Prop]:
        props = [binary(0x0FF9, self.record_key), text(0x3001, self.display_name), binary(0x35E0, self.entry_id(NID_IPM_SUBTREE)),
                 binary(0x35E7, self.entry_id(NID_SEARCH_ROOT)), integer(0x67FF, 0)]
        mask = 0x01 | 0x80
        for role, tag, bit in ((OUTBOX, 0x35E2, 0x04), (TRASH, 0x35E3, 0x08), (SENT, 0x35E4, 0x10)):
            if role in self.roles:
                props.append(binary(tag, self.entry_id(self.roles[role])))
                mask |= bit
        if INBOX in self.roles:
            mask |= 0x02
        props.append(integer(0x35DF, mask))
        return props

    def close(self) -> None:
        """Write everything that waited for the end, then the node database's own structures and the header."""
        if TRASH not in self.roles:
            self.folder(NID_IPM_SUBTREE, "Deleted Items", role=TRASH)
        pointers = self._pointers()
        self.folders[NID_ROOT_FOLDER].extra = list(pointers)
        if INBOX in self.roles:
            self.folders[self.roles[INBOX]].extra = list(pointers)
        for record in self.folders.values():
            self._context(record.nid, self._folder_props(record), record.parent)
            base = record.nid & ~0x1F
            children = [(child, _row(self._folder_props(self.folders[child]), HIERARCHY_COLUMNS)) for child in record.children]
            self._table(base | 0x0D, HIERARCHY_COLUMNS, children)
            self._table(base | 0x0E, CONTENTS_COLUMNS, record.rows)
            self._table(base | 0x0F, FAI_COLUMNS, [])
        for nid, columns in ((0x60D, HIERARCHY_COLUMNS), (0x60E, CONTENTS_COLUMNS), (0x60F, FAI_COLUMNS), (0x610, CONTENTS_COLUMNS),
                             (0x671, ATTACHMENT_COLUMNS), (0x692, RECIPIENT_COLUMNS)):
            self._table(nid, columns, [])
        self._context(NID_MESSAGE_STORE, self._store())
        self._context(NID_NAME_TO_ID_MAP, self.names.props())
        for nid in _EMPTY_NODES:
            self.database.node(nid, self.database.block(b""))
        self.database.close()

    def abandon(self) -> None:
        self.database.abandon()
        try:
            os.unlink(self.path)
        except OSError:
            pass

