from collections.abc import Iterator
from dataclasses import replace

import pytest

from chrono_mail.config import AppConfig
from chrono_mail.core import MessageFlag
from chrono_mail.engine.commands_calendar import OccurrenceInfo
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.ical import cancelled_on, parse_events
from chrono_mail.protocols.dav import DavClient
from chrono_mail.sync.davitems import ItemGateway, ServerRefused
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts, local_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_event_menus.py 4.4.557"

# --- Version history ----------------------------------------------------
# v4.4.557: Delete of a date stays final with event.restore here for Cancelled; a changed date with no status counts as the calendar shows it (D-310)
# v4.4.553: A date in an object of only changed dates is deleted as that date (D-310)
# v4.4.552: Initial tests: what a row of the list knows, what Show declined shows, and Delete: silent on someone else's meeting, one date for good, no mail on a Cancelled meeting (D-310)

DAYS = {"from_date": "2030-03-04", "to_date": "2030-03-08"}
OLIVIA = "ORGANIZER:mailto:olivia@example.org"
ME = "ORGANIZER:mailto:me@example.com"


def calendar_of(*events: list[str]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN"]
    for event in events:
        lines += ["BEGIN:VEVENT", *event, "END:VEVENT"]
    return "\r\n".join([*lines, "END:VCALENDAR"]) + "\r\n"


def single(*people: str) -> str:
    return calendar_of(["UID:single@example.org", "DTSTAMP:20300101T000000Z", "DTSTART:20300304T100000Z", "DTEND:20300304T103000Z", "SUMMARY:Single", *people])


def series(*people: str, dates: tuple[list[str], ...] = ()) -> str:
    """Three days in a row, and changed dates, each with the lines given."""
    common = ["UID:series@example.org", "DTSTAMP:20300101T000000Z", "SUMMARY:Daily"]
    changed = [[*common, f"RECURRENCE-ID:{each[0]}", f"DTSTART:{each[0]}", f"DTEND:{each[0][:11]}103000Z", *each[1:]] for each in dates]
    return calendar_of([*common, "DTSTART:20300304T100000Z", "DTEND:20300304T103000Z", "RRULE:FREQ=DAILY;COUNT=3", *people], *changed)


def me(answer: str) -> str:
    return f"ATTENDEE;PARTSTAT={answer}:mailto:me@example.com"


SAM = "ATTENDEE;PARTSTAT=ACCEPTED:mailto:sam@example.org"


def added(setup: Setup, calendar: str, text: str) -> str:  # noqa: F811
    (item,) = call(setup.engine, "event.import", calendar=calendar, text=text).items
    return item


def rows(setup: Setup, hidden: bool = False, **days: str) -> tuple[OccurrenceInfo, ...]:  # noqa: F811
    return call(setup.engine, "event.list", hidden=hidden, **(days or DAYS)).events


def row_on(setup: Setup, day: str, hidden: bool = False) -> OccurrenceInfo:  # noqa: F811
    return next(row for row in rows(setup, hidden) if row.start.startswith(day))


@pytest.fixture
def kerio(setup: Setup) -> Iterator[FakeDav]:  # noqa: F811 - the fixture of this module's import
    """A calendar server that mails whatever it is told, the way the Kerio test server was measured."""
    with server_for(setup, dav_always_answers=True, dav_addresses=("me@example.com",)) as fake:
        yield fake


@pytest.fixture
def listening(setup: Setup) -> Iterator[FakeDav]:  # noqa: F811
    """A calendar server that mails by itself and can be told not to."""
    with server_for(setup) as fake:
        yield fake


class server_for:  # noqa: N801 - reads as the context manager it is
    def __init__(self, setup: Setup, **values: object) -> None:  # noqa: F811
        self.setup = setup
        self.values = values
        self.fake = FakeDav()

    def __enter__(self) -> FakeDav:
        fake = self.fake.__enter__()
        fake.announces = "calendar-auto-schedule"
        fake.user_addresses = ("mailto:me@example.com",)
        engine, account = self.setup.engine, self.setup.account_id
        call(engine, "account.set-dav", account=account, url=fake.url, username="ann", password="secret")
        call(engine, "dav.sync", account=account)

        def change(config: AppConfig) -> AppConfig:
            return config.with_account(replace(config.find(account), **self.values))  # type: ignore[arg-type]

        engine.change_config(change)
        return fake

    def __exit__(self, *details: object) -> None:
        self.fake.__exit__(*details)  # type: ignore[arg-type]


def server_calendar(setup: Setup) -> str:  # noqa: F811
    return next(item.collection for item in call(setup.engine, "collection.list") if item.kind == "events" and item.account == setup.account_id)


def on_server(fake: FakeDav) -> list[str]:
    return [text for _, text in fake.collections["events"].items.values()]


def writes(fake: FakeDav) -> list[str]:
    return [method for method, _ in fake.requests if method in ("PUT", "DELETE")]


def puts_from_now_on(fake: FakeDav, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fake.requests.clear()
    seen: list[str] = []
    original = DavClient.put

    def watched(client: DavClient, href: str, text: str, *rest: object, **options: object) -> str:
        seen.append(text)
        return original(client, href, text, *rest, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(DavClient, "put", watched)
    return seen


# --- what a row knows (changelist items 2 and 3) ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("people", "invited", "answer"), [
    ((OLIVIA, me("NEEDS-ACTION")), True, "NEEDS-ACTION"),
    ((OLIVIA, me("ACCEPTED")), True, "ACCEPTED"),
    ((OLIVIA, me("TENTATIVE")), True, "TENTATIVE"),
    ((OLIVIA, me("DELEGATED")), True, "DELEGATED"),
    ((OLIVIA, SAM), False, ""),  # names another organizer and does not list you: self-created
    ((ME, me("ACCEPTED"), SAM), False, ""),  # your own meeting, as a server writes it
    ((SAM,), False, ""),  # no organizer
    ((), False, ""),
], ids=["unanswered", "accepted", "tentative", "delegated", "not-listed", "yours", "no-organizer", "plain"])
def test_a_row_says_whether_you_are_invited_and_what_you_answered(setup: Setup, people: tuple[str, ...], invited: bool, answer: str) -> None:  # noqa: F811
    added(setup, local_calendar(setup), single(*people))
    (row,) = rows(setup)
    assert (row.invited, row.answer) == (invited, answer)


def test_a_declined_row_says_so_too(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), single(OLIVIA, me("DECLINED")))
    (row,) = rows(setup, hidden=True)
    assert row.invited and row.answer == "DECLINED" and row.hidden


def test_the_answer_of_a_date_and_of_the_series_and_the_status_are_separate_facts(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("TENTATIVE"), "STATUS:CANCELLED"],)))
    first, second, third = rows(setup)
    assert (first.answer, first.status) == ("ACCEPTED", "") and (third.answer, third.status) == ("ACCEPTED", "")
    assert (second.answer, second.status) == ("TENTATIVE", "CANCELLED")


def test_a_cancelled_series_keeps_your_answer_on_every_date(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), "STATUS:CANCELLED"))
    assert {(row.answer, row.status) for row in rows(setup)} == {("ACCEPTED", "CANCELLED")}


def test_a_row_says_when_its_calendar_is_read_only(setup: Setup) -> None:  # noqa: F811
    with FakeDav(read_only=("events",)) as fake:
        fake.collections["events"].items["one.ics"] = ('"1"', single(OLIVIA, me("ACCEPTED")))
        call(setup.engine, "account.set-dav", account=setup.account_id, url=fake.url, username="ann", password="secret")
        call(setup.engine, "dav.sync", account=setup.account_id)
        added(setup, local_calendar(setup), series())
        assert {row.summary: row.read_only for row in rows(setup)} == {"Single": True, "Daily": False}


# --- Show declined (changelist item 12) ---------------------------------------------------------------------------------------------------


def test_a_date_you_deleted_shows_nowhere_also_in_someone_elses_series(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED")))
    call(setup.engine, "event.delete", item=item, occurrence=row_on(setup, "2030-03-05").occurrence)
    assert len(rows(setup, hidden=True)) == 2 and not any(row.hidden for row in rows(setup, hidden=True))


def test_a_date_the_organizer_removed_shows_nowhere(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), "EXDATE:20300305T100000Z"))
    assert [row.start[:10] for row in rows(setup, hidden=True)] == ["2030-03-04", "2030-03-06"]


def test_show_declined_shows_a_declined_date_and_a_declined_meeting(setup: Setup) -> None:  # noqa: F811
    calendar = local_calendar(setup)
    added(setup, calendar, series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("DECLINED")],)))
    added(setup, calendar, single(OLIVIA, me("DECLINED")))
    assert len(rows(setup)) == 2
    assert sorted(row.summary for row in rows(setup, hidden=True) if row.hidden) == ["Daily", "Single"]


def test_what_is_declined_in_a_calendar_not_ticked_shows_nothing_under_show_declined_too(setup: Setup) -> None:  # noqa: F811
    calendar = local_calendar(setup)
    added(setup, calendar, single(OLIVIA, me("DECLINED")))
    other = call(setup.engine, "collection.create", account=next(item.account for item in call(setup.engine, "collection.list") if item.collection == calendar), kind="events", name="Work").collection
    assert call(setup.engine, "event.list", hidden=True, calendars=(other,), **DAYS).events == ()
    assert len(call(setup.engine, "event.list", hidden=True, calendars=(calendar,), **DAYS).events) == 1


# --- Delete of someone else's meeting (changelist item 19) --------------------------------------------------------------------------------


@pytest.mark.parametrize("people", [(OLIVIA, me("ACCEPTED")), (OLIVIA, me("NEEDS-ACTION")), (OLIVIA, me("DECLINED")), (OLIVIA, SAM)], ids=["accepted", "unanswered", "declined", "not-listed"])
def test_a_whole_invited_event_goes_with_its_organizer_taken_off_first_on_a_server_that_mails_whatever_it_is_told(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch, people: tuple[str, ...]) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(*people))
    sent = puts_from_now_on(kerio, monkeypatch)
    result = call(setup.engine, "event.delete", item=item)
    assert writes(kerio) == ["PUT", "DELETE"] and on_server(kerio) == []
    assert "ORGANIZER" not in sent[0] and "ATTENDEE" not in sent[0]
    assert not result.draft.message and not drafts(setup)


def test_a_whole_invited_event_goes_with_the_server_told_to_stay_silent(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))
    sent = puts_from_now_on(listening, monkeypatch)
    result = call(setup.engine, "event.delete", item=item)
    assert writes(listening) == ["PUT", "DELETE"] and "SCHEDULE-AGENT=CLIENT" in sent[0].replace("\r\n ", "")
    assert not result.draft.message and not drafts(setup)


def test_a_whole_invited_series_goes_the_silent_way_too(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("TENTATIVE")],)))
    sent = puts_from_now_on(kerio, monkeypatch)
    call(setup.engine, "event.delete", item=item)
    assert "ORGANIZER" not in sent[0] and "ATTENDEE" not in sent[0] and on_server(kerio) == []


def test_an_appointment_of_your_own_is_deleted_straight_away(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single())
    kerio.requests.clear()
    call(setup.engine, "event.delete", item=item)
    assert writes(kerio) == ["DELETE"]


def test_the_mark_of_a_copy_kept_in_local_folders_still_comes_off(setup: Setup) -> None:  # noqa: F811
    raw = b"Message-ID: <invite-1@example.org>\r\n" + invitation_mail(calendar_text())
    message = deliver(setup, raw)
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="DECLINED", mail=False)
    (row,) = call(setup.engine, "event.list", hidden=True, from_date="2026-10-26", to_date="2026-11-02").events
    call(setup.engine, "event.delete", item=row.item)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert MessageFlag.INVITE_DECLINED not in mailbox.current_flags(mailbox.reference(mailbox.folder("Inbox"), message))


def test_one_date_of_an_invited_series_tells_a_listening_server_to_stay_silent(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), series(OLIVIA, me("ACCEPTED")))
    moment = row_on(setup, "2030-03-05").occurrence
    sent = puts_from_now_on(listening, monkeypatch)
    result = call(setup.engine, "event.delete", item=item, occurrence=moment)
    assert writes(listening) == ["PUT"] and "EXDATE" in sent[0] and "SCHEDULE-AGENT=CLIENT" in sent[0].replace("\r\n ", "")
    assert not result.draft.message and not drafts(setup)


def test_one_date_on_a_server_that_mails_whatever_it_is_told_is_one_plain_write_and_no_mail_from_here(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), series(OLIVIA, me("ACCEPTED")))
    moment = row_on(setup, "2030-03-05").occurrence
    sent = puts_from_now_on(kerio, monkeypatch)
    result = call(setup.engine, "event.delete", item=item, occurrence=moment)
    assert writes(kerio) == ["PUT"] and "ORGANIZER" in sent[0] and "SCHEDULE-AGENT" not in sent[0].replace("\r\n ", "")
    assert not result.draft.message and not drafts(setup)


# --- Delete when something goes wrong (AGENTS.md 4.1.4) -----------------------------------------------------------------------------------


def test_offline_the_delete_of_an_invited_event_waits_and_stays_silent_when_the_connection_is_back(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))
    kerio.writes_failing = True
    call(setup.engine, "event.delete", item=item)
    assert rows(setup) == () and len(on_server(kerio)) == 1  # gone here at once, still on the server
    kerio.writes_failing = False
    sent = puts_from_now_on(kerio, monkeypatch)
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(kerio) == ["PUT", "DELETE"] and on_server(kerio) == [] and "ORGANIZER" not in sent[0]


def test_interrupted_after_the_copy_went_only_the_delete_is_left_for_next_time(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))
    kerio.writes_failing = True
    call(setup.engine, "event.delete", item=item)
    kerio.writes_failing = False
    original = DavClient.delete

    def busy(client: DavClient, href: str, etag: str) -> None:
        kerio.writes_failing = True  # the copy went, the delete does not
        try:
            original(client, href, etag)
        finally:
            kerio.writes_failing = False

    monkeypatch.setattr(DavClient, "delete", busy)
    call(setup.engine, "dav.sync", account=setup.account_id)
    monkeypatch.setattr(DavClient, "delete", original)
    assert len(on_server(kerio)) == 1 and "ORGANIZER" not in on_server(kerio)[0]
    kerio.requests.clear()
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert writes(kerio) == ["DELETE"] and on_server(kerio) == []


def test_a_refused_delete_leaves_the_invited_event_as_it_was(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))

    def refused(*_: object) -> None:
        raise ServerRefused("not allowed")

    monkeypatch.setattr(ItemGateway, "delete", refused)
    with pytest.raises(UsageError):
        call(setup.engine, "event.delete", item=item)
    assert "ORGANIZER" in on_server(kerio)[0] and "ATTENDEE" in on_server(kerio)[0]
    (row,) = rows(setup)
    assert row.invited and row.answer == "ACCEPTED"


def test_interrupted_between_the_two_steps_online_nothing_is_lost_and_delete_can_be_given_again(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))

    class Stopped(BaseException):
        pass

    def stopped(*_: object) -> None:
        raise Stopped

    with monkeypatch.context() as patch:
        patch.setattr(ItemGateway, "delete", stopped)
        with pytest.raises(Stopped):
            call(setup.engine, "event.delete", item=item)
    assert len(on_server(kerio)) == 1 and len(rows(setup)) == 1
    call(setup.engine, "event.delete", item=item)
    assert on_server(kerio) == [] and rows(setup) == ()


# --- one date for good, declined dates (changelist item 20) -------------------------------------------------------------------------------


def test_a_deleted_date_cannot_be_restored(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series())
    moment = row_on(setup, "2030-03-05").occurrence
    call(setup.engine, "event.delete", item=item, occurrence=moment)
    with pytest.raises(UsageError, match="not in this event"):
        call(setup.engine, "event.restore", item=item, occurrence=moment)


def test_a_declined_date_is_deleted_as_that_one_date_and_the_series_stays(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("DECLINED")],)))
    declined = next(row for row in rows(setup, hidden=True) if row.hidden)
    assert not declined.whole and declined.occurrence
    call(setup.engine, "event.delete", item=item, occurrence=declined.occurrence)
    assert [row.start[:10] for row in rows(setup, hidden=True)] == ["2030-03-04", "2030-03-06"]
    shown = call(setup.engine, "event.show", item=item)
    assert not shown.changed_occurrences


def only_dates(first: str = "ACCEPTED", second: str = "ACCEPTED", *lines: str) -> str:
    """An invitation to two dates of someone else's series: the object holds no main event."""
    common = ["UID:dates@example.org", "DTSTAMP:20300101T000000Z", "SUMMARY:Two dates", OLIVIA]
    return calendar_of(
        [*common, "RECURRENCE-ID:20300305T100000Z", "DTSTART:20300305T100000Z", "DTEND:20300305T103000Z", me(first), *lines],
        [*common, "RECURRENCE-ID:20300306T100000Z", "DTSTART:20300306T100000Z", "DTEND:20300306T103000Z", me(second)],
    )


def test_each_date_of_an_object_of_only_changed_dates_has_its_own_row_date(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), only_dates("DECLINED"))
    first, second = rows(setup, hidden=True)
    assert first.hidden and not first.whole and first.occurrence.startswith("2030-03-05")
    assert not second.hidden and second.occurrence.startswith("2030-03-06")


@pytest.mark.parametrize(("day", "left"), [("2030-03-05", "2030-03-06"), ("2030-03-06", "2030-03-05")], ids=["first", "second"])
def test_a_date_of_an_object_of_only_changed_dates_is_deleted_as_that_one_date(setup: Setup, day: str, left: str) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), only_dates())
    call(setup.engine, "event.delete", item=item, occurrence=row_on(setup, day).occurrence)
    assert [row.start[:10] for row in rows(setup, hidden=True)] == [left]


def test_a_declined_date_of_such_an_object_is_deleted_as_that_date_and_the_other_stays(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), only_dates("DECLINED"))
    call(setup.engine, "event.delete", item=item, occurrence=row_on(setup, "2030-03-05", hidden=True).occurrence)
    assert [(row.start[:10], row.hidden) for row in rows(setup, hidden=True)] == [("2030-03-06", False)]


def test_the_last_date_of_such_an_object_takes_the_object_along_the_silent_way(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), only_dates())
    call(setup.engine, "event.delete", item=item, occurrence=row_on(setup, "2030-03-05").occurrence)
    sent = puts_from_now_on(kerio, monkeypatch)
    call(setup.engine, "event.delete", item=item, occurrence=row_on(setup, "2030-03-06").occurrence)
    assert writes(kerio) == ["PUT", "DELETE"] and "ORGANIZER" not in sent[0] and on_server(kerio) == [] and rows(setup) == ()


def test_a_date_not_in_such_an_object_is_refused_and_a_single_event_still_does_not_repeat(setup: Setup) -> None:  # noqa: F811
    calendar = local_calendar(setup)
    item = added(setup, calendar, only_dates())
    with pytest.raises(UsageError, match="not in this event"):
        call(setup.engine, "event.delete", item=item, occurrence="2030-03-09T10:00:00+00:00")
    plain = added(setup, calendar, single())
    with pytest.raises(UsageError, match="does not repeat"):
        call(setup.engine, "event.delete", item=plain, occurrence="2030-03-04T10:00:00+00:00")


def test_an_answer_on_a_declined_date_of_such_an_object_changes_that_date_only(setup: Setup) -> None:  # noqa: F811
    added(setup, local_calendar(setup), only_dates("DECLINED", "TENTATIVE"))
    row = row_on(setup, "2030-03-05", hidden=True)
    call(setup.engine, "invitation.answer", account="", folder="", message="", event=row.item, occurrence=row.occurrence, answer="ACCEPTED", calendar=row.collection, mail=False)
    assert [row.answer for row in rows(setup)] == ["ACCEPTED", "TENTATIVE"]


def test_the_status_of_a_date_in_such_an_object_is_its_own(setup: Setup) -> None:  # noqa: F811
    series_of = parse_events(only_dates("ACCEPTED", "ACCEPTED", "STATUS:CANCELLED"))[0]
    first, second = series_of.overrides
    assert cancelled_on(series_of, first.recurrence_id) and not cancelled_on(series_of, second.recurrence_id) and not cancelled_on(series_of, None)


# --- Delete of a Cancelled meeting (changelist item 21) -----------------------------------------------------------------------------------


def cancelled_meeting(setup: Setup, calendar: str) -> str:  # noqa: F811
    return added(setup, calendar, single(ME, SAM, "STATUS:CANCELLED"))


def test_a_cancelled_meeting_asks_nothing_and_makes_no_mail(setup: Setup) -> None:  # noqa: F811
    item = cancelled_meeting(setup, local_calendar(setup))
    mailing = call(setup.engine, "event.mailing", item=item, deleting=True)
    assert mailing.cancelled and not mailing.chrono
    assert not call(setup.engine, "event.delete", item=item, tell=True).draft.message and not drafts(setup)


def test_a_meeting_not_cancelled_still_asks_and_makes_its_cancellation(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, SAM))
    mailing = call(setup.engine, "event.mailing", item=item, deleting=True)
    assert mailing.chrono and not mailing.cancelled
    assert call(setup.engine, "event.delete", item=item, tell=True).draft.message


def test_saving_a_cancelled_meeting_still_asks(setup: Setup) -> None:  # noqa: F811
    item = cancelled_meeting(setup, local_calendar(setup))
    mailing = call(setup.engine, "event.mailing", item=item)
    assert mailing.chrono and not mailing.cancelled


def test_a_server_that_mails_whatever_it_is_told_is_followed_at_the_delete_of_a_cancelled_meeting(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = cancelled_meeting(setup, server_calendar(setup))
    kerio.requests.clear()
    result = call(setup.engine, "event.delete", item=item, tell=True)
    assert writes(kerio) == ["DELETE"] and not result.draft.message  # the organizer stays on it: the server mails its cancellation itself


def test_a_server_that_listens_is_told_to_stay_silent_at_the_delete_of_a_cancelled_meeting(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = cancelled_meeting(setup, server_calendar(setup))
    sent = puts_from_now_on(listening, monkeypatch)
    call(setup.engine, "event.delete", item=item, tell=True)
    assert writes(listening) == ["PUT", "DELETE"] and "SCHEDULE-AGENT=CLIENT" in sent[0].replace("\r\n ", "")


@pytest.mark.parametrize(("lines", "date_lines", "cancelled"), [
    (("STATUS:CANCELLED",), (), True),  # the series is Cancelled, the date follows it
    ((), ("STATUS:CANCELLED",), True),  # only the date
    (("STATUS:CANCELLED",), ("STATUS:CONFIRMED",), False),  # a date with its own status keeps it
    (("STATUS:CANCELLED",), ("LOCATION:Room 2",), False),  # a changed date with no status shows as not Cancelled, as one restored on its own
    ((), (), False),
], ids=["series", "date", "own-status", "changed-date", "none"])
def test_one_date_of_your_meeting_goes_by_the_status_of_that_date(setup: Setup, lines: tuple[str, ...], date_lines: tuple[str, ...], cancelled: bool) -> None:  # noqa: F811
    dates = (["20300305T100000Z", ME, SAM, *date_lines],) if date_lines else ()
    item = added(setup, local_calendar(setup), series(ME, SAM, *lines, dates=dates))
    moment = row_on(setup, "2030-03-05").occurrence
    mailing = call(setup.engine, "event.mailing", item=item, deleting=True, occurrence=moment)
    assert mailing.cancelled is cancelled and mailing.chrono is not cancelled
    assert bool(call(setup.engine, "event.delete", item=item, occurrence=moment, tell=True).draft.message) is not cancelled


def test_one_cancelled_date_does_not_make_the_whole_series_cancelled(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(ME, SAM, dates=(["20300305T100000Z", ME, SAM, "STATUS:CANCELLED"],)))
    assert not call(setup.engine, "event.mailing", item=item, deleting=True).cancelled
