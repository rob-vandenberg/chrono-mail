from pathlib import Path

import pytest

from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.session import Engine as Session
from chrono_mail.protocols.dav import DavClient
from tests.fakedav import Collection, FakeDav
from tests.test_engine_commands import call
from tests.test_engine_dav import linked, shown

# --- Version ------------------------------------------------------------
__version__ = "test_engine_conflicts.py 4.3.498"

# --- Version history ----------------------------------------------------
# v4.3.498: a contact changed on a server that ignores If-Match (Kerio) is never written over, and waits when its version cannot be read;
#           settled another way after an interrupted Keep both, its copy goes on the server first (offline later) and out of its group,
#           unless the person changed it (also on another computer during a write not finished) or was told it is made;
#           Keep both (B-30, B-31, the owner): a contact, a repeating appointment and a task kept as two, the copy made on the server first,
#           a group that cannot be stored stays as it was and the person is told; interrupted at any step, a second try finishes the one
#           copy, in the groups; offline without the server's version tag;
#           in every group of the original, without invitations; offline sent later; refused: nothing changes; a field's entries kept both
# v4.2.496: Use Chrono Mail keeps what the server changed that is not shown, Use Server data sends nothing then; a field key with a
#           tab is read whole (the owner)
# v4.2.483: changes on both sides, also to different fields, are a conflict the person settles (the owner)
# v4.2.481: conflict.recheck at start settles a conflict without a difference a person sees, a real one stays (D-308)
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


def test_changes_to_different_fields_meanwhile_are_a_conflict_the_person_settles_field_by_field(tmp_path: Path) -> None:  # the owner, 4.2.483
    with FakeDav() as server:
        engine, account, told = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "LOCATION:Room 1", "LOCATION:Room 2")  # on the phone
        call(engine, "event.edit", item=item, summary="Team meeting")
        (conflict,) = call(engine, "conflict.list").conflicts
        assert [field.label for field in conflict.fields] == ["Title", "Location"]  # changed on both sides: every difference is a choice
        keys = {field.label: field.key for field in conflict.fields}
        call(engine, "conflict.settle", choices=(f"{conflict.conflict}\t{keys['Title']}\tlocal", f"{conflict.conflict}\t{keys['Location']}\tserver"))
        text = (on_server(server, "events", uid) or ("", ""))[1]
        assert "SUMMARY:Team meeting" in text and "LOCATION:Room 2" in text and call(engine, "conflict.list").conflicts == ()


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
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.reason == "clash"  # changed on both sides (the owner, 4.2.483); was: deleted on the server


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
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.reason == "clash" and found[1] in (on_server(server, "contacts", uid) or ("", ""))[1]  # nothing sent before the choice


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


def test_at_start_a_conflict_without_a_difference_a_person_sees_is_settled_and_a_real_one_stays(tmp_path: Path) -> None:
    from dataclasses import replace

    from chrono_mail.engine.pimaccess import open_pim
    from chrono_mail.sync.davconflicts import load_conflicts, record

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs")
        call(engine, "event.edit", item=item, summary="Ours")
        assert call(engine, "conflict.recheck").settled == 0 and len(call(engine, "conflict.list").conflicts) == 1  # a real one stays
        with open_pim(engine, account) as store:  # as kept before D-308: the server's version differs only in what is not shown
            (conflict,) = load_conflicts(store)
            hidden = conflict.server_text.replace("SUMMARY:Theirs", "SUMMARY:Ours").replace("SUMMARY:Ours", "SUMMARY:Ours\r\nURL:https://example.com/x", 1)
            record(store, replace(conflict, server_text=hidden))
        assert call(engine, "conflict.recheck").settled == 1 and call(engine, "conflict.list").conflicts == ()
        with open_pim(engine, account) as store:
            text = store.get(conflict.collection, item)
        assert "URL:https://example.com/x" in text and "SUMMARY:Ours" in text  # the server's for what is not seen, Chrono Mail's own kept


def test_use_chrono_mail_keeps_what_the_server_changed_that_is_not_shown(tmp_path: Path) -> None:  # D-308
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs\r\nX-OTHER-PROGRAM:2")
        call(engine, "event.edit", item=item, summary="Ours")
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="local")
        text = (on_server(server, "events", uid) or ("", ""))[1]
        assert "SUMMARY:Ours" in text and "X-OTHER-PROGRAM:2" in text  # never Chrono Mail's whole text over it


def test_a_field_key_with_a_tab_is_read_whole(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs")
        call(engine, "event.edit", item=item, summary="Ours")
        (conflict,) = call(engine, "conflict.list").conflicts
        result = call(engine, "conflict.settle", choices=(f"{conflict.conflict}\tNICKNAME\x1fan\tnie\tlocal",))  # a list entry's value with a tab
        assert result.open == (conflict.conflict,)  # no such field here: still open, never "A choice is conflict<TAB>…"


def test_use_server_data_sends_nothing_when_the_server_s_version_is_what_comes_out(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid = add_event(engine, account)
        change_on_server(server, uid, "SUMMARY:Meeting", "SUMMARY:Theirs\r\nSEQUENCE:3")
        before = on_server(server, "events", uid)
        call(engine, "event.edit", item=item, summary="Ours")
        (conflict,) = call(engine, "conflict.list").conflicts
        call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server")
        assert on_server(server, "events", uid) == before and call(engine, "event.show", item=item).summary == "Theirs"  # nothing sent back


def collection_of(engine: Engine, account: str, kind: str) -> str:
    """The id of the account's collection of this kind (events, tasks, contacts)."""
    return str(next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == kind))


def contact_clash(engine: Engine, server: FakeDav, account: str) -> tuple[str, str, str]:
    """A contact in the group Family whose e-mail address was changed here and on the server: a conflict. Returns (item, uid, the group)."""
    book = collection_of(engine, account, "contacts")
    made = call(engine, "contact.add", collection=book, name="Anna de Vries", email=("work|anna@old.example",), birthday="1980-04-12")
    group = call(engine, "contact-group.create", collection=book, name="Family", contacts=(made.item,)).item
    found = on_server(server, "contacts", str(made.uid))
    assert found is not None
    server.add("contacts", found[0], found[1].replace("anna@old.example", "anna@there.example"))  # on the phone
    call(engine, "contact.edit", item=made.item, email=("work|anna@here.example",))
    return str(made.item), str(made.uid), str(group)


def cards_on_server(server: FakeDav) -> list[str]:
    return [text.replace("\r\n ", "") for _, (_, text) in server.collections["contacts"].items.items()]


def test_keep_both_makes_chrono_mail_s_version_a_contact_of_its_own_in_every_group_of_the_original(tmp_path: Path) -> None:  # B-30
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.both_allowed
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.settled == (conflict.conflict,) and result.problems == () and call(engine, "conflict.list").conflicts == ()
        original = next(text for text in cards_on_server(server) if f"UID:{uid}" in text)
        assert "anna@there.example" in original and "anna@here.example" not in original  # the server's place keeps the server's version
        (copy,) = [text for text in cards_on_server(server) if "anna@here.example" in text]
        copy_uid = next(line[4:] for line in copy.split("\r\n") if line.startswith("UID:"))
        assert copy_uid != uid and "BDAY" in copy  # Chrono Mail's version, whole, with a uid of its own
        assert call(engine, "contact.show", item=item).emails[0].value == "anna@there.example"  # here too
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert uid.removeprefix("urn:uuid:") in family and copy_uid.removeprefix("urn:uuid:") in family  # both in the group (the owner)
        (row,) = call(engine, "contact-group.list")
        assert row.members == 2
        assert call(engine, "dav.sync", account=account)[0].pushed == 0  # nothing left for the sync


def test_keep_both_of_a_repeating_appointment_copies_the_whole_series_without_invitations(tmp_path: Path) -> None:  # B-30, Q22
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "event.add", calendar=collection_of(engine, account, "events"), summary="Weekly", start="2026-11-03T10:00",
                    end="2026-11-03T11:00", rrule="FREQ=WEEKLY", attendee=("Bob <bob@example.com>",), organizer="ann@example.com")
        uid = str(made.uid)
        change_on_server(server, uid, "SUMMARY:Weekly", "SUMMARY:Weekly there")
        call(engine, "event.edit", item=made.item, summary="Weekly here")
        (conflict,) = call(engine, "conflict.list").conflicts
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both").settled == (conflict.conflict,)
        texts = [text.replace("\r\n ", "") for _, (_, text) in server.collections["events"].items.items()]
        assert len(texts) == 2 and any(f"UID:{uid}" in text and "SUMMARY:Weekly there" in text for text in texts)
        (copy,) = [text for text in texts if "SUMMARY:Weekly here" in text]
        assert f"UID:{uid}" not in copy and "RRULE:FREQ=WEEKLY" in copy  # the whole series, as its own item
        assert all("SCHEDULE-AGENT=CLIENT" in line for line in copy.split("\r\n") if line.startswith(("ATTENDEE", "ORGANIZER")))
        assert "bob@example.com" in copy  # the same attendees, never invited again (the owner)


def test_keep_both_offline_is_done_here_kept_through_a_restart_and_sent_later(tmp_path: Path) -> None:  # D-193
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        task = call(engine, "task.add", list=collection_of(engine, account, "tasks"), summary="Write")
        found = on_server(server, "tasks", str(task.uid))
        assert found is not None
        server.add("tasks", found[0], found[1].replace("SUMMARY:Write", "SUMMARY:Write there"))
        call(engine, "task.edit", item=task.item, summary="Write here")
        (conflict,) = call(engine, "conflict.list").conflicts
        server.writes_failing = True
        facts: list[object] = []
        engine.changed = facts.append
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both").settled == (conflict.conflict,)
        assert any(getattr(fact, "kind") == "waiting_changed" for fact in facts)
        assert sorted(row.summary for row in call(engine, "task.list").tasks) == ["Write here", "Write there"]  # here at once
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        (waiting,) = call(later, "sync.waiting").accounts
        assert waiting.changes == 1
        server.writes_failing = False
        assert call(later, "dav.sync", account=account)[0].pushed == 1
        texts = [text for _, (_, text) in server.collections["tasks"].items.items()]
        assert sorted("Write here" in text for text in texts) == [False, True] and call(later, "sync.waiting").accounts == ()


def test_keep_both_refused_by_the_server_changes_nothing(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, _, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        server.writes_refused = True
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.open == (conflict.conflict,) and "did not take the copy" in result.problems[0]
        assert call(engine, "contact.show", item=item).emails[0].value == "anna@here.example" and len(call(engine, "contact.list").contacts) == 1


def test_a_field_s_entries_kept_both_and_a_field_of_one_value_never(tmp_path: Path) -> None:  # B-31
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        keys = [field.key for field in conflict.fields]
        assert all(key.startswith("EMAIL") for key in keys) and len(keys) == 2
        assert call(engine, "conflict.settle", choices=tuple(f"{conflict.conflict}\t{key}\tboth" for key in keys)).settled == (conflict.conflict,)
        card = next(text for text in cards_on_server(server) if f"UID:{uid}" in text)
        assert "anna@here.example" in card and "anna@there.example" in card
        assert sorted(entry.value for entry in call(engine, "contact.show", item=item).emails) == ["anna@here.example", "anna@there.example"]
        event, event_uid = add_event(engine, account)
        change_on_server(server, event_uid, "SUMMARY:Meeting", "SUMMARY:Theirs")
        call(engine, "event.edit", item=event, summary="Ours")
        (title,) = call(engine, "conflict.list").conflicts
        assert not call(engine, "conflict.settle", choices=(f"{title.conflict}\tSUMMARY\tboth",)).settled  # one title only: stays open


def test_keep_both_with_a_group_that_cannot_be_stored_keeps_both_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import CollectionInfo, StorageError
    from chrono_mail.sync.davitems import ItemGateway, Outcome

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        save = ItemGateway.save

        def failing(self: ItemGateway, info: CollectionInfo, text: str, item: str | None = None) -> Outcome:
            if "Family" in text:  # the group: its file here cannot be written
                raise StorageError("the disk is full")
            return save(self, info, text, item)

        monkeypatch.setattr(ItemGateway, "save", failing)
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.settled == (conflict.conflict,) and "every group" in result.problems[0]  # never an error that stops the command
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert len([text for text in cards_on_server(server) if "anna@" in text]) == 2 and family.count("urn:uuid:") == 1  # as it was


class Interrupted(Exception):
    """The program stopped at this point (a crash, the power gone)."""


STEPS: tuple[str, ...] = ("copy sent", "copy kept here", "groups", "original kept here", "conflict forgotten")


def kept_both_once(engine: Engine, server: FakeDav, account: str, uid: str, address: str) -> None:
    """Keep both came out as it should: one copy, with Chrono Mail's version (this address), in the group; the original the server's; no
    conflict; nothing waits, and a further synchronisation makes nothing new."""
    for _ in range(2):
        cards = [text for text in cards_on_server(server) if "anna@" in text]
        assert len(cards) == 2 and sum(address in text for text in cards) == 1 and any(f"UID:{uid}" in text and "anna@there.example" in text for text in cards)
        copy_uid = next(line[4:] for text in cards if address in text for line in text.split("\r\n") if line.startswith("UID:"))
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert uid.removeprefix("urn:uuid:") in family and copy_uid.removeprefix("urn:uuid:") in family  # the copy is in the group
        assert call(engine, "conflict.list").conflicts == () and len(call(engine, "contact.list").contacts) == 2
        assert call(engine, "sync.waiting").accounts == ()
        assert call(engine, "dav.sync", account=account)[0].errors == ()


def changed_on_the_phone(server: FakeDav, uid: str) -> None:
    """Another computer changes the original on the server: a note added."""
    found = next((name, text) for name, (_, text) in server.collections["contacts"].items.items() if f"UID:{uid}" in text)
    server.add("contacts", found[0], found[1].replace("END:VCARD", "NOTE:From the phone\r\nEND:VCARD"))


@pytest.mark.parametrize("between", ["nothing", "the check at start and a synchronisation", "the server changes the original"])
@pytest.mark.parametrize("step", STEPS)
def test_keep_both_interrupted_at_any_step_and_tried_again_makes_one_copy_in_the_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str, between: str,
) -> None:  # AGENTS.md 4.1.4
    from chrono_mail.pimstore import PimStore
    from chrono_mail.sync import davitems

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stopped: list[str] = []
        put, send, forget = PimStore.put, DavClient.put, davitems.forget

        def stop(here: str) -> None:
            if here == step and not stopped:
                stopped.append(step)
                raise Interrupted(step)

        def storing(self: PimStore, collection: str, text: str, item: str | None = None, verify: bool = True) -> str:
            stop("copy kept here" if "anna@here.example" in text else "original kept here" if "anna@there" in text and "Family" not in text else "")
            return put(self, collection, text, item, verify)

        def sending(self: DavClient, href: str, text: str, kind: str, etag: str = "", create: bool = False) -> str:
            stop("copy sent" if "anna@here.example" in text else "groups" if "Family" in text else "")  # before the server has it
            return send(self, href, text, kind, etag, create)

        def forgetting(store: PimStore, conflict_id: str) -> None:
            stop("conflict forgotten")
            forget(store, conflict_id)

        monkeypatch.setattr(PimStore, "put", storing)
        monkeypatch.setattr(DavClient, "put", sending)
        monkeypatch.setattr(davitems, "forget", forgetting)
        with pytest.raises(Interrupted):
            call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert stopped == [step]
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        if between == "the server changes the original":
            changed_on_the_phone(server, uid)
        if between != "nothing":
            call(later, "conflict.recheck")
            call(later, "dav.sync", account=account)
        if call(later, "conflict.list").conflicts:  # still open: the person chooses Keep both again
            result = call(later, "conflict.settle", conflicts=(conflict.conflict,), side="both")
            assert result.settled == (conflict.conflict,) and result.problems == ()
        kept_both_once(later, server, account, uid, "anna@here.example")
        if between == "the server changes the original":  # the other computer's change stays, there and here
            assert any("From the phone" in text and f"UID:{uid}" in text for text in cards_on_server(server))
            assert any(row.note == "From the phone" for row in (call(later, "contact.show", item=c.item) for c in call(later, "contact.list").contacts))


@pytest.mark.parametrize("after", ["a refusal", "an interruption after the copy was sent"])
def test_keep_both_tried_again_after_an_edit_copies_the_newest_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: str) -> None:
    from chrono_mail.pimstore import PimStore

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        if after == "a refusal":
            server.writes_refused = True
            assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both").open == (conflict.conflict,)
            server.writes_refused = False
        else:
            put = PimStore.put

            def storing(self: PimStore, collection: str, text: str, item_id: str | None = None, verify: bool = True) -> str:
                if "anna@there.example" in text and "Family" not in text:  # the original about to take the server's version
                    raise Interrupted("stopped")
                return put(self, collection, text, item_id, verify)

            monkeypatch.setattr(PimStore, "put", storing)
            with pytest.raises(Interrupted):
                call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
            monkeypatch.undo()
        call(engine, "contact.edit", item=item, email=("work|anna@newest.example",))  # the person changes it before trying again
        (still,) = call(engine, "conflict.list").conflicts
        assert call(engine, "conflict.settle", conflicts=(still.conflict,), side="both").settled == (still.conflict,)
        kept_both_once(engine, server, account, uid, "anna@newest.example")  # the newest version is the copy: nothing of it lost


def test_keep_both_offline_without_the_server_s_version_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses
    from collections.abc import Callable

    from chrono_mail.core import CollectionInfo, ProtocolError
    from chrono_mail.sync.davconflicts import SyncConflict
    from chrono_mail.sync.davitems import ItemGateway, Outcome

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        keep_both = ItemGateway.keep_both

        def untagged(self: ItemGateway, info: CollectionInfo, found: SyncConflict, joined: Callable[[str, str], None] | None = None) -> Outcome:
            return keep_both(self, info, dataclasses.replace(found, server_etag=""), joined)  # recorded without the server's version tag

        def offline(self: DavClient, href: str) -> str:
            raise ProtocolError("the server cannot be reached")

        monkeypatch.setattr(ItemGateway, "keep_both", untagged)
        monkeypatch.setattr(DavClient, "etag_of", offline)
        server.writes_failing = True
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.settled == (conflict.conflict,) and result.problems == ()  # done here, sent later
        monkeypatch.undo()
        server.writes_failing = False
        call(engine, "dav.sync", account=account)
        assert sorted("anna@here.example" in text for text in cards_on_server(server) if "anna@" in text) == [False, True]
        assert call(engine, "conflict.list").conflicts == () and call(engine, "sync.waiting").accounts == ()


def test_keep_both_tried_again_after_a_sync_recorded_the_conflict_again_makes_one_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.pimstore import PimStore

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        put = PimStore.put

        def storing(self: PimStore, collection: str, text: str, item_id: str | None = None, verify: bool = True) -> str:
            if "anna@there.example" in text and "Family" not in text:  # stopped before the original takes the server's version
                raise Interrupted("stopped")
            return put(self, collection, text, item_id, verify)

        monkeypatch.setattr(PimStore, "put", storing)
        with pytest.raises(Interrupted):
            call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        monkeypatch.undo()
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)
        call(later, "conflict.recheck")
        changed_on_the_phone(server, uid)  # another computer changes the original; the sync records the conflict again
        call(later, "dav.sync", account=account)
        (again,) = call(later, "conflict.list").conflicts
        assert call(later, "conflict.settle", conflicts=(again.conflict,), side="both").settled == (again.conflict,)
        kept_both_once(later, server, account, uid, "anna@here.example")  # one copy, never a second
        assert any("From the phone" in text and f"UID:{uid}" in text for text in cards_on_server(server))


@pytest.mark.parametrize("failing", ["the state", "the agreed version"])
def test_keep_both_never_loses_chrono_mail_s_version_when_the_original_changes_after_it_took_the_server_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str,
) -> None:
    from chrono_mail.core import StorageError
    from chrono_mail.pimstore import PimStore
    from chrono_mail.sync import davitems
    from chrono_mail.sync.davsync import DavState, digest_of

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        save, drop = DavState.save, davitems.drop_base
        failed: list[bool] = []

        def saving(self: DavState) -> None:
            taken = any(link.digest == digest_of(conflict.server_text) for state in self.collections.values() for link in state.links.values())
            if failing == "the state" and taken and not failed:  # the original took the server's version; then the disk refuses to keep that
                failed.append(True)
                raise StorageError("the disk is full")
            save(self)

        def dropping(store: PimStore, collection: str, item: str) -> None:
            if failing == "the agreed version" and item == conflict.item and not failed:
                failed.append(True)
                raise StorageError("the disk is full")
            drop(store, collection, item)

        monkeypatch.setattr(DavState, "save", saving)
        monkeypatch.setattr(davitems, "drop_base", dropping)
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both").open == (conflict.conflict,) and failed
        monkeypatch.undo()
        changed_on_the_phone(server, uid)
        call(engine, "dav.sync", account=account)
        call(engine, "conflict.settle", conflicts=tuple(c.conflict for c in call(engine, "conflict.list").conflicts), side="both")
        cards = [text for text in cards_on_server(server) if "anna@" in text]
        assert len(cards) == 2 and sum("anna@here.example" in text for text in cards) == 1  # one copy: Chrono Mail's version, never lost
        assert call(engine, "conflict.list").conflicts == ()
        assert any("From the phone" in text and f"UID:{uid}" in text for text in cards)  # nor the other computer's change
        assert sum(entry.value == "anna@here.example" for c in call(engine, "contact.list").contacts
                   for entry in call(engine, "contact.show", item=c.item).emails) == 1


def stop_keep_both(engine: Engine, conflict: str, monkeypatch: pytest.MonkeyPatch, before: str) -> None:
    """Keep both, stopped before the groups reach the server ("groups") or before the original takes the server's version ("original")."""
    from chrono_mail.pimstore import PimStore

    put, send = PimStore.put, DavClient.put

    def storing(self: PimStore, collection: str, text: str, item: str | None = None, verify: bool = True) -> str:
        if before == "original" and "anna@there.example" in text and "Family" not in text:
            raise Interrupted(before)
        return put(self, collection, text, item, verify)

    def sending(self: DavClient, href: str, text: str, kind: str, etag: str = "", create: bool = False) -> str:
        if before == "groups" and "Family" in text:
            raise Interrupted(before)
        return send(self, href, text, kind, etag, create)

    with monkeypatch.context() as patched:
        patched.setattr(PimStore, "put", storing)
        patched.setattr(DavClient, "put", sending)
        with pytest.raises(Interrupted):
            call(engine, "conflict.settle", conflicts=(conflict,), side="both")


@pytest.mark.parametrize("before", ["groups", "original"])
def test_keep_both_never_overwrites_the_copy_the_person_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stop_keep_both(engine, conflict.conflict, monkeypatch, before)
        copy = next(c.item for c in call(engine, "contact.list").contacts if c.item != item)
        call(engine, "contact.edit", item=copy, note="Mine")  # the person changes the copy: it is theirs now
        if before == "original":
            call(engine, "contact.edit", item=item, email=("work|anna@newest.example",))  # and the original, before it took the server's
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        (mine,) = [text for text in cards_on_server(server) if "anna@here.example" in text]
        assert "NOTE:Mine" in mine and call(engine, "contact.show", item=copy).note == "Mine"  # never overwritten by Keep both
        if before == "original":  # the original's newest version is never lost either: it stays a conflict to choose again
            assert result.open == (conflict.conflict,) and call(engine, "contact.show", item=item).emails[0].value == "anna@newest.example"
        else:
            assert result.settled == (conflict.conflict,)
            kept_both_once(engine, server, account, uid, "anna@here.example")


def test_the_check_at_start_never_forgets_a_keep_both_that_is_not_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine.pimaccess import open_pim
    from chrono_mail.sync.davconflicts import base_name

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stop_keep_both(engine, conflict.conflict, monkeypatch, "original")  # the copy is done; the original still holds Chrono Mail's version
        with open_pim(engine, account) as store:  # the agreed version read as the server's: the check now finds nothing to choose
            store.write_private(base_name(conflict.collection, conflict.item), conflict.server_text)
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)
        call(later, "conflict.recheck")
        call(later, "dav.sync", account=account)
        assert any(f"UID:{uid}" in text and "anna@there.example" in text for text in cards_on_server(server))  # the server's version stays
        open_now = call(later, "conflict.list").conflicts
        assert [c.conflict for c in open_now] == [conflict.conflict]  # left for the person: Keep both again finishes it
        assert call(later, "conflict.settle", conflicts=(conflict.conflict,), side="both").settled == (conflict.conflict,)
        kept_both_once(later, server, account, uid, "anna@here.example")


@pytest.mark.parametrize("then", ["the server writes the copy its own way", "a sync brings the copy and the person edits it"])
def test_keep_both_after_the_copy_reached_the_server_only_finds_it_and_never_makes_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, then: str,
) -> None:  # Kerio rewrites cards: its own PRODID, its own line folding
    from chrono_mail.pimstore import PimStore

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        put = PimStore.put

        def storing(self: PimStore, collection: str, text: str, item_id: str | None = None, verify: bool = True) -> str:
            if "anna@here.example" in text:  # the server has the copy; stopped before it is kept here
                raise Interrupted("stopped")
            return put(self, collection, text, item_id, verify)

        with monkeypatch.context() as patched:
            patched.setattr(PimStore, "put", storing)
            with pytest.raises(Interrupted):
                call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        name, text = next((name, text) for name, (_, text) in server.collections["contacts"].items.items() if "anna@here.example" in text)
        server.add("contacts", name, text.replace("BEGIN:VCARD\r\n", "BEGIN:VCARD\r\nPRODID:-//Other Server//EN\r\n").replace("anna@here", "anna\r\n @here"))
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        if then == "a sync brings the copy and the person edits it":
            call(later, "dav.sync", account=account)
            copy = next(c.item for c in call(later, "contact.list").contacts if c.item != item)
            call(later, "contact.edit", item=copy, note="Mine")
        result = call(later, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.settled == (conflict.conflict,) and result.problems == ()  # never a false "changed meanwhile"
        kept_both_once(later, server, account, uid, "anna@here.example")  # one copy, in the group
        if then == "a sync brings the copy and the person edits it":
            assert any("NOTE:Mine" in text for text in cards_on_server(server))  # the person's own change of the copy stays


# --- a server that ignores If-Match on a contact's PUT (Kerio, measured 2026-11-03) ---------------------------------------------------------


def test_a_contact_changed_on_a_server_that_ignores_its_version_is_never_written_over(tmp_path: Path) -> None:  # D-193, D-194
    with FakeDav() as server:
        server.no_contact_match = True
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "contact.add", collection=collection_of(engine, account, "contacts"), name="Bob Lee", organization="Acme")
        found = on_server(server, "contacts", str(made.uid))
        assert found is not None
        server.add("contacts", found[0], found[1].replace("ORG:Acme", "ORG:Acme Two"))  # on the phone
        call(engine, "contact.edit", item=made.item, title="Boss")  # here, online, no synchronisation in between
        assert "ORG:Acme Two" in (on_server(server, "contacts", str(made.uid)) or ("", ""))[1]  # the phone's change is still there
        (conflict,) = call(engine, "conflict.list").conflicts
        assert conflict.reason == "clash"  # changed on both sides: the person's choice (D-194)


def test_a_contact_change_whose_server_version_cannot_be_read_now_waits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # D-193
    from chrono_mail.protocols.dav import DavError

    with FakeDav() as server:
        server.no_contact_match = True
        engine, account, _ = linked(tmp_path, server)
        made = call(engine, "contact.add", collection=collection_of(engine, account, "contacts"), name="Bob Lee", organization="Acme")

        def failing(client: DavClient, collection: object, href: str) -> None:
            raise DavError("busy", 503)

        with monkeypatch.context() as patched:
            patched.setattr(DavClient, "read", failing)
            call(engine, "contact.edit", item=made.item, title="Boss")
        assert "TITLE:Boss" not in (on_server(server, "contacts", str(made.uid)) or ("", ""))[1]  # nothing sent without the server's version
        assert call(engine, "sync.waiting").accounts != ()
        call(engine, "dav.sync", account=account)
        assert "TITLE:Boss" in (on_server(server, "contacts", str(made.uid)) or ("", ""))[1] and call(engine, "conflict.list").conflicts == ()


# --- the copy of an interrupted Keep both, when the person then settles the conflict another way ----------------------------------------------


def settled_another_way(engine: Engine, conflict: str, way: str) -> object:
    """The conflict settled with Use Server data, Use Chrono Mail, or a choice per field (each Chrono Mail's)."""
    if way == "fields":
        (summary,) = [c for c in call(engine, "conflict.list").conflicts if c.conflict == conflict]
        return call(engine, "conflict.settle", choices=tuple(f"{conflict}\t{each.key}\tlocal" for each in summary.fields))
    return call(engine, "conflict.settle", conflicts=(conflict,), side=way)


@pytest.mark.parametrize("way", ["server", "local", "fields"])
@pytest.mark.parametrize("before", ["groups", "original"])
def test_settled_another_way_after_an_interrupted_keep_both_the_copy_goes_on_the_server_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str, way: str,
) -> None:  # OR-1: the server first, both computers the same
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stop_keep_both(engine, conflict.conflict, monkeypatch, before)
        assert any("anna@here.example" in text and f"UID:{uid}" not in text for text in cards_on_server(server))  # the copy is there
        result = settled_another_way(engine, conflict.conflict, way)
        assert getattr(result, "settled") == (conflict.conflict,) and getattr(result, "problems") == ()
        anna = [text for text in cards_on_server(server) if "anna@" in text]
        assert len(anna) == 1 and f"UID:{uid}" in anna[0]  # the copy is gone from the server, the original stays
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert family.count("urn:uuid:") == 1 and uid.removeprefix("urn:uuid:") in family  # and from its group
        assert [c.item for c in call(engine, "contact.list").contacts] == [item] and call(engine, "conflict.list").conflicts == ()
        assert call(engine, "sync.waiting").accounts == () and call(engine, "dav.sync", account=account)[0].errors == ()


def test_offline_the_copy_of_an_interrupted_keep_both_goes_here_and_later_on_the_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stop_keep_both(engine, conflict.conflict, monkeypatch, "original")
        server.writes_failing = True
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
        assert [c.item for c in call(engine, "contact.list").contacts] == [item]  # gone here at once
        later = Session(engine.directories, engine.passwords, engine.keys, durable=False)  # the program started again
        assert call(later, "sync.waiting").accounts != ()
        server.writes_failing = False
        call(later, "dav.sync", account=account)
        anna = [text for text in cards_on_server(server) if "anna@" in text]
        assert len(anna) == 1 and f"UID:{uid}" in anna[0] and "anna@there.example" in anna[0]
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert family.count("urn:uuid:") == 1 and call(later, "sync.waiting").accounts == ()


def test_the_copy_of_an_interrupted_keep_both_the_person_changed_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, _, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        stop_keep_both(engine, conflict.conflict, monkeypatch, "original")
        copy = next(c.item for c in call(engine, "contact.list").contacts if c.item != item)
        call(engine, "contact.edit", item=copy, note="Mine")  # the person's now
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
        assert any("NOTE:Mine" in text for text in cards_on_server(server)) and call(engine, "contact.show", item=copy).note == "Mine"
        (family,) = [text for text in cards_on_server(server) if "Family" in text]
        assert family.count("urn:uuid:") == 2  # still in its group


def test_the_copy_keep_both_told_the_person_it_made_stays_when_the_original_is_settled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        from chrono_mail.engine.pimaccess import open_pim

        stop_keep_both(engine, conflict.conflict, monkeypatch, "original")  # the copy is done
        with open_pim(engine, account) as store:  # a synchronisation merged into the original before it took the server's version
            store.put(conflict.collection, store.get(conflict.collection, item).replace("END:VCARD", "NOTE:Merged\r\nEND:VCARD"), item)
        result = call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        assert result.open == (conflict.conflict,) and "the copy with Chrono Mail's version is made" in result.problems[0]  # told: it is made
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
        assert any("anna@here.example" in text and f"UID:{uid}" not in text for text in cards_on_server(server))  # the copy stays
        assert len(call(engine, "contact.list").contacts) == 2


# --- a server that stores the copy its own way (Kerio: its own attendee parameters, TRANSP, a reminder that does nothing) ----------------------


def kerio_rewrite(text: str) -> str:
    """A new appointment as Kerio stores it: the attendees with its own parameters, Show as written out, a reminder of its own."""
    lines = [line.replace("ATTENDEE;", "ATTENDEE;X-SENT=TRUE;CUTYPE=INDIVIDUAL;", 1) if line.startswith("ATTENDEE;") else line for line in text.split("\r\n")]
    end = lines.index("END:VEVENT")
    return "\r\n".join([*lines[:end], "TRANSP:OPAQUE", "BEGIN:VALARM", "TRIGGER;VALUE=DATE-TIME:19760401T005545Z", "ACTION:NONE", "END:VALARM",
                        *lines[end:]])


def rewritten_clash(engine: Engine, server: FakeDav, account: str) -> tuple[str, str, str]:
    """A weekly appointment with an attendee changed here and on the server (a conflict); from now on the server stores new items its own way.
    Returns (item, uid, the conflict)."""
    made = call(engine, "event.add", calendar=collection_of(engine, account, "events"), summary="Weekly", start="2026-11-03T10:00",
                end="2026-11-03T11:00", rrule="FREQ=WEEKLY", attendee=("Bob <bob@example.com>",), organizer="ann@example.com")
    change_on_server(server, str(made.uid), "SUMMARY:Weekly", "SUMMARY:Weekly there")
    call(engine, "event.edit", item=made.item, summary="Weekly here")
    (conflict,) = call(engine, "conflict.list").conflicts
    server.rewrite = kerio_rewrite
    return str(made.item), str(made.uid), conflict.conflict


def interrupted_keep_both(engine: Engine, conflict: str, monkeypatch: pytest.MonkeyPatch, before: str) -> None:
    """Keep both stopped before the copy is kept here ("copy") or before the original takes the server's version ("original")."""
    from chrono_mail.pimstore import PimStore

    put = PimStore.put

    def storing(self: PimStore, collection: str, text: str, item: str | None = None, verify: bool = True) -> str:
        if (before == "copy" and "SUMMARY:Weekly here" in text) or (before == "original" and "SUMMARY:Weekly there" in text):
            raise Interrupted(before)
        return put(self, collection, text, item, verify)

    with monkeypatch.context() as patched:
        patched.setattr(PimStore, "put", storing)
        with pytest.raises(Interrupted):
            call(engine, "conflict.settle", conflicts=(conflict,), side="both")


def events_on_server(server: FakeDav) -> list[str]:
    return [text.replace("\r\n ", "") for _, (_, text) in server.collections["events"].items.items()]


@pytest.mark.parametrize("before", ["copy", "original"])
def test_a_copy_the_server_stored_its_own_way_goes_when_settled_another_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        _, uid, conflict = rewritten_clash(engine, server, account)
        interrupted_keep_both(engine, conflict, monkeypatch, before)
        assert any("X-SENT=TRUE" in text for text in events_on_server(server))  # the copy, stored the server's way
        call(engine, "dav.sync", account=account)  # brings the server's newer version of the copy, as any item
        assert call(engine, "conflict.settle", conflicts=(conflict,), side="server").settled == (conflict,)
        texts = events_on_server(server)
        assert any(f"UID:{uid}" in text and "SUMMARY:Weekly there" in text for text in texts)
        if before == "original":  # read back after it was sent: Keep both's own copy, gone; only the original stays
            assert len(texts) == 1
        else:  # never read back, it reads otherwise than written: it could be the person's, so it stays (never lost, OR-2)
            assert len(texts) == 2


def test_a_copy_the_server_stored_its_own_way_is_brought_up_to_date_by_keep_both_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, uid, conflict = rewritten_clash(engine, server, account)
        interrupted_keep_both(engine, conflict, monkeypatch, "original")
        call(engine, "dav.sync", account=account)
        call(engine, "event.edit", item=item, summary="Weekly here again")  # the original changed before it took the server's
        result = call(engine, "conflict.settle", conflicts=(conflict,), side="both")
        assert result.settled == (conflict,) and result.problems == ()  # never "the original was changed meanwhile"
        texts = events_on_server(server)
        assert len(texts) == 2 and any(f"UID:{uid}" in text and "SUMMARY:Weekly there" in text for text in texts)
        assert any("SUMMARY:Weekly here again" in text and f"UID:{uid}" not in text for text in texts)  # the one copy, up to date
        assert call(engine, "conflict.list").conflicts == ()



def test_a_copy_changed_on_another_computer_after_an_unfinished_write_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # OR-2
    from chrono_mail.pimstore import PimStore

    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        item, _, _ = contact_clash(engine, server, account)
        (conflict,) = call(engine, "conflict.list").conflicts
        put = PimStore.put

        def storing(self: PimStore, collection: str, text: str, item_id: str | None = None, verify: bool = True) -> str:
            if "anna@here.example" in text:  # the server has the copy; stopped before it is kept here
                raise Interrupted("stopped")
            return put(self, collection, text, item_id, verify)

        with monkeypatch.context() as patched:
            patched.setattr(PimStore, "put", storing)
            with pytest.raises(Interrupted):
                call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="both")
        name, text = next((name, text) for name, (_, text) in server.collections["contacts"].items.items() if "anna@here.example" in text)
        server.add("contacts", name, text.replace("END:VCARD", "TEL;TYPE=cell:+31 6 1234 5678\r\nEND:VCARD"))  # another computer adds a phone
        call(engine, "dav.sync", account=account)
        assert call(engine, "conflict.settle", conflicts=(conflict.conflict,), side="server").settled == (conflict.conflict,)
        assert any("anna@here.example" in text and "+31 6 1234 5678" in text for text in cards_on_server(server))  # the copy stays, with it
        copy = next(c.item for c in call(engine, "contact.list").contacts if c.item != item)
        assert call(engine, "contact.show", item=copy).phones[0].value == "+31 6 1234 5678"
