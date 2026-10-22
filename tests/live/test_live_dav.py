import time
import tomllib
from datetime import date
from pathlib import Path

import pytest

from chrono_mail.pim.contact import Contact, Labeled, contact_to_text, parse_contacts
from chrono_mail.pim.ical import Event, EventSeries, Task, events_to_text, new_uid, parse_events, parse_tasks, tasks_to_text
from chrono_mail.protocols.dav import DavClient, RemoteCollection
from tests.live.account import KERIO_FILE, SETTINGS_DIRECTORY, load_live_account
from tests.live.test_live_imap import connect
from datetime import datetime
from zoneinfo import ZoneInfo

# --- Version ------------------------------------------------------------
__version__ = "test_live_dav.py 2.0.283"

# --- Version history ----------------------------------------------------
# v2.0.283: dav.local.toml is read from outside the project folder (D-230)
# v1.10.278: One contact read with read (the multiget first): Kerio answers its GET with 404
# v1.10.277: The server keeps a contact group in both forms and reads back its members (R-39)
# v1.1.236: A calendar and a task list made in the right home at a new place; an address book refused when the server makes none (R-29)
# v1.1.235: A calendar deleted here offline is deleted on the server at the next synchronisation, only when unchanged there (R-28)
# v1.1.234: A calendar made on the server and deleted there goes here too, on the server's confirmation (R-25)
# v1.1.225: An event made, changed, merged with a change on the server and deleted, each on the server first (D-194)
# v1.1.221: The server keeps a calendar's new name and colour (PROPPATCH), read back in one form; both are put back after (D-192)
# v0.5.123: The items Kerio keeps in Deleted Items after the test deleted them are removed, found by their unique UID
# v0.0.66: Live test of the synchronisation
# v0.0.65: Initial live tests of the CalDAV and CardDAV client

SETTINGS = SETTINGS_DIRECTORY / "dav.local.toml"  # outside the project folder (D-230)
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def dav() -> DavClient:
    if not SETTINGS.exists():
        pytest.skip(f"{SETTINGS} is missing (see tests/live/dav.example.toml)")
    with SETTINGS.open("rb") as handle:
        values = tomllib.load(handle)
    trusted = frozenset({str(values["fingerprint"])}) if values.get("fingerprint") else frozenset()
    return DavClient(str(values["url"]), str(values["user"]), str(values["password"]), trusted)


MONTHS: tuple[str, ...] = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def remove_from_deleted_items(uid: str, expected: int) -> None:
    """Kerio keeps a deleted calendar, task or contact item in the mail folder Deleted Items as a message. Remove the ones
    this test made, found by their unique UID (Kerio's search does not look inside them), so nothing else is touched."""
    if not KERIO_FILE.exists():
        return
    account = load_live_account(KERIO_FILE)
    today = date.today()
    with connect(account, secure=True) as imap:
        ours: list[int] = []
        deadline = time.monotonic() + 20
        while len(ours) < expected and time.monotonic() < deadline:
            imap.select(account.trash_folder)
            recent = imap.uid_search(f"SINCE {today.day}-{MONTHS[today.month - 1]}-{today.year}")
            fetched = imap.uid_fetch(",".join(str(number) for number in recent), "(UID BODY.PEEK[])") if recent else ()
            ours = [item.uid for item in fetched if uid.encode() in (item.data or b"")]
            if len(ours) < expected:
                time.sleep(1)
        if ours:
            numbers = ",".join(str(number) for number in ours)
            imap.uid_store(numbers, ["\\Deleted"], "add")
            imap.uid_expunge(numbers)  # these UIDs only; inside Deleted Items this is final


def collection(dav: DavClient, kind: str) -> RemoteCollection:
    homes = dav.homes()
    found = [c for c in dav.collections(*homes) if c.kind == kind]
    assert found, f"no {kind} collection on the server"
    return found[0]


def test_discovery_finds_calendar_tasks_and_contacts_and_skips_the_mail_folders(dav: DavClient) -> None:
    assert "calendar-access" in dav.options() or "addressbook" in dav.options()
    calendar_home, address_home = dav.homes()
    assert calendar_home and address_home
    found = dav.collections(calendar_home, address_home)
    assert {c.kind for c in found} >= {"events", "tasks", "contacts"}
    assert not any(c.name in ("INBOX", "Drafts", "Junk E-mail", "Sent Items", "Deleted Items") for c in found)  # Kerio lists its mail folders here too: they are not calendars


def test_the_items_can_be_listed_followed_by_sync_token_and_read(dav: DavClient) -> None:
    for kind, parser in (("events", parse_events), ("tasks", parse_tasks), ("contacts", parse_contacts)):
        target = collection(dav, kind)
        listed = dav.items(target)
        assert all(item.etag for item in listed)
        if target.sync_token:
            first = dav.changes(target, "")
            assert {i.href for i in first.changed} == {i.href for i in listed} and first.token
            again = dav.changes(target, first.token)
            assert again.changed == () and again.removed == ()
        got = dav.fetch(target, [item.href for item in listed][:20])
        assert len(got) == min(20, len(listed))
        for _, text in got.values():
            assert parser(text)  # what the server holds can be read


def test_an_event_a_task_and_a_contact_are_made_changed_and_deleted_and_the_server_keeps_what_we_wrote(dav: DavClient) -> None:
    ams = ZoneInfo("Europe/Amsterdam")
    uid = new_uid()
    cases = [
        ("events", events_to_text(EventSeries(Event(uid=uid, summary="Live test event", start=datetime(2030, 1, 15, 10, tzinfo=ams), end=datetime(2030, 1, 15, 11, tzinfo=ams), rrule="FREQ=WEEKLY;COUNT=3"))), "SUMMARY:Live test event", "SUMMARY:Live test changed"),
        ("tasks", tasks_to_text(Task(uid=uid, summary="Live test task", due=datetime(2030, 1, 15, 17, tzinfo=ams))), "SUMMARY:Live test task", "SUMMARY:Live test changed"),
        ("contacts", contact_to_text(Contact(uid=uid, given_name="Live", family_name="Test", emails=(Labeled("live.test@example.com"),))), "FN:Live Test", "FN:Live Changed"),
    ]
    for kind, text, before, after in cases:
        target = collection(dav, kind)
        href = target.href + f"live-test-{uid}.{'vcf' if kind == 'contacts' else 'ics'}"
        etag = dav.put(href, text, kind, create=True) or dav.etag_of(href)
        try:
            assert etag
            fetched = dav.fetch(target, [href])
            assert before in fetched[href][1].replace("\r\n", "\n").replace("\n", "\r\n") or uid in fetched[href][1]
            changed = text.replace(before.split(":", 1)[1], after.split(":", 1)[1])
            newer = dav.put(href, changed, kind, etag=etag) or dav.etag_of(href)
            assert newer and newer != etag
            assert after.split(":", 1)[1] in dav.fetch(target, [href])[href][1]
            if target.sync_token:
                token = dav.changes(target, "").token
                assert token
        finally:
            dav.delete(href)
        assert href not in {item.href for item in dav.items(target)}
    remove_from_deleted_items(uid, len(cases))


def test_the_synchronisation_brings_the_server_data_here_and_back(dav: DavClient, tmp_path: Path) -> None:
    from chrono_mail.core import CollectionKind
    from chrono_mail.pimstore import PimStore
    from chrono_mail.sync.davsync import sync_dav

    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    first = sync_dav(dav, store, state, durable=False)
    assert first.errors == [] and first.created >= 3 and first.pulled > 0
    kinds = {info.kind for info in store.collections()}
    assert kinds >= {CollectionKind.EVENTS, CollectionKind.TASKS, CollectionKind.CONTACTS}
    assert sync_dav(dav, store, state, durable=False).pulled == 0
    contacts = next(info for info in store.collections(CollectionKind.CONTACTS))
    uid = new_uid()
    text = contact_to_text(Contact(uid=uid, given_name="Chrono Mail", family_name="Sync Test"))
    created = store.put(contacts.collection_id, text)
    try:
        assert sync_dav(dav, store, state, durable=False).pushed == 1
        remote = collection(dav, "contacts")
        assert any("Chrono Mail Sync Test" in body for _, body in dav.fetch(remote, [item.href for item in dav.items(remote)]).values())
    finally:
        store.delete(contacts.collection_id, created)  # always remove the test item from the server
        report = sync_dav(dav, store, state, durable=False)
    assert report.deleted_on_server == 1
    remove_from_deleted_items(uid, 1)
    remote = collection(dav, "contacts")
    assert not any("Chrono Mail Sync Test" in body for _, body in dav.fetch(remote, [item.href for item in dav.items(remote)]).values())


def test_the_server_keeps_a_calendar_s_new_name_and_colour(dav: DavClient) -> None:
    from chrono_mail.protocols.dav import canonical_colour

    def now() -> RemoteCollection:
        return next(c for c in dav.collections(*dav.homes()) if c.href == calendar.href and c.kind == "events")

    calendar = collection(dav, "events")
    name, colour = calendar.name, calendar.colour
    try:
        statuses = dav.set_collection(calendar.href, f"{name} (test)", "#12ab34")
        print(f"\nthe server answered {statuses}; it lists the calendar as {now().name!r} {now().colour!r}")
        assert statuses["name"] == 200 and now().name == f"{name} (test)"
        assert statuses["colour"] != 200 or canonical_colour(now().colour) == "#12ab34"
    finally:
        dav.set_collection(calendar.href, name, colour or None)
    assert now().name == name


def test_an_event_goes_to_the_server_first_and_a_change_there_meanwhile_is_merged(dav: DavClient, tmp_path: Path) -> None:
    from chrono_mail.core import CollectionKind
    from chrono_mail.pimstore import PimStore
    from chrono_mail.sync.davitems import DONE, ItemGateway
    from chrono_mail.sync.davsync import sync_dav

    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    sync_dav(dav, store, state, durable=False)
    calendar = next(info for info in store.collections(CollectionKind.EVENTS) if not info.name.endswith("(public)"))
    uid = new_uid()
    text = events_to_text(EventSeries(Event(uid=uid, summary="Chrono Mail live test", location="Room 1", start=datetime(2026, 11, 3, 10, tzinfo=ZoneInfo("UTC")),
                                            end=datetime(2026, 11, 3, 11, tzinfo=ZoneInfo("UTC")))))
    gateway = ItemGateway(dav, store, state, durable=False)
    remote = collection(dav, "events")
    item = ""
    try:
        made = gateway.save(calendar, text)
        item = made.item
        assert made.status == DONE
        href, (etag, theirs) = next((h, v) for h, v in dav.fetch(remote, [i.href for i in dav.items(remote)]).items() if f"UID:{uid}" in v[1])
        dav.put(href, theirs.replace("LOCATION:Room 1", "LOCATION:Room 2"), "events", etag=etag)  # the phone changes the place
        changed = gateway.save(calendar, store.get(calendar.collection_id, item).replace("SUMMARY:Chrono Mail live test", "SUMMARY:Chrono Mail live test (renamed)"), item)
        assert changed.status == DONE
        now = dav.get(href)
        assert now is not None and "Chrono Mail live test (renamed)" in now[1] and "Room 2" in now[1]
    finally:
        if item:
            gateway.delete(calendar, item)
    assert dav.get(href) is None
    remove_from_deleted_items(uid, 1)


def test_a_calendar_deleted_on_the_server_goes_here_only_on_its_confirmation(dav: DavClient, tmp_path: Path) -> None:
    from chrono_mail.pimstore import PimStore
    from chrono_mail.protocols.dav import folder_key
    from chrono_mail.sync.davsync import sync_dav

    others = frozenset(folder_key(item) for item in dav.all_collections())  # only the test calendar is synchronised
    calendar_home, _ = dav.homes()
    href = calendar_home.rstrip("/") + f"/chrono-mail-test-{new_uid()[:8]}/"
    dav.make_collection(href, "events", "Chrono Mail live test", "#12ab34")
    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    try:
        assert dav.exists(href) is True
        assert sync_dav(dav, store, state, durable=False, left_out=others).created == 1
        assert [info.name for info in store.collections()] == ["Chrono Mail live test"]
    finally:
        dav.request("DELETE", href)  # always remove the test calendar from the server
    assert dav.exists(href) is False
    report = sync_dav(dav, store, state, durable=False, left_out=others)
    assert report.removed == 1 and report.errors == [] and list(store.collections()) == []


def test_a_calendar_deleted_here_offline_is_deleted_on_the_server_at_the_next_sync(dav: DavClient, tmp_path: Path) -> None:
    from chrono_mail.pimstore import PimStore
    from chrono_mail.protocols.dav import folder_key
    from chrono_mail.sync.davdeletes import changed_on_server, remember
    from chrono_mail.sync.davsync import DavState, sync_dav

    others = frozenset(folder_key(item) for item in dav.all_collections())
    calendar_home, _ = dav.homes()
    href = calendar_home.rstrip("/") + f"/chrono-mail-test-{new_uid()[:8]}/"
    dav.make_collection(href, "events", "Chrono Mail live test")
    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state_path = tmp_path / "pim" / "dav-state.json"
    try:
        assert sync_dav(dav, store, state_path, durable=False, left_out=others).created == 1
        (info,) = store.collections()
        assert changed_on_server(dav, href, "events", {}) == 0
        remember(store, info.server_value("href"), (info.collection_id,), "events", info.name, {})  # as the engine does when the server cannot be reached
        store.delete_collection(info.collection_id)
        DavState(state_path, durable=False).collections.pop(info.collection_id, None)
        report = sync_dav(dav, store, state_path, durable=False, left_out=others)
        assert report.errors == [] and report.deleted_on_server == 1 and list(store.collections()) == []
        assert dav.exists(info.server_value("href")) is False and dav.exists(href) is False
    finally:
        for place in (href, *(item.href for item in dav.all_collections() if item.name == "Chrono Mail live test")):
            if dav.exists(place):
                dav.request("DELETE", place)


def test_a_calendar_made_on_the_server_is_found_where_the_server_lists_it(dav: DavClient) -> None:
    calendar_home, address_home = dav.homes()
    href = calendar_home.rstrip("/") + f"/chrono-mail-test-{new_uid()[:8]}/"
    made = dav.make_collection(href, "events", "Chrono Mail live test")
    try:
        assert made.name == "Chrono Mail live test" and made.kind == "events" and dav.exists(made.href)
        print(f"\nasked for {href}, the server lists it at {made.href}")
    finally:
        dav.request("DELETE", made.href)
    assert dav.exists(made.href) is False
    try:
        book = dav.make_collection(address_home.rstrip("/") + f"/chrono-mail-test-{new_uid()[:8]}/", "contacts", "Chrono Mail live test")
        dav.request("DELETE", book.href)
        print("the server makes address books")
    except Exception as error:  # Kerio says yes and makes none: that must be an error, never a silent "done"
        print(f"the server makes no address books: {error}")


@pytest.mark.parametrize("kind", ["events", "tasks", "contacts"])
def test_a_new_collection_is_made_on_the_server_or_refused_in_plain_words(dav: DavClient, kind: str) -> None:
    from chrono_mail.protocols.dav import DavError
    from chrono_mail.sync.davcreates import make_on_server

    try:
        made = make_on_server(dav, kind, "Chrono Mail live test", "#12ab34")
    except DavError as error:
        print(f"\n{kind}: refused: {error}")
        assert "made no" in str(error) or error.status >= 400
        return
    try:
        print(f"\n{kind}: made at {made.href}")
        assert made.kind == kind and made.name == "Chrono Mail live test"
    finally:
        dav.request("DELETE", made.href)
    assert dav.exists(made.href) is False


def test_the_server_keeps_a_contact_group_in_both_forms_with_its_members(dav: DavClient) -> None:
    """R-39, checked on Kerio 2026-10-22: a vCard 4 group is kept as it was written; Apple's form becomes a group of Kerio's own (X-DL), which
    still reads back in Apple's form with its members."""
    from chrono_mail.pim.contact import GroupForm

    target = collection(dav, "contacts")
    member = new_uid()
    four_uid = new_uid()
    cards = {
        "member": contact_to_text(Contact(uid=member, given_name="Live", family_name="Member", emails=(Labeled("live.member@example.com"),))),
        "vcard4": contact_to_text(Contact(uid=four_uid, display_name="Live group four", group_form=GroupForm.VCARD4, members=(f"urn:uuid:{member}", "mailto:plain@example.com"))),
        "apple": contact_to_text(Contact(uid=new_uid(), display_name="Live group apple", family_name="Live group apple", group_form=GroupForm.APPLE, members=(f"urn:uuid:{member}",))),
    }
    made: list[str] = []
    try:
        for name, text in cards.items():
            href = target.href + f"live-group-{name}-{new_uid()[:8]}.vcf"
            dav.put(href, text, "contacts", create=True)
            made.append(href)
        fetched = dav.fetch(target, made)
        four = parse_contacts(fetched[made[1]][1])[0]
        apple = parse_contacts(fetched[made[2]][1])[0]
        assert four.group_form is GroupForm.VCARD4 and four.members == (f"urn:uuid:{member}", "mailto:plain@example.com")
        assert apple.group_form is GroupForm.APPLE and apple.members == (f"urn:uuid:{member}",) and apple.full_name() == "Live group apple"
    finally:
        for href in made:
            dav.delete(href)
    remove_from_deleted_items(member, 1)  # Kerio keeps the member and the vCard 4 group in Deleted Items, not a group of its own; each is
    remove_from_deleted_items(four_uid, 1)  # found by its own uid (the member's is folded over two lines in the group)


def test_one_contact_is_read_from_the_server_although_its_get_is_404_on_kerio(dav: DavClient) -> None:
    target = collection(dav, "contacts")
    uid = new_uid()
    href = target.href + f"live-read-{uid[:8]}.vcf"
    dav.put(href, contact_to_text(Contact(uid=uid, given_name="Live", family_name="Read")), "contacts", create=True)
    try:
        found = dav.read(target, href)
        assert found is not None and found[0] and parse_contacts(found[1])[0].full_name() == "Live Read"
    finally:
        dav.delete(href)
    assert dav.read(target, href) is None  # gone: not there
    remove_from_deleted_items(uid, 1)
