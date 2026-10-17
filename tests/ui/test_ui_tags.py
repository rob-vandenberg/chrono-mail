from types import SimpleNamespace

from PySide6.QtCore import QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.ui.models import ROW_ROLE
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import paint_tag_marks, tag_icon
from chrono_mail.ui.tagsdialog import TagRow, TagsDialog
from tests.ui.test_ui_window import started, subjects
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tags.py 0.5.164"

# --- Version history ----------------------------------------------------
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
    world.window._toggle_tag(name, True)  # what a click in the Tag menu does
    wait(qtbot, lambda: all(name in tags_of(world, row) for row in rows))


def test_the_tag_menu_gives_and_takes_a_tag_and_offers_new_and_manage(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 0, 1)
    wait(qtbot, lambda: any(str(getattr(tag, "name")) == "Work" for tag in world.window._tags))  # a new tag is loaded with its colour
    menu = QMenu()
    select(world, 0, 1)
    world.window._fill_tag_menu(menu)
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == ["Work", "New tag…", "Manage tags…"]
    world.window._toggle_tag_by_key("Work")  # both have it: the key takes it away
    wait(qtbot, lambda: tags_of(world, 0) == () and tags_of(world, 1) == ())
    assert world.window.tag_button.isEnabled()


def test_the_list_is_filtered_by_a_tag_and_the_filter_button_shows_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tag_rows(world, qtbot, 1)
    wait(qtbot, lambda: bool(world.window._tags))
    world.window._fill_filter_tags()
    assert [action.text() for action in world.window.filter_tag_menu.actions()] == ["Work"]
    world.window._set_filter_tag("Work")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window.filter_button.isChecked() and world.window.filter_tag_menu.menuAction().font().bold()
    world.window._clear_filters()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    assert not world.window.filter_button.isChecked()


def test_the_tags_section_of_the_folder_list_shows_the_mail_of_a_tag(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tagged = sorted([subjects(window)[0], subjects(window)[2]])
    tag_rows(world, qtbot, 0, 2)
    wait(qtbot, lambda: window._find_tree_item(("", "tagview", "Work")) is not None)
    found = window._find_tree_item(("", "tagview", "Work"))
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window._combined == "tagged" and window.list_model.rowCount() == 2)
    assert sorted(subjects(window)) == tagged  # the two tagged messages, from the combined view


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
    assert brush is not None and brush.color() == world.window._tag_book.colour("Work")


def listed_names(dialog: TagsDialog) -> list[str]:
    items = [dialog.list.topLevelItem(row) for row in range(dialog.list.topLevelItemCount())]
    return [item.text(0) for item in items if item is not None]


def test_the_tags_window_turns_its_changes_into_commands(qtbot: QtBot, world: World) -> None:
    tags = (
        SimpleNamespace(name="Home", colour="#ff0000", shortcut="", kinds=("mail",), mails=2, contacts=0, events=0, tasks=0),
        SimpleNamespace(name="Work", colour="#00ff00", shortcut="Ctrl+Alt+1", kinds=("mail", "tasks"), mails=0, contacts=0, events=0, tasks=0),
    )
    dialog = TagsDialog(world.backend, tags, lambda: None)
    qtbot.addWidget(dialog)
    assert listed_names(dialog) == ["Home", "Work"]
    dialog.rows[1].name = "Job"  # as the editor does
    dialog.rows.append(TagRow("New", "#0000ff", "Ctrl+Alt+2", ("mail",)))
    second = dialog.list.topLevelItem(1)
    assert second is not None
    dialog.list.setCurrentItem(second)
    dialog.move_tag(-1)
    dialog.deleted.append(dialog.rows.pop(dialog.rows.index(next(row for row in dialog.rows if row.name == "Home"))))
    commands = dialog.commands()
    assert commands[0] == ("tag.delete", {"name": "Home"})
    assert ("tag.rename", {"name": "Work", "new_name": "Job"}) in commands and ("tag.create", {"name": "New", "colour": "#0000ff"}) in commands
    assert [values["name"] for command, values in commands if command == "tag.edit"] == ["Job", "New"]
    dialog.tabs.setCurrentIndex(3)  # Tasks: only the tags used for tasks
    assert listed_names(dialog) == ["Job"]


def test_save_and_close_makes_the_tags(qtbot: QtBot, world: World) -> None:
    saved: list[bool] = []
    dialog = TagsDialog(world.backend, (), lambda: saved.append(True))
    qtbot.addWidget(dialog)
    dialog.rows.append(TagRow("Later", "#00aa00", "Ctrl+Alt+5", ("mail", "events")))
    dialog.save()
    wait(qtbot, lambda: bool(saved))
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
