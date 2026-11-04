from email import message_from_bytes

from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts, kept, local_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail
from tests.test_engine_meetings import calendar_of

# --- Version ------------------------------------------------------------
__version__ = "test_engine_calendar_mail.py 4.4.533"

# --- Version history ----------------------------------------------------
# v4.4.533: A meeting text shows 250 characters and the dots up to 2048, a mark set here reaches the server after a move, and blocks that cannot be read offer Try again (D-310)
# v4.4.532: Initial tests: a calendar mail goes without the composer, asked before anything changes, an organizer named by the person, a cancellation for an attendee taken off (D-310)


NO_ORGANIZER = calendar_text().replace("ORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org\r\n", "")


def outbox(setup: Setup) -> list[bytes]:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return [mailbox.store.read(stored.ref) for stored in mailbox.store.list_messages(mailbox.folder("Outbox"))]


def test_a_calendar_mail_goes_with_your_message_above_it_and_keeps_its_calendar_part(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    result = answer(setup, message, "ACCEPTED", local_calendar(setup))
    call(setup.engine, "calendar.mail-send", account=setup.account_id, folder=result.draft_folder, message=result.draft_message, note="I will bring the samples", delay_seconds=900)
    assert not drafts(setup)
    (raw,) = outbox(setup)
    text = (parse_message(raw).text_body or "").replace("\r", "")
    assert text.startswith("I will bring the samples\n") and "has accepted this invitation" in text
    calendar = next(part for part in message_from_bytes(raw).walk() if part.get_content_type() == "text/calendar")
    assert calendar.get_param("method") == "REPLY"
    assert "olivia@example.org" in message_from_bytes(raw)["To"]


def test_a_dry_answer_says_who_mails_and_changes_nothing(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    result = call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="ACCEPTED", calendar=local_calendar(setup), dry=True)
    assert result.chrono and not result.no_organizer
    assert not kept(setup) and not drafts(setup)


def test_an_invitation_without_an_organizer_says_so_and_who_sent_the_mail(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(NO_ORGANIZER))
    result = call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="ACCEPTED", calendar=local_calendar(setup), dry=True)
    assert result.chrono and result.no_organizer and "@" in result.sender


def test_the_address_the_person_gives_gets_the_answer_as_organizer(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(NO_ORGANIZER))
    result = call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="ACCEPTED", calendar=local_calendar(setup), to="sam@example.org")
    assert len(kept(setup)) == 1 and result.draft_message is not None
    (raw,) = drafts(setup)
    assert "sam@example.org" in message_from_bytes(raw)["To"]
    assert "mailto:sam@example.org" in calendar_of(raw).replace("\r\n ", "")


def test_without_an_address_an_invitation_with_no_organizer_is_only_added(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(NO_ORGANIZER))
    result = answer(setup, message, "ACCEPTED", local_calendar(setup), mail=False)
    assert len(kept(setup)) == 1 and result.draft_message is None and not drafts(setup)


def test_delegating_without_a_mail_makes_none_and_is_remembered(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    result = call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.org", mail=False)
    assert not result.request_message and not result.answer_message and not drafts(setup)
    assert call(setup.engine, "invitation.card", account=setup.account_id, folder="Inbox", message=message).your_answer == "DELEGATED"


def test_a_new_meeting_you_keep_to_yourself_makes_no_mail(setup: Setup) -> None:
    info = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",), tell=False)
    assert not info.draft.message and not drafts(setup)


def test_the_window_can_ask_who_would_mail_a_meeting(setup: Setup) -> None:
    calendar = local_calendar(setup)
    assert call(setup.engine, "event.mailing", calendar=calendar).chrono
    info = call(setup.engine, "event.add", calendar=calendar, summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",))
    assert call(setup.engine, "event.mailing", item=info.item, deleting=True).chrono


def test_an_attendee_taken_off_gets_a_cancellation_the_others_the_change(setup: Setup) -> None:
    info = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org", "kim@example.org"))
    changed = call(setup.engine, "event.edit", item=info.item, attendee=("kim@example.org",), tell=True)
    assert changed.draft.message and changed.cancel_draft.message
    opened = call(setup.engine, "draft.open", account=setup.account_id, message=changed.cancel_draft.message, folder=changed.cancel_draft.folder)
    assert opened.to == ("sam@example.org",)
    raw = next(each for each in drafts(setup) if b"Cancelled" in each)
    cancel = read_calendar_message(calendar_of(raw))
    assert cancel is not None and cancel.method == "CANCEL" and cancel.sequence >= 1


def test_the_last_attendee_taken_off_hears_of_it_too(setup: Setup) -> None:
    info = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",))
    changed = call(setup.engine, "event.edit", item=info.item, clear=("attendee",), tell=True)
    assert changed.cancel_draft.message and not changed.draft.message


def test_nobody_hears_of_it_when_you_keep_the_change_to_yourself(setup: Setup) -> None:
    info = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org", "kim@example.org"))
    changed = call(setup.engine, "event.edit", item=info.item, attendee=("kim@example.org",), tell=False)
    assert not changed.draft.message and not changed.cancel_draft.message
