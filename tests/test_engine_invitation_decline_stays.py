from datetime import datetime
from typing import Any

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import commands_invitations
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.ical import occurrences
from chrono_mail.pim.itip import keeps_busy, kept_mail, merged_answer
from chrono_mail.pim.times import local_zone
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import answer, drafts
from tests.test_engine_invitation_declined import SERIES, card, marks, rows, second_calendar, with_id
from tests.test_engine_invitation_mailing import calendar_id, calendar_items, invited, on_server, reopen, server, settings, writes  # noqa: F401 - server is a fixture
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_decline_stays.py 4.4.554"

# --- Version history ----------------------------------------------------
# v4.4.554: Initial tests: a declined event stays hidden in the calendar it is in, Change my answer leaves it there, the mark only for Local Folders, an answer on all dates, busy again, Delegate from Local Folders (D-310)

ME = "me@example.com"
YEAR = {"from_date": "2026-06-01", "to_date": "2027-06-01"}
SERVER_FREE = "TRANSP:TRANSPARENT\r\nX-MICROSOFT-CDO-BUSYSTATUS:FREE\r\n"
DATE = "20261029T100000Z"
ONE_DATE = SERIES.replace("RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261028T100000Z\r\n", "").replace("DTSTART:20261027T100000Z", f"RECURRENCE-ID:{DATE}\r\nDTSTART:{DATE}").replace("DTEND:20261027T103000Z", "DTEND:20261029T103000Z")


def _kept_text(setup: Setup, item: str, account: str) -> str:  # noqa: F811
    with open_pim(setup.engine, account) as store:
        found = store.index.get(item)
        assert found is not None
        return store.get(found.collection, item)


def _delete(setup: Setup, item: str, occurrence: str = "") -> None:  # noqa: F811
    call(setup.engine, "event.delete", item=item, occurrence=occurrence)


def _series_accepted(setup: Setup) -> str:  # noqa: F811
    message = deliver(setup, invitation_mail(SERIES))
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    return message


def test_a_declined_event_stays_hidden_in_its_server_calendar_server_first(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    (shown,) = call(setup.engine, "event.list", **YEAR).events
    server.requests.clear()
    answer(setup, message, "DECLINED")
    assert writes(server) == ["PUT"] and "PARTSTAT=DECLINED" in on_server(server)[0]
    assert call(setup.engine, "event.list", **YEAR).events == ()
    (hidden,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    assert hidden.hidden and hidden.item == shown.item and hidden.collection == calendar_id(setup) and hidden.account == setup.account_id


def test_a_copy_that_stays_keeps_what_you_added_to_it(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    (row,) = call(setup.engine, "event.list", **YEAR).events
    text = _kept_text(setup, row.item, setup.account_id)
    call(setup.engine, "event.edit", item=row.item, location="My own room")
    answer(setup, message, "DECLINED")
    kept = _kept_text(setup, row.item, setup.account_id)
    assert "My own room" in kept and "My own room" not in text and "PARTSTAT=DECLINED" in kept


def test_an_invite_with_no_copy_anywhere_goes_to_local_folders_and_nothing_to_the_server(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    server.requests.clear()
    answer(setup, message, "DECLINED")
    assert writes(server) == []
    (row,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    assert row.account == LOCAL_ACCOUNT_ID


def test_change_my_answer_leaves_a_declined_event_where_it_is(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    answer(setup, message, "DECLINED")
    kept, there = calendar_items(setup), on_server(server)
    server.requests.clear()
    result = reopen(setup, message)
    assert result.stays and not result.removed and writes(server) == []
    assert calendar_items(setup) == kept and on_server(server) == there and card(setup, message).your_answer == "DECLINED"


def test_after_change_my_answer_accept_answers_in_place_with_no_calendar_asked(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    answer(setup, message, "DECLINED")
    reopen(setup, message)
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert choices.stored == calendar_id(setup) and not choices.declined_here
    answer(setup, message, "ACCEPTED")
    assert "PARTSTAT=ACCEPTED" in on_server(server)[0] and len(on_server(server)) == 1


def test_change_my_answer_on_a_copy_in_local_folders_changes_nothing_either(setup: Setup) -> None:  # noqa: F811
    message = deliver(setup, with_id(invitation_mail(calendar_text())))
    answer(setup, message, "DECLINED")
    (row,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    result = reopen(setup, message)
    assert result.stays and marks(setup, message) == {MessageFlag.INVITE_DECLINED}
    assert call(setup.engine, "event.list", **YEAR, hidden=True).events == (row,)


def test_change_my_answer_on_a_decline_only_the_mail_remembers_takes_the_mark_off(setup: Setup) -> None:  # noqa: F811
    """A declined task leaves no copy: the mark is all there is, and the answers come back as before."""
    task = calendar_text().replace("VEVENT", "VTODO").replace("DTEND:", "DUE:")
    message = deliver(setup, invitation_mail(task))
    answer(setup, message, "DECLINED")
    assert marks(setup, message) == {MessageFlag.INVITE_DECLINED}
    assert not reopen(setup, message).stays and marks(setup, message) == frozenset()


def test_a_decline_in_a_server_calendar_leaves_no_mark_and_delete_brings_the_answers_back(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    answer(setup, message, "DECLINED")
    assert marks(setup, message) == frozenset() and card(setup, message).your_answer == "DECLINED"
    (row,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    _delete(setup, row.item)
    fresh = card(setup, message)
    assert fresh.stored_in == "" and fresh.your_answer != "DECLINED" and not fresh.answered_on_mail


def test_a_decline_of_a_copy_in_a_calendar_of_local_folders_marks_the_mail_and_delete_takes_it_off(setup: Setup) -> None:  # noqa: F811
    message = deliver(setup, with_id(invitation_mail(calendar_text())))
    answer(setup, message, "ACCEPTED", second_calendar(setup))
    (row,) = call(setup.engine, "event.list", **YEAR).events
    call(setup.engine, "event.edit", item=row.item, location="My own room")
    answer(setup, message, "DECLINED")
    kept = _kept_text(setup, row.item, LOCAL_ACCOUNT_ID)
    assert marks(setup, message) == {MessageFlag.INVITE_DECLINED} and kept_mail(kept) is not None and "My own room" in kept
    _delete(setup, row.item)
    assert marks(setup, message) == frozenset() and not card(setup, message).answered_on_mail


def test_a_date_declined_in_a_copy_in_local_folders_has_no_mark_so_delete_of_the_date_brings_the_answers_back(setup: Setup) -> None:  # noqa: F811
    series = deliver(setup, invitation_mail(SERIES))
    answer(setup, series, "ACCEPTED", second_calendar(setup))
    message = deliver(setup, invitation_mail(ONE_DATE))
    answer(setup, message, "DECLINED")
    assert marks(setup, message) == frozenset() and card(setup, message).your_answer == "DECLINED"
    (hidden,) = (row for row in rows(setup, hidden=True) if row.hidden)
    _delete(setup, hidden.item, hidden.occurrence)
    assert card(setup, message).your_answer != "DECLINED" or not card(setup, message).occurrence_kept
    assert not card(setup, message).answered_on_mail


def test_a_decline_that_writes_nothing_keeps_its_mark(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """A date that is not in your copy any more: Decline adds nothing, so only the mail can remember it."""
    _series_accepted(setup)
    first = next(row for row in rows(setup))
    _delete(setup, first.item, "2026-10-29T10:00:00+00:00")
    message = deliver(setup, invitation_mail(ONE_DATE))
    server.requests.clear()
    answer(setup, message, "DECLINED")
    assert writes(server) == [] and marks(setup, message) == {MessageFlag.INVITE_DECLINED}


def test_a_declined_date_stays_in_its_series_in_the_server_calendar_with_no_mark(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    _series_accepted(setup)
    message = deliver(setup, invitation_mail(ONE_DATE))
    answer(setup, message, "DECLINED")
    assert marks(setup, message) == frozenset() and len(on_server(server)) == 1
    assert len(rows(setup)) == 1 and [row.hidden for row in rows(setup, hidden=True)].count(True) == 1


@pytest.mark.parametrize("given", ["ACCEPTED", "TENTATIVE", "DECLINED"])
def test_an_answer_on_all_occurrences_reaches_a_date_answered_on_its_own(setup: Setup, server: FakeDav, given: str) -> None:  # noqa: F811
    series = _series_accepted(setup)
    separate = "TENTATIVE" if given == "DECLINED" else "DECLINED"
    answer(setup, deliver(setup, invitation_mail(ONE_DATE)), separate)
    answer(setup, series, given)
    assert on_server(server)[0].count(f"PARTSTAT={given}") == 2


def test_accept_of_a_whole_series_brings_a_declined_date_back_in_the_calendar(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    series = _series_accepted(setup)
    answer(setup, deliver(setup, invitation_mail(ONE_DATE)), "DECLINED")
    assert len(rows(setup)) == 1
    answer(setup, series, "TENTATIVE")
    assert len(rows(setup)) == 2 and not any(row.hidden for row in rows(setup, hidden=True))


def _server_free(text: str) -> str:
    return text.replace("SUMMARY:", f"{SERVER_FREE}SUMMARY:", 1)


def _blocks(text: str) -> bool:
    (first, *_) = occurrences([text], datetime(2026, 1, 1, tzinfo=local_zone()), datetime(2027, 12, 31, tzinfo=local_zone()), local_zone())
    return keeps_busy(first.event, frozenset({ME}))


@pytest.mark.parametrize("given", ["ACCEPTED", "TENTATIVE"])
def test_accept_and_tentative_on_a_declined_event_make_it_busy_again(given: str) -> None:
    """Measured on Kerio: after a Decline it writes TRANSPARENT and its own free mark, and keeps them after an Accept (T2)."""
    copy = _server_free(calendar_text(method="", answer="DECLINED"))
    assert not _blocks(copy)
    again = merged_answer(copy, calendar_text(), ME, given, None, take_message=False)
    assert again is not None and "TRANSP:OPAQUE" in again and "TRANSPARENT" not in again and "BUSYSTATUS" not in again and _blocks(again)


def test_a_date_accepted_out_of_a_declined_series_is_busy_and_the_series_stays_free() -> None:
    copy = _server_free(SERIES.replace("METHOD:REQUEST\r\n", "").replace("PARTSTAT=ACCEPTED", "PARTSTAT=DECLINED"))
    again = merged_answer(copy, _server_free(ONE_DATE), ME, "ACCEPTED", datetime.fromisoformat("2026-10-29T10:00:00+00:00"), take_message=True)
    assert again is not None and again.count("TRANSP:OPAQUE") == 1 and again.count("TRANSP:TRANSPARENT") == 1


def test_a_free_event_you_never_declined_stays_free_after_an_answer() -> None:
    """Only what a Decline left is taken back: the organizer's own free meeting stays free."""
    copy = calendar_text(method="", answer="TENTATIVE").replace("SUMMARY:", "TRANSP:TRANSPARENT\r\nSUMMARY:", 1)
    again = merged_answer(copy, calendar_text(), ME, "ACCEPTED", None, take_message=False)
    assert again is not None and "TRANSP:TRANSPARENT" in again


def test_decline_and_delegate_leave_the_free_mark_alone() -> None:
    copy = _server_free(calendar_text(method="", answer="DECLINED"))
    for given in ("DECLINED", "DELEGATED"):
        again = merged_answer(copy, calendar_text(), ME, given, None, take_message=False)
        assert again is not None and "TRANSP:TRANSPARENT" in again


def test_accept_on_a_declined_event_in_a_server_calendar_warns_of_what_you_have_at_that_time(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    """What you declined blocks nothing: Accept may meet an appointment, as for a new invitation."""
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    answer(setup, message, "DECLINED")
    other = calendar_text(method="", answer="ACCEPTED").replace("UID:", "UID:other-", 1).replace("ORGANIZER", "X-ORGANIZER", 1).replace("SUMMARY:Choice of colours", "SUMMARY:Dentist")
    call(setup.engine, "event.import", calendar=calendar_id(setup), text=other)
    choices = call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message)
    assert choices.clashes == ("Dentist",)


def _declined_in_local_folders(setup: Setup) -> tuple[str, Any]:  # noqa: F811
    message = deliver(setup, with_id(invitation_mail(calendar_text())))
    answer(setup, message, "DECLINED")
    (row,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    return message, row


def _delegate(setup: Setup, message: str, calendar: str = "") -> Any:  # noqa: ANN401, F811 - the command's own answer
    return call(setup.engine, "invitation.delegate", account=setup.account_id, folder="Inbox", message=message, to="sam@example.org", calendar=calendar)


def test_delegate_on_a_declined_event_in_local_folders_needs_a_calendar(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message, row = _declined_in_local_folders(setup)
    assert call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message).declined_here
    before = len(drafts(setup))
    with pytest.raises(UsageError, match="Choose the calendar"):
        _delegate(setup, message)
    assert call(setup.engine, "event.list", **YEAR, hidden=True).events == (row,) and len(drafts(setup)) == before


def test_delegate_moves_a_declined_event_from_local_folders_to_the_calendar_chosen(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message, _ = _declined_in_local_folders(setup)
    _delegate(setup, message, calendar_id(setup))
    (row,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    assert row.account == setup.account_id and not row.hidden and "PARTSTAT=DELEGATED" in on_server(server)[0] and "sam@example.org" in on_server(server)[0]
    assert marks(setup, message) == {MessageFlag.INVITE_DELEGATED}


def test_delegate_into_the_calendar_of_local_folders_it_is_in_changes_that_copy(setup: Setup) -> None:  # noqa: F811
    message, row = _declined_in_local_folders(setup)
    _delegate(setup, message, row.collection)
    (now,) = call(setup.engine, "event.list", **YEAR, hidden=True).events
    assert now.item == row.item and "PARTSTAT=DELEGATED" in _kept_text(setup, now.item, LOCAL_ACCOUNT_ID)


def test_delegate_on_a_declined_event_in_a_server_calendar_asks_no_calendar(setup: Setup, server: FakeDav) -> None:  # noqa: F811
    message = invited(setup)
    answer(setup, message, "ACCEPTED", calendar_id(setup))
    answer(setup, message, "DECLINED")
    assert not call(setup.engine, "invitation.choices", account=setup.account_id, folder="Inbox", message=message).declined_here
    _delegate(setup, message)
    assert "PARTSTAT=DELEGATED" in on_server(server)[0] and len(on_server(server)) == 1


@pytest.mark.parametrize("action", ["accept", "delegate"])
def test_the_copy_in_local_folders_goes_only_after_the_new_copy_is_written(setup: Setup, server: FakeDav, monkeypatch: pytest.MonkeyPatch, action: str) -> None:  # noqa: F811
    """AGENTS.md 4.1.4, interrupted: the step that removes the old copy fails, and the meeting is in the calendar chosen and still in Local Folders."""
    message, row = _declined_in_local_folders(setup)

    def interrupted(*_: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(commands_invitations, "_leave_local_folders", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        if action == "accept":
            answer(setup, message, "ACCEPTED", calendar_id(setup))
        else:
            _delegate(setup, message, calendar_id(setup))
    assert len(on_server(server)) == 1
    assert row.item in {each.item for each in call(setup.engine, "event.list", **YEAR, hidden=True).events}


def test_delegate_to_a_calendar_that_does_not_exist_leaves_local_folders_alone(setup: Setup) -> None:  # noqa: F811
    message, row = _declined_in_local_folders(setup)
    with pytest.raises(UsageError, match="no such calendar"):
        _delegate(setup, message, "no-such-calendar")
    assert call(setup.engine, "event.list", **YEAR, hidden=True).events == (row,)


def test_an_edit_of_a_declined_date_or_of_one_date_of_a_declined_series_leaves_it_declined(setup: Setup) -> None:  # noqa: F811
    series = deliver(setup, invitation_mail(SERIES))
    answer(setup, series, "DECLINED")
    (first, *_) = rows(setup, hidden=True)
    call(setup.engine, "event.edit", item=first.item, occurrence="2026-10-29T10:00:00+00:00", location="My own room")
    found = rows(setup, hidden=True)
    assert rows(setup) == () and len(found) == 2 and all(row.hidden for row in found)
