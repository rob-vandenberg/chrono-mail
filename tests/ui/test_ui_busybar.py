import threading
import time
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters
from chrono_mail.ui.operations import ClickableBar, SteadyProgressBar
from chrono_mail.ui.reader import make_safe_view
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_busybar.py 4.2.465"

# --- Version history ----------------------------------------------------
# v4.2.465: a window's moving progress bar steps at most 20 times a second, only while on screen; Import, Backup, Restore and the update
#           download use it (D-266)
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


def paints_during(qtbot: QtBot, bar: QWidget, seconds: float) -> int:
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


def test_a_windows_moving_progress_bar_steps_at_most_20_times_a_second_and_only_while_it_moves_on_screen(qtbot: QtBot) -> None:
    QApplication.setStyle("Fusion")  # the program's style (D-158): Qt's own moving bar of this style redraws 60 times a second
    bar = SteadyProgressBar()
    qtbot.addWidget(bar)
    bar.resize(200, 20)
    bar.show()
    qtbot.waitExposed(bar)
    bar.setRange(0, 0)  # the end is not known: it moves
    assert paints_during(qtbot, bar, 0.5) > 0  # it moves
    assert bar._timer.isActive() and bar._timer.interval() == 1000 // 20  # 20 steps a second (it was 60)
    bar.setRange(0, 10)
    bar.setValue(4)
    assert not bar._timer.isActive()  # a count never moves by itself
    qtbot.wait(100)
    assert paints_during(qtbot, bar, 0.5) == 0
    bar.setRange(0, 0)
    bar.hide()
    assert not bar._timer.isActive()  # nothing to see: no steps
    bar.show()
    assert bar._timer.isActive()


def test_import_export_backup_restore_and_the_update_download_use_the_steady_progress_bar(world: World, qtbot: QtBot) -> None:
    from pathlib import Path

    from chrono_mail.engine.commands_backup import ContentsResult, ContentsRow
    from chrono_mail.engine.commands_updates import UpdateCheck
    from chrono_mail.ui.backupdialog import BackupDialog
    from chrono_mail.ui.exportdialog import ExportDialog
    from chrono_mail.ui.forms import part
    from chrono_mail.ui.importdialog import ImportDialog
    from chrono_mail.ui.restoredialog import RestoreDialog
    from chrono_mail.ui.updates import UpdateAvailableWindow

    parts = ("messages", "rules", "calendars", "contacts", "tasks", "settings")
    contents = ContentsResult("2026-10-20T10:00:00", "4.0.375", parts, (ContentsRow("acc1", "anna@example.com", parts, 12, 3, False),), 12, 3, True, True)
    check = UpdateCheck("available", "4.2.465", "4.2.466", "https://github.com/rob-vandenberg/chrono-mail/releases/tag/v4.2.466", "chrono-mail_4.2.466_amd64.deb", 1000)
    windows: list[QWidget] = [
        ImportDialog(world.backend, lambda: None, world.window), ExportDialog(world.backend, world.window), BackupDialog(world.backend, world.window.settings, world.window),
        RestoreDialog(world.backend, world.window.settings, Path("backup.cmz"), None, contents, world.window), UpdateAvailableWindow(world.backend, check, world.window),
    ]
    for window in windows:
        qtbot.addWidget(window)
        assert isinstance(part(window, QWidget, "progress"), SteadyProgressBar), type(window).__name__
