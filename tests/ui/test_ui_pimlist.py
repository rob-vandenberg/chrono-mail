from collections.abc import Callable

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.core import StorageError
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.pimlist import MarkDelegate, PimItemModel, PimListPage
from chrono_mail.ui.sections import CONTACTS, TASKS
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pimlist.py 4.4.615"

# --- Version history ----------------------------------------------------
# v4.4.615: Delete asks the plain question, and says that a server item goes there too; no store directory or days (OR-18); with a
#           server and a local list or book both shown, the selected items' own lists or books decide the sentence
# v1.1.245: Initial: Tasks and Contacts share one Delete, Move to, greying, error line and triangle; only the words differ (R-07)


def test_tasks_and_contacts_share_one_list_design(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("pim.enable")
    started(qtbot, world)
    tasks, contacts = world.window.pages[TASKS], world.window.pages[CONTACTS]
    assert isinstance(tasks, TasksPage) and isinstance(contacts, ContactsPage)
    for page, word, section in ((tasks, "task", "Tasks"), (contacts, "contact", "Contacts")):
        assert isinstance(page, PimListPage) and isinstance(page.model, PimItemModel) and isinstance(page.delegate, MarkDelegate)
        asked: list[str] = []

        def question(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
            asked.append(str(arguments[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", question)
        monkeypatch.setattr(page, "_selected_items", lambda: ["one", "two"])
        page.delete_selected()
        assert asked == [f"Delete these 2 {word}s?"]  # no store directory or days in it (OR-18)
        server = CollectionSummary("account", "Work", "on-server", "", "Work", "", False, 2, server=True)
        monkeypatch.setattr(page.panel, "visible_ids", lambda: ["on-server"])

        def on_server(collection: str) -> CollectionSummary:
            return server

        monkeypatch.setattr(page.panel, "info", on_server)
        page.delete_selected()
        monkeypatch.setattr(page, "_selected_items", lambda: ["one"])
        page.delete_selected()
        assert asked[1:] == [f"Delete these 2 {word}s?\n\nThey are also deleted on the server.", f"Delete this {word}?\n\nIt is also deleted on the server."]
        said: list[str] = []
        monkeypatch.setattr(page, "status", said.append)  # only the section on the screen writes to the status line
        page._failed(StorageError("the index is busy"))  # noqa: SLF001
        assert said == [f"{section}: the index is busy"]
        assert page.conflict_sources() == [page.delegate.conflict_clicked]


def test_delete_names_the_server_by_the_selected_items_own_lists_and_books(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    here = {summary.kind: summary.collection for summary in world.call("pim.enable")}
    work = {kind: world.call("collection.create", kind=kind, name="Work").collection for kind in ("tasks", "contacts")}
    task_items = {
        "work": [world.call("task.add", list=work["tasks"], summary=summary).item for summary in ("Send the quote", "Call Dora")],
        "here": [world.call("task.add", list=here["tasks"], summary="Water the plants").item],
    }
    contact_items = {
        "work": [world.call("contact.add", collection=work["contacts"], given=given, family="Lee").item for given in ("Ann", "Cy")],
        "here": [world.call("contact.add", collection=here["contacts"], given="Bob", family="Stone").item],
    }
    started(qtbot, world)
    tasks, contacts = world.window.pages[TASKS], world.window.pages[CONTACTS]
    assert isinstance(tasks, TasksPage) and isinstance(contacts, ContactsPage)
    for page, word, place, items in ((tasks, "task", "a list", task_items), (contacts, "contact", "an address book", contact_items)):
        page.panel.reload()
        wait(qtbot, lambda page=page: {here[page.kind], work[page.kind]} <= set(page.panel.visible_ids()))  # both shown
        page.reload_list()
        every = items["work"] + items["here"]
        wait(qtbot, lambda page=page, every=every: all(page._collection_of(item) for item in every))  # noqa: SLF001

        def kept_on_server(collection: str, real_info: Callable[[str], CollectionSummary | None] = page.panel.info) -> CollectionSummary | None:
            info = real_info(collection)
            if info is not None and collection in work.values():
                return CollectionSummary(info.account, info.account_name, collection, info.kind, info.name, "", False, info.items, server=True)
            return info

        monkeypatch.setattr(page.panel, "info", kept_on_server)
        asked: list[str] = []

        def question(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
            asked.append(str(arguments[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", question)
        for chosen in (items["work"][:1], items["work"], items["here"], every):
            monkeypatch.setattr(page, "_selected_items", lambda chosen=chosen: chosen)
            page.delete_selected()
        assert asked == [
            f"Delete this {word}?\n\nIt is also deleted on the server.",  # a server item while a local list or book is shown too
            f"Delete these 2 {word}s?\n\nThey are also deleted on the server.",
            f"Delete this {word}?",
            f"Delete these 3 {word}s?\n\nThose in {place} on a server are also deleted there.",
        ]
