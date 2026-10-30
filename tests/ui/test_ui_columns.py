from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QHeaderView, QToolBar
from pytestqt.qtbot import QtBot

from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, cell_text, date_text, valid_columns
from chrono_mail.ui.columnsetup import STARTING, read_setups
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.messagelist import CONTACT_ROLE, HEADER_ICON_ROLE
from chrono_mail.ui.sections import CONTACTS
from tests.ui.world import account_node, World, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columns.py 4.2.436"

# --- Version history ----------------------------------------------------
# v4.2.436: Reset (was Standard columns) is the picker's
# v4.2.430: the stand-in row has to_shown (the person From / To shows for mail one sent, D-295)
# v3.6.360: the Delete icon test waits for the engine's delete, not only for the row (it leaves at the click since 3.6.353, D-266)
# v2.0.280: Columns per folder (B-16): the standard columns of incoming mail have From; Sent starts with To and Sent; the widths are kept per folder; the one setup of earlier versions is handed over to incoming mail
# v1.9.265: the header icons are checked in the program's own grey, with a tolerance for the soft edges (Qt's own grey is black: it passed without testing grey)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: The list's PAGE_SIZE and roles from messagelist.py (R-15)
# v1.3.255: a column added or removed, and cards and back, keep the selected messages, the current one and the scroll; the list is never reset (R-13)
# v1.1.209: a dragged column keeps its place when a column is added and when another folder opens (D-179)
# v0.5.165: going back to the message on screen calls off the one still opening
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.162: a message read in Favorites > Unread stays there after a sort and a fetch, until the view is left
# v0.5.161: a delete spans no group header again, and the headers keep their bar
# v0.5.160: After a new sort the whole row stays selected and Delete deletes it (folder, Favorites); several selected messages stay selected; a message chosen meanwhile wins; fetching the list again keeps the whole row; the list is never reset
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
    to_shown = ""  # not filled in yet: From / To reads the recipients as before


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


def test_saved_columns_are_checked() -> None:
    assert valid_columns(["subject", "nonsense", "subject", "date"]) == ("subject", "date") and valid_columns([]) == DEFAULT_COLUMNS and valid_columns(["x"]) == DEFAULT_COLUMNS


def test_the_list_is_a_table_by_default_with_the_standard_columns(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    model = world.window.list_model
    header = world.window.list_view.header()
    assert model.columns == DEFAULT_COLUMNS and not header.isHidden()
    assert [section for section in range(header.count()) if not header.isSectionHidden(section)] == list(range(len(DEFAULT_COLUMNS)))  # the others are hidden (R-13)
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
    wait(qtbot, lambda: world.window.messages.sort == "subject" and world.window.messages.oldest_first)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 1")  # A to Z
    world.window.list_view.header().sectionClicked.emit(subject)
    wait(qtbot, lambda: world.window.messages.oldest_first is False)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 3")
    world.window.list_view.header().sectionClicked.emit(size)
    wait(qtbot, lambda: world.window.messages.sort == "size")  # noqa: SLF001


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
    wait(qtbot, lambda: world.window.messages.sort == "flagged" and not world.window.messages.oldest_first)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 1")  # the flagged message on top
    assert header.sortIndicatorSection() == flag and world.window.sort_button.text() == "Sorted by Flagged ▾"
    header.sectionClicked.emit(flag)
    wait(qtbot, lambda: world.window.list_model.index(2, subject).data() == "Message 1")  # and at the bottom
    middle = QPoint(header.sectionViewportPosition(trash) + header.sectionSize(trash) // 2, header.height() // 2)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=middle)  # a real click: Qt moves the arrow by itself
    # the one column that cannot sort changes nothing, also not the arrow
    assert world.window.messages.sort == "flagged" and header.sortIndicatorSection() == flag  # noqa: SLF001


def test_columns_can_be_toggled_from_the_header_and_are_remembered(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.columns.toggle_column("preview")  # noqa: SLF001
    assert world.window.list_model.columns[-1] == "preview" and "preview" in str(world.window.settings.value("folder_columns"))
    world.window.columns.toggle_column("size")  # noqa: SLF001
    assert "size" not in world.window.list_model.columns
    for column in list(world.window.list_model.columns)[1:]:
        world.window.columns.toggle_column(column)  # noqa: SLF001
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
    dialog.picker.reset_button.click()  # Reset: the columns after a fresh install, of received mail here
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
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    wait(qtbot, lambda: world.window.reading.shown is not None)  # noqa: SLF001
    world.window.reading.set_place("off")
    assert world.window.reading_pane.isHidden() and world.window.reading.shown is None  # noqa: SLF001
    world.window.list_view.setCurrentIndex(world.window.list_model.index(1, 0))
    qtbot.wait(150)
    assert world.window.reading.shown is None  # nothing is opened, so nothing is marked read  # noqa: SLF001
    world.window.act_open.trigger()
    wait(qtbot, lambda: len(world.window.reading.message_windows) == 1)  # noqa: SLF001
    window: MessageWindow = world.window.reading.message_windows[0]  # noqa: SLF001
    assert window.windowTitle().startswith("Message") and window.isVisible()
    wait(qtbot, lambda: "seen" in row_of(world.window, 1).flags)
    window.close()
    world.window.reading.set_place("right")
    assert not world.window.reading_pane.isHidden() and world.window.content_splitter.orientation() == Qt.Orientation.Horizontal
    wait(qtbot, lambda: world.window.reading.shown is not None)  # noqa: SLF001 - the pane shows the selected message again


def test_a_message_window_can_start_a_reply(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    world.window.open_selected()
    wait(qtbot, lambda: len(world.window.reading.message_windows) == 1)  # noqa: SLF001
    window: MessageWindow = world.window.reading.message_windows[0]  # noqa: SLF001
    window.findChildren(QToolBar)[0].actions()[0].trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    composer = world.window.composing.composers[0]  # noqa: SLF001
    assert composer.subject.text().startswith("Re: Message")
    composer.body.document().setModified(False)
    composer._finished = True  # noqa: SLF001
    composer.close()
    window.close()


def test_the_sent_folder_starts_with_to_and_sent_and_sorts_by_sent(world: World, qtbot: QtBot) -> None:
    world.call("mail.send", account=world.account_id, to="Dora <dora@example.org>", subject="Out", body="x")
    world.window.start()
    settle(qtbot, world, 3)
    account = account_node(world.window)
    for position in range(world.window.tree_model.rowCount(account)):
        index = world.window.tree_model.index(position, 0, account)
        if index.data(256 + 2) == "Sent":
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1 and world.window.list_model.row_at(0) is not None)
    model = world.window.list_model
    assert model.columns == STARTING["sent"].columns and world.window.messages.sort == "sent" and not world.window.messages.oldest_first
    assert "Dora" in model.index(0, model.columns.index("to")).data() and model.index(0, model.columns.index("sent")).data() != ""
    assert model.headerData(model.columns.index("sent"), Qt.Orientation.Horizontal) == "Sent" and world.window.sort_button.text() == "Sorted by Sent ▾"


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
    assert header.sectionResizeMode(len(model.columns) - 1) == QHeaderView.ResizeMode.Fixed  # the last column shown
    assert header.sectionResizeMode(model.columns.index("subject")) == QHeaderView.ResizeMode.Interactive
    assert header.sectionSize(model.columns.index("status")) == 24
    world.window.resize(900, 600)
    qtbot.wait(50)
    assert sum(header.sectionSize(index) for index in range(header.count())) == view.viewport().width()
    subject, sender = model.columns.index("subject"), model.columns.index("from")
    header.resizeSection(sender, header.sectionSize(sender) + 40)  # as a drag does
    assert sum(header.sectionSize(index) for index in range(header.count())) == view.viewport().width()  # Subject and the others gave the room
    assert '\\"shares\\"' in str(world.window.settings.value("folder_columns")) and header.sectionSize(subject) > 50  # kept for the Inbox only


def test_rows_have_the_height_of_the_density(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    view = world.window.list_view
    assert view.visualRect(world.window.list_model.index(0, 4)).height() == 29
    world.window.settings.setValue("list_density", "compact")
    world.window.columns.apply_list_look()  # noqa: SLF001
    qtbot.wait(50)
    assert view.visualRect(world.window.list_model.index(0, 4)).height() == 22


def test_a_click_on_the_delete_icon_deletes_that_message_only(world: World, qtbot: QtBot) -> None:
    shown(world, qtbot)
    view, model = world.window.list_view, world.window.list_model
    view.setCurrentIndex(model.index(0, 4))
    rect = view.visualRect(model.index(1, model.columns.index("trash")))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    wait(qtbot, lambda: model.rowCount() == 2 and world.backend._pending == {})  # noqa: SLF001 (the row leaves at the click, the engine's delete after it, D-266)
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
    world.window.messages.load_contact_addresses()  # noqa: SLF001
    wait(qtbot, lambda: model.index(0, column).data(CONTACT_ROLE) == (added.item, book))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=nobody)
    page = world.window.contacts_page
    wait(qtbot, lambda: world.window._section == CONTACTS and page._selected_items() == [added.item])  # noqa: SLF001


def test_the_one_setup_of_earlier_versions_becomes_that_of_incoming_mail(world: World) -> None:
    settings, columns = world.window.settings, world.window.columns
    settings.setValue("columns", "subject,date,size")
    settings.setValue("sort", "size")
    settings.setValue("oldest_first", True)
    settings.remove("columns_contact_and_delete_added")  # saved before Contact and Delete existed: they are added once
    columns._type_setups.clear()  # noqa: SLF001
    columns._take_over_earlier_setup()  # noqa: SLF001 - as at the first start of 2.0.280
    incoming = columns.standard_setup("incoming")
    assert (incoming.columns, incoming.sort, incoming.oldest_first) == (("subject", "date", "size", "contact", "trash"), "size", True)
    assert columns.standard_setup("sent") == STARTING["sent"]  # the outgoing kinds start with their own
    assert "incoming" in read_setups(str(settings.value("type_columns"))) and not settings.contains("columns")
    columns._take_over_earlier_setup()  # noqa: SLF001 - once only
    assert columns.standard_setup("incoming") == incoming


def test_the_contact_and_delete_headers_are_grey_icons(world: World) -> None:
    from PySide6.QtGui import QGuiApplication, QIcon, QPalette

    world.window.settings.setValue("color_scheme", "light")
    world.window.apply_preferences()  # the program's own colours: the grey of placeholder text is a real grey (Qt's own is black)
    model = world.window.list_model
    model.set_columns(("subject", "contact", "trash"))
    grey = QGuiApplication.palette().color(QPalette.ColorRole.PlaceholderText)
    assert grey.saturation() == 0 and 60 < grey.lightness() < 200
    for column in (1, 2):
        icon = model.headerData(column, Qt.Orientation.Horizontal, HEADER_ICON_ROLE)
        assert model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DecorationRole) is None  # the header draws it, in the middle
        assert isinstance(icon, QIcon) and not icon.isNull() and model.headerData(column, Qt.Orientation.Horizontal) == ""
        image = icon.pixmap(64, 64).toImage()
        inked = [image.pixelColor(x, y) for x in range(64) for y in range(64) if image.pixelColor(x, y).alpha() >= 128]
        assert inked  # something is drawn
        # wholly grey, also the bin that is red elsewhere; the soft edges of the lines read back a shade off, so a small tolerance
        assert all(abs(pixel.red() - grey.red()) <= 6 and abs(pixel.green() - grey.green()) <= 6 and abs(pixel.blue() - grey.blue()) <= 6 for pixel in inked)
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
    order = list(read_setups(str(window.settings.value("folder_columns")))[window.columns._key or ""][0].columns)  # noqa: SLF001
    assert order.index(first) == 2  # remembered for this folder


def test_a_dragged_column_keeps_its_place_when_a_column_is_added_and_another_folder_opens(world: World, qtbot: QtBot) -> None:
    """The owner's bug (1.1.208): after dragging Replied to the left, adding a column or opening another folder jumbled the
    order, because the header kept the drag as well as the saved order and applied it a second time (D-179). Since B-16 the
    other folder has its own columns, and the first folder's come back as they were."""
    shown(world, qtbot)
    window, header = world.window, world.window.list_view.header()

    def on_screen() -> list[str]:
        return [window.list_model.columns[header.logicalIndex(visual)] for visual in range(header.count()) if not header.isSectionHidden(header.logicalIndex(visual))]

    window.columns.toggle_column("replied")  # noqa: SLF001
    header.moveSection(window.list_model.columns.index("replied"), 1)  # what a drag of the Replied header to second place does
    dragged = on_screen()
    assert dragged[1] == "replied"
    window.columns.toggle_column("tag")  # noqa: SLF001
    assert on_screen() == [*dragged, "tag"]
    account = account_node(window)
    sent = next(window.tree_model.index(position, 0, account) for position in range(window.tree_model.rowCount(account))
                if window.tree_model.index(position, 0, account).data(256 + 2) == "Sent")
    window.tree.setCurrentIndex(sent)
    wait(qtbot, lambda: window.list_model.rowCount() == 0 and world.backend._pending == {})  # noqa: SLF001 - Sent is empty
    assert on_screen() == list(STARTING["sent"].columns)  # Sent has its own columns
    inbox = next(window.tree_model.index(position, 0, account) for position in range(window.tree_model.rowCount(account))
                 if window.tree_model.index(position, 0, account).data(256 + 2) == "Inbox")
    window.tree.setCurrentIndex(inbox)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and world.backend._pending == {})  # noqa: SLF001
    assert on_screen() == [*dragged, "tag"]


def test_a_column_added_or_removed_and_the_cards_keep_the_selected_messages(world: World, qtbot: QtBot) -> None:
    """R-13: adding or removing a column (Settings, the header's menu) and switching to cards and back used to empty the list
    and fill it again, so the selected messages were no longer selected. Now a column is only shown or hidden."""
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    chosen = select_subject(world, qtbot, "Message 2")
    other = next(row for row in range(window.list_model.rowCount()) if window.list_model.row_at(row) is not None
                 and str(getattr(window.list_model.row_at(row), "message_id")) != chosen)
    window.list_view.selectionModel().select(window.list_model.index(other, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selected = sorted(whole_rows_selected(world))
    assert len(selected) == 2
    resets: list[int] = []
    window.list_model.modelReset.connect(lambda: resets.append(1))
    for step in (lambda: window.columns.toggle_column("tag"), lambda: window.columns.toggle_column("tag"), lambda: window.columns.toggle_column("size"),  # noqa: SLF001
                 lambda: window.columns.set_list_layout("cards"), lambda: window.columns.set_list_layout("columns")):
        step()
        assert sorted(whole_rows_selected(world)) == selected  # every selected message, its whole row
        assert selected_message(world) == chosen
    assert resets == []
    assert "size" not in window.list_model.columns and window.list_view.header().isSectionHidden(window.list_model.columnCount() - 1)
    shown_columns = [window.list_model.columns[section] for section in window.columns.visual_sections()]  # noqa: SLF001
    assert shown_columns == list(window.list_model.columns)


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
    wait(qtbot, lambda: world.window.reading.shown is not None and world.window.reading.shown[2] == message_id)  # noqa: SLF001
    return message_id


def sort_by_subject(world: World) -> None:
    world.window.list_view.header().sectionClicked.emit(world.window.list_model.columns.index("subject"))


def whole_rows_selected(world: World) -> list[str]:
    """The messages whose whole row is selected: what Delete, Move and the other actions act on."""
    model = world.window.list_model
    return [str(getattr(model.row_at(index.row()), "message_id")) for index in world.window.list_view.selectionModel().selectedRows()]


def show_favorites_unread(world: World, qtbot: QtBot) -> None:
    window = world.window
    top = window.tree_model.index(0, 0)
    unread = next(window.tree_model.index(row, 0, top) for row in range(window.tree_model.rowCount(top)) if window.tree_model.index(row, 0, top).data() == "Unread")
    window.tree.setCurrentIndex(unread)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.combined == "unread" and window.list_model.rowCount() == 3)


def sorted_by_subject(world: World, qtbot: QtBot) -> None:
    window = world.window
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject" and world.backend._pending == {})  # noqa: SLF001


@pytest.mark.parametrize("favorites", [False, True])
def test_a_new_sort_order_keeps_the_selected_message_selected(world: World, qtbot: QtBot, favorites: bool) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    if favorites:
        show_favorites_unread(world, qtbot)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    chosen = select_subject(world, qtbot, "Message 2")
    sort_by_subject(world)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject" and selected_message(world) == chosen)
    assert window.reading.shown is not None and window.reading.shown[2] == chosen  # noqa: SLF001 - the reading pane shows the same message


@pytest.mark.parametrize("favorites", [False, True])
def test_after_a_new_sort_the_whole_row_stays_selected_and_delete_deletes_it(world: World, qtbot: QtBot, favorites: bool) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    if favorites:
        show_favorites_unread(world, qtbot)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    chosen = select_subject(world, qtbot, "Message 2")
    resets: list[int] = []
    window.list_model.modelReset.connect(lambda: resets.append(1))
    sort_by_subject(world)
    sorted_by_subject(world, qtbot)
    assert selected_message(world) == chosen
    assert whole_rows_selected(world) == [chosen]  # the whole row, never one cell
    assert resets == []  # the list was rearranged, never thrown away
    QTest.keyClick(window.list_view, Qt.Key.Key_Delete)
    wait(qtbot, lambda: window.list_model.find(chosen) < 0 and world.backend._pending == {})  # noqa: SLF001
    trash = world.call("message.list", account=world.account_id, folder="Trash", limit=10).messages
    assert [row.message_id for row in trash] == [chosen]  # the Delete deleted the message that was selected


def test_a_new_sort_keeps_every_selected_message_selected(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    model, selection = window.list_model, window.list_view.selectionModel()
    column = model.columns.index("subject")
    rows = [next(number for number in range(model.rowCount()) if model.index(number, column).data() == subject) for subject in ("Message 1", "Message 3")]
    selection.select(model.index(rows[0], 0), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    selection.select(model.index(rows[1], 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    selection.setCurrentIndex(model.index(rows[1], 0), QItemSelectionModel.SelectionFlag.NoUpdate)
    chosen = {str(getattr(model.row_at(row), "message_id")) for row in rows}
    sort_by_subject(world)
    sorted_by_subject(world, qtbot)
    assert set(whole_rows_selected(world)) == chosen and len(whole_rows_selected(world)) == 2


def test_the_selected_message_is_found_on_a_page_that_was_not_loaded(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.ui import messagelist

    monkeypatch.setattr(messagelist, "PAGE_SIZE", 1)  # one message per page: after the sort only the first page is loaded
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
    assert whole_rows_selected(world) == [chosen]


def test_choosing_another_message_while_the_new_order_is_on_its_way_wins(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    select_subject(world, qtbot, "Message 1")
    sort_by_subject(world)  # the new order is asked for; its answers arrive later
    model = window.list_model
    column = model.columns.index("subject")
    row = next(number for number in range(model.rowCount()) if model.index(number, column).data() == "Message 3")
    window.list_view.setCurrentIndex(model.index(row, 0))  # chosen before the answers are there
    other = str(getattr(model.row_at(row), "message_id"))
    sorted_by_subject(world, qtbot)
    assert whole_rows_selected(world) == [other] and selected_message(world) == other


def test_fetching_the_list_again_keeps_the_whole_row_selected(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    chosen = select_subject(world, qtbot, "Message 2")
    resets: list[int] = []
    window.list_model.modelReset.connect(lambda: resets.append(1))
    window.messages.refetch()  # noqa: SLF001 - as after a change from outside (new mail, another program)
    wait(qtbot, lambda: not window.messages.refetching and world.backend._pending == {})  # noqa: SLF001
    assert whole_rows_selected(world) == [chosen] and selected_message(world) == chosen
    assert resets == []


def test_a_delete_spans_no_group_header_again_and_the_headers_keep_their_bar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.act_show_groups.setChecked(True)
    sort_by_subject(world)
    sorted_by_subject(world, qtbot)
    view, model = window.list_view, window.list_model
    wait(qtbot, lambda: len(model.header_rows()) == 3)  # one heading per subject
    spanned: list[int] = []
    real = view.setFirstColumnSpanned

    def counting(row: int, parent: QModelIndex, span: bool) -> None:
        spanned.append(row)
        real(row, parent, span)

    monkeypatch.setattr(view, "setFirstColumnSpanned", counting)
    gone = str(getattr(model.row_at(model.header_rows()[1] + 1), "message_id"))
    world.call("message.delete", account=world.account_id, folder="Inbox", message=gone)
    wait(qtbot, lambda: model.find(gone) < 0 and len(model.header_rows()) == 2)
    assert spanned == []  # its heading went with it; the others keep their bar without being touched
    assert all(view.isFirstColumnSpanned(row, QModelIndex()) for row in model.header_rows())


def test_a_message_read_in_favorites_unread_stays_there_after_a_sort_and_a_fetch(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.settings.setValue("mark_read_delay_ms", 0)
    window.apply_preferences()
    show_favorites_unread(world, qtbot)
    model = window.list_model
    chosen = select_subject(world, qtbot, "Message 2")
    wait(qtbot, lambda: "seen" in getattr(model.row_at(model.find(chosen)), "flags") and world.backend._pending == {})  # noqa: SLF001 - read where it is shown
    sort_by_subject(world)
    sorted_by_subject(world, qtbot)
    assert model.find(chosen) >= 0 and model.message_count() == 3  # read here: it stays until the view is left
    assert whole_rows_selected(world) == [chosen]
    window.messages.refetch()  # noqa: SLF001 - as after a change from outside
    wait(qtbot, lambda: not window.messages.refetching and world.backend._pending == {})  # noqa: SLF001
    assert model.find(chosen) >= 0 and model.message_count() == 3 and whole_rows_selected(world) == [chosen]
    show_favorites_unread_again = window.tree.currentIndex()
    window.tree.setCurrentIndex(account_node(window))  # the view is left ...
    wait(qtbot, lambda: model.spec is not None and model.spec.combined is None and world.backend._pending == {})  # noqa: SLF001
    window.tree.setCurrentIndex(show_favorites_unread_again)  # ... and opened again: now the read message is gone
    wait(qtbot, lambda: model.spec is not None and model.spec.combined == "unread" and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001
    assert model.find(chosen) < 0


def test_going_back_to_the_message_on_screen_calls_off_the_one_still_opening(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    first = select_subject(world, qtbot, "Message 1")
    model = window.list_model
    column = model.columns.index("subject")
    other = next(number for number in range(model.rowCount()) if model.index(number, column).data() == "Message 2")
    window.list_view.setCurrentIndex(model.index(other, 0))  # Message 2 starts opening ...
    window.list_view.setCurrentIndex(model.index(model.find(first), 0))  # ... and the person goes back before it is there
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    qtbot.wait(50)
    assert window.reading.shown is not None and window.reading.shown[2] == first  # noqa: SLF001 - the pane shows the message selected
