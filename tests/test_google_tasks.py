import os
from collections.abc import Iterator
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.core import ProtocolError
from chrono_mail.engine import Engine, commands_dav
from chrono_mail.protocols import google_tasks
from chrono_mail.protocols.google import SCOPES
from chrono_mail.protocols.google_tasks import API_OFF, NEEDS_SIGN_IN, TASKS_SCOPE, TOO_LONG_TITLE
from tests.fakedav import FakeDav
from tests.fakegoogletasks import FakeTasksApi
from tests.test_engine_commands import call
from tests.test_google_colours import FakeCalendarApi, google_world  # noqa: F401 - google_world is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_google_tasks.py 4.7.658"

# --- Version history ----------------------------------------------------
# v4.7.658: Initial (B-14, D-370): Google's task lists in the Tasks section, Google first; what Google does not keep stays here; done on
#           Google shows done; deleted, moved, subtasks; changed on both sides, offline, refused; the permission or the API missing;
#           a full disk never turns a task Google took into a conflict; a move whose next step failed never makes a second task


type World = tuple[Engine, str, FakeTasksApi]


@pytest.fixture
def tasks_world(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """A Google calendar account whose Google Tasks has the lists Work (one task) and Home, on this computer."""
    engine, account, calendars, _ = google_world
    with FakeTasksApi() as api:
        monkeypatch.setattr(google_tasks, "TASKS_API", api.url)
        real = commands_dav.google_kinds

        def kinds(address: str) -> frozenset[str]:  # the calendar server on this computer is Google's calendars
            return frozenset({"events", "tasks"}) if f":{calendars.port}/" in address else real(address)

        monkeypatch.setattr(commands_dav, "google_kinds", kinds)
        api.add_list("work", "Work")
        api.add_list("home", "Home")
        api.add("work", "t1", "Write the report", notes="Two pages", due="2030-11-20T00:00:00.000Z")
        refresh(engine, account)
        yield engine, account, api


def refresh(engine: Engine, account: str) -> Any:  # noqa: ANN401 - a DavSyncSummary
    return call(engine, "dav.sync", account=account)[0]


def list_id(engine: Engine, name: str) -> str:
    return next(item.collection for item in call(engine, "collection.list", kind="tasks") if item.name == name)


def tasks(engine: Engine, name: str) -> dict[str, Any]:
    """The tasks of a list here, by title, as task.show gives them."""
    rows = call(engine, "task.list", list=list_id(engine, name), state="all").tasks
    return {row.summary: call(engine, "task.show", item=row.item) for row in rows}


def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(*arguments: object, **named: object) -> dict[str, object]:
        raise ProtocolError("Google could not be reached: no route")

    monkeypatch.setattr(google_tasks, "call", unreachable)


def test_googles_task_lists_and_tasks_are_in_the_tasks_section(tasks_world: World) -> None:
    engine, _, _ = tasks_world
    assert {"Work", "Home"} <= {item.name for item in call(engine, "collection.list", kind="tasks")}
    report = tasks(engine, "Work")["Write the report"]
    assert report.description == "Two pages" and report.due.startswith("2030-11-20") and not report.done


def test_a_task_made_here_goes_to_google_first_and_keeps_its_due_time_and_priority_here(tasks_world: World) -> None:
    engine, account, api = tasks_world
    call(engine, "task.add", list=list_id(engine, "Home"), summary="Buy paint", due="2030-11-21T17:00", priority=1, tag=("Shop",))
    on_google = api.titled("home", "Buy paint")
    assert str(on_google["due"]).startswith("2030-11-21") and "priority" not in on_google
    api.change("home", str(on_google["id"]), title="Buy white paint")
    refresh(engine, account)
    here = tasks(engine, "Home")["Buy white paint"]
    assert "17:00" in here.due and here.priority == 1 and here.tags == ("Shop",)  # what Google does not keep stays here


def test_done_here_is_done_on_google_and_done_on_google_shows_done_here(tasks_world: World) -> None:
    engine, account, api = tasks_world
    call(engine, "task.complete", item=tasks(engine, "Work")["Write the report"].item)
    assert api.titled("work", "Write the report")["status"] == "completed"
    api.add("home", "t2", "Water the plants")
    refresh(engine, account)
    api.change("home", "t2", status="completed", completed="2030-11-20T10:00:00.000Z", hidden=True)  # ticked off in Google's own app
    refresh(engine, account)
    assert tasks(engine, "Home")["Water the plants"].done  # hidden on Google, done here (the owner, Q138)


def test_a_task_deleted_on_google_leaves_here_and_one_deleted_here_leaves_google(tasks_world: World) -> None:
    engine, account, api = tasks_world
    api.add("home", "t2", "Water the plants")
    refresh(engine, account)
    api.remove("home", "t2")
    refresh(engine, account)
    assert "Water the plants" not in tasks(engine, "Home")
    call(engine, "task.delete", item=tasks(engine, "Work")["Write the report"].item)
    assert api.live("work") == {}


def test_a_task_moved_to_another_list_is_moved_on_google_as_the_same_task(tasks_world: World) -> None:
    engine, account, api = tasks_world
    call(engine, "task.move", item=tasks(engine, "Work")["Write the report"].item, list=list_id(engine, "Home"))
    assert api.live("work") == {} and "t1" in api.live("home") and api.moves[0][:2] == ("t1", "home")
    refresh(engine, account)
    assert list(tasks(engine, "Home")) == ["Write the report"] and tasks(engine, "Work") == {}


def test_subtasks_follow_both_ways(tasks_world: World) -> None:
    engine, account, api = tasks_world
    api.add("work", "t2", "Find the figures", parent="t1")
    refresh(engine, account)
    parent = tasks(engine, "Work")["Write the report"]
    assert tasks(engine, "Work")["Find the figures"].parent == parent.uid
    call(engine, "task.add", list=list_id(engine, "Work"), summary="Check the spelling", parent=parent.uid)
    assert api.titled("work", "Check the spelling").get("parent") == "t1"


def test_a_task_changed_here_offline_and_on_google_is_a_sync_conflict(tasks_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, api = tasks_world
    real = google_tasks.call
    offline(monkeypatch)
    call(engine, "task.edit", item=tasks(engine, "Work")["Write the report"].item, summary="Write the short report")
    monkeypatch.setattr(google_tasks, "call", real)
    api.change("work", "t1", title="Write the long report")
    report = refresh(engine, account)
    assert report.conflicts == 1 and api.titled("work", "Write the long report")  # never written over
    assert len(call(engine, "conflict.list").conflicts) == 1


def test_a_task_made_offline_goes_to_google_at_the_next_refresh(tasks_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, api = tasks_world
    real = google_tasks.call
    offline(monkeypatch)
    call(engine, "task.add", list=list_id(engine, "Home"), summary="Call the plumber")
    assert all(task.get("title") != "Call the plumber" for task in api.live("home").values())
    monkeypatch.setattr(google_tasks, "call", real)
    refresh(engine, account)
    assert api.titled("home", "Call the plumber")
    refresh(engine, account)
    assert list(tasks(engine, "Home")) == ["Call the plumber"]  # once, not twice


def test_a_lasting_no_of_google_is_said_and_the_task_stays_here(tasks_world: World) -> None:
    engine, _, api = tasks_world
    api.refusing = True
    with pytest.raises(Exception, match="Invalid task"):
        call(engine, "task.add", list=list_id(engine, "Home"), summary="Not allowed")
    assert api.live("home") == {}


def test_a_title_too_long_for_google_is_refused_before_anything_is_sent(tasks_world: World) -> None:
    engine, _, api = tasks_world
    before = api.writes
    with pytest.raises(Exception, match="1,024 characters"):
        call(engine, "task.edit", item=tasks(engine, "Work")["Write the report"].item, summary="x" * 1025)
    assert api.writes == before and TOO_LONG_TITLE.startswith("Google keeps at most 1,024")


def test_without_the_tasks_permission_the_refresh_says_to_sign_in_again_and_keeps_the_lists(tasks_world: World) -> None:
    engine, account, api = tasks_world
    api.no_scope = True
    report = refresh(engine, account)
    assert NEEDS_SIGN_IN in report.errors and "Write the report" in tasks(engine, "Work")


def test_with_the_api_switched_off_the_refresh_says_where_to_switch_it_on(tasks_world: World) -> None:
    engine, account, api = tasks_world
    api.api_off = True
    assert API_OFF in refresh(engine, account).errors


def test_a_deleted_task_comes_back_on_google_with_undo(tasks_world: World) -> None:
    engine, _, api = tasks_world
    call(engine, "task.delete", item=tasks(engine, "Work")["Write the report"].item, undo=1)
    assert api.live("work") == {}
    call(engine, "pim.undo", section="tasks")
    assert api.titled("work", "Write the report")["notes"] == "Two pages"


def test_the_sign_in_asks_for_the_tasks_permission_and_never_for_mail() -> None:
    assert TASKS_SCOPE in SCOPES.split() and not any("mail.google.com" in scope or "auth/gmail" in scope for scope in SCOPES.split())


def test_a_full_disk_never_turns_a_task_google_took_into_a_conflict(tasks_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, api = tasks_world

    class FullDisk:
        """The disk is full for what the Google Tasks part remembers (only there: the task itself is written here as always)."""

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def replace(self, source: object, target: object) -> None:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(google_tasks, "os", FullDisk())
    call(engine, "task.add", list=list_id(engine, "Home"), summary="Fix the gate")
    assert api.titled("home", "Fix the gate")
    report = refresh(engine, account)
    assert report.conflicts == 0 and list(tasks(engine, "Home")) == ["Fix the gate"]  # once, no conflict


def test_a_move_whose_change_after_it_failed_is_finished_without_a_second_task(tasks_world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, api = tasks_world
    real = google_tasks.call

    def breaks_after_the_move(access: str, method: str, path: str, query: dict[str, str] | None = None, body: dict[str, object] | None = None) -> dict[str, object]:
        if method == "PATCH":
            raise ProtocolError("Google could not be reached: the connection broke")
        return real(access, method, path, query, body)

    monkeypatch.setattr(google_tasks, "call", breaks_after_the_move)
    call(engine, "task.move", item=tasks(engine, "Work")["Write the report"].item, list=list_id(engine, "Home"))
    monkeypatch.setattr(google_tasks, "call", real)
    refresh(engine, account)
    assert list(api.live("home")) == ["t1"] and api.live("work") == {}  # the same task, once
    assert list(tasks(engine, "Home")) == ["Write the report"]
