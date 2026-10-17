from collections.abc import Callable, Mapping
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.ui import models
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.models import MessageListModel, ViewSpec
from tests.ui.world import World, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_list_changes.py 0.5.163"

# --- Version history ----------------------------------------------------
# v0.5.163: messages that come in at once are placed with one question; messages gone from a part not loaded give one rearrange, never a reset
# v0.5.156: Initial tests: the list applies each fact of the engine to its own rows, without fetching the view again (D-146 step 2)


class Watched:
    """A list fed only by the facts of the engine (not yet wired into the window), with the commands it asks for."""

    def __init__(self, world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, spec: ViewSpec, groups: bool = False) -> None:
        self.world, self.qtbot = world, qtbot
        self.asked: list[str] = []
        self.resets = 0
        self.model = MessageListModel(world.backend)
        self.model.show_groups = groups
        self.model.modelReset.connect(self._reset)
        real_call = world.backend.call

        def call(
            command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
            in_order: bool = False, checking: bool = False,
        ) -> int:
            self.asked.append(command)
            return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

        monkeypatch.setattr(world.backend, "call", call)
        world.backend.changed.connect(self.model.apply)
        self.model.show(spec)
        self.settle()
        self.asked.clear()
        self.resets = 0

    def _reset(self) -> None:
        self.resets += 1

    def settle(self) -> None:
        wait(self.qtbot, lambda: self.world.backend._pending == {} and self.model.settled)  # noqa: SLF001

    def subjects(self) -> list[str]:
        model = self.model
        return [str(getattr(model.row_at(row), "subject")) for row in range(model.rowCount()) if not model.is_header(row) and model.row_at(row) is not None]

    def headers(self) -> list[str]:
        return [str(self.model.index(row, 0).data()) for row in self.model.header_rows()]


def ids(world: World, folder: str = "Inbox", **values: Any) -> dict[str, str]:  # noqa: ANN401 - forwarded to message.list
    """subject -> message id in a folder."""
    return {row.subject: row.message_id for row in world.call("message.list", account=world.account_id, folder=folder, limit=5000, **values).messages}


def listed(world: World, folder: str = "Inbox", **values: Any) -> list[str]:  # noqa: ANN401 - forwarded to message.list
    return [row.subject for row in world.call("message.list", account=world.account_id, folder=folder, limit=5000, **values).messages]


def test_a_delete_takes_out_that_one_row_and_asks_for_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    assert watched.subjects() == ["Message 3", "Message 2", "Message 1"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    assert watched.subjects() == ["Message 3", "Message 1"] and watched.asked == [] and watched.resets == 0
    assert watched.model.message_count() == 2


def test_a_mark_changes_that_one_row_and_the_unread_count(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    counts: list[tuple[int, int]] = []
    def counted(total: int, unread: int) -> None:
        counts.append((total, unread))

    watched.model.counts_changed.connect(counted)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=ids(world)["Message 1"], seen=True, flagged=True)
    row = watched.model.row_at(2)
    assert set(getattr(row, "flags")) == {"seen", "flagged"} and counts[-1] == (3, 2) and watched.asked == [] and watched.resets == 0


def test_a_message_moved_into_the_folder_shown_comes_in_at_its_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject in ("Message 3", "Message 1"):
        world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)[subject], target="Kept")
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="subject", oldest_first=True))
    assert watched.subjects() == ["Message 1", "Message 3"]
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"], target="Kept")
    wait(qtbot, lambda: watched.subjects() == ["Message 1", "Message 2", "Message 3"])
    assert watched.subjects() == listed(world, "Kept", sort="subject", oldest_first=True) and watched.resets == 0
    assert "message.list" not in watched.asked  # only where it stands was asked


def test_group_headings_follow_and_an_emptied_heading_goes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="subject", oldest_first=True), groups=True)
    assert watched.headers() == ["Message 1", "Message 2", "Message 3"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    assert watched.headers() == ["Message 1", "Message 3"] and watched.subjects() == ["Message 1", "Message 3"] and watched.resets == 0


def test_favorites_unread_takes_out_a_deleted_message_and_does_not_take_it_back_from_trash(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec("", combined="unread"))
    assert sorted(watched.subjects()) == ["Message 1", "Message 2", "Message 3"]
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 3"])
    assert sorted(watched.subjects()) == ["Message 1", "Message 2"] and watched.asked == []  # Trash is not in Unread: nothing asked


def test_a_search_of_all_folders_keeps_a_message_that_moved_and_puts_it_at_its_new_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, None, query="Message"))
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 2"])
    wait(qtbot, lambda: len(watched.subjects()) == 3 and watched.asked == ["message.places"])  # one question for the messages that came in
    assert watched.subjects() == ["Message 3", "Message 2", "Message 1"]
    assert str(getattr(watched.model.row_at(1), "folder")) == "Trash"


def test_folder_read_emptied_and_renamed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    world.call("folder.mark-read", account=world.account_id, folder="Inbox")
    assert all("seen" in getattr(watched.model.row_at(row), "flags") for row in range(3)) and watched.asked == []
    trash = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Trash"))
    for message_id in ids(world).values():
        world.call("message.delete", account=world.account_id, folder="Inbox", message=message_id)
    wait(qtbot, lambda: len(trash.subjects()) == 3)
    world.call("folder.empty", account=world.account_id, folder="Trash")
    assert trash.subjects() == [] and watched.subjects() == []
    world.call("folder.create", account=world.account_id, folder="Projects")
    projects = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Projects"))
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")
    assert projects.model.spec is not None and projects.model.spec.folder == "Work"


def test_an_answer_asked_before_a_change_is_asked_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "PAGE_SIZE", 1)  # one message per page: the second page is asked separately
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    held: list[Callable[[], None]] = []
    holding_done: list[bool] = []
    passing = world.backend.call

    def holding(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == "message.list" and not holding_done:
            holding_done.append(True)
            answer = world.call(command, **values)
            held.append(lambda: on_success(answer))  # the answer as it was when asked, delivered after a change
            return 0
        return passing(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", holding)
    assert watched.model.row_at(1, fetch=True) is None and held  # the second page is on its way, not answered yet
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 3"])  # the first row goes: places move
    held.pop()()  # the late answer counted the old places
    wait(qtbot, lambda: [watched.model.row_at(row, fetch=True) is not None for row in range(watched.model.rowCount())] == [True, True])
    assert watched.subjects() == ["Message 2", "Message 1"]


def test_a_large_folder_deletes_a_row_without_asking_the_engine_anything(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    store_folder = "Bulk"
    world.call("folder.create", account=world.account_id, folder=store_folder)
    with world.engine.quietly():
        raws = [mail(number, message_id=f"<bulk{number}@x>", subject=f"Bulk {number:04d}") for number in range(3000)]
        from chrono_mail.engine.mailbox import open_mailbox

        with open_mailbox(world.engine, world.account_id) as mailbox:
            for raw in raws:
                mailbox.deliver((store_folder,), raw, frozenset())
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, store_folder))
    assert watched.model.message_count() == 3000
    first = str(getattr(watched.model.row_at(0), "message_id"))
    world.call("message.delete", account=world.account_id, folder=store_folder, message=first)
    assert watched.model.message_count() == 2999 and watched.asked == [] and watched.resets == 0


def test_a_message_that_was_not_loaded_goes_without_a_reset(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "PAGE_SIZE", 1)  # only the first message is loaded
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    assert watched.model.message_count() == 3 and watched.model.row_at(2) is None
    world.call("message.delete", account=world.account_id, folder="Inbox", message=ids(world)["Message 1"])  # the last one: not loaded
    assert watched.model.message_count() == 2 and watched.resets == 0 and watched.asked == []
    wait(qtbot, lambda: watched.model.row_at(1, fetch=True) is not None)
    assert watched.subjects() == ["Message 3", "Message 2"]


def test_messages_that_come_in_at_once_are_placed_with_one_question(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox"))
    for number in (4, 5, 6):
        world.pop.messages[f"uid{number}"] = mail(number)
    world.call("mail.fetch", account=world.account_id)  # three new messages, each told as a fact
    wait(qtbot, lambda: watched.model.message_count() == 6 and world.backend._pending == {})  # noqa: SLF001
    assert watched.asked == ["message.places"] and watched.resets == 0  # one question for all three, no fetch of the view
    assert watched.subjects() == listed(world)


def test_a_message_gone_from_a_part_not_loaded_under_headings_rearranges_once_and_never_resets(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "PAGE_SIZE", 1)  # only the first message is loaded
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Inbox", sort="subject"), groups=True)
    assert watched.model.row_at(watched.model.display_row(2)) is None
    message_ids = ids(world)
    world.call("message.delete", account=world.account_id, folder="Inbox", message=message_ids["Message 3"])
    world.call("message.delete", account=world.account_id, folder="Inbox", message=message_ids["Message 2"])
    wait(qtbot, lambda: watched.model.message_count() == 1 and world.backend._pending == {})  # noqa: SLF001
    assert watched.resets == 0 and watched.asked.count("message.groups") == 1  # the one not loaded: one rearrange, never a reset
    assert watched.headers() == ["Message 1"]
