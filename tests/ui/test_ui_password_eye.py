import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLineEdit, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.calendaraccount import CalendarAccountDialog
from chrono_mail.ui.passwordfield import HIDE_TEXT, SHOW_TEXT, PasswordBox, make_password_field, run_password_box
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_password_eye.py 4.2.437"

# --- Version history ----------------------------------------------------
# v4.2.437: Initial tests: the eye in every password field (UR-03, D-296)


def eye_of(field: QLineEdit) -> QAction:
    eyes = [action for action in field.actions() if action.objectName() == "passwordEye"]
    assert len(eyes) == 1, "a password field has exactly one eye"
    return eyes[0]


def assert_hidden_with_eye(field: QLineEdit) -> None:
    assert field.echoMode() == QLineEdit.EchoMode.Password
    assert eye_of(field).toolTip() == SHOW_TEXT


def test_the_eye_shows_the_password_and_hides_it_again(qtbot: QtBot) -> None:
    field = QLineEdit()
    qtbot.addWidget(field)
    eye = make_password_field(field)
    field.setText("secret")
    assert_hidden_with_eye(field)
    eye.trigger()
    assert field.echoMode() == QLineEdit.EchoMode.Normal and eye.toolTip() == HIDE_TEXT
    eye.trigger()
    assert field.echoMode() == QLineEdit.EchoMode.Password and eye.toolTip() == SHOW_TEXT
    assert field.text() == "secret"


def test_the_eye_greys_with_its_field(qtbot: QtBot) -> None:
    field = QLineEdit()
    qtbot.addWidget(field)
    make_password_field(field)
    buttons = field.findChildren(QToolButton)
    assert buttons and all(button.isEnabled() for button in buttons)
    field.setEnabled(False)
    assert not any(button.isEnabled() for button in buttons)


def test_add_account_has_the_eye_in_both_passwords(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    assert_hidden_with_eye(dialog.servers.password)
    assert_hidden_with_eye(dialog.servers.outgoing_password)


def test_edit_account_has_the_eye_in_every_password(world: World, qtbot: QtBot) -> None:
    account = world.call("account.list")[0]
    assert isinstance(account, AccountSummary)
    dialog = AccountEditDialog(world.backend, account)
    qtbot.addWidget(dialog)
    assert_hidden_with_eye(dialog.servers.password)
    assert_hidden_with_eye(dialog.servers.outgoing_password)
    assert_hidden_with_eye(dialog.dav_password)


def test_the_calendar_account_window_has_the_eye(world: World, qtbot: QtBot) -> None:
    dialog = CalendarAccountDialog(world.backend, world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    assert_hidden_with_eye(dialog.password)


def test_the_password_box_asks_with_the_eye_hidden_and_answers_what_was_typed(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def typed_and_ok(box: PasswordBox) -> int:
        field = box.findChild(QLineEdit)
        assert isinstance(field, QLineEdit)
        assert_hidden_with_eye(field)
        seen.append(f"{box.windowTitle()}|{box.labelText()}")
        box.setTextValue("my passphrase")
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PasswordBox, "exec", typed_and_ok)
    assert run_password_box(None, "Recovery file", "The passphrase of the recovery file:") == ("my passphrase", True)
    assert seen == ["Recovery file|The passphrase of the recovery file:"]


def test_the_password_box_answers_not_accepted_on_cancel(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def cancelled(box: PasswordBox) -> int:
        box.setTextValue("typed, then cancelled")
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PasswordBox, "exec", cancelled)
    assert run_password_box(None, "Backup password", "The backup password of backup.cmz:") == ("", False)
