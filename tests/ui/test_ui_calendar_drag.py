from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_calendar import EVENT_RESCHEDULE, EventInfo, RescheduleParameters
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarview import FIRST_HOUR, CalendarPage
from chrono_mail.ui.calendarwidgets import EntryHits, GridCanvas, gutter
from chrono_mail.ui.syncmarks import WAITING

from tests.ui.test_ui_calendar import cancelled
from tests.ui.test_ui_calendar_title_in_place import place, settled, shown
from tests.ui.test_ui_pim_undo import press
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_drag.py 4.8.660"

# --- Version history ----------------------------------------------------
# v4.8.660: Initial (B-36, D-371): an appointment dragged in the hours, into the all-day row, in the all-day row, in Month; its end dragged; the
#           clock until the server answers, a refusal puts it back; the series question; Escape; a click stays a click; Ctrl+Z


def mouse(widget: QWidget, kind: QEvent.Type, global_position: QPoint, button: Qt.MouseButton, buttons: Qt.MouseButton) -> None:
    local = widget.mapFromGlobal(global_position)
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(local), QPointF(global_position), button, buttons, Qt.KeyboardModifier.NoModifier))


def drag(source: EntryHits, start: QPointF, target: QWidget, end: QPointF, release: bool = True) -> None:
    """The left button pressed at start in source, the mouse moved to end in target (the source keeps the mouse, as Qt does), and released."""
    pressed_at = source.mapToGlobal(start.toPoint())
    mouse(source, QEvent.Type.MouseButtonPress, pressed_at, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    there = target.mapToGlobal(end.toPoint())
    mouse(source, QEvent.Type.MouseMove, there, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    if release:
        mouse(source, QEvent.Type.MouseButtonRelease, there, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


def at_moment(canvas: GridCanvas, moment: datetime) -> QPointF:
    """The point in the hours of this moment, in the middle of its day's column."""
    index = canvas.days.index(moment.date())
    return QPointF(gutter(canvas) + (index + 0.5) * canvas.column_width(), (moment.hour * 60 + moment.minute) / 60 * canvas.hour_height)


def in_view(page: CalendarPage, point: QPointF) -> None:
    page.grid.area.ensureVisible(int(point.x()), int(point.y()), 10, 4 * page.grid.canvas.hour_height)


def opened(world: World, qtbot: QtBot, view: str = layout.WEEK) -> CalendarPage:
    """The calendar shown; in the hours after its own scroll to the morning, which comes just after it is shown (else it could undo in_view)."""
    page = shown(world, qtbot, view)
    if view != layout.MONTH:
        wait(qtbot, lambda: page.grid.area.verticalScrollBar().value() == FIRST_HOUR * page.grid.canvas.hour_height)
    return page


def when(world: World, page: CalendarPage, item: str) -> tuple[str, str]:
    """The appointment's start and end as this computer's clock shows them (days for an all-day one)."""
    info = world.call("event.show", item=item)
    assert isinstance(info, EventInfo)
    if info.all_day:
        return info.start, info.end
    return tuple(datetime.fromisoformat(text).astimezone(page.zone).strftime("%Y-%m-%d %H:%M") for text in (info.start, info.end))  # type: ignore[return-value]


def entry_named(page: CalendarPage, summary: str, day: int | None = None) -> Entry:
    return next(entry for entry in page.entries if entry.summary == summary and (day is None or entry.start.day == day))


def moved_in_hours(world: World, qtbot: QtBot, page: CalendarPage, summary: str, new_start: datetime) -> Entry:
    """Drags an appointment in the hours by its middle so that it starts at new_start."""
    canvas = page.grid.canvas
    rect, entry = place(canvas, summary)
    in_view(page, rect.center())
    rect, entry = place(canvas, summary)
    grabbed = canvas.place_at(rect.center())
    assert grabbed is not None and grabbed.moment is not None
    target = at_moment(canvas, new_start + (grabbed.moment - entry.start))
    drag(canvas, rect.center(), canvas, target)
    return entry


class HeldReschedule:
    """event.reschedule held back, as a server that has not answered yet: the test answers it (D-325)."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = world
        self.asked: list[tuple[RescheduleParameters, Callable[[EventInfo], None], Failure]] = []
        original = world.backend.ask

        def ask[P, R](command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
                      in_order: bool = False, waiting: bool = False, checking: bool = False) -> int:
            if command is EVENT_RESCHEDULE and isinstance(parameters, RescheduleParameters) and on_error is not None:
                self.asked.append((parameters, cast(Callable[[EventInfo], None], on_success), on_error))  # the answer of event.reschedule is an EventInfo
                return 0
            return original(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

        monkeypatch.setattr(world.backend, "ask", ask)

    def server_says_no(self, reason: str) -> None:
        _, _, on_error = self.asked.pop(0)
        on_error(ChronoMailError(reason))


def test_an_appointment_dragged_in_the_hours_lands_on_the_day_and_quarter_where_it_is_dropped(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    lunch = entry_named(page, "Lunch")
    new_start = lunch.start.replace(hour=lunch.start.hour, minute=15) + timedelta(days=1, hours=1)
    entry = moved_in_hours(world, qtbot, page, "Lunch", new_start)
    wait(qtbot, lambda: when(world, page, entry.row.item)[0] == new_start.strftime("%Y-%m-%d %H:%M"))
    assert when(world, page, entry.row.item)[1] == (new_start + (lunch.end - lunch.start)).strftime("%Y-%m-%d %H:%M")  # its length kept
    settled(qtbot, world)
    assert page.selected is not None and page.selected.row.item == entry.row.item  # it stays selected (D-325)


def test_its_bottom_edge_dragged_changes_only_its_end(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    in_view(page, rect.center())
    rect, entry = place(canvas, "Lunch")
    edge = QPointF(rect.center().x(), rect.bottom() - 2)
    new_end = entry.end + timedelta(hours=1)
    drag(canvas, edge, canvas, QPointF(edge.x(), at_moment(canvas, new_end).y()))
    wait(qtbot, lambda: when(world, page, entry.row.item) == (entry.start.strftime("%Y-%m-%d %H:%M"), new_end.strftime("%Y-%m-%d %H:%M")))


def test_a_timed_appointment_dropped_in_the_all_day_row_becomes_all_day(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    canvas, header = page.grid.canvas, page.grid.header
    rect, entry = place(canvas, "Lunch")
    in_view(page, rect.center())
    rect, entry = place(canvas, "Lunch")
    friday = entry.start.date() + timedelta(days=2)
    target = QPointF(gutter(header) + (header.days.index(friday) + 0.5) * header.column_width(), header.height() - 4)
    drag(canvas, rect.center(), header, target)
    wait(qtbot, lambda: when(world, page, entry.row.item) == (friday.isoformat(), (friday + timedelta(days=1)).isoformat()))


def test_an_all_day_appointment_moves_by_whole_days_in_its_row_and_its_end_stretches(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    header = page.grid.header
    rect, entry = place(header, "Holiday")
    column = header.column_width()
    drag(header, QPointF(rect.left() + column / 2, rect.center().y()), header, QPointF(rect.left() + column * 1.5, rect.center().y()))  # one day on
    wait(qtbot, lambda: when(world, page, entry.row.item) == ("2026-10-14", "2026-10-17"))
    settled(qtbot, world)
    wait(qtbot, lambda: place(header, "Holiday")[0].left() > rect.left())
    rect, entry = place(header, "Holiday")
    drag(header, QPointF(rect.right() - 2, rect.center().y()), header, QPointF(rect.right() + column, rect.center().y()))  # its last day one on
    wait(qtbot, lambda: when(world, page, entry.row.item) == ("2026-10-14", "2026-10-18"))


def test_in_month_an_appointment_moves_to_another_day_at_its_own_time(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot, layout.MONTH)
    month = page.month
    rect, entry = place(month, "Lunch")
    cell_width = month.width() / 7
    target = QPointF(rect.center().x() + cell_width, rect.center().y() + (month.height() - 30) / 6)  # a week and a day on
    drag(month, rect.center(), month, target)
    new_start = entry.start + timedelta(days=8)
    wait(qtbot, lambda: when(world, page, entry.row.item)[0] == new_start.strftime("%Y-%m-%d %H:%M"))


def test_in_month_the_end_of_an_all_day_bar_is_dragged_to_another_day(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot, layout.MONTH)
    month = page.month
    rect, entry = place(month, "Holiday")
    drag(month, QPointF(rect.right() - 2, rect.center().y()), month, QPointF(rect.right() + month.width() / 7, rect.center().y()))
    wait(qtbot, lambda: when(world, page, entry.row.item) == ("2026-10-13", "2026-10-17"))


def test_it_shows_at_its_new_place_with_the_clock_and_a_refusal_puts_it_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = opened(world, qtbot)
    said: list[str] = []
    page.status = said.append
    held = HeldReschedule(world, monkeypatch)
    lunch = entry_named(page, "Lunch")
    new_start = lunch.start + timedelta(days=1)
    moved_in_hours(world, qtbot, page, "Lunch", new_start)
    wait(qtbot, lambda: len(held.asked) == 1)
    shown_now = entry_named(page, "Lunch")
    assert shown_now.start == new_start and page.grid.canvas.mark_for(shown_now) == WAITING  # at once, with the clock
    assert not page._may_drag(shown_now)  # noqa: SLF001 - no second drag while the server has not answered
    held.server_says_no("The server did not take this change")
    back = entry_named(page, "Lunch")
    assert back.start == lunch.start and page.grid.canvas.mark_for(back) == "" and any("did not take" in text for text in said)
    assert when(world, page, lunch.row.item)[0] == lunch.start.strftime("%Y-%m-%d %H:%M")


def test_a_repeating_appointment_asks_this_date_or_all_and_closing_the_question_changes_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = opened(world, qtbot)
    answers = ["", "occurrence"]

    def scope(verb: str) -> str:
        return answers.pop(0)

    monkeypatch.setattr(page, "_ask_scope", scope)
    tuesday = entry_named(page, "Standup", 13)
    item = tuesday.row.item
    before = [one.start for one in page.entries if one.row.item == item]
    moved_in_hours(world, qtbot, page, "Standup", tuesday.start + timedelta(hours=2))  # the first Standup drawn is Monday's: it is that one
    assert [one.start for one in page.entries if one.row.item == item] == before  # closed: back where it was
    monday = entry_named(page, "Standup", 12)
    moved_in_hours(world, qtbot, page, "Standup", monday.start + timedelta(hours=2))
    wait(qtbot, lambda: sorted(one.start.hour - monday.start.hour for one in page.entries if one.row.item == item) == [0, 0, 0, 0, 2])  # only this date
    assert answers == []


def test_escape_ends_a_drag_and_a_click_stays_a_click(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    in_view(page, rect.center())
    rect, entry = place(canvas, "Lunch")
    canvas.setFocus()
    drag(canvas, rect.center(), canvas, QPointF(rect.center().x(), rect.center().y() + 3 * canvas.hour_height), release=False)
    assert page.drag.dragging and page.drag.preview is not None
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    assert not page.drag.dragging and page.drag.preview is None
    mouse(canvas, QEvent.Type.MouseButtonRelease, canvas.mapToGlobal(rect.center().toPoint()), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    drag(canvas, rect.center(), canvas, QPointF(rect.center().x() + 1, rect.center().y()))  # less than the drag distance: a click
    assert page.selected is not None and page.selected.row.item == entry.row.item
    settled(qtbot, world)
    assert when(world, page, entry.row.item)[0] == entry.start.strftime("%Y-%m-%d %H:%M")


def test_a_cancelled_appointment_is_not_dragged(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    cancelled(page, world, qtbot, "Lunch")
    lunch = entry_named(page, "Lunch")
    moved_in_hours(world, qtbot, page, "Lunch", lunch.start + timedelta(days=1))
    assert not page.drag.dragging
    settled(qtbot, world)
    assert when(world, page, lunch.row.item)[0] == lunch.start.strftime("%Y-%m-%d %H:%M")


def test_ctrl_z_puts_a_dragged_appointment_back(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    lunch = entry_named(page, "Lunch")
    moved_in_hours(world, qtbot, page, "Lunch", lunch.start + timedelta(days=1))
    wait(qtbot, lambda: world.window.act_undo.text() == "Undo Change time (1 appointment)")
    settled(qtbot, world)
    page.grid.canvas.setFocus()
    press(Qt.Key.Key_Z)
    wait(qtbot, lambda: when(world, page, lunch.row.item)[0] == lunch.start.strftime("%Y-%m-%d %H:%M"))
    wait(qtbot, lambda: entry_named(page, "Lunch").start == lunch.start)


def test_the_cursor_shows_the_end_of_an_appointment_that_can_be_dragged(world: World, qtbot: QtBot) -> None:
    page = opened(world, qtbot)
    canvas = page.grid.canvas
    rect, _ = place(canvas, "Lunch")
    in_view(page, rect.center())
    rect, _ = place(canvas, "Lunch")
    mouse(canvas, QEvent.Type.MouseMove, canvas.mapToGlobal(QPoint(int(rect.center().x()), int(rect.bottom() - 2))), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    assert canvas.cursor().shape() == Qt.CursorShape.SizeVerCursor
    mouse(canvas, QEvent.Type.MouseMove, canvas.mapToGlobal(rect.center().toPoint()), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
