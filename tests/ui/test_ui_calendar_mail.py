from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from chrono_mail.core import CollectionKind
from chrono_mail.ui.calendarmail import MISSING_ORGANIZER, CalendarMailDialog, Choice
from chrono_mail.ui.eventeditor import EventEditor
from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.test_ui_invitation_window import REQUEST_FILE, calendar_file, first_choice, kept_items, opened_file
from tests.ui.world import World, answer_calendar_mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_mail.py 4.4.532"

# --- Version history ----------------------------------------------------
# v4.4.532: Initial tests: the one dialog of every calendar mail (D-310)


def sends(world: World, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What goes out: the person's own message of each mail."""
    gone: list[str] = []

    def went(_account: str, _folder: str, _message: str, note: str = "", _sent: object = None) -> None:
        gone.append(note)

    monkeypatch.setattr(world.window.composing, "send_calendar_mail", went)
    return gone


def test_the_dialog_closed_with_esc_takes_the_action_back(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Send your answer", "Tell the organizer that you accept?", "Why")
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.choice is Choice.BACK and not dialog.isVisible()


def test_send_is_greyed_until_the_dialog_has_an_address(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Missing organizer", MISSING_ORGANIZER, "Why", address="")
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.send_button.isEnabled() and dialog.quiet_button.isEnabled()
    dialog.to_field.setText("sam@example.org,kim@example.org")
    assert not dialog.send_button.isEnabled()
    dialog.to_field.setText("sam@example.org")
    assert dialog.send_button.isEnabled()
    dialog.send_button.click()
    assert dialog.choice is Choice.SEND and dialog.address == "sam@example.org"


def test_closing_the_question_leaves_the_invitation_unhandled(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    first_choice(monkeypatch)
    asked = answer_calendar_mail(monkeypatch, Choice.BACK)
    window.card.accept_button.click()
    wait(qtbot, lambda: bool(asked))
    qtbot.wait(200)
    assert kept_items(world, CollectionKind.EVENTS) == 0 and window.card.accept_button.isEnabled()
    window.close()


def test_send_answers_and_mails_with_your_message_without_the_composer(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = sends(world, monkeypatch)
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    first_choice(monkeypatch)
    answer_calendar_mail(monkeypatch, Choice.SEND, "See you there")
    window.card.accept_button.click()
    wait(qtbot, lambda: gone == ["See you there"])
    assert kept_items(world, CollectionKind.EVENTS) == 1
    window.close()


def test_a_file_without_an_organizer_asks_an_address_and_starts_empty(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = sends(world, monkeypatch)
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE.replace("ORGANIZER:mailto:olivia@example.org\r\n", "")))
    first_choice(monkeypatch)
    asked = answer_calendar_mail(monkeypatch, Choice.QUIET)
    window.card.accept_button.click()
    wait(qtbot, lambda: kept_items(world, CollectionKind.EVENTS) == 1)
    assert not asked[0].to_field.isHidden() and asked[0].address == "" and not gone
    window.close()


def editor_with_attendee(world: World, qtbot: QtBot) -> EventEditor:
    page = open_calendar(world, qtbot)
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    editor.summary.setText("Budget")
    editor.attendees.setText("sam@example.org")
    return editor


def test_closing_the_question_goes_back_to_the_editor_with_nothing_saved(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    editor = editor_with_attendee(world, qtbot)
    editor.show()
    before = kept_items(world, CollectionKind.EVENTS)
    asked = answer_calendar_mail(monkeypatch, Choice.BACK)
    button: QPushButton = editor.save_button
    button.click()
    assert not button.isEnabled()
    wait(qtbot, lambda: bool(asked))
    qtbot.wait(200)
    assert editor.isVisible() and editor.summary.text() == "Budget" and button.isEnabled()
    assert kept_items(world, CollectionKind.EVENTS) == before
