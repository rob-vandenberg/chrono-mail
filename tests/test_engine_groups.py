from pathlib import Path

import pytest

from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.session import Engine as Session
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_engine_conflicts import on_server
from tests.test_engine_dav import linked, shown

# --- Version ------------------------------------------------------------
__version__ = "test_engine_groups.py 1.10.277"

# --- Version history ----------------------------------------------------
# v1.10.277: Initial tests of contact groups (R-39): made, renamed, deleted, members added and removed, here and on the server first, offline
#            and back, refused, changed on the server, two computers; a contact deleted or moved leaves its groups; a group's addresses


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    made = make_engine(tmp_path)
    call(made, "pim.enable")
    return made


def book(engine: Engine) -> str:
    return next(c.collection for c in call(engine, "collection.list", kind="contacts"))


def contact(engine: Engine, collection: str, given: str, family: str, *emails: str) -> str:
    return str(call(engine, "contact.add", collection=collection, given=given, family=family, email=emails).item)


def names(engine: Engine, **values: object) -> list[str]:
    return [row.name for row in call(engine, "contact.list", **values).contacts]


def test_a_group_is_made_listed_renamed_and_deleted_and_its_members_stay(engine: Engine) -> None:
    b = book(engine)
    ann = contact(engine, b, "Ann", "Lee", "pref|ann@example.com", "ann@home.example")
    bob = contact(engine, b, "Bob", "Kim")
    group = call(engine, "contact-group.create", collection=b, name="Family", contacts=(ann, bob))
    assert (group.name, group.members, group.others, group.read_only) == ("Family", 2, 0, False)
    assert [row.name for row in call(engine, "contact-group.list", collection=b)] == ["Family"]
    assert names(engine) == ["Bob Kim", "Ann Lee"] and call(engine, "contact.list").total == 2  # a group is no contact
    assert names(engine, group=group.item) == ["Bob Kim", "Ann Lee"]
    assert call(engine, "contact.show", item=ann).groups == ("Family",)
    assert [row.groups for row in call(engine, "contact.list").contacts] == [(group.item,), (group.item,)]
    with pytest.raises(UsageError, match="already"):
        call(engine, "contact-group.create", collection=b, name="family")
    assert call(engine, "contact-group.rename", item=group.item, name="Kin").name == "Kin"
    assert call(engine, "contact.show", item=ann).groups == ("Kin",)
    call(engine, "contact-group.delete", item=group.item)
    assert call(engine, "contact-group.list", collection=b) == () and names(engine) == ["Bob Kim", "Ann Lee"]


def test_members_are_added_once_and_taken_out_and_a_member_is_of_its_own_address_book(engine: Engine) -> None:
    b = book(engine)
    other = call(engine, "collection.create", kind="contacts", name="Friends").collection
    ann = contact(engine, b, "Ann", "Lee")
    bob = contact(engine, b, "Bob", "Kim")
    stranger = contact(engine, other, "Cy", "Fox")
    group = call(engine, "contact-group.create", collection=b, name="Team", contacts=(ann,))
    assert call(engine, "contact-group.add-members", item=group.item, contacts=(ann, bob)).members == 2
    assert call(engine, "contact-group.remove-members", item=group.item, contacts=(ann,)).members == 1
    assert names(engine, group=group.item) == ["Bob Kim"] and names(engine, collection=b) == ["Bob Kim", "Ann Lee"]
    with pytest.raises(UsageError, match="own address book"):
        call(engine, "contact-group.add-members", item=group.item, contacts=(stranger,))
    with pytest.raises(UsageError, match="Only contacts"):
        call(engine, "contact-group.add-members", item=group.item, contacts=(group.item,))
    with pytest.raises(UsageError, match="not a group"):
        call(engine, "contact-group.rename", item=ann, name="X")
    with pytest.raises(UsageError, match="no such group"):
        call(engine, "contact.list", group=ann)


def test_a_contact_deleted_or_moved_away_leaves_the_groups_of_its_address_book(engine: Engine) -> None:
    b = book(engine)
    other = call(engine, "collection.create", kind="contacts", name="Friends").collection
    ann = contact(engine, b, "Ann", "Lee")
    bob = contact(engine, b, "Bob", "Kim")
    group = call(engine, "contact-group.create", collection=b, name="Team", contacts=(ann, bob)).item
    call(engine, "contact.delete", item=ann)
    assert names(engine, group=group) == ["Bob Kim"]
    text = call(engine, "contact.export", item=group).text
    assert text.count("X-ADDRESSBOOKSERVER-MEMBER:") == 1  # the card names it no more (not only the list)
    call(engine, "contact.move", item=bob, collection=other)
    assert names(engine, group=group) == [] and call(engine, "contact-group.list", collection=b)[0].members == 0


def test_groups_from_elsewhere_in_both_forms_are_followed_and_a_new_group_takes_the_form_of_its_address_book(engine: Engine) -> None:
    b = book(engine)
    text = (
        "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:ann-1\r\nFN:Ann Lee\r\nEMAIL:ann@example.com\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:urn:uuid:g-1\r\nKIND:group\r\nFN:Club\r\nMEMBER:urn:uuid:ann-1\r\nMEMBER:urn:uuid:gone\r\nMEMBER:mailto:plain@example.com\r\nEND:VCARD\r\n"
    )
    assert call(engine, "contact.import", collection=b, text=text).imported == 2
    (club,) = call(engine, "contact-group.list", collection=b)
    assert (club.name, club.members, club.others) == ("Club", 1, 2) and names(engine, group=club.item) == ["Ann Lee"]
    new = call(engine, "contact-group.create", collection=b, name="Second").item
    exported = call(engine, "contact.export", item=new).text
    assert "VERSION:4.0" in exported and "KIND:group" in exported  # the form the address book uses
    fresh = call(engine, "collection.create", kind="contacts", name="Fresh").collection
    assert "X-ADDRESSBOOKSERVER-KIND:group" in call(engine, "contact.export", item=call(engine, "contact-group.create", collection=fresh, name="G").item).text


def test_a_message_to_a_group_goes_to_every_member_s_preferred_address(engine: Engine) -> None:
    b = book(engine)
    ann = contact(engine, b, "Ann", "Lee", "ann@work.example", "pref|ann@example.com")
    bob = contact(engine, b, "Bob", "Kim")  # no address
    cy = contact(engine, b, "Cy", "Fox", "cy@example.com")
    group = call(engine, "contact-group.create", collection=b, name="Family", contacts=(ann, bob, cy)).item
    found = call(engine, "contact-group.addresses", item=group)
    assert found.name == "Family" and set(found.addresses) == {"Ann Lee <ann@example.com>", "Cy Fox <cy@example.com>"} and found.without_address == 1


def test_a_group_is_suggested_by_its_name_with_the_addresses_of_its_members(engine: Engine) -> None:
    b = book(engine)
    ann = contact(engine, b, "Ann", "Lee", "ann@example.com")
    cy = contact(engine, b, "Cy", "Fox", "cy@example.com")
    call(engine, "contact-group.create", collection=b, name="Family", contacts=(ann, cy))
    call(engine, "contact-group.create", collection=b, name="Famous", contacts=())  # no addresses: never suggested
    (found,) = call(engine, "contact.suggest", text="fam")
    assert found.label.startswith("Family (group, 2 addresses)") and set(found.display.split(", ")) == {"Ann Lee <ann@example.com>", "Cy Fox <cy@example.com>"}


def test_a_group_goes_to_the_server_first_in_apple_s_form(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        b = shown(engine, account, "contacts").collection
        ann = call(engine, "contact.add", collection=b, name="Ann Lee", email=("ann@example.com",))
        group = call(engine, "contact-group.create", collection=b, name="Team", contacts=(ann.item,))
        uid = call(engine, "contact.show", item=group.item).uid
        found = on_server(server, "contacts", uid)
        assert found is not None and "X-ADDRESSBOOKSERVER-KIND:group" in found[1] and f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{ann.uid}" in found[1]
        call(engine, "contact-group.rename", item=group.item, name="Crew")
        assert "FN:Crew" in (on_server(server, "contacts", uid) or ("", ""))[1]
        call(engine, "contact.delete", item=ann.item)
        assert "X-ADDRESSBOOKSERVER-MEMBER" not in (on_server(server, "contacts", uid) or ("", ""))[1]  # the delete took it out there too
        call(engine, "contact-group.delete", item=group.item)
        assert on_server(server, "contacts", uid) is None
        assert told == [] and call(engine, "dav.sync", account=account)[0].pushed == 0


def test_offline_a_group_is_changed_here_kept_through_a_restart_and_sent_later(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        b = shown(engine, account, "contacts").collection
        ann = call(engine, "contact.add", collection=b, name="Ann Lee")
        group = call(engine, "contact-group.create", collection=b, name="Team")
        uid = call(engine, "contact.show", item=group.item).uid
        server.writes_failing = True
        assert call(engine, "contact-group.add-members", item=group.item, contacts=(ann.item,)).members == 1  # here at once
        assert "MEMBER" not in (on_server(server, "contacts", uid) or ("", ""))[1] and any("next synchronisation" in line for line in told)
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)
        server.writes_failing = False
        assert call(later, "dav.sync", account=account)[0].pushed == 1
        assert f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{ann.uid}" in (on_server(server, "contacts", uid) or ("", ""))[1]


def test_a_lasting_no_of_the_server_changes_no_group_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        b = shown(engine, account, "contacts").collection
        group = call(engine, "contact-group.create", collection=b, name="Team")
        server.writes_refused = True
        with pytest.raises(UsageError, match="did not take"):
            call(engine, "contact-group.rename", item=group.item, name="Never")
        with pytest.raises(UsageError, match="did not take"):
            call(engine, "contact-group.create", collection=b, name="Other")
        assert [row.name for row in call(engine, "contact-group.list", collection=b)] == ["Team"]


def test_a_group_changed_on_the_server_follows_here_and_members_added_on_both_sides_are_merged(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        b = shown(engine, account, "contacts").collection
        ann = call(engine, "contact.add", collection=b, name="Ann Lee")
        bob = call(engine, "contact.add", collection=b, name="Bob Kim")
        group = call(engine, "contact-group.create", collection=b, name="Team")
        uid = call(engine, "contact.show", item=group.item).uid
        name, text = on_server(server, "contacts", uid) or ("", "")
        server.add("contacts", name, text.replace("END:VCARD", f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{bob.uid}\r\nEND:VCARD"))  # on the phone
        call(engine, "contact-group.add-members", item=group.item, contacts=(ann.item,))  # here, before a sync saw the phone's change
        assert names(engine, group=group.item) == ["Ann Lee", "Bob Kim"] and call(engine, "conflict.list").conflicts == ()
        server.add("contacts", "made-on-phone.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:phone-g\r\nFN:Phone group\r\nX-ADDRESSBOOKSERVER-KIND:group\r\n"
                   f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{ann.uid}\r\nEND:VCARD\r\n")
        call(engine, "dav.sync", account=account)
        assert [(row.name, row.members) for row in call(engine, "contact-group.list", collection=b)] == [("Phone group", 1), ("Team", 2)] and told == []


def test_two_computers_end_up_showing_the_same_groups(tmp_path: Path) -> None:
    with FakeDav() as server:
        first, first_account, _ = linked(tmp_path / "first", server)
        second, second_account, _ = linked(tmp_path / "second", server)
        b = shown(first, first_account, "contacts").collection
        ann = call(first, "contact.add", collection=b, name="Ann Lee")
        call(first, "contact-group.create", collection=b, name="Team", contacts=(ann.item,))
        call(second, "dav.sync", account=second_account)
        (seen,) = call(second, "contact-group.list")
        assert (seen.name, seen.members) == ("Team", 1) and names(second, group=seen.item) == ["Ann Lee"]
