from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine import Engine, build_registry
from chrono_mail.engine.commands_backup import BACKUP_MAKE, BACKUP_NEW_PASSWORD
from chrono_mail.ui.backupdialog import may_quit
from chrono_mail.ui.recoverydialog import RESTORING_TEXT, RecoveryDialog
from chrono_mail.ui.restoredialog import RestoreDialog
from tests.enginefactory import make_engine
from tests.ui.world import Dialogs, World, answer_input, answer_open_file, build_world, menu_named, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_restore.py 2.4.324"

# --- Version history ----------------------------------------------------
# v2.4.324: Initial: File > Restore… (D-244): the key first on a fresh installation, then the file, then its password (asked again when
#           wrong); the Restore window's report with the test of every account it made; the recovery code window during a restore;
#           Quit while a backup runs asks first

REGISTRY = build_registry()


@pytest.fixture
def blank(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path / "blank", qtbot, dialogs, with_account=False)


def make_backup(engine: Engine, target: Path, protected: bool) -> str:
    password = BACKUP_NEW_PASSWORD.answer(REGISTRY.run(engine, BACKUP_NEW_PASSWORD.id, {})).password if protected else ""
    rows = getattr(REGISTRY.run(engine, "backup.choices", {}), "rows")  # getattr: the choices' rows, by name
    values: dict[str, object] = {"path": str(target), "accounts": tuple(row.entry_id for row in rows), "passwords": protected}
    if protected:
        values["password"] = password
    BACKUP_MAKE.answer(REGISTRY.run(engine, BACKUP_MAKE.id, values))
    return password


def restore_window(world: World, qtbot: QtBot) -> RestoreDialog:
    world.window.act_restore.trigger()
    wait(qtbot, lambda: world.window.restores.window is not None)
    window = world.window.restores.window
    assert window is not None
    return window


def test_the_file_menu_has_restore(world: World) -> None:
    assert world.window.act_restore.isEnabled() and "Restore…" in [action.text() for action in menu_named(world.window, "File").actions()]


def test_a_protected_backup_asks_its_password_again_when_wrong_and_doubles_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    password = make_backup(world.engine, tmp_path / "mine.cmz", protected=True)
    answer_open_file(monkeypatch, tmp_path / "mine.cmz")
    answer_input(monkeypatch, [("WRONGPASSWORD", True), (password, True)])
    window = restore_window(world, qtbot)
    assert any(kind == "warning" and "does not open" in text for kind, _, text in world.dialogs.shown)
    assert "already here" in window.summary.text() and "Messages: 3" in window.summary.text() and "passwords are in it" in window.summary.text()
    assert window.remove_first.isEnabled() and window.replace_existing.isEnabled() and not window.remove_first.isChecked()
    window.remove_first.setChecked(True)
    assert not window.replace_existing.isEnabled()  # nothing is left to replace when everything is removed first
    window.remove_first.setChecked(False)
    window.restore_button.click()
    wait(qtbot, lambda: not window.running and "restored" in window.status.text())
    assert "3 were here already" in window.status.text() and not window.restore_button.isEnabled() and window.close_button.text() == "Close"


def test_on_a_fresh_installation_the_key_comes_first_then_the_accounts_are_tested(blank: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    other = make_engine(tmp_path / "other")
    REGISTRY.run(other, "account.add", {
        "display_name": "Anna", "email_address": "anna@example.com", "username": "user", "password": "secret", "incoming_host": "127.0.0.1",
        "incoming_port": blank.pop.port, "incoming_security": "none", "outgoing_host": "127.0.0.1", "outgoing_port": blank.smtp.port, "outgoing_security": "none",
    })
    password = make_backup(other, tmp_path / "other.cmz", protected=True)
    shown: list[bool] = []

    def recovery_shown(self: RecoveryDialog) -> int:
        shown.append(self._restoring)  # noqa: SLF001 - the window was made for a restore
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(RecoveryDialog, "exec", recovery_shown)
    answer_open_file(monkeypatch, tmp_path / "other.cmz")
    answer_input(monkeypatch, [(password, True)])
    assert blank.engine.config().key_id is None
    window = restore_window(blank, qtbot)
    assert shown == [True] and blank.engine.config().key_id is not None  # the key and its recovery code before the backup
    window.restore_button.click()
    wait(qtbot, lambda: window.tests_done() and "Accounts:" in window.status.text())
    assert "anna@example.com: Connected" in window.status.text()
    assert [settings.account.email_address for settings in blank.engine.config().accounts] == ["anna@example.com"]


def test_a_refused_recovery_code_window_stops_the_restore(blank: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(self: RecoveryDialog) -> int:
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(RecoveryDialog, "exec", refused)
    opened: list[bool] = []

    def get_open_file_name(*args: object, **kwargs: object) -> tuple[str, str]:
        opened.append(True)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(get_open_file_name))
    blank.window.act_restore.trigger()
    wait(qtbot, lambda: blank.engine.config().key_id is not None)
    qtbot.wait(200)
    assert opened == [] and blank.window.restores.window is None


def test_the_recovery_code_window_of_a_restore_says_what_comes_next_and_offers_no_file(world: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(world.backend, restoring=True)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowTitle() == "Your recovery code" and not dialog.file_button.isVisible()
    assert RESTORING_TEXT in [label.text() for label in dialog.findChildren(QLabel)]
    world.dialogs.answer = QMessageBox.StandardButton.No
    dialog.reject()  # without the tick: asked first; No keeps the window
    assert dialog.isVisible() and any("Stop the restore" in text for _, _, text in world.dialogs.shown)


def test_quit_while_a_backup_runs_asks_first(world: World) -> None:
    world.dialogs.answer = QMessageBox.StandardButton.No
    assert may_quit(world.window, False, False)
    assert not may_quit(world.window, True, False) and any("A backup is being made" in text for _, _, text in world.dialogs.shown)
