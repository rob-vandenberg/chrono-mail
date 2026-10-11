from datetime import datetime
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.engine import UsageError
from chrono_mail.engine.recurring import Repeat, add_months, describe_repeat, index_after, load_repeats, next_due, save_repeats, slot_time
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_schedule import NOW, at, iso, rows

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_recurring.py 0.0.67"

# --- Version history ----------------------------------------------------
# v0.0.67: Initial tests of messages that are sent again and again

DAY = 86_400


def local(year: int, month: int, day: int, hour: int = 9) -> int:
    return int(datetime(year, month, day, hour).timestamp())


def repeating(setup: Setup, **extra: Any) -> Any:  # noqa: ANN401
    values: dict[str, Any] = {"repeat_days": 5, "send_at": iso(3600), "repeat_until": iso(12 * DAY)}
    values.update(extra)
    values = {key: value for key, value in values.items() if value is not None}
    return call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Again", body="hello", **values)


def test_months_keep_counting_from_the_start_and_use_the_last_day_of_a_short_month() -> None:
    repeat = Repeat(local(2027, 1, 31), None, months=1)
    assert [datetime.fromtimestamp(slot_time(repeat, number) or 0).strftime("%m-%d") for number in range(4)] == ["01-31", "02-28", "03-31", "04-30"]
    assert add_months(datetime(2027, 12, 15), 2) == datetime(2028, 2, 15)


def test_an_interval_can_combine_months_weeks_and_days() -> None:
    repeat = Repeat(local(2027, 1, 1), None, months=1, weeks=2, days=1)
    assert datetime.fromtimestamp(slot_time(repeat, 1) or 0).strftime("%Y-%m-%d") == "2027-02-16"
    assert describe_repeat(repeat) == "every 1 month and 2 weeks and 1 day"
    assert describe_repeat(Repeat(local(2027, 1, 1), local(2027, 6, 1), days=5)).startswith("every 5 days, until 2027-06-01")


def test_missed_sends_are_skipped_and_the_end_is_respected() -> None:
    repeat = Repeat(1_000_000_000, 1_000_000_000 + 10 * DAY + 100, days=1)
    assert index_after(repeat, 1_000_000_000 + 3 * DAY + 5) == 4
    assert next_due(Repeat(1_000_000_000, 1_000_000_000 + 100, days=1, index=1)) is None
    assert next_due(repeat) == 1_000_000_000


@pytest.mark.parametrize(
    "arguments",
    [{"days": 0}, {"days": -1}, {"days": 4000}, {"months": 121, "days": 1}, {"days": 1, "end": 5}],
)
def test_bad_intervals_and_ends_are_refused(arguments: dict[str, int]) -> None:
    values: dict[str, Any] = {"start": 10, "end": None, "months": 0, "weeks": 0, "days": 0}
    values.update(arguments)
    with pytest.raises(UsageError):
        Repeat(values["start"], values["end"], values["months"], values["weeks"], values["days"])


def test_the_schedule_file_survives_a_round_trip_and_damage(tmp_path: Any) -> None:  # noqa: ANN401
    path = tmp_path / "recurring.json"
    save_repeats(path, {"a": Repeat(10, 99, months=1, sent=2, paused=True, last_error="x")})
    assert load_repeats(path) == {"a": Repeat(10, 99, months=1, sent=2, paused=True, last_error="x")}
    path.write_text("{ not json")
    assert load_repeats(path) == {}
    path.write_text('{"a": {"start": "x"}, "b": 5}')
    assert load_repeats(path) == {}


def test_a_message_is_sent_every_interval_until_the_end_and_then_the_pattern_goes(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup)
    assert not result.sent and result.scheduled and setup.smtp.received == []
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.repeat.startswith("every 5 days, until") and not info.paused and info.times_sent == 0
    at(setup, 2 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 1
    assert len(setup.smtp.received) == 1 and b"X-Chrono-Mail" not in setup.smtp.received[0].data
    assert [row.subject for row in rows(setup, "Outbox")] == ["Again"]  # the pattern stays
    assert call(setup.engine, "mail.send-due").sent == 0
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.times_sent == 1
    at(setup, 6 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 2
    at(setup, 30 * DAY)  # the program was off for a long time: one send, not one for every missed day
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 3
    call(setup.engine, "mail.send-due")  # the series is over now
    assert rows(setup, "Outbox") == [] and call(setup.engine, "mail.scheduled") == ()
    assert [row.subject for row in rows(setup, "Sent")] == ["Again"] * 3
    at(setup, 60 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 0 and len(setup.smtp.received) == 3


def test_without_an_end_the_message_repeats_for_ever(setup: Setup) -> None:
    at(setup, 0)
    repeating(setup, repeat_until=None)
    for number in range(1, 6):
        at(setup, number * 5 * DAY + 7200)
        assert call(setup.engine, "mail.send-due").sent == 1
    assert len(setup.smtp.received) == 5 and len(call(setup.engine, "mail.scheduled")) == 1


def test_pausing_stops_the_sends_and_resuming_skips_what_was_missed(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup, repeat_until=None)
    ids = {"account": setup.account_id, "folder": "Outbox", "message": result.message_id}
    call(setup.engine, "mail.repeat-pause", **ids)
    at(setup, 12 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 0 and setup.smtp.received == []
    assert call(setup.engine, "mail.scheduled")[0].paused
    state = call(setup.engine, "mail.repeat-resume", **ids)
    assert not state.paused
    assert call(setup.engine, "mail.send-due").sent == 0  # nothing is made up
    at(setup, 16 * DAY + 7200)
    assert call(setup.engine, "mail.send-due").sent == 1


def test_sending_now_adds_a_copy_without_changing_the_schedule(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup)
    before = call(setup.engine, "mail.scheduled")[0]
    done = call(setup.engine, "mail.send-due", message=result.message_id)
    assert done.sent == 1 and len(setup.smtp.received) == 1
    after = call(setup.engine, "mail.scheduled")[0]
    assert (after.seconds, after.message_id) == (before.seconds, before.message_id)


def test_cancelling_removes_the_pattern_and_what_was_sent_stays(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup)
    at(setup, 2 * 3600)
    call(setup.engine, "mail.send-due")
    call(setup.engine, "mail.repeat-cancel", account=setup.account_id, folder="Outbox", message=result.message_id)
    assert rows(setup, "Outbox") == [] and call(setup.engine, "mail.scheduled") == ()
    at(setup, 20 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 0 and len(setup.smtp.received) == 1
    assert [row.subject for row in rows(setup, "Sent")] == ["Again"]
    with pytest.raises(UsageError):
        call(setup.engine, "mail.repeat-pause", account=setup.account_id, folder="Outbox", message=result.message_id)


def test_the_interval_end_and_start_can_be_changed(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup)
    ids = {"account": setup.account_id, "folder": "Outbox", "message": result.message_id}
    state = call(setup.engine, "mail.repeat-change", **ids, days=0, weeks=1, until="never")
    assert state.repeat == "every 1 week"
    at(setup, 2 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 1
    at(setup, 6 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 0  # a week, not five days
    at(setup, 8 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 1
    with pytest.raises(UsageError):
        call(setup.engine, "mail.repeat-change", **ids, days=0, weeks=0)


def test_taking_the_message_back_ends_the_schedule(setup: Setup) -> None:
    at(setup, 0)
    result = repeating(setup)
    call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Outbox", message=result.message_id)
    assert call(setup.engine, "mail.scheduled") == ()
    at(setup, 20 * DAY)
    assert call(setup.engine, "mail.send-due").sent == 0 and setup.smtp.received == []
    assert [row.subject for row in rows(setup, "Drafts")] == ["Again"]


def test_repeating_needs_a_start_an_interval_and_a_sensible_end(setup: Setup) -> None:
    at(setup, 0)
    base = {"account": setup.account_id, "to": "you@example.com", "subject": "x"}
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", **base, repeat_days=2)  # no start
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", **base, send_at=iso(3600), repeat_until=iso(7200))  # an end but no interval
    with pytest.raises(UsageError):
        call(setup.engine, "mail.send", **base, send_at=iso(7200), repeat_days=1, repeat_until=iso(3600))  # the end is before the start
    assert call(setup.engine, "mail.scheduled") == () and rows(setup, "Outbox") == []
    assert NOW > 0


def test_a_failed_send_is_tried_again_three_times_and_then_the_series_goes_on(setup: Setup) -> None:
    at(setup, 0)
    repeating(setup, repeat_until=None)
    setup.smtp._size_limit = 10  # noqa: SLF001 - the server refuses everything
    at(setup, 2 * 3600)
    for attempt in range(3):
        done = call(setup.engine, "mail.send-due")
        assert done.sent == 0 and len(done.failed) == 1, attempt
    assert setup.smtp.received == []
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.last_error and info.times_sent == 0
    assert call(setup.engine, "mail.send-due").sent == 0  # given up on that send: the next one is five days on
    setup.smtp._size_limit = 1_000_000  # noqa: SLF001
    at(setup, 5 * DAY + 7200)
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1
