import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.ui.sections import CALENDAR
from tests.fakedav import FakeDav
from tests.test_sync_dav import event
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_refresh_everywhere.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: a calendars row is named by the account's own name and what it holds; Google's two parts apart (D-346)
# v4.4.606: a calendars-only account's row is named by its Shown as
# v4.4.605: Initial tests: each calendar account has its row in the Operations window; a Refresh while one runs is ignored (D-344)


def with_calendars(world: World, qtbot: QtBot, server: FakeDav) -> str:
    window = world.window
    window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: bool(window.mail_state.all_accounts))
    mail = str(world.call("account.list")[0].account_id)
    world.call("account.set-dav", account=mail, url=server.url, username="ann", password="secret")
    window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: any(str(getattr(item, "dav_url", "")) for item in window.mail_state.all_accounts))
    return mail


def idle(world: World, qtbot: QtBot) -> None:
    checking = world.window.checking
    wait(qtbot, lambda: not checking.dav_syncing and not checking.checking and not checking.sending)


def test_a_calendar_account_has_its_row_in_the_operations_window(world: World, qtbot: QtBot) -> None:
    """The owner (D-344): the Operations window shows exactly what it does, the calendars too, also from Refresh in the Calendar."""
    with FakeDav() as server:
        mail = with_calendars(world, qtbot, server)
        server.add("events", "r1.ics", event("r1", "From the server"))
        window = world.window
        window.switch_section(CALENDAR)
        window.pages[CALENDAR].act_refresh.trigger()
        wait(qtbot, lambda: (mail, "calendars") in window.checking.operations.rows())
        idle(world, qtbot)
        row = window.checking.operations.rows()[(mail, "calendars")]
        assert row.finished and row.name_label.text().endswith("(Calendars and contacts)")


def test_a_calendar_error_goes_to_the_errors_tab(world: World, qtbot: QtBot) -> None:
    with FakeDav() as server:
        mail = with_calendars(world, qtbot, server)
    world.window.checking.send_and_receive()  # the calendar server is gone
    idle(world, qtbot)
    operations = world.window.checking.operations
    row = operations.rows()[(mail, "calendars")]
    assert row.finished and row.failed and operations.error_texts()  # its error in the Errors tab, never a red status line


def test_a_refresh_while_one_runs_is_ignored(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    checking = world.window.checking
    started: list[str] = []

    def send_outbox(*args: object, **kwargs: object) -> None:
        started.append("send")

    monkeypatch.setattr(checking, "send_outbox", send_outbox)
    checking.dav_syncing = True  # a calendar synchronisation runs (the timer's, say)
    try:
        checking.send_and_receive()
    finally:
        checking.dav_syncing = False
    assert started == []
    checking.send_and_receive()
    assert started == ["send"]


def test_an_automatic_check_with_nothing_to_do_opens_no_window(world: World, qtbot: QtBot) -> None:
    checking = world.window.checking
    checking.check_accounts(set())  # the timer, with no account to check
    assert not checking.operations.isVisible()


def test_a_calendars_only_account_is_named_by_its_name_in_the_operations_window(world: World, qtbot: QtBot) -> None:
    from dataclasses import replace

    state = world.window.mail_state
    mail = world.call("account.list")[0]
    calendars = replace(mail, account_id="c" * 32, kind="dav", shown_name="Rob", own_name="Rob", email_address="", calendar_kind="Calendars and contacts")
    google = replace(calendars, account_id="d" * 32, shown_name="Rob - Google Contacts", calendar_kind="Google Contacts", google=True)
    state.set_accounts([mail, calendars, google])
    assert world.window.checking._operation_name(calendars.account_id, "calendars") == "Rob (Calendars and contacts)"  # noqa: SLF001
    assert world.window.checking._operation_name(google.account_id, "calendars") == "Rob (Google Contacts)"  # noqa: SLF001 - never "Rob - Google Contacts (...)"
