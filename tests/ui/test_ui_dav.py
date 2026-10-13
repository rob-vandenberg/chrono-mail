from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountedit import AccountEditDialog
from tests.fakedav import FakeDav
from tests.test_sync_dav import card, event, task
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_dav.py 0.4.117"

# --- Version history ----------------------------------------------------
# v0.4.117: A contact that came from the server is known to the message list at once
# v0.0.66: Initial tests of the calendar and contacts server in the account settings and the window


def test_the_server_is_found_and_saved_in_the_account_dialog(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        account = world.call("account.list")[0]
        dialog = AccountEditDialog(world.backend, account)
        qtbot.addWidget(dialog)
        dialog.show()
        titles = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
        assert "Calendars and contacts" in titles
        assert dialog._dav_values() is None  # noqa: SLF001 - nothing changed
        dialog.dav_url.setText(server.url)
        dialog.dav_username.setText("ann")
        dialog.dav_password.setText("secret")
        dialog.dav_check_button.click()
        wait(qtbot, lambda: "3 found" in dialog.dav_result.text())
        dialog.save_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        assert dialog.dav_changed
        stored = world.call("account.list")[0]
        assert stored.dav_url == server.url and stored.dav_username == "ann"


def test_the_window_synchronises_the_calendars_when_mail_is_checked(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        server.add("tasks", "t1.ics", task("t1", "Write"))
        server.add("contacts", "c1.vcf", card("c1", "Ann Lee"))
        account = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=account, url=server.url, username="ann", password="secret")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: bool(world.window._accounts) and bool(getattr(world.window._accounts[0], "dav_url", "")))  # noqa: SLF001
        world.window.check_mail()
        wait(qtbot, lambda: len([item for item in world.call("collection.list") if item.account == account]) == 3)
        wait(qtbot, lambda: not world.window._dav_syncing)  # noqa: SLF001
        assert [item.name for item in world.call("contact.list").contacts] == ["Ann Lee"]


def test_a_contact_from_the_server_is_known_to_the_message_list_at_once(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        server.add("contacts", "c1.vcf", "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Ann Lee\r\nEMAIL:ann@example.org\r\nEND:VCARD\r\n")
        account = world.call("account.list")[0].account_id
        world.call("account.set-dav", account=account, url=server.url, username="ann", password="secret")
        world.window.reload_accounts(select_saved=False)
        wait(qtbot, lambda: bool(world.window._accounts) and bool(getattr(world.window._accounts[0], "dav_url", "")))  # noqa: SLF001
        world.window._sync_calendars()  # noqa: SLF001 - only the calendar and contacts sync, no mail check (that loads the addresses too)
        wait(qtbot, lambda: "ann@example.org" in world.window.list_model.contacts)
