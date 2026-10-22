import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QGroupBox, QLabel, QLineEdit, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from tests.ui.world import Dialogs, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_settings.py 1.9.271"

# --- Version history ----------------------------------------------------
# v1.9.271: The account editor's changes are typed (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.2.253: the Settings window hands back the typed settings (chosen(), R-12)
# v1.1.243: Test connection in the editor uses the stored password, never asks for it again; both account windows have the same server boxes (R-05)
# v1.1.213: no help lines; the login per server and Use the same user name and password as for incoming mail
# v1.1.211: Account name and Your name in both account windows; the account name in Settings > Accounts, the folder list and the Refresh menu
# v1.1.174: the accounts button is called Edit…
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.42: Settings window with pages for everything
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Initial settings window tests


def given(parameters: object) -> dict[str, object]:
    """The fields a typed changes object sets (R-19): what the old dictionary of changes held."""
    if parameters is None:
        return {}
    return {name: value for name in parameters.__dataclass_fields__ if name != "account" and (value := getattr(parameters, name)) not in (None, ())}  # type: ignore[attr-defined]


def open_settings(world: World, qtbot: QtBot, **chosen: object) -> SettingsDialog:
    for key, value in chosen.items():
        world.window.settings.setValue(key, value)
    dialog = SettingsDialog(world.backend, world.window.settings)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.accounts.count() == 1)
    return dialog


def test_the_settings_list_the_accounts_and_return_the_general_choices(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot, auto_check=False, auto_check_minutes=12, conversations=True)
    assert "me@example.com" in dialog.accounts.item(0).text()
    assert dialog.accounts.currentRow() == 0 and dialog.edit_button.isEnabled() and dialog.remove_button.isEnabled()
    assert dialog.edit_button.text() == "Edit…"
    assert not dialog.auto_check.isChecked() and not dialog.interval.isEnabled() and dialog.interval.value() == 12 and dialog.conversations.isChecked()
    dialog.auto_check.setChecked(True)
    dialog.interval.setValue(30)
    chosen = dialog.chosen()
    assert dialog.interval.isEnabled() and chosen.auto_check is True and chosen.auto_check_minutes == 30 and chosen.conversations is True


def test_changing_an_account_sends_only_the_differences(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    account = dialog.accounts.item(0).data(256)
    edit = AccountEditDialog(world.backend, account)
    qtbot.addWidget(edit)
    edit.show()
    assert edit._values() is None  # noqa: SLF001 - nothing changed yet
    edit.signature.setPlainText("Rob\nChrono Mail")
    edit.name.setText("Rob V")
    assert given(edit._values()) == {"display_name": "Rob V", "signature": "Rob\nChrono Mail"}  # noqa: SLF001
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
    assert edit.days.isEnabled() and given(edit._values()) == {"delete_policy": "delete_after_days", "delete_days": 9}  # noqa: SLF001


def test_testing_a_connection_uses_the_stored_password_and_never_asks_for_it(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    edit.test_button.click()
    wait(qtbot, lambda: "✓ Incoming" in edit.result_label.text() and "✓ Outgoing" in edit.result_label.text())
    assert "password" not in edit.result_label.text().lower()
    edit.password.setText("wrong-xyz")  # a typed new password is tried instead
    edit.test_button.click()
    wait(qtbot, lambda: "✗ Incoming" in edit.result_label.text())


def server_rows(group: QGroupBox) -> list[str]:
    form = group.layout()
    assert isinstance(form, QFormLayout)
    labels: list[str] = []
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        text = label.widget().text() if label is not None and isinstance(label.widget(), QLabel) else ""
        labels.append(text or (field.widget().text() if field is not None and isinstance(field.widget(), QCheckBox) else ""))
    return labels


def test_both_account_windows_have_the_same_server_boxes(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    add = AccountDialog(world.backend)
    qtbot.addWidget(edit)
    qtbot.addWidget(add)
    for adding, editing in ((add.servers.incoming_group, edit.servers.incoming_group), (add.servers.outgoing_group, edit.servers.outgoing_group)):
        assert [row.replace("New password", "Password") for row in server_rows(editing)] == server_rows(adding)
    assert server_rows(edit.servers.outgoing_group) == ["Server", "Security", "Port", "Use the same user name and password as for incoming mail", "User name", "New password"]
    assert edit.password.placeholderText() == "(unchanged)" and add.password.placeholderText() == ""
    edit.outgoing_port.setValue(2525)  # typed: stays
    edit.outgoing_security.setCurrentIndex(edit.outgoing_security.findData("tls"))
    assert edit.outgoing_port.value() == 2525
    edit.incoming_security.setCurrentIndex(edit.incoming_security.findData("starttls"))
    start = edit.incoming_port.value()
    edit.incoming_port.setValue(110)  # the standard POP3 port of STARTTLS: it follows the choice in the editor too
    edit.incoming_security.setCurrentIndex(edit.incoming_security.findData("tls"))
    assert edit.incoming_port.value() == 995, start


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
    assert world.window.checking.auto_minutes() == 17 and world.window.checking.timer.interval() == 17 * 60 * 1000  # noqa: SLF001
    assert world.window.act_conversations.isChecked()
    wait(qtbot, lambda: getattr(world.window.mail_state.accounts[0], "display_name") == "Changed in settings")  # noqa: SLF001


def test_adding_an_account_from_the_settings_runs_the_normal_dialog(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    asked: list[str] = []

    def fake_add_exec(self: AccountDialog) -> QDialog.DialogCode:
        asked.append("add")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AccountDialog, "exec", fake_add_exec)
    dialog = SettingsDialog(world.backend, world.window.settings, SettingsContext(tray_available=True), world.window)
    qtbot.addWidget(dialog)
    dialog.add_account_requested.connect(lambda: world.window.add_account(after=dialog.reload))
    dialog.add_button.click()
    assert asked == ["add"]


def test_the_account_type_is_shown_and_imap_accounts_have_no_server_copies_tab(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    assert dialog.accounts.item(0).text().endswith("(POP3)")
    pop3 = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(pop3)
    assert [pop3.tabs.tabText(index) for index in range(pop3.tabs.count())] == ["General", "Servers", "Calendars and contacts", "Server copies"]
    imap_account = world.call("account.list")[0]
    from dataclasses import replace

    imap = AccountEditDialog(world.backend, replace(imap_account, kind="imap"))
    qtbot.addWidget(imap)
    assert [imap.tabs.tabText(index) for index in range(imap.tabs.count())] == ["General", "Servers", "Calendars and contacts", "Folders"]


def test_the_storage_choice_is_in_the_setup_and_the_account_settings_and_moves_the_mail(world: World, qtbot: QtBot) -> None:
    from chrono_mail.core import LOCAL_ACCOUNT_ID

    setup = AccountDialog(world.backend)
    qtbot.addWidget(setup)
    assert setup.storage.currentData() == "own" and setup.storage.isEnabled()  # the default is a mailbox of its own
    setup.kind.setCurrentIndex(setup.kind.findData("imap"))
    assert not setup.storage.isEnabled()
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    assert edit.storage is not None and edit.storage.currentData() == "own" and edit._values() is None  # noqa: SLF001
    edit.storage.setCurrentIndex(edit.storage.findData("local"))
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    listed = {item.account_id: item for item in world.call("account.list")}
    assert listed[world.account_id].storage == "local" and listed[LOCAL_ACCOUNT_ID].kind == "local"
    again = open_settings(world, qtbot)  # Local Folders itself is not listed as an account to edit
    assert again.accounts.count() == 1


def name_rows(form: QFormLayout) -> list[str]:
    """The labels and grey lines of a form, in order, up to the address."""
    rows: list[str] = []
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        text = label.widget().text() if label is not None and isinstance(label.widget(), QLabel) else ""
        widget = field.widget() if field is not None else None
        rows.append(text or (widget.text() if isinstance(widget, QLabel) else ""))
    return rows


def test_both_account_windows_have_account_name_your_name_and_address(world: World, qtbot: QtBot) -> None:
    added = AccountDialog(world.backend)
    qtbot.addWidget(added)
    edited = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(edited)
    for dialog in (added, edited):
        form = next(item for item in dialog.findChildren(QFormLayout) if item.indexOf(dialog.account_name) >= 0)
        rows = name_rows(form)
        start = rows.index("Account name")
        assert rows[start : start + 3] == ["Account name", "Your name", "E-mail address"]
        assert "User name" not in rows and "Password" not in rows  # the login is with each server
        assert "Name" not in rows
        assert isinstance(dialog.account_name, QLineEdit) and dialog.account_name.text() == ""


def test_the_account_name_is_saved_and_shown_in_the_lists_and_the_folder_list(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    edit = AccountEditDialog(world.backend, dialog.accounts.item(0).data(256))
    qtbot.addWidget(edit)
    edit.show()
    your_name = edit.name.text()
    edit.account_name.setText("Kerio test")
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    stored = world.call("account.list")[0]
    assert stored.account_name == "Kerio test" and stored.display_name == your_name and stored.shown_name == "Kerio test"
    dialog.reload()
    wait(qtbot, lambda: dialog.accounts.count() == 1 and dialog.accounts.item(0).text().startswith("Kerio test  <me@example.com>  - "))
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: bool(world.window.tree_model.findItems("Kerio test", Qt.MatchFlag.MatchRecursive)))
    world.window._fill_refresh_menu()  # noqa: SLF001
    assert '"Kerio test" only' in [action.text() for action in world.window.refresh_menu.actions()]
    edit = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(edit)
    assert edit.account_name.text() == "Kerio test"


def test_the_outgoing_login_follows_the_incoming_one_while_ticked(world: World, qtbot: QtBot) -> None:
    for dialog in (AccountDialog(world.backend), AccountEditDialog(world.backend, world.call("account.list")[0])):
        qtbot.addWidget(dialog)
        incoming = next(item for item in dialog.findChildren(QFormLayout) if item.indexOf(dialog.incoming_host) >= 0)
        outgoing = next(item for item in dialog.findChildren(QFormLayout) if item.indexOf(dialog.outgoing_host) >= 0)
        assert incoming.indexOf(dialog.username) >= 0 and incoming.indexOf(dialog.password) >= 0
        assert outgoing.indexOf(dialog.same_login) < outgoing.indexOf(dialog.outgoing_username) < outgoing.indexOf(dialog.outgoing_password)
        assert dialog.same_login.text() == "Use the same user name and password as for incoming mail" and dialog.same_login.isChecked()
        assert not dialog.outgoing_username.isEnabled() and not dialog.outgoing_password.isEnabled()  # greyed, not hidden
        dialog.username.setText("someone")
        assert dialog.outgoing_username.text() == "someone"
        dialog.same_login.setChecked(False)
        assert dialog.outgoing_username.isEnabled() and dialog.outgoing_password.isEnabled()
        dialog.username.setText("someone else")
        assert dialog.outgoing_username.text() == "someone"


def test_a_separate_outgoing_login_is_saved_and_the_tick_brings_the_incoming_one_back(world: World, qtbot: QtBot) -> None:
    account = world.call("account.list")[0]
    edit = AccountEditDialog(world.backend, account)
    qtbot.addWidget(edit)
    edit.same_login.setChecked(False)
    edit.outgoing_username.setText("smtp-user")
    edit.outgoing_password.setText("smtp-secret")
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    stored = world.call("account.list")[0]
    assert (stored.outgoing_same_login, stored.outgoing_username) == (False, "smtp-user")
    assert world.engine.passwords.get(world.account_id, ServerRole.OUTGOING) == "smtp-secret"
    edit = AccountEditDialog(world.backend, stored)
    qtbot.addWidget(edit)
    assert not edit.same_login.isChecked() and edit.outgoing_username.text() == "smtp-user"
    edit.same_login.setChecked(True)
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    stored = world.call("account.list")[0]
    assert (stored.outgoing_same_login, stored.outgoing_username) == (True, stored.incoming_username)
    assert world.engine.passwords.get(world.account_id, ServerRole.OUTGOING) == world.engine.passwords.get(world.account_id, ServerRole.INCOMING)
