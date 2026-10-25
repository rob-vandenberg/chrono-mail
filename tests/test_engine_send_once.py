import dataclasses
import threading
from collections.abc import Callable

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import MessageRef, StorageError
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine import commands_schedule
from chrono_mail.engine.mailbox import Mailbox
from chrono_mail.mime import ParsedMessage
from chrono_mail.store import Store
from chrono_mail.store.store import Placed
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_recurring import repeating
from tests.test_engine_schedule import at, rows, schedule

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_send_once.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: a message sent from the Outbox whose move to Sent fails, also twice, is never sent again (RV-178); a scheduled message whose
#           removal fails after it is gone is sent, never lost (RV-179); a pause made just before the send counts (RV-180); a message sent already is never made a draft (RV-181);
#           the review of these fixes: a message moved out of Scheduled meanwhile is not sent, a sent one never goes to Drafts, it is filed
#           in Sent without a password, a message made repeating meanwhile waits
# v3.1.341: Initial: a message is never sent twice (the code review's batch 1: RV-109, RV-140, RV-50, RV-123, RV-126, RV-127, RV-132; D-256)


def held(setup: Setup) -> tuple[threading.Event, threading.Event]:
    """The server holds every message it receives until released: (a message arrived, release)."""
    arrived, release = threading.Event(), threading.Event()

    def hold() -> None:
        arrived.set()
        release.wait(10)

    setup.smtp.before_queued = hold
    return arrived, release


def in_background(work: Callable[[], object]) -> threading.Thread:
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def subjects(setup: Setup, folder: str) -> list[str]:
    return [str(row.subject) for row in rows(setup, folder)]


def test_a_refresh_during_a_send_never_sends_the_same_message_again(setup: Setup) -> None:
    """RV-109: Send put its copy in the Outbox and talked to the server; a refresh meanwhile found that copy and sent it too."""
    arrived, release = held(setup)
    sending = in_background(lambda: call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x"))
    assert arrived.wait(5)
    refresh = in_background(lambda: call(setup.engine, "mail.send-outbox"))
    refresh.join(0.5)  # it waits for the send that runs
    release.set()
    sending.join(10)
    refresh.join(10)
    assert len(setup.smtp.received) == 1 and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_server_that_took_the_message_and_dropped_the_goodbye_has_sent_it(setup: Setup) -> None:
    """RV-140: the server answered "queued", then did not answer QUIT; the send counted as failed and every refresh sent it again."""
    setup.smtp.drop_on_quit = True
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]
    call(setup.engine, "mail.send-outbox")
    assert len(setup.smtp.received) == 1


def test_a_message_sent_but_not_moved_to_sent_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-50: the move to Sent failed after the server took the message; it stayed in the Outbox and the next refresh sent it again."""
    real_move = Store.move_placed
    failures: list[str] = []

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if not failures:
            failures.append("the disk is full")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    result = call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    assert result.sent and "as sent" in result.warning and subjects(setup, "Outbox") == ["Once"]
    done = call(setup.engine, "mail.send-outbox")
    assert done.failed == () and len(setup.smtp.received) == 1  # only moved, never sent again
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_message_sent_from_the_outbox_but_not_moved_to_sent_is_never_sent_again(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-178: sent from the Outbox (a refresh, Send now, a delayed send), its move to Sent failed: the "sent" mark was written and wiped at
    once, and the next refresh sent it again. Also when the move fails again at the next refresh."""
    real_move = Store.move_placed
    failures: list[str] = []

    def move_failing_twice(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        if len(failures) < 2:
            failures.append("the disk is full")
            raise StorageError("No space left on the disk")
        return real_move(self, ref, target_folder, hint)

    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x", hold=True)
    monkeypatch.setattr(Store, "move_placed", move_failing_twice)
    for _ in range(3):
        call(setup.engine, "mail.send-outbox")
    assert len(setup.smtp.received) == 1 and len(failures) == 2
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"]


def test_a_repeating_send_that_fails_leaves_no_copy_to_be_sent_by_a_refresh(setup: Setup) -> None:
    """RV-123: each failed try of a repeating message left a copy in the Outbox, and the next refresh sent every one of them."""
    at(setup, 0)
    repeating(setup)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    at(setup, 2 * 3600)
    for _ in range(2):  # two tries of the same send
        assert call(setup.engine, "mail.send-due").sent == 0
        assert subjects(setup, "Outbox") == []
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert call(setup.engine, "mail.send-outbox").sent == 0 and setup.smtp.received == []
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1  # the series sends it itself, once


def test_a_scheduled_message_due_and_sent_now_at_the_same_moment_goes_once(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-126: the minute check and Send now both read the scheduled message before either removed it, and both sent it."""
    at(setup, 0)
    scheduled = schedule(setup, hours=0.01)
    at(setup, 3600)
    real_read = commands_schedule._read  # noqa: SLF001
    both_read = threading.Barrier(2, timeout=5)
    reads: list[str] = []

    def read_together(mailbox: Mailbox, reference: MessageRef) -> ParsedMessage:
        parsed = real_read(mailbox, reference)
        reads.append(reference.store_id)
        if len(reads) <= 2:
            both_read.wait()  # both sends have read it before either goes on
        return parsed

    monkeypatch.setattr(commands_schedule, "_read", read_together)
    results: list[object] = []
    first = in_background(lambda: results.append(call(setup.engine, "mail.send-due")))
    second = in_background(lambda: results.append(call(setup.engine, "mail.send-due", message=scheduled.message_id)))
    first.join(10)
    second.join(10)
    assert len(results) == 2 and len(setup.smtp.received) == 1
    assert subjects(setup, "Scheduled") == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Later"]


def test_a_scheduled_message_whose_original_cannot_be_removed_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-127: the copy went to the Outbox, removing the scheduled original failed; both were sent later."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)

    def cannot_remove(mailbox: Mailbox, reference: MessageRef) -> Callable[[], None]:
        def remove() -> None:
            raise StorageError("Access is denied")

        return remove

    monkeypatch.setattr(commands_schedule, "_remover", cannot_remove)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and "Access is denied" in done.failed[0] and setup.smtp.received == []
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Scheduled") == ["Later"]  # it waits where it was, for the next check
    monkeypatch.undo()
    at(setup, 3700)
    assert call(setup.engine, "mail.send-due").sent == 1 and len(setup.smtp.received) == 1


def test_a_failed_scheduled_send_says_it_is_tried_again(setup: Setup) -> None:
    """RV-132: the text said the message was not sent again by itself, while every refresh that sends tries it again (D-028)."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    done = call(setup.engine, "mail.send-due")
    assert "tried again at the next refresh that sends" in done.failed[0] and subjects(setup, "Outbox") == ["Later"]


def test_a_scheduled_message_gone_although_its_removal_failed_is_sent_not_lost(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-179: removing the scheduled original failed after the file was gone (the folder could not be written to disk); the copy in the
    Outbox was thrown away too, and the message was lost unsent."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_delete = Mailbox.delete

    def delete_then_fail(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        real_delete(self, ref, permanent)
        raise StorageError("The folder could not be written to the disk")

    monkeypatch.setattr(Mailbox, "delete", delete_then_fail)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 1 and len(setup.smtp.received) == 1
    assert subjects(setup, "Scheduled") == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Later"]


def test_a_pause_made_just_before_the_send_counts(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-180: the minute check read the message, then waited for its locks while the person paused it; it sent the old text anyway and
    removed the paused message."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_lock = commands_schedule._REPEAT_LOCK  # noqa: SLF001
    paused: list[str] = []

    class PausedMeanwhile:
        """The lock the minute check takes: the person pauses the message just before it gets it."""

        def __enter__(self) -> None:
            if not paused:
                monkeypatch.setattr(commands_schedule, "_REPEAT_LOCK", real_lock)
                for row in rows(setup, "Scheduled"):
                    call(setup.engine, "mail.repeat-pause", account=setup.account_id, folder="Scheduled", message=row.message_id)
                    paused.append(row.message_id)
            real_lock.acquire()

        def __exit__(self, *_: object) -> None:
            real_lock.release()

    monkeypatch.setattr(commands_schedule, "_REPEAT_LOCK", PausedMeanwhile())
    call(setup.engine, "mail.send-due")
    assert paused and setup.smtp.received == [] and subjects(setup, "Scheduled") == ["Later"] and subjects(setup, "Outbox") == []


def test_a_message_sent_already_is_never_made_a_draft(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-181: opening an Outbox message the server took (its move to Sent failed) made it a draft; Send then sent it a second time."""
    from chrono_mail.engine.commands_outbox import SentAlready

    real_move = Store.move_placed

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        monkeypatch.setattr(Store, "move_placed", real_move)
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (row,) = rows(setup, "Outbox")
    with pytest.raises(SentAlready):
        call(setup.engine, "mail.cancel-send", account=setup.account_id, message=row.message_id)
    assert subjects(setup, "Outbox") == ["Once"] and subjects(setup, "Drafts") == [] and len(setup.smtp.received) == 1


def test_a_scheduled_message_moved_away_meanwhile_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-179: while the minute check sent it, the person dragged it to Drafts; the removal of the original failed, the
    copy was sent anyway and the line of the draft was removed from the index."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_delete = Mailbox.delete

    def moved_meanwhile(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        self.move(ref, self.folder("Drafts"))  # the person, a moment before
        return real_delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "delete", moved_meanwhile)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Outbox") == [] and subjects(setup, "Drafts") == ["Later"]


def _sent_but_not_filed(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> str:
    real_move = Store.move_placed

    def move_failing_once(self: Store, ref: MessageRef, target_folder: tuple[str, ...], hint: str | None = None) -> Placed:
        monkeypatch.setattr(Store, "move_placed", real_move)
        raise StorageError("No space left on the disk")

    monkeypatch.setattr(Store, "move_placed", move_failing_once)
    call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject="Once", body="x")
    (row,) = rows(setup, "Outbox")
    return str(row.message_id)


def test_a_message_sent_already_never_goes_to_drafts(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-181: dragged from the Outbox to Drafts it became an ordinary draft, and Send sent it a second time."""
    message = _sent_but_not_filed(setup, monkeypatch)
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "message.move", account=setup.account_id, folder="Outbox", message=message, target="Drafts")
    assert subjects(setup, "Drafts") == [] and subjects(setup, "Outbox") == ["Once"]


def test_a_message_sent_already_is_filed_in_sent_without_a_password(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-178: a message the server took waited for its password and its countdown before it was only moved to Sent."""
    _sent_but_not_filed(setup, monkeypatch)
    setup.engine.passwords.delete(setup.account_id, ServerRole.OUTGOING)
    call(setup.engine, "mail.send-outbox")
    assert subjects(setup, "Outbox") == [] and subjects(setup, "Sent") == ["Once"] and len(setup.smtp.received) == 1


def test_a_message_made_repeating_meanwhile_waits_for_the_next_check(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-180: made repeating while the minute check waited for its locks, it was sent once as a one-off and its series ended."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_read = commands_schedule._read  # noqa: SLF001
    reads: list[str] = []

    def read_repeating_the_second_time(mailbox: Mailbox, reference: MessageRef) -> ParsedMessage:
        parsed = real_read(mailbox, reference)
        reads.append("read")
        if len(reads) == 2:  # the read under the locks: the person made it repeating a moment before
            return dataclasses.replace(parsed, chrono_headers=(*parsed.chrono_headers, ("x-chrono-mail-kind", "recurring")))
        return parsed

    monkeypatch.setattr(commands_schedule, "_read", read_repeating_the_second_time)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Scheduled") == ["Later"]


def test_unscheduling_that_cannot_take_the_original_away_leaves_no_draft(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second review of RV-180: when the scheduled original could not be removed, it stayed scheduled and a draft of it was made too;
    it was sent at its time and the draft could be sent again."""
    at(setup, 0)
    schedule(setup, hours=2)
    (row,) = rows(setup, "Scheduled")
    real_delete = Mailbox.delete

    def scheduled_cannot_go(self: Mailbox, ref: MessageRef, permanent: bool) -> str:
        if ref.folder_path == ("Scheduled",):
            raise StorageError("Access is denied")
        return real_delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "delete", scheduled_cannot_go)
    with pytest.raises(StorageError, match="stays scheduled"):
        call(setup.engine, "mail.unschedule", account=setup.account_id, folder="Scheduled", message=row.message_id)
    assert subjects(setup, "Scheduled") == ["Later"] and subjects(setup, "Drafts") == []


def test_a_scheduled_message_deleted_for_good_just_before_its_send_is_not_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The third review of RV-179: deleted for good a moment before its send (Shift+Delete takes no send lock), the removal answered
    "gone" without an error and the copy was sent anyway."""
    at(setup, 0)
    schedule(setup, hours=0.01)
    at(setup, 3600)
    real_remover = commands_schedule._remover  # noqa: SLF001

    def deleted_by_the_person_first(mailbox: Mailbox, reference: MessageRef) -> Callable[[], None]:
        remove = real_remover(mailbox, reference)

        def person_then_remove() -> None:
            mailbox.delete(reference, permanent=True)  # the person's Shift+Delete, a moment before the send's own removal
            remove()

        return person_then_remove

    monkeypatch.setattr(commands_schedule, "_remover", deleted_by_the_person_first)
    done = call(setup.engine, "mail.send-due")
    assert done.sent == 0 and setup.smtp.received == [] and subjects(setup, "Outbox") == []


def test_send_now_of_a_repeating_message_that_is_due_takes_the_place_of_that_send(setup: Setup) -> None:
    """The final review: Send now of a repeating message whose send was due did not move its timetable on: the next minute check sent it
    again."""
    at(setup, 0)
    repeating(setup)
    at(setup, 3700)  # the first send is due now
    (row,) = rows(setup, "Scheduled")
    call(setup.engine, "mail.send-due", message=row.message_id)  # Send now
    call(setup.engine, "mail.send-due")  # the minute check a moment later
    assert len(setup.smtp.received) == 1


def test_a_message_sent_already_is_copied_only_from_sent(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """The final review of RV-181: a copy lost the "sent" mark, so copy to Archive, then drag the copy to Drafts made it a draft again."""
    message = _sent_but_not_filed(setup, monkeypatch)
    with pytest.raises(UsageError, match="sent already"):
        call(setup.engine, "message.copy", account=setup.account_id, folder="Outbox", message=message, target="Trash")

