from pathlib import Path

import pytest

from chrono_mail.engine import Engine, UsageError
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_event_reschedule.py 4.8.660"

# --- Version history ----------------------------------------------------
# v4.8.660: Initial (B-36, D-371): an appointment dragged to another time or day, its end dragged, to and from all day, one date or the whole
#           series, the refusals, Undo and Redo; a whole series keeps to the days it was dropped on (weekly, monthly, yearly), its last date, its time zone


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    return engine


def calendar(engine: Engine) -> str:
    return next(c.collection for c in call(engine, "collection.list", kind="events"))


def shown(engine: Engine, first: str = "2030-03-01", last: str = "2030-03-20") -> list[tuple[str, str, str, bool]]:
    """Each date as the calendar shows it: title, start and end on this computer's clock (without the offset), all day or not."""
    return [(row.summary, row.start[:16], row.end[:16], row.all_day) for row in call(engine, "event.list", from_date=first, to_date=last).events]


def test_an_appointment_dragged_to_another_day_and_hour_keeps_its_length(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2030-03-04T10:00", end="2030-03-04T10:45").item
    call(engine, "event.reschedule", item=item, start="2030-03-06T14:15", end="2030-03-06T15:00")
    assert shown(engine) == [("Dentist", "2030-03-06T14:15", "2030-03-06T15:00", False)]


def test_its_end_dragged_changes_only_its_end(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2030-03-04T10:00", end="2030-03-04T10:45").item
    call(engine, "event.reschedule", item=item, start="2030-03-04T10:00", end="2030-03-04T12:30")
    assert shown(engine) == [("Dentist", "2030-03-04T10:00", "2030-03-04T12:30", False)]
    holiday = call(engine, "event.add", calendar=calendar(engine), summary="Holiday", start="2030-03-10", end="2030-03-12").item
    call(engine, "event.reschedule", item=holiday, start="2030-03-10", end="2030-03-15")
    assert call(engine, "event.show", item=holiday).end == "2030-03-15"


def test_a_timed_appointment_becomes_all_day_and_back(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2030-03-04T10:00").item
    call(engine, "event.reschedule", item=item, start="2030-03-05", end="2030-03-06")
    assert shown(engine) == [("Dentist", "2030-03-05", "2030-03-06", True)]
    call(engine, "event.reschedule", item=item, start="2030-03-07T09:00", end="2030-03-07T10:00")
    assert shown(engine) == [("Dentist", "2030-03-07T09:00", "2030-03-07T10:00", False)]


def test_one_date_of_a_series_moves_alone(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Standup", start="2030-03-04T09:00", end="2030-03-04T09:15", rrule="FREQ=DAILY;COUNT=5").item
    third = call(engine, "event.list", from_date="2030-03-06").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=third, start="2030-03-06T16:00", end="2030-03-06T16:15")
    assert [start[11:] for _, start, _, _ in shown(engine)] == ["09:00", "09:00", "16:00", "09:00", "09:00"]


def test_the_whole_series_moves_by_as_much_as_the_date_dragged(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Standup", start="2030-03-04T09:00", end="2030-03-04T09:15", rrule="FREQ=DAILY;COUNT=5").item
    third = call(engine, "event.list", from_date="2030-03-06").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=third, series=True, start="2030-03-07T11:30", end="2030-03-07T12:00")
    assert [(start, end) for _, start, end, _ in shown(engine)] == [(f"2030-03-{day:02d}T11:30", f"2030-03-{day:02d}T12:00") for day in range(5, 10)]


def test_a_series_with_a_last_day_becomes_all_day_and_keeps_its_dates(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Course", start="2030-03-04T09:00", rrule="FREQ=DAILY;UNTIL=20300306T120000Z").item
    first = call(engine, "event.list", from_date="2030-03-04").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=first, series=True, start="2030-03-04", end="2030-03-05")
    assert [(start, all_day) for _, start, _, all_day in shown(engine)] == [("2030-03-04", True), ("2030-03-05", True), ("2030-03-06", True)]


def test_what_an_edit_refuses_a_drag_refuses(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2030-03-04T10:00").item
    with pytest.raises(UsageError, match="end before it starts"):
        call(engine, "event.reschedule", item=item, start="2030-03-04T10:00", end="2030-03-04T10:00")
    with pytest.raises(UsageError, match="both be dates or both be times"):
        call(engine, "event.reschedule", item=item, start="2030-03-04", end="2030-03-04T11:00")
    call(engine, "event.cancel", item=item)
    with pytest.raises(UsageError, match="cancelled"):
        call(engine, "event.reschedule", item=item, start="2030-03-05T10:00", end="2030-03-05T11:00")


def test_a_drag_is_undone_and_redone(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2030-03-04T10:00").item
    call(engine, "event.reschedule", item=item, start="2030-03-06T14:00", end="2030-03-06T15:00", undo=1)
    assert call(engine, "pim.undo-labels", section="events").undo == "Change time (1 appointment)"
    call(engine, "pim.undo", section="events")
    assert shown(engine) == [("Dentist", "2030-03-04T10:00", "2030-03-04T11:00", False)]
    call(engine, "pim.redo", section="events")
    assert shown(engine) == [("Dentist", "2030-03-06T14:00", "2030-03-06T15:00", False)]


def test_a_weekly_series_dragged_a_day_on_repeats_on_its_new_weekday(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Team", start="2030-03-04T10:00", rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=3").item  # Mondays
    second = call(engine, "event.list", from_date="2030-03-11").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=second, series=True, start="2030-03-12T10:00", end="2030-03-12T11:00")
    assert [start[:10] for _, start, _, _ in shown(engine, "2030-03-01", "2030-03-31")] == ["2030-03-05", "2030-03-12", "2030-03-19"]  # Tuesdays


def test_a_monthly_series_dragged_a_day_on_keeps_to_its_new_day_of_the_month(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Rent", start="2030-03-14", rrule="FREQ=MONTHLY;BYMONTHDAY=14;COUNT=3").item
    first = call(engine, "event.list", from_date="2030-03-14").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=first, series=True, start="2030-03-15", end="2030-03-16")
    assert [start for _, start, _, _ in shown(engine, "2030-03-01", "2030-05-31")] == ["2030-03-15", "2030-04-15", "2030-05-15"]


def test_the_days_and_months_a_rule_names_follow_the_date_dropped() -> None:
    from datetime import date

    from chrono_mail.pim.repeat import moved_by, series_first

    second_monday = "FREQ=MONTHLY;BYDAY=2MO"  # first on Monday 14 January 2030; its date of Monday 11 March dropped on Thursday 21 March
    moved = moved_by(second_monday, date(2030, 3, 11), date(2030, 3, 21))
    assert moved == "FREQ=MONTHLY;BYDAY=3TH" and series_first(moved, date(2030, 1, 14), date(2030, 3, 11), date(2030, 3, 21)) == date(2030, 1, 17)
    assert moved_by("FREQ=MONTHLY;BYDAY=-1FR", date(2030, 1, 25), date(2030, 1, 26)) == "FREQ=MONTHLY;BYDAY=-1SA"
    assert moved_by("FREQ=WEEKLY;BYDAY=MO,WE", date(2030, 1, 7), date(2030, 1, 6)) == "FREQ=WEEKLY;BYDAY=SU,TU"
    rent = moved_by("FREQ=MONTHLY;BYMONTHDAY=14", date(2030, 3, 14), date(2030, 4, 1))  # the 14th dropped on the 1st of the next month
    assert rent == "FREQ=MONTHLY;BYMONTHDAY=1" and series_first(rent, date(2030, 1, 14), date(2030, 3, 14), date(2030, 4, 1)) == date(2030, 1, 1)
    assert moved_by("FREQ=MONTHLY;BYMONTHDAY=-1", date(2030, 3, 31), date(2030, 4, 30)) == "FREQ=MONTHLY;BYMONTHDAY=-1"  # the last day stays the last day
    birthday = moved_by("FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=14", date(2031, 3, 14), date(2031, 4, 5))
    assert birthday == "FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=5" and series_first(birthday, date(2030, 3, 14), date(2031, 3, 14), date(2031, 4, 5)) == date(2030, 4, 5)
    assert moved_by("FREQ=YEARLY;BYMONTH=3;BYDAY=2MO", date(2030, 3, 11), date(2030, 4, 8)) == "FREQ=YEARLY;BYMONTH=4;BYDAY=2MO"  # the second Monday of April
    the_31st = moved_by("FREQ=MONTHLY", date(2030, 3, 14), date(2030, 3, 31))
    assert series_first(the_31st, date(2030, 2, 14), date(2030, 3, 14), date(2030, 3, 31)) == date(2030, 3, 31)  # February has no 31st


def test_a_monthly_series_dropped_on_another_weekday_of_the_month_repeats_there(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Board", start="2030-01-14T10:00", rrule="FREQ=MONTHLY;BYDAY=2MO;COUNT=4").item
    march = next(row for row in call(engine, "event.list", from_date="2030-03-01", to_date="2030-03-31").events)
    call(engine, "event.reschedule", item=item, occurrence=march.occurrence, series=True, start="2030-03-21T10:00", end="2030-03-21T11:00")
    assert [start[:10] for _, start, _, _ in shown(engine, "2030-01-01", "2030-04-30")] == ["2030-01-17", "2030-02-21", "2030-03-21", "2030-04-18"]  # third Thursdays


def test_a_series_with_a_last_date_dragged_later_keeps_every_date(engine: Engine) -> None:
    item = call(engine, "event.add", calendar=calendar(engine), summary="Course", start="2030-03-04T10:00", rrule="FREQ=WEEKLY;UNTIL=20300318T100000").item
    first = call(engine, "event.list", from_date="2030-03-04").events[0].occurrence
    call(engine, "event.reschedule", item=item, occurrence=first, series=True, start="2030-03-05T10:00", end="2030-03-05T11:00")
    assert [start[:10] for _, start, _, _ in shown(engine, "2030-03-01", "2030-03-31")] == ["2030-03-05", "2030-03-12", "2030-03-19"]  # the last one too


def test_a_dragged_appointment_keeps_its_own_time_zone(engine: Engine) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from chrono_mail.pim.times import local_zone

    item = call(engine, "event.add", calendar=calendar(engine), summary="Call", start="2030-03-04T10:00", time_zone="Pacific/Auckland").item
    later = datetime(2030, 3, 5, 10, 0, tzinfo=ZoneInfo("Pacific/Auckland")).astimezone(local_zone())  # a day on, as this computer's clock shows it
    call(engine, "event.reschedule", item=item, start=later.strftime("%Y-%m-%dT%H:%M"), end=(later + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))
    moved = call(engine, "event.show", item=item)
    assert (moved.time_zone, moved.start, moved.end) == ("Pacific/Auckland", "2030-03-05T10:00:00+13:00", "2030-03-05T11:00:00+13:00")
