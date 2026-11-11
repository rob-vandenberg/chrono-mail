from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.core import CollectionKind
from chrono_mail.engine.commands_invitations import ReopenResult
from chrono_mail.ui.calendarmail import Choice
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.invitationcalendar import InvitationCalendarDialog, InvitationDelegateDialog
from chrono_mail.ui.invitationcard import is_handled, status_text
from chrono_mail.ui.invitationwindow import CalendarSource, InvitationWindow
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_calendar import calendar_id, open_calendar, summaries, yes
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_invitation_window import REQUEST_FILE, calendar_file, kept_items, opened_file
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture
from tests.ui.world import Dialogs, World, answer_calendar_mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_decline_stays.py 4.4.576"

# --- Version history ----------------------------------------------------
# v4.4.576: A declined event opens ready to change: no Edit step (the owner)
# v4.4.558: Delete of a declined series asks this occurrence or all occurrences, as every repeating event (D-318)
# v4.4.554: Initial tests: the sentence of a declined event, Change my answer leaves it in its calendar, Delegate asks a calendar, a declined event opens with a double click (D-310)

DECLINED = replace(CARD, stored_in="Calendar (Work)", version="same", your_answer="DECLINED", occurrence_kept=True)
SERIES_DECLINED = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", "BEGIN:VEVENT", "UID:declined@example.org", "DTSTAMP:20261001T000000Z", "SUMMARY:Daily",
    "ORGANIZER:mailto:olivia@example.org", "DTSTART:20261012T090000Z", "DTEND:20261012T093000Z", "RRULE:FREQ=DAILY;COUNT=2", "ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com", "END:VEVENT", "END:VCALENDAR",
]) + "\r\n"
ONE_DECLINED = SERIES_DECLINED.replace("RRULE:FREQ=DAILY;COUNT=2\r\n", "")


def test_the_mail_of_a_declined_event_says_where_it_is_hidden() -> None:
    assert status_text(DECLINED) == "You declined. It is in your Calendar (Work), marked hidden."


def test_a_task_you_declined_says_nothing_of_hidden() -> None:
    assert status_text(replace(DECLINED, kind="task")) == "You declined. This task is in your task list Calendar (Work)."


def test_a_decline_only_the_mail_remembers_names_no_calendar() -> None:
    assert status_text(replace(CARD, your_answer="DECLINED", answered_on_mail=True)) == "You declined."


def test_the_card_brings_back_the_four_answers_and_says_the_same(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    card = reader.card
    qtbot.addWidget(card)
    card.show_invitation(DECLINED)
    assert is_handled(DECLINED) and not card.change_button.isHidden() and card.accept_button.isHidden()
    card.ask_again(-1)
    assert card.change_button.isHidden() and all(not button.isHidden() for button in (card.accept_button, card.tentative_button, card.decline_button, card.delegate_button))
    assert card.status.text() == "You declined. It is in your Calendar (Work), marked hidden."
    card.show_invitation(DECLINED)  # shown anew: what the calendar holds, and that did not change
    assert not card.change_button.isHidden() and card.accept_button.isHidden()


def test_a_card_with_several_items_brings_back_the_answers_of_that_block_only(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    card = reader.card
    parts = (replace(DECLINED, item=0, title="First"), replace(DECLINED, item=1, title="Second"))
    card.show_invitation(replace(DECLINED, parts=parts, items=2))
    card.ask_again(1)
    first, second = card.blocks
    assert not first.change_button.isHidden() and first.accept_button.isHidden()
    assert second.change_button.isHidden() and not second.accept_button.isHidden()


def _declined_in_the_window(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InvitationWindow:
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    answer_calendar_mail(monkeypatch, Choice.QUIET)
    window.card.decline_button.click()
    qtbot.waitUntil(lambda: window.card.change_button.isVisible(), timeout=5000)
    return window


def test_change_my_answer_on_a_declined_event_asks_nothing_and_it_stays(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _declined_in_the_window(world, qtbot, tmp_path, monkeypatch)
    assert kept_items(world, CollectionKind.EVENTS) == 1

    def never(box: QMessageBox) -> int:
        raise AssertionError(box.text())

    monkeypatch.setattr(QMessageBox, "exec", never)
    window.card.change_button.click()
    qtbot.waitUntil(lambda: window.card.accept_button.isVisible(), timeout=5000)
    assert kept_items(world, CollectionKind.EVENTS) == 1 and not window.card.change_button.isVisible()
    assert window.card.status.text().startswith("You declined. It is in your ")
    window.close()


def test_delegate_on_a_declined_event_in_local_folders_asks_a_calendar_first(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _declined_in_the_window(world, qtbot, tmp_path, monkeypatch)
    window.card.change_button.click()
    qtbot.waitUntil(lambda: window.card.delegate_button.isVisible(), timeout=5000)
    order: list[str] = []

    def chosen(dialog: InvitationCalendarDialog) -> int:
        order.append("calendar")
        next(iter(dialog.radios.values())).setChecked(True)
        return dialog.DialogCode.Accepted

    def stopped(dialog: InvitationDelegateDialog) -> int:
        order.append("delegate")
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(InvitationCalendarDialog, "exec", chosen)
    monkeypatch.setattr(InvitationDelegateDialog, "exec", stopped)
    window.card.delegate_button.click()
    qtbot.waitUntil(lambda: order == ["calendar", "delegate"], timeout=5000)
    assert kept_items(world, CollectionKind.EVENTS) == 1  # closed: nothing changed
    window.close()


def test_delegate_of_an_invitation_with_no_copy_asks_no_calendar(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    order: list[str] = []

    def never(dialog: InvitationCalendarDialog) -> int:
        raise AssertionError(dialog.windowTitle())

    def stopped(dialog: InvitationDelegateDialog) -> int:
        order.append("delegate")
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(InvitationCalendarDialog, "exec", never)
    monkeypatch.setattr(InvitationDelegateDialog, "exec", stopped)
    window.card.delegate_button.click()
    qtbot.waitUntil(lambda: order == ["delegate"], timeout=5000)
    window.close()


def _declined_row(world: World, qtbot: QtBot, text: str) -> CalendarPage:
    page = open_calendar(world, qtbot)
    world.call("event.import", calendar=calendar_id(world), text=text)
    page.hidden_button.setChecked(True)
    wait(qtbot, lambda: any(entry.row.hidden for entry in page.entries))
    return page


def test_a_double_click_opens_a_declined_event_ready_to_change(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _declined_row(world, qtbot, ONE_DECLINED)
    seen: list[tuple[bool, bool]] = []

    def opened(editor: EventEditor) -> int:
        seen.append((editor.save_button.isHidden(), editor.summary.isReadOnly()))
        return 0

    monkeypatch.setattr(EventEditor, "exec", opened)
    page._entry_activated(next(entry for entry in page.entries if entry.row.hidden))  # noqa: SLF001
    wait(qtbot, lambda: len(seen) == 1)
    assert seen == [(False, False)]


def test_a_click_selects_a_declined_event_and_the_toolbar_works_on_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, dialogs: Dialogs) -> None:
    page = _declined_row(world, qtbot, ONE_DECLINED)
    page._entry_clicked(next(entry for entry in page.entries if entry.row.hidden))  # noqa: SLF001
    assert page.selected is not None and page.act_edit.isEnabled() and page.act_delete.isEnabled()
    asked: list[str] = []

    def question(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return yes(_parent, _title, text)

    monkeypatch.setattr(QMessageBox, "question", question)
    page.delete_selected()
    wait(qtbot, lambda: not any(entry.row.hidden for entry in page.entries))
    assert len(asked) == 1 and "Daily" not in summaries(page)


def test_delete_from_the_toolbar_asks_one_question_for_a_declined_series(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, dialogs: Dialogs) -> None:
    page = _declined_row(world, qtbot, SERIES_DECLINED)
    page._entry_clicked(next(entry for entry in page.entries if entry.row.hidden))  # noqa: SLF001
    asked: list[str] = []

    def question(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return yes(_parent, _title, text)

    def which(verb: str) -> str:
        asked.append(verb)
        return "series"

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(page, "_ask_scope", which)
    page.delete_selected()
    wait(qtbot, lambda: not any(entry.row.hidden for entry in page.entries))
    assert asked == ["Delete"]  # the question of this occurrence or all occurrences is the one question


def test_change_my_answer_leaves_the_card_of_another_message_chosen_meanwhile(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    pane = world.window.reading
    pane.reader.card.show_invitation(DECLINED)
    pane.shown = ("account", "Inbox", "another")
    answers: list[Callable[[ReopenResult], None]] = []

    def asked(*arguments: object, **_: object) -> None:
        answers.append(cast(Callable[[ReopenResult], None], arguments[2]))

    monkeypatch.setattr(pane._backend, "ask", asked)  # noqa: SLF001
    pane.change_answer(pane.reader, CalendarSource("account", "Inbox", "first"))
    answers[0](ReopenResult(False, True))
    assert not pane.reader.card.change_button.isHidden() and pane.reader.card.accept_button.isHidden()
