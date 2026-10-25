from collections.abc import Generator

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import statusreport
from chrono_mail.ui.statusreport import StatusReport
from tests.ui.world import wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_statusclear.py 3.6.363"

# --- Version history ----------------------------------------------------
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


def test_nothing_goes_while_work_runs_and_the_minute_counts_from_its_end(status: StatusReport, qtbot: QtBot) -> None:
    status.busy_changed(1)
    status.show("Preparing to move 5,000 messages to Junk…")
    qtbot.wait(200)
    assert status.label.text() == "Preparing to move 5,000 messages to Junk…"
    status.busy_changed(0)
    wait(qtbot, lambda: status.label.text() == "")
