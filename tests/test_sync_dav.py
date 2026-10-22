from pathlib import Path
from typing import override

import pytest

from chrono_mail.core import AuthenticationError, CollectionKind, ProtocolError
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient, DavConflict, RemoteChanges, RemoteCollection, RemoteItem, folder_key
from chrono_mail.sync.davconflicts import load_conflicts
from chrono_mail.sync.davsync import DavReport, sync_dav, unsent_changes
from tests.fakedav import Collection, FakeDav

# --- Version ------------------------------------------------------------
__version__ = "test_sync_dav.py 1.1.234"

# --- Version history ----------------------------------------------------
# v1.1.234: a collection deleted on the server goes here only on the server's confirmation; a failed list or check removes nothing; changed here:
#           a sync conflict (R-25)
# v1.1.225: a change on both sides is a sync conflict (no more "[Conflict]" copies); nothing of it is sent until the person settles it (D-194)
# v1.1.220: a real mass delete on the server settles at the second synchronisation
# v1.1.218: The Windows computer's report: a change report cut short, a listing that lost most items, one item that times out
# v1.1.217: a server that writes @ as %40 in its change report and lists items twice (Kerio)
# v1.1.210: public folders, folders left out, changes not yet sent
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

    def sync(self, left_out: frozenset[str] = frozenset()) -> DavReport:
        return sync_dav(DavClient(self.server.url, "ann", "secret"), self.store, self.state, durable=False, left_out=left_out)

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


def test_a_change_on_both_sides_without_an_agreed_version_is_one_whole_conflict_and_nothing_is_sent(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        events = setup.collection(CollectionKind.EVENTS)
        meeting = setup.store.index.find_uid(events, "e1")
        assert meeting is not None
        setup.store.put(events, event("e1", "Mine"), meeting.item_id)
        server.add("events", "e1.ics", event("e1", "Theirs"))
        report = setup.sync()  # changed here before 1.1.225 (no agreed version kept): the whole item is one conflict
        assert report.conflicts == 1 and report.pushed == 0
        assert setup.titles(CollectionKind.EVENTS) == ["Lunch", "Mine"] and "Theirs" in server.collections["events"].items["e1.ics"][1]
        (conflict,) = load_conflicts(setup.store)
        assert conflict.whole and [(c.label, c.local, c.server) for c in conflict.clashes] == [("Title", "Mine", "Theirs")]
        again = setup.sync()  # nothing is sent while it is open, and it is not doubled
        assert (again.pushed, again.conflicts) == (0, 0) and len(load_conflicts(setup.store)) == 1


def test_a_change_made_here_loses_nothing_when_the_server_changed_the_item_at_the_same_time(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        events = setup.collection(CollectionKind.EVENTS)
        meeting = setup.store.index.find_uid(events, "e1")
        assert meeting is not None
        setup.store.put(events, event("e1", "Mine"), meeting.item_id)
        report = sync_dav(RacingClient(server, server.url, "ann", "secret"), setup.store, setup.state, durable=False)
        assert "Theirs" in server.collections["events"].items["e1.ics"][1]  # the server's change is not overwritten
        assert report.conflicts == 1 and "Mine" in " ".join(setup.titles(CollectionKind.EVENTS)) and len(load_conflicts(setup.store)) == 1  # nor ours lost


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


def test_a_collection_the_server_confirms_deleted_goes_here_too(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        del server.collections["tasks"]
        report = setup.sync()
        assert report.removed == 1 and report.conflicts == 0 and report.errors == []
        assert list(setup.store.collections(CollectionKind.TASKS)) == [] and load_conflicts(setup.store) == []
        assert len(list(setup.store.collections())) == 2  # the others are untouched


def test_a_list_the_server_could_not_give_removes_nothing(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        server.homes_failing = True
        with pytest.raises(ProtocolError):
            setup.sync()
        assert len(list(setup.store.collections())) == 3
        assert all(info.server_value("href") for info in setup.store.collections())


def test_no_confirmation_of_the_deletion_keeps_the_collection_and_asks_again(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        del server.collections["tasks"]
        server.collection_check_failing = True
        report = setup.sync()
        info = setup.store.get_collection(setup.collection(CollectionKind.TASKS))
        assert report.removed == 0 and info.server_value("href") and setup.titles(CollectionKind.TASKS) == ["Write"]
        assert any("did not confirm it was deleted" in line for line in report.errors)
        server.collection_check_failing = False
        assert setup.sync().removed == 1  # asked again: now confirmed


class ShortListClient(DavClient):
    """A client whose listing leaves the task list out, while the server still has it."""

    @override
    def all_collections(self) -> list[RemoteCollection]:
        return [item for item in super().all_collections() if item.kind != "tasks"]


def test_a_collection_missing_from_the_list_but_still_on_the_server_is_kept(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        report = sync_dav(ShortListClient(server.url, "ann", "secret"), setup.store, setup.state, durable=False)
        assert report.removed == 0 and any("still there" in line for line in report.errors)
        assert setup.store.get_collection(setup.collection(CollectionKind.TASKS)).server_value("href")


def test_a_collection_deleted_on_the_server_with_changes_here_is_a_sync_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        tasks = setup.collection(CollectionKind.TASKS)
        setup.store.put(tasks, task("t2", "Made offline"))
        del server.collections["tasks"]
        report = setup.sync()
        (conflict,) = load_conflicts(setup.store)
        assert report.removed == 0 and report.conflicts == 1 and conflict.reason == "collection_deleted_on_server" and conflict.item == ""
        assert dict(conflict.tie)["href"] and setup.store.get_collection(tasks).server == ()
        assert setup.titles(CollectionKind.TASKS) == ["Made offline", "Write"]  # nothing lost
        assert setup.sync().conflicts == 0 and len(load_conflicts(setup.store)) == 1  # never doubled


def test_a_wrong_password_stops_the_synchronisation(tmp_path: Path) -> None:
    with FakeDav() as server:
        setup = Setup(tmp_path, server)
        with pytest.raises(AuthenticationError):
            sync_dav(DavClient(server.url, "ann", "wrong"), setup.store, setup.state, durable=False)


def test_the_public_folders_next_to_the_own_ones_are_kept_in_step_and_marked(tmp_path: Path) -> None:
    with FakeDav(public=True) as server:
        fill(server)
        server.add("pubevents", "p1.ics", event("p1", "Company day"))
        setup = Setup(tmp_path, server)
        report = setup.sync()
        assert report.errors == [] and report.created == 5
        names = sorted(info.name for info in setup.store.collections())
        assert names == ["Calendar", "Calendar (public)", "Contacts", "Public Contacts (public)", "Tasks"]
        public = next(info for info in setup.store.collections() if info.name == "Calendar (public)")
        found = (setup.store.index.get(item) for item in setup.store.index.item_ids(public.collection_id))
        assert [item.title for item in found if item is not None] == ["Company day"]


def test_a_folder_left_out_is_never_looked_at_and_one_made_later_arrives(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        tasks = next(item for item in DavClient(server.url, "ann", "secret").all_collections() if item.kind == "tasks")
        report = setup.sync(frozenset({folder_key(tasks)}))
        assert report.created == 2 and not setup.store.collections(CollectionKind.TASKS)
        assert not any(path.endswith("/tasks/") and method == "REPORT" for method, path in server.requests)
        server.collections["later"] = Collection("Holidays", "calendar", "VEVENT")
        assert setup.sync(frozenset({folder_key(tasks)})).created == 1
        assert sorted(info.name for info in setup.store.collections()) == ["Calendar", "Contacts", "Holidays"]


def test_changes_not_yet_on_the_server_are_known(tmp_path: Path) -> None:
    with FakeDav() as server:
        fill(server)
        setup = Setup(tmp_path, server)
        setup.sync()
        events = setup.store.get_collection(setup.collection(CollectionKind.EVENTS))
        assert not unsent_changes(setup.store, events, setup.state)
        setup.store.put(events.collection_id, event("e9", "Made here"))
        assert unsent_changes(setup.store, events, setup.state)
        setup.sync()
        assert not unsent_changes(setup.store, events, setup.state)
        setup.store.delete(events.collection_id, next(iter(setup.store.index.item_ids(events.collection_id))))
        assert unsent_changes(setup.store, events, setup.state)


def test_a_server_that_writes_the_same_address_two_ways_loses_nothing(tmp_path: Path) -> None:
    with FakeDav(like_kerio=True) as server:
        server.add("events", "e1@test.ics", event("e1", "Meeting"))
        setup = Setup(tmp_path, server)
        first = setup.sync()
        assert (first.pulled, first.errors) == (1, [])
        for number in range(2, 6):
            server.add("events", f"e{number}@test.ics", event(f"e{number}", f"Event {number}"))
        later = setup.sync()  # only what changed since the token: written with %40, and every item twice
        assert (later.pulled, later.errors) == (4, [])
        assert setup.titles(CollectionKind.EVENTS) == ["Event 2", "Event 3", "Event 4", "Event 5", "Meeting"]
        server.remove("events", "e3@test.ics")
        assert setup.sync().deleted_here == 1 and len(setup.titles(CollectionKind.EVENTS)) == 4


def many(server: FakeDav, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        server.add("events", f"m{number}.ics", event(f"m{number}", f"Event {number:03}"))


def test_a_change_report_cut_short_is_followed_to_its_end(tmp_path: Path) -> None:
    with FakeDav() as server:
        many(server, 250)
        server.cut_after = 100  # the server gives 100 at a time and says 507
        setup = Setup(tmp_path, server)
        report = setup.sync()
        assert report.errors == [] and len(setup.titles(CollectionKind.EVENTS)) == 250


class ForgetfulClient(DavClient):
    """A server that forgot the token and then lists only the first items (a cut-short listing that does not say so)."""

    def __init__(self, url: str, shown: int) -> None:
        super().__init__(url, "ann", "secret")
        self.shown: int = shown

    def changes(self, collection: RemoteCollection, token: str) -> RemoteChanges:
        if token:
            raise DavConflict("forgotten", 403)
        full = super().changes(collection, "")
        return RemoteChanges(full.changed[: self.shown], (), full.token)


class EmptyListingClient(DavClient):
    def items(self, collection: RemoteCollection) -> list[RemoteItem]:
        return []


def test_a_listing_that_lost_most_items_removes_nothing_and_is_asked_again(tmp_path: Path) -> None:
    with FakeDav() as server:
        many(server, 50)
        setup = Setup(tmp_path, server)
        setup.sync()
        server.add("events", "new.ics", event("new", "New one"))
        report = sync_dav(ForgetfulClient(server.url, 10), setup.store, setup.state, durable=False)
        assert len(setup.titles(CollectionKind.EVENTS)) >= 50 and report.deleted_here == 0
        assert any("nothing was removed here" in error for error in report.errors)
        assert setup.sync().errors == [] and len(setup.titles(CollectionKind.EVENTS)) == 51  # asked again: all of it back in step
    with FakeDav(sync_tokens=False) as server:
        many(server, 20)
        setup = Setup(tmp_path / "ctag", server)
        setup.sync()
        server.add("events", "new.ics", event("new", "New one"))
        report = sync_dav(EmptyListingClient(server.url, "ann", "secret"), setup.store, setup.state, durable=False)
        assert len(setup.titles(CollectionKind.EVENTS)) == 20 and any("nothing was removed here" in error for error in report.errors)
        assert setup.sync().errors == [] and len(setup.titles(CollectionKind.EVENTS)) == 21


class SlowItemClient(DavClient):
    """One item never comes: its GET times out."""

    def get(self, href: str) -> tuple[str, str] | None:
        if href.endswith("m7.ics"):
            raise ProtocolError("The server did not answer in time")
        return super().get(href)


def test_one_item_that_times_out_costs_only_that_item(tmp_path: Path) -> None:
    with FakeDav(multiget=False) as server:
        many(server, 30)
        setup = Setup(tmp_path, server)
        report = sync_dav(SlowItemClient(server.url, "ann", "secret"), setup.store, setup.state, durable=False)
        assert len(setup.titles(CollectionKind.EVENTS)) == 29
        assert report.errors == ["Calendar: 1 item(s) could not be fetched; they are asked for again at the next synchronisation"]
        assert setup.sync().errors == [] and len(setup.titles(CollectionKind.EVENTS)) == 30  # the token was kept back: it comes next time


def test_a_real_mass_delete_on_the_server_is_done_here_at_the_second_synchronisation(tmp_path: Path) -> None:
    with FakeDav(sync_tokens=False) as server:
        many(server, 20)
        setup = Setup(tmp_path, server)
        setup.sync()
        for number in range(15):
            server.remove("events", f"m{number}.ics")
        first = setup.sync()
        assert len(setup.titles(CollectionKind.EVENTS)) == 20 and any("nothing was removed here" in error for error in first.errors)
        second = setup.sync()  # exactly the same listing again: trusted
        assert (second.errors, second.deleted_here) == ([], 15) and len(setup.titles(CollectionKind.EVENTS)) == 5
        assert setup.sync().errors == []
    with FakeDav() as server:  # a server with tokens that forgot ours: its fresh listing counts as complete
        many(server, 20)
        setup = Setup(tmp_path / "tokens", server)
        setup.sync()
        for number in range(15):
            server.remove("events", f"m{number}.ics")
        forgetful = ForgetfulClient(server.url, 1000)
        assert sync_dav(forgetful, setup.store, setup.state, durable=False).errors != []
        assert sync_dav(forgetful, setup.store, setup.state, durable=False).deleted_here == 15 and len(setup.titles(CollectionKind.EVENTS)) == 5
