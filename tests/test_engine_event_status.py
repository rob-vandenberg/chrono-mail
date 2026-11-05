import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_calendar import OccurrenceInfo
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.ical import parse_events
from chrono_mail.sync.davitems import ItemGateway, ServerRefused
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_event_menus import ME, OLIVIA, SAM, added, kerio, listening, me, on_server, puts_from_now_on, row_on, rows, series, server_calendar, single, writes  # noqa: F401 - kerio and listening are fixtures
from tests.test_engine_invitation_answers import drafts, local_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_event_status.py 4.4.557"

# --- Version history ----------------------------------------------------
# v4.4.557: Initial tests: Cancel and Restore of an event, of your own meeting and of your copy of someone else's, and what a Cancelled event refuses (D-310)

MIDDLE = "2030-03-05"
ACCEPTED = "ATTENDEE;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:sam@example.org"


def plain(raw: bytes) -> str:
    """A draft as text with its parts decoded: the calendar part is what an attendee's program reads."""
    import email  # noqa: PLC0415
    from email import policy  # noqa: PLC0415

    message = email.message_from_bytes(raw, policy=policy.default)
    parts = [str(message["Subject"])]
    for part in message.walk():
        if part.get_content_maintype() == "text" or part.get_content_type() == "application/ics":
            parts.append(part.get_payload(decode=True).decode("utf-8", "replace").replace("\r\n ", ""))  # type: ignore[union-attr]
    return "\n".join(parts)


def stored(setup: Setup, item: str) -> str:  # noqa: F811
    return call(setup.engine, "event.export", item=item).text.replace("\r\n ", "")


def date_row(setup: Setup) -> OccurrenceInfo:  # noqa: F811
    return row_on(setup, MIDDLE)


def statuses(setup: Setup) -> list[str]:  # noqa: F811
    return [row.status for row in sorted(rows(setup), key=lambda row: row.start)]


# --- Cancel, self-created (changelist item 13) --------------------------------------------------------------------------------------------


def test_cancel_of_your_own_event_sets_cancelled_and_mails_nothing(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single())
    result = call(setup.engine, "event.cancel", item=item, tell=True)
    (row,) = rows(setup)
    assert row.cancelled and result.status == "CANCELLED" and not result.draft.message and not drafts(setup)


def test_cancel_of_one_date_leaves_the_other_dates(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series())
    call(setup.engine, "event.cancel", item=item, occurrence=date_row(setup).occurrence)
    assert statuses(setup) == ["", "CANCELLED", ""]


def test_cancel_goes_to_the_server_first(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single())
    kerio.requests.clear()
    call(setup.engine, "event.cancel", item=item)
    assert writes(kerio) == ["PUT"] and "STATUS:CANCELLED" in on_server(kerio)[0]


def test_offline_cancel_shows_here_at_once_and_goes_when_the_connection_is_back(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single())
    kerio.writes_failing = True
    call(setup.engine, "event.cancel", item=item)
    assert rows(setup)[0].cancelled and "STATUS:CANCELLED" not in on_server(kerio)[0]
    kerio.writes_failing = False
    call(setup.engine, "dav.sync", account=setup.account_id)
    assert "STATUS:CANCELLED" in on_server(kerio)[0]


def test_a_refused_cancel_leaves_the_meeting_as_it_was_and_makes_no_mail(setup: Setup, kerio: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM))

    def refused(*_: object) -> None:
        raise ServerRefused("not allowed")

    monkeypatch.setattr(ItemGateway, "save", refused)
    with pytest.raises(UsageError, match="not allowed"):
        call(setup.engine, "event.cancel", item=item, tell=True)
    assert not rows(setup)[0].cancelled and not drafts(setup)


# --- Cancel of your own meeting (changelist item 14) --------------------------------------------------------------------------------------


def test_cancel_of_your_meeting_makes_a_real_cancellation_of_a_higher_version(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, SAM, "SEQUENCE:2"))
    result = call(setup.engine, "event.cancel", item=item, tell=True)
    (mail,) = [plain(raw) for raw in drafts(setup)]
    assert result.draft.message and "Cancelled: Single" in mail and "METHOD:CANCEL" in mail and "STATUS:CANCELLED" in mail and "SEQUENCE:3" in mail and "Changed" not in mail
    assert "SEQUENCE:3" in stored(setup, item)


def test_cancel_of_one_date_of_your_meeting_mails_that_date_only(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(ME, SAM))
    call(setup.engine, "event.cancel", item=item, occurrence=date_row(setup).occurrence, tell=True)
    (mail,) = [plain(raw) for raw in drafts(setup)]
    assert "METHOD:CANCEL" in mail and "RECURRENCE-ID" in mail and "RRULE" not in mail and "One date of Daily is cancelled" in mail and "SEQUENCE:1" in mail
    assert statuses(setup) == ["", "CANCELLED", ""]


def test_dont_send_sets_cancelled_with_no_mail_and_the_same_version(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, SAM, "SEQUENCE:2"))
    assert not call(setup.engine, "event.cancel", item=item).draft.message
    assert rows(setup)[0].cancelled and not drafts(setup) and "SEQUENCE:2" in stored(setup, item)


def test_on_a_server_that_mails_whatever_it_is_told_chrono_mail_mails_the_cancellation(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM))
    assert not call(setup.engine, "event.mailing", item=item).chrono  # (an edit: the server mails)
    assert call(setup.engine, "event.mailing", item=item, status=True).chrono
    kerio.requests.clear()
    assert call(setup.engine, "event.cancel", item=item, tell=True).draft.message
    assert writes(kerio) == ["PUT"] and "METHOD:CANCEL" in plain(drafts(setup)[0])


def test_on_a_server_that_listens_the_server_mails_and_chrono_mail_makes_no_mail(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM))
    assert not call(setup.engine, "event.mailing", item=item, status=True).chrono
    sent = puts_from_now_on(listening, monkeypatch)
    assert not call(setup.engine, "event.cancel", item=item, tell=True).draft.message
    assert "SCHEDULE-AGENT=CLIENT" not in sent[-1].replace("\r\n ", "")


def test_dont_send_on_a_server_that_listens_tells_it_to_stay_silent(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM))
    sent = puts_from_now_on(listening, monkeypatch)
    call(setup.engine, "event.cancel", item=item)
    assert "SCHEDULE-AGENT=CLIENT" in sent[-1].replace("\r\n ", "")


def test_a_meeting_with_no_organizer_yet_gets_you_as_its_organizer(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(SAM))
    call(setup.engine, "event.cancel", item=item, tell=True)
    assert "ORGANIZER" in stored(setup, item) and "ORGANIZER" in plain(drafts(setup)[0])


# --- Cancel, invited (changelist item 15) -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("people", [(OLIVIA, me("ACCEPTED")), (OLIVIA, SAM)], ids=["invited", "another-organizer-not-listing-you"])
def test_cancel_of_someone_elses_meeting_changes_your_copy_only(setup: Setup, people: tuple[str, ...]) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(*people, "SEQUENCE:4"))
    uid = rows(setup)[0].uid
    assert not call(setup.engine, "event.cancel", item=item, tell=True).draft.message
    text = stored(setup, item)
    assert rows(setup)[0].cancelled and rows(setup)[0].uid == uid and "SEQUENCE:4" in text and not drafts(setup)


def test_cancel_of_an_invited_event_tells_a_listening_server_to_stay_silent(setup: Setup, listening: FakeDav, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(OLIVIA, me("ACCEPTED")))
    sent = puts_from_now_on(listening, monkeypatch)
    call(setup.engine, "event.cancel", item=item)
    assert "SCHEDULE-AGENT=CLIENT" in sent[-1].replace("\r\n ", "")


def test_cancel_keeps_your_answer(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(OLIVIA, me("TENTATIVE")))
    call(setup.engine, "event.cancel", item=item)
    (row,) = rows(setup)
    assert row.cancelled and row.answer == "TENTATIVE" and item == row.item


# --- Cancel of a whole series (changelist item 16) ----------------------------------------------------------------------------------------


def test_cancel_of_a_series_covers_a_changed_date_with_no_status_and_leaves_one_with_its_own(setup: Setup) -> None:  # noqa: F811
    changed = (["20300305T100000Z", "LOCATION:Room 2"], ["20300306T100000Z", "STATUS:CONFIRMED"])
    item = added(setup, local_calendar(setup), series(dates=changed))
    call(setup.engine, "event.cancel", item=item)
    assert statuses(setup) == ["CANCELLED", "CANCELLED", "CONFIRMED"]


# --- Restore (changelist item 17) ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("before", ["STATUS:CONFIRMED", "STATUS:TENTATIVE", ""], ids=["confirmed", "tentative", "none"])
def test_restore_leaves_no_status_whatever_it_had_before(setup: Setup, before: str) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(*([before] if before else [])))
    call(setup.engine, "event.cancel", item=item) if not before else call(setup.engine, "event.import", calendar=local_calendar(setup), text=single("STATUS:CANCELLED"))
    if before:
        item = next(row.item for row in rows(setup) if row.cancelled)
    result = call(setup.engine, "event.restore", item=item)
    assert result.status == "" and "STATUS" not in stored(setup, item)


def test_restore_of_one_date_leaves_the_other_cancelled_dates(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series())
    for row in rows(setup):
        call(setup.engine, "event.cancel", item=item, occurrence=row.occurrence)
    call(setup.engine, "event.restore", item=item, occurrence=date_row(setup).occurrence)
    assert statuses(setup) == ["CANCELLED", "", "CANCELLED"]


def test_restore_of_one_date_of_a_cancelled_series_brings_back_that_date(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series())
    call(setup.engine, "event.cancel", item=item)
    call(setup.engine, "event.restore", item=item, occurrence=date_row(setup).occurrence)
    assert statuses(setup) == ["CANCELLED", "", "CANCELLED"]
    call(setup.engine, "event.cancel", item=item)  # All does all: the series is Cancelled, that date had no status
    assert statuses(setup) == ["CANCELLED", "CANCELLED", "CANCELLED"]


def test_restore_of_all_takes_cancelled_off_every_date(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(dates=(["20300305T100000Z", "STATUS:CANCELLED"],)))
    call(setup.engine, "event.restore", item=item)
    assert statuses(setup) == ["", "", ""]


def test_restore_of_your_meeting_sends_a_new_version_and_the_attendees_answer_again(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, ACCEPTED, me("ACCEPTED"), "SEQUENCE:2", "STATUS:CANCELLED"))
    result = call(setup.engine, "event.restore", item=item, tell=True)
    (mail,) = [plain(raw) for raw in drafts(setup)]
    assert result.draft.message and "Restored: Single" in mail and "METHOD:REQUEST" in mail and "SEQUENCE:3" in mail and "STATUS:CANCELLED" not in mail
    assert "PARTSTAT=NEEDS-ACTION" in mail
    (master,) = [series_of.master for series_of in parse_events(stored(setup, item))]
    assert {person.address: person.status for person in master.attendees} == {"sam@example.org": "NEEDS-ACTION", "me@example.com": "ACCEPTED"} and master.sequence == 3  # you stay as you are


def test_restore_of_one_date_of_your_meeting_asks_again_for_that_date_only(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(ME, ACCEPTED, dates=(["20300305T100000Z", ME, ACCEPTED, "STATUS:CANCELLED"],)))
    call(setup.engine, "event.restore", item=item, occurrence=date_row(setup).occurrence, tell=True)
    (mail,) = [plain(raw) for raw in drafts(setup)]
    assert "METHOD:REQUEST" in mail and "RRULE" not in mail and "One date of Daily is on again" in mail
    (parsed,) = parse_events(stored(setup, item))
    assert parsed.master.attendees[0].status == "ACCEPTED" and parsed.overrides[0].attendees[0].status == "NEEDS-ACTION" and parsed.overrides[0].status == ""


def test_dont_send_restores_with_no_mail_and_the_answers_stay(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, ACCEPTED, "SEQUENCE:2", "STATUS:CANCELLED"))
    assert not call(setup.engine, "event.restore", item=item).draft.message
    text = stored(setup, item)
    assert "STATUS" not in text and "SEQUENCE:2" in text and "PARTSTAT=ACCEPTED" in text and not drafts(setup)


def test_on_a_server_that_mails_whatever_it_is_told_chrono_mail_mails_the_restore(setup: Setup, kerio: FakeDav) -> None:  # noqa: F811
    item = added(setup, server_calendar(setup), single(ME, SAM, "STATUS:CANCELLED"))
    assert call(setup.engine, "event.mailing", item=item, status=True).chrono
    assert call(setup.engine, "event.restore", item=item, tell=True).draft.message


def test_restore_of_an_invited_event_changes_your_copy_only_also_when_the_organizer_cancelled_it(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(OLIVIA, me("ACCEPTED"), "SEQUENCE:4", "STATUS:CANCELLED"))
    assert not call(setup.engine, "event.restore", item=item, tell=True).draft.message
    (row,) = rows(setup)
    assert not row.cancelled and row.answer == "ACCEPTED" and "SEQUENCE:4" in stored(setup, item) and not drafts(setup)


# --- What a Cancelled event refuses (changelist item 18) ----------------------------------------------------------------------------------


def test_a_cancelled_event_takes_no_change_until_it_is_restored(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single())
    call(setup.engine, "event.cancel", item=item)
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "event.edit", item=item, summary="New title")
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "event.edit", item=item, status="confirmed")
    call(setup.engine, "event.restore", item=item)
    assert call(setup.engine, "event.edit", item=item, summary="New title").summary == "New title"


def test_a_cancelled_date_takes_no_change_and_the_rest_of_the_series_does(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series())
    moment = date_row(setup).occurrence
    call(setup.engine, "event.cancel", item=item, occurrence=moment)
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "event.edit", item=item, occurrence=moment, summary="New title")
    assert call(setup.engine, "event.edit", item=item, location="Room 3").location == "Room 3"


def test_a_cancelled_event_is_not_moved(setup: Setup) -> None:  # noqa: F811
    calendar = local_calendar(setup)
    other = call(setup.engine, "collection.create", account=LOCAL_ACCOUNT_ID, kind="events", name="Second").collection
    item = added(setup, calendar, single("STATUS:CANCELLED"))
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "event.move", item=item, calendar=other)


def test_status_cancelled_goes_only_through_cancel(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(ME, SAM))
    with pytest.raises(UsageError, match=r"event\.cancel"):
        call(setup.engine, "event.edit", item=item, status="cancelled", tell=True)
    assert not rows(setup)[0].cancelled and not drafts(setup)


def test_a_cancelled_event_can_still_be_deleted(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single())
    call(setup.engine, "event.cancel", item=item)
    call(setup.engine, "event.delete", item=item)
    assert rows(setup) == ()


def test_cancel_is_refused_when_cancelled_and_restore_when_not(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single())
    with pytest.raises(UsageError, match="not cancelled"):
        call(setup.engine, "event.restore", item=item)
    call(setup.engine, "event.cancel", item=item)
    with pytest.raises(UsageError, match="cancelled already"):
        call(setup.engine, "event.cancel", item=item)


def test_a_declined_event_cannot_be_cancelled_and_a_cancelled_declined_one_can_be_restored(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(OLIVIA, me("DECLINED")))
    with pytest.raises(UsageError, match="declined"):
        call(setup.engine, "event.cancel", item=item)
    cancelled = added(setup, local_calendar(setup), single(OLIVIA, me("DECLINED"), "STATUS:CANCELLED").replace("single@example.org", "other@example.org"))
    call(setup.engine, "event.restore", item=cancelled)
    assert not any(row.cancelled for row in rows(setup, hidden=True))


def test_a_read_only_calendar_refuses_both(setup: Setup) -> None:  # noqa: F811
    with FakeDav(read_only=("events",)) as fake:
        fake.collections["events"].items["one.ics"] = ('"1"', single("STATUS:CANCELLED"))
        fake.collections["events"].items["two.ics"] = ('"2"', single().replace("single@example.org", "two@example.org"))
        call(setup.engine, "account.set-dav", account=setup.account_id, url=fake.url, username="ann", password="secret")
        call(setup.engine, "dav.sync", account=setup.account_id)
        for row in rows(setup):
            with pytest.raises(UsageError, match="read-only"):
                call(setup.engine, "event.restore" if row.cancelled else "event.cancel", item=row.item)


def test_a_date_that_is_no_date_of_the_event_is_refused(setup: Setup) -> None:  # noqa: F811
    single_item = added(setup, local_calendar(setup), single())
    with pytest.raises(UsageError, match="does not repeat"):
        call(setup.engine, "event.cancel", item=single_item, occurrence="2030-03-04T10:00:00+00:00")
    item = added(setup, local_calendar(setup), series())
    moment = date_row(setup).occurrence
    call(setup.engine, "event.delete", item=item, occurrence=moment)
    with pytest.raises(UsageError, match="not in this event"):
        call(setup.engine, "event.cancel", item=item, occurrence=moment)


# --- answers on a Cancelled event (changelist item 18, OR-4) ------------------------------------------------------------------------------


def accepted_and_cancelled(setup: Setup) -> tuple[str, str]:  # noqa: F811
    message = deliver(setup, invitation_mail(calendar_text()))
    calendar = local_calendar(setup)
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="ACCEPTED", calendar=calendar, mail=False)
    days = {"from_date": "2026-10-29", "to_date": "2026-10-30"}
    (row,) = call(setup.engine, "event.list", **days).events
    call(setup.engine, "event.cancel", item=row.item)
    return message, row.item


@pytest.mark.parametrize("answer", ["ACCEPTED", "TENTATIVE", "DECLINED"])
def test_a_cancelled_event_takes_no_answer(setup: Setup, answer: str) -> None:  # noqa: F811
    message, _ = accepted_and_cancelled(setup)
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer=answer, mail=False)


def test_a_cancelled_event_takes_no_delegate(setup: Setup) -> None:  # noqa: F811
    message, _ = accepted_and_cancelled(setup)
    with pytest.raises(UsageError, match="restore it first"):
        call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.org")


def test_after_restore_it_takes_an_answer_again(setup: Setup) -> None:  # noqa: F811
    message, item = accepted_and_cancelled(setup)
    call(setup.engine, "event.restore", item=item)
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="TENTATIVE", mail=False)
    assert "PARTSTAT=TENTATIVE" in stored(setup, item)


def test_a_newer_version_of_the_organizer_can_be_answered_and_replaces_your_cancel(setup: Setup) -> None:  # noqa: F811
    _, item = accepted_and_cancelled(setup)
    newer = deliver(setup, invitation_mail(calendar_text(sequence=1, stamp="20261028T150900Z")))
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=newer, answer="ACCEPTED", mail=False)
    assert "STATUS" not in stored(setup, item)


def test_the_card_knows_the_event_is_cancelled(setup: Setup) -> None:  # noqa: F811
    message, item = accepted_and_cancelled(setup)
    assert call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation.kept_cancelled
    call(setup.engine, "event.restore", item=item)
    assert not call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation.kept_cancelled
