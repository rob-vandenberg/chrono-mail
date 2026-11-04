from dataclasses import replace

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_invitations import CalendarChoice, ChoicesResult
from chrono_mail.ui.infomark import InfoMark
from chrono_mail.ui.invitationcalendar import InvitationBusyDialog, InvitationCalendarDialog, InvitationDelegateDialog
from chrono_mail.ui.invitationcard import adds_all, apply_text, intro_text, status_text
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_answers.py 4.4.525"

# --- Version history ----------------------------------------------------
# v4.4.525: your own mails, a file that does not name you, Add all with one item (D-310)
# v4.4.522: Update calendar is for an answer that stands (D-310)
# v4.4.517: An answer only the mail remembers shows like any other (D-310)
# v4.4.512: a date added to a series: the four answers, or only Request; a calendar that holds the meeting is greyed; a shared one is not ticked for busy times (D-310)
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


def test_an_answer_only_the_mail_remembers_shows_it_with_change_my_answer(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, your_answer="DECLINED", answered_on_mail=True)))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.change_button.isVisible())
    assert card.status.text() == "You declined."
    assert not card.accept_button.isVisible() and not card.decline_button.isVisible()


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
    assert apply_text(replace(CARD, stored_in="Calendar (Work)", version="newer", your_answer="ACCEPTED")) == "Update calendar"
    assert apply_text(replace(CARD, stored_in="Calendar (Work)", version="same")) == ""
    assert apply_text(replace(CARD, method="ADD")) == "Request"
    assert apply_text(replace(CARD, method="ADD", stored_in="Calendar (Work)", occurrence_kept=True)) == ""  # it has the four answers


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
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.delegate_button.isVisible())
    row = [card.accept_button, card.tentative_button, card.decline_button, card.delegate_button]
    assert [button.x() for button in row] == sorted(button.x() for button in row)
    seen: list[QWidget] = [card.accept_button]
    current: QWidget | None = card.accept_button
    for _ in range(12):  # Tab from Accept: the row's buttons come in the order they read
        current = current.nextInFocusChain() if current is not None else None
        if current is not None and current in row and current not in seen:
            seen.append(current)
    assert seen == row


def test_a_date_added_to_your_series_has_the_four_answers_and_no_other_button(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show()
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="ADD", stored_in="Calendar (Work)", occurrence_kept=True, version="newer", your_answer="TENTATIVE")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.accept_button.isVisible())
    assert all(button.isVisible() and button.isEnabled() for button in (card.tentative_button, card.decline_button, card.delegate_button))
    assert not card.apply_button.isVisible() and not card.request_label.isVisible()
    assert card.status.text() == "You accepted tentatively. In Calendar (Work)."  # never "newer": an added date always is


def test_a_date_of_a_series_you_do_not_have_offers_only_request(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show()
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="ADD")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.apply_button.isVisible())
    assert card.apply_button.text() == "Request" and card.apply_button.isEnabled()
    assert card.request_label.isVisible() and card.request_label.text() == "Request to resend the original invite:"
    assert card.intro.text().startswith("This invite was part of a series that is not in your calendar, so it cannot be added or answered on its own.")
    assert card.intro.text().endswith("and the organizer can then send you the invite again.")
    assert not any(button.isVisible() for button in (card.accept_button, card.tentative_button, card.decline_button, card.delegate_button, card.add_button))


def test_a_calendar_that_holds_the_meeting_is_greyed_with_its_reason(qtbot: QtBot) -> None:
    held = ChoicesResult((CalendarChoice("a" * 32, "Team", "acc1", "Office", False, False, True, True), CalendarChoice("b" * 32, "Work", "acc1", "Office", False, False)), "b" * 32, "")
    dialog = InvitationCalendarDialog(held)
    qtbot.addWidget(dialog)
    assert not dialog.radios["a" * 32].isEnabled() and dialog.radios["b" * 32].isEnabled() and dialog.chosen == "b" * 32
    (mark,) = dialog.findChildren(InfoMark)
    assert "is in this calendar already" in mark.toolTip()


def test_a_shared_calendar_does_not_start_ticked_for_your_busy_times(qtbot: QtBot) -> None:
    choices = ChoicesResult((CalendarChoice("a" * 32, "Team", "acc1", "Office", False, False, True), CalendarChoice("b" * 32, "Work", "acc1", "Office", False, False)), "b" * 32, "", "acc1")
    dialog = InvitationBusyDialog(choices)
    qtbot.addWidget(dialog)
    assert dialog.chosen == ("b" * 32,)


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


def test_your_own_mails_say_what_you_sent_and_have_no_buttons(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, own_mail="sent")))
    card = reader._card  # noqa: SLF001
    assert card.intro.text() == "You sent this invitation." and card.status.text() == ""
    assert not card.accept_button.isVisibleTo(card) and not card.delegate_button.isVisibleTo(card) and not card.apply_button.isVisibleTo(card)
    assert intro_text(replace(CARD, own_mail="draft")) == "This invitation is waiting in Drafts."
    me = replace(CARD.attendees[0], you=True, answer="ACCEPTED")
    assert intro_text(replace(CARD, method="REPLY", attendees=(me,), own_mail="sent")) == "You sent this answer. You accepted."
    assert intro_text(replace(CARD, method="CANCEL", own_mail="sent")) == "You sent this cancellation."
    assert status_text(replace(CARD, stored_in="Personal (account)", own_mail="sent")) == ""


def test_add_all_needs_more_than_one_item_to_add() -> None:
    several = replace(CARD, method="PUBLISH", parts=(CARD, CARD), events=2, left=2)
    assert adds_all(several) and not adds_all(replace(several, events=1)) and not adds_all(replace(several, own_mail="sent"))


def test_a_file_that_does_not_name_you_greys_the_answers_with_a_reason(reader: ReaderWidget, qtbot: QtBot) -> None:
    others = tuple(replace(person, you=False) for person in CARD.attendees)
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, position=-1, attendees=others)))
    card = reader._card  # noqa: SLF001
    assert card.accept_button.isVisibleTo(card) and not card.accept_button.isEnabled() and not card.delegate_button.isEnabled()
    assert card.answers_mark.isVisibleTo(card) and "no address to answer with" in card.answers_mark.toolTip()


def test_a_mail_that_does_not_name_you_can_be_answered_as_an_invitation_passed_on(reader: ReaderWidget, qtbot: QtBot) -> None:
    others = tuple(replace(person, you=False) for person in CARD.attendees)
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, attendees=others)))
    assert reader._card.accept_button.isEnabled()  # noqa: SLF001


def test_a_block_of_your_own_mail_stays_without_buttons_when_it_is_read_again(reader: ReaderWidget, qtbot: QtBot) -> None:
    part = replace(CARD, method="PUBLISH", item=0)
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, method="PUBLISH", parts=(part, replace(part, item=1)), items=2, events=2, own_mail="sent")))
    card = reader._card  # noqa: SLF001
    assert not card.blocks[0].add_button.isVisibleTo(card.blocks[0])
    card.update_blocks((part,), 0)
    assert not card.blocks[0].add_button.isVisibleTo(card.blocks[0])
