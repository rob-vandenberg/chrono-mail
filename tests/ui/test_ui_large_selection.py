from collections.abc import Callable, Generator, Iterable
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import Change
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.engine.commands_view import MESSAGE_ORDER, OrderParameters
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.mailstate import MailState, Placed
from tests.ui.test_ui_rapid_actions import fill
from tests.ui.world import Dialogs, World, build_world, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_large_selection.py 3.0.339"

# --- Version history ----------------------------------------------------
# v3.0.339: the Delete test waits for the list to empty (the rows leave in bursts, D-218) instead of reading it at one moment
# v2.4.334: the messages that leave in one moment go in a few removals, not one each
# v2.4.334: an action right after a large folder opened waits for the rest of the order and takes every selected message (W12b)
# v2.4.329: Initial (the Windows computer's W12 and W13): an action on a selection larger than what came into view takes every selected
#           message; Toggle read counts them all; the key press does its work once, not once per selected message


@pytest.fixture
def empty(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False)


def opened(qtbot: QtBot, world: World, size: int) -> None:
    """size unread messages in the Inbox, shown, only the first page of rows loaded, all of them selected (Ctrl+A)."""
    fill(world, size)
    window = world.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == size and world.backend._pending == {})  # noqa: SLF001
    assert model.row_at(size - 1) is None  # not come into view: not loaded
    window.list_view.setFocus()
    window.list_view.selectAll()


def held_in(world: World, folder: str) -> int:
    return message_list(world.engine, ListParameters(world.account_id, folder)).total


def test_delete_takes_every_selected_message_also_those_not_loaded(empty: World, qtbot: QtBot) -> None:
    """W12: Ctrl+A on 1,142 messages and Delete moved 100 to Trash, the rows loaded so far."""
    opened(qtbot, empty, 250)
    assert len(empty.window.messages.selected_rows()) == 250
    empty.window.mail_actions.delete_selected()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and empty.window.list_model.rowCount() == 0)  # noqa: SLF001 - the rows leave in bursts (D-218): waited for, not assumed
    assert held_in(empty, "Inbox") == 0


def test_an_action_right_after_opening_waits_for_the_rest_of_the_order(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """W12b: for a moment after a large folder opens the list knows only its first 100 messages; Ctrl+A and Delete then acted on 100."""
    held: list[Callable[[], None]] = []
    real_ask = empty.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.whole and not held:

            def later() -> None:
                real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

            held.append(later)  # the rest of the order is held back until the test lets it through
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    assert not model.complete
    window.list_view.setFocus()
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    assert held_in(empty, "Trash") == 0  # it waits: nothing done yet on 100 of them
    held[0]()  # the rest of the order arrives
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Inbox") == 0


def test_messages_that_leave_in_one_moment_go_in_one_step(empty: World, qtbot: QtBot) -> None:
    """The Windows computer's note at 15,927 messages: the rows left one by one, each laying the list out and painting it again, and the
    window answered slowly for minutes. The facts of one moment now go together: one removal for a run of neighbouring rows."""
    opened(qtbot, empty, 300)
    model = empty.window.list_model
    wait(qtbot, lambda: model.complete)
    keys = [place[0] for place in (model.place_at(row) for row in range(model.rowCount())) if place is not None]
    removals: list[int] = []

    def removed(parent: QModelIndex, first: int, last: int) -> None:
        removals.append(last - first + 1)

    model.rowsRemoved.connect(removed)
    for number, key in enumerate(keys):
        model.apply(Change("removed", empty.account_id, "Inbox", key, sequence=1_000_000 + number))
    assert model.rowCount() > 0  # nothing went one by one
    wait(qtbot, lambda: model.rowCount() == 0)
    assert len(removals) <= 3 and sum(removals) >= 300  # the rows and their group headings, in a few runs


def test_toggle_read_counts_every_selected_message(empty: World, qtbot: QtBot) -> None:
    opened(qtbot, empty, 250)
    model = empty.window.list_model
    empty.window.mail_actions.mark_messages([item for item in empty.window.messages.selected_rows() if item.row is not None], seen=True)
    wait(qtbot, lambda: empty.backend._pending == {})  # noqa: SLF001
    empty.window.mail_actions.toggle_read()  # the loaded ones are read, the others not: all become read
    wait(qtbot, lambda: empty.backend._pending == {} and all("seen" in getattr(model.row_at(row), "flags", ()) for row in range(250)))  # noqa: SLF001
    assert message_list(empty.engine, ListParameters(empty.account_id, "Inbox")).unread == 0


def test_the_key_press_works_once_not_once_per_selected_message(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """W13: every row greyed at the key press was redrawn alone, and every redraw looked at the whole selection again: 1,042 selected
    messages froze the window for about 139 s."""
    opened(qtbot, empty, 300)
    window = empty.window
    model = window.list_model
    for row in range(0, 300, 50):  # every row loaded, as after scrolling to the end
        model.row_at(row, fetch=True)
    wait(qtbot, lambda: all(model.row_at(row) is not None for row in range(300)) and empty.backend._pending == {})  # noqa: SLF001
    window.list_view.selectAll()
    looked: list[int] = []
    redraws: list[int] = []
    real_all_archived = MailState.all_archived

    def all_archived(state: MailState, rows: Iterable[Placed]) -> bool:
        looked.append(1)
        return real_all_archived(state, rows)

    def redrawn(first: QModelIndex | QPersistentModelIndex, last: QModelIndex | QPersistentModelIndex) -> None:
        redraws.append(last.row() - first.row() + 1)

    monkeypatch.setattr(MailState, "all_archived", all_archived)
    model.dataChanged.connect(redrawn)
    window.mail_actions.delete_selected()
    assert redraws == [300]  # the greyed rows: one redraw for the run of rows, not 300
    assert len(looked) <= 1  # the selection is looked at once at most
    wait(qtbot, lambda: held_in(empty, "Trash") == 300 and empty.backend._pending == {})  # noqa: SLF001
    model.dataChanged.disconnect(redrawn)
