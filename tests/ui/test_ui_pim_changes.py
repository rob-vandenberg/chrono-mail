from collections.abc import Mapping
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.changes import Change
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.collectionpanel import collection_items
from chrono_mail.ui.sections import CALENDAR
from tests.ui.test_ui_contacts import open_contacts, seed, select_rows
from tests.ui.test_ui_tasks import open_tasks
from tests.ui.test_ui_tasks import seed as seed_tasks
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pim_changes.py 1.1.205"

# --- Version history ----------------------------------------------------
# v1.1.205: the rows of given tasks (a page, or a task that changed) are not a whole list; nor may the places of every task be asked again (R-02)
# v0.5.159: Initial tests: calendar, contacts and tasks follow the facts; one item changes, nothing is fetched again (D-146 step 5)


def asked(world: World, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    """The commands the window asks for from now on, with what it asks."""
    found: list[tuple[str, dict[str, object]]] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        found.append((command, dict(values)))
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    return found


def whole_lists(commands: list[tuple[str, dict[str, object]]], command: str) -> int:
    """How often a whole list was fetched (asking for one item, or for the rows of given items, does not count)."""
    return sum(1 for name, values in commands if name == command and not values.get("item") and not values.get("items"))


def test_contacts_change_one_row_and_the_selection_moves_at_the_key_press(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    names = [str(getattr(row, "name")) for row in page.model.rows]
    commands = asked(world, monkeypatch)
    select_rows(page, 0)
    first, second = (str(getattr(page.model.rows[row], "item")) for row in (0, 1))
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    page.delete_selected()
    current = page.list.currentIndex()
    assert current.isValid() and str(getattr(page.model.rows[current.row()], "item")) == second  # at the key press, before the answer
    wait(qtbot, lambda: page.model.rowCount() == 2 and not page.model.pending)
    assert [str(getattr(row, "name")) for row in page.model.rows] == names[1:] and page.count_label.text() == "Contacts (2)"
    world.call("contact.edit", item=second, organization="Hoffman & Co")
    wait(qtbot, lambda: any(str(getattr(row, "subtitle")) == "Hoffman & Co" for row in page.model.rows))
    assert whole_lists(commands, "contact.list") == 0  # only the one contact was asked for
    assert first not in [str(getattr(row, "item")) for row in page.model.rows]


def test_tasks_change_one_task_and_a_completed_task_leaves_upcoming(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_tasks(world)
    page = open_tasks(world, qtbot, 4)
    page._choose_tab("upcoming")  # noqa: SLF001
    wait(qtbot, lambda: len(page.model.rows()) == 3)
    commands = asked(world, monkeypatch)
    rent = next(str(getattr(row, "item")) for row in page.model.rows() if getattr(row, "summary") == "Pay rent")
    world.call("task.complete", item=rent)
    wait(qtbot, lambda: len(page.model.rows()) == 2)
    world.call("task.edit", item=next(str(getattr(row, "item")) for row in page.model.rows() if getattr(row, "summary") == "Tidy the desk"), summary="Tidy the whole desk")
    wait(qtbot, lambda: any(getattr(row, "summary") == "Tidy the whole desk" for row in page.model.rows()))
    assert whole_lists(commands, "task.list") == 0 and whole_lists(commands, "task.places") == 0


def test_the_calendar_asks_only_for_the_event_that_changed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("pim.enable")
    calendar = next(info.collection for info in world.call("collection.list", kind="events"))
    today = date.today()
    event = world.call("event.add", calendar=calendar, summary="Dentist", start=f"{today.isoformat()}T10:00", end=f"{today.isoformat()}T11:00")
    started(qtbot, world)
    world.window.switch_section(CALENDAR)
    page = world.window.pages[CALENDAR]
    assert isinstance(page, CalendarPage)
    wait(qtbot, lambda: any(entry.summary == "Dentist" for entry in page.entries))
    commands = asked(world, monkeypatch)
    world.call("event.edit", item=event.item, summary="Dentist (moved)", start=f"{(today + timedelta(days=0)).isoformat()}T14:00", end=f"{today.isoformat()}T15:00")
    wait(qtbot, lambda: any(entry.summary == "Dentist (moved)" for entry in page.entries))
    assert [values.get("item") for name, values in commands if name == "event.list"] == [event.item]
    world.call("event.delete", item=event.item)
    wait(qtbot, lambda: not page.entries)
    assert sum(1 for name, _ in commands if name == "event.list") == 1  # a delete asks nothing


def test_the_panel_changes_one_entry_and_its_numbers(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    commands = asked(world, monkeypatch)
    made = world.call("collection.create", kind="contacts", name="Friends").collection
    wait(qtbot, lambda: any(str(item.data(0, 0x100 + 1)) == made for item in collection_items(page.panel.tree)))
    world.call("collection.edit", collection=made, name="Close friends", colour="#336699")
    wait(qtbot, lambda: any(item.text(0) == "Close friends" for item in collection_items(page.panel.tree)))
    book = page.panel.current_id()
    world.call("contact.add", collection=book, given="Ada", family="Lovelace")
    wait(qtbot, lambda: int(getattr(page.panel.info(book), "items")) == 4)
    world.call("collection.delete", collection=made)
    wait(qtbot, lambda: all(str(item.data(0, 0x100 + 1)) != made for item in collection_items(page.panel.tree)))
    assert whole_lists(commands, "collection.list") == 0


def test_a_sync_fetches_again_once_one_at_a_time(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(world)
    page = open_contacts(world, qtbot, 3)
    select_rows(page, 1)
    chosen = str(getattr(page.model.rows[1], "item"))
    commands = asked(world, monkeypatch)
    for _ in range(3):
        world.backend.changed.emit(Change("collection_changed", LOCAL_ACCOUNT_ID))
    wait(qtbot, lambda: world.backend._pending == {} and not page._refetching)  # noqa: SLF001
    assert whole_lists(commands, "collection.list") == 2  # one, then once more for what came meanwhile
    current = page.list.currentIndex()
    assert current.isValid() and str(getattr(page.model.rows[current.row()], "item")) == chosen
