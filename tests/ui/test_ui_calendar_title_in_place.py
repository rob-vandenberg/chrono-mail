import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import EntryHits, TitleEditor
from chrono_mail.ui.eventeditor import EventEditor

from tests.ui.test_ui_calendar import ANCHOR, calendar_id, cancelled, open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_title_in_place.py 4.4.566"

# --- Version history ----------------------------------------------------
# v4.4.566: Initial: an event's title edited in place by a click on the selected event or F2 (D-324)


def shown(world: World, qtbot: QtBot, view: str = layout.WEEK) -> CalendarPage:
    page = open_calendar(world, qtbot, view)
    world.window.show()
    world.window.activateWindow()
    if view == layout.MONTH:
        page.month.resize(900, 600)
        page.month.grab()
    else:
        page.grid.resize(900, 600)
        page.grid.canvas.grab()
        page.grid.header.grab()
    return page


def place(widget: EntryHits, summary: str, start_day: int | None = None) -> tuple[QRectF, Entry]:
    return next((rect, entry) for rect, entry in widget.hits if entry.summary == summary and (start_day is None or entry.start.day == start_day))


def click(qtbot: QtBot, widget: EntryHits, rect: QRectF) -> None:
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(int(rect.center().x()), int(rect.center().y())))


def editing(qtbot: QtBot, widget: EntryHits) -> TitleEditor:
    wait(qtbot, lambda: widget.title_editor is not None and widget.title_editor.isVisible())
    editor = widget.title_editor
    assert editor is not None
    return editor


def title_of(world: World, item: str) -> str:
    return str(world.call("event.show", item=item).summary)


def settled(qtbot: QtBot, world: World) -> None:
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


def test_a_click_on_the_selected_event_edits_its_title_where_it_is_drawn_and_enter_saves_it(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    assert canvas.title_editor is None  # the first click only selects
    click(qtbot, canvas, rect)
    editor = editing(qtbot, canvas)
    assert editor.parent() is canvas and editor.text() == "Lunch" and editor.selectedText() == "Lunch"
    block, _ = place(canvas, "Lunch")  # where it is drawn now
    assert block.contains(QRectF(editor.geometry()).adjusted(2, 0, 0, 0)) and editor.font().bold()  # on the block, the title's own bold font
    assert canvas.title_shown(entry) == ""  # the drawn title gives way to the edit
    QTest.keyClicks(editor, "Team lunch")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert canvas.title_editor is None and QApplication.focusWidget() is canvas  # the keyboard comes back to the view
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch")
    wait(qtbot, lambda: any(one.summary == "Team lunch" for one in page.entries))
    assert not any(one.summary == "Lunch" for one in page.entries)


def test_a_double_click_on_the_selected_event_opens_it_and_edits_no_title(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, _ = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    opened: list[str] = []

    def fake_exec(editor: EventEditor) -> int:
        opened.append(editor.summary.text())
        return 0

    monkeypatch.setattr(EventEditor, "exec", fake_exec)
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(int(rect.center().x()), int(rect.center().y())))
    wait(qtbot, lambda: opened == ["Lunch"])
    assert not canvas._title_click.isActive() and canvas.title_editor is None  # noqa: SLF001 - the double click stopped the wait for a single click


def test_escape_and_an_empty_title_keep_the_old_title(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    click(qtbot, canvas, rect)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Something else")
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert canvas.title_editor is None and canvas.title_shown(entry) == "Lunch"
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClick(editor, Qt.Key.Key_Delete)  # the whole title was selected
    QTest.keyClick(editor, Qt.Key.Key_Return)
    settled(qtbot, world)
    assert canvas.title_editor is None and title_of(world, entry.row.item) == "Lunch"


def test_f2_edits_the_title_of_the_selected_event_and_a_click_elsewhere_saves_it(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    header = page.grid.header
    rect, entry = place(header, "Holiday")
    click(qtbot, header, rect)
    QTest.keyClick(header, Qt.Key.Key_F2)
    editor = editing(qtbot, header)  # at once, no wait for a double click
    assert editor.text() == "Holiday"
    QTest.keyClicks(editor, "Autumn holiday")
    page.grid.canvas.setFocus(Qt.FocusReason.MouseFocusReason)  # what a click elsewhere does first
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Autumn holiday")
    assert header.title_editor is None


def test_a_repeating_event_changes_only_the_date_edited(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Standup", 13)
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)  # no question: this date only
    QTest.keyClicks(editor, "Planning")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    wait(qtbot, lambda: [one.start.day for one in page.entries if one.summary == "Planning"] == [13])
    assert sorted(one.start.day for one in page.entries if one.summary == "Standup") == [12, 14, 15, 16]
    assert entry.row.repeats


def test_a_read_only_cancelled_or_declined_event_cannot_be_edited_in_place(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    cancelled(page, world, qtbot, "Dentist")
    canvas.grab()
    rect, _ = place(canvas, "Dentist")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    assert canvas.title_editor is None
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(calendar_id(world), read_only=True)
    page.reload()
    wait(qtbot, lambda: any(one.summary == "Lunch" and one.row.read_only for one in page.entries))
    canvas.grab()
    rect, _ = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    assert canvas.title_editor is None


def test_a_refusal_of_the_server_says_so_and_the_title_stays(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    said: list[str] = []
    page.status = said.append
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(calendar_id(world), read_only=True)  # the calendar took no more changes meanwhile
    QTest.keyClicks(editor, "Team lunch")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    wait(qtbot, lambda: any("read-only" in text for text in said))
    assert title_of(world, entry.row.item) == "Lunch"


def test_your_meeting_gets_its_new_title_and_chrono_mail_mails_no_one(world: World, qtbot: QtBot) -> None:
    world.call("pim.enable")
    world.call("event.add", calendar=calendar_id(world), summary="Budget", start=f"{ANCHOR.isoformat()}T15:00", end=f"{ANCHOR.isoformat()}T16:00", time_zone="UTC",
               attendee=("sam@example.org",))
    page = shown(world, qtbot)
    mails: list[str] = []

    def mailed(account: str, folder: str, message: str, note: str) -> None:
        mails.append(message)

    page.mail_ready.connect(mailed)
    canvas = page.grid.canvas
    wait(qtbot, lambda: any(entry.summary == "Budget" for entry in page.entries))
    canvas.grab()
    rect, entry = place(canvas, "Budget")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Budget review")
    QTest.keyClick(editor, Qt.Key.Key_Return)  # no question about a mail: a message box nobody answers fails the test
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Budget review")
    settled(qtbot, world)
    assert mails == []


def test_in_the_month_only_the_title_after_the_time_is_edited(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot, layout.MONTH)
    month = page.month
    rect, entry = place(month, "Lunch")
    click(qtbot, month, rect)
    QTest.keyClick(month, Qt.Key.Key_F2)
    editor = editing(qtbot, month)
    assert editor.text() == "Lunch"
    selected_text = month.palette().color(QPalette.ColorRole.HighlightedText)
    wait(qtbot, lambda: editor.palette().color(QPalette.ColorRole.Text) == selected_text)  # the colours of the line as it is drawn selected
    time_width = QFontMetrics(editor.font()).horizontalAdvance(f"{entry.start.strftime('%H:%M')} ")
    assert editor.geometry().left() + 2 >= int(rect.left()) + time_width  # the time stays drawn before it
    QTest.keyClicks(editor, "Team lunch")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch")


def test_going_to_another_week_while_editing_saves_what_was_typed(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Team lunch")
    page.next_button.click()  # a toolbar button takes no keyboard: the event is simply no longer drawn
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch")
    assert canvas.title_editor is None


def test_a_change_of_the_same_event_arriving_while_editing_keeps_the_edit_open(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Team lu")
    world.call("event.edit", item=entry.row.item, location="Canteen")  # a sync brings a change of the same event
    wait(qtbot, lambda: any(one.row.item == entry.row.item and one.row.location == "Canteen" for one in page.entries))
    settled(qtbot, world)
    canvas.grab()
    assert canvas.title_editor is editor and editor.text() == "Team lu" and title_of(world, entry.row.item) == "Lunch"
    QTest.keyClicks(editor, "nch")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch")


def test_f2_while_editing_keeps_what_was_typed(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, _ = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Team")
    QTest.keyClick(editor, Qt.Key.Key_F2)
    assert canvas.title_editor is editor and editor.text() == "Team"


def test_a_click_on_its_own_block_outside_the_edit_saves_and_does_not_edit_again(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    rect, entry = place(canvas, "Lunch")
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, "Team lunch")
    block, _ = place(canvas, "Lunch")
    below = QPoint(int(block.center().x()), int(block.bottom()) - 4)  # the time line of the block, under the edit
    assert not editor.geometry().contains(below)
    canvas.setFocus(Qt.FocusReason.MouseFocusReason)  # what Qt does first at a press on a widget that takes the keyboard
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=below)
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=below)
    assert canvas.title_editor is None and not canvas._title_click.isActive()  # noqa: SLF001 - no new edit is on its way
    wait(qtbot, lambda: title_of(world, entry.row.item) == "Team lunch")
