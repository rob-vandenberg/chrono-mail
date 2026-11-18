import json
from pathlib import Path

from chrono_mail.engine.commands_pim import CollectionSummary
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_engine_dav import linked, shown
from tests.test_sync_dav import event

# --- Version ------------------------------------------------------------
__version__ = "test_engine_dav_names.py 4.4.624"

# --- Version history ----------------------------------------------------
# v4.4.624: Initial tests (also: a taken name skips only its item; a change made elsewhere still arrives): a server name never has an @; Chrono Mail's own names with one are renamed once, on the server first (D-356)

OLD = "11111111-2222-3333-4444-555555555555@chrono-mail"  # an appointment an older Chrono Mail made: its server name has the @


def names(server: FakeDav) -> list[str]:
    return sorted(server.collections["events"].items)


def state_names(tmp_path: Path) -> list[str]:
    (state,) = list(tmp_path.rglob("dav-state.json"))[:1] or [None]
    assert state is not None
    collections = json.loads(state.read_text())["collections"].values()
    return sorted(href.rsplit("/", 1)[-1] for each in collections for href in each["links"])


def test_a_new_appointment_gets_a_server_name_without_an_at_and_keeps_its_identifier(tmp_path: Path) -> None:
    with FakeDav() as server:
        engine, account, _ = linked(tmp_path, server)
        calendar = shown(engine, account)
        assert isinstance(calendar, CollectionSummary)
        call(engine, "event.add", calendar=calendar.collection, summary="Pedicure", start="2026-11-19T17:00", end="2026-11-19T18:00")
        (name,) = names(server)
        assert "@" not in name and name.endswith(".ics")
        assert "@chrono-mail" in server.collections["events"].items[name][1]  # the identifier inside stays as it is


def test_an_own_name_with_an_at_is_renamed_once_on_the_server_and_the_link_follows(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.add("events", f"{OLD}.ics", event(OLD, "Pedicure"))
        engine, account, _ = linked(tmp_path, server)  # the first synchronisation takes it in under its old name
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert names(server) == ["11111111-2222-3333-4444-555555555555.ics"] and "@" not in "".join(state_names(tmp_path))
        moves = server.count("MOVE")
        assert call(engine, "dav.sync", account=account)[0].errors == () and server.count("MOVE") == moves  # done once
        assert [row.summary for row in call(engine, "event.list", from_date="2026-10-01", to_date="2026-11-01").events] == ["Pedicure"]  # one, not two


def test_a_server_that_does_not_rename_keeps_the_names_and_is_not_asked_again(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.moves_refused = True
        server.add("events", f"{OLD}.ics", event(OLD, "Pedicure"))
        engine, account, _ = linked(tmp_path, server)
        assert call(engine, "dav.sync", account=account)[0].errors == () and names(server) == [f"{OLD}.ics"]
        asked = server.count("MOVE")
        assert call(engine, "dav.sync", account=account)[0].errors == () and server.count("MOVE") == asked == 1


def test_a_rename_that_fails_for_now_changes_nothing_and_is_done_at_a_later_synchronisation(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.add("events", f"{OLD}.ics", event(OLD, "Pedicure"))
        engine, account, _ = linked(tmp_path, server)
        server.moves_failing = True
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert names(server) == [f"{OLD}.ics"] and state_names(tmp_path) == [f"{OLD}.ics"]
        server.moves_failing = False
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert names(server) == ["11111111-2222-3333-4444-555555555555.ics"] == state_names(tmp_path)


def test_a_name_another_program_chose_with_an_at_is_left_alone(tmp_path: Path) -> None:
    with FakeDav() as server:
        other = "abc123@google.com"
        server.add("events", f"{other}.ics", event(other, "From another program"))
        engine, account, _ = linked(tmp_path, server)
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert names(server) == [f"{other}.ics"] and server.count("MOVE") == 0


def test_an_item_already_renamed_or_a_name_already_taken_skips_only_that_item(tmp_path: Path) -> None:
    with FakeDav() as server:
        second = "66666666-7777-8888-9999-000000000000@chrono-mail"
        server.add("events", f"{OLD}.ics", event(OLD, "Pedicure"))
        server.add("events", f"{second}.ics", event(second, "Dentist"))
        server.moves_refused = True  # the first synchronisation takes both in under their old names
        engine, account, _ = linked(tmp_path, server)
        server.moves_refused = False
        server.add("events", "11111111-2222-3333-4444-555555555555.ics", event("someone-else", "Taken"))  # the new name of the first is taken
        state = list(tmp_path.rglob("dav-state.json"))[0]
        kept = json.loads(state.read_text())
        for each in kept["collections"].values():
            each["no_rename"] = False  # as before the refusal: this test is about the taken name
        state.write_text(json.dumps(kept))
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert f"{OLD}.ics" in names(server) and "66666666-7777-8888-9999-000000000000.ics" in names(server)  # the second still renamed


def test_a_change_made_elsewhere_before_the_rename_still_arrives(tmp_path: Path) -> None:
    with FakeDav() as server:
        server.add("events", f"{OLD}.ics", event(OLD, "Pedicure"))
        engine, account, _ = linked(tmp_path, server)
        server.moves_refused = False
        server.collections["events"].items[f"{OLD}.ics"] = ("changed-elsewhere", event(OLD, "Pedicure at 18:00"))  # another computer changed it
        assert call(engine, "dav.sync", account=account)[0].errors == ()
        assert [row.summary for row in call(engine, "event.list", from_date="2026-10-01", to_date="2026-11-01").events] == ["Pedicure at 18:00"]
