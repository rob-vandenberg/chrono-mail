from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from chrono_mail.pim.ical import (
    Attendee,
    Event,
    EventSeries,
    Reminder,
    Task,
    complete_task,
    events_to_text,
    occurrences,
    parse_events,
    parse_tasks,
    reopen_task,
    tasks_to_text,
)

# --- Version ------------------------------------------------------------
__version__ = "test_pim_ical.py 3.6.356"

# --- Version history ----------------------------------------------------
# v3.6.356: an event kept with a length, or with no end written, keeps its end when only its start changes (CR-58)
# v1.1.218: an event every second costs only what it shows; the limit
# v0.0.58: Initial iCalendar tests: events, tasks, reminders, recurrence, hostile input

AMS = ZoneInfo("Europe/Amsterdam")

WINDOWS_ZONE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTIMEZONE
TZID:W. Europe Standard Time
BEGIN:STANDARD
DTSTART:16011028T030000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010325T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:abc@example
DTSTAMP:20261001T100000Z
DTSTART;TZID=W. Europe Standard Time:20261012T090000
DTEND;TZID=W. Europe Standard Time:20261012T100000
RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO
EXDATE;TZID=W. Europe Standard Time:20261026T090000
SUMMARY:Weekly meeting
DESCRIPTION:Line one\\nLine two\\, with a comma
LOCATION:Room 5
CATEGORIES:Work,Team
ORGANIZER;CN=Boss:mailto:boss@example.com
ATTENDEE;CN=Ann;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:ann@example.com
ATTENDEE:mailto:bob@example.com
X-CUSTOM:kept
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Reminder
X-ALARM-EXTRA:kept too
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:abc@example
DTSTAMP:20261001T100000Z
RECURRENCE-ID;TZID=W. Europe Standard Time:20261019T090000
DTSTART;TZID=W. Europe Standard Time:20261019T110000
DTEND;TZID=W. Europe Standard Time:20261019T120000
SUMMARY:Weekly meeting (moved)
END:VEVENT
END:VCALENDAR
"""


def span(event: Event) -> timedelta:
    assert isinstance(event.start, datetime) and isinstance(event.end, datetime)
    return event.end - event.start


def hour(moment: date) -> int:
    assert isinstance(moment, datetime)
    return moment.hour


def one(text: str) -> EventSeries:
    parsed = parse_events(text)
    assert len(parsed) == 1
    return parsed[0]


def test_an_event_with_a_windows_time_zone_exceptions_people_and_reminders() -> None:
    series = one(WINDOWS_ZONE)
    event = series.master
    assert (event.uid, event.summary, event.description, event.location) == ("abc@example", "Weekly meeting", "Line one\nLine two, with a comma", "Room 5")
    assert event.start == datetime(2026, 10, 12, 9, tzinfo=AMS) and span(event) == timedelta(hours=1) and not event.all_day and event.repeats
    assert event.rrule == "FREQ=WEEKLY;COUNT=4;BYDAY=MO" and event.exdates == (datetime(2026, 10, 26, 9, tzinfo=AMS),) and event.categories == ("Work", "Team")
    assert event.organizer == Attendee("boss@example.com", "Boss", "REQ-PARTICIPANT", "NEEDS-ACTION", False)
    assert event.attendees == (Attendee("ann@example.com", "Ann", "REQ-PARTICIPANT", "ACCEPTED", True), Attendee("bob@example.com"))
    assert event.reminders == (Reminder(15, None, "DISPLAY", False, "Reminder"),)
    assert len(series.overrides) == 1 and series.overrides[0].summary == "Weekly meeting (moved)" and hour(series.overrides[0].start) == 11


def test_the_range_view_expands_repeats_honours_exceptions_and_moved_occurrences() -> None:
    found = occurrences([WINDOWS_ZONE], datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC))
    assert [(o.event.summary, o.start.isoformat()) for o in found] == [
        ("Weekly meeting", "2026-10-12T09:00:00+02:00"), ("Weekly meeting (moved)", "2026-10-19T11:00:00+02:00"), ("Weekly meeting", "2026-11-02T09:00:00+01:00"),
    ]  # the 26th is an exception, the 19th moved, and the clock change of the 25th is followed
    assert [o.changed for o in found] == [False, True, False]
    assert occurrences([WINDOWS_ZONE], datetime(2026, 11, 3, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC)) == []
    assert occurrences([WINDOWS_ZONE], datetime(2026, 11, 3, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC)) == []


def test_all_day_floating_and_duration_events() -> None:
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART;VALUE=DATE:20261224\r\nSUMMARY:Christmas Eve\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:b\r\nDTSTART:20261224T180000\r\nDURATION:PT90M\r\nSUMMARY:Dinner (floating)\r\nTRANSP:TRANSPARENT\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:c\r\nDTSTART;VALUE=DATE:20261230\r\nDTEND;VALUE=DATE:20270103\r\nSUMMARY:Holiday\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    a, b, c = (s.master for s in parse_events(text))
    assert a.all_day and a.start == date(2026, 12, 24) and a.end == date(2026, 12, 25)
    assert not b.all_day and b.start == datetime(2026, 12, 24, 18) and b.end == datetime(2026, 12, 24, 19, 30) and b.transparency == "TRANSPARENT"  # no zone: floating
    assert c.end == date(2027, 1, 3)
    found = occurrences([text], datetime(2026, 12, 24, tzinfo=UTC), datetime(2026, 12, 26, tzinfo=UTC), zone=AMS)
    assert [o.event.uid for o in found] == ["a", "b"]  # all-day first, then the dinner


def test_an_event_without_a_written_end_keeps_its_end_when_only_its_start_changes() -> None:
    """CR-58: an end kept as a length (DURATION), or not written at all, must be written when the start changes, also when the end did not."""
    text = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:b\r\nDTSTART:20261224T100000Z\r\nDURATION:PT1H\r\nSUMMARY:Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    (series,) = parse_events(text)
    earlier = replace(series.master, start=datetime(2026, 12, 24, 9, tzinfo=UTC))  # an hour earlier, the end left at 11:00
    (written,) = parse_events(events_to_text(replace(series, master=earlier), text))
    assert written.master.start == datetime(2026, 12, 24, 9, tzinfo=UTC) and written.master.end == datetime(2026, 12, 24, 11, tzinfo=UTC)
    all_day = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:c\r\nDTSTART;VALUE=DATE:20261228\r\nSUMMARY:Away\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    (away,) = parse_events(all_day)  # no end written: one day, the 28th
    sooner = replace(away.master, start=date(2026, 12, 27))  # from the 27th, still until the 29th
    (written,) = parse_events(events_to_text(replace(away, master=sooner), all_day))
    assert written.master.start == date(2026, 12, 27) and written.master.end == date(2026, 12, 29)


def test_reminder_kinds() -> None:
    def reminders(trigger: str) -> tuple[Reminder, ...]:
        text = f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:r\r\nDTSTART:20261001T100000Z\r\nBEGIN:VALARM\r\nACTION:AUDIO\r\n{trigger}\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        return one(text).master.reminders

    assert reminders("TRIGGER:-P1D")[0].minutes_before == 1440 and reminders("TRIGGER:PT0S")[0].minutes_before == 0 and reminders("TRIGGER:PT30M")[0].minutes_before == -30
    assert reminders("TRIGGER;RELATED=END:-PT5M")[0].relative_to_end and reminders("TRIGGER;VALUE=DATE-TIME:20261001T090000Z")[0].at == datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert reminders("TRIGGER:garbage") == () and reminders("TRIGGER:-PT15M")[0].action == "AUDIO"


def test_writing_a_new_event_and_reading_it_back() -> None:
    event = Event(
        uid="new@x", summary="Lunch, with a comma; and more", start=datetime(2026, 11, 3, 12, 30, tzinfo=AMS), end=datetime(2026, 11, 3, 13, 30, tzinfo=AMS),
        description="Bring\nthe papers", location="Café", rrule="FREQ=WEEKLY;COUNT=3", exdates=(datetime(2026, 11, 10, 12, 30, tzinfo=AMS),),
        categories=("Food",), organizer=Attendee("me@example.com", "Me"), attendees=(Attendee("you@example.com", "You", "REQ-PARTICIPANT", "ACCEPTED", True),),
        reminders=(Reminder(10), Reminder(None, datetime(2026, 11, 3, 8, tzinfo=UTC), "DISPLAY")), status="CONFIRMED", transparency="TRANSPARENT",
    )
    text = events_to_text(EventSeries(event))
    assert "BEGIN:VTIMEZONE" in text and "TZID:Europe/Amsterdam" in text and "PRODID:" in text and "VERSION:2.0" in text and "DTSTAMP:" in text
    again = one(text).master
    assert replace_nothing(again, event)
    assert [o.start for o in occurrences([text], datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))] == [event.start, datetime(2026, 11, 17, 12, 30, tzinfo=AMS)]


def replace_nothing(read: Event, written: Event) -> bool:
    """The event that was read back equals the one that was written, apart from what the writer adds (stamps)."""
    from dataclasses import replace

    return replace(read, created=None, modified=None) == replace(written, created=None, modified=None)


def test_an_all_day_event_and_utc_times_need_no_time_zone_component() -> None:
    day = Event(uid="d@x", summary="Day", start=date(2026, 12, 1), end=date(2026, 12, 2))
    utc = Event(uid="u@x", summary="Utc", start=datetime(2026, 12, 1, 10, tzinfo=UTC), end=datetime(2026, 12, 1, 11, tzinfo=UTC))
    for event in (day, utc):
        text = events_to_text(EventSeries(event))
        assert "VTIMEZONE" not in text and replace_nothing(one(text).master, event)
    assert "DTSTART;VALUE=DATE:20261201" in events_to_text(EventSeries(day)) and "DTSTART:20261201T100000Z" in events_to_text(EventSeries(utc))


def test_changing_one_property_leaves_everything_else_as_it_was() -> None:
    from dataclasses import replace

    series = one(WINDOWS_ZONE)
    changed = events_to_text(EventSeries(replace(series.master, summary="Renamed"), series.overrides), WINDOWS_ZONE)
    assert "SUMMARY:Renamed" in changed and "X-CUSTOM:kept" in changed and "X-ALARM-EXTRA:kept too" in changed  # what we do not know stays
    assert "TZID:W. Europe Standard Time" in changed and "Weekly meeting (moved)" in changed and "Europe/Amsterdam" not in changed
    again = one(changed)
    assert again.master.summary == "Renamed" and replace(again.master, summary="", created=None, modified=None) == replace(series.master, summary="", created=None, modified=None)
    assert again.overrides == series.overrides
    unchanged = events_to_text(series, WINDOWS_ZONE)  # nothing edited: nothing written but the calendar is the same
    assert one(unchanged) == series and "X-CUSTOM:kept" in unchanged and "DTSTAMP:20261001T100000Z" in unchanged  # the stamp is not touched either


def test_reminders_people_and_occurrences_can_be_changed_and_removed() -> None:
    from dataclasses import replace

    series = one(WINDOWS_ZONE)
    master = replace(series.master, reminders=(Reminder(60),), attendees=(), organizer=None, rrule="", exdates=())
    text = events_to_text(EventSeries(master, ()), WINDOWS_ZONE)
    again = one(text)
    assert again.master.reminders == (Reminder(60, None, "DISPLAY", False, ""),) and again.master.attendees == () and again.master.organizer is None
    assert again.overrides == () and not again.master.repeats and "X-ALARM-EXTRA" not in text and "X-CUSTOM:kept" in text and "TRIGGER:-PT1H" in text


def test_an_occurrence_can_be_moved_by_adding_an_override() -> None:
    from dataclasses import replace

    master = Event(uid="m@x", summary="Daily", start=datetime(2026, 11, 2, 9, tzinfo=AMS), end=datetime(2026, 11, 2, 9, 30, tzinfo=AMS), rrule="FREQ=DAILY;COUNT=3")
    moved = replace(master, summary="Daily (late)", start=datetime(2026, 11, 3, 15, tzinfo=AMS), end=datetime(2026, 11, 3, 15, 30, tzinfo=AMS), rrule="", recurrence_id=datetime(2026, 11, 3, 9, tzinfo=AMS))
    text = events_to_text(EventSeries(master, (moved,)))
    found = occurrences([text], datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 10, tzinfo=UTC))
    assert [(o.event.summary, hour(o.start)) for o in found] == [("Daily", 9), ("Daily (late)", 15), ("Daily", 9)]
    reverted = events_to_text(EventSeries(master, ()), text)
    assert [o.event.summary for o in occurrences([reverted], datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 10, tzinfo=UTC))] == ["Daily"] * 3


def test_tasks_are_read_written_completed_and_reopened() -> None:
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Buy milk\r\nDUE;VALUE=DATE:20261015\r\nPRIORITY:1\r\nPERCENT-COMPLETE:40\r\n"
        "STATUS:IN-PROCESS\r\nCATEGORIES:Home\r\nRELATED-TO:parent-1\r\nX-APPLE-SORT-ORDER:99\r\nEND:VTODO\r\nBEGIN:VTODO\r\nUID:t2\r\nSUMMARY:Done\r\n"
        "STATUS:COMPLETED\r\nCOMPLETED:20261001T100000Z\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )
    first, second = parse_tasks(text)
    assert (first.summary, first.due, first.priority, first.percent, first.status, first.categories, first.parent) == ("Buy milk", date(2026, 10, 15), 1, 40, "IN-PROCESS", ("Home",), "parent-1")
    assert not first.done and second.done and second.completed == datetime(2026, 10, 1, 10, tzinfo=UTC)
    finished = complete_task(first, datetime(2026, 10, 2, 8, tzinfo=UTC))
    assert finished.done and finished.percent == 100 and finished.status == "COMPLETED"
    written = tasks_to_text(finished, text)
    assert "STATUS:COMPLETED" in written and "COMPLETED:20261002T080000Z" in written and "PERCENT-COMPLETE:100" in written and "X-APPLE-SORT-ORDER:99" in written
    assert reopen_task(finished).status == "NEEDS-ACTION" and reopen_task(finished).completed is None
    fresh = Task(uid="n@x", summary="New", due=datetime(2026, 11, 1, 17, tzinfo=AMS), priority=5, reminders=(Reminder(30),), categories=("Work",), rrule="FREQ=WEEKLY")
    again = parse_tasks(tasks_to_text(fresh))[0]
    from dataclasses import replace

    assert replace(again, created=None, modified=None) == fresh and "VTIMEZONE" in tasks_to_text(fresh)


@pytest.mark.parametrize(
    "junk",
    [
        "", "not a calendar", "BEGIN:VCALENDAR", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:garbage\r\nEND:VEVENT\r\nEND:VCALENDAR",
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20260230T100000\r\nEND:VEVENT\r\nEND:VCALENDAR", "\x00\x01\x02", "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261001T100000Z\r\nRRULE:FREQ=NEVER\r\nEND:VEVENT\r\nEND:VCALENDAR",
    ],
)
def test_junk_gives_nothing_and_never_raises(junk: str) -> None:
    parse_events(junk)
    parse_tasks(junk)
    assert isinstance(occurrences([junk], datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)), list)


def test_hostile_input_is_cut_down() -> None:
    many = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261001T100000Z\r\n" + "".join(f"ATTENDEE:mailto:a{n}@x\r\n" for n in range(2000)) + "END:VEVENT\r\nEND:VCALENDAR\r\n"
    assert len(one(many).master.attendees) == 500
    assert parse_events(b"\xff\xfe\x00BEGIN:VCALENDAR") == []
    assert parse_events("x" * (20 * 1024 * 1024)) == []
    forever = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:f\r\nDTSTART:20260101T000000Z\r\nDTEND:20260101T000100Z\r\nRRULE:FREQ=SECONDLY\r\nSUMMARY:every second\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    assert len(occurrences([forever], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))) <= 5000  # an endless rule cannot flood a view
    assert [o.event.uid for o in occurrences([WINDOWS_ZONE, "junk", forever][:2], datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))] == ["abc@example"]  # a damaged item leaves the rest alone


def test_an_event_every_second_costs_only_what_it_shows() -> None:
    import time as clock
    from datetime import UTC, datetime as moment
    from chrono_mail.pim.ical import MAXIMUM_OCCURRENCES, occurrences
    text = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:s1\r\nDTSTART:20261020T000000Z\r\nDTEND:20261020T000001Z\r\nRRULE:FREQ=SECONDLY\r\nSUMMARY:Tick\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    began = clock.monotonic()
    found = occurrences([text], moment(2026, 10, 20, tzinfo=UTC), moment(2026, 10, 27, tzinfo=UTC), UTC)
    assert len(found) == MAXIMUM_OCCURRENCES and clock.monotonic() - began < 3  # a week of seconds is 604,800 occurrences
    assert len(occurrences([text], moment(2026, 10, 20, tzinfo=UTC), moment(2026, 10, 21, tzinfo=UTC), UTC, limit=10)) == 10
    weekly = text.replace("FREQ=SECONDLY", "FREQ=WEEKLY").replace("UID:s1", "UID:w1")
    assert [item.start.day for item in occurrences([weekly], moment(2026, 10, 20, tzinfo=UTC), moment(2026, 11, 10, tzinfo=UTC), UTC)] == [20, 27, 3]
