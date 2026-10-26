from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.sections import CONTACTS, MAIL
from tests.ui.world import Dialogs, World, build_world, dialogs, mail, settle, wait, world  # noqa: F401 - fixtures


def said(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every text the status line is given from now on (another part may write the line again a moment later, so it is never read at one moment)."""
    texts: list[str] = []
    real_show = window.report.show

    def show(text: str, error: bool = False) -> None:
        texts.append(text)
        real_show(text, error)

    monkeypatch.setattr(window.report, "show", show)
    return texts

# --- Version ------------------------------------------------------------
__version__ = "test_ui_undo.py 3.6.368"

# --- Version history ----------------------------------------------------
# v3.6.368: Delete held down and then Ctrl+Z held down in Junk with headings: every message back once, under its own heading
# v2.3.317: the status line's texts are recorded, never read at one moment (another part may write the line again)
# v2.2.313: Initial (B-18): Ctrl+Z and Ctrl+Y in the message list, Ctrl+Z right after an action, Ctrl+Z in a text field, greyed outside Mail


def started(qtbot: QtBot, world: World) -> MainWindow:
    world.window.start()
    settle(qtbot, world, 3)
    return world.window


def subject_at(window: MainWindow, position: int) -> str:
    row = window.list_model.row_at(position)
    return "" if row is None else row.subject


def held(world: World, folder: str) -> int:
    """How many messages the engine keeps in a folder."""
    return message_list(world.engine, ListParameters(world.account_id, folder)).total


def press(window: MainWindow, key: Qt.Key, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.ControlModifier) -> None:
    focused = window.focusWidget()
    assert focused is not None
    QTest.keyClick(focused, key, modifiers)


def deleted_first(qtbot: QtBot, world: World) -> str:
    """The first message of the Inbox moved to Trash with Delete in the list; its subject."""
    window = started(qtbot, world)
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    subject = subject_at(window, 0)
    press(window, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and window.act_undo.text() == "Undo Move to Trash (1 message)")
    return subject


def test_ctrl_z_brings_the_deleted_message_back_selected_and_ctrl_y_deletes_it_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    subject = deleted_first(qtbot, world)
    texts = said(window, monkeypatch)
    assert window.act_undo.isEnabled() and not window.act_redo.isEnabled()
    press(window, Qt.Key.Key_Z)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and window.act_redo.text() == "Redo Move to Trash (1 message)")
    wait(qtbot, lambda: subject_at(window, window.list_view.currentIndex().row()) == subject)
    assert "Undone: Move to Trash (1 message)" in texts and held(world, "Trash") == 0
    assert window.act_undo.text() == "Undo" and not window.act_undo.isEnabled()
    press(window, Qt.Key.Key_Y)
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and held(world, "Trash") == 1 and window.act_undo.isEnabled())
    press(window, Qt.Key.Key_Z)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and window.act_redo.isEnabled())
    press(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)  # the other key of Redo
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and held(world, "Trash") == 1)


def test_ctrl_z_right_after_delete_waits_for_it_and_undoes_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = started(qtbot, world)
    texts = said(window, monkeypatch)
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    press(window, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    press(window, Qt.Key.Key_Z)  # before the delete is answered
    wait(qtbot, lambda: "Undone: Move to Trash (1 message)" in texts)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and held(world, "Trash") == 0 and held(world, "Inbox") == 3)


def test_ctrl_z_in_the_search_field_undoes_the_typing_and_not_the_delete(world: World, qtbot: QtBot) -> None:
    window = world.window
    deleted_first(qtbot, world)
    window.search_box.setFocus()
    QTest.keyClicks(window.search_box, "abc")
    press(window, Qt.Key.Key_Z)
    qtbot.wait(200)
    assert window.search_box.text() == "" and held(world, "Trash") == 1 and window.list_model.rowCount() == 2


def test_undo_is_greyed_outside_mail_and_settings_name_the_keys_undo_and_redo(world: World, qtbot: QtBot) -> None:
    window = world.window
    deleted_first(qtbot, world)
    window.switch_section(CONTACTS)
    assert not window.act_undo.isEnabled()
    window.switch_section(MAIL)
    assert window.act_undo.isEnabled()
    keys = window._shortcut_registry()  # noqa: SLF001 - what Settings > Keyboard lists
    assert keys["undo"] == ("Undo", "Ctrl+Z") and keys["redo"] == ("Redo", "Ctrl+Y") and keys["redo_other"] == ("Redo (second key)", "Ctrl+Shift+Z")


SPAM: int = 60


@pytest.fixture
def spam(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, {f"uid{number}": mail(number, subject=f"Spam {number:02d}") for number in range(SPAM)})


def shown_messages(window: MainWindow) -> list[tuple[str, str]]:
    """What the list shows from top to bottom, as the view walks its rows: each message's subject with the heading above it."""
    model, view = window.list_model, window.list_view
    shown: list[tuple[str, str]] = []
    heading = ""
    index = model.index(0, 0)
    while index.isValid():
        if model.is_header(index.row()):
            heading = str(model.data(index))
        else:
            row = model.row_at(index.row())
            shown.append((row.subject if row is not None else "?", heading))
        index = view.indexBelow(index)
    return shown


def test_delete_held_down_then_ctrl_z_held_down_brings_every_message_back_once_under_its_own_heading(spam: World, qtbot: QtBot) -> None:
    """A held Ctrl+Z: an Undo between the engine's reading of the order and of the headings put messages under the heading above
    theirs, and the list showed rows twice."""
    window = spam.window
    window.start()
    settle(qtbot, spam, SPAM)
    ids = tuple(row.message_id for row in spam.call("message.list", account=spam.account_id, folder="Inbox", limit=SPAM).messages)
    spam.call("message.move-many", account=spam.account_id, folder="Inbox", target="Junk", messages=ids)
    junk = window.folders.find_item((spam.account_id, "folder", "Junk"))
    assert junk is not None
    window.tree.setCurrentIndex(junk)
    wait(qtbot, lambda: window.list_model.message_count() == SPAM and window.mail_state.view.folder == "Junk")
    window.messages.set_sort("subject", True)
    window.act_show_groups.setChecked(True)
    wait(qtbot, lambda: len(window.list_model.header_rows()) == SPAM)
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(window.list_model.index(1, 0))  # the first message, under the first heading
    for _ in range(20):
        QTest.keyClick(window.list_view, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, 30)  # as a held key repeats
    for _ in range(22):
        QTest.keyClick(window.list_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, 30)
    wanted = [(f"Spam {number:02d}", f"Spam {number:02d}") for number in range(SPAM)]
    wait(qtbot, lambda: held(spam, "Junk") == SPAM and not window.undoing.busy and shown_messages(window) == wanted, timeout=12000)
