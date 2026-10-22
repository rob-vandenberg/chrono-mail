import poplib
import time
from collections.abc import Iterator
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from email.utils import make_msgid

import pytest

from tests.live.account import LiveAccount
from tests.live.helpers import SMTP_MODES, pop3_session, smtp_session

# --- Version ------------------------------------------------------------
__version__ = "test_live_roundtrip.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial live round-trip test

pytestmark = pytest.mark.live

DELIVERY_WAIT_SECONDS: float = 60.0
POLL_INTERVAL_SECONDS: float = 2.0


def find_message_number(connection: poplib.POP3, subject: str) -> int | None:
    """The message number of the message with this subject, or None."""
    count, _ = connection.stat()
    for number in range(1, count + 1):
        _, lines, _ = connection.top(number, 0)
        header = message_from_bytes(b"\r\n".join(lines), policy=default)
        if header["Subject"] == subject:
            return number
    return None


def delete_message_with_subject(live_account: LiveAccount, subject: str) -> bool:
    with pop3_session(live_account) as connection:
        number = find_message_number(connection, subject)
        if number is None:
            return False
        connection.dele(number)
        return True


@pytest.fixture
def subject_to_clean_up(live_account: LiveAccount) -> Iterator[list[str]]:
    subjects: list[str] = []
    yield subjects
    for subject in subjects:
        delete_message_with_subject(live_account, subject)


@pytest.mark.parametrize("mode", SMTP_MODES)
def test_send_to_self_and_receive(live_account: LiveAccount, mode: str, subject_to_clean_up: list[str]) -> None:
    token = make_msgid(domain="chrono-mail.test")
    subject = f"Chrono Mail live test {mode} {token}"
    subject_to_clean_up.append(subject)
    message = EmailMessage()
    message["From"] = live_account.user
    message["To"] = live_account.user
    message["Subject"] = subject
    message.set_content(f"Round trip over {mode}.\nToken: {token}\n")

    with smtp_session(live_account, mode) as connection:
        connection.login(live_account.user, live_account.password)
        connection.send_message(message)

    deadline = time.monotonic() + DELIVERY_WAIT_SECONDS
    received: EmailMessage | None = None
    while received is None and time.monotonic() < deadline:
        with pop3_session(live_account) as connection:
            number = find_message_number(connection, subject)
            if number is not None:
                _, lines, _ = connection.retr(number)
                parsed = message_from_bytes(b"\r\n".join(lines), policy=default)
                assert isinstance(parsed, EmailMessage)
                received = parsed
        if received is None:
            time.sleep(POLL_INTERVAL_SECONDS)

    assert received is not None, f"message not delivered within {DELIVERY_WAIT_SECONDS} seconds"
    assert received["From"] == live_account.user
    body = received.get_body(preferencelist=("plain",))
    assert body is not None and token in body.get_content()

    assert delete_message_with_subject(live_account, subject)
    with pop3_session(live_account) as connection:
        assert find_message_number(connection, subject) is None
