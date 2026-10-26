from collections.abc import Generator

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import statusreport
from chrono_mail.ui.statusreport import StatusReport
from tests.ui.world import wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_statusclear.py 3.6.365"

# --- Version history ----------------------------------------------------
# v3.6.365: short work never moves the busy bar, longer work does
# v3.6.364: a line has its own clock: the busy bar flashing never keeps it; a line about work still running stays
# v3.6.363: Initial: an informative line goes a minute after it came (here shortened), a warning or an error stays, nothing goes while work runs


@pytest.fixture
def status(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> Generator[StatusReport]:
    monkeypatch.setattr(statusreport, "CLEAR_AFTER_MS", 50, raising=False)  # the minute, shortened
    window = QWidget()
    qtbot.addWidget(window)
    yield StatusReport(window, lambda account: account)


def test_an_informative_line_goes_after_its_minute(status: StatusReport, qtbot: QtBot) -> None:
    status.show("Moved 3 messages to Junk")
    wait(qtbot, lambda: status.label.text() == "")


def test_a_warning_or_an_error_stays(status: StatusReport, qtbot: QtBot) -> None:
    status.show("Could not move: the server said no", error=True)
    qtbot.wait(200)
    assert status.label.text() == "Could not move: the server said no"


def test_a_line_goes_on_its_own_clock_while_the_busy_bar_flashes(status: StatusReport, qtbot: QtBot) -> None:
    """Short work that has nothing to do with the line (the busy bar flashing more often than the minute) never keeps it."""
    status.show("Moved 1,292 messages to Trash")
    flash = QTimer()
    flash.timeout.connect(lambda: (status.busy_changed(1), status.busy_changed(0)))
    flash.start(20)  # more often than the (shortened) minute, all the time
    try:
        wait(qtbot, lambda: status.label.text() == "", timeout=2000)
    finally:
        flash.stop()


def test_a_line_about_work_still_running_stays(status: StatusReport, qtbot: QtBot) -> None:
    status.show("Preparing to move 5,000 messages to Junk…", running=True)
    qtbot.wait(200)
    assert status.label.text() == "Preparing to move 5,000 messages to Junk…"


def test_short_work_never_moves_the_busy_bar(status: StatusReport, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Work shorter than the moment (the minute check for scheduled and snoozed messages: about 36 ms) never shows on the bar."""
    monkeypatch.setattr(statusreport, "BUSY_AFTER_MS", 300, raising=False)
    moved: list[bool] = []
    status.progress_bar.set_busy = lambda busy: moved.append(busy)  # type: ignore[method-assign]
    for _ in range(3):
        status.busy_changed(1)
        qtbot.wait(30)
        status.busy_changed(0)
    qtbot.wait(400)
    assert True not in moved


def test_longer_work_moves_the_busy_bar(status: StatusReport, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(statusreport, "BUSY_AFTER_MS", 50, raising=False)
    status.busy_changed(1)
    wait(qtbot, lambda: status.progress_bar.busy)
    status.busy_changed(0)
    assert not status.progress_bar.busy
