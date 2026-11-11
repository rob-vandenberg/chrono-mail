import pytest
from PySide6.QtCore import QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem
from pytestqt.qtbot import QtBot

from collections.abc import Callable
from typing import cast

from chrono_mail.core import LOCAL_ACCOUNT_ID, ChronoMailError
from chrono_mail.engine.commands_calendar import EVENT_EDIT, EVENT_LIST, EditParameters, EventInfo
from chrono_mail.engine.commands_calendar import ListParameters as EventListParameters
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import EntryHits, TitleEditor
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.syncmarks import WAITING

from tests.ui.test_ui_calendar import ANCHOR, calendar_id, cancelled, fake_editor, open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_title_in_place.py 4.4.567"

# --- Version history ----------------------------------------------------
# v4.4.567: The event stays selected after a change, its block never leaves meanwhile, a title typed in place shows with the clock until the server answers (D-325)
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



class HeldEdit:
    """event.edit held back, as a server that has not answered yet: the test answers it (D-325)."""

    def __init__(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        self.world = world
        self.asked: list[tuple[EditParameters, Callable[[EventInfo], None], Failure]] = []
        original = world.backend.ask

        def ask[P, R](command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
                      in_order: bool = False, waiting: bool = False, checking: bool = False) -> int:
            if command is EVENT_EDIT and isinstance(parameters, EditParameters) and on_error is not None:
                self.asked.append((parameters, cast(Callable[[EventInfo], None], on_success), on_error))  # the answer of event.edit is an EventInfo
                return 0
            return original(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

        monkeypatch.setattr(world.backend, "ask", ask)

    def server_says_yes(self) -> None:
        parameters, on_success, _ = self.asked.pop(0)
        info = self.world.call("event.edit", item=parameters.item, occurrence=parameters.occurrence, summary=parameters.summary)  # the server takes it
        assert isinstance(info, EventInfo)
        on_success(info)

    def server_says_no(self, reason: str) -> None:
        _, _, on_error = self.asked.pop(0)
        on_error(ChronoMailError(reason))


def edited_in_place(qtbot: QtBot, page: CalendarPage, summary: str, title: str) -> Entry:
    canvas = page.grid.canvas
    rect, entry = place(canvas, summary)
    click(qtbot, canvas, rect)
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    editor = editing(qtbot, canvas)
    QTest.keyClicks(editor, title)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    return entry


def test_after_a_title_saved_in_place_the_appointment_stays_selected_and_a_click_edits_it_again(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    wait(qtbot, lambda: any(one.row.summary == "Team lunch" for one in page.entries))
    settled(qtbot, world)
    assert page.selected is not None and page.selected.row.summary == "Team lunch" and page.act_edit.isEnabled() and page.act_delete.isEnabled()
    assert canvas.selected is not None and canvas.selected.row.item == entry.row.item  # its outline
    canvas.grab()
    rect, _ = place(canvas, "Team lunch")
    click(qtbot, canvas, rect)  # selected already: a click edits it again
    assert editing(qtbot, canvas).text() == "Team lunch"


def test_after_a_save_in_the_appointment_window_it_stays_selected_also_at_another_time(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page._entry_clicked(lunch)  # noqa: SLF001

    def change(editor: EventEditor) -> None:
        editor.summary.setText("Long lunch")
        editor.set_start(editor.start_moment().addSecs(3600))

    monkeypatch.setattr(EventEditor, "exec", fake_editor(change))
    page.edit_selected()
    wait(qtbot, lambda: any(one.summary == "Long lunch" for one in page.entries))
    settled(qtbot, world)
    assert page.selected is not None and page.selected.summary == "Long lunch" and page.selected.start.hour == lunch.start.hour + 1
    assert page.grid.canvas.selected is not None and page.grid.canvas.selected.summary == "Long lunch"


def test_a_change_from_the_server_keeps_it_selected_and_its_block_never_leaves_the_screen(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page._entry_clicked(lunch)  # noqa: SLF001
    shown_with_it: list[bool] = []
    show = page._show_data  # noqa: SLF001

    def watched(keep: bool = False) -> None:
        shown_with_it.append(any(one.row.item == lunch.row.item for one in page.entries))
        show(keep)

    monkeypatch.setattr(page, "_show_data", watched)
    world.call("event.edit", item=lunch.row.item, summary="Lunch with Sam")
    wait(qtbot, lambda: any(one.summary == "Lunch with Sam" for one in page.entries))
    settled(qtbot, world)
    assert shown_with_it and all(shown_with_it)  # no moment without its block
    assert page.selected is not None and page.selected.summary == "Lunch with Sam"


def test_the_agenda_keeps_the_selected_appointment_after_a_change(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.AGENDA)
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if (page.agenda.item(row).data(Qt.ItemDataRole.UserRole) or None) is not None
                and getattr(page.agenda.item(row).data(Qt.ItemDataRole.UserRole), "summary", "") == "Lunch")
    page.agenda.setCurrentItem(line)
    chosen = page.agenda.selected_entry()
    assert chosen is not None
    world.call("event.edit", item=chosen.row.item, location="Canteen")
    wait(qtbot, lambda: any(one.row.location == "Canteen" for one in page.entries))
    settled(qtbot, world)
    now = page.agenda.selected_entry()
    assert now is not None and now.row.item == chosen.row.item and now.row.location == "Canteen"


def test_a_deleted_appointment_is_no_longer_selected(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page._entry_clicked(lunch)  # noqa: SLF001
    world.call("event.delete", item=lunch.row.item)
    wait(qtbot, lambda: not any(one.row.item == lunch.row.item for one in page.entries))
    assert page.selected is None and page.grid.canvas.selected is None


def test_the_new_title_shows_at_once_with_the_clock_until_the_server_says_yes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    assert len(held.asked) == 1 and held.asked[0][0].summary == "Team lunch"
    assert canvas.title_shown(entry) == "Team lunch" and canvas.mark_for(entry) == WAITING  # at once, with the clock
    assert page.selected is not None and page.selected.row.item == entry.row.item
    QTest.keyClick(canvas, Qt.Key.Key_F2)  # no new edit while the server has not answered
    assert canvas.title_editor is None
    held.server_says_yes()
    wait(qtbot, lambda: canvas.mark_for(entry) == "")
    assert canvas.title_shown(entry) == "Team lunch"  # the clock goes, the title stays
    wait(qtbot, lambda: any(one.row.summary == "Team lunch" for one in page.entries))
    settled(qtbot, world)
    new = next(one for one in page.entries if one.row.item == entry.row.item)
    assert canvas.title_shown(new) == "Team lunch" and page._pending == {}  # noqa: SLF001 - its own title now
    QTest.keyClick(canvas, Qt.Key.Key_F2)
    assert editing(qtbot, canvas).text() == "Team lunch"


def test_a_refusal_of_the_server_brings_the_old_title_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    said: list[str] = []
    page.status = said.append
    canvas = page.grid.canvas
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    assert canvas.mark_for(entry) == WAITING
    held.server_says_no("The server did not take this change")
    assert canvas.title_shown(entry) == "Lunch" and canvas.mark_for(entry) == "" and any("did not take" in text for text in said)
    assert title_of(world, entry.row.item) == "Lunch"


def test_a_new_time_for_a_whole_series_keeps_its_date_selected(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    tuesday = next(one for one in page.entries if one.summary == "Standup" and one.start.day == 13)
    page._entry_clicked(tuesday)  # noqa: SLF001
    world.call("event.edit", item=tuesday.row.item, start="2026-10-12T10:00", end="2026-10-12T10:15", time_zone="UTC")  # every date an hour later
    wait(qtbot, lambda: not any(one.row.item == tuesday.row.item and one.row.occurrence == tuesday.row.occurrence for one in page.entries))
    settled(qtbot, world)
    assert page.selected is not None and page.selected.row.item == tuesday.row.item and page.selected.start.day == 13
    assert page.grid.canvas.selected is page.selected  # its outline


def test_a_fetch_asked_before_the_title_was_sent_does_not_end_the_new_title(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    before = page._fetches  # noqa: SLF001 - a reload asked before Enter
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    page._fetched(None, before)  # noqa: SLF001 - its answer, the event as it was, comes while the title waits
    held.server_says_yes()
    assert canvas.title_shown(entry) == "Team lunch"  # never the old title in between
    wait(qtbot, lambda: any(one.row.summary == "Team lunch" for one in page.entries))


def test_deleting_one_date_of_a_series_never_selects_its_other_date(world: World, qtbot: QtBot) -> None:
    world.call("pim.enable")
    world.call("event.add", calendar=calendar_id(world), summary="Pair", start="2026-10-12T15:00", end="2026-10-12T16:00", time_zone="UTC", rrule="FREQ=DAILY;COUNT=2")
    page = shown(world, qtbot)
    wait(qtbot, lambda: [one.start.day for one in page.entries if one.summary == "Pair"] == [12, 13])
    first = next(one for one in page.entries if one.summary == "Pair" and one.start.day == 12)
    page._entry_clicked(first)  # noqa: SLF001
    world.call("event.delete", item=first.row.item, occurrence=first.row.occurrence)
    wait(qtbot, lambda: [one.start.day for one in page.entries if one.summary == "Pair"] == [13])
    settled(qtbot, world)
    assert page.selected is None and page.grid.canvas.selected is None  # the other date is not the one that was selected


def test_a_reload_asked_before_the_server_said_yes_does_not_bring_the_old_title_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    canvas = page.grid.canvas
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    page._fetches += 1  # noqa: SLF001 - another week, a search: a reload asked while the title waits
    page._fetched(None, page._fetches)  # noqa: SLF001 - its answer, read before the server took the title
    held.server_says_yes()
    assert canvas.title_shown(entry) == "Team lunch"  # never the old title in between
    wait(qtbot, lambda: any(one.row.summary == "Team lunch" for one in page.entries))
    settled(qtbot, world)
    assert page._pending == {}  # noqa: SLF001 - the event's own fetch after the change ended it


def test_the_appointment_window_does_not_open_while_the_title_waits_and_the_agenda_shows_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    opened: list[str] = []

    def open_window(editor: EventEditor) -> int:
        opened.append(editor.summary.text())
        return 0

    monkeypatch.setattr(EventEditor, "exec", open_window)
    page.read_selected()  # a double click
    page.edit_selected()  # Edit
    settled(qtbot, world)
    assert opened == []  # it would show the old title
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.agenda.count() > 1)
    line = next(page.agenda.item(row) for row in range(page.agenda.count()) if getattr(page.agenda.item(row).data(Qt.ItemDataRole.UserRole), "row", None) is not None
                and page.agenda.item(row).data(Qt.ItemDataRole.UserRole).row.item == entry.row.item)
    assert "Team lunch" in line.text() and "Waiting for the server" in line.toolTip()
    held.server_says_yes()


def agenda_line_of(page: CalendarPage, item: str) -> QListWidgetItem:
    return next(page.agenda.item(row) for row in range(page.agenda.count())
                if isinstance(entry := page.agenda.item(row).data(Qt.ItemDataRole.UserRole), Entry) and entry.row.item == item)


def test_the_agenda_shows_the_old_title_again_after_a_refusal(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.agenda.count() > 1 and "Team lunch" in agenda_line_of(page, entry.row.item).text())
    held.server_says_no("The server did not take this change")
    line = agenda_line_of(page, entry.row.item)
    assert "Team lunch" not in line.text() and "Lunch" in line.text() and "Waiting for the server" not in line.toolTip()


def test_the_events_own_fetch_ends_the_new_title_also_after_a_week_change(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    held = HeldEdit(world, monkeypatch)
    edited_in_place(qtbot, page, "Lunch", "Team lunch")
    held.server_says_yes()  # the change is stored: the event's own fetch is on its way
    page._generation += 1  # noqa: SLF001 - the person went to another week meanwhile
    settled(qtbot, world)
    assert page._pending == {}  # noqa: SLF001 - it can never mask a later title from the server


def test_a_failed_fetch_of_a_changed_appointment_takes_its_old_block_away(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    original = world.backend.ask

    def ask[P, R](command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
                  in_order: bool = False, waiting: bool = False, checking: bool = False) -> int:
        if command is EVENT_LIST and isinstance(parameters, EventListParameters) and parameters.item and on_error is not None:
            on_error(ChronoMailError("The calendar could not be read"))  # the fetch of the changed event fails
            return 0
        return original(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(world.backend, "ask", ask)
    world.call("event.edit", item=lunch.row.item, location="Canteen")
    wait(qtbot, lambda: not any(one.row.item == lunch.row.item for one in page.entries))


def test_after_a_change_the_agenda_selects_the_appointment_selected_in_another_view(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page._entry_clicked(lunch)  # noqa: SLF001 - selected in the week
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.agenda.count() > 1)
    world.call("event.edit", item=lunch.row.item, location="Canteen")
    wait(qtbot, lambda: any(one.row.location == "Canteen" for one in page.entries))
    settled(qtbot, world)
    now = page.agenda.selected_entry()
    assert now is not None and now.row.item == lunch.row.item and page.selected is not None and page.selected.row.item == lunch.row.item


def test_the_agenda_keeps_its_place_to_the_pixel_after_a_change(world: World, qtbot: QtBot) -> None:
    world.call("pim.enable")
    for day in range(15, 31):
        world.call("event.add", calendar=calendar_id(world), summary=f"Visit {day}", start=f"2026-10-{day}T09:00", end=f"2026-10-{day}T10:00", time_zone="UTC")
    page = open_calendar(world, qtbot, layout.AGENDA)
    wait(qtbot, lambda: any(one.summary == "Visit 30" for one in page.entries))
    page.agenda.resize(400, 200)
    page.agenda.show()
    middle = agenda_line_of(page, next(one.row.item for one in page.entries if one.summary == "Visit 22"))
    page.agenda.scrollToItem(middle, page.agenda.ScrollHint.PositionAtTop)
    page.agenda.verticalScrollBar().setValue(page.agenda.verticalScrollBar().value() + 5)  # a wheel stops anywhere in a line
    scrolled = page.agenda.verticalScrollBar().value()
    visit = next(one for one in page.entries if one.summary == "Visit 24")
    world.call("event.edit", item=visit.row.item, location="Office")
    wait(qtbot, lambda: any(one.row.location == "Office" for one in page.entries))
    settled(qtbot, world)
    assert page.agenda.verticalScrollBar().value() == scrolled


def test_a_heading_selected_in_the_agenda_stays_selected_after_a_change(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.AGENDA)
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page.agenda.setCurrentItem(agenda_line_of(page, lunch.row.item))  # an event first: the calendar's selection
    heading = next(page.agenda.item(row) for row in range(page.agenda.count()) if page.agenda.item(row).data(Qt.ItemDataRole.UserRole) == ANCHOR)
    page.agenda.setCurrentItem(heading)  # then a day's heading
    world.call("event.edit", item=lunch.row.item, location="Canteen")
    wait(qtbot, lambda: any(one.row.location == "Canteen" for one in page.entries))
    settled(qtbot, world)
    current = page.agenda.currentItem()
    assert current is not None and current.data(Qt.ItemDataRole.UserRole) == ANCHOR


def test_a_change_of_the_sync_marks_keeps_the_clock_of_a_waiting_title_in_the_agenda(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = shown(world, qtbot)
    held = HeldEdit(world, monkeypatch)
    entry = edited_in_place(qtbot, page, "Lunch", "Team lunch")
    page.set_view(layout.AGENDA)
    wait(qtbot, lambda: page.agenda.count() > 1 and "Waiting for the server" in agenda_line_of(page, entry.row.item).toolTip())
    marks = page.marks
    assert marks is not None
    page.marks_changed(marks)  # a sync of another event changed the marks
    assert "Waiting for the server" in agenda_line_of(page, entry.row.item).toolTip()
    held.server_says_yes()


def test_an_appointment_moved_to_another_calendar_shown_is_selected_again(world: World, qtbot: QtBot) -> None:
    page = shown(world, qtbot)
    work = world.call("collection.create", kind="events", name="Work").collection
    wait(qtbot, lambda: work in page.panel.visible_ids())
    lunch = next(one for one in page.entries if one.summary == "Lunch")
    page._entry_clicked(lunch)  # noqa: SLF001
    world.call("event.move", item=lunch.row.item, calendar=work)
    wait(qtbot, lambda: any(one.summary == "Lunch" and one.row.collection == work for one in page.entries))
    settled(qtbot, world)
    assert page.selected is not None and page.selected.row.collection == work and page.selected.summary == "Lunch"
    assert page.grid.canvas.selected is page.selected and page.act_delete.isEnabled()
