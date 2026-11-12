import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QLabel, QMainWindow, QTabWidget, QToolButton, QVBoxLayout
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_pim import TagInfo
from chrono_mail.ui.sections import MAIL, SECTIONS
from chrono_mail.ui.sidebarpage import SidebarPage
from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.messagelist import CONTACT_ROLE
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.tagsdialog import SHOW_IN, TagsPanel
from chrono_mail.ui.windowstate import restore_geometry
from tests.ui.test_ui_contacts import book, open_contacts, seed
from tests.ui.test_ui_columns import shown
from tests.ui.test_ui_settings import open_settings
from tests.ui.test_ui_tag_overview import open_overview
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_settings_tabs.py 4.4.589"

# --- Version history ----------------------------------------------------
# v4.4.589: Every tab of Settings > Sidebar is headed "Show in the sidebar:", Mail's Tags tick first; the colour in Settings > Tags is a circle as tall as the tag's name (the owner)
# v4.4.587: A sender's contact in an unticked address book opens in its own window; both address books ticked together (D-334)
# v4.4.584: The Tags block of D-334 in Contacts
# v4.4.581: Settings > Tags is one list without tabs, its Show in columns in the sections' order; a contact's window offers the tags shown in
#           Contacts in Add tag… (D-332)
# v4.4.580: Settings > Tags has the same tab page as Settings > Sidebar, in the same place, the list inside its frame (the owner)
# v4.4.578: Initial: Settings > Sidebar and Settings > Tags have the same tabs, Mail, Calendar, Contacts and Tasks; the Contacts tab of
#           Settings > Sidebar (D-331); the Contact icon of a sender in a left-out address book opens the contact in its own window (the owner); a Tag overview keeps its size when the program quits with it open (D-328)

SECTION_NAMES: list[str] = [name for _, name, _, _ in SECTIONS]


def sidebar_tabs(dialog: SettingsDialog) -> QTabWidget:
    tabs = dialog.sidebar_page.findChild(QTabWidget, "tabs")
    assert tabs is not None
    return tabs


def test_settings_sidebar_has_the_sections_as_tabs_and_settings_tags_shows_them_in_that_order(world: World, qtbot: QtBot) -> None:
    dialog = open_settings(world, qtbot)
    tags = dialog.findChild(TagsPanel)
    assert tags is not None
    sidebar = sidebar_tabs(dialog)
    assert [sidebar.tabText(number) for number in range(sidebar.count())] == SECTION_NAMES == ["Mail", "Calendar", "Contacts", "Tasks"]
    header = tags.list.headerItem()
    assert [header.text(column) for _, column in SHOW_IN] == SECTION_NAMES  # one list, its Show in columns in the sections' order (D-332)
    assert tags.findChild(QTabWidget) is None  # no tabs: one list, as the tags are (the owner)


def test_every_tab_of_settings_sidebar_is_headed_show_in_the_sidebar_and_mails_tags_tick_is_first(qtbot: QtBot) -> None:
    page = SidebarPage((("inboxes", "All Inboxes"),), set(), [], True, True, True, True, "1" * 32, [("a" * 32, "one")], set(), "")  # mail carries tags
    qtbot.addWidget(page)
    for name in ("displayHeading", "calendarHeading", "contactsHeading", "tasksHeading"):
        heading = page.findChild(QLabel, name)
        assert heading is not None and heading.text() == "Show in the sidebar:"
    ticks = page.findChild(QVBoxLayout, "ticksLayout")
    assert ticks is not None and page.tags_box is not None
    assert ticks.indexOf(page.tags_box) == 0 and ticks.indexOf(page.favorites_box) == 1  # Tags first, as on the other tabs (the owner)


def test_the_colour_of_a_tag_in_settings_tags_is_a_circle_as_tall_as_its_name(world: World, qtbot: QtBot) -> None:
    tags = TagsPanel(world.backend, (TagInfo("Garden", "#2e7d32", 0, 0, 0),))
    qtbot.addWidget(tags)
    row = tags.rows[0]
    button = next(button for button in tags.findChildren(QToolButton) if button.accessibleName() == f"Colour of {row.name}")
    diameter = tags.list.fontMetrics().height() + 2  # the height of the tag's name, drawn as a label
    assert button.iconSize().width() == button.iconSize().height() == diameter
    picture = button.icon().pixmap(button.iconSize(), tags.devicePixelRatioF()).toImage()
    middle, corner = picture.pixelColor(picture.width() // 2, picture.height() // 2), picture.pixelColor(0, 0)
    assert middle.name() == QColor(row.colour).name() and middle.alpha() == 255
    assert corner.alpha() == 0  # round: nothing in the corners


def ticks_and_ok(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, untick: list[str], tags: bool = True) -> list[bool]:
    """Settings > Sidebar, Contacts tab: untick these address books, set the Tags tick, OK. Gives whether each tick left could be unticked."""
    enabled: list[bool] = []

    def choose_and_ok(dialog: SettingsDialog) -> QDialog.DialogCode:
        wait(qtbot, lambda: "contacts" in dialog.sidebar_page.list_boxes)
        boxes = dialog.sidebar_page.list_boxes["contacts"]
        for collection in untick:
            boxes[collection].setChecked(False)
        enabled.extend(box.isEnabled() for box in boxes.values() if box.isChecked())
        dialog.sidebar_page.tags_boxes["contacts"].setChecked(tags)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", choose_and_ok)
    world.window.open_settings("Sidebar")
    return enabled


def test_an_address_book_left_out_in_settings_leaves_the_contacts_sidebar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    own = book(world)
    work = str(world.call("collection.create", kind="contacts", name="Work").collection)
    world.call("contact.add", collection=work, given="Lena", family="Brouwer")
    page = open_contacts(world, qtbot, 4)  # both address books ticked: their contacts together (D-334)
    enabled = ticks_and_ok(world, qtbot, monkeypatch, [own])
    assert enabled == [False]  # Work, the last one shown, cannot be left out
    wait(qtbot, lambda: page.panel._entry(own) is None and page.panel.visible_ids() == [work])  # noqa: SLF001 - only the one left
    wait(qtbot, lambda: page.model.rowCount() == 1)
    assert own in [info.collection for info in page.panel.every]  # still kept and synchronised


def test_the_tags_tick_of_contacts_hides_its_tags_list_and_every_contact_shows_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: page.tag_filter.list.count() > 0)
    page.tag_filter.choose("Business")  # one tag chosen: fewer contacts
    wait(qtbot, lambda: page.model.rowCount() == 1)
    ticks_and_ok(world, qtbot, monkeypatch, [], tags=False)
    wait(qtbot, lambda: page.tag_side.isHidden() and page.model.rowCount() == 3)
    handle = page.side_splitter.handle(1)
    assert handle is not None and handle.isHidden()
    assert str(world.window.settings.value("show_tags_contacts")) == "false"
    ticks_and_ok(world, qtbot, monkeypatch, [], tags=True)
    wait(qtbot, lambda: not page.tag_side.isHidden())


def test_a_tag_overview_keeps_its_size_when_the_program_quits_with_it_open(world: World, qtbot: QtBot) -> None:
    overview = open_overview(world, qtbot)
    overview.resize(720, 510)
    wait(qtbot, lambda: overview.size() == QSize(720, 510))
    world.window.close()  # Quit: the overview stays open
    assert overview.isVisible()
    probe = QMainWindow()
    qtbot.addWidget(probe)
    assert restore_geometry(probe, world.window.settings, "tag_overview_geometry")
    assert probe.size() == QSize(720, 510)


def test_the_contact_icon_of_a_sender_in_a_left_out_address_book_opens_the_contact_in_its_own_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown(world, qtbot)
    model, view = world.window.list_model, world.window.list_view
    column = model.columns.index("contact")
    world.call("pim.enable")
    own = book(world)
    world.call("collection.create", kind="contacts", name="Work")  # one shown, so this one can be left out
    sender = str(getattr(model.row_at(0), "sender"))
    added = world.call("contact.add", collection=own, name="The sender", email=(sender.split("<")[-1].rstrip(">").strip(),), tag=("Harbourline",))
    world.window.messages.load_contact_addresses()
    wait(qtbot, lambda: model.index(0, column).data(CONTACT_ROLE) == (added.item, own))
    world.window.settings.setValue("left_out_contacts", [own])  # as Settings > Sidebar keeps it, in a run that never opened Contacts: its lists never came
    assert world.window.contacts_page.panel.every == [] and world.window.contacts_page.tag_filter.list.count() == 0
    opened: list[str] = []
    known: list[list[str]] = []

    def answer(editor: ContactEditor) -> int:
        opened.append(str(editor.book.currentData()))  # the contact's own address book, also when it is left out
        known.append(list(editor.tags.known))  # offered by Add tag…, also with Contacts not opened yet
        return 0  # Cancel

    monkeypatch.setattr(ContactEditor, "exec", answer)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(model.index(0, column)).center())
    wait(qtbot, lambda: opened == [own])
    assert known == [["Harbourline"]]
    assert world.window._section == MAIL  # noqa: SLF001 - Mail stays; the hidden address book is not shown
    assert world.window.contacts_page._show_item == ""  # noqa: SLF001 - nothing waits to be selected later


def test_the_contact_icon_of_a_sender_in_an_unticked_address_book_opens_the_contact_in_its_own_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown(world, qtbot)
    model, view = world.window.list_model, world.window.list_view
    column = model.columns.index("contact")
    world.call("pim.enable")
    own = book(world)
    sender = str(getattr(model.row_at(0), "sender"))
    added = world.call("contact.add", collection=own, name="The sender", email=(sender.split("<")[-1].rstrip(">").strip(),))
    world.window.messages.load_contact_addresses()
    wait(qtbot, lambda: model.index(0, column).data(CONTACT_ROLE) == (added.item, own))
    world.window.settings.setValue("hidden_contacts", [own])  # its tick is off in Contacts (D-334)
    opened: list[str] = []

    def answer(editor: ContactEditor) -> int:
        opened.append(str(editor.book.currentData()))
        return 0  # Cancel

    monkeypatch.setattr(ContactEditor, "exec", answer)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(model.index(0, column)).center())
    wait(qtbot, lambda: opened == [own])
    assert world.window._section == MAIL  # noqa: SLF001 - Mail stays
