from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine import Engine, UsageError
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pim.py 0.0.60"

# --- Version history ----------------------------------------------------
# v0.0.60: Initial tests of the collection, tag and contact commands


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path)


def books(engine: Engine) -> dict[str, str]:
    return {item.kind: item.collection for item in call(engine, "collection.list")}


def test_enabling_makes_a_calendar_a_task_list_and_an_address_book_once(engine: Engine) -> None:
    assert call(engine, "collection.list") == ()
    made = call(engine, "pim.enable")
    assert [(c.kind, c.name, c.account) for c in made] == [("events", "Calendar", LOCAL_ACCOUNT_ID), ("contacts", "Contacts", LOCAL_ACCOUNT_ID), ("tasks", "Tasks", LOCAL_ACCOUNT_ID)]  # by name
    assert len(call(engine, "pim.enable")) == 3  # again: nothing new
    assert [c.name for c in call(engine, "collection.list", kind="tasks")] == ["Tasks"] and all(c.items == 0 for c in made) and all(c.colour for c in made)
    assert {c.account_name for c in made} == {"Local Folders"}


def test_collections_can_be_made_renamed_and_deleted(engine: Engine) -> None:
    call(engine, "pim.enable")
    work = call(engine, "collection.create", kind="events", name="Work", colour="#ff0000")
    assert (work.kind, work.name, work.colour) == ("events", "Work", "#ff0000")
    renamed = call(engine, "collection.edit", collection=work.collection, name="Job", colour="#00ff00")
    assert (renamed.name, renamed.colour) == ("Job", "#00ff00")
    with pytest.raises(UsageError, match="#rrggbb"):
        call(engine, "collection.create", kind="events", name="Bad", colour="red")
    with pytest.raises(UsageError, match="name"):
        call(engine, "collection.create", kind="tasks", name="  ")
    book = books(engine)["contacts"]
    call(engine, "contact.add", collection=book, name="Ann Lee")
    gone = call(engine, "collection.delete", collection=book)
    assert gone.items == 1 and "contacts" not in books(engine)
    with pytest.raises(UsageError, match="no such"):
        call(engine, "collection.delete", collection=book)


def test_a_contact_is_added_shown_changed_found_and_deleted(engine: Engine) -> None:
    call(engine, "pim.enable")
    book = books(engine)["contacts"]
    added = call(
        engine, "contact.add", collection=book, given="John", family="Reynolds", email=("work,pref|john@example.com", "home|john@home.example"), phone=("cell|+1 555 123 4567",),
        address=("work|230 Park Ave|New York|NY|10169|USA",), organization="Reynolds Inc.", department="Sales", title="CEO", birthday="1980-04-15", note="Line one\nLine two",
        tag=("Important", "Key Customer"), nickname=("Johnny",),
    )
    assert added.name == "John Reynolds" and added.family_name == "Reynolds" and len(added.item) == 32 and added.birthday == "1980-04-15"
    shown = call(engine, "contact.show", item=added.item)
    assert [(e.value, e.types, e.preferred) for e in shown.emails] == [("john@example.com", ("work",), True), ("john@home.example", ("home",), False)]
    assert shown.addresses[0].lines == ("230 Park Ave", "10169 New York", "NY", "USA") and shown.note == "Line one\nLine two" and shown.tags == ("Important", "Key Customer")
    assert shown.organization == "Reynolds Inc." and shown.department == "Sales" and shown.nicknames == ("Johnny",) and shown.photo_media_type == ""
    listed = call(engine, "contact.list")
    assert listed.total == 1 and (listed.contacts[0].name, listed.contacts[0].subtitle, listed.contacts[0].tags) == ("John Reynolds", "Reynolds Inc.", ("Important", "Key Customer"))
    assert [c.item for c in call(engine, "contact.list", text="rey").contacts] == [added.item] and call(engine, "contact.list", text="nobody").contacts == ()
    assert [c.item for c in call(engine, "contact.list", tag="Important").contacts] == [added.item]
    changed = call(engine, "contact.edit", item=added.item, title="Chairman", email=("new@example.com",), add_tag=("VIP",), remove_tag=("important",), clear=("nickname",), birthday="--0415")
    assert changed.title == "Chairman" and [e.value for e in changed.emails] == ["new@example.com"] and changed.tags == ("Key Customer", "VIP") and changed.nicknames == () and changed.birthday == "--0415"
    assert changed.organization == "Reynolds Inc." and changed.phones[0].value == "+1 555 123 4567"  # what was not edited stays
    with pytest.raises(UsageError, match="Only these"):
        call(engine, "contact.edit", item=added.item, clear=("title",))
    with pytest.raises(UsageError, match="birthday"):
        call(engine, "contact.edit", item=added.item, birthday="yesterday")
    call(engine, "contact.delete", item=added.item)
    assert call(engine, "contact.list").total == 0
    with pytest.raises(UsageError, match="no such item"):
        call(engine, "contact.show", item=added.item)
    with pytest.raises(UsageError, match="32"):
        call(engine, "contact.show", item="short")


def test_a_contact_needs_a_name_or_an_address_and_a_book(engine: Engine) -> None:
    call(engine, "pim.enable")
    book = books(engine)["contacts"]
    with pytest.raises(UsageError, match="needs a name"):
        call(engine, "contact.add", collection=book)
    assert call(engine, "contact.add", collection=book, email=("only@example.com",)).name == "only@example.com"
    with pytest.raises(UsageError, match="not a address book"):
        call(engine, "contact.add", collection=books(engine)["events"], name="x")


def test_import_export_and_moving_contacts(engine: Engine) -> None:
    call(engine, "pim.enable")
    one = books(engine)["contacts"]
    two = call(engine, "collection.create", kind="contacts", name="Friends").collection
    text = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u1\r\nFN:Ann Lee\r\nEMAIL:ann@example.com\r\nEND:VCARD\r\nBEGIN:VCARD\r\nVERSION:3.0\r\nUID:u2\r\nFN:Bo Kim\r\nEND:VCARD\r\nBEGIN:VCARD\r\nVERSION:3.0\r\nEND:VCARD\r\n"
    done = call(engine, "contact.import", collection=one, text=text)
    assert (done.imported, done.updated, done.skipped, len(done.items)) == (2, 0, 1, 2)
    again = call(engine, "contact.import", collection=one, text=text.replace("Ann Lee", "Ann Leigh"))
    assert (again.imported, again.updated) == (0, 2) and [c.name for c in call(engine, "contact.list").contacts] == ["Ann Leigh", "Bo Kim"]  # same uid: replaced, not doubled
    exported = call(engine, "contact.export", collection=one)
    assert exported.contacts == 2 and exported.text.count("BEGIN:VCARD") == 2 and "Ann Leigh" in exported.text
    first = done.items[0]
    moved = call(engine, "contact.move", item=first, collection=two)
    assert moved.collection == two and moved.item != first and [c.name for c in call(engine, "contact.list", collection=two).contacts] == ["Ann Leigh"]
    assert call(engine, "contact.export", item=moved.item).contacts == 1
    with pytest.raises(UsageError, match="either"):
        call(engine, "contact.export")
    assert call(engine, "contact.list").total == 2 and call(engine, "contact.list", collection=one).total == 1


def test_tags_have_colours_counts_and_can_be_renamed_everywhere(engine: Engine) -> None:
    call(engine, "pim.enable")
    book = books(engine)["contacts"]
    a = call(engine, "contact.add", collection=book, name="A", tag=("Work", "Friends"))
    call(engine, "contact.add", collection=book, name="B", tag=("work",))
    listed = {t.name: t for t in call(engine, "tag.list")}
    assert listed["Work"].contacts == 2 and listed["Friends"].contacts == 1 and listed["Work"].colour == ""
    coloured = {t.name: t for t in call(engine, "tag.colour", name="Work", colour="#AA0000")}
    assert coloured["Work"].colour == "#aa0000" and coloured["Work"].contacts == 2  # the written name is kept for display
    with pytest.raises(UsageError, match="#rrggbb"):
        call(engine, "tag.colour", name="Work", colour="nope")
    renamed = call(engine, "tag.rename", name="work", new_name="Job")
    assert renamed.items == 2
    after = {t.name: t for t in call(engine, "tag.list")}
    assert "Work" not in after and after["Job"].contacts == 2 and after["Job"].colour == "#aa0000"  # the colour went with it
    assert call(engine, "contact.show", item=a.item).tags == ("Job", "Friends")
    assert len(call(engine, "tag.colour", name="Job", colour="")) == 2  # the colour is taken away
