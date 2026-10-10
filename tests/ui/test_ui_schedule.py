import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.whendialog import WhenDialog, presets
from tests.ui.test_ui_composer import fill, new_composer
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_schedule.py 0.0.50"

# --- Version history ----------------------------------------------------
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.34: Initial send later and snooze window tests


def real_clock(world: World) -> None:
    """The test engine's clock stands still in 2027; these tests schedule from the real now."""
    world.engine.clock = time.time


def choose_time(monkeypatch: pytest.MonkeyPatch, text: str, accept: bool = True) -> None:
    def execute(self: WhenDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    def chosen(self: WhenDialog) -> str:
        return text

    monkeypatch.setattr(WhenDialog, "exec", execute)
    monkeypatch.setattr(WhenDialog, "chosen", chosen)


def in_hours(hours: float) -> str:
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def test_the_presets_are_ahead_and_in_order() -> None:
    now = datetime(2026, 10, 7, 10, 30)  # a Wednesday morning
    choices = dict(presets(now))
    assert choices["In an hour"] == datetime(2026, 10, 7, 11, 30) and choices["This evening"] == datetime(2026, 10, 7, 18, 0)
    assert choices["Tomorrow morning"] == datetime(2026, 10, 8, 8, 0) and choices["Monday morning"] == datetime(2026, 10, 12, 8, 0)
    late = dict(presets(datetime(2026, 10, 7, 17, 50)))
    assert "This evening" not in late  # less than half an hour away: not offered
    sunday = dict(presets(datetime(2026, 10, 11, 12, 0)))
    assert sunday["Monday morning"] == datetime(2026, 10, 12, 8, 0)


def test_the_dialog_returns_the_chosen_time(qtbot: QtBot) -> None:
    dialog = WhenDialog("t", "q", "ok", now=datetime(2026, 10, 7, 10, 30))
    qtbot.addWidget(dialog)
    assert dialog.chosen() == "2026-10-08T08:00"
    next(button for button in dialog.preset_buttons if button.text() == "In an hour").click()
    assert dialog.chosen() == "2026-10-07T11:30"


def test_send_later_keeps_the_message_in_the_outbox(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = new_composer(world, qtbot)
    fill(window)
    real_clock(world)
    choose_time(monkeypatch, in_hours(3))
    window.later_button.click()
    wait(qtbot, lambda: not window.isVisible())
    assert world.smtp.received == []
    (info,) = world.call("mail.scheduled")
    assert info.subject == "Hello"


def test_cancelling_the_time_dialog_sends_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = new_composer(world, qtbot)
    fill(window)
    choose_time(monkeypatch, in_hours(3), accept=False)
    window.later_button.click()
    qtbot.wait(200)
    assert window.isVisible() and world.call("mail.scheduled") == ()
    window._finished = True  # noqa: SLF001
    window.close()


def test_the_window_sends_what_is_due_and_wakes_snoozed_mail(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    target = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=target.message_id, until=in_hours(1))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Due soon", body="x", send_at=in_hours(1))
    world.engine.clock = lambda: datetime.now().timestamp() + 2 * 3600
    world.window.run_due()
    wait(qtbot, lambda: "1 scheduled message(s) sent, 1 snoozed message(s) back" in world.window.status_label.text())
    assert len(world.smtp.received) == 1


def test_snoozing_from_the_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0))
    real_clock(world)
    choose_time(monkeypatch, in_hours(5))
    world.window.act_snooze.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert len(world.call("message.snoozed")) == 1 and "Snoozed 1 message(s)" in world.window.status_label.text()


def test_the_scheduled_window_sends_now_and_takes_back(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    first = world.call("mail.send", account=world.account_id, to="you@example.com", subject="One", body="x", send_at=in_hours(4))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Two", body="x", send_at=in_hours(8))
    dialog = ScheduledPanel(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == 2)
    assert "One" in dialog.list.item(0).text() and dialog.now_button.isEnabled() and dialog.back_button.isEnabled()
    dialog.now_button.click()
    wait(qtbot, lambda: dialog.list.count() == 1 and len(world.smtp.received) == 1)
    dialog.back_button.click()
    wait(qtbot, lambda: dialog.list.count() == 0)
    assert dialog.changed and [row.subject for row in world.call("message.list", account=world.account_id, folder="Drafts").messages] == ["Two"] and first.message_id


def test_a_snoozed_message_can_be_brought_back_from_the_window(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    target = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=target.message_id, until=in_hours(6))
    dialog = ScheduledPanel(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == 1)
    assert not dialog.back_button.isEnabled()
    dialog.now_button.click()
    wait(qtbot, lambda: dialog.list.count() == 0)
    assert world.call("message.list", account=world.account_id, folder="Snoozed").total == 0
