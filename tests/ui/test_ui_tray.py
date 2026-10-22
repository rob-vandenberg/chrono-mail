import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QSystemTrayIcon
from pytestqt.qtbot import QtBot

from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.tray import TrayController
from tests.mailfactory import raw_message
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tray.py 1.9.265"

# --- Version history ----------------------------------------------------
# v1.9.265: the tray choices are read as the program reads them (a setting at its standard is not written since R-12, D-211)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v0.0.85: The window title names the open folder and its unread messages
# v0.0.42: Settings window with pages for everything
# v0.0.21: Initial tray and notification tests


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every notification that would have been shown. The tray of the test machine is pretended to be there."""
    messages: list[tuple[str, str]] = []

    def record(self: QSystemTrayIcon, title: str, text: str, *rest: object) -> None:
        messages.append((title, text))

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", record)

    def visible(self: QSystemTrayIcon) -> bool:
        return True

    monkeypatch.setattr(QSystemTrayIcon, "isVisible", visible)
    return messages


def test_without_a_tray_everything_still_works(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    tray = TrayController()
    assert not tray.available and not tray.visible and not tray.notify("x", "y")
    tray.set_unread(4)
    tray.set_visible(True)  # nothing to show, nothing breaks


def test_the_tray_has_a_menu_and_reports_clicks(qtbot: QtBot, shown: list[tuple[str, str]]) -> None:
    tray = TrayController()
    assert tray.available and tray.notify("Title", "Text") and shown == [("Title", "Text")]
    clicks: list[str] = []
    tray.toggle_requested.connect(lambda: clicks.append("toggle"))
    tray.check_requested.connect(lambda: clicks.append("check"))
    tray.settings_requested.connect(lambda: clicks.append("settings"))
    tray.quit_requested.connect(lambda: clicks.append("quit"))
    for action in (tray.show_action, tray.check_action, tray.settings_action, tray.quit_action):
        action.trigger()
    tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001
    tray._activated(QSystemTrayIcon.ActivationReason.Context)  # noqa: SLF001 - the menu, not a toggle
    assert clicks == ["toggle", "check", "settings", "quit", "toggle"]


def test_the_title_and_tooltip_show_the_unread_count(shown: list[tuple[str, str]], world: World, qtbot: QtBot) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.windowTitle() == "Inbox (3) - Chrono Mail"
    assert world.window.tray._icon is not None and world.window.tray._icon.toolTip() == "Chrono Mail: 3 unread"  # noqa: SLF001


def window_in_use(monkeypatch: pytest.MonkeyPatch, in_use: bool) -> None:
    def active(self: MainWindow) -> bool:
        return in_use

    monkeypatch.setattr(MainWindow, "isActiveWindow", active)


def test_new_mail_gives_a_notification_with_a_number_and_no_content(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window_in_use(monkeypatch, False)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.pop.messages["uid-new-1"] = raw_message(subject="Secret subject", body="Secret body\n", message_id="<n1@x>")
    world.pop.messages["uid-new-2"] = raw_message(subject="Another secret", body="more\n", message_id="<n2@x>")
    wait(qtbot, lambda: not world.window.checking.checking)  # noqa: SLF001
    world.window.checking.check_mail()
    wait(qtbot, lambda: shown != [])
    assert shown == [("New mail", "2 new messages")]
    assert not any("ecret" in text for _, text in shown)


def test_no_notification_while_the_window_is_in_use(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window_in_use(monkeypatch, True)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    wait(qtbot, lambda: not world.window.checking.checking)  # noqa: SLF001
    world.pop.messages["uid-seen"] = raw_message(subject="Seen at once", body="x\n", message_id="<s@x>")
    world.window.checking.check_mail()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    assert shown == []


def test_notifications_can_be_turned_off_and_nothing_new_is_silent(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window_in_use(monkeypatch, False)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.checking.check_mail()  # nothing new
    qtbot.wait(300)
    assert shown == []
    world.window.settings.setValue("notifications", False)
    world.pop.messages["uid-quiet"] = raw_message(subject="Quiet", body="x\n", message_id="<q@x>")
    world.window.checking.check_mail()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    assert shown == []


def test_closing_hides_the_window_only_when_asked_to(shown: list[tuple[str, str]], world: World, qtbot: QtBot) -> None:
    world.window.show()
    world.window.settings.setValue("close_to_tray", True)
    event = QCloseEvent()
    world.window.closeEvent(event)
    assert not event.isAccepted() and not world.window.isVisible()
    world.window.toggle_visibility()
    assert world.window.isVisible()
    world.window.toggle_visibility()
    assert not world.window.isVisible()
    closed: list[bool] = []
    world.window.closed_for_good.connect(lambda: closed.append(True))
    world.window.quit_program()
    assert closed == [True]


def test_without_the_setting_closing_ends_the_program(shown: list[tuple[str, str]], world: World, qtbot: QtBot) -> None:
    world.window.show()
    closed: list[bool] = []
    world.window.closed_for_good.connect(lambda: closed.append(True))
    assert world.window.close() and closed == [True]


def test_the_settings_carry_the_tray_choices(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
        self.notifications.setChecked(False)
        self.close_to_tray.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    world.window.act_settings.trigger()
    chosen = read_preferences(world.window.settings)  # as the program reads them: a setting at its standard is not written (D-211)
    assert (chosen.notifications, chosen.close_to_tray, chosen.tray) == (False, True, True)


def test_the_settings_dialog_explains_a_missing_tray(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, world.window.settings, SettingsContext(tray_available=False))
    qtbot.addWidget(dialog)
    assert not dialog.tray.isEnabled() and not dialog.tray.isChecked() and not dialog.close_to_tray.isChecked() and not dialog.notifications.isEnabled()
    available = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(available)
    available.tray.setChecked(False)
    assert not available.close_to_tray.isChecked() and not available.close_to_tray.isEnabled()
    available.tray.setChecked(True)
    assert available.close_to_tray.isEnabled()
