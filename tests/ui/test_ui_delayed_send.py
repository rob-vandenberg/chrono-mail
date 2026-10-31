from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ListParameters, SendParameters, mail_send, message_list
from chrono_mail.ui import sendnotices
from chrono_mail.ui.colours import theme
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_push import build, mail
from tests.ui.test_ui_window import open_folder, started
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_delayed_send.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: nothing is checked or sent after a rebuild (D-300)
# v4.0.374: the stand-in of the status line takes the running flag of 3.6.364 and passes it on
# v3.1.341: a message opened from the Outbox leaves it first, with its countdown (RV-156)
# v2.4.327: the check after a restore, a rebuild, an import or a first account never sends; a refresh a rebuild held back runs after it (D-245)
# v2.3.317: the status line's texts are recorded, never read at one moment; Cancel waits for the window it opens (not the one that sent, which may exist until Qt deletes it); the notice as the owner's mockup: the buttons below the text at the right, the outline from the colour file
# v2.3.316: the refresh at start does its own choice; the two rows of Settings, each greyed with its tick
# v2.3.315: Send only (the timer sends and fetches nothing, the server's "new mail" fetches nothing); the one Automatically row of Settings
# v2.2.314: Initial (B-21, B-20): the notice of a delayed send (Send now, Cancel, its end), until the next refresh, the scheduled refresh
#           (Send and Receive, or Receive only), the question on Quit, the settings


def said(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every text the status line is given from now on (another part may write the line again a moment later, so it is never read at one moment)."""
    texts: list[str] = []
    real_show = window.report.show

    def show(text: str, error: bool = False, running: bool = False) -> None:
        texts.append(text)
        real_show(text, error, running)

    monkeypatch.setattr(window.report, "show", show)
    return texts


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
    label, send_now, cancel = notice.label.geometry(), notice.send_button.geometry(), notice.cancel_button.geometry()
    assert send_now.top() >= label.bottom() and cancel.left() > send_now.right() and notice.width() - cancel.right() < notice.width() // 4  # below, at the right
    assert f"solid {theme().notice_border}" in notice.styleSheet() and "border-radius" in notice.styleSheet()
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
    composing = world.window.composing
    before = list(composing.composers)  # the window that sent it may still exist, closed, until Qt deletes it
    notices.notices()[0].cancel_button.click()
    wait(qtbot, lambda: any(window not in before and window.subject.text() == "Oops" for window in composing.composers))
    reopened = next(window for window in composing.composers if window not in before and window.subject.text() == "Oops")
    assert held(world, "Outbox") == 0 and world.smtp.received == [] and reopened.is_modified()
    world.dialogs.answer = QMessageBox.StandardButton.Discard
    reopened.close()
    wait(qtbot, lambda: held(world, "Drafts") == 0)
    assert ("question", "Save this message?") in [(kind, title) for kind, title, _ in world.dialogs.shown]


def test_a_message_opened_from_the_outbox_leaves_it_before_it_can_be_changed(world: World, qtbot: QtBot) -> None:
    """RV-156: a message opened from the Outbox stayed there while it was changed; a refresh sent it, then Send sent the changed copy too."""
    delays(world, 60)
    written(qtbot, world, "Typo").send_button.click()
    notices = world.window.notices
    wait(qtbot, lambda: notices.counting() == 1)
    open_folder(world, qtbot, "Outbox", 1)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    composing = world.window.composing
    before = list(composing.composers)
    composing.edit_draft()
    wait(qtbot, lambda: any(window not in before and window.subject.text() == "Typo" for window in composing.composers))
    assert held(world, "Outbox") == 0 and held(world, "Drafts") == 1 and notices.counting() == 0 and world.smtp.received == []


def test_with_both_delays_the_end_of_the_countdown_leaves_it_for_the_next_refresh(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 1, on_refresh=True)
    texts = said(world.window, monkeypatch)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: world.window.notices.counting() == 1)
    wait(qtbot, lambda: world.window.notices.counting() == 0 and "In the Outbox: the message goes with the next refresh" in texts)
    assert world.smtp.received == [] and held(world, "Outbox") == 1
    world.window.checking.send_and_receive()  # Refresh
    wait(qtbot, lambda: len(world.smtp.received) == 1 and held(world, "Outbox") == 0)


def test_until_the_next_refresh_alone_has_no_notice(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    delays(world, 0, on_refresh=True)
    texts = said(world.window, monkeypatch)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1 and "In the Outbox: “Hello” goes with the next refresh" in texts)
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


def test_each_refresh_row_greys_its_choice_with_its_tick(world: World, qtbot: QtBot) -> None:
    settings = world.window.settings
    settings.setValue("auto_check", False)
    settings.setValue("check_on_start", False)
    settings.setValue("scheduled_refresh", "send")
    dialog = SettingsDialog(world.backend, settings)
    qtbot.addWidget(dialog)
    for box in (dialog.start_refresh, dialog.scheduled_refresh):
        assert [box.itemText(index) for index in range(box.count())] == ["Send and Receive", "Receive only", "Send only"] and not box.isEnabled()
    assert dialog.start_refresh.currentData() == "send_receive" and dialog.scheduled_refresh.currentData() == "send" and not dialog.interval.isEnabled()
    dialog.check_on_start.setChecked(True)
    assert dialog.start_refresh.isEnabled() and not dialog.scheduled_refresh.isEnabled()
    dialog.auto_check.setChecked(True)
    assert dialog.scheduled_refresh.isEnabled() and dialog.interval.isEnabled()
    dialog.start_refresh.setCurrentIndex(dialog.start_refresh.findData("receive"))
    chosen = dialog.chosen()
    assert (chosen.start_refresh, chosen.scheduled_refresh, chosen.check_on_start, chosen.auto_check) == ("receive", "send", True, True)
    assert dialog.check_on_start.text() == "Refresh when the program starts" and dialog.auto_check.text() == "Automatic refresh"
    assert world.window.act_auto.text() == "Refresh automatically"


@pytest.mark.parametrize("start", ["send_receive", "receive"])
def test_the_refresh_at_start_does_its_own_choice(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, start: str) -> None:
    mail_send(world.engine, SendParameters(world.account_id, to=("you@example.com",), subject="Waiting", body="x", hold=True))
    settings = world.window.settings
    settings.setValue("check_on_start", True)
    settings.setValue("start_refresh", start)
    settings.setValue("scheduled_refresh", "send_receive" if start == "receive" else "receive")  # the automatic row says the other
    checking = world.window.checking
    received: list[set[str] | None] = []
    real_check = checking.check_accounts

    def recorded(only: set[str] | None, manual: bool = False, new_round: bool = True, also_calendars: set[str] | None = None) -> None:
        received.append(only)
        real_check(only, manual, new_round, also_calendars)

    monkeypatch.setattr(checking, "check_accounts", recorded)
    world.window.start()
    wait(qtbot, lambda: bool(received) and not checking.checking and not checking.sending and world.backend._pending == {})  # noqa: SLF001 - the refresh at start is done
    assert len(world.smtp.received) == (1 if start == "send_receive" else 0)


def test_nothing_is_checked_or_sent_after_a_rebuild(world: World, qtbot: QtBot) -> None:
    """The Windows computer's W6: the check after a restore sent the restored Outbox (D-245); since 4.2.449 a rebuild checks nothing by itself (D-300)."""
    delays(world, 0, on_refresh=True)
    world.window.settings.setValue("scheduled_refresh", "send_receive")
    world.window.settings.setValue("auto_check", True)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1)
    checking = world.window.checking
    checking.rebuild_running(True)
    checking.rebuild_running(False)  # nothing was held back: nothing runs
    assert not checking.checking and not checking.sending
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    assert world.smtp.received == [] and held(world, "Outbox") == 1


@pytest.mark.parametrize("start", ["send_receive", "receive"])
def test_a_refresh_a_rebuild_held_back_runs_after_it_with_its_own_choice(world: World, qtbot: QtBot, start: str) -> None:
    delays(world, 0, on_refresh=True)
    world.window.settings.setValue("start_refresh", start)
    written(qtbot, world).send_button.click()
    wait(qtbot, lambda: held(world, "Outbox") == 1)
    checking = world.window.checking
    checking.rebuild_running(True)
    checking.start_refresh()  # the refresh at start, while an index is filled in
    assert not checking.sending and not checking.checking
    checking.rebuild_running(False)
    wait(qtbot, lambda: not checking.checking and not checking.sending and world.backend._pending == {})  # noqa: SLF001
    assert len(world.smtp.received) == (1 if start == "send_receive" else 0)
