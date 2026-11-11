import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QImage, QPalette, QStandardItemModel
from PySide6.QtWidgets import QDialog, QTabWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.collectionpanel import PLUS_COLUMN, PLUS_SIZE, CollectionPanel, collection_items
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.sections import TASKS
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.taskeditor import TaskEditor
from chrono_mail.ui.tasksview import TasksPage

from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.test_ui_tasks import open_tasks
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sidebar_lists.py 4.4.578"

# --- Version history ----------------------------------------------------
# v4.4.578: Settings > Sidebar has a Contacts tab between Calendar and Tasks (the owner, D-331)
# v4.4.570: Unticking keeps the blue plus; it moves when its calendar is left out in Settings (the owner)
# v4.4.569: A new appointment or task cannot be put in a read-only calendar or task list (D-326)
# v4.4.568: Initial: Settings > Sidebar's Calendar and Tasks tabs, the blue plus and Use for new appointments or tasks (D-326)


def with_work(world: World, qtbot: QtBot) -> tuple[CalendarPage, str, str]:
    """The calendar of this computer and a second one, Work: (page, the first's id, Work's id)."""
    page = open_calendar(world, qtbot, layout.WEEK)
    own = calendar_id(world)
    work = str(world.call("collection.create", kind="events", name="Work").collection)
    wait(qtbot, lambda: page.panel._entry(work) is not None)  # noqa: SLF001
    return page, own, work


def entry_of(panel: CollectionPanel, collection: str) -> QTreeWidgetItem:
    entry = panel._entry(collection)  # noqa: SLF001
    assert entry is not None
    return entry


def marked(panel: CollectionPanel) -> list[str]:
    """The collections with the blue plus."""
    return [str(item.data(0, Qt.ItemDataRole.UserRole + 1)) for item in collection_items(panel.tree) if not item.icon(PLUS_COLUMN).isNull()]


def menu_action(panel: CollectionPanel, collection: str, text: str) -> QAction:
    menu = panel.menu_at(panel.tree.visualItemRect(entry_of(panel, collection)).center())
    return next(action for action in menu.actions() if action.text() == text)


def settings_ok(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, untick: dict[str, list[str]]) -> list[list[str]]:
    """Settings, Sidebar: untick these calendars ("events") or task lists ("tasks"), then OK. Gives the tabs' names as they were."""
    seen: list[list[str]] = []

    def untick_and_ok(dialog: SettingsDialog) -> QDialog.DialogCode:
        tabs = dialog.sidebar_page.findChild(QTabWidget, "tabs")
        assert tabs is not None
        seen.append([tabs.tabText(number) for number in range(tabs.count())])
        for kind, collections in untick.items():
            wait(qtbot, lambda chosen=kind: chosen in dialog.sidebar_page.list_boxes)
            for collection in collections:
                dialog.sidebar_page.list_boxes[kind][collection].setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", untick_and_ok)
    world.window.open_settings("Sidebar")
    return seen


def test_settings_sidebar_has_tabs_and_a_calendar_left_out_leaves_the_sidebar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    world.call("event.add", calendar=work, summary="Planning", start="2026-10-14T09:00", end="2026-10-14T10:00", time_zone="UTC")
    wait(qtbot, lambda: any(entry.summary == "Planning" for entry in page.entries))
    seen = settings_ok(world, qtbot, monkeypatch, {"events": [work]})
    assert seen == [["Mail", "Calendar", "Contacts", "Tasks"]]  # the sections' names and order (D-331)
    wait(qtbot, lambda: page.panel._entry(work) is None)  # noqa: SLF001 - out of the sidebar
    wait(qtbot, lambda: not any(entry.summary == "Planning" for entry in page.entries))  # its appointments are not shown
    assert [info.collection for info in page.panel.collections] == [own]  # never offered for new appointments
    assert work in [info.collection for info in page.panel.every]  # still kept and synchronised


def test_the_last_calendar_shown_cannot_be_left_out(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    greyed: list[bool] = []

    def untick_and_ok(dialog: SettingsDialog) -> QDialog.DialogCode:
        wait(qtbot, lambda: "events" in dialog.sidebar_page.list_boxes)
        boxes = dialog.sidebar_page.list_boxes["events"]
        boxes[work].setChecked(False)
        greyed.append(not boxes[own].isEnabled() and "cannot be left out" in boxes[own].toolTip())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", untick_and_ok)
    world.window.open_settings("Sidebar")
    assert greyed == [True]
    assert page.panel._entry(work) is not None  # noqa: SLF001 - Cancel changes nothing


def test_the_blue_plus_marks_where_new_appointments_go_and_the_menu_moves_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    assert marked(page.panel) == [own] and entry_of(page.panel, own).toolTip(PLUS_COLUMN) == "New appointments go here"
    action = menu_action(page.panel, work, "Use for new appointments")
    assert action.isEnabled() and not action.isChecked()
    action.trigger()
    assert marked(page.panel) == [work] and entry_of(page.panel, work).toolTip(PLUS_COLUMN) == "New appointments go here"
    assert entry_of(page.panel, own).toolTip(PLUS_COLUMN) == ""
    opened: list[str] = []

    def open_editor(editor: EventEditor) -> int:
        opened.append(str(editor.calendar.currentData()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_editor)
    page.new_item()
    page._slot_activated(page.anchor, 600)  # noqa: SLF001 - a double click on empty space
    assert opened == [work, work]


def test_a_read_only_calendar_never_gets_the_blue_plus(world: World, qtbot: QtBot) -> None:
    page, own, work = with_work(world, qtbot)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(work, read_only=True)
    page.panel.reload()
    wait(qtbot, lambda: bool(getattr(page.panel.info(work), "read_only", False)))
    action = menu_action(page.panel, work, "Use for new appointments")
    assert not action.isEnabled() and "read only" in action.toolTip()


def test_unticking_the_marked_calendar_keeps_the_plus_and_new_appointments_go_there(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick only hides or shows a calendar's appointments for a moment: the blue plus stays (the owner, 4.4.570)."""
    page, own, work = with_work(world, qtbot)
    said: list[str] = []
    page.status = said.append
    page.panel.use_for_new(work)
    entry_of(page.panel, work).setCheckState(0, Qt.CheckState.Unchecked)
    assert marked(page.panel) == [work] and said == []
    assert menu_action(page.panel, work, "Use for new appointments").isEnabled()
    entry_of(page.panel, own).setCheckState(0, Qt.CheckState.Unchecked)
    assert menu_action(page.panel, own, "Use for new appointments").isEnabled()  # an unticked one can get it too
    opened: list[str] = []

    def open_editor(editor: EventEditor) -> int:
        opened.append(str(editor.calendar.currentData()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_editor)
    page.new_item()
    assert opened == [work]


def test_the_plus_moves_when_its_calendar_is_left_out_in_settings(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    said: list[str] = []
    page.status = said.append
    page.panel.use_for_new(work)
    settings_ok(world, qtbot, monkeypatch, {"events": [work]})
    wait(qtbot, lambda: marked(page.panel) == [own])
    assert said[-1] == "New appointments now go to Calendar"


def test_the_marked_calendar_is_remembered(world: World, qtbot: QtBot) -> None:
    page, own, work = with_work(world, qtbot)
    page.panel.use_for_new(work)
    again = CollectionPanel(world.backend, world.window.settings, "events", True)
    qtbot.addWidget(again)
    again.reload()
    wait(qtbot, lambda: again._entry(work) is not None)  # noqa: SLF001
    assert marked(again) == [work]


def test_new_tasks_go_to_the_task_list_with_the_blue_plus(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_tasks(world, qtbot, 0)
    assert isinstance(page, TasksPage)
    errands = str(world.call("collection.create", kind="tasks", name="Errands").collection)
    wait(qtbot, lambda: page.panel._entry(errands) is not None)  # noqa: SLF001
    first = page.panel.target_id()
    assert first != errands and entry_of(page.panel, first).toolTip(PLUS_COLUMN) == "New tasks go here"
    menu_action(page.panel, errands, "Use for new tasks").trigger()
    assert marked(page.panel) == [errands]
    opened: list[str] = []

    def open_editor(editor: TaskEditor) -> int:
        opened.append(str(editor.list.currentData()))
        return 0

    monkeypatch.setattr(TaskEditor, "exec", open_editor)
    page.new_item()
    assert opened == [errands]
    assert world.window.pages[TASKS] is page


def test_a_task_list_left_out_in_settings_leaves_the_tasks_sidebar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_tasks(world, qtbot, 0)
    errands = str(world.call("collection.create", kind="tasks", name="Errands").collection)
    wait(qtbot, lambda: page.panel._entry(errands) is not None)  # noqa: SLF001
    settings_ok(world, qtbot, monkeypatch, {"tasks": [errands]})
    wait(qtbot, lambda: page.panel._entry(errands) is None)  # noqa: SLF001


def test_the_blue_plus_shows_on_a_selected_row_too(world: World, qtbot: QtBot) -> None:
    page, own, work = with_work(world, qtbot)
    icon = entry_of(page.panel, own).icon(PLUS_COLUMN)
    normal = icon.pixmap(PLUS_SIZE, PLUS_SIZE, QIcon.Mode.Normal).toImage()
    selected = icon.pixmap(PLUS_SIZE, PLUS_SIZE, QIcon.Mode.Selected).toImage()
    palette = page.panel.tree.palette()
    assert solid_colour(normal) == palette.color(QPalette.ColorRole.Highlight).rgb()
    assert solid_colour(selected) == palette.color(QPalette.ColorRole.HighlightedText).rgb()  # never blue on the selection's blue


def solid_colour(image: QImage) -> int:
    """The colour of the most solid pixel of a drawing (its edges are softened)."""
    pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())]
    return max(pixels, key=lambda colour: colour.alpha()).rgb()


def test_with_every_calendar_unticked_new_starts_in_the_one_with_the_plus_never_a_read_only_one(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(own, read_only=True)
    page.panel.reload()
    wait(qtbot, lambda: bool(getattr(page.panel.info(own), "read_only", False)))
    for collection in (own, work):
        entry_of(page.panel, collection).setCheckState(0, Qt.CheckState.Unchecked)
    assert marked(page.panel) == [work]  # unticked: the plus stays on the one that takes appointments
    opened: list[str] = []

    def open_editor(editor: EventEditor) -> int:
        opened.append(str(editor.calendar.currentData()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_editor)
    page.new_item()
    assert opened == [work]  # never the read-only one


def test_with_only_read_only_calendars_new_opens_nothing_and_says_why(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot, layout.WEEK)
    own = calendar_id(world)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(own, read_only=True)
    page.panel.reload()
    wait(qtbot, lambda: bool(getattr(page.panel.info(own), "read_only", False)))
    said: list[str] = []
    page.status = said.append
    opened: list[str] = []

    def open_editor(editor: EventEditor) -> int:
        opened.append(str(editor.calendar.currentData()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_editor)
    page.new_item()
    page._slot_activated(page.anchor, 600)  # noqa: SLF001
    assert opened == [] and said and "read only" in said[-1]


def test_a_new_appointment_cannot_be_put_in_a_read_only_calendar(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page, own, work = with_work(world, qtbot)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(own, read_only=True)
    page.panel.reload()
    wait(qtbot, lambda: bool(getattr(page.panel.info(own), "read_only", False)))
    seen: list[tuple[bool, str, bool, str]] = []

    def open_editor(editor: EventEditor) -> int:
        model = editor.calendar.model()
        assert isinstance(model, QStandardItemModel)
        row = editor.calendar.findData(own)
        free = editor.calendar.findData(work)
        item, other = model.item(row), model.item(free)
        assert item is not None and other is not None
        seen.append((item.isEnabled(), item.toolTip(), other.isEnabled(), str(editor.calendar.currentData())))
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_editor)
    page.new_item()
    assert seen == [(False, "This calendar is read only: it takes no new appointments", True, work)]


def test_a_new_task_cannot_be_put_in_a_read_only_task_list(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_tasks(world, qtbot, 0)
    errands = str(world.call("collection.create", kind="tasks", name="Errands").collection)
    wait(qtbot, lambda: page.panel._entry(errands) is not None)  # noqa: SLF001
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(errands, read_only=True)
    page.panel.reload()
    wait(qtbot, lambda: bool(getattr(page.panel.info(errands), "read_only", False)))
    seen: list[tuple[bool, str]] = []

    def open_editor(editor: TaskEditor) -> int:
        model = editor.list.model()
        assert isinstance(model, QStandardItemModel)
        item = model.item(editor.list.findData(errands))
        assert item is not None
        seen.append((item.isEnabled(), item.toolTip()))
        return 0

    monkeypatch.setattr(TaskEditor, "exec", open_editor)
    page.new_item()
    assert seen == [(False, "This task list is read only: it takes no new tasks")]


def test_unticking_the_marked_task_list_keeps_the_plus(world: World, qtbot: QtBot) -> None:
    page = open_tasks(world, qtbot, 0)
    errands = str(world.call("collection.create", kind="tasks", name="Errands").collection)
    wait(qtbot, lambda: page.panel._entry(errands) is not None)  # noqa: SLF001
    page.panel.use_for_new(errands)
    entry_of(page.panel, errands).setCheckState(0, Qt.CheckState.Unchecked)
    assert marked(page.panel) == [errands] and page.panel.new_items_id() == errands
