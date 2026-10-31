import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_view import MESSAGE_ORDER
from chrono_mail.ui.messagelist import ROW_ROLE
from tests.ui.test_ui_rename_while_loading import hold_first
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sort_refetch.py 4.2.457"

# --- Version history ----------------------------------------------------
# v4.2.457: Initial version: a refetch while a new sort is on its way keeps the new sort, as the sort menu shows it; after another view
#           is shown, a refetch never brings back the view left


def subjects(world: World) -> list[str]:
    model = world.window.list_model
    return [str(model.index(row, 0).data(ROW_ROLE).subject) for row in range(model.rowCount())]


def test_a_refetch_while_a_new_sort_is_on_its_way_keeps_the_new_sort(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    before = subjects(world)
    pane = world.window.messages
    held = hold_first(world, monkeypatch, MESSAGE_ORDER)
    pane.set_sort(pane.sort, not pane.oldest_first)  # the person turns the order round: its question is on its way
    wait(qtbot, lambda: len(held) == 1)
    world.window.list_model.reload()  # a mail check or an import changed the mailbox meanwhile: the list is fetched again
    held[0]()  # the sort's answer comes last
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    wait(qtbot, lambda: subjects(world) == list(reversed(before)))  # the order the sort menu shows
    spec = world.window.list_model.spec
    assert spec is not None and spec.oldest_first == pane.oldest_first


def test_a_refetch_after_another_view_is_shown_never_brings_back_the_view_left(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    pane, model = world.window.messages, world.window.list_model
    held = hold_first(world, monkeypatch, MESSAGE_ORDER)
    pane.set_sort(pane.sort, not pane.oldest_first)  # a new sort is on its way
    wait(qtbot, lambda: len(held) == 1)
    model.show(None)  # another view that asks nothing (nothing chosen)
    assert not model.loading
    model.reload()
    assert model.spec is None  # the folder left does not come back
