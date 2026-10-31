from typing import override

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QSystemTrayIcon, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import mainwindow
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.tray import TrayController
from tests.mailfactory import raw_message
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tray.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: the automatic check is check_accounts(None) (check_mail is gone, D-300)
# v4.2.405: Wayland (simulated): a tray click brings back a window without the focus and hides one with it (or with a dialog that has
#           it); a second start shows the window again, never under its dialog nor while it has the focus (D-286)
# v4.2.404: a maximized window closed to the tray comes back maximized from the tray icon (the owner's report from Windows)
# v4.2.402: a window minimized while maximized is opened maximized again, from the tray and by a second start; a recovery code or
#           the first account asked at a minimized start opens the window first; a window started out of sight (Wayland says nothing
#           about minimized) is opened by the tray, never hidden (D-284)
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
    world.window.checking.check_accounts(None)
    wait(qtbot, lambda: shown != [])
    assert shown == [("New mail", "2 new messages")]
    assert not any("ecret" in text for _, text in shown)


def test_no_notification_while_the_window_is_in_use(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window_in_use(monkeypatch, True)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    wait(qtbot, lambda: not world.window.checking.checking)  # noqa: SLF001
    world.pop.messages["uid-seen"] = raw_message(subject="Seen at once", body="x\n", message_id="<s@x>")
    world.window.checking.check_accounts(None)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    assert shown == []


def test_notifications_can_be_turned_off_and_nothing_new_is_silent(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window_in_use(monkeypatch, False)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.checking.check_accounts(None)  # nothing new
    qtbot.wait(300)
    assert shown == []
    world.window.settings.setValue("notifications", False)
    world.pop.messages["uid-quiet"] = raw_message(subject="Quiet", body="x\n", message_id="<q@x>")
    world.window.checking.check_accounts(None)
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


def test_a_minimized_maximized_window_opens_maximized(world: World, qtbot: QtBot) -> None:
    window = world.window
    for opening in (window.toggle_visibility, window.bring_forward):
        window.showMaximized()
        window.setWindowState(window.windowState() | Qt.WindowState.WindowMinimized)
        assert window.isMinimized()
        opening()
        assert not window.isMinimized() and window.isMaximized() and window.isVisible()


def test_a_recovery_code_asked_at_a_minimized_start_opens_the_window_first(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    window.setWindowState(window.windowState() | Qt.WindowState.WindowMinimized)
    minimized_when_asked: list[bool] = []

    def asked(dialog: QDialog) -> int:
        minimized_when_asked.append(window.isMinimized())
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(mainwindow.RestoreKeyDialog, "exec", asked)
    window._ask_for_key(lambda: None)  # noqa: SLF001 - the question the start asks when the key is missing
    assert minimized_when_asked == [False]


def test_a_window_started_minimized_is_opened_by_the_tray_also_when_the_desktop_does_not_say_it_is_minimized(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mainwindow, "on_wayland", lambda: True)
    window = world.window
    window.start_minimized()
    window.show()
    assert window.isMinimized() and window.out_of_sight
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)  # Wayland: the window never hears it is minimized
    window.toggle_visibility()
    assert window.isVisible() and not window.out_of_sight  # opened, not hidden
    given_focus(monkeypatch, window, True)  # the person works in it
    window.toggle_visibility()
    assert not window.isVisible()  # opened and in use, the next click hides it as always (D-286)


def test_the_first_account_asked_at_a_minimized_start_opens_the_window_first(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start_minimized()
    window.show()
    minimized_when_asked: list[bool] = []

    def asked(dialog: QDialog) -> int:
        minimized_when_asked.append(window.isMinimized() or window.out_of_sight)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(mainwindow.AccountDialog, "exec", asked)
    window.add_account(first=True)
    assert minimized_when_asked == [False]


def test_a_maximized_window_closed_to_the_tray_comes_back_maximized(shown: list[tuple[str, str]], world: World, qtbot: QtBot) -> None:
    window = world.window
    window.settings.setValue("close_to_tray", True)
    window.showMaximized()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and not window.isVisible()  # in the notification area
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001 - a click on the tray icon, as the tray reports it
    assert window.isVisible() and window.isMaximized()


def has_the_focus(window: MainWindow) -> bool:
    return True


def has_no_focus(window: MainWindow) -> bool:
    return False


def given_focus(monkeypatch: pytest.MonkeyPatch, window: MainWindow, focus: bool) -> None:
    """Whether the window (and so the program) has the focus, as the desktop would say it."""
    monkeypatch.setattr(MainWindow, "isActiveWindow", has_the_focus if focus else has_no_focus)
    active: QWidget | None = window if focus else None
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: active))


def on_wayland_with_focus(monkeypatch: pytest.MonkeyPatch, window: MainWindow, focus: bool) -> list[str]:
    """Wayland simulated: the window says it is not minimized, as Qt reports it there; whether it has the focus is given. Returns the
    calls to hide and show, in order."""
    monkeypatch.setattr(mainwindow, "on_wayland", lambda: True)
    given_focus(monkeypatch, window, focus)
    calls: list[str] = []
    real_hide, real_show = MainWindow.hide, MainWindow.show

    def hide(self: MainWindow) -> None:
        calls.append("hide")
        real_hide(self)

    def show(self: MainWindow) -> None:
        calls.append("show")
        real_show(self)

    monkeypatch.setattr(MainWindow, "hide", hide)
    monkeypatch.setattr(MainWindow, "show", show)
    return calls


def test_on_wayland_a_tray_click_brings_back_a_window_without_the_focus(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()  # minimized by the person: Wayland still reports it visible and not minimized
    calls = on_wayland_with_focus(monkeypatch, window, focus=False)
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001 - a click on the tray icon
    assert window.isVisible() and calls == ["hide", "show"]  # shown again: the only way back from minimized on Wayland


def test_on_wayland_a_tray_click_hides_the_window_that_has_the_focus(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    on_wayland_with_focus(monkeypatch, window, focus=True)
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001
    assert not window.isVisible()


def test_on_wayland_a_second_start_brings_the_window_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    calls = on_wayland_with_focus(monkeypatch, window, focus=False)
    window.bring_forward()
    assert window.isVisible() and calls == ["hide", "show"]


class DialogWithTheFocus(QDialog):
    """Settings, Import or a question, open and with the focus, as the desktop would say it."""

    @override
    def isActiveWindow(self) -> bool:  # noqa: N802 - Qt's name
        return True


def test_on_wayland_a_dialog_with_the_focus_keeps_its_window_where_it_is(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    dialog = DialogWithTheFocus(window)
    calls = on_wayland_with_focus(monkeypatch, window, focus=False)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: dialog))
    window.bring_forward()
    assert window.isVisible() and "hide" not in calls  # never shown again under its dialog
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001 - the program is in use: the click hides it, as everywhere
    assert not window.isVisible()


def test_on_wayland_a_window_whose_dialog_has_no_focus_is_brought_back(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()  # minimized with Settings open, or behind another program
    dialog = QDialog(window)  # open, without the focus (never shown here: its isActiveWindow is False)
    calls = on_wayland_with_focus(monkeypatch, window, focus=False)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: dialog))
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001
    assert window.isVisible() and calls == ["hide", "show"]  # brought back, never hidden by the click; the dialog itself is never hidden


def test_on_wayland_a_window_with_the_focus_is_only_raised(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    calls = on_wayland_with_focus(monkeypatch, window, focus=True)
    window.bring_forward()
    assert window.isVisible() and "hide" not in calls  # no flicker


def test_on_wayland_a_tray_click_brings_the_window_back_from_behind_another_window_of_the_program(shown: list[tuple[str, str]], world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()  # minimized; the person works in a message window
    calls = on_wayland_with_focus(monkeypatch, window, focus=False)
    window.tray._activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001 - the main window has no focus: another window of the program has it
    assert window.isVisible() and calls == ["hide", "show"]  # brought back, never hidden by the first click

