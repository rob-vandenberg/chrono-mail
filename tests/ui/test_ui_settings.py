import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.settingsdialog import GeneralSettings, SettingsDialog
from tests.ui.world import Dialogs, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_settings.py 0.0.31"

# --- Version history ----------------------------------------------------
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Initial settings window tests


def open_settings(world: World, qtbot: QtBot, general: GeneralSettings | None = None) -> SettingsDialog:
    dialog = SettingsDialog(world.backend, general or GeneralSettings())
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.accounts.count() == 1)
    return dialog


def test_the_settings_list_the_accounts_and_return_the_general_choices(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot, GeneralSettings(False, 12, True))
    assert "me@example.com" in dialog.accounts.item(0).text()
    assert dialog.accounts.currentRow() == 0 and dialog.edit_button.isEnabled() and dialog.remove_button.isEnabled()
    assert not dialog.auto_check.isChecked() and not dialog.interval.isEnabled() and dialog.interval.value() == 12 and dialog.conversations.isChecked()
    dialog.auto_check.setChecked(True)
    dialog.interval.setValue(30)
    assert dialog.interval.isEnabled() and dialog.general() == GeneralSettings(True, 30, True)


def test_changing_an_account_sends_only_the_differences(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    account = dialog.accounts.item(0).data(256)
    edit = AccountEditDialog(world.backend, account)
    qtbot.addWidget(edit)
    edit.show()
    assert edit._values() == {}  # noqa: SLF001 - nothing changed yet
    edit.signature.setPlainText("Rob\nChrono Mail")
    edit.name.setText("Rob V")
    assert edit._values() == {"display_name": "Rob V", "signature": "Rob\nChrono Mail"}  # noqa: SLF001
    edit.password.setText("new-secret")
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    after = world.call("account.list")[0]
    assert after.display_name == "Rob V" and after.signature == "Rob\nChrono Mail"
    assert world.engine.passwords.get(world.account_id, ServerRole.INCOMING) == "new-secret"


def test_a_refused_change_stays_open_and_says_why(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    edit.email.setText("not-an-address")
    edit.save_button.click()
    wait(qtbot, lambda: "could not be changed" in edit.result_label.text())
    assert edit.isVisible() and edit.save_button.isEnabled() and world.call("account.list")[0].email_address == "me@example.com"


def test_the_signature_length_is_limited_in_the_dialog(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    edit.signature.setPlainText("x" * 4001)
    assert not edit.save_button.isEnabled() and "4001 of 4000" in edit.signature_count.text()
    edit.signature.setPlainText("x" * 4000)
    assert edit.save_button.isEnabled()


def test_the_policy_days_only_matter_for_delete_after_days(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    assert not edit.days.isEnabled()
    edit.policy.setCurrentIndex(edit.policy.findData("delete_after_days"))
    edit.days.setValue(9)
    assert edit.days.isEnabled() and edit._values() == {"delete_policy": "delete_after_days", "delete_days": 9}  # noqa: SLF001


def test_testing_a_connection_needs_the_password_typed(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    edit.test_button.click()
    assert "Type the password" in edit.result_label.text()
    edit.password.setText("secret")
    edit.test_button.click()
    wait(qtbot, lambda: "Incoming" in edit.result_label.text() and "Outgoing" in edit.result_label.text())


def test_removing_an_account_asks_first_and_keeps_the_mail(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    changed: list[bool] = []
    dialog.accounts_changed.connect(lambda: changed.append(True))
    world.dialogs.answer = QMessageBox.StandardButton.No
    dialog.remove_button.click()
    qtbot.wait(100)
    assert dialog.accounts.count() == 1 and not changed
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    dialog.remove_button.click()
    wait(qtbot, lambda: dialog.accounts.count() == 0 and changed == [True])
    assert not dialog.edit_button.isEnabled() and not dialog.remove_button.isEnabled()
    assert world.call("account.list") == ()
    assert "not deleted" in next(text for kind, title, text in world.dialogs.shown if title == "Remove account")


def test_the_window_applies_the_general_settings_and_refreshes_after_account_changes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    seen: list[str] = []

    def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
        world.call("account.edit", account=world.account_id, display_name="Changed in settings")
        self.accounts_changed.emit()
        self.interval.setValue(17)
        self.auto_check.setChecked(True)
        self.conversations.setChecked(True)
        seen.append("run")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    world.window.act_settings.trigger()
    assert seen == ["run"]
    assert world.window._auto_minutes() == 17 and world.window._timer.interval() == 17 * 60 * 1000  # noqa: SLF001
    assert world.window.act_conversations.isChecked()
    wait(qtbot, lambda: getattr(world.window._accounts[0], "display_name") == "Changed in settings")  # noqa: SLF001


def test_adding_an_account_from_the_settings_runs_the_normal_dialog(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    asked: list[str] = []

    def fake_add_exec(self: AccountDialog) -> QDialog.DialogCode:
        asked.append("add")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AccountDialog, "exec", fake_add_exec)
    dialog = SettingsDialog(world.backend, GeneralSettings(), True, world.window)
    qtbot.addWidget(dialog)
    dialog.add_account_requested.connect(lambda: world.window.add_account(after=dialog.reload))
    dialog.add_button.click()
    assert asked == ["add"]


def test_the_account_type_is_shown_and_imap_accounts_have_no_server_copies_tab(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    assert dialog.accounts.item(0).text().endswith("(POP3)")
    pop3 = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(pop3)
    assert [pop3.tabs.tabText(index) for index in range(pop3.tabs.count())] == ["General", "Servers", "Server copies"]
    imap_account = world.call("account.list")[0]
    from dataclasses import replace

    imap = AccountEditDialog(world.backend, replace(imap_account, kind="imap"))
    qtbot.addWidget(imap)
    assert [imap.tabs.tabText(index) for index in range(imap.tabs.count())] == ["General", "Servers"]
