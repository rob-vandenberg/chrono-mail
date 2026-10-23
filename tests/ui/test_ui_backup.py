import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from chrono_mail.engine.backup import MANIFEST_ENTRY, PASSWORD_LENGTH
from chrono_mail.ui.backupdialog import BackupDialog
from chrono_mail.ui.statusreport import CLICK_BACKUP
from tests.ui.world import World, answer_save_file, menu_named, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_backup.py 2.4.322"

# --- Version history ----------------------------------------------------
# v2.4.322: Initial: the Back up window (D-244): greyed, never hidden; the passwords force the protection; Copy; a whole backup;
#           Run in background and the busy bar bringing the window back


def open_backup(world: World, qtbot: QtBot) -> BackupDialog:
    world.call("pim.enable")  # Local Folders and this computer's calendar, task list and address book
    world.window.act_backup.trigger()
    window = world.window.backups.window
    assert window is not None
    wait(qtbot, lambda: window.accounts.count() > 0 and len(window.password.text()) == PASSWORD_LENGTH)
    return window


def checked(window: BackupDialog) -> list[str]:
    return [window.accounts.item(number).text() for number in range(window.accounts.count()) if window.accounts.item(number).checkState() == Qt.CheckState.Checked]


def test_the_file_menu_has_back_up_and_a_greyed_restore(world: World) -> None:
    texts = [action.text() for action in menu_named(world.window, "File").actions()]
    assert texts[2:5] == ["", "Back up…", "Restore…"] and world.window.act_backup.isEnabled() and not world.window.act_restore.isEnabled()


def test_back_up_waits_for_the_saved_password_and_the_passwords_force_the_protection(world: World, qtbot: QtBot) -> None:
    window = open_backup(world, qtbot)
    assert checked(window) == ["me@example.com", "Local Folders", "On this computer"] and window.select_all.isChecked()
    assert all(box.isChecked() for box in window.part_boxes.values()) and window.protect.isChecked() and not window.include_passwords.isChecked()
    assert not window.back_up_button.isEnabled()  # not before "I have saved the backup password"
    window.saved.setChecked(True)
    assert window.back_up_button.isEnabled()
    window.protect.setChecked(False)
    assert window.back_up_button.isEnabled() and not window.password.isEnabled() and not window.copy_button.isEnabled() and not window.saved.isEnabled()
    window.include_passwords.setChecked(True)
    assert window.protect.isChecked() and not window.protect.isEnabled()  # never passwords without a backup password
    window.include_passwords.setChecked(False)
    assert window.protect.isEnabled()


def test_parts_no_chosen_account_has_are_greyed_and_nothing_chosen_greys_back_up(world: World, qtbot: QtBot) -> None:
    window = open_backup(world, qtbot)
    window.protect.setChecked(False)
    window.select_all.click()
    assert checked(window) == [] and not window.back_up_button.isEnabled()
    assert [part for part, box in window.part_boxes.items() if box.isEnabled()] == ["rules", "settings"]
    window.accounts.item(2).setCheckState(Qt.CheckState.Checked)  # On this computer: calendars, contacts and tasks, no messages
    assert not window.part_boxes["messages"].isEnabled() and window.part_boxes["calendars"].isEnabled() and window.back_up_button.isEnabled()
    assert not window.select_all.isChecked()


def test_copy_puts_the_password_on_the_clipboard_and_says_so(world: World, qtbot: QtBot) -> None:
    window = open_backup(world, qtbot)
    window.copy_button.click()
    assert QGuiApplication.clipboard().text() == window.password.text() and window.copy_button.text() == "Copied"
    wait(qtbot, lambda: window.copy_button.text() == "Copy", timeout=4000)


def test_a_backup_is_written_and_the_window_says_where(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    window = open_backup(world, qtbot)
    window.protect.setChecked(False)
    answer_save_file(monkeypatch, tmp_path / "mine")
    window.back_up_button.click()
    wait(qtbot, lambda: not window.running and "saved as" in window.status.text())
    target = tmp_path / "mine.cmz"
    assert window.close_button.text() == "Close" and "Messages: 3" in window.status.text()
    with zipfile.ZipFile(target) as archive:
        assert MANIFEST_ENTRY in archive.namelist() and "config/window.json" in archive.namelist()  # the window's own settings too
    assert "mine.cmz" in world.window.status_label.text()


def test_run_in_background_and_the_busy_bar_brings_the_window_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    window = open_backup(world, qtbot)
    window.protect.setChecked(False)
    answer_save_file(monkeypatch, tmp_path / "background.cmz")
    window.back_up_button.click()  # its answer comes through the event loop: until then it runs
    assert window.running and window.close_button.text() == "Cancel" and window.background_button.isEnabled() and not window.back_up_button.isEnabled()
    window.background_button.click()
    assert not window.isVisible() and CLICK_BACKUP in world.window.report.bar_text()
    world.window.report.bar_clicked.emit()
    assert window.isVisible()
    wait(qtbot, lambda: not window.running)
    assert (tmp_path / "background.cmz").exists() and CLICK_BACKUP not in world.window.report.bar_text()
