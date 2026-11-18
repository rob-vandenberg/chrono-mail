from datetime import date, timedelta
from zoneinfo import ZoneInfo

from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_calendar import OccurrenceInfo
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarwidgets import MonthView

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_month_rows.py 4.4.620"

# --- Version history ----------------------------------------------------
# v4.4.620: Initial tests: a day's timed events start below that day's all-day bars, not below the deepest bar of its week (the owner)


def entry(summary: str, start: str, end: str, all_day: bool = False) -> Entry:
    row = OccurrenceInfo(summary, "account", "calendar", "Personal", "#336699", summary, summary, start, end, all_day, "", False, False, "", "CONFIRMED", True, ())
    return layout.entries_from([row], ZoneInfo("Europe/Amsterdam"))[0]


def drawn_tops(qtbot: QtBot, entries: list[Entry]) -> dict[str, float]:
    """Draws one week (Monday 16 November 2026) and gives the top of each event as drawn, by its title."""
    view = MonthView()
    qtbot.addWidget(view)
    view.resize(700, 400)
    monday = date(2026, 11, 16)
    view.set_data([monday + timedelta(days=offset) for offset in range(7)], entries, 0, monday, 0)
    view.grab()  # paints, which places the events
    return {hit_entry.summary: rect.top() for rect, hit_entry in view.hits}


def test_a_day_without_bars_starts_its_timed_events_at_the_top_while_another_day_has_three_bars(qtbot: QtBot) -> None:
    tops = drawn_tops(qtbot, [
        entry("Day off", "2026-11-16", "2026-11-17", all_day=True),
        entry("Paper bin", "2026-11-16", "2026-11-17", all_day=True),
        entry("Birthday", "2026-11-16", "2026-11-17", all_day=True),
        entry("Doctor", "2026-11-17T12:00:00", "2026-11-17T13:00:00"),
    ])
    assert tops["Doctor"] == tops["Birthday"]  # the first row (bars are sorted by name), on Tuesday, not below Monday's three bars


def test_a_day_starts_its_timed_events_below_its_own_bars_and_a_bar_over_several_days_counts_on_each(qtbot: QtBot) -> None:
    tops = drawn_tops(qtbot, [
        entry("Discounts", "2026-11-16", "2026-11-20", all_day=True),  # Monday to Thursday, the first row
        entry("Day off", "2026-11-16", "2026-11-17", all_day=True),
        entry("Paper bin", "2026-11-16", "2026-11-17", all_day=True),
        entry("Green bin", "2026-11-17", "2026-11-18", all_day=True),  # Tuesday, the second row
        entry("Doctor", "2026-11-17T12:00:00", "2026-11-17T13:00:00"),
        entry("Shopping", "2026-11-18T13:00:00", "2026-11-18T14:00:00"),
    ])
    assert tops["Doctor"] == tops["Paper bin"]  # Tuesday: below Discounts and Green bin, the third row
    assert tops["Shopping"] == tops["Day off"]  # Wednesday: only Discounts above it, the second row
