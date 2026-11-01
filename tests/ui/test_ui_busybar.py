import threading
import time
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters
from chrono_mail.ui.operations import ClickableBar
from chrono_mail.ui.reader import make_safe_view
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_busybar.py 4.2.464"

# --- Version history ----------------------------------------------------
# v4.2.464: the busy bar is never redrawn once its work ended, steps at most 20 times a second, and stops its timer when hidden; a web
#           view has a window of its own (D-266)
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


class PaintCount(QObject):
    """Counts the paints one widget gets."""

    def __init__(self, watched: QObject) -> None:
        super().__init__()
        self.watched: QObject = watched
        self.count: int = 0

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.watched and event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


def paints_during(qtbot: QtBot, bar: ClickableBar, seconds: float) -> int:
    counter = PaintCount(bar)
    bar.installEventFilter(counter)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qtbot.wait(10)
    bar.removeEventFilter(counter)
    return counter.count


def test_the_busy_bar_is_never_redrawn_once_its_work_ended_and_steps_at_most_20_times_a_second(qtbot: QtBot) -> None:
    QApplication.setStyle("Fusion")  # the program's style (D-158): Qt's own busy animation of this style went on unseen
    bar = ClickableBar()
    qtbot.addWidget(bar)
    bar.resize(120, 12)
    bar.show()
    qtbot.waitExposed(bar)
    bar.set_busy(True)
    assert paints_during(qtbot, bar, 0.5) > 0  # it moves
    bar.set_busy(False)
    qtbot.wait(100)  # the paint that empties it
    assert paints_during(qtbot, bar, 1.0) == 0  # nothing runs: nothing is drawn again (it was about 60 a second, 4.2.463)
    bar.set_busy(True)
    assert bar._timer.interval() == 1000 // 20  # 20 steps a second (the owner, 4.2.464)
    bar.set_busy(False)


def test_the_busy_bars_timer_runs_only_while_work_runs_and_the_bar_is_on_screen(qtbot: QtBot) -> None:
    bar = ClickableBar()
    qtbot.addWidget(bar)
    bar.show()
    qtbot.waitExposed(bar)
    bar.set_busy(True)
    assert bar._timer.isActive()
    bar.hide()
    assert not bar._timer.isActive()  # nothing to see: no steps
    bar.show()
    assert bar._timer.isActive()
    bar.set_busy(False)
    assert not bar._timer.isActive()
    bar.hide()
    bar.show()
    assert not bar._timer.isActive()  # shown again while nothing runs: still still


def test_a_web_view_has_a_window_of_its_own(qtbot: QtBot) -> None:
    view = make_safe_view()[0]
    qtbot.addWidget(view)
    assert view.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)  # a change elsewhere never makes Qt put the whole window together again


def test_only_the_mail_display_and_the_parts_it_sits_in_have_a_window_of_their_own(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.show()
    qtbot.waitExposed(window)
    views = [view for view in window.findChildren(QWidget) if type(view).__name__ == "QWebEngineView"]
    assert views
    allowed: set[int] = set()
    for view in views:
        part: QWidget | None = view
        while part is not None and part is not window:
            allowed.add(id(part))
            part = part.parentWidget()
    natives = [type(part).__name__ for part in window.findChildren(QWidget) if part.internalWinId() and not part.isWindow() and id(part) not in allowed]
    assert natives == []  # their neighbours are never made windows of their own: splitters, toolbars and the status bar stay as they were
