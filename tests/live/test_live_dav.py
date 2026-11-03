import time
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

from chrono_mail.pim.contact import Contact, Labeled, contact_to_text, parse_contacts
from chrono_mail.pim.ical import Event, EventSeries, Task, events_to_text, new_uid, parse_events, parse_tasks, tasks_to_text
from chrono_mail.protocols.dav import DavClient, RemoteCollection
from tests.live.account import KERIO_FILE, SETTINGS_DIRECTORY, LiveAccount, load_live_account
from tests.live.test_live_imap import connect
from datetime import datetime
from zoneinfo import ZoneInfo

# --- Version ------------------------------------------------------------
__version__ = "test_live_dav.py 4.3.498"

# --- Version history ----------------------------------------------------
# v4.3.498: a contact changed on the server and then here keeps the server's change (Kerio ignores If-Match on contacts); Keep both sends
#           no invitation for the copy, or records that it did (an invitee at another server, Kerio's Sent Items watched; all mails
#           removed again; Kerio sends one, which the owner accepts); the older Keep both test invites that real mailbox too;
#           Keep both on the server (B-30, B-31, the owner): a contact and an appointment with an attendee kept as two, each with its own uid,
#           the contact's copy in the original's group; a contact's two e-mail addresses kept both; everything made is removed again
# v4.2.497: Deleted Items is searched from the day before today: Kerio dates by UTC, so between 00:00 and 01:00 CET nothing was found;
#           the UID check keeps it exact
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
    since = date.today() - timedelta(days=1)  # Kerio dates by UTC: just after midnight here it is still yesterday there; the UID keeps it exact
    with connect(account, secure=True) as imap:
        ours: list[int] = []
        deadline = time.monotonic() + 20
        while len(ours) < expected and time.monotonic() < deadline:
            imap.select(account.trash_folder)
            recent = imap.uid_search(f"SINCE {since.day}-{MONTHS[since.month - 1]}-{since.year}")
            fetched = imap.uid_fetch(",".join(str(number) for number in recent), "(UID BODY.PEEK[])") if recent else ()
            ours = [item.uid for item in fetched if uid.encode() in (item.data or b"")]
            if len(ours) < expected:
                time.sleep(1)
        if ours:
            numbers = ",".join(str(number) for number in ours)
            imap.uid_store(numbers, ["\\Deleted"], "add")
            imap.uid_expunge(numbers)  # these UIDs only; inside Deleted Items this is final


def remove_mails(account: LiveAccount, folder: str, mark: str, wait: float = 0) -> int:
    """The mails in a folder that carry a test's mark (its subject or its calendar), removed (on Kerio they go to Deleted Items); `wait`: up
    to so many seconds for the first to arrive. Returns how many."""
    since = date.today() - timedelta(days=1)
    deadline = time.monotonic() + wait
    with connect(account, secure=True) as imap:
        while True:
            imap.select(folder)
            recent = imap.uid_search(f"SINCE {since.day}-{MONTHS[since.month - 1]}-{since.year}")
            fetched = imap.uid_fetch(",".join(str(number) for number in recent), "(UID BODY.PEEK[])") if recent else ()
            ours = [item.uid for item in fetched if mark.encode() in (item.data or b"")]
            if ours or time.monotonic() >= deadline:
                break
            time.sleep(1)
        if ours:
            numbers = ",".join(str(number) for number in ours)
            imap.uid_store(numbers, ["\\Deleted"], "add")
            imap.uid_expunge(numbers)
        return len(ours)


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


def test_keep_both_makes_two_items_on_the_server_and_a_split_row_keeps_both_addresses(dav: DavClient, tmp_path: Path) -> None:
    """B-30, B-31 (the owner): the copy is made on the server first with a uid of its own, the original keeps the server's version, a
    contact's copy joins the original's group, and two e-mail addresses kept both are both on the server. The appointment's attendee is the
    invitee's real mailbox (invitee.local.toml), so what the server sent can be checked there; the mails this test causes are removed again
    (the invitee's, Kerio's sent ones and the organizer's returned ones, then Deleted Items)."""
    from chrono_mail.core import CollectionKind
    from chrono_mail.engine.commands_conflicts import _keep_both  # noqa: PLC2701: the engine's own Keep both, groups included
    from chrono_mail.pim.contact import GroupForm
    from chrono_mail.pim.ical import Attendee
    from chrono_mail.pim.merge import BOTH
    from chrono_mail.pim.vcard import unfold
    from chrono_mail.pimstore import PimStore
    from chrono_mail.protocols.dav import canonical_href
    from chrono_mail.sync.davconflicts import SyncConflict, keep_base, open_conflict
    from chrono_mail.sync.davitems import ItemGateway
    from chrono_mail.sync.davsync import sync_dav
    from tests.live.account import INVITEE_FILE

    if not INVITEE_FILE.exists():
        pytest.skip(f"{INVITEE_FILE} is missing")
    invitee = load_live_account(INVITEE_FILE)
    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    sync_dav(dav, store, state, durable=False)
    remotes = {kind: collection(dav, kind) for kind in ("contacts", "events")}
    infos = {kind: next(info for info in store.collections(CollectionKind(kind)) if canonical_href(info.server_value("href")) == canonical_href(remotes[kind].href))
             for kind in remotes}
    gateway = ItemGateway(dav, store, state, durable=False)
    mark = new_uid()[:8]  # every item of this test carries it in its name: found on the server, and removed, by it

    def on_server(kind: str) -> dict[str, str]:
        """The test's items on the server: href -> text, one line per property (unfolded, CRLF)."""
        remote = remotes[kind]
        fetched = dav.fetch(remote, [item.href for item in dav.items(remote)]).items()
        return {href: "\r\n".join(unfold(text)) for href, (_, text) in fetched if mark in "".join(unfold(text))}

    def clash(kind: str, text: str, theirs: tuple[str, str], ours: tuple[str, str]) -> SyncConflict:
        """An item made here and on the server, then changed on the server and here while offline: the synchronisation finds the sync conflict.
        (Kerio takes a contact's change sent with an old version without a word (no If-Match check), so a change sent at once cannot clash.)"""
        info = infos[kind]
        item = gateway.save(info, text).item
        href = next(href for href, body in on_server(kind).items() if f"UID:{uid_of(text)}" in body)
        etag, body = dav.read(remotes[kind], href) or ("", "")
        dav.put(href, body.replace(*theirs), kind, etag=etag)  # on the phone
        current = store.get(info.collection_id, item)
        keep_base(store, info.collection_id, item, current)  # changed here offline: the agreed version kept, as ItemGateway does
        store.put(info.collection_id, current.replace(*ours), item)
        sync_dav(dav, store, state, durable=False)
        found = open_conflict(store, info.collection_id, item=item)
        assert found is not None and found.reason == "clash", found
        return found

    def uid_of(text: str) -> str:
        return next(line.partition(":")[2].strip() for line in unfold(text) if line.split(":", 1)[0].split(";", 1)[0].strip().upper() == "UID")

    try:
        anna, group = new_uid(), new_uid()
        card = contact_to_text(Contact(uid=anna, given_name="Live", family_name=f"Keep both {mark}", emails=(Labeled("live.old@example.com", ("work",)),)))
        gateway.save(infos["contacts"], contact_to_text(Contact(uid=group, display_name=f"Live group {mark}", group_form=GroupForm.VCARD4, members=(f"urn:uuid:{anna}",))))
        conflict = clash("contacts", card, ("live.old@example.com", "live.there@example.com"), ("live.old@example.com", "live.here@example.com"))
        assert _keep_both(store, gateway, conflict) == ("", False)
        cards = on_server("contacts")
        (original,) = [text for text in cards.values() if f"UID:{anna}" in text]
        (copy,) = [text for text in cards.values() if "live.here@example.com" in text]
        copy_uid = uid_of(copy)
        print(f"\ncontact: original {anna}, copy {copy_uid}")
        assert "live.there@example.com" in original and "live.here@example.com" not in original and copy_uid != anna
        (members,) = [parse_contacts(text)[0].members for text in cards.values() if f"UID:{group}" in text]
        print(f"group members on the server: {members}")
        assert {member.removeprefix("urn:uuid:") for member in members} == {anna, copy_uid}  # the copy is in the original's group

        bob = new_uid()
        second = contact_to_text(Contact(uid=bob, given_name="Live", family_name=f"Split {mark}", emails=(Labeled("split.old@example.com", ("work",)),)))
        split = clash("contacts", second, ("split.old@example.com", "split.there@example.com"), ("split.old@example.com", "split.here@example.com"))
        choices = {each.key: BOTH for each in split.clashes}
        assert choices and all(key.startswith("EMAIL") for key in choices)
        assert gateway.settle(infos["contacts"], split, "", choices).status == "done"
        (both,) = [text for text in on_server("contacts").values() if f"UID:{bob}" in text]
        print(f"split row: {[line for line in both.split(chr(13) + chr(10)) if line.startswith('EMAIL')]}")
        assert "split.here@example.com" in both and "split.there@example.com" in both

        meeting = new_uid()
        ams = ZoneInfo("Europe/Amsterdam")
        event = events_to_text(EventSeries(Event(
            uid=meeting, summary=f"Live keep both {mark}", start=datetime(2030, 2, 4, 10, tzinfo=ams), end=datetime(2030, 2, 4, 11, tzinfo=ams),
            rrule="FREQ=WEEKLY;COUNT=3", organizer=Attendee("live.organizer@example.com", "Organizer"), attendees=(Attendee(invitee.user, "Attendee"),),
        )))
        conflict = clash("events", event, (f"SUMMARY:Live keep both {mark}", f"SUMMARY:Live keep both {mark} there"),
                         (f"SUMMARY:Live keep both {mark}", f"SUMMARY:Live keep both {mark} here"))
        assert _keep_both(store, gateway, conflict) == ("", False)
        events = on_server("events")
        (theirs,) = [text for text in events.values() if f"UID:{meeting}" in text]
        (copied,) = [text for text in events.values() if f"{mark} here" in text]
        print(f"appointment: original {meeting}, copy {uid_of(copied)}; the copy's people on the server: "
              f"{[line for line in copied.split(chr(13) + chr(10)) if line.startswith(('ATTENDEE', 'ORGANIZER'))]}")
        assert f"{mark} there" in theirs and uid_of(copied) != meeting and "RRULE:FREQ=WEEKLY;COUNT=3" in copied and invitee.user in copied
    finally:
        deleted = 0
        for kind in ("contacts", "events"):
            for href in on_server(kind):
                dav.delete(href)  # always remove everything this test made, also from Deleted Items
                deleted += 1
        kerio = load_live_account(KERIO_FILE)
        received = remove_mails(invitee, "INBOX", mark, wait=3)  # what the server sent to the attendee: removed for good
        print(f"\nthe attendee received {received} mails of this test")
        moved = remove_mails(kerio, kerio.sent_folder, mark) + remove_mails(kerio, "INBOX", mark, wait=2)  # sent, and the organizer's returned
        remove_from_deleted_items(mark, deleted + moved)  # every item and mail has the mark: one search (the limit of 20 s per test)
    assert on_server("contacts") == {} and on_server("events") == {}


def test_a_contact_changed_on_the_server_and_then_here_keeps_the_server_s_change(dav: DavClient, tmp_path: Path) -> None:
    """D-193, D-194: a contact changed on the server (a phone), then here while online without a synchronisation in between, and sent. Kerio
    takes a contact's PUT with an old version without a word (no If-Match check, measured 2026-11-03): the server's change must survive,
    merged or a sync conflict, never written over."""
    from chrono_mail.core import CollectionKind
    from chrono_mail.pimstore import PimStore
    from chrono_mail.protocols.dav import canonical_href
    from chrono_mail.sync.davconflicts import open_conflict
    from chrono_mail.sync.davitems import ItemGateway
    from chrono_mail.sync.davsync import sync_dav

    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    sync_dav(dav, store, state, durable=False)
    remote = collection(dav, "contacts")
    info = next(info for info in store.collections(CollectionKind.CONTACTS) if canonical_href(info.server_value("href")) == canonical_href(remote.href))
    gateway = ItemGateway(dav, store, state, durable=False)
    uid = new_uid()
    card = contact_to_text(Contact(uid=uid, given_name="Live", family_name="If-Match", note="Written here"))
    item = gateway.save(info, card).item
    href = next(href for href, link in gateway.state.get(info.collection_id).links.items() if link.item_id == item)
    try:
        etag, body = dav.read(remote, href) or ("", "")
        accepted = dav.put(href, body.replace("FN:Live If-Match", "FN:Live If-Match there"), "contacts", etag=etag)  # on the phone
        print(f"\nthe server's change took the version {accepted!r} (was {etag!r})")
        outcome = gateway.save(info, store.get(info.collection_id, item).replace("Written here", "Changed here"), item)
        now = dav.read(remote, href)
        print(f"Chrono Mail's change: {outcome.status}; on the server now: {[line for line in (now[1] if now else '').splitlines() if line.startswith(('FN', 'NOTE'))]}")
        assert now is not None
        kept = "Live If-Match there" in now[1]
        assert kept or open_conflict(store, info.collection_id, item=item) is not None, "the server's change was written over"
        assert kept, "the server's change was written over (a conflict was recorded, but the server lost it)"
    finally:
        dav.delete(href)
        remove_from_deleted_items(uid, 1)  # also when the test fails: nothing of it stays on the server


def test_keep_both_sends_no_invitation_for_the_copy(dav: DavClient, tmp_path: Path) -> None:
    """B-30 (the owner): an appointment of the Kerio account with an attendee at another server, changed here and on the server, kept as two:
    an invitation for the original may be sent; whether one carries the copy's uid (its people are SCHEDULE-AGENT=CLIENT) is printed: Kerio
    sends one anyway, which the owner accepts. What Kerio sent (its
    Sent Items) and the invitee's Inbox are watched within the 20 s limit of every test (conftest.py; only -m perf is exempt). Everything
    made is removed again: the items, Deleted Items, Kerio's sent invitations, the mails the invitee received; before the original is
    deleted its attendee is set to SCHEDULE-AGENT=CLIENT, so no cancellation is sent. A server's own lines in the copy (an attendee line for
    the organizer) are no change of the copy (merge.reading)."""
    import email
    import email.policy

    from chrono_mail.core import CollectionKind
    from chrono_mail.engine.commands_conflicts import _keep_both  # noqa: PLC2701: the engine's own Keep both
    from chrono_mail.pim.ical import Attendee
    from chrono_mail.pim.merge import SEPARATOR, VALUE_MARK, reading
    from chrono_mail.pim.vcard import unfold
    from chrono_mail.pimstore import PimStore
    from chrono_mail.protocols.dav import canonical_href
    from chrono_mail.sync.davconflicts import keep_base, open_conflict
    from chrono_mail.sync.davitems import ItemGateway, written_digest
    from chrono_mail.sync.davsync import sync_dav
    from tests.live.account import INVITEE_FILE

    if not INVITEE_FILE.exists():
        pytest.skip(f"{INVITEE_FILE} is missing")
    started = time.monotonic()
    invitee = load_live_account(INVITEE_FILE)
    with SETTINGS.open("rb") as handle:
        organizer = str(tomllib.load(handle)["user"])
    store = PimStore.create(tmp_path / "pim", None, durable=False)
    state = tmp_path / "pim" / "dav-state.json"
    sync_dav(dav, store, state, durable=False)
    remote = collection(dav, "events")
    info = next(info for info in store.collections(CollectionKind.EVENTS) if canonical_href(info.server_value("href")) == canonical_href(remote.href))
    gateway = ItemGateway(dav, store, state, durable=False)
    mark = new_uid()[:8]
    meeting = new_uid()

    def on_server() -> dict[str, tuple[str, str]]:
        fetched = dav.fetch(remote, [item.href for item in dav.items(remote)]).items()
        return {href: (etag, "\r\n".join(unfold(text))) for href, (etag, text) in fetched if mark in "".join(unfold(text))}

    kerio = load_live_account(KERIO_FILE)

    def mails(account: LiveAccount, folder: str, remove: bool = False) -> dict[int, str]:
        """The mails of this test in a folder (its mark in them): number -> its subject, To and calendar's METHOD and UID lines. `remove`:
        removed for good."""
        since = date.today() - timedelta(days=1)
        with connect(account, secure=True) as imap:
            imap.select(folder)
            recent = imap.uid_search(f"SINCE {since.day}-{MONTHS[since.month - 1]}-{since.year}")
            fetched = imap.uid_fetch(",".join(str(number) for number in recent), "(UID BODY.PEEK[])") if recent else ()
            ours: dict[int, str] = {}
            for item in fetched:
                message = email.message_from_bytes(item.data or b"", policy=email.policy.default)
                calendar = "".join(str(part.get_content()) for part in message.walk() if part.get_content_type() == "text/calendar")
                if mark in str(message["Subject"]) or mark in calendar:
                    lines = [line.strip() for line in calendar.replace("\r", "").split("\n") if line.startswith(("METHOD:", "UID:"))]
                    ours[item.uid] = f"{message['Subject']!r} to {message['To']}: {lines}"
            if remove and ours:
                numbers = ",".join(str(number) for number in ours)
                imap.uid_store(numbers, ["\\Deleted"], "add")
                imap.uid_expunge(numbers)
            return ours

    copy_uid = ""
    try:
        event = events_to_text(EventSeries(Event(
            uid=meeting, summary=f"Live invitation {mark}", start=datetime(2030, 3, 4, 10, tzinfo=ZoneInfo("Europe/Amsterdam")),
            end=datetime(2030, 3, 4, 11, tzinfo=ZoneInfo("Europe/Amsterdam")), organizer=Attendee(organizer, "Chrono Mail"),
            attendees=(Attendee(invitee.user, "Invitee"),),
        )))
        item = gateway.save(info, event).item
        href, (etag, body) = next((href, value) for href, value in on_server().items() if f"UID:{meeting}" in value[1])
        dav.put(href, body.replace(f"SUMMARY:Live invitation {mark}", f"SUMMARY:Live invitation {mark} there"), "events", etag=etag)  # the phone
        current = store.get(info.collection_id, item)
        keep_base(store, info.collection_id, item, current)  # changed here offline: the agreed version kept, as ItemGateway does
        store.put(info.collection_id, current.replace(f"SUMMARY:Live invitation {mark}", f"SUMMARY:Live invitation {mark} here"), item)
        sync_dav(dav, store, state, durable=False)
        conflict = open_conflict(store, info.collection_id, item=item)
        assert conflict is not None and conflict.reason == "clash", conflict
        assert _keep_both(store, gateway, conflict) == ("", False)
        (copied,) = [text for _, text in on_server().values() if f"{mark} here" in text]
        copy_uid = next(line.partition(":")[2].strip() for line in copied.split("\r\n") if line.startswith("UID:"))
        here = next(found for found in [store.index.find_uid(info.collection_id, copy_uid)] if found is not None)
        print(f"\noriginal {meeting}, copy {copy_uid}; the copy's people on the server: "
              f"{[line for line in copied.split(chr(13) + chr(10)) if line.startswith(('ATTENDEE', 'ORGANIZER'))]}")
        ours, theirs = reading(store.get(info.collection_id, here.item_id)) or "", reading(copied) or ""
        differing = [part for part in theirs.split(VALUE_MARK) if part not in ours]  # what reads otherwise in the copy as the server keeps it
        print(f"the copy reads otherwise on the server in: {differing}")
        assert not any(f"ATTENDEE{SEPARATOR}mailto:{organizer.lower()}" in part for part in differing)  # never the organizer's line (Kerio's)
        sent: dict[int, str] = {}
        arrived: dict[int, str] = {}
        while time.monotonic() - started < 12:  # the whole window within the limit of 20 s, with the clean-up still to come
            sent, arrived = mails(kerio, kerio.sent_folder), mails(invitee, "INBOX")
            if any(f"UID:{copy_uid}" in text for text in (*sent.values(), *arrived.values())):
                break
            time.sleep(1)
        for where, found in (("Kerio sent", sent), ("the invitee received", arrived)):
            for number, text in found.items():
                print(f"{where} {number}: {text}")
        print(f"after {time.monotonic() - started:.0f} s: {len(sent)} sent, {len(arrived)} received")
        invited = any(f"UID:{copy_uid}" in text for text in (*sent.values(), *arrived.values()))
        print(f"the server sent an invitation for the copy: {invited}")  # Kerio does, despite SCHEDULE-AGENT=CLIENT (2026-11-03, also with
        # X-SENT=TRUE): recorded, not a failure (the owner accepts it)
    finally:
        made = on_server()
        for place, (etag, text) in made.items():
            if f"UID:{meeting}" in text:  # its attendee no longer the server's to invite: no cancellation is sent when it goes
                quiet = "\r\n".join(line.replace("ATTENDEE;", "ATTENDEE;SCHEDULE-AGENT=CLIENT;", 1) if line.startswith("ATTENDEE;") and "SCHEDULE-AGENT" not in line else line
                                    for line in text.split("\r\n"))
                etag = dav.put(place, quiet, "events", etag=etag) or ""
            dav.delete(place)
        mails(invitee, "INBOX", remove=True)  # the mails the invitee received: removed for good (they are this test's alone)
        expected = len(made) + len(mails(kerio, kerio.sent_folder, remove=True))  # Kerio's sent invitations go to Deleted Items too
        remove_from_deleted_items(mark, expected)
    assert on_server() == {}
