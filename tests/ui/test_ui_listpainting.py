import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import override

import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStatusBar, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_listpainting.py 4.2.393"

# --- Version history ----------------------------------------------------
# v4.2.393: a removal looks as painted whole at 100, 125 and 150 % screen scale, each in a process of its own; the test of the partial
#           painting after a Delete became: after each Delete the list on screen looks as painted whole (D-266)
# v3.6.365: the busy bar test waits for the bar, which moves once work has run for a moment
# v3.6.353: a Delete without a server takes the row out at the key press; rapid presses take exactly the highlighted messages; a failure brings them back and stops the rest (D-266)
# v3.6.352: Initial: a Delete moves the rows below up as they are and paints only the new strip and the changed rows, and the list looks
#           exactly as when painted whole; a removal is painted with the rest of its change; the Archive button and the busy bar move
#           nothing in the window while nothing about them changes (D-266)

MESSAGES: int = 40
@pytest.fixture
def unread(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    """Favorites > Unread with 40 unread messages in the column layout, the list focused on the fourth row."""
    for world in build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False):
        with world.engine.quietly(), open_mailbox(world.engine, world.account_id) as mailbox:
            for number in range(MESSAGES):
                mailbox.deliver(("Inbox",), mail(number, subject=f"Note {number:05d}", message_id=f"<paint{number}@x>"), frozenset())
        window = world.window
        window.resize(1200, 800)
        window.start()
        wait(qtbot, lambda: bool(window.mail_state.accounts) and world.backend._pending == {})  # noqa: SLF001
        favorite = window.folders.find_item(("", "favorite", "unread"))
        assert favorite is not None
        window.tree.setCurrentIndex(favorite)
        model = window.list_model
        wait(qtbot, lambda: model.spec is not None and model.spec.combined == "unread" and model.settled and world.backend._pending == {})  # noqa: SLF001
        window.list_view.setFocus()
        window.list_view.setCurrentIndex(model.index(3, 0))
        wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
        yield world


class PaintCounter(QObject):
    """Counts the paint events of one widget and the rows' height of the last one."""

    def __init__(self, watched: QWidget) -> None:
        super().__init__(watched)
        self.count: int = 0
        watched.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


def on_screen(widget: QWidget) -> QImage:
    """What the window shows of this widget: the window's own pixels, not a fresh paint (grab paints anew)."""
    window = widget.window()
    screen = QApplication.primaryScreen()
    assert screen is not None
    image = screen.grabWindow(window.winId()).toImage()
    place, ratio = widget.mapTo(window, QPoint(0, 0)), image.devicePixelRatio()
    return image.copy(int(place.x() * ratio), int(place.y() * ratio), int(widget.width() * ratio), int(widget.height() * ratio))


def same_but_antialiasing(first: QImage, second: QImage) -> bool:
    """The same picture; a pixel may differ by at most 2 steps of a colour (the soft edge of the selection's corner)."""
    if first.size() != second.size():
        return False
    for y in range(first.height()):
        for x in range(first.width()):
            one, two = first.pixel(x, y), second.pixel(x, y)
            if one != two and max(abs(((one >> shift) & 255) - ((two >> shift) & 255)) for shift in (0, 8, 16)) > 2:
                return False
    return True


def test_after_a_delete_the_list_on_screen_looks_as_painted_whole(unread: World, qtbot: QtBot) -> None:
    """After each Delete the list on screen is exactly the list painted whole: the list paints what Qt asks for and never leaves rows
    of an earlier picture on screen (messages twice, others missing, D-266)."""
    model, view = unread.window.list_model, unread.window.list_view
    viewport = view.viewport()
    for _ in range(3):
        before = model.rowCount()
        QTest.keyClick(view, Qt.Key.Key_Delete)
        wait(qtbot, lambda: model.rowCount() == before - 1 and unread.backend._pending == {})  # noqa: SLF001
        QCoreApplication.sendPostedEvents()  # the paint the removal asked for
        shown = on_screen(viewport)
        view.repaint_all()
        viewport.repaint()
        assert same_but_antialiasing(shown, on_screen(viewport)), "the list on screen differs from the list painted whole"


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5"])
def test_a_removal_looks_as_painted_whole_at_every_screen_scale(scale: str) -> None:
    """At 125 % two rows of 17 are 42.5 screen pixels, which no screen can move. Before 4.2.393 the list moved its rows after a removal
    and painted only a strip; Qt could not move them, and the old rows stayed on screen (messages twice, others missing). The list must
    look as painted whole at every scale (D-266)."""
    project = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": scale}
    result = subprocess.run([sys.executable, "-m", "tests.ui.scaled_removal"], cwd=project, env=environment, capture_output=True, text=True,
                            timeout=15, check=False)
    assert result.returncode == 0, f"the list on screen differs from the list painted whole:\n{result.stdout}{result.stderr}"


def test_a_removal_is_painted_with_the_rest_of_its_change(unread: World, qtbot: QtBot) -> None:
    """When the list takes a row out, its repaint is asked for at once, so it comes in the same pass of painting as the folder numbers
    and the status line: Qt itself lays the rows out again only when a timer of its own runs, a later pass and a step of its own on screen."""
    window, model, view = unread.window, unread.window.list_model, unread.window.list_view
    view.viewport().grab()
    paints = PaintCounter(view.viewport())
    removed: list[bool] = []
    after: list[int] = []

    def rows_removed(*_: object) -> None:
        removed.append(True)
        paints.count = 0

    def change_done(*_: object) -> None:
        if removed and not after:
            QCoreApplication.sendPostedEvents()  # what was asked for so far, without the timers: Qt's own later layout cannot run here
            after.append(paints.count)

    model.rowsRemoved.connect(rows_removed)
    model.counts_changed.connect(change_done)  # the list tells its new numbers once the whole change is applied
    try:
        before = model.rowCount()
        QTest.keyClick(view, Qt.Key.Key_Delete)
        wait(qtbot, lambda: model.rowCount() == before - 1 and bool(after))
        assert after[0] >= 1, "the removal was left for a later pass of painting"
    finally:
        model.rowsRemoved.disconnect(rows_removed)
        model.counts_changed.disconnect(change_done)


def test_a_second_delete_right_after_the_first_is_not_held_back(unread: World, qtbot: QtBot) -> None:
    """A row that leaves right after another goes as soon as its fact is there, not when a batch interval ends: rows were gathered for
    100 ms, so a held Delete key removed at most ten rows a second and fell behind (D-266)."""
    model, view, backend = unread.window.list_model, unread.window.list_view, unread.window.backend
    before = model.rowCount()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    wait(qtbot, lambda: model.rowCount() == before - 1)
    facts: list[object] = []
    backend.changed.connect(facts.append)
    try:
        QTest.keyClick(view, Qt.Key.Key_Delete)
        wait(qtbot, lambda: bool(facts))
        QCoreApplication.processEvents()  # one pass of the event loop, no waiting
        assert model.rowCount() == before - 2, "the second removal waited for a batch interval"
    finally:
        backend.changed.disconnect(facts.append)


def test_a_delete_without_a_server_takes_the_row_out_at_the_key_press(unread: World) -> None:
    """POP3 and Local Folders need no server's word: the row is gone at the key press, before the engine has answered (D-266)."""
    model, view, backend = unread.window.list_model, unread.window.list_view, unread.window.backend
    before = model.rowCount()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert model.rowCount() == before - 1 and backend._pending  # noqa: SLF001 (the engine has not answered yet)


def selected_message(world: World) -> str:
    row = world.window.list_model.row_at(world.window.list_view.currentIndex().row())
    assert row is not None
    return row.message_id


def test_rapid_deletes_take_exactly_the_highlighted_messages(unread: World, qtbot: QtBot) -> None:
    """Each press takes the message highlighted when it is read, also when the presses come faster than the engine answers (D-266)."""
    window = unread.window
    model, view, backend = window.list_model, window.list_view, window.backend
    before = model.rowCount()
    chosen: list[str] = []
    for _ in range(12):
        chosen.append(selected_message(unread))
        QTest.keyClick(view, Qt.Key.Key_Delete)
    wait(qtbot, lambda: backend._pending == {})  # noqa: SLF001
    shown = {row.message_id for row in (model.row_at(number) for number in range(model.rowCount())) if row is not None}
    assert len(set(chosen)) == 12 and model.rowCount() == before - 12 and not set(chosen) & shown
    trash = window.folders.find_item((unread.account_id, "folder", "Trash"))
    assert trash is not None


def test_a_failed_delete_brings_its_rows_back_and_stops_the_rest(unread: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A delete that fails brings its message back, and the deletes waiting behind it are not done: they come back too, and the status
    line says so (D-266)."""
    window = unread.window
    model, view, backend = window.list_model, window.list_view, window.backend

    def refuse(*_: object, **__: object) -> None:
        raise ChronoMailError("The disk is full")

    monkeypatch.setattr(Mailbox, "delete", refuse)
    before = model.rowCount()
    chosen: list[str] = []
    for _ in range(4):
        chosen.append(selected_message(unread))
        QTest.keyClick(view, Qt.Key.Key_Delete)
    assert model.rowCount() == before - 4
    wait(qtbot, lambda: backend._pending == {} and model.rowCount() == before)  # noqa: SLF001
    shown = {row.message_id for row in (model.row_at(number) for number in range(model.rowCount())) if row is not None}
    assert set(chosen) <= shown
    assert "back in the list" in window.report.label.text()


def test_the_archive_button_is_left_alone_while_it_stays_archive(unread: World) -> None:
    window = unread.window
    changes: list[int] = []
    window.act_archive.changed.connect(lambda: changes.append(1))
    for row in (4, 5, 6, 4):
        window.list_view.setCurrentIndex(window.list_model.index(row, 0))
    assert changes == [] and window.act_archive.text() == "Archive"


def test_the_busy_bar_keeps_its_place_and_is_empty_while_nothing_runs(unread: World, qtbot: QtBot) -> None:
    window = unread.window
    report, bar = window.report, window.progress_bar
    status = window.findChild(QStatusBar)
    assert status is not None
    QCoreApplication.sendPostedEvents()
    line, place = report.line.geometry(), bar.geometry()
    assert bar.isVisible() and not bar.busy
    clicks: list[int] = []
    bar.clicked.connect(lambda: clicks.append(1))
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)
    assert clicks == []  # an empty place does nothing
    window.backend.busy_changed.disconnect(report.busy_changed)  # the test alone says when work runs (the window's own short work would stop the bar)
    report.busy_changed(1)
    wait(qtbot, lambda: bar.busy)  # it moves once the work has run for a moment (3.6.365)
    QCoreApplication.sendPostedEvents()
    assert bar.busy and bar.isVisible() and report.line.geometry() == line and bar.geometry() == place
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton)
    assert clicks == [1]
    report.busy_changed(0)
    QCoreApplication.sendPostedEvents()
    assert not bar.busy and bar.isVisible() and report.line.geometry() == line and bar.geometry() == place
