from dataclasses import dataclass, field
from pathlib import Path
from typing import override

import pytest

from chrono_mail.core.folder import FolderRole
from chrono_mail.core.pim import CollectionKind
from chrono_mail.pst.convert import convert, item_uid, kind_of_class
from chrono_mail.pst.folders import FolderNode, PlacedFolder, place
from chrono_mail.pst.model import PstError, PstEvent, PstFailure, PstGroup, PstMail
from chrono_mail.pst.names import PSETID_ADDRESS, PSETID_APPOINTMENT
from chrono_mail.pst.properties import STRING8, Message
from chrono_mail.pst.reading import file_codepage, items_from, message_of, read_store, walk_items
from tests.test_pst_helpers import NAMES, Plain, encode, named, props

# --- Version ------------------------------------------------------------
__version__ = "test_pst_reading.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: folders and their roles, the numbered walk, items from the reading library's objects (small fakes of them)


def entry_id(node: int) -> bytes:
    return b"\x00" * 4 + b"\x22" * 16 + node.to_bytes(4, "little")


def folder(node: int, name: str, values: dict[int, Plain] | None = None, items: int = 0, children: list[FolderNode] | None = None) -> FolderNode:
    return FolderNode(node, name, props(values or {}), items, None, children or [])


def test_roles_and_kinds_come_from_the_pointers() -> None:
    inbox = folder(0x8062, "Postvak IN", {0x36D0: entry_id(0x8102), 0x36D1: entry_id(0x8122), 0x36D4: entry_id(0x8142), 0x36D7: entry_id(0x80C2),
                                          0x36D8: (b"", b"", b"", b"", entry_id(0x80E2))}, items=3, children=[folder(0x8202, "Sub", items=1)])
    top = folder(0x8022, "Top", children=[
        inbox, folder(0x8082, "Verzonden"), folder(0x80A2, "Prullenbak"), folder(0x80C2, "Concepten"), folder(0x80E2, "Ongewenst"),
        folder(0x8102, "Agenda", {0x3613: "IPF.Appointment"}, items=2), folder(0x8122, "Contactpersonen", {0x3613: "IPF.Contact"}),
        folder(0x8142, "Taken", {0x3613: "IPF.Task"}), folder(0x8162, "Notities", {0x3613: "IPF.StickyNote"}, items=4),
        folder(0x8182, "Journaal", {0x3613: "IPF.Journal"}), folder(0x81A2, "Settings", {0x10F4: True}, items=9),
        folder(0x81C2, "Other calendar", {0x3613: "IPF.Appointment"}), folder(0x81E2, "Inbox"),
    ])
    root = folder(0x122, "", children=[top, folder(0x8042, "Search")])
    store = props({0x35E0: entry_id(0x8022), 0x35E4: entry_id(0x8082), 0x35E3: entry_id(0x80A2)})
    placed = place(store, root)
    by_path = {item.folder.path: item.folder for item in placed}
    assert list(by_path)[:3] == [("Postvak IN",), ("Postvak IN", "Sub"), ("Verzonden",)]  # depth first, the file's order
    assert ("Settings",) not in by_path and ("Search",) not in by_path
    assert by_path[("Postvak IN",)].role == FolderRole.INBOX and by_path[("Postvak IN",)].items == 3
    assert by_path[("Verzonden",)].role == FolderRole.SENT
    assert by_path[("Prullenbak",)].role == FolderRole.TRASH
    assert by_path[("Concepten",)].role == FolderRole.DRAFTS
    assert by_path[("Ongewenst",)].role == FolderRole.JUNK
    assert by_path[("Inbox",)].role is None  # a name is never a role (OR-26)
    assert by_path[("Agenda",)].kind == CollectionKind.EVENTS and by_path[("Agenda",)].main
    assert by_path[("Other calendar",)].kind == CollectionKind.EVENTS and not by_path[("Other calendar",)].main
    assert by_path[("Contactpersonen",)].main and by_path[("Taken",)].main
    assert by_path[("Notities",)].unsupported == "notes" and by_path[("Journaal",)].unsupported == "journal entries"


def test_walk_numbers_and_start() -> None:
    top = folder(1, "Top", children=[
        folder(2, "A", items=2), folder(3, "Notes", {0x3613: "IPF.StickyNote"}, items=5), folder(4, "B", items=3, children=[folder(5, "C", items=1)]),
    ])
    placed = place(props({0x35E0: entry_id(1)}), folder(0, "", children=[top]))
    walked = [(number, item.folder.path, index) for number, item, index in walk_items(placed, 0)]
    assert walked == [(0, ("A",), 0), (1, ("A",), 1), (2, ("B",), 0), (3, ("B",), 1), (4, ("B",), 2), (5, ("B", "C"), 0)]
    assert [(number, index) for number, _, index in walk_items(placed, 3)] == [(3, 1), (4, 2), (5, 0)]
    assert list(walk_items(placed, 6)) == []


# --- small stand-ins for the reading library's objects -------------------------------------------------------------------------------


@dataclass
class FakeEntry:
    entry_type: int
    value_type: int
    data: bytes | None


@dataclass
class FakeRecordSet:
    entries: list[FakeEntry]
    broken: int = -1  # an entry the library cannot give

    @property
    def number_of_entries(self) -> int:
        return len(self.entries)

    def get_entry(self, index: int) -> FakeEntry:
        if index == self.broken:
            raise OSError("unreadable entry")
        return self.entries[index]


def record_set(values: dict[int, Plain]) -> FakeRecordSet:
    return FakeRecordSet([FakeEntry(tag, *encode(value)) for tag, value in values.items()])


class NotToBeRead:
    """An entry whose data must not be fetched (an attachment's contents, read once through read_buffer)."""

    entry_type = 0x3701
    value_type = 0x0102

    @property
    def data(self) -> bytes | None:
        raise AssertionError("the attachment's data was read through its property")


@dataclass
class MixedRecordSet:
    entries: list[FakeEntry | NotToBeRead]

    @property
    def number_of_entries(self) -> int:
        return len(self.entries)

    def get_entry(self, index: int) -> FakeEntry | NotToBeRead:
        return self.entries[index]


@dataclass
class FakeItem:
    sets: list[FakeRecordSet | MixedRecordSet]
    identifier: int = 0
    subs: list["FakeItem"] = field(default_factory=list["FakeItem"])

    @property
    def number_of_record_sets(self) -> int:
        return len(self.sets)

    def get_record_set(self, index: int) -> FakeRecordSet | MixedRecordSet:
        return self.sets[index]

    @property
    def number_of_sub_items(self) -> int:
        return len(self.subs)

    def get_sub_item(self, index: int) -> "FakeItem":
        return self.subs[index]


@dataclass
class FakeAttachment(FakeItem):
    content: bytes = b""

    @property
    def size(self) -> int:
        return len(self.content)

    def read_buffer(self, size: int) -> bytes:
        return self.content[:size]


@dataclass
class FakeMessage(FakeItem):
    recipients: FakeItem | None = None
    attached: list[FakeAttachment | None] = field(default_factory=list[FakeAttachment | None])

    @property
    def number_of_attachments(self) -> int:
        return len(self.attached)

    def get_attachment(self, index: int) -> FakeAttachment:
        found = self.attached[index]
        if found is None:
            raise OSError("unreadable attachment")
        return found


def test_message_from_the_library_objects() -> None:
    inner = FakeMessage([record_set({0x001A: "IPM.Note", 0x0037: "Inner", 0x1000: "Inner body"})])
    message = FakeMessage(
        [FakeRecordSet([FakeEntry(0x001A, *encode("IPM.Note")), FakeEntry(0x0037, *encode("Subject")), FakeEntry(0x1000, *encode("Body"))], broken=1)],
        identifier=0x200024,
        recipients=FakeItem([record_set({0x0C15: 1, 0x3001: "Bob", 0x39FE: "bob@example.net"}), record_set({0x0C15: 2, 0x39FE: "c@example.net"})]),
        attached=[
            FakeAttachment([record_set({0x3705: 1, 0x3707: "a.txt"})], content=b"file data"),
            None,
            FakeAttachment([record_set({0x3705: 5})], subs=[inner]),
        ],
    )
    read = message_of(message, NAMES)
    assert read.node == 0x200024
    assert read.props.text(0x0037) == ""  # the entry the library could not give is left out, the rest stands
    assert read.props.text(0x1000) == "Body"
    assert [row.text(0x39FE) for row in read.recipients] == ["bob@example.net", "c@example.net"]
    assert read.attachments[0].data == b"file data"
    assert read.attachments[1].embedded is not None and read.attachments[1].embedded.props.text(0x0037) == "Inner"
    assert read.unread_attachments == 1
    items = convert(read, ("Inbox",), b"store")
    assert isinstance(items[0], PstMail) and b"message/rfc822" in items[0].raw
    assert isinstance(items[1], PstFailure) and "1 attachment" in items[1].what and "Subject" not in items[1].what


def test_a_message_over_the_limit_is_refused() -> None:
    from chrono_mail.pst import reading

    message = FakeMessage([record_set({0x001A: "IPM.Note"})], attached=[FakeAttachment([record_set({0x3705: 1})], content=b"x" * 64)])
    with pytest.raises(MemoryError):
        reading.message_of(message, NAMES, budget=[32])


def test_kinds_of_items() -> None:
    assert kind_of_class("IPM.Note.SMIME") == "mail"
    assert kind_of_class("IPM.Schedule.Meeting.Request") == "mail"
    assert kind_of_class("REPORT.IPM.Note.NDR") == "mail"
    assert kind_of_class("IPM.Appointment") == "event"
    assert kind_of_class("IPM.Task") == "task"
    assert kind_of_class("IPM.TaskRequest.Accept") == "mail"
    assert kind_of_class("IPM.Contact") == "contact"
    assert kind_of_class("IPM.DistList") == "group"
    assert kind_of_class("IPM.StickyNote") == "a note"
    assert kind_of_class("IPM.Something.Odd") == "an item of a kind Chrono Mail cannot import: IPM.Something.Odd"


def test_a_bad_item_is_a_failure_without_its_contents() -> None:
    event = Message(props({0x001A: "IPM.Appointment", 0x0037: "Secret subject"}))  # no start: cannot be made
    [failure] = convert(event, ("Calendar",), b"store")
    assert isinstance(failure, PstFailure) and failure.folder == ("Calendar",)
    assert "event" in failure.what and "Secret" not in failure.what


def test_event_uid_is_stable() -> None:
    message = Message(props({0x300B: b"\x01" * 16}), node=7)
    assert item_uid(message, b"store") == item_uid(message, b"store")
    assert item_uid(message, b"store") != item_uid(message, b"other store")
    start = named(PSETID_APPOINTMENT, 0x820D)
    assert start >= 0x8000


def test_event_item_converts() -> None:
    from datetime import UTC, datetime

    start = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    event = Message(props({0x001A: "IPM.Appointment", named(PSETID_APPOINTMENT, 0x820D): start, named(PSETID_APPOINTMENT, 0x820E): start}))
    [made] = convert(event, ("Calendar",), b"store")
    assert isinstance(made, PstEvent) and "BEGIN:VEVENT" in made.ical


def test_not_a_data_file(tmp_path: Path) -> None:
    pytest.importorskip("pypff")
    other = tmp_path / "other.pst"
    other.write_bytes(b"not a data file at all" * 100)
    with pytest.raises(PstError, match="not a data file"):
        read_store(other)
    with pytest.raises(PstError, match="does not exist"):
        read_store(tmp_path / "missing.pst")


# --- faults found by the review --------------------------------------------------------------------------------------------------------


@dataclass
class GuardedAttachment(FakeAttachment):
    @override
    def read_buffer(self, size: int) -> bytes:
        if size > 1024:
            raise AssertionError("read before the size was checked")
        return self.content[:size]


def test_attachment_data_is_read_once_after_its_size_is_checked() -> None:
    """Review fault 13: the data property (0x3701) was read with the other properties, before the size check, then read again."""
    data_entry = NotToBeRead()
    attachment = GuardedAttachment([MixedRecordSet([FakeEntry(0x3705, *encode(1)), FakeEntry(0x3707, *encode("a.bin")), data_entry])], content=b"12345")
    read = message_of(FakeMessage([record_set({0x001A: "IPM.Note"})], attached=[attachment]), NAMES)
    assert read.attachments[0].data == b"12345" and 0x3701 not in read.attachments[0].props.values
    big = GuardedAttachment([MixedRecordSet([FakeEntry(0x3705, *encode(1)), data_entry])], content=b"x" * 4096)
    with pytest.raises(MemoryError):
        message_of(FakeMessage([record_set({0x001A: "IPM.Note"})], attached=[big]), NAMES, budget=[2048])


def test_attachment_kept_as_a_link_has_no_data() -> None:
    linked = GuardedAttachment([record_set({0x3705: 2, 0x3707: "far.doc"})], content=b"never read" * 1000)
    read = message_of(FakeMessage([record_set({0x001A: "IPM.Note", 0x1000: "x"})], attached=[linked]), NAMES)
    assert read.attachments[0].data is None
    items = convert(read, ("Inbox",), b"store")
    assert isinstance(items[1], PstFailure) and "link" in items[1].what


def test_8_bit_strings_use_the_code_page_of_their_message_and_file() -> None:
    """Review fault 6: recipients, attachments and folders of an older data file read their 8-bit strings as cp1252."""
    cyrillic = "Привет"
    message = FakeMessage(
        [FakeRecordSet([FakeEntry(0x001A, *encode("IPM.Note")), FakeEntry(0x3FFD, *encode(1251)), FakeEntry(0x0037, STRING8, cyrillic.encode("cp1251"))])],
        recipients=FakeItem([FakeRecordSet([FakeEntry(0x0C15, *encode(1)), FakeEntry(0x3001, STRING8, cyrillic.encode("cp1251"))])]),
        attached=[FakeAttachment([FakeRecordSet([FakeEntry(0x3705, *encode(1)), FakeEntry(0x3707, STRING8, cyrillic.encode("cp1251"))])], content=b"x")],
    )
    read = message_of(message, NAMES, codepage=1250)
    assert read.props.text(0x0037) == cyrillic
    assert read.recipients[0].text(0x3001) == cyrillic
    assert read.attachments[0].props.text(0x3707) == cyrillic
    plain = message_of(FakeMessage([FakeRecordSet([FakeEntry(0x0037, STRING8, "Łódź".encode("cp1250"))])]), NAMES, codepage=1250)
    assert plain.props.text(0x0037) == "Łódź"  # no code page of its own: the file's
    assert file_codepage(b"cp1250") == 1250 and file_codepage("windows-1251") == 1251 and file_codepage(None) == 1252


@dataclass
class FakeFolder(FakeItem):
    messages: list[FakeMessage] = field(default_factory=list[FakeMessage])

    def get_sub_message(self, index: int) -> FakeMessage:
        return self.messages[index]


def _wrapped(node: int, kind: int = 0x03) -> bytes:
    return b"\x00" * 4 + bytes.fromhex("C091ADD3519DCF11A4A900AA0047FAA4") + bytes([kind]) + b"\x00" * 20 + node.to_bytes(4, "little")


def _contacts_file() -> list[PlacedFolder]:
    group = FakeMessage([record_set({0x001A: "IPM.DistList", 0x3001: "Team", named(PSETID_ADDRESS, 0x8055): (_wrapped(0x300044, 0x13),)})], identifier=0x300024)
    contact = FakeMessage([record_set({
        0x001A: "IPM.Contact", 0x3001: "Ann", named(PSETID_ADDRESS, 0x8083): "ann@example.org", named(PSETID_ADDRESS, 0x8093): "ann@home.example",
    })], identifier=0x300044)
    mail = FakeMessage([record_set({0x001A: "IPM.Note", 0x1000: "x"})], identifier=0x300064)
    contacts = FakeFolder([], messages=[group, contact])
    inbox = FakeFolder([], messages=[mail])
    top = FolderNode(1, "Top", props({}), 0, None, [
        FolderNode(2, "Inbox", props({}), 1, inbox, []), FolderNode(3, "Contacts", props({0x3613: "IPF.Contact"}), 2, contacts, []),
    ])
    return place(props({0x35E0: entry_id(1)}), FolderNode(0, "", props({}), 0, None, [top]))


def test_groups_are_the_same_whatever_was_read_before() -> None:
    """Review fault 7: members were found only through contacts read earlier in the same child; a group before its contact, a resume
    or a restart lost them. Now they are looked up in the file itself."""
    placed = _contacts_file()
    whole = dict(items_from(placed, NAMES, b"store", 1252, 0, None))
    resumed = dict(items_from(placed, NAMES, b"store", 1252, 1, None))  # starting at the group: the contact was never read here
    expected = PstGroup(("Contacts",), "Team", (("Ann", "ann@home.example"),))  # Email2, as the member's type byte says
    assert whole[1] == expected and resumed[1] == expected


def test_copies_of_an_item_get_their_own_uid() -> None:
    """Second review fault 2: a copy keeps the search key (and a meeting's global id), so two copies got one UID and the import kept one."""
    from chrono_mail.pst.names import PSETID_MEETING

    shared: dict[int, Plain] = {0x300B: b"\x01" * 16, named(PSETID_MEETING, 0x0003): b"\x04" * 40}
    first = Message(props(shared), node=0x200024)
    copy = Message(props(shared), node=0x200044)
    assert item_uid(first, b"store") != item_uid(copy, b"store")
    assert item_uid(first, b"store") == item_uid(Message(props(shared), node=0x200024), b"store")


def test_uids_without_a_store_key_or_node_id(tmp_path: Path) -> None:
    """Third review fault 5: every file without a record key had the same key, every item without a node id the same seed."""
    from chrono_mail.pst.reading import fingerprint

    one = tmp_path / "one.pst"
    two = tmp_path / "two.pst"
    one.write_bytes(b"first file" * 1000)
    two.write_bytes(b"other file" * 1000)
    assert fingerprint(one) == fingerprint(one) and fingerprint(one) != fingerprint(two)
    nameless = Message(props({}), node=0)
    assert item_uid(nameless, b"store", 3) != item_uid(nameless, b"store", 4)
    assert item_uid(nameless, b"store", 3) == item_uid(nameless, b"store", 3)


def test_contact_index_has_limits() -> None:
    """Third review fault 3: the look through the contacts had no end; now a count and a time end it, and the missing members are said once."""
    from chrono_mail.pst.reading import ContactsOfFile

    placed = _contacts_file()
    limited = ContactsOfFile(placed, NAMES, 1252, limit=1)  # only the group is looked at, not the contact after it
    assert limited(0x300044) is None and limited.partial
    late = ContactsOfFile(placed, NAMES, 1252, seconds=-1.0)
    assert late(0x300044) is None and late.partial
    whole = ContactsOfFile(placed, NAMES, 1252)
    assert whole(0x300044) is not None and not whole.partial


def test_missing_contacts_are_said_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.pst import reading

    monkeypatch.setattr(reading, "MAXIMUM_CONTACTS", 1)
    placed = _contacts_file()
    found = list(items_from(placed, NAMES, b"store", 1252, 0, None))
    notes = [item for _, item in found if isinstance(item, PstFailure) and "not every contact" in item.what]
    assert len(notes) == 1


# --- the owner's exception (D-278): standard folders by name in a data file without pointers to them ------------------------------------


def _export(children: list[FolderNode], store_values: dict[int, Plain] | None = None) -> dict[tuple[str, ...], tuple[FolderRole | None, bool]]:
    """A file as the program's Export writes it: pointers only for the Inbox (on the folder) and Deleted Items (in the store)."""
    top = folder(0x8022, "Top", children=[folder(0x8062, "Postvak IN", {0x36D7: b""}), folder(0x80A2, "Verwijderde items"), *children])
    store = props({0x35E0: entry_id(0x8022), 0x35E3: entry_id(0x80A2), **(store_values or {})})
    return {item.folder.path: (item.folder.role, item.folder.main) for item in place(store, folder(0x122, "", children=[top]))}


def test_standard_folders_by_name_when_the_file_has_no_pointers() -> None:
    found = _export([folder(0x8102, "Sent Items"), folder(0x8122, " concepten "), folder(0x8142, "Postvak UIT"), folder(0x8162, "Ongewenste e-mail")])
    assert found[("Postvak IN",)] == (FolderRole.INBOX, False) and found[("Verwijderde items",)] == (FolderRole.TRASH, False)
    assert found[("Sent Items",)][0] == FolderRole.SENT
    assert found[(" concepten ",)][0] == FolderRole.DRAFTS
    assert found[("Postvak UIT",)][0] == FolderRole.OUTBOX
    assert found[("Ongewenste e-mail",)][0] == FolderRole.JUNK


def test_a_pointer_always_wins_over_a_name() -> None:
    found = _export([folder(0x8102, "Sent Items"), folder(0x8122, "Mine")], {0x35E4: entry_id(0x8122)})
    assert found[("Mine",)][0] == FolderRole.SENT and found[("Sent Items",)][0] is None
    dangling = _export([folder(0x8102, "Sent Items")], {0x35E4: entry_id(0x9999)})  # a pointer to no folder: the name counts
    assert dangling[("Sent Items",)][0] == FolderRole.SENT
    inbox_by_name = _export([folder(0x8102, "Inbox")])  # the Inbox has its pointer: a second "Inbox" stays plain
    assert inbox_by_name[("Inbox",)][0] is None


def test_only_folders_directly_below_the_top_and_only_the_first() -> None:
    found = _export([
        folder(0x8102, "Archive", children=[folder(0x8202, "Sent Items")]), folder(0x8122, "Verzonden items"), folder(0x8142, "Sent Items"),
        folder(0x8162, "Drafts", {0x3613: "IPF.Appointment"}),
    ])
    assert found[("Archive", "Sent Items")][0] is None
    assert found[("Verzonden items",)][0] == FolderRole.SENT and found[("Sent Items",)][0] is None
    assert found[("Drafts",)][0] is None  # not a mail folder


def test_main_calendar_by_name() -> None:
    found = _export([folder(0x8102, "Agenda", {0x3613: "IPF.Appointment"}), folder(0x8122, "Calendar", {0x3613: "IPF.Appointment"}),
                     folder(0x8142, "Taken", {0x3613: "IPF.Note"})])
    assert found[("Agenda",)] == (None, True) and found[("Calendar",)] == (None, False)
    assert found[("Taken",)] == (None, False)  # a mail folder named Tasks is not the main task list
    top = folder(0x8022, "Top", children=[
        folder(0x8062, "Inbox", {0x36D0: entry_id(0x8122)}), folder(0x8102, "Agenda", {0x3613: "IPF.Appointment"}),
        folder(0x8122, "Work", {0x3613: "IPF.Appointment"}),
    ])
    pointed = {item.folder.path: item.folder.main for item in place(props({0x35E0: entry_id(0x8022)}), folder(0x122, "", children=[top]))}
    assert pointed[("Work",)] and not pointed[("Agenda",)]  # the calendar pointer wins over the name


def test_a_pointer_to_a_hidden_folder_leaves_the_role_to_the_name() -> None:
    """Seen in an export of that program: its Junk pointer leads to a hidden settings folder (IPF.Configuration), not to the Junk folder."""
    top = folder(0x8022, "Top", children=[
        folder(0x8062, "Inbox", {0x36D7: entry_id(0x8182)}), folder(0x8102, "Drafts"),
        folder(0x8182, "Settings", {0x10F4: True, 0x3613: "IPF.Configuration"}),
    ])
    found = {item.folder.path: item.folder.role for item in place(props({0x35E0: entry_id(0x8022)}), folder(0x122, "", children=[top]))}
    assert found[("Drafts",)] == FolderRole.DRAFTS
    assert ("Settings",) not in found


def test_a_pointer_of_the_wrong_kind_neither_gives_nor_blocks() -> None:
    sent_to_settings = _export([folder(0x8102, "Cfg", {0x3613: "IPF.Configuration"}), folder(0x8122, "Sent Items")], {0x35E4: entry_id(0x8102)})
    assert sent_to_settings[("Sent Items",)][0] == FolderRole.SENT and sent_to_settings[("Cfg",)][0] is None  # one Sent, never two
    top = folder(0x8022, "Top", children=[folder(0x8062, "Inbox", {0x36D0: entry_id(0x8102)}), folder(0x8102, "Sent Items")])
    found = {item.folder.path: item.folder.role for item in place(props({0x35E0: entry_id(0x8022)}), folder(0x122, "", children=[top]))}
    assert found[("Sent Items",)] == FolderRole.SENT  # a calendar pointer to a mail folder does not keep its name from counting
