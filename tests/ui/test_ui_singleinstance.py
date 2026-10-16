import os
import subprocess
import sys

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui import app
from chrono_mail.ui.singleinstance import ShowRequests, ask_to_show
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_singleinstance.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: Initial tests: a second start shows the running program's window, or says that it is running

SECOND_START = (
    "import sys; from PySide6.QtCore import QCoreApplication; from chrono_mail.ui.singleinstance import ask_to_show; "
    "application = QCoreApplication([]); sys.exit(0 if ask_to_show(sys.argv[1]) else 3)"
)


def test_a_second_start_gets_the_running_window_shown(world: World, qtbot: QtBot) -> None:
    name = f"chrono-mail-test-{os.getpid()}"
    requests = ShowRequests(name)
    requests.show_requested.connect(world.window.bring_forward)
    assert requests.listen()
    world.window.hide()  # as in the tray
    second = subprocess.Popen([sys.executable, "-c", SECOND_START, name], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    with qtbot.waitSignal(requests.show_requested, timeout=10000):
        pass
    qtbot.waitUntil(lambda: second.poll() is not None, timeout=10000)
    assert second.returncode == 0  # the running program answered: the second start just quits
    assert world.window.isVisible()


def test_a_second_start_without_an_answer_says_that_the_program_is_running(monkeypatch: pytest.MonkeyPatch, qtbot: QtBot) -> None:
    assert ask_to_show(f"chrono-mail-test-nobody-{os.getpid()}", 500) is False
    said: list[str] = []
    monkeypatch.setattr(app, "ask_to_show", lambda: False)
    def information(parent: object, title: str, text: str, *rest: object) -> QMessageBox.StandardButton:
        said.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", information)
    assert app.second_start() == 1
    assert said == ["Chrono Mail is already running."]
