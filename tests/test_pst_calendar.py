import struct
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from chrono_mail.pim.ical import parse_events, parse_tasks
from chrono_mail.pst import recurrence
from chrono_mail.pst.calendar import build_event, build_task
from chrono_mail.pst.names import PSETID_APPOINTMENT, PSETID_COMMON, PSETID_TASK
from chrono_mail.pst.properties import Message
from chrono_mail.pst.zones import WINDOWS_ZONES, from_definition, from_structure, from_windows_name
from tests.test_pst_helpers import KEYWORDS, Plain, named, props

# --- Version ------------------------------------------------------------
__version__ = "test_pst_calendar.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: events with their repeat patterns and changed occurrences, tasks, time zones

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
START = named(PSETID_APPOINTMENT, 0x820D)
END = named(PSETID_APPOINTMENT, 0x820E)
RECUR = named(PSETID_APPOINTMENT, 0x8216)
ZONE = named(PSETID_APPOINTMENT, 0x825E)
LOCATION = named(PSETID_APPOINTMENT, 0x8208)
ALL_DAY = named(PSETID_APPOINTMENT, 0x8215)
BUSY = named(PSETID_APPOINTMENT, 0x8205)


def minutes(moment: datetime) -> int:
    """Wall-clock minutes since 1601, the unit of the repeat blobs."""
    return int((moment.replace(tzinfo=None) - datetime(1601, 1, 1)).total_seconds() // 60)


def definition(key: str) -> bytes:
    """A TZDEFINITION with its key name and no rules (the reader needs the name only)."""
    name = key.encode("utf-16-le")
    return struct.pack("<BBHHH", 2, 1, 6 + len(name), 2, len(key)) + name + struct.pack("<H", 0)


def pattern(
    frequency: int, kind: int, period: int, specific: bytes, end_type: int, count: int, deleted: list[int], modified: list[int], start: int,
    end: int, first_day: int = 1,
) -> bytes:
    data = struct.pack("<HHHHHIII", 0x3004, 0x3004, frequency, kind, 0, 0, period, 0) + specific
    data += struct.pack("<III", end_type, count, first_day)
    data += struct.pack("<I", len(deleted)) + b"".join(struct.pack("<I", value) for value in deleted)
    data += struct.pack("<I", len(modified)) + b"".join(struct.pack("<I", value) for value in modified)
    return data + struct.pack("<II", start, end)


def changed(start: int, end: int, original: int, subject: str | None) -> tuple[bytes, bytes]:
    """An ExceptionInfo and its ExtendedException."""
    flags = recurrence.ARO_SUBJECT if subject is not None else 0
    info = struct.pack("<IIIH", start, end, original, flags)
    extended = struct.pack("<I", 0) + struct.pack("<I", 0)  # ChangeHighlight (writer 0x3009), ReservedBlockEE1
    if subject is not None:
        encoded = subject.encode("cp1252")
        info += struct.pack("<HH", len(encoded) + 1, len(encoded)) + encoded
        extended += struct.pack("<III", start, end, original) + struct.pack("<H", len(subject)) + subject.encode("utf-16-le") + struct.pack("<I", 0)
    return info, extended


def appointment(base: bytes, start_offset: int, end_offset: int, changes: list[tuple[bytes, bytes]]) -> bytes:
    data = base + struct.pack("<IIIIH", 0x3006, 0x3009, start_offset, end_offset, len(changes))
    data += b"".join(info for info, _ in changes) + struct.pack("<I", 0)
    return data + b"".join(extended for _, extended in changes) + struct.pack("<I", 0)


def _event(values: dict[int, Plain]) -> Message:
    return Message(props(values), node=0x200044)


def test_weekly_with_a_deleted_and_a_changed_occurrence() -> None:
    first = datetime(2026, 3, 2, 9, 0, tzinfo=AMSTERDAM)  # a Monday
    deleted_day = datetime(2026, 3, 9)
    moved_day = datetime(2026, 3, 16)
    moved_start = datetime(2026, 3, 16, 10, 0)
    base = pattern(
        recurrence.WEEKLY, recurrence.PATTERN_WEEK, 1, struct.pack("<I", 0x02), recurrence.END_AFTER, 10,
        [minutes(deleted_day), minutes(moved_day)], [minutes(moved_day)], minutes(datetime(2026, 3, 2)), minutes(datetime(2026, 5, 4)),
    )
    blob = appointment(base, 540, 600, [changed(minutes(moved_start), minutes(moved_start) + 60, minutes(datetime(2026, 3, 16, 9, 0)), "Moved")])
    text = build_event(_event({
        0x0037: "Weekly", START: first.astimezone(UTC), END: (first + timedelta(hours=1)).astimezone(UTC), RECUR: blob,
        ZONE: definition("W. Europe Standard Time"), LOCATION: "Room 1", KEYWORDS: ("Work",),
    }), "uid-1")
    assert "TZID=Europe/Berlin" in text and "BEGIN:VTIMEZONE" in text
    [series] = parse_events(text)
    master = series.master
    assert master.uid == "uid-1"
    assert master.summary == "Weekly" and master.location == "Room 1" and master.categories == ("Work",)
    assert isinstance(master.start, datetime) and master.start.replace(tzinfo=None) == datetime(2026, 3, 2, 9, 0)
    assert set(master.rrule.split(";")) == {"FREQ=WEEKLY", "INTERVAL=1", "BYDAY=MO", "COUNT=10"}
    assert [moment.replace(tzinfo=None) for moment in master.exdates if isinstance(moment, datetime)] == [datetime(2026, 3, 9, 9, 0)]
    [override] = series.overrides
    assert override.summary == "Moved" and override.location == "Room 1"
    assert isinstance(override.recurrence_id, datetime) and override.recurrence_id.replace(tzinfo=None) == datetime(2026, 3, 16, 9, 0)
    assert isinstance(override.start, datetime) and override.start.replace(tzinfo=None) == datetime(2026, 3, 16, 10, 0)


def test_monthly_nth_weekday_until_a_date() -> None:
    first = datetime(2026, 3, 10, 14, 0, tzinfo=AMSTERDAM)  # the second Tuesday of March
    base = pattern(
        recurrence.MONTHLY, recurrence.PATTERN_MONTH_NTH, 1, struct.pack("<II", 0x04, 2), recurrence.END_BY_DATE, 0, [], [],
        minutes(datetime(2026, 3, 1)), minutes(datetime(2026, 12, 8)), first_day=0,
    )
    text = build_event(_event({
        0x0037: "Monthly", START: first.astimezone(UTC), END: (first + timedelta(hours=1)).astimezone(UTC),
        RECUR: appointment(base, 840, 900, []), ZONE: definition("W. Europe Standard Time"),
    }), "uid-2")
    [series] = parse_events(text)
    parts = dict(part.split("=", 1) for part in series.master.rrule.split(";"))
    assert parts["FREQ"] == "MONTHLY" and parts["BYDAY"] == "TU" and parts["BYSETPOS"] == "2" and parts["WKST"] == "SU"
    assert parts["UNTIL"] == "20261208T130000Z"  # the last occurrence's start (14:00 in winter time)


def test_last_weekday_yearly_and_daily_rules() -> None:
    yearly = recurrence.parse(pattern(recurrence.YEARLY, recurrence.PATTERN_MONTH_NTH, 12, struct.pack("<II", 0x02, 5), 0x2023, 0, [], [], 0, 0), False)
    assert recurrence.rrule(yearly, date(2026, 5, 25), None) == "FREQ=YEARLY;INTERVAL=1;BYMONTH=5;BYDAY=MO;BYSETPOS=-1"
    weekdays = recurrence.parse(pattern(recurrence.DAILY, recurrence.PATTERN_WEEK, 1, struct.pack("<I", 0x3E), 0x2023, 0, [], [], 0, 0), False)
    assert recurrence.rrule(weekdays, date(2026, 3, 2), None) == "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
    every_other = recurrence.parse(pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 2880, b"", recurrence.END_AFTER, 3, [], [], 0, 0), False)
    assert recurrence.rrule(every_other, date(2026, 3, 2), None) == "FREQ=DAILY;INTERVAL=2;COUNT=3"


def test_damaged_pattern_leaves_a_single_event() -> None:
    first = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    text = build_event(_event({0x0037: "Cut", START: first, END: first + timedelta(hours=1), RECUR: b"\x04\x30\x04\x30\x0b\x20"}), "uid-3")
    [series] = parse_events(text)
    assert series.master.rrule == "" and series.master.start == first


def test_all_day_event_with_reminder_and_free_time() -> None:
    start = datetime(2026, 4, 26, 22, 0, tzinfo=UTC)  # local midnight in Amsterdam's summer time
    text = build_event(_event({
        0x0037: "Holiday", START: start, END: start + timedelta(days=1), ALL_DAY: True, BUSY: 0, ZONE: definition("W. Europe Standard Time"),
        named(PSETID_COMMON, 0x8503): True, named(PSETID_COMMON, 0x8501): 30, 0x0036: 2, 0x1000: "Notes",
    }), "uid-4")
    [series] = parse_events(text)
    master = series.master
    assert master.start == date(2026, 4, 27) and master.end == date(2026, 4, 28)
    assert master.transparency == "TRANSPARENT" and master.classification == "PRIVATE" and master.description == "Notes"
    assert [reminder.minutes_before for reminder in master.reminders] == [30]


def test_meeting_attendees_and_organizer() -> None:
    start = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    message = Message(
        props({0x0037: "Meeting", START: start, END: start + timedelta(hours=1), 0x0C1A: "Ann", 0x5D01: "ann@example.org"}),
        (props({0x0C15: 1, 0x3001: "Bob", 0x39FE: "bob@example.net"}), props({0x0C15: 2, 0x3001: "Carol", 0x39FE: "carol@example.com"}),
         props({0x0C15: 1, 0x3001: "Ann", 0x39FE: "ann@example.org", 0x5FFD: 3})),
    )
    [series] = parse_events(build_event(message, "uid-5"))
    assert series.master.organizer is not None and series.master.organizer.address == "ann@example.org"
    assert [(person.address, person.role) for person in series.master.attendees] == [("bob@example.net", "REQ-PARTICIPANT"), ("carol@example.com", "OPT-PARTICIPANT")]


def test_zones() -> None:
    assert from_windows_name("Pacific Standard Time") == ZoneInfo("America/Los_Angeles")
    assert from_windows_name("Unknown Zone") is None
    assert from_definition(definition("Tokyo Standard Time")) == ZoneInfo("Asia/Tokyo")
    assert from_definition(b"\x02\x01\x00") is None
    # TZSTRUCT: bias, standard bias, daylight bias, standard year, standard date, daylight year, daylight date
    fixed = struct.pack("<iiiH8HH8H", 300, 0, 0, 0, *(0,) * 8, 0, *(0,) * 8)
    assert from_structure(fixed) == ZoneInfo("Etc/GMT+5")


def test_every_windows_zone_has_a_named_zone() -> None:
    """Review fault 11: the full CLDR table, every name of which the zone database knows."""
    assert len(WINDOWS_ZONES) >= 135
    for name, iana in WINDOWS_ZONES.items():
        assert from_windows_name(name) == ZoneInfo(iana)
    assert from_windows_name("Aleutian Standard Time") == ZoneInfo("America/Adak")


EUROPE_STRUCT = struct.pack("<iiiH8HH8H", -60, 0, -60, 0, 0, 10, 0, 5, 3, 0, 0, 0, 0, 0, 3, 0, 5, 2, 0, 0, 0)  # summer: last Sunday of March


def test_zone_from_its_rules_when_no_name_is_known() -> None:
    """Review fault 11: a zone with summer time and no name was UTC or a fixed offset; now it follows the rules the data file keeps."""
    zone = from_structure(EUROPE_STRUCT)
    assert zone is not None
    assert datetime(2026, 1, 15, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=1)
    assert datetime(2026, 7, 15, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=2)
    assert datetime(2026, 3, 29, 1, 59, tzinfo=zone).utcoffset() == timedelta(hours=1)
    assert datetime(2026, 3, 29, 2, 0, tzinfo=zone).utcoffset() == timedelta(hours=2)
    assert datetime(2026, 10, 25, 3, 0, tzinfo=zone).utcoffset() == timedelta(hours=1)
    # a TZDEFINITION with an unknown name and one effective rule
    name = "Custom Zone".encode("utf-16-le")
    rule = struct.pack("<BBHHH14s", 2, 1, 0x3E, 0x0002, 2007, b"\x00" * 14) + EUROPE_STRUCT[:12] + EUROPE_STRUCT[14:30] + EUROPE_STRUCT[32:48]
    blob = struct.pack("<BBHHH", 2, 1, 6 + len(name), 2, 11) + name + struct.pack("<H", 1) + rule
    defined = from_definition(blob)
    assert defined is not None and defined.tzname(None) == "Custom Zone"
    assert datetime(2026, 7, 1, tzinfo=defined).utcoffset() == timedelta(hours=2)
    start = datetime(2026, 7, 6, 9, 0, tzinfo=defined)
    text = build_event(_event({0x0037: "Custom", START: start.astimezone(UTC), END: (start + timedelta(hours=1)).astimezone(UTC), ZONE: blob}), "uid-z")
    assert "TZID:Custom Zone" in text and "DTSTART;TZID=Custom Zone:20260706T090000" in text
    assert "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3" in text and "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10" in text
    [series] = parse_events(text)
    assert isinstance(series.master.start, datetime) and series.master.start.astimezone(UTC) == start.astimezone(UTC)


def test_task() -> None:
    task_pattern = pattern(recurrence.WEEKLY, recurrence.PATTERN_WEEK, 2, struct.pack("<I", 0x20), 0x2023, 0, [], [], 0, 0)
    message = Message(props({
        0x0037: "Report", 0x1000: "Write it", 0x0017: 2, KEYWORDS: ("Office",),
        named(PSETID_TASK, 0x8104): datetime(2026, 3, 6, tzinfo=UTC), named(PSETID_TASK, 0x8105): datetime(2026, 3, 13, tzinfo=UTC),
        named(PSETID_TASK, 0x8101): 1, named(PSETID_TASK, 0x8102): 0.5, named(PSETID_TASK, 0x8116): task_pattern, named(PSETID_TASK, 0x8126): True,
        named(PSETID_COMMON, 0x8503): True, named(PSETID_COMMON, 0x8502): datetime(2026, 3, 13, 8, 0, tzinfo=UTC),
    }))
    [task] = parse_tasks(build_task(message, "uid-6"))
    assert task.summary == "Report" and task.description == "Write it" and task.categories == ("Office",)
    assert task.start == date(2026, 3, 6) and task.due == date(2026, 3, 13)
    assert task.status == "IN-PROCESS" and task.percent == 50 and task.priority == 1
    assert task.rrule == "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR"
    assert [reminder.at for reminder in task.reminders] == [datetime(2026, 3, 13, 8, 0, tzinfo=UTC)]


def test_completed_task() -> None:
    done = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
    message = Message(props({0x0037: "Done", named(PSETID_TASK, 0x811C): True, named(PSETID_TASK, 0x810F): done}))
    [task] = parse_tasks(build_task(message, "uid-7"))
    assert task.status == "COMPLETED" and task.percent == 100 and task.completed == done


# The worked examples of MS-OXOCAL 4.1.1.1 and 4.1.1.2, copied byte for byte from the specification (not made by the helpers above):
# every Monday, Thursday and Friday 10:00-10:30 from 26 March 2007, 12 times; the second has the 16 April occurrence moved to 11:00 with
# another subject and location.
SPEC_WEEKLY = bytes.fromhex(
    "043004300B2001000000C0210000010000000000000032000000222000000C0000000000000000000000000000008"
    "020BC0C20ADBC0C0630000009300000580200007602000000000000000000000000"
)
SPEC_WEEKLY_CHANGED = bytes.fromhex(
    "043004300B2001000000C0210000010000000000000032000000222000000C0000000000000001000000A096BC0C0"
    "1000000A096BC0C8020BC0C20ADBC0C0630000009300000580200007602000001003499BC0C5299BC0CF898BC0C11"
    "002200210053696D706C6520526563757272656E6365207769746820657863657074696F6E730800070033342F343"
    "13431000000000400000000000000000000003499BC0C5299BC0CF898BC0C2100530069006D0070006C0065002000"
    "52006500630075007200720065006E006300650020007700690074006800200065007800630065007000740069006"
    "F006E0073000700330034002F0034003100340031000000000000000000"
)


def test_specification_example_without_changes() -> None:
    rule = recurrence.parse(SPEC_WEEKLY, appointment=True)
    assert (rule.frequency, rule.pattern_type, rule.period, rule.day_mask) == (recurrence.WEEKLY, recurrence.PATTERN_WEEK, 1, 0x32)
    assert (rule.end_type, rule.occurrences, rule.first_day_of_week) == (recurrence.END_AFTER, 12, 0)
    assert (rule.start_offset, rule.end_offset, rule.changes) == (600, 630, ())
    assert recurrence.rrule(rule, date(2007, 3, 26), None) == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TH,FR;COUNT=12;WKST=SU"


def test_specification_example_with_a_changed_occurrence() -> None:
    """The ExtendedException: ChangeHighlight (writer 0x3009) skipped, the subject and location in full Unicode."""
    assert len(SPEC_WEEKLY_CHANGED) == 0x106
    rule = recurrence.parse(SPEC_WEEKLY_CHANGED, appointment=True)
    [change] = rule.changes
    assert change.subject == "Simple Recurrence with exceptions" and change.location == "34/4141"
    assert recurrence.local_moment(change.start, None).replace(tzinfo=None) == datetime(2007, 4, 16, 11, 0)
    assert recurrence.local_moment(change.end, None).replace(tzinfo=None) == datetime(2007, 4, 16, 11, 30)
    assert recurrence.local_moment(change.original_start, None).replace(tzinfo=None) == datetime(2007, 4, 16, 10, 0)
    assert recurrence.deleted_days(rule) == []  # the deleted list holds the changed occurrence: no EXDATE for it


def test_unicode_texts_of_a_change_win_over_the_8_bit_ones() -> None:
    base = pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 1440, b"", 0x2023, 0, [], [], 0, 0)
    moment = minutes(datetime(2026, 3, 3, 9, 0))
    info, extended = changed(moment, moment + 30, moment, "Caf?")
    extended = extended.replace("Caf?".encode("utf-16-le"), "Café".encode("utf-16-le"))
    [change] = recurrence.parse(appointment(base, 540, 570, [(info, extended)]), appointment=True).changes
    assert change.subject == "Café"


def test_day_past_the_end_of_shorter_months() -> None:
    """Review fault 10: "every month on the 31st" falls on the last day of shorter months, and 29 February on 28 February."""
    monthly = recurrence.parse(pattern(recurrence.MONTHLY, recurrence.PATTERN_MONTH, 1, struct.pack("<I", 31), 0x2023, 0, [], [], 0, 0), False)
    assert recurrence.rrule(monthly, date(2026, 1, 31), None) == "FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=28,29,30,31;BYSETPOS=-1"
    yearly = recurrence.parse(pattern(recurrence.YEARLY, recurrence.PATTERN_MONTH, 12, struct.pack("<I", 29), 0x2023, 0, [], [], 0, 0), False)
    assert recurrence.rrule(yearly, date(2028, 2, 29), None) == "FREQ=YEARLY;INTERVAL=1;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
    plain = recurrence.parse(pattern(recurrence.MONTHLY, recurrence.PATTERN_MONTH, 2, struct.pack("<I", 15), 0x2023, 0, [], [], 0, 0), False)
    assert recurrence.rrule(plain, date(2026, 1, 15), None) == "FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=15"


def test_gregorian_calendar_variants() -> None:
    """Review fault 12: the Gregorian variants (2, 9-12) were refused."""
    for calendar_type in (0, 1, 2, 9, 10, 11, 12):
        data = bytearray(pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 1440, b"", 0x2023, 0, [], [], 0, 0))
        data[8:10] = struct.pack("<H", calendar_type)
        assert recurrence.rrule(recurrence.parse(bytes(data), False), date(2026, 1, 1), None) == "FREQ=DAILY;INTERVAL=1"
    data = bytearray(pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 1440, b"", 0x2023, 0, [], [], 0, 0))
    data[8:10] = struct.pack("<H", 6)  # Hijri
    try:
        recurrence.rrule(recurrence.parse(bytes(data), False), date(2026, 1, 1), None)
    except recurrence.RecurrenceError:
        pass
    else:
        raise AssertionError("a Hijri pattern was written as Gregorian")


def test_other_era_calendars_with_gregorian_months() -> None:
    """Second review fault 4: Japanese era, Taiwan, Korean and Thai calendars count months and days as the Gregorian one does."""
    for calendar_type in (3, 4, 5, 7):
        data = bytearray(pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 1440, b"", 0x2023, 0, [], [], 0, 0))
        data[8:10] = struct.pack("<H", calendar_type)
        assert recurrence.rrule(recurrence.parse(bytes(data), False), date(2026, 1, 1), None) == "FREQ=DAILY;INTERVAL=1"


def test_a_damaged_start_offset_costs_only_the_pattern() -> None:
    """Third review fault 6: a start offset of years made the date arithmetic overflow and the whole event fail."""
    base = pattern(recurrence.DAILY, recurrence.PATTERN_DAY, 1440, b"", 0x2023, 0, [minutes(datetime(2026, 3, 3))], [], 0, 0)
    for offsets in ((0xFFFFFFF0, 0xFFFFFFFF), (600, 500), (1440, 1500)):
        try:
            recurrence.parse(appointment(base, *offsets, []), appointment=True)
        except recurrence.RecurrenceError:
            pass
        else:
            raise AssertionError(f"offsets {offsets} were accepted")
    first = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    text = build_event(_event({0x0037: "Daily", START: first, END: first + timedelta(hours=1), RECUR: appointment(base, 0xFFFFFFF0, 0xFFFFFFFF, [])}), "uid-o")
    [series] = parse_events(text)
    assert series.master.start == first and series.master.rrule == ""
