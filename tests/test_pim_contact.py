import base64

import pytest

from chrono_mail.pim.contact import (
    Address, Contact, GroupForm, Labeled, PartialDate, Photo, bare_uid, contact_to_text, member_address, member_reference, member_uid, parse_contacts,
)
from chrono_mail.pim.vcard import (
    MAXIMUM_LINES_PER_CARD,
    ContentLine,
    escape,
    fold,
    listed,
    parse_cards,
    parse_line,
    split_unescaped,
    structured,
    unescape,
    unfold,
)

# --- Version ------------------------------------------------------------
__version__ = "test_pim_contact.py 1.10.277"

# --- Version history ----------------------------------------------------
# v1.10.277: Contact groups in both forms, written back in their own form; a card keeps its own version (R-39)
# v0.0.57: Initial vCard and contact tests

V3 = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc-123\r\nN:Reynolds;John;Paul,Q;Dr.;Jr.\r\nFN:John Reynolds\r\nNICKNAME:Johnny,JR\r\n"
    "EMAIL;TYPE=INTERNET,WORK,pref:john@example.com\r\nEMAIL;TYPE=HOME:john@home.example\r\nTEL;TYPE=CELL:+1 (555) 123-4567\r\n"
    "ADR;TYPE=WORK:;Suite 4;230 Park Ave;New York;NY;10169;USA\r\nORG:Reynolds Inc.;Sales\r\nTITLE:CEO\r\nBDAY:1980-04-15\r\n"
    "NOTE:Line one\\nLine two\\, with a comma\\; and a semicolon\r\nCATEGORIES:Important,Key Customer\r\nREV:2026-10-01T10:00:00Z\r\n"
    "item1.URL:https://example.com\r\nitem1.X-ABLabel:_$!<HomePage>!$_\r\nX-CUSTOM;FOO=bar:kept\r\nEND:VCARD\r\n"
)


def one(text: str) -> Contact:
    contacts = parse_contacts(text)
    assert len(contacts) == 1
    return contacts[0]


def test_a_vcard_3_gives_every_field() -> None:
    c = one(V3)
    assert (c.uid, c.display_name, c.family_name, c.given_name, c.additional_names, c.prefix, c.suffix) == ("abc-123", "John Reynolds", "Reynolds", "John", "Paul Q", "Dr.", "Jr.")
    assert c.nicknames == ("Johnny", "JR") and c.categories == ("Important", "Key Customer") and c.revision == "2026-10-01T10:00:00Z"
    assert [(e.value, e.types, e.preferred) for e in c.emails] == [("john@example.com", ("internet", "work"), True), ("john@home.example", ("home",), False)]
    assert c.phones == (Labeled("+1 (555) 123-4567", ("cell",)),)
    assert c.addresses[0] == Address("230 Park Ave", "New York", "NY", "10169", "USA", "", "Suite 4", ("work",))
    assert (c.organization, c.department, c.title, c.birthday) == ("Reynolds Inc.", "Sales", "CEO", PartialDate(4, 15, 1980))
    assert c.note == "Line one\nLine two, with a comma; and a semicolon"
    assert c.urls[0].value == "https://example.com" and c.urls[0].group == "item1"
    assert [(line.name, line.group) for line in c.extra] == [("X-ABLABEL", "item1"), ("X-CUSTOM", "")]  # what we do not understand is kept
    assert c.full_name() == "John Reynolds" and c.sort_name() == "reynolds john"


def test_writing_and_reading_again_changes_nothing() -> None:
    first = one(V3)
    second = one(contact_to_text(first))
    assert second == first
    text = contact_to_text(first)
    assert text.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n") and text.endswith("END:VCARD\r\n") and "X-CUSTOM;FOO=bar:kept" in text and "item1.X-ABLabel" in text.replace("X-ABLABEL", "X-ABLabel")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_a_vcard_2_1_with_quoted_printable_and_bare_types() -> None:
    text = (
        "BEGIN:VCARD\nVERSION:2.1\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:J=C3=BCrgen M=C3=BC=\n=C3=B6ller\n"
        "TEL;HOME;VOICE:+49 30 1234\nTEL;CELL:+49 170 5678\nEMAIL;INTERNET:j@example.de\nEND:VCARD\n"
    )
    c = one(text)
    assert (c.family_name, c.given_name) == ("Müller", "Jürgen") and c.display_name == "Jürgen Müöller"  # the soft line break joined the two halves
    assert [(p.value, p.types) for p in c.phones] == [("+49 30 1234", ("home", "voice")), ("+49 170 5678", ("cell",))]
    assert c.emails[0].types == ("internet",)


def test_a_vcard_4_with_pref_and_a_photo_in_a_data_uri() -> None:
    pixel = base64.b64encode(b"\x89PNG\r\n\x1a\nxxxx").decode()
    text = f"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ann\r\nEMAIL;PREF=1;TYPE=work:ann@example.com\r\nBDAY:--0229\r\nANNIVERSARY:20100606\r\nPHOTO:data:image/png;base64,{pixel}\r\nEND:VCARD\r\n"
    c = one(text)
    assert c.birthday is not None and c.emails[0].preferred and c.birthday == PartialDate(2, 29) and c.birthday.to_text() == "--0229" and c.anniversary == PartialDate(6, 6, 2010)
    assert c.photo == Photo("image/png", b"\x89PNG\r\n\x1a\nxxxx")
    again = one(contact_to_text(c, "4.0"))
    assert again.photo == c.photo and again.emails[0].preferred


def test_a_photo_that_is_a_link_is_kept_as_it_is() -> None:
    c = one("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A\r\nPHOTO;VALUE=uri:https://example.com/a.jpg\r\nEND:VCARD\r\n")
    assert c.photo is None and any(line.name == "PHOTO" and line.value == "https://example.com/a.jpg" for line in c.extra)
    assert "PHOTO;VALUE=uri:https://example.com/a.jpg" in contact_to_text(c)


def test_several_cards_in_one_text_and_text_around_them() -> None:
    text = "junk before\r\nBEGIN:VCARD\r\nFN:A\r\nEND:VCARD\r\nmore junk\r\nBEGIN:VCARD\r\nFN:B\r\nEND:VCARD\r\nBEGIN:VCARD\r\nFN:C (never closed)\r\n"
    assert [c.display_name for c in parse_contacts(text)] == ["A", "B", "C (never closed)"]
    assert [c.display_name for c in parse_contacts(b"\xef\xbb\xbfBEGIN:VCARD\r\nFN:Bom\r\nEND:VCARD\r\n")] == ["Bom"]


def test_the_name_to_show_falls_back() -> None:
    assert Contact(given_name="Ann", family_name="Lee").full_name() == "Ann Lee"
    assert Contact(organization="Acme").full_name() == "Acme"
    assert Contact(emails=(Labeled("a@example.com"),)).full_name() == "a@example.com"
    assert Contact().full_name() == "" and Contact().with_uid().uid != ""


def test_lines_values_and_escapes() -> None:
    assert unfold("A:1\r\n 2\r\nB:x\r\n\ty\r\nC:z").__next__() == "A:12"
    assert list(unfold("A:1\r\n 2\r\nB:x\r\n\ty\r\nC:z")) == ["A:12", "B:xy", "C:z"]
    assert unescape("a\\nb\\Nc\\,d\\;e\\\\f\\") == "a\nb\nc,d;e\\f\\" and escape("a\nb,c;d\\e") == "a\\nb\\,c\\;d\\\\e"
    assert split_unescaped("a\\;b;c", ";") == ["a\\;b", "c"] and structured("a\\;b;c") == ["a;b", "c"] and listed("x, y\\,z,,") == ["x", "y,z"]
    line = parse_line('item2.TEL;TYPE="a;b,c",d;X-Q=1:12:34')
    assert line == ContentLine("TEL", "12:34", (("TYPE", ("a;b,c", "d")), ("X-Q", ("1",))), "item2")
    assert fold("x" * 200).split("\r\n ")[0] == "x" * 75 and all(len(p.encode()) <= 75 for p in fold("é" * 100).split("\r\n "))
    assert "".join(fold("é" * 100).split("\r\n ")) == "é" * 100  # a character is never cut in two


@pytest.mark.parametrize("junk", ["", "no colon here", ":no name", "BAD NAME:x", ".X:1", "A.:1", "\x00\x01\x02", "BEGIN:VCARD", "END:VCARD", "﻿", "TEL;;;;:1"])
def test_junk_lines_are_ignored_and_never_raise(junk: str) -> None:
    assert parse_cards(junk) in ([], [[]])
    parse_contacts(junk)


def test_hostile_input_is_cut_down_and_never_stalls() -> None:
    many = "BEGIN:VCARD\r\n" + "".join(f"EMAIL:a{n}@x\r\n" for n in range(MAXIMUM_LINES_PER_CARD + 5000)) + "END:VCARD\r\n"
    c = one(many)
    assert len(c.emails) == 500  # the number of addresses of one contact is capped
    assert len(parse_cards(many)[0]) == MAXIMUM_LINES_PER_CARD
    assert parse_contacts(b"\xff\xfe\x00" * 1000) == []  # not a card at all
    big = "BEGIN:VCARD\r\nPHOTO;ENCODING=b:" + "A" * (20 * 1024 * 1024) + "\r\nEND:VCARD\r\n"
    assert one(big).photo is None  # a photo that is too large is not decoded
    bad = one("BEGIN:VCARD\r\nBDAY:99999999\r\nN:a\r\nPHOTO;ENCODING=b:!!!notbase64!!!\r\nANNIVERSARY:--13xx\r\nEND:VCARD\r\n")
    assert bad.birthday is None and bad.anniversary is None
    deep = "BEGIN:VCARD\r\n" * 1000 + "FN:x\r\n"
    assert len(parse_contacts(deep)) >= 1
    assert parse_contacts("BEGIN:VCARD\r\nFN;CHARSET=nonsense;ENCODING=QUOTED-PRINTABLE:=ZZ=C3\r\nEND:VCARD\r\n")[0].display_name != "\x00"


# --- contact groups (R-39) ---------------------------------------------------------------------------------------------------------------

GROUP_4 = (
    "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:g-1\r\nKIND:group\r\nFN:Family\r\nMEMBER:urn:uuid:ann-1\r\nMEMBER:mailto:plain@example.com\r\n"
    "MEMBER:urn:uuid:ann-1\r\nX-OTHER:kept\r\nEND:VCARD\r\n"
)
GROUP_APPLE = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//kerio.com/Contacts//NONSGML v1.0//EN\r\nX-DL:TRUE\r\nFN:Team\r\nUID:g-2\r\nX-ADDRESSBOOKSERVER-KIND:group\r\n"
    "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:bob-2\r\nEND:VCARD\r\n"
)


def test_a_vcard_4_group_is_read_with_its_members_and_written_back_in_its_own_form_and_version() -> None:
    group = one(GROUP_4)
    assert group.group_form is GroupForm.VCARD4 and group.full_name() == "Family" and group.version == "4.0"
    assert group.members == ("urn:uuid:ann-1", "mailto:plain@example.com")  # each once
    text = contact_to_text(group)
    assert text.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n") and "KIND:group\r\n" in text and "MEMBER:mailto:plain@example.com" in text and "X-OTHER:kept" in text
    assert "X-ADDRESSBOOKSERVER" not in text and one(text) == group


def test_an_apple_group_as_kerio_keeps_it_is_read_and_written_back_in_apple_s_form() -> None:
    group = one(GROUP_APPLE)
    assert group.group_form is GroupForm.APPLE and group.members == ("urn:uuid:bob-2",) and group.full_name() == "Team"
    text = contact_to_text(group)
    assert text.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n") and "X-ADDRESSBOOKSERVER-KIND:group" in text and "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:bob-2" in text
    assert "X-DL:TRUE" in text and "\r\nKIND:" not in text and "\r\nMEMBER:" not in text and one(text) == group


def test_a_kind_that_is_no_group_and_member_lines_of_another_form_are_kept_as_they_were() -> None:
    person = one("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Acme\r\nKIND:org\r\nMEMBER:urn:uuid:x\r\nEND:VCARD\r\n")
    assert person.group_form is None and person.members == ()
    text = contact_to_text(person)
    assert "KIND:org" in text and "MEMBER:urn:uuid:x" in text
    mixed = one(GROUP_APPLE.replace("END:VCARD", "MEMBER:urn:uuid:other\r\nEND:VCARD"))
    assert mixed.members == ("urn:uuid:bob-2",) and "MEMBER:urn:uuid:other" in contact_to_text(mixed)


def test_a_vcard_4_card_stays_a_vcard_4_card_when_it_is_written_back() -> None:
    card = one("BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nFN:Ann\r\nEMAIL:ann@example.com\r\nEND:VCARD\r\n")
    assert contact_to_text(card).startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n")  # it was written as 3.0
    assert contact_to_text(one("BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Old\r\nEND:VCARD\r\n")).startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert contact_to_text(Contact(display_name="New")).startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert contact_to_text(Contact(display_name="G", group_form=GroupForm.VCARD4)).startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n")


def test_members_are_named_by_uid_or_by_address() -> None:
    assert member_reference("abc") == "urn:uuid:abc" and member_reference("urn:uuid:abc") == "urn:uuid:abc"
    assert member_uid("urn:uuid:abc") == "abc" and member_uid("URN:UUID:abc") == "abc" and member_uid("mailto:a@b.c") == ""
    assert member_address("mailto:a@b.c") == "a@b.c" and member_address("urn:uuid:abc") == ""
    assert bare_uid("urn:uuid:abc") == bare_uid("abc") == "abc"
