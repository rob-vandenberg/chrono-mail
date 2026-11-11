from collections.abc import Callable
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QContextMenuEvent
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarmail import Choice
from chrono_mail.ui.calendarview import CalendarPage
from tests.ui.test_ui_calendar import calendar_id, open_calendar, summaries
from tests.ui.test_ui_event_status import _LookedAt, entry_of, menu_of
from tests.ui.world import Dialogs, World, answer_calendar_mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_event_menus.py 4.4.576"

# --- Version history ----------------------------------------------------
# v4.4.576: Edit… is the first item of both menus, greyed for a cancelled event (the owner, D-318)
# v4.4.558: Initial tests: the two event menus in every view, their greying, the question of this occurrence or all occurrences (D-318)

ANSWERS = ["Accept", "Tentative", "Delegate…", "Decline"]
INVITED = ["Edit…", "", "Accept", "Tentative", "Delegate…", "Decline", "", "Cancel", "Restore", "", "Delete"]
SELF_CREATED = ["Edit…", "", "Cancel", "Restore", "", "Delete"]
MOMENT = "2026-10-14T12:30:00+00:00"
ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Example//Example//EN\r
BEGIN:VEVENT\r
UID:invited@example.org\r
DTSTAMP:20261001T000000Z\r
DTSTART:20261014T150000Z\r
DTEND:20261014T153000Z\r
SUMMARY:Invited\r
ORGANIZER:mailto:olivia@example.org\r
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:me@example.com\r
END:VEVENT\r
END:VCALENDAR\r
"""


def changed(entry: Entry, **values: object) -> Entry:
    return replace(entry, row=replace(entry.row, **values))  # type: ignore[arg-type]


def texts_of(page: CalendarPage, entry: Entry | None, monkeypatch: pytest.MonkeyPatch, slot: object = None) -> list[str]:
    monkeypatch.setattr("chrono_mail.ui.calendarview.QMenu", _LookedAt)
    page._context(entry, slot, page.content.pos())  # noqa: SLF001
    return [action.text() for action in _LookedAt.shown[-1].actions()]


def enabled(actions: dict[str, QAction]) -> set[str]:
    return {text for text, action in actions.items() if action.isEnabled()}


def invited(page: CalendarPage, world: World, qtbot: QtBot) -> Entry:
    world.call("event.import", calendar=calendar_id(world), text=ICS)
    wait(qtbot, lambda: "Invited" in summaries(page))
    return entry_of(page, "Invited")


def scope_is(scope: str) -> Callable[[str], str]:
    def ask(_verb: str) -> str:
        return scope

    return ask


def delegates_of(page: CalendarPage) -> list[tuple[str, str]]:
    delegated: list[tuple[str, str]] = []

    def wanted(item: str, moment: str) -> None:
        delegated.append((item, moment))

    page.delegate_wanted.connect(wanted)
    return delegated


def answers_of(page: CalendarPage) -> list[tuple[str, str, str]]:
    asked: list[tuple[str, str, str]] = []

    def wanted(item: str, moment: str, answer: str) -> None:
        asked.append((item, moment, answer))

    page.answer_wanted.connect(wanted)
    return asked


def test_the_invited_menu_is_the_same_in_every_view_and_says_decline(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = invited(page, world, qtbot)
    assert entry.row.invited
    for view in (layout.DAY, layout.WEEK, layout.MONTH, layout.AGENDA):
        page.set_view(view)
        assert texts_of(page, entry, monkeypatch) == INVITED


def test_a_declined_invited_event_has_the_same_menu(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = invited(page, world, qtbot)
    assert texts_of(page, changed(entry, hidden=True, answer="DECLINED"), monkeypatch) == INVITED


def test_the_self_created_menu_has_edit_first_and_no_new(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    assert texts_of(page, entry_of(page, "Lunch"), monkeypatch) == SELF_CREATED


def test_an_empty_space_offers_only_new(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    assert texts_of(page, None, monkeypatch) == ["New event"]
    assert texts_of(page, None, monkeypatch, (page.anchor, 600)) == ["New event here"]


def test_a_right_click_in_the_agenda_opens_the_menu_of_that_event(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    monkeypatch.setattr("chrono_mail.ui.calendarview.QMenu", _LookedAt)
    page.set_view(layout.AGENDA)
    page.selected = None
    wait(qtbot, lambda: any(page.agenda._entry(page.agenda.item(row)) is not None for row in range(page.agenda.count())))  # noqa: SLF001
    row = next(row for row in range(page.agenda.count()) if page.agenda._entry(page.agenda.item(row)) is not None)  # noqa: SLF001
    place = page.agenda.visualItemRect(page.agenda.item(row)).center()
    page.agenda.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, place, QPoint(0, 0)))
    assert [action.text() for action in _LookedAt.shown[-1].actions()] == SELF_CREATED
    assert page.agenda.selected_entry() is page.agenda._entry(page.agenda.item(row))  # noqa: SLF001
    assert page.selected is not None and page.selected is page.agenda.selected_entry()  # the toolbar acts on it too
    heading = page.agenda.visualItemRect(page.agenda.item(0)).center()
    page.agenda.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, heading, QPoint(0, 0)))
    assert [action.text() for action in _LookedAt.shown[-1].actions()] == ["New event"]


@pytest.mark.parametrize(("answer", "greyed"), [("ACCEPTED", "Accept"), ("TENTATIVE", "Tentative"), ("DELEGATED", "Delegate…"), ("DECLINED", "Decline")])
def test_the_answer_you_gave_is_greyed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, answer: str, greyed: str) -> None:
    page = open_calendar(world, qtbot)
    actions = menu_of(page, changed(invited(page, world, qtbot), answer=answer), monkeypatch)
    assert enabled(actions) == {"Edit…", *ANSWERS, "Cancel", "Delete"} - {greyed}


def test_with_no_answer_yet_every_answer_can_be_given(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    assert enabled(menu_of(page, invited(page, world, qtbot), monkeypatch)) == {"Edit…", *ANSWERS, "Cancel", "Delete"}


def test_a_cancelled_invited_event_keeps_only_restore_and_delete(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    assert enabled(menu_of(page, changed(invited(page, world, qtbot), status="CANCELLED"), monkeypatch)) == {"Restore", "Delete"}


def test_a_hidden_event_cannot_be_cancelled(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    actions = menu_of(page, changed(invited(page, world, qtbot), hidden=True, answer="DECLINED"), monkeypatch)
    assert enabled(actions) == {"Edit…", "Accept", "Tentative", "Delegate…", "Delete"}


@pytest.mark.parametrize("summary", ["Lunch", "Invited"])
def test_everything_is_greyed_in_a_read_only_calendar_with_a_hover_text(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, summary: str) -> None:
    page = open_calendar(world, qtbot)
    invited(page, world, qtbot)
    actions = menu_of(page, changed(entry_of(page, summary), read_only=True, status="CANCELLED"), monkeypatch)
    assert enabled(actions) == set()
    assert {action.toolTip() for action in actions.values()} == {"This calendar is read-only"} and _LookedAt.shown[-1].toolTipsVisible()


@pytest.mark.parametrize(("scope", "moment"), [("occurrence", MOMENT), ("series", "")])
def test_an_answer_on_a_repeating_event_asks_which_after_the_click_with_both_choices(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, scope: str, moment: str) -> None:
    page = open_calendar(world, qtbot)
    entry = changed(invited(page, world, qtbot), repeats=True, occurrence=MOMENT)
    asked = answers_of(page)
    questions: list[str] = []

    def ask(verb: str) -> str:
        questions.append(verb)
        return scope

    monkeypatch.setattr(page, "_ask_scope", ask)
    actions = menu_of(page, entry, monkeypatch)
    assert questions == [] and asked == []  # the menu itself asks nothing
    actions["Decline"].trigger()
    actions["Accept"].trigger()
    assert questions == ["Decline", "Accept"]
    assert asked == [(entry.row.item, moment, "DECLINED"), (entry.row.item, moment, "ACCEPTED")]


def test_a_declined_date_of_a_series_asks_which_too(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = changed(invited(page, world, qtbot), repeats=True, hidden=True, answer="DECLINED", occurrence=MOMENT)
    asked = answers_of(page)
    monkeypatch.setattr(page, "_ask_scope", scope_is("series"))
    menu_of(page, entry, monkeypatch)["Tentative"].trigger()
    assert asked == [(entry.row.item, "", "TENTATIVE")]


def test_closing_the_question_does_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = changed(invited(page, world, qtbot), repeats=True, occurrence=MOMENT)
    asked = answers_of(page)
    delegated = delegates_of(page)
    monkeypatch.setattr(page, "_ask_scope", scope_is(""))
    actions = menu_of(page, entry, monkeypatch)
    actions["Accept"].trigger()
    actions["Delegate…"].trigger()
    assert asked == [] and delegated == []


def test_delegate_of_a_date_goes_with_that_date(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = changed(invited(page, world, qtbot), repeats=True, occurrence=MOMENT)
    delegated = delegates_of(page)
    monkeypatch.setattr(page, "_ask_scope", scope_is("occurrence"))
    menu_of(page, entry, monkeypatch)["Delegate…"].trigger()
    assert delegated == [(entry.row.item, MOMENT)]


def test_decline_from_the_menu_asks_for_no_calendar_also_in_a_calendar_shared_with_you(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, dialogs: Dialogs) -> None:
    """Your calendars hold no copy, so Accept shows the calendar dialog; Decline never does (a window that waits for a click fails the test)."""
    page = open_calendar(world, qtbot)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        theirs = store.create_collection(CollectionKind.EVENTS, "Olivia", "#112233").collection_id
        store.update_collection(theirs, server={"shared": "1"})
    item = world.call("event.import", calendar=theirs, text=ICS).items[0]
    answer_calendar_mail(monkeypatch, Choice.QUIET)
    world.window.reading.answer_event(item, "", "DECLINED")
    wait(qtbot, lambda: any(row.summary == "Invited" and row.collection == calendar_id(world) for row in world.call("event.list", from_date="2026-10-14", to_date="2026-10-15", hidden=True).events))
    assert dialogs.shown == []
