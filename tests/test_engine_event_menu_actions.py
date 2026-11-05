from datetime import UTC, datetime

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine.commands_calendar import OccurrenceInfo
from chrono_mail.engine.commands_invitations import AnswerResult, DelegateResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.pim.itip import answer_given, kept_mail, read_calendar_message, you_declined
from tests.fakedav import FakeDav
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_event_menus import OLIVIA, added, me, row_on, rows, series, single
from tests.test_engine_invitation_answers import local_calendar
from tests.test_engine_invitation_declined import second_calendar
from tests.test_engine_invitations import calendar_text, deliver, invitation_mail

# --- Version ------------------------------------------------------------
__version__ = "test_engine_event_menu_actions.py 4.4.558"

# --- Version history ----------------------------------------------------
# v4.4.558: Initial tests: what the commands refuse of what the menu greys, an answer on one date of a series declined in Local Folders, a calendar shared with you (D-318)

SECOND_DAY = "2030-03-05T10:00:00+00:00"
WEEKLY = calendar_text().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:", 1)
MEETING_DAYS = {"from_date": "2026-10-29", "to_date": "2026-11-13"}
SECOND_WEEK = datetime(2026, 11, 5, 10, tzinfo=UTC)


def from_calendar(setup: Setup, item: str, answer: str, occurrence: str = "", calendar: str = "") -> AnswerResult:  # noqa: F811
    return call(setup.engine, "invitation.answer", account="", folder="", message="", event=item, occurrence=occurrence, answer=answer, calendar=calendar, mail=False)


def delegated(setup: Setup, item: str, occurrence: str = "", calendar: str = "") -> DelegateResult:  # noqa: F811
    return call(setup.engine, "invitation.delegate", account="", folder="", message="", event=item, occurrence=occurrence, to="sam@example.org", calendar=calendar, mail=False)


def stored_text(setup: Setup, row: OccurrenceInfo) -> str:  # noqa: F811
    with open_pim(setup.engine, row.account) as store:
        return store.get(row.collection, row.item)


def declined_series(setup: Setup) -> tuple[str, OccurrenceInfo]:  # noqa: F811
    """A weekly meeting you declined as a whole, with no copy anywhere: it is in Local Folders. The mail, and the row of its second date."""
    message = deliver(setup, invitation_mail(WEEKLY))
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, answer="DECLINED", mail=False)
    return message, next(row for row in call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events if row.start.startswith("2026-11-05"))


def shared_calendar(setup: Setup, text: str) -> tuple[str, OccurrenceInfo]:  # noqa: F811
    """A calendar of someone else with write access, holding `text`: its id and the row."""
    calendar = second_calendar(setup)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(calendar, server={"shared": "1"})
    added(setup, calendar, text)
    return calendar, rows(setup)[0]


@pytest.mark.parametrize("answer", ["ACCEPTED", "TENTATIVE", "DECLINED"])
def test_the_answer_you_gave_is_refused_from_the_calendar(setup: Setup, answer: str) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(OLIVIA, me(answer)))
    with pytest.raises(UsageError, match="this answer already"):
        from_calendar(setup, item, answer)


def test_delegate_is_refused_when_you_delegated_already(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), single(OLIVIA, me("DELEGATED")))
    with pytest.raises(UsageError, match="this answer already"):
        delegated(setup, item)


def test_the_answer_of_another_date_is_no_reason_to_refuse(setup: Setup) -> None:  # noqa: F811
    """The menu greys by the date right-clicked: a series accepted, one date tentative."""
    item = added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("TENTATIVE")],)))
    from_calendar(setup, item, "ACCEPTED", SECOND_DAY)
    assert row_on(setup, "2030-03-05").answer == "ACCEPTED"


def test_all_occurrences_goes_on_while_one_date_has_another_answer(setup: Setup) -> None:  # noqa: F811
    item = added(setup, local_calendar(setup), series(OLIVIA, me("ACCEPTED"), dates=(["20300305T100000Z", OLIVIA, me("TENTATIVE")],)))
    from_calendar(setup, item, "ACCEPTED")
    assert {row.answer for row in rows(setup)} == {"ACCEPTED"}
    with pytest.raises(UsageError, match="this answer already"):
        from_calendar(setup, item, "ACCEPTED")


def test_no_answer_goes_from_a_read_only_calendar(setup: Setup) -> None:  # noqa: F811
    with FakeDav(read_only=("events",)) as fake:
        fake.collections["events"].items["one.ics"] = ('"1"', single(OLIVIA, me("NEEDS-ACTION")))
        call(setup.engine, "account.set-dav", account=setup.account_id, url=fake.url, username="ann", password="secret")
        call(setup.engine, "dav.sync", account=setup.account_id)
        (row,) = rows(setup)
        with pytest.raises(UsageError, match="read-only"):
            from_calendar(setup, row.item, "ACCEPTED", calendar=local_calendar(setup))
        with pytest.raises(UsageError, match="read-only"):
            delegated(setup, row.item, calendar=local_calendar(setup))
        assert not fake.collections["events"].items["one.ics"][1].count("ACCEPTED")


def test_no_answer_goes_from_a_cancelled_event_in_a_calendar_shared_with_you(setup: Setup) -> None:  # noqa: F811
    mine = local_calendar(setup)
    _, row = shared_calendar(setup, single(OLIVIA, me("NEEDS-ACTION"), "STATUS:CANCELLED"))
    with pytest.raises(UsageError, match="restore it first"):
        from_calendar(setup, row.item, "ACCEPTED", calendar=mine)


def test_accept_of_an_event_in_a_shared_calendar_asks_a_calendar_and_makes_your_own_copy(setup: Setup) -> None:  # noqa: F811
    mine = local_calendar(setup)
    calendar, row = shared_calendar(setup, single(OLIVIA, me("NEEDS-ACTION")))
    choices = call(setup.engine, "invitation.choices", account="", folder="", message="", event=row.item)
    assert choices.stored == ""  # their copy is no copy of yours: the calendar dialog comes
    from_calendar(setup, row.item, "ACCEPTED", calendar=mine)
    assert {each.collection: each.answer for each in rows(setup)} == {calendar: "NEEDS-ACTION", mine: "ACCEPTED"}


def test_cancel_restore_and_delete_act_on_the_event_in_a_shared_calendar(setup: Setup) -> None:  # noqa: F811
    own = added(setup, local_calendar(setup), single(OLIVIA, me("ACCEPTED")).replace("UID:single", "UID:own"))
    calendar, _ = shared_calendar(setup, single(OLIVIA, me("NEEDS-ACTION")))
    row = next(each for each in rows(setup) if each.collection == calendar)
    call(setup.engine, "event.cancel", item=row.item)
    assert {each.item: each.cancelled for each in rows(setup)} == {own: False, row.item: True}
    call(setup.engine, "event.restore", item=row.item)
    call(setup.engine, "event.delete", item=row.item)
    assert [each.item for each in rows(setup)] == [own]


def test_an_answer_on_one_date_of_a_series_declined_in_local_folders_moves_the_whole_series(setup: Setup) -> None:  # noqa: F811
    message, row = declined_series(setup)
    target = second_calendar(setup)
    from_calendar(setup, row.item, "ACCEPTED", row.occurrence, target)
    assert [(each.start[:10], each.answer) for each in call(setup.engine, "event.list", **MEETING_DAYS).events] == [("2026-11-05", "ACCEPTED")]
    hidden = call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events
    assert [(each.start[:10], each.answer) for each in hidden] == [("2026-10-29", "DECLINED"), ("2026-11-05", "ACCEPTED"), ("2026-11-12", "DECLINED")]
    assert {each.item for each in hidden} == {hidden[0].item} and {each.collection for each in hidden} == {target}  # one record, nothing left behind
    text = stored_text(setup, hidden[0])
    assert you_declined(text, {"me@example.com"}) and kept_mail(text) is None
    kept = read_calendar_message(text, SECOND_WEEK)
    assert kept is not None and [each.status for each in kept.attendees if each.address == "me@example.com"] == ["ACCEPTED"]
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert MessageFlag.INVITE_DECLINED not in mailbox.current_flags(mailbox.reference(mailbox.folder("Inbox"), message))


def test_an_answer_on_one_date_in_the_calendar_it_is_in_keeps_it_one_record(setup: Setup) -> None:  # noqa: F811
    _, row = declined_series(setup)
    from_calendar(setup, row.item, "TENTATIVE", row.occurrence, row.collection)
    hidden = call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events
    assert [each.answer for each in hidden] == ["DECLINED", "TENTATIVE", "DECLINED"] and {each.item for each in hidden} == {row.item}
    assert kept_mail(stored_text(setup, row)) is None


def test_delegate_of_one_date_of_a_series_declined_in_local_folders_moves_the_whole_series(setup: Setup) -> None:  # noqa: F811
    _, row = declined_series(setup)
    target = second_calendar(setup)
    delegated(setup, row.item, row.occurrence, target)
    hidden = call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events
    assert [each.answer for each in hidden] == ["DECLINED", "DELEGATED", "DECLINED"] and {each.collection for each in hidden} == {target}


def test_a_refused_move_leaves_the_declined_series_where_it_is(setup: Setup) -> None:  # noqa: F811
    _, row = declined_series(setup)
    with pytest.raises(UsageError):
        from_calendar(setup, row.item, "ACCEPTED", row.occurrence, "no-such-calendar")
    hidden = call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events
    assert [each.answer for each in hidden] == ["DECLINED"] * 3 and {each.item for each in hidden} == {row.item}


def test_with_two_of_your_addresses_the_command_reads_your_answer_as_the_row() -> None:
    text = single(OLIVIA, "ATTENDEE;PARTSTAT=ACCEPTED:mailto:me@example.com", "ATTENDEE;PARTSTAT=DECLINED:mailto:other@example.com")
    yours = {"me@example.com", "other@example.com"}
    assert answer_given(text, yours, "DECLINED") and not answer_given(text, yours, "ACCEPTED")


def test_a_move_that_cannot_be_made_keeps_the_declined_series(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _, row = declined_series(setup)
    target = second_calendar(setup)

    def nothing(*_: object) -> None:
        return None

    monkeypatch.setattr("chrono_mail.engine.commands_invitations.moved_answer", nothing)
    with pytest.raises(UsageError, match="could not be read"):
        from_calendar(setup, row.item, "ACCEPTED", row.occurrence, target)
    with pytest.raises(UsageError, match="could not be read"):
        delegated(setup, row.item, row.occurrence, target)
    hidden = call(setup.engine, "event.list", hidden=True, **MEETING_DAYS).events
    assert [each.answer for each in hidden] == ["DECLINED"] * 3 and {each.item for each in hidden} == {row.item}
