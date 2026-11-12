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
__version__ = "test_ui_tag_filters.py 4.4.584"

# --- Version history ----------------------------------------------------
# v4.4.584: One Tags block (D-334): no "All …" line, a click turns a filter on and off, Space and Enter too, the active line blue with a check mark
#           without the focus, the heading names the tag, a filter set elsewhere scrolls its line into view; the active tag stays when it loses its items
# v4.4.582: a contact that loses the chosen tag: All contacts is chosen and every contact shows (D-327)
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
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home  (1)"])
    page.tag_filter.choose("Home")
    wait(qtbot, lambda: names(page) == ["Pay rent"])
    page.tag_filter.choose("")
    wait(qtbot, lambda: len(names(page)) == 4)


def test_a_task_that_loses_the_active_tag_leaves_the_tag_stays_on_until_clicked_off_and_then_every_task_shows(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home  (1)"])
    page.tag_filter.choose("Home")
    wait(qtbot, lambda: names(page) == ["Pay rent"])
    select(page, "Pay rent")
    page._edit_selected(remove_tag=("Home",))  # noqa: SLF001 - the Tag menu's own step
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home"] and page.tag_filter.tag() == "Home" and names(page) == [])  # the active tag stays, without a number (D-334)
    page.tag_filter.choose("")  # a second click: no filter
    wait(qtbot, lambda: page.tag_filter.tag() == "" and len(names(page)) == 4)


def test_the_tag_option_of_event_list_gives_only_the_events_with_that_tag(world: World, qtbot: QtBot) -> None:
    open_calendar(world, qtbot)
    found = world.call("event.list", from_date="2026-10-12", to_date="2026-10-19", tag="food")
    assert [event.summary for event in found.events] == ["Lunch"]  # a tag is found whatever its capitals


def test_the_calendar_sidebar_lists_the_tags_of_events_and_a_tag_filters_every_view(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    wait(qtbot, lambda: rows(page.tag_filter) == ["Food  (1)"])
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
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home  (1)"])
    page._choose_tab("upcoming")  # noqa: SLF001 - the Upcoming tab: the completed task is not shown
    wait(qtbot, lambda: "Old job" not in names(page))
    done = next(task.item for task in world.call("task.list", state="all").tasks if task.summary == "Old job")
    world.call("task.edit", item=done, add_tag=("Home",))
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home  (2)"])


def icon_colour(tag_filter: TagFilter, row: int) -> str:
    return tag_filter.list.item(row).icon().pixmap(12, 12).toImage().pixelColor(6, 6).name()


def test_a_tag_given_another_colour_in_settings_shows_it_in_the_tags_lists_of_tasks_and_calendar(world: World, qtbot: QtBot) -> None:
    calendar = open_calendar(world, qtbot)
    wait(qtbot, lambda: rows(calendar.tag_filter) == ["Food  (1)"])
    world.call("tag.colour", name="Food", colour="#123456")
    calendar.reload_tags()  # what the main window does after Settings saved tags
    wait(qtbot, lambda: icon_colour(calendar.tag_filter, 0) == "#123456")
    world.call("task.add", list=next(c.collection for c in world.call("collection.list", kind="tasks")), summary="Buy bread", tag=("Food",))
    world.window.switch_section(TASKS)
    tasks = world.window.pages[TASKS]
    assert isinstance(tasks, TasksPage)
    wait(qtbot, lambda: rows(tasks.tag_filter) == ["Food  (1)"])
    world.call("tag.colour", name="Food", colour="#654321")
    tasks.reload_tags()
    wait(qtbot, lambda: icon_colour(tasks.tag_filter, 0) == "#654321")


def test_an_answer_about_one_task_asked_before_the_list_was_asked_again_is_not_used(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_tasks(world, qtbot, 4)
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home  (1)"])
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
    wait(qtbot, lambda: rows(page.tag_filter) == ["Home"] and len(held) >= 1)  # the filter stays on (D-334); the answer about Pay rent is held
    page.tag_filter.choose("")  # turned off: every task is asked again
    wait(qtbot, lambda: len(names(page)) == 4)
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


def test_a_contact_that_loses_the_chosen_tag_leaves_and_with_no_contact_left_every_contact_shows_again(world: World, qtbot: QtBot) -> None:
    from tests.ui.test_ui_contacts import open_contacts, seed as seed_contacts, select_rows  # here: test_ui_contacts imports from this file

    seed_contacts(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: rows(page.tag_filter) == ["Business  (1)", "Important  (1)", "Key Customer  (1)"])
    page.tag_filter.choose("Business")  # only Rebecca Hoffman
    wait(qtbot, lambda: [str(getattr(r, "name")) for r in page.model.rows] == ["Rebecca Hoffman"])
    select_rows(page, 0)
    page._edit_selected(remove_tag=("Business",))  # noqa: SLF001 - the Tag menu's own step
    wait(qtbot, lambda: rows(page.tag_filter) == ["Business", "Important  (1)", "Key Customer  (1)"])  # the active tag stays, without a number (D-334)
    page.tag_filter.choose("")  # a second click: no filter
    wait(qtbot, lambda: page.tag_filter.tag() == "" and page.model.rowCount() == 3)
    page.reload_tags()
    wait(qtbot, lambda: rows(page.tag_filter) == ["Important  (1)", "Key Customer  (1)"])  # no filter on it: the tag with no contact goes


def tag_block(qtbot: QtBot, tag_names: list[str]) -> TagFilter:
    from chrono_mail.ui.tagchips import TagColourBook

    block = TagFilter(TagColourBook())
    qtbot.addWidget(block)
    block.resize(220, 160)
    block.show()
    block.follow([(name, number + 1) for number, name in enumerate(tag_names)])
    return block


def test_a_click_on_a_tag_turns_its_filter_on_and_a_second_click_off_with_no_all_line(qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt as QtKeys

    block = tag_block(qtbot, ["Home", "Work"])
    assert rows(block) == ["Home  (1)", "Work  (2)"]  # no "All …" line (D-334)
    chosen: list[str] = []
    block.chosen.connect(chosen.append)
    place = block.list.visualItemRect(block.list.item(1)).center()
    qtbot.mouseClick(block.list.viewport(), QtKeys.MouseButton.LeftButton, pos=place)
    assert block.tag() == "Work" and chosen == ["Work"]
    assert block.title.text() == "Tags: Work"  # the heading names the filter, also when its line is scrolled away
    assert block.list.item(1).toolTip() == "Click again to show everything" and block.list.item(0).toolTip() == ""
    qtbot.mouseClick(block.list.viewport(), QtKeys.MouseButton.LeftButton, pos=place)
    assert block.tag() == "" and chosen == ["Work", ""] and block.title.text() == "Tags"
    block.list.setFocus()
    block.list.setCurrentRow(0)
    qtbot.keyClick(block.list, QtKeys.Key.Key_Space)
    assert block.tag() == "Home"
    qtbot.keyClick(block.list, QtKeys.Key.Key_Return)
    assert block.tag() == ""


def test_the_active_line_is_blue_with_a_check_mark_also_without_the_focus(qtbot: QtBot) -> None:
    from PySide6.QtGui import QImage, QPainter, QPalette
    from PySide6.QtWidgets import QStyleOptionViewItem

    block = tag_block(qtbot, ["Home"])
    block.choose("Home")
    block.list.clearFocus()
    block.list.setProperty("focused", False)  # the list has no focus: a selection would be grey
    palette = block.list.palette()
    palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, palette.color(QPalette.ColorRole.Mid))  # the window in the background: a grey highlight
    block.list.setPalette(palette)
    index = block.list.model().index(0, 0)
    option = QStyleOptionViewItem()
    option.initFrom(block.list)
    handed: QPalette = getattr(option, "palette")  # getattr: PySide's stubs give the fields of a style option no type
    handed.setCurrentColorGroup(QPalette.ColorGroup.Inactive)  # what a background window hands the delegate
    setattr(option, "palette", handed)
    line = block.list.visualRect(index)
    setattr(option, "rect", line)  # setattr: PySide's stubs give the fields of a style option no type
    setattr(option, "widget", block.list)
    image = QImage(line.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.translate(-line.topLeft())
    block.list.itemDelegate().paint(painter, option, index)
    painter.end()
    blue = block.list.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
    assert image.pixelColor(image.width() // 2, 2) == blue  # the whole line blue: a state, never a selection that turns grey (D-334)
    text = block.list.palette().color(QPalette.ColorRole.HighlightedText)
    right = [image.pixelColor(x, y) for x in range(image.width() - 20, image.width()) for y in range(image.height())]
    assert any(abs(colour.red() - text.red()) + abs(colour.green() - text.green()) + abs(colour.blue() - text.blue()) < 60 for colour in right)  # the check mark at the right
    assert block.list.item(0).data(COUNT_ROLE) == 1  # the number is kept, only not drawn while the filter is on


def test_a_filter_turned_on_from_elsewhere_brings_its_line_into_view(qtbot: QtBot) -> None:
    block = tag_block(qtbot, [f"Tag {number:02d}" for number in range(40)])
    block.choose("Tag 39")
    rect = block.list.visualItemRect(block.list.item(39))
    assert block.list.viewport().rect().contains(rect.center())  # scrolled into view
    assert block.tag() == "Tag 39" and block.title.text() == "Tags: Tag 39"
    block.list.scrollToTop()  # the person looks at other tags
    block.follow([(f"Tag {number:02d}", number + 2) for number in range(40)])  # the tags refresh (a sync, an edit)
    assert block.list.verticalScrollBar().value() == 0  # a refresh never scrolls back to the active line


def test_a_long_active_tag_never_widens_the_sidebar_and_a_held_space_turns_the_filter_once(qtbot: QtBot) -> None:
    from PySide6.QtCore import QEvent, Qt as QtKeys
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    long_name = "L" * 100
    block = tag_block(qtbot, [long_name])
    narrow = block.minimumSizeHint().width()
    block.choose(long_name)
    assert block.title.text() == f"Tags: {long_name}" and block.minimumSizeHint().width() <= narrow  # cut with "…" in the heading, never wider
    block.choose("")
    block.list.setFocus()
    block.list.setCurrentRow(0)
    QApplication.sendEvent(block.list, QKeyEvent(QEvent.Type.KeyPress, QtKeys.Key.Key_Space, QtKeys.KeyboardModifier.NoModifier, "", False))
    for _ in range(3):
        QApplication.sendEvent(block.list, QKeyEvent(QEvent.Type.KeyPress, QtKeys.Key.Key_Space, QtKeys.KeyboardModifier.NoModifier, "", True))  # the key held: repeats
    assert block.tag() == long_name  # turned on once, the repeats ignored
