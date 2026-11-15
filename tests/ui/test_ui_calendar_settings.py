from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDateEdit, QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui import weekstart
from chrono_mail.ui.calendarwidgets import block_colours, fill_colour
from chrono_mail.ui.colours import theme
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_calendar import ANCHOR, open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_settings.py 4.4.616"

# --- Version history ----------------------------------------------------
# v4.4.616: Initial: Settings > Calendar and the n weeks view (D-352)


@pytest.fixture(autouse=True)
def system_week_start() -> Iterator[None]:
    """Start week on is one choice for the whole program: every test leaves it as the system's (AGENTS.md 4.5.5)."""
    yield
    weekstart.choose("system")


def settings_ok(world: World, monkeypatch: pytest.MonkeyPatch, **choices: object) -> None:
    """Settings > Calendar set as given, then OK, as a person does."""
    def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
        for name, value in choices.items():
            field = getattr(self, name)  # getattr: the page's field of each choice the test names
            if isinstance(value, bool):
                field.setChecked(value)
            elif isinstance(value, int):
                field.setValue(value)
            else:
                field.setCurrentIndex(field.findData(value))
        self.save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    world.window.act_settings.trigger()


def test_the_calendar_page_shows_the_choices_and_ok_keeps_them(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(dialog)
    dialog.show_page("Calendar")
    assert dialog.sidebar.currentItem().text() == "Calendar" and [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())].index("Calendar") == \
        [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())].index("Tags") + 1  # between Tags and Scheduled messages
    assert (dialog.upcoming_days.value(), dialog.custom_weeks.value(), dialog.week_start.currentText()) == (7, 4, "System default")
    assert not dialog.week_numbers.isChecked() and not dialog.short_as_half_hour.isChecked() and not dialog.dim_past.isChecked()
    assert [dialog.week_start.itemText(index) for index in range(dialog.week_start.count())] == ["System default", "Saturday", "Sunday", "Monday"]
    assert (dialog.upcoming_days.minimum(), dialog.upcoming_days.maximum(), dialog.custom_weeks.minimum(), dialog.custom_weeks.maximum()) == (2, 14, 2, 6)
    dialog.upcoming_days.setValue(10)
    dialog.custom_weeks.setValue(3)
    dialog.week_numbers.setChecked(True)
    dialog.dim_past.setChecked(True)
    dialog.week_start.setCurrentIndex(dialog.week_start.findData("sunday"))
    dialog.save()
    again = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(again)
    assert (again.upcoming_days.value(), again.custom_weeks.value(), again.week_start.currentData()) == (10, 3, "sunday")
    assert again.week_numbers.isChecked() and again.dim_past.isChecked() and not again.short_as_half_hour.isChecked()


def test_n_weeks_has_today_on_top_and_is_named_by_its_weeks(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    settings_ok(world, monkeypatch, custom_weeks=3, week_start="monday")
    assert page.view_actions[layout.WEEKS].text() == "3 weeks"
    page.set_view(layout.WEEKS)
    today = date.today()
    top = today - timedelta(days=today.weekday())
    wait(qtbot, lambda: len(page.month.days) == 21 and page.month.days[0] == top)
    assert page.stack.currentWidget() is page.month and page.views_button.text() == "3 weeks" and page.month.month == 0  # no day greyed as another month
    page.step(1)
    wait(qtbot, lambda: page.month.days[0] == top + timedelta(days=21))
    page.today_button.click()
    wait(qtbot, lambda: page.month.days[0] == top)


def test_upcoming_shows_its_days_from_today_and_the_arrows_move_them(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    settings_ok(world, monkeypatch, upcoming_days=10)
    page.set_view(layout.UPCOMING)
    today = date.today()
    wait(qtbot, lambda: len(page.grid.canvas.days) == 10 and page.grid.canvas.days[0] == today)
    page.step(1)
    wait(qtbot, lambda: page.grid.canvas.days[0] == today + timedelta(days=10))


def test_week_numbers_short_events_and_past_events(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)  # Week of 14 October 2026: the seeded events are past
    assert page.grid.header.week_number == 0 and all(not entry.past for entry in page.entries)
    standup = next(segment for segment in page.grid.canvas.segments[date(2026, 10, 12)] if segment.entry.summary == "Standup")  # 15 minutes
    assert standup.end - standup.start == layout.MINIMUM_MINUTES
    settings_ok(world, monkeypatch, week_numbers=True, short_as_half_hour=True, dim_past=True)
    wait(qtbot, lambda: page.grid.header.week_number == 42)
    standup = next(segment for segment in page.grid.canvas.segments[date(2026, 10, 12)] if segment.entry.summary == "Standup")
    assert standup.end - standup.start == 30
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    own, base, dimmed = QColor(lunch.colour), QColor(theme().roles["Base"][0]), fill_colour(lunch)
    assert lunch.past and dimmed.alpha() == 255 and dimmed.red() == round((own.red() + base.red()) / 2)  # half its colour, half the background, opaque
    assert block_colours(lunch) == (dimmed, TagColourBook.text_colour(dimmed))  # its text chosen against the colour really drawn
    page.anchor = date(2026, 10, 12)
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: any("Lunch" in page.agenda.item(row).text() for row in range(page.agenda.count())))
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if "Lunch" in page.agenda.item(row).text())
    assert line.data(Qt.ItemDataRole.ForegroundRole) is None  # the theme's text colour, dimmed when drawn: readable and following the theme
    world.call("event.cancel", item=next(entry.row.item for entry in page.entries if entry.summary == "Lunch"))
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.row.cancelled for entry in page.entries))
    wait(qtbot, lambda: any("Lunch" in page.agenda.item(row).text() for row in range(page.agenda.count())))
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if "Lunch" in page.agenda.item(row).text())
    assert line.foreground().color() == QColor(theme().task_grey)  # a past cancelled event keeps its grey
    page.agenda.resize(700, 400)
    page.agenda.grab()  # drawn less bright, without an error
    page.anchor = ANCHOR
    page.set_view(layout.MONTH)
    wait(qtbot, lambda: len(page.month.days) == 42)
    assert page.month.week_numbers
    page.month.resize(900, 600)
    page.month.grab()  # drawn with the week numbers, without an error


def test_an_event_still_to_come_is_not_dimmed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    calendar = next(c.collection for c in world.call("collection.list", kind="events"))
    soon = datetime.now().replace(microsecond=0) + timedelta(days=2)
    world.call("event.add", calendar=calendar, summary="Coming", start=soon.isoformat(), end=(soon + timedelta(hours=1)).isoformat(), time_zone="UTC")
    settings_ok(world, monkeypatch, dim_past=True)
    page.set_view(layout.UPCOMING)
    wait(qtbot, lambda: any(entry.summary == "Coming" for entry in page.entries))
    assert not next(entry for entry in page.entries if entry.summary == "Coming").past


def test_start_week_on_moves_the_views_the_small_month_and_every_date_picker(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    settings_ok(world, monkeypatch, week_start="sunday")
    wait(qtbot, lambda: page.grid.canvas.days[0] == date(2026, 10, 11))  # the week of 14 October from Sunday
    assert page.mini.firstDayOfWeek() == Qt.DayOfWeek.Sunday
    picker = QDateEdit()
    qtbot.addWidget(picker)
    weekstart.follow(picker)
    assert picker.calendarWidget().firstDayOfWeek() == Qt.DayOfWeek.Sunday
    settings_ok(world, monkeypatch, week_start="saturday")
    wait(qtbot, lambda: page.grid.canvas.days[0] == date(2026, 10, 10))
    settings_ok(world, monkeypatch, week_start="monday")
    wait(qtbot, lambda: page.grid.canvas.days[0] == date(2026, 10, 12))
    assert page.mini.firstDayOfWeek() == Qt.DayOfWeek.Monday
