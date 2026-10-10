from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from chrono_mail.ui.backend import Backend
from chrono_mail.ui.mainwindow import MainWindow
from tests.enginefactory import make_engine
from tests.fakeimap import FakeImapServer, Fault
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.ui.world import REGISTRY, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_push.py 0.0.32"

# --- Version history ----------------------------------------------------
# v0.0.32: Initial tests of push (IDLE) in the window


def mail(number: int) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<m{number}@x>")


class Imap:
    def __init__(self, window: MainWindow, server: FakeImapServer, account: str) -> None:
        self.window, self.server, self.account = window, server, account


def build(tmp_path: Path, qtbot: QtBot, idle: bool = True, auto: bool = True, fault: Fault | None = None) -> Generator[Imap]:
    engine = make_engine(tmp_path)
    with FakeImapServer(plain=False, idle=idle, fault=fault) as server, FakeSmtpServer() as smtp:
        server.add("INBOX", mail(1))
        created = REGISTRY.run(engine, "account.add", dict(
            kind="imap", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=server.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
        ))
        account = str(getattr(created, "account_id"))
        REGISTRY.run(engine, "mail.fetch", {"account": account})
        backend = Backend(engine)
        settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
        settings.setValue("check_on_start", False)
        settings.setValue("auto_check", auto)
        settings.setValue("notifications", False)
        window = MainWindow(backend, settings)
        qtbot.addWidget(window)
        window.show()
        yield Imap(window, server, account)
        window.close()
        backend.wait_for_done(8000)


@pytest.fixture
def imap(tmp_path: Path, qtbot: QtBot) -> Generator[Imap]:
    yield from build(tmp_path, qtbot)


def test_new_mail_on_the_server_shows_up_without_asking(imap: Imap, qtbot: QtBot) -> None:
    imap.window.start()
    wait(qtbot, lambda: imap.window.list_model.rowCount() == 1 and imap.account in imap.window._watching)  # noqa: SLF001
    imap.server.add("INBOX", mail(2))
    imap.server.push()
    wait(qtbot, lambda: imap.window.list_model.rowCount() == 2, timeout=15000)
    assert imap.account in imap.window._watching or imap.window._watching == set()  # noqa: SLF001 - it goes on waiting afterwards
    imap.server.add("INBOX", mail(3))
    imap.server.push()
    wait(qtbot, lambda: imap.window.list_model.rowCount() == 3, timeout=15000)


def test_waiting_for_mail_is_not_busy_and_ends_when_the_window_closes(imap: Imap, qtbot: QtBot) -> None:
    imap.window.start()
    wait(qtbot, lambda: imap.account in imap.window._watching and imap.window.list_model.rowCount() == 1 and imap.window.backend._pending == {})  # noqa: SLF001
    qtbot.wait(300)
    assert imap.window.backend._pending == {} and imap.account in imap.window._watching  # noqa: SLF001 - a wait does not count as work
    imap.window.close()
    assert imap.window.backend.wait_for_done(5000)
    assert imap.window.backend.engine.stop_event.is_set()


def test_a_server_without_idle_is_left_to_the_timer(tmp_path: Path, qtbot: QtBot) -> None:
    for world in build(tmp_path, qtbot, idle=False):
        world.window.start()
        wait(qtbot, lambda: world.account in world.window._no_push)  # noqa: SLF001
        assert world.account not in world.window._watching  # noqa: SLF001


def test_no_watching_when_automatic_checking_is_off(tmp_path: Path, qtbot: QtBot) -> None:
    for world in build(tmp_path, qtbot, auto=False):
        world.window.start()
        wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
        qtbot.wait(300)
        assert world.window._watching == set()  # noqa: SLF001
        world.window.act_auto.setChecked(True)
        wait(qtbot, lambda: world.account in world.window._watching)  # noqa: SLF001


def test_a_lost_connection_is_tried_again_later(tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.ui import mainwindow

    monkeypatch.setattr(mainwindow, "WATCH_RETRY_MS", 200)
    cut: list[int] = []

    def cut_the_first_wait(verb: str, count: int) -> bool:
        if verb == "IDLE" and not cut:
            cut.append(1)
            return True
        return False

    for world in build(tmp_path, qtbot, fault=cut_the_first_wait):
        world.window.start()
        wait(qtbot, lambda: sum(command == "IDLE" for command in world.server.commands) >= 2, timeout=15000)
        wait(qtbot, lambda: world.account in world.window._watching)  # noqa: SLF001
