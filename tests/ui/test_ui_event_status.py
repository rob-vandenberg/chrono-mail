from dataclasses import replace

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_calendar import StatusParameters
from chrono_mail.ui import calendarview
from chrono_mail.ui.calendarmail import Choice
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_calendar import calendar_id, open_calendar, summaries
from tests.ui.test_ui_decline_stays import ONE_DECLINED
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import reader  # noqa: F401 - reader is a fixture
from tests.ui.world import Dialogs, World, answer_calendar_mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_event_status.py 4.4.558"

# --- Version history ----------------------------------------------------
# v4.4.558: Edit is off the event menu (D-318)
# v4.4.557: Initial tests: Cancel and Restore on the right-click menu, what greys on a Cancelled event, the mail question, the card (D-310)


class _LookedAt(QMenu):
    """The menu a right click makes, kept to look at instead of waiting for a click."""

    shown: list[QMenu] = []

    def exec(self, *_: object) -> None:  # type: ignore[override]
        _LookedAt.shown.append(self)


def menu_of(page: CalendarPage, entry: Entry, monkeypatch: pytest.MonkeyPatch) -> dict[str, QAction]:
    monkeypatch.setattr(calendarview, "QMenu", _LookedAt)
    page._context(entry, None, page.content.pos())  # noqa: SLF001
    return {action.text(): action for action in _LookedAt.shown[-1].actions() if action.text()}


def entry_of(page: CalendarPage, summary: str) -> Entry:
    return next(entry for entry in page.entries if entry.summary == summary)


def cancelled_in_view(page: CalendarPage, qtbot: QtBot, summary: str) -> Entry:
    wait(qtbot, lambda: any(entry.summary == summary and entry.row.cancelled for entry in page.entries))
    return entry_of(page, summary)


def meeting(page: CalendarPage, world: World, qtbot: QtBot, status: str | None = None) -> Entry:
    world.call("event.add", calendar=calendar_id(world), summary="Budget", start="2026-10-14T15:00", end="2026-10-14T16:00", time_zone="UTC", attendee=("sam@example.org",), status=status, tell=False)
    wait(qtbot, lambda: "Budget" in summaries(page))
    return entry_of(page, "Budget")


def mails_of(page: CalendarPage) -> list[str]:
    opened: list[str] = []

    def mail_ready(_account: str, _folder: str, message: str, note: str) -> None:
        opened.append(f"{message} {note}")

    page.mail_ready.connect(mail_ready)
    return opened


def commands_sent(page: CalendarPage, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The verbs given to the engine, at the moment they are given: what was not sent can then be proven at once."""
    sent: list[str] = []
    original = page._status  # noqa: SLF001

    def recorded(verb: str, values: StatusParameters, note: str) -> None:
        sent.append(verb)
        original(verb, values, note)

    monkeypatch.setattr(page, "_status", recorded)
    return sent


def test_the_menu_of_an_event_offers_cancel_and_greys_restore(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    actions = menu_of(page, entry_of(page, "Lunch"), monkeypatch)
    assert actions["Cancel"].isEnabled() and not actions["Restore"].isEnabled() and actions["Delete"].isEnabled()


def test_cancel_from_the_menu_cancels_and_restore_brings_it_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    menu_of(page, entry_of(page, "Lunch"), monkeypatch)["Cancel"].trigger()
    entry = cancelled_in_view(page, qtbot, "Lunch")
    actions = menu_of(page, entry, monkeypatch)
    assert not actions["Cancel"].isEnabled() and actions["Restore"].isEnabled() and actions["Delete"].isEnabled()
    actions["Restore"].trigger()
    wait(qtbot, lambda: any(entry.summary == "Lunch" and not entry.row.cancelled for entry in page.entries))  # (its blocks go first and come back)


@pytest.mark.parametrize(("scope", "greys"), [("occurrence", 1), ("series", 5)], ids=["this-occurrence", "all-occurrences"])
def test_a_repeating_event_asks_which_and_does_that(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, scope: str, greys: int) -> None:
    page = open_calendar(world, qtbot)
    asked: list[str] = []

    def chosen(verb: str) -> str:
        asked.append(verb)
        return scope

    monkeypatch.setattr(page, "_ask_scope", chosen)
    standup = next(entry for entry in page.entries if entry.summary == "Standup" and entry.row.start.startswith("2026-10-14"))
    menu_of(page, standup, monkeypatch)["Cancel"].trigger()
    wait(qtbot, lambda: len([entry for entry in page.entries if entry.summary == "Standup" and entry.row.cancelled]) == greys)
    assert asked == ["Cancel"]


def test_closing_the_question_of_a_repeating_event_changes_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    def closed(_verb: str) -> str:
        return ""

    monkeypatch.setattr(page, "_ask_scope", closed)
    asked: list[object] = []

    def recorded(*values: object, **_: object) -> None:
        asked.append(values)

    actions = menu_of(page, entry_of(page, "Standup"), monkeypatch)
    monkeypatch.setattr(page._backend, "ask", recorded)  # noqa: SLF001
    actions["Cancel"].trigger()
    assert asked == []  # the engine is not even asked: nothing can still come after this line


def test_the_question_to_cancel_has_no_button_called_cancel(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    seen: list[list[str]] = []

    def looked_at(box: QMessageBox) -> int:
        seen.append([button.text().replace("&", "") for button in box.buttons()])
        return 0

    monkeypatch.setattr(QMessageBox, "exec", looked_at)
    assert page._ask_scope("Cancel") == ""  # noqa: SLF001
    assert "This occurrence" in seen[0] and "All occurrences" in seen[0] and "Cancel" not in seen[0]


def test_a_cancelled_event_does_not_open_and_its_toolbar_edit_is_greyed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    world.call("event.cancel", item=entry_of(page, "Lunch").row.item)
    entry = cancelled_in_view(page, qtbot, "Lunch")
    opened: list[object] = []

    def editor(*values: object, **_: object) -> None:
        opened.append(values)

    monkeypatch.setattr(page, "_editor", editor)
    page._entry_activated(entry)  # noqa: SLF001
    assert page.selected is not None and not page.act_edit.isEnabled() and page.act_delete.isEnabled()
    page.edit_selected()
    assert opened == []


def test_cancel_of_your_meeting_asks_about_the_mail_and_send_makes_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = meeting(page, world, qtbot)
    asked = answer_calendar_mail(monkeypatch, Choice.SEND, "Sorry")
    opened = mails_of(page)
    menu_of(page, entry, monkeypatch)["Cancel"].trigger()
    wait(qtbot, lambda: bool(opened))
    assert len(asked) == 1 and asked[0].windowTitle() == "Cancel" and opened[0].endswith(" Sorry")
    cancelled_in_view(page, qtbot, "Budget")


def test_dont_send_cancels_with_no_mail_and_closing_keeps_the_meeting(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = meeting(page, world, qtbot)
    opened = mails_of(page)
    asked = answer_calendar_mail(monkeypatch, Choice.BACK)
    sent = commands_sent(page, monkeypatch)
    menu_of(page, entry, monkeypatch)["Cancel"].trigger()
    wait(qtbot, lambda: bool(asked))
    assert sent == []  # (the answer to the question and what follows it happen in one go)
    answer_calendar_mail(monkeypatch, Choice.QUIET)
    menu_of(page, entry_of(page, "Budget"), monkeypatch)["Cancel"].trigger()
    cancelled_in_view(page, qtbot, "Budget")
    assert sent == ["Cancel"] and opened == []


def test_restore_of_your_meeting_asks_about_the_mail(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = open_calendar(world, qtbot)
    entry = meeting(page, world, qtbot, "cancelled")
    asked = answer_calendar_mail(monkeypatch, Choice.SEND)
    opened = mails_of(page)
    menu_of(page, entry, monkeypatch)["Restore"].trigger()
    wait(qtbot, lambda: bool(opened) and any(entry.summary == "Budget" and not entry.row.cancelled for entry in page.entries))
    assert asked[0].windowTitle() == "Restore"


def test_a_hidden_cancelled_event_has_its_answers_greyed_and_can_be_restored(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, dialogs: Dialogs) -> None:
    page = open_calendar(world, qtbot)
    text = ONE_DECLINED.replace("DTSTART:20261012T090000Z", "DTSTART:20261014T090000Z").replace("DTEND:20261012T093000Z", "DTEND:20261014T093000Z").replace("SUMMARY:Daily", "SUMMARY:Hidden\r\nSTATUS:CANCELLED")
    world.call("event.import", calendar=calendar_id(world), text=text)
    page.hidden_button.setChecked(True)
    entry = cancelled_in_view(page, qtbot, "Hidden")
    actions = menu_of(page, entry, monkeypatch)
    assert not actions["Accept"].isEnabled() and not actions["Tentative"].isEnabled() and not actions["Delegate…"].isEnabled()
    assert not actions["Cancel"].isEnabled() and actions["Restore"].isEnabled() and actions["Delete"].isEnabled()
    actions["Restore"].trigger()
    wait(qtbot, lambda: any(entry.summary == "Hidden" and not entry.row.cancelled for entry in page.entries))
    assert not menu_of(page, entry_of(page, "Hidden"), monkeypatch)["Cancel"].isEnabled()  # hidden: Cancel stays greyed


def test_the_card_greys_the_answers_of_a_cancelled_event_until_a_newer_version(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    card = reader.card
    qtbot.addWidget(card)
    kept = replace(CARD, stored_in="Calendar (Work)", version="same", your_answer="NEEDS-ACTION", occurrence_kept=True, kept_cancelled=True)
    card.show_invitation(kept)
    assert not card.accept_button.isEnabled() and not card.decline_button.isEnabled() and not card.delegate_button.isEnabled()
    card.show_invitation(replace(kept, version="newer"))
    assert card.accept_button.isEnabled()
