from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.crypto import DataKey, InMemoryKeyStore
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.restorekeydialog import RestoreKeyDialog
from tests.ui.world import World, answer_input, answer_open_file, reject_dialog, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_key_recovery.py 0.0.15"

# --- Version history ----------------------------------------------------
# v0.0.15: Initial key recovery tests

# Task 1.12: the key recovery procedure, end to end, through the real window. Losing the key means losing
# all the mail (AGENTS.md section 4.1.5), so every way of getting it back, and every way it can go wrong,
# is tested here.


def lose_the_key(world: World) -> DataKey:
    """The credential store loses the key (a new computer, a reset wallet) while the program is not running."""
    key = world.engine.data_key(world.engine.config())
    assert isinstance(world.engine.keys, InMemoryKeyStore)
    world.engine.keys.delete(key.key_id)
    world.engine.forget_keys()
    return key


def drive(monkeypatch: pytest.MonkeyPatch, qtbot: QtBot, steps: Callable[[RestoreKeyDialog], None]) -> list[RestoreKeyDialog]:
    """Replace the modal dialog by a script that works on it; returns the dialogs that were shown."""
    shown: list[RestoreKeyDialog] = []

    def fake_exec(self: RestoreKeyDialog) -> int:
        shown.append(self)
        outcome: list[bool] = []
        self.accepted.connect(lambda: outcome.append(True))
        self.rejected.connect(lambda: outcome.append(False))
        steps(self)
        qtbot.waitUntil(lambda: bool(outcome), timeout=8000)
        return int(QDialog.DialogCode.Accepted if outcome[0] else QDialog.DialogCode.Rejected)

    monkeypatch.setattr(RestoreKeyDialog, "exec", fake_exec)
    return shown


def type_code(dialog: RestoreKeyDialog, code: str) -> None:
    dialog.code.setPlainText(code)
    dialog.restore_code_button.click()


def mail_is_readable(qtbot: QtBot, world: World) -> None:
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.window.list_model.row_at(0) is not None)
    assert [row.subject for row in world.call("message.list", account=world.account_id).messages] != []


def test_no_dialog_when_the_key_is_there(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = drive(monkeypatch, qtbot, lambda dialog: dialog.reject())
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert shown == []


def test_a_lost_key_is_restored_with_the_recovery_code(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    code = world.call("key.recovery-code").code
    lose_the_key(world)
    shown = drive(monkeypatch, qtbot, lambda dialog: type_code(dialog, code))
    world.window.start()
    mail_is_readable(qtbot, world)
    assert len(shown) == 1 and world.call("key.status").in_credential_store is True
    wait(qtbot, lambda: "key is back" in world.window.status_label.text() or "Mail checked" in world.window.status_label.text() or world.window.list_model.rowCount() == 3)


def test_a_lost_key_is_restored_with_the_recovery_file(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = tmp_path / "backup.key"
    world.call("key.export-file", path=str(backup), passphrase="my passphrase")
    lose_the_key(world)

    def use_file(dialog: RestoreKeyDialog) -> None:
        answer_open_file(monkeypatch, backup)
        answer_input(monkeypatch, [("my passphrase", True)])
        dialog.file_button.click()

    drive(monkeypatch, qtbot, use_file)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_a_wrong_code_is_explained_and_the_right_one_then_works(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    code = world.call("key.recovery-code").code
    lose_the_key(world)
    messages: list[str] = []

    def steps(dialog: RestoreKeyDialog) -> None:
        dialog.restore_code_button.click()  # nothing typed yet
        assert "Type or paste" in dialog.result_label.text()
        type_code(dialog, "AAAA-BBBB-CCCC")
        qtbot.waitUntil(lambda: "recovery code" in dialog.result_label.text().lower(), timeout=5000)
        messages.append(dialog.result_label.text())
        assert dialog.restore_code_button.isEnabled()
        mistyped = code[:-1] + ("A" if code[-1] != "A" else "B")
        type_code(dialog, mistyped)
        qtbot.waitUntil(lambda: "typing errors" in dialog.result_label.text(), timeout=5000)
        messages.append(dialog.result_label.text())
        type_code(dialog, code.lower())  # case does not matter
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)
    assert len(messages) == 2 and all(code not in text for text in messages)


def test_a_wrong_passphrase_is_explained(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = tmp_path / "backup.key"
    world.call("key.export-file", path=str(backup), passphrase="right one")
    code = world.call("key.recovery-code").code
    lose_the_key(world)

    def steps(dialog: RestoreKeyDialog) -> None:
        answer_open_file(monkeypatch, backup)
        answer_input(monkeypatch, [("wrong one", True)])
        dialog.file_button.click()
        qtbot.waitUntil(lambda: "passphrase" in dialog.result_label.text().lower(), timeout=8000)
        assert "wrong one" not in dialog.result_label.text()
        type_code(dialog, code)
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_the_key_of_another_installation_is_refused(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.enginefactory import make_engine
    from tests.fakepop3 import FakePop3Server
    from tests.fakesmtp import FakeSmtpServer
    from tests.ui.world import REGISTRY

    other = make_engine(tmp_path / "other")
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        REGISTRY.run(other, "account.add", dict(display_name="O", email_address="o@example.com", username="u", password="p", incoming_host="127.0.0.1", incoming_port=pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none"))
    foreign = str(getattr(REGISTRY.run(other, "key.recovery-code", {}), "code"))
    code = world.call("key.recovery-code").code
    lose_the_key(world)

    def steps(dialog: RestoreKeyDialog) -> None:
        type_code(dialog, foreign)
        qtbot.waitUntil(lambda: "not the key" in dialog.result_label.text(), timeout=5000)
        type_code(dialog, code)
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_a_locked_wallet_that_is_unlocked_later_needs_no_code(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    key = lose_the_key(world)

    def steps(dialog: RestoreKeyDialog) -> None:
        dialog.retry_button.click()
        qtbot.waitUntil(lambda: "still not available" in dialog.result_label.text(), timeout=5000)
        assert isinstance(world.engine.keys, InMemoryKeyStore)
        world.engine.keys.save(key)  # the person unlocks the wallet
        dialog.retry_button.click()
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_not_now_leaves_the_program_usable_and_says_what_is_wrong(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    code = world.call("key.recovery-code").code
    lose_the_key(world)
    drive(monkeypatch, qtbot, lambda dialog: dialog.cancel_button.click())
    world.window.start()
    wait(qtbot, lambda: "cannot be read yet" in world.window.status_label.text())
    assert "c0392b" in world.window.status_label.styleSheet() and world.window.list_model.rowCount() == 0
    drive(monkeypatch, qtbot, lambda dialog: type_code(dialog, code))  # later, from the menu
    world.window.act_restore_key.trigger()
    mail_is_readable(qtbot, world)


def test_nothing_is_asked_when_no_account_uses_a_key(tmp_path: Path, qtbot: QtBot, dialogs: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.ui.world import Dialogs, build_world

    assert isinstance(dialogs, Dialogs)
    for world in build_world(tmp_path, qtbot, dialogs, with_account=False):
        shown = drive(monkeypatch, qtbot, lambda dialog: dialog.reject())
        reject_dialog(monkeypatch, AccountDialog)
        world.window.start()
        qtbot.wait(500)
        assert shown == []


def test_an_unencrypted_account_never_asks_for_a_key(tmp_path: Path, qtbot: QtBot, dialogs: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.ui.world import REGISTRY, Dialogs, build_world

    assert isinstance(dialogs, Dialogs)
    for world in build_world(tmp_path, qtbot, dialogs, with_account=False):
        REGISTRY.run(world.engine, "account.add", dict(display_name="P", email_address="p@example.com", username="u", password="p", encrypted=False, incoming_host="127.0.0.1", incoming_port=world.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none"))
        shown = drive(monkeypatch, qtbot, lambda dialog: dialog.reject())
        world.window.start()
        qtbot.wait(500)
        assert shown == [] and world.window.tree_model.rowCount() == 1


def test_the_menu_has_the_action(world: World) -> None:
    assert world.window.act_restore_key.text() == "Restore the encryption key…"
    file_menu = world.window.menuBar().findChildren(QMenu)[0]
    assert world.window.act_restore_key in file_menu.actions()
