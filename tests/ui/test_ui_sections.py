from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.ui.collectionpanel import ROLE_COLLECTION, collection_items
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, SECTIONS, TASKS
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sections.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.0.86: A message is selected before the mail actions are compared
# v0.0.85: The mail title ends with the program name
# v0.0.76: Contacts before Tasks (Ctrl+3 and Ctrl+4)
# v0.0.64: The calendar section
# v0.0.61: Initial tests of the section buttons and the sidebars of calendar, tasks and contacts


def test_four_square_buttons_switch_the_sidebar_the_main_area_and_the_toolbar(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    assert [name for name, *_ in SECTIONS] == [MAIL, CALENDAR, CONTACTS, TASKS] and list(window.section_bar.buttons) == [MAIL, CALENDAR, CONTACTS, TASKS]
    assert window.section == MAIL and window.section_bar.buttons[MAIL].isChecked() and window.side_stack.currentWidget() is window.tree and window.toolbar.isVisible()
    for section, key in ((CALENDAR, "Ctrl+2"), (CONTACTS, "Ctrl+3"), (TASKS, "Ctrl+4"), (MAIL, "Ctrl+1")):
        assert window.section_actions[section].shortcut().toString() == key
    window.section_bar.buttons[CALENDAR].click()
    page = window.pages[CALENDAR]
    assert window.section == CALENDAR and window.side_stack.currentWidget() is page.side and window.content_stack.currentWidget() is page.content
    assert page.toolbar.parent() is window and page.toolbar.isVisible() and not window.toolbar.isVisible() and window.section_bar.buttons[CALENDAR].isChecked() and not window.section_bar.buttons[MAIL].isChecked()
    assert "Calendar" in window.windowTitle()
    window.section_bar.buttons[CALENDAR].click()  # pressing the pressed one leaves it pressed
    assert window.section_bar.buttons[CALENDAR].isChecked()
    window.section_actions[CONTACTS].trigger()
    assert window.section == CONTACTS and window.pages[CONTACTS].toolbar.isVisible() and not page.toolbar.isVisible()
    window.section_actions[MAIL].trigger()
    assert window.section == MAIL and window.toolbar.isVisible() and window.windowTitle().endswith("Chrono Mail")


def test_the_sidebar_lists_the_calendars_under_their_account_and_remembers_what_is_hidden(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.switch_section(CALENDAR)
    panel = window.pages[CALENDAR].panel
    wait(qtbot, lambda: len(collection_items(panel.tree)) == 1)
    item = collection_items(panel.tree)[0]
    assert item.text(0) == "Calendar" and item.checkState(0) == Qt.CheckState.Checked and not item.icon(0).isNull()
    top = panel.tree.topLevelItem(0)
    assert top is not None and top.text(0) == "Local Folders"
    assert panel.visible_ids() == [str(item.data(0, ROLE_COLLECTION))]
    item.setCheckState(0, Qt.CheckState.Unchecked)
    assert panel.visible_ids() == [] and window.settings.value("hidden_events") == [str(item.data(0, ROLE_COLLECTION))]
    panel.reload()
    wait(qtbot, lambda: len(collection_items(panel.tree)) == 1 and collection_items(panel.tree)[0].checkState(0) == Qt.CheckState.Unchecked)
    window.switch_section(CONTACTS)
    contacts = window.pages[CONTACTS].panel
    wait(qtbot, lambda: len(collection_items(contacts.tree)) == 1)
    assert contacts.current_id() != "" and contacts.visible_ids() == [contacts.current_id()]  # an address book is chosen, not ticked
    window.switch_section(TASKS)
    wait(qtbot, lambda: len(collection_items(window.pages[TASKS].panel.tree)) == 1)


def test_mail_actions_are_off_in_the_other_sections_and_come_back_as_they_were(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    before = {name: getattr(window, f"act_{name}").isEnabled() for name in window.MAIL_ONLY}
    assert before["reply"] and not before["not_junk"]  # not junk is only on in Junk
    window.switch_section(TASKS)
    assert not any(getattr(window, f"act_{name}").isEnabled() for name in window.MAIL_ONLY)  # Delete in Tasks must never reach the messages
    window.switch_section(CALENDAR)
    window.switch_section(MAIL)
    assert {name: getattr(window, f"act_{name}").isEnabled() for name in window.MAIL_ONLY} == before


def answering(text: str) -> Callable[..., tuple[str, bool]]:
    def get_text(*arguments: object, **keywords: object) -> tuple[str, bool]:
        return text, True

    return get_text


def say_yes(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def test_new_refresh_and_the_collection_menu_work_in_a_section(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QInputDialog

    started(qtbot, world)
    window = world.window
    window.switch_section(CALENDAR)
    panel = window.pages[CALENDAR].panel
    wait(qtbot, lambda: len(collection_items(panel.tree)) == 1)
    asked: list[str] = []
    def new_item(self: object) -> None:
        asked.append("event")

    monkeypatch.setattr(type(window.pages[CALENDAR]), "new_item", new_item)
    window.act_new.trigger()  # New in the calendar section is a new event, not a new message
    assert asked == ["event"] and not window._composers  # noqa: SLF001
    monkeypatch.setattr(QInputDialog, "getText", answering("Work"))
    panel.new_collection()
    wait(qtbot, lambda: len(collection_items(panel.tree)) == 2)
    assert sorted(i.text(0) for i in collection_items(panel.tree)) == ["Calendar", "Work"]
    work = next(i for i in collection_items(panel.tree) if i.text(0) == "Work")
    monkeypatch.setattr(QInputDialog, "getText", answering("Job"))
    panel.rename_collection(str(work.data(0, ROLE_COLLECTION)))
    wait(qtbot, lambda: "Job" in [i.text(0) for i in collection_items(panel.tree)])
    monkeypatch.setattr(QMessageBox, "question", say_yes)
    panel.delete_collection(str(next(i for i in collection_items(panel.tree) if i.text(0) == "Job").data(0, ROLE_COLLECTION)))
    wait(qtbot, lambda: len(collection_items(panel.tree)) == 1)
    window.act_refresh.trigger()  # Refresh in a section reloads it, it does not check mail
    assert not window._checking  # noqa: SLF001
    window.switch_section(MAIL)
    window.act_refresh.trigger()
    wait(qtbot, lambda: not window._checking)  # noqa: SLF001


def test_the_last_section_is_remembered(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.switch_section(TASKS)
    assert world.window.settings.value("last_section") == TASKS
    world.window.switch_section(MAIL)
    assert world.window.settings.value("last_section") == MAIL
