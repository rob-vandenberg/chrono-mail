from pathlib import Path

import pytest

from chrono_mail.engine import Engine, UsageError
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_calendar.py 1.9.265"

# --- Version history ----------------------------------------------------
# v1.9.265: the fact of a calendar changed carries its palette; a change of the palette tells every calendar of the account (R-44)
# v1.1.221: the palette belongs to the account: its calendars share it, another account has its own; palettes calendars kept are taken over
# v1.1.219: a calendar's palette and an appointment's colour
# v1.1.218: the cap of occurrences is for the whole range, not per event
# v0.0.63: The tasks section
# v0.0.60: Initial tests of the event and task commands


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    return engine


def collection(engine: Engine, kind: str) -> str:
    return next(c.collection for c in call(engine, "collection.list", kind=kind))


def test_an_event_is_added_listed_on_its_day_changed_and_deleted(engine: Engine) -> None:
    cal = collection(engine, "events")
    added = call(
        engine, "event.add", calendar=cal, summary="Lunch, with a comma", start="2026-11-03T12:30", end="2026-11-03T13:30", time_zone="Europe/Amsterdam", location="Café",
        description="Bring\nthe papers", reminder=("10",), attendee=("Ann <ann@example.com>", "bob@example.com"), organizer="me@example.com", tag=("Food",), status="confirmed", busy=False,
    )
    assert (added.summary, added.start, added.end, added.all_day, added.time_zone, added.busy, added.status) == ("Lunch, with a comma", "2026-11-03T12:30:00+01:00", "2026-11-03T13:30:00+01:00", False, "Europe/Amsterdam", False, "CONFIRMED")
    assert [(a.address, a.name) for a in added.attendees] == [("ann@example.com", "Ann"), ("bob@example.com", "")] and added.reminders[0].minutes_before == 10 and added.tags == ("Food",)
    day = call(engine, "event.list", from_date="2026-11-03").events
    assert [(o.summary, o.calendar, o.item, o.repeats, o.occurrence) for o in day] == [("Lunch, with a comma", "Calendar", added.item, False, "")] and day[0].colour
    assert call(engine, "event.list", from_date="2026-11-04").events == () and call(engine, "event.list", from_date="2026-11-01", to_date="2026-11-30").events[0].item == added.item
    assert [o.item for o in call(engine, "event.list", from_date="2026-11-03", text="caf").events] == [added.item] and call(engine, "event.list", from_date="2026-11-03", text="nothing").events == ()
    changed = call(engine, "event.edit", item=added.item, summary="Late lunch", start="2026-11-03T13:00", clear=("attendee",), reminder=("30", "at:2026-11-03T09:00"))
    assert (changed.summary, changed.start, changed.end) == ("Late lunch", "2026-11-03T13:00:00+01:00", "2026-11-03T14:00:00+01:00")  # the length stays when only the start moves
    assert changed.attendees == () and [r.minutes_before for r in changed.reminders] == [30, None] and changed.location == "Café" and changed.organizer is not None
    shown = call(engine, "event.show", item=added.item)
    assert shown.summary == "Late lunch" and shown.reminders[1].at.startswith("2026-11-03T08:00")  # 09:00 in Amsterdam, written as an exact moment
    call(engine, "event.delete", item=added.item)
    assert call(engine, "event.list", from_date="2026-11-03").events == ()
    with pytest.raises(UsageError, match="no such item"):
        call(engine, "event.show", item=added.item)


def test_all_day_events_and_the_checks_on_what_is_written(engine: Engine) -> None:
    cal = collection(engine, "events")
    holiday = call(engine, "event.add", calendar=cal, summary="Holiday", start="2026-12-25", end="2026-12-28")
    assert holiday.all_day and (holiday.start, holiday.end) == ("2026-12-25", "2026-12-28")
    assert [o.summary for o in call(engine, "event.list", from_date="2026-12-27").events] == ["Holiday"] and call(engine, "event.list", from_date="2026-12-28").events == ()  # the end is the day after the last day
    one_day = call(engine, "event.add", calendar=cal, summary="Day", start="2026-12-01")
    assert one_day.end == "2026-12-02"
    bad_events: list[tuple[dict[str, object], str]] = [
        (dict(summary="x"), "needs a summary and a start"), (dict(start="2026-01-01"), "needs a summary and a start"), (dict(summary="x", start="tomorrow"), "not a date"),
        (dict(summary="x", start="2026-01-01T10:00", end="2026-01-01T09:00"), "before it starts"), (dict(summary="x", start="2026-01-01", end="2026-01-01"), "after the start"),
        (dict(summary="x", start="2026-01-01T10:00", end="2026-01-02"), "both"), (dict(summary="x", start="2026-01-01", rrule="WEEKLY"), "FREQ="),
        (dict(summary="x", start="2026-01-01", reminder=("soon",)), "minutes"), (dict(summary="x", start="2026-01-01", attendee=("nobody",)), "e-mail"),
        (dict(summary="x", start="2026-01-01T10:00", time_zone="Mars/Base"), "Unknown time zone"),
    ]
    for values, message in bad_events:
        with pytest.raises(UsageError, match=message):
            call(engine, "event.add", calendar=cal, **values)
    with pytest.raises(UsageError, match="after from_date"):
        call(engine, "event.list", from_date="2026-12-02", to_date="2026-12-01").events
    with pytest.raises(UsageError, match="At most"):
        call(engine, "event.list", from_date="2026-01-01", to_date="2028-01-01").events
    with pytest.raises(UsageError, match="not a calendar"):
        call(engine, "event.add", calendar=collection(engine, "tasks"), summary="x", start="2026-01-01")


def test_a_repeating_event_one_occurrence_changed_and_one_taken_out(engine: Engine) -> None:
    cal = collection(engine, "events")
    series = call(engine, "event.add", calendar=cal, summary="Standup", start="2026-11-02T09:00", end="2026-11-02T09:15", time_zone="Europe/Amsterdam", rrule="FREQ=DAILY;COUNT=5")
    days = call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-09").events
    assert [o.start[:10] for o in days] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"] and all(o.repeats and o.occurrence for o in days)
    third = days[2].occurrence
    moved = call(engine, "event.edit", item=series.item, occurrence=third, summary="Standup (late)", start="2026-11-04T15:00")
    assert [(c.occurrence[:10], c.start, c.summary) for c in moved.changed_occurrences] == [("2026-11-04", "2026-11-04T15:00:00+01:00", "Standup (late)")] and moved.summary == "Standup"
    days = call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-09").events
    changed = [o for o in days if o.changed]
    assert len(days) == 5 and [(o.summary, o.start[11:16]) for o in changed] == [("Standup (late)", "15:00")] and [o.summary for o in days if not o.changed] == ["Standup"] * 4
    call(engine, "event.edit", item=series.item, occurrence=third, location="Room 2")  # changed again: the same override
    assert call(engine, "event.show", item=series.item).changed_occurrences.__len__() == 1
    gone = call(engine, "event.delete", item=series.item, occurrence=days[0].occurrence)
    assert not gone.deleted and [o.start[:10] for o in call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-09").events] == ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    call(engine, "event.delete", item=series.item, occurrence=third)  # the changed occurrence is taken out too
    after = call(engine, "event.show", item=series.item)
    assert after.changed_occurrences == () and len(after.exceptions) == 2 and len(call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-09").events) == 3
    plain = call(engine, "event.add", calendar=cal, summary="Once", start="2026-11-10T10:00", time_zone="Europe/Amsterdam")
    with pytest.raises(UsageError, match="does not repeat"):
        call(engine, "event.delete", item=plain.item, occurrence="2026-11-10T10:00")
    with pytest.raises(UsageError, match="does not repeat"):
        call(engine, "event.edit", item=plain.item, occurrence="2026-11-10T10:00:00+01:00", summary="x")
    with pytest.raises(UsageError, match="own rule"):
        call(engine, "event.edit", item=series.item, occurrence=third, rrule="FREQ=DAILY")


def test_events_are_moved_between_calendars_imported_and_exported(engine: Engine) -> None:
    one = collection(engine, "events")
    two = call(engine, "collection.create", kind="events", name="Work").collection
    event = call(engine, "event.add", calendar=one, summary="Meet", start="2026-11-05T10:00", time_zone="UTC")
    moved = call(engine, "event.move", item=event.item, calendar=two)
    assert moved.collection == two and moved.item != event.item and [o.calendar for o in call(engine, "event.list", from_date="2026-11-05").events] == ["Work"]
    exported = call(engine, "event.export", item=moved.item)
    assert exported.events == 1 and "SUMMARY:Meet" in exported.text and exported.text.count("BEGIN:VCALENDAR") == 1
    ics = exported.text.replace("SUMMARY:Meet", "SUMMARY:Meet again")
    done = call(engine, "event.import", calendar=two, text=ics)
    assert (done.imported, done.updated) == (0, 1) and [o.summary for o in call(engine, "event.list", from_date="2026-11-05").events] == ["Meet again"]  # the same uid: replaced
    other = call(engine, "event.import", calendar=one, text=ics)
    assert other.imported == 1 and call(engine, "event.export", calendar=one).events == 1 and len(call(engine, "event.list", from_date="2026-11-05").events) == 2
    with pytest.raises(UsageError, match="No event"):
        call(engine, "event.import", calendar=one, text="garbage")
    with pytest.raises(UsageError, match="either"):
        call(engine, "event.export")


def test_tasks_are_added_listed_by_due_date_changed_completed_and_reopened(engine: Engine) -> None:
    tasks = collection(engine, "tasks")
    late = call(engine, "task.add", list=tasks, summary="Pay rent", due="2020-01-31", priority=1, tag=("Home",), reminder=("60",))
    soon = call(engine, "task.add", list=tasks, summary="Buy milk", due="2099-01-01T17:00", time_zone="Europe/Amsterdam")
    whenever = call(engine, "task.add", list=tasks, summary="Tidy up")
    assert (late.due, late.priority, late.tags, late.reminders, late.status, late.done, late.overdue) == ("2020-01-31", 1, ("Home",), (60,), "NEEDS-ACTION", False, True)
    assert not soon.overdue and soon.due == "2099-01-01T17:00:00+01:00"
    listed = call(engine, "task.list")
    assert [t.summary for t in listed.tasks] == ["Pay rent", "Buy milk", "Tidy up"] and (listed.total, listed.active, listed.completed) == (3, 3, 0)  # no due date: last
    assert [t.summary for t in call(engine, "task.list", due_before="2050-01-01").tasks] == ["Pay rent"] and [t.item for t in call(engine, "task.list", tag="home").tasks] == [late.item]
    assert [t.item for t in call(engine, "task.list", text="mil").tasks] == [soon.item]
    done = call(engine, "task.complete", item=late.item)
    assert not done.next_time and done.task.done and done.task.percent == 100 and done.task.completed and done.task.status == "COMPLETED" and not done.task.overdue
    assert [t.summary for t in call(engine, "task.list").tasks] == ["Buy milk", "Tidy up"] and [t.summary for t in call(engine, "task.list", state="done").tasks] == ["Pay rent"]
    everything = call(engine, "task.list", state="all")
    assert everything.total == 3 and (everything.active, everything.completed) == (2, 1)
    reopened = call(engine, "task.reopen", item=late.item)
    assert not reopened.done and reopened.percent == 0 and reopened.completed == ""
    edited = call(engine, "task.edit", item=whenever.item, summary="Tidy the desk", due="2099-02-01", percent=40, status="in-process", priority=5, clear=("tag",), tag=("Work",))
    assert (edited.summary, edited.percent, edited.status, edited.priority, edited.tags) == ("Tidy the desk", 40, "IN-PROCESS", 5, ("Work",))
    assert call(engine, "task.edit", item=whenever.item, status="completed").done and call(engine, "task.show", item=whenever.item).completed
    call(engine, "task.delete", item=soon.item)
    with pytest.raises(UsageError, match="no such item"):
        call(engine, "task.show", item=soon.item)
    bad_tasks: list[tuple[dict[str, object], str]] = [
        (dict(), "needs a summary"), (dict(summary="x", priority=10), "priority"), (dict(summary="x", percent=101), "percentage"), (dict(summary="x", due="soon"), "due"),
        (dict(summary="x", start="2026-02-01", due="2026-01-01"), "before it starts"), (dict(summary="x", rrule="DAILY"), "FREQ="),
    ]
    for values, message in bad_tasks:
        with pytest.raises(UsageError, match=message):
            call(engine, "task.add", list=tasks, **values)


def test_a_repeating_task_goes_on_to_its_next_time(engine: Engine) -> None:
    tasks = collection(engine, "tasks")
    weekly = call(engine, "task.add", list=tasks, summary="Water the plants", due="2026-11-02T18:00", time_zone="Europe/Amsterdam", rrule="FREQ=WEEKLY")
    first = call(engine, "task.complete", item=weekly.item)
    assert first.next_time and not first.task.done and first.task.due == "2026-11-09T18:00:00+01:00" and first.task.percent == 0
    second = call(engine, "task.complete", item=weekly.item)
    assert second.task.due == "2026-11-16T18:00:00+01:00"
    ended = call(engine, "task.add", list=tasks, summary="Once more", due="2026-11-02", rrule="FREQ=DAILY;COUNT=1")
    last = call(engine, "task.complete", item=ended.item)
    assert not last.next_time and last.task.done  # the rule has no next time: the task is done


def test_tasks_move_import_and_export_and_tags_are_renamed_in_all_kinds(engine: Engine) -> None:
    one = collection(engine, "tasks")
    two = call(engine, "collection.create", kind="tasks", name="Work").collection
    task = call(engine, "task.add", list=one, summary="Report", due="2099-03-01", tag=("Work",))
    event = call(engine, "event.add", calendar=collection(engine, "events"), summary="Meeting", start="2099-03-02T10:00", time_zone="UTC", tag=("work", "Team"))
    moved = call(engine, "task.move", item=task.item, list=two)
    assert moved.collection == two and [t.list_name for t in call(engine, "task.list").tasks] == ["Work"]
    text = call(engine, "task.export", list=two)
    assert text.tasks == 1 and "SUMMARY:Report" in text.text
    again = call(engine, "task.import", list=two, text=text.text.replace("Report", "Report v2"))
    assert (again.imported, again.updated) == (0, 1) and [t.summary for t in call(engine, "task.list").tasks] == ["Report v2"]
    with pytest.raises(UsageError, match="No task"):
        call(engine, "task.import", list=two, text="garbage")
    assert call(engine, "tag.rename", name="WORK", new_name="Job").items == 2
    assert call(engine, "task.show", item=moved.item).tags == ("Job",) and call(engine, "event.show", item=event.item).tags == ("Job", "Team")
    names = {t.name: t for t in call(engine, "tag.list")}
    assert names["Job"].tasks == 1 and names["Job"].events == 1 and names["Job"].contacts == 0 and "Work" not in names


def test_tags_are_added_and_removed_on_events_and_tasks_without_regard_to_case(engine: Engine) -> None:
    event = call(engine, "event.add", calendar=collection(engine, "events"), summary="Meet", start="2026-11-05T10:00", time_zone="UTC", tag=("Work",))
    task = call(engine, "task.add", list=collection(engine, "tasks"), summary="Do", tag=("Work", "Home"))
    after_event = call(engine, "event.edit", item=event.item, add_tag=("work", "Team"), remove_tag=("HOME",))
    after_task = call(engine, "task.edit", item=task.item, add_tag=("Chores", "home"), remove_tag=("WORK",))
    assert after_event.tags == ("Work", "Team") and after_task.tags == ("Home", "Chores")


def test_the_cap_of_occurrences_is_for_the_whole_range(engine: Engine) -> None:
    from chrono_mail.pim.ical import MAXIMUM_OCCURRENCES
    cal = collection(engine, "events")
    for name in ("Tick", "Tock"):
        call(engine, "event.add", calendar=cal, summary=name, start="2026-11-02T09:00", end="2026-11-02T09:01", time_zone="UTC", rrule="FREQ=MINUTELY")
    listed = call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-09")
    assert len(listed.events) == MAXIMUM_OCCURRENCES and listed.cut  # two events of 10,080 a week each: 5,000 in all, not 5,000 each


def calendar_summary(engine: Engine, cal: str) -> object:
    return next(item for item in call(engine, "collection.list") if item.collection == cal)


def test_a_calendar_has_its_own_colour_first_and_the_suggestions_after_it(engine: Engine) -> None:
    cal = collection(engine, "events")
    palette = calendar_summary(engine, cal).palette
    assert (palette[0].colour_id, palette[0].name) == ("calendar", "Calendar colour") and palette[0].colour == calendar_summary(engine, cal).colour
    assert [entry.name for entry in palette[1:]] == ["Tomato", "Flamingo", "Tangerine", "Banana", "Sage", "Basil", "Peacock", "Blueberry", "Lavender", "Grape", "Graphite"]
    assert palette[1].colour == "#d50000" and palette[-1].colour == "#616161"
    tasks = next(item for item in call(engine, "collection.list") if item.kind == "tasks")
    assert tasks.palette == ()  # task lists: later (the owner decides)


def test_the_palette_is_the_account_s_and_a_calendar_s_own_colour_cannot_go(engine: Engine) -> None:
    cal = collection(engine, "events")
    other = call(engine, "collection.create", kind="events", name="Work").collection
    summary = call(engine, "collection.set-palette", collection=cal, own_name="Mine", own_colour="#112233", colours=("g11\tRood\t#ff0000", "\tGeel\t#ffee00"))
    assert [(entry.name, entry.colour) for entry in summary.palette] == [("Mine", "#112233"), ("Rood", "#ff0000"), ("Geel", "#ffee00")]
    assert calendar_summary(engine, cal).colour == "#112233" and len(calendar_summary(engine, cal).palette) == 3  # kept, read back
    assert [(entry.name, entry.colour) for entry in calendar_summary(engine, other).palette[1:]] == [("Rood", "#ff0000"), ("Geel", "#ffee00")]  # shared
    assert calendar_summary(engine, other).palette[0].name == "Mine" and calendar_summary(engine, other).colour != "#112233"  # each its own colour
    with pytest.raises(UsageError):
        call(engine, "collection.set-palette", collection=cal, colours=("calendar\tAgain\t#000000",))
    with pytest.raises(UsageError):
        call(engine, "collection.set-palette", collection=cal, colours=("\t\t#000000",))  # a colour needs a name
    call(engine, "collection.set-palette", collection=cal)
    assert [entry.colour_id for entry in calendar_summary(engine, cal).palette] == ["calendar"]  # every other colour may go, its own stays


def test_an_appointment_has_a_colour_of_its_calendar_and_follows_the_palette(engine: Engine) -> None:
    cal = collection(engine, "events")
    made = call(engine, "event.add", calendar=cal, summary="Dentist", start="2026-11-03T10:00", end="2026-11-03T11:00", colour="g11")
    plain = call(engine, "event.add", calendar=cal, summary="Plain", start="2026-11-04T10:00", end="2026-11-04T11:00")
    def shown() -> dict[str, tuple[str, str]]:
        return {row.summary: (row.colour, row.colour_id) for row in call(engine, "event.list", from_date="2026-11-01", to_date="2026-11-10").events}
    own = calendar_summary(engine, cal).colour
    assert shown() == {"Dentist": ("#d50000", "g11"), "Plain": (own, "")}
    assert made.colour_id == "g11"
    with pytest.raises(UsageError):
        call(engine, "event.add", calendar=cal, summary="x", start="2026-11-05T10:00", colour="nosuch")
    call(engine, "collection.set-palette", collection=cal, colours=("g11\tRood\t#ff0000",))
    assert shown()["Dentist"] == ("#ff0000", "g11")  # the palette changed: the appointment follows
    call(engine, "collection.edit", collection=cal, colour="#00aa00")
    assert shown()["Plain"] == ("#00aa00", "")  # on its calendar's colour: follows that
    call(engine, "collection.set-palette", collection=cal)
    assert shown()["Dentist"] == ("#00aa00", "g11")  # its colour was deleted: the calendar's own
    call(engine, "event.edit", item=made.item, colour="")
    assert shown()["Dentist"] == ("#00aa00", "")


def test_the_colour_travels_in_the_appointment_for_other_programs_too(engine: Engine) -> None:
    cal = collection(engine, "events")
    made = call(engine, "event.add", calendar=cal, summary="Dentist", start="2026-11-03T10:00", end="2026-11-03T11:00", colour="g11")
    from chrono_mail.engine.pimaccess import located
    with located(engine, made.item) as found:
        written = found.store.get(found.item.collection, made.item)
    assert "X-CHRONO-MAIL-COLOR-ID:g11" in written and "COLOR:red" in written  # #d50000 is nearest to the CSS name red
    other = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:o1\r\nDTSTART:20261105T100000Z\r\nDTEND:20261105T110000Z\r\nSUMMARY:From elsewhere\r\nCOLOR:turquoise\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    call(engine, "event.import", calendar=cal, text=other)
    row = next(row for row in call(engine, "event.list", from_date="2026-11-01", to_date="2026-11-10").events if row.summary == "From elsewhere")
    assert (row.colour, row.colour_id, row.other_colour) == ("#40e0d0", "", "#40e0d0")


def test_another_account_has_its_own_palette(engine: Engine) -> None:
    cal = collection(engine, "events")
    call(engine, "collection.set-palette", collection=cal, colours=("g11\tRood\t#ff0000",))
    created = call(
        engine, "account.add", display_name="Me", email_address="bob@example.com", username="bob", password="secret", incoming_host="127.0.0.1",
        incoming_port=1, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )
    from chrono_mail.core import CollectionKind
    from chrono_mail.engine.pimaccess import open_pim

    with open_pim(engine, created.account_id, create=True) as store:
        work = store.create_collection(CollectionKind.EVENTS, "Work").collection_id
    assert len(calendar_summary(engine, work).palette) == 12  # the suggestions: nothing of the first account's
    assert len(calendar_summary(engine, cal).palette) == 2


def test_the_palettes_calendars_kept_are_taken_over_by_their_account_once(tmp_path: Path) -> None:
    import json

    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    first = collection(engine, "events")
    second = call(engine, "collection.create", kind="events", name="Work").collection
    root = next(engine.directories.pim_directory(account) for account in ("0" * 31 + "1",))
    def keep(collection_id: str, palette: object) -> None:  # as 1.1.219 kept it
        path = root / "collections" / collection_id / "collection.json"
        data = json.loads(path.read_text())
        data["palette"] = palette
        path.write_text(json.dumps(data))
    keep(first, {"own_name": "Mine", "colours": [["g11", "Rood", "#ff0000"], ["x1", "Own", "#123456"]]})
    keep(second, {"own_name": "", "colours": [["g11", "Rood", "#aa0000"], ["x2", "Other", "#654321"]]})
    told: list[str] = []
    from chrono_mail.engine.session import Engine as Session

    later = Session(engine.directories, engine.passwords, engine.keys, durable=False, notice=told.append)  # the next start of the program
    palette = calendar_summary(later, first).palette
    assert [(entry.colour_id, entry.colour) for entry in palette[1:]] == [("g11", "#ff0000"), ("x1", "#123456"), ("x2", "#654321")]
    assert palette[0].name == "Mine" and calendar_summary(later, second).palette == (calendar_summary(later, second).palette[0], *palette[1:])
    assert len(told) == 1 and "Rood" in told[0] and "Work" in told[0]  # never silent: the colour that could not be kept is named
    assert all("palette" not in json.loads((root / "collections" / c / "collection.json").read_text()) for c in (first, second))


def test_the_fact_of_a_calendar_carries_its_palette_and_a_palette_change_tells_every_calendar(engine: Engine) -> None:
    from chrono_mail.engine.changes import Change

    cal = collection(engine, "events")
    other = call(engine, "collection.create", kind="events", name="Work").collection
    told: list[Change] = []
    engine.changed = told.append
    call(engine, "collection.edit", collection=other, name="Office", colour="#336699")
    (renamed,) = [change for change in told if change.kind == "collection_edited"]
    assert renamed.folder == other and renamed.palette == calendar_summary(engine, other).palette and renamed.palette[0].colour == "#336699"
    assert not renamed.read_only
    told.clear()
    call(engine, "collection.set-palette", collection=cal, colours=("g11\tRood\t#ff0000",))
    palettes = {change.folder: change.palette for change in told if change.kind == "collection_edited"}
    assert palettes[cal] == calendar_summary(engine, cal).palette and palettes[other] == calendar_summary(engine, other).palette  # every calendar of the account
    assert [(entry.name, entry.colour) for entry in palettes[other][1:]] == [("Rood", "#ff0000")]
