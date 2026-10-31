from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, AccountSummary
from chrono_mail.ui.accountdialog import CALDAV, AccountDialog
from chrono_mail.ui.calendaraccount import NEW_ACCOUNT_TEXT
from chrono_mail.ui.serverfields import account_line
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_for_list.py 4.2.444"

# --- Version history ----------------------------------------------------
# v4.2.444: Initial version: For lists each mail account as Settings > Accounts does (the owner)


def mail_accounts(world: World) -> list[AccountSummary]:
    listed = world.call(ACCOUNT_LIST.id)
    assert isinstance(listed, tuple)
    return [account for account in listed if isinstance(account, AccountSummary) and account.kind in ("pop3", "imap")]


def test_for_lists_each_mail_account_as_settings_accounts_does(world: World, qtbot: QtBot) -> None:
    accounts = mail_accounts(world)
    dialog = AccountDialog(world.backend, mail_accounts=accounts, start_kind=CALDAV)
    qtbot.addWidget(dialog)
    target = dialog.calendar.target
    shown = [target.itemText(row) for row in range(target.count())]
    account = accounts[0]
    assert shown == [NEW_ACCOUNT_TEXT, account_line(account)]
    assert f"<{account.email_address}>" in shown[1] and shown[1].endswith(f"- {account.server_label}") and "(POP3)" in shown[1]
    settings = SettingsDialog(world.backend, world.window.settings, world.window._settings_context(), world.window)  # noqa: SLF001 - what the main window gives it
    qtbot.addWidget(settings)
    settings.show_page("Accounts")
    wait(qtbot, lambda: settings.accounts.count() == 1)
    item = settings.accounts.item(0)
    assert item is not None and item.text() == shown[1]  # the same text in both lists
