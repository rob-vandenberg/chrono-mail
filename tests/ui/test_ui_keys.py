import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.undohistory import UndoAction, UndoStep
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import FOLDER_PATH_ROLE, KIND_ROLE
from chrono_mail.ui.sections import CONTACTS
from tests.ui.test_ui_window import started
from tests.ui.world import World, dialogs, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_keys.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: Undo and Redo (B-18): window-wide in Mail and in the read-only text of a message, never in a text field, whose own undo they are
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.7.262: Initial (R-43): every key of the window pressed in every part (the folder list, the message list, the search field, the reading
#           pane, a section's sidebar) starts the command of that part, or nothing: never one that acts on a selection elsewhere (D-217)

NO_TARGET = {"new", "refresh", "settings", "open_menu", "search", "advanced", "toggle_folders", "about", "section_mail", "section_calendar", "section_contacts", "section_tasks"}
MESSAGE_COMMANDS = {"reply", "reply_all", "forward", "save_as", "source", "print", "junk", "read", "unread", "move_to", "copy_to"}  # can only mean messages (D-217)
UNDO_KEYS = {"undo", "redo", "redo_other"}  # greyed until there is something to undo; a text field keeps them for its typing (B-18)
MESSAGE_KEYS = {"archive", "snooze", "toggle_read", "quick_move", "quick_copy", "next_unread", "previous_unread", "next_message", "previous_message", "go_to_folder", "toggle_flag"}


def watched(window: MainWindow) -> tuple[dict[str, QAction], list[str]]:
    """Every action with a key, its work replaced by a note of its name (nothing is done, no window opens)."""
    fired: list[str] = []
    actions = {name[4:]: action for name, action in vars(window).items() if name.startswith("act_") and isinstance(action, QAction) and not action.shortcut().isEmpty()}
    for name, action in actions.items():
        try:
            action.triggered.disconnect()
        except RuntimeError:
            pass
        action.triggered.connect(lambda checked=False, chosen=name: fired.append(chosen))
    return actions, fired


def something_to_undo(qtbot: QtBot, world: World) -> None:
    """The engine has an action to undo and one to redo, so Undo and Redo are usable (their work is replaced by watched)."""
    history = world.engine.undo
    step = UndoStep(world.account_id, "0" * 32, ("Inbox",), ("Trash",))
    history.record(1, "Move to Trash", [step])
    undone = history.take(redo=False)
    assert undone is not None
    history.put(undone, redo=True)
    history.put(UndoAction(2, "Move to Trash", [step]), redo=False)
    world.window.undoing.refresh()
    wait(qtbot, lambda: world.window.act_undo.isEnabled() and world.window.act_redo.isEnabled())


def press(qtbot: QtBot, window: MainWindow, part: QWidget, sequence: QKeySequence) -> None:
    part.setFocus()
    qtbot.wait(20)
    focused = window.focusWidget()
    assert focused is not None
    QTest.keySequence(focused, sequence)
    qtbot.wait(20)


def fired_by(qtbot: QtBot, window: MainWindow, part: QWidget, actions: dict[str, QAction], fired: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, action in actions.items():
        fired.clear()
        press(qtbot, window, part, action.shortcut())
        found[name] = list(fired)
    return found


def test_every_key_in_the_message_list_and_the_reading_pane_acts_on_the_messages(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    wait(qtbot, lambda: window.reader.text_view.toPlainText() != "")
    something_to_undo(qtbot, world)
    actions, fired = watched(window)
    in_list = fired_by(qtbot, window, window.list_view, actions, fired)
    assert all(in_list[name] == [name] for name in actions if name != "rename_folder"), in_list  # every key, F2 aside
    assert in_list["rename_folder"] == []
    in_text = fired_by(qtbot, window, window.reader.text_view, actions, fired)  # clicked into the message
    for name in actions:
        assert in_text[name] == ([] if name in ("open", "rename_folder") else [name]), (name, in_text[name])  # Enter follows a link there


def test_in_the_folder_list_no_key_acts_on_the_messages_and_delete_is_the_folders(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Projects")
    started(qtbot, world)
    window = world.window
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))  # messages selected in the list (grey once the folder list has the focus)
    something_to_undo(qtbot, world)
    actions, fired = watched(window)
    folders: list[str] = []
    monkeypatch.setattr(window.folders, "delete_folder", lambda: folders.append(str(window.mail_state.view.folder)))  # noqa: SLF001
    in_tree = fired_by(qtbot, window, window.tree, actions, fired)
    for name in actions:
        expected: list[str] = [name] if name in NO_TARGET | MESSAGE_COMMANDS | UNDO_KEYS | {"rename_folder"} else []
        assert in_tree[name] == expected, (name, in_tree[name])  # before: Delete trashed the messages, Shift+Delete deleted them for good, the letters acted on them
    assert folders == []  # the Inbox is a standard folder: Delete there does nothing
    projects = window.tree_model.find_item(world.account_id, "folder", "Projects")
    assert projects is not None
    window.tree.setCurrentIndex(projects.index())
    wait(qtbot, lambda: window.mail_state.view.folder == "Projects")  # noqa: SLF001
    for key in (Qt.Key.Key_Delete,):
        press(qtbot, window, window.tree, QKeySequence(key))
    press(qtbot, window, window.tree, QKeySequence("Shift+Delete"))
    assert folders == ["Projects", "Projects"] and "delete" not in fired and "delete_forever" not in fired


def test_a_letter_in_the_folder_list_jumps_to_a_folder(world: World, qtbot: QtBot) -> None:
    world.call("folder.create", account=world.account_id, folder="Projects")
    started(qtbot, world)
    window = world.window
    inbox = window.tree.currentIndex()
    window.tree.setFocus()
    QTest.keyClick(window.tree, Qt.Key.Key_P)
    wait(qtbot, lambda: window.tree.currentIndex() != inbox)
    assert window.tree.currentIndex().data(FOLDER_PATH_ROLE) == "Projects" and window.tree.currentIndex().data(KIND_ROLE) == "folder"


def test_in_the_search_field_the_keys_of_text_are_the_fields(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    something_to_undo(qtbot, world)
    actions, fired = watched(window)
    in_field = fired_by(qtbot, window, window.search_box, actions, fired)
    for name in MESSAGE_KEYS | UNDO_KEYS | {"open", "delete", "delete_forever", "rename_folder"}:
        assert in_field[name] == [], (name, in_field[name])  # letters are typed, Delete deletes text, Enter searches, Ctrl+Z undoes the typing
    for name in NO_TARGET | {"reply", "forward"}:
        assert in_field[name] == [name], name


def test_delete_in_a_sections_sidebar_is_the_calendar_or_address_books(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    window.switch_section(CONTACTS)
    page = window.pages[CONTACTS]
    items: list[str] = []
    collections: list[str] = []
    monkeypatch.setattr(page, "delete_selected", lambda: items.append("items"))
    monkeypatch.setattr(page.panel, "delete_collection", lambda collection: collections.append(collection))
    wait(qtbot, lambda: page.panel.tree.topLevelItemCount() > 0)
    tree = page.panel.tree
    entry = next((tree.topLevelItem(row).child(column) for row in range(tree.topLevelItemCount()) for column in range(tree.topLevelItem(row).childCount())), None)
    assert entry is not None
    tree.setCurrentItem(entry)
    press(qtbot, window, tree, QKeySequence(Qt.Key.Key_Delete))
    assert items == [] and len(collections) == 1  # before: the contacts selected in the list were deleted
    tree.setCurrentItem(tree.topLevelItem(0))  # a heading: nothing to delete
    press(qtbot, window, tree, QKeySequence(Qt.Key.Key_Delete))
    assert items == [] and len(collections) == 1
    window.tree.setCurrentIndex(QModelIndex())
