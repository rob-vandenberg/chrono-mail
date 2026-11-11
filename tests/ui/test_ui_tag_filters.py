from pytestqt.qtbot import QtBot

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.sections import TASKS
from chrono_mail.ui.tagfilter import TagFilter
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.test_ui_calendar import open_calendar, summaries
from tests.ui.test_ui_tasks import names, open_tasks, seed, select
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tag_filters.py 4.4.571"

# --- Version history ----------------------------------------------------
# v4.4.571: Initial tests of the tag filters in the Tasks and Calendar sidebars (D-327)


def rows(tag_filter: TagFilter) -> list[str]:
    return [tag_filter.list.item(row).text() for row in range(tag_filter.list.count())]


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
