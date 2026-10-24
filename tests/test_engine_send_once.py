import threading
from collections.abc import Callable

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.core import MessageRef, StorageError
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
__version__ = "test_engine_send_once.py 3.1.341"

# --- Version history ----------------------------------------------------
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
