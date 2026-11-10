from collections.abc import Callable, Mapping
from datetime import date, datetime

import pytest
from PySide6.QtCore import QItemSelectionModel, QObject, QPoint
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_calendar import EVENT_LIST
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui import dayheadings
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.tasksview import TaskPlace, TasksPage
from tests.ui.test_ui_calendar import ANCHOR, calendar_id, open_calendar
from tests.ui.test_ui_tasks import headers, lists, open_tasks
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_new_day.py 4.4.564"

# --- Version history ----------------------------------------------------
# v4.4.564: Initial: after midnight the calendar and the tasks look as after a restart (D-322)

EVENING: datetime = datetime(2026, 10, 14, 23, 0).astimezone()  # the evening of the calendar tests' anchor, a Wednesday
NIGHT: datetime = datetime(2026, 10, 15, 0, 0, 1).astimezone()  # just past midnight
THURSDAY: date = date(2026, 10, 15)


def _clock(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> list[datetime]:
    """The local time the day watches of the calendar and the tasks read; set the first item to move it."""
    now = [moment]
    monkeypatch.setattr(dayheadings, "local_now", lambda: now[0])
    return now


def _calendar_at_evening(world: World, qtbot: QtBot, view: str) -> CalendarPage:
    """The calendar of the seeded week on its Wednesday evening (the window was made before the clock was set: a look sets its day)."""
    page = open_calendar(world, qtbot, layout.WEEK)  # Upcoming starts at today: it shows the seeded week only once the clock is on it
    page.day_watch.look()
    wait(qtbot, lambda: page._day_shown == ANCHOR)  # noqa: SLF001
    page.set_view(view)
    wait(qtbot, lambda: _today_shown(page, view) == ANCHOR and any(entry.summary == "Lunch" for entry in page.entries))
    return page


def _tasks_at_evening(world: World, qtbot: QtBot, tasks: int) -> TasksPage:
    page = open_tasks(world, qtbot, tasks)
    page.day_watch.look()
    wait(qtbot, lambda: page._day_shown == EVENING.date().isoformat())  # noqa: SLF001
    return page


def _today_shown(page: CalendarPage, view: str) -> date | None:
    """The day the open view marks as today."""
    if view == layout.MONTH:
        return page.month.today
    if view == layout.AGENDA:
        titles = [page.agenda.item(row).text() for row in range(page.agenda.count())]
        marked = next((text for text in titles if text.endswith("— Today")), "")
        return next((day for day in page.days() if marked.startswith(f"{day.strftime('%A')}, {day.strftime('%B')} {day.day},")), None)
    assert page.grid.header.today == page.grid.canvas.today
    return page.grid.canvas.today


@pytest.mark.parametrize("view", [layout.DAY, layout.WEEK, layout.UPCOMING, layout.MONTH, layout.AGENDA])
def test_after_midnight_a_calendar_on_today_shows_the_new_today(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, view: str) -> None:
    now = _clock(monkeypatch, EVENING)
    page = _calendar_at_evening(world, qtbot, view)
    assert page.anchor == ANCHOR and _today_shown(page, view) == ANCHOR
    now[0] = NIGHT
    page.day_watch.look()
    wait(qtbot, lambda: _today_shown(page, view) == THURSDAY)
    assert page.anchor == THURSDAY and THURSDAY in page.days()  # as after a restart: the calendar opens on today
    assert page.days()[0] == (THURSDAY if view in (layout.DAY, layout.UPCOMING, layout.AGENDA) else page.days()[0])


def test_after_midnight_a_calendar_on_another_day_stays_there(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch, EVENING)
    page = _calendar_at_evening(world, qtbot, layout.WEEK)
    page.goto(date(2026, 11, 3))  # the person went to another week
    wait(qtbot, lambda: [entry.summary for entry in page.entries] == ["Next month"])
    now[0] = NIGHT
    page.day_watch.look()
    wait(qtbot, lambda: page.grid.canvas.today == THURSDAY)
    assert page.anchor == date(2026, 11, 3) and [entry.summary for entry in page.entries] == ["Next month"]


def test_a_calendar_load_that_fails_after_midnight_says_nothing_and_is_asked_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch, EVENING)
    page = _calendar_at_evening(world, qtbot, layout.WEEK)
    complaints: list[str] = []

    def complain(error: Exception) -> None:
        complaints.append(str(error))

    monkeypatch.setattr(page, "_failed", complain)
    real_call = world.backend.call
    refused: list[str] = []

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == EVENT_LIST.id and on_error is not None:
            refused.append(command)
            on_error(ChronoMailError("The index could not be read"))
            return 0
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    now[0] = NIGHT
    page.day_watch.look()
    assert refused == [EVENT_LIST.id] and complaints == []  # asked, refused, and nothing said
    assert page.grid.canvas.today == ANCHOR
    monkeypatch.setattr(world.backend, "call", real_call)
    page.day_watch.look()  # the next look, the same day
    wait(qtbot, lambda: page.grid.canvas.today == THURSDAY)
    assert complaints == []


def _seed_tasks(world: World, due: Mapping[str, str]) -> dict[str, str]:
    world.call("pim.enable")
    book = lists(world)
    return {summary: world.call("task.add", list=book, summary=summary, due=day).item for summary, day in due.items()}


def test_after_midnight_the_tasks_are_grouped_by_the_new_day_and_the_selection_stays(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch, EVENING)
    items = _seed_tasks(world, {"Call the plumber": "2026-10-14", "Water the plants": "2026-10-15", "Book the hotel": "2026-10-16"})
    page = _tasks_at_evening(world, qtbot, 3)
    assert headers(page) == ["Today", "Tomorrow", "This week"]
    position = page.model.position_of(items["Water the plants"])
    page.list.selectionModel().setCurrentIndex(page.model.index(position), QItemSelectionModel.SelectionFlag.ClearAndSelect)
    now[0] = NIGHT
    page.day_watch.look()
    wait(qtbot, lambda: headers(page) == ["Overdue", "Today", "Tomorrow"])
    assert page._selected_items() == [items["Water the plants"]]  # noqa: SLF001


def test_after_midnight_the_task_at_the_top_of_the_list_stays_there(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch, EVENING)
    due = {"Book the hotel": "2026-10-16"} | {f"Read chapter {number}": "2026-10-20" for number in range(1, 61)}
    _seed_tasks(world, due)
    page = _tasks_at_evening(world, qtbot, len(due))
    page.list.verticalScrollBar().setValue(page.list.verticalScrollBar().maximum() // 2)
    top: Callable[[], str] = lambda: next(payload.item for _, payload in page.model.entries[page.list.indexAt(QPoint(0, 0)).row():] if isinstance(payload, TaskPlace))
    before = top()
    assert headers(page) == ["This week"]
    now[0] = NIGHT
    page.day_watch.look()
    wait(qtbot, lambda: headers(page) == ["Tomorrow", "This week"])  # a heading came above: every row moved one down
    assert top() == before


def test_after_midnight_the_agenda_keeps_its_selected_event_and_the_line_at_its_top(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch, EVENING)
    world.call("pim.enable")
    world.call("event.add", calendar=calendar_id(world), summary="Walk the dog", start="2026-10-14T07:00", end="2026-10-14T07:30", time_zone="UTC", rrule="FREQ=DAILY;COUNT=40")
    page = _calendar_at_evening(world, qtbot, layout.AGENDA)
    agenda = page.agenda
    wait(qtbot, lambda: agenda.verticalScrollBar().maximum() > 0)
    agenda.verticalScrollBar().setValue(agenda.verticalScrollBar().maximum() // 2)
    first = agenda.itemAt(0, 0)
    assert first is not None
    top_text = first.text()
    chosen = next(agenda.item(row) for row in range(agenda.row(first), agenda.count()) if agenda.item(row).text().strip().endswith("(Calendar)"))
    chosen_text = chosen.text()
    agenda.setCurrentItem(chosen)  # the person clicks an event in sight
    now[0] = NIGHT
    page.day_watch.look()
    wait(qtbot, lambda: _today_shown(page, layout.AGENDA) == THURSDAY)
    first, current = agenda.itemAt(0, 0), agenda.currentItem()
    assert first is not None and current is not None and first.text() == top_text and current.text() == chosen_text
    assert page.selected is not None and page.selected.summary == "Walk the dog"
    page.set_view(layout.WEEK)
    page.set_view(layout.AGENDA)  # another view and back: no event of an earlier visit is highlighted, as before
    wait(qtbot, lambda: page._day_shown == THURSDAY and agenda.count() > 0 and agenda.currentItem() is None)  # noqa: SLF001
