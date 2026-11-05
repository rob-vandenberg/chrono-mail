from datetime import UTC, datetime, timedelta
from pathlib import Path

from chrono_mail.core import ANSWER_MARKS, LOCAL_ACCOUNT_ID, CollectionKind, MessageFlag
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.pim.itip import asked_again, kept_mail, read_calendar_message, you_declined
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_declined.py 4.4.558"

# --- Version history ----------------------------------------------------
# v4.4.558: A row says invited, no longer answerable and whole
# v4.4.552: What is declined follows the ticked calendars; a deleted date has no way back and shows nowhere (D-310)
# v4.4.528: Initial tests: a declined invitation stays as a hidden event of this computer, can be answered again from the calendar or deleted (D-310)

DAYS = {"from_date": "2026-10-26", "to_date": "2026-11-02"}
SERIES = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", "METHOD:REQUEST", "BEGIN:VEVENT", "UID:series-1@example.com", "SEQUENCE:0", "DTSTAMP:20261027T150900Z",
                      "DTSTART:20261027T100000Z", "DTEND:20261027T103000Z", "RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20261028T100000Z", "SUMMARY:Daily", "ORGANIZER:mailto:olivia@example.org",
                      "ATTENDEE;PARTSTAT=ACCEPTED:mailto:me@example.com", "END:VEVENT", "END:VCALENDAR"]) + "\r\n"


def with_id(raw: bytes) -> bytes:
    return b"Message-ID: <invite-1@example.org>\r\n" + raw


def decline(setup: Setup, message: str) -> None:
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="DECLINED")


def card(setup: Setup, message: str):  # noqa: ANN201 - the command's own answer
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation
    assert shown is not None
    return shown


def rows(setup: Setup, hidden: bool = False):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "event.list", hidden=hidden, **DAYS).events


def marks(setup: Setup, message: str) -> frozenset[MessageFlag]:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        return mailbox.current_flags(mailbox.reference(mailbox.folder("Inbox"), message)) & ANSWER_MARKS


def second_calendar(setup: Setup) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        return store.create_collection(CollectionKind.EVENTS, "Work", "#112233").collection_id


def test_a_declined_invitation_stays_hidden_on_this_computer_and_the_mark_stays_on_the_mail(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    decline(setup, message)
    assert rows(setup) == () and marks(setup, message) == {MessageFlag.INVITE_DECLINED}
    (row,) = rows(setup, hidden=True)
    assert row.hidden and row.invited and row.account == LOCAL_ACCOUNT_ID
    shown = card(setup, message)
    assert shown.your_answer == "DECLINED"
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert choices.stored == "" and not any(each.holds for each in choices.calendars)  # no appointment: Accept asks for a calendar


def test_a_declined_invitation_with_no_organizer_can_be_answered_again_never_restored(setup: Setup) -> None:
    text = "".join(line + "\r\n" for line in calendar_text().splitlines() if not line.startswith("ORGANIZER"))
    message = deliver(setup, invitation_mail(text))
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="DECLINED", mail=False)
    (row,) = rows(setup, hidden=True)
    assert row.invited


def test_what_is_declined_shows_only_when_its_calendar_is_ticked(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    decline(setup, message)
    other = second_calendar(setup)
    (row,) = rows(setup, hidden=True)
    assert call(setup.engine, "event.list", hidden=True, calendars=(other,), **DAYS).events == ()
    (shown,) = call(setup.engine, "event.list", hidden=True, calendars=(row.collection,), **DAYS).events
    assert shown.hidden


def test_deleting_a_declined_event_really_deletes_it_with_no_mail_and_the_invitation_shows_as_new(setup: Setup) -> None:
    message = deliver(setup, with_id(invitation_mail(calendar_text())))
    decline(setup, message)
    (row,) = rows(setup, hidden=True)
    result = call(setup.engine, "event.delete", item=row.item)
    assert not result.draft.message and rows(setup, hidden=True) == () and marks(setup, message) == frozenset()
    fresh = card(setup, message)
    assert fresh.your_answer != "DECLINED" and not fresh.answered_on_mail


def test_accept_from_the_calendar_makes_it_a_normal_appointment_in_the_calendar_chosen(setup: Setup) -> None:
    message = deliver(setup, with_id(invitation_mail(calendar_text())))
    decline(setup, message)
    other = second_calendar(setup)
    (row,) = rows(setup, hidden=True)
    call(setup.engine, "invitation.answer", account="", folder="", message="", event=row.item, answer="ACCEPTED", calendar=other, mail=False)
    (now,) = rows(setup, hidden=True)
    assert not now.hidden and now.collection == other and marks(setup, message) == frozenset()
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        text = store.get(other, now.item)
    assert kept_mail(text) is None and "PARTSTAT=ACCEPTED" in text and "METHOD" not in text


def test_accept_on_the_mail_into_the_calendar_of_the_copy_changes_the_copy_there(setup: Setup) -> None:
    message = deliver(setup, invitation_mail(calendar_text()))
    decline(setup, message)
    (row,) = rows(setup, hidden=True)
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="TENTATIVE", calendar=row.collection)
    (now,) = rows(setup)
    assert now.item == row.item and not now.hidden


def test_an_invitation_in_a_file_remembers_its_decline_too(setup: Setup, tmp_path: Path) -> None:
    file = tmp_path / "invite.ics"
    file.write_text(calendar_text())
    message = deliver(setup, invitation_mail(calendar_text()))
    decline(setup, message)
    assert call(setup.engine, "invitation.card", account="", folder="", message="", file=str(file)).your_answer == "DECLINED"


def test_a_date_deleted_from_your_own_series_shows_nowhere(setup: Setup) -> None:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (info,) = store.collections(CollectionKind.EVENTS)
        calendar = info.collection_id
    series = call(setup.engine, "event.add", calendar=calendar, summary="Mine", start="2026-10-27T09:00", end="2026-10-27T09:15", time_zone="UTC", rrule="FREQ=DAILY;COUNT=3")
    date = next(row for row in rows(setup) if row.start.startswith("2026-10-28"))
    call(setup.engine, "event.delete", item=series.item, occurrence=date.occurrence)
    assert len(rows(setup, hidden=True)) == 2 and not any(row.hidden for row in rows(setup, hidden=True))


def test_a_date_taken_out_of_a_meeting_is_asked_as_that_date_alone() -> None:
    moment = datetime(2026, 10, 28, 10, tzinfo=UTC)
    message = read_calendar_message(asked_again(SERIES.replace("METHOD:REQUEST\r\n", ""), moment) or "")
    assert message is not None and message.method == "REQUEST" and message.occurrence == moment
    assert message.event is not None and message.event.start == moment and message.event.end == moment + timedelta(minutes=30) and not message.event.repeats


def test_you_declined_looks_at_the_whole_item() -> None:
    assert you_declined(calendar_text(method="", answer="DECLINED"), {"me@example.com"})
    assert not you_declined(calendar_text(method="", answer="ACCEPTED"), {"me@example.com"})
