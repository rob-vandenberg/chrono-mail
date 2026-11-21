from pathlib import Path

import pytest

from PySide6.QtCore import QModelIndex, QSettings
from PySide6.QtWidgets import QApplication, QLabel, QStyleOptionViewItem, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.sections import CALENDAR, CONTACTS, TASKS
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from chrono_mail.ui.tasksview import TasksPage
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_view_sizes.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: the active window sets the tooltips' size only while the mouse is over no window of the program
# v4.6.655: the Main window slider sizes every part of the main window and nothing else; Other windows leaves the main window alone;
#           the shares stay; a plain text message follows Text of messages only; tooltips follow the active window
# v4.6.654: the row of day names and the column left of the days follow the Calendar size
# v4.6.653: Month: the weekday row and a day's top follow the Calendar size, in clicks too
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


def test_the_month_view_weekday_row_and_days_follow_the_calendar_size(qtbot: QtBot) -> None:
    from datetime import date, timedelta

    from PySide6.QtCore import QPointF

    from chrono_mail.ui.calendarwidgets import MonthView

    month = MonthView()
    qtbot.addWidget(month)
    month.resize(700, 600)
    first = date(2026, 11, 16)
    month.set_data([first + timedelta(days=offset) for offset in range(28)], [], 11, first, 0)
    try:
        scale.set_view("calendar", 2.0)
        assert month.slot_at(QPointF(10, 40)) is None  # still the row of weekday names: 44 pixels high at twice the size
        assert month.slot_at(QPointF(10, 46)) == (first, -1)  # just below it: the first day
        week_height = (600 - 44) / 4
        assert month.slot_at(QPointF(10, 44 + week_height + 2)) == (first + timedelta(days=7), -1)
        month.grab()  # drawn at that size without trouble
    finally:
        scale.set_view("calendar", 1.0)
    assert month.slot_at(QPointF(10, 23)) == (first, -1)  # at the standard size the row is 22 pixels


def test_the_row_of_day_names_and_the_column_left_of_it_follow_the_calendar_size(qtbot: QtBot) -> None:
    from datetime import date, timedelta

    from PySide6.QtGui import QFont, QFontMetrics, QImage

    from zoneinfo import ZoneInfo

    from chrono_mail.ui.calendarwidgets import DayHeader, GridCanvas, gutter

    header, canvas = DayHeader(), GridCanvas()
    for widget in (header, canvas):
        qtbot.addWidget(widget)
    first = date(2026, 11, 16)
    days = [first + timedelta(days=offset) for offset in range(5)]
    try:
        scale.set_view("calendar", 2.0)
        font = QFont(header.font())
        font.setPointSizeF(_system_points() * 2.0)
        for widget in (header, canvas):
            widget.setFont(font)
        header.week_number = 47
        header.set_data(days, [], first)
        header.resize(900, header.height())
        widest = QFontMetrics(font).horizontalAdvance("Week 53")
        assert gutter(header) == gutter(canvas) >= widest + 10  # "Week 47" whole, and the hours below lined up with the days
        assert header.height() >= 2 * 40  # the weekday and the number one below the other, then the all-day row
        from PySide6.QtCore import QPointF

        canvas.set_data(days, [], ZoneInfo("UTC"), first)
        canvas.resize(900, canvas.height())
        for widget in (header, canvas):  # a click on the hours, "All day" or "Week 47" is on no day, also where 54 pixels end
            assert widget.slot_at(QPointF(gutter(widget) - 2, 30)) is None
            slot = widget.slot_at(QPointF(gutter(widget) + 2, 30))
            assert slot is not None and slot[0] == first
        image = QImage(header.size(), QImage.Format.Format_ARGB32)
        header.render(image)  # drawn at that size without trouble
    finally:
        scale.set_view("calendar", 1.0)


def _inside_own_size(widget: QWidget) -> bool:
    part: QWidget | None = widget
    while part is not None:
        if part.property(scale.OWN_SIZE_PROPERTY):
            return True
        part = part.parentWidget()
    return False


def _main_parts(world: World) -> dict[str, float]:
    window = world.window
    buttons = [button for button in window.findChildren(QToolButton) if button.window() is window and button.isVisibleTo(window)
               and not _inside_own_size(button)]  # a button of the top bar or the sidebar, not the Sorted by of the message list
    return {"tree": window.tree.font().pointSizeF(), "button": buttons[0].font().pointSizeF(), "status": window.report.label.font().pointSizeF(),
            "subject": window.reader._subject.font().pointSizeF()}  # noqa: SLF001


def _own_parts(world: World) -> dict[str, float]:
    window = world.window
    return {"list": window.list_view.font().pointSizeF(), "calendar": window.pages[CALENDAR].content.font().pointSizeF(),
            "contacts": window.pages[CONTACTS].content.font().pointSizeF(), "message": window.reader._text.font().pointSizeF()}  # noqa: SLF001


def test_the_main_window_slider_sizes_every_part_of_the_main_window_and_nothing_else(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    window.show()
    own = _own_parts(world)
    try:
        window.try_interface_scale(2.0)
        main = round(_system_points() * 2.0, 2)
        parts = _main_parts(world)
        assert parts == {"tree": main, "button": main, "status": main, "subject": round(main * 1.35, 2)}
        assert _own_parts(world) == own  # the message list, the views and the content of a message keep their sizes
        later = QLabel("made later", window.tree.parentWidget())
        later.show()
        qtbot.wait(10)
        assert later.font().pointSizeF() == main  # a part made after the change takes the size of the part it is in
    finally:
        window.try_interface_scale(1.0)
    assert _main_parts(world)["tree"] == round(_system_points(), 2)


def test_the_other_windows_slider_leaves_the_main_window_alone(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QDialog

    window = world.window
    window.start()
    window.show()
    before, own = _main_parts(world), _own_parts(world)
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    inside = QLabel("in a dialog", dialog)
    try:
        window.try_windows_scale(2.0)
        assert _main_parts(world) == before and _own_parts(world) == own
        dialog.show()
        qtbot.wait(10)
        assert inside.font().pointSizeF() == round(_system_points() * 2.0, 2)  # the other windows do grow
    finally:
        window.try_windows_scale(1.0)


def test_a_plain_text_message_follows_text_of_messages_only(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    try:
        window.try_windows_scale(2.0)
        window.try_interface_scale(1.5)
        window.try_reading_scale(1.25)
        assert window.reader._text.font().pointSizeF() == max(6.0, _system_points() * 1.25)  # noqa: SLF001
    finally:
        window.try_windows_scale(1.0)
        window.try_interface_scale(1.0)
        window.try_reading_scale(1.0)


def test_a_tooltip_takes_the_size_of_the_window_that_is_active(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QDialog, QToolTip

    window = world.window
    window.start()
    original = QToolTip.font()
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    try:
        window.try_interface_scale(2.0)
        window.try_windows_scale(0.75)
        def nowhere(*where: object) -> QWidget | None:
            return None  # the mouse over no window of the program: the active one says (4.8.662)

        monkeypatch.setattr(QApplication, "widgetAt", staticmethod(nowhere))
        monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: window))
        window._tooltip_font()  # noqa: SLF001
        assert QToolTip.font().pointSizeF() == round(_system_points() * 2.0, 2)
        monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dialog))
        window._tooltip_font()  # noqa: SLF001
        assert QToolTip.font().pointSizeF() == round(_system_points() * 0.75, 2)
    finally:
        monkeypatch.undo()
        window.try_interface_scale(1.0)
        window.try_windows_scale(1.0)
        QToolTip.setFont(original)
