from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.ui.calendarmail import MISSING_ORGANIZER, CalendarMailDialog, Choice
from chrono_mail.ui.eventattendees import Person
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.invitationwindow import InvitationWindow
from chrono_mail.ui.readingpane import CalendarSource
from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.test_ui_invitation_window import REQUEST_FILE, calendar_file, first_choice, kept_items, opened_file
from tests.ui.world import World, answer_calendar_mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_mail.py 4.4.555"

# --- Version history ----------------------------------------------------
# v4.4.555: The three ways out of the dialog of an invitation with no organizer; a file with no organizer shows no answers (D-310)
# v4.4.535: The window's new attendees (round 4)
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


def test_a_file_without_an_organizer_shows_no_answers_and_is_only_added(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = sends(world, monkeypatch)
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE.replace("ORGANIZER:mailto:olivia@example.org\r\n", "")))
    wait(qtbot, lambda: window.card.add_button.isVisible())
    assert not window.card.accept_button.isVisible() and not window.card.decline_button.isVisible()
    first_choice(monkeypatch)
    asked = answer_calendar_mail(monkeypatch, Choice.QUIET)
    window.card.add_button.click()
    wait(qtbot, lambda: kept_items(world, CollectionKind.EVENTS) == 1)
    assert not asked and not gone
    window.close()


NO_ORGANIZER_MAIL = (b"From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
                     b"--B\r\nContent-Type: text/plain\r\n\r\nSee the file\r\n--B\r\nContent-Type: text/calendar; method=REQUEST\r\nContent-Disposition: attachment; filename=invite.ics\r\n\r\n"
                     + REQUEST_FILE.replace("ORGANIZER:mailto:olivia@example.org\r\n", "").encode() + b"\r\n--B--\r\n")


def opened_attachment(world: World, qtbot: QtBot) -> InvitationWindow:
    """An invitation with no organizer, as a card in a window of its own."""
    ensure_local_pim(world.engine)
    world.call("mail.fetch", account=world.account_id)
    with open_mailbox(world.engine, world.account_id) as mailbox:
        message = mailbox.deliver(mailbox.folder("Inbox"), NO_ORGANIZER_MAIL, frozenset()).store_id
    world.window.reading.open_calendar_card(CalendarSource(world.account_id, "Inbox", message, part=0), "invite.ics")
    wait(qtbot, lambda: bool(world.window.reading.card_windows))
    return world.window.reading.card_windows[0]


def stored_text(world: World) -> str:
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        (item,) = store.index.item_ids(calendar.collection_id)
        return store.get(calendar.collection_id, item).replace("\r\n ", "")


@pytest.mark.parametrize(("choice", "mails"), [(Choice.SEND, 1), (Choice.QUIET, 0)])
def test_send_and_dont_send_store_the_field_and_the_window_closes_with_the_dialog(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, choice: Choice, mails: int) -> None:
    gone = sends(world, monkeypatch)
    window = opened_attachment(world, qtbot)
    first_choice(monkeypatch)
    asked = answer_calendar_mail(monkeypatch, choice, address="sam@example.org")
    window.card.accept_button.click()
    wait(qtbot, lambda: not world.window.reading.card_windows)
    assert asked[0].windowTitle() == "Missing organizer" and len(gone) == mails
    assert "ORGANIZER:mailto:sam@example.org" in stored_text(world)


def test_the_field_starts_with_the_sender_and_emptied_it_leaves_no_organizer(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_attachment(world, qtbot)
    first_choice(monkeypatch)
    seen: list[str] = []

    def emptied(dialog: CalendarMailDialog) -> int:
        seen.append(dialog.address)
        dialog.to_field.clear()
        dialog.choice = Choice.QUIET
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(CalendarMailDialog, "exec", emptied)
    window.card.accept_button.click()
    wait(qtbot, lambda: not world.window.reading.card_windows)
    assert seen == ["olivia@example.org"] and "ORGANIZER" not in stored_text(world)


def test_cancel_closes_only_the_dialog_and_nothing_changes(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_attachment(world, qtbot)
    first_choice(monkeypatch)
    asked = answer_calendar_mail(monkeypatch, Choice.BACK)
    window.card.accept_button.click()
    wait(qtbot, lambda: bool(asked))
    qtbot.wait(200)
    assert kept_items(world, CollectionKind.EVENTS) == 0 and window.isVisible()
    window.close()


def test_the_dialog_with_an_address_has_cancel_and_cancel_takes_the_action_back(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Missing organizer", MISSING_ORGANIZER, "Why", address="sam@example.org")
    qtbot.addWidget(dialog)
    dialog.show()
    cancel = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Cancel")
    cancel.click()
    assert dialog.choice is Choice.BACK and not dialog.isVisible()


def test_every_other_dialog_has_no_cancel(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Send your answer", "Tell the organizer that you accept?", "Why")
    qtbot.addWidget(dialog)
    assert not any(button.text() == "Cancel" for button in dialog.findChildren(QPushButton))


def test_a_field_that_is_no_address_greys_dont_send_too_and_an_empty_one_does_not(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Missing organizer", MISSING_ORGANIZER, "Why", address="not an address")
    qtbot.addWidget(dialog)
    assert not dialog.quiet_button.isEnabled() and not dialog.send_button.isEnabled()
    dialog.to_field.clear()
    assert dialog.quiet_button.isEnabled() and not dialog.send_button.isEnabled()


def test_a_mail_the_server_sends_cannot_carry_a_message(qtbot: QtBot) -> None:
    dialog = CalendarMailDialog("Missing organizer", MISSING_ORGANIZER, "Why", address="sam@example.org", server_mails=True)
    qtbot.addWidget(dialog)
    assert not dialog.note_field.isEnabled() and dialog.note_field.toolTip()


def editor_with_attendee(world: World, qtbot: QtBot) -> EventEditor:
    page = open_calendar(world, qtbot)
    editor = EventEditor(world.backend, page._calendars(), [], collection=calendar_id(world))  # noqa: SLF001
    qtbot.addWidget(editor)
    editor.summary.setText("Budget")
    editor.set_people([Person("sam@example.org", "", "sam@example.org")])
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
