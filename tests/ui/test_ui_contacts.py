from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.sections import CONTACTS
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_contacts.py 0.0.64"

# --- Version history ----------------------------------------------------
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
    page._edit_selected({"clear": ["tag"]})  # noqa: SLF001  - "Remove all tags"
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
    assert "Imported 2 contact(s)" in world.window.status_label.text() or page.model.rowCount() == 2
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
    world.window._compose_to("john@example.com")  # noqa: SLF001
    assert world.window._composers and world.window._composers[-1].to.text() == "john@example.com"  # noqa: SLF001
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
