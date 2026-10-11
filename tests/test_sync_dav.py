from pathlib import Path
from typing import override

import pytest

from chrono_mail.core import AuthenticationError, CollectionKind
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient
from chrono_mail.sync.davsync import DavReport, conflict_copy, sync_dav
from tests.fakedav import FakeDav

# --- Version ------------------------------------------------------------
__version__ = "test_sync_dav.py 0.0.66"

# --- Version history ----------------------------------------------------
# v0.0.66: Initial tests of the synchronisation with a CalDAV and CardDAV server


def event(uid: str, summary: str) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:20261012T090000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def task(uid: str, summary: str) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:{uid}\r\nSUMMARY:{summary}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"


def card(uid: str, name: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\nFN:{name}\r\nEND:VCARD\r\n"


class Setup:
    def __init__(self, tmp_path: Path, server: FakeDav) -> None:
        self.server = server
        self.store = PimStore.create(tmp_path / "pim", None, durable=False)
        self.state = tmp_path / "pim" / "dav-state.json"

    def sync(self) -> DavReport:
        return sync_dav(DavClient(self.server.url, "ann", "secret"), self.store, self.state, durable=False)

    def collection(self, kind: CollectionKind) -> str:
        return next(info.collection_id for info in self.store.collections(kind))

    def titles(self, kind: CollectionKind) -> list[str]:
        identifier = self.collection(kind)
        found = (self.store.index.get(item) for item in self.store.index.item_ids(identifier))
        return sorted(item.title for item in found if item is not None)


class RacingClient(DavClient):
    """A client whose writes are always preceded by a change of the same item on the server."""

    def __init__(self, server: FakeDav, url: str, user: str, password: str) -> None:
        super().__init__(url, user, password)
        self.server: FakeDav = server

    @override
    def put(self, href: str, text: str, kind: str, etag: str = "", create: bool = False) -> str:
        self.server.add("events", "e1.ics", event("e1", "Theirs"))
        return super().put(href, text, kind, etag, create)


def fill(server: FakeDav) -> None:
    server.add("events", "e1.ics", event("e1", "Meeting"))
    server.add("events", "e2.ics", event("e2", "Lunch"))
    server.add("tasks", "t1.ics", task("t1", "Write"))
    server.add("contacts", "c1.vcf", card("c1", "Ann Lee"))


@pytest.mark.parametrize(
    ("sync_tokens", "multiget", "etag_on_put"),
    [(True, True, True), (False, True, True), (True, False, True), (True, True, False), (False, False, False)],
)
def test_the_whole_cycle_with_servers_of_every_kind(tmp_path: Path, sync_tokens: bool, multiget: bool, etag_on_put: bool) -> None:
    with FakeDav(sync_tokens=sync_tokens, multiget=multiget, etag_on_put=etag_on_put) as server:
        fill(server)
        setup = Setup(tmp_path, server)
        first = setup.sync()
        assert (first.created, first.pulled, first.pushed, first.errors) == (3, 4, 0, [])
        assert setup.titles(CollectionKind.EVENTS) == ["Lunch", "Meeting"] and setup.titles(CollectionKind.TASKS) == ["Write"] and setup.titles(CollectionKind.CONTACTS) == ["Ann Lee"]
        again = setup.sync()
        assert (again.created, again.pulled, again.pushed, again.deleted_here, again.errors) == (0, 0, 0, 0, [])

        events = setup.collection(CollectionKind.EVENTS)  # new here, changed here, deleted here
        setup.store.put(events, event("e3", "Made here"))
        meeting = setup.store.index.find_uid(events, "e1")
        assert meeting is not None
        setup.store.put(events, event("e1", "Meeting moved"), meeting.item_id)
        lunch = setup.store.index.find_uid(events, "e2")
        assert lunch is not None
        setup.store.delete(events, lunch.item_id)
        report = setup.sync()
        assert (report.pushed, report.deleted_on_server, report.errors) == (2, 1, [])
        names = server.collections["events"].items
        assert sorted(names) == ["e1.ics", "e3.ics"] and "Meeting moved" in names["e1.ics"][1]
        assert setup.sync().pulled == 0  # what was pushed is not pulled back as news

        server.add("events", "e1.ics", event("e1", "Changed on the server"))  # changed and deleted on the server
        server.remove("events", "e3.ics")
        server.add("contacts", "c2.vcf", card("c2", "Bob"))
        report = setup.sync()
        assert (report.pulled, report.deleted_here, report.errors) == (2, 1, [])
        assert setup.titles(CollectionKind.EVENTS) == ["Changed on the server"] and setup.titles(CollectionKind.CONTACTS) == ["Ann Lee", "Bob"]


def test_a_change_on_both_sides_is_settled_for_the_server_and_the_local_version_is_kept(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        events = setup.collection(CollectionKind.EVENTS)
        meeting = setup.store.index.find_uid(events, "e1")
        assert meeting is not None
        setup.store.put(events, event("e1", "Mine"), meeting.item_id)
        server.add("events", "e1.ics", event("e1", "Theirs"))
        report = setup.sync()
        assert report.conflicts == 1 and report.pushed == 1
        assert setup.titles(CollectionKind.EVENTS) == ["Lunch", "Theirs", "[Conflict] Mine"]
        assert any("[Conflict] Mine" in text for _, text in server.collections["events"].items.values())  # the kept version is on the server as an item of its own


def test_a_change_made_here_loses_nothing_when_the_server_changed_the_item_at_the_same_time(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        events = setup.collection(CollectionKind.EVENTS)
        meeting = setup.store.index.find_uid(events, "e1")
        assert meeting is not None
        setup.store.put(events, event("e1", "Mine"), meeting.item_id)
        sync_dav(RacingClient(server, server.url, "ann", "secret"), setup.store, setup.state, durable=False)
        assert "Theirs" in server.collections["events"].items["e1.ics"][1]
        report = setup.sync()  # the next round settles it
        assert report.conflicts == 1 and "Mine" in " ".join(setup.titles(CollectionKind.EVENTS))


def test_a_read_only_collection_is_pulled_and_never_written(tmp_path: Path) -> None:
    with FakeDav(read_only=("tasks",)) as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        tasks = setup.collection(CollectionKind.TASKS)
        assert setup.store.get_collection(tasks).read_only
        setup.store.put(tasks, task("t9", "Local only"))
        setup.store.delete(tasks, next(iter(setup.store.index.item_ids(tasks))))
        report = setup.sync()
        assert report.pushed == 0 and report.deleted_on_server == 0 and "t1.ics" in server.collections["tasks"].items


def test_losing_the_state_file_does_not_double_anything(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        setup.state.unlink()
        report = setup.sync()
        assert (report.created, report.conflicts, report.pushed, report.errors) == (0, 0, 0, [])
        assert setup.titles(CollectionKind.EVENTS) == ["Lunch", "Meeting"]


def test_items_made_here_get_a_safe_name_on_the_server(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        setup.store.put(setup.collection(CollectionKind.CONTACTS), card("a/b c@d", "Odd"))
        report = setup.sync()
        assert report.pushed == 1 and report.errors == [] and len(server.collections["contacts"].items) == 2
        assert all("/" not in name and " " not in name for name in server.collections["contacts"].items)


def test_a_collection_that_is_gone_from_the_server_stays_here_without_a_tie(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        del server.collections["tasks"]
        setup.sync()
        info = setup.store.get_collection(setup.collection(CollectionKind.TASKS))
        assert info.server == () and setup.titles(CollectionKind.TASKS) == ["Write"]


def test_a_wrong_password_stops_the_synchronisation(tmp_path: Path) -> None:
    with FakeDav() as server:
        setup = Setup(tmp_path, server)
        with pytest.raises(AuthenticationError):
            sync_dav(DavClient(server.url, "ann", "wrong"), setup.store, setup.state, durable=False)


def test_a_conflict_copy_has_a_new_uid_and_a_marked_title() -> None:
    copy = conflict_copy(CollectionKind.EVENTS, event("e1", "Meeting"))
    assert "UID:e1" not in copy and "SUMMARY:[Conflict] Meeting" in copy and copy.endswith("\r\n")
    plain = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No uid\r\nEND:VCARD\r\n"
    assert "UID:" in conflict_copy(CollectionKind.CONTACTS, plain) and "FN:[Conflict] No uid" in conflict_copy(CollectionKind.CONTACTS, plain)
