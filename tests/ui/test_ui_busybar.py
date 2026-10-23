import threading

from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_busybar.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: Initial: the busy bar's hover text always names what runs, with its accounts, then what a click does


def test_the_busy_bars_hover_text_names_every_job_that_runs(world: World, qtbot: QtBot) -> None:
    release = threading.Event()

    def slow(engine: object, parameters: AccountParameters) -> None:
        release.wait(10)

    def slow_index(engine: object, parameters: NoParameters) -> None:
        release.wait(10)

    world.backend.registry.register("test.slow", "Takes long", AccountParameters, slow, doing="Taking long")
    world.backend.registry.register("test.slow-index", "Takes long", NoParameters, slow_index, doing="Rebuilding the search index")
    status = world.window.report
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: world.window.mail_state.account(world.account_id) is not None)  # the hover text names accounts as the window knows them
    done: list[object] = []
    try:
        world.backend.call("test.slow", {"account": world.account_id}, done.append)
        world.backend.call("test.slow", {"account": world.account_id}, done.append)  # the same work twice: one line, the account once
        tip = status.progress_bar.toolTip().split("\n")
        assert "Taking long: me@example.com" in tip and tip.count("Taking long: me@example.com") == 1 and tip[-1] == "Click to show the Operations window"
        world.backend.call("test.slow-index", {}, done.append)
        status.bar_hint(True)  # an index is updated: its line first, and a click shows its progress
        tip = status.progress_bar.toolTip().split("\n")
        assert tip[0] == "Rebuilding the search index" and "Taking long: me@example.com" in tip and tip[-1] == "Click to show the index progress"
        status.bar_hint(False)
    finally:
        release.set()
    wait(qtbot, lambda: len(done) == 3)
    wait(qtbot, lambda: "Taking long" not in status.progress_bar.toolTip())
    assert status.progress_bar.toolTip().split("\n")[-1] == "Click to show the Operations window"
