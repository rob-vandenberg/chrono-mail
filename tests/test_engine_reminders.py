import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import override
from zoneinfo import ZoneInfo

import pytest

from chrono_mail.config import AccountSettings
from chrono_mail.engine import Engine, UsageError, commands_reminders
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_reminders import _LOOKING, ReminderCheck, _Due, _due_in, _next_moment, reminder_check
from chrono_mail.pim.ical import parse_events
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.pimstore import DueReminder, PimIndex, PimStore
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from tests.test_config import make_account
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import local_calendar

# --- Version ------------------------------------------------------------
__version__ = "test_engine_reminders.py 4.4.545"

# --- Version history ----------------------------------------------------
# v4.4.545: a task reminds through the same look; the upgrade of the index to format 5 (D-316)
# v4.4.544: the index knows which reminders are due: no limit in days, the look reads only those, the upgrade of the index (D-316)
# v4.4.537: a placeholder alarm is no reminder and stays; old events are not read; account.edit takes none for invitation_answers
# v4.4.536: Initial version (D-313)


def store_get(monkeypatch: pytest.MonkeyPatch, read: list[str]) -> None:
    """Every item the store gives from now on is noted in `read`."""
    real = PimStore.get

    def get(store: PimStore, collection: str, item: str) -> str:
        text = real(store, collection, item)
        read.append(text)
        return text

    monkeypatch.setattr(PimStore, "get", get)


def looked_at(setup: Setup, moment: datetime) -> None:
    path = setup.engine.directories.reminders_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"checked": "' + moment.isoformat() + '"}\n')


def check(setup: Setup) -> ReminderCheck:
    return reminder_check(setup.engine, NoParameters())


def event_in(setup: Setup, minutes: int, summary: str, reminder: tuple[str, ...], **more: str) -> str:
    start = (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0)
    return str(call(setup.engine, "event.add", calendar=local_calendar(setup), summary=summary, start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat(), reminder=reminder, **more).item)


def test_the_first_look_ever_gives_nothing_and_remembers_the_moment(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    assert check(setup) == ReminderCheck()
    assert setup.engine.directories.reminders_file.exists()


def test_a_notification_comes_once_also_after_a_restart(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",), location="Room 4")
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    first = check(setup)
    assert [(note.summary, note.location) for note in first.notes] == [("Dentist", "Room 4")]
    assert check(setup).notes == ()


def test_a_reminder_not_yet_due_waits(setup: Setup) -> None:
    event_in(setup, 60, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    assert check(setup).notes == ()


def test_one_missed_while_closed_counts_only_for_what_has_not_begun(setup: Setup) -> None:
    event_in(setup, 30, "Still to come", ("60",))
    event_in(setup, -30, "Over", ("60",))
    looked_at(setup, datetime.now(UTC) - timedelta(hours=3))
    assert [note.summary for note in check(setup).notes] == ["Still to come"]


def test_a_repeating_event_reminds_for_each_occurrence(setup: Setup) -> None:
    start = (datetime.now(UTC) - timedelta(days=30) + timedelta(minutes=10)).replace(microsecond=0)
    call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Daily", start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat(), rrule="FREQ=DAILY", reminder=("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Daily"]


def test_an_email_reminder_goes_to_your_own_address_and_is_no_notification(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("email:15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    result = check(setup)
    assert result.notes == () and result.mails == 1
    (received,) = setup.smtp.received
    assert b"Reminder: Dentist" in received.data and received.recipients == ("me@example.com",)


def test_with_the_box_off_this_computer_sends_no_reminder_email(setup: Setup) -> None:
    call(setup.engine, "account.edit", account=setup.account_id, reminder_emails="none")
    event_in(setup, 10, "Dentist", ("email:15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup) == ReminderCheck()
    assert setup.smtp.received == []


def test_an_email_reminder_is_stored_with_what_the_standard_asks(setup: Setup) -> None:
    item = event_in(setup, 10, "Dentist", ("email:15", "30"))
    shown = call(setup.engine, "event.show", item=item)
    assert [(r.action, r.minutes_before) for r in shown.reminders] == [("EMAIL", 15), ("DISPLAY", 30)]
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        text = store.get(shown.collection, item)
    alarm = parse_events(text)[0].master.reminders[0]
    assert "@" in alarm.to and "ACTION:EMAIL" in text and "ATTENDEE:mailto:" in text


def test_at_most_five_are_given_and_keep_leaves_one_whole(setup: Setup) -> None:
    with pytest.raises(UsageError):
        event_in(setup, 10, "Too many", ("1", "2", "3", "4", "5", "6"))
    item = event_in(setup, 10, "Dentist", ("email:15", "30"))
    call(setup.engine, "event.edit", item=item, reminder=("keep:0", "45"))
    assert [(r.action, r.minutes_before) for r in call(setup.engine, "event.show", item=item).reminders] == [("EMAIL", 15), ("DISPLAY", 45)]


def test_server_is_chosen_only_with_a_calendar_server_to_do_it() -> None:
    account = AccountSettings(make_account(), reminder_emails="server")
    assert account.sends_reminder_emails
    assert not replace(account, dav_url="https://dav.example.org/").sends_reminder_emails


PLACEHOLDER = "BEGIN:VALARM\r\nUID:from-the-server\r\nTRIGGER;VALUE=DATE-TIME:19760401T005545Z\r\nACTION:NONE\r\nEND:VALARM\r\n"


def with_placeholder(setup: Setup, item: str) -> None:
    """The alarm a server adds to every item, as it comes from the server."""
    collection = local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.put(collection, store.get(collection, item).replace("END:VEVENT", f"{PLACEHOLDER}END:VEVENT"), item)


def stored(setup: Setup, item: str) -> str:
    collection = local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return store.get(collection, item)


def test_a_placeholder_is_no_reminder_and_stays_when_the_reminders_change(setup: Setup) -> None:
    item = event_in(setup, 600, "Neighbourhood meeting", ())
    with_placeholder(setup, item)
    assert call(setup.engine, "event.show", item=item).reminders == ()
    call(setup.engine, "event.edit", item=item, reminder=("15",))
    assert [reminder.minutes_before for reminder in call(setup.engine, "event.show", item=item).reminders] == [15]
    assert "UID:from-the-server" in stored(setup, item) and stored(setup, item).count("BEGIN:VALARM") == 2
    call(setup.engine, "event.edit", item=item, clear=("reminder",))
    assert call(setup.engine, "event.show", item=item).reminders == ()
    assert "UID:from-the-server" in stored(setup, item) and stored(setup, item).count("BEGIN:VALARM") == 1


def test_a_placeholder_never_fires_and_a_real_reminder_beside_it_does(setup: Setup) -> None:
    item = event_in(setup, 10, "Dentist", ("15",))
    with_placeholder(setup, item)
    with_placeholder(setup, event_in(setup, 10, "Baker", ()))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


def test_the_look_reads_only_what_is_due(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    event_in(setup, -40 * 24 * 60, "Old", ("15",))
    event_in(setup, 600, "Later", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()  # each is worked out at the first look after it is stored
    event_in(setup, 10, "Coming", ("15",))
    read: list[str] = []
    store_get(monkeypatch, read)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Coming"]
    assert len(read) == 1 and "Coming" in read[0]
    read.clear()
    assert check(setup).notes == () and read == []


def test_a_series_begun_long_ago_and_a_reminder_days_after_an_event_still_come(setup: Setup) -> None:
    event_in(setup, 10 - 400 * 24 * 60, "Every day", ("15",), rrule="FREQ=DAILY")
    item = event_in(setup, -3 * 24 * 60 - 60, "Report back", ())
    collection = local_calendar(setup)
    after = "BEGIN:VALARM\r\nTRIGGER;RELATED=END:PT72H\r\nACTION:DISPLAY\r\nDESCRIPTION:Back\r\nEND:VALARM\r\nEND:VEVENT"
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.put(collection, store.get(collection, item).replace("END:VEVENT", after), item)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert sorted(note.summary for note in check(setup).notes) == ["Every day", "Report back"]


def test_unticking_the_confirmation_emails_is_taken_by_account_edit(setup: Setup) -> None:
    call(setup.engine, "account.edit", account=setup.account_id, invitation_answers="none")
    assert setup.engine.config().find(setup.account_id).invitation_answers == "none"
    with pytest.raises(UsageError):
        call(setup.engine, "account.edit", account=setup.account_id, invitation_answers="nobody")


FIXED_ALARM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:fixed
DTSTAMP:20260101T000000Z
DTSTART:20300105T100000Z
DTEND:20300105T110000Z
RRULE:FREQ=DAILY
SUMMARY:Series
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Series
TRIGGER;VALUE=DATE-TIME:20300101T090000Z
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:fixed
DTSTAMP:20260101T000000Z
RECURRENCE-ID:20300106T100000Z
DTSTART:20300106T120000Z
DTEND:20300106T130000Z
SUMMARY:Series
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Series
TRIGGER;VALUE=DATE-TIME:20300101T090000Z
END:VALARM
END:VEVENT
END:VCALENDAR
"""


def test_a_fixed_moment_reminds_once_also_with_a_changed_occurrence() -> None:
    moment = datetime(2030, 1, 1, 9, 1, tzinfo=UTC)
    assert len(_due_in(FIXED_ALARM, "account", moment - timedelta(minutes=2), moment, UTC, ())) == 1


def test_a_damaged_item_fails_only_itself(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    event_in(setup, 10, "Odd", ("15",))
    event_in(setup, 10, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))

    def odd(text: str, account: str, since: datetime, now: datetime, zone: tzinfo, yours: Collection[str], kind: CollectionKind) -> list[_Due]:
        if "Odd" in text:
            raise TypeError("odd")
        return real(text, account, since, now, zone, yours, kind)

    real = _due_in
    monkeypatch.setattr(commands_reminders, "_due_in", odd)
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


def test_an_email_reminder_with_nowhere_to_go_comes_as_a_notification(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    def nobody(engine: Engine, account: str) -> AccountSettings | None:
        return None

    monkeypatch.setattr(commands_reminders, "organizing_account", nobody)
    event_in(setup, 10, "Dentist", ("email:15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


# --- the index knows which reminders are due (D-316) ---------------------------------------------------------------------------------------


STAMP = "%Y%m%dT%H%M%SZ"


def alarm(trigger: str) -> str:
    return f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Look\r\nTRIGGER{trigger}\r\nEND:VALARM\r\n"


def vevent(start: datetime, summary: str, more: str = "", uid: str = "made") -> str:
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{start.strftime(STAMP)}\r\nDTEND:{(start + timedelta(hours=1)).strftime(STAMP)}\r\n"
        f"SUMMARY:{summary}\r\n{more}END:VEVENT\r\n"
    )


def calendar_of(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def put(setup: Setup, text: str, item: str | None = None) -> str:
    """As a synchronisation stores what the server holds."""
    collection = local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return store.put(collection, text, item)


def due_in_index(setup: Setup, moment: datetime) -> list[DueReminder]:
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return store.index.reminders_due(int(moment.timestamp()))


def far_ahead() -> datetime:
    return datetime.now(UTC) + timedelta(days=100_000)


def minute() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def test_a_reminder_40_days_after_its_event_waits_for_its_moment(setup: Setup) -> None:
    start = minute() - timedelta(days=40, hours=1, minutes=-5)
    put(setup, calendar_of(vevent(start, "Report back", alarm(";RELATED=END:P40D"))))
    looked_at(setup, datetime.now(UTC) + timedelta(minutes=2))
    assert check(setup).notes == ()  # not yet due: the first look worked it out
    assert len(due_in_index(setup, far_ahead())) == 1


def test_a_reminder_40_days_after_its_event_comes(setup: Setup) -> None:
    start = minute() - timedelta(days=40, hours=1, minutes=3)
    put(setup, calendar_of(vevent(start, "Report back", alarm(";RELATED=END:P40D"))))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Report back"]


def moved_occurrence(moved_to: datetime) -> str:
    """A daily series that ended 60 days ago, with one date moved to a time that is still to come."""
    first = minute() - timedelta(days=70)
    until = (first + timedelta(days=9)).strftime(STAMP)
    master = vevent(first, "Series", f"RRULE:FREQ=DAILY;UNTIL={until}\r\n{alarm(':-PT15M')}", "series")
    moved = vevent(moved_to, "Series moved", f"RECURRENCE-ID:{(first + timedelta(days=4)).strftime(STAMP)}\r\n{alarm(':-PT15M')}", "series")
    return calendar_of(master, moved)


def test_a_moved_occurrence_of_a_series_that_ended_long_ago_comes(setup: Setup) -> None:
    put(setup, moved_occurrence(minute() + timedelta(minutes=10)))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Series moved"]


def test_a_moved_occurrence_far_ahead_is_the_next_moment_of_its_ended_series() -> None:
    moved_to = minute() + timedelta(days=500)
    assert _next_moment(moved_occurrence(moved_to), datetime.now(UTC), UTC) == moved_to - timedelta(minutes=15)


def test_a_series_without_an_end_keeps_only_its_next_reminder() -> None:
    first = minute() - timedelta(days=4000)
    text = calendar_of(vevent(first, "Daily", f"RRULE:FREQ=DAILY\r\n{alarm(':-PT15M')}"))
    moment = datetime.now(UTC)
    found = _next_moment(text, moment, UTC)
    assert found is not None and moment < found <= moment + timedelta(days=1)
    following = _next_moment(text, found, UTC)  # after it fired the next is worked out
    assert following == found + timedelta(days=1)


def test_a_series_is_given_again_for_each_of_its_reminders(setup: Setup) -> None:
    first = minute() - timedelta(days=30) + timedelta(minutes=30)
    put(setup, calendar_of(vevent(first, "Daily", f"RRULE:FREQ=DAILY\r\n{alarm(':-PT15M')}")))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    check(setup)
    assert due_in_index(setup, datetime.now(UTC) + timedelta(minutes=14)) == []
    assert len(due_in_index(setup, datetime.now(UTC) + timedelta(minutes=16))) == 1


def test_an_earlier_moved_date_comes_before_the_next_of_its_series() -> None:
    first = minute() - timedelta(days=30) + timedelta(hours=5)
    moved = vevent(first + timedelta(days=30) - timedelta(hours=3), "Moved", f"RECURRENCE-ID:{(first + timedelta(days=31)).strftime(STAMP)}\r\n{alarm(':-PT15M')}")
    text = calendar_of(vevent(first, "Daily", f"RRULE:FREQ=DAILY\r\n{alarm(':-PT15M')}"), moved)
    assert _next_moment(text, datetime.now(UTC), UTC) == first + timedelta(days=30) - timedelta(hours=3, minutes=15)


def test_several_reminders_give_the_first_to_come_also_after_the_event() -> None:
    start = minute() + timedelta(hours=1)
    text = calendar_of(vevent(start, "Dentist", alarm(":-PT30M") + alarm(";RELATED=END:PT2H") + alarm(":-PT5M")))
    assert _next_moment(text, datetime.now(UTC), UTC) == start - timedelta(minutes=30)
    assert _next_moment(text, start, UTC) == start + timedelta(hours=3)
    assert _next_moment(text, start + timedelta(hours=4), UTC) is None


def test_a_reminder_at_a_fixed_moment_counts_once() -> None:
    moment = minute() + timedelta(days=3)
    text = calendar_of(vevent(minute() + timedelta(days=5), "Dentist", alarm(f";VALUE=DATE-TIME:{moment.strftime(STAMP)}")))
    assert _next_moment(text, datetime.now(UTC), UTC) == moment
    assert _next_moment(text, moment, UTC) is None


def test_a_series_that_only_a_moved_date_reminds_of_is_walked_step_by_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_reminders, "MAXIMUM_OCCURRENCES", 10)
    first = minute() - timedelta(days=3)
    moved_to = minute() + timedelta(days=25)
    moved = vevent(moved_to, "Moved", f"RECURRENCE-ID:{(first + timedelta(days=40)).strftime(STAMP)}\r\n{alarm(':-PT15M')}")
    text = calendar_of(vevent(first, "Daily", "RRULE:FREQ=DAILY\r\n"), moved)
    moment = datetime.now(UTC)
    for _ in range(10):
        found = _next_moment(text, moment, UTC)
        assert found is not None and found > moment
        if found == moved_to - timedelta(minutes=15):
            break
        moment = found
    else:
        pytest.fail("the walk never reached the moved date")


def test_a_floating_time_west_of_greenwich_comes_at_its_own_clock_time() -> None:
    zone = ZoneInfo("America/New_York")
    start = datetime(2030, 1, 5, 9, 0, tzinfo=zone)
    wall = "DTSTART:20300105T090000\r\nDTEND:20300105T100000\r\n"
    text = calendar_of(f"BEGIN:VEVENT\r\nUID:f\r\nDTSTAMP:20260101T000000Z\r\n{wall}SUMMARY:Floating\r\n{alarm(':-PT15M')}END:VEVENT\r\n")
    trigger = start - timedelta(minutes=15)
    assert _next_moment(text, trigger - timedelta(minutes=5), zone) == trigger
    moment = trigger.astimezone(UTC) + timedelta(seconds=30)
    assert [due.note.summary for due in _due_in(text, "account", moment - timedelta(minutes=1), moment, zone, ())] == ["Floating"]


def test_another_time_zone_works_every_moment_out_again(setup: Setup) -> None:
    event_in(setup, 600, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    check(setup)
    assert due_in_index(setup, datetime.now(UTC)) == []
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.index.keep_reminder_zone("Elsewhere/Town")
        assert len(store.index.reminders_due(int(datetime.now(UTC).timestamp()))) == 1
        (item,) = store.index.reminders_due(int(datetime.now(UTC).timestamp()))
        store.index.keep_reminder_zone("Elsewhere/Town")
        assert store.index.reminders_due(int(datetime.now(UTC).timestamp())) == [item]


def test_a_change_by_a_sync_is_worked_out_again(setup: Setup) -> None:
    item = put(setup, calendar_of(vevent(minute() + timedelta(days=5), "Dentist", alarm(":-PT15M"))))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    check(setup)
    put(setup, calendar_of(vevent(minute() + timedelta(minutes=10), "Dentist moved", alarm(":-PT15M"))), item)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist moved"]


def test_a_reminder_taken_away_by_a_sync_leaves_nothing_to_read(setup: Setup) -> None:
    item = put(setup, calendar_of(vevent(minute() + timedelta(days=5), "Dentist", alarm(":-PT15M"))))
    put(setup, calendar_of(vevent(minute() + timedelta(days=5), "Dentist")), item)
    assert due_in_index(setup, far_ahead()) == []


def test_a_look_that_read_an_item_before_it_changed_leaves_the_new_text_to_the_next_look(setup: Setup) -> None:
    item = put(setup, calendar_of(vevent(minute() + timedelta(days=5), "Dentist", alarm(":-PT15M"))))
    (read,) = due_in_index(setup, datetime.now(UTC))
    put(setup, calendar_of(vevent(minute() + timedelta(days=2), "Dentist", alarm(":-PT15M"))), item)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.index.set_reminders([(read, int(far_ahead().timestamp()))])
    (waiting,) = due_in_index(setup, datetime.now(UTC))
    assert waiting.due < 0 and waiting.due != read.due


def test_a_deleted_event_has_no_due_moment(setup: Setup) -> None:
    item = event_in(setup, 10, "Dentist", ("15",))
    call(setup.engine, "event.delete", item=item)
    assert due_in_index(setup, far_ahead()) == []
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()


def test_an_event_without_reminders_is_never_read(setup: Setup) -> None:
    event_in(setup, 10, "Lunch", ())
    assert due_in_index(setup, far_ahead()) == []


def test_a_rebuild_makes_the_moments_again(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    check(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        assert store.rebuild_index() == 1
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


def index_file(setup: Setup) -> Path:
    return setup.engine.directories.pim_directory(LOCAL_ACCOUNT_ID) / "pim.sqlite"


def back_to_format_3(setup: Setup) -> None:
    connection = sqlite3.connect(index_file(setup))
    connection.execute("DROP INDEX items_by_reminder")
    connection.execute("ALTER TABLE items DROP COLUMN reminder_due")
    connection.execute("UPDATE meta SET value = '3' WHERE key = 'schema'")
    connection.commit()
    connection.close()


def file_format(setup: Setup) -> tuple[str, bool]:
    connection = sqlite3.connect(index_file(setup))
    found = str(connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0])
    columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(items)")]
    connection.close()
    return found, "reminder_due" in columns


def test_the_upgrade_gives_existing_events_their_moment_and_keeps_the_items(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    event_in(setup, 20, "Lunch", ())
    back_to_format_3(setup)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist"]
    assert file_format(setup) == ("5", True)
    assert due_in_index(setup, far_ahead()) == []  # the one reminder fired, the other event has none: nothing is left to read
    assert check(setup).notes == ()


class Stop(BaseException):
    """The program ends here."""


@pytest.mark.parametrize("step", range(1, 6))
def test_the_upgrade_interrupted_at_any_step_leaves_the_old_format_and_is_done_at_the_next_start(setup: Setup, monkeypatch: pytest.MonkeyPatch, step: int) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    back_to_format_3(setup)
    made: list[int] = []

    class Stopping(sqlite3.Connection):
        @override
        def execute(self, sql: str, parameters: Sequence[str | int] = ()) -> sqlite3.Cursor:  # pyrefly: ignore[bad-override]
            if sql.startswith(("ALTER", "UPDATE", "CREATE INDEX")):
                made.append(1)
                if len(made) == step:
                    raise Stop
            return super().execute(sql, parameters)

    real = sqlite3.connect

    def connect(path: Path, isolation_level: None, timeout: float) -> sqlite3.Connection:
        return real(path, isolation_level=isolation_level, timeout=timeout, factory=Stopping)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(Stop):
        PimIndex(index_file(setup))
    monkeypatch.undo()
    assert file_format(setup) == ("3", False)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert [note.summary for note in check(setup).notes] == ["Dentist"]
    assert file_format(setup) == ("5", True)


def test_a_look_that_stops_before_the_last_look_is_written_finds_it_all_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    moment = datetime.now(UTC) - timedelta(minutes=6)
    looked_at(setup, moment)

    def stopped(engine: Engine, when: datetime) -> None:
        raise Stop

    with monkeypatch.context() as patch:
        patch.setattr(commands_reminders, "_write_checked", stopped)
        with pytest.raises(Stop):
            check(setup)
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


def test_moments_that_cannot_be_written_cost_no_reminder_and_nothing_comes_twice(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))

    def refused(index: PimIndex, moments: object) -> None:
        raise sqlite3.OperationalError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(PimIndex, "set_reminders", refused)
        assert [note.summary for note in check(setup).notes] == ["Dentist"]
    assert check(setup).notes == ()
    assert due_in_index(setup, datetime.now(UTC)) == []


def test_a_look_while_the_one_before_it_runs_gives_nothing_and_the_reminder_comes_once(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    with _LOOKING:
        assert check(setup) == ReminderCheck()
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


def test_a_look_stops_when_the_program_ends_and_the_next_start_finds_it_all(setup: Setup) -> None:
    event_in(setup, 10, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    setup.engine.stop_event.set()
    assert check(setup) == ReminderCheck()
    setup.engine.stop_event.clear()
    assert [note.summary for note in check(setup).notes] == ["Dentist"]


# --- a task reminds through the same look (D-316) -------------------------------------------------------------------------------------------------


def local_list(setup: Setup) -> str:
    local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (tasks,) = store.collections(CollectionKind.TASKS)
        return tasks.collection_id


def task_in(setup: Setup, minutes: int, summary: str, reminder: tuple[str, ...], **more: str | int) -> str:
    due = (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0)
    return str(call(setup.engine, "task.add", list=local_list(setup), summary=summary, due=due.isoformat(), reminder=reminder, **more).item)


def fixed_in(minutes: int) -> str:
    return "at:" + (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def vtodo(due: datetime | None, summary: str, more: str = "", uid: str = "made-task") -> str:
    moment = f"DUE:{due.strftime(STAMP)}\r\n" if due is not None else ""
    return f"BEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20260101T000000Z\r\n{moment}SUMMARY:{summary}\r\n{more}END:VTODO\r\n"


def put_task(setup: Setup, text: str, item: str | None = None) -> str:
    """As a synchronisation stores what the server holds."""
    collection = local_list(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return store.put(collection, text, item)


def summaries(setup: Setup) -> list[str]:
    return [note.summary for note in check(setup).notes]


def test_a_task_reminds_minutes_before_its_due_time_and_once(setup: Setup) -> None:
    task_in(setup, 10, "Pay rent", ("15",), location="Bank")
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    (note,) = check(setup).notes
    assert (note.summary, note.location) == ("Pay rent", "Bank") and note.when
    assert check(setup).notes == ()


def test_a_task_reminds_at_its_due_time(setup: Setup) -> None:
    task_in(setup, -2, "Pay rent", ("0",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=3))
    assert summaries(setup) == ["Pay rent"]


def test_a_task_not_yet_due_for_a_reminder_waits_and_then_comes(setup: Setup) -> None:
    item = task_in(setup, 60, "Pay rent", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=1))
    assert check(setup).notes == ()
    (waiting,) = [one for one in due_in_index(setup, far_ahead()) if one.item_id == item]
    assert waiting.kind is CollectionKind.TASKS and waiting.due > 0
    assert due_in_index(setup, datetime.now(UTC)) == []  # not read again until its moment


def test_a_reminder_of_a_task_at_a_fixed_time_comes(setup: Setup) -> None:
    task_in(setup, 600, "Pay rent", (fixed_in(-2),))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=3))
    assert summaries(setup) == ["Pay rent"]


def test_minutes_count_from_the_due_time_also_when_the_alarm_names_the_start() -> None:
    """The task editor says minutes before the due time, and a reminder set before there was a due time stays RELATED=START."""
    due = minute() + timedelta(days=3)
    text = calendar_of(vtodo(due, "Pay rent", f"DTSTART:{(due - timedelta(days=2)).strftime(STAMP)}\r\n{alarm(':-PT15M')}"))
    assert _next_moment(text, minute(), UTC, CollectionKind.TASKS) == due - timedelta(minutes=15)


def test_a_task_due_on_a_day_reminds_from_the_start_of_that_day() -> None:
    day = (datetime.now(UTC) + timedelta(days=3)).date()
    text = calendar_of(vtodo(None, "Pay rent", f"DUE;VALUE=DATE:{day.strftime('%Y%m%d')}\r\n{alarm(';RELATED=END:-P1D')}"))
    assert _next_moment(text, minute(), UTC, CollectionKind.TASKS) == datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=1)


def test_a_repeating_task_keeps_only_its_next_reminder_and_goes_on_after_it_fired(setup: Setup) -> None:
    first = minute() - timedelta(days=3)  # long past due and never completed: the rule goes on, as a series of events does
    text = calendar_of(vtodo(first, "Water plants", f"RRULE:FREQ=DAILY\r\n{alarm(';RELATED=END:-PT15M')}"))
    now = minute()
    found = _next_moment(text, now, UTC, CollectionKind.TASKS)
    assert found is not None and now < found <= now + timedelta(days=1)
    following = _next_moment(text, found, UTC, CollectionKind.TASKS)
    assert following == found + timedelta(days=1)
    put_task(setup, text)
    looked_at(setup, found - timedelta(minutes=1))
    assert [one.due for one in due_in_index(setup, far_ahead())] != []
    assert len(_due_in(text, "account", found - timedelta(minutes=1), found + timedelta(minutes=1), UTC, (), CollectionKind.TASKS)) == 1


def test_a_repeating_task_that_is_completed_goes_on_from_its_next_time(setup: Setup) -> None:
    item = task_in(setup, 10, "Water plants", ("15",), rrule="FREQ=DAILY")
    call(setup.engine, "task.complete", item=item)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()  # this time is done
    (next_time,) = [one for one in due_in_index(setup, far_ahead()) if one.item_id == item]
    assert timedelta(hours=23) < datetime.fromtimestamp(next_time.due, UTC) - datetime.now(UTC) < timedelta(hours=25)


def test_a_moved_occurrence_of_a_task_counts_like_any_other() -> None:
    first = minute() - timedelta(days=3)
    moved_to = minute() + timedelta(days=25)
    moved = vtodo(moved_to, "Moved", f"RECURRENCE-ID:{(first + timedelta(days=40)).strftime(STAMP)}\r\n{alarm(';RELATED=END:-PT15M')}")
    text = calendar_of(vtodo(first, "Daily", "RRULE:FREQ=DAILY;COUNT=5\r\n"), moved)
    assert _next_moment(text, minute(), UTC, CollectionKind.TASKS) == moved_to - timedelta(minutes=15)


def test_a_change_by_a_sync_moves_the_reminder_of_a_task(setup: Setup) -> None:
    far = minute() + timedelta(days=2)
    item = put_task(setup, calendar_of(vtodo(far, "Pay rent", alarm(";RELATED=END:-PT15M"))))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()
    put_task(setup, calendar_of(vtodo(minute() + timedelta(minutes=10), "Pay rent", alarm(";RELATED=END:-PT15M"))), item)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert summaries(setup) == ["Pay rent"]


def test_a_reminder_taken_away_by_a_sync_leaves_a_task_with_nothing_to_read(setup: Setup) -> None:
    item = put_task(setup, calendar_of(vtodo(minute() + timedelta(minutes=10), "Pay rent", alarm(";RELATED=END:-PT15M"))))
    put_task(setup, calendar_of(vtodo(minute() + timedelta(minutes=10), "Pay rent")), item)
    assert due_in_index(setup, far_ahead()) == []


def test_a_deleted_task_has_no_due_moment_and_gives_nothing(setup: Setup) -> None:
    item = task_in(setup, 10, "Pay rent", ("15",))
    call(setup.engine, "task.delete", item=item)
    assert due_in_index(setup, far_ahead()) == []
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()


def test_one_missed_while_closed_counts_only_for_a_task_not_yet_due(setup: Setup) -> None:
    task_in(setup, 30, "Not yet due", ("60",))
    task_in(setup, -10, "Past due", ("20",))
    looked_at(setup, datetime.now(UTC) - timedelta(hours=2))
    assert summaries(setup) == ["Not yet due"]


def test_a_task_without_a_due_time_that_missed_its_reminder_while_closed_still_gets_it(setup: Setup) -> None:
    call(setup.engine, "task.add", list=local_list(setup), summary="Call the bank", reminder=(fixed_in(-60),))
    looked_at(setup, datetime.now(UTC) - timedelta(hours=2))
    assert summaries(setup) == ["Call the bank"]


def test_a_finished_task_fires_nothing_and_is_not_read(setup: Setup) -> None:
    item = task_in(setup, 10, "Pay rent", ("15",))
    call(setup.engine, "task.complete", item=item)
    assert due_in_index(setup, far_ahead()) == []
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()


def test_a_task_finished_by_a_sync_fires_nothing(setup: Setup) -> None:
    put_task(setup, calendar_of(vtodo(minute() + timedelta(minutes=10), "Pay rent", f"STATUS:COMPLETED\r\n{alarm(';RELATED=END:-PT15M')}")))
    assert due_in_index(setup, far_ahead()) == []


def test_a_task_opened_again_counts_again(setup: Setup) -> None:
    item = task_in(setup, 10, "Pay rent", ("15",))
    call(setup.engine, "task.complete", item=item)
    call(setup.engine, "task.reopen", item=item)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert summaries(setup) == ["Pay rent"]


def test_a_cancelled_task_is_inert_and_counts_again_when_its_status_changes_back(setup: Setup) -> None:
    item = task_in(setup, 10, "Pay rent", ("15",))
    call(setup.engine, "task.edit", item=item, status="cancelled")
    assert due_in_index(setup, far_ahead()) == []
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert check(setup).notes == ()
    call(setup.engine, "task.edit", item=item, status="needs-action")
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert summaries(setup) == ["Pay rent"]


def test_a_repeating_task_cancelled_by_a_sync_fires_nothing_at_any_of_its_times() -> None:
    text = calendar_of(vtodo(minute() - timedelta(days=3), "Daily", f"STATUS:CANCELLED\r\nRRULE:FREQ=DAILY\r\n{alarm(';RELATED=END:-PT15M')}"))
    assert _next_moment(text, minute(), UTC, CollectionKind.TASKS) is None


def test_a_task_without_a_due_date_fires_only_a_reminder_at_a_fixed_time(setup: Setup) -> None:
    start = (datetime.now(UTC) + timedelta(minutes=10)).replace(microsecond=0).isoformat()
    call(setup.engine, "task.add", list=local_list(setup), summary="Counted from the start", start=start, reminder=("15",))
    call(setup.engine, "task.add", list=local_list(setup), summary="No dates", reminder=("15",))
    call(setup.engine, "task.add", list=local_list(setup), summary="Fixed", reminder=(fixed_in(-2),))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert summaries(setup) == ["Fixed"]
    assert due_in_index(setup, far_ahead()) == []


def test_a_repeating_task_without_a_due_date_gets_no_made_up_due_time() -> None:
    start = minute() - timedelta(days=3)
    text = calendar_of(vtodo(None, "Daily", f"DTSTART:{start.strftime(STAMP)}\r\nRRULE:FREQ=DAILY\r\n{alarm(':-PT15M')}"))
    assert _next_moment(text, minute(), UTC, CollectionKind.TASKS) is None


def test_the_note_and_the_mail_of_a_task_without_a_due_date_have_no_empty_when(setup: Setup) -> None:
    moment = (minute() - timedelta(minutes=2)).strftime(STAMP)
    call(setup.engine, "task.add", list=local_list(setup), summary="Call the bank", reminder=(fixed_in(-2),))
    put_task(setup, calendar_of(vtodo(None, "Mail the bank", f"BEGIN:VALARM\r\nACTION:EMAIL\r\nSUMMARY:Bank\r\nDESCRIPTION:Bank\r\nTRIGGER;VALUE=DATE-TIME:{moment}\r\nEND:VALARM\r\n")))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=3))
    result = check(setup)
    assert [(note.summary, note.when) for note in result.notes] == [("Call the bank", "")] and result.mails == 1
    (received,) = setup.smtp.received
    assert b"Reminder: Mail the bank" in received.data and b"When:" not in received.data


def test_an_email_alarm_of_a_task_from_elsewhere_is_mailed_like_an_event_s(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    mails: list[_Due] = []

    def send(_engine: Engine, due: _Due) -> bool:
        mails.append(due)
        return True

    monkeypatch.setattr(commands_reminders, "_send", send)
    text = vtodo(minute() + timedelta(minutes=10), "Pay rent", "BEGIN:VALARM\r\nACTION:EMAIL\r\nSUMMARY:Pay\r\nDESCRIPTION:Pay\r\nTRIGGER;RELATED=END:-PT15M\r\nEND:VALARM\r\n")
    put_task(setup, calendar_of(text))
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    result = check(setup)
    assert result.notes == () and result.mails == 1 and [one.note.summary for one in mails] == ["Pay rent"]


def back_to_format_4(setup: Setup) -> None:
    connection = sqlite3.connect(index_file(setup))
    connection.execute("UPDATE items SET reminder_due = NULL WHERE kind = 'tasks'")
    connection.execute("UPDATE meta SET value = '4' WHERE key = 'schema'")
    connection.commit()
    connection.close()


def test_the_upgrade_gives_existing_tasks_their_moment_and_leaves_events_as_they_are(setup: Setup) -> None:
    task_in(setup, 10, "Pay rent", ("15",))
    task_in(setup, 20, "Tidy up", ())
    event_in(setup, 300, "Dentist", ("15",))
    looked_at(setup, datetime.now(UTC) - timedelta(seconds=1))
    check(setup)  # every moment worked out
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    events_before = due_in_index(setup, far_ahead())
    back_to_format_4(setup)
    assert file_format(setup) == ("4", True)
    assert summaries(setup) == ["Pay rent"]
    assert file_format(setup) == ("5", True)
    assert [one for one in due_in_index(setup, far_ahead()) if one.kind is CollectionKind.TASKS] == []  # the one fired, the other has none
    assert due_in_index(setup, far_ahead()) == [one for one in events_before if one.kind is CollectionKind.EVENTS]
    assert check(setup).notes == ()


def test_format_3_comes_to_format_5_with_events_and_tasks(setup: Setup) -> None:
    task_in(setup, 10, "Pay rent", ("15",))
    event_in(setup, 10, "Dentist", ("15",))
    back_to_format_3(setup)
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert sorted(summaries(setup)) == ["Dentist", "Pay rent"]
    assert file_format(setup) == ("5", True)


@pytest.mark.parametrize("step", range(1, 3))
def test_the_upgrade_for_tasks_interrupted_at_any_step_leaves_the_old_format_and_is_done_at_the_next_start(setup: Setup, monkeypatch: pytest.MonkeyPatch, step: int) -> None:
    task_in(setup, 10, "Pay rent", ("15",))
    back_to_format_4(setup)
    made: list[int] = []

    class Stopping(sqlite3.Connection):
        @override
        def execute(self, sql: str, parameters: Sequence[str | int] = ()) -> sqlite3.Cursor:  # pyrefly: ignore[bad-override]
            if sql.startswith("UPDATE"):
                made.append(1)
                if len(made) == step:
                    raise Stop
            return super().execute(sql, parameters)

    real = sqlite3.connect

    def connect(path: Path, isolation_level: None, timeout: float) -> sqlite3.Connection:
        return real(path, isolation_level=isolation_level, timeout=timeout, factory=Stopping)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(Stop):
        PimIndex(index_file(setup))
    monkeypatch.undo()
    assert file_format(setup) == ("4", True)
    connection = sqlite3.connect(index_file(setup))
    assert connection.execute("SELECT COUNT(*) FROM items WHERE kind = 'tasks' AND reminder_due IS NOT NULL").fetchone() == (0,)
    connection.close()
    looked_at(setup, datetime.now(UTC) - timedelta(minutes=6))
    assert summaries(setup) == ["Pay rent"]
    assert file_format(setup) == ("5", True)

