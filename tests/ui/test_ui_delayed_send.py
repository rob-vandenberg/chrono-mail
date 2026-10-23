from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.ui import sendnotices
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_push import build, mail
from tests.ui.test_ui_window import started
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_delayed_send.py 2.3.315"

# --- Version history ----------------------------------------------------
# v2.3.315: Send only (the timer sends and fetches nothing, the server's "new mail" fetches nothing); the one Automatically row of Settings
# v2.2.314: Initial (B-21, B-20): the notice of a delayed send (Send now, Cancel, its end), until the next refresh, the scheduled refresh
#           (Send and Receive, or Receive only), the question on Quit, the settings


def held(world: World, folder: str) -> int:
    return message_list(world.engine, ListParameters(world.account_id, folder)).total


def written(qtbot: QtBot, world: World, subject: str = "Hello") -> ComposeWindow:
    """A message window of the program with a message ready to send."""
    started(qtbot, world)
    window = world.window.composing.open_composer(world.account_id, None, None)
    window.to.setText("you@example.com")
    window.subject.setText(subject)
    window.body.setPlainText("Hi there")
    return window


def delays(world: World, seconds: int = 0, on_refresh: bool = False) -> None:
    settings = world.window.settings
    settings.setValue("send_delay", seconds > 0)
    settings.setValue("send_delay_seconds", max(1, seconds))
    settings.setValue("send_on_refresh", on_refresh)


def test_send_shows_a_notice_and_send_now_sends_at_once(world: World, qtbot: QtBot) -> None:
    delays(world, 60)
    composer = written(qtbot, world)
    composer.send_button.click()
    notices = world.window.notices
    wait(qtbot, lambda: notices.counting() == 1 and held(world, "Outbox") == 1)
    notice = notices.notices()[0]
    assert notice.label.text() == "Sending “Hello” in 60 s" and notice.isVisible() and world.smtp.received == []
    notice.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1 and held(world, "Outbox") == 0 and held(world, "Sent") == 1)
    assert notices.counting() == 0


def test_the_end_of_the_countdown_sends_it(world: World, qtbot: QtBot) -> None:
    delays(world, 1)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: world.window.notices.counting() == 1)
    wait(qtbot, lambda: len(world.smtp.received) == 1 and world.window.notices.counting() == 0)


def test_cancel_opens_the_message_again_and_discard_leaves_nothing(world: World, qtbot: QtBot) -> None:
    delays(world, 60)
    written(qtbot, world, "Oops").send_button.click()
    notices = world.window.notices
    wait(qtbot, lambda: notices.counting() == 1)
    notices.notices()[0].cancel_button.click()
    composing = world.window.composing
    wait(qtbot, lambda: any(window.subject.text() == "Oops" for window in composing.composers))
    reopened = next(window for window in composing.composers if window.subject.text() == "Oops")
    assert held(world, "Outbox") == 0 and world.smtp.received == [] and reopened.is_modified()
    world.dialogs.answer = QMessageBox.StandardButton.Discard
    reopened.close()
    wait(qtbot, lambda: held(world, "Drafts") == 0)
    assert ("question", "Save this message?") in [(kind, title) for kind, title, _ in world.dialogs.shown]


def test_with_both_delays_the_end_of_the_countdown_leaves_it_for_the_next_refresh(world: World, qtbot: QtBot) -> None:
    delays(world, 1, on_refresh=True)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: world.window.notices.counting() == 1)
    wait(qtbot, lambda: world.window.notices.counting() == 0 and world.window.report.label.text() == "In the Outbox: the message goes with the next refresh")
    assert world.smtp.received == [] and held(world, "Outbox") == 1
    world.window.checking.send_and_receive()  # Refresh
    wait(qtbot, lambda: len(world.smtp.received) == 1 and held(world, "Outbox") == 0)


def test_until_the_next_refresh_alone_has_no_notice(world: World, qtbot: QtBot) -> None:
    delays(world, 0, on_refresh=True)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1 and world.window.report.label.text() == "In the Outbox: “Hello” goes with the next refresh")
    assert world.window.notices.counting() == 0 and world.smtp.received == []


@pytest.mark.parametrize("refresh", ["send_receive", "receive", "send"])
def test_the_scheduled_refresh_sends_and_receives_as_chosen(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, refresh: str) -> None:
    delays(world, 0, on_refresh=True)
    world.window.settings.setValue("scheduled_refresh", refresh)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1)
    checking = world.window.checking
    received: list[set[str] | None] = []
    real_check = checking.check_accounts

    def recorded(only: set[str] | None, manual: bool = False, new_round: bool = True, also_calendars: set[str] | None = None) -> None:
        received.append(only)
        real_check(only, manual, new_round, also_calendars)

    monkeypatch.setattr(checking, "check_accounts", recorded)
    checking.check_on_general_interval()  # the timer
    wait(qtbot, lambda: not checking.checking and not checking.sending and world.backend._pending == {})  # noqa: SLF001
    assert len(world.smtp.received) == (0 if refresh == "receive" else 1)
    assert bool(received) == (refresh != "send")  # Send only fetches no mail


def quitting(world: World, monkeypatch: pytest.MonkeyPatch, answer: str, quiet: bool = False) -> list[int]:
    """Quit is asked for with this answer to the question about the Outbox; the counts it was asked with."""
    asked: list[int] = []

    def question(parent: QWidget, count: int) -> tuple[str, bool]:
        asked.append(count)
        return answer, quiet

    monkeypatch.setattr(sendnotices, "quit_question", question)
    world.window.settings.setValue("warn_outbox_on_quit", True)
    return asked


@pytest.mark.parametrize("answer", ["cancel", "quit", "send"])
def test_quitting_with_mail_in_the_outbox_asks_first(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    delays(world, 0, on_refresh=True)
    written(qtbot, world).send_button.click()
    window: MainWindow = world.window
    wait(qtbot, lambda: held(world, "Outbox") == 1 and window.mail_state.outbox_count() == 1)
    asked = quitting(world, monkeypatch, answer, quiet=answer == "quit")
    window.quit_program()
    assert asked == [1]
    if answer == "cancel":
        assert window.isVisible() and world.smtp.received == []
    elif answer == "quit":
        wait(qtbot, lambda: not window.isVisible())
        assert world.smtp.received == [] and not read_preferences(window.settings).warn_outbox_on_quit  # Do not warn again
    else:
        wait(qtbot, lambda: len(world.smtp.received) == 1 and not window.isVisible())


def test_quitting_during_a_countdown_without_the_question_sends_it_first(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 60)
    written(qtbot, world).send_button.click()
    window = world.window
    wait(qtbot, lambda: window.notices.counting() == 1)
    asked = quitting(world, monkeypatch, "cancel")
    window.settings.setValue("warn_outbox_on_quit", False)
    window.quit_program()
    wait(qtbot, lambda: len(world.smtp.received) == 1 and not window.isVisible())
    assert asked == []


def test_settings_general_holds_the_new_choices(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(dialog)
    assert not dialog.send_delay_seconds.isEnabled() and dialog.scheduled_refresh.currentData() == "send_receive"
    dialog.send_delay.setChecked(True)
    dialog.send_delay_seconds.setValue(25)
    dialog.send_on_refresh.setChecked(True)
    dialog.scheduled_refresh.setCurrentIndex(dialog.scheduled_refresh.findData("receive"))
    dialog.warn_outbox_on_quit.setChecked(False)
    chosen = dialog.chosen()
    assert dialog.send_delay_seconds.isEnabled()
    assert (chosen.send_delay, chosen.send_delay_seconds, chosen.send_on_refresh, chosen.scheduled_refresh, chosen.warn_outbox_on_quit) == (True, 25, True, "receive", False)


def test_with_send_only_the_servers_new_mail_fetches_nothing(tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    for imap in build(tmp_path, qtbot):
        window = imap.window
        window.settings.setValue("scheduled_refresh", "send")
        window.start()
        wait(qtbot, lambda: imap.account in window.checking.watching and window.backend._pending == {})  # noqa: SLF001
        received: list[set[str] | None] = []

        def recorded(only: set[str] | None, manual: bool = False, new_round: bool = True, also_calendars: set[str] | None = None) -> None:
            received.append(only)

        monkeypatch.setattr(window.checking, "check_accounts", recorded)
        waits = sum(1 for command in imap.server.commands if command.upper().endswith("IDLE"))
        imap.server.add("INBOX", mail(2))
        imap.server.push()
        wait(qtbot, lambda: sum(1 for command in imap.server.commands if command.upper().endswith("IDLE")) > waits, timeout=15000)  # it waits again
        assert received == []


def test_the_automatic_row_greys_its_choice_and_minutes_with_its_tick(world: World, qtbot: QtBot) -> None:
    world.window.settings.setValue("auto_check", False)
    world.window.settings.setValue("scheduled_refresh", "send")
    dialog = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(dialog)
    assert [dialog.scheduled_refresh.itemText(index) for index in range(dialog.scheduled_refresh.count())] == ["Send and Receive", "Receive only", "Send only"]
    assert dialog.scheduled_refresh.currentData() == "send" and not dialog.scheduled_refresh.isEnabled() and not dialog.interval.isEnabled()
    dialog.auto_check.setChecked(True)
    assert dialog.scheduled_refresh.isEnabled() and dialog.interval.isEnabled() and dialog.auto_check.text() == "Automatically"
    assert dialog.check_on_start.text() == "Refresh when the program starts" and world.window.act_auto.text() == "Refresh automatically"
