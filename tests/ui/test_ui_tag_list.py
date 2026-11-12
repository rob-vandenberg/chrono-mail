from collections.abc import Callable

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QKeyEvent, QKeySequence, QPainter, QStandardItemModel
from PySide6.QtWidgets import QApplication, QCheckBox, QColorDialog, QComboBox, QLineEdit, QMessageBox, QRadioButton, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_pim import RenameParameters, TagInfo
from chrono_mail.ui.columns import BY_ID
from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.keyfield import KeyField
from chrono_mail.ui.models import COUNT_ROLE
from chrono_mail.ui.tagbox import TagBox
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import MARK_GAP, MARK_WIDTH, paint_tag_marks
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.tagsdialog import COLOUR, DELETE, NAME, NOT_ASSIGNED, SHORTCUT, SHOW_IN, TagRow, TagsPanel
from chrono_mail.ui.taskeditor import TaskEditor
from tests.ui.test_ui_contacts import open_contacts
from tests.ui.test_ui_contacts import seed as seed_contacts
from tests.ui.test_ui_tags import tag_rows
from tests.ui.test_ui_tasks import open_tasks, seed, select
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tag_list.py 4.4.581"

# --- Version history ----------------------------------------------------
# v4.4.581: Initial: Settings > Tags edited in place, the key fields' ✕, Show in per section, the sidebars' numbers, the Tags box of contacts
#           and tasks, the Tags column (the owner, D-327, D-332)


def panel_with(world: World, qtbot: QtBot, *tags: tuple[str, str, str, tuple[str, ...]]) -> TagsPanel:
    """Settings > Tags with these tags (name, colour, shortcut, shown in), on screen."""
    for name, colour, shortcut, kinds in tags:
        world.call("tag.create", name=name, colour=colour)
        world.call("tag.edit", name=name, shortcut=shortcut, use_for=kinds)
    panel = TagsPanel(world.backend, tuple(world.call("tag.list")))
    qtbot.addWidget(panel)
    panel.resize(700, 360)
    panel.show()
    qtbot.waitExposed(panel)
    return panel


def cell(panel: TagsPanel, row: TagRow, column: int) -> QWidget:
    line = panel.line_of(row)
    assert line is not None
    found = panel.list.itemWidget(line, column)
    assert found is not None
    return found


def tick(panel: TagsPanel, row: TagRow, kind: str) -> QCheckBox:
    found = cell(panel, row, dict(SHOW_IN)[kind]).findChild(QCheckBox)
    assert found is not None
    return found


def shortcut_box(panel: TagsPanel, row: TagRow) -> QComboBox:
    found = cell(panel, row, SHORTCUT)
    assert isinstance(found, QComboBox)
    return found


def named(panel: TagsPanel, name: str) -> TagRow:
    return next(row for row in panel.rows if row.name == name)


def test_each_tag_is_one_line_with_its_colour_show_in_shortcut_and_delete(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Lakeside", "#33b679", "", ("mail", "contacts")), ("Important", "#f6bf26", "Ctrl+Alt+1", ("mail", "events", "contacts", "tasks")))
    lakeside, important = named(panel, "Lakeside"), named(panel, "Important")
    assert panel.list.topLevelItemCount() == 2 and panel.rows == [lakeside, important]  # one list, no tabs (D-332)
    assert [tick(panel, lakeside, kind).isChecked() for kind, _ in SHOW_IN] == [True, False, True, False]
    assert shortcut_box(panel, important).currentText() == "Ctrl+Alt+1" and shortcut_box(panel, lakeside).currentText() == NOT_ASSIGNED
    assert cell(panel, lakeside, COLOUR).findChild(QToolButton) is not None
    delete = cell(panel, lakeside, DELETE).findChild(QToolButton)
    assert delete is not None and delete.text() == "✕"
    assert panel.show_in.text() == "Show in"
    sizes = panel.list.header()
    assert sizes.sectionResizeMode(NAME) == sizes.ResizeMode.Stretch and sizes.sectionSize(NAME) > 3 * sizes.sectionSize(COLOUR)  # the name takes the room left
    header = panel.list.headerItem()
    assert [header.text(column) for column in range(panel.list.columnCount())] == ["Colour", "Tag", "Mail", "Calendar", "Contacts", "Tasks", "Shortcut", ""]  # the colour first (the owner)
    line = panel.line_of(lakeside)
    assert line is not None
    panel.list.setCurrentItem(line, COLOUR)
    panel.activateWindow()  # the window the person works in has the keys (F2 is the main window's Rename folder… there)
    wait(qtbot, lambda: QApplication.activeWindow() is panel)
    QApplication.sendEvent(panel.list, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier))
    wait(qtbot, lambda: panel.list.state() == panel.list.State.EditingState)
    editors = [child for child in panel.list.viewport().findChildren(QLineEdit) if child.isVisible()]
    name_cell = panel.list.visualItemRect(line).adjusted(0, 0, 0, 0)
    name_cell.setLeft(panel.list.header().sectionViewportPosition(NAME))
    assert len(editors) == 1 and editors[0].geometry().left() >= name_cell.left()  # F2 edits the name, not the colour cell


def test_a_name_is_changed_in_place_and_one_another_tag_has_is_refused_with_why(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Lakeside", "#33b679", "", ("mail",)), ("Important", "#f6bf26", "", ("mail",)))
    lakeside = named(panel, "Lakeside")
    panel.rename(lakeside, "important")
    assert lakeside.name == "Lakeside" and "There is a tag named" in panel.status.text()  # capitals do not make another tag
    panel.rename(lakeside, "   ")
    assert lakeside.name == "Lakeside"  # empty: nothing changes
    panel.rename(lakeside, "  Harbourline  ")
    assert lakeside.name == "Harbourline" and panel.status.text() == ""
    commands = [(step.command.id, step.parameters) for step in panel.commands()]  # type: ignore[attr-defined]
    assert [parameters for command, parameters in commands if command == "tag.rename"] == [RenameParameters("Lakeside", "Harbourline")]
    line = panel.line_of(lakeside)
    assert line is not None and bool(line.flags() & Qt.ItemFlag.ItemIsEditable)  # a click on the selected one or F2 edits it in place


def test_the_colour_square_opens_the_colour_picker(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = panel_with(world, qtbot, ("Lakeside", "#33b679", "", ("mail",)))
    asked: list[str] = []

    def pick(colour: QColor, parent: QWidget, title: str) -> QColor:
        asked.append(colour.name())
        return QColor("#d50000")

    monkeypatch.setattr(QColorDialog, "getColor", pick)
    lakeside = named(panel, "Lakeside")
    square = cell(panel, lakeside, COLOUR).findChild(QToolButton)
    assert square is not None
    square.click()
    assert asked == ["#33b679"] and lakeside.colour == "#d50000"


def test_the_last_section_a_tag_is_shown_in_cannot_be_unticked(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Lakeside", "#33b679", "", ("mail", "contacts")))
    lakeside = named(panel, "Lakeside")
    tick(panel, lakeside, "contacts").setChecked(False)
    assert lakeside.kinds == ("mail",)
    assert not tick(panel, lakeside, "mail").isEnabled() and tick(panel, lakeside, "mail").toolTip()  # greyed, with why (OR-15)
    tick(panel, lakeside, "tasks").setChecked(True)
    assert lakeside.kinds == ("mail", "tasks") and tick(panel, lakeside, "mail").isEnabled()


def test_a_shortcut_another_tag_has_is_greyed_with_its_name(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Lakeside", "#33b679", "", ("mail",)), ("Important", "#f6bf26", "Ctrl+Alt+1", ("mail",)))
    box = shortcut_box(panel, named(panel, "Lakeside"))
    assert box.itemText(0) == NOT_ASSIGNED and box.itemText(1) == "Ctrl+Alt+1 — Important"
    model = box.model()
    assert isinstance(model, QStandardItemModel)
    first, second = model.item(1), model.item(2)
    assert first is not None and not first.isEnabled() and second is not None and second.isEnabled()
    box.setCurrentIndex(2)
    box.activated.emit(2)
    assert named(panel, "Lakeside").shortcut == "Ctrl+Alt+2"
    again = shortcut_box(panel, named(panel, "Lakeside"))
    again.setCurrentIndex(0)
    again.activated.emit(0)
    assert named(panel, "Lakeside").shortcut == ""  # Not assigned


def test_the_cross_deletes_a_tag_and_asks_first_when_something_carries_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_contacts(world)  # John carries Important and Key Customer
    world.call("tag.create", name="Spare", colour="#33b679")
    panel = TagsPanel(world.backend, tuple(world.call("tag.list")))
    qtbot.addWidget(panel)
    panel.show()
    asked: list[str] = []
    answer: list[QMessageBox.StandardButton] = [QMessageBox.StandardButton.No]

    def question(parent: QWidget, title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return answer[0]

    monkeypatch.setattr(QMessageBox, "question", question)
    important = named(panel, "Important")
    assert important.carried_by == 1  # from the counts of mail, contacts, appointments and tasks
    cross = cell(panel, important, DELETE).findChild(QToolButton)
    assert cross is not None
    cross.click()
    assert len(asked) == 1 and "1" in asked[0] and important in panel.rows  # No: it stays
    answer[0] = QMessageBox.StandardButton.Yes
    again = cell(panel, important, DELETE).findChild(QToolButton)
    assert again is not None
    again.click()
    assert important not in panel.rows and panel.deleted == [important]
    spare = named(panel, "Spare")
    unused = cell(panel, spare, DELETE).findChild(QToolButton)
    assert unused is not None
    unused.click()
    assert len(asked) == 2 and spare not in panel.rows  # nothing carries it: deleted without a question

def test_add_tag_adds_a_line_shown_everywhere_in_a_colour_no_tag_has(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Tomato", "#d50000", "", ("mail",)))
    panel.add_button.click()
    new = panel.rows[-1]
    assert new.name == "New tag" and new.colour == "#e67c73" and new.kinds == ("mail", "events", "contacts", "tasks")  # Flamingo: Tomato is taken
    wait(qtbot, lambda: panel.list.state() == panel.list.State.EditingState)  # its name ready to type
    panel.add_button.click()
    assert panel.rows[-1].name == "New tag 2"


def test_a_line_dragged_to_another_place_moves_its_tag(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("One", "#d50000", "", ("mail",)), ("Two", "#e67c73", "", ("mail",)), ("Three", "#f4511e", "", ("mail",)))
    assert panel.list.dragDropMode() == panel.list.DragDropMode.InternalMove
    first = panel.list.topLevelItem(0)
    assert first is not None and bool(first.flags() & Qt.ItemFlag.ItemIsDragEnabled) and not first.flags() & Qt.ItemFlag.ItemIsDropEnabled  # never into another line
    line = panel.list.takeTopLevelItem(2)  # what a drop does with the dragged line
    assert line is not None
    panel.list.insertTopLevelItem(0, line)
    panel.list.reordered.emit()
    assert [row.name for row in panel.rows] == ["Three", "One", "Two"]
    assert [parameters.name for command, parameters in ((step.command.id, step.parameters) for step in panel.commands()) if command == "tag.edit"] == ["Three", "One", "Two"]  # type: ignore[attr-defined]


def test_a_key_field_has_a_cross_and_backspace_or_delete_empties_it(qtbot: QtBot) -> None:
    field = KeyField(QKeySequence("Ctrl+K"))
    qtbot.addWidget(field)
    assert field.isClearButtonEnabled()
    for key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
        field.setKeySequence(QKeySequence("Ctrl+K"))
        QApplication.sendEvent(field, QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        assert field.keySequence().isEmpty()


def test_settings_keyboard_has_the_key_fields_with_a_cross(world: World, qtbot: QtBot) -> None:

    started(qtbot, world)
    dialog = SettingsDialog(world.backend, world.window.settings, world.window._settings_context(), world.window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    assert dialog.shortcut_edits and all(isinstance(edit, KeyField) and edit.isClearButtonEnabled() for edit in dialog.shortcut_edits.values())
    edit = next(iter(dialog.shortcut_edits.values()))
    edit.setKeySequence(QKeySequence("Ctrl+K"))
    QApplication.sendEvent(edit, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier))
    assert edit.keySequence().isEmpty()


def test_the_tag_menus_of_tasks_and_contacts_offer_the_tags_shown_there(world: World, qtbot: QtBot) -> None:
    seed(world)
    world.call("tag.create", name="Calendar only", colour="#039be5")
    world.call("tag.edit", name="Calendar only", use_for=("events",))
    world.call("tag.create", name="Errands", colour="#33b679")
    world.call("tag.edit", name="Errands", use_for=("tasks",))
    page = open_tasks(world, qtbot, 4)
    page.reload_tags()
    wait(qtbot, lambda: "Errands" in page._tags_known)  # noqa: SLF001
    select(page, "Pay rent")
    page._fill_tag_menu()  # noqa: SLF001 - what opening the Tag menu does
    offered = [action.text() for action in page.tag_menu.actions()]
    assert "Errands" in offered and "Calendar only" not in offered and "Home" in offered  # Home: a selected task carries it


def test_the_contacts_tag_menu_offers_the_tags_shown_there_and_one_a_contact_carries(world: World, qtbot: QtBot) -> None:
    seed_contacts(world)
    world.call("tag.create", name="Calendar only", colour="#039be5")
    world.call("tag.edit", name="Calendar only", use_for=("events",))
    world.call("tag.edit", name="Business", use_for=("events",))  # Rebecca carries it all the same
    world.call("tag.create", name="Suppliers", colour="#33b679")
    world.call("tag.edit", name="Suppliers", use_for=("contacts",))
    page = open_contacts(world, qtbot, 3)
    page.reload_tags()
    wait(qtbot, lambda: "Suppliers" in page._tag_names())  # noqa: SLF001
    row = next(position for position, contact in enumerate(page.model.rows) if contact.name.startswith("Rebecca"))
    page.list.selectionModel().select(page.model.index(row, 0), page.list.selectionModel().SelectionFlag.ClearAndSelect | page.list.selectionModel().SelectionFlag.Rows)
    page._fill_tag_menu()  # noqa: SLF001 - what opening the Tag menu does
    offered = [action.text() for action in page.tag_menu.actions()]
    assert "Suppliers" in offered and "Calendar only" not in offered and "Business" in offered


def test_the_sidebars_show_the_tags_in_use_with_their_number_drawn_apart(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    world.call("tag.create", name="Unused", colour="#039be5")
    tag_rows(world, qtbot, 0, 2)
    wait(qtbot, lambda: (found := window.folders.find_item(("", "tagview", "Work"))) is not None and found.data(COUNT_ROLE) == 2)
    window.tags.load()
    wait(qtbot, lambda: any(str(getattr(tag, "name")) == "Unused" for tag in window.mail_state.tags))
    assert window.folders.find_item(("", "tagview", "Unused")) is None  # no mail carries it: not listed (D-327)
    found = window.folders.find_item(("", "tagview", "Work"))
    assert found is not None and found.data(Qt.ItemDataRole.DisplayRole) == "Work"
    icon = found.data(Qt.ItemDataRole.DecorationRole)
    assert isinstance(icon, QIcon) and not icon.isNull()  # its coloured tag
    tag_rows(world, qtbot, 1)
    wait(qtbot, lambda: (again := window.folders.find_item(("", "tagview", "Work"))) is not None and again.data(COUNT_ROLE) == 3)  # follows the fact


def test_the_contacts_tags_list_draws_its_numbers_apart(world: World, qtbot: QtBot) -> None:
    seed_contacts(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: page.tag_list.count() > 1)
    item = page.tag_list.item(1)
    assert item.text() == "Business" and item.data(COUNT_ROLE) == 1


def test_the_contact_and_task_windows_have_the_tags_box_without_radio_buttons(world: World, qtbot: QtBot) -> None:
    colours = TagColourBook()
    for editor in (ContactEditor(world.backend, [], ["Work", "Home"], None, "", None, colours), TaskEditor(world.backend, [], ["Work", "Home"], None, "", None, colours)):
        qtbot.addWidget(editor)
        box = editor.tag_box
        assert isinstance(box, TagBox)
        assert not editor.tags._remove_button.isEnabled()  # noqa: SLF001 - greyed until a tag is selected
        editor.tags._fill()  # noqa: SLF001 - what opening Add tag… does
        assert [action.text() for action in editor.tags.menu.actions() if action.text()] == ["Work", "Home", "New tag…"]
        editor.tags.menu.actions()[0].trigger()
        assert box.names() == ["Work"] and box.findChild(QRadioButton) is None  # a tag colours neither: no radio buttons (D-332)
        editor.tags._fill()  # noqa: SLF001
        assert [action.text() for action in editor.tags.menu.actions() if action.text()] == ["Home", "New tag…"]
        box.select("Work")
        assert editor.tags._remove_button.isEnabled()  # noqa: SLF001
        editor.tags._remove_button.click()  # noqa: SLF001
        assert box.names() == []
        editor.reject()


def test_the_tags_column_is_called_tags_and_draws_only_small_tags(world: World, qtbot: QtBot) -> None:
    assert BY_ID["tag"].label == "Tags"
    model = world.window.list_model
    model.set_columns(("subject", "tag"))
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Tags"
    book = TagColourBook()
    drawn: list[QImage] = []
    for selected in (False, True):
        image = QImage(120, 24, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        paint_tag_marks(painter, QRect(0, 0, 120, 24), ("Work", "Home", "VIP"), book, selected, QFont())
        painter.end()
        drawn.append(image)
    assert drawn[0] == drawn[1]  # the selected row as the others: small tags, never names (the owner)
    painted = [x for x in range(120) if any(drawn[1].pixelColor(x, y).alpha() > 0 for y in range(24))]
    assert painted and max(painted) < 4 + 3 * (MARK_WIDTH + MARK_GAP)  # three small tags from the left, nothing else (a name would reach further)


def test_renames_never_join_two_tags(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Alpha", "#d50000", "", ("mail",)), ("Beta", "#e67c73", "", ("mail",)), ("Gamma", "#f4511e", "", ("mail",)))
    alpha, beta = named(panel, "Alpha"), named(panel, "Beta")
    panel.rename(beta, "Delta")
    panel.rename(alpha, "Beta")  # Alpha above Beta: sent first, Beta would still exist
    renames = [(parameters.name, parameters.new_name) for command, parameters in ((step.command.id, step.parameters) for step in panel.commands()) if command == "tag.rename"]  # type: ignore[attr-defined]
    assert renames == [("Beta", "Delta"), ("Alpha", "Beta")]
    gamma = named(panel, "Gamma")
    panel.rename(gamma, "Gamma2")
    panel.rename(named(panel, "Delta"), "Gamma")  # Gamma's old name, given back: after Gamma's own rename
    renames = [(parameters.name, parameters.new_name) for command, parameters in ((step.command.id, step.parameters) for step in panel.commands()) if command == "tag.rename"]  # type: ignore[attr-defined]
    assert renames.index(("Gamma", "Gamma2")) < renames.index(("Beta", "Gamma"))


def test_two_tags_that_swap_names_go_through_a_passing_name(world: World, qtbot: QtBot) -> None:
    panel = panel_with(world, qtbot, ("Left", "#d50000", "", ("mail",)), ("Right", "#e67c73", "", ("mail",)))
    left, right = named(panel, "Left"), named(panel, "Right")
    left.name, right.name = "Right", "Left"  # as two renames in place give, after the first went through
    renames = [(parameters.name, parameters.new_name) for command, parameters in ((step.command.id, step.parameters) for step in panel.commands()) if command == "tag.rename"]  # type: ignore[attr-defined]
    assert renames == [("Left", "Left (renaming)"), ("Right", "Left"), ("Left (renaming)", "Right")]
    panel.save(lambda: None, lambda: None)
    wait(qtbot, lambda: {str(tag.name): str(tag.colour) for tag in world.call("tag.list")} == {"Right": "#d50000", "Left": "#e67c73"})


def test_the_mail_tag_menu_offers_a_tag_not_shown_in_mail_that_a_selected_message_carries(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QMenu

    started(qtbot, world)
    tag_rows(world, qtbot, 0, name="Planning")
    world.call("tag.edit", name="Planning", use_for=("events",))
    world.window.tags.load()
    wait(qtbot, lambda: any(str(tag.name) == "Planning" and tuple(tag.kinds) == ("events",) for tag in world.window.mail_state.tags))
    menu = QMenu()
    from tests.ui.test_ui_tags import select as select_mail
    select_mail(world, 0)
    world.window.tags.fill_tag_menu(menu)
    assert "Planning" in [action.text() for action in menu.actions()]  # it can be taken off again
    select_mail(world, 1)
    world.window.tags.fill_tag_menu(menu)
    assert "Planning" not in [action.text() for action in menu.actions()]


def test_facts_that_come_while_the_tags_are_counted_ask_once_more(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    tags = world.window.tags
    asked: list[int] = []
    original = tags.load

    def counted() -> None:
        asked.append(1)
        original()

    tags._again.timeout.disconnect()  # noqa: SLF001
    tags._again.timeout.connect(counted)  # noqa: SLF001
    tags._counting = True  # noqa: SLF001 - an answer is on its way
    for _ in range(5):
        tags._fact(Change("added", world.account_id, "INBOX", "m1"))  # noqa: SLF001
    qtbot.wait(50)
    assert asked == []  # never a count per fact while one runs
    tags._answered()  # noqa: SLF001 - the answer came
    wait(qtbot, lambda: len(asked) == 1)


def test_the_tag_shown_stays_in_the_sidebar_when_its_last_mail_loses_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    tag_rows(world, qtbot, 0)
    wait(qtbot, lambda: window.folders.find_item(("", "tagview", "Work")) is not None)
    found = window.folders.find_item(("", "tagview", "Work"))
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.combined == "tagged" and window.list_model.rowCount() == 1)
    from tests.ui.test_ui_tags import select as select_mail
    select_mail(world, 0)
    window.tags.toggle_tag("Work", False)  # the last mail loses it, while its mail is shown
    wait(qtbot, lambda: any(str(tag.name) == "Work" and tag.mails == 0 for tag in window.mail_state.tags))  # the folder list is laid out with them in the same step
    entry = window.folders.find_item(("", "tagview", "Work"))
    assert entry is not None and window.mail_state.view.combined == "tagged" and window.mail_state.view.combined_tag == "Work"  # nothing jumps (D-146)
    inbox = window.folders.find_item(("", "favorite", "inboxes"))
    assert inbox is not None
    window.tree.setCurrentIndex(inbox)  # left: the tag no mail carries goes
    wait(qtbot, lambda: window.folders.find_item(("", "tagview", "Work")) is None)


def test_an_older_answer_of_the_tags_never_overwrites_a_newer_one(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    tags = world.window.tags
    held: list[Callable[[tuple[TagInfo, ...]], None]] = []

    def hold(command: object, parameters: object, done: Callable[[tuple[TagInfo, ...]], None], failed: object, owner: object = None) -> None:
        held.append(done)

    monkeypatch.setattr(tags._backend, "ask", hold)  # noqa: SLF001 - the answers come when the test gives them
    tags.load()
    tags.load()
    older, newer = held
    fresh = (TagInfo("Fresh", "#d50000", 0, 0, 0),)
    newer(fresh)
    older((TagInfo("Stale", "#039be5", 0, 0, 0),))  # the older answer comes last
    assert [str(tag.name) for tag in world.window.mail_state.tags] == ["Fresh"]
