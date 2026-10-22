from pathlib import Path

import pytest

from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.session import Engine as Session
from chrono_mail.protocols.dav import DavClient
from tests.fakedav import Collection, FakeDav
from tests.test_engine_commands import call
from tests.test_engine_dav import linked, shown

# --- Version ------------------------------------------------------------
__version__ = "test_engine_conflicts.py 2.0.286"

# --- Version history ----------------------------------------------------
# v2.0.286: a calendar's name or colour changed on two computers, or a name the server refused, is a sync conflict settled as a whole or per field;
#           afterwards both computers and the server show the same (the Windows computer's F3)
# v1.10.279: Deleted here offline and changed on the server before the next sync: a sync conflict (changed on the server), as online; unchanged
#            there: deleted on the server (the owner)
# v1.10.278: On a server whose GET of a contact is 404 (Kerio): changed on both sides merged (saved here and in a sync), deleted here offline and
#            changed there a conflict, never a false "deleted on the server"
# v1.1.236: New in a server's account: on the server first; refused: nothing made; offline: here, made there with its items at the next
#           synchronisation; refused then: Keep here only moves it to the local ones, Retry sync; a colour the server keeps not (R-29)
# v1.1.235: Delete of a server's calendar: on the server first; changed there: nothing deleted; offline: here at once, there at the next
#           synchronisation, never made here again meanwhile; changed there meanwhile or refused: a sync conflict; a calendar with tasks too (R-28)
# v1.1.234: a whole task list deleted on the server with a change made offline: listed, made there again with Use Chrono Mail, deleted here with
#           Use Server data (R-25)
# v1.1.228: Retry sync of a refused change; refused for a clash
# v1.1.225: Initial: appointments, tasks and contacts on the server first; offline and back; refused; merged; sync conflicts kept and settled (D-194)


def on_server(server: FakeDav, collection: str, uid: str) -> tuple[str, str] | None:
    """(the item's name on the server, its text), found by its UID."""
    return next(((name, text) for name, (_, text) in server.collections[collection].items.items() if f"UID:{uid}" in text), None)


def add_event(engine: Engine, account: str, summary: str = "Meeting", location: str = "Room 1") -> tuple[str, str]:
    made = call(engine, "event.add", calendar=shown(engine, account).collection, summary=summary, location=location, start="2026-11-03T10:00", end="2026-11-03T11:00")
    return str(made.item), str(made.uid)


def change_on_server(server: FakeDav, uid: str, old: str, new: str) -> None:
    found = on_server(server, "events", uid)
    assert found is not None
    server.add("events", found[0], found[1].replace(old, new))


def test_an_event_a_task_and_a_contact_go_to_the_server_first(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.collections["work"] = Collection("Work", "calendar", "VEVENT")
        engine, account, told = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        assert on_server(server, "events", uid) is not None  # there before the command answered
        call(engine, "event.edit", item=item, summary="Renamed")
        assert "SUMMARY:Renamed" in (on_server(server, "events", uid) or ("", ""))[1]
        work = next(c.collection for c in call(engine, "collection.list") if c.account == account and c.name == "Work")
        moved = call(engine, "event.move", item=item, calendar=work)
        assert on_server(server, "events", uid) is None and on_server(server, "work", uid) is not None
        call(engine, "event.delete", item=moved.item)
        assert on_server(server, "work", uid) is None
        task = call(engine, "task.add", list=shown(engine, account, "tasks").collection, summary="Write")
        assert on_server(server, "tasks", str(task.uid)) is not None
        contact = call(engine, "contact.add", collection=shown(engine, account, "contacts").collection, name="Bob Lee")
        assert on_server(server, "contacts", str(contact.uid)) is not None
        assert told == [] and call(engine, "dav.sync", account=account)[0].pushed == 0  # nothing left for the sync


def test_offline_a_change_is_made_here_kept_through_a_restart_and_sent_later(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        server.writes_failing = True
        call(engine, "event.edit", item=item, summary="Offline")
        assert "SUMMARY:Meeting" in (on_server(server, "events", uid) or ("", ""))[1] and any("next synchronisation" in line for line in told)
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        (waiting,) = call(later, "sync.waiting").accounts
        assert waiting.changes == 1 and waiting.items == (item,)
        server.writes_failing = False
        assert call(later, "dav.sync", account=account)[0].pushed == 1
        assert "SUMMARY:Offline" in (on_server(server, "events", uid) or ("", ""))[1] and call(later, "sync.waiting").accounts == ()


def test_a_lasting_no_of_the_server_changes_nothing_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, _ = add_event(engine, account)
        server.writes_refused = True
        with pytest.raises(UsageError, match="did not take the change"):
            call(engine, "event.edit", item=item, summary="Never")
        assert call(engine, "event.show", item=item).summary == "Meeting"
        with pytest.raises(UsageError, match="did not take the delete"):
            call(engine, "event.delete", item=item)
        assert call(engine, "event.show", item=item).summary == "Meeting"


def test_changes_to_different_fields_meanwhile_are_merged_without_a_word(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "LOCATION:Room 1", "LOCATION:Room 2")  # on the phone
        call(engine, "event.edit", item=item, summary="Team meeting")
        text = (on_server(server, "events", uid) or ("", ""))[1]
        assert "SUMMARY:Team meeting" in text and "LOCATION:Room 2" in text
        shown_here = call(engine, "event.show", item=item)
        assert (shown_here.summary, shown_here.location) == ("Team meeting", "Room 2") and told == [] and call(engine, "conflict.list").conflicts == ()


def test_the_same_change_on_both_sides_is_no_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Same")
        call(engine, "event.edit", item=item, summary="Same")
        assert call(engine, "conflict.list").conflicts == ()


def test_a_clash_is_a_conflict_kept_once_and_settled_with_chrono_mail_s_version(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs")
        facts: list[object] = []
        engine.changed = facts.append
        call(engine, "event.edit", item=item, summary="Ours")
        assert any(getattr(fact, "kind") == "conflicts_changed" and getattr(fact, "mailbox") == account for fact in facts)  # the window follows at once
        (conflict,) = call(engine, "conflict.list").conflicts
        assert (conflict.reason, conflict.name, conflict.local_allowed) == ("clash", "Ours", True)
        assert [(f.label, f.local, f.server) for f in conflict.fields] == [("Title", "Ours", "Theirs")] and any("Sync conflicts" in line for line in told)
        assert call(engine, "event.show", item=item).summary == "Ours" and "SUMMARY:Theirs" in (on_server(server, "events", uid) or ("", ""))[1]
        call(engine, "event.edit", item=item, summary="Ours again")  # a new change here: the same conflict, brought up to date
        (again,) = call(engine, "conflict.list").conflicts
        assert again.conflict == conflict.conflict and again.fields[0].local == "Ours again"
        call(engine, "dav.sync", account=account)
        assert "SUMMARY:Theirs" in (on_server(server, "events", uid) or ("", ""))[1]  # nothing is sent while it is open
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        assert result.settled == (conflict.conflict,) and "SUMMARY:Ours again" in (on_server(server, "events", uid) or ("", ""))[1]
        assert call(engine, "conflict.list").conflicts == ()


def test_a_conflict_settled_with_the_server_s_version_and_per_field(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs")
        call(engine, "event.edit", item=item, summary="Ours")
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert call(engine, "event.show", item=item).summary == "Theirs" and call(engine, "conflict.list").conflicts == ()
        change_on_server(server, uid, "LOCATION:Room 1", "LOCATION:Their room")
        change_on_server(server, uid, "SUMMARY:Theirs", "SUMMARY:Their title")
        call(engine, "event.edit", item=item, summary="My title", location="My room")
        (mixed,) = call(engine, "conflict.list").conflicts
        keys = {f.label: f.key for f in mixed.fields}
        choices = (f"{mixed.conflict}\t{keys['Title']}\tlocal", f"{mixed.conflict}\t{keys['Location']}\tserver")
        assert call(engine, "conflict.settle", choices=choices).settled == (mixed.conflict,)
        text = (on_server(server, "events", uid) or ("", ""))[1]
        assert "SUMMARY:My title" in text and "LOCATION:Their room" in text
        assert (call(engine, "event.show", item=item).summary, call(engine, "event.show", item=item).location) == ("My title", "Their room")


def test_changed_here_and_deleted_on_the_server_is_a_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        server.writes_failing = True
        call(engine, "event.edit", item=item, summary="Changed offline")
        server.writes_failing = False
        found = on_server(server, "events", uid)
        assert found is not None
        server.remove("events", found[0])
        assert call(engine, "dav.sync", account=account)[0].conflicts == 1
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.reason == "deleted_on_server" and call(engine, "event.show", item=item).summary == "Changed offline"
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        assert "SUMMARY:Changed offline" in (on_server(server, "events", uid) or ("", ""))[1]


def test_deleted_here_and_changed_on_the_server_is_a_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Changed there")
        call(engine, "event.delete", item=item)
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.reason == "changed_on_server" and on_server(server, "events", uid) is not None
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert [e.summary for e in call(engine, "event.list", from_date="2026-11-01", to_date="2026-11-05").events] == ["Changed there"]


def test_an_offline_change_the_server_refuses_later_is_a_conflict_only_the_server_s_version_settles(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        server.writes_failing = True
        call(engine, "event.edit", item=item, summary="Offline")
        server.writes_failing, server.writes_refused = False, True
        assert call(engine, "dav.sync", account=account)[0].conflicts == 1
        (conflict,) = call(engine, "conflict.list").conflicts
        assert (conflict.reason, conflict.local_allowed) == ("refused", False) and "403" in conflict.detail
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local").open == (conflict.conflict,)
        server.writes_refused = False
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert call(engine, "event.show", item=item).summary == "Meeting" and call(engine, "conflict.list").conflicts == ()


def test_two_computers_end_up_showing_the_same(tmp_path: Path) -> None:
    with FakeDav() as server:
        first, first_account, _ = linked(tmp_path / "first", server)
        second, second_account, _ = linked(tmp_path / "second", server)
        item, _ = add_event(first, first_account, "Shared")
        call(second, "dav.sync", account=second_account)
        call(first, "event.edit", item=item, location="Room 7")
        call(second, "dav.sync", account=second_account)
        (seen,) = call(second, "event.list", from_date="2026-11-01", to_date="2026-11-05").events
        assert (seen.summary, call(second, "event.show", item=seen.item).location) == ("Shared", "Room 7")


def test_retry_sync_sends_a_refused_change_again_and_is_refused_for_a_clash(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        server.writes_failing = True
        call(engine, "event.edit", item=item, summary="Offline")
        server.writes_failing, server.writes_refused = False, True
        call(engine, "dav.sync", account=account)
        (refused,) = call(engine, "conflict.list").conflicts
        assert refused.retry_allowed
        server.writes_refused = False  # the server allows it again
        result = call(engine, "conflict.settle", conflicts=(refused.conflict,), side="retry")
        assert result.retried == (account,) and call(engine, "conflict.list").conflicts == ()
        call(engine, "dav.sync", account=account)
        assert "SUMMARY:Offline" in (on_server(server, "events", uid) or ("", ""))[1]
        change_on_server(server, uid, "SUMMARY:Offline", "SUMMARY:Theirs")
        call(engine, "event.edit", item=item, summary="Ours")
        (clash,) = call(engine, "conflict.list").conflicts
        assert not clash.retry_allowed
        assert call(engine, "conflict.settle", conflicts=(clash.conflict,), side="retry").open == (clash.conflict,)  # a clash needs a side


def deleted_with_an_offline_task(tmp_path: Path, server: FakeDav) -> tuple[Engine, str, str]:
    """A task added offline, then the task list deleted on the server: the synchronisation makes it a conflict (R-25)."""
    engine, account, _ = linked(tmp_path, server)
    server.writes_failing = True
    call(engine, "task.add", list=shown(engine, account, "tasks").collection, summary="Made offline")
    server.writes_failing = False
    del server.collections["tasks"]
    (report,) = call(engine, "dav.sync", account=account)
    assert report.conflicts == 1 and report.removed == 0
    return engine, account, shown(engine, account, "tasks").collection


def test_a_task_list_deleted_on_the_server_with_changes_here_is_listed_as_one_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = deleted_with_an_offline_task(tmp_path, server)
        (conflict,) = call(engine, "conflict.list").conflicts
        assert (conflict.kind, conflict.name, conflict.whole, conflict.local_allowed, conflict.server_allowed, conflict.retry_allowed) == (
            "tasklist", "Tasks", True, True, True, False,
        )


def test_use_chrono_mail_makes_a_deleted_task_list_again_with_everything_in_it(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, tasks = deleted_with_an_offline_task(tmp_path, server)
        (conflict,) = call(engine, "conflict.list").conflicts
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        assert result.settled == (conflict.conflict,) and result.retried == (account,) and "tasks" in server.collections
        assert server.collections["tasks"].name == "Tasks" and server.collections["tasks"].components == "VTODO"
        (report,) = call(engine, "dav.sync", account=account)
        assert report.pushed == 1 and report.errors == ()
        assert any("SUMMARY:Made offline" in text for _, text in server.collections["tasks"].items.values())
        assert shown(engine, account, "tasks").collection == tasks and call(engine, "conflict.list").conflicts == ()


def test_use_server_data_deletes_the_task_list_here_too(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = deleted_with_an_offline_task(tmp_path, server)
        (conflict,) = call(engine, "conflict.list").conflicts
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
        assert [c for c in call(engine, "collection.list") if c.account == account and c.kind == "tasks"] == []
        assert call(engine, "conflict.list").conflicts == ()


def test_use_chrono_mail_on_a_place_taken_again_keeps_the_conflict_open(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, _, _ = deleted_with_an_offline_task(tmp_path, server)
        server.collections["tasks"] = Collection("Other", "calendar", "VTODO")  # someone made one there meanwhile
        (conflict,) = call(engine, "conflict.list").conflicts
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        assert result.open == (conflict.conflict,) and "something at its place again" in result.problems[0]


def ids(engine: Engine, account: str, kind: str = "events") -> list[str]:
    return [c.collection for c in call(engine, "collection.list") if c.account == account and c.kind == kind]


def test_delete_of_a_server_calendar_deletes_it_on_the_server_first(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        assert shown(engine, account).server is True
        call(engine, "collection.delete", collection=shown(engine, account).collection)
        assert "events" not in server.collections and ids(engine, account) == []
        assert call(engine, "dav.sync", account=account)[0].created == 0 and ids(engine, account) == []  # it never comes back


def test_a_calendar_changed_on_the_server_since_the_last_sync_is_not_deleted(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.add("events", "new.ics", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:n1\r\nDTSTART:20261012T090000Z\r\nSUMMARY:Phone\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        with pytest.raises(UsageError, match="changed on the server since it was last synchronised"):
            call(engine, "collection.delete", collection=shown(engine, account).collection)
        assert "events" in server.collections and len(ids(engine, account)) == 1


def test_a_read_only_calendar_is_never_deleted(tmp_path: Path) -> None:
    with FakeDav(read_only=("events",)) as server:
        engine, account, _ = linked(tmp_path, server)
        with pytest.raises(UsageError, match="read only"):
            call(engine, "collection.delete", collection=shown(engine, account).collection)
        assert "events" in server.collections and len(ids(engine, account)) == 1


def test_offline_a_calendar_is_deleted_here_at_once_and_on_the_server_later(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        server.writes_failing = True
        call(engine, "collection.delete", collection=shown(engine, account).collection)
        assert ids(engine, account) == [] and "events" in server.collections and any("next synchronisation" in line for line in told)
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        assert call(later, "sync.waiting").accounts[0].changes == 1
        call(later, "dav.sync", account=account)  # still failing: tried again later, never made here again meanwhile
        assert ids(later, account) == [] and "events" in server.collections
        server.writes_failing = False
        assert call(later, "dav.sync", account=account)[0].errors == ()
        assert "events" not in server.collections and ids(later, account) == [] and call(later, "sync.waiting").accounts == ()


def offline_delete_then(tmp_path: Path, server: FakeDav, change: bool, refuse: bool) -> tuple[Engine, str, str]:
    engine, account, _ = linked(tmp_path, server)
    server.writes_failing = True
    call(engine, "collection.delete", collection=shown(engine, account).collection)
    server.writes_failing = False
    if change:
        server.add("events", "new.ics", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:n1\r\nDTSTART:20261012T090000Z\r\nSUMMARY:Phone\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    server.writes_refused = refuse
    (report,) = call(engine, "dav.sync", account=account)
    server.writes_refused = False
    assert report.conflicts == 1 and "events" in server.collections and ids(engine, account) == []
    (conflict,) = call(engine, "conflict.list").conflicts
    return engine, account, conflict.conflict


def test_a_calendar_deleted_offline_and_changed_on_the_server_is_a_conflict(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, conflict = offline_delete_then(tmp_path, server, change=True, refuse=False)
        (row,) = call(engine, "conflict.list").conflicts
        assert (row.kind, row.reason, row.local_allowed, row.server_allowed, row.retry_allowed) == ("calendar", "collection_changed_on_server", True, True, False)
        assert call(engine, "dav.sync", account=account)[0].conflicts == 0 and "events" in server.collections  # nothing sent while open
        result = call(engine, "conflict.settle", conflicts=(conflict,), side="local")
        assert result.settled == (conflict,) and "events" not in server.collections


def test_use_server_data_brings_a_calendar_deleted_offline_back(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, conflict = offline_delete_then(tmp_path, server, change=True, refuse=False)
        assert call(engine, "conflict.settle", conflicts=(conflict,), side="server").retried == (account,)
        call(engine, "dav.sync", account=account)
        assert len(ids(engine, account)) == 1 and call(engine, "conflict.list").conflicts == ()


def test_a_delete_the_server_refuses_is_a_conflict_that_can_be_tried_again(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, conflict = offline_delete_then(tmp_path, server, change=False, refuse=True)
        (row,) = call(engine, "conflict.list").conflicts
        assert (row.reason, row.local_allowed, row.retry_allowed) == ("collection_delete_refused", False, True)
        assert call(engine, "conflict.settle", conflicts=(conflict,), side="retry").retried == (account,)
        call(engine, "dav.sync", account=account)
        assert "events" not in server.collections and call(engine, "conflict.list").conflicts == ()


def test_a_calendar_that_holds_tasks_too_takes_its_task_list_along(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.collections["both"] = Collection("Both", "calendar", "VEVENT,VTODO")
        engine, account, _ = linked(tmp_path, server)
        events = next(c for c in call(engine, "collection.list") if c.account == account and c.name == "Both" and c.kind == "events")
        assert events.together == ("Both",)
        call(engine, "collection.delete", collection=events.collection)
        assert "both" not in server.collections
        assert [c.name for c in call(engine, "collection.list") if c.account == account and c.name == "Both"] == []


def test_a_new_collection_can_be_made_in_the_local_ones_and_in_every_account_with_a_server(tmp_path: Path) -> None:
    from chrono_mail.core import LOCAL_ACCOUNT_ID

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        assert [place.account for place in call(engine, "collection.accounts").places] == [LOCAL_ACCOUNT_ID, account]


def test_a_new_calendar_in_a_server_account_is_made_on_the_server_first(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        made = call(engine, "collection.create", kind="events", name="Work", colour="#aabbcc", account=account)
        assert made.account == account and made.server is True and made.colour == "#aabbcc"
        (key,) = [k for k, c in server.collections.items() if c.name == "Work"]
        assert server.collections[key].components == "VEVENT" and told == []
        (report,) = call(engine, "dav.sync", account=account)
        assert report.created == 0 and report.errors == () and ids(engine, account).count(made.collection) == 1 and len(ids(engine, account)) == 2
        item, uid = add_event(engine, account)  # into the account's first calendar; the new one takes items on the server first too
        moved = call(engine, "event.move", item=item, calendar=made.collection)
        assert on_server(server, key, uid) is not None and moved.item


def test_a_new_calendar_the_server_refuses_is_not_made_here_either(tmp_path: Path) -> None:
    from chrono_mail.engine import UsageError as Refused

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.writes_refused = True
        with pytest.raises(Refused, match="did not make the calendar"):
            call(engine, "collection.create", kind="events", name="Work", account=account)
        assert len(ids(engine, account)) == 1 and not any(c.name == "Work" for c in server.collections.values())


def test_a_server_that_says_yes_and_makes_no_address_book_makes_nothing_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.makes_nothing = True  # Kerio over CardDAV
        with pytest.raises(UsageError, match="made no address book"):
            call(engine, "collection.create", kind="contacts", name="Club", account=account)
        assert len(ids(engine, account, "contacts")) == 1


def test_offline_a_new_task_list_is_made_here_and_on_the_server_with_its_items_later(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        server.writes_failing = True
        made = call(engine, "collection.create", kind="tasks", name="Chores", account=account)
        assert made.server is False and any("next synchronisation" in line for line in told)
        task = call(engine, "task.add", list=made.collection, summary="Sweep")
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        (waiting,) = call(later, "sync.waiting").accounts
        assert waiting.changes == 2 and waiting.items == (task.item,)
        server.writes_failing = False
        (report,) = call(later, "dav.sync", account=account)
        assert report.made_on_server == 1 and report.pushed == 1 and report.errors == () and report.created == 0
        (key,) = [k for k, c in server.collections.items() if c.name == "Chores"]
        assert on_server(server, key, str(task.uid)) is not None and call(later, "sync.waiting").accounts == ()
        assert [c.collection for c in call(later, "collection.list") if c.name == "Chores"] == [made.collection]


def test_a_new_task_list_the_server_refuses_later_can_be_kept_here_only(tmp_path: Path) -> None:
    from chrono_mail.core import LOCAL_ACCOUNT_ID

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.writes_failing = True
        made = call(engine, "collection.create", kind="tasks", name="Chores", account=account)
        call(engine, "task.add", list=made.collection, summary="Sweep")
        server.writes_failing, server.writes_refused = False, True
        assert call(engine, "dav.sync", account=account)[0].conflicts == 1
        (row,) = call(engine, "conflict.list").conflicts
        assert (row.kind, row.reason, row.local_allowed, row.server_allowed, row.keep_allowed, row.retry_allowed) == (
            "tasklist", "collection_create_refused", False, False, True, True,
        )
        refused = call(engine, "conflict.settle", conflicts=(row.conflict,), side="server")  # never offered: it would lose it
        assert refused.open == (row.conflict,) and "would lose it" in refused.problems[0]
        assert call(engine, "conflict.settle", conflicts=(row.conflict,), side="keep").settled == (row.conflict,)
        local = [c for c in call(engine, "collection.list") if c.account == LOCAL_ACCOUNT_ID and c.name == "Chores"]
        assert len(local) == 1 and [t.summary for t in call(engine, "task.list", list=local[0].collection).tasks] == ["Sweep"]
        assert [c for c in call(engine, "collection.list") if c.account == account and c.name == "Chores"] == []
        assert call(engine, "conflict.list").conflicts == () and call(engine, "sync.waiting").accounts == ()


def test_retry_sync_makes_a_refused_task_list_once_the_server_takes_it(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.writes_failing = True
        made = call(engine, "collection.create", kind="tasks", name="Chores", account=account)
        server.writes_failing, server.writes_refused = False, True
        call(engine, "dav.sync", account=account)
        server.writes_refused = False
        (row,) = call(engine, "conflict.list").conflicts
        assert call(engine, "conflict.settle", conflicts=(row.conflict,), side="retry").retried == (account,)
        assert call(engine, "dav.sync", account=account)[0].made_on_server == 1
        assert any(c.name == "Chores" for c in server.collections.values()) and shown_named(engine, "Chores").server is True
        assert made.collection == shown_named(engine, "Chores").collection


def shown_named(engine: Engine, name: str) -> object:
    return next(c for c in call(engine, "collection.list") if c.name == name)


def test_a_colour_the_server_does_not_keep_is_kept_here(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.keeps_colour = False
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "collection.create", kind="events", name="Work", colour="#aabbcc", account=account)
        call(engine, "dav.sync", account=account)
        assert shown_named(engine, "Work").colour == "#aabbcc" and made.server is True


# --- a server whose GET of an existing contact answers 404 (Kerio, checked 2026-10-22) ------------------------------------------------------


def kerio_contact(tmp_path: Path, server: FakeDav) -> tuple[Engine, str, str, str]:
    """A contact kept on the server, changed there by another program afterwards (its company); GET of a contact answers 404 from then on."""
    engine, account, _ = linked(tmp_path, server)
    made = call(engine, "contact.add", collection=shown(engine, account, "contacts").collection, name="Bob Lee", organization="Acme")
    server.no_contact_by_get = True
    found = on_server(server, "contacts", str(made.uid))
    assert found is not None
    server.add("contacts", found[0], found[1].replace("ORG:Acme", "ORG:Acme Two"))  # on the phone
    return engine, account, str(made.item), str(made.uid)


def test_a_contact_changed_on_both_sides_on_kerio_is_merged_and_never_called_deleted(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, _, item, uid = kerio_contact(tmp_path, server)
        call(engine, "contact.edit", item=item, title="Boss")  # here, on the server first: it says "changed meanwhile"
        text = (on_server(server, "contacts", uid) or ("", ""))[1]
        assert "TITLE:Boss" in text and "ORG:Acme Two" in text and call(engine, "conflict.list").conflicts == ()  # was: deleted on the server


def test_a_contact_changed_offline_and_on_kerio_is_merged_by_the_sync(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _, uid = kerio_contact(tmp_path, server)
        found = on_server(server, "contacts", uid)
        assert found is not None
        item = next(row.item for row in call(engine, "contact.list").contacts if row.name == "Bob Lee")
        server.writes_failing = True
        call(engine, "contact.edit", item=item, title="Boss")
        server.writes_failing = False
        call(engine, "dav.sync", account=account)
        text = (on_server(server, "contacts", uid) or ("", ""))[1]
        assert "TITLE:Boss" in text and "ORG:Acme Two" in text and call(engine, "conflict.list").conflicts == ()


def test_a_contact_deleted_offline_and_changed_on_kerio_just_before_the_sync_deletes_it_is_a_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The phone changes it after the sync read the server's changes and before the sync sends the delete: the server refuses the delete (412)."""
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "contact.add", collection=shown(engine, account, "contacts").collection, name="Bob Lee", organization="Acme")
        server.no_contact_by_get = True
        server.writes_failing = True
        call(engine, "contact.delete", item=made.item)
        server.writes_failing = False
        real_delete = DavClient.delete

        def changed_first(client: DavClient, href: str, etag: str = "") -> None:
            found = on_server(server, "contacts", str(made.uid))
            if found is not None and "ORG:Acme Two" not in found[1]:
                server.add("contacts", found[0], found[1].replace("ORG:Acme", "ORG:Acme Two"))  # on the phone, at that moment
            real_delete(client, href, etag)

        monkeypatch.setattr(DavClient, "delete", changed_first)
        call(engine, "dav.sync", account=account)
        (conflict,) = call(engine, "conflict.list").conflicts  # was: no conflict, the phone's change never shown
        assert conflict.reason == "changed_on_server" and conflict.name == "Bob Lee" and on_server(server, "contacts", str(made.uid)) is not None


# --- deleted here offline, changed on the server before the next sync (the owner: a sync conflict, as online) -------------------------------


def deleted_offline_changed_there(tmp_path: Path, server: FakeDav, kind: str) -> tuple[Engine, str, str]:
    """An item of this kind deleted here while the server could not be reached, then changed on the server (another program). Returns the engine,
    the account and the item's uid."""
    engine, account, _ = linked(tmp_path, server)
    collection = shown(engine, account, kind).collection
    if kind == "events":
        item, uid = add_event(engine, account)
        old, new, command = "SUMMARY:Meeting", "SUMMARY:Changed there", "event.delete"
    elif kind == "tasks":
        made = call(engine, "task.add", list=collection, summary="Write")
        item, uid, old, new, command = str(made.item), str(made.uid), "SUMMARY:Write", "SUMMARY:Changed there", "task.delete"
    else:
        made = call(engine, "contact.add", collection=collection, name="Bob Lee", organization="Acme")
        item, uid, old, new, command = str(made.item), str(made.uid), "ORG:Acme", "ORG:Changed there", "contact.delete"
    server.writes_failing = True
    call(engine, command, item=item)
    server.writes_failing = False
    found = on_server(server, kind, uid)
    assert found is not None
    server.add(kind, found[0], found[1].replace(old, new))
    return engine, account, uid


@pytest.mark.parametrize("kind", ["events", "tasks", "contacts"])
def test_deleted_offline_and_changed_on_the_server_is_a_conflict_never_brought_back_quietly(tmp_path: Path, kind: str) -> None:
    with FakeDav() as server:
        engine, account, uid = deleted_offline_changed_there(tmp_path, server, kind)
        assert call(engine, "dav.sync", account=account)[0].conflicts == 1
        (conflict,) = call(engine, "conflict.list").conflicts  # was: none, and the item was back here
        assert conflict.reason == "changed_on_server" and on_server(server, kind, uid) is not None
        listed = {"events": lambda: call(engine, "event.list", from_date="2026-11-01", to_date="2026-11-05").events,
                  "tasks": lambda: call(engine, "task.list").tasks, "contacts": lambda: call(engine, "contact.list").contacts}[kind]
        assert list(listed()) == []  # not made here again
        call(engine, "dav.sync", account=account)
        assert len(call(engine, "conflict.list").conflicts) == 1 and on_server(server, kind, uid) is not None  # one row; no delete sent


def test_a_conflict_of_a_delete_made_offline_is_settled_either_way(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, uid = deleted_offline_changed_there(tmp_path / "server", server, "contacts")
        call(engine, "dav.sync", account=account)
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert [row.name for row in call(engine, "contact.list").contacts] == ["Bob Lee"] and call(engine, "conflict.list").conflicts == ()
    with FakeDav() as server:
        engine, account, uid = deleted_offline_changed_there(tmp_path / "local", server, "contacts")
        call(engine, "dav.sync", account=account)
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        assert on_server(server, "contacts", uid) is None and call(engine, "contact.list").contacts == () and call(engine, "conflict.list").conflicts == ()


def test_deleted_offline_and_unchanged_on_the_server_is_deleted_there_at_the_next_sync(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "contact.add", collection=shown(engine, account, "contacts").collection, name="Bob Lee")
        server.writes_failing = True
        call(engine, "contact.delete", item=made.item)
        server.writes_failing = False
        assert call(engine, "dav.sync", account=account)[0].deleted_on_server == 1
        assert on_server(server, "contacts", str(made.uid)) is None and call(engine, "conflict.list").conflicts == ()


# --- a collection's name and colour (D-192, the Windows computer's F3) -----------------------------------------------------------------------


def renamed_on_two_computers(tmp_path: Path, server: FakeDav) -> tuple[Engine, str, Engine, str]:
    """Computer A renames the calendar while the server cannot be reached; computer B renames it online meanwhile; A synchronises."""
    first, first_account, _ = linked(tmp_path / "a", server)
    second, second_account, _ = linked(tmp_path / "b", server)
    server.failing = True
    call(first, "collection.edit", collection=shown(first, first_account).collection, name="A's name")
    server.failing = False
    call(second, "collection.edit", collection=shown(second, second_account).collection, name="B's name")
    return first, first_account, second, second_account


def test_a_name_changed_on_two_computers_is_one_conflict_and_nothing_is_sent_while_it_is_open(tmp_path: Path) -> None:
    with FakeDav() as server:
        first, account, _, _ = renamed_on_two_computers(tmp_path, server)
        sent = server.count("PROPPATCH")
        for _ in range(3):
            (result,) = call(first, "dav.sync", account=account)
            assert result.errors == ()  # no line at every synchronisation any more
        (conflict,) = call(first, "conflict.list").conflicts
        assert (conflict.reason, conflict.kind, conflict.name, conflict.whole, conflict.local_allowed, conflict.retry_allowed) == (
            "collection_look_clash", "calendar", "A's name", False, True, False,
        )
        assert [(f.label, f.local, f.server) for f in conflict.fields] == [("Name", "A's name", "B's name")]
        assert server.count("PROPPATCH") == sent and server.collections["events"].name == "B's name" and shown(first, account).name == "A's name"
        assert call(first, "conflict.settle", conflicts=(conflict.conflict,), side="retry").open == (conflict.conflict,)  # a clash needs a side


@pytest.mark.parametrize("side", ["local", "server"])
def test_a_name_conflict_settled_leaves_both_computers_and_the_server_the_same(tmp_path: Path, side: str) -> None:
    with FakeDav() as server:
        first, first_account, second, second_account = renamed_on_two_computers(tmp_path, server)
        call(first, "dav.sync", account=first_account)
        (conflict,) = call(first, "conflict.list").conflicts
        result = call(first, "conflict.settle", conflicts=(conflict.conflict,), side=side)
        assert result.settled == (conflict.conflict,) and result.retried == (first_account,)
        if side == "server":
            assert shown(first, first_account).name == "B's name"  # here at once
        call(first, "dav.sync", account=first_account)
        call(second, "dav.sync", account=second_account)
        expected = "A's name" if side == "local" else "B's name"
        assert server.collections["events"].name == shown(first, first_account).name == shown(second, second_account).name == expected
        assert call(first, "conflict.list").conflicts == () and first.config().find(first_account).collection_looks == ()


def test_a_name_and_colour_conflict_is_settled_per_field(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.failing = True
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Mine", colour="#111111")
        server.failing = False
        server.collections["events"].name, server.collections["events"].colour = "Theirs", "#222222FF"
        call(engine, "dav.sync", account=account)
        (conflict,) = call(engine, "conflict.list").conflicts
        assert [(f.key, f.local, f.server) for f in conflict.fields] == [("name", "Mine", "Theirs"), ("colour", "#111111", "#222222")]
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), choices=(f"{conflict.conflict}\tname\tlocal", f"{conflict.conflict}\tcolour\tserver"))
        call(engine, "dav.sync", account=account)
        assert (server.collections["events"].name, shown(engine, account).name, shown(engine, account).colour) == ("Mine", "Mine", "#222222")
        assert call(engine, "conflict.list").conflicts == ()


def test_a_name_conflict_goes_by_itself_when_the_server_takes_the_same_name(tmp_path: Path) -> None:
    with FakeDav() as server:
        first, account, _, _ = renamed_on_two_computers(tmp_path, server)
        call(first, "dav.sync", account=account)
        assert len(call(first, "conflict.list").conflicts) == 1
        server.collections["events"].name = "A's name"  # B named it the same in the end
        call(first, "dav.sync", account=account)
        assert call(first, "conflict.list").conflicts == () and first.config().find(account).collection_looks == ()


@pytest.mark.parametrize("when", ["online", "offline"])
def test_a_name_the_server_refuses_is_a_conflict_only_the_server_s_name_or_retry_settles(tmp_path: Path, when: str) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.refuses_names = True
        server.failing = when == "offline"
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Home")
        server.failing = False
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        (conflict,) = call(engine, "conflict.list").conflicts
        assert (conflict.reason, conflict.local_allowed, conflict.retry_allowed) == ("collection_look_refused", False, True)
        assert [(f.local, f.server) for f in conflict.fields] == [("Home", "Calendar")]
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local").open == (conflict.conflict,)
        server.refuses_names = False  # the server allows it now
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="retry")
        call(engine, "dav.sync", account=account)
        assert server.collections["events"].name == "Home" and call(engine, "conflict.list").conflicts == ()


def test_use_server_data_gives_a_refused_name_up(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        server.refuses_names = True
        call(engine, "collection.edit", collection=shown(engine, account).collection, name="Home")
        call(engine, "dav.sync", account=account)
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert shown(engine, account).name == "Calendar"
        call(engine, "dav.sync", account=account)
        assert call(engine, "conflict.list").conflicts == () and engine.config().find(account).collection_looks == ()
