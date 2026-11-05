from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.ui.calendarmail import Choice
from tests.ui.test_ui_calendar import calendar_id
from tests.ui.test_ui_invitation_window import REQUEST_FILE, calendar_file, opened_file
from tests.ui.world import World, answer_calendar_mail

# --- Version ------------------------------------------------------------
__version__ = "test_ui_answer_from_calendar.py 4.4.556"

# --- Version history ----------------------------------------------------
# v4.4.556: Initial tests: an answer from the calendar refreshes an open card (D-310)


def test_an_answer_from_the_calendar_refreshes_an_open_card_of_that_meeting(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    assert window.card.accept_button.isVisible()
    copy = REQUEST_FILE.replace("METHOD:REQUEST\r\n", "")
    item = world.call("event.import", calendar=calendar_id(world), text=copy).items[0]
    answer_calendar_mail(monkeypatch, Choice.QUIET)
    world.window.reading.answer_event(item, "", "TENTATIVE")
    qtbot.waitUntil(lambda: window.card.change_button.isVisible(), timeout=5000)
    assert window.invitation is not None and window.invitation.your_answer == "TENTATIVE"
