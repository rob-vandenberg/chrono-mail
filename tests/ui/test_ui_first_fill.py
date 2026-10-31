from collections.abc import Callable, Mapping
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.commands_folders import FOLDER_LIST, FolderInfo
from chrono_mail.engine.commands_watch import MAIL_WAIT, WaitParameters, WaitResult
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.fakedav import FakeDav
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_first_fill.py 4.2.449"

# --- Version history ----------------------------------------------------
# v4.2.449: a new account only receives, once, and only by the rule of D-300; the automatic refresh's clocks start again after it; a new
#           account's push waits for the next full check, also the very first one; nothing is checked after a restore or an Edit account;
#           a calendar server given in Edit account only receives; no calendar sync by itself during an import; a first fill a full check
#           already covered is not done; the server's news of new mail leaves the fresh accounts out, and during an import or a
#           rebuild is checked when that ends, never after a restore; calendars given to a mail account leave its mail out of fresh;
#           an imported POP3 account gets no first fill; a first fill starts the moment the window knows the account; a folder made
#           while the folder lists load stays, also when two loads overlap

SERVER_WORK: frozenset[str] = frozenset({"mail.fetch", "mail.first-fill", "mail.send-outbox", "dav.sync"})


def recorded(world: World, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """The commands that reach a server, as the window asks for them: (command, receive_only for dav.sync)."""
    asked: list[tuple[str, bool]] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command in SERVER_WORK:
            asked.append((command, bool(values.get("receive_only", False))))
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    return asked


def ready(world: World, qtbot: QtBot) -> None:
    world.window.start()
    wait(qtbot, lambda: bool(world.window.mail_state.accounts) and world.window.list_model.rowCount() == 3)
    wait(qtbot, lambda: not world.window.checking.checking and world.backend._pending == {})  # noqa: SLF001


def kind_of(world: World, kind: str, dav_url: str = "") -> None:
    """The world's account as the window sees it, of another kind (the commands still reach the world's POP3 server)."""
    state = world.window.mail_state
    state.set_accounts([replace(account, kind=kind, dav_url=dav_url) for account in state.all_accounts])


def idle(world: World, qtbot: QtBot) -> None:
    checking = world.window.checking
    wait(qtbot, lambda: not checking.checking and not checking.dav_syncing and world.backend._pending == {})  # noqa: SLF001


@pytest.mark.parametrize(
    ("start", "auto", "how", "fills"),
    [
        (False, False, "send_receive", False),  # nothing receives by itself: a POP3 account waits for Refresh
        (True, False, "receive", True),
        (False, True, "send_receive", True),
        (True, True, "send", False),  # both only send: no wish to receive
    ],
)
def test_a_new_pop3_account_receives_once_only_when_a_refresh_of_its_own_receives(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, start: bool, auto: bool, how: str, fills: bool,
) -> None:
    ready(world, qtbot)
    settings = world.window.settings
    settings.setValue("check_on_start", start)
    settings.setValue("auto_check", auto)
    settings.setValue("start_refresh", how)
    settings.setValue("scheduled_refresh", how)
    asked = recorded(world, monkeypatch)
    world.window._first_fill({world.account_id}, mail=True)  # noqa: SLF001 - what Add account does once the window knows the account
    idle(world, qtbot)
    assert asked == ([("mail.first-fill", False)] if fills else [])  # never mail.fetch, never the Outbox


def test_a_new_imap_account_and_a_calendar_server_always_get_their_first_fill(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    world.window.settings.setValue("check_on_start", False)
    world.window.settings.setValue("auto_check", False)
    kind_of(world, "imap", dav_url="http://127.0.0.1:1/dav/")
    asked = recorded(world, monkeypatch)
    world.window._first_fill({world.account_id}, mail=True)  # noqa: SLF001
    idle(world, qtbot)
    assert sorted(asked) == [("dav.sync", True), ("mail.first-fill", False)]


def test_after_a_first_fill_the_automatic_refresh_counts_a_whole_interval_again(world: World, qtbot: QtBot) -> None:
    ready(world, qtbot)
    checking = world.window.checking
    world.window.settings.setValue("auto_check_minutes", 30)
    world.window.act_auto.setChecked(True)
    checking.timer.start(5_000)  # five seconds left of the interval
    world.window.settings.setValue("check_on_start", True)
    world.window._first_fill({world.account_id}, mail=True)  # noqa: SLF001
    idle(world, qtbot)
    assert checking.timer.isActive() and checking.timer.remainingTime() > 29 * 60 * 1000  # the full refresh comes a whole interval later
    world.window.act_auto.setChecked(False)


def test_a_new_imap_account_waits_for_new_mail_only_from_the_next_full_check(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    checking = world.window.checking
    watched: list[str] = []
    monkeypatch.setattr(checking, "_watch", watched.append)
    world.window.act_auto.setChecked(True)
    watched.clear()
    state = world.window.mail_state
    known = state.all_accounts
    new = replace(known[0], account_id="f" * 32, kind="imap")
    state.set_accounts([*known, new])  # an account added since the last load
    checking.accounts_loaded()
    assert new.account_id not in watched and new.account_id in checking.fresh
    checking.check_accounts({world.account_id})  # a check that does not take it in
    idle(world, qtbot)
    assert new.account_id in checking.fresh and new.account_id not in watched
    monkeypatch.setattr(state, "mail_account_ids", lambda: [world.account_id, new.account_id])
    checking.check_accounts(None)  # the next full check (the stand-in account has no mailbox: its row fails, the check still ends)
    idle(world, qtbot)
    assert new.account_id not in checking.fresh and new.account_id in watched  # it waits for new mail from now on
    world.window.act_auto.setChecked(False)


def test_nothing_is_checked_after_a_restore(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    world.window.settings.setValue("check_on_start", True)
    world.window.settings.setValue("auto_check", True)
    asked = recorded(world, monkeypatch)

    def rebuilt(then: Callable[[], None]) -> None:
        then()  # every index usable at once

    monkeypatch.setattr(world.window.rebuilds, "start", rebuilt)
    world.window._after_restore(False)  # noqa: SLF001 - what the Restore window's end calls
    idle(world, qtbot)
    assert asked == []


def test_no_calendar_sync_by_itself_while_an_import_runs(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    kind_of(world, "pop3", dav_url="http://127.0.0.1:1/dav/")
    world.window.settings.setValue("scheduled_refresh", "send")
    asked = recorded(world, monkeypatch)
    checking = world.window.checking
    checking.import_running(True)
    checking.scheduled_refresh(None)  # a timer's turn, Send only
    checking.sync_calendars()
    checking.import_running(False)
    idle(world, qtbot)
    assert asked == []


def test_the_very_first_account_waits_for_new_mail_until_the_next_full_check(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    window = world.window
    checking = window.checking
    summary = window.mail_state.all_accounts[0]

    def no_window(first: bool = False) -> None:
        pass  # the first-start Add window

    monkeypatch.setattr(window, "add_account", no_window)
    world.call("account.remove", account=world.account_id)
    window.reload_accounts()
    wait(qtbot, lambda: world.backend._pending == {} and window.mail_state.all_accounts == [])  # noqa: SLF001
    first = replace(summary, kind="imap")
    window.mail_state.set_accounts([first])  # the account added at the first start, as the next load gives it
    checking.accounts_loaded()
    assert first.account_id in checking.fresh


def test_edit_account_synchronises_nothing_by_itself_and_a_calendar_server_given_there_only_receives(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready(world, qtbot)
    with FakeDav() as server:
        world.call("account.set-dav", account=world.account_id, url=server.url, username="ann", password="secret")
        asked = recorded(world, monkeypatch)

        def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
            self.accounts_changed.emit()  # Edit account > Save without a calendar change, or Remove
            idle(world, qtbot)
            assert asked == []
            self.calendars_given.emit(world.account_id)  # Edit account gave it a calendar server
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
        world.window.act_settings.trigger()
        wait(qtbot, lambda: asked == [("dav.sync", True)])
        idle(world, qtbot)


def test_a_first_fill_waiting_behind_a_full_check_that_took_the_account_in_is_not_done(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    world.window.settings.setValue("check_on_start", True)
    asked = recorded(world, monkeypatch)
    checking = world.window.checking
    checking.check_accounts(None)  # a timer's full check, under way
    assert checking.checking
    world.window._first_fill({world.account_id}, mail=True)  # noqa: SLF001 - it waits behind that check
    idle(world, qtbot)
    assert asked == [("mail.fetch", False)]  # the full check did it all: no first fill after it (its rules would be skipped)


def test_news_of_new_mail_checks_every_account_but_the_fresh_ones(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    checking = world.window.checking
    state = world.window.mail_state
    known = state.all_accounts
    new = replace(known[0], account_id="f" * 32, kind="imap")
    calendars = replace(known[0], account_id="c" * 32, kind="dav")
    state.set_accounts([*known, new, calendars])  # an IMAP account just added, and an older account of calendars only
    checking.fresh = {new.account_id}
    checked: list[tuple[set[str] | None, set[str] | None]] = []

    def check_accounts(only: set[str] | None, manual: bool = False, new_round: bool = True, also_calendars: set[str] | None = None, first_fill: bool = False) -> None:
        checked.append((only, also_calendars))

    def ask(command: TypedCommand[WaitParameters, WaitResult], parameters: WaitParameters, done: Callable[[WaitResult], None], *_rest: object, **_named: object) -> int:
        if command is MAIL_WAIT and not checked:
            done(WaitResult(supported=True, changed=True))  # the server tells of new mail, once
        return 0

    monkeypatch.setattr(checking, "check_accounts", check_accounts)
    monkeypatch.setattr(world.backend, "ask", ask)
    monkeypatch.setattr(checking._auto, "isChecked", lambda: True)  # noqa: SLF001 - Automatic refresh on, without starting the real waits
    checking._watch("a" * 32)  # noqa: SLF001 - an IMAP account's wait for new mail
    assert checked == [({world.account_id}, {calendars.account_id})]  # all but the fresh one: it keeps its whole interval (D-300)


@pytest.mark.parametrize("holding", ["import", "rebuild"])
def test_news_of_new_mail_during_an_import_or_a_rebuild_is_checked_after_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, holding: str) -> None:
    ready(world, qtbot)
    asked = recorded(world, monkeypatch)
    checking = world.window.checking
    hold = checking.import_running if holding == "import" else checking.rebuild_running
    hold(True)
    checking._news_of_mail()  # noqa: SLF001 - the server tells it once, while nothing may be checked
    idle(world, qtbot)
    assert asked == []
    hold(False)
    idle(world, qtbot)
    assert asked == [("mail.fetch", False)]  # the push's own check, delayed; nothing else by itself (D-300)


def test_news_of_new_mail_during_a_restore_is_not_checked_after_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    asked = recorded(world, monkeypatch)
    checking = world.window.checking
    checking.restore_running(True)
    checking._news_of_mail()  # noqa: SLF001 - a server tells of new mail during the restore
    checking.restore_running(False)
    idle(world, qtbot)
    assert asked == []  # nothing checks by itself after a restore; its accounts are fresh (D-300)


def test_calendars_given_to_an_existing_mail_account_leave_its_mail_out_of_fresh(world: World, qtbot: QtBot) -> None:
    ready(world, qtbot)
    checking = world.window.checking
    kind_of(world, "imap", dav_url="http://127.0.0.1:1/dav/")
    checking.first_fill(set(), {world.account_id})  # Edit account gave it a calendar server
    assert world.account_id not in checking.fresh  # its own news of new mail still checks its mail
    idle(world, qtbot)


def test_an_imported_pop3_account_gets_no_first_fill(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    world.window.settings.setValue("auto_check", True)
    world.window.settings.setValue("scheduled_refresh", "send_receive")  # a refresh of its own receives: an added POP3 account would get one
    asked = recorded(world, monkeypatch)
    world.call(
        "account.add", display_name="Imported", email_address="imported@example.com", username="imp", password="secret", incoming_host="127.0.0.1",
        incoming_port=1, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
    )  # what an import makes
    world.window._after_import()  # noqa: SLF001 - what the Import window calls when it is done
    wait(qtbot, lambda: len(world.window.mail_state.all_accounts) == 2)
    idle(world, qtbot)
    assert asked == []  # its mail waits for a refresh (D-300)


def test_a_new_account_is_filled_the_moment_the_window_knows_it(world: World, qtbot: QtBot) -> None:
    ready(world, qtbot)
    order: list[str] = []
    world.window.reload_accounts(then=lambda: order.append("folders laid out"), known=lambda: order.append("known"))
    wait(qtbot, lambda: len(order) == 2)
    assert order == ["known", "folders laid out"]  # the first fill starts before the folder list waits for its answers: no timer comes first


def test_a_folder_made_while_the_folder_lists_load_stays_in_the_folder_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    pane = world.window.folders
    held: list[Callable[[], None]] = []
    real_ask = world.backend.ask

    def ask(command: TypedCommand[AccountParameters, tuple[FolderInfo, ...]], parameters: AccountParameters, done: Callable[[tuple[FolderInfo, ...]], None], *rest: object, **named: object) -> int:
        if command is FOLDER_LIST and not held:
            stale: tuple[FolderInfo, ...] = world.call("folder.list", account=parameters.account)  # read before the folder is made
            held.append(lambda: done(stale))
            return 0
        return real_ask(command, parameters, done, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(world.backend, "ask", ask)
    pane.load(False)
    wait(qtbot, lambda: bool(held))
    world.call("folder.create", account=world.account_id, folder="Projects")  # a first fill makes a folder meanwhile, told as a fact
    wait(qtbot, lambda: any(info.path == "Projects" for info in world.window.mail_state.folders.get(world.account_id, [])))
    monkeypatch.undo()
    held[0]()  # the older list arrives and is put in place
    wait(qtbot, lambda: any(info.path == "Projects" for info in world.window.mail_state.folders.get(world.account_id, [])) and world.backend._pending == {})  # noqa: SLF001


def test_a_folder_made_while_two_loads_overlap_stays_in_the_folder_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    pane = world.window.folders
    held: list[Callable[[], None]] = []
    real_ask = world.backend.ask

    def ask(command: TypedCommand[AccountParameters, tuple[FolderInfo, ...]], parameters: AccountParameters, done: Callable[[tuple[FolderInfo, ...]], None], *rest: object, **named: object) -> int:
        if command is FOLDER_LIST and len(held) < 2:
            stale: tuple[FolderInfo, ...] = world.call("folder.list", account=parameters.account)  # read before the folder is made
            held.append(lambda: done(stale))
            return 0
        return real_ask(command, parameters, done, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(world.backend, "ask", ask)
    pane.load(False)
    pane.load(False)  # a second reload starts before the first is in
    wait(qtbot, lambda: len(held) == 2)
    world.call("folder.create", account=world.account_id, folder="Projects")
    wait(qtbot, lambda: any(info.path == "Projects" for info in world.window.mail_state.folders.get(world.account_id, [])))
    monkeypatch.undo()
    held[0]()  # the older round ends first, with its older list
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - whatever that ending asked has been answered
    held[1]()  # then the newer one, with its older list too: it counts again what was told meanwhile
    wait(qtbot, lambda: any(info.path == "Projects" for info in world.window.mail_state.folders.get(world.account_id, [])) and world.backend._pending == {})  # noqa: SLF001
