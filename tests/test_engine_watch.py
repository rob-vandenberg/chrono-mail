import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_mail.core import AuthenticationError
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer
from tests.fakesmtp import FakeSmtpServer
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_imap import World, world  # noqa: F401 - world is a fixture

__all__ = ["setup", "world"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_watch.py 0.0.32"

# --- Version history ----------------------------------------------------
# v0.0.32: Initial mail.wait tests


def test_waiting_ends_when_the_server_reports_new_mail(world: World) -> None:
    threading.Timer(0.4, world.imap.push).start()
    started = time.monotonic()
    result = call(world.engine, "mail.wait", account=world.account, seconds=20)
    assert result.supported and result.changed and time.monotonic() - started < 10


def test_waiting_ends_by_itself_and_when_the_program_is_ending(world: World) -> None:
    started = time.monotonic()
    quiet = call(world.engine, "mail.wait", account=world.account, seconds=2)
    assert quiet.supported and not quiet.changed and 1.5 < time.monotonic() - started < 8
    threading.Timer(0.5, world.engine.stop_event.set).start()
    started = time.monotonic()
    stopped = call(world.engine, "mail.wait", account=world.account, seconds=600)
    assert stopped.supported and not stopped.changed and time.monotonic() - started < 10


def test_a_pop3_account_and_a_server_without_idle_say_so(setup: Setup, tmp_path: Path) -> None:
    assert call(setup.engine, "mail.wait", account=setup.account_id, seconds=5) == type(call(setup.engine, "mail.wait", account=setup.account_id, seconds=5))(False, False)
    engine = make_engine(tmp_path / "other")
    with FakeImapServer(plain=False, idle=False) as imap, FakeSmtpServer() as smtp:
        created = call(
            engine, "account.add", kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=imap.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
        )
        result = call(engine, "mail.wait", account=str(created.account_id), seconds=5)
        assert not result.supported and not result.changed


def test_waiting_with_a_wrong_password_is_an_authentication_error(world: World) -> None:
    call(world.engine, "account.edit", account=world.account, password="wrong")
    with pytest.raises(AuthenticationError):
        call(world.engine, "mail.wait", account=world.account, seconds=2)
