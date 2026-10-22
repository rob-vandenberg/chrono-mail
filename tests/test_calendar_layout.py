from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Entry

# --- Version ------------------------------------------------------------
__version__ = "test_calendar_layout.py 0.0.64"

# --- Version history ----------------------------------------------------
# v0.0.64: Initial tests of the layout of events in the calendar views

AMS = ZoneInfo("Europe/Amsterdam")


@dataclass
class Row:
    summary: str
    start: str
    end: str
    all_day: bool = False
    colour: str = "#336699"


def entry(summary: str, start: str, end: str, all_day: bool = False) -> Entry:
    return layout.entries_from([Row(summary, start, end, all_day)], AMS)[0]


def test_the_days_of_each_view_and_the_range_asked_for() -> None:
    anchor = date(2026, 10, 14)  # a Wednesday
    assert layout.days_of(layout.DAY, anchor) == [anchor]
    week = layout.days_of(layout.WEEK, anchor)
    assert (week[0], week[-1], len(week)) == (date(2026, 10, 12), date(2026, 10, 18), 7)
    assert layout.days_of(layout.WEEK, anchor, first_weekday=6)[0] == date(2026, 10, 11)  # a week that starts on Sunday
    work = layout.days_of(layout.WORK_WEEK, anchor)
    assert [d.day for d in work] == [12, 13, 14, 15, 16] and [d.day for d in layout.days_of(layout.WORK_WEEK, anchor, work_days=(1, 3))] == [13, 15]
    upcoming = layout.days_of(layout.UPCOMING, anchor, today=date(2026, 10, 20))
    assert (upcoming[0], upcoming[-1]) == (date(2026, 10, 20), date(2026, 10, 26))
    month = layout.days_of(layout.MONTH, anchor)
    assert len(month) == 42 and month[0] == date(2026, 9, 28) and month[-1] == date(2026, 11, 8)  # six whole weeks around October
    assert layout.range_of(layout.WEEK, anchor) == (date(2026, 10, 12), date(2026, 10, 19)) and layout.range_of(layout.DAY, anchor) == (anchor, date(2026, 10, 15))
    assert len(layout.days_of(layout.AGENDA, anchor)) == layout.AGENDA_DAYS


def test_next_and_previous_follow_the_view_and_a_month_end_is_clamped() -> None:
    anchor = date(2026, 1, 31)
    assert layout.step(layout.DAY, anchor, 1) == date(2026, 2, 1) and layout.step(layout.WEEK, anchor, -1) == date(2026, 1, 24)
    assert layout.step(layout.MONTH, anchor, 1) == date(2026, 2, 28) and layout.step(layout.MONTH, date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert layout.step(layout.MONTH, date(2026, 12, 15), 1) == date(2027, 1, 15) and layout.step(layout.MONTH, date(2026, 1, 15), -1) == date(2025, 12, 15)
    assert layout.step(layout.AGENDA, anchor, 1) == anchor + timedelta(days=layout.AGENDA_DAYS)


def test_titles() -> None:
    anchor = date(2026, 10, 14)
    assert layout.title_for(layout.MONTH, anchor, []) == "October 2026"
    week = layout.title_for(layout.WEEK, anchor, layout.days_of(layout.WEEK, anchor))
    assert week.endswith("12 – 18, 2026")  # one month: the month once
    crossing = layout.title_for(layout.WEEK, date(2026, 10, 30), layout.days_of(layout.WEEK, date(2026, 10, 30)))
    assert crossing == "Oct 26 – Nov 1, 2026"  # two months: both named
    new_year = layout.title_for(layout.WEEK, date(2026, 12, 30), layout.days_of(layout.WEEK, date(2026, 12, 30)))
    assert "2026" in new_year and "2027" in new_year  # two years: both
    assert "Wednesday" in layout.title_for(layout.DAY, anchor, [anchor]) and layout.title_for(layout.AGENDA, anchor, [anchor]).startswith("From ")


def test_entries_are_read_in_the_local_zone() -> None:
    aware = entry("Call", "2026-10-14T10:00:00+00:00", "2026-10-14T11:00:00+00:00")
    assert aware.start == datetime(2026, 10, 14, 12, tzinfo=AMS) and aware.end - aware.start == timedelta(hours=1) and not aware.all_day  # UTC shown in Amsterdam time
    floating = entry("Lunch", "2026-10-14T12:30:00", "2026-10-14T13:30:00")
    assert floating.start.hour == 12 and floating.start.utcoffset() == timedelta(hours=2)  # a time without a zone is taken as the time on the clock here
    day = entry("Holiday", "2026-12-25", "2026-12-28", all_day=True)
    assert (day.first_day, day.last_day) == (date(2026, 12, 25), date(2026, 12, 27))  # the end is the day after the last day
    broken = layout.entries_from([Row("x", "garbage", "garbage")], AMS)
    assert broken == []
    backwards = entry("Odd", "2026-10-14T12:00:00+02:00", "2026-10-14T11:00:00+02:00")
    assert backwards.end == backwards.start  # an end before the start is made a point in time
    ordered = layout.entries_from([Row("b", "2026-10-15T09:00:00+02:00", "2026-10-15T10:00:00+02:00"), Row("a", "2026-10-14T09:00:00+02:00", "2026-10-14T10:00:00+02:00")], AMS)
    assert [e.summary for e in ordered] == ["a", "b"]


def test_a_day_cuts_events_at_midnight_and_gives_overlapping_ones_columns() -> None:
    day = date(2026, 10, 14)
    entries = layout.entries_from([
        Row("Morning", "2026-10-14T09:00:00+02:00", "2026-10-14T10:30:00+02:00"),
        Row("Overlap", "2026-10-14T10:00:00+02:00", "2026-10-14T11:00:00+02:00"),
        Row("Third", "2026-10-14T10:15:00+02:00", "2026-10-14T10:45:00+02:00"),
        Row("Later", "2026-10-14T13:00:00+02:00", "2026-10-14T14:00:00+02:00"),
        Row("Short", "2026-10-14T16:00:00+02:00", "2026-10-14T16:05:00+02:00"),
        Row("Night", "2026-10-13T22:00:00+02:00", "2026-10-14T02:00:00+02:00"),
        Row("Tomorrow", "2026-10-15T01:00:00+02:00", "2026-10-15T02:00:00+02:00"),
        Row("All day", "2026-10-14", "2026-10-15", True),
    ], AMS)
    segments = {s.entry.summary: s for s in layout.day_segments(entries, day, AMS)}
    assert set(segments) == {"Morning", "Overlap", "Third", "Later", "Short", "Night"}  # all-day and other days are not here
    assert (segments["Night"].start, segments["Night"].end, segments["Night"].continues_before, segments["Night"].continues_after) == (0, 120, True, False)
    assert (segments["Morning"].start, segments["Morning"].end) == (540, 630)
    assert sorted((segments[name].column, segments[name].columns) for name in ("Morning", "Overlap", "Third")) == [(0, 3), (1, 3), (2, 3)]  # three that overlap: three columns
    assert (segments["Later"].column, segments["Later"].columns) == (0, 1) and (segments["Short"].column, segments["Short"].columns) == (0, 1)  # separate groups use the whole width
    assert segments["Short"].end - segments["Short"].start == layout.MINIMUM_MINUTES  # drawn long enough to be clicked
    assert segments["Night"].column == 0 and segments["Night"].columns == 1
    tail = layout.day_segments(entries, date(2026, 10, 13), AMS)
    assert [(s.entry.summary, s.start, s.end, s.continues_after) for s in tail] == [("Night", 1320, 1440, True)]


def test_columns_are_reused_inside_a_group() -> None:
    entries = layout.entries_from([
        Row("A", "2026-10-14T09:00:00+02:00", "2026-10-14T12:00:00+02:00"),
        Row("B", "2026-10-14T09:30:00+02:00", "2026-10-14T10:00:00+02:00"),
        Row("C", "2026-10-14T10:00:00+02:00", "2026-10-14T11:00:00+02:00"),
    ], AMS)
    placed = {s.entry.summary: (s.column, s.columns) for s in layout.day_segments(entries, date(2026, 10, 14), AMS)}
    assert placed == {"A": (0, 2), "B": (1, 2), "C": (1, 2)}  # C starts when B ends: it goes in B's column


def test_all_day_events_become_bars_in_rows() -> None:
    days = layout.days_of(layout.WEEK, date(2026, 10, 14))
    entries = layout.entries_from([
        Row("Trip", "2026-10-13", "2026-10-16", True), Row("Day", "2026-10-14", "2026-10-15", True), Row("Long", "2026-10-09", "2026-10-22", True),
        Row("Away", "2026-10-30", "2026-10-31", True), Row("Timed", "2026-10-14T10:00:00+02:00", "2026-10-14T11:00:00+02:00"),
        Row("Overnight", "2026-10-14T22:00:00+02:00", "2026-10-15T06:00:00+02:00"),
    ], AMS)
    found = {b.entry.summary: b for b in layout.bars(entries, days, include_timed_spanning=False)}
    assert set(found) == {"Trip", "Day", "Long"}
    assert (found["Trip"].first, found["Trip"].last) == (1, 3) and (found["Day"].first, found["Day"].last) == (2, 2)
    assert (found["Long"].first, found["Long"].last, found["Long"].continues_before, found["Long"].continues_after) == (0, 6, True, True)  # cut to the week, with marks
    assert found["Long"].row == 0 and found["Trip"].row == 1 and found["Day"].row == 2  # the longest starts first; overlapping ones go in other rows
    with_overnight = {b.entry.summary for b in layout.bars(entries, days, include_timed_spanning=True)}
    assert "Overnight" in with_overnight and "Timed" not in with_overnight
    assert layout.bars(entries, []) == []
    spaced = layout.bars(layout.entries_from([Row("A", "2026-10-12", "2026-10-13", True), Row("B", "2026-10-14", "2026-10-15", True)], AMS), days)
    assert [b.row for b in spaced] == [0, 0]  # side by side in time: the same row


def test_the_month_and_the_agenda() -> None:
    days = layout.days_of(layout.MONTH, date(2026, 10, 14))
    entries = layout.entries_from([
        Row("Timed", "2026-10-14T10:00:00+02:00", "2026-10-14T11:00:00+02:00"), Row("All day", "2026-10-14", "2026-10-15", True), Row("Trip", "2026-10-13", "2026-10-16", True),
        Row("Early", "2026-10-14T08:00:00+02:00", "2026-10-14T09:00:00+02:00"),
    ], AMS)
    cells = {cell.day: [e.summary for e in cell.entries] for cell in layout.month_cells(entries, days)}
    assert cells[date(2026, 10, 14)] == ["Trip", "All day", "Early", "Timed"] and cells[date(2026, 10, 13)] == ["Trip"] and cells[date(2026, 10, 17)] == []
    now = datetime(2026, 10, 14, 9, 30, tzinfo=AMS)
    agenda = layout.agenda_groups(entries, layout.days_of(layout.AGENDA, date(2026, 10, 12)), False, now)
    assert [(d.day, [e.summary for e in found]) for d, found in agenda] == [(13, ["Trip"]), (14, ["Trip", "All day", "Early", "Timed"]), (15, ["Trip"])]
    hidden = layout.agenda_groups(entries, layout.days_of(layout.AGENDA, date(2026, 10, 12)), True, now)
    assert [d.day for d, _ in hidden] == [14, 15] and "Early" not in [e.summary for e in hidden[0][1]] and "Timed" in [e.summary for e in hidden[0][1]]  # events that are over, and earlier days, are hidden
    assert layout.time_text(entry("T", "2026-10-14T10:00:00+02:00", "2026-10-14T11:00:00+02:00")) == "10:00 – 11:00" and layout.time_text(entry("D", "2026-10-14", "2026-10-15", True)) == "All day"
    assert layout.time_text(entry("P", "2026-10-14T10:00:00+02:00", "2026-10-14T10:00:00+02:00")) == "10:00"
