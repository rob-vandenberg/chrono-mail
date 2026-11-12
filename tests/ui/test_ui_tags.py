from types import SimpleNamespace

import pytest

from PySide6.QtCore import QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import Change, MessageRow
from chrono_mail.engine.commands_pim import RenameParameters
from chrono_mail.engine.commands_tags import CreateParameters, DeleteParameters
from chrono_mail.ui.messagelist import ROW_ROLE
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import paint_tag_marks, tag_icon
from chrono_mail.ui.tagsdialog import TagRow, TagsPanel
from tests.ui.test_ui_window import started, subjects
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tags.py 4.4.590"

# --- Version history ----------------------------------------------------
# v4.4.590: Favorites starts with All Messages, then All Inboxes (the owner)
# v4.4.588: A tag renamed in Settings > Tags while its filter is on: the filter stays on under the new name, also in other capitals (D-334)
# v4.4.586: Favorites > All messages: every message, its own columns, the tag filter (D-334)
# v4.4.585: Mail's Tags block: a click filters the open folder, one filter with Tagged with, the Sidebar tick (D-334)
# v4.4.581: Settings > Tags is one list edited in place (D-332): the names read from the lines; the tests of the tabs and of the Name and
#           Shortcut columns' shared width went with them
# v4.4.580: The list and its buttons inside the tab page, as Settings > Sidebar (the owner, D-331); no longer tabs as wide as the list (v1.1.186)
# v4.2.479: the reading pane shows the tags as squares under the date: the test asks their names
# v1.9.274: A coming message is a real Change with a MessageRow (R-19)
# v1.9.271: The Tags page's changes are typed steps (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: The message list from messagelist.py (R-15)
# v1.1.242: the filters narrow a Favorites view and a Tags view; a message that stops matching while shown stays; a message coming in must match the filter too (R-03)
# v1.1.187: Name and Shortcut always fit, half each at first, the dragged width remembered
# v1.1.186: the tabs of the Tags page are as wide as the list
# v1.1.182: the Tags window is the Tags page of Settings (TagsPanel); Menu > Tags opens it; OK saves the tags
# v1.1.181: the Tags section comes right after Favorites
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.135: Initial tests: tags in the mail window (Tag menu, filter, folder list, reading pane, Tags window, colours)


def select(world: World, *rows: int) -> None:
    view, model = world.window.list_view, world.window.list_model
    view.selectionModel().clearSelection()
    for row in rows:
        view.selectionModel().select(model.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    view.selectionModel().setCurrentIndex(model.index(rows[0], 0), QItemSelectionModel.SelectionFlag.NoUpdate)


def tags_of(world: World, row: int) -> tuple[str, ...]:
    return tuple(world.window.list_model.index(row, 0).data(ROW_ROLE).tags)


def tag_rows(world: World, qtbot: QtBot, *rows: int, name: str = "Work") -> None:
    select(world, *rows)
    world.window.tags.toggle_tag(name, True)  # what a click in the Tag menu does
    wait(qtbot, lambda: all(name in tags_of(world, row) for row in rows))


def test_the_tag_menu_gives_and_takes_a_tag_and_offers_new_and_manage(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 0, 1)
    wait(qtbot, lambda: any(str(getattr(tag, "name")) == "Work" for tag in world.window.mail_state.tags))  # a new tag is loaded with its colour
    menu = QMenu()
    select(world, 0, 1)
    world.window.tags.fill_tag_menu(menu)
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == ["Work", "New tag…", "Manage tags…"]
    world.window.tags.toggle_tag_by_key("Work")  # both have it: the key takes it away
    wait(qtbot, lambda: tags_of(world, 0) == () and tags_of(world, 1) == ())
    assert world.window.tag_button.isEnabled()


def test_the_list_is_filtered_by_a_tag_and_the_filter_button_shows_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 1)
    wait(qtbot, lambda: bool(world.window.mail_state.tags))
    world.window.filters.fill_tags()
    assert [action.text() for action in world.window.filters.tag_menu.actions()] == ["Work"]
    world.window.filters.set_tag("Work")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.filters.button.isChecked() and world.window.filters.tag_menu.menuAction().font().bold()
    world.window.filters.clear_filters()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert not world.window.filters.button.isChecked()


def test_a_click_on_a_tag_in_mails_tags_block_shows_only_its_mail_of_the_open_folder_and_a_second_click_all(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    folder = window.mail_state.view.folder
    tagged = sorted([subjects(window)[0], subjects(window)[2]])
    every = window.list_model.rowCount()
    tag_rows(world, qtbot, 0, 2)
    wait(qtbot, lambda: window.mail_tags.list.count() == 1)
    window.mail_tags.choose("Work")  # a click on the tag in the Tags block
    wait(qtbot, lambda: window.mail_state.view.tag == "Work" and window.list_model.rowCount() == 2)
    assert sorted(subjects(window)) == tagged and window.mail_state.view.folder == folder  # the open folder, filtered (D-334)
    assert window.mail_state.view.filtered and window.filters.button.isChecked()  # the Filter button is blue: a filter is on
    window.mail_tags.choose("")  # a second click
    wait(qtbot, lambda: window.mail_state.view.tag is None and window.list_model.rowCount() == every)


def test_mails_tags_block_stands_below_the_folder_list_and_never_in_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0)
    wait(qtbot, lambda: window.mail_tags.list.count() == 1)
    titles = [window.tree_model.item(row).text() for row in range(window.tree_model.rowCount())]
    assert "Tags" not in titles and window.folders.find_item(("", "tagview", "Work")) is None  # no Tags entries in the folder list (D-334)
    assert window.mail_side.widget(0) is window.tree and window.mail_side.widget(1) is window.mail_tags


def test_the_reading_pane_shows_the_tags_of_the_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 0)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(1, 0))
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    wait(qtbot, lambda: world.window.reader._tags.names() == ("Work",) and world.window.reader._tags.isVisibleTo(world.window.reader))  # noqa: SLF001 - a square under the date (4.2.479)


def test_the_tag_colour_can_be_the_text_colour(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 0)
    model = world.window.list_model
    column = model.columns.index("subject")
    assert model.index(0, column).data(Qt.ItemDataRole.ForegroundRole) is None
    model.tag_text = True
    brush = model.index(0, column).data(Qt.ItemDataRole.ForegroundRole)
    assert brush is not None and brush.color() == world.window.tags.book.colour("Work")


def listed_names(dialog: TagsPanel) -> list[str]:
    """The names on the lines of the list, in their order (each drawn on a label in its colour)."""
    lines = [dialog.list.topLevelItem(row) for row in range(dialog.list.topLevelItemCount())]
    rows = [dialog.row_at(dialog.list.indexFromItem(line)) for line in lines if line is not None]
    return [row.name for row in rows if row is not None]


def test_the_tags_window_turns_its_changes_into_commands(qtbot: QtBot, world: World) -> None:
    tags = (
        SimpleNamespace(name="Home", colour="#ff0000", shortcut="", kinds=("mail",), mails=2, contacts=0, events=0, tasks=0),
        SimpleNamespace(name="Work", colour="#00ff00", shortcut="Ctrl+Alt+1", kinds=("mail", "tasks"), mails=0, contacts=0, events=0, tasks=0),
    )
    dialog = TagsPanel(world.backend, tags)
    qtbot.addWidget(dialog)
    assert listed_names(dialog) == ["Home", "Work"]
    dialog.rows[1].name = "Job"  # as the editor does
    dialog.rows.append(TagRow("New", "#0000ff", "Ctrl+Alt+2", ("mail",)))
    dialog.move_tag(dialog.rows[1], -1)
    dialog.deleted.append(dialog.rows.pop(dialog.rows.index(next(row for row in dialog.rows if row.name == "Home"))))
    commands = [(step.command.id, step.parameters) for step in dialog.commands()]  # type: ignore[attr-defined]
    assert commands[0] == ("tag.delete", DeleteParameters("Home"))
    assert ("tag.rename", RenameParameters("Work", "Job")) in commands and ("tag.create", CreateParameters("New", "#0000ff")) in commands
    assert [parameters.name for command, parameters in commands if command == "tag.edit"] == ["Job", "New"]
    dialog.refresh()
    assert listed_names(dialog) == ["Job", "New"]  # one list: every tag on its own line, whatever it is shown in (D-332)


def test_saving_the_tags_page_makes_the_tags(qtbot: QtBot, world: World) -> None:
    saved: list[bool] = []
    dialog = TagsPanel(world.backend, ())
    qtbot.addWidget(dialog)
    assert not dialog.changed()
    dialog.rows.append(TagRow("Later", "#00aa00", "Ctrl+Alt+5", ("mail", "events")))
    assert dialog.changed()
    dialog.save(lambda: saved.append(True), lambda: saved.append(False))
    wait(qtbot, lambda: bool(saved))
    assert saved == [True]
    listed = {str(item.name): item for item in world.call("tag.list")}
    assert listed["Later"].colour == "#00aa00" and listed["Later"].shortcut == "Ctrl+Alt+5" and listed["Later"].kinds == ("mail", "events")


def test_tags_are_drawn_without_trouble() -> None:
    book = TagColourBook()
    assert not tag_icon(QColor("#ff0000")).isNull() and not tag_icon(QColor("#ff0000"), checked=True).isNull()
    pixmap = QPixmap(200, 24)
    painter = QPainter(pixmap)
    for selected in (False, True):
        paint_tag_marks(painter, QRect(0, 0, 200, 24), ("Work", "Home", "A long tag name"), book, selected, QFont())
        paint_tag_marks(painter, QRect(0, 0, 20, 24), ("Work",), book, selected, QFont())  # too narrow for a pill: a small tag
    painter.end()


def test_menu_tags_opens_the_tags_page_of_settings_and_ok_saves(qtbot: QtBot, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.ui.settingsdialog import SettingsDialog

    started(qtbot, world)
    window = world.window
    seen: list[str] = []

    def exec_on_tags(dialog: SettingsDialog) -> int:
        current = dialog.sidebar.currentItem()
        seen.append(current.text() if current is not None else "")
        dialog.tags_panel.rows.append(TagRow("Fresh", "#123456", "", ("mail",)))
        dialog.accept()  # OK waits for the tags to be saved
        wait(qtbot, lambda: dialog.result() == SettingsDialog.DialogCode.Accepted)
        return int(dialog.result())

    monkeypatch.setattr(SettingsDialog, "exec", exec_on_tags)
    window.open_tags()
    assert seen == ["Tags"]
    assert "Fresh" in {str(item.name) for item in world.call("tag.list")}
    wait(qtbot, lambda: any(str(getattr(tag, "name")) == "Fresh" for tag in window.mail_state.tags))  # noqa: SLF001  the window loaded them again


def test_the_filters_narrow_a_favorites_view(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    first = window.list_model.index(0, 0).data(ROW_ROLE)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, flagged=True)
    found = window.folders.find_item(("", "favorite", "inboxes"))
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.combined == "inboxes" and window.list_model.rowCount() == 3)
    window.filters.flagged.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 1)
    assert subjects(window) == [first.subject] and window.count_label.text().startswith("1")
    window.filters.flagged.trigger()
    window.filters.unread.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    select(world, 0)
    window.mail_actions.mark_selected(seen=True)
    wait(qtbot, lambda: "seen" in window.list_model.index(0, 0).data(ROW_ROLE).flags)
    window.list_model.reload()
    qtbot.wait(300)
    assert window.list_model.rowCount() == 3  # read while shown: it stays until the view is left (D-149)
    window.filters.clear_filters()


def test_tagged_with_and_the_tags_block_are_one_filter(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    every = window.list_model.rowCount()
    tag_rows(world, qtbot, 0, 2)
    urgent = window.list_model.index(2, 0).data(ROW_ROLE).subject
    tag_rows(world, qtbot, 2, name="Urgent")
    wait(qtbot, lambda: window.mail_tags.list.count() == 2)
    window.filters.set_tag("Urgent")  # Filter > Tagged with
    wait(qtbot, lambda: window.list_model.rowCount() == 1 and window.mail_tags.tag() == "Urgent")  # the block shows the same filter (D-334)
    assert subjects(window) == [urgent] and window.mail_tags.title.text() == "Tags: Urgent"
    window.mail_tags.choose("Work")  # a click on another tag in the block: Tagged with follows
    wait(qtbot, lambda: window.mail_state.view.tag == "Work" and window.list_model.rowCount() == 2)
    window.filters.clear_filters()  # Filter > Clear
    wait(qtbot, lambda: window.list_model.rowCount() == every and window.mail_tags.tag() == "")


def test_a_message_coming_into_a_filtered_favorites_view_must_match_the_filter(world: World) -> None:
    from chrono_mail.ui.messagelist import MessageListModel, ViewSpec

    model = MessageListModel(world.backend)
    model._spec = ViewSpec("", combined="inboxes", flagged=True)
    def arrival(flags: tuple[str, ...], tags: tuple[str, ...]) -> Change:
        row = MessageRow("m1", "Inbox", "Hello", "a@example.com", None, 10, flags, False, "", "me@example.com", tags=tags)
        return Change("added", "a" * 32, folder="Inbox", message_id="m1", row=row, role="inbox")

    assert not model._may_hold(arrival(("seen",), ())) and model._may_hold(arrival(("seen", "flagged"), ()))
    model._spec = ViewSpec("", combined="tagged", combined_tag="Work", tag="Urgent")
    assert not model._may_hold(arrival((), ("Work",)))
    assert model._may_hold(arrival((), ("work", "urgent")))


def rename_in_settings(qtbot: QtBot, world: World, monkeypatch: pytest.MonkeyPatch, old: str, new: str) -> None:
    """Settings > Tags: the tag old renamed in place to new, and OK."""
    from chrono_mail.ui.settingsdialog import SettingsDialog

    def rename_and_ok(dialog: SettingsDialog) -> int:
        row = next(row for row in dialog.tags_panel.rows if row.name == old)
        dialog.tags_panel.rename(row, new)
        dialog.accept()  # OK waits for the tags to be saved
        wait(qtbot, lambda: dialog.result() == SettingsDialog.DialogCode.Accepted)
        return int(dialog.result())

    monkeypatch.setattr(SettingsDialog, "exec", rename_and_ok)
    world.window.open_tags()


def test_a_tag_renamed_in_settings_while_its_filter_is_on_keeps_the_filter_on_under_its_new_name(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0, 2)
    wait(qtbot, lambda: window.mail_tags.list.count() == 1)
    window.mail_tags.choose("Work")
    wait(qtbot, lambda: window.list_model.rowCount() == 2)
    rename_in_settings(qtbot, world, monkeypatch, "Work", "Projects")
    wait(qtbot, lambda: window.mail_state.view.tag == "Projects" and window.list_model.rowCount() == 2 and [window.mail_tags.list.item(row).text() for row in range(window.mail_tags.list.count())] == ["Projects"])
    assert window.mail_tags.tag() == "Projects" and window.mail_tags.title.text() == "Tags: Projects"
    rename_in_settings(qtbot, world, monkeypatch, "Projects", "PROJECTS")  # other capitals only: followed too
    wait(qtbot, lambda: window.mail_state.view.tag == "PROJECTS" and window.list_model.rowCount() == 2 and window.mail_tags.title.text() == "Tags: PROJECTS")


def test_the_tag_filter_stays_on_another_folder_and_the_sidebar_tick_hides_the_block_and_its_filter(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QDialog

    from chrono_mail.ui.settingsdialog import SettingsDialog

    started(qtbot, world)
    window = world.window
    every = window.list_model.rowCount()
    tag_rows(world, qtbot, 0)
    wait(qtbot, lambda: window.mail_tags.list.count() == 1)
    window.mail_tags.choose("Work")
    wait(qtbot, lambda: window.list_model.rowCount() == 1)
    unread = window.folders.find_item(("", "favorite", "unread"))
    assert unread is not None
    window.tree.setCurrentIndex(unread)  # another entry of the folder list: the filter stays on it (D-334)
    wait(qtbot, lambda: window.mail_state.view.combined == "unread" and window.mail_state.view.tag == "Work")
    assert window.mail_tags.tag() == "Work"

    def untick_and_ok(dialog: SettingsDialog) -> QDialog.DialogCode:
        box = dialog.sidebar_page.tags_box
        assert box is not None and box.isChecked()
        box.setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", untick_and_ok)
    window.open_settings("Sidebar")
    wait(qtbot, lambda: window.mail_tags.isHidden() and window.mail_state.view.tag is None)  # hidden, it filters nothing
    inbox = window.folders.find_item(("", "favorite", "inboxes"))
    assert inbox is not None
    window.tree.setCurrentIndex(inbox)
    wait(qtbot, lambda: window.list_model.rowCount() == every)


def test_all_messages_under_favorites_shows_every_message_with_its_own_columns_and_takes_the_tag_filter(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.columnsetup import folder_type, setup_key

    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0)
    select(world, 1)
    window.mail_actions.delete_selected()  # one message to Trash
    wait(qtbot, lambda: window.list_model.rowCount() == 2)
    titles = [window.folders.model.index(row, 0, window.folders.model.index(0, 0)).data() for row in range(window.folders.model.rowCount(window.folders.model.index(0, 0)))]
    assert titles[:2] == ["All Messages", "All Inboxes"]  # first, then All Inboxes (the owner)
    entry = window.folders.find_item(("", "favorite", "everywhere"))
    assert entry is not None
    window.tree.setCurrentIndex(entry)
    wait(qtbot, lambda: window.mail_state.view.combined == "everywhere" and window.list_model.rowCount() == 3)  # the Inbox's two and the one in Trash
    assert setup_key(window.mail_state.view) == "favorite\x1feverywhere" and folder_type(window.mail_state.view) == "incoming"  # its own columns, starting as incoming mail
    window.mail_tags.choose("Work")
    wait(qtbot, lambda: window.list_model.rowCount() == 1)  # every message with the tag, wherever it is
