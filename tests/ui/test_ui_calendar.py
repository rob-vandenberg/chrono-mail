from collections.abc import Callable
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QWidget
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
__version__ = "test_ui_calendar.py 2.0.291"

# --- Version history ----------------------------------------------------
# v2.0.291: a margin at the right of the list of colours
# v2.0.290: the Event colours window has no outline around its list (the owner)
# v1.9.273: The event editor's parameters are typed (R-19)
# v1.1.235: Delete greyed for a read-only calendar, with a hover text (R-28)
# v1.1.222: the palette editor's title is Event colours, with the event editor's Calendar row
# v1.1.221: the calendars of an account share its palette and follow it at once; the editor names the account; Rename and Colour greyed when read only
# v1.1.219: the Colour row of the event editor, the palette editor, appointments redrawn in their colours at once
# v1.1.217: the wheel goes a period back or forward (month, the row of day names, Shift over the hours); a touchpad adds up
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


def turn_wheel(widget: QWidget, notch: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
    """Send a wheel turn of this many units (120 is one notch of a mouse; positive is up) to the middle of a widget."""
    middle = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(middle, widget.mapToGlobal(middle), QPoint(0, 0), QPoint(0, notch), Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_the_wheel_goes_a_month_back_or_forward_in_the_month_view(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    turn_wheel(page.month, -120)  # down: forward
    assert (page.anchor.year, page.anchor.month) == ((ANCHOR.year + (ANCHOR.month == 12)), ANCHOR.month % 12 + 1)
    turn_wheel(page.month, 120)
    turn_wheel(page.month, 120)
    assert page.anchor == layout.step(layout.MONTH, ANCHOR, -1)
    for _ in range(3):
        turn_wheel(page.month, -40)  # a touchpad: three small movements make one notch
    assert page.anchor == ANCHOR


def test_the_wheel_over_the_day_names_goes_a_week_and_over_the_hours_scrolls(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.WEEK)
    turn_wheel(page.grid.header, -120)
    assert page.anchor == ANCHOR + timedelta(days=7)
    turn_wheel(page.grid.canvas, -120)  # the hours: no other week
    assert page.anchor == ANCHOR + timedelta(days=7)
    turn_wheel(page.grid.canvas, 120, Qt.KeyboardModifier.ShiftModifier)  # Shift over the hours: a week back
    assert page.anchor == ANCHOR
    page.set_view(layout.DAY)
    turn_wheel(page.grid.header, -120)
    assert page.anchor == ANCHOR + timedelta(days=1)


def test_the_editor_has_a_colour_row_with_the_calendars_colours(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QFormLayout, QLabel
    page = open_calendar(world, qtbot)
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    form = next(item for item in editor.findChildren(QFormLayout) if item.indexOf(editor.colour) >= 0)
    row, _ = form.getWidgetPosition(editor.colour)
    label = form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
    assert isinstance(label, QLabel) and label.text() == "Colour" and form.getWidgetPosition(editor.calendar)[0] == row - 1
    assert editor.colour.text() == "Calendar colour"
    names = [action.text() for action in editor.colour_menu.actions()]
    assert names[:3] == ["Calendar colour", "Tomato", "Flamingo"] and names[-1] == "Edit colours…" and len([n for n in names if n]) == 13
    tomato = editor.colour_menu.actions()[1]
    tomato.trigger()
    assert editor.colour.text() == "Tomato" and editor.parameters().colour == "g11"
    editor.summary.setText("Coloured")
    editor._save()  # noqa: SLF001
    wait(qtbot, lambda: any(row.summary == "Coloured" for row in world.call("event.list", from_date="2026-10-01", to_date="2026-12-01").events))
    row_ = next(row for row in world.call("event.list", from_date="2026-10-01", to_date="2026-12-01").events if row.summary == "Coloured")
    assert (row_.colour_id, row_.colour) == ("g11", "#d50000")


def test_the_palette_editor_keeps_its_own_colour_and_saves_the_rest(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.paletteeditor import PaletteEditor
    page = open_calendar(world, qtbot)
    calendar = page.panel.info(calendar_id(world))
    editor = PaletteEditor(world.backend, calendar)
    qtbot.addWidget(editor)
    assert len(editor.rows) == 12 and not editor.rows[0].delete_button.isEnabled() and editor.rows[1].delete_button.isEnabled()
    (scroll,) = editor.findChildren(QScrollArea)
    assert scroll.frameShape() == QFrame.Shape.NoFrame  # no outline around the list (the owner, 2.0.290)
    assert editor.rows_layout.contentsMargins().right() >= 8  # room between the delete buttons and the scrollbar (the owner, 2.0.291)
    editor.rows[1].name.setText("Rood")
    editor.remove_row(editor.rows[2])
    editor.remove_row(editor.rows[0])  # its own colour: never
    editor.add_button.click()
    editor.rows[-1].name.setText("Geel")
    editor._save()  # noqa: SLF001
    wait(qtbot, lambda: editor.result_summary is not None)
    names = [entry.name for entry in world.call("collection.list", kind="events")[0].palette]
    assert names[:2] == ["Calendar colour", "Rood"] and "Flamingo" not in names and names[-1] == "Geel" and len(names) == 12


def test_appointments_take_their_new_colour_at_once(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    cal = calendar_id(world)
    lunch = next(row for row in world.call("event.list", from_date="2026-10-01", to_date="2026-11-01").events if row.summary == "Lunch")
    world.call("event.edit", item=lunch.item, colour="g7")  # Peacock
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.colour == "#039be5" for entry in page.entries))
    world.call("collection.edit", collection=cal, colour="#123456")
    wait(qtbot, lambda: any(entry.summary == "Dentist" and entry.colour == "#123456" for entry in page.entries))
    assert next(entry for entry in page.entries if entry.summary == "Lunch").colour == "#039be5"  # its own colour stays
    world.call("collection.set-palette", collection=cal, colours=("g7\tPeacock\t#00ffff",))
    wait(qtbot, lambda: next(entry for entry in page.entries if entry.summary == "Lunch").colour == "#00ffff")
    menu = page.panel.menu_at(page.panel.tree.visualItemRect(page.panel._entry(cal)).center())  # noqa: SLF001
    assert "Edit colours…" in [action.text() for action in menu.actions()]


def test_the_calendars_of_an_account_share_its_palette_and_a_read_only_one_is_greyed(world: World, qtbot: QtBot) -> None:
    from chrono_mail.core import LOCAL_ACCOUNT_ID
    from chrono_mail.engine.pimaccess import open_pim
    from chrono_mail.ui.paletteeditor import PaletteEditor

    page = open_calendar(world, qtbot, layout.MONTH)
    cal = calendar_id(world)
    work = world.call("collection.create", kind="events", name="Work").collection
    wait(qtbot, lambda: page.panel.info(work) is not None)
    world.call("event.add", calendar=work, summary="Review", start="2026-10-21T10:00", end="2026-10-21T11:00", colour="g7")
    wait(qtbot, lambda: any(entry.summary == "Review" and entry.colour == "#039be5" for entry in page.entries))
    world.call("collection.set-palette", collection=cal, colours=("g7\tPeacock\t#00ffff",))  # from the other calendar of the account
    wait(qtbot, lambda: any(entry.summary == "Review" and entry.colour == "#00ffff" for entry in page.entries))
    editor = PaletteEditor(world.backend, page.panel.info(work))
    qtbot.addWidget(editor)
    assert editor.windowTitle() == "Event colours"
    assert editor.calendar.currentText() == f"{getattr(page.panel.info(work), 'account_name')} — Work" and not editor.calendar.isEnabled()  # as in the event editor
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(work, read_only=True)
    wait(qtbot, lambda: bool(getattr(page.panel.info(work), "read_only", False)))
    menu = page.panel.menu_at(page.panel.tree.visualItemRect(page.panel._entry(work)).center())  # noqa: SLF001
    enabled = {action.text(): action.isEnabled() for action in menu.actions() if action.text()}
    assert (enabled["Rename…"], enabled["Colour…"], enabled["Edit colours…"], enabled["Delete…"]) == (False, False, False, False)
    delete = next(action for action in menu.actions() if action.text() == "Delete…")
    assert "untick it in the account" in delete.toolTip()
