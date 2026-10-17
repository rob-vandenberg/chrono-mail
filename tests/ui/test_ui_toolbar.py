from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QLayout, QMenu, QTableView, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui import theme
from chrono_mail.ui.calendarlayout import MONTH
from chrono_mail.ui.calendarview import CalendarPage, RangeCalendar
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, TASKS
from chrono_mail.ui.toolbar import ToolbarRow
from tests.ui.test_ui_window import started
from tests.ui.world import World, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_toolbar.py 1.1.173"

# --- Version history ----------------------------------------------------
# v1.1.173: The window's minimum is 768 px in every section (more only when the contents need it at a larger size), also when it is shown in Calendar before Mail was ever on screen
# v0.5.170: Another section never makes the window wider (one toolbar, a row per section)
# v0.5.169: At 240 px the small month calendar shows all seven days
# v0.5.168: A section's search field is Mail's without This folder; the small month calendar lets the sidebar be 240 px
# v0.5.167: Initial tests of the folding toolbar: More only when needed, the order things give way, the search field always shown (D-154)

NAMES: tuple[str, ...] = ("Reply", "Reply all", "Forward", "Archive", "Delete")


def make_row(qtbot: QtBot, extras: bool = False) -> tuple[ToolbarRow, list[QAction], SearchField]:
    """A toolbar row on its own, like Mail's: Menu, New and Refresh at the left, five buttons in the middle, the search field with This folder."""
    row = ToolbarRow()
    qtbot.addWidget(row)
    row.line.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)  # on its own it is a window; in the program the toolbar gives it its width
    holder = row
    menu = QToolButton()
    menu.setText("Menu")
    row.set_menu_button(menu)
    actions = [QAction(name, holder) for name in ("New", "Refresh", *NAMES)]
    for action in actions[:2]:
        row.add_left(action)
    for action in actions[2:]:
        row.add_center(action)
    field = SearchField(holder)
    row.set_search(field)
    if extras:
        row.set_more_extras([QAction("Import…", holder), QAction("Export…", holder)])
    row.show()
    return row, actions, field


def state_at(row: ToolbarRow, width: int) -> tuple[bool, tuple[str, ...]]:
    row.resize(width, row.height())
    row.arrange()
    return row.compact, tuple(button_text(unit.buttons[0]) for unit in row.folded)


def button_text(button: QToolButton) -> str:
    action = button.defaultAction()
    return action.text() if action is not None else button.text()


def test_more_is_shown_only_when_a_button_does_not_fit(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    row.resize(row.sizeHint().width() + 50, row.height())
    row.arrange()
    assert row.more.isHidden() and not row.folded and not row.compact
    assert all(not row.button_for(action).isHidden() for action in actions)
    assert field.scope.text() == "This folder"
    row.resize(row.minimumSizeHint().width(), row.height())
    row.arrange()
    assert not row.more.isHidden() and len(row.folded) == len(actions)
    assert all(row.button_for(action).isHidden() for action in actions)


def test_this_folder_shrinks_first_then_the_buttons_fold_from_the_right(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    seen: list[tuple[bool, tuple[str, ...]]] = []
    for width in range(row.sizeHint().width() + 20, row.minimumSizeHint().width() - 1, -4):
        state = state_at(row, width)
        if not seen or seen[-1] != state:
            seen.append(state)
    order = ("New", "Refresh", *NAMES)
    assert seen[0] == (False, ()) and seen[1] == (True, ())  # This folder shows only its ▾ before any button folds
    folded = [state[1] for state in seen[1:]]
    assert all(compact for compact, _names in seen[1:]) and all(names == order[len(order) - len(names):] for names in folded)  # always the right-most ones
    assert all(len(earlier) < len(later) for earlier, later in zip(folded, folded[1:])) and folded[-1] == order  # one more at a time (two when More itself takes the room), until all are in More
    assert field.scope.text() == ""  # only its ▾


def test_the_search_field_keeps_its_width_until_everything_is_folded_and_never_goes(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    state_at(row, row.sizeHint().width())
    assert field.width() == field.preferred_width(False)
    state_at(row, row.sizeHint().width() - 30)
    assert field.width() == field.preferred_width(True)
    narrowest = row.minimumSizeHint().width()
    state_at(row, narrowest + 40)
    assert len(row.folded) == len(actions) and field.preferred_width(True) > field.width() > field.minimum_width()  # it shrinks only now
    state_at(row, narrowest)
    assert field.width() == field.minimum_width() and field.isVisibleTo(row) and row.menu_button is not None and row.menu_button.isVisibleTo(row)


def test_everything_comes_back_as_the_row_gets_wider(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    widths = list(range(row.minimumSizeHint().width(), row.sizeHint().width() + 20, 7))
    down = [state_at(row, width) for width in reversed(widths)]
    up = [state_at(row, width) for width in widths]
    assert up == list(reversed(down))  # a width always gives the same state, whichever way the window was resized
    assert not row.folded and row.more.isHidden() and field.scope.text() == "This folder"


def test_more_comes_right_after_the_last_button_shown(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    for width in range(row.sizeHint().width() - 30, row.minimumSizeHint().width() - 1, -10):
        state_at(row, width)
        if row.more.isHidden():
            continue
        shown = [action for action in actions if row.button_for(action).isVisibleTo(row)]
        last = row.button_for(shown[-1]) if shown else row.menu_button
        assert last is not None
        order = row.widgets_in_order()
        assert order.index(row.more) == order.index(last) + 1


def test_more_holds_the_folded_buttons_greyed_out_as_they_are_with_their_menus(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot)
    forward, delete = actions[4], actions[6]
    forward_menu = QMenu(row)
    forward_menu.addAction("Forward as attachment")
    forward_button = row.button_for(forward)
    forward_button.setMenu(forward_menu)
    forward_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    delete.setEnabled(False)
    state_at(row, row.minimumSizeHint().width())
    row.more_menu.aboutToShow.emit()
    entries = row.more_menu.actions()
    assert [entry.text() for entry in entries] == ["New", "Refresh", "Reply", "Reply all", "Forward", "Archive", "Delete"]
    assert entries[-1] is delete and not entries[-1].isEnabled()  # greyed out there too
    submenu = entries[4].menu()
    assert isinstance(submenu, QMenu)
    submenu.aboutToShow.emit()
    assert [entry.text() for entry in submenu.actions() if not entry.isSeparator()] == ["Forward", "Forward as attachment"]  # a click on the button forwards: so does the first entry
    assert submenu.actions()[0] is forward


def test_entries_that_live_in_more_keep_it_shown_and_come_last(qtbot: QtBot) -> None:
    row, actions, field = make_row(qtbot, extras=True)
    state_at(row, row.sizeHint().width() + 50)
    assert not row.more.isHidden() and not row.folded
    row.more_menu.aboutToShow.emit()
    assert [entry.text() for entry in row.more_menu.actions()] == ["Import…", "Export…"]
    state_at(row, row.minimumSizeHint().width())
    row.more_menu.aboutToShow.emit()
    entries = row.more_menu.actions()
    assert entries[-3].isSeparator() and [entry.text() for entry in entries[-2:]] == ["Import…", "Export…"] and entries[0].text() == "New"


def test_the_mail_toolbar_keeps_menu_and_the_search_field_at_the_narrowest_window(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.resize(3000, 700)
    qtbot.wait(50)
    assert window.toolbar.more.isHidden() and window.scope.text() == "This folder"
    window.resize(200, 700)  # the window stops at its minimum
    qtbot.wait(50)
    assert window.width() >= window.toolbar.minimumSizeHint().width()
    assert window.search_pill.isVisible() and window.menu_buttons[0].isVisible() and window.toolbar.more.isVisible()
    assert window.toolbar.button_for(window.act_delete).isHidden() and window.scope.text() == ""
    folded = [action for action in (window.act_reply, window.act_reply_all, window.act_forward, window.act_archive, window.act_snooze, window.act_delete) if window.toolbar.button_for(action).isHidden()]
    assert folded and all(action in window.actions() for action in folded)  # their keys still work: the window holds the actions


def test_the_calendar_views_merge_into_one_button_that_shows_the_chosen_view(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.switch_section(CALENDAR)
    page = window.pages[CALENDAR]
    assert isinstance(page, CalendarPage)
    row = page.toolbar
    views = [row.button_for(action) for action in page.view_actions.values()]
    assert all(button.objectName() == "viewButton" and not button.icon().isNull() for button in views)
    assert 'QToolButton#viewButton:checked' in theme.stylesheet() and "palette(highlight)" in next(line for line in theme.stylesheet().splitlines() if "#viewButton:checked {" in line)
    window.resize(3000, 700)
    qtbot.wait(50)
    assert not row.merged and all(button.isVisible() for button in views) and page.views_button.isHidden()
    seen: list[tuple[bool, int]] = []
    for width in range(row.sizeHint().width() + 100, window.minimumSizeHint().width() - 1, -4):
        window.resize(width, 700)
        QApplication.processEvents()
        if not seen or seen[-1] != (row.merged, len(row.folded)):
            seen.append((row.merged, len(row.folded)))
    assert seen[:2] == [(False, 0), (True, 0)] and seen[2][0] and seen[2][1] >= 1  # the views merge before Delete folds
    assert [button_text(unit.buttons[0]) for unit in row.folded][-1] == "Delete"
    assert page.views_button.text() == "Week" and not page.views_button.icon().isNull()
    page.view_actions[MONTH].trigger()
    assert page.views_button.text() == "Month" and page.view == MONTH


def test_contacts_and_tasks_have_icons_and_contacts_keeps_import_and_export_in_more(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.resize(3000, 700)
    window.switch_section(CONTACTS)
    qtbot.wait(50)
    contacts = window.pages[CONTACTS]
    tasks = window.pages[TASKS]
    for page in (contacts, tasks):
        assert not getattr(page, "tag_button").icon().isNull() and not getattr(page, "move_button").icon().isNull()
    row = contacts.toolbar
    assert row.more.isVisible() and not row.folded
    row.more_menu.aboutToShow.emit()
    assert [entry.text() for entry in row.more_menu.actions()] == ["Import vCard file…", "Export this address book…"]


def test_the_search_field_of_a_section_searches_while_typing_and_its_cross_empties_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.switch_section(TASKS)
    page = window.pages[TASKS]
    field = getattr(page, "search_field")
    assert isinstance(field, SearchField) and field.objectName() == "searchPill" and not field.has_scope and field.scope.isHidden()
    assert field.line_edit.placeholderText() == "Search tasks" and field.look() != "active"
    field.line_edit.setText("holiday")
    assert field.look() == "active"
    field.end_button.click()
    assert field.line_edit.text() == "" and getattr(page, "search") is field.line_edit and field.look() != "active"


def test_a_section_search_field_is_as_wide_as_mails_without_this_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.resize(3000, 700)
    qtbot.wait(50)
    mail = window.search_pill
    layout = mail.layout()
    assert layout is not None
    this_folder = mail.scope.width() + mail.separator.width() + 2 * layout.spacing()
    assert mail.width() == mail.preferred_width(False)
    for section in (CALENDAR, CONTACTS, TASKS):
        window.switch_section(section)
        qtbot.wait(50)
        field = getattr(window.pages[section], "search_field")
        assert isinstance(field, SearchField) and field.width() == mail.width() - this_folder


def test_the_small_month_calendar_lets_the_sidebar_be_240_pixels(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    page = window.pages[CALENDAR]
    assert isinstance(page, CalendarPage)
    assert page.mini.minimumWidth() == 240  # the owner's choice; its own wish was 266 before its day columns could shrink
    assert window.side_stack.minimumSizeHint().width() == 240  # the widest of the sections' sidebars, so the sidebar of every section


def test_the_small_month_calendar_shows_all_seven_days_at_240_pixels(qtbot: QtBot) -> None:
    for points in (9.0, 11.0):  # the owner's size, and larger
        calendar = RangeCalendar()
        qtbot.addWidget(calendar)
        font = calendar.font()
        font.setPointSizeF(points)
        calendar.setFont(font)
        calendar.resize(240, 190)
        calendar.show()
        QApplication.processEvents()
        grid = calendar.findChild(QTableView)
        assert grid is not None
        header = grid.horizontalHeader()
        assert header.count() == 7 and grid.horizontalScrollBar().maximum() == 0  # nothing sideways out of sight
        assert sum(header.sectionSize(column) for column in range(7)) <= grid.viewport().width()


def test_showing_another_section_keeps_the_width_of_the_narrowest_window(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.switch_section(CALENDAR)
    window.resize(200, 700)  # as narrow as it will go
    qtbot.wait(50)
    narrowest = window.width()
    for section in (MAIL, CONTACTS, TASKS, CALENDAR, MAIL):
        window.switch_section(section)
        qtbot.wait(50)
        assert window.width() == narrowest, section  # the owner's report: Mail made the window 26 px wider


def test_the_window_is_never_narrower_than_768_pixels_and_mail_keeps_the_width_of_calendar(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    window.switch_section(CALENDAR)  # shown in Calendar before Mail was laid out on screen (it was 652 px, then Mail made it 768)
    window.resize(100, 700)
    qtbot.wait(50)
    assert window.width() == 768 and window.minimumWidth() == 768
    window.switch_section(MAIL)
    qtbot.wait(50)
    assert window.width() == 768


def test_at_a_larger_size_the_minimum_is_what_the_contents_need_and_it_comes_back(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    for factor, larger in ((2.0, True), (1.0, False)):
        window.try_interface_scale(factor)
        qtbot.wait(50)
        widths = set()
        for section in (CALENDAR, MAIL, CONTACTS, TASKS):
            window.switch_section(section)
            window.resize(100, 700)
            qtbot.wait(30)
            widths.add(window.width())
        assert len(widths) == 1, (factor, widths)  # one minimum for every section
        assert (widths.pop() > 768) is larger, factor  # 768, or more when the contents need it (926 at 2.00)
