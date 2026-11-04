import os
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

from chrono_mail.core import ConnectionSecurity, ServerSettings
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.ui import app
from tests.fakeimap import FakeImapServer
from tests.ui.test_ui_delayed_send import delays, held, quitting, written
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_closing.py 4.4.540"

# --- Version history ----------------------------------------------------
# v4.4.540: Initial: closing waits for no server (Quit anyway stood still for a minute while a sync or a LOGOUT waited for a silent server)

ANYWAY: float = 5.0  # a wait of these tests ends by itself, so a program that does wait fails its test instead of hanging


def holding_the_connection(world: World) -> tuple[threading.Thread, threading.Event, list[bool]]:
    """A sync that holds its account's connection until the program has closed; what it says then: whether it held it at the end."""
    link = world.engine.imap_link(world.account_id)
    holds, closed = threading.Event(), threading.Event()
    still_holding: list[bool] = []

    def sync() -> None:
        with link.lock:
            holds.set()
            still_holding.append(closed.wait(ANYWAY))

    thread = threading.Thread(target=sync)
    thread.start()
    assert holds.wait(ANYWAY)
    world.window.closed_for_good.connect(closed.set)
    return thread, closed, still_holding


def slow_command(world: World, name: str, checking: bool = False) -> tuple[threading.Event, threading.Event]:
    """A command that runs until it is released; its start and its release."""
    started, release = threading.Event(), threading.Event()

    def slow(engine: Engine, parameters: NoParameters) -> None:
        started.set()
        release.wait(ANYWAY)

    world.backend.registry.register(name, "Takes long", NoParameters, slow, doing="Taking long")
    world.backend.call(name, {}, ignored, checking=checking)
    assert started.wait(ANYWAY)
    return started, release


def ignored(result: object) -> None:
    pass


def exits_recorded(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    return exits


def test_closing_does_not_wait_for_a_sync_that_holds_its_connection(world: World) -> None:
    thread, _, still_holding = holding_the_connection(world)
    world.window.close()
    thread.join()
    assert still_holding == [True]


def test_quit_anyway_closes_while_a_sync_hangs_and_the_mail_stays_in_the_outbox(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 0, on_refresh=True)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1 and world.window.mail_state.outbox_count() == 1)
    asked = quitting(world, monkeypatch, "quit")
    thread, _, still_holding = holding_the_connection(world)
    world.window.quit_program()
    thread.join()
    assert asked == [1] and still_holding == [True] and not world.window.isVisible()
    assert held(world, "Outbox") == 1 and world.smtp.received == []


def test_closing_says_logout_and_does_not_wait_for_the_answer(world: World) -> None:
    closed, served = threading.Event(), threading.Event()
    closed_when_heard: list[bool] = []

    def silent_on_logout(verb: str, count: int) -> bool:
        if verb != "LOGOUT":
            return False
        closed_when_heard.append(closed.wait(ANYWAY))
        served.set()
        return True

    with FakeImapServer(fault=silent_on_logout) as server:
        client = ImapClient(ServerSettings("127.0.0.1", server.port, ConnectionSecurity.NONE, "user"), "secret")
        client.connect()
        world.engine.imap_link(world.account_id).client = client
        world.window.closed_for_good.connect(closed.set)
        world.window.close()
        assert served.wait(ANYWAY)
    assert closed_when_heard == [True] and world.engine.imap_link(world.account_id).client is None


def test_the_window_is_gone_while_a_command_still_ends(world: World) -> None:
    _, release = slow_command(world, "test.slow")
    visible: list[bool] = []

    def look() -> None:
        visible.append(world.window.isVisible())
        release.set()

    QTimer.singleShot(0, look)
    world.window.close()
    assert visible == [False]


def test_the_process_ends_while_a_mail_check_hangs_with_the_settings_on_disk(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = exits_recorded(monkeypatch)
    _, release = slow_command(world, "test.slow-check", checking=True)
    world.window.settings.setValue("written_at_the_end", "yes")
    app.end_of_program(world.backend, world.window.settings, 7)
    release.set()
    assert exits == [7] and "written_at_the_end" in Path(world.window.settings.fileName()).read_text(encoding="utf-8")


def test_the_process_ends_as_usual_when_nothing_runs(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = exits_recorded(monkeypatch)
    assert app.end_of_program(world.backend, world.window.settings, 3) == 3 and exits == []


def test_a_command_of_the_person_is_never_cut_off_at_the_end(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = exits_recorded(monkeypatch)
    _, release = slow_command(world, "test.slow")
    ended = threading.Event()

    def finish() -> None:
        ended.set()
        release.set()

    timer = threading.Timer(0.2, finish)
    timer.start()
    code = app.end_of_program(world.backend, world.window.settings, 0)
    timer.join()
    assert code == 0 and ended.is_set() and exits == []
