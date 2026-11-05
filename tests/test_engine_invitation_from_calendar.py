"""Answers from the calendar: what goes out, which event changes, and the mail they belong to (D-310)."""
import email
import email.policy
from collections.abc import Iterator

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import commands_invitations
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.pim.itip import read_calendar_message, shared_text
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts, local_calendar
from tests.test_engine_invitation_mailing import calendar_id, settings
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_from_calendar.py 4.4.556"

# --- Version history ----------------------------------------------------
# v4.4.556: Initial tests (D-310)

SERIES = calendar_text().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
PRIVATE = ("My private title", "T120000Z", "VALARM", "Private tag", "SCHEDULE-AGENT")


@pytest.fixture
def server(setup: Setup) -> Iterator[FakeDav]:  # noqa: F811 - the fixture
    with FakeDav() as fake:
        fake.announces = "calendar-auto-schedule"
        fake.user_addresses = ("mailto:me@example.com",)
        call(setup.engine, "account.set-dav", account=setup.account_id, url=fake.url, username="ann", password="secret")
        call(setup.engine, "dav.sync", account=setup.account_id)
        settings(setup, invitation_answers="chrono")
        yield fake


def edited(setup: Setup, text: str = SERIES) -> tuple[str, str]:  # noqa: F811 - the fixture
    """An accepted meeting you changed for yourself: the mail, and the event."""
    message = deliver(setup, invitation_mail(text))
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    item = call(setup.engine, "event.list", from_date="2026-10-29", to_date="2026-11-30").events[0].item
    call(setup.engine, "event.edit", item=item, summary="My private title", start="2026-10-29T12:00", end="2026-10-29T12:30", reminder=("15", "email:60"), add_tag=("Private tag",))
    return message, item


def from_calendar(setup: Setup, item: str, what: str, occurrence: str = ""):  # noqa: ANN201, F811 - the command's own answer
    return call(setup.engine, "invitation.answer", account="", folder="", message="", event=item, occurrence=occurrence, answer=what, mail=True)


def delegated(setup: Setup, item: str, occurrence: str = ""):  # noqa: ANN201, F811 - the command's own answer
    return call(setup.engine, "invitation.delegate", account="", folder="", message="", event=item, occurrence=occurrence, to="dan@example.org", mail=True)


def gone(setup: Setup, message: str) -> None:  # noqa: F811 - the fixture
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.delete(mailbox.reference(mailbox.folder("Inbox"), message), True)


def new_drafts(setup: Setup, before: list[bytes]) -> list[tuple[str, str, str]]:  # noqa: F811 - the fixture
    """To, Subject and calendar text of the drafts made since."""
    made: list[tuple[str, str, str]] = []
    for raw in drafts(setup):
        if raw not in before:
            mail = email.message_from_bytes(raw, policy=email.policy.default)
            part = next(each for each in mail.walk() if each.get_content_type() == "text/calendar")
            data = part.get_payload(decode=True)
            assert isinstance(data, bytes)
            made.append((str(mail["To"]), str(mail["Subject"]), data.decode().replace("\r\n ", "")))
    return made


def flags_of(setup: Setup, message: str) -> frozenset[MessageFlag]:  # noqa: F811 - the fixture
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.current_flags(mailbox.reference(mailbox.folder("Inbox"), message))


@pytest.mark.parametrize("what", ["ACCEPTED", "TENTATIVE", "DECLINED"])
def test_an_answer_says_what_the_organizer_wrote_and_nothing_you_changed(setup: Setup, server: FakeDav, what: str) -> None:  # noqa: F811 - the fixture
    _, item = edited(setup)
    before = drafts(setup)
    from_calendar(setup, item, what)
    ((to, subject, text),) = new_drafts(setup, before)
    assert "olivia@example.org" in to and subject.endswith(": Choice of colours")
    assert "SUMMARY:Choice of colours" in text and "DTSTART:20261029T100000Z" in text and f"PARTSTAT={what}" in text
    assert not [each for each in PRIVATE if each in text]


def test_a_delegate_gets_the_meeting_and_nothing_you_changed(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    _, item = edited(setup)
    before = drafts(setup)
    delegated(setup, item)
    made = new_drafts(setup, before)
    assert sorted(to.split("<")[-1] for to, _, _ in made) == ["dan@example.org", "olivia@example.org>"]
    for _, subject, text in made:
        assert subject.endswith(": Choice of colours") and "SUMMARY:Choice of colours" in text and "DTSTART:20261029T100000Z" in text
        assert not [each for each in PRIVATE if each in text]


@pytest.mark.parametrize("delegate", [False, True], ids=["answer", "delegate"])
def test_a_date_of_a_series_you_moved_is_named_as_the_organizers_date(setup: Setup, server: FakeDav, delegate: bool) -> None:  # noqa: F811 - the fixture
    _, item = edited(setup)
    moment = call(setup.engine, "event.list", from_date="2026-11-05", to_date="2026-11-07").events[0].occurrence
    before = drafts(setup)
    if delegate:
        delegated(setup, item, moment)
    else:
        from_calendar(setup, item, "TENTATIVE", moment)
    made = new_drafts(setup, before)
    assert made and all("RECURRENCE-ID:20261105T100000Z" in text and "DTSTART:20261105T100000Z" in text for _, _, text in made)
    assert not [each for _, _, text in made for each in PRIVATE if each in text]


def test_with_the_mail_gone_an_answer_names_the_meeting_only_by_its_id(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    gone(setup, message)
    before = drafts(setup)
    from_calendar(setup, item, "TENTATIVE")
    ((_, subject, text),) = new_drafts(setup, before)
    item_text = text[text.index("BEGIN:VEVENT"):]
    assert subject == "Tentative" and "SUMMARY" not in item_text and "DTSTART" not in item_text
    assert "UID:meeting-1@example.com" in text and "PARTSTAT=TENTATIVE" in text and not [each for each in PRIVATE if each in text]
    read = read_calendar_message(text)
    assert read is not None and read.method == "REPLY"  # the organizer's program can read it


def test_with_the_mail_gone_a_delegate_gets_no_reminder_tag_or_colour_and_the_organizer_no_title(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    gone(setup, message)
    before = drafts(setup)
    delegated(setup, item)
    made = {to.split("@")[0].split("<")[-1]: (subject, text) for to, subject, text in new_drafts(setup, before)}
    subject, text = made["olivia"]
    assert subject == "Delegated" and "SUMMARY" not in text and "SCHEDULE-AGENT" not in text
    _, text = made["dan"]
    assert not [each for each in ("VALARM", "Private tag", "SCHEDULE-AGENT", "X-CHRONO") if each in text]


def test_what_is_yours_alone_is_not_shared() -> None:
    own = "TRANSP:TRANSPARENT\r\nX-CHRONO-MAIL-COLOR-ID:3\r\nCOLOR:red\r\nCATEGORIES:Private tag\r\nX-MICROSOFT-CDO-BUSYSTATUS:FREE\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Soon\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
    text = shared_text(calendar_text(answer="ACCEPTED").replace("END:VEVENT", own + "END:VEVENT"))
    assert text is not None and "SUMMARY:Choice of colours" in text and "LOCATION" in text
    assert not [each for each in ("TRANSP", "X-", "COLOR", "CATEGORIES", "VALARM") if each in text]


def test_a_mail_of_another_version_says_nothing_and_gets_no_mark(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    gone(setup, message)
    newer = deliver(setup, invitation_mail(calendar_text(sequence=1, title="Newer")))
    before = drafts(setup)
    from_calendar(setup, item, "ACCEPTED")
    ((_, subject, text),) = new_drafts(setup, before)
    assert subject == "Accepted" and "SUMMARY" not in text
    assert MessageFlag.REPLIED not in flags_of(setup, newer)


def test_a_mail_that_names_another_organizer_is_not_the_invitation(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup, SERIES)
    gone(setup, message)
    other = invitation_mail(SERIES.replace("mailto:olivia@example.org", "mailto:mallory@example.net"))
    forged = deliver(setup, other)
    before = drafts(setup)
    from_calendar(setup, item, "ACCEPTED")
    assert new_drafts(setup, before)[0][1] == "Accepted" and MessageFlag.REPLIED not in flags_of(setup, forged)


def test_a_copy_of_two_dates_answered_as_a_whole_answers_both_also_with_the_mail_of_one(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    one = calendar_text().replace("SUMMARY:", "RECURRENCE-ID:20261029T100000Z\r\nSUMMARY:", 1)
    deliver(setup, invitation_mail(one))
    two = calendar_text(answer="ACCEPTED", method="").replace("SUMMARY:", "RECURRENCE-ID:20261029T100000Z\r\nSUMMARY:", 1)
    second = two[two.index("BEGIN:VEVENT"):two.index("END:VCALENDAR")].replace("20261029T1", "20261105T1")
    item = call(setup.engine, "event.import", calendar=calendar_id(setup), text=two.replace("END:VCALENDAR", second + "END:VCALENDAR")).items[0]
    before = drafts(setup)
    from_calendar(setup, item, "TENTATIVE")
    ((_, _, text),) = new_drafts(setup, before)
    assert text.count("BEGIN:VEVENT") == 2 and "RECURRENCE-ID:20261105T100000Z" in text


def test_a_copy_of_one_date_answered_as_a_whole_finds_the_mail_of_that_date(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    one = calendar_text().replace("SUMMARY:", "RECURRENCE-ID:20261029T100000Z\r\nSUMMARY:", 1)
    message = deliver(setup, invitation_mail(one))
    item = call(setup.engine, "event.import", calendar=calendar_id(setup), text=one.replace("METHOD:REQUEST\r\n", "")).items[0]
    before = drafts(setup)
    from_calendar(setup, item, "TENTATIVE")
    assert new_drafts(setup, before)[0][1] == "Tentative: Choice of colours"


def test_only_the_newest_mails_of_the_organizer_are_read(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the fixture
    _, item = edited(setup)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), b"From: Olivia <OLIVIA@example.org>\r\nTo: me@example.com\r\nDate: Mon, 1 Nov 2027 10:00:00 +0000\r\nSubject: Lunch\r\n\r\nNoon?\r\n", frozenset())
    monkeypatch.setattr(commands_invitations, "MAILS_LOOKED_AT", 1)
    before = drafts(setup)
    from_calendar(setup, item, "ACCEPTED")
    assert new_drafts(setup, before)[0][1] == "Accepted"


def test_the_mail_gets_its_replied_mark_when_the_answer_is_sent(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        reference = mailbox.reference(mailbox.folder("Inbox"), message)
        mailbox.set_flags(reference, mailbox.current_flags(reference) - {MessageFlag.REPLIED})
    result = from_calendar(setup, item, "TENTATIVE")
    assert MessageFlag.REPLIED not in flags_of(setup, message)  # it has not gone yet
    call(setup.engine, "calendar.mail-send", account=setup.account_id, folder=result.draft_folder, message=result.draft_message, note="", delay_seconds=0)
    assert MessageFlag.REPLIED in flags_of(setup, message)


def test_a_server_that_mails_the_answer_gives_the_mail_its_replied_mark_at_once(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    settings(setup, invitation_answers="server")
    from_calendar(setup, item, "TENTATIVE")
    assert MessageFlag.REPLIED in flags_of(setup, message)


def test_a_mark_of_your_decline_goes_from_the_mail_when_you_answer_from_the_calendar(setup: Setup, server: FakeDav) -> None:  # noqa: F811 - the fixture
    message, item = edited(setup)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        reference = mailbox.reference(mailbox.folder("Inbox"), message)
        mailbox.set_flags(reference, mailbox.current_flags(reference) | {MessageFlag.INVITE_DECLINED})
    from_calendar(setup, item, "ACCEPTED")
    assert MessageFlag.INVITE_DECLINED not in flags_of(setup, message)


def two_copies(setup: Setup) -> tuple[str, str, str, str]:  # noqa: F811 - the fixture
    """The same meeting in two calendars of yours: each calendar and its event."""
    first = local_calendar(setup)
    second = call(setup.engine, "collection.create", kind="events", name="Second").collection
    copy = calendar_text(method="", answer="ACCEPTED")
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return first, store.put(first, copy), second, store.put(second, copy)


def answer_of(setup: Setup, calendar: str, item: str) -> str:  # noqa: F811 - the fixture
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        read = read_calendar_message(store.get(calendar, item))
    assert read is not None
    return read.attendees[0].status


@pytest.mark.parametrize("meant", [0, 1], ids=["first", "second"])
def test_an_answer_changes_the_event_right_clicked_and_not_the_other_copy(setup: Setup, meant: int) -> None:  # noqa: F811 - the fixture
    ensure_local_pim(setup.engine)
    first, one, second, other = two_copies(setup)
    from_calendar(setup, (one, other)[meant], "TENTATIVE")
    assert [answer_of(setup, first, one), answer_of(setup, second, other)] == [["TENTATIVE", "ACCEPTED"], ["ACCEPTED", "TENTATIVE"]][meant]


def test_a_delegation_changes_the_event_right_clicked_and_not_the_other_copy(setup: Setup) -> None:  # noqa: F811 - the fixture
    ensure_local_pim(setup.engine)
    first, one, second, other = two_copies(setup)
    delegated(setup, other)
    assert [answer_of(setup, first, one), answer_of(setup, second, other)] == ["ACCEPTED", "DELEGATED"]


def test_the_calendars_offered_know_the_copy_right_clicked(setup: Setup) -> None:  # noqa: F811 - the fixture
    ensure_local_pim(setup.engine)
    _, _, second, other = two_copies(setup)
    choices = call(setup.engine, "invitation.choices", account="", folder="", message="", event=other)
    assert choices.stored == second
