from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.crypto import DataKey, InMemoryKeyStore, KeyringKeyStore
from tests.doubles import BrokenBackend
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.colours import red
from chrono_mail.ui.restorekeydialog import RestoreKeyDialog
from tests.ui.world import World, answer_input, answer_open_file, menu_named, reject_dialog, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_key_recovery.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: kept Local Folders whose key is gone: Start without this mail, greyed when it does not apply; with its older key here,
#           Start without says that Try again can still bring the mail back
# v2.4.322: the key items are "Show the recovery code…" and "Use a recovery code…" (D-244)
# v1.9.266: R-45: Not now looks for the red of the theme in use, not the light theme's red
# v1.1.197: The dialog says why the key could not be had and tells a wallet that could not be opened apart; Try again shows the newest reason
# v0.0.73: The menus are inside one Menu button
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
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
    assert red() in world.window.status_label.styleSheet() and world.window.list_model.rowCount() == 0  # the red of the theme in use
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
        assert shown == [] and world.window.tree_model.rowCount() == 2  # Favorites and the account


def test_the_menu_has_the_action(world: World) -> None:
    assert world.window.act_restore_key.text() == "Use a recovery code…" and world.window.act_recovery.text() == "Show the recovery code…"
    file_menu = menu_named(world.window, "File")
    assert world.window.act_restore_key in file_menu.actions()


def test_a_wallet_that_could_not_be_opened_is_named_with_its_reason(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = world.engine.keys
    world.engine.keys = KeyringKeyStore("chrono-mail-test", BrokenBackend())  # locked, or not started yet

    def steps(dialog: RestoreKeyDialog) -> None:
        assert dialog.windowTitle() == "The wallet could not be opened"
        assert "KeyringLocked: locked" in dialog.explanation.text() and "unlock it" in dialog.explanation.text()
        world.engine.keys = keys  # the person unlocks the wallet
        dialog.retry_button.click()
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_try_again_shows_the_newest_reason(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    key = lose_the_key(world)
    keys = world.engine.keys

    def steps(dialog: RestoreKeyDialog) -> None:
        assert dialog.windowTitle() == "The encryption key is missing" and "was not found" in dialog.explanation.text()
        world.engine.keys = KeyringKeyStore("chrono-mail-test", BrokenBackend())
        dialog.retry_button.click()
        qtbot.waitUntil(lambda: dialog.windowTitle() == "The wallet could not be opened", timeout=5000)
        assert "KeyringLocked: locked" in dialog.explanation.text()
        assert isinstance(keys, InMemoryKeyStore)
        keys.save(key)
        world.engine.keys = keys
        dialog.retry_button.click()
    drive(monkeypatch, qtbot, steps)
    world.window.start()
    mail_is_readable(qtbot, world)


def test_start_without_this_mail_sets_kept_local_folders_aside(world: World, qtbot: QtBot) -> None:
    """Kept Local Folders whose settings were lost and whose key is gone for good: the key window offers Start without this mail (asked
    first), which sets that mail aside, never deleted; for any other missing key the button is greyed, never hidden."""
    from chrono_mail.config import AppConfig
    from chrono_mail.core import LOCAL_ACCOUNT_ID
    from chrono_mail.engine.commands_accounts import KeyStatus

    plain = RestoreKeyDialog(world.backend, None, KeyStatus("ab" * 16, False, "not there"))
    qtbot.addWidget(plain)
    assert not plain.start_without_button.isEnabled() and not plain.start_without_button.isHidden()
    world.call("local.enable")
    key_id = world.engine.config().key_id
    assert key_id is not None
    world.engine.change_config(lambda config: AppConfig())
    assert isinstance(world.engine.keys, InMemoryKeyStore)
    world.engine.keys.delete(bytes.fromhex(key_id))
    world.engine.forget_keys()
    status = world.call("key.status")
    dialog = RestoreKeyDialog(world.backend, None, status)
    qtbot.addWidget(dialog)
    assert dialog.start_without_button.isEnabled() and "Start without this mail" in dialog.explanation.text()
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.start_without_button.click()  # the question is answered Yes
    assert any(title == "Start without this mail" for _, title, _ in world.dialogs.shown)
    assert dialog.started_without and Path(dialog.started_without).exists()
    assert not world.engine.directories.account_directory(LOCAL_ACCOUNT_ID).exists()


def test_a_missing_key_is_never_hidden_behind_local_folders_older_key(world: World, qtbot: QtBot) -> None:
    """The review of 3.6.350: with this computer's key missing and Local Folders written with an older key, the window said the other mail
    was not affected and offered Start without this mail; and for a locked wallet it hid the locked text."""
    from chrono_mail.engine.commands_accounts import KeyStatus
    from chrono_mail.ui.restorekeydialog import LOCKED_TITLE, MISSING_TITLE, OTHER_KEY_TITLE, OTHER_STILL_MISSING, STILL_MISSING

    for status, title, offered, declined in (
        (KeyStatus("ab" * 16, False, "not there", kept_other=True), MISSING_TITLE, False, STILL_MISSING),
        (KeyStatus("ab" * 16, False, "locked", store_answered=False, kept_other=True), LOCKED_TITLE, False, STILL_MISSING),
        (KeyStatus("ab" * 16, True, kept_other=True), OTHER_KEY_TITLE, True, OTHER_STILL_MISSING),
    ):
        dialog = RestoreKeyDialog(world.backend, None, status)
        qtbot.addWidget(dialog)
        assert (dialog.windowTitle(), dialog.start_without_button.isEnabled(), dialog.declined) == (title, offered, declined)


def _declined(self: RestoreKeyDialog) -> int:
    return int(QDialog.DialogCode.Rejected)


def test_only_local_folders_waiting_never_stops_the_start(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350: Not now in the key window stopped the whole start (no accounts, no mail check) also when only Local Folders
    waited for its older key; and the window could be closed while Local Folders was being taken back."""
    from chrono_mail.engine.commands_accounts import KeyStatus
    from chrono_mail.ui.restorekeydialog import NOT_TAKEN_BACK, OTHER_STILL_MISSING

    for status, declined in ((KeyStatus("ab" * 16, True, kept_other=True), OTHER_STILL_MISSING), (KeyStatus("ab" * 16, True, take_back=True), NOT_TAKEN_BACK)):
        monkeypatch.setattr(RestoreKeyDialog, "exec", _declined)
        went_on: list[bool] = []
        world.window._ask_for_key(lambda: went_on.append(True), status)  # noqa: SLF001 - the start's question
        assert went_on == [True] and declined in world.window.status_label.text()
    went_on.clear()
    world.window._ask_for_key(lambda: went_on.append(True), KeyStatus("ab" * 16, False, "not there"))  # noqa: SLF001
    assert went_on == []  # this computer's own key missing: nothing can be opened
    dialog = RestoreKeyDialog(world.backend, None, KeyStatus("ab" * 16, False, "not there"))
    qtbot.addWidget(dialog)
    dialog._busy(True)  # noqa: SLF001 - while work runs
    closed: list[int] = []
    dialog.finished.connect(closed.append)
    dialog.reject()
    assert closed == [] and not dialog.cancel_button.isEnabled()


def test_not_now_with_the_key_in_place_leaves_the_status_line_alone(world: World, qtbot: QtBot) -> None:
    """The review of 3.6.350: File > Use a recovery code with the key in place, then Not now, emptied the status line in red."""
    world.window.report.show("Mail checked")
    dialog = RestoreKeyDialog(world.backend, None, None)
    qtbot.addWidget(dialog)
    dialog.tell(world.window.report, accepted=False)
    assert world.window.status_label.text() == "Mail checked"


def test_start_without_says_try_again_can_bring_the_mail_back_when_its_older_key_is_here(world: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350: with Local Folders' older key in the password store and only its take-back failed (no room on the
    disk), Start without asked as if the mail could only be opened with a recovery code, and the person gave it up believing it lost."""
    from PySide6.QtWidgets import QMessageBox

    from chrono_mail.engine.commands_accounts import KeyStatus
    from chrono_mail.ui.restorekeydialog import START_WITHOUT_KEY_HERE_QUESTION, START_WITHOUT_QUESTION

    dialog = RestoreKeyDialog(world.backend, None, KeyStatus("ab" * 16, False, "not there"))
    qtbot.addWidget(dialog)
    dialog.show_status(KeyStatus("ab" * 16, True, take_back=True))  # the older key is here; the take-back that ran failed
    world.dialogs.answer = QMessageBox.StandardButton.No
    dialog.start_without_button.click()
    assert [text for _, title, text in world.dialogs.shown if title == "Start without this mail"] == [START_WITHOUT_KEY_HERE_QUESTION]
    assert START_WITHOUT_KEY_HERE_QUESTION != START_WITHOUT_QUESTION and dialog.started_without == ""
