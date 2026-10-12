from datetime import datetime
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole

from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.schedule import describe_when, load_snoozed, parse_when, snooze_presets
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_schedule.py 0.0.86"

# --- Version history ----------------------------------------------------
# v0.0.86: Ready-made snooze times
# v0.0.34: Initial send later and snooze tests

NOW = 1_800_000_000.0


def iso(seconds_from_now: float) -> str:
    return datetime.fromtimestamp(NOW + seconds_from_now).strftime("%Y-%m-%dT%H:%M")


def at(setup: Setup, seconds_from_now: float) -> None:
    setup.engine.clock = lambda: NOW + seconds_from_now


def rows(setup: Setup, folder: str) -> list[Any]:
    return list(call(setup.engine, "message.list", account=setup.account_id, folder=folder).messages)


def schedule(setup: Setup, hours: float = 2, **extra: Any) -> Any:  # noqa: ANN401
    return call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Later", body="see you", send_at=iso(hours * 3600), **extra)


def test_times_are_parsed_and_checked() -> None:
    assert parse_when("2026-10-11T09:00", datetime(2026, 10, 1).timestamp()) == int(datetime(2026, 10, 11, 9).timestamp())
    assert parse_when("2026-10-11 09:00", datetime(2026, 10, 1).timestamp()) == parse_when("2026-10-11T09:00", datetime(2026, 10, 1).timestamp())
    now = datetime(2026, 10, 1).timestamp()
    for bad in ("tomorrow", "2026-13-40", "", "2026-09-01T09:00", "2099-01-01T00:00"):
        with pytest.raises(UsageError):
            parse_when(bad, now)
    assert describe_when(int(datetime(2026, 10, 11, 9, 5).timestamp())) == "2026-10-11 09:05"


def test_a_scheduled_message_waits_in_the_outbox_and_is_sent_when_due(setup: Setup) -> None:
    at(setup, 0)
    result = schedule(setup)
    assert not result.sent and result.folder == "Outbox" and result.scheduled and setup.smtp.received == []
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.subject == "Later" and info.to == ("you@example.com",) and info.message_id == result.message_id
    assert call(setup.engine, "mail.send-due").sent == 0 and setup.smtp.received == []
    at(setup, 3 * 3600)
    done = call(setup.engine, "mail.send-due")
    assert (done.sent, done.failed, done.waiting) == (1, (), 0) and len(setup.smtp.received) == 1
    assert b"Subject: Later" in setup.smtp.received[0].data and b"X-Chrono-Mail" not in setup.smtp.received[0].data
    assert rows(setup, "Outbox") == [] and [row.subject for row in rows(setup, "Sent")] == ["Later"] and call(setup.engine, "mail.scheduled") == ()
    assert call(setup.engine, "mail.send-due").sent == 0 and len(setup.smtp.received) == 1  # never twice


def test_bcc_and_attachments_survive_the_wait(setup: Setup, tmp_path: Any) -> None:  # noqa: ANN401
    at(setup, 0)
    file = tmp_path / "plan.txt"
    file.write_text("the plan")
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", bcc="hidden@example.com", subject="With", body="x", attach=[str(file)], send_at=iso(3600))
    at(setup, 7200)
    call(setup.engine, "mail.send-due")
    (received,) = setup.smtp.received
    assert "hidden@example.com" in received.recipients and b"plan.txt" in received.data and b"Bcc" not in received.data


def test_a_past_time_is_refused_and_nothing_is_stored(setup: Setup) -> None:
    at(setup, 0)
    with pytest.raises(UsageError, match="passed"):
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="x", send_at=iso(-3600))
    assert rows(setup, "Outbox") == []


def test_send_now_sends_one_scheduled_message_whatever_its_time(setup: Setup) -> None:
    at(setup, 0)
    first, second = schedule(setup, 5), schedule(setup, 9)
    done = call(setup.engine, "mail.send-due", message=second.message_id)
    assert done.sent == 1 and done.waiting == 1 and len(setup.smtp.received) == 1
    assert [info.message_id for info in call(setup.engine, "mail.scheduled")] == [first.message_id]


def test_a_failed_send_stays_in_the_outbox_unscheduled(setup: Setup) -> None:
    at(setup, 0)
    schedule(setup)
    at(setup, 3 * 3600)
    setup.engine.passwords.delete(setup.account_id, ServerRole.OUTGOING)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and len(done.failed) == 1 and "no password" in done.failed[0]
    assert len(call(setup.engine, "mail.scheduled")) == 1  # nothing was lost: it waits for the password
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-due").sent == 1


def test_unscheduling_gives_back_a_draft(setup: Setup) -> None:
    at(setup, 0)
    result = schedule(setup)
    draft = call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Outbox", message=result.message_id)
    assert draft.folder == "Drafts" and rows(setup, "Outbox") == [] and [row.subject for row in rows(setup, "Drafts")] == ["Later"]
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=draft.message_id)
    assert opened.to == ("you@example.com",) and opened.body.strip() == "see you"
    with pytest.raises(UsageError, match="not scheduled"):
        with open_mailbox(setup.engine, setup.account_id) as mailbox:
            plain = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="stuck"), frozenset()).store_id
        call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Outbox", message=plain)


def test_scheduling_a_draft_replaces_it_and_a_reply_is_marked_when_sent(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    original = rows(setup, "Inbox")[0]
    draft = call(setup.engine, "draft.save", account=setup.account_id, to="alice@example.com", subject="Re: x", body="ok")
    schedule(setup, replaces_folder="Drafts", replaces_message=draft.message_id, source_folder="Inbox", source_message=original.message_id, mark_replied=True)
    assert rows(setup, "Drafts") == []
    at(setup, 3 * 3600)
    call(setup.engine, "mail.send-due")
    assert "replied" in next(row for row in rows(setup, "Inbox") if row.message_id == original.message_id).flags


# --- snooze --------------------------------------------------------------------------------------------------------------------------------------


def test_a_snoozed_message_leaves_and_comes_back_unread(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    target = rows(setup, "Inbox")[0]
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=target.message_id, seen=True)
    result = call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=target.message_id, until=iso(3600))
    assert target.message_id not in {row.message_id for row in rows(setup, "Inbox")} and [row.message_id for row in rows(setup, "Snoozed")] == [target.message_id]
    (info,) = call(setup.engine, "message.snoozed")
    assert info.comes_back_to == "Inbox" and info.message_id == target.message_id and result.until == info.until
    assert call(setup.engine, "message.wake").woken == 0
    at(setup, 2 * 3600)
    woken = call(setup.engine, "message.wake")
    assert woken.woken == 1 and rows(setup, "Snoozed") == [] and call(setup.engine, "message.snoozed") == ()
    back = next(row for row in rows(setup, "Inbox") if row.message_id == target.message_id)
    assert back.flags == ()  # unread again, at the top of the Inbox


def test_wake_now_and_odd_cases(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    first, second = rows(setup, "Inbox")[:2]
    for row in (first, second):
        call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=row.message_id, until=iso(86400))
    with pytest.raises(UsageError, match="snoozed already"):
        call(setup.engine, "message.snooze", account=setup.account_id, folder="Snoozed", message=first.message_id, until=iso(86400))
    with pytest.raises(UsageError):
        call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=rows(setup, "Inbox")[0].message_id, until=iso(-7200))
    assert call(setup.engine, "message.wake", message=first.message_id).woken == 1 and len(rows(setup, "Snoozed")) == 1
    with open_mailbox(setup.engine, setup.account_id) as mailbox:  # a snoozed message deleted by hand: the record is dropped
        victim = mailbox.reference(("Snoozed",), second.message_id)
        mailbox.delete(victim, permanent=True)
    at(setup, 2 * 86400)
    assert call(setup.engine, "message.wake").woken == 0 and call(setup.engine, "message.snoozed") == ()


def test_a_damaged_snooze_file_is_an_empty_list(tmp_path: Any) -> None:  # noqa: ANN401
    path = tmp_path / "snoozed.json"
    assert load_snoozed(path) == {}
    for text in ("{bad", "[]", '{"a": 5}', '{"a": {"until": "x", "folder": "Inbox"}}'):
        path.write_text(text)
        assert load_snoozed(path) == {}
    path.write_text('{"a": {"until": 5, "folder": "Inbox"}, "b": 3}')
    assert load_snoozed(path) == {"a": {"until": 5, "folder": "Inbox"}}


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 10, 12, 14, 30), [("Tomorrow", 13), ("Later this week", 14), ("This weekend", 17), ("Next week", 19)]),  # Monday
    (datetime(2026, 10, 14, 8, 0), [("Tomorrow", 15), ("Later this week", 16), ("This weekend", 17), ("Next week", 19)]),  # Wednesday
    (datetime(2026, 10, 15, 23, 59), [("Tomorrow", 16), ("This weekend", 17), ("Next week", 19)]),  # Thursday: two days ahead is Saturday
    (datetime(2026, 10, 17, 10, 0), [("Tomorrow", 18), ("Next week", 19)]),  # Saturday
    (datetime(2026, 10, 18, 10, 0), [("Tomorrow", 19), ("Next week", 19)]),  # Sunday
])
def test_the_ready_made_snooze_times(now: datetime, expected: list[tuple[str, int]]) -> None:
    presets = snooze_presets(now)
    assert [(name, moment.day) for name, moment in presets] == expected
    assert all(moment.month == 10 and (moment.hour, moment.minute, moment.second) == (9, 0, 0) for _, moment in presets)
