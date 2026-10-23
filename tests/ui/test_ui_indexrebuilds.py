import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import RebuildStep
from chrono_mail.ui import indexrebuilds
from chrono_mail.ui.indexrebuilds import ABORT_CLOSES, ABORT_KEEPS, ABORT_TITLE, ask_abort
from tests.test_engine_rebuilds import as_format_11
from tests.ui.world import World, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_indexrebuilds.py 2.1.310"

# --- Version history ----------------------------------------------------
# v2.1.310: "The index is ready." is read when the rebuild ends: the junk filter's first learning may say more after it (D-238)
# v2.0.303: a click on the busy bar brings the window back after Run in background; afterwards it opens the Operations window
# v2.0.281: Initial version: the rebuild window at start, Run in background only while the old index is usable, Abort after its
#           question (closes the program only when nothing usable is left), the status line, the paused mail checks (D-229)


def test_an_unusable_index_is_rebuilt_before_the_mail_is_shown(world: World, qtbot: QtBot) -> None:
    window = world.window
    world.engine.directories.index_file(world.account_id).write_bytes(b"not a database")
    shown: list[tuple[str, str, bool]] = []
    window.rebuilds.running_changed.connect(lambda running: shown.append(("running", str(running), window.list_model.rowCount() > 0)))
    said_when_done: list[str] = []
    window.rebuilds.running_changed.connect(lambda running: None if running else said_when_done.append(window.report.label.text()))
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and not window.rebuilds.running and world.backend._pending == {})  # noqa: SLF001
    assert shown[0] == ("running", "True", False) and shown[-1][:2] == ("running", "False")  # rebuilt first, the mail shown after
    rebuild = window.rebuilds.window
    assert rebuild is not None and not rebuild.isVisible() and rebuild.windowTitle() == "Rebuilding the index"
    assert rebuild.account_label.text().startswith("Account 1 of 1: ") and rebuild.count_label.text() == "3 of 3"
    assert not rebuild.background_button.isEnabled() and not rebuild.background_button.isHidden()  # nothing usable meanwhile: greyed, not hidden
    assert said_when_done == ["The index is ready."]  # what starts after it (the junk filter's first learning) may say more later


def test_an_upgraded_index_is_used_at_once_and_its_sent_dates_are_filled_in_meanwhile(world: World, qtbot: QtBot) -> None:
    window = world.window
    as_format_11(world.engine.directories.index_file(world.account_id))
    lines: list[str] = []
    world.backend.rebuilding.connect(lambda step: lines.append(window.report.label.text()))
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and not window.rebuilds.running and world.backend._pending == {})  # noqa: SLF001
    rebuild = window.rebuilds.window
    assert rebuild is not None and rebuild.background_button.isEnabled()  # the old index stays in use: it may run in the background
    assert any(line.startswith("Rebuilding the index of ") and line.endswith("3 of 3") for line in lines)
    wait(qtbot, lambda: not window.checking.checking and world.backend._pending == {})  # noqa: SLF001 - the check that waited for it


def test_the_status_line_says_whose_index_and_how_many_of_how_many(world: World) -> None:
    world.window.report.rebuild_progress("me@example.com", 18_000, 140_567)
    assert world.window.report.label.text() == "Rebuilding the index of me@example.com… 18,000 of 140,567"


def test_run_in_background_hides_the_window_and_the_status_line_goes_on(world: World) -> None:
    rebuilds = world.window.rebuilds
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 10, 100, usable=True))
    assert rebuilds.window is not None and rebuilds.window.isVisible() and rebuilds.running
    rebuilds.window.background_button.click()
    assert not rebuilds.window.isVisible()
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 50, 100, usable=True))
    assert not rebuilds.window.isVisible() and world.window.report.label.text().endswith("50 of 100")
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 0, 0, usable=True, ended=True))
    assert not rebuilds.running


def test_a_click_on_the_bar_brings_the_window_back_and_after_the_end_the_operations_window(world: World) -> None:
    """The owner (D-229): after Run in background the window could not be brought back."""
    rebuilds, bar = world.window.rebuilds, world.window.progress_bar
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 10, 100, usable=True))
    assert rebuilds.window is not None and bar.toolTip() == "Show the index progress"
    rebuilds.window.background_button.click()
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 83, 100, usable=True))
    assert not rebuilds.window.isVisible()
    bar.clicked.emit()
    assert rebuilds.window.isVisible() and rebuilds.window.count_label.text() == "83 of 100" and not world.window.operations.isVisible()
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 90, 100, usable=True))
    assert rebuilds.window.count_label.text() == "90 of 100"  # it follows again
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 0, 0, usable=True, ended=True))
    assert not rebuilds.running and bar.toolTip() == "Show the Operations window"
    bar.clicked.emit()
    assert world.window.operations.isVisible() and not rebuilds.window.isVisible()
    world.window.operations.close()


@pytest.mark.parametrize("usable", [True, False], ids=["usable", "nothing_usable"])
def test_abort_asks_first_and_closes_the_program_only_when_nothing_usable_is_left(world: World, monkeypatch: pytest.MonkeyPatch, usable: bool) -> None:
    rebuilds, asked = world.window.rebuilds, []
    answers = [False, True]  # Continue rebuilding, then Abort
    monkeypatch.setattr(indexrebuilds, "ask_abort", lambda parent, can_use: asked.append(can_use) or answers.pop(0))
    quits: list[bool] = []
    rebuilds.quit_requested.disconnect()
    rebuilds.quit_requested.connect(lambda: quits.append(True))
    world.backend.rebuilding.emit(RebuildStep(world.account_id, 10, 100, usable=usable))
    assert rebuilds.window is not None
    rebuilds.window.abort_button.click()  # Continue rebuilding: nothing happens
    assert asked == [usable] and quits == [] and rebuilds.window.isVisible()
    rebuilds.window.abort_button.click()
    assert asked == [usable, usable] and quits == ([] if usable else [True])


@pytest.mark.parametrize("usable", [True, False], ids=["usable", "nothing_usable"])
def test_the_abort_question_says_what_happens_and_continues_by_default(world: World, monkeypatch: pytest.MonkeyPatch, usable: bool) -> None:
    seen: list[tuple[str, str, str, list[str]]] = []

    def shown(box: QMessageBox) -> int:
        default = box.defaultButton()
        seen.append((box.windowTitle(), box.text(), default.text() if default is not None else "", [button.text() for button in box.buttons()]))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", shown)
    assert not ask_abort(world.window, usable)  # closed without a choice: it goes on
    title, text, default, buttons = seen[0]
    assert (title, default) == (ABORT_TITLE, "Continue rebuilding") and text == (ABORT_KEEPS if usable else ABORT_CLOSES)
    assert sorted(buttons) == sorted(["Abort" if usable else "Abort and close", "Continue rebuilding"])


def test_the_automatic_checks_wait_while_an_index_is_rebuilt_and_one_runs_after(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    checking = world.window.checking
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.backend._pending == {})  # noqa: SLF001
    started: list[str] = []
    monkeypatch.setattr(checking, "_begin_operations", lambda manual: started.append("manual" if manual else "automatic"))
    checking.rebuild_running(True)
    checking.check_accounts(None)
    assert started == [] and not checking.checking  # the timer's turn is skipped
    checking.rebuild_running(False)
    assert started == ["automatic"]  # one check when it is done
    wait(qtbot, lambda: not checking.checking and world.backend._pending == {})  # noqa: SLF001
