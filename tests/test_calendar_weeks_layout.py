from datetime import date, timedelta
from zoneinfo import ZoneInfo

from chrono_mail.engine.commands_calendar import OccurrenceInfo
from chrono_mail.ui import calendarlayout as layout

# --- Version ------------------------------------------------------------
__version__ = "test_calendar_weeks_layout.py 4.4.616"

# --- Version history ----------------------------------------------------
# v4.4.616: Initial: the n weeks view, Upcoming's number of days, week numbers and the length of a short event (D-352)

AMS = ZoneInfo("Europe/Amsterdam")
LAST_OF_NOVEMBER = date(2026, 11, 30)  # a Monday: Month shows it in its bottom row


def test_n_weeks_puts_the_week_of_the_day_on_top_where_month_puts_it_at_the_bottom() -> None:
    weeks = layout.days_of(layout.WEEKS, LAST_OF_NOVEMBER, first_weekday=0, weeks=4)
    assert (len(weeks), weeks[0], weeks[-1]) == (28, LAST_OF_NOVEMBER, date(2026, 12, 27))  # the week of the 30th is the top row, three weeks below it
    month = layout.days_of(layout.MONTH, LAST_OF_NOVEMBER, first_weekday=0)
    assert month.index(LAST_OF_NOVEMBER) // 7 == 5  # in Month the same day is in the last of six rows
    sunday_first = layout.days_of(layout.WEEKS, LAST_OF_NOVEMBER, first_weekday=6, weeks=3)
    assert (len(sunday_first), sunday_first[0]) == (21, date(2026, 11, 29))  # a week that starts on Sunday: the 29th starts the top row
    assert layout.range_of(layout.WEEKS, LAST_OF_NOVEMBER, weeks=2) == (LAST_OF_NOVEMBER, date(2026, 12, 14))


def test_the_arrows_move_n_weeks_by_their_weeks_and_upcoming_by_its_days() -> None:
    assert layout.step(layout.WEEKS, LAST_OF_NOVEMBER, 1, weeks=3) == LAST_OF_NOVEMBER + timedelta(days=21)
    assert layout.step(layout.WEEKS, LAST_OF_NOVEMBER, -1, weeks=5) == LAST_OF_NOVEMBER - timedelta(days=35)
    assert layout.step(layout.UPCOMING, LAST_OF_NOVEMBER, 1, upcoming_days=10) == date(2026, 12, 10)
    assert layout.step(layout.WEEK, LAST_OF_NOVEMBER, 1, upcoming_days=10, weeks=3) == date(2026, 12, 7)  # the other views keep their step


def test_upcoming_shows_its_number_of_days_from_the_day_shown() -> None:
    days = layout.days_of(layout.UPCOMING, LAST_OF_NOVEMBER, upcoming_days=10)
    assert (len(days), days[0], days[-1]) == (10, LAST_OF_NOVEMBER, date(2026, 12, 9))
    assert len(layout.days_of(layout.UPCOMING, LAST_OF_NOVEMBER)) == layout.UPCOMING_DAYS == 7


def test_a_row_has_the_iso_week_number_of_its_thursday() -> None:
    monday_row = layout.days_of(layout.WEEK, LAST_OF_NOVEMBER, first_weekday=0)
    sunday_row = layout.days_of(layout.WEEK, LAST_OF_NOVEMBER, first_weekday=6)  # Sunday the 29th to Saturday 5 December
    assert layout.week_number(monday_row) == layout.week_number(sunday_row) == 49
    assert layout.week_number([date(2027, 1, 1)]) == 53  # one day of Day: its own ISO week (2026 has 53)


def test_a_short_event_is_drawn_as_long_as_chosen() -> None:
    row = OccurrenceInfo(item="i", account="a", collection="c", calendar="Work", colour="#336699", uid="u", summary="Short", start="2026-10-14T16:00:00+02:00",
                         end="2026-10-14T16:05:00+02:00", all_day=False, location="", repeats=False, changed=False, occurrence="", status="CONFIRMED",
                         busy=True, tags=())
    entries = layout.entries_from([row], AMS)
    usual = layout.day_segments(entries, date(2026, 10, 14), AMS)[0]
    half_hour = layout.day_segments(entries, date(2026, 10, 14), AMS, layout.SHORT_AS_HALF_HOUR)[0]
    assert usual.end - usual.start == layout.MINIMUM_MINUTES == 25 and half_hour.end - half_hour.start == 30
