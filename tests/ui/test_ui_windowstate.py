from pathlib import Path

from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.windowstate import reachable, restore_geometry, save_geometry
from tests.ui.test_ui_reader import shown
from tests.ui.world import World, dialogs, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_windowstate.py 1.9.264"

# --- Version history ----------------------------------------------------
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v0.0.83: Initial tests of the remembered size and position of the windows


def screen() -> QRect:
    return QGuiApplication.primaryScreen().availableGeometry()


def store(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "w.ini"), QSettings.Format.IniFormat)


def test_a_window_is_reachable_when_enough_of_its_top_edge_is_on_a_screen(qtbot: QtBot) -> None:
    area = screen()
    assert reachable(QRect(area.left() + 10, area.top() + 10, 400, 300))
    assert reachable(QRect(area.right() - 150, area.top() + 10, 400, 300))  # 150 pixels of the top edge are on the screen: enough to grab
    assert not reachable(QRect(area.right() - 50, area.top() + 10, 400, 300))  # only 50
    assert not reachable(QRect(area.left() + 10, area.bottom() + 100, 400, 300))  # below the screen
    assert not reachable(QRect(area.left() + 100000, area.top(), 400, 300))  # a screen that is gone
    assert not reachable(QRect(area.left() + 10, area.top() - 400, 400, 300))  # above it


def test_a_saved_position_is_used_when_reachable_and_ignored_when_it_is_not(tmp_path: Path, qtbot: QtBot) -> None:
    settings = store(tmp_path)
    area = screen()
    first = QWidget()
    qtbot.addWidget(first)
    first.setGeometry(area.left() + 20, area.top() + 30, 420, 310)
    first.show()
    save_geometry(first, settings, "k")
    again = QWidget()
    qtbot.addWidget(again)
    again.resize(300, 200)
    assert restore_geometry(again, settings, "k") and (again.width(), again.height()) == (420, 310)
    gone = QWidget()
    qtbot.addWidget(gone)
    gone.setGeometry(area.left() + 100000, area.top() + 30, 420, 310)
    gone.show()
    save_geometry(gone, settings, "away")
    fresh = QWidget()
    qtbot.addWidget(fresh)
    fresh.resize(300, 200)
    restore_geometry(fresh, settings, "away")
    assert reachable(fresh.frameGeometry())  # whatever was saved, the window ends up where the person can reach it
    assert not restore_geometry(fresh, settings, "nothing saved") and not restore_geometry(fresh, None, "k")


def test_a_message_window_remembers_its_size(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    settings = store(tmp_path)

    def make() -> MessageWindow:
        window = MessageWindow(world.window.backend, "a", "INBOX", "m", shown(html="", text="x"), lambda *args: None, lambda url: None, world.window.reading.prepare_message_reader, settings)  # noqa: SLF001
        qtbot.addWidget(window)
        return window

    window = make()
    window.resize(555, 444)
    window.show()
    window.close()
    assert (make().width(), make().height()) == (555, 444)


def test_a_composer_remembers_its_size(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    settings = store(tmp_path)

    def make() -> ComposeWindow:
        window = ComposeWindow(world.window.backend, world.window.mail_state.mail_accounts(), world.account_id, settings=settings)  # noqa: SLF001
        qtbot.addWidget(window)
        return window

    window = make()
    window.resize(601, 477)
    window.show()
    window.close()
    assert (make().width(), make().height()) == (601, 477)


def test_the_settings_window_remembers_its_size_with_ok_and_with_cancel(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    settings = store(tmp_path)

    def make() -> SettingsDialog:
        dialog = SettingsDialog(world.window.backend, settings, SettingsContext(tray_available=False, shortcut_actions=world.window._shortcut_registry()), world.window)  # noqa: SLF001
        qtbot.addWidget(dialog)
        return dialog

    dialog = make()
    dialog.resize(781, 581)
    dialog.show()
    dialog.reject()
    first = make()
    assert abs(first.width() - 781) <= 4 and abs(first.height() - 581) <= 4  # the screen of the test is 800 wide, so the size is within it
    first.show()
    first.reject()
    second = make()
    assert (second.width(), second.height()) == (first.width(), first.height())  # and it does not drift with every opening
    second.resize(790, 600)
    second.show()
    second.done(int(SettingsDialog.DialogCode.Accepted))  # what accept() does once there is no conflict between keys
    third = make()
    assert abs(third.width() - 790) <= 4 and abs(third.height() - 600) <= 4
