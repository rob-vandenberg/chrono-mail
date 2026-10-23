from collections.abc import Generator, Mapping
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.models import COUNT_ROLE
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_rapid_actions.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: the folder list's number of quick deletes comes within a tenth of a second: the test waits for it (D-223)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.247: a delete goes as message.delete-many (R-08)
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.158: the folder list's Unread number is right afterwards
# v0.5.157: Initial test (the Windows computer's test made a project test): four quick Delete presses in Favorites > Unread act on the
#           message the person sees selected at each press, at every pace and list size (D-146 step 3)

SPAM: tuple[str, ...] = tuple(f"***SPAM*** Offer {letter}" for letter in "ABCD")


@pytest.fixture
def empty(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False)


def fill(world: World, size: int) -> None:
    """size unread messages in the Inbox: the four spam messages and others after them in subject order."""
    with world.engine.quietly(), open_mailbox(world.engine, world.account_id) as mailbox:
        for number in range(size):
            subject = SPAM[number] if number < len(SPAM) else f"Note {number:05d}"
            mailbox.deliver(("Inbox",), mail(number, subject=subject, message_id=f"<rapid{number}@x>"), frozenset())


def subject_of(world: World, row: object) -> str:
    return str(getattr(row, "subject"))


@pytest.mark.parametrize("size", [16, 600, 3000])
@pytest.mark.parametrize("pause", [0, 200, 1500])
def test_four_quick_deletes_act_on_what_is_selected_at_each_press(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, size: int, pause: int) -> None:
    world, window = empty, empty.window
    fill(world, size)
    window.start()
    wait(qtbot, lambda: bool(window.mail_state.accounts) and world.backend._pending == {})  # noqa: SLF001
    unread = window.folders.find_item(("", "favorite", "unread"))  # noqa: SLF001
    assert unread is not None
    window.tree.setCurrentIndex(unread)
    window.act_show_groups.setChecked(True)
    window.messages.set_sort("subject", True)  # noqa: SLF001 - A to Z: the four spam messages one below the other
    model = window.list_model
    wait(qtbot, lambda: model.spec is not None and model.spec.combined == "unread" and model.spec.sort == "subject" and model.settled and world.backend._pending == {})  # noqa: SLF001
    first = next(row for row in range(model.rowCount()) if not model.is_header(row) and model.row_at(row) is not None)
    assert subject_of(world, model.row_at(first)) == SPAM[0]
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(model.index(first, 0))

    deleted: list[str] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == "message.delete":
            deleted.append(str(values["message"]))
        elif command == "message.delete-many":  # several at once go as one command per folder (R-08)
            messages = values["messages"]
            assert isinstance(messages, (tuple, list))
            deleted.extend(str(message) for message in messages)
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    resets: list[int] = []
    model.modelReset.connect(lambda: resets.append(1))

    seen_at_press: list[str] = []
    for _ in range(4):
        current = window.list_view.currentIndex()
        assert current.isValid(), "nothing is selected: the next Delete would do nothing"
        chosen = model.row_at(current.row())
        assert chosen is not None
        seen_at_press.append(str(getattr(chosen, "message_id")))
        QTest.keyClick(window.list_view, Qt.Key.Key_Delete)
        assert deleted[-1] == seen_at_press[-1], "the Delete acted on another message than the one selected"
        qtbot.wait(pause)

    wait(qtbot, lambda: world.backend._pending == {} and not model.pending, timeout=30000)  # noqa: SLF001
    names = {str(row.message_id): row.subject for row in world.call("message.list", account=world.account_id, folder="Trash", limit=10).messages}
    assert [names.get(message_id) for message_id in deleted] == list(SPAM)  # exactly the four, each once, in the order pressed
    inbox = world.call("message.list", account=world.account_id, folder="Inbox", limit=1)
    assert inbox.total == size - 4  # nothing else moved, nothing made invisible
    unread_favorite = window.tree_model.find_item("", "favorite", "unread")
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - the message shown now may have been marked read
    truth = world.call("message.list", account=world.account_id, folder="Inbox", limit=1).unread
    assert unread_favorite is not None
    wait(qtbot, lambda: (unread_favorite.data(COUNT_ROLE) or 0) == truth)  # the folder list's number followed the facts (within a tenth of a second)
    assert resets == []  # the list was never reset: no blank, no jump
    assert not any("Missing" in title or "missing" in text for _, title, text in world.dialogs.shown)
    current = window.list_view.currentIndex()
    assert current.isValid() and subject_of(world, model.row_at(current.row())).startswith("Note")  # the selection went on to the next message
