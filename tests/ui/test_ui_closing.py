import threading

import pytest
from PySide6.QtCore import QTimer

from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.engine import Engine, commands_outbox
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.ui import mainwindow
from tests.fakeimap import FakeImapServer
from tests.ui.test_ui_delayed_send import delays, held, quitting, written
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_closing.py 4.4.542"

# --- Version history ----------------------------------------------------
# v4.4.542: Initial: closing waits for no server: Quit anyway stood still for a minute while a sync or a LOGOUT waited for a silent server

ANYWAY: float = 5.0  # a wait of these tests ends by itself, so a program that does wait fails its test instead of hanging


class SilentServer:
    """An IMAP server that stops answering at this command, until the program has closed; what it knows then."""

    def __init__(self, verb: str) -> None:
        self.verb: str = verb
        self.heard: threading.Event = threading.Event()
        self.closed: threading.Event = threading.Event()
        self.served: threading.Event = threading.Event()
        self.closed_in_time: list[bool] = []

    def fault(self, verb: str, count: int) -> bool:
        if verb != self.verb:
            return False
        self.heard.set()
        self.closed_in_time.append(self.closed.wait(ANYWAY))
        self.served.set()
        return True


def connected(server: FakeImapServer) -> ImapClient:
    client = ImapClient(ServerSettings("127.0.0.1", server.port, ConnectionSecurity.NONE, "user"), "secret")
    client.connect()
    return client


def slow_command(world: World, name: str, error: ChronoMailError | None = None) -> threading.Event:
    """A command that runs until it is released; its release."""
    started, release = threading.Event(), threading.Event()

    def slow(engine: Engine, parameters: NoParameters) -> None:
        started.set()
        release.wait(ANYWAY)
        if error is not None:
            raise error

    world.backend.registry.register(name, "Takes long", NoParameters, slow, doing="Taking long")
    return_of_the_call.clear()
    world.backend.call(name, {}, return_of_the_call.append, return_of_the_call.append)
    assert started.wait(ANYWAY)
    return release


return_of_the_call: list[object] = []


def test_closing_ends_a_sync_that_waits_for_a_silent_server(world: World) -> None:
    silent = SilentServer("NOOP")
    link = world.engine.imap_link(world.account_id)
    with FakeImapServer(fault=silent.fault) as server:
        client = connected(server)

        def sync(engine: Engine, parameters: NoParameters) -> None:
            with link.lock:
                client.noop()

        world.backend.registry.register("test.sync", "A sync", NoParameters, sync, doing="Syncing")
        world.backend.call("test.sync", {}, return_of_the_call.append, return_of_the_call.append)
        assert silent.heard.wait(ANYWAY)
        world.window.closed_for_good.connect(silent.closed.set)
        world.window.close()
        assert silent.served.wait(ANYWAY)
    assert silent.closed_in_time == [True]


def test_closing_says_logout_and_does_not_wait_for_the_answer(world: World) -> None:
    silent = SilentServer("LOGOUT")
    with FakeImapServer(fault=silent.fault) as server:
        world.engine.imap_link(world.account_id).client = connected(server)
        world.window.closed_for_good.connect(silent.closed.set)
        world.window.close()
        assert silent.served.wait(ANYWAY)
    assert silent.closed_in_time == [True] and world.engine.imap_link(world.account_id).client is None


def test_quit_anyway_with_a_silent_server_closes_and_the_mail_stays_in_the_outbox(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 0, on_refresh=True)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1 and world.window.mail_state.outbox_count() == 1)
    asked = quitting(world, monkeypatch, "quit")
    silent = SilentServer("LOGOUT")
    with FakeImapServer(fault=silent.fault) as server:
        world.engine.imap_link(world.account_id).client = connected(server)
        world.window.closed_for_good.connect(silent.closed.set)
        world.window.quit_program()
        assert silent.served.wait(ANYWAY)
    assert asked == [1] and silent.closed_in_time == [True] and not world.window.isVisible()
    assert held(world, "Outbox") == 1 and world.smtp.received == []


def test_the_window_stays_until_the_commands_ended_and_says_what_it_waits_for(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mainwindow, "CLOSING_QUIETLY_SECONDS", 0.0)
    release = slow_command(world, "test.slow")
    world.engine.sends = 1
    seen: list[tuple[str, bool, bool]] = []
    window = world.window
    timer = QTimer()

    def look() -> None:
        line = window.report.label.text()
        if line and (not seen or seen[-1][0] != line):
            seen.append((line, window.isVisible(), window.report.progress_bar.busy))
        if line.startswith("Waiting"):
            world.engine.sends = 0
        elif line.startswith("Shutting"):
            release.set()

    timer.timeout.connect(look)
    timer.start(5)
    window.close()
    timer.stop()
    release.set()
    assert seen[-2:] == [("Waiting for messages to be sent…", True, True), ("Shutting down…", True, True)]
    assert not window.isVisible()


def test_a_short_end_says_nothing(world: World) -> None:
    world.window.close()
    assert "Shutting" not in world.window.report.label.text()


def test_a_lost_connection_at_closing_is_no_news_to_the_person(world: World) -> None:
    release = slow_command(world, "test.lost", ProtocolError("The server closed the connection unexpectedly"))
    QTimer.singleShot(0, release.set)
    world.window.close()
    assert return_of_the_call == [] and "closed the connection" not in world.window.report.label.text()


def test_asking_to_close_again_while_it_ends_changes_nothing(world: World) -> None:
    release = slow_command(world, "test.slow-again")
    ended: list[int] = []
    world.window.closed_for_good.connect(lambda: ended.append(1))

    def again() -> None:
        world.window.close()
        release.set()

    QTimer.singleShot(0, again)
    world.window.close()
    assert ended == [1]


def test_a_send_at_closing_finishes_its_mail_and_the_next_one_stays_for_the_next_start(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 0, on_refresh=True)
    written(qtbot, world, "First").send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1)
    written(qtbot, world, "Second").send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 2)
    real_send = commands_outbox.send_stored

    def closing_during_the_send(*arguments: object, **options: object) -> object:
        world.engine.stop_event.set()
        world.engine.hang_up()
        return real_send(*arguments, **options)  # pyrefly: ignore

    monkeypatch.setattr(commands_outbox, "send_stored", closing_during_the_send)
    world.call("mail.send-outbox", now=True)
    assert len(world.smtp.received) == 1 and held(world, "Outbox") == 1 and held(world, "Sent") == 1
    monkeypatch.setattr(commands_outbox, "send_stored", real_send)
    world.engine.stop_event.clear()
    world.call("mail.send-outbox", now=True)
    assert len(world.smtp.received) == 2 and held(world, "Outbox") == 0 and held(world, "Sent") == 2
