from collections.abc import Callable

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.commands_folders import FOLDER_LIST, FolderInfo
from chrono_mail.engine.registry import TypedCommand
from tests.ui.world import World, dialogs, wait, world  # noqa: F401 - fixtures

# --- Version ------------------------------------------------------------
__version__ = "test_ui_folder_loads.py 4.2.450"

# --- Version history ----------------------------------------------------
# v4.2.450: a folder-list load started while one runs takes it over: both follow-ups run once, in order; the older answers are ignored;
#           a load waiting for the tags still ends


def ready(world: World, qtbot: QtBot) -> None:
    world.window.start()
    wait(qtbot, lambda: bool(world.window.mail_state.accounts) and world.window.list_model.rowCount() == 3)
    wait(qtbot, lambda: not world.window.checking.checking and world.backend._pending == {})  # noqa: SLF001


def has_projects(world: World) -> bool:
    return any(info.path == "Projects" for info in world.window.mail_state.folders.get(world.account_id, []))


def holding(world: World, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """The folder lists the window asks for are read at once but answered only when the test says so (one entry per question)."""
    held: list[Callable[[], None]] = []
    real_ask = world.backend.ask

    def ask(command: TypedCommand[AccountParameters, tuple[FolderInfo, ...]], parameters: AccountParameters, done: Callable[[tuple[FolderInfo, ...]], None], *rest: object, **named: object) -> int:
        if command is FOLDER_LIST:
            read: tuple[FolderInfo, ...] = world.call("folder.list", account=parameters.account)
            held.append(lambda: done(read))
            return 0
        return real_ask(command, parameters, done, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(world.backend, "ask", ask)
    return held


def test_two_overlapping_loads_run_both_follow_ups_once_in_order(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    pane = world.window.folders
    held = holding(world, monkeypatch)
    ran: list[str] = []
    pane.load(False, lambda: ran.append("first"))
    pane.load(False, lambda: ran.append("second"))  # a second reload before the first is in
    wait(qtbot, lambda: len(held) == 2 and world.backend._pending == {})  # noqa: SLF001 - all else of both loads is in
    held[1]()  # the newer load ends first
    held[0]()  # the older answer, last: ignored
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    assert ran == ["first", "second"]


def test_an_older_load_answering_last_never_replaces_the_newer_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    pane = world.window.folders
    held = holding(world, monkeypatch)
    pane.load(False)  # reads the list before the folder is made
    wait(qtbot, lambda: len(held) == 1)
    world.call("folder.create", account=world.account_id, folder="Projects")
    wait(qtbot, lambda: has_projects(world))
    pane.load(False)  # reads it with the folder
    wait(qtbot, lambda: len(held) == 2)
    monkeypatch.undo()
    held[1]()
    wait(qtbot, lambda: world.backend._pending == {} and has_projects(world))  # noqa: SLF001
    held[0]()  # the older list comes in last
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    assert has_projects(world)


def test_a_load_waiting_for_the_tags_still_ends_when_a_newer_one_takes_over(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ready(world, qtbot)
    pane = world.window.folders
    tags = world.window.tags
    ran: list[str] = []
    tags.ready = False  # the tags of this reload have not answered yet
    pane.load(False, lambda: ran.append("first"))
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - its folder lists are in: it waits for the tags
    pane.load(False, lambda: ran.append("second"))
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    assert ran == []
    tags.ready = True
    pane._tags_arrived()  # noqa: SLF001 - the tags answer
    assert ran == ["first", "second"]  # what was to follow the first load is not lost
