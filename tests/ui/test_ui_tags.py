from pathlib import Path
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QItemSelectionModel, QRect, QSettings, Qt
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
__version__ = "test_ui_tags.py 1.9.274"

# --- Version history ----------------------------------------------------
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


def test_the_tags_section_of_the_folder_list_shows_the_mail_of_a_tag(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tagged = sorted([subjects(window)[0], subjects(window)[2]])
    tag_rows(world, qtbot, 0, 2)
    wait(qtbot, lambda: window.folders.find_item(("", "tagview", "Work")) is not None)
    found = window.folders.find_item(("", "tagview", "Work"))
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.combined == "tagged" and window.list_model.rowCount() == 2)
    assert sorted(subjects(window)) == tagged  # the two tagged messages, from the combined view


def test_the_tags_section_comes_right_after_favorites(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0)
    wait(qtbot, lambda: window.folders.find_item(("", "tagview", "Work")) is not None)
    titles = [window.tree_model.item(row).text() for row in range(window.tree_model.rowCount())]
    assert titles.index("Tags") == titles.index("Favorites") + 1


def test_the_reading_pane_shows_the_tags_of_the_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 0)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(1, 0))
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    wait(qtbot, lambda: "Work" in world.window.reader._tags.text() and world.window.reader._tags.isVisibleTo(world.window.reader))


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
    items = [dialog.list.topLevelItem(row) for row in range(dialog.list.topLevelItemCount())]
    return [item.text(0) for item in items if item is not None]


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
    second = dialog.list.topLevelItem(1)
    assert second is not None
    dialog.list.setCurrentItem(second)
    dialog.move_tag(-1)
    dialog.deleted.append(dialog.rows.pop(dialog.rows.index(next(row for row in dialog.rows if row.name == "Home"))))
    commands = [(step.command.id, step.parameters) for step in dialog.commands()]  # type: ignore[attr-defined]
    assert commands[0] == ("tag.delete", DeleteParameters("Home"))
    assert ("tag.rename", RenameParameters("Work", "Job")) in commands and ("tag.create", CreateParameters("New", "#0000ff")) in commands
    assert [parameters.name for command, parameters in commands if command == "tag.edit"] == ["Job", "New"]
    dialog.tabs.setCurrentIndex(3)  # Tasks: only the tags used for tasks
    assert listed_names(dialog) == ["Job"]


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


def test_the_tabs_of_the_tags_page_are_as_wide_as_the_list(qtbot: QtBot, world: World) -> None:
    panel = TagsPanel(world.backend, ())
    qtbot.addWidget(panel)
    panel.resize(700, 400)
    panel.show()
    qtbot.waitExposed(panel)
    assert panel.tabs.x() == panel.list.x() and panel.tabs.width() == panel.list.width()
    assert panel.tabs.geometry().right() < panel.add_button.x()  # never over the buttons


def test_name_and_shortcut_always_fit_and_the_dragged_width_is_remembered(qtbot: QtBot, world: World, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "tags.ini"), QSettings.Format.IniFormat)
    panel = TagsPanel(world.backend, (), settings=settings)
    qtbot.addWidget(panel)
    panel.resize(700, 400)
    panel.show()
    qtbot.waitExposed(panel)
    header, width = panel.list.header(), panel.list.viewport().width()
    assert abs(header.sectionSize(0) - header.sectionSize(1)) <= 1 and header.sectionSize(0) + header.sectionSize(1) == width  # half each
    assert panel.list.horizontalScrollBar().maximum() == 0
    header.resizeSection(0, round(width * 0.7))  # as a drag does
    assert abs(float(str(settings.value("tags_name_share"))) - 0.7) < 0.01
    header.resizeSection(0, width + 200)  # dragged past the list: Shortcut keeps its smallest width
    assert header.sectionSize(0) + header.sectionSize(1) == width and panel.list.horizontalScrollBar().maximum() == 0
    panel.resize(900, 400)  # wider: Name keeps its share, both still fit
    wait(qtbot, lambda: panel.list.viewport().width() > width)
    assert header.sectionSize(0) + header.sectionSize(1) == panel.list.viewport().width()
    again = TagsPanel(world.backend, (), settings=settings)  # the next time: the remembered share
    qtbot.addWidget(again)
    again.resize(700, 400)
    again.show()
    qtbot.waitExposed(again)
    assert again.list.header().sectionSize(0) > again.list.header().sectionSize(1)



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


def test_tagged_with_narrows_a_tags_view(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0, 2)
    urgent = window.list_model.index(2, 0).data(ROW_ROLE).subject
    tag_rows(world, qtbot, 2, name="Urgent")
    wait(qtbot, lambda: window.folders.find_item(("", "tagview", "Work")) is not None)
    found = window.folders.find_item(("", "tagview", "Work"))
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.combined == "tagged" and window.list_model.rowCount() == 2)
    window.filters.set_tag("Urgent")
    wait(qtbot, lambda: window.list_model.rowCount() == 1)
    assert subjects(window) == [urgent]
    spec = window.messages.view_spec()
    assert spec is not None and spec.combined_tag == "Work" and spec.tag == "Urgent"
    window.filters.clear_filters()
    wait(qtbot, lambda: window.list_model.rowCount() == 2)


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
