from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountdialog import INCOMING_PORTS, OUTGOING_PORTS, AccountDialog
from chrono_mail.ui.recoverydialog import RecoveryDialog
from tests.ui.world import Dialogs, World, answer_input, answer_save_file, build_world, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_dialogs.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial dialog tests


@pytest.fixture
def blank(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, with_account=False)


def fill(dialog: AccountDialog, world: World, password: str = "secret") -> None:
    dialog.email.setText("new@example.com")
    dialog.name.setText("New Person")
    dialog.username.setText("user")
    dialog.password.setText(password)
    dialog.incoming_host.setText("127.0.0.1")
    dialog.outgoing_host.setText("127.0.0.1")
    dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData("none"))
    dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData("none"))
    dialog.incoming_port.setValue(world.pop.port)
    dialog.outgoing_port.setValue(world.smtp.port)


def test_defaults_and_the_first_account_welcome(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    assert dialog.incoming_security.currentData() == "tls" and dialog.incoming_port.value() == 995
    assert dialog.outgoing_security.currentData() == "starttls" and dialog.outgoing_port.value() == 587
    assert dialog.encrypt.isChecked() and dialog.policy.currentData() == "keep"
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password


def test_the_port_follows_the_security_choice(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    for security in ("tls", "starttls", "none"):
        dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData(security))
        dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData(security))
        assert dialog.incoming_port.value() == INCOMING_PORTS[security] and dialog.outgoing_port.value() == OUTGOING_PORTS[security]


def test_the_address_suggests_the_user_name_and_the_servers(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    dialog.email.textEdited.emit("rob@example.org")
    assert dialog.username.text() == "rob@example.org" and dialog.incoming_host.text() == "mail.example.org" and dialog.outgoing_host.text() == "mail.example.org"
    dialog.incoming_host.setText("pop.provider.net")  # the person types their own
    dialog.email.textEdited.emit("rob@example.net")
    assert dialog.incoming_host.text() == "pop.provider.net" and dialog.outgoing_host.text() == "mail.example.net"


def test_missing_fields_are_pointed_out_and_nothing_happens(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    dialog.add_button.click()
    dialog.test_button.click()
    assert "Please fill in" in dialog.result_label.text()
    qtbot.wait(200)
    assert blank.call("account.list") == () and dialog.created_account_id is None


def test_the_connection_test_reports_both_servers(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.test_button.click()
    wait(qtbot, lambda: "✓ Incoming" in dialog.result_label.text() and "✓ Outgoing" in dialog.result_label.text())
    assert dialog.add_button.isEnabled() and blank.call("account.list") == ()  # a test creates nothing


def test_a_wrong_password_shows_a_cross_without_the_password(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank, password="not-the-password-123")
    dialog.test_button.click()
    wait(qtbot, lambda: "✗ Incoming" in dialog.result_label.text() and "✗ Outgoing" in dialog.result_label.text())
    assert "not-the-password-123" not in dialog.result_label.text()


def test_an_unreachable_server_is_reported(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.incoming_port.setValue(1)
    dialog.test_button.click()
    wait(qtbot, lambda: "✗ Incoming" in dialog.result_label.text() and "✓ Outgoing" in dialog.result_label.text())


def test_adding_the_account(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.policy.setCurrentIndex(dialog.policy.findData("delete_after_download"))
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    accounts = blank.call("account.list")
    assert len(accounts) == 1 and accounts[0].email_address == "new@example.com" and accounts[0].delete_policy == "delete_after_download"
    assert dialog.created_account_id == accounts[0].account_id and dialog.key_created is True and accounts[0].encrypted


def test_without_encryption_no_key_is_created(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.encrypt.setChecked(False)
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    assert dialog.key_created is False and blank.call("key.status").key_id is None


def test_a_second_account_with_the_same_address_is_refused_in_the_dialog(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    fill(dialog, world)
    dialog.email.setText("me@example.com")
    dialog.add_button.click()
    wait(qtbot, lambda: "could not be added" in dialog.result_label.text())
    assert "already exists" in dialog.result_label.text() and dialog.add_button.isEnabled()
    assert dialog.created_account_id is None and len(world.call("account.list")) == 1


# --- the recovery dialog ------------------------------------------------------------------------------------------------------------


def test_the_recovery_code_is_shown_and_the_dialog_cannot_be_closed_lightly(world: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    expected = world.call("key.recovery-code").code
    wait(qtbot, lambda: dialog.code.toPlainText() == expected)
    assert not dialog.done_button.isEnabled()
    dialog.confirm.setChecked(True)
    assert dialog.done_button.isEnabled()
    dialog.confirm.setChecked(False)
    assert not dialog.done_button.isEnabled()


def test_the_code_can_be_copied(world: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    expected = world.call("key.recovery-code").code
    wait(qtbot, lambda: dialog.code.toPlainText() == expected)
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == expected


def test_a_recovery_file_can_be_saved_and_restores_the_key(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("Loading…", ""))
    target = tmp_path / "backup.key"
    answer_save_file(monkeypatch, target)
    answer_input(monkeypatch, [("a long passphrase", True)])
    dialog.file_button.click()
    wait(qtbot, lambda: target.exists())
    wait(qtbot, lambda: any(title == "Recovery file" and "was saved" in text for _, title, text in world.dialogs.shown))
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert world.call("key.restore-file", path=str(target), passphrase="a long passphrase").in_credential_store is True


def test_different_passphrases_or_cancelling_save_nothing(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("Loading…", ""))
    target = tmp_path / "never.key"
    answer_save_file(monkeypatch, target)
    answer_input(monkeypatch, [("first", True), ("second", True)])
    dialog.file_button.click()
    assert any("not the same" in text for _, _, text in world.dialogs.shown) and not target.exists()
    answer_input(monkeypatch, [("", True)])
    dialog.file_button.click()
    answer_input(monkeypatch, [("x", False)])
    dialog.file_button.click()
    answer_save_file(monkeypatch, Path(""))
    dialog.file_button.click()
    qtbot.wait(200)
    assert not target.exists() and [p for p in tmp_path.iterdir() if p.suffix == ".key"] == []


def test_without_a_key_the_dialog_says_so(blank: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(blank.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() == "(the code could not be shown)")
    assert any(kind == "warning" for kind, _, _ in blank.dialogs.shown)
