import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts, kept, local_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail, show, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_added.py 4.4.512"

# --- Version history ----------------------------------------------------
# v4.4.512: Initial tests: a date added to a series is answered like an invitation; only your own calendars hold a meeting (D-310)

NEW_DATE = "20261231T100000Z"


def series(answered: str = "ACCEPTED") -> str:
    return calendar_text(method="", answer=answered).replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)


def added() -> str:
    return calendar_text(method="ADD", sequence=1).replace("DTSTART:20261029T100000Z", f"DTSTART:{NEW_DATE}").replace("DTEND:20261029T103000Z", "DTEND:20261231T103000Z")


def reply_of(raw: bytes) -> str:
    (part,) = [each for each in parse_message(raw).attachments if each.content_type == "text/calendar"]
    return part.data.decode()


def test_accept_adds_the_date_with_your_answer_and_the_series_keeps_its_own(setup: Setup) -> None:
    stored(setup, series("TENTATIVE"))
    result = answer(setup, deliver(setup, invitation_mail(added())), "ACCEPTED")
    (text,) = kept(setup)
    assert text.count("RDATE") == 1 and text.count("PARTSTAT=TENTATIVE") == 1 and text.count("PARTSTAT=ACCEPTED") == 1 and result.stored_in
    (draft,) = drafts(setup)
    assert f"RECURRENCE-ID:{NEW_DATE}" in reply_of(draft) and "PARTSTAT=ACCEPTED" in reply_of(draft)


def test_decline_adds_nothing_and_answers_only_that_date(setup: Setup) -> None:
    """Without RECURRENCE-ID the organizer would read a decline of the whole series."""
    stored(setup, series())
    before = kept(setup)
    result = answer(setup, deliver(setup, invitation_mail(added())), "DECLINED")
    assert kept(setup) == before and result.stored_in == ""
    (draft,) = drafts(setup)
    assert f"RECURRENCE-ID:{NEW_DATE}" in reply_of(draft) and "PARTSTAT=DECLINED" in reply_of(draft)


def test_decline_of_a_date_there_already_marks_it_and_leaves_the_series(setup: Setup) -> None:
    stored(setup, series())
    message = deliver(setup, invitation_mail(added()))
    answer(setup, message, "ACCEPTED")
    answer(setup, message, "DECLINED")
    (text,) = kept(setup)
    assert "RRULE:FREQ=WEEKLY" in text and text.count("RDATE") == 1 and text.count("PARTSTAT=DECLINED") == 1 and text.count("PARTSTAT=ACCEPTED") == 1
    card = show(setup, message).invitation
    assert card is not None and card.your_answer == "DECLINED"  # the answer to this date, not the series'


def test_the_card_shows_no_answer_of_yours_before_the_date_is_answered(setup: Setup) -> None:
    stored(setup, series())
    card = show(setup, deliver(setup, invitation_mail(added()))).invitation
    assert card is not None and card.your_answer == "" and card.stored_in


def test_a_date_of_a_series_you_do_not_have_cannot_be_answered_or_passed_on(setup: Setup) -> None:
    local_calendar(setup)
    message = deliver(setup, invitation_mail(added()))
    with pytest.raises(UsageError, match="series of this date is not in your calendar"):
        answer(setup, message, "ACCEPTED")
    with pytest.raises(UsageError, match="series of this date is not in your calendar"):
        call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.net")
    assert kept(setup) == [] and drafts(setup) == []


def test_delegate_passes_on_only_that_date_and_adds_nothing(setup: Setup) -> None:
    stored(setup, series())
    before = kept(setup)
    result = call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(added())), to="sam@example.net")
    assert kept(setup) == before and result.answer_message
    made = {parse_message(raw).subject: raw for raw in drafts(setup)}
    request = reply_of(made["Invitation: Choice of colours"])
    assert "METHOD:REQUEST" in request and f"RECURRENCE-ID:{NEW_DATE}" in request
    assert f"RECURRENCE-ID:{NEW_DATE}" in reply_of(made["Delegated: Choice of colours"])


def test_delegate_of_a_date_there_already_marks_only_that_date(setup: Setup) -> None:
    stored(setup, series())
    message = deliver(setup, invitation_mail(added()))
    answer(setup, message, "ACCEPTED")
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.net")
    (text,) = kept(setup)
    assert text.count("PARTSTAT=DELEGATED") == 1 and text.count("PARTSTAT=ACCEPTED") == 1 and 'DELEGATED-TO="mailto:sam@example.net"' in text


def test_a_date_is_never_added_without_an_answer(setup: Setup) -> None:
    stored(setup, series())
    before = kept(setup)
    with pytest.raises(UsageError, match="changes nothing"):
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(added())))
    assert kept(setup) == before


def someone_elses(setup: Setup, value: str) -> tuple[str, str]:
    """The meeting in a calendar of someone else you may write in, next to your own empty calendar: (theirs, yours)."""
    yours = local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        theirs = store.create_collection(CollectionKind.EVENTS, "Team", "", False, {value: "1"})
        store.put(theirs.collection_id, calendar_text(method="", answer="ACCEPTED"))
    return theirs.collection_id, yours


@pytest.mark.parametrize("value", ["shared", "public"])
def test_a_calendar_of_someone_else_never_counts_as_holding_your_meeting(setup: Setup, value: str) -> None:
    theirs, yours = someone_elses(setup, value)
    message = deliver(setup, invitation_mail(calendar_text()))
    card = show(setup, message).invitation
    assert card is not None and card.stored_in == ""
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert choices.stored == "" and {each.collection: each.holds for each in choices.calendars} == {theirs: True, yours: False}
    with pytest.raises(UsageError, match="in that calendar already"):
        answer(setup, message, "ACCEPTED", theirs)
    assert answer(setup, message, "ACCEPTED", yours).stored_in.startswith("Calendar (")


def test_a_shared_calendar_is_never_the_one_the_window_starts_on(setup: Setup) -> None:
    ensure = local_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(ensure, server={"shared": "1"})
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(calendar_text())))
    assert choices.chosen == "" and next(each for each in choices.calendars if each.collection == ensure).shared


def test_a_reply_is_readable_as_about_one_date(setup: Setup) -> None:
    stored(setup, series())
    answer(setup, deliver(setup, invitation_mail(added())), "TENTATIVE")
    (draft,) = drafts(setup)
    reply = read_calendar_message(reply_of(draft))
    assert reply is not None and reply.occurrence is not None
