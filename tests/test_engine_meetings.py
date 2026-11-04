from dataclasses import replace

import pytest

from chrono_mail.config import AccountSettings
from chrono_mail.engine import meetingmail
from chrono_mail.engine.session import Engine
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts, kept, local_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_meetings.py 4.4.518"

# --- Version history ----------------------------------------------------
# v4.4.518: Initial tests: a meeting you make invites its attendees, a change or cancellation tells them when you say so (D-310)


def meeting(setup: Setup):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Budget", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",))


def calendar_of(raw: bytes) -> str:
    return next(part for part in parse_message(raw).attachments if part.content_type == "text/calendar").data.decode("utf-8")


def test_saving_a_meeting_with_attendees_makes_the_invitation_and_names_you_as_organizer(setup: Setup) -> None:
    saved = meeting(setup)
    assert saved.draft.message and saved.organizer is not None
    (raw,) = drafts(setup)
    message = read_calendar_message(calendar_of(raw))
    assert message is not None and message.method == "REQUEST" and message.organizer is not None
    assert parse_message(raw).subject == "Invitation: Budget" and [address.address for address in parse_message(raw).to_addresses] == ["sam@example.org"]
    assert "ORGANIZER" in kept(setup)[0]


def test_an_event_without_attendees_mails_nobody(setup: Setup) -> None:
    saved = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Dentist", start="2030-03-04T10:00", end="2030-03-04T11:00")
    assert not saved.draft.message and saved.organizer is None and drafts(setup) == []


def test_a_change_tells_the_attendees_with_a_higher_version_when_you_say_so(setup: Setup) -> None:
    saved = meeting(setup)
    changed = call(setup.engine, "event.edit", item=saved.item, start="2030-03-04T12:00", end="2030-03-04T13:00", tell=True)
    assert changed.draft.message
    mails = [read_calendar_message(calendar_of(raw)) for raw in drafts(setup)]
    assert sorted(message.sequence for message in mails if message is not None) == [0, 1]


def test_a_change_you_keep_to_yourself_makes_no_mail_and_no_new_version(setup: Setup) -> None:
    saved = meeting(setup)
    assert not call(setup.engine, "event.edit", item=saved.item, start="2030-03-04T12:00", end="2030-03-04T13:00").draft.message
    assert len(drafts(setup)) == 1 and "SEQUENCE" not in kept(setup)[0]


def test_cancelling_tells_the_attendees_when_you_say_so_and_the_meeting_goes(setup: Setup) -> None:
    saved = meeting(setup)
    result = call(setup.engine, "event.delete", item=saved.item, tell=True)
    assert result.draft.message and kept(setup) == []
    methods = [message.method for message in (read_calendar_message(calendar_of(raw)) for raw in drafts(setup)) if message is not None]
    assert sorted(methods) == ["CANCEL", "REQUEST"]


def test_cancelling_without_a_word_makes_no_mail(setup: Setup) -> None:
    saved = meeting(setup)
    assert not call(setup.engine, "event.delete", item=saved.item).draft.message
    assert kept(setup) == [] and len(drafts(setup)) == 1


def test_cancelling_one_date_of_a_series_names_that_date(setup: Setup) -> None:
    saved = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Weekly", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",), rrule="FREQ=WEEKLY")
    result = call(setup.engine, "event.delete", item=saved.item, occurrence="2030-03-11T10:00", tell=True)
    assert not result.deleted and result.draft.message
    texts = [calendar_of(raw) for raw in drafts(setup)]
    assert any("METHOD:CANCEL" in text and "RECURRENCE-ID" in text for text in texts)


def test_accepting_a_proposal_without_a_word_makes_no_mail_whatever_the_box_says(setup: Setup) -> None:
    saved = meeting(setup)
    text = kept(setup)[0].replace("BEGIN:VEVENT", "METHOD:COUNTER\nBEGIN:VEVENT", 1).replace("T100000", "T140000").replace("T110000", "T150000")
    message = deliver(setup, invitation_mail(text))
    result = call(setup.engine, "invitation.organize", account=setup.account_id, folder="Inbox", message=message, action="accept_proposal", mail=False)
    assert result.done == "moved" and result.draft_message is None and call(setup.engine, "event.show", item=saved.item).start.startswith("2030-03-04T14")


def test_the_box_of_the_account_that_got_the_invite_decides_whether_an_answer_goes(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    calendar = local_calendar(setup)
    before = meetingmail.settings_of

    def box_off(engine: Engine, account: str) -> AccountSettings | None:
        settings = before(engine, setup.account_id)  # the calendar's account has its box on
        return replace(settings, invitation_answers="none") if settings is not None and account == setup.account_id else settings

    monkeypatch.setattr(meetingmail, "settings_of", box_off)
    message = deliver(setup, invitation_mail(calendar_text()))
    assert answer(setup, message, "ACCEPTED", calendar).draft_message is None


def test_news_of_your_own_meeting_is_not_covered_by_that_box(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    before = meetingmail.settings_of

    def box_off(engine: Engine, account: str) -> AccountSettings | None:
        settings = before(engine, account)
        return replace(settings, invitation_answers="none") if settings is not None else settings

    monkeypatch.setattr(meetingmail, "settings_of", box_off)
    assert meeting(setup).draft.message


def test_a_meeting_of_someone_else_is_not_yours_to_tell_about(setup: Setup) -> None:
    saved = call(setup.engine, "event.add", calendar=local_calendar(setup), summary="Theirs", start="2030-03-04T10:00", end="2030-03-04T11:00", attendee=("sam@example.org",), organizer="pat@example.org")
    assert not saved.draft.message and not call(setup.engine, "event.show", item=saved.item).yours
    assert not call(setup.engine, "event.edit", item=saved.item, summary="Theirs too", tell=True).draft.message
    assert not call(setup.engine, "event.delete", item=saved.item, tell=True).draft.message
