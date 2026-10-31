from collections.abc import Mapping
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_watch import WaitParameters, WaitResult
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure, Success
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_send_receive_tick.py 4.2.451"

# --- Version history ----------------------------------------------------
# v4.2.451: an account not included in Send and Receive takes part in nothing but its own Refresh menu entry; that entry sends and
#           receives, sends or receives one account; a calendars-only account's entry synchronises it; the calendar refresh names the
#           accounts included; no wait for new mail and no first fill for an account left out (D-059)

SERVER_WORK: frozenset[str] = frozenset({"mail.fetch", "mail.first-fill", "mail.send-outbox", "dav.sync"})


def recorded(world: World, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """The commands that reach a server, with the accounts or senders they were given."""
    asked: list[tuple[str, object]] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command in SERVER_WORK:
            what = values.get("senders") if command == "mail.send-outbox" else values.get("account") or values.get("accounts")
            asked.append((command, tuple(what) if isinstance(what, (list, tuple)) else what))
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    return asked


def ready(world: World, qtbot: QtBot, included: bool = True) -> None:
    world.call("account.edit", account=world.account_id, in_send_receive=included)
    world.window.start()
    wait(qtbot, lambda: bool(world.window.mail_state.accounts) and world.window.list_model.rowCount() == 3)
    wait(qtbot, lambda: not world.window.checking.checking and world.backend._pending == {})  # noqa: SLF001


def idle(world: World, qtbot: QtBot) -> None:
    checking = world.window.checking
    wait(qtbot, lambda: not checking.checking and not checking.sending and not checking.dav_syncing and world.backend._pending == {})  # noqa: SLF001


def test_an_account_left_out_takes_part_in_no_refresh_but_its_own(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot, included=False)
    state = world.window.mail_state
    state.set_accounts([replace(account, dav_url="http://127.0.0.1:1/dav/") for account in state.all_accounts])  # its calendars stay out too
    asked = recorded(world, monkeypatch)
    checking = world.window.checking
    checking.send_and_receive()  # Refresh, Send and Receive All
    checking.check_accounts(None, manual=True)  # Receive All
    checking.send_outbox(manual=True)  # Send All
    checking.check_on_general_interval()  # Automatic refresh
    checking.start_refresh()  # the refresh at start
    checking.sync_calendars(None, manual=True)  # Refresh in Calendar, Contacts or Tasks
    idle(world, qtbot)
    assert asked == []


@pytest.mark.parametrize(("how", "expected"), [
    ("receive", ["mail.fetch"]), ("send", ["mail.send-outbox"]), ("send_receive", ["mail.send-outbox", "mail.fetch"]),
])
def test_an_accounts_own_entry_works_also_when_it_is_left_out(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, how: str, expected: list[str]) -> None:
    ready(world, qtbot, included=False)
    asked = recorded(world, monkeypatch)
    world.window.checking.refresh_account(world.account_id, how)
    idle(world, qtbot)
    assert [command for command, _ in asked] == expected
    assert all(what in (world.account_id, (world.account_id,)) for _, what in asked)  # that account only, its own Outbox only


def test_an_included_account_takes_part_in_send_all(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    asked = recorded(world, monkeypatch)
    world.window.checking.send_outbox(manual=True)
    idle(world, qtbot)
    assert asked == [("mail.send-outbox", (world.account_id,))]


def test_the_refresh_menu_has_a_submenu_per_mail_account_and_one_item_per_calendars_only_account(world: World, qtbot: QtBot) -> None:
    ready(world, qtbot)
    state = world.window.mail_state
    mail = state.all_accounts[0]
    calendars = replace(mail, account_id="c" * 32, kind="dav", account_name="Shared calendars")
    state.set_accounts([*state.all_accounts, calendars])
    world.window._fill_refresh_menu()  # noqa: SLF001 - what the Refresh button's menu does when it opens
    entries = [action for action in world.window.refresh_menu.actions() if action.text().endswith(" only")]
    assert [action.text() for action in entries] == [f"\"{mail.shown_name}\" only", f"\"{calendars.shown_name}\" only"]
    submenu = entries[0].menu()
    assert isinstance(submenu, QMenu) and [action.text() for action in submenu.actions()] == ["Send and Receive", "Send", "Receive"]
    assert entries[1].menu() is None  # it only synchronises: one item, no submenu


def test_the_calendar_refresh_names_only_the_accounts_included(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    state = world.window.mail_state
    mine = replace(state.all_accounts[0], dav_url="http://127.0.0.1:1/dav/")
    left_out = replace(mine, account_id="c" * 32, kind="dav", in_send_receive=False)
    state.set_accounts([mine, left_out])
    asked = recorded(world, monkeypatch)
    world.window.checking.refresh_calendars()  # Refresh in Calendar, Contacts or Tasks
    idle(world, qtbot)
    assert asked == [("dav.sync", (mine.account_id,))]  # named: never the engine's "every account"


def test_an_imap_account_left_out_gets_no_wait_for_new_mail(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    state = world.window.mail_state
    state.set_accounts([replace(account, kind="imap", in_send_receive=False) for account in state.all_accounts])
    waits: list[str] = []

    def ask(command: TypedCommand[WaitParameters, WaitResult], *_rest: object, **_named: object) -> int:
        waits.append(command.id)
        return 0

    monkeypatch.setattr(world.backend, "ask", ask)
    world.window.checking._watch(world.account_id)  # noqa: SLF001 - also a wait that re-arms itself after the tick was taken away
    assert waits == []


def test_an_account_left_out_gets_no_first_fill(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot, included=False)
    state = world.window.mail_state
    state.set_accounts([replace(account, kind="imap", dav_url="http://127.0.0.1:1/dav/") for account in state.all_accounts])
    asked = recorded(world, monkeypatch)
    world.window._first_fill({world.account_id}, mail=True)  # noqa: SLF001 - e.g. a calendar server given in Edit account
    idle(world, qtbot)
    assert asked == []
