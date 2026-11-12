from collections.abc import Callable
from typing import Any

import pytest
from PySide6.QtWidgets import QListWidgetItem
from pytestqt.qtbot import QtBot

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.sections import TASKS
from chrono_mail.engine.commands_tasklist import TASK_PLACES
from chrono_mail.ui.models import COUNT_ROLE
from chrono_mail.ui.tagfilter import TagFilter
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.test_ui_calendar import open_calendar, summaries
from tests.ui.test_ui_tasks import lists, names, open_tasks, seed, select
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tag_filters.py 4.4.581"

# --- Version history ----------------------------------------------------
# v4.4.581: a late answer about one task, asked with the old tag, never takes it out of the list of every task (D-146); a row's number is read from where it is drawn apart, as a folder's unread (D-327)
# v4.4.571: Initial tests of the tag filters in the Tasks and Calendar sidebars (D-327)


def rows(tag_filter: TagFilter) -> list[str]:
    """Each row as "name  (count)": the count is drawn apart, as a folder's unread (D-327)."""
    return [counted_row(tag_filter.list.item(row)) for row in range(tag_filter.list.count())]


def counted_row(item: QListWidgetItem) -> str:
    count = item.data(COUNT_ROLE)
    return f"{item.text()}  ({count})" if count else item.text()


def test_the_tasks_sidebar_lists_the_tags_of_tasks_and_a_tag_shows_only_its_tasks(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks", "Home  (1)"])
    page.tag_filter.choose("Home")
    wait(qtbot, lambda: names(page) == ["Pay rent"])
    page.tag_filter.choose("")
    wait(qtbot, lambda: len(names(page)) == 4)


def test_a_task_that_loses_the_chosen_tag_leaves_and_with_no_task_left_every_task_shows_again(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks", "Home  (1)"])
    page.tag_filter.choose("Home")
    wait(qtbot, lambda: names(page) == ["Pay rent"])
    select(page, "Pay rent")
    page._edit_selected(remove_tag=("Home",))  # noqa: SLF001 - the Tag menu's own step
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks"] and page.tag_filter.tag() == "" and len(names(page)) == 4)


def test_the_tag_option_of_event_list_gives_only_the_events_with_that_tag(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    found = world.call("event.list", from_date="2026-10-12", to_date="2026-10-19", tag="food")
    assert [event.summary for event in found.events] == ["Lunch"]  # a tag is found whatever its capitals


def test_the_calendar_sidebar_lists_the_tags_of_events_and_a_tag_filters_every_view(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    wait(qtbot, lambda: rows(page.tag_filter) == ["All events", "Food  (1)"])
    page.tag_filter.choose("Food")
    wait(qtbot, lambda: summaries(page) == ["Lunch"])
    page.set_view(layout.MONTH)
    wait(qtbot, lambda: page.view == layout.MONTH and summaries(page) == ["Lunch"])
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.view == layout.AGENDA and summaries(page) == ["Lunch"])
    page.tag_filter.choose("")
    wait(qtbot, lambda: "Dentist" in summaries(page))


def test_a_task_that_is_not_shown_and_gets_a_tag_is_counted_at_once(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks", "Home  (1)"])
    page._choose_tab("upcoming")  # noqa: SLF001 - the Upcoming tab: the completed task is not shown
    wait(qtbot, lambda: "Old job" not in names(page))
    done = next(task.item for task in world.call("task.list", state="all").tasks if task.summary == "Old job")
    world.call("task.edit", item=done, add_tag=("Home",))
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks", "Home  (2)"])


def icon_colour(tag_filter: TagFilter, row: int) -> str:
    return tag_filter.list.item(row).icon().pixmap(12, 12).toImage().pixelColor(6, 6).name()


def test_a_tag_given_another_colour_in_settings_shows_it_in_the_tags_lists_of_tasks_and_calendar(world: World, qtbot: QtBot) -> None:
    calendar = open_calendar(world, qtbot)
    wait(qtbot, lambda: rows(calendar.tag_filter) == ["All events", "Food  (1)"])
    world.call("tag.colour", name="Food", colour="#123456")
    calendar.reload_tags()  # what the main window does after Settings saved tags
    wait(qtbot, lambda: icon_colour(calendar.tag_filter, 1) == "#123456")
    world.call("task.add", list=next(c.collection for c in world.call("collection.list", kind="tasks")), summary="Buy bread", tag=("Food",))
    world.window.switch_section(TASKS)
    tasks = world.window.pages[TASKS]
    assert isinstance(tasks, TasksPage)
    wait(qtbot, lambda: rows(tasks.tag_filter) == ["All tasks", "Food  (1)"])
    world.call("tag.colour", name="Food", colour="#654321")
    tasks.reload_tags()
    wait(qtbot, lambda: icon_colour(tasks.tag_filter, 1) == "#654321")


def test_an_answer_about_one_task_asked_before_the_list_was_asked_again_is_not_used(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks", "Home  (1)"])
    page.tag_filter.choose("Home")
    wait(qtbot, lambda: names(page) == ["Pay rent"])
    held: list[Callable[[], None]] = []
    ask = page._backend.ask  # noqa: SLF001

    def hold_one_task(command: Any, parameters: Any, done: Callable[[Any], None], failed: Any, owner: Any = None) -> None:  # noqa: ANN401 - forwarded as given
        if command is TASK_PLACES and getattr(parameters, "item", ""):  # the answer about the one task: it comes late, as on a busy computer
            ask(command, parameters, lambda result: held.append(lambda: done(result)), failed, owner=owner)
            return
        ask(command, parameters, done, failed, owner=owner)

    monkeypatch.setattr(page._backend, "ask", hold_one_task)  # noqa: SLF001
    select(page, "Pay rent")
    page._edit_selected(remove_tag=("Home",))  # noqa: SLF001 - the Tag menu's own step
    wait(qtbot, lambda: rows(page.tag_filter) == ["All tasks"] and len(names(page)) == 4 and len(held) >= 1)
    for late in held:
        late()  # the answer asked with Home chosen comes after every task was asked again
    assert len(names(page)) == 4 and "Pay rent" in names(page)


def test_a_task_just_saved_is_selected_also_when_the_list_was_asked_again_before_its_own_answer(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    held: list[Callable[[], None]] = []
    ask = page._backend.ask  # noqa: SLF001

    def hold_one_task(command: Any, parameters: Any, done: Callable[[Any], None], failed: Any, owner: Any = None) -> None:  # noqa: ANN401 - forwarded as given
        if command is TASK_PLACES and getattr(parameters, "item", ""):
            ask(command, parameters, lambda result: held.append(lambda: done(result)), failed, owner=owner)
            return
        ask(command, parameters, done, failed, owner=owner)

    monkeypatch.setattr(page._backend, "ask", hold_one_task)  # noqa: SLF001
    saved = world.call("task.add", list=lists(world), summary="Water the plants")
    page._after_save(str(saved.item))  # noqa: SLF001 - what the task window does after Save
    wait(qtbot, lambda: len(held) >= 1)  # its own answer is late
    page.reload_list()  # the list is asked again first (a search, a tag, a tab)
    wait(qtbot, lambda: "Water the plants" in names(page) and page._selected_items() == [str(saved.item)])  # noqa: SLF001
    assert page._select_when_there == ""  # noqa: SLF001 - nothing waits to jump to it later
    for late in held:
        late()
    assert page._selected_items() == [str(saved.item)]  # noqa: SLF001
