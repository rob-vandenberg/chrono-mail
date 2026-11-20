from pathlib import Path

from PySide6.QtCore import QModelIndex, QSettings
from PySide6.QtWidgets import QApplication, QLabel, QStyleOptionViewItem, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.sections import CALENDAR, CONTACTS, TASKS
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_view_sizes.py 4.6.652"

# --- Version history ----------------------------------------------------
# v4.6.652: Calendar, Contacts and Tasks: the rows of Sizes, each slider its own view only, Cancel, the first start (D-367)
# v4.6.651: the row above the message list (Sorted by, Filter, the count) follows the Message list slider, not the Main window slider (D-367)


def _row_sizes(header: QWidget) -> list[float]:
    return [header.font().pointSizeF()] + [part.font().pointSizeF() for part in header.findChildren(QWidget) if part.isVisibleTo(header)]


def test_the_row_above_the_message_list_follows_the_message_list_slider(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    header = window.messages.list_header
    texts = [window.messages.sort_button.text(), window.messages.count_label.text()]
    assert texts[0].startswith("Sorted by") and "message" in texts[1]
    standard = window.list_view.font().pointSizeF()
    try:
        window.try_list_scale(1.5)
        large = window.list_view.font().pointSizeF()
        assert large > standard
        assert set(_row_sizes(header)) == {large}  # the button, the Filter, the count: all at the list's size
        window.try_interface_scale(0.75)  # the Main window slider leaves the row alone
        assert set(_row_sizes(header)) == {large}
        margins = window.messages.header_layout.contentsMargins()
        assert (margins.left(), margins.top()) == (scale.lpx(10), scale.lpx(6))
    finally:
        window.try_interface_scale(1.0)
        window.try_list_scale(1.0)
    assert set(_row_sizes(header)) == {standard}


def _system_points() -> float:
    app = QApplication.instance()
    return appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS


def _sizes_of(world: World) -> dict[str, object]:
    """What each view shows at its size: its text, and one part of a fixed size."""
    window = world.window
    calendar, contacts, tasks = window.pages[CALENDAR], window.pages[CONTACTS], window.pages[TASKS]
    assert isinstance(calendar, CalendarPage) and isinstance(contacts, ContactsPage) and isinstance(tasks, TasksPage)
    option, index = QStyleOptionViewItem(), QModelIndex()
    return {
        "calendar text": calendar.today_button.font().pointSizeF(), "hours": calendar.grid.canvas.hour_height,
        "title": calendar.title.font().pointSizeF(),
        "contacts text": contacts.count_label.font().pointSizeF(), "contact row": contacts.delegate.sizeHint(option, index).height(),
        "photo": contacts.detail.photo.width(),
        "tasks text": tasks.count_label.font().pointSizeF(), "task row": tasks.delegate.sizeHint(option, index).height(),
        "sidebars": tuple(page.side.font().pointSizeF() for page in (calendar, contacts, tasks)),
        "list": window.list_view.font().pointSizeF(),
    }


def test_the_sizes_page_has_calendar_contacts_and_tasks_below_message_list(world: World, qtbot: QtBot) -> None:
    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, SettingsContext(tray_available=False, shortcut_actions=window._shortcut_registry()), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    page = dialog.interface_slider.parentWidget()
    assert page is not None
    rows = [label.text() for label in page.findChildren(QLabel) if label.text() in (
        "Main window", "Message list", "Calendar", "Contacts", "Tasks", "Other windows and dialogs", "Text of messages")]
    by_place = sorted(rows, key=lambda text: next(label.y() for label in page.findChildren(QLabel) if label.text() == text))
    assert by_place == ["Main window", "Message list", "Calendar", "Contacts", "Tasks", "Other windows and dialogs", "Text of messages"]
    for view in scale.VIEWS:
        assert dialog.view_sliders[view].parentWidget() is page and dialog.view_resets[view].text() == "Reset"


def test_each_view_slider_sizes_its_own_view_and_nothing_else(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    standard = _sizes_of(world)
    try:
        window.try_view_scale("calendar", 1.5)
        calendar = _sizes_of(world)
        assert calendar["calendar text"] == round(_system_points() * 1.5, 2) and calendar["hours"] == round(48 * 1.5)
        assert calendar["title"] == round(_system_points() * 1.5, 2) * 1.15
        assert {key: value for key, value in calendar.items() if key not in ("calendar text", "hours", "title")} == \
            {key: value for key, value in standard.items() if key not in ("calendar text", "hours", "title")}
        window.try_view_scale("contacts", 2.0)
        contacts = _sizes_of(world)
        assert contacts["contacts text"] == round(_system_points() * 2.0, 2) and contacts["contact row"] == 112 and contacts["photo"] == 100
        assert contacts["tasks text"] == standard["tasks text"] and contacts["task row"] == standard["task row"]
        window.try_view_scale("tasks", 0.75)
        tasks = _sizes_of(world)
        assert tasks["tasks text"] == round(_system_points() * 0.75, 2) and tasks["task row"] == round(52 * 0.75)
        assert tasks["sidebars"] == standard["sidebars"] and tasks["list"] == standard["list"]  # the sidebars and the message list keep theirs
        window.try_interface_scale(1.25)  # the Main window slider leaves the three alone
        assert _sizes_of(world)["calendar text"] == calendar["calendar text"]
    finally:
        window.try_interface_scale(1.0)
        for view in scale.VIEWS:
            window.try_view_scale(view, 1.0)
    assert _sizes_of(world) == standard


def test_cancel_puts_the_view_sizes_back_and_ok_keeps_them(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    standard = _sizes_of(world)
    dialog = SettingsDialog(world.backend, window.settings, SettingsContext(tray_available=False, shortcut_actions=window._shortcut_registry()), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.view_scale_tried.connect(window.try_view_scale)
    try:
        dialog.view_sliders["calendar"].setValue(scale.slider_position(1.5))
        assert _sizes_of(world)["hours"] == 72  # at once
        window._settings_closed(dialog, False)  # noqa: SLF001 - Cancel
        assert _sizes_of(world) == standard
        dialog.view_sliders["tasks"].setValue(scale.slider_position(1.5))
        assert dialog.chosen().tasks_scale == 1.5
        dialog.view_resets["tasks"].click()
        assert dialog.chosen().tasks_scale == 1.0
    finally:
        for view in scale.VIEWS:
            window.try_view_scale(view, 1.0)


def test_at_the_first_start_the_views_keep_the_main_window_size(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    store.setValue("interface_scale", 1.25)
    store.setValue("contacts_scale", 0.9)
    assert prefs.saved_view(store, "calendar") == 1.25  # not stored yet: the Main window's
    prefs.settle_view_sizes(store)
    store.setValue("interface_scale", 1.5)  # the Main window slider moves later: the views no longer follow it
    assert [prefs.saved_view(store, view) for view in scale.VIEWS] == [1.25, 0.9, 1.25]
    assert prefs.read_preferences(store).tasks_scale == 1.25
    empty = QSettings(str(tmp_path / "b.ini"), QSettings.Format.IniFormat)
    prefs.settle_view_sizes(empty)
    assert [prefs.saved_view(empty, view) for view in scale.VIEWS] == [1.0, 1.0, 1.0]
