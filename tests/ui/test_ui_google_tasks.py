from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine import commands_dav, commands_google, google_access, google_colours
from chrono_mail.protocols import google, google_tasks
from chrono_mail.protocols.google import GoogleClient
from chrono_mail.ui.sections import TASKS
from chrono_mail.ui.taskeditor import TaskEditor
from chrono_mail.ui.tasksview import TasksPage
from tests.fakedav import FakeDav
from tests.fakegoogletasks import FakeTasksApi
from tests.test_google_colours import CALENDAR, FakeCalendarApi, GoogleDav
from tests.test_google_sign_in import CODES, signs_in
from tests.ui.test_ui_tasks import fake_exec, names, select
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_google_tasks.py 4.7.658"

# --- Version history ----------------------------------------------------
# v4.7.658: Initial (B-14, D-370): Google's task lists and tasks in the Tasks section; a task made, changed and marked done there is so on Google


@pytest.fixture
def google_tasks_world(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTasksApi]:
    """The window's engine with a Google account signed in: its calendars, contacts and tasks on this computer; Google Tasks has the list
    Work with one task."""
    calendars, contacts = GoogleDav(), FakeDav()
    with FakeCalendarApi() as api, FakeTasksApi() as tasks:
        calendars.api = api
        for server in (calendars, contacts):
            server.bearer = "access-1"
            server.__enter__()
        try:
            def renew(client: GoogleClient, refresh: str) -> tuple[str, int]:
                return "access-1", 3600

            def no_calendar_api(href: str) -> str:  # the colours of Google's events play no part here
                return ""

            monkeypatch.setattr(google_access, "refresh_access", renew)
            monkeypatch.setattr(commands_google, "PARTS", (("Google Calendar", f"http://127.0.0.1:{calendars.port}/dav/"),
                                                           ("Google Contacts", f"http://localhost:{contacts.port}/dav/")))
            monkeypatch.setattr(google, "CALENDAR_API", api.url)
            monkeypatch.setattr(google_colours, "calendar_id_of", no_calendar_api)
            monkeypatch.setattr(google_tasks, "TASKS_API", tasks.url)
            real = commands_dav.google_kinds

            def kinds(address: str) -> frozenset[str]:  # the calendar server on this computer is Google's calendars
                return frozenset({"events", "tasks"}) if f":{calendars.port}/" in address else real(address)

            monkeypatch.setattr(commands_dav, "google_kinds", kinds)
            tasks.add_list("work", "Work")
            tasks.add("work", "t1", "Write the report", due="2099-11-20T00:00:00.000Z")
            signs_in(monkeypatch, CALENDAR, "refresh-1")
            world.call("pim.enable")
            world.call("google.sign-in", **CODES)
            world.call("dav.sync")
            started(qtbot, world)
            yield tasks
        finally:
            google_access.forget_access("")
            for server in (calendars, contacts):
                server.__exit__(None, None, None)


def test_googles_task_list_shows_in_tasks_and_a_task_done_there_is_done_on_google(world: World, qtbot: QtBot, google_tasks_world: FakeTasksApi) -> None:
    world.window.switch_section(TASKS)
    page = world.window.pages[TASKS]
    assert isinstance(page, TasksPage)
    wait(qtbot, lambda: "Write the report" in names(page))
    work = next(item.collection for item in world.call("collection.list", kind="tasks") if item.name == "Work")
    assert work in page.panel.visible_ids()
    select(page, "Write the report")
    page.complete_selected()
    wait(qtbot, lambda: google_tasks_world.titled("work", "Write the report").get("status") == "completed")


def test_a_task_made_and_changed_in_the_editor_goes_to_google(world: World, qtbot: QtBot, google_tasks_world: FakeTasksApi, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.switch_section(TASKS)
    page = world.window.pages[TASKS]
    assert isinstance(page, TasksPage)
    wait(qtbot, lambda: "Write the report" in names(page))
    work = next(item.collection for item in world.call("collection.list", kind="tasks") if item.name == "Work")

    def fill(editor: TaskEditor) -> None:
        editor.list.setCurrentIndex(editor.list.findData(work))
        editor.summary.setText("Book the room")
        editor.note.setPlainText("For ten people")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(fill))
    page.act_new.trigger()
    wait(qtbot, lambda: any(task.get("title") == "Book the room" for task in google_tasks_world.live("work").values()))
    assert google_tasks_world.titled("work", "Book the room")["notes"] == "For ten people"

    def change(editor: TaskEditor) -> None:
        editor.summary.setText("Book the big room")

    monkeypatch.setattr(TaskEditor, "exec", fake_exec(change))
    wait(qtbot, lambda: "Book the room" in names(page))
    select(page, "Book the room")
    wait(qtbot, lambda: "Book the room" in page.detail.toPlainText())
    page.act_edit.trigger()
    wait(qtbot, lambda: any(task.get("title") == "Book the big room" for task in google_tasks_world.live("work").values()))
