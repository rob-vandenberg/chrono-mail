from collections.abc import Callable
import dataclasses
from datetime import date

import pytest
from PySide6.QtCore import QDate, QPoint, Qt, QTime
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_calendar import EVENT_SHOW, EventInfo, ReminderInfo
from chrono_mail.engine.commands_calendar import ItemParameters as EventItem
from chrono_mail.engine.commands_reminders import ReminderNote
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import one_line
from chrono_mail.ui.eventattendees import AttendeesDialog, Person
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.eventrepeat import RepeatDialog

from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_appointment_window.py 4.4.576"

# --- Version history ----------------------------------------------------
# v4.4.576: An appointment opens ready to change; one of a read-only calendar only to read; a repeating one asks when opened; the Add tag… list (the owner, D-329)
# v4.4.536: Reminders (round 4)
# v4.4.535: Initial version: read-only with Edit, attendees, tags, location (round 4)


def lunch(world: World, qtbot: QtBot, page: CalendarPage) -> EventInfo:
    entry = next(entry for entry in page.entries if entry.summary == "Lunch")
    shown: list[EventInfo] = []
    world.backend.ask(EVENT_SHOW, EventItem(entry.row.item), shown.append, lambda error: None)
    wait(qtbot, lambda: bool(shown))
    return shown[0]


def double_click_lunch(qtbot: QtBot, page: CalendarPage) -> None:
    page.grid.resize(900, 600)
    page.grid.show()
    page.grid.grab()
    rect, _entry = next((r, e) for r, e in page.grid.canvas.hits if e.summary == "Lunch")
    QTest.mouseDClick(page.grid.canvas, Qt.MouseButton.LeftButton, pos=QPoint(int(rect.center().x()), int(rect.center().y())))


def test_a_double_click_opens_a_saved_appointment_ready_to_change(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    seen: list[tuple[bool, bool, str, bool, bool]] = []

    def fake_exec(self: EventEditor) -> int:
        seen.append((self.summary.isReadOnly(), self.save_button.isHidden(), self.cancel_button.text(), self.busy.isEnabled(), self.attendees_button.isEnabled()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    double_click_lunch(qtbot, page)
    wait(qtbot, lambda: len(seen) == 1)
    assert seen[0] == (False, False, "Cancel", True, True)  # no reading step first (the owner)


def test_an_appointment_of_a_read_only_calendar_opens_only_to_read_with_the_reason(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.eventeditor import READ_ONLY_REASON

    page = open_calendar(world, qtbot)
    event = lunch(world, qtbot, page)
    calendars = [dataclasses.replace(info, read_only=True) for info in page._calendars()]  # noqa: SLF001
    editor = EventEditor(world.backend, calendars, [], event, event.collection, reading=True)
    qtbot.addWidget(editor)
    assert editor.summary.isReadOnly() and editor.save_button.isHidden() and editor.cancel_button.text() == "Close"
    assert editor.result_label.text() == READ_ONLY_REASON


def answering(answers: list[str]) -> "Callable[[str], str]":
    """The question this date or all of them, answered in turn from the list."""

    def asked(verb: str) -> str:
        return answers.pop(0)

    return asked


def test_a_repeating_appointment_asks_when_it_is_opened(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    opened: list[str] = []

    def fake_exec(self: EventEditor) -> int:
        opened.append(self.parameters().occurrence)
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    answers = ["", "series", "occurrence"]
    monkeypatch.setattr(page, "_ask_scope", answering(answers))
    standup = next(entry for entry in page.entries if entry.summary == "Standup")
    page._entry_activated(standup)  # noqa: SLF001 - the question closed: nothing opens
    page._entry_activated(standup)  # noqa: SLF001 - all of them: the series
    page._entry_activated(standup)  # noqa: SLF001 - this one: its date
    wait(qtbot, lambda: len(opened) == 2)
    assert sorted(opened) == sorted(["", standup.row.occurrence])  # the series and that date (the answers may come in either order)


def test_the_attendees_are_one_per_line_with_the_name_when_known(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    editor.set_people([Person("sam@example.org", "Sam Lee", "sam@example.org"), Person("kim@example.org", "", "kim@example.org")])
    assert [editor.attendees.item(row).text() for row in range(editor.attendees.count())] == ["Sam Lee", "kim@example.org"]
    assert editor.parameters().attendee == ("sam@example.org", "kim@example.org")  # the name shown changes nothing in the event


def test_the_attendee_dialog_adds_removes_and_refuses_what_is_no_address(world: World, qtbot: QtBot) -> None:
    dialog = AttendeesDialog(world.backend, [Person("sam@example.org", "Sam Lee", "sam@example.org")])
    qtbot.addWidget(dialog)
    assert not dialog.remove_button.isEnabled()
    dialog.address.setText("nobody")
    assert not dialog.add_typed() and dialog.address.text() == "nobody" and len(dialog.people) == 1
    dialog.address.setText("Kim Ray <kim@example.org>, SAM@example.org")
    assert dialog.add_typed() and dialog.address.text() == ""
    assert [person.shown for person in dialog.people] == ["Sam Lee", "Kim Ray"]  # an address already there comes only once
    dialog.list.setCurrentRow(0)
    dialog.remove_chosen()
    assert [person.address for person in dialog.people] == ["kim@example.org"]


def test_enter_in_the_attendee_field_adds_and_never_closes_the_dialog(world: World, qtbot: QtBot) -> None:
    dialog = AttendeesDialog(world.backend, [])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.address.setFocus()
    dialog.address.setText("kim@example.org")
    QTest.keyClick(dialog.address, Qt.Key.Key_Return)
    assert dialog.isVisible() and [person.address for person in dialog.people] == ["kim@example.org"]


def test_the_add_tag_list_offers_only_the_tags_it_does_not_have(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    event = lunch(world, qtbot, page)
    editor = EventEditor(world.backend, page._calendars(), page._tags_known, event, event.collection)  # noqa: SLF001
    qtbot.addWidget(editor)
    editor.choose_tag("From elsewhere", True)
    editor._fill_tag_menu()  # noqa: SLF001
    assert "From elsewhere" not in [action.text() for action in editor.tag_menu.actions()]
    editor.tag_box.select("From elsewhere")
    editor.remove_tag.click()
    assert "From elsewhere" not in editor.parameters().tag


def test_enter_in_the_location_gives_a_new_line_and_never_saves(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    editor.show()
    editor.summary.setText("Visit")
    editor.location.setFocus()
    QTest.keyClicks(editor.location, "Room 4")
    QTest.keyClick(editor.location, Qt.Key.Key_Return)
    QTest.keyClicks(editor.location, "Floor 2")
    assert editor.isVisible() and editor.result_label.text() == ""
    assert editor.parameters().location == "Room 4\nFloor 2"


def test_a_location_of_several_lines_is_drawn_on_one_line() -> None:
    assert one_line("Room 4\n\n Floor 2 \n") == "Room 4, Floor 2"


def new_editor(world: World, qtbot: QtBot, page: CalendarPage, start: str, end: str) -> EventEditor:
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world), start=start, end=end)  # noqa: SLF001
    qtbot.addWidget(editor)
    return editor


def test_the_repeat_list_follows_the_date_and_a_chosen_line_goes_along(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T10:00", "2026-11-10T11:00")
    labels = [editor.repeat.itemText(row) for row in range(editor.repeat.count())]
    assert labels == ["Does not repeat", "Daily", "Weekly on Tuesday", "Monthly on the second Tuesday", "Annually on November 10", "Every weekday (Monday to Friday)", "Custom…"]
    editor.repeat.setCurrentIndex(2)
    editor.start_date.setDate(QDate(2026, 11, 11))
    assert editor.repeat.currentText() == "Weekly on Wednesday" and editor.parameters().rrule == "FREQ=WEEKLY;BYDAY=WE"


def test_a_rule_said_in_other_words_stays_as_the_event_has_it_and_another_gets_its_own_line(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    event = lunch(world, qtbot, page)
    weekly = EventEditor(world.backend, page._calendars(), [], dataclasses.replace(event, rrule="FREQ=WEEKLY"), event.collection)  # noqa: SLF001
    custom = EventEditor(world.backend, page._calendars(), [], dataclasses.replace(event, rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=6"), event.collection)  # noqa: SLF001
    qtbot.addWidget(weekly)
    qtbot.addWidget(custom)
    assert weekly.repeat.currentText().startswith("Weekly on ") and weekly.parameters().rrule == "FREQ=WEEKLY"
    assert custom.repeat.currentText() == "Every 2 weeks on Monday, Wednesday, 6 times"


def test_a_last_day_written_in_the_other_form_is_no_change_until_all_day_changes(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    event = lunch(world, qtbot, page)
    rule = "FREQ=DAILY;UNTIL=20301201"  # a date on an event with a time: other programs write it so
    editor = EventEditor(world.backend, page._calendars(), [], dataclasses.replace(event, rrule=rule), event.collection)  # noqa: SLF001
    qtbot.addWidget(editor)
    assert editor.parameters().rrule == rule


def test_custom_gives_the_chosen_rule_and_closing_it_leaves_the_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T10:00", "2026-11-10T11:00")
    custom_row = editor.repeat.count() - 1
    def closed(self: RepeatDialog) -> int:
        return 0

    monkeypatch.setattr(RepeatDialog, "exec", closed)
    editor.repeat.setCurrentIndex(custom_row)
    editor.repeat.activated.emit(custom_row)
    assert editor.repeat.currentText() == "Does not repeat"

    def biweekly(self: RepeatDialog) -> int:
        self.every.setValue(2)
        self.ends_after.setChecked(True)
        self.count.setValue(4)
        return 1

    monkeypatch.setattr(RepeatDialog, "exec", biweekly)
    editor.repeat.setCurrentIndex(custom_row)
    editor.repeat.activated.emit(custom_row)
    assert editor.parameters().rrule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU;COUNT=4"
    assert editor.repeat.currentText() == "Every 2 weeks on Tuesday, 4 times"


def test_the_custom_window_greys_what_does_not_apply(qtbot: QtBot) -> None:
    dialog = RepeatDialog("", date(2026, 11, 10), False)
    qtbot.addWidget(dialog)
    assert dialog.days["TU"].isChecked() and dialog.days["TU"].isEnabled() and not dialog.count.isEnabled() and not dialog.end_date.isEnabled()
    dialog.unit.setCurrentIndex(dialog.unit.findData("MONTHLY"))
    dialog.ends_on.setChecked(True)
    assert not dialog.days["TU"].isEnabled() and dialog.end_date.isEnabled()


def test_one_day_has_one_date_and_several_days_a_second(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T10:00", "2026-11-10T11:00")
    assert editor.end_date.isHidden() and not editor.several_days.isChecked()
    editor.several_days.setChecked(True)
    assert not editor.end_date.isHidden() and editor.end_date.date() == QDate(2026, 11, 11)
    editor.several_days.setChecked(False)
    assert editor.end_date.isHidden() and editor.parameters().end == "2026-11-10T11:00"


def test_moving_the_start_moves_the_end_by_the_same_amount_also_after_the_end_was_changed(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T10:00", "2026-11-10T11:00")
    editor.end_time.setTime(QTime(12, 0))
    editor.start_time.setTime(QTime(11, 0))
    assert editor.parameters().end == "2026-11-10T13:00"
    editor.start_date.setDate(QDate(2026, 11, 12))
    assert editor.parameters().end == "2026-11-12T13:00"


def test_a_start_moved_past_midnight_shows_the_second_date_and_moved_back_hides_it(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T22:00", "2026-11-10T23:00")
    editor.start_time.setTime(QTime(23, 30))
    assert editor.parameters().end == "2026-11-11T00:30" and not editor.end_date.isHidden()
    editor.start_time.setTime(QTime(22, 0))
    assert editor.parameters().end == "2026-11-10T23:00" and editor.end_date.isHidden()


def test_new_starts_all_day_on_the_date_in_view_and_taking_the_tick_away_gives_an_hour(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    seen: list[tuple[bool, bool, str, str]] = []

    def fake_exec(self: EventEditor) -> int:
        day = self.parameters()
        hidden = self.start_time.isHidden()
        self.all_day.setChecked(False)
        seen.append((hidden, self.end_date.isHidden(), str(day.start), str(self.parameters().end)))
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    page.new_item()
    day = page.anchor.isoformat()
    assert seen == [(True, True, day, f"{day}T10:00")]


def test_a_double_click_in_the_day_view_starts_at_that_time_with_all_day_off(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    seen: list[tuple[bool, str]] = []

    def fake_exec(self: EventEditor) -> int:
        seen.append((self.all_day.isChecked(), str(self.parameters().start)))
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    page._slot_activated(page.anchor, 14 * 60)  # noqa: SLF001
    page._slot_activated(page.anchor, -1)  # noqa: SLF001
    assert seen == [(False, f"{page.anchor.isoformat()}T14:00"), (True, page.anchor.isoformat())]


def test_reminders_are_rows_of_a_kind_a_number_and_a_unit_at_most_five_and_each_can_go(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    editor = new_editor(world, qtbot, page, "2026-11-10T10:00", "2026-11-10T11:00")
    assert editor.parameters().reminder == ()
    for _ in range(5):
        editor.reminders.add_button.click()
    assert not editor.reminders.add_button.isEnabled() and len(editor.reminders.rows) == 5
    row = editor.reminders.rows[1]
    row.kind.setCurrentIndex(1)
    row.number.setValue(2)
    row.unit.setCurrentIndex(row.unit.findText("hours"))
    editor.reminders.rows[0].remove.click()
    assert editor.reminders.add_button.isEnabled()
    assert editor.parameters().reminder == ("email:120", "15", "15", "15")


def test_a_reminder_left_alone_stays_whole_and_one_from_elsewhere_is_shown_as_it_is(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    event = lunch(world, qtbot, page)
    kept = (ReminderInfo(120, "", "DISPLAY", False), ReminderInfo(30, "", "DISPLAY", True), ReminderInfo(None, "2030-01-01T09:00:00", "DISPLAY", False))
    editor = EventEditor(world.backend, page._calendars(), [], dataclasses.replace(event, reminders=kept), event.collection)  # noqa: SLF001
    qtbot.addWidget(editor)
    rows = editor.reminders.rows
    assert (rows[0].number.value(), rows[0].unit.currentText()) == (2, "hours")
    assert rows[1].fixed.text() == "30 minutes before the end" and rows[2].fixed.text() == "At 2030-01-01 09:00"
    assert editor.parameters().reminder == ()  # nothing changed, nothing sent
    rows[0].number.setValue(3)
    assert editor.parameters().reminder == ("180", "keep:1", "keep:2")


def test_a_reminder_goes_to_the_tray_with_its_time_and_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    told: list[tuple[str, str]] = []

    def tray(title: str, text: str) -> bool:
        told.append((title, text))
        return True

    monkeypatch.setattr(world.window.tray, "notify", tray)
    world.window.reminders.show_note(ReminderNote("Dentist", "2026-11-10 10:00", "Room 4"))
    assert told == [("Reminder: Dentist", "2026-11-10 10:00\nRoom 4")]
