from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QKeyEvent
from pytestqt.qtbot import QtBot

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.messagelist import MessageListModel
from chrono_mail.ui.messagelistpane import MessageListPane
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_heldkey.py 3.6.362"

# --- Version history ----------------------------------------------------
# v3.6.362: a held key is pressed as its automatic repeats; the repeats that piled up during one delete are thrown away, four quick
#           separate presses delete four messages (D-266)
# v3.6.360: Initial: a held Delete, Archive or Junk key pauses where the loaded rows end, forgets the presses meanwhile, goes on once the
#           next rows are painted, and stops after the last message with nothing selected; Move to waits for the next rows; rows that
#           cannot be read end the folder there (D-266)

MESSAGES: int = 250


@pytest.fixture
def inbox(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs, monkeypatch: pytest.MonkeyPatch) -> Generator[World]:
    """A POP3 Inbox of 250 messages (no server's word to wait for), its first 100 rows loaded; rows load only when a move asks them, never
    by being painted, so the end of the loaded rows is always met."""
    def no_fetch(self: MessageListModel, position: int) -> None:
        pass

    monkeypatch.setattr(MessageListModel, "_fetch", no_fetch)
    for world in build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False):
        with world.engine.quietly(), open_mailbox(world.engine, world.account_id) as mailbox:
            for number in range(MESSAGES):
                mailbox.deliver(("Inbox",), mail(number, subject=f"Note {number:05d}", message_id=f"<held{number}@x>"), frozenset())
        window = world.window
        window.resize(1200, 800)
        window.start()
        wait(qtbot, lambda: bool(window.mail_state.accounts) and world.backend._pending == {})  # noqa: SLF001
        folder = window.folders.find_item((world.account_id, "folder", "Inbox"))
        assert folder is not None
        window.tree.setCurrentIndex(folder)
        model = window.list_model
        wait(qtbot, lambda: model.spec is not None and model.spec.folder == "Inbox" and model.settled and model.complete and world.backend._pending == {})  # noqa: SLF001
        window.list_view.setFocus()
        yield world


def press(world: World, action: QAction, key: Qt.Key, modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, repeat: bool = True) -> None:
    """A key press as Qt's shortcut handling delivers it: the shortcut override to the focused list first (repeat: the automatic repeat of
    a held key, else a separate press), and the shortcut acts unless the override was taken."""
    override = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifier, "", repeat)
    override.ignore()
    QCoreApplication.sendEvent(world.window.list_view, override)
    if not override.isAccepted():
        action.trigger()


def keys_in_order(model: MessageListModel) -> list[str]:
    return [place[0] for place in (model.place_at(row) for row in range(model.rowCount())) if place is not None]


@pytest.mark.parametrize(("name", "key", "modifier"), [("act_delete", Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
                                                       ("act_archive", Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier),
                                                       ("act_junk", Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)], ids=["delete", "archive", "junk"])
def test_a_held_key_pauses_where_the_rows_end_and_stops_after_the_last(inbox: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, name: str, key: Qt.Key,
                                                                        modifier: Qt.KeyboardModifier) -> None:
    """From message 50 to the end of the folder: at the end of the loaded rows the presses are forgotten until the next 100 are painted
    (twice: at 100 and at 200); after the last message nothing is selected and further presses do nothing; messages 1 to 49 stay (D-266)."""
    window = inbox.window
    model, view, pane = window.list_model, window.list_view, window.messages
    action = {"act_delete": window.act_delete, "act_archive": window.act_archive, "act_junk": window.act_junk}[name]
    pauses: list[int] = []
    pause = getattr(MessageListPane, "_pause", None)  # (also on a version without it: the test then fails on what it sees)

    def counted_pause(self: MessageListPane, keys: list[str]) -> None:
        pauses.append(len(keys))
        if pause is not None:
            pause(self, keys)

    monkeypatch.setattr(MessageListPane, "_pause", counted_pause, raising=False)
    order = keys_in_order(model)
    assert len(order) == MESSAGES and model.loaded_row(order[100]) is None
    view.setCurrentIndex(model.index(model.display_row(49), 0))
    forgotten = 0
    for _ in range(3000):  # the key held: a press, then whatever the event loop has meanwhile
        before = len(keys_in_order(model))
        press(inbox, action, key, modifier)
        if getattr(pane, "_paused", False) and len(keys_in_order(model)) == before:
            forgotten += 1
        QCoreApplication.processEvents()
        if not view.currentIndex().isValid() and not getattr(pane, "_paused", False):
            break
    wait(qtbot, lambda: inbox.backend._pending == {})  # noqa: SLF001
    press(inbox, action, key, modifier)  # nothing is selected: nothing more goes
    wait(qtbot, lambda: inbox.backend._pending == {})  # noqa: SLF001
    assert keys_in_order(model) == order[:49]
    assert not view.currentIndex().isValid() and not view.selectionModel().hasSelection()
    assert pauses == [100, 50] and forgotten >= 2


def test_move_to_on_the_last_loaded_row_waits_for_the_next_rows_and_selects_the_next(inbox: World, qtbot: QtBot) -> None:
    """Move to, chosen in a menu: the next message is loaded and painted first, then the move is done and the next message selected,
    never the one above (D-266)."""
    window = inbox.window
    model, view = window.list_model, window.list_view
    order = keys_in_order(model)
    view.setCurrentIndex(model.index(model.display_row(99), 0))
    window.mail_actions.move_selected("Trash")
    wait(qtbot, lambda: inbox.backend._pending == {} and order[99] not in keys_in_order(model))  # noqa: SLF001
    current = model.place_at(view.currentIndex().row())
    assert current is not None and current[0] == order[100] and current[2] is not None


def test_rows_that_cannot_be_read_end_the_folder_there(inbox: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The next rows could not be read: one pause, then the next press is done and nothing is selected after it; never a pause without
    end at that row (D-266)."""
    window = inbox.window
    model, view, pane = window.list_model, window.list_view, window.messages
    asked: list[int] = []

    def unreadable(self: MessageListModel, keys: list[str], then: Callable[[], None]) -> None:
        asked.append(len(keys))
        then()  # answered, but nothing came (as a failed question)

    monkeypatch.setattr(MessageListModel, "load_rows", unreadable)
    order = keys_in_order(model)
    view.setCurrentIndex(model.index(model.display_row(99), 0))
    press(inbox, window.act_delete, Qt.Key.Key_Delete)  # the pause: forgotten
    wait(qtbot, lambda: not pane._paused)  # noqa: SLF001
    assert order[99] in keys_in_order(model)
    press(inbox, window.act_delete, Qt.Key.Key_Delete)
    wait(qtbot, lambda: inbox.backend._pending == {} and order[99] not in keys_in_order(model))  # noqa: SLF001
    assert asked == [100] and not view.currentIndex().isValid()


def test_repeats_that_piled_up_during_a_delete_are_thrown_away(inbox: World, qtbot: QtBot) -> None:
    """A held key whose repeats came faster than one delete is done: they are thrown away after it, so letting go stops at once (D-266)."""
    window = inbox.window
    model, view = window.list_model, window.list_view
    order = keys_in_order(model)
    view.setCurrentIndex(model.index(model.display_row(10), 0))
    for _ in range(5):  # waiting in the event loop while the first delete is done
        QTimer.singleShot(0, lambda: press(inbox, window.act_delete, Qt.Key.Key_Delete))
    press(inbox, window.act_delete, Qt.Key.Key_Delete)
    qtbot.wait(50)
    wait(qtbot, lambda: inbox.backend._pending == {})  # noqa: SLF001
    assert keys_in_order(model) == order[:10] + order[11:]


def test_four_quick_separate_presses_delete_four_messages(inbox: World, qtbot: QtBot) -> None:
    """Four taps, however fast: four messages (a separate press is never thrown away, D-266)."""
    window = inbox.window
    model, view = window.list_model, window.list_view
    order = keys_in_order(model)
    view.setCurrentIndex(model.index(model.display_row(10), 0))
    for _ in range(3):
        QTimer.singleShot(0, lambda: press(inbox, window.act_delete, Qt.Key.Key_Delete, repeat=False))
    press(inbox, window.act_delete, Qt.Key.Key_Delete, repeat=False)
    qtbot.wait(50)
    wait(qtbot, lambda: inbox.backend._pending == {})  # noqa: SLF001
    assert keys_in_order(model) == order[:10] + order[14:]
