import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.core import StorageError
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.pimlist import MarkDelegate, PimItemModel, PimListPage
from chrono_mail.ui.sections import CONTACTS, TASKS
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.test_ui_window import started
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pimlist.py 1.1.245"

# --- Version history ----------------------------------------------------
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
        assert asked == [f"Delete these 2 {word}s?\n\nThe files are kept in the store's .deleted directory for 30 days."]
        said: list[str] = []
        monkeypatch.setattr(page, "status", said.append)  # only the section on the screen writes to the status line
        page._failed(StorageError("the index is busy"))  # noqa: SLF001
        assert said == [f"{section}: the index is busy"]
        assert page.conflict_sources() == [page.delegate.conflict_clicked]
