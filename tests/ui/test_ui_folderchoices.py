from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountedit import AccountEditDialog
from tests.ui.test_ui_push import Imap, build
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folderchoices.py 2.3.316"

# --- Version history ----------------------------------------------------
# v2.3.316: the own-interval timer test lets the timers only receive (since 2.2.314 they send first by default)
# v1.9.271: The account editor's changes are typed (R-19)
# v1.9.265: the general timer also passes the accounts of calendars only (since 1.1.217): the stand-in takes it (R-44)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v0.0.44: Initial tests of the folder choices and the check interval of an account


def given(parameters: object) -> dict[str, object]:
    """The fields a typed changes object sets (R-19): what the old dictionary of changes held."""
    if parameters is None:
        return {}
    return {name: value for name in parameters.__dataclass_fields__ if name != "account" and (value := getattr(parameters, name)) not in (None, ())}  # type: ignore[attr-defined]


def rows_by_name(dialog: AccountEditDialog) -> dict[str, int]:
    table = dialog.folder_table
    assert table is not None
    names: dict[str, int] = {}
    for row in range(table.rowCount()):
        item = table.item(row, 1)
        assert item is not None
        names[item.text()] = row
    return names


def test_the_folders_tab_lists_the_server_folders_and_saves_the_changes(tmp_path: Path, qtbot: QtBot) -> None:
    for world in build(tmp_path, qtbot, auto=False):
        world.server.add("Projects", b"Subject: p\r\nMessage-ID: <p@x>\r\n\r\nbody\r\n")
        listed = world.window.backend.run_now("account.list", {})
        assert isinstance(listed, tuple)
        dialog = AccountEditDialog(world.window.backend, listed[0])
        qtbot.addWidget(dialog)
        dialog.show()
        wait(qtbot, lambda: dialog.folder_table is not None and dialog.folder_table.rowCount() >= 6)
        rows = rows_by_name(dialog)
        table = dialog.folder_table
        assert table is not None and {"INBOX", "Sent", "Projects"} <= set(rows)
        inbox_item = table.item(rows["INBOX"], 0)
        assert inbox_item is not None and not bool(inbox_item.flags() & Qt.ItemFlag.ItemIsUserCheckable)  # the Inbox is always there
        projects = table.item(rows["Projects"], 0)
        assert projects is not None
        projects.setCheckState(Qt.CheckState.Unchecked)
        chooser = table.cellWidget(rows["Sent"], 2)
        assert isinstance(chooser, QComboBox)
        other = table.cellWidget(rows["Projects"], 2)
        assert isinstance(other, QComboBox)
        other.setCurrentIndex(other.findData("archive"))
        assert given(dialog._folder_changes()) == {"hide": ("Projects",), "role": ("Projects=archive",)}  # noqa: SLF001
        dialog.save_button.click()
        wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
        result = world.window.backend.run_now("account.folders", {"account": world.account})
        assert isinstance(result, tuple)
        folders = {str(getattr(choice, "server_name")): choice for choice in result}
        assert not getattr(folders["Projects"], "shown") and getattr(folders["Projects"], "chosen_role") == "archive"


def test_unchanged_folders_save_nothing_and_a_check_interval_is_saved(world: World, qtbot: QtBot) -> None:
    listed = world.call("account.list")
    dialog = AccountEditDialog(world.backend, listed[0])
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.folder_table is None and dialog._folder_changes() is None  # noqa: SLF001 - a POP3 account has no folders tab
    assert dialog.check_minutes.value() == 0 and dialog._values() is None  # noqa: SLF001
    dialog.check_minutes.setValue(20)
    assert given(dialog._values()) == {"check_minutes": 20}  # noqa: SLF001
    dialog.save_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert world.call("account.list")[0].check_minutes == 20


def test_an_account_with_its_own_interval_has_its_own_timer(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.settings.setValue("scheduled_refresh", "receive")  # which accounts each timer checks; sending first is tested in test_ui_delayed_send
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.checking.account_timers == {}  # noqa: SLF001
    world.call("account.edit", account=world.account_id, check_minutes=7)
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: world.account_id in world.window.checking.account_timers)  # noqa: SLF001
    timer = world.window.checking.account_timers[world.account_id]  # noqa: SLF001
    assert timer.isActive() and timer.interval() == 7 * 60_000
    checked: list[set[str] | None] = []

    def record(only: set[str] | None, also_calendars: set[str] | None = None) -> None:
        checked.append(only)

    monkeypatch.setattr(world.window.checking, "check_accounts", record)
    world.window.checking.check_on_general_interval()  # noqa: SLF001 - this account has its own interval: the general timer leaves it out
    timer.timeout.emit()
    assert checked == [set(), {world.account_id}]
    world.window.act_auto.setChecked(False)
    assert world.account_id not in world.window.checking.account_timers and not timer.isActive()  # noqa: SLF001 - no automatic checking, no timers
    world.call("account.edit", account=world.account_id, check_minutes=0)
    world.window.act_auto.setChecked(True)
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: world.account_id not in world.window.checking.account_timers)  # noqa: SLF001
