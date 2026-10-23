import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole

from chrono_mail.engine import UsageError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.store import Store
from chrono_mail.engine.commands_schedule import paused_if_overdue
from chrono_mail.engine.recurring import Repeat, repeat_from_text, repeat_text
from chrono_mail.engine.schedule import describe_when, parse_when, snooze_group, snooze_presets
from chrono_mail.mime import parse_message, with_extra_headers
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_schedule.py 2.4.327"

# --- Version history ----------------------------------------------------
# v2.4.327: a one-off scheduled message can be paused and resumed; a restore pauses the overdue ones, one-off and repeating (D-245)
# v1.9.276: The send times filled in after the upgrade from format 8 are gone: every older index is rebuilt (D-226)
# v1.1.207: mail.edit-scheduled: in place, the same id, time and repeat kept or changed (D-177)
# v1.1.206: Snooze records are in the messages' properties files; the snoozed.json tests are gone with that file (D-176)
# v1.1.200: Scheduled messages wait in the Scheduled folder, made when first used; those in the Outbox move there once; it sorts by send time
# v1.1.196: A scheduled message that cannot be read is told once and never sent; only due messages are opened; an index from before send times finds them (R-01)
# v0.5.165: reading the snooze file waits while it is replaced (SNOOZED_FILE_LOCK)
# v0.2.101: Waking a snoozed message without a record brings it to the Inbox
# v0.2.100: The Snoozed folder: rows carry the time they come back, sorting and grouping by it
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
    assert not result.sent and result.folder == "Scheduled" and result.scheduled and setup.smtp.received == []
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.subject == "Later" and info.to == ("you@example.com",) and info.message_id == result.message_id
    assert call(setup.engine, "mail.send-due").sent == 0 and setup.smtp.received == []
    at(setup, 3 * 3600)
    done = call(setup.engine, "mail.send-due")
    assert (done.sent, done.failed, done.waiting) == (1, (), 0) and len(setup.smtp.received) == 1
    assert b"Subject: Later" in setup.smtp.received[0].data and b"X-Chrono-Mail" not in setup.smtp.received[0].data
    assert rows(setup, "Scheduled") == [] and [row.subject for row in rows(setup, "Sent")] == ["Later"] and call(setup.engine, "mail.scheduled") == ()
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
    draft = call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Scheduled", message=result.message_id)
    assert draft.folder == "Drafts" and rows(setup, "Scheduled") == [] and [row.subject for row in rows(setup, "Drafts")] == ["Later"]
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


def test_the_snoozed_folder_knows_and_sorts_by_when_messages_come_back(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    first, second, third = rows(setup, "Inbox")[:3]
    for row, hours in ((first, 72), (second, 1), (third, 480)):
        call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=row.message_id, until=iso(hours * 3600))
    listed = call(setup.engine, "message.list", account=setup.account_id, folder="Snoozed", sort="snoozed", oldest_first=True).messages
    assert [row.message_id for row in listed] == [second.message_id, first.message_id, third.message_id]
    assert listed[0].snoozed_until == datetime.fromtimestamp(int(datetime.fromisoformat(iso(3600)).timestamp())).astimezone()
    latest_first = call(setup.engine, "message.list", account=setup.account_id, folder="Snoozed", sort="snoozed", offset=1, limit=1).messages
    assert [row.message_id for row in latest_first] == [first.message_id]
    found = call(setup.engine, "message.search", account=setup.account_id, folder="Snoozed", sort="snoozed", oldest_first=True)
    assert [row.message_id for row in found] == [second.message_id, first.message_id, third.message_id]
    groups = call(setup.engine, "message.groups", account=setup.account_id, folder="Snoozed", sort="snoozed", oldest_first=True)
    keys = [group.key for group in groups]
    assert sum(group.count for group in groups) == 3 and keys == sorted(keys, key=["today", "tomorrow", "this_week", "next_week", "later"].index)
    assert all(row.snoozed_until is None for row in rows(setup, "Inbox"))
    assert call(setup.engine, "message.list", account=setup.account_id, folder="Inbox", sort="snoozed").total >= 0  # elsewhere it sorts by date


def test_a_snoozed_message_without_a_record_goes_to_the_inbox_when_woken(setup: Setup) -> None:
    at(setup, 0)
    call(setup.engine, "mail.fetch", account=setup.account_id)
    target = rows(setup, "Inbox")[0]
    call(setup.engine, "message.flag", account=setup.account_id, folder="Inbox", message=target.message_id, seen=True)
    call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=target.message_id, until=iso(3600))
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.store.properties.forget(target.message_id)  # the record is lost
    assert call(setup.engine, "message.wake").woken == 0 and len(rows(setup, "Snoozed")) == 1  # nothing comes back by itself
    assert call(setup.engine, "message.wake", account=setup.account_id, message=target.message_id).woken == 1
    back = next(row for row in rows(setup, "Inbox") if row.message_id == target.message_id)
    assert back.flags == () and rows(setup, "Snoozed") == []


def test_the_groups_of_the_snoozed_folder() -> None:
    now = datetime(2026, 10, 14, 15, 0).astimezone()  # a Wednesday
    def at_day(day: int, hour: int = 9) -> int:
        return int(datetime(2026, 10, day, hour, 0).astimezone().timestamp())
    assert snooze_group(at_day(14, 8), now) == "today" and snooze_group(at_day(14, 20), now) == "today"
    assert snooze_group(at_day(15), now) == "tomorrow" and snooze_group(at_day(18), now) == "this_week"
    assert snooze_group(at_day(19), now) == "next_week" and snooze_group(at_day(25), now) == "next_week"
    assert snooze_group(at_day(26), now) == "later" and snooze_group(None, now) == "later"


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


def damage(setup: Setup, store_id: str) -> tuple[Path, bytes]:
    """Make a stored message unreadable (as a damaged disk would); returns its file and what it held, to mend it again."""
    (path,) = [found for found in (setup.engine.directories.data_directory / "accounts").rglob(f"{store_id}*") if found.is_file() and "properties" not in found.parts]
    kept = path.read_bytes()
    path.write_bytes(b"not a message any more")
    return path, kept


def test_a_scheduled_message_that_cannot_be_read_is_told_once_and_never_sent(setup: Setup) -> None:
    at(setup, 0)
    result = schedule(setup)
    damage(setup, result.message_id)
    at(setup, 3 * 3600)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == []
    (told,) = done.failed
    assert "cannot be read" in told and "stays where it is" in told
    assert call(setup.engine, "mail.send-due").failed == ()  # told once, not every minute
    assert [row.message_id for row in rows(setup, "Scheduled")] == [result.message_id]  # nothing removed


def test_the_minute_check_opens_only_the_messages_that_are_due(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    at(setup, 0)
    soon, later, last = (schedule(setup, hours=hours).message_id for hours in (2, 5, 9))
    opened: list[str] = []
    reading = Store.read

    def counting(self: Store, ref: Any, hint: str | None = None) -> bytes:  # noqa: ANN401
        opened.append(ref.store_id)
        return reading(self, ref, hint)

    monkeypatch.setattr(Store, "read", counting)
    at(setup, 3 * 3600)
    done = call(setup.engine, "mail.send-due")
    assert (done.sent, done.waiting) == (1, 2)
    assert soon in opened and later not in opened and last not in opened  # the others are not opened before their time


def folders(setup: Setup) -> set[str]:
    return {folder.path for folder in call(setup.engine, "folder.list", account=setup.account_id)}


def test_the_scheduled_folder_is_made_when_first_used_and_lists_by_send_time(setup: Setup) -> None:
    at(setup, 0)
    assert "Scheduled" not in folders(setup)
    later, sooner = schedule(setup, hours=5).message_id, schedule(setup, hours=2).message_id
    assert "Scheduled" in folders(setup) and rows(setup, "Outbox") == []
    listed = list(call(setup.engine, "message.list", account=setup.account_id, folder="Scheduled", sort="snoozed", oldest_first=True).messages)
    assert [row.message_id for row in listed] == [sooner, later]  # soonest first, by the time each is sent
    assert [None if row.snoozed_until is None else int(row.snoozed_until.timestamp()) for row in listed] == [int(NOW) + 2 * 3600, int(NOW) + 5 * 3600]


def test_messages_scheduled_before_the_scheduled_folder_move_there_once(setup: Setup) -> None:
    at(setup, 0)
    once = schedule(setup, hours=2).message_id
    again = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Again", body="x", send_at=iso(3600), repeat_days=5).message_id
    with open_mailbox(setup.engine, setup.account_id) as mailbox:  # where they waited before 1.1.200
        for store_id in (once, again):
            mailbox.move(mailbox.reference(("Scheduled",), store_id), ("Outbox",))
    assert {row.message_id for row in rows(setup, "Outbox")} == {once, again}
    assert call(setup.engine, "mail.send-due").sent == 0  # not due yet: only moved
    assert {row.message_id for row in rows(setup, "Scheduled")} == {once, again} and rows(setup, "Outbox") == []
    at(setup, 3 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 2 and len(setup.smtp.received) == 2


def edit(setup: Setup, message: str, **values: Any) -> Any:  # noqa: ANN401
    fields: dict[str, Any] = {"to": "you@example.com", "subject": "Changed", "body": "new text"}
    fields.update(values)
    return call(setup.engine, "mail.edit-scheduled", account=setup.account_id, message=message, **fields)


def test_a_scheduled_message_is_edited_in_place_and_keeps_its_id_and_time(setup: Setup) -> None:
    at(setup, 0)
    first = schedule(setup, hours=2).message_id
    info = edit(setup, first, keep_time=True)
    assert (info.message_id, info.seconds, info.subject) == (first, int(NOW) + 2 * 3600, "Changed")
    (row,) = rows(setup, "Scheduled")
    assert row.message_id == first and row.subject == "Changed" and rows(setup, "Drafts") == []
    at(setup, 3 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 1 and b"new text" in setup.smtp.received[0].data


def test_editing_a_repeating_message_keeps_how_often_it_was_sent_and_a_new_time_counts_from_there(setup: Setup) -> None:
    at(setup, 0)
    again = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Again", body="x", send_at=iso(3600), repeat_days=1).message_id
    at(setup, 2 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 1
    kept = edit(setup, again, keep_time=True)
    assert kept.times_sent == 1 and kept.repeat == "every 1 day" and kept.seconds == int(NOW) + 3600 + 86_400
    moved = edit(setup, again, send_at=iso(5 * 3600), repeat_weeks=1)
    assert (moved.message_id, moved.times_sent, moved.repeat, moved.seconds) == (again, 1, "every 1 week", int(NOW) + 5 * 3600)
    once = edit(setup, again, send_at=iso(4 * 3600))  # no interval: it is sent once
    assert once.repeat == "" and once.seconds == int(NOW) + 4 * 3600 and rows(setup, "Drafts") == []


def test_only_a_waiting_message_with_a_recipient_can_be_edited(setup: Setup) -> None:
    at(setup, 0)
    first = schedule(setup, hours=2).message_id
    with pytest.raises(UsageError, match="recipient"):
        edit(setup, first, to=(), keep_time=True)
    with pytest.raises(UsageError, match="new time"):
        edit(setup, first)
    call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Scheduled", message=first)
    with pytest.raises(UsageError, match="no longer waits"):
        edit(setup, first, keep_time=True)


def test_a_paused_one_off_message_waits_until_it_is_resumed(setup: Setup) -> None:
    at(setup, 0)
    result = schedule(setup)
    paused = call(setup.engine, "mail.repeat-pause", account=setup.account_id, folder="Scheduled", message=result.message_id)
    assert paused.paused and paused.line is not None and paused.line.paused and paused.repeat == ""
    at(setup, 3 * 3600)
    assert call(setup.engine, "mail.send-due").sent == 0 and setup.smtp.received == []  # its time passed while paused
    (info,) = call(setup.engine, "mail.scheduled")
    assert info.paused
    resumed = call(setup.engine, "mail.repeat-resume", account=setup.account_id, folder="Scheduled", message=result.message_id)
    assert not resumed.paused and resumed.line is not None and not resumed.line.paused
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1  # resumed after its time: the next check sends it


def test_send_now_sends_a_paused_message(setup: Setup) -> None:
    at(setup, 0)
    result = schedule(setup)
    call(setup.engine, "mail.repeat-pause", account=setup.account_id, folder="Scheduled", message=result.message_id)
    assert call(setup.engine, "mail.send-due", account=setup.account_id, message=result.message_id).sent == 1 and len(setup.smtp.received) == 1


def test_only_scheduled_messages_whose_time_passed_are_paused_after_a_restore() -> None:
    raw = raw_message(subject="Greeting")
    one_off = with_extra_headers(raw, {"X-Chrono-Mail-Kind": "scheduled", "X-Chrono-Mail-Send-At": str(int(NOW) - 60)})
    paused = paused_if_overdue(one_off, NOW)
    assert paused is not None and dict(parse_message(paused).chrono_headers)["x-chrono-mail-paused"] == "yes"
    assert paused_if_overdue(paused, NOW) is None  # paused already
    later = with_extra_headers(raw, {"X-Chrono-Mail-Kind": "scheduled", "X-Chrono-Mail-Send-At": str(int(NOW) + 60)})
    assert paused_if_overdue(later, NOW) is None and paused_if_overdue(raw, NOW) is None
    repeat = Repeat(int(NOW) - 86_400, None, days=7)
    repeating = with_extra_headers(raw, {"X-Chrono-Mail-Kind": "recurring", "X-Chrono-Mail-Send-At": str(repeat.start), "X-Chrono-Mail-Repeat": repeat_text(repeat)})
    paused = paused_if_overdue(repeating, NOW)
    assert paused is not None
    kept = repeat_from_text(dict(parse_message(paused).chrono_headers)["x-chrono-mail-repeat"])
    assert kept is not None and kept.paused and kept.start == repeat.start
