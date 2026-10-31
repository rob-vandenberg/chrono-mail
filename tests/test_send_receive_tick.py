from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_outbox import failed_send
from tests.test_engine_schedule import rows

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_send_receive_tick.py 4.2.451"

# --- Version history ----------------------------------------------------
# v4.2.451: mail.send-outbox senders: only the messages sent from the accounts named; the others wait (D-059)


def test_send_all_sends_only_the_messages_of_the_accounts_named(setup: Setup) -> None:
    failed_send(setup)
    assert call(setup.engine, "mail.send-outbox", senders=["f" * 32]).sent == 0  # another account: this one is left out
    assert setup.smtp.received == [] and [row.subject for row in rows(setup, "Outbox")] == ["Waiting"]
    assert call(setup.engine, "mail.send-outbox", senders=[setup.account_id]).sent == 1
    assert len(setup.smtp.received) == 1 and rows(setup, "Outbox") == []
