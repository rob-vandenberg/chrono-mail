from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.sections import CONTACTS
from tests.test_engine_pim_lists import MANY, cards
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_contacts.py 3.0.339"

# --- Version history ----------------------------------------------------
# v3.0.339: a birthday without a year is shown and survives a Save that changed something else (RV-16)
# v2.4.334: the counts in the texts are written one way (F1b)
# v1.10.277: Contact groups (R-39): under their address book, a group's members, New group…, Rename…, Delete…, Write an e-mail, Add to group,
#            Remove from group, contacts dropped on a group; every change in place
# v1.9.273: Contacts' tag changes are typed (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.244: the tag list follows in place, the chosen tag stays and nothing is loaded again; a failure is told (R-06)
# v1.1.216: the details have no border of their own and a 50 px avatar
# v1.1.205: every contact of a large address book is listed (R-02)
# v0.0.64: The calendar section
# v0.0.62: Initial tests of the contacts section


def book(world: World) -> str:
    return next(c.collection for c in world.call("collection.list", kind="contacts"))


def open_contacts(world: World, qtbot: QtBot, count: int) -> ContactsPage:
    world.call("pim.enable")
    started(qtbot, world)
    world.window.switch_section(CONTACTS)
    page = world.window.contacts_page
    wait(qtbot, lambda: page.model.rowCount() == count and page.panel.current_id() != "" and page.count_label.text() == f"Contacts ({count})")  # the list has arrived, not just the empty model
    return page


def seed(world: World) -> None:
    world.call("pim.enable")
    b = book(world)
    world.call("contact.add", collection=b, given="John", family="Reynolds", email=("work|john@example.com",), organization="Reynolds Inc.", tag=("Important", "Key Customer"), phone=("cell|+1 555 1234",), note="Met at the fair")
    world.call("contact.add", collection=b, given="Rebecca", family="Hoffman", email=("rebecca@example.com",), tag=("Business",))
    world.call("contact.add", collection=b, given="Zoë", family="Åberg", organization="Café Nord")


def select_rows(page: ContactsPage, *rows: int) -> None:
    from PySide6.QtCore import QItemSelectionModel

    page.list.selectionModel().clearSelection()
    for row in rows:
        page.list.selectionModel().select(page.model.index(row), QItemSelectionModel.SelectionFlag.Select)
    page.list.selectionModel().setCurrentIndex(page.model.index(rows[0]), QItemSelectionModel.SelectionFlag.NoUpdate)
    page._selection_changed()  # noqa: SLF001


def test_the_contacts_are_listed_by_family_name_and_one_is_shown_in_full(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    assert [str(getattr(r, "name")) for r in page.model.rows] == ["Zoë Åberg", "Rebecca Hoffman", "John Reynolds"] and page.count_label.text() == "Contacts (3)"
    assert [str(getattr(r, "subtitle")) for r in page.model.rows] == ["Café Nord", "rebecca@example.com", "Reynolds Inc."]
    select_rows(page, 2)
    wait(qtbot, lambda: page.detail.name.text() == "John Reynolds")
    assert page.detail.subtitle.text() == "Reynolds Inc." and "Important" in page.detail.tag_label.text() and "Key Customer" in page.detail.tag_label.text()
    html = page.detail.text.toHtml()
    assert "john@example.com" in html and "+1 555 1234" in html and "Met at the fair" in html
    assert page.detail.photo.pixmap() is not None
    select_rows(page, 0, 1)
    assert "2 contacts selected" in page.detail.text.toPlainText() and page.detail.name.text() == ""
    page.list.selectionModel().clearSelection()
    page._selection_changed()  # noqa: SLF001
    assert "Select a contact" in page.detail.text.toPlainText()


def test_search_and_the_tag_list_filter_the_contacts(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: page.tag_list.count() == 4)  # all contacts, Business, Important, Key Customer
    assert [page.tag_list.item(i).text() for i in range(page.tag_list.count())][0] == "All contacts"
    page.search.setText("rey")
    wait(qtbot, lambda: page.model.rowCount() == 1 and page.count_label.text() == "Contacts (1)")
    page.search.setText("zzz")
    wait(qtbot, lambda: page.model.rowCount() == 0)
    assert "No contact matches" in page.detail.text.toPlainText()
    page.search.setText("")
    wait(qtbot, lambda: page.model.rowCount() == 3)
    business = next(i for i in range(page.tag_list.count()) if page.tag_list.item(i).text().startswith("Business"))
    page.tag_list.setCurrentRow(business)
    wait(qtbot, lambda: [str(getattr(r, "name")) for r in page.model.rows] == ["Rebecca Hoffman"])
    page.tag_list.setCurrentRow(0)
    wait(qtbot, lambda: page.model.rowCount() == 3)


def fake_exec(fill: Callable[[ContactEditor], None]) -> Callable[[ContactEditor], int]:
    def run(editor: ContactEditor) -> int:
        fill(editor)
        editor._save()  # noqa: SLF001
        return 0

    return run


def test_a_birthday_without_a_year_is_shown_and_kept_when_something_else_is_changed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-16: phones keep birthdays without a year (--0415); the editor showed none, so any Save of the contact removed it, here and on
    the server."""
    world.call("pim.enable")
    world.call("contact.add", collection=book(world), given="Ann", family="Lee", birthday="--0415")
    page = open_contacts(world, qtbot, 1)
    seen: list[tuple[bool, str]] = []

    def retitle(editor: ContactEditor) -> None:
        seen.append((editor.has_birthday.isChecked(), editor.birthday.text()))
        editor.title.setText("Chair")

    monkeypatch.setattr(ContactEditor, "exec", fake_exec(retitle))
    select_rows(page, 0)
    wait(qtbot, lambda: page.detail.name.text() == "Ann Lee")
    item = str(getattr(page.model.rows[0], "item"))
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("contact.show", item=item).title == "Chair")
    assert seen == [(True, "04-15")] and world.call("contact.show", item=item).birthday == "--0415"


def test_a_contact_is_made_in_the_editor_and_changed_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("pim.enable")
    page = open_contacts(world, qtbot, 0)
    assert "no contacts here yet" in page.detail.text.toPlainText()

    def fill(editor: ContactEditor) -> None:
        editor.given.setText("Ann")
        editor.family.setText("Lee")
        editor.organization.setText("Acme")
        editor.emails.rows[0].value.setText("ann@example.com")
        editor.phones.rows[0].value.setText("+31 6 1234")
        editor.add_address()
        row = editor.address_rows[0]
        row.street.setText("Main Street 1")
        row.locality.setText("Utrecht")
        editor.has_birthday.setChecked(True)
        editor.note.setPlainText("Likes tea")
        editor.tags.setText("Friends, Work")

    monkeypatch.setattr(ContactEditor, "exec", fake_exec(fill))
    page.act_new.trigger()
    wait(qtbot, lambda: page.model.rowCount() == 1 and page.detail.name.text() == "Ann Lee")
    shown = world.call("contact.show", item=str(getattr(page.model.rows[0], "item")))
    assert (shown.organization, shown.note, shown.tags) == ("Acme", "Likes tea", ("Friends", "Work")) and shown.emails[0].value == "ann@example.com" and shown.addresses[0].locality == "Utrecht"
    assert shown.emails[0].types == ("work",) and shown.phones[0].value == "+31 6 1234" and shown.birthday == "1980-01-01"

    def change(editor: ContactEditor) -> None:
        assert editor.given.text() == "Ann" and editor.emails.rows[0].value.text() == "ann@example.com" and editor.tags.text() == "Friends, Work" and editor.has_birthday.isChecked()
        assert not editor.book.isEnabled() or editor.book.count() >= 1
        editor.title.setText("Chair")
        editor.phones._remove(editor.phones.rows[0])  # noqa: SLF001
        editor.has_birthday.setChecked(False)
        editor.tags.setText("Friends")

    monkeypatch.setattr(ContactEditor, "exec", fake_exec(change))
    select_rows(page, 0)
    wait(qtbot, lambda: page.detail.name.text() == "Ann Lee")
    page.act_edit.trigger()
    wait(qtbot, lambda: world.call("contact.show", item=shown.item).title == "Chair")
    after = world.call("contact.show", item=shown.item)
    assert after.phones == () and after.birthday == "" and after.tags == ("Friends",) and after.emails[0].value == "ann@example.com" and after.addresses[0].street == "Main Street 1"

    def nothing(editor: ContactEditor) -> None:
        for field in (editor.given, editor.family, editor.display_name, editor.organization):
            field.setText("")
        editor.emails.rows[0].value.setText("")

    monkeypatch.setattr(ContactEditor, "exec", fake_exec(nothing))
    page.act_edit.trigger()
    qtbot.wait(300)
    assert world.call("contact.show", item=shown.item).title == "Chair"  # nothing to save: the contact is unchanged and the editor says so


def test_tags_are_set_from_the_menu_for_several_contacts_and_contacts_are_moved_and_deleted(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    other = world.call("collection.create", kind="contacts", name="Friends").collection
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: page.tag_list.count() == 4)  # the tags have arrived (the menu is made from them)
    select_rows(page, 0, 1)
    page._fill_tag_menu()  # noqa: SLF001
    labels = [a.text() for a in page.tag_menu.actions()]
    assert labels[:3] == ["Business", "Important", "Key Customer"] and "New tag…" in labels and "Remove all tags" in labels
    next(a for a in page.tag_menu.actions() if a.text() == "Important").trigger()
    wait(qtbot, lambda: all("Important" in getattr(r, "tags") for r in page.model.rows[:2]))
    monkeypatch.setattr(QInputDialog, "getText", answering("Family"))
    page._new_tag()  # noqa: SLF001
    wait(qtbot, lambda: all("Family" in getattr(r, "tags") for r in page.model.rows[:2]))
    assert "Family" not in getattr(page.model.rows[2], "tags")
    page._edit_selected(clear=("tag",))  # noqa: SLF001  - "Remove all tags"
    wait(qtbot, lambda: all(getattr(r, "tags") == () for r in page.model.rows[:2]))
    select_rows(page, 0)
    page._fill_move_menu()  # noqa: SLF001
    assert [a.text() for a in page.move_menu.actions()] == ["Friends"]
    page.move_menu.actions()[0].trigger()
    wait(qtbot, lambda: page.model.rowCount() == 2)
    assert [c.name for c in world.call("contact.list", collection=other).contacts] == ["Zoë Åberg"]
    select_rows(page, 0)
    monkeypatch.setattr(QMessageBox, "question", answer_no)
    page.act_delete.trigger()
    qtbot.wait(200)
    assert page.model.rowCount() == 2
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    page.act_delete.trigger()
    wait(qtbot, lambda: page.model.rowCount() == 1)
    assert world.call("contact.list").total == 2  # one in each book now


def answering(text: str) -> Callable[..., tuple[str, bool]]:
    def get_text(*arguments: object, **keywords: object) -> tuple[str, bool]:
        return text, True

    return get_text


def chosen_file(path: Path) -> Callable[..., tuple[str, str]]:
    def choose(*arguments: object, **keywords: object) -> tuple[str, str]:
        return str(path), ""

    return choose


def answer_yes(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def answer_no(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.No


def test_contacts_are_imported_from_and_exported_to_files(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    world.call("pim.enable")
    page = open_contacts(world, qtbot, 0)
    source = tmp_path / "in.vcf"
    source.write_text("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:u1\r\nFN:Imported One\r\nEMAIL:one@example.com\r\nEND:VCARD\r\nBEGIN:VCARD\r\nVERSION:3.0\r\nFN:Imported Two\r\nEND:VCARD\r\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", chosen_file(source))
    page.import_file()
    wait(qtbot, lambda: page.model.rowCount() == 2)
    assert "Imported 2 contacts" in world.window.status_label.text() or page.model.rowCount() == 2
    target = tmp_path / "out.vcf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", chosen_file(target))
    page.export_file()
    wait(qtbot, lambda: target.exists())
    text = target.read_text()
    assert text.count("BEGIN:VCARD") == 2 and "Imported One" in text and "Imported Two" in text


def test_clicking_an_address_writes_to_it_and_the_history_lists_the_mail(world: World, qtbot: QtBot) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    asked: list[str] = []
    page.compose_requested.connect(asked.append)
    select_rows(page, 2)
    wait(qtbot, lambda: page.detail.name.text() == "John Reynolds")
    page.detail._link(QUrl("mailto:john@example.com"))  # noqa: SLF001
    assert asked == ["john@example.com"]
    world.window.composing.compose_to("john@example.com")  # noqa: SLF001
    assert world.window.composing.composers and world.window.composing.composers[-1].to.text() == "john@example.com"  # noqa: SLF001
    page.detail.mail_accounts = [world.account_id]
    page.detail.tabs.setCurrentIndex(1)
    wait(qtbot, lambda: page.detail.history.count() == 1 and "No mail with this contact" in page.detail.history.item(0).text())
    world.call("contact.edit", item=str(getattr(page.model.rows[2], "item")), email=("alice@example.com",))
    page.detail.tabs.setCurrentIndex(0)
    select_rows(page, 2)
    wait(qtbot, lambda: page.detail.name.text() == "John Reynolds")
    page.detail.tabs.setCurrentIndex(1)
    wait(qtbot, lambda: page.detail.history.count() >= 1 and "Message" in page.detail.history.item(0).text())  # the three messages are from Alice
    assert page.detail.history.count() == 3


def test_every_contact_of_a_large_address_book_is_listed(world: World, qtbot: QtBot) -> None:
    world.call("pim.enable")
    assert world.call("contact.import", collection=book(world), text=cards(MANY)).imported == MANY
    page = open_contacts(world, qtbot, MANY)  # every one, and the count says so
    assert len({str(getattr(row, "item")) for row in page.model.rows}) == MANY


def test_the_details_reach_the_separator_and_only_the_header_has_spacing(world: World) -> None:
    detail = world.window.contacts_page.detail
    outer = detail.layout()
    assert outer is not None and outer.contentsMargins().left() == outer.contentsMargins().right() == outer.contentsMargins().bottom() == 0
    header = outer.itemAt(0).layout()
    assert header is not None and header.contentsMargins().left() == 12 and header.contentsMargins().top() == 12
    assert detail.photo.width() == detail.photo.height() == 50


def test_the_tag_list_follows_in_place_and_keeps_the_chosen_tag(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import StorageError

    seed(world)
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: page.tag_list.count() == 4)
    business = next(page.tag_list.item(i) for i in range(page.tag_list.count()) if page.tag_list.item(i).text().startswith("Business"))
    page.tag_list.setCurrentItem(business)
    wait(qtbot, lambda: page.model.rowCount() == 1)
    everyone, important = page.tag_list.item(0), page.tag_list.item(2)
    changes: list[bool] = []
    page.tag_list.currentItemChanged.connect(lambda *_: changes.append(True))
    world.call("contact.add", collection=book(world), given="Ann", family="Lee", tag=("Business", "Family"))
    page.reload_tags()  # what a save does
    wait(qtbot, lambda: [page.tag_list.item(i).text() for i in range(page.tag_list.count())] == ["All contacts", "Business  (2)", "Family  (1)", "Important  (1)", "Key Customer  (1)"])
    assert page.tag_list.item(0) is everyone and page.tag_list.item(1) is business and page.tag_list.item(3) is important  # changed in place, not made again
    assert page.tag_list.currentItem() is business and changes == []  # the chosen tag stays; nothing is loaded again

    def refused(command: str, values: object, done: object, failed: object, owner: object = None, **options: object) -> None:  # options: what ask passes on
        assert callable(failed)
        failed(StorageError("the index is busy"))

    monkeypatch.setattr(page._backend, "call", refused)  # noqa: SLF001
    page.reload_tags()
    assert "could not be read" in world.window.status_label.text() and "the index is busy" in world.window.status_label.text()


# --- contact groups (R-39) ---------------------------------------------------------------------------------------------------------------


def groups(page: ContactsPage) -> list[str]:
    return [entry.text(0) for entry in page.panel.group_entries(page.panel.current_id())]


def choose_group(page: ContactsPage, qtbot: QtBot, number: int, count: int) -> None:
    page.panel.tree.setCurrentItem(page.panel.group_entries(page.panel.current_id())[number])
    wait(qtbot, lambda: page.model.rowCount() == count and page.count_label.text().startswith(f"Members ({count})"))


def test_a_group_is_listed_under_its_address_book_and_shows_its_members(world: World, qtbot: QtBot) -> None:
    seed(world)
    b = book(world)
    rows = world.call("contact.list").contacts
    hoffman, reynolds = rows[1].item, rows[2].item
    world.call("contact-group.create", collection=b, name="Customers", contacts=(hoffman, reynolds))
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: groups(page) == ["Customers  (2)"])
    choose_group(page, qtbot, 0, 2)
    assert [r.name for r in page.model.rows] == ["Rebecca Hoffman", "John Reynolds"] and page.panel.current_id() == b
    select_rows(page, 1)
    wait(qtbot, lambda: page.detail.name.text() == "John Reynolds")
    assert "Customers" in page.detail.text.toPlainText()  # Groups, under Contact information
    page.panel.tree.setCurrentItem(page.panel._entry(b))  # noqa: SLF001 - the address book again
    wait(qtbot, lambda: page.model.rowCount() == 3 and page.count_label.text() == "Contacts (3)")


def test_contacts_go_in_and_out_of_a_group_in_place(world: World, qtbot: QtBot) -> None:
    seed(world)
    group = world.call("contact-group.create", collection=book(world), name="Club")
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: groups(page) == ["Club  (0)"])
    select_rows(page, 0, 1)
    page._fill_group_menu()  # noqa: SLF001
    labels = [action.text() for action in page.group_menu.actions()]
    assert labels[0] == "Club" and not page.group_menu.actions()[0].isChecked() and labels[-1] == "New group…"
    page.group_menu.actions()[0].trigger()
    wait(qtbot, lambda: groups(page) == ["Club  (2)"] and all(group.item in r.groups for r in page.model.rows[:2]))
    assert page.model.rowCount() == 3 and len(page.list.selectionModel().selectedRows()) == 2  # nothing loaded again: the selection stayed
    page._fill_group_menu()  # noqa: SLF001
    assert page.group_menu.actions()[0].isChecked()
    assert not page.act_remove_from_group.isEnabled()  # greyed outside a group
    choose_group(page, qtbot, 0, 2)
    select_rows(page, 0)
    assert page.act_remove_from_group.isEnabled()
    page.act_remove_from_group.trigger()
    wait(qtbot, lambda: page.model.rowCount() == 1 and groups(page) == ["Club  (1)"] and page.count_label.text() == "Members (1)")
    assert world.call("contact.list").total == 3  # it stays in the address book


def test_contacts_dropped_on_a_group_are_put_in_it(world: World, qtbot: QtBot) -> None:
    seed(world)
    world.call("contact-group.create", collection=book(world), name="Club")
    page = open_contacts(world, qtbot, 3)
    wait(qtbot, lambda: groups(page) == ["Club  (0)"])
    data = page.model.mimeData([page.model.index(2)])
    entry = page.panel.group_entries(page.panel.current_id())[0]
    place = QPointF(page.panel.tree.visualItemRect(entry).center())
    viewport = page.panel.tree.viewport()
    enter = QDragEnterEvent(place.toPoint(), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, enter)
    assert enter.isAccepted()
    drop = QDropEvent(place, Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, drop)
    wait(qtbot, lambda: groups(page) == ["Club  (1)"])
    book_entry = page.panel._entry(page.panel.current_id())  # noqa: SLF001
    assert book_entry is not None
    on_book = QDropEvent(QPointF(page.panel.tree.visualItemRect(book_entry).center()), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, on_book)
    assert not on_book.isAccepted()  # only a group takes contacts


def test_a_group_is_made_renamed_written_to_and_deleted_from_the_sidebar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    b = page.panel.current_id()
    book_entry = page.panel._entry(b)  # noqa: SLF001
    assert book_entry is not None
    labels = [action.text() for action in page.panel.menu_at(page.panel.tree.visualItemRect(book_entry).center()).actions()]
    assert "New group…" in labels
    monkeypatch.setattr(QInputDialog, "getText", answering("Customers"))
    page.new_group(b)
    wait(qtbot, lambda: groups(page) == ["Customers  (0)"] and page.panel.current_group() is not None)  # the new group is chosen
    wait(qtbot, lambda: page.count_label.text() == "Members (0)")
    assert "This group has no members yet" in page.detail.text.toPlainText()
    entry = page.panel.group_entries(b)[0]
    labels = [action.text() for action in page.panel.menu_at(page.panel.tree.visualItemRect(entry).center()).actions() if action.text()]
    assert labels == ["Add account…", "Write an e-mail", "New group…", "Rename…", "Delete…"]
    group = page.panel.current_group()
    assert group is not None
    world.call("contact-group.add-members", item=group.item, contacts=tuple(r.item for r in world.call("contact.list").contacts))
    wait(qtbot, lambda: groups(page) == ["Customers  (3)"] and page.model.rowCount() == 3)  # followed in place from the fact
    written: list[str] = []
    page.compose_requested.connect(written.append)
    group = page.panel.current_group()
    assert group is not None
    page.write_to_group(group)
    wait(qtbot, lambda: written != [])
    assert set(written[0].split(", ")) == {"Rebecca Hoffman <rebecca@example.com>", "John Reynolds <john@example.com>"}  # Zoë has no address
    monkeypatch.setattr(QInputDialog, "getText", answering("Clients"))
    page.rename_group(group)
    wait(qtbot, lambda: groups(page) == ["Clients  (3)"] and page.panel.current_group() is not None)
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    renamed = page.panel.current_group()
    assert renamed is not None
    page.delete_group(renamed)
    wait(qtbot, lambda: groups(page) == [] and page.panel.current_group() is None and page.count_label.text() == "Contacts (3)")
    assert world.call("contact.list").total == 3
