import dataclasses
from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import MESSAGE_PLACES
from chrono_mail.engine.commands_view import MESSAGE_ORDER
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.messagelist import ROW_ROLE
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_rename_while_loading.py 4.2.456"

# --- Version history ----------------------------------------------------
# v4.2.456: Initial version: a folder renamed while its list is on its way shows its messages under the new name: no error, never empty; a message that comes in meanwhile is shown too; a new sort on its way meanwhile is kept


def with_projects(world: World, qtbot: QtBot) -> str:
    """The window on screen and a folder Projects with one message; returns its subject."""
    world.window.start()
    settle(qtbot, world, 3)
    world.call("folder.create", account=world.account_id, folder="Projects")
    first = world.window.list_model.index(0, 0).data(ROW_ROLE)
    world.call("message.move", account=world.account_id, folder="Inbox", message=first.message_id, target="Projects")
    wait(qtbot, lambda: world.window.folders.find_item((world.account_id, "folder", "Projects")) is not None)  # noqa: SLF001
    settle(qtbot, world, 2)
    return str(first.subject)


def shows_work(world: World, qtbot: QtBot, subject: str) -> None:
    """The list shows the folder under its new name, with its message, and no error."""
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.list_model.index(0, 0).data(ROW_ROLE).subject == subject
    assert "Could not load" not in world.window.status_label.text()


def hold_first(world: World, monkeypatch: pytest.MonkeyPatch, wanted: object) -> list[Callable[[], int]]:
    """The window's first question of this command is held back (it is on its way); calling the held entry sends it on as it came."""
    real_ask = world.backend.ask
    held: list[Callable[[], int]] = []

    def holding[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is wanted and not held:
            held.append(lambda: real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking))
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(world.backend, "ask", holding)
    return held


def test_a_folder_renamed_while_its_list_waits_to_be_read_shows_its_messages(world: World, qtbot: QtBot) -> None:
    subject = with_projects(world, qtbot)
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    with world.engine.place_lock(world.account_id):  # the list's question waits behind it, as behind any change of the mailbox
        world.window.tree.setCurrentIndex(projects)
        qtbot.wait(200)  # the question is on its way and waits
        world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")  # the person renames it meanwhile
    shows_work(world, qtbot, subject)  # read after the rename, the old name held nothing: never an empty list


def test_a_folder_renamed_before_its_list_is_asked_shows_its_messages_and_no_error(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    subject = with_projects(world, qtbot)
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    held = hold_first(world, monkeypatch, MESSAGE_ORDER)
    world.window.tree.setCurrentIndex(projects)
    wait(qtbot, lambda: len(held) == 1)  # the list's question is on its way
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")  # the person renames it meanwhile
    held[0]()  # the engine reads it now: "There is no folder 'Projects'"
    shows_work(world, qtbot, subject)


def test_a_message_that_comes_in_while_its_folder_is_renamed_is_shown(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    subject = with_projects(world, qtbot)
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    settle(qtbot, world, 1)
    held = hold_first(world, monkeypatch, MESSAGE_PLACES)
    second = str(world.call("message.list", account=world.account_id, folder="Inbox").messages[0].message_id)
    world.call("message.move", account=world.account_id, folder="Inbox", message=second, target="Projects")  # it comes in: its place is asked
    wait(qtbot, lambda: len(held) == 1)
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")  # the person renames it meanwhile
    held[0]()  # the engine reads it now: "There is no folder 'Projects'"
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)  # none is left out
    assert subject in {world.window.list_model.index(row, 0).data(ROW_ROLE).subject for row in range(2)}
    assert "Could not load" not in world.window.status_label.text()


def test_a_folder_renamed_while_a_new_sort_is_on_its_way_keeps_both(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    subject = with_projects(world, qtbot)
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    settle(qtbot, world, 1)
    spec = world.window.list_model._spec  # noqa: SLF001
    assert spec is not None
    held = hold_first(world, monkeypatch, MESSAGE_ORDER)
    world.window.list_model.rearrange(dataclasses.replace(spec, oldest_first=not spec.oldest_first))  # a new sort is on its way
    wait(qtbot, lambda: len(held) == 1)
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")  # the person renames it meanwhile
    held[0]()  # the engine reads it now: "There is no folder 'Projects'"
    shows_work(world, qtbot, subject)
    now = world.window.list_model._spec  # noqa: SLF001
    assert now is not None and (now.folder, now.oldest_first) == ("Work", not spec.oldest_first)  # the new name and the new sort
