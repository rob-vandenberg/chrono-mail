from collections.abc import Callable
from datetime import date

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.collectionpanel import collection_items
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.sections import CALENDAR
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar.py 0.0.64"

# --- Version history ----------------------------------------------------
# v0.0.64: Initial tests of the calendar section

ANCHOR = date(2026, 10, 14)  # a Wednesday


def calendar_id(world: World) -> str:
    return next(c.collection for c in world.call("collection.list", kind="events"))


def seed(world: World) -> None:
    world.call("pim.enable")
    cal = calendar_id(world)
    world.call("event.add", calendar=cal, summary="Lunch", start="2026-10-14T12:30", end="2026-10-14T13:30", time_zone="UTC", location="Café", tag=("Food",))
    world.call("event.add", calendar=cal, summary="Dentist", start="2026-10-14T12:45", end="2026-10-14T13:15", time_zone="UTC")
    world.call("event.add", calendar=cal, summary="Holiday", start="2026-10-13", end="2026-10-16")
    world.call("event.add", calendar=cal, summary="Standup", start="2026-10-12T08:00", end="2026-10-12T08:15", time_zone="UTC", rrule="FREQ=DAILY;COUNT=5")
    world.call("event.add", calendar=cal, summary="Next month", start="2026-11-03T10:00", end="2026-11-03T11:00", time_zone="UTC")


def open_calendar(world: World, qtbot: QtBot, view: str = layout.WEEK) -> CalendarPage:
    seed(world)
    started(qtbot, world)
    world.window.settings.setValue("calendar_view", view)
    world.window.switch_section(CALENDAR)
    page = world.window.pages[CALENDAR]
    assert isinstance(page, CalendarPage)
    wait(qtbot, lambda: page.panel.visible_ids() != [])
    page.anchor = ANCHOR
    page.set_view(view)
    wait(qtbot, lambda: page.entries != [])
    return page


def title(view: str, anchor: date, page: CalendarPage) -> str:
    """The title the page should show (month and day names follow the language of the system, so the test asks the same function)."""
    return layout.title_for(view, anchor, layout.days_of(view, anchor, page.first_weekday, (0, 1, 2, 3, 4), date.today()))


def summaries(page: CalendarPage) -> list[str]:
    return [entry.summary for entry in page.entries]


def test_the_week_shows_the_days_the_all_day_bars_and_the_timed_blocks(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    assert [d.day for d in page.grid.canvas.days] == [12, 13, 14, 15, 16, 17, 18] and page.title.text() == title(layout.WEEK, ANCHOR, page)
    assert sorted(summaries(page)) == ["Dentist", "Holiday", "Lunch", "Standup", "Standup", "Standup", "Standup", "Standup"]  # the five standups, none of next month
    assert [b.entry.summary for b in page.grid.header.bars] == ["Holiday"] and (page.grid.header.bars[0].first, page.grid.header.bars[0].last) == (1, 3)
    wednesday = page.grid.canvas.segments[ANCHOR]
    assert sorted((s.entry.summary, s.column, s.columns) for s in wednesday) == [("Dentist", 1, 2), ("Lunch", 0, 2), ("Standup", 0, 1)]  # Lunch and the dentist overlap: side by side
    page.grid.resize(900, 600)
    page.grid.show()
    page.grid.grab()
    names = [entry.summary for _, entry in page.grid.canvas.hits]
    assert names.count("Standup") == 5 and "Lunch" in names and "Dentist" in names


def test_clicking_an_event_selects_it_and_a_double_click_edits_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    page.grid.resize(900, 600)
    page.grid.show()
    page.grid.grab()
    rect, entry = next((r, e) for r, e in page.grid.canvas.hits if e.summary == "Lunch")
    point = QPoint(int(rect.center().x()), int(rect.center().y()))
    qtbot.mouseClick(page.grid.canvas, Qt.MouseButton.LeftButton, pos=point)
    assert page.selected is not None and page.selected.summary == "Lunch" and page.act_edit.isEnabled() and page.act_delete.isEnabled()
    page.grid.canvas.grab()
    qtbot.mouseClick(page.grid.canvas, Qt.MouseButton.LeftButton, pos=QPoint(GUTTER_X, 5))  # an empty place: nothing is selected
    assert page.selected is None and not page.act_edit.isEnabled()
    shown: list[str] = []

    def fake_exec(self: EventEditor) -> int:
        shown.append(self.summary.text())
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    QTest.mouseDClick(page.grid.canvas, Qt.MouseButton.LeftButton, pos=point)
    wait(qtbot, lambda: shown == ["Lunch"])
    assert entry.summary == "Lunch"


GUTTER_X = 5


def fake_editor(fill: Callable[[EventEditor], None]) -> Callable[[EventEditor], int]:
    def run(editor: EventEditor) -> int:
        fill(editor)
        editor._save()  # noqa: SLF001
        return 0

    return run


def test_a_double_click_on_a_free_place_makes_an_event_there_and_the_editor_changes_one(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)

    def make(editor: EventEditor) -> None:
        assert editor.summary.text() == "" and editor.start.dateTime().toString("yyyy-MM-dd HH:mm") == "2026-10-15 15:00" and editor.end.dateTime().toString("HH:mm") == "16:00"
        editor.summary.setText("Planning")
        editor.location.setText("Room 4")
        editor.reminder.setCurrentIndex(editor.reminder.findData("15"))
        editor.tags.setText("Work")

    monkeypatch.setattr(EventEditor, "exec", fake_editor(make))
    page._slot_activated(date(2026, 10, 15), 15 * 60)  # noqa: SLF001
    wait(qtbot, lambda: "Planning" in summaries(page))
    made = next(e for e in page.entries if e.summary == "Planning")
    shown = world.call("event.show", item=str(getattr(made.row, "item")))
    assert (shown.location, shown.tags, [r.minutes_before for r in shown.reminders]) == ("Room 4", ("Work",), [15]) and shown.start.startswith("2026-10-15T15:00")

    def all_day(editor: EventEditor) -> None:
        assert editor.all_day.isChecked() and editor.start.date().toString("yyyy-MM-dd") == "2026-10-17"
        editor.summary.setText("Free day")

    monkeypatch.setattr(EventEditor, "exec", fake_editor(all_day))
    page._slot_activated(date(2026, 10, 17), -1)  # noqa: SLF001
    wait(qtbot, lambda: "Free day" in summaries(page))
    free = world.call("event.show", item=str(getattr(next(e for e in page.entries if e.summary == "Free day").row, "item")))
    assert free.all_day and (free.start, free.end) == ("2026-10-17", "2026-10-18")

    def change(editor: EventEditor) -> None:
        assert editor.summary.text() == "Planning" and editor.location.text() == "Room 4" and editor.reminder.currentData() == "15" and editor.tags.text() == "Work"
        editor.summary.setText("Planning (moved)")
        editor.start.setDateTime(editor.start.dateTime().addSecs(3600))
        editor.reminder.setCurrentIndex(0)
        editor.tags.setText("")

    monkeypatch.setattr(EventEditor, "exec", fake_editor(change))
    page._entry_clicked(made)  # noqa: SLF001
    page.edit_selected()
    wait(qtbot, lambda: "Planning (moved)" in summaries(page))
    after = world.call("event.show", item=str(getattr(made.row, "item")))
    assert after.start.startswith("2026-10-15T16:00") and after.end.startswith("2026-10-15T17:00") and after.reminders == () and after.tags == ()  # the end moved with the start

    def empty(editor: EventEditor) -> None:
        editor.summary.setText("")

    monkeypatch.setattr(EventEditor, "exec", fake_editor(empty))
    page.edit_selected()
    qtbot.wait(300)
    assert "Planning (moved)" in summaries(page)  # an event needs a title: nothing was saved


def test_one_occurrence_of_a_repeating_event_is_changed_or_deleted_or_the_whole_series(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    thursday = next(e for e in page.entries if e.summary == "Standup" and e.first_day == date(2026, 10, 15))
    page._entry_clicked(thursday)  # noqa: SLF001
    asked: list[str] = []

    def scope(verb: str) -> str:
        asked.append(verb)
        return "occurrence"

    monkeypatch.setattr(page, "_ask_scope", scope)

    def move(editor: EventEditor) -> None:
        assert not editor.repeat.isEnabled() and "Occurrence" in editor.windowTitle()  # one occurrence has no rule of its own
        editor.summary.setText("Standup (late)")
        editor.start.setDateTime(editor.start.dateTime().addSecs(3 * 3600))

    monkeypatch.setattr(EventEditor, "exec", fake_editor(move))
    page.edit_selected()
    wait(qtbot, lambda: "Standup (late)" in summaries(page))
    assert summaries(page).count("Standup") == 4 and asked == ["Change"]
    friday = next(e for e in page.entries if e.summary == "Standup" and e.first_day == date(2026, 10, 16))
    page._entry_clicked(friday)  # noqa: SLF001
    page.delete_selected()
    wait(qtbot, lambda: summaries(page).count("Standup") == 3)
    assert asked == ["Change", "Delete"]
    monday = next(e for e in page.entries if e.summary == "Standup")
    page._entry_clicked(monday)  # noqa: SLF001
    monkeypatch.setattr(page, "_ask_scope", series_scope)
    page.delete_selected()
    wait(qtbot, lambda: "Standup" not in summaries(page) and "Standup (late)" not in summaries(page))


def test_a_single_event_is_deleted_after_asking(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    page._entry_clicked(next(e for e in page.entries if e.summary == "Dentist"))  # noqa: SLF001
    monkeypatch.setattr(QMessageBox, "question", no)
    page.delete_selected()
    qtbot.wait(200)
    assert "Dentist" in summaries(page)
    monkeypatch.setattr(QMessageBox, "question", yes)
    page.delete_selected()
    wait(qtbot, lambda: "Dentist" not in summaries(page))
    assert page.selected is None and not page.act_delete.isEnabled()


def series_scope(verb: str) -> str:
    return "series"


def no(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.No


def yes(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def test_the_views_navigation_the_small_calendar_and_the_search(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    page.set_view(layout.DAY)
    wait(qtbot, lambda: page.title.text() == title(layout.DAY, ANCHOR, page) and sorted(summaries(page)) == ["Dentist", "Holiday", "Lunch", "Standup"])
    page.step(1)
    wait(qtbot, lambda: page.title.text() == title(layout.DAY, date(2026, 10, 15), page))
    page.step(-1)
    page.set_view(layout.WORK_WEEK)
    wait(qtbot, lambda: [d.day for d in page.grid.canvas.days] == [12, 13, 14, 15, 16])
    page.set_view(layout.MONTH)
    wait(qtbot, lambda: page.title.text() == title(layout.MONTH, ANCHOR, page) and len(page.month.days) == 42)
    page.month.resize(900, 600)
    page.month.show()
    page.month.grab()
    texts = {entry.summary for _, entry in page.month.hits}
    assert {"Holiday", "Lunch", "Dentist", "Standup"} <= texts
    page.step(1)
    wait(qtbot, lambda: page.title.text() == title(layout.MONTH, date(2026, 11, 14), page) and "Next month" in summaries(page))
    page.today_button.click()
    wait(qtbot, lambda: page.anchor == date.today())
    page.anchor = ANCHOR
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.agenda.count() >= 4)
    items = [page.agenda.item(i).text() for i in range(page.agenda.count())]
    assert any(f"{ANCHOR.day}, {ANCHOR.year}" in text for text in items) and any("Lunch" in text and "Café" in text for text in items)
    page.past_button.setChecked(True)
    assert page.hide_past and page.agenda.count() >= 1  # past events are left out
    page.past_button.setChecked(False)
    page.set_view(layout.UPCOMING)
    wait(qtbot, lambda: len(page.grid.canvas.days) == 7 and page.grid.canvas.days[0] == date.today())
    page.set_view(layout.WEEK)
    page.mini.clicked.emit(QDate(2026, 10, 20))
    wait(qtbot, lambda: page.anchor == date(2026, 10, 20) and page.title.text() == title(layout.WEEK, date(2026, 10, 20), page))
    page.anchor = ANCHOR
    page.search.setText("lunch")
    wait(qtbot, lambda: summaries(page) == ["Lunch"])
    page.search.setText("")
    wait(qtbot, lambda: len(page.entries) == 8)
    assert world.window.settings.value("calendar_view") == layout.WEEK


def test_a_calendar_that_is_not_ticked_is_not_shown_and_the_hours_can_be_zoomed(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    item = collection_items(page.panel.tree)[0]
    item.setCheckState(0, Qt.CheckState.Unchecked)
    wait(qtbot, lambda: page.entries == [])
    item.setCheckState(0, Qt.CheckState.Checked)
    wait(qtbot, lambda: len(page.entries) == 8)
    page.grid.canvas.set_hour_height(80)
    assert page.grid.canvas.height() == 24 * 80
    page.grid.canvas.set_hour_height(5)
    assert page.grid.canvas.hour_height == 24  # not smaller than this
    page.grid.canvas.set_hour_height(999)
    assert page.grid.canvas.hour_height == 140
    page.grid.canvas.resize(900, 24 * 140)
    slot = page.grid.canvas.slot_at(page.grid.canvas.rect().center().toPointF())
    assert slot is not None and slot[1] % 30 == 0
