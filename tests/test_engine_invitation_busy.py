from datetime import UTC, datetime, timedelta, timezone

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import BusyPeriod, read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts
from tests.test_engine_invitations import deliver, invitation_mail, show

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_busy.py 4.4.527"

# --- Version history ----------------------------------------------------
# v4.4.527: a long period is answered for all of it (D-310)
# v4.4.507: Initial tests: someone's busy times on the card; a request answered from the calendars you tick, only times, as a draft (D-310 step 4)


def busy_text(method: str = "REQUEST", lines: tuple[str, ...] = (), start: str = "20261102T000000Z", end: str = "20261109T000000Z",
              organizer: str = "olivia@example.org") -> str:
    rows = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", f"METHOD:{method}", "BEGIN:VFREEBUSY", "UID:busy-1@example.com",
            "DTSTAMP:20261028T090000Z", f"DTSTART:{start}", f"DTEND:{end}", f"ORGANIZER;CN=Olivia:mailto:{organizer}",
            "ATTENDEE;CN=Me:mailto:me@example.com", *lines, "END:VFREEBUSY", "END:VCALENDAR"]
    return "\r\n".join(rows) + "\r\n"


def event(uid: str, start: str, end: str, title: str = "Secret plans", extra: tuple[str, ...] = ()) -> str:
    rows = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", "BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20261028T090000Z",
            f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{title}", *extra, "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(rows) + "\r\n"


def calendar_of(setup: Setup, *texts: str) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        for text in texts:
            store.put(calendar.collection_id, text)
        return calendar.collection_id


def answered(setup: Setup, calendar: str, request: str | None = None):  # noqa: ANN201 - the calendar part of the draft
    message = deliver(setup, invitation_mail(request or busy_text()))
    result = call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=message, calendars=(calendar,))
    assert result.account == setup.account_id
    (draft,) = drafts(setup)
    parsed = parse_message(draft)
    part = next(each for each in parsed.attachments if each.content_type == "text/calendar")
    return parsed, part.data


def test_someones_busy_times_are_shown_on_the_card_with_no_answer_to_give(setup: Setup) -> None:
    lines = ("FREEBUSY;FBTYPE=BUSY-TENTATIVE:20261103T100000Z/20261103T110000Z,20261104T100000Z/PT1H", "FREEBUSY:20261105T100000Z/PT1H",
             "FREEBUSY;FBTYPE=FREE:20261106T100000Z/PT1H")
    message = deliver(setup, invitation_mail(busy_text("REPLY", lines)))
    card = show(setup, message).invitation
    assert card is not None and card.kind == "freebusy" and card.stored_in == "" and card.start == datetime(2026, 11, 2, tzinfo=UTC)
    assert [(each.start.day, each.kind) for each in card.busy] == [(3, "BUSY-TENTATIVE"), (4, "BUSY-TENTATIVE"), (5, "BUSY")]  # a free time is no busy time
    assert card.busy[1].end == datetime(2026, 11, 4, 11, tzinfo=UTC)


def test_damaged_busy_times_are_left_out_and_the_number_is_capped() -> None:
    assert read_calendar_message(busy_text("PUBLISH", ("FREEBUSY:nonsense",))) is None  # the text is damaged: the mail is shown without a card
    assert read_calendar_message(busy_text("PUBLISH", ("FREEBUSY:20261105T110000Z/20261105T100000Z",))) is None  # an end before its start: damaged as well
    many = "FREEBUSY:" + ",".join(f"20261105T{hour:02}{minute:02}00Z/PT1M" for hour in range(24) for minute in range(60))
    capped = read_calendar_message(busy_text("PUBLISH", tuple([many] * 5)))
    assert capped is not None and len(capped.busy) == 5000


def test_your_answer_holds_times_only_merged_and_in_utc_as_a_draft_to_the_requester(setup: Setup) -> None:
    calendar = calendar_of(setup, event("a@example.com", "20261103T100000Z", "20261103T110000Z"), event("b@example.com", "20261103T103000Z", "20261103T120000Z"))
    parsed, data = answered(setup, calendar)
    reply = read_calendar_message(data)
    assert reply is not None and reply.method == "REPLY" and reply.kind == "freebusy" and reply.uid == "busy-1@example.com"
    assert reply.busy == (BusyPeriod(datetime(2026, 11, 3, 10, tzinfo=UTC), datetime(2026, 11, 3, 12, tzinfo=UTC), "BUSY"),)
    assert reply.organizer is not None and reply.organizer.address == "olivia@example.org" and [each.address for each in reply.attendees] == ["me@example.com"]
    assert reply.period_start == datetime(2026, 11, 2, tzinfo=UTC) and reply.period_end == datetime(2026, 11, 9, tzinfo=UTC)
    assert [each.address for each in parsed.to_addresses] == ["olivia@example.org"] and parsed.subject == "Busy times"
    assert b"Secret plans" not in data and "Secret plans" not in (parsed.text_body or "") and "busy times of" in (parsed.text_body or "")


def test_free_cancelled_and_declined_times_are_left_out_and_tentative_ones_are_marked(setup: Setup) -> None:
    calendar = calendar_of(
        setup,
        event("free@example.com", "20261103T100000Z", "20261103T110000Z", extra=("TRANSP:TRANSPARENT",)),
        event("gone@example.com", "20261103T120000Z", "20261103T130000Z", extra=("STATUS:CANCELLED",)),
        event("no@example.com", "20261103T140000Z", "20261103T150000Z", extra=("ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com",)),
        event("perhaps@example.com", "20261103T160000Z", "20261103T170000Z", extra=("STATUS:TENTATIVE",)),
        event("maybe@example.com", "20261103T180000Z", "20261103T190000Z", extra=("ATTENDEE;PARTSTAT=TENTATIVE:mailto:me@example.com",)),
    )
    _, data = answered(setup, calendar)
    reply = read_calendar_message(data)
    assert reply is not None and [(each.start.hour, each.kind) for each in reply.busy] == [(16, "BUSY-TENTATIVE"), (18, "BUSY-TENTATIVE")]


def test_a_busy_time_wins_from_a_tentative_one_at_the_same_time(setup: Setup) -> None:
    calendar = calendar_of(setup, event("a@example.com", "20261103T100000Z", "20261103T120000Z", extra=("STATUS:TENTATIVE",)), event("b@example.com", "20261103T110000Z", "20261103T113000Z"))
    _, data = answered(setup, calendar)
    reply = read_calendar_message(data)
    assert reply is not None and [(each.start.minute + 60 * each.start.hour, each.end.minute + 60 * each.end.hour, each.kind) for each in reply.busy] == [
        (600, 660, "BUSY-TENTATIVE"), (660, 690, "BUSY"), (690, 720, "BUSY-TENTATIVE"),
    ]


def test_a_repeating_item_counts_on_each_date_and_a_time_is_cut_at_the_period_asked(setup: Setup) -> None:
    calendar = calendar_of(setup, event("a@example.com", "20261101T230000Z", "20261102T010000Z", extra=("RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20261102T230000Z")))
    _, data = answered(setup, calendar)
    reply = read_calendar_message(data)
    assert reply is not None and [(each.start, each.end) for each in reply.busy] == [
        (datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 2, 1, tzinfo=UTC)), (datetime(2026, 11, 3, 23, tzinfo=UTC), datetime(2026, 11, 4, 1, tzinfo=UTC)),
    ]


def test_a_whole_day_counts_from_midnight_on_this_computer(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chrono_mail.engine.commands_invitations.local_zone", lambda: timezone(timedelta(hours=2)))
    calendar = calendar_of(setup, event("a@example.com", "20261104", "20261105").replace("DTSTART:", "DTSTART;VALUE=DATE:").replace("DTEND:", "DTEND;VALUE=DATE:"))
    _, data = answered(setup, calendar)
    reply = read_calendar_message(data)
    assert reply is not None and [(each.start, each.end) for each in reply.busy] == [(datetime(2026, 11, 3, 22, tzinfo=UTC), datetime(2026, 11, 4, 22, tzinfo=UTC))]


def test_a_long_period_is_answered_for_all_of_it(setup: Setup) -> None:
    _, data = answered(setup, calendar_of(setup), busy_text(end="20391102T000000Z"))
    reply = read_calendar_message(data)
    assert reply is not None and reply.period_end == datetime(2039, 11, 2, tzinfo=UTC) and reply.busy == ()


def test_a_period_that_cannot_be_used_makes_no_answer(setup: Setup) -> None:
    calendar = calendar_of(setup)
    message = deliver(setup, invitation_mail(busy_text(start="20261109T000000Z", end="20261102T000000Z")))
    with pytest.raises(UsageError, match="names no period"):
        call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=message, calendars=(calendar,))
    assert drafts(setup) == []


def test_no_calendar_ticked_or_one_that_is_not_there_makes_no_answer(setup: Setup) -> None:
    calendar_of(setup)
    message = deliver(setup, invitation_mail(busy_text()))
    with pytest.raises(UsageError, match="at least one"):
        call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=message, calendars=())
    with pytest.raises(UsageError, match="could not be read"):  # a time left out would say that you are free
        call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=message, calendars=("0" * 32,))
    assert drafts(setup) == []


def test_only_a_request_for_your_busy_times_can_be_answered(setup: Setup) -> None:
    calendar = calendar_of(setup)
    published = deliver(setup, invitation_mail(busy_text("PUBLISH")))
    with pytest.raises(UsageError, match="does not ask"):
        call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=published, calendars=(calendar,))


def test_your_own_request_is_not_answered(setup: Setup) -> None:
    calendar = calendar_of(setup)
    message = deliver(setup, invitation_mail(busy_text(organizer="me@example.com")))
    with pytest.raises(UsageError, match="yourself"):
        call(setup.engine, "invitation.busy", account=setup.account_id, folder="Inbox", message=message, calendars=(calendar,))


@pytest.mark.parametrize(("command", "more"), [
    ("invitation.answer", {"answer": "ACCEPTED", "mail": False}), ("invitation.apply", {}), ("invitation.delegate", {"to": "pat@example.net"}), ("invitation.organize", {"action": "take"}),
])
def test_busy_times_cannot_be_answered_applied_delegated_or_organized(setup: Setup, command: str, more: dict[str, object]) -> None:
    message = deliver(setup, invitation_mail(busy_text()))
    with pytest.raises(UsageError, match="busy times"):
        call(setup.engine, command, account=setup.account_id, folder="Inbox", message=message, **more)


def test_the_choices_start_from_the_account_that_received_the_request(setup: Setup) -> None:
    calendar = calendar_of(setup)
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=deliver(setup, invitation_mail(busy_text())))
    assert calendar in [each.collection for each in choices.calendars] and choices.receiving == setup.account_id and choices.stored == ""
