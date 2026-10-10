import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from tests.ui.test_ui_window import select, started
from tests.ui.world import World, row_of, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_shortcuts.py 0.0.53"

# --- Version history ----------------------------------------------------
# v0.0.53: Initial tests of the standard shortcuts

DEFAULT_KEYS: dict[str, str] = {
    "refresh": "F5", "new": "Ctrl+N", "reply": "Ctrl+R", "reply_all": "Ctrl+Shift+R", "forward": "Ctrl+F", "toggle_flag": "Ins", "toggle_read": "R",
    "read": "Ctrl+Q", "unread": "Ctrl+U", "snooze": "S", "copy_to": "Ctrl+Shift+C", "quick_copy": "C", "move_to": "Ctrl+Shift+V", "quick_move": "M",
    "archive": "A", "junk": "Ctrl+J", "print": "Ctrl+P", "delete": "Del", "delete_forever": "Shift+Del", "save_as": "Ctrl+Shift+S", "open": "Return",
    "search": "Ctrl+E", "advanced": "Ctrl+Shift+F", "toggle_folders": "F9", "go_to_folder": "G", "previous_message": "B", "previous_unread": "P",
    "next_message": "F", "next_unread": "N",
}


def test_the_standard_keys_are_those_people_expect(world: World) -> None:
    window = world.window
    for name, keys in DEFAULT_KEYS.items():
        assert getattr(window, f"act_{name}").shortcut().toString() == keys, name
    taken = [action.shortcut().toString() for name, action in vars(window).items() if name.startswith("act_") and hasattr(action, "shortcut") and action.shortcut().toString()]
    assert len(taken) == len(set(taken))  # no key is given twice


def test_a_key_that_is_a_letter_works_only_in_the_lists(world: World) -> None:
    window = world.window
    for name in ("archive", "snooze", "toggle_read", "next_unread", "go_to_folder", "quick_move"):
        action = getattr(window, f"act_{name}")
        assert action.shortcutContext() == Qt.ShortcutContext.WidgetShortcut and action in window.list_view.actions() and action in window.tree.actions(), name
    for name in ("read", "delete", "toggle_flag", "refresh", "reply"):
        assert getattr(window, f"act_{name}").shortcutContext() == Qt.ShortcutContext.WindowShortcut, name  # typing in the search box is never taken over
    window.settings.setValue("shortcuts", '{"archive": "Ctrl+Alt+A"}')
    window._apply_shortcuts()  # noqa: SLF001
    assert window.act_archive.shortcutContext() == Qt.ShortcutContext.WindowShortcut  # a key with Ctrl works everywhere


def test_toggling_read_and_flag_and_stepping_through_the_list(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window._mark_read_timer.stop()  # noqa: SLF001
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
    window._mark_read_timer.stop()  # noqa: SLF001
    select(window, 0)
    world.dialogs.answer = QMessageBox.StandardButton.No
    window.act_delete_forever.trigger()
    qtbot.wait(200)
    assert window.list_model.rowCount() == 3
    window.copy_selected("Archive")
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
    assert isinstance(window._folder_popup, QMenu)  # noqa: SLF001
    titles = [action.text() for action in window._folder_popup.actions()]  # noqa: SLF001
    assert "Archive" in titles and "Inbox" not in titles and "Outbox" not in titles  # the folder it is in is left out
    asked: list[list[str]] = []

    def fake_get_item(parent: object, title: str, label: str, items: list[str], current: int, editable: bool) -> tuple[str, bool]:
        asked.append(items)
        return "me@example.com / Sent", True

    monkeypatch.setattr(QInputDialog, "getItem", fake_get_item)
    window.act_go_to_folder.trigger()
    assert any(item == "me@example.com / Sent" for item in asked[0])
    wait(qtbot, lambda: window._folder == "Sent")  # noqa: SLF001
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
    wait(qtbot, lambda: len(world.window._message_windows) == 1)  # noqa: SLF001
    window = world.window._message_windows[0]  # noqa: SLF001
    toolbar_keys = {action.text(): action.shortcut().toString() for action in window.findChildren(type(world.window.act_reply)) if action.text() in ("Reply", "Reply all", "Forward")}
    assert toolbar_keys == {"Reply": "Ctrl+R", "Reply all": "Ctrl+Shift+R", "Forward": "Ctrl+F"}
    next(a for a in window.actions() if a.text() == "Close").trigger()
    assert not window.isVisible()
