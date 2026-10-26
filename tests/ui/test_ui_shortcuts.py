import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QMessageBox, QToolButton
from pytestqt.qtbot import QtBot

from tests.ui.test_ui_window import open_folder, select, started
from tests.ui.world import World, menu_named, row_of, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_shortcuts.py 3.6.369"

# --- Version history ----------------------------------------------------
# v3.6.369: Not junk shows the junk key in the menus (or its own key), in Junk the Message menu shows Not junk only, Ctrl+J there brings it back;
#           Not junk works in Favorites > Junk, greyed with nothing selected and outside Junk
# v2.0.305: Menu > Message > Go to holds the five ways to move to another message or folder; their keys work as before
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.7.262: The one-letter keys and Insert work in the message list and the reading pane, never in the folder list (R-43)
# v1.5.259: Enter and Insert act on the selected messages only while the message list has the focus; typed into the search field they open and flag nothing
# v1.1.181: F1 is the standard key for About Chrono Mail
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.125: The test waits until Mark as unread has finished before it marks the messages read
# v0.4.117: Alt+M opens the Menu of the section that is shown
# v0.0.61: The four section buttons and the sidebars of the calendar, task and contact sections
# v0.0.54: A flag change redraws every column of the row
# v0.0.53: Initial tests of the standard shortcuts

DEFAULT_KEYS: dict[str, str] = {
    "open_menu": "Alt+M",
    "refresh": "F5", "new": "Ctrl+N", "reply": "Ctrl+R", "reply_all": "Ctrl+Shift+R", "forward": "Ctrl+F", "toggle_flag": "Ins", "toggle_read": "R",
    "read": "Ctrl+Q", "unread": "Ctrl+U", "snooze": "S", "copy_to": "Ctrl+Shift+C", "quick_copy": "C", "move_to": "Ctrl+Shift+V", "quick_move": "M",
    "archive": "A", "junk": "Ctrl+J", "print": "Ctrl+P", "delete": "Del", "delete_forever": "Shift+Del", "save_as": "Ctrl+Shift+S", "open": "Return",
    "search": "Ctrl+E", "advanced": "Ctrl+Shift+F", "toggle_folders": "F9", "go_to_folder": "G", "previous_message": "B", "previous_unread": "P",
    "next_message": "F", "next_unread": "N", "about": "F1",
}


def test_the_standard_keys_are_those_people_expect(world: World) -> None:
    window = world.window
    for name, keys in DEFAULT_KEYS.items():
        assert getattr(window, f"act_{name}").shortcut().toString() == keys, name
    taken = [action.shortcut().toString() for name, action in vars(window).items() if name.startswith("act_") and hasattr(action, "shortcut") and action.shortcut().toString()]
    assert len(taken) == len(set(taken))  # no key is given twice


def test_the_message_menu_keeps_moving_to_another_message_in_its_go_to_submenu(world: World) -> None:
    """The owner: Menu > Message was very tall; the five ways to move on went into Go to, and their keys still work."""
    window = world.window
    go_to = menu_named(window, "Message", "Go to")
    expected = [window.act_next_message, window.act_previous_message, window.act_next_unread, window.act_previous_unread, window.act_go_to_folder]
    assert go_to.actions() == expected
    assert not any(action in menu_named(window, "Message").actions() for action in expected)
    assert all(action in window.actions() or action in window.list_view.actions() for action in expected)  # their keys work where they belong (D-217)


def test_a_key_that_is_a_letter_works_only_in_the_message_list_and_the_reading_pane(world: World) -> None:
    window = world.window
    for name in ("archive", "snooze", "toggle_read", "next_unread", "go_to_folder", "quick_move", "toggle_flag"):  # and Insert (R-43)
        action = getattr(window, f"act_{name}")
        assert action.shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut, name
        assert action in window.list_view.actions() and action in window.reader.actions() and action not in window.tree.actions() and action not in window.actions(), name
    for name in ("read", "delete", "refresh", "reply"):
        assert getattr(window, f"act_{name}").shortcutContext() == Qt.ShortcutContext.WindowShortcut, name  # typing in the search box is never taken over
    action = window.act_open  # Enter: only in the message list (1.5.259)
    assert action.shortcutContext() == Qt.ShortcutContext.WidgetShortcut and action in window.list_view.actions() and action not in window.tree.actions()
    window.settings.setValue("shortcuts", '{"archive": "Ctrl+Alt+A"}')
    window._apply_shortcuts()  # noqa: SLF001
    assert window.act_archive.shortcutContext() == Qt.ShortcutContext.WindowShortcut and window.act_archive in window.actions()  # a key with Ctrl works everywhere
    assert window.act_archive not in window.list_view.actions()


def test_toggling_read_and_flag_and_stepping_through_the_list(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.reading.mark_read_timer.stop()  # noqa: SLF001
    select(window, 0)
    before = "seen" in row_of(window, 0).flags
    window.act_toggle_read.trigger()
    wait(qtbot, lambda: ("seen" in row_of(window, 0).flags) is (not before))
    window.act_toggle_read.trigger()
    wait(qtbot, lambda: ("seen" in row_of(window, 0).flags) is before)
    window.act_toggle_flag.trigger()
    wait(qtbot, lambda: "flagged" in row_of(window, 0).flags)
    window.act_toggle_flag.trigger()
    wait(qtbot, lambda: "flagged" not in row_of(window, 0).flags)
    window.act_next_message.trigger()
    assert window.list_view.currentIndex().row() == 1
    window.act_previous_message.trigger()
    window.act_previous_message.trigger()  # at the top: stays
    assert window.list_view.currentIndex().row() == 0


def test_delete_permanently_asks_first_and_copy_leaves_the_original(world: World, qtbot: QtBot) -> None:
    world.call("folder.create", account=world.account_id, folder="Archive")
    started(qtbot, world)
    window = world.window
    window.reading.mark_read_timer.stop()  # noqa: SLF001
    select(window, 0)
    world.dialogs.answer = QMessageBox.StandardButton.No
    window.act_delete_forever.trigger()
    qtbot.wait(200)
    assert window.list_model.rowCount() == 3
    window.mail_actions.copy_selected("Archive")
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Archive").total == 1)
    wait(qtbot, lambda: window.list_model.rowCount() == 3)  # the copy leaves the message where it was
    select(window, 0)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    window.act_delete_forever.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 2)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 0  # not even a stop in Trash


def test_the_folder_menu_and_go_to_folder(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QInputDialog

    world.call("folder.create", account=world.account_id, folder="Archive")
    started(qtbot, world)
    window = world.window
    select(window, 0)
    window.act_quick_move.trigger()
    assert isinstance(window.mail_actions.folder_popup, QMenu)  # noqa: SLF001
    titles = [action.text() for action in window.mail_actions.folder_popup.actions()]  # noqa: SLF001
    assert "Archive" in titles and "Inbox" not in titles and "Outbox" not in titles  # the folder it is in is left out
    asked: list[list[str]] = []

    def fake_get_item(parent: object, title: str, label: str, items: list[str], current: int, editable: bool) -> tuple[str, bool]:
        asked.append(items)
        return "me@example.com / Sent", True

    monkeypatch.setattr(QInputDialog, "getItem", fake_get_item)
    window.act_go_to_folder.trigger()
    assert any(item == "me@example.com / Sent" for item in asked[0])
    wait(qtbot, lambda: window.mail_state.view.folder == "Sent")  # noqa: SLF001
    window.act_toggle_folders.trigger()
    assert window.tree.isHidden()
    window.act_toggle_folders.trigger()
    assert not window.tree.isHidden()


def test_the_composer_and_the_message_window_have_the_same_keys(world: World, qtbot: QtBot) -> None:
    from tests.ui.test_ui_composer import new_composer

    composer = new_composer(world, qtbot)
    keys = {action.text(): [a.shortcut().toString() for a in composer.actions() if a.text() == action.text()] for action in composer.actions()}
    assert keys["Send"] == ["Ctrl+Return", "Ctrl+Enter"] and keys["Close"] == ["Esc"] and keys["Save draft"] == ["Ctrl+S"]
    composer.actions()[[a.text() for a in composer.actions()].index("Close")].trigger()  # Esc on an untouched message closes it
    assert not composer.isVisible()
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_open.trigger()
    wait(qtbot, lambda: len(world.window.reading.message_windows) == 1)  # noqa: SLF001
    window = world.window.reading.message_windows[0]  # noqa: SLF001
    toolbar_keys = {action.text(): action.shortcut().toString() for action in window.findChildren(type(world.window.act_reply)) if action.text() in ("Reply", "Reply all", "Forward")}
    assert toolbar_keys == {"Reply": "Ctrl+R", "Reply all": "Ctrl+Shift+R", "Forward": "Ctrl+F"}
    next(a for a in window.actions() if a.text() == "Close").trigger()
    assert not window.isVisible()


def test_marking_as_read_redraws_every_cell_of_the_selected_rows(world: World, qtbot: QtBot) -> None:
    world.window.settings.setValue("mark_read", "never")  # reading must not change the flags while the test looks at them
    started(qtbot, world)
    window = world.window
    window.reading.mark_read_timer.stop()  # noqa: SLF001
    assert window.list_model.columnCount() > 1  # the table layout: each column of a row is a cell of its own
    changed: list[tuple[int, int, int, int]] = []

    def record(top: QModelIndex, bottom: QModelIndex) -> None:
        changed.append((top.row(), top.column(), bottom.row(), bottom.column()))

    window.list_model.dataChanged.connect(record)
    for row in range(3):
        window.list_view.selectionModel().select(window.list_model.index(row, 0), window.list_view.selectionModel().SelectionFlag.Select | window.list_view.selectionModel().SelectionFlag.Rows)
    window.act_unread.trigger()
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - the messages were unread already: wait for the change itself
    wait(qtbot, lambda: all("seen" not in row_of(window, position).flags for position in range(3)))
    changed.clear()
    window.act_read.trigger()
    wait(qtbot, lambda: all("seen" in row_of(window, position).flags for position in range(3)))
    last = window.list_model.columnCount() - 1
    assert {(top, 0, top, last) for top in range(3)} <= set(changed)  # from the first column to the last, for every row


def test_alt_m_opens_the_menu_of_the_section_that_is_shown(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.show()
    opened: list[object] = []

    def show_menu(button: QToolButton) -> None:
        opened.append(button)

    monkeypatch.setattr(QToolButton, "showMenu", show_menu)  # a real menu would wait for a click
    assert window.act_open_menu in window.actions() and "open_menu" in window._shortcut_registry()  # noqa: SLF001 - changeable in Settings > Keyboard
    window.act_open_menu.trigger()
    assert opened == [window.menu_buttons[0]]  # the mail section: the first toolbar


def test_enter_and_insert_act_on_messages_only_while_the_message_list_has_the_focus(world: World, qtbot: QtBot) -> None:
    """The owner's report (1.5.258, the same on 1.5.257): Enter typed into the search field opened the message selected in the list
    instead of running the search; Insert there flagged it. A key acts on what has the focus, never on a selection elsewhere."""
    started(qtbot, world)
    window = world.window
    window.show()
    window.reading.mark_read_timer.stop()  # noqa: SLF001
    select(window, 0)
    window.search_box.setFocus()
    qtbot.keyClicks(window.search_box, "number 2")
    qtbot.keyClick(window.search_box, Qt.Key.Key_Insert)
    qtbot.keyClick(window.search_box, Qt.Key.Key_Return)
    assert window.mail_state.view.query == "number 2" and window.reading.message_windows == []  # noqa: SLF001 - the search ran, nothing opened
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and world.backend._pending == {})  # noqa: SLF001
    assert "flagged" not in row_of(window, 0).flags
    window.list_view.setFocus()
    select(window, 0)
    qtbot.keyClick(window.list_view, Qt.Key.Key_Insert)
    wait(qtbot, lambda: "flagged" in row_of(window, 0).flags)
    qtbot.keyClick(window.list_view, Qt.Key.Key_Return)
    wait(qtbot, lambda: len(window.reading.message_windows) == 1)  # noqa: SLF001 - in the message list Enter opens the message


def test_not_junk_shows_the_junk_key_and_in_junk_the_message_menu_offers_only_not_junk(world: World, qtbot: QtBot) -> None:
    """The owner: Not junk showed no key, although in Junk the junk key (Ctrl+J) brings a message back to the Inbox."""
    window = started(qtbot, world).window
    assert window.act_not_junk.text() == "Not junk (move to Inbox)\tCtrl+J" and window.act_not_junk.shortcut().isEmpty()  # shown only: the key acts once
    message_menu = menu_named(window, "Message")
    message_menu.aboutToShow.emit()
    actions = message_menu.actions()
    assert window.act_junk in actions and actions.index(window.act_junk) + 1 == actions.index(window.act_not_junk)  # Not junk greyed below it
    select(window, 0)
    window.act_junk.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Junk").total == 1)
    open_folder(world, qtbot, "Junk", 1)
    message_menu.aboutToShow.emit()
    assert window.act_junk not in message_menu.actions() and window.act_not_junk in message_menu.actions()
    window.list_view.setFocus()
    select(window, 0)
    QTest.keyClick(window.list_view, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Junk").total == 0)
    wait(qtbot, lambda: window.list_model.rowCount() == 0)
    assert not window.act_not_junk.isEnabled()  # nothing selected: nothing to bring back
    open_folder(world, qtbot, "Inbox", 3)
    select(window, 0)
    assert not window.act_not_junk.isEnabled()  # outside Junk
    message_menu.aboutToShow.emit()
    actions = message_menu.actions()
    assert window.act_junk in actions and actions.index(window.act_junk) + 1 == actions.index(window.act_not_junk)  # back at its place
    window.settings.setValue("shortcuts", '{"junk": "Ctrl+K"}')
    window._apply_shortcuts()  # noqa: SLF001
    assert window.act_not_junk.text() == "Not junk (move to Inbox)\tCtrl+K"
    window.settings.setValue("shortcuts", '{"not_junk": "Ctrl+Alt+J"}')
    window._apply_shortcuts()  # noqa: SLF001
    assert window.act_not_junk.text() == "Not junk (move to Inbox)" and window.act_not_junk.shortcut().toString() == "Ctrl+Alt+J"  # its own key shows by itself
    assert window._shortcut_registry()["not_junk"][0] == "Not junk (move to Inbox)"  # noqa: SLF001 - Settings > Keyboard names it without a key


def test_not_junk_works_in_favorites_junk_as_the_junk_key_does(world: World, qtbot: QtBot) -> None:
    """A reviewer: in Favorites > Junk, Not junk was greyed although the junk key brought the message back there."""
    window = started(qtbot, world).window
    select(window, 0)
    window.act_junk.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Junk").total == 1)
    favorite = window.folders.find_item(("", "favorite", "junk"))
    assert favorite is not None
    window.tree.setCurrentIndex(favorite)
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and window.mail_state.view.folder_role == "junk")
    assert not window.act_not_junk.isEnabled()  # nothing selected yet
    select(window, 0)
    assert window.act_not_junk.isEnabled()
    window.act_not_junk.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Junk").total == 0 and world.call("message.list", account=world.account_id, folder="Inbox").total == 3)
