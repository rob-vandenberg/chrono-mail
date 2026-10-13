import time
import tomllib
from datetime import date
from pathlib import Path

import pytest

from chrono_mail.pim.contact import Contact, Labeled, contact_to_text, parse_contacts
from chrono_mail.pim.ical import Event, EventSeries, Task, events_to_text, new_uid, parse_events, parse_tasks, tasks_to_text
from chrono_mail.protocols.dav import DavClient, RemoteCollection
from tests.live.account import KERIO_FILE, load_live_account
from tests.live.test_live_imap import connect
from datetime import datetime
from zoneinfo import ZoneInfo

# --- Version ------------------------------------------------------------
__version__ = "test_live_dav.py 0.5.123"

# --- Version history ----------------------------------------------------
# v0.5.123: The items Kerio keeps in Deleted Items after the test deleted them are removed, found by their unique UID
# v0.0.66: Live test of the synchronisation
# v0.0.65: Initial live tests of the CalDAV and CardDAV client

SETTINGS = Path(__file__).with_name("dav.local.toml")
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def dav() -> DavClient:
    if not SETTINGS.exists():
        pytest.skip("tests/live/dav.local.toml is missing (see dav.example.toml)")
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
