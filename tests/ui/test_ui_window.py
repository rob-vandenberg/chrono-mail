import base64
from collections.abc import Generator, Mapping
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QObject, QPoint, QPointF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDragLeaveEvent, QDragMoveEvent, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLabel, QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.engine.commands_sidebar import FAVORITE_PIN, PinParameters
from chrono_mail.ui.sections import CALENDAR, MAIL
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.columnsdialog import ListChoice
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import COUNT_ROLE, FOLDER_PATH_ROLE, KIND_ROLE
from chrono_mail.ui.messagelist import GROUP_HEADER_ROLE, ROW_ROLE
from tests.ui.world import account_node, Dialogs, World, answer_input, answer_save_file, build_world, mail, record_opened_urls, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_window.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: the counts in the texts are written one way ("1 message", "3 messages"), never "message(s)"
# v2.2.313: the commands of a move counted without the question what Undo would do (mail.undo-labels, B-18)
# v2.1.310: a group's account starts with Inbox, then Unread (the order of D-232 since 2.0.298)
# v2.0.306: Archive or Unarchive follows the selected messages, not the folder on the left (the owner's search of all folders, D-101)
# v2.0.302: an attachment is saved through its right-click menu's Save as… (a click only selects it, D-236)
# v2.0.300: the Favorites menu lists Snoozed, Scheduled and Archive only once a mailbox has that folder (D-234)
# v2.0.299: a right-click in Favorites lists every standard favorite with a tick, a click shows or hides it (D-234)
# v2.0.298: Favorites and the folders under an account in the owner's new order (D-061, D-232)
# v2.0.292: Favorites and the folders under an account in the owner's order (D-061, D-232; the Windows computer's report)
# v2.0.280: the sort is remembered per folder (B-16)
# v1.9.276: The count line says "1 message"
# v1.9.270: The sidebar changes are typed calls (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: The list's roles and model from messagelist.py (R-15)
# v1.5.256: text being typed is never searched by a sort or a filter; quick filter clicks and a folder change leave the buttons and the list agreeing; the window blocks no signals (R-14)
# v1.1.247: several messages go as one command per folder; a message that could not be moved comes back, with one line for all that failed (R-08)
# v1.1.193: the pinned folder after nine standard favorites; no New group on a favorite
# v1.1.191: Favorites in the owner's order with Unreplied (Snoozed and Archive only once used)
# v0.5.167: This folder / All folders is a button (set_current_index)
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.159: the delete test waits for the status text (the row goes with the fact, the text comes with the answer just after)
# v0.5.139: a reload keeps the old rows until the new ones arrive: the unread test waits for the new ones
# v0.5.135: the Filter menu has Tagged with
# v0.5.125: Mark all as read: the test waits until the list is shown again
# v0.2.104: The drag cursors are the desktop's (tested in test_ui_dragdrop.py)
# v0.2.103: The indent of a selected folder keeps the list colour; the drop bar; the drag cursors
# v0.2.102: Unarchive in the Archive folder; the Junk items say where they move to
# v0.2.99: After a delete the next message is selected (or the selection stays); F2 renames the folder
# v0.2.97: The choice of the Message list window carries the row height
# v0.2.96: The card layout fills the list; View > Message list; one row height only in the column layout; Message list… only in the column header menu
# v0.2.94: Group headers in the window; sorting by To; a search follows the sort order
# v0.1.91: Clear filter and bold filters in the Filter menu
# v0.1.90: The Filter button and its menu
# v0.0.87: The folder list draws the unread count itself; the labels add it from COUNT_ROLE
# v0.0.86: Message buttons are greyed out while no message is selected
# v0.0.85: The window title names the open folder and its unread messages
# v0.0.77: Received instead of Date
# v0.0.53: Standard keyboard shortcuts
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.34: Junk and unread navigation
# v0.0.26: Version number in the About dialog
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.17: Message actions: archive, mark all as read, empty folder, drag and drop
# v0.0.16: Drafts
# v0.0.13: Initial main window tests

SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")


def started(qtbot: QtBot, world: World, rows: int = 3) -> World:
    world.window.start()
    settle(qtbot, world, rows)
    return world


def subjects(window: MainWindow) -> list[str]:
    model = window.list_model
    return [model.index(row, 0).data(ROW_ROLE).subject for row in range(model.rowCount())]


def select(window: MainWindow, row: int) -> None:
    window.list_view.setCurrentIndex(window.list_model.index(row, 0))


def tree_labels(window: MainWindow) -> list[str]:
    labels: list[str] = []

    def walk(parent: QModelIndex, depth: int) -> None:
        for row in range(window.tree_model.rowCount(parent)):
            index = window.tree_model.index(row, 0, parent)
            count = int(index.data(COUNT_ROLE) or 0)
            labels.append("  " * depth + str(index.data()) + (f"  ({count})" if count else ""))
            walk(index, depth + 1)

    walk(QModelIndex(), 0)
    return labels


def seen_count(world: World) -> int:
    """How many messages of the Inbox are marked as read, according to the engine."""
    return sum(1 for row in world.call("message.list", account=world.account_id).messages if "seen" in row.flags)


def flag_sets(world: World) -> list[tuple[str, ...]]:
    return [tuple(row.flags) for row in world.call("message.list", account=world.account_id).messages]


@pytest.fixture
def empty_world(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, with_account=False)


# --- first start -------------------------------------------------------------------------------------------------


def test_first_start_without_an_account_asks_for_one(empty_world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    welcome: list[str] = []

    def fake_exec(self: AccountDialog) -> int:
        welcome.append(" ".join(label.text() for label in self.findChildren(QLabel)))
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(AccountDialog, "exec", fake_exec)
    empty_world.window.start()
    qtbot.waitUntil(lambda: bool(welcome), timeout=5000)
    assert "Welcome to Chrono Mail" in welcome[0]
    assert tree_labels(empty_world.window) == []


# --- folders and the list ------------------------------------------------------------------------------------------


def test_tree_shows_the_account_and_its_folders_with_unread_counts(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    assert tree_labels(world.window) == ["Favorites", "  All Inboxes  (3)", "  Unread  (3)", "  Outbox", "  Sent", "  Drafts", "  Flagged", "  Unreplied", "  Junk", "  Trash", "me@example.com", "  Inbox  (3)", "  Unread", "  Outbox", "  Sent", "  Drafts", "  Flagged", "  Junk", "  Trash"]  # the owner's order (D-061, D-232)
    assert world.window.count_label.text() == "3 messages, 3 unread"
    assert world.window.sort_button.text() == "Sorted by Received ▾"
    assert world.window.windowTitle() == "Inbox (3) - Chrono Mail"


def test_message_buttons_are_greyed_out_while_no_message_is_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.list_view.clearSelection()
    message_buttons = [window.act_reply, window.act_reply_all, window.act_forward, window.act_archive, window.act_snooze, window.act_delete, window.act_open]
    assert not any(action.isEnabled() for action in message_buttons) and not window.mark_button.isEnabled() and not window.move_button.isEnabled()
    assert window.act_new.isEnabled() and window.act_refresh.isEnabled()
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    assert all(action.isEnabled() for action in message_buttons) and window.mark_button.isEnabled() and window.move_button.isEnabled()
    window.list_view.clearSelection()
    window.switch_section(CALENDAR)
    assert window.act_delete.isEnabled()  # Delete of the calendar
    window.switch_section(MAIL)
    assert not window.act_reply.isEnabled() and not window.act_delete.isEnabled()


def test_the_window_title_follows_the_open_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    sent = world.window.folders.find_item((world.account_id, "folder", "Sent"))  # noqa: SLF001
    assert sent is not None
    world.window.tree.setCurrentIndex(sent)
    wait(qtbot, lambda: world.window.mail_state.view.folder == "Sent")  # noqa: SLF001
    assert world.window.windowTitle() == "Sent - Chrono Mail"


def test_the_inbox_is_selected_at_the_start(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    assert world.window.tree.currentIndex().data() == "Inbox" and world.window.tree.currentIndex().data(COUNT_ROLE) == 3
    assert world.window.mail_state.view.folder == "Inbox" and world.window.mail_state.view.account == world.account_id  # noqa: SLF001


def test_opening_a_message_shows_it_and_then_marks_it_read(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    wait(qtbot, lambda: "Body of message" in world.window.reader.text_view.toPlainText())
    assert world.window.reader.isVisible()
    wait(qtbot, lambda: world.window.count_label.text() == "3 messages, 2 unread" or "2 unread" in world.window.count_label.text(), timeout=8000)
    wait(qtbot, lambda: any(label.startswith("  Inbox  (2)") for label in tree_labels(world.window)))
    assert "seen" in row_of(world.window, 0).flags
    assert seen_count(world) == 1


def test_switching_quickly_between_messages_shows_the_last_one(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    for row in (0, 1, 2, 1):
        select(world.window, row)
    wait(qtbot, lambda: world.window.reading.shown is not None)  # noqa: SLF001
    expected = row_of(world.window, 1).message_id
    wait(qtbot, lambda: world.window.reading.shown is not None and world.window.reading.shown[2] == expected)  # noqa: SLF001
    wait(qtbot, lambda: "Message 2" in world.window.reader._subject.text())  # noqa: SLF001


def test_a_big_folder_is_loaded_page_by_page(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    messages = {f"u{n:03d}": mail(n) for n in range(250)}
    for world in build_world(tmp_path, qtbot, dialogs, messages=messages):
        world.window.start()
        wait(qtbot, lambda: world.window.list_model.rowCount() == 250)
        model = world.window.list_model
        assert model.row_at(0) is not None and model.row_at(99) is not None
        assert model.row_at(150) is None and model.row_at(249) is None  # pages 1 and 2 have not been asked for
        assert model.index(150, 0).data(ROW_ROLE) is None  # asking starts the fetch
        wait(qtbot, lambda: model.row_at(150) is not None)
        assert model.row_at(249) is None
        wait(qtbot, lambda: model.index(249, 0).data(ROW_ROLE) is not None)
        assert model.row_at(249) is not None
        assert len({row_of(world.window, row).message_id for row in (0, 99, 150, 249)}) == 4
        world.window.list_view.scrollToBottom()
        qtbot.wait(100)


def message_subjects(window: MainWindow) -> list[str]:
    """The subjects of the message rows, without the group headers."""
    model = window.list_model
    return [str(getattr(model.row_at(row), "subject")) for row in range(model.rowCount()) if not model.is_header(row) and model.row_at(row) is not None]


def test_group_headers_fold_with_a_click_and_the_arrows_pass_them(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.act_show_groups.setChecked(True)
    assert window.settings.value("show_groups") in (True, "true")
    window.messages.set_sort("subject", True)  # noqa: SLF001
    wait(qtbot, lambda: window.list_model.rowCount() == 6 and window.list_model.row_at(5) is not None)
    model = window.list_model
    assert model.header_rows() == [0, 2, 4] and model.index(0, 0).data(GROUP_HEADER_ROLE) == ("Message 1", False)
    assert all(window.list_view.isFirstColumnSpanned(row, QModelIndex()) for row in (0, 2, 4))
    window.list_view.setCurrentIndex(model.index(1, 0))
    window.messages.step_message(1)  # noqa: SLF001
    assert window.list_view.currentIndex().row() == 3  # the header at row 2 is passed
    window.messages.step_message(-1)  # noqa: SLF001
    assert window.list_view.currentIndex().row() == 1
    rect = window.list_view.visualRect(model.index(2, 0))
    qtbot.mouseClick(window.list_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert model.rowCount() == 5 and model.index(2, 0).data(GROUP_HEADER_ROLE) == ("Message 2", True)
    assert message_subjects(window) == ["Message 1", "Message 3"]
    window.act_show_groups.setChecked(False)
    wait(qtbot, lambda: model.rowCount() == 3 and not model.header_rows())


def test_sorting_by_to_and_a_search_follows_the_sort(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    assert "To" in [action.text() for action in window.sort_menu.actions()]
    window.messages.set_sort("to", False)  # noqa: SLF001
    assert window.sort_button.text() == "Sorted by To ▾" and window.messages.view_spec() is not None and getattr(window.messages.view_spec(), "sort") == "to"
    wait(qtbot, lambda: len(message_subjects(window)) == 3)
    window.messages.set_sort("subject", True)  # noqa: SLF001
    window.search_box.setText("Message")
    window.search_box.returnPressed.emit()
    wait(qtbot, lambda: message_subjects(window) == ["Message 1", "Message 2", "Message 3"])
    window.messages.set_sort("subject", False)  # noqa: SLF001
    wait(qtbot, lambda: message_subjects(window) == ["Message 3", "Message 2", "Message 1"])


def test_the_card_layout_fills_the_list(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.resize(1000, 600)
    assert window.layout_actions["columns"].isChecked() and not window.layout_actions["cards"].isChecked()
    view_menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "&View")
    assert window.act_columns not in view_menu.actions()  # Message list… is in the menu of the column headers, not in View
    shown: list[QAction] = []

    def read_and_close() -> None:
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            shown.extend(popup.actions())
            popup.close()

    QTimer.singleShot(200, read_and_close)
    window.columns.header_menu(QPoint(10, 5))  # noqa: SLF001 - the menu of the column headers; the timer closes it
    assert window.act_columns in shown
    window.layout_actions["cards"].trigger()  # View > Message list > Cards
    assert window.layout_actions["cards"].isChecked() and not window.layout_actions["columns"].isChecked()
    assert window.settings.value("list_layout") == "cards"
    assert not window.list_view.uniformRowHeights()  # group headers are lower than cards
    wait(qtbot, lambda: window.list_view.columnWidth(0) == window.list_view.viewport().width() > 100)
    window.resize(1300, 600)
    wait(qtbot, lambda: window.list_view.columnWidth(0) == window.list_view.viewport().width())
    window.apply_list_choice(ListChoice("columns", window.columns.columns, window.reading.place, window.columns.date_style, "normal"))  # noqa: SLF001 - the Message list window
    assert window.layout_actions["columns"].isChecked() and not window.layout_actions["cards"].isChecked()
    assert window.list_view.uniformRowHeights()  # the column layout stays fast with any number of messages


def test_sorting_changes_the_order(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.messages.set_sort("subject", True)  # noqa: SLF001
    wait(qtbot, lambda: subjects(world.window) == ["Message 1", "Message 2", "Message 3"])
    assert world.window.sort_button.text() == "Sorted by Subject ▾"
    world.window.messages.set_sort("subject", False)  # noqa: SLF001
    wait(qtbot, lambda: subjects(world.window) == ["Message 3", "Message 2", "Message 1"])


def test_quick_filters(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    first = row_of(world.window, 0)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, seen=True, flagged=True)
    world.window.list_model.reload()
    settle(qtbot, world, 3)
    world.window.filters.unread.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and world.window.count_label.text().startswith("2"))
    world.window.filters.unread.trigger()
    world.window.filters.flagged.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert subjects(world.window) == [first.subject]
    world.window.filters.flagged.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)


def test_text_being_typed_is_never_searched_by_a_sort_or_a_filter(world: World, qtbot: QtBot) -> None:
    """R-14: the window read the search box whenever the list asked again, so half-typed words were searched by a sort or a filter
    click while the search field did not look active."""
    started(qtbot, world)
    window = world.window
    window.search_box.setText("number 2")  # typed, Enter not pressed
    window.messages.set_sort("subject", False)  # noqa: SLF001
    window.filters.attachments.trigger()
    window.filters.attachments.trigger()
    wait(qtbot, lambda: world.backend._pending == {} and window.list_model.spec is not None and window.list_model.spec.sort == "subject")  # noqa: SLF001
    spec = window.messages.view_spec()
    assert spec is not None and spec.query == "" and window.list_model.spec == spec and window.list_model.rowCount() == 3
    window.search_box.returnPressed.emit()
    wait(qtbot, lambda: subjects(window) == ["Message 2"])
    assert window.mail_state.view.query == "number 2"  # noqa: SLF001


def test_quick_filter_clicks_and_a_folder_change_leave_the_buttons_and_the_list_agreeing(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    for entry in (window.filters.unread, window.filters.flagged, window.filters.unread, window.filters.attachments, window.filters.flagged):
        entry.trigger()
    spec = window.messages.view_spec()
    assert spec is not None and window.list_model.spec == spec and (spec.unread, spec.flagged, spec.has_attachments) == (None, None, True)
    assert [entry.isChecked() for entry in (window.filters.unread, window.filters.flagged, window.filters.attachments)] == [False, False, True]
    open_folder(world, qtbot, "Sent", 0)
    spec = window.messages.view_spec()
    assert spec is not None and window.list_model.spec == spec and spec.has_attachments is None and not window.filters.button.isChecked()
    assert not any(entry.isChecked() for entry in (window.filters.unread, window.filters.flagged, window.filters.attachments))


def test_the_window_never_switches_its_widgets_signals_off() -> None:
    """R-14: the window shows its one current view in the widgets, and they answer only the person: nothing to keep quiet."""
    source = Path(MainWindow.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[2] / "src" / source).read_text(encoding="utf-8")
    assert "blockSignals" not in text


def test_filter_button_menu(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    settle(qtbot, world, 3)
    button = world.window.filters.button
    assert button.text() == "Filter" and button.menu() is world.window.filters.menu
    entries = world.window.filters.menu.actions()
    assert [entry.text() for entry in entries] == ["Filter by", "Flagged", "Unread", "With attachments", "Tagged with", "", "Clear filter"]
    assert not entries[0].isEnabled() and all(entry.isCheckable() for entry in entries[1:4])
    assert entries[4].menu() is world.window.filters.tag_menu and entries[5].isSeparator() and entries[6] is world.window.filters.clear
    assert not button.isChecked() and not world.window.filters.clear.isEnabled()
    world.window.filters.unread.trigger()
    assert button.isChecked() and world.window.filters.clear.isEnabled()
    assert world.window.filters.unread.font().bold() and not world.window.filters.flagged.font().bold()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.window.messages.view_spec() is not None)
    spec = world.window.messages.view_spec()
    assert spec is not None and spec.unread
    world.window.filters.unread.trigger()
    assert not button.isChecked() and not world.window.filters.unread.font().bold()
    world.window.filters.unread.trigger()
    world.window.filters.flagged.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)
    world.window.filters.clear.trigger()
    assert not any(entry.isChecked() for entry in entries[1:4]) and not button.isChecked()
    assert not world.window.filters.clear.isEnabled()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)


def test_the_tree_has_unread_and_flagged_views(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    first = row_of(world.window, 0)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, flagged=True)
    for row in range(world.window.tree_model.rowCount(account_node(world.window))):
        index = world.window.tree_model.index(row, 0, account_node(world.window))
        if index.data(KIND_ROLE) == "flagged":
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.mail_state.view.folder is None  # noqa: SLF001 - a view across the folders


def test_search_in_the_folder_and_in_all_folders(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.search_box.setText("number 2")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: subjects(world.window) == ["Message 2"])
    world.window.search_box.clear()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.call("message.move", account=world.account_id, folder="Inbox", message=row_of(world.window, 0).message_id, target="Junk")
    world.window.search_box.setText("Message")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    world.window.scope.set_current_index(1)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.search_box.setText("zzzzzz")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)


# --- actions on messages ----------------------------------------------------------------------------------------------


def test_mark_flag_and_unflag(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 1)
    world.window.act_flag.trigger()
    wait(qtbot, lambda: "flagged" in row_of(world.window, 1).flags)
    world.window.act_read.trigger()
    wait(qtbot, lambda: "seen" in row_of(world.window, 1).flags)
    world.window.act_unread.trigger()
    wait(qtbot, lambda: "seen" not in row_of(world.window, 1).flags)
    world.window.act_unflag.trigger()
    wait(qtbot, lambda: "flagged" not in row_of(world.window, 1).flags)
    assert flag_sets(world).count(()) == 3


def test_delete_moves_to_trash_and_deleting_in_trash_asks_first(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    wait(qtbot, lambda: world.window.status_label.text() == "Moved 1 message to Trash")  # the row goes with the fact, the text comes with the answer just after
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 1
    trash = next(world.window.folders.find_item((world.account_id, "folder", "Trash")) for _ in [0])  # noqa: SLF001
    assert trash is not None
    world.window.tree.setCurrentIndex(trash)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1 and world.window.mail_state.view.folder == "Trash")  # noqa: SLF001
    select(world.window, 0)
    world.dialogs.answer = QMessageBox.StandardButton.No
    world.window.act_delete.trigger()
    qtbot.wait(300)
    assert world.window.list_model.rowCount() == 1 and any("for good" in text for _, _, text in world.dialogs.shown)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 0


def test_delete_several_messages_at_once(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.list_view.selectAll()
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 3
    assert world.window.reader._header.isHidden()  # noqa: SLF001


def message_id_at(window: MainWindow, row: int) -> str:
    return str(getattr(window.list_model.row_at(row), "message_id"))


def current_message_id(window: MainWindow) -> str | None:
    index = window.list_view.currentIndex()
    row = window.list_model.row_at(index.row()) if index.isValid() else None
    return None if row is None else str(getattr(row, "message_id"))


def test_after_a_delete_the_next_message_is_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    last = message_id_at(window, 2)
    select(window, 1)
    window.act_delete.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and current_message_id(window) == last)
    assert window.list_view.currentIndex().row() == 1 and len(window.list_view.selectionModel().selectedRows()) == 1


@pytest.mark.parametrize("how", ["move", "archive", "junk", "snooze", "drag"])
def test_move_archive_junk_and_snooze_select_the_next_message(world: World, qtbot: QtBot, how: str) -> None:
    started(qtbot, world)
    window = world.window
    last, middle = message_id_at(window, 2), message_id_at(window, 1)
    select(window, 1)
    if how == "move":
        window.mail_actions.move_selected("Trash")
    elif how == "archive":
        window.mail_actions.archive_selected()
    elif how == "junk":
        window.mail_actions.junk_selected(True)
    elif how == "snooze":
        window.mail_actions.snooze_until("2027-06-01T09:00")  # at most five years ahead
    else:
        window.mail_actions.messages_dropped(world.account_id, "Trash", [("Inbox", middle)])  # noqa: SLF001 - a drag onto a folder
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and current_message_id(window) == last)


def test_in_the_archive_folder_archive_is_unarchive(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    assert window.act_archive.text() == "Archive" and window.act_junk.text() == "Move to Junk" and window.act_not_junk.text() == "Not junk (move to Inbox)"
    target = message_id_at(window, 0)
    select(window, 0)
    window.act_archive.trigger()
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Archive")) is not None)  # noqa: SLF001
    archive = window.folders.find_item((world.account_id, "folder", "Archive"))  # noqa: SLF001
    assert archive is not None
    window.tree.setCurrentIndex(archive)
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and window.list_model.row_at(0) is not None)
    select(window, 0)
    assert window.act_archive.text() == "Unarchive"  # the selected message is archived (D-101)
    window.act_archive.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 0)
    assert target in {row.message_id for row in world.call("message.list", account=world.account_id, folder="Inbox").messages}


def test_the_selection_of_a_folder_starts_at_its_icon(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    sent = window.folders.find_item((world.account_id, "folder", "Sent"))  # noqa: SLF001
    assert sent is not None
    window.tree.setCurrentIndex(sent)
    window.tree.setFocus()
    qtbot.wait(100)
    rect = window.tree.visualRect(sent)
    image = window.tree.viewport().grab().toImage()
    background = window.tree.palette().color(QPalette.ColorRole.Window).name()
    assert image.pixelColor(rect.left() - 6, rect.center().y()).name() == background  # the indent: the colour of the list
    assert image.pixelColor(rect.left() + 6, rect.center().y()).name() != background  # the folder itself: selected


def test_a_folder_that_can_take_a_drop_gets_a_bar_across_the_row(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    data = window.list_model.mimeData([window.list_model.index(0, 0)])

    def drag_over(folder: str) -> None:
        item = window.folders.find_item((world.account_id, "folder", folder))  # noqa: SLF001
        assert item is not None
        point = QPointF(window.tree.visualRect(item).center())
        window.tree.dragMoveEvent(QDragMoveEvent(point, Qt.DropAction.MoveAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    drag_over("Trash")
    assert window.tree.drop_row is not None and window.tree.drop_row.data(FOLDER_PATH_ROLE) == "Trash"
    qtbot.wait(50)
    trash = window.folders.find_item((world.account_id, "folder", "Trash"))  # noqa: SLF001
    assert trash is not None
    row = window.tree.visualRect(trash)
    image = window.tree.viewport().grab().toImage()
    highlight = window.tree.palette().color(QPalette.ColorRole.Highlight).name()
    assert image.pixelColor(1, row.center().y()).name() == highlight  # the whole row, also left of the icon
    drag_over("Inbox")  # where the message is now: no drop
    assert window.tree.drop_row is None
    drag_over("Trash")
    window.tree.dragLeaveEvent(QDragLeaveEvent())
    assert window.tree.drop_row is None


def test_after_deleting_the_last_message_the_one_before_is_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    middle = message_id_at(window, 1)
    select(window, 2)
    window.act_delete.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and current_message_id(window) == middle)


def test_the_bin_of_another_row_keeps_the_selection(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    first = message_id_at(window, 0)
    select(window, 0)
    window._icon_clicked(2, "trash")  # noqa: SLF001 - a click on the bin of the last row
    wait(qtbot, lambda: window.list_model.rowCount() == 2 and current_message_id(window) == first)
    assert window.list_view.currentIndex().row() == 0


def test_f2_in_the_folder_list_renames_the_folder(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    asked: list[str] = []

    def get_text(parent: object, title: str, *args: object, **kwargs: object) -> tuple[str, bool]:
        asked.append(title)
        return ("", False)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(get_text))
    assert window.act_rename_folder.shortcut().toString() == "F2"
    window.show()
    window.activateWindow()
    window.list_view.setFocus()
    qtbot.keyClick(window.list_view, Qt.Key.Key_F2)
    qtbot.wait(50)
    assert asked == []  # not in the message list
    window.tree.setFocus()
    wait(qtbot, lambda: window.tree.hasFocus())
    qtbot.keyClick(window.tree, Qt.Key.Key_F2)
    wait(qtbot, lambda: asked == ["Rename folder"])


def test_move_to_another_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.move_menu.aboutToShow.emit()
    titles = [action.text() for action in world.window.move_menu.actions()]
    assert "Junk" in titles and "Inbox" not in titles and "Outbox" not in titles
    world.window.mail_actions.move_selected("Junk")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert world.call("message.list", account=world.account_id, folder="Junk").total == 1


def test_several_messages_go_as_one_command_and_failures_are_told_once(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import MessageRef, StorageError
    from chrono_mail.engine.mailbox import Mailbox

    started(qtbot, world)
    window = world.window
    model = window.list_model
    refused = str(getattr(model.row_at(1), "message_id"))
    real_move = Mailbox.move

    def move(self: Mailbox, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        if ref.store_id == refused:
            raise StorageError("the disk is full")
        return real_move(self, ref, target)

    monkeypatch.setattr(Mailbox, "move", move)
    asked: list[str] = []
    real_call = world.backend.call

    def counted(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        asked.append(command)
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", counted)
    window.list_view.selectAll()
    window.mail_actions.move_selected("Junk")
    wait(qtbot, lambda: model.rowCount() == 1 and not model.pending)
    assert [command for command in asked if command != "mail.undo-labels"] == ["message.move-many"]  # one command for the three
    assert str(getattr(model.row_at(0), "message_id")) == refused  # it came back
    assert window.status_label.text() == "Could not move: 1 of 3 messages: the disk is full"
    assert world.call("message.list", account=world.account_id, folder="Junk").total == 2


def test_actions_without_a_selection_do_nothing(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    for action in (world.window.act_delete, world.window.act_flag, world.window.act_reply, world.window.act_forward, world.window.act_read):
        action.trigger()
    qtbot.wait(200)
    assert world.window.list_model.rowCount() == 3 and world.window.composing.composers == []  # noqa: SLF001


# --- mail --------------------------------------------------------------------------------------------------------------------


def test_get_mail_downloads_new_messages_and_shows_them(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.pop.messages["uid9"] = mail(9)
    world.window.act_refresh.trigger()
    assert not world.window.act_refresh.isEnabled()  # a check is running
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    wait(qtbot, lambda: world.window.act_refresh.isEnabled())
    assert world.window.status_label.text() == "Mail checked: 1 new message"
    wait(qtbot, lambda: any(label.startswith("  Inbox  (4)") for label in tree_labels(world.window)))
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.status_label.text() == "Mail checked: nothing new")


def test_a_failing_mail_check_shows_the_problem(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.engine.passwords.set(world.account_id, ServerRole.INCOMING, "wrong")
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.status_label.text().startswith("Mail check:"))
    assert "login" in world.window.status_label.text() and "wrong" not in world.window.status_label.text().replace("wrong password", "")
    assert "c0392b" in world.window.status_label.styleSheet()
    wait(qtbot, lambda: world.window.act_refresh.isEnabled())


def test_automatic_checking_can_be_turned_off(world: World, qtbot: QtBot) -> None:
    assert world.window.checking.timer.isActive()  # noqa: SLF001
    world.window.act_auto.setChecked(False)
    assert not world.window.checking.timer.isActive() and world.window.settings.value("auto_check") in (False, "false")  # noqa: SLF001
    world.window.act_auto.setChecked(True)
    assert world.window.checking.timer.isActive()  # noqa: SLF001


# --- folders --------------------------------------------------------------------------------------------------------------------


def test_create_rename_and_delete_a_folder(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    answer_input(monkeypatch, [("Projects", True), ("Projects/Alpha", True)])
    world.window.folders.new_folder()
    wait(qtbot, lambda: "  Projects" in tree_labels(world.window))
    world.window.folders.new_folder()
    wait(qtbot, lambda: "    Alpha" in tree_labels(world.window))
    projects = world.window.folders.find_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    answer_input(monkeypatch, [("Work", True)])
    world.window.folders.rename_folder()
    wait(qtbot, lambda: "  Work" in tree_labels(world.window) and "    Alpha" in tree_labels(world.window))
    assert "  Projects" not in tree_labels(world.window)
    work = world.window.folders.find_item((world.account_id, "folder", "Work"))  # noqa: SLF001
    assert work is not None
    world.window.tree.setCurrentIndex(work)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.folders.delete_folder()
    wait(qtbot, lambda: "  Work" not in tree_labels(world.window))


def test_a_folder_that_cannot_be_created_reports_why(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    answer_input(monkeypatch, [("Inbox", True)])
    world.window.folders.new_folder()
    wait(qtbot, lambda: "already exists" in world.window.status_label.text())
    assert "c0392b" in world.window.status_label.styleSheet()


# --- composing, attachments, links -------------------------------------------------------------------------------------------


def test_reply_opens_a_prefilled_composer(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    composer = world.window.composing.composers[0]  # noqa: SLF001
    assert composer.to.text() == "Alice <alice@example.com>" and composer.subject.text().startswith("Re: Message")
    assert "wrote:" in composer.body.toPlainText()
    composer.body.document().setModified(False)
    composer._finished = True  # noqa: SLF001
    composer.close()


def test_a_draft_is_edited_from_the_drafts_folder_and_sent(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.call("draft.save", account=world.account_id, to=["you@example.com"], subject="Half done", body="first part")
    for row in range(world.window.tree_model.rowCount(account_node(world.window))):
        index = world.window.tree_model.index(row, 0, account_node(world.window))
        if index.data(FOLDER_PATH_ROLE) == "Drafts":
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.act_edit_draft.trigger()  # nothing selected yet: nothing happens
    select(world.window, 0)
    world.window.act_edit_draft.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    composer = world.window.composing.composers[0]  # noqa: SLF001
    assert composer.subject.text() == "Half done" and composer.body.toPlainText().strip() == "first part" and not composer.is_modified()
    world.window.act_edit_draft.trigger()  # the same draft is not opened twice
    qtbot.wait(150)
    assert len(world.window.composing.composers) == 1  # noqa: SLF001
    composer.body.setPlainText("first part and the rest")
    composer.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Drafts").total == 0)


def test_edit_draft_does_nothing_outside_drafts(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_edit_draft.trigger()
    qtbot.wait(150)
    assert world.window.composing.composers == []  # noqa: SLF001


def test_forward_and_reply_all_and_new_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_forward.trigger()
    world.window.act_reply_all.trigger()
    world.window.act_new.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 3)  # noqa: SLF001
    subjects_seen = sorted(composer.subject.text()[:4] for composer in world.window.composing.composers)  # noqa: SLF001
    assert subjects_seen == ["", "Fwd:", "Re: "]
    for composer in list(world.window.composing.composers):  # noqa: SLF001
        composer._finished = True  # noqa: SLF001
        composer.close()


def test_an_attachment_can_be_saved(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = (b"From: A <a@x.org>\r\nTo: me@example.com\r\nSubject: Report\r\nMessage-ID: <r@x>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: text/plain\r\n\r\nSee attached\r\n--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"report.pdf\"\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"%PDF-1.4 content") + b"--B--\r\n")
    for world in build_world(tmp_path, qtbot, dialogs, messages={"r": raw}):
        world.window.start()
        settle(qtbot, world, 1)
        select(world.window, 0)
        wait(qtbot, lambda: not world.window.reader._attachments.isHidden())  # noqa: SLF001
        buttons = world.window.reader._attachments.findChildren(type(world.window.reader._load_remote))  # noqa: SLF001
        assert len(buttons) == 1 and "report.pdf" in buttons[0].text()
        target = tmp_path / "saved-report.pdf"
        answer_save_file(monkeypatch, target)
        buttons[0].click()  # a click only selects it (D-236)
        world.window.reader.attachment_menu(0).actions()[1].trigger()  # Save as…
        wait(qtbot, lambda: target.exists())
        assert target.read_bytes() == b"%PDF-1.4 content"
        wait(qtbot, lambda: world.window.status_label.text().startswith("Saved"))


def test_links_ask_before_they_open(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    opened = record_opened_urls(monkeypatch)
    world.dialogs.answer = QMessageBox.StandardButton.No
    world.window.reading.open_link(QUrl("https://example.com/page"))  # noqa: SLF001
    assert opened == [] and "https://example.com/page" in world.dialogs.shown[-1][2]
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.reading.open_link(QUrl("https://example.com/page"))  # noqa: SLF001
    assert opened == ["https://example.com/page"]
    world.window.reading.open_link(QUrl("file:///etc/passwd"))  # noqa: SLF001
    world.window.reading.open_link(QUrl("javascript:alert(1)"))  # noqa: SLF001
    world.window.reading.open_link(QUrl("ftp://example.com/x"))  # noqa: SLF001
    assert opened == ["https://example.com/page"]  # nothing but web addresses is ever opened
    world.window.reading.open_link(QUrl("mailto:friend@example.org"))  # noqa: SLF001
    assert len(world.window.composing.composers) == 1 and world.window.composing.composers[0].to.text() == "friend@example.org"  # noqa: SLF001
    world.window.composing.composers[0]._finished = True  # noqa: SLF001
    world.window.composing.composers[0].close()


# --- settings ------------------------------------------------------------------------------------------------------------------------


def test_sort_order_and_selected_folder_are_remembered(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    ini = None
    for world in build_world(tmp_path, qtbot, dialogs):
        world.window.start()
        settle(qtbot, world, 3)
        world.window.messages.set_sort("sender", True)  # noqa: SLF001
        qtbot.wait(200)
        world.window.close()
        ini = world.settings_path
        settings = QSettings(str(ini), QSettings.Format.IniFormat)
        assert '"sort": "sender"' in str(settings.value("folder_columns")) and settings.value("last_folder") == "Inbox"  # the Inbox's own sort (B-16)
        reopened = MainWindow(world.backend, settings)
        qtbot.addWidget(reopened)
        reopened.start()
        wait(qtbot, lambda: reopened.mail_state.view.folder == "Inbox")
        assert reopened.messages.sort == "sender" and reopened.messages.oldest_first is True  # noqa: SLF001
        reopened.close()
    assert ini is not None


def test_a_screenshot_for_review(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.resize(1280, 800)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=row_of(world.window, 1).message_id, flagged=True, seen=True)
    world.window.list_model.reload()
    settle(qtbot, world, 3)
    select(world.window, 0)
    wait(qtbot, lambda: "Body of message" in world.window.reader.text_view.toPlainText())
    qtbot.wait(900)
    world.window.grab().save(str(SHOTS / "window.png"))


def test_deleting_right_after_clicking_does_not_report_a_missing_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_delete.trigger()  # the message is moved while its content is still being fetched
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    qtbot.wait(600)
    assert world.window.status_label.text() == "Moved 1 message to Trash"
    assert "c0392b" not in world.window.status_label.styleSheet()


def test_mail_is_checked_when_the_program_starts(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    for world in build_world(tmp_path, qtbot, dialogs, fetch=False):
        world.window.settings.setValue("check_on_start", True)
        world.window.start()
        wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
        wait(qtbot, lambda: world.window.status_label.text() == "Mail checked: 3 new messages")
        assert world.window.act_refresh.isEnabled()


def test_a_background_check_keeps_the_message_being_read_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 1)
    wait(qtbot, lambda: world.window.reading.shown is not None)  # noqa: SLF001
    assert world.window.reading.shown is not None  # noqa: SLF001
    reading = world.window.reading.shown[2]  # noqa: SLF001
    world.pop.messages["uid9"] = mail(9, date=None)
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    wait(qtbot, lambda: world.window.list_view.currentIndex().isValid() and row_of(world.window, world.window.list_view.currentIndex().row()).message_id == reading)
    assert "Message" in world.window.reader._subject.text()  # noqa: SLF001


# --- archive, mark all as read, empty folder, drag and drop (0.0.17) -----------------------------------------------------------------


def open_folder(world: World, qtbot: QtBot, name: str, rows: int) -> None:
    account = account_node(world.window)
    for row in range(world.window.tree_model.rowCount(account)):
        index = world.window.tree_model.index(row, 0, account)
        if index.data(FOLDER_PATH_ROLE) == name:
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == rows and world.window.mail_state.view.folder == name)  # noqa: SLF001


def test_archive_or_unarchive_follows_the_selected_messages_not_the_folder_on_the_left(world: World, qtbot: QtBot) -> None:
    """The owner (D-101): in a search of all folders started in Archive, Unarchive was offered for messages that were never archived."""
    started(qtbot, world)
    window = world.window
    first = message_id_at(window, 0)
    select(window, 0)
    window.act_archive.trigger()
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Archive")) is not None and window.list_model.rowCount() == 2)  # noqa: SLF001
    unread = window.folders.find_item(("", "favorite", "unread"))  # noqa: SLF001 - the archived one and the two in the Inbox, together
    assert unread is not None
    window.tree.setCurrentIndex(unread)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and all(window.list_model.row_at(row) is not None for row in range(3)))
    folders = [row.folder for row in (window.list_model.row_at(number) for number in range(3)) if row is not None]
    archived = folders.index("Archive")
    select(window, archived)
    assert window.act_archive.text() == "Unarchive"
    select(window, (archived + 1) % 3)
    assert window.act_archive.text() == "Archive"  # a message in the Inbox
    window.list_view.selectAll()
    assert window.act_archive.text() == "Archive"  # mixed: the others are archived, the archived one stays
    window.act_archive.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Archive").total == 3)
    assert first in {row.message_id for row in world.call("message.list", account=world.account_id, folder="Archive").messages}


def test_archive_moves_the_selected_messages_and_creates_the_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_archive.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert world.call("message.list", account=world.account_id, folder="Archive").total == 1
    wait(qtbot, lambda: any(label.strip().startswith("Archive") for label in tree_labels(world.window)))
    open_folder(world, qtbot, "Archive", 1)
    select(world.window, 0)
    world.window.act_archive.trigger()  # in the Archive folder it is Unarchive
    wait(qtbot, lambda: "Unarchived 1" in world.window.status_label.text())
    assert world.call("message.list", account=world.account_id, folder="Archive").total == 0


def test_mark_all_as_read_and_empty_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.act_mark_all_read.trigger()
    wait(qtbot, lambda: world.call("folder.list", account=world.account_id)[3].unread == 0 and "as read" in world.window.status_label.text())
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and all((row := row_of(world.window, position)) is not None and "seen" in row.flags for position in range(3)))  # the list is shown again
    select(world.window, 0)
    world.window.mail_actions.delete_selected()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    open_folder(world, qtbot, "Trash", 1)
    assert world.window.act_empty_folder.isEnabled()
    world.dialogs.answer = QMessageBox.StandardButton.No
    world.window.act_empty_folder.trigger()
    qtbot.wait(150)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 1
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.act_empty_folder.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Trash").total == 0)
    open_folder(world, qtbot, "Inbox", 2)
    assert not world.window.act_empty_folder.isEnabled()
    world.window.mail_actions.empty_folder()  # not allowed in the Inbox: nothing happens
    qtbot.wait(150)
    assert world.call("message.list", account=world.account_id, folder="Inbox").total == 2


# --- conversations (0.0.18) ---------------------------------------------------------------------------------------------------------------


def make_conversation(world: World) -> tuple[str, str]:
    from datetime import UTC, datetime, timedelta

    from chrono_mail.core import MessageFlag
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.mailfactory import raw_message

    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    with open_mailbox(world.engine, world.account_id) as mailbox:
        inbox, sent = mailbox.folder("Inbox"), mailbox.folder("Sent")
        first = mailbox.deliver(inbox, raw_message(subject="Plans", sender="Dora <dora@example.com>", message_id="<k1@x>", date=start), frozenset())
        mailbox.deliver(sent, raw_message(subject="Re: Plans", sender="me@example.com", message_id="<k2@x>", references="<k1@x>", date=start + timedelta(minutes=5)), frozenset({MessageFlag.SEEN}))
        last = mailbox.deliver(inbox, raw_message(subject="Re: Plans", sender="Dora <dora@example.com>", message_id="<k3@x>", references="<k1@x>", date=start + timedelta(minutes=9)), frozenset())
    return first.store_id, last.store_id


def test_conversation_mode_groups_a_folder_and_shows_the_conversation_bar(world: World, qtbot: QtBot) -> None:
    first, last = make_conversation(world)
    world.window.start()
    settle(qtbot, world, 5)
    assert world.window.list_model.rowCount() == 5
    world.window.act_conversations.setChecked(True)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)  # the 3 original messages and 1 conversation (2 of its 3 are in the Inbox)
    assert subjects(world.window).count("Re: Plans") == 1 and "Plans" not in subjects(world.window)
    position = subjects(world.window).index("Re: Plans")
    assert row_of(world.window, position).thread_size == 3
    select(world.window, position)
    wait(qtbot, lambda: not world.window.conversation_bar.isHidden())
    assert world.window.conversation_bar._list.count() == 3  # noqa: SLF001
    assert world.window.settings.value("conversations") in (True, "true")


def test_choosing_a_message_in_the_conversation_bar_shows_it(world: World, qtbot: QtBot) -> None:
    first, last = make_conversation(world)
    world.window.start()
    settle(qtbot, world, 5)
    world.window.act_conversations.setChecked(True)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    select(world.window, subjects(world.window).index("Re: Plans"))
    wait(qtbot, lambda: not world.window.conversation_bar.isHidden())
    bar = world.window.conversation_bar
    bar._list.setCurrentRow(0)  # noqa: SLF001
    bar._chosen(bar._list.item(0))  # noqa: SLF001
    wait(qtbot, lambda: world.window.reading.shown is not None and world.window.reading.shown[2] == first)  # noqa: SLF001
    wait(qtbot, lambda: "seen" in world.call("message.show", account=world.account_id, folder="Inbox", message=first).flags)  # read after the delay


def test_conversation_mode_is_off_while_searching_or_filtering(world: World, qtbot: QtBot) -> None:
    make_conversation(world)
    world.window.start()
    settle(qtbot, world, 5)
    world.window.act_conversations.setChecked(True)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    spec = world.window.messages.view_spec()
    assert spec is not None and spec.conversations
    world.window.filters.unread.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() >= 5)  # every unread message is listed, not one per conversation
    spec = world.window.messages.view_spec()
    assert spec is not None and not spec.conversations
    world.window.filters.unread.trigger()
    world.window.act_conversations.setChecked(False)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 5)
    assert world.window.conversation_bar.isHidden()


def test_a_conversation_row_is_painted_with_its_count_at_any_width(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QListView

    from chrono_mail.ui.delegate import ROW_HEIGHT, MessageDelegate
    from chrono_mail.ui.messagelist import MessageListModel, ViewSpec

    make_conversation(world)
    images = []
    for conversations in (False, True):
        model = MessageListModel(world.backend)
        model.show_groups = False  # written before group headers existed
        view = QListView()
        qtbot.addWidget(view)
        view.setModel(model)
        view.setItemDelegate(MessageDelegate())
        view.resize(420, ROW_HEIGHT * 6)
        view.show()
        model.show(ViewSpec(world.account_id, "Inbox", conversations=conversations))
        wait(qtbot, lambda: model.rowCount() > 0 and model.row_at(0) is not None)
        qtbot.wait(100)
        images.append(view.viewport().grab().toImage())
    assert images[0] != images[1]  # the count box changes the picture
    for width in (60, 150, 800):
        narrow = QListView()
        qtbot.addWidget(narrow)
        narrow_model = MessageListModel(world.backend)
        narrow_model.show_groups = False  # written before group headers existed
        narrow.setModel(narrow_model)
        narrow.setItemDelegate(MessageDelegate())
        narrow.resize(width, ROW_HEIGHT * 3)
        narrow.show()
        narrow_model.show(ViewSpec(world.account_id, "Inbox", conversations=True))
        wait(qtbot, lambda: narrow_model.rowCount() > 0 and narrow_model.row_at(0) is not None)
        qtbot.wait(50)
        assert not narrow.viewport().grab().isNull()


def test_the_about_text_names_the_program_and_its_version(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import program_version

    shown: list[str] = []

    def about(parent: object, title: str, text: str) -> None:
        shown.append(f"{title}|{text}")

    monkeypatch.setattr(QMessageBox, "about", staticmethod(about))
    world.window.act_about.trigger()
    assert len(shown) == 1 and shown[0].startswith("About Chrono Mail|")
    assert f"version {program_version()}" in shown[0] and program_version()[0].isdigit() and "AGPL" in shown[0]


# --- junk and unread navigation (0.0.34) -----------------------------------------------------------------------------------------------


def test_mark_as_junk_and_not_junk(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_junk.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert world.call("message.list", account=world.account_id, folder="Junk").total == 1
    open_folder(world, qtbot, "Junk", 1)
    select(world.window, 0)
    assert world.window.act_junk.isEnabled() and world.window.act_not_junk.isEnabled()  # Ctrl+J is a switch: in Junk it brings the message back
    world.window.act_junk.trigger()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Junk").total == 0)
    open_folder(world, qtbot, "Inbox", 3)
    select(world.window, 0)
    assert world.window.act_junk.isEnabled() and not world.window.act_not_junk.isEnabled()


def test_next_and_previous_unread_inside_the_list(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.reading.mark_read_timer.stop()  # noqa: SLF001 - reading a message would mark it read after a moment
    first, second, third = (row_of(world.window, position).message_id for position in range(3))
    world.call("message.flag", account=world.account_id, folder="Inbox", message=second, seen=True)
    world.window.list_model.reload()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and row_of(world.window, 1) is not None and "seen" in row_of(world.window, 1).flags)  # the reloaded rows (the old ones stay until then)
    select(world.window, 0)
    assert world.window.messages.go_to_unread(1) and world.window.list_view.currentIndex().row() == 2  # the read one in the middle is skipped
    assert world.window.messages.go_to_unread(-1) and world.window.list_view.currentIndex().row() == 0
    assert first and third


def test_next_unread_goes_on_to_the_next_folder_with_unread_mail(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.reading.mark_read_timer.stop()  # noqa: SLF001
    for position in range(3):
        world.call("message.flag", account=world.account_id, folder="Inbox", message=row_of(world.window, position).message_id, seen=True)  # the Inbox has nothing unread
    junked = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.junk", account=world.account_id, folder="Inbox", message=junked.message_id)
    world.call("message.flag", account=world.account_id, folder="Junk", message=junked.message_id, seen=False)
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: any(label.strip().startswith("Junk  (1)") for label in tree_labels(world.window)))
    world.window.list_model.reload()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and row_of(world.window, 0) is not None)
    select(world.window, 0)
    assert world.window.messages.go_to_unread(1) is True  # nothing unread below: on to the Junk folder
    wait(qtbot, lambda: world.window.mail_state.view.folder == "Junk" and world.window.list_view.currentIndex().isValid())  # noqa: SLF001
    assert world.window.list_view.currentIndex().row() == 0
    assert world.window.messages.go_to_unread(1) is False  # and nothing more after that


def test_the_favorites_show_the_same_folder_of_every_mailbox_and_can_be_switched_off(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    favorites = world.window.tree_model.index(0, 0)
    inboxes = world.window.tree_model.index(0, 0, favorites)  # All Inboxes first (the owner's order, D-061)
    assert favorites.data() == "Favorites" and inboxes.data() == "All Inboxes" and inboxes.data(COUNT_ROLE) == 3
    world.window.tree.setCurrentIndex(inboxes)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    row = world.window.list_model.row_at(0)
    assert getattr(row, "account") == world.account_id
    select(world.window, 0)
    wait(qtbot, lambda: "seen" in getattr(world.window.list_model.row_at(0), "flags"))  # reading it from here marks it in its own account
    assert seen_count(world) == 1
    world.window.settings.setValue("show_favorites", False)
    world.window.apply_preferences()
    assert world.window.tree_model.index(0, 0).data() == "me@example.com"


def test_a_pinned_folder_and_a_group_show_in_the_folder_list(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.call("favorite.pin", account=world.account_id, folder="Sent")
    group = world.call("group.add", name="Work", account=(world.account_id,)).groups[0]
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: "Work" in tree_labels(world.window))
    labels = tree_labels(world.window)
    assert labels[0] == "Favorites" and labels[10] == "  Sent"  # the pinned folder follows the standard favorites
    assert labels[11:15] == ["Work", "  All Inboxes  (3)", "  me@example.com", "    Inbox  (3)"]  # Inbox first, then Unread (D-232)
    inbox = next(world.window.tree_model.index(row, 0, world.window.tree_model.index(1, 0)) for row in range(2) if world.window.tree_model.index(row, 0, world.window.tree_model.index(1, 0)).data(KIND_ROLE) == "groupinbox")
    world.window.tree.setCurrentIndex(inbox)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert world.window.mail_state.view.combined_accounts == (world.account_id,)  # noqa: SLF001
    world.window.search_box.setText("Message 2")  # a search stays inside the combined view
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    pinned = world.window.tree_model.index(9, 0, world.window.tree_model.index(0, 0))  # after the nine standard favorites
    assert pinned.data(KIND_ROLE) == "pinlink"
    world.window.tree.setCurrentIndex(pinned)
    wait(qtbot, lambda: world.window.mail_state.view.folder == "Sent")  # noqa: SLF001
    world.call("group.remove", group=group.group_id)
    world.call("favorite.show", view="trash", visible=False)
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: "Work" not in tree_labels(world.window))
    assert "  Trash" not in tree_labels(world.window)[:10]


def test_the_folder_list_menu_offers_favorites_and_groups(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QMenu

    started(qtbot, world)
    account = account_node(world.window)
    inbox = world.window.tree_model.index(0, 0, account)  # Inbox, Unread (D-232)
    assert inbox.data() == "Inbox" and inbox.data(COUNT_ROLE) == 3

    def offered(index: QModelIndex) -> list[str]:
        menu = QMenu()
        world.window.folders.tree_extras(menu, index)  # noqa: SLF001
        return [action.text() for action in menu.actions() if action.text()]

    assert offered(inbox) == ["Add to Favorites"]
    assert "Put in group" in offered(account) and "Take out of its group" not in offered(account)
    world.window.folders.sidebar_call(FAVORITE_PIN, PinParameters(world.account_id, "Inbox"))  # noqa: SLF001
    wait(qtbot, lambda: any(pin.folder == "Inbox" for pin in world.call("sidebar.show").pins))
    wait(qtbot, lambda: "  Inbox  (3)" in tree_labels(world.window)[:12] and offered(world.window.tree_model.index(0, 0, account_node(world.window))) == [])
    pinned = world.window.tree_model.index(9, 0, world.window.tree_model.index(0, 0))  # after the nine standard favorites
    assert pinned.data(KIND_ROLE) == "pinlink" and offered(pinned)[:4] == ["Remove from Favorites", "Rename…", "Move up", "Move down"]
    assert offered(pinned)[4:] == STANDARD_FAVORITES  # then every standard favorite (D-234)
    standard = world.window.tree_model.index(2, 0, world.window.tree_model.index(0, 0))
    assert offered(standard) == STANDARD_FAVORITES  # a group is made from an account (1.1.190)
    assert offered(world.window.tree_model.index(0, 0)) == STANDARD_FAVORITES  # the heading too, also while nothing is hidden


STANDARD_FAVORITES = ["All Inboxes", "Unread", "Outbox", "Sent", "Drafts", "Flagged", "Unreplied", "Junk", "Trash"]  # Snoozed, Scheduled, Archive: no such folder yet


def test_a_right_click_in_favorites_ticks_every_standard_favorite_and_a_click_shows_or_hides_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner (D-234): every favorite is listed, ticked while shown; a click toggles it; the last one left is greyed."""
    started(qtbot, world)

    def menu_of_heading() -> QMenu:
        menu = QMenu()
        world.window.folders.tree_extras(menu, world.window.tree_model.index(0, 0))  # noqa: SLF001
        return menu

    def ticks() -> dict[str, tuple[bool, bool]]:
        menu = menu_of_heading()
        return {action.text(): (action.isChecked(), action.isEnabled()) for action in menu.actions() if action.text()}

    def click(label: str) -> None:
        menu = menu_of_heading()  # kept while its action runs
        next(action for action in menu.actions() if action.text() == label).trigger()

    first = ticks()
    assert list(first) == STANDARD_FAVORITES and all(checked and enabled for checked, enabled in first.values())  # nothing hidden: all ticked
    click("Sent")
    wait(qtbot, lambda: "  Sent" not in tree_labels(world.window)[:10] and ticks()["Sent"] == (False, True))  # hidden, still listed
    click("Sent")
    wait(qtbot, lambda: "  Sent" in tree_labels(world.window)[:10] and ticks()["Sent"] == (True, True))  # shown again
    in_use = ["All Inboxes", "Unread", "Outbox", "Sent", "Drafts", "Flagged", "Unreplied", "Junk"]  # Trash stays
    for label in in_use:
        click(label)
        wait(qtbot, lambda label=label: not ticks()[label][0])
    assert ticks()["Trash"] == (True, False)  # the last one left in Favorites cannot be hidden: the heading and its menu stay
    assert ticks()["Sent"] == (False, True)
    assert "Snoozed" not in ticks()  # no Snoozed folder anywhere: no Snoozed favorite, so no tick
    inbox = world.window.folders.find_item((world.account_id, "folder", "Inbox"))  # noqa: SLF001
    assert inbox is not None
    world.window.tree.setCurrentIndex(inbox)
    answer_input(monkeypatch, [("Snoozed", True)])
    world.window.folders.new_folder()  # a Snoozed folder appears: so do its favorite and its tick, in their place
    wait(qtbot, lambda: "Snoozed" in ticks())
    assert list(ticks()).index("Snoozed") == list(ticks()).index("Unreplied") + 1


def test_the_count_line_says_one_message_not_one_messages(world: World, qtbot: QtBot) -> None:
    world.window.messages._counts_changed(1, 1)  # noqa: SLF001 - what the list reports for a view of one message
    assert world.window.count_label.text() == "1 message, 1 unread"
    world.window.messages._counts_changed(2, 0)  # noqa: SLF001
    assert world.window.count_label.text() == "2 messages"
