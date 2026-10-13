from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QToolBar
from pytestqt.qtbot import QtBot

from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, cell_text, column_widths, date_text, valid_columns
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.models import CONTACT_ROLE, HEADER_ICON_ROLE
from chrono_mail.ui.sections import CONTACTS
from tests.ui.world import account_node, World, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columns.py 0.1.89"

# --- Version history ----------------------------------------------------
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
    assert model.index(0, model.columns.index("status")).data() == "●" and model.headerData(0, Qt.Orientation.Horizontal) == "●"
    assert model.index(0, subject_column).data(Qt.ItemDataRole.FontRole).bold()  # unread is bold
    assert model.index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) is not None and model.index(99, 0).data() is None


def test_clicking_a_column_header_sorts_and_clicking_again_reverses(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    columns = world.window.list_model.columns
    subject, size, tick = columns.index("subject"), columns.index("size"), columns.index("flag")
    world.window.list_view.header().sectionClicked.emit(subject)
    wait(qtbot, lambda: world.window._sort == "subject" and world.window._oldest_first)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 1")  # A to Z
    world.window.list_view.header().sectionClicked.emit(subject)
    wait(qtbot, lambda: world.window._oldest_first is False)  # noqa: SLF001
    wait(qtbot, lambda: world.window.list_model.index(0, subject).data() == "Message 3")
    world.window.list_view.header().sectionClicked.emit(size)
    wait(qtbot, lambda: world.window._sort == "size")  # noqa: SLF001
    world.window.list_view.header().sectionClicked.emit(tick)  # a column that cannot sort changes nothing
    assert world.window._sort == "size"  # noqa: SLF001


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
    dialog = ColumnsDialog(ListChoice("columns", ("subject", "date"), "bottom", "iso"))
    qtbot.addWidget(dialog)
    shown = [dialog.list.item(row).text() for row in range(dialog.list.count()) if dialog.list.item(row).checkState() == Qt.CheckState.Checked]
    assert shown == ["Subject", "Received"] and dialog.list.item(0).text() == "Subject" and dialog.pane_box.currentData() == "bottom" and dialog.date_box.currentData() == "iso"
    dialog.list.setCurrentRow(1)
    dialog._move(-1)  # noqa: SLF001
    dialog.list.item(2).setCheckState(Qt.CheckState.Checked)
    third = str(dialog.list.item(2).data(Qt.ItemDataRole.UserRole))
    assert dialog.choice() == ListChoice("columns", ("date", "subject", third), "bottom", "iso")
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
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ColumnsDialog, "exec", fake_exec)
    world.window.act_columns.trigger()
    settings: QSettings = world.window.settings
    assert world.window.list_model.columns == ("card",) and world.window.list_view.header().isHidden() and world.window.list_model.date_style == "iso"
    assert world.window.content_splitter.orientation() == Qt.Orientation.Vertical and str(settings.value("list_layout")) == "cards" and str(settings.value("reading_pane")) == "bottom"
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
