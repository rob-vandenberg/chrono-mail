from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from chrono_mail.config import AccountSettings
from chrono_mail.engine import Engine, UsageError, commands_reminders
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_reminders import ReminderCheck, _Due, _due_in, reminder_check
from chrono_mail.pim.ical import parse_events
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.core import LOCAL_ACCOUNT_ID
from tests.test_config import make_account
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import local_calendar

# --- Version ------------------------------------------------------------
__version__ = "test_engine_reminders.py 4.4.536"

# --- Version history ----------------------------------------------------
# v4.4.536: Initial version (D-313)


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

    def odd(text: str, account: str, since: datetime, now: datetime, zone: tzinfo, yours: Collection[str]) -> list[_Due]:
        if "Odd" in text:
            raise TypeError("odd")
        return real(text, account, since, now, zone, yours)

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
