from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.ui.mailstate import MailState, Placed
from tests.ui.test_ui_rapid_actions import fill
from tests.ui.world import Dialogs, World, build_world, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_large_selection.py 2.4.329"

# --- Version history ----------------------------------------------------
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


def held(world: World, folder: str) -> int:
    return message_list(world.engine, ListParameters(world.account_id, folder)).total


def test_delete_takes_every_selected_message_also_those_not_loaded(empty: World, qtbot: QtBot) -> None:
    """W12: Ctrl+A on 1,142 messages and Delete moved 100 to Trash, the rows loaded so far."""
    opened(qtbot, empty, 250)
    assert len(empty.window.messages.selected_rows()) == 250
    empty.window.mail_actions.delete_selected()
    wait(qtbot, lambda: held(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held(empty, "Inbox") == 0 and empty.window.list_model.rowCount() == 0


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

    def all_archived(state: MailState, rows: list[Placed]) -> bool:
        looked.append(len(rows))
        return real_all_archived(state, rows)

    def redrawn(first: QModelIndex | QPersistentModelIndex, last: QModelIndex | QPersistentModelIndex) -> None:
        redraws.append(last.row() - first.row() + 1)

    monkeypatch.setattr(MailState, "all_archived", all_archived)
    model.dataChanged.connect(redrawn)
    window.mail_actions.delete_selected()
    assert redraws == [300]  # the greyed rows: one redraw for the run of rows, not 300
    assert len(looked) <= 1  # the selection is looked at once at most
    wait(qtbot, lambda: held(empty, "Trash") == 300 and empty.backend._pending == {})  # noqa: SLF001
    model.dataChanged.disconnect(redrawn)
