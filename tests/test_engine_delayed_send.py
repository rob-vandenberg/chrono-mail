from chrono_mail.config import ServerRole
from chrono_mail.core import MessageFlag
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_mail import SendParameters, SendResult, mail_send
from chrono_mail.engine.commands_outbox import (
    OutboxMessageParameters, SendOutboxParameters, SendOutboxResult, mail_cancel_send, mail_end_countdown, mail_send_outbox,
)
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import UsageError
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_delayed_send.py 2.2.314"

# --- Version history ----------------------------------------------------
# v2.2.314: Initial (B-21): a delayed send waits in the Outbox with its moment, which every send of the Outbox keeps; Send now, Cancel,
#           the end of a countdown, never twice, nothing of it in the mail, a failed send tried again, a reply marked once really sent

START: float = 1_800_000_000.0


def at(engine: Engine, seconds: float) -> None:
    engine.clock = lambda: START + seconds


def send(setup: Setup, subject: str = "Hello", delay: int = 10, hold: bool = False, answering: str | None = None) -> SendResult:
    """Send a message as the message window does with a delay; answering: the id of the Inbox message it replies to."""
    return mail_send(setup.engine, SendParameters(
        setup.account_id, to=("you@example.com",), subject=subject, body="x", delay_seconds=delay, hold=hold,
        source_folder="Inbox" if answering else None, source_message=answering, mark_replied=answering is not None,
    ))


def outbox(setup: Setup) -> dict[str, str]:
    """subject -> store id of what waits in the Outbox."""
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return {row.subject: row.store_id for row in mailbox.index.page(("Outbox",), limit=100)}


def send_outbox(setup: Setup, message: str | None = None, now: bool = False) -> SendOutboxResult:
    return mail_send_outbox(setup.engine, SendOutboxParameters(message=message, now=now))


def test_a_delayed_send_waits_in_the_outbox_and_no_send_of_the_outbox_takes_it_before_its_time(setup: Setup) -> None:
    at(setup.engine, 0)
    result = send(setup)
    assert result.waiting and not result.sent and setup.smtp.received == [] and list(outbox(setup)) == ["Hello"]
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert mailbox.timed(("Outbox",)) == {result.message_id: int(START) + 10}  # the Send at column
    assert send_outbox(setup).sent == 0 and setup.smtp.received == []  # a refresh during the countdown
    at(setup.engine, 11)
    assert send_outbox(setup).sent == 1 and len(setup.smtp.received) == 1 and outbox(setup) == {}
    assert b"X-Chrono-Mail" not in setup.smtp.received[0].data  # nothing of the waiting reaches the mail


def test_send_now_sends_at_once_and_never_twice(setup: Setup) -> None:
    at(setup.engine, 0)
    first = send(setup, "First")
    send(setup, "Second")
    done = send_outbox(setup, message=first.message_id)  # Send now on the notice of the first
    assert done.sent == 1 and list(outbox(setup)) == ["Second"]
    assert send_outbox(setup, message=first.message_id).sent == 0 and len(setup.smtp.received) == 1  # the end of its countdown comes after it
    assert send_outbox(setup, now=True).sent == 1 and len(setup.smtp.received) == 2  # Send now on Quit: every one


def test_cancel_takes_the_message_back_as_a_draft_and_sends_nothing(setup: Setup) -> None:
    at(setup.engine, 0)
    result = send(setup, "Oops")
    taken = mail_cancel_send(setup.engine, OutboxMessageParameters(setup.account_id, result.message_id))
    assert taken.account == setup.account_id and taken.folder == "Drafts" and outbox(setup) == {}
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert [row.subject for row in mailbox.index.page(("Drafts",), limit=10)] == ["Oops"]
    at(setup.engine, 60)
    assert send_outbox(setup).sent == 0 and setup.smtp.received == []


def test_cancel_after_the_send_says_so(setup: Setup) -> None:
    at(setup.engine, 0)
    result = send(setup)
    send_outbox(setup, message=result.message_id)
    try:
        mail_cancel_send(setup.engine, OutboxMessageParameters(setup.account_id, result.message_id))
    except UsageError as error:
        assert "sent already" in str(error)
    else:
        raise AssertionError("a sent message cannot be cancelled")


def test_the_end_of_a_countdown_with_until_the_next_refresh_leaves_it_for_that_refresh(setup: Setup) -> None:
    at(setup.engine, 0)
    result = send(setup, hold=True)
    mail_end_countdown(setup.engine, OutboxMessageParameters(setup.account_id, result.message_id))
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert mailbox.timed(("Outbox",)) == {}  # the Send at column is empty again
    assert setup.smtp.received == []
    assert send_outbox(setup).sent == 1  # the next refresh, at once (no time to wait for any more)


def test_until_the_next_refresh_only_waits_without_a_countdown(setup: Setup) -> None:
    result = send(setup, delay=0, hold=True)
    assert result.waiting and setup.smtp.received == []
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert mailbox.timed(("Outbox",)) == {}
    assert send_outbox(setup).sent == 1


def test_a_reply_that_waited_marks_its_original_once_it_is_really_sent(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        original = mailbox.index.page(("Inbox",), limit=1)[0].store_id

    def replied() -> bool:
        with open_mailbox(setup.engine, setup.account_id) as mailbox:
            return MessageFlag.REPLIED in mailbox.current_flags(mailbox.reference(("Inbox",), original))

    at(setup.engine, 0)
    send(setup, "Re: it", answering=original)
    assert not replied()
    at(setup.engine, 20)
    send_outbox(setup)
    assert replied()


def test_a_failed_send_waits_and_goes_with_the_next_send_of_the_outbox_marking_its_original(setup: Setup) -> None:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        original = mailbox.index.page(("Inbox",), limit=1)[0].store_id
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "wrong")
    failed = send(setup, "Re: it", delay=0, answering=original)
    assert not failed.sent and not failed.waiting and list(outbox(setup)) == ["Re: it"]
    setup.engine.passwords.set(setup.account_id, ServerRole.OUTGOING, "secret")
    assert send_outbox(setup).sent == 1
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert MessageFlag.REPLIED in mailbox.current_flags(mailbox.reference(("Inbox",), original))


def test_a_delay_out_of_range_is_refused_and_nothing_is_stored(setup: Setup) -> None:
    try:
        send(setup, delay=1000)
    except UsageError:
        pass
    else:
        raise AssertionError("1000 seconds is too long")
    assert outbox(setup) == {}
