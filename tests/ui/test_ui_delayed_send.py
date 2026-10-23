import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.ui import sendnotices
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_window import started
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_delayed_send.py 2.2.314"

# --- Version history ----------------------------------------------------
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


@pytest.mark.parametrize("refresh", ["send_receive", "receive"])
def test_the_scheduled_refresh_sends_the_outbox_unless_it_only_receives(world: World, qtbot: QtBot, refresh: str) -> None:
    delays(world, 0, on_refresh=True)
    world.window.settings.setValue("scheduled_refresh", refresh)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1)
    world.window.checking.check_on_general_interval()  # the timer
    wait(qtbot, lambda: not world.window.checking.checking and not world.window.checking.sending and world.backend._pending == {})  # noqa: SLF001
    assert len(world.smtp.received) == (1 if refresh == "send_receive" else 0)


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
