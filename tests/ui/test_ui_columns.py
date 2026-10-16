from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QToolBar
from pytestqt.qtbot import QtBot

from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, cell_text, column_widths, date_text, valid_columns
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.models import CONTACT_ROLE, HEADER_ICON_ROLE
from chrono_mail.ui.sections import CONTACTS
from tests.ui.world import account_node, World, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columns.py 0.5.153"

# --- Version history ----------------------------------------------------
# v0.5.153: A new sort order keeps the selected message selected (folder, Favorites, a page not loaded yet)
# v0.5.152: A click on a column header sorts a Favorites view too, with its group headers; the standard columns start with Flag, Unread, Attachment (0.5.150)
# v0.5.140: the first column can be dragged, and the new order is saved; a group row still starts at the left edge
# v0.3.109: The icon columns sort too (yes first); a click on Delete leaves the sort arrow where it was
# v0.2.97: Row height in the Message list window
# v0.2.96: The column list of the Message list window is a part of its own
# v0.1.89: Column fit, row heights, the Contact and Delete columns
# v0.0.77: Received instead of Date
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.41: Initial tests of the columns, the reading pane and message windows


class Row:
    flags: tuple[str, ...] = ("flagged",)
    attachments = True
    sender = "Ann Archer <ann@example.org>"
    recipients = "Bob <bob@example.org>, carol@example.org"
    subject = "Plans"
    date = datetime(2026, 3, 4, 14, 5, tzinfo=UTC)
    size = 2048
    folder = "Inbox"
    thread_size = 3
    preview = "first words"


def test_every_column_shows_something_sensible() -> None:
    row = Row()
    assert cell_text("status", row, False) == "●" and cell_text("flag", row, False) == "⚑" and cell_text("attachment", row, False) == "📎" and cell_text("replied", row, False) == ""
    assert cell_text("from_to", row, False) == "Ann Archer" and cell_text("from_to", row, True) == "Bob" and cell_text("from", row, True) == "Ann Archer"
    assert cell_text("to", row, False).startswith("Bob") and cell_text("subject", row, False) == "Plans" and cell_text("size", row, False) == "2.0 KB"
    assert cell_text("folder", row, False) == "Inbox" and cell_text("conversation", row, False) == "3" and cell_text("preview", row, False) == "first words"
    assert cell_text("unknown", row, False) == "" and {column.column_id for column in COLUMNS} >= set(DEFAULT_COLUMNS)


def test_date_styles() -> None:
    moment = datetime(2020, 3, 4, 14, 5, tzinfo=UTC)
    assert date_text(moment, "iso") == moment.astimezone().strftime("%Y-%m-%d %H:%M") and date_text(moment, "full") == moment.astimezone().strftime("%d %b %Y %H:%M")
    assert "2020" in date_text(moment, "short")
    assert date_text(None, "iso") == ""


def test_saved_columns_and_widths_are_checked() -> None:
    assert valid_columns(["subject", "nonsense", "subject", "date"]) == ("subject", "date") and valid_columns([]) == DEFAULT_COLUMNS and valid_columns(["x"]) == DEFAULT_COLUMNS
    assert column_widths('{"subject": 400, "bad": 5, "date": "x", "size": 3, "from": 99999}') == {"subject": 400}
    assert column_widths("not json") == {} and column_widths("[]") == {}


def test_the_list_is_a_table_by_default_with_the_standard_columns(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    model = world.window.list_model
    assert model.columns == DEFAULT_COLUMNS and model.columnCount() == len(DEFAULT_COLUMNS) and not world.window.list_view.header().isHidden()
    subject_column = model.columns.index("subject")
    assert model.index(0, subject_column).data().startswith("Message") and model.headerData(subject_column, Qt.Orientation.Horizontal) == "Subject"
    assert model.index(0, model.columns.index("status")).data() == "●" and model.headerData(model.columns.index("status"), Qt.Orientation.Horizontal) == "●" and model.columns[:3] == ("flag", "status", "attachment")
    assert model.index(0, subject_column).data(Qt.ItemDataRole.FontRole).bold()  # unread is bold
    assert model.index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) is not None and model.index(99, 0).data() is None


def test_clicking_a_column_header_sorts_and_clicking_again_reverses(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    columns = world.window.list_model.columns
    subject, size = columns.index("subject"), columns.index("size")
    world.window.list_view.header().sectionClicked.emit(subject)
    wait(qtbot, lambda: world.window._sort == "subject" and world.window._oldest_first)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 1")  # A to Z
    world.window.list_view.header().sectionClicked.emit(subject)
    wait(qtbot, lambda: world.window._oldest_first is False)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 3")
    world.window.list_view.header().sectionClicked.emit(size)
    wait(qtbot, lambda: world.window._sort == "size")  # noqa: SLF001


def test_a_click_on_a_column_header_sorts_a_favorites_view_with_its_groups(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    favorites = window.tree_model.index(0, 0)
    unread = next(window.tree_model.index(row, 0, favorites) for row in range(window.tree_model.rowCount(favorites)) if window.tree_model.index(row, 0, favorites).data() == "Unread")
    window.tree.setCurrentIndex(unread)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.combined == "unread" and window.list_model.rowCount() == 3)
    window.act_show_groups.setChecked(True)
    header = window.list_view.header()
    subject = window.list_model.columns.index("subject")

    def click_subject() -> None:
        middle = QPoint(header.sectionViewportPosition(subject) + header.sectionSize(subject) // 2, header.height() // 2)
        qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=middle)

    def subjects() -> list[str]:
        model = window.list_model
        return [str(model.index(row, subject).data()) for row in range(model.rowCount()) if not model.is_header(row)]

    def first_group() -> str:
        model = window.list_model
        return str(model.index(0, 0).data()) if model.rowCount() and model.is_header(0) else ""

    click_subject()
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject" and window.list_model.spec.oldest_first)
    wait(qtbot, lambda: subjects() == ["Message 1", "Message 2", "Message 3"])  # A to Z
    assert "Message 1" in first_group()  # the group headers follow the subject
    click_subject()
    wait(qtbot, lambda: subjects() == ["Message 3", "Message 2", "Message 1"])
    assert "Message 3" in first_group()


def test_an_icon_column_sorts_yes_first_and_delete_does_not_sort(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    columns = world.window.list_model.columns
    subject, flag, trash = columns.index("subject"), columns.index("flag"), columns.index("trash")
    first = next(row_of(world.window, position) for position in range(3) if row_of(world.window, position).subject == "Message 1")
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, flagged=True)
    header = world.window.list_view.header()
    header.sectionClicked.emit(flag)
    wait(qtbot, lambda: world.window._sort == "flagged" and not world.window._oldest_first)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 1")  # the flagged message on top
    assert header.sortIndicatorSection() == flag and world.window.sort_button.text() == "Sorted by Flagged ▾"
    header.sectionClicked.emit(flag)
    wait(qtbot, lambda: world.window.list_model.index(2, subject).data() == "Message 1")  # and at the bottom
    middle = QPoint(header.sectionViewportPosition(trash) + header.sectionSize(trash) // 2, header.height() // 2)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=middle)  # a real click: Qt moves the arrow by itself
    # the one column that cannot sort changes nothing, also not the arrow
    assert world.window._sort == "flagged" and header.sortIndicatorSection() == flag  # noqa: SLF001


def test_columns_can_be_toggled_from_the_header_and_are_remembered(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window._toggle_column("preview")  # noqa: SLF001
    assert world.window.list_model.columns[-1] == "preview" and "preview" in str(world.window.settings.value("columns"))
    world.window._toggle_column("size")  # noqa: SLF001
    assert "size" not in world.window.list_model.columns
    for column in list(world.window.list_model.columns)[1:]:
        world.window._toggle_column(column)  # noqa: SLF001
    assert len(world.window.list_model.columns) == 1  # the last one cannot be switched off


def test_the_column_editor_returns_the_choice(qtbot: QtBot) -> None:
    dialog = ColumnsDialog(ListChoice("columns", ("subject", "date"), "bottom", "iso", "roomy"))
    qtbot.addWidget(dialog)
    assert dialog.density_box.currentData() == "roomy"
    shown = [dialog.list.item(row).text() for row in range(dialog.list.count()) if dialog.list.item(row).checkState() == Qt.CheckState.Checked]
    assert shown == ["Subject", "Received"] and dialog.list.item(0).text() == "Subject" and dialog.pane_box.currentData() == "bottom" and dialog.date_box.currentData() == "iso"
    dialog.list.setCurrentRow(1)
    dialog.up_button.click()
    dialog.list.item(2).setCheckState(Qt.CheckState.Checked)
    third = str(dialog.list.item(2).data(Qt.ItemDataRole.UserRole))
    dialog.density_box.setCurrentIndex(dialog.density_box.findData("compact"))
    assert dialog.choice() == ListChoice("columns", ("date", "subject", third), "bottom", "iso", "compact")
    dialog.reset_button.click()
    assert dialog.choice().columns == DEFAULT_COLUMNS
    dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("cards"))
    assert not dialog.list.isEnabled() and not dialog.up_button.isEnabled() and dialog.choice().layout == "cards"
    for item_row in range(dialog.list.count()):
        dialog.list.item(item_row).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.choice().columns == DEFAULT_COLUMNS  # never none


def test_the_window_applies_the_editor_and_keeps_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)

    def fake_exec(self: ColumnsDialog) -> QDialog.DialogCode:
        self.layout_box.setCurrentIndex(self.layout_box.findData("cards"))
        self.pane_box.setCurrentIndex(self.pane_box.findData("bottom"))
        self.date_box.setCurrentIndex(self.date_box.findData("iso"))
        self.density_box.setCurrentIndex(self.density_box.findData("roomy"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ColumnsDialog, "exec", fake_exec)
    world.window.act_columns.trigger()
    settings: QSettings = world.window.settings
    assert world.window.list_model.columns == ("card",) and world.window.list_view.header().isHidden() and world.window.list_model.date_style == "iso"
    assert world.window.content_splitter.orientation() == Qt.Orientation.Vertical and str(settings.value("list_layout")) == "cards" and str(settings.value("reading_pane")) == "bottom"
    assert str(settings.value("list_density")) == "roomy" and world.window.column_delegate.row_height == 36
    assert world.window.pane_actions["bottom"].isChecked() and not world.window.pane_actions["right"].isChecked()
    row = row_of(world.window, 0)
    assert row is not None


def test_the_reading_pane_can_be_switched_off_and_a_message_opens_in_a_window(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0))
    wait(qtbot, lambda: world.window._shown is not None)  # noqa: SLF001
    world.window.set_reading_pane("off")
    assert world.window.reading_pane.isHidden() and world.window._shown is None  # noqa: SLF001
    world.window.list_view.setCurrentIndex(world.window.list_model.index(1))
    qtbot.wait(150)
    assert world.window._shown is None  # nothing is opened, so nothing is marked read  # noqa: SLF001
    world.window.act_open.trigger()
    wait(qtbot, lambda: len(world.window._message_windows) == 1)  # noqa: SLF001
    window: MessageWindow = world.window._message_windows[0]  # noqa: SLF001
    assert window.windowTitle().startswith("Message") and window.isVisible()
    wait(qtbot, lambda: "seen" in row_of(world.window, 1).flags)
    window.close()
    world.window.set_reading_pane("right")
    assert not world.window.reading_pane.isHidden() and world.window.content_splitter.orientation() == Qt.Orientation.Horizontal
    wait(qtbot, lambda: world.window._shown is not None)  # noqa: SLF001 - the pane shows the selected message again


def test_a_message_window_can_start_a_reply(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0))
    world.window.open_selected()
    wait(qtbot, lambda: len(world.window._message_windows) == 1)  # noqa: SLF001
    window: MessageWindow = world.window._message_windows[0]  # noqa: SLF001
    window.findChildren(QToolBar)[0].actions()[0].trigger()
    wait(qtbot, lambda: len(world.window._composers) == 1)  # noqa: SLF001
    composer = world.window._composers[0]  # noqa: SLF001
    assert composer.subject.text().startswith("Re: Message")
    composer.body.document().setModified(False)
    composer._finished = True  # noqa: SLF001
    composer.close()
    window.close()


def test_in_the_sent_folder_the_from_to_column_shows_the_recipient(world: World, qtbot: QtBot) -> None:
    world.call("mail.send", account=world.account_id, to="Dora <dora@example.org>", subject="Out", body="x")
    world.window.start()
    settle(qtbot, world, 3)
    account = account_node(world.window)
    for position in range(world.window.tree_model.rowCount(account)):
        index = world.window.tree_model.index(position, 0, account)
        if index.data(256 + 2) == "Sent":
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1 and world.window.list_model.row_at(0) is not None)
    assert world.window.list_model.index(0, world.window.list_model.columns.index("from_to")).data() == "Dora" and BY_ID["from_to"].label == "From / To"


def shown(world: World, qtbot: QtBot) -> None:
    world.window.resize(1200, 600)
    world.window.show()
    world.window.start()
    settle(qtbot, world, 3)
    qtbot.wait(50)


def test_the_columns_fill_the_list_and_the_last_has_no_drag_line(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    view, model = world.window.list_view, world.window.list_model
    header = view.header()
    assert model.columns[-2:] == ("contact", "trash")  # the new columns are on the right by default
    assert sum(header.sectionSize(index) for index in range(header.count())) == view.viewport().width()
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert header.sectionResizeMode(header.logicalIndex(header.count() - 1)) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(model.columns.index("subject")) == QHeaderView.ResizeMode.Interactive
    assert header.sectionSize(model.columns.index("status")) == 24
    world.window.resize(900, 600)
    qtbot.wait(50)
    assert sum(header.sectionSize(index) for index in range(header.count())) == view.viewport().width()
    subject, sender = model.columns.index("subject"), model.columns.index("from_to")
    header.resizeSection(sender, header.sectionSize(sender) + 40)  # as a drag does
    assert sum(header.sectionSize(index) for index in range(header.count())) == view.viewport().width()  # Subject and the others gave the room
    assert '"shares"' in str(world.window.settings.value("column_layout")) and header.sectionSize(subject) > 50


def test_rows_have_the_height_of_the_density(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    view = world.window.list_view
    assert view.visualRect(world.window.list_model.index(0, 4)).height() == 29
    world.window.settings.setValue("list_density", "compact")
    world.window._apply_list_look()  # noqa: SLF001
    qtbot.wait(50)
    assert view.visualRect(world.window.list_model.index(0, 4)).height() == 22


def test_a_click_on_the_delete_icon_deletes_that_message_only(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    view, model = world.window.list_view, world.window.list_model
    view.setCurrentIndex(model.index(0, 4))
    rect = view.visualRect(model.index(1, model.columns.index("trash")))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    wait(qtbot, lambda: model.rowCount() == 2)
    assert next(info for info in world.call("folder.list", account=world.account_id) if info.path == "Inbox").messages == 2


def test_the_contact_icon_shows_for_contacts_and_opens_the_contact(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    model, view = world.window.list_model, world.window.list_view
    column = model.columns.index("contact")
    assert model.index(0, column).data(CONTACT_ROLE) is None
    nobody = view.visualRect(model.index(0, column)).center()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=nobody)  # no icon: nothing happens
    assert world.window._section != CONTACTS  # noqa: SLF001
    world.call("pim.enable")
    book = next(item.collection for item in world.call("collection.list") if item.kind == "contacts")
    sender = str(getattr(model.row_at(0), "sender"))
    address = sender.split("<")[-1].rstrip(">").strip()
    added = world.call("contact.add", collection=book, name="The sender", email=(address,))
    world.window._load_contact_addresses()  # noqa: SLF001
    wait(qtbot, lambda: model.index(0, column).data(CONTACT_ROLE) == (added.item, book))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=nobody)
    page = world.window.contacts_page
    wait(qtbot, lambda: world.window._section == CONTACTS and page._selected_items() == [added.item])  # noqa: SLF001


def test_contact_and_delete_are_added_once_to_a_saved_set_of_columns(world: World) -> None:
    settings = world.window.settings
    settings.setValue("columns", "subject,date")
    settings.remove("columns_contact_and_delete_added")
    assert world.window._saved_columns() == ("subject", "date", "contact", "trash")  # noqa: SLF001
    settings.setValue("columns", "subject,date")  # the user took them away again
    assert world.window._saved_columns() == ("subject", "date")  # noqa: SLF001


def test_the_contact_and_delete_headers_are_grey_icons(world: World) -> None:
    from PySide6.QtGui import QGuiApplication, QIcon, QPalette

    model = world.window.list_model
    model.set_columns(("subject", "contact", "trash"))
    grey = QGuiApplication.palette().color(QPalette.ColorRole.PlaceholderText).rgb() & 0xFFFFFF
    for column in (1, 2):
        icon = model.headerData(column, Qt.Orientation.Horizontal, HEADER_ICON_ROLE)
        assert model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DecorationRole) is None  # the header draws it, in the middle
        assert isinstance(icon, QIcon) and not icon.isNull() and model.headerData(column, Qt.Orientation.Horizontal) == ""
        image = icon.pixmap(16, 16).toImage()
        inked = {image.pixelColor(x, y).rgb() & 0xFFFFFF for x in range(16) for y in range(16) if image.pixelColor(x, y).alpha() > 0}
        assert inked == {grey}  # wholly grey, also the bin that is red elsewhere
    assert model.headerData(0, Qt.Orientation.Horizontal, HEADER_ICON_ROLE) is None


def test_a_line_dragged_with_the_mouse_stays_under_the_pointer(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import QPoint

    shown(world, qtbot)
    model, header = world.window.list_model, world.window.list_view.header()
    contact, trash = model.columns.index("contact"), model.columns.index("trash")
    viewport = header.viewport()
    for column, line_of in ((contact, model.columns.index("size")), (trash, contact)):  # Size | Contact, then Contact | Delete
        before = header.sectionSize(column)
        line = header.sectionViewportPosition(line_of) + header.sectionSize(line_of) - 1
        y = header.height() // 2
        qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(line, y))
        for step in range(1, 11):  # ten moves of one pixel to the left
            qtbot.mouseMove(viewport, QPoint(line - step, y))
        qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(line - 10, y))
        assert header.sectionSize(column) == before + 10  # the column grew by exactly the distance moved
        assert sum(header.sectionSize(index) for index in range(header.count())) == world.window.list_view.viewport().width()
    assert header.sectionSize(contact) == 34  # Contact kept its width while Delete grew


def test_the_first_column_can_be_dragged_to_another_place(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    window, header = world.window, world.window.list_view.header()
    first = window.list_model.columns[header.logicalIndex(0)]
    assert header.isFirstSectionMovable()
    header.moveSection(0, 2)  # what a drag of the first column header does
    assert window.list_model.columns[header.logicalIndex(2)] == first
    order = str(window.settings.value("columns")).split(",")
    assert order.index(first) == 2  # remembered


def test_a_group_row_starts_at_the_left_edge_whatever_the_order_of_the_columns(world: World, qtbot: QtBot) -> None:
    from PySide6.QtGui import QColor

    from chrono_mail.ui.colours import group_header_colour

    shown(world, qtbot)
    window, view = world.window, world.window.list_view
    window.act_show_groups.setChecked(True)  # View > Show groups
    wait(qtbot, lambda: bool(window.list_model.header_rows()) and world.backend._pending == {})  # noqa: SLF001
    header_rows = window.list_model.header_rows()
    view.header().moveSection(0, 1)  # the first column (Unread) to second place
    qtbot.wait(50)
    row_rect = view.visualRect(window.list_model.index(header_rows[0], 0))
    image = view.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    left = image.pixelColor(int(3 * ratio), int(row_rect.center().y() * ratio))
    assert left == QColor(group_header_colour(view.palette()))  # the bar, not an empty cell



def selected_message(world: World) -> str | None:
    window = world.window
    current = window.list_view.currentIndex()
    row = window.list_model.row_at(current.row()) if current.isValid() else None
    return str(getattr(row, "message_id")) if row is not None else None


def select_subject(world: World, qtbot: QtBot, subject: str) -> str:
    """Select the message with this subject (it is shown in the reading pane); its id."""
    model = world.window.list_model
    column = model.columns.index("subject")
    row = next(number for number in range(model.rowCount()) if model.index(number, column).data() == subject)
    world.window.list_view.setCurrentIndex(model.index(row, 0))
    message_id = str(getattr(model.row_at(row), "message_id"))
    wait(qtbot, lambda: world.window._shown is not None and world.window._shown[2] == message_id)  # noqa: SLF001
    return message_id


def sort_by_subject(world: World) -> None:
    world.window.list_view.header().sectionClicked.emit(world.window.list_model.columns.index("subject"))


@pytest.mark.parametrize("favorites", [False, True])
def test_a_new_sort_order_keeps_the_selected_message_selected(world: World, qtbot: QtBot, favorites: bool) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    if favorites:
        top = window.tree_model.index(0, 0)
        unread = next(window.tree_model.index(row, 0, top) for row in range(window.tree_model.rowCount(top)) if window.tree_model.index(row, 0, top).data() == "Unread")
        window.tree.setCurrentIndex(unread)
        wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.combined == "unread" and window.list_model.rowCount() == 3)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    chosen = select_subject(world, qtbot, "Message 2")
    sort_by_subject(world)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject" and selected_message(world) == chosen)
    assert window._shown is not None and window._shown[2] == chosen  # noqa: SLF001 - the reading pane shows the same message


def test_the_selected_message_is_found_on_a_page_that_was_not_loaded(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.ui import models

    monkeypatch.setattr(models, "PAGE_SIZE", 1)  # one message per page: after the sort only the first page is loaded
    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.message_count() == 3 and world.backend._pending == {})  # noqa: SLF001
    for row in range(3):
        window.list_model.row_at(row, fetch=True)
    wait(qtbot, lambda: all(window.list_model.row_at(row) is not None for row in range(3)))
    chosen = select_subject(world, qtbot, "Message 3")  # newest first: on the first page; A to Z: the last
    sort_by_subject(world)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject" and selected_message(world) == chosen)
    assert window.list_model.find(chosen) == 2


def test_choosing_another_message_meanwhile_wins(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window._reselect = "a message that is still being looked for"  # noqa: SLF001
    select_subject(world, qtbot, "Message 1")
    assert getattr(window, "_reselect") is None  # read again: choosing a message cleared it
