import struct
from datetime import UTC, datetime

from chrono_mail.pim.contact import parse_contacts
from chrono_mail.pim.vcard import parse_cards, unescape
from chrono_mail.pst.contacts import build_contact, group_members, one_off, wrapped_node
from chrono_mail.pst.convert import convert
from chrono_mail.pst.model import PstFailure, PstGroup
from chrono_mail.pst.names import PSETID_ADDRESS
from chrono_mail.pst.properties import Attachment, Message
from tests.test_pst_helpers import KEYWORDS, multi, named, props

# --- Version ------------------------------------------------------------
__version__ = "test_pst_contacts.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: contacts as vCard 4.0 and distribution lists

ONE_OFF_PROVIDER = bytes.fromhex("812B1FA4BEA310199D6E00DD010F5402")
WRAPPED_PROVIDER = bytes.fromhex("C091ADD3519DCF11A4A900AA0047FAA4")


def one_off_entry(name: str, address: str, unicode: bool = True) -> bytes:
    flags = 0x8000 if unicode else 0
    head = b"\x00" * 4 + ONE_OFF_PROVIDER + struct.pack("<HH", 0, flags)
    if unicode:
        return head + b"".join((text + "\x00").encode("utf-16-le") for text in (name, "SMTP", address))
    return head + b"".join((text + "\x00").encode("cp1252") for text in (name, "SMTP", address))


def wrapped_entry(node: int, kind: int = 0xC3) -> bytes:
    inner = b"\x00" * 4 + b"\x11" * 16 + struct.pack("<I", node)
    return b"\x00" * 4 + WRAPPED_PROVIDER + bytes([kind]) + inner


CONTACTS: dict[int, tuple[str, tuple[str, ...]]] = {0x200064: ("Ann", ("ann@example.org", "ann@home.example", ""))}


def lookup(node: int) -> tuple[str, tuple[str, ...]] | None:
    return CONTACTS.get(node)


def test_contact_as_vcard() -> None:
    message = Message(props({
        0x3001: "Dr. Ann B. Example", 0x3A06: "Ann", 0x3A11: "Example", 0x3A44: "B.", 0x3A45: "Dr.", 0x3A4F: "Annie",
        named(PSETID_ADDRESS, 0x8083): "ann@example.org", named(PSETID_ADDRESS, 0x8082): "SMTP",
        named(PSETID_ADDRESS, 0x8093): "/o=Org/cn=ann", named(PSETID_ADDRESS, 0x8092): "EX", named(PSETID_ADDRESS, 0x8094): "Ann (ann@work.example)",
        0x3A08: "+31 20 123 4567", 0x3A1C: "+31 6 1234 5678", 0x3A16: "Example Ltd", 0x3A18: "Sales", 0x3A17: "Manager",
        0x3A5D: "1 Home Street", 0x3A59: "Hometown", 0x3A5B: "1234 AB", 0x3A5A: "Netherlands",
        0x3A42: datetime(1980, 5, 3, 22, 0, tzinfo=UTC), 0x3A51: "https://example.org", 0x1000: "Met; at a fair, twice", KEYWORDS: ("Friends",),
    }), attachments=(Attachment(props({0x7FFF: True, 0x370E: "image/png"}), b"\x89PNG"),))
    text = build_contact(message, "0f8fad5b-d9cb-469f-a165-70867728950e")
    [card] = parse_cards(text)
    lines = {(line.name, line.value) for line in card}
    assert ("VERSION", "4.0") in lines
    assert ("UID", "urn:uuid:0f8fad5b-d9cb-469f-a165-70867728950e") in lines
    assert ("FN", "Dr. Ann B. Example") in lines
    assert ("N", "Example;Ann;B.;Dr.;") in lines
    assert ("NICKNAME", "Annie") in lines
    emails = [line.value for line in card if line.name == "EMAIL"]
    assert emails == ["ann@example.org", "ann@work.example"]
    phones = {line.value: line.types() for line in card if line.name == "TEL"}
    assert phones == {"+31 20 123 4567": ("work", "voice"), "+31 6 1234 5678": ("cell",)}
    assert ("ADR", ";;1 Home Street;Hometown;;1234 AB;Netherlands") in lines
    assert ("ORG", "Example Ltd;Sales") in lines and ("TITLE", "Manager") in lines
    assert ("BDAY", "19800504") in lines
    assert ("URL", "https://example.org") in lines
    assert ("NOTE", "Met\\; at a fair\\, twice") in lines
    assert ("CATEGORIES", "Friends") in lines
    assert ("PHOTO", "data:image/png;base64,iVBORw==") in lines
    assert not any(line.name == "REV" for line in card)  # no time kept: no REV (never the time of the import)


def test_contact_card_is_the_same_every_time() -> None:
    """Review fault 16: REV fell back to the time of the import, so the same contact gave other bytes each time."""
    message = Message(props({0x3001: "Ann", 0x3007: datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)}))
    first = build_contact(message, "0f8fad5b-d9cb-469f-a165-70867728950e")
    assert first == build_contact(message, "0f8fad5b-d9cb-469f-a165-70867728950e")
    assert "REV:20200102T030405Z" in first


def test_one_off_and_wrapped_entry_ids() -> None:
    assert one_off(one_off_entry("Bob", "bob@example.net")) == ("Bob", "bob@example.net")
    assert one_off(one_off_entry("Café", "cafe@example.net", unicode=False)) == ("Café", "cafe@example.net")
    assert one_off(b"\x00" * 30) is None
    assert one_off(one_off_entry("X", "not-an-address")) is None
    assert wrapped_node(wrapped_entry(0x200064)) == (0x200064, 4)
    assert wrapped_node(wrapped_entry(0x200064, 0x13)) == (0x200064, 1)


def test_distribution_list_members() -> None:
    members = named(PSETID_ADDRESS, 0x8055)
    one_offs = named(PSETID_ADDRESS, 0x8054)
    values = props({
        0x3001: "Team",
        members: (one_off_entry("Bob", "bob@example.net"), wrapped_entry(0x200064), wrapped_entry(0x200084), b"junk"),
        one_offs: (one_off_entry("Bob", "bob@example.net"),),
    })
    found, missing = group_members(values, lookup)
    assert found == (("Bob", "bob@example.net"), ("Ann", "ann@example.org"))
    assert missing == 2


def test_group_member_by_email_index() -> None:
    """Review fault 7: a member pointing at the contact's Email2 gets that address, not Email1."""
    group = Message(props({
        0x001A: "IPM.DistList", 0x3001: "Team", named(PSETID_ADDRESS, 0x8055): (wrapped_entry(0x200064, 0x13), wrapped_entry(0x999)),
    }))
    items = convert(group, ("Contacts",), b"store", lookup)
    assert items[0] == PstGroup(("Contacts",), "Team", (("Ann", "ann@home.example"),))
    assert isinstance(items[1], PstFailure) and "1 member" in items[1].what


def test_raw_bytes_lists() -> None:
    assert multi([b"a"]) == struct.pack("<II", 1, 8) + b"a"


def test_values_cannot_break_the_card() -> None:
    """Second review fault 3: a web address with a line break ended the card and started another."""
    message = Message(props({
        0x3001: "A\r\nB;C", 0x1000: "line1\r\nline2\rEND:VCARD", 0x3A51: "http://x/a\r\nEND:VCARD\r\nBEGIN:VCARD", 0x3A50: "https://example.org/a,b",
        0x3A08: "1\n2", 0x3A17: "Boss\r\nX", 0x3A16: "Co\nInc", 0x3A4F: "N\r\n", named(PSETID_ADDRESS, 0x8083): "a@example.org\r\nX:y",
    }))
    text = build_contact(message, "0f8fad5b-d9cb-469f-a165-70867728950e")
    lines = text.split("\r\n")
    assert lines.count("BEGIN:VCARD") == 1 and lines.count("END:VCARD") == 1
    assert all(line.startswith(" ") or ":" in line for line in lines if line)
    [card] = parse_cards(text)
    urls = [unescape(line.value) for line in card if line.name == "URL"]
    assert urls == ["https://example.org/a,b"]  # the damaged one is left out, a good one stays as it is


def test_web_address_reads_back_as_it_was() -> None:
    """Third review fault 2: a URL was written without escapes, so a reader that undoes them read \\\\server\\share\\new wrongly."""
    unc = "\\\\examplehost\\share\\new"
    text = build_contact(Message(props({0x3001: "Ann", 0x3A51: unc, 0x3A50: "https://example.org/a,b;c"})), "0f8fad5b-d9cb-469f-a165-70867728950e")
    [contact] = parse_contacts(text)
    assert [url.value for url in contact.urls] == [unc, "https://example.org/a,b;c"]
