from dataclasses import replace

import pytest

from chrono_mail.config import AccountSettings, AppConfig
from chrono_mail.core import ANSWER_MARKS, LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.invitations import InvitationInfo
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_answers.py 4.4.517"

# --- Version history ----------------------------------------------------
# v4.4.517: A Decline or a Delegate is remembered on the mail, Change my answer takes it off (D-310)
# v4.4.516: Change my answer on a date takes only that date, and the date comes back with a new answer (D-310)
# v4.4.514: the server can be chosen without its word (D-310)
# v4.4.512: a date added to a series is answered (D-310)
# v4.4.510: a server that announces nothing never counts as mailing (D-310)
# v4.4.505: Initial tests: Accept, Tentative and Decline keep the item with your answer, server first, and make the answer to send as a
#           draft with the calendar answer (METHOD:REPLY) when Chrono Mail mails it; the calendars to choose from (D-310)


def local_calendar(setup: Setup) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        return calendar.collection_id


def kept(setup: Setup) -> list[str]:
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        return [store.get(calendar.collection_id, item) for item in store.index.item_ids(calendar.collection_id)]


def drafts(setup: Setup) -> list[bytes]:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        folder = mailbox.folder("Drafts")
        return [mailbox.store.read(stored.ref) for stored in mailbox.store.list_messages(folder)]


def answer(setup: Setup, message: str, what: str, calendar: str = "", mail: bool = True):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer=what, calendar=calendar, mail=mail)


def reopened(setup: Setup, message: str) -> None:
    assert call(setup.engine, "invitation.reopen", account=setup.account_id, folder="Inbox", message=message).removed


def test_change_my_answer_on_one_date_keeps_the_series_and_the_date_comes_back(setup: Setup) -> None:
    calendar = local_calendar(setup)
    stored(setup, calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    message = deliver(setup, invitation_mail(calendar_text().replace("SUMMARY:", "RECURRENCE-ID:20261029T100000Z\r\nSUMMARY:", 1)))
    answer(setup, message, "TENTATIVE", calendar)
    reopened(setup, message)
    (text,) = kept(setup)
    assert "RRULE" in text and "EXDATE" in text and "RECURRENCE-ID" not in text
    answer(setup, message, "ACCEPTED", calendar)
    (text,) = kept(setup)
    assert "EXDATE" not in text and "RECURRENCE-ID" in text


def test_change_my_answer_on_an_added_date_keeps_the_series_and_the_date_comes_back(setup: Setup) -> None:
    calendar = local_calendar(setup)
    stored(setup, calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    added = calendar_text(method="ADD", sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART:20261231T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261231T103000Z")
    message = deliver(setup, invitation_mail(added))
    answer(setup, message, "ACCEPTED", calendar)
    assert "RDATE" in kept(setup)[0]
    reopened(setup, message)
    (text,) = kept(setup)
    assert "RRULE" in text and "RDATE" not in text and "EXDATE" not in text and "RECURRENCE-ID" not in text
    answer(setup, message, "ACCEPTED", calendar)
    (text,) = kept(setup)
    assert "RDATE" in text and "EXDATE" not in text and "RECURRENCE-ID" in text


def test_accept_keeps_the_meeting_with_your_answer_and_makes_the_answer_to_send(setup: Setup) -> None:
    calendar = local_calendar(setup)
    message = deliver(setup, invitation_mail(calendar_text()))
    result = answer(setup, message, "ACCEPTED", calendar)
    (text,) = kept(setup)
    assert "PARTSTAT=ACCEPTED" in text and "METHOD" not in text and "RSVP" not in text and result.stored_in.startswith("Calendar (")
    assert not result.server_answers and result.draft_message is not None
    (draft,) = drafts(setup)
    parsed = parse_message(draft)
    assert parsed.subject == "Accepted: Choice of colours" and [address.address for address in parsed.to_addresses] == ["olivia@example.org"]
    (part,) = [each for each in parsed.attachments if each.content_type == "text/calendar"]
    reply = read_calendar_message(part.data)
    assert part.method == "REPLY" and reply is not None and reply.method == "REPLY" and reply.uid == "meeting-1@example.com"
    assert [(person.address, person.status) for person in reply.attendees] == [("me@example.com", "ACCEPTED")]


def test_without_the_tick_no_answer_mail_is_made(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    result = answer(setup, message, "TENTATIVE", local_calendar(setup), mail=False)
    assert result.draft_message is None and drafts(setup) == [] and "PARTSTAT=TENTATIVE" in kept(setup)[0]


def card(setup: Setup, message: str) -> InvitationInfo:
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation
    assert shown is not None
    return shown


def test_a_decline_is_remembered_on_the_mail_and_change_my_answer_takes_it_off(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    assert not card(setup, message).answered_on_mail
    answer(setup, message, "DECLINED")
    declined = card(setup, message)
    assert declined.answered_on_mail and declined.your_answer == "DECLINED" and declined.stored_in == ""
    assert [person.answer for person in declined.attendees if person.you] == ["DECLINED"]
    assert not call(setup.engine, "invitation.reopen", account=setup.account_id, folder="Inbox", message=message).removed  # no copy was there
    assert not card(setup, message).answered_on_mail and card(setup, message).your_answer == ""


def test_a_delegation_that_leaves_no_copy_is_remembered_on_the_mail(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="other@example.com")
    delegated = card(setup, message)
    assert delegated.answered_on_mail and delegated.your_answer == "DELEGATED"


def test_a_copy_in_your_calendar_knows_better_than_the_mark(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    answer(setup, message, "DECLINED")
    stored(setup, calendar_text(method="", answer="ACCEPTED"))  # accepted later, from another mail or computer
    kept_copy = card(setup, message)
    assert not kept_copy.answered_on_mail and kept_copy.your_answer == "ACCEPTED"


def test_a_new_answer_takes_an_older_mark_off(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    answer(setup, message, "DECLINED")
    answer(setup, message, "ACCEPTED", local_calendar(setup))
    assert not card(setup, message).answered_on_mail and card(setup, message).your_answer == "ACCEPTED"


def test_a_mark_speaks_only_for_the_mails_own_invitation(setup: Setup) -> None:
    both = calendar_text() + calendar_text().replace("UID:", "UID:second-")
    message = deliver(setup, invitation_mail(both))
    answer_one = call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="DECLINED", calendar="", mail=False, item=0)
    assert answer_one.answer == "DECLINED"
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        reference = mailbox.reference(mailbox.folder("Inbox"), message)
        assert not mailbox.current_flags(reference) & ANSWER_MARKS  # one mail cannot say which of its items was declined


def test_decline_removes_a_kept_copy_and_makes_the_answer(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    message = deliver(setup, invitation_mail(calendar_text()))
    result = answer(setup, message, "DECLINED")
    assert kept(setup) == [] and result.stored_in == "" and result.draft_message is not None
    assert parse_message(drafts(setup)[0]).subject == "Declined: Choice of colours"


def test_accept_of_a_kept_item_changes_it_where_it_is(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="TENTATIVE").replace("LOCATION:", "COMMENT:kept here\r\nLOCATION:", 1))
    message = deliver(setup, invitation_mail(calendar_text()))
    answer(setup, message, "ACCEPTED", mail=False)
    (text,) = kept(setup)
    assert "PARTSTAT=ACCEPTED" in text and "kept here" in text  # the copy's own change stays


def test_accept_needs_a_calendar_when_nothing_is_kept(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    with pytest.raises(UsageError, match="Choose the calendar"):
        answer(setup, message, "ACCEPTED")


def test_an_answer_asks_no_answer(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text(method="REPLY")))
    with pytest.raises(UsageError, match="asks for no answer"):
        answer(setup, message, "ACCEPTED", local_calendar(setup))


def test_the_calendars_to_choose_from(setup: Setup) -> None:
    calendar = local_calendar(setup)
    message = deliver(setup, invitation_mail(calendar_text()))
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert [each.collection for each in choices.calendars] == [calendar] and choices.stored == ""
    assert choices.chosen == ""  # the receiving account has no calendar of its own: no gamble


def test_who_mails_the_answer_follows_the_account() -> None:
    from chrono_mail.core.account import Account

    base = AccountSettings(Account.__new__(Account))  # only the calendar fields count here
    assert not replace(base, dav_url="").server_answers
    assert replace(base, dav_url="https://dav.example.com/", dav_auto_schedule=True).server_answers
    assert not replace(base, dav_url="https://dav.example.com/", dav_auto_schedule=True, invitation_answers="chrono").server_answers
    assert replace(base, dav_url="https://dav.example.com/", dav_auto_schedule=True, invitation_answers="server").server_answers
    assert replace(base, dav_url="https://dav.example.com/", dav_always_answers=True, invitation_answers="none").answers_by == "server"
    assert replace(base, invitation_answers="none").answers_by == "none"
    assert replace(base, dav_url="https://dav.example.com/", invitation_answers="server").answers_by == "server"  # the person's choice, also without the server's word
    assert replace(base, invitation_answers="server").answers_by == "chrono"  # no server: nobody else can send it


def test_a_newer_version_updates_the_copy_and_keeps_your_answer(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    message = deliver(setup, invitation_mail(calendar_text(sequence=1, title="Choice of colours, room 2")))
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    (text,) = kept(setup)
    assert result.done == "updated" and "room 2" in text and "PARTSTAT=ACCEPTED" in text and "SEQUENCE:1" in text


def test_a_new_time_asks_your_answer_again(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    moved = calendar_text(sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART:20261029T110000Z").replace("DTEND:20261029T103000Z", "DTEND:20261029T113000Z")
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(moved)))
    assert "PARTSTAT=NEEDS-ACTION" in kept(setup)[0]


def test_a_cancellation_removes_the_meeting_or_its_one_occurrence(setup: Setup) -> None:
    series = calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    stored(setup, series)
    one = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(one)))
    (text,) = kept(setup)
    assert "EXDATE:20261105T100000Z" in text
    whole = calendar_text(method="CANCEL", sequence=2).replace("Choice of colours", "Choice of colours")
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(whole).replace(b"Subject: Invitation", b"Subject: Invitation", 1)))
    assert result.done == "removed" and kept(setup) == []


def test_an_added_occurrence_of_a_series_you_do_not_have_asks_for_it(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text(method="ADD")))
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    assert result.done == "asked" and result.draft_message is not None
    (draft,) = drafts(setup)
    (part,) = [each for each in parse_message(draft).attachments if each.content_type == "text/calendar"]
    assert part.method == "REFRESH"


def test_delegate_makes_the_invitation_for_them_and_your_answer(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="NEEDS-ACTION"))
    message = deliver(setup, invitation_mail(calendar_text()))
    result = call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.net")
    made = {parse_message(raw).subject: parse_message(raw) for raw in drafts(setup)}
    passed, answered = made["Invitation: Choice of colours"], made["Delegated: Choice of colours"]
    assert [address.address for address in passed.to_addresses] == ["sam@example.net"] and result.request_message and result.answer_message
    request = read_calendar_message(next(each for each in passed.attachments if each.content_type == "text/calendar").data)
    answer_part = next(each for each in answered.attachments if each.content_type == "text/calendar")
    assert request is not None and request.method == "REQUEST" and answer_part.method == "REPLY"
    assert ("sam@example.net", "NEEDS-ACTION") in [(person.address, person.status) for person in request.attendees]
    assert "PARTSTAT=DELEGATED" in kept(setup)[0]
    with pytest.raises(UsageError, match="yourself"):
        call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="me@example.com")


def test_an_added_occurrence_is_added_once(setup: Setup) -> None:
    """Step 2 review: Accept on an ADD replaced the whole series; a second Add wrote the occurrence twice."""
    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    added = calendar_text(method="ADD", sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART:20261231T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261231T103000Z")
    message = deliver(setup, invitation_mail(added))
    answer(setup, message, "ACCEPTED")
    answer(setup, message, "ACCEPTED")
    (text,) = kept(setup)
    assert "RRULE:FREQ=WEEKLY" in text and text.count("RDATE") == 1 and text.count("RECURRENCE-ID") == 1


def test_an_update_of_one_occurrence_keeps_the_series_answer_once(setup: Setup) -> None:
    """Step 2 review: the answer went back to NEEDS-ACTION and the copy got two attendee lines for you."""
    stored(setup, calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    one = calendar_text(sequence=1, title="Choice of colours, room 2").replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    one = one.replace("DTSTART:20261029T100000Z", "DTSTART:20261105T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261105T103000Z")
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(one)))
    (text,) = kept(setup)
    assert "NEEDS-ACTION" not in text and text.count("mailto:me@example.com") == 2  # one line in the series, one in the occurrence


def test_an_older_update_changes_nothing(setup: Setup) -> None:
    stored(setup, calendar_text(method="", sequence=3, answer="ACCEPTED"))
    with pytest.raises(UsageError, match="newer"):
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(calendar_text(sequence=1))))


def test_a_cancellation_of_the_only_occurrence_kept_removes_it(setup: Setup) -> None:
    only = calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    stored(setup, only)
    cancel = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(cancel)))
    assert result.done == "removed" and kept(setup) == []


def test_the_answer_to_a_delegation_names_the_delegate() -> None:
    from datetime import UTC, datetime

    from chrono_mail.pim.itip import delegated_texts

    texts = delegated_texts(calendar_text(), "me@example.com", "sam@example.net", datetime(2026, 11, 3, tzinfo=UTC))
    assert texts is not None
    reply = read_calendar_message(texts[1])
    assert reply is not None and {person.address for person in reply.attendees} == {"me@example.com", "sam@example.net"}


def test_a_cancellation_of_an_occurrence_not_kept_changes_nothing(setup: Setup) -> None:
    """Step 2 review round 2: a copy of the 5 November occurrence only was deleted by a cancellation of 12 November."""
    stored(setup, calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1))
    cancel = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261112T100000Z", 1)
    with pytest.raises(UsageError, match="not in your calendar"):
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(cancel)))
    assert len(kept(setup)) == 1


def test_an_older_cancellation_changes_nothing(setup: Setup) -> None:
    stored(setup, calendar_text(method="", sequence=4))
    with pytest.raises(UsageError, match="out of date"):
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(calendar_text(method="CANCEL", sequence=2))))
    assert len(kept(setup)) == 1


def test_a_shared_item_is_kept_as_sent(setup: Setup) -> None:
    """Step 2 review round 2: Add to calendar made up an attendee line saying you accepted."""
    shared = calendar_text(method="PUBLISH").replace("ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com\r\n", "")
    result = answer(setup, deliver(setup, invitation_mail(shared)), "ACCEPTED", local_calendar(setup))
    (text,) = kept(setup)
    assert "ATTENDEE" not in text and "METHOD" not in text and result.draft_message is None


def test_a_calendar_only_account_follows_its_choice(setup: Setup) -> None:
    """Step 2 review round 2: an account of calendars only has no address, so the server never counted as answering for it."""
    from chrono_mail.core import CollectionInfo
    from chrono_mail.engine.commands_invitations import _server_answers  # noqa: PLC2701 - the rule itself

    on_server = CollectionInfo("d" * 32, CollectionKind.EVENTS, "Work", server=(("href", "/calendars/work/"),))
    here = CollectionInfo("e" * 32, CollectionKind.EVENTS, "Here")

    def with_account(address: str, answers: str) -> None:
        def change(config: AppConfig) -> AppConfig:
            settings = config.find(setup.account_id)
            account = replace(settings.account, email_address=address)
            return config.with_account(replace(settings, account=account, dav_url="https://dav.example.com/", dav_auto_schedule=True, invitation_answers=answers))

        setup.engine.change_config(change)

    with_account("", "server")
    assert _server_answers(setup.engine, setup.account_id, on_server, "me@example.com")
    assert not _server_answers(setup.engine, setup.account_id, here, "me@example.com")  # only on this computer: Chrono Mail
    with_account("other@example.com", "server")
    assert not _server_answers(setup.engine, setup.account_id, on_server, "me@example.com")  # a server answers only for its own user
    with_account("me@example.com", "chrono")
    assert not _server_answers(setup.engine, setup.account_id, on_server, "me@example.com")


def test_an_outdated_invitation_cannot_be_answered(setup: Setup) -> None:
    """Step 2 review round 3: the command answered an invitation older than the copy (the window greyed it, the command did not, OR-4)."""
    stored(setup, calendar_text(method="", sequence=3, answer="ACCEPTED"))
    message = deliver(setup, invitation_mail(calendar_text(sequence=1)))
    with pytest.raises(UsageError, match="out of date"):
        answer(setup, message, "DECLINED")
    assert len(kept(setup)) == 1 and drafts(setup) == []


def test_a_new_version_of_the_whole_series_drops_occurrences_it_no_longer_changes(setup: Setup) -> None:
    """Step 2 review round 4: Update calendar kept a moved occurrence the organizer had put back."""
    series = calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    moved = calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1).split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    stored(setup, series.replace("END:VCALENDAR", "BEGIN:VEVENT" + moved + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1))
    newer = calendar_text(sequence=1).replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(newer)))
    assert "RECURRENCE-ID" not in kept(setup)[0]


def test_another_time_zone_asks_the_answer_again() -> None:
    from chrono_mail.pim.itip import updated_copy

    here = calendar_text(method="", answer="ACCEPTED").replace("DTSTART:20261029T100000Z", "DTSTART;TZID=Europe/Amsterdam:20261029T100000")
    there = calendar_text(sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART;TZID=America/New_York:20261029T100000")
    copy = updated_copy(here, there, "me@example.com")
    assert copy is not None and "PARTSTAT=NEEDS-ACTION" in copy


def test_an_outdated_invitation_cannot_be_delegated(setup: Setup) -> None:
    stored(setup, calendar_text(method="", sequence=3))
    with pytest.raises(UsageError, match="out of date"):
        call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(calendar_text(sequence=1))), to="sam@example.net")
    assert drafts(setup) == []


def test_the_card_sees_a_cancelled_occurrence_not_kept_as_not_in_your_calendar(setup: Setup) -> None:
    from tests.test_engine_invitations import show

    stored(setup, calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1))
    cancel = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261112T100000Z", 1)
    card = show(setup, deliver(setup, invitation_mail(cancel))).invitation
    assert card is not None and card.stored_in and not card.occurrence_kept  # the series is kept, this occurrence is not: nothing to remove


def test_an_occurrence_of_a_kept_series_goes_into_its_calendar_and_decline_adds_nothing(setup: Setup) -> None:
    """Step 2 review round 5: the card asked for a calendar the engine then ignored; Decline added a declined occurrence."""
    stored(setup, calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1))
    other = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261112T100000Z", 1)
    message = deliver(setup, invitation_mail(other))
    answer(setup, message, "DECLINED", mail=False)
    assert "20261112" not in kept(setup)[0]
    answer(setup, message, "ACCEPTED", mail=False)
    (text,) = kept(setup)
    assert "RECURRENCE-ID:20261112T100000Z" in text and "RECURRENCE-ID:20261105T100000Z" in text


def test_a_delegated_copy_names_the_delegate() -> None:
    from chrono_mail.pim.itip import with_delegate

    copy = with_delegate(calendar_text(method="", answer="DELEGATED"), "me@example.com", "sam@example.net", [])
    assert copy is not None and 'DELEGATED-TO="mailto:sam@example.net"' in copy and 'DELEGATED-FROM="mailto:me@example.com"' in copy


def test_an_added_date_takes_the_series_answer(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    added = calendar_text(method="ADD", sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART:20261231T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261231T103000Z")
    answer(setup, deliver(setup, invitation_mail(added)), "ACCEPTED")
    assert "NEEDS-ACTION" not in kept(setup)[0]


def test_a_removed_occurrence_offers_nothing_more_and_is_never_excluded_twice(setup: Setup) -> None:
    """Step 2 review round 6: Remove from calendar stayed after the removal, and a second click wrote the same EXDATE twice."""
    from tests.test_engine_invitations import show

    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    cancel = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    message = deliver(setup, invitation_mail(cancel))
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    card = show(setup, message).invitation
    assert card is not None and not card.occurrence_kept
    with pytest.raises(UsageError, match="not in your calendar"):  # removed already: said so, nothing written
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    assert kept(setup)[0].count("EXDATE") == 1


def test_a_new_version_of_the_series_keeps_your_declined_date(setup: Setup) -> None:
    """Step 2 review round 6: a whole-series update dropped your answer to one occurrence."""
    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    declined = calendar_text(method="", answer="DECLINED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    declined = declined.replace("DTSTART:20261029T100000Z", "DTSTART:20261105T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261105T103000Z")
    part = declined.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    stored(setup, series.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1))
    renamed = calendar_text(sequence=1, title="Choice of colours, room 2").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(renamed)))
    (text,) = kept(setup)
    assert "room 2" in text and "RECURRENCE-ID:20261105T100000Z" in text and "PARTSTAT=DECLINED" in text and "PARTSTAT=ACCEPTED" in text


def test_the_same_dates_written_another_way_are_no_change_of_time() -> None:
    """Step 2 review round 7: two EXDATE lines against one with commas, or a zone against UTC, reset your answer."""
    from chrono_mail.pim.itip import updated_copy

    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nEXDATE:20261105T100000Z\r\nEXDATE:20261112T100000Z\r\nSUMMARY:", 1)
    renamed = calendar_text(sequence=1, title="Choice of colours, room 2").replace(
        "SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nEXDATE;TZID=Europe/Amsterdam:20261105T110000,20261112T110000\r\nSUMMARY:", 1)
    renamed = renamed.replace("DTSTART:20261029T100000Z", "DTSTART;TZID=Europe/Amsterdam:20261029T110000")
    copy = updated_copy(series, renamed, "me@example.com")
    assert copy is not None and "PARTSTAT=ACCEPTED" in copy and "NEEDS-ACTION" not in copy


def test_an_organizer_named_last_name_first_can_be_answered(setup: Setup) -> None:
    """Step 2 review round 8: "Doe, John" made the answer fail after the calendar was changed."""
    text = calendar_text().replace("ORGANIZER;CN=Olivia Organizer:", 'ORGANIZER;CN="Organizer, Olivia":', 1)
    result = answer(setup, deliver(setup, invitation_mail(text)), "ACCEPTED", local_calendar(setup))
    (draft,) = drafts(setup)
    assert result.draft_message and [address.address for address in parse_message(draft).to_addresses] == ["olivia@example.org"]


def test_a_moved_occurrence_asks_the_answer_again() -> None:
    from chrono_mail.pim.itip import updated_copy

    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    moved = calendar_text(sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    moved = moved.replace("DTSTART:20261029T100000Z", "DTSTART:20261106T090000Z").replace("DTEND:20261029T103000Z", "DTEND:20261106T093000Z")
    copy = updated_copy(series, moved, "me@example.com")
    assert copy is not None and "PARTSTAT=NEEDS-ACTION" in copy and "PARTSTAT=ACCEPTED" in copy  # the series stays accepted, the moved date asks


def test_your_own_meeting_cannot_be_answered(setup: Setup) -> None:
    """Step 2 review round 9: Decline on your own meeting deleted it, and its server would cancel it for every attendee."""
    own = calendar_text().replace("ORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org", "ORGANIZER;CN=Me:mailto:me@example.com", 1)
    message = deliver(setup, invitation_mail(own))
    with pytest.raises(UsageError, match="organizer"):
        answer(setup, message, "DECLINED")
    with pytest.raises(UsageError, match="organizer"):
        call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.net")


def test_a_new_answer_to_the_series_keeps_a_date_answered_otherwise() -> None:
    from chrono_mail.pim.itip import merged_answer

    series = calendar_text(method="", answer="TENTATIVE").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    declined = calendar_text(method="", answer="DECLINED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    part = declined.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    both = series.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    copy = merged_answer(both, calendar_text(), "me@example.com", "ACCEPTED", None, take_message=False)
    assert copy is not None and "PARTSTAT=DECLINED" in copy and "PARTSTAT=ACCEPTED" in copy


def test_an_invitation_to_several_occurrences_reaches_the_copy_whole() -> None:
    from chrono_mail.pim.itip import merged_answer

    series = calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    first = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    second = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261112T100000Z", 1).split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    two = first.replace("END:VCALENDAR", "BEGIN:VEVENT" + second + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    copy = merged_answer(series, two, "me@example.com", "ACCEPTED", read_calendar_message(two).occurrence, take_message=True)  # type: ignore[union-attr]
    assert copy is not None and "RECURRENCE-ID:20261105T100000Z" in copy and "RECURRENCE-ID:20261112T100000Z" in copy


def two_occurrences(method: str, sequence: int, first: str = "20261105T100000Z", second: str = "20261112T100000Z", answer_text: str = "NEEDS-ACTION") -> str:
    one = calendar_text(method=method, sequence=sequence, answer=answer_text).replace(f"SEQUENCE:{sequence}", f"SEQUENCE:{sequence}\r\nRECURRENCE-ID:{first}", 1)
    other = calendar_text(method=method, sequence=sequence, answer=answer_text).replace(f"SEQUENCE:{sequence}", f"SEQUENCE:{sequence}\r\nRECURRENCE-ID:{second}", 1)
    part = other.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    return one.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)


def test_a_cancellation_of_several_occurrences_removes_them_all(setup: Setup) -> None:
    """Step 2 review round 10: only the first cancelled occurrence was removed."""
    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(two_occurrences("CANCEL", 1))))
    (text,) = kept(setup)
    assert "20261105T100000Z" in text and "20261112T100000Z" in text and text.count("EXDATE") == 2


def test_delegating_several_occurrences_names_the_delegate_on_each(setup: Setup) -> None:
    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(two_occurrences("REQUEST", 0))), to="sam@example.net")
    (text,) = kept(setup)
    assert text.count('DELEGATED-TO="mailto:sam@example.net"') == 2


def test_accepting_a_newer_series_keeps_a_declined_date_it_resends() -> None:
    """Step 2 review round 10: Accept overwrote a declined date the new version carried again; Update calendar kept it."""
    from chrono_mail.pim.itip import merged_answer

    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    declined = calendar_text(method="", answer="DECLINED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    part = declined.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    both = series.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    newer = both.replace("SEQUENCE:0", "SEQUENCE:2").replace("PARTSTAT=DECLINED", "PARTSTAT=NEEDS-ACTION").replace("PARTSTAT=ACCEPTED", "PARTSTAT=NEEDS-ACTION")
    newer = newer.replace("BEGIN:VCALENDAR\r\n", "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\n", 1)
    copy = merged_answer(both, newer, "me@example.com", "ACCEPTED", None, take_message=True)
    assert copy is not None and "PARTSTAT=DECLINED" in copy and "PARTSTAT=ACCEPTED" in copy


def test_a_second_delegation_leaves_the_first_one_alone(setup: Setup) -> None:
    """Step 2 review round 11: delegating 12 November to someone else rewrote 5 November to them too."""
    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    first = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    second = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261112T100000Z", 1)
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(first)), to="x@example.net")
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(second)), to="y@example.net")
    (text,) = kept(setup)
    assert text.count('DELEGATED-TO="mailto:x@example.net"') == 1 and text.count('DELEGATED-TO="mailto:y@example.net"') == 1


def test_declining_a_date_removed_already_writes_nothing(setup: Setup) -> None:
    stored(setup, calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nEXDATE:20261105T100000Z\r\nSUMMARY:", 1))
    before = kept(setup)[0]
    old = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    answer(setup, deliver(setup, invitation_mail(old)), "DECLINED", mail=False)
    assert kept(setup)[0] == before


def test_a_shared_item_sent_to_an_alias_can_be_added(setup: Setup) -> None:
    """Step 2 review round 11: Add to calendar failed for mail to an address that is none of your accounts'."""
    shared = calendar_text(method="PUBLISH").replace("mailto:me@example.com", "mailto:alias@example.net")
    raw = invitation_mail(shared).replace(b"To: me@example.com", b"To: alias@example.net", 1)
    result = answer(setup, deliver(setup, raw), "ACCEPTED", local_calendar(setup))
    assert result.stored_in and len(kept(setup)) == 1


def test_delegating_a_series_with_a_moved_date_names_the_delegate_on_the_series(setup: Setup) -> None:
    """Step 2 review round 12: the series stayed without the delegate when the invitation also carried a moved date."""
    series = calendar_text().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    moved = calendar_text().replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1).split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    both = series.replace("END:VCALENDAR", "BEGIN:VEVENT" + moved + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    stored(setup, both.replace("METHOD:REQUEST\r\n", ""))
    call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(both)), to="sam@example.net")
    (text,) = kept(setup)
    assert text.count('DELEGATED-TO="mailto:sam@example.net"') == 2


def test_an_added_date_for_a_copy_of_single_occurrences_asks_for_the_series(setup: Setup) -> None:
    """Step 2 review round 13: Add to calendar was offered and failed when the copy held single occurrences only."""
    from tests.test_engine_invitations import show

    stored(setup, calendar_text(method="").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1))
    added = calendar_text(method="ADD", sequence=1).replace("DTSTART:20261029T100000Z", "DTSTART:20261231T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261231T103000Z")
    message = deliver(setup, invitation_mail(added))
    card = show(setup, message).invitation
    assert card is not None and card.stored_in == ""  # the card offers Request
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    assert result.done == "asked"


def test_the_answer_mail_says_what_your_calendar_keeps(setup: Setup) -> None:
    """Step 2 review round 14: the REPLY accepted a date your calendar kept declined."""
    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    declined = calendar_text(method="", answer="DECLINED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    part = declined.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    both = series.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    stored(setup, both)
    newer = both.replace("SEQUENCE:0", "SEQUENCE:2").replace("PARTSTAT=DECLINED", "PARTSTAT=NEEDS-ACTION").replace("PARTSTAT=ACCEPTED", "PARTSTAT=NEEDS-ACTION")
    newer = newer.replace("BEGIN:VCALENDAR\r\n", "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\n", 1)
    answer(setup, deliver(setup, invitation_mail(newer)), "ACCEPTED")
    (draft,) = drafts(setup)
    part_text = next(each for each in parse_message(draft).attachments if each.content_type == "text/calendar").data.decode()
    assert "PARTSTAT=DECLINED" in part_text and "PARTSTAT=ACCEPTED" in part_text


def test_a_shared_item_kept_already_is_said_to_be_there(setup: Setup) -> None:
    """Step 2 review round 14: Add to calendar on a kept shared item wrote an empty attendee line."""
    shared = calendar_text(method="PUBLISH").replace("ATTENDEE;CN=Me;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com\r\n", "")
    stored(setup, shared.replace("METHOD:PUBLISH\r\n", ""))
    with pytest.raises(UsageError, match="in your calendar already"):
        answer(setup, deliver(setup, invitation_mail(shared)), "ACCEPTED", local_calendar(setup))
    assert "ATTENDEE" not in kept(setup)[0]


def test_a_new_answer_to_a_newer_series_reaches_the_dates_that_followed_it() -> None:
    """Step 2 review round 15: a moved date kept the answer the organizer had on record instead of your new one."""
    from chrono_mail.pim.itip import merged_answer

    series = calendar_text(method="", answer="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    moved = calendar_text(method="", answer="ACCEPTED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    moved = moved.replace("DTSTART:20261029T100000Z", "DTSTART:20261106T100000Z").replace("DTEND:20261029T103000Z", "DTEND:20261106T103000Z")
    part = moved.split("BEGIN:VEVENT", 1)[1].split("END:VCALENDAR")[0]
    both = series.replace("END:VCALENDAR", "BEGIN:VEVENT" + part + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1)
    newer = both.replace("SEQUENCE:0", "SEQUENCE:1").replace("BEGIN:VCALENDAR\r\n", "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\n", 1)
    copy = merged_answer(both, newer, "me@example.com", "TENTATIVE", None, take_message=True)
    assert copy is not None and "PARTSTAT=ACCEPTED" not in copy and copy.count("PARTSTAT=TENTATIVE") == 2


def test_declining_a_newer_date_takes_its_version(setup: Setup) -> None:
    from tests.test_engine_invitations import show

    one = calendar_text(method="", sequence=1, answer="ACCEPTED").replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    stored(setup, one)
    moved = calendar_text(sequence=2).replace("SEQUENCE:2", "SEQUENCE:2\r\nRECURRENCE-ID:20261105T100000Z", 1).replace("DTSTART:20261029T100000Z", "DTSTART:20261106T100000Z")
    message = deliver(setup, invitation_mail(moved))
    answer(setup, message, "DECLINED", mail=False)
    (text,) = kept(setup)
    assert "SEQUENCE:2" in text and "PARTSTAT=DECLINED" in text
    card = show(setup, message).invitation
    assert card is not None and card.version == "same"


def test_a_header_on_several_lines_is_read_whole() -> None:
    """Step 2 review round 16: a server's DAV header on three lines lost calendar-auto-schedule on its second line."""
    from chrono_mail.protocols.dav import _joined_headers  # noqa: PLC2701 - the reading itself

    headers = _joined_headers([("DAV", "1, 2, 3"), ("Content-Type", "text/xml"), ("DAV", "calendar-access, calendar-auto-schedule"), ("dav", "addressbook")])
    assert "calendar-auto-schedule" in headers["dav"] and headers["dav"].endswith("addressbook") and headers["content-type"] == "text/xml"
