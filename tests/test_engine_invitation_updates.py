from datetime import UTC, datetime, timedelta

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim import itip
from chrono_mail.pim.ical import MAXIMUM_DATES
from chrono_mail.pim.itip import cancelled_copy, cancelled_in, merged_answer, read_calendar_message, updated_copy
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import card, kept
from tests.test_engine_invitation_tasks import kept_tasks, run, stored_task, task_text
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_updates.py 4.4.555"

# --- Version history ----------------------------------------------------
# v4.4.555: Initial tests: an update is copied into the event you keep, many dates cost no more than their number, a server's reset loses its free mark, your deleted dates are no change of time, a Cancel mail sets Cancelled, what a server did by itself is followed (D-310)

ME = "me@example.com"
WEEKLY = "RRULE:FREQ=WEEKLY;COUNT=4\r\n"
ALARM = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Soon\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
YOURS = "CATEGORIES:Project\r\nCOLOR:teal\r\nX-CHRONO-MAIL-COLOR-ID:7\r\n"
SECOND = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def copy_of(answer: str = "ACCEPTED", extra: str = "", series: bool = True) -> str:
    """Your copy: with your reminder, tag and colour, and `extra` on the main item."""
    return calendar_text(method="", answer=answer).replace("END:VEVENT", f"{WEEKLY if series else ''}{YOURS}{extra}{ALARM}END:VEVENT")


def update(sequence: int = 1, location: str = "Room 2", start: str = "20261029T100000Z", end: str = "20261029T103000Z", series: bool = True, extra: str = "") -> str:
    return (calendar_text(sequence=sequence).replace("Main Street 1, Springfield", location).replace("DTSTART:20261029T100000Z", f"DTSTART:{start}")
            .replace("DTEND:20261029T103000Z", f"DTEND:{end}").replace("END:VEVENT", f"{WEEKLY if series else ''}{extra}END:VEVENT"))


def changed_date(when: str, start: str, end: str, title: str = "My own title", answer: str = "ACCEPTED", extra: str = "") -> str:
    """A date of the series with an item of its own."""
    return (f"BEGIN:VEVENT\r\nUID:meeting-1@example.com\r\nSEQUENCE:0\r\nDTSTAMP:20261027T150900Z\r\nRECURRENCE-ID:{when}\r\nDTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\n"
            f"LOCATION:Main Street 1, Springfield\r\nORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org\r\nATTENDEE;CN=Me;PARTSTAT={answer}:mailto:{ME}\r\n{extra}END:VEVENT\r\n")


def with_date(text: str, date: str) -> str:
    return text.replace("END:VCALENDAR", date + "END:VCALENDAR")


def lines_of(text: str) -> str:
    return text.replace("\r\n ", "")


def date_item(text: str, moment: datetime) -> str:
    """The lines of the item of that date."""
    wanted = f"RECURRENCE-ID:{moment:%Y%m%dT%H%M%SZ}"
    return next(part for part in lines_of(text).split("BEGIN:VEVENT") if wanted in part)


def test_an_update_copies_what_changed_and_leaves_what_is_yours() -> None:
    after = updated_copy(copy_of(extra="EXDATE:20261112T100000Z\r\n"), update(), ME)
    assert after is not None
    text = lines_of(after)
    assert "LOCATION:Room 2" in text and "SEQUENCE:1" in text
    assert "PARTSTAT=ACCEPTED" in text and "TRIGGER:-PT15M" in text and "CATEGORIES:Project" in text and "COLOR:teal" in text and "X-CHRONO-MAIL-COLOR-ID:7" in text
    assert "EXDATE:20261112T100000Z" in text


def test_accept_of_an_update_that_moved_copies_it_in_as_well() -> None:
    after = merged_answer(copy_of(), update(start="20261029T110000Z", end="20261029T113000Z"), ME, "ACCEPTED", None, take_message=True)
    assert after is not None
    text = lines_of(after)
    assert "DTSTART:20261029T110000Z" in text and "PARTSTAT=ACCEPTED" in text and "TRIGGER:-PT15M" in text and "CATEGORIES:Project" in text


def test_accept_of_a_new_version_of_one_date_keeps_the_reminder_of_that_date() -> None:
    kept_copy = with_date(copy_of(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", extra=ALARM))
    one = update(start="20261105T120000Z", end="20261105T123000Z", series=False).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    after = merged_answer(kept_copy, one, ME, "TENTATIVE", SECOND, take_message=True)
    assert after is not None
    item = date_item(after, SECOND)
    assert "DTSTART:20261105T120000Z" in item and "PARTSTAT=TENTATIVE" in item and "TRIGGER:-PT15M" in item


def test_a_new_time_reaches_a_changed_date_and_a_deleted_date_stays_deleted() -> None:
    kept_copy = with_date(copy_of(extra="EXDATE:20261112T100000Z\r\n"), changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z", extra=ALARM))
    after = updated_copy(kept_copy, update(start="20261029T110000Z", end="20261029T120000Z"), ME)
    assert after is not None
    text = lines_of(after)
    assert "EXDATE:20261112T110000Z" in text and "EXDATE:20261112T100000Z" not in text
    item = date_item(text, SECOND.replace(hour=11))
    assert "DTSTART:20261105T110000Z" in item and "DTEND:20261105T120000Z" in item
    assert "My own title" in item and "TRIGGER:-PT15M" in item and "PARTSTAT=NEEDS-ACTION" in item  # a new time asks again, on that date too


def test_what_changed_reaches_a_changed_date_and_what_is_its_own_stays() -> None:
    kept_copy = with_date(copy_of(), changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z", answer="TENTATIVE"))
    after = updated_copy(kept_copy, update(), ME)
    assert after is not None
    item = date_item(after, SECOND)
    assert "LOCATION:Room 2" in item and "SEQUENCE:1" in item
    assert "My own title" in item and "DTSTART:20261105T140000Z" in item and "PARTSTAT=TENTATIVE" in item


def test_a_date_the_organizer_writes_again_takes_their_fields_and_keeps_yours() -> None:
    kept_copy = with_date(copy_of(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", extra=ALARM))
    message = with_date(update(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", title="Their title", answer="NEEDS-ACTION").replace("Main Street 1, Springfield", "Room 9"))
    after = updated_copy(kept_copy, message, ME)
    assert after is not None
    item = date_item(after, SECOND)
    assert "Their title" in item and "LOCATION:Room 9" in item and "TRIGGER:-PT15M" in item and "PARTSTAT=ACCEPTED" in item


def test_a_date_the_organizer_gives_an_item_keeps_the_reminder_it_had_through_the_series() -> None:
    message = with_date(update(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", title="Their title", answer="NEEDS-ACTION"))
    after = updated_copy(copy_of(), message, ME)
    assert after is not None
    item = date_item(after, SECOND)
    assert "TRIGGER:-PT15M" in item and "CATEGORIES:Project" in item and "PARTSTAT=ACCEPTED" in item


def test_a_date_you_deleted_stays_deleted_when_the_organizer_writes_it_again() -> None:
    message = with_date(update(), changed_date("20261112T100000Z", "20261112T100000Z", "20261112T103000Z", title="Their title", answer="NEEDS-ACTION"))
    after = updated_copy(copy_of(extra="EXDATE:20261112T100000Z\r\n"), message, ME)
    assert after is not None and "Their title" not in after and "EXDATE:20261112T100000Z" in lines_of(after)


def test_a_date_the_organizer_removed_joins_your_deleted_dates_and_asks_again() -> None:
    kept_copy = with_date(copy_of(extra="EXDATE:20261112T100000Z\r\n"), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z"))
    after = updated_copy(kept_copy, update(extra="EXDATE:20261105T100000Z\r\n"), ME)
    assert after is not None
    text = lines_of(after)
    removed = "".join(line for line in text.splitlines() if line.startswith("EXDATE"))
    assert "20261112T100000Z" in removed and "20261105T100000Z" in removed
    assert "RECURRENCE-ID" not in text and "PARTSTAT=NEEDS-ACTION" in text


def test_a_changed_date_that_is_no_date_of_the_series_any_more_goes() -> None:
    kept_copy = with_date(copy_of(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z"))
    message = update().replace(WEEKLY, "RRULE:FREQ=MONTHLY;COUNT=4\r\n")
    after = updated_copy(kept_copy, message, ME)
    assert after is not None and "RECURRENCE-ID" not in after and "FREQ=MONTHLY" in after


def test_an_update_of_a_mail_that_names_no_organizer_leaves_the_one_you_stored() -> None:
    message = update().replace("ORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org\r\n", "")
    after = updated_copy(copy_of(), message, ME)
    assert after is not None and "mailto:olivia@example.org" in lines_of(after)


def test_a_field_the_organizer_took_away_goes() -> None:
    after = updated_copy(copy_of(), update().replace("LOCATION:Room 2\r\n", ""), ME)
    assert after is not None and "LOCATION" not in after


def test_your_own_deleted_date_is_no_change_of_time(setup: Setup) -> None:  # noqa: F811
    stored(setup, copy_of(extra="EXDATE:20261112T100000Z\r\n"))
    message = deliver(setup, invitation_mail(update()))
    assert not card(setup, message).answer_again
    assert call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message).done == "updated"
    (text,) = kept(setup)
    assert "PARTSTAT=ACCEPTED" in text and "EXDATE:20261112T100000Z" in lines_of(text)


def test_a_cancel_mail_sets_cancelled_and_the_event_stays_as_it_is(setup: Setup) -> None:  # noqa: F811
    stored(setup, copy_of())
    message = deliver(setup, invitation_mail(calendar_text(method="CANCEL", sequence=1).replace("END:VEVENT", WEEKLY + "END:VEVENT")))
    assert not card(setup, message).kept_cancelled
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    assert result.done == "cancelled"
    (text,) = kept(setup)
    assert "STATUS:CANCELLED" in text and "SEQUENCE:1" in text and "PARTSTAT=ACCEPTED" in text and "TRIGGER:-PT15M" in text and "CATEGORIES:Project" in text
    assert card(setup, message).kept_cancelled


def test_a_cancel_mail_cancels_only_once(setup: Setup) -> None:  # noqa: F811
    stored(setup, copy_of())
    message = deliver(setup, invitation_mail(calendar_text(method="CANCEL", sequence=1)))
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    with pytest.raises(UsageError, match="cancelled in your calendar already"):
        call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)


def test_a_cancel_mail_about_one_date_cancels_that_date_only(setup: Setup) -> None:  # noqa: F811
    stored(setup, copy_of())
    one = calendar_text(method="CANCEL", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    message = deliver(setup, invitation_mail(one))
    result = call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    assert result.done == "cancelled"
    (text,) = kept(setup)
    assert "STATUS:CANCELLED" in date_item(text, SECOND) and text.count("STATUS:CANCELLED") == 1
    assert card(setup, message).kept_cancelled and "EXDATE" not in text


def test_a_cancel_mail_about_a_series_covers_a_changed_date_with_no_status_and_a_date_with_one_keeps_it() -> None:
    kept_copy = with_date(with_date(copy_of(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z")),
                          changed_date("20261112T100000Z", "20261112T100000Z", "20261112T103000Z", extra="STATUS:CONFIRMED\r\n"))
    after = cancelled_copy(kept_copy)
    assert after is not None
    assert "STATUS:CANCELLED" in date_item(after, SECOND)
    assert "STATUS:CONFIRMED" in date_item(after, SECOND.replace(day=12))
    assert cancelled_in(after) and not cancelled_in(after, [SECOND.replace(day=12)]) and cancelled_in(after, [SECOND.replace(day=19)])


def test_a_declined_event_stays_declined_and_hidden_when_a_cancel_mail_is_taken(setup: Setup) -> None:  # noqa: F811
    stored(setup, copy_of(answer="DECLINED"))
    message = deliver(setup, invitation_mail(calendar_text(method="CANCEL", sequence=1)))
    call(setup.engine, "invitation.apply", account=setup.account_id, folder="Inbox", message=message)
    (text,) = kept(setup)
    assert "STATUS:CANCELLED" in text and "PARTSTAT=DECLINED" in text


def test_a_cancel_mail_about_a_task_sets_cancelled_and_the_task_stays(setup: Setup) -> None:  # noqa: F811
    stored_task(setup, task_text(method="", answer="ACCEPTED"))
    assert run(setup, "invitation.apply", deliver(setup, invitation_mail(task_text("CANCEL", sequence=1)))).done == "cancelled"
    (text,) = kept_tasks(setup)
    assert "STATUS:CANCELLED" in text


def test_a_copy_a_server_made_is_answered_in_place_and_no_calendar_is_asked(setup: Setup) -> None:  # noqa: F811
    stored(setup, calendar_text(method=""))
    message = deliver(setup, invitation_mail(calendar_text()))
    shown = card(setup, message)
    assert shown.your_answer == "NEEDS-ACTION" and shown.stored_in
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert choices.stored
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="ACCEPTED", mail=False)
    (text,) = kept(setup)
    assert "PARTSTAT=ACCEPTED" in text


def test_an_event_a_server_reset_shows_as_unanswered_until_you_answer(setup: Setup) -> None:  # noqa: F811
    message = deliver(setup, invitation_mail(calendar_text()))
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        item = store.put(calendar.collection_id, calendar_text(method="", answer="DECLINED"))
    assert card(setup, message).your_answer == "DECLINED"
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.put(calendar.collection_id, calendar_text(method="", answer="NEEDS-ACTION", sequence=0), item)
    assert card(setup, message).your_answer == "NEEDS-ACTION"
    copy = read_calendar_message(kept(setup)[0])
    assert copy is not None and copy.attendees[0].status == "NEEDS-ACTION"


def test_a_date_the_new_version_writes_itself_keeps_the_time_it_gives_when_the_series_moves() -> None:
    kept = with_date(copy_of(), changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z"))
    message = with_date(update(start="20261029T110000Z", end="20261029T113000Z"), changed_date("20261105T110000Z", "20261105T160000Z", "20261105T163000Z", title="Their title"))
    after = updated_copy(kept, message, ME)
    assert after is not None
    item = date_item(after, datetime(2026, 11, 5, 11, 0, tzinfo=UTC))
    assert "DTSTART:20261105T160000Z" in item and "DTEND:20261105T163000Z" in item and "SUMMARY:Their title" in item


def many_dates(count: int, first: int = 0) -> str:
    return "".join(f"EXDATE:{date:%Y%m%dT100000Z}\r\n" for date in (datetime(2026, 11, 12, tzinfo=UTC) + timedelta(days=first + step) for step in range(count)))


def test_thousands_of_removed_dates_are_never_compared_each_with_each(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each list against the other, date by date: tens of seconds of work at 5,000 dates (AGENTS.md 4.2)."""
    compared: list[object] = []
    original = itip._same_moment  # pyright: ignore[reportPrivateUsage]

    def counted(first: object, second: object) -> bool:
        compared.append(first)
        return original(first, second)  # type: ignore[arg-type]

    monkeypatch.setattr(itip, "_same_moment", counted)
    after = updated_copy(copy_of(extra=many_dates(5000)), update(extra=many_dates(5000, 5000)), ME)
    assert after is not None and lines_of(after).count("EXDATE") == 10000 and len(compared) < 1000


def test_a_message_with_endless_removed_dates_is_read_only_as_far_as_the_most() -> None:
    after = updated_copy(copy_of(), update(extra=many_dates(MAXIMUM_DATES + 5)), ME)
    assert after is not None and lines_of(after).count("EXDATE") == MAXIMUM_DATES


def test_many_changed_dates_are_not_read_again_for_each_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """A series that moves: reading the whole series for each changed date took a minute at 400 dates and 2,000 removed dates (AGENTS.md 4.2)."""
    days = [datetime(2026, 11, 5, 10, 0, tzinfo=UTC) + timedelta(days=7 * step) for step in range(50)]
    dates = "".join(changed_date(f"{day:%Y%m%dT%H%M%SZ}", f"{day:%Y%m%dT140000Z}", f"{day:%Y%m%dT143000Z}") for day in days)
    kept = with_date(copy_of(extra=many_dates(5, 1)).replace("COUNT=4", "COUNT=60"), dates)
    read: list[object] = []
    original = itip._parse  # pyright: ignore[reportPrivateUsage]

    def counted(text: str | bytes) -> object:
        read.append(text)
        return original(text)

    monkeypatch.setattr(itip, "_parse", counted)
    after = updated_copy(kept, update(start="20261029T110000Z", end="20261029T113000Z").replace("COUNT=4", "COUNT=60"), ME)
    assert after is not None and lines_of(after).count("RECURRENCE-ID:") == 50 and "RECURRENCE-ID:20261105T110000Z" in lines_of(after) and len(read) < 10


def test_a_changed_date_the_moved_series_no_longer_has_goes_and_the_others_stay() -> None:
    dates = changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z") + changed_date("20261112T100000Z", "20261112T140000Z", "20261112T143000Z")
    after = updated_copy(with_date(copy_of(), dates), update(extra="").replace("COUNT=4", "COUNT=2"), ME)
    assert after is not None and lines_of(after).count("RECURRENCE-ID:") == 1 and "RECURRENCE-ID:20261105T100000Z" in lines_of(after)


RESET = "TRANSP:TRANSPARENT\r\nX-MICROSOFT-CDO-BUSYSTATUS:FREE\r\n"


def test_accept_of_a_copy_a_server_reset_after_your_decline_blocks_your_time_again() -> None:
    """Measured on Kerio: a Decline marks its copy free, a new version resets the answer and keeps that mark, and it keeps what the next save says."""
    after = merged_answer(copy_of("NEEDS-ACTION", extra=RESET), update(sequence=0), ME, "ACCEPTED", None, take_message=False)
    assert after is not None and "TRANSP:OPAQUE" in after and "TRANSPARENT" not in after and "BUSYSTATUS" not in after


def test_an_organizers_free_meeting_stays_free_when_you_accept_it() -> None:
    after = merged_answer(copy_of("NEEDS-ACTION", extra=RESET), update(sequence=0, extra="TRANSP:TRANSPARENT\r\n"), ME, "ACCEPTED", None, take_message=False)
    assert after is not None and "TRANSP:TRANSPARENT" in after


def test_a_copy_you_marked_free_with_no_mark_of_a_server_stays_free_when_you_accept_it() -> None:
    after = merged_answer(copy_of("NEEDS-ACTION", extra="TRANSP:TRANSPARENT\r\n"), update(sequence=0), ME, "ACCEPTED", None, take_message=False)
    assert after is not None and "TRANSP:TRANSPARENT" in after


def test_a_date_at_the_edge_of_the_years_takes_no_other_changed_date_with_it() -> None:
    dates = changed_date("20261029T100000Z", "20261029T140000Z", "20261029T143000Z") + changed_date("99991231T100000Z", "99991231T140000Z", "99991231T143000Z")
    after = updated_copy(with_date(copy_of(extra="").replace(WEEKLY, ""), dates), update(start="20261029T110000Z", end="20261029T113000Z", series=False), ME)
    assert after is not None and "RECURRENCE-ID:20261029T110000Z" in lines_of(after)


def test_a_series_cancelled_with_a_changed_date_still_live_is_not_cancelled_yet() -> None:
    date = changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z")
    assert not cancelled_in(with_date(copy_of(extra="STATUS:CANCELLED\r\n"), date))
    after = cancelled_copy(with_date(copy_of(), date))
    assert after is not None and cancelled_in(after)


def test_a_changed_date_with_a_status_of_its_own_does_not_keep_the_button_of_a_cancelled_series_live() -> None:
    date = changed_date("20261105T100000Z", "20261105T140000Z", "20261105T143000Z", extra="STATUS:CONFIRMED\r\n")
    after = cancelled_copy(with_date(copy_of(), date))
    assert after is not None and cancelled_in(after) and "STATUS:CONFIRMED" in after
