from dataclasses import replace

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_invitations import CalendarChoice, ChoicesResult
from chrono_mail.ui.invitationcalendar import InvitationCalendarDialog, InvitationDelegateDialog
from chrono_mail.ui.invitationcard import apply_text
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_answers.py 4.4.510"

# --- Version history ----------------------------------------------------
# v4.4.510: the tick of Settings > Calendar is gone (D-310)
# v4.4.506: your own meeting: its buttons, greyed with a reason when they cannot work (D-310 step 3)
# v4.4.505: Initial tests: the card's buttons per kind of message, greyed when out of date; the calendar window; the Delegate window (D-310)


def test_an_invitation_has_accept_tentative_decline_and_delegate(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.accept_button.isVisible())
    assert card.tentative_button.isVisible() and card.decline_button.isVisible() and card.delegate_button.isVisible()
    assert not card.add_button.isVisible() and not card.apply_button.isVisible()
    with qtbot.waitSignal(reader.invitation_answer_requested, timeout=2000) as signal:
        card.accept_button.click()
    assert signal.args == ["ACCEPTED"]


def test_an_out_of_date_invitation_greys_its_buttons_with_a_reason(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, stored_in="Calendar (Work)", version="older")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.accept_button.isVisible())
    assert not card.accept_button.isEnabled() and not card.decline_button.isEnabled() and card.answers_mark.isVisible()


def test_a_shared_item_offers_add_to_calendar(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="PUBLISH")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.add_button.isVisible())
    assert not card.accept_button.isVisible()


def test_the_organizers_changes_have_their_own_button() -> None:
    assert apply_text(replace(CARD, method="CANCEL", stored_in="Calendar (Work)", occurrence_kept=True)) == "Remove from calendar"
    assert apply_text(replace(CARD, method="CANCEL", stored_in="Calendar (Work)")) == ""  # the series is kept, that occurrence is not
    assert apply_text(replace(CARD, method="CANCEL")) == ""
    assert apply_text(replace(CARD, stored_in="Calendar (Work)", version="newer")) == "Update calendar"
    assert apply_text(replace(CARD, stored_in="Calendar (Work)", version="same")) == ""
    assert apply_text(replace(CARD, method="ADD")) == "Ask for the latest version"
    assert apply_text(replace(CARD, method="ADD", stored_in="Calendar (Work)", occurrence_kept=True)) == "Add to calendar"


CHOICES = ChoicesResult(
    (CalendarChoice("a" * 32, "Work", "acc1", "Office", False, False), CalendarChoice("b" * 32, "Public holidays", "acc1", "Office", True, True),
     CalendarChoice("c" * 32, "Home", "acc2", "Private", False, False)),
    "a" * 32, "",
)


def test_the_calendar_window_starts_on_the_receiving_accounts_calendar(qtbot: QtBot) -> None:
    dialog = InvitationCalendarDialog(CHOICES)
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert dialog.chosen == "a" * 32 and ok is not None and ok.isEnabled()
    assert not dialog.radios["b" * 32].isEnabled()  # read-only: greyed, with its (i)


def test_with_no_sensible_calendar_none_is_chosen_and_ok_is_greyed(qtbot: QtBot) -> None:
    dialog = InvitationCalendarDialog(replace(CHOICES, chosen=""))
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert dialog.chosen == "" and ok is not None and not ok.isEnabled()
    dialog.radios["c" * 32].setChecked(True)
    assert ok.isEnabled() and dialog.chosen == "c" * 32


def test_the_delegate_window_needs_an_address(qtbot: QtBot) -> None:
    dialog = InvitationDelegateDialog()
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok is not None and not ok.isEnabled()
    dialog.address_field.setText("sam@example.net")
    assert ok.isEnabled() and dialog.address == "sam@example.net"


def test_the_buttons_read_and_tab_in_one_order(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Step 2 review: Delegate… and the apply button stood before Accept, and Tab went another way."""
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, stored_in="Calendar (Work)", version="newer")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.apply_button.isVisible())
    row = [card.accept_button, card.tentative_button, card.decline_button, card.delegate_button, card.apply_button]
    assert [button.x() for button in row] == sorted(button.x() for button in row)
    seen: list[QWidget] = [card.accept_button]
    current: QWidget | None = card.accept_button
    for _ in range(12):  # Tab from Accept: the row's buttons come in the order they read
        current = current.nextInFocusChain() if current is not None else None
        if current is not None and current in row and current not in seen:
            seen.append(current)
    assert seen == row


def test_dates_added_already_offer_nothing_more() -> None:
    assert apply_text(replace(CARD, method="ADD", stored_in="Calendar (Work)", occurrence_kept=False)) == ""


def test_your_own_meeting_has_no_answer_buttons(reader: ReaderWidget, qtbot: QtBot) -> None:
    from chrono_mail.engine.invitations import InvitationPerson

    own = replace(CARD, organizer=InvitationPerson("me@example.com", "Me", "ACCEPTED", True))
    reader.show_message(replace(shown(html="<p></p>"), invitation=own))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.isVisible())
    assert not card.accept_button.isVisible() and not card.decline_button.isVisible() and not card.delegate_button.isVisible()


def test_your_own_meeting_has_its_own_buttons() -> None:
    from chrono_mail.engine.invitations import InvitationPerson
    from chrono_mail.ui.invitationcard import organize_choices

    you = InvitationPerson("me@example.com", "Me", "ACCEPTED", True)
    assert organize_choices(replace(CARD, method="REPLY", organizer=you, stored_in="Calendar (Work)")) == [("Take their answer", "take")]
    assert [action for _, action in organize_choices(replace(CARD, method="COUNTER", organizer=you, stored_in="Calendar (Work)"))] == ["accept_proposal", "decline_proposal"]
    assert organize_choices(replace(CARD, method="REFRESH", organizer=you)) == [("Send the latest version", "send_latest")]
    assert organize_choices(replace(CARD, method="REPLY", stored_in="Calendar (Work)")) == []  # not your meeting


def test_buttons_of_your_own_meeting_are_greyed_with_a_reason_when_they_cannot_work(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Step 3 review (OR-15): Send the latest version worked on a meeting no longer kept; an answer for a date you removed."""
    from chrono_mail.engine.invitations import InvitationPerson

    you = InvitationPerson("me@example.com", "Me", "ACCEPTED", True)
    card = reader._card  # noqa: SLF001
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="REFRESH", organizer=you)))
    qtbot.waitUntil(lambda: card.organize_buttons[0].isVisible())
    assert not card.organize_buttons[0].isEnabled() and card.answers_mark.isVisible() and "not in your calendar" in card.answers_mark.reason
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="REPLY", organizer=you, stored_in="Calendar (Work)", occurrence_kept=False)))
    qtbot.waitUntil(lambda: card.organize_buttons[0].text() == "Take their answer")
    assert not card.organize_buttons[0].isEnabled() and "This date" in card.answers_mark.reason
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="REFRESH", organizer=you, stored_in="Calendar (Work)", version="same")))
    qtbot.waitUntil(lambda: card.organize_buttons[0].isEnabled())
    assert not card.answers_mark.isVisible()
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="REPLY", organizer=you, stored_in="Calendar (Work)", occurrence_kept=True, organized_by_you=False)))
    qtbot.waitUntil(lambda: card.answers_mark.isVisible())
    assert not card.organize_buttons[0].isEnabled() and "another organizer" in card.answers_mark.reason
    useless = replace(CARD, method="COUNTER", organizer=you, stored_in="Calendar (Work)", occurrence_kept=True, version="same", proposal_usable=False)
    reader.show_message(replace(shown(html="<p></p>"), invitation=useless))
    qtbot.waitUntil(lambda: card.organize_buttons[1].isEnabled())
    assert not card.organize_buttons[0].isEnabled() and "changes nothing" in card.answers_mark.reason  # Decline proposal still answers it
