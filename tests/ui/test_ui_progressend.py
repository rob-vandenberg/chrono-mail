from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.statusreport import StatusReport
from tests.enginefactory import make_engine

# --- Version ------------------------------------------------------------
__version__ = "test_ui_progressend.py 4.2.392"

# --- Version history ----------------------------------------------------
# v4.2.392: Initial: the progress line of a command (an import's count) leaves the status bar when the command ends, done or failed


@pytest.fixture
def wired(tmp_path: Path, qtbot: QtBot) -> Generator[tuple[Backend, StatusReport]]:
    window = QWidget()
    qtbot.addWidget(window)
    backend = Backend(make_engine(tmp_path))
    report = StatusReport(window, lambda account: account)
    backend.progress.connect(report.progress)
    backend.progress_ended.connect(report.progress_ended)
    yield backend, report


def _importing(engine: Engine, parameters: NoParameters) -> None:
    engine.progress("Importing mail", 139_593, 139_593)


def _failing(engine: Engine, parameters: NoParameters) -> None:
    engine.progress("Importing from the data files", 12, 40)
    raise ChronoMailError("The data file is damaged")


@pytest.mark.parametrize(("command", "handler"), [("test.import-done", _importing), ("test.import-failed", _failing)])
def test_the_progress_line_goes_when_the_command_ends(
    wired: tuple[Backend, StatusReport], qtbot: QtBot, command: str, handler: Callable[[Engine, NoParameters], None],
) -> None:
    backend, report = wired
    seen: list[str] = []

    def record(what: str, done: int, total: object) -> None:
        seen.append(report.label.text())

    backend.progress.connect(record)
    backend.registry.register(command, "An import", NoParameters, handler, doing="Importing")
    answered: list[str] = []
    backend.call(command, {}, lambda result: answered.append("done"), lambda error: answered.append("failed"))
    qtbot.waitUntil(lambda: bool(answered), timeout=5000)
    assert seen and "… " in seen[0]  # the count was shown while it ran
    assert report.label.text() == ""  # and is gone as soon as the command ended


def test_a_line_shown_after_the_progress_stays(wired: tuple[Backend, StatusReport], qtbot: QtBot) -> None:
    backend, report = wired
    report.progress("Importing mail", 5, 10)
    report.show("Could not move: the server said no", error=True)
    report.progress_ended("Importing mail")
    assert report.label.text() == "Could not move: the server said no"
