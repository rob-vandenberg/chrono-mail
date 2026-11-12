from pytestqt.qtbot import QtBot

from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.sections import CALENDAR, CONTACTS, TASKS
from chrono_mail.ui.splitter import SideSplitter
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.test_ui_calendar import open_calendar
from tests.ui.test_ui_startup_view import restarted
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_side_separator.py 4.4.584"

# --- Version history ----------------------------------------------------
# v4.4.584: Contacts has the shared Tags block below the separator (D-334)
# v4.4.577: Initial tests of the separator between a section's folder list and its Tags list, its place kept per section (D-330)

DRAGGED: list[int] = [100, 400]  # the folder list a fifth of the height: far from the first place, 3 to 2
PLACES: dict[str, list[int]] = {CALENDAR: DRAGGED, TASKS: [400, 100], CONTACTS: [250, 250]}  # each section its own, none 3 to 2


def share(splitter: SideSplitter) -> float:
    """The part of the height the folder list has."""
    first, second = splitter.sizes()
    return first / (first + second)


def splitters(window: MainWindow) -> dict[str, SideSplitter]:
    calendar, tasks, contacts = window.pages[CALENDAR], window.pages[TASKS], window.pages[CONTACTS]
    assert isinstance(calendar, CalendarPage) and isinstance(tasks, TasksPage) and isinstance(contacts, ContactsPage)
    return {CALENDAR: calendar.side_splitter, TASKS: tasks.side_splitter, CONTACTS: contacts.side_splitter}


def test_calendar_tasks_and_contacts_each_have_a_separator_above_their_tags(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    calendar, tasks, contacts = world.window.pages[CALENDAR], world.window.pages[TASKS], world.window.pages[CONTACTS]
    assert isinstance(calendar, CalendarPage) and isinstance(tasks, TasksPage) and isinstance(contacts, ContactsPage)
    assert calendar.side_splitter.widget(0) is calendar.panel and calendar.side_splitter.widget(1) is calendar.tag_filter
    assert tasks.side_splitter.widget(0) is tasks.panel and tasks.side_splitter.widget(1) is tasks.tag_filter
    assert contacts.side_splitter.widget(0) is contacts.panel and contacts.side_splitter.widget(1) is contacts.tag_filter  # the heading moves with its list
    assert not calendar.side_splitter.childrenCollapsible()
    wait(qtbot, lambda: abs(share(calendar.side_splitter) - 0.6) < 0.05)  # the first time: 3 to 2, as before


def test_each_section_keeps_its_own_place_of_the_separator_through_a_restart(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    for section, sizes in PLACES.items():  # a different place in each section: a shared or misspelt key shows
        world.window.switch_section(section)
        splitters(world.window)[section].setSizes(sizes)
    again = restarted(world, qtbot)
    places = splitters(again)
    for section, sizes in PLACES.items():
        again.switch_section(section)
        wait(qtbot, lambda: abs(share(places[section]) - sizes[0] / sum(sizes)) < 0.05)  # noqa: B023 - waited for at once
    again.close()


def test_a_section_not_opened_in_a_run_keeps_the_place_from_before(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    world.window.switch_section(TASKS)
    splitters(world.window)[TASKS].setSizes(PLACES[TASKS])
    world.window.switch_section(CALENDAR)  # the next start opens Calendar: Tasks is not shown in that run
    world.window = restarted(world, qtbot)
    world.window.switch_section(CALENDAR)
    third = restarted(world, qtbot)  # quitting the run without Tasks kept its place
    third.switch_section(TASKS)
    wait(qtbot, lambda: abs(share(splitters(third)[TASKS]) - 0.8) < 0.05)
    third.close()


def test_a_hidden_tags_list_hides_the_separator_and_brings_its_place_back(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    page.side_splitter.setSizes(DRAGGED)
    page.tag_filter.setVisible(False)
    handle = page.side_splitter.handle(1)
    assert handle is not None
    wait(qtbot, handle.isHidden)
    page.tag_filter.setVisible(True)
    wait(qtbot, lambda: not handle.isHidden() and abs(share(page.side_splitter) - 0.2) < 0.05)


def test_quitting_with_the_tags_list_hidden_keeps_the_place_from_before(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    splitters(world.window)[CALENDAR].setSizes(DRAGGED)
    world.window = restarted(world, qtbot)  # the Quit kept a fifth
    world.window.switch_section(CALENDAR)
    page = world.window.pages[CALENDAR]
    assert isinstance(page, CalendarPage)
    wait(qtbot, lambda: abs(share(page.side_splitter) - 0.2) < 0.05)
    page.side_splitter.setSizes([400, 100])
    page.tag_filter.setVisible(False)
    third = restarted(world, qtbot)  # quit with the Tags list hidden
    kept = third.settings.value("calendar_side_splitter")
    assert isinstance(kept, list) and len(kept) == 2
    assert abs(int(kept[0]) / (int(kept[0]) + int(kept[1])) - 0.2) < 0.05
    third.close()
