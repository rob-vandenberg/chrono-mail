from pathlib import Path
import time
from datetime import UTC, datetime

import pytest

from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import parse_message
from chrono_mail.pim.itip import countered, in_meeting, proposal_usable, read_calendar_message, with_attendee_answer
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts, kept
from tests.test_engine_invitations import deliver, invitation_mail, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_organizer.py 4.4.530"

# --- Version history ----------------------------------------------------
# v4.4.530: Every block of a mail with several items knows who wrote it (D-310)
# v4.4.523: the meeting's text and an attendee's comment reach the card (D-310)
# v4.4.522: an update knows the old times and whether it asks your answer again; one date says so (D-310)
# v4.4.519: a message about your own meeting has two states, knows who wrote it, and a proposal knows the times now (D-310)
# v4.4.514: a delegator's answer adds the delegate as not answered yet (D-310)
# v4.4.509: Take their answer takes only the line of the attendee who wrote it (D-310)
# v4.4.506: Initial tests: you are the organizer: an attendee's answer into your meeting, a proposal accepted or declined, the latest version
#           sent to whoever asks; only the organizer can (D-310 step 3)


def meeting(method: str = "", sequence: int = 0, sam: str = "NEEDS-ACTION", start: str = "20261029T100000Z", end: str = "20261029T103000Z") -> str:
    """Your meeting: you (me@example.com) organize it, Sam is invited."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", *([f"METHOD:{method}"] if method else []), "BEGIN:VEVENT",
             "UID:meeting-9@example.com", f"SEQUENCE:{sequence}", "DTSTAMP:20261028T090000Z", f"DTSTART:{start}", f"DTEND:{end}",
             "SUMMARY:Planning", "ORGANIZER;CN=Me:mailto:me@example.com", f"ATTENDEE;CN=Sam;PARTSTAT={sam};RSVP=TRUE:mailto:sam@example.net",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def from_sam(calendar: str) -> bytes:
    return invitation_mail(calendar).replace(b"From: Olivia Organizer <olivia@example.org>", b"From: Sam <sam@example.net>", 1)


def organize(setup: Setup, message: str, action: str, mail: bool = True):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "invitation.organize", account=setup.account_id, folder="Inbox", message=message, action=action, mail=mail)


def test_an_attendees_answer_goes_into_your_meeting(setup: Setup) -> None:
    stored(setup, meeting())
    result = organize(setup, deliver(setup, from_sam(meeting("REPLY", sam="ACCEPTED"))), "take")
    (text,) = kept(setup)
    assert result.done == "taken" and "PARTSTAT=ACCEPTED" in text and drafts(setup) == []


def test_an_answer_changes_only_the_line_of_who_wrote_it(setup: Setup) -> None:
    stored(setup, meeting())
    other = "ATTENDEE;PARTSTAT=DECLINED:mailto:alice@example.net\r\nEND:VEVENT"
    organize(setup, deliver(setup, from_sam(meeting("REPLY", sam="ACCEPTED").replace("END:VEVENT", other))), "take")
    (text,) = kept(setup)
    assert "PARTSTAT=ACCEPTED" in text and "alice@example.net" not in text


def test_an_answer_with_several_lines_from_someone_else_is_refused(setup: Setup) -> None:
    stored(setup, meeting())
    other = "ATTENDEE;PARTSTAT=DECLINED:mailto:alice@example.net\r\nEND:VEVENT"
    message = deliver(setup, invitation_mail(meeting("REPLY", sam="ACCEPTED").replace("END:VEVENT", other)))
    with pytest.raises(UsageError, match="whose it is"):
        organize(setup, message, "take")


def test_an_answer_to_an_older_version_is_refused(setup: Setup) -> None:
    stored(setup, meeting(sequence=2))
    with pytest.raises(UsageError, match="older version"):
        organize(setup, deliver(setup, from_sam(meeting("REPLY", sequence=1, sam="ACCEPTED"))), "take")


def test_an_accepted_proposal_moves_your_meeting_and_asks_everyone_again(setup: Setup) -> None:
    stored(setup, meeting(sam="ACCEPTED"))
    proposal = meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z")
    result = organize(setup, deliver(setup, from_sam(proposal)), "accept_proposal")
    (text,) = kept(setup)
    assert result.done == "moved" and "DTSTART:20261030T140000Z" in text and "SEQUENCE:1" in text and "PARTSTAT=NEEDS-ACTION" in text
    (draft,) = drafts(setup)
    parsed = parse_message(draft)
    assert [address.address for address in parsed.to_addresses] == ["sam@example.net"]
    request = next(each for each in parsed.attachments if each.content_type == "text/calendar")
    assert request.method == "REQUEST"


def test_a_declined_proposal_answers_the_attendee(setup: Setup) -> None:
    stored(setup, meeting())
    result = organize(setup, deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z"))), "decline_proposal")
    assert result.done == "declined" and "DTSTART:20261029T100000Z" in kept(setup)[0]
    (draft,) = drafts(setup)
    part = next(each for each in parse_message(draft).attachments if each.content_type == "text/calendar")
    assert part.method == "DECLINECOUNTER"


def test_the_latest_version_goes_to_whoever_asks(setup: Setup) -> None:
    stored(setup, meeting(sequence=3))
    result = organize(setup, deliver(setup, from_sam(meeting("REFRESH"))), "send_latest")
    assert result.done == "sent"
    (draft,) = drafts(setup)
    parsed = parse_message(draft)
    request = read_calendar_message(next(each for each in parsed.attachments if each.content_type == "text/calendar").data)
    assert request is not None and request.method == "REQUEST" and request.sequence == 3 and [a.address for a in parsed.to_addresses] == ["sam@example.net"]


def test_only_the_organizer_can(setup: Setup) -> None:
    from tests.test_engine_invitations import calendar_text

    stored(setup, calendar_text(method=""))
    with pytest.raises(UsageError, match="not the organizer"):
        organize(setup, deliver(setup, invitation_mail(calendar_text(method="REPLY", answer="ACCEPTED"))), "take")


def test_an_answer_for_one_date_goes_onto_that_date_only(setup: Setup) -> None:
    """Step 3 review: Sam's decline of one date was written onto the whole series."""
    stored(setup, meeting(sam="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    one = meeting("REPLY", sam="DECLINED").replace("SEQUENCE:0", "SEQUENCE:0\r\nRECURRENCE-ID:20261105T100000Z", 1)
    organize(setup, deliver(setup, from_sam(one)), "take")
    (text,) = kept(setup)
    series, occurrence = text.split("RECURRENCE-ID:20261105T100000Z", 1)
    assert "PARTSTAT=ACCEPTED" in series and "PARTSTAT=DECLINED" in occurrence


def test_a_proposal_accepted_once_is_out_of_date_after(setup: Setup) -> None:
    """Step 3 review: a second Accept proposal moved the meeting again and asked everyone again."""
    stored(setup, meeting())
    message = deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z")))
    organize(setup, message, "accept_proposal")
    with pytest.raises(UsageError, match="older version"):
        organize(setup, message, "accept_proposal")
    assert "SEQUENCE:1" in kept(setup)[0]


def test_a_refusal_of_a_proposal_is_made_whatever_the_tick(setup: Setup) -> None:
    """Step 3 review: with the tick off the refusal said it was ready, and nothing was made."""
    stored(setup, meeting())
    result = organize(setup, deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z"))), "decline_proposal", mail=False)
    assert result.draft_message is not None and len(drafts(setup)) == 1


def test_a_meeting_kept_with_another_organizer_is_never_changed(setup: Setup) -> None:
    stored(setup, meeting().replace("ORGANIZER;CN=Me:mailto:me@example.com", "ORGANIZER;CN=Olivia:mailto:olivia@example.org"))
    with pytest.raises(UsageError, match="not the organizer"):
        organize(setup, deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z"))), "accept_proposal")


SERIES_RULE = "RRULE:FREQ=WEEKLY;COUNT=6\r\nSUMMARY:"


def proposal() -> str:
    """Sam proposes the same day at another hour."""
    return meeting("COUNTER", start="20261029T140000Z", end="20261029T143000Z")


def series(sequence: int = 0, extra: str = "") -> str:
    return meeting(sequence=sequence, sam="ACCEPTED").replace("SUMMARY:", extra + SERIES_RULE, 1)


def changed_date(moment: str, start: str, end: str, sequence: int = 0) -> str:
    return (f"BEGIN:VEVENT\r\nUID:meeting-9@example.com\r\nSEQUENCE:{sequence}\r\nDTSTAMP:20261028T090000Z\r\nRECURRENCE-ID:{moment}\r\nDTSTART:{start}\r\n"
            "DTEND:" + end + "\r\nSUMMARY:Planning\r\nORGANIZER;CN=Me:mailto:me@example.com\r\nATTENDEE;CN=Sam;PARTSTAT=ACCEPTED:mailto:sam@example.net\r\nEND:VEVENT\r\n")


def with_date(calendar: str, item: str) -> str:
    return calendar.replace("END:VCALENDAR", item + "END:VCALENDAR", 1)


def for_date(calendar: str, moment: str) -> str:
    return calendar.replace("SUMMARY:", f"RECURRENCE-ID:{moment}\r\nSUMMARY:", 1)


def test_a_request_for_the_latest_version_is_never_out_of_date(setup: Setup) -> None:
    """Step 3 review: a REFRESH has no SEQUENCE, so a meeting ever changed greyed Send the latest version."""
    from tests.test_engine_invitations import show

    stored(setup, meeting(sequence=3))
    card = show(setup, deliver(setup, from_sam(meeting("REFRESH")))).invitation
    assert card is not None and card.version == "same"


def test_a_proposal_for_one_date_moves_only_that_date(setup: Setup) -> None:
    """Step 3 review: Accept proposal failed for a date the meeting had not changed."""
    stored(setup, series())
    one = for_date(meeting("COUNTER", start="20261105T140000Z", end="20261105T143000Z"), "20261105T100000Z")
    result = organize(setup, deliver(setup, from_sam(one)), "accept_proposal")
    (text,) = kept(setup)
    head, date = text.split("END:VEVENT", 1)
    assert result.done == "moved" and "DTSTART:20261029T100000Z" in head and "RECURRENCE-ID:20261105T100000Z" in date and "DTSTART:20261105T140000Z" in date and "SEQUENCE:1" in date
    assert "SEQUENCE:0" in head and "PARTSTAT=ACCEPTED" in head and "PARTSTAT=NEEDS-ACTION" in date  # the series is untouched
    request = next(each for each in parse_message(drafts(setup)[0]).attachments if each.content_type == "text/calendar")
    assert request.data.decode().count("BEGIN:VEVENT") == 1 and b"RECURRENCE-ID:20261105T100000Z" in request.data  # only that date, or Sam's copy calls it the same version


def test_a_proposal_for_a_series_keeps_its_cancelled_dates_cancelled(setup: Setup) -> None:
    """Step 3 review: the skipped date kept the old time, so it came back."""
    stored(setup, series(extra="EXDATE:20261112T100000Z\r\n"))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    (text,) = kept(setup)
    assert "EXDATE:20261112T140000Z" in text and "20261112T100000Z" not in text


def test_a_proposal_for_a_series_takes_its_changed_dates_along(setup: Setup) -> None:
    """Step 3 review: a changed date stayed behind, so its date was shown twice."""
    stored(setup, with_date(series(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z")))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    (text,) = kept(setup)
    assert "RECURRENCE-ID:20261105T140000Z" in text and "DTSTART:20261105T140000Z" in text and "DTEND:20261105T143000Z" in text
    assert "20261105T100000Z" not in text and text.count("SEQUENCE:1") == 2 and "PARTSTAT=ACCEPTED" not in text


def test_a_date_with_its_own_time_keeps_it_when_the_series_moves(setup: Setup) -> None:
    stored(setup, with_date(series(), changed_date("20261105T100000Z", "20261106T080000Z", "20261106T083000Z")))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    (text,) = kept(setup)
    assert "RECURRENCE-ID:20261105T140000Z" in text and "DTSTART:20261106T080000Z" in text


def test_a_proposal_that_names_no_end_keeps_the_length(setup: Setup) -> None:
    """Step 3 review: a COUNTER without DTEND or DURATION lost the meeting's length."""
    stored(setup, series())
    organize(setup, deliver(setup, from_sam(proposal().replace("DTEND:20261029T143000Z\r\n", "", 1))), "accept_proposal")
    assert "DTEND:20261029T143000Z" in kept(setup)[0]


def test_a_rule_that_sets_its_own_hours_leaves_its_dates_where_they_are(setup: Setup) -> None:
    stored(setup, meeting(sam="ACCEPTED").replace("SUMMARY:", "EXDATE:20261112T100000Z\r\nRRULE:FREQ=WEEKLY;BYHOUR=10\r\nSUMMARY:", 1))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    assert "EXDATE:20261112T100000Z" in kept(setup)[0]


def test_a_date_is_compared_with_its_own_version_as_on_the_card(setup: Setup) -> None:
    """Step 3 review: the card compared a date with its changed version, the command with the series."""
    stored(setup, with_date(series(sequence=3), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", sequence=1)))
    answer = for_date(meeting("REPLY", sequence=1, sam="DECLINED"), "20261105T100000Z")
    organize(setup, deliver(setup, from_sam(answer)), "take")
    assert "PARTSTAT=DECLINED" in kept(setup)[0].split("RECURRENCE-ID:20261105T100000Z", 1)[1]


def test_an_answer_to_an_older_version_of_a_date_is_refused(setup: Setup) -> None:
    stored(setup, with_date(series(sequence=1), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z", sequence=4)))
    with pytest.raises(UsageError, match="older version"):
        organize(setup, deliver(setup, from_sam(for_date(meeting("REPLY", sequence=1, sam="DECLINED"), "20261105T100000Z"))), "take")


def test_the_latest_version_of_a_meeting_no_longer_kept_cannot_be_sent(setup: Setup) -> None:
    from tests.test_engine_invitations import show

    message = deliver(setup, from_sam(meeting("REFRESH")))
    card = show(setup, message).invitation
    assert card is not None and card.stored_in == ""
    with pytest.raises(UsageError, match="any more"):
        organize(setup, message, "send_latest")


def test_an_answer_for_a_date_you_removed_adds_nothing(setup: Setup) -> None:
    """Step 3 review: Take brought back a date the organizer had excluded."""
    from tests.test_engine_invitations import show

    stored(setup, series(extra="EXDATE:20261105T100000Z\r\n"))
    message = deliver(setup, from_sam(for_date(meeting("REPLY", sam="DECLINED"), "20261105T100000Z")))
    card = show(setup, message).invitation
    assert card is not None and not card.occurrence_kept
    with pytest.raises(UsageError, match="not in your meeting"):
        organize(setup, message, "take")
    assert "RECURRENCE-ID" not in kept(setup)[0]
    assert with_attendee_answer(kept(setup)[0], for_date(meeting("REPLY", sam="DECLINED"), "20261105T100000Z"), "sam@example.net") is None


def test_a_series_keeps_its_zone_so_its_skipped_dates_stay_skipped(setup: Setup) -> None:
    """Step 3 review: the same moment in UTC made the series a UTC one, and a skipped date came back after the clocks changed."""
    calendar = meeting(sam="ACCEPTED", start="20261020T100000", end="20261020T103000").replace("DTSTART:", "DTSTART;TZID=Europe/Amsterdam:", 1).replace("DTEND:", "DTEND;TZID=Europe/Amsterdam:", 1)
    stored(setup, calendar.replace("SUMMARY:", "EXDATE;TZID=Europe/Amsterdam:20261110T100000\r\nRRULE:FREQ=WEEKLY;COUNT=6\r\nSUMMARY:", 1))
    organize(setup, deliver(setup, from_sam(meeting("COUNTER", start="20261020T080000Z", end="20261020T090000Z"))), "accept_proposal")
    (text,) = kept(setup)
    assert "DTSTART;TZID=Europe/Amsterdam:20261020T100000" in text and "EXDATE;TZID=Europe/Amsterdam:20261110T100000" in text


def test_a_proposal_in_utc_is_read_on_the_days_of_the_series() -> None:
    """Step 3 review: 01:00 UTC on Wednesday is still Tuesday evening for a Tuesday series in New York."""
    series_new_york = meeting(start="20261103T190000", end="20261103T193000").replace("DTSTART:", "DTSTART;TZID=America/New_York:", 1).replace("DTEND:", "DTEND;TZID=America/New_York:", 1).replace("SUMMARY:", "RRULE:FREQ=WEEKLY;BYDAY=TU\r\nSUMMARY:", 1)
    later = meeting("COUNTER", start="20261104T010000Z", end="20261104T013000Z")
    assert proposal_usable(series_new_york, later)
    text = countered(series_new_york, later, "me@example.com")
    assert text is not None and "DTSTART;TZID=America/New_York:20261103T200000" in text
    assert not proposal_usable(series_new_york, meeting("COUNTER", start="20261103T020000Z", end="20261103T023000Z"))  # Monday evening there


def test_a_proposal_at_the_edge_of_the_years_shows_its_card_and_cannot_be_accepted(setup: Setup) -> None:
    """Step 3 review: reading it in the meeting's zone overflowed, and the mail could not be shown."""
    from tests.test_engine_invitations import show

    stored(setup, meeting(start="20261103T190000", end="20261103T193000").replace("DTSTART:", "DTSTART;TZID=America/New_York:", 1).replace("DTEND:", "DTEND;TZID=America/New_York:", 1))
    message = deliver(setup, from_sam(meeting("COUNTER", start="00010101T000000Z", end="00010101T003000Z")))
    card = show(setup, message).invitation
    assert card is not None and not card.proposal_usable
    with pytest.raises(UsageError, match="cannot be used"):
        organize(setup, message, "accept_proposal")


def test_a_rule_that_cannot_be_read_never_replaces_yours(setup: Setup) -> None:
    """Step 3 review: a broken rule in a proposal crashed the command or was written over the meeting's rule."""
    stored(setup, series())
    message = deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z").replace("SUMMARY:", "RRULE:FREQ=DAILY;UNTIL=garbage\r\nSUMMARY:", 1)))
    with pytest.raises(UsageError, match="cannot be used"):
        organize(setup, message, "accept_proposal")
    assert "RRULE:FREQ=WEEKLY;COUNT=6" in kept(setup)[0]


def test_a_last_moment_at_the_edge_of_the_years_stays_as_it_is() -> None:
    zoned = meeting(start="20261029T100000", end="20261029T103000").replace("DTSTART:", "DTSTART;TZID=America/New_York:", 1).replace("DTEND:", "DTEND;TZID=America/New_York:", 1)
    daily = zoned.replace("SUMMARY:", "RRULE:FREQ=DAILY;UNTIL=99991231T235959Z\r\nSUMMARY:", 1)
    text = countered(daily, meeting("COUNTER", start="20261029T180000Z", end="20261029T183000Z"), "me@example.com")
    assert text is not None and "UNTIL=99991231T235959Z" in text


@pytest.mark.parametrize("change", [
    ("SUMMARY:", "RRULE:garbage\r\nSUMMARY:"), ("SUMMARY:", "RRULE:FREQ=DAILY;BYHOUR=25\r\nSUMMARY:"), ("SUMMARY:", "RRULE:FREQ=WEEKLY;X-FOO=BAR\r\nSUMMARY:"),
    ("SUMMARY:", "RRULE:FREQ=SECONDLY\r\nSUMMARY:"), ("DTEND:20261029T143000Z", "DTEND:20261030T140000Z/PT1H"), ("DTSTART:20261029T140000Z", "DTSTART;VALUE=TEXT:soon"),
    ("SUMMARY:", "DURATION:PT1H\r\nSUMMARY:"), ("DTEND:20261029T143000Z", "DURATION:-PT1H"),
    ("SUMMARY:", "RRULE:FREQ=WEEKLY;INTERVAL=0\r\nSUMMARY:"), ("SUMMARY:", "RRULE:FREQ=WEEKLY;INTERVAL=-1\r\nSUMMARY:"), ("DTEND:20261029T143000Z", "DTEND:21000101T000000Z"),
], ids=["rule-garbage", "rule-no-dates", "rule-unknown-part", "rule-every-second", "end-a-period", "start-text", "end-and-duration", "negative-duration",
        "interval-zero", "interval-negative", "a-century-long"])
def test_a_proposal_with_a_damaged_value_is_never_taken(change: tuple[str, str]) -> None:
    """Step 3 review: these went into the meeting, and a series showed no dates or one."""
    assert not proposal_usable(series(), proposal().replace(*change, 1))


def test_a_series_moved_must_keep_its_first_date() -> None:
    """Step 3 review: a proposal past the series' end, or onto a skipped date, left a series without dates."""
    ending = meeting().replace("SUMMARY:", "RRULE:FREQ=DAILY;BYHOUR=10;UNTIL=20261102T100000Z\r\nSUMMARY:", 1)
    assert not proposal_usable(ending, meeting("COUNTER", start="20261201T100000Z", end="20261201T103000Z"))
    skipped = meeting().replace("SUMMARY:", "EXDATE:20261112T100000Z\r\nRDATE:20261112T100000Z\r\nSUMMARY:", 1)
    assert not proposal_usable(skipped, meeting("COUNTER", start="20261112T100000Z", end="20261112T103000Z"))
    assert not proposal_usable(series(), meeting("COUNTER", start="99991231T100000Z", end="99991231T103000Z"))


def test_an_address_that_cannot_be_mailed_is_left_out(setup: Setup) -> None:
    stored(setup, meeting().replace("SUMMARY:", "ATTENDEE;CN=Kim:mailto:€@example.net\r\nSUMMARY:", 1))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    assert [address.address for address in parse_message(drafts(setup)[0]).to_addresses] == ["sam@example.net"]


def test_a_proposal_for_one_date_cannot_end_it_before_its_start() -> None:
    assert not proposal_usable(series(), for_date(meeting("COUNTER", start="20261105T140000Z", end="20261105T143000Z"), "20261105T100000Z").replace("DTSTART:20261105T140000Z\r\n", "", 1).replace("DTEND:20261105T143000Z", "DTEND:20261105T090000Z", 1))


def test_a_damaged_attendee_line_is_left_out(setup: Setup) -> None:
    """Step 3 review: a line with a line break crashed Take; on the meeting it moved the meeting and then failed to make the mail."""
    damaged = 'ATTENDEE;CN=Kim:mailto:"a b"@c.d\r\nSUMMARY:'
    assert with_attendee_answer(meeting(), meeting("REPLY", sam="ACCEPTED").replace("mailto:sam@example.net", "mailto:a b@c.d", 1), "sam@example.net") is not None
    stored(setup, meeting().replace("SUMMARY:", damaged, 1))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    assert [address.address for address in parse_message(drafts(setup)[0]).to_addresses] == ["sam@example.net"]


def test_a_date_long_before_the_series_takes_no_time() -> None:
    hourly = meeting().replace("SUMMARY:", "RRULE:FREQ=HOURLY\r\nSUMMARY:", 1)
    began = time.monotonic()
    assert not in_meeting(hourly, datetime(1900, 1, 1, 10, tzinfo=UTC))
    assert time.monotonic() - began < 1


def test_a_rule_that_picks_a_day_by_position_still_moves_its_skipped_dates() -> None:
    second_tuesday = meeting(start="20261110T100000Z", end="20261110T103000Z").replace("SUMMARY:", "EXDATE:20261208T100000Z\r\nRRULE:FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2\r\nSUMMARY:", 1)
    text = countered(second_tuesday, meeting("COUNTER", start="20261110T110000Z", end="20261110T113000Z"), "me@example.com")
    assert text is not None and "EXDATE:20261208T110000Z" in text


def test_a_new_rule_on_the_same_day_still_moves_the_skipped_dates() -> None:
    written_out = proposal().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=6;BYDAY=TH\r\nSUMMARY:", 1)
    text = countered(series(extra="EXDATE:20261112T100000Z\r\n"), written_out, "me@example.com")
    assert text is not None and "EXDATE:20261112T140000Z" in text


def test_an_answer_from_your_own_second_account_is_taken(setup: Setup) -> None:
    """Step 3 review: every attendee being yours stopped Take, which needs no one to write to."""
    second = meeting(sam="NEEDS-ACTION").replace("sam@example.net", "me@example.com")
    stored(setup, second)
    answer = meeting("REPLY", sam="ACCEPTED").replace("sam@example.net", "me@example.com")
    assert organize(setup, deliver(setup, invitation_mail(answer)), "take").done == "taken"


def test_the_last_date_of_a_series_is_not_lost(setup: Setup) -> None:
    """Step 3 review: UNTIL stayed behind, so a later hour cut off the last date."""
    stored(setup, meeting(sam="ACCEPTED").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;UNTIL=20261119T100000Z\r\nSUMMARY:", 1))
    organize(setup, deliver(setup, from_sam(proposal())), "accept_proposal")
    assert "UNTIL=20261119T140000Z" in kept(setup)[0]


def test_a_date_the_meeting_never_had_is_refused_and_greyed(setup: Setup) -> None:
    """Step 3 review: an answer for a date outside the series added that date to the meeting."""
    from tests.test_engine_invitations import show

    stored(setup, series())
    message = deliver(setup, from_sam(for_date(meeting("REPLY", sam="DECLINED"), "20270101T100000Z")))
    card = show(setup, message).invitation
    assert card is not None and not card.occurrence_kept
    with pytest.raises(UsageError, match="not in your meeting"):
        organize(setup, message, "take")
    proposed = deliver(setup, from_sam(for_date(proposal(), "20270101T100000Z")))
    with pytest.raises(UsageError, match="not in your meeting"):
        organize(setup, proposed, "accept_proposal")
    assert "RECURRENCE-ID" not in kept(setup)[0]


def test_a_date_at_the_edge_of_the_years_is_refused(setup: Setup) -> None:
    """Step 3 review: a date at the end of year 9999 gave an overflow instead of a refusal."""
    stored(setup, series())
    with pytest.raises(UsageError, match="not in your meeting"):
        organize(setup, deliver(setup, from_sam(for_date(meeting("REPLY", sam="DECLINED"), "99991231T235900Z"))), "take")


def test_the_draft_carries_the_title_of_your_meeting(setup: Setup) -> None:
    """Step 3 review: a REFRESH has no SUMMARY, so the subject said (no title)."""
    stored(setup, meeting())
    organize(setup, deliver(setup, from_sam(meeting("REFRESH").replace("SUMMARY:Planning\r\n", ""))), "send_latest")
    assert parse_message(drafts(setup)[0]).subject == "Invitation: Planning"
    stored_title = "SUMMARY:Attendee's own words"
    message = deliver(setup, from_sam(proposal().replace("SUMMARY:Planning", stored_title)))
    organize(setup, message, "decline_proposal")
    assert any(parse_message(each).subject == "Proposal declined: Planning" for each in drafts(setup))


def test_an_answer_to_a_meeting_kept_as_single_dates_goes_onto_those_dates(setup: Setup) -> None:
    """Step 3 review: it said taken and changed nothing."""
    only = meeting(sam="NEEDS-ACTION").replace("SUMMARY:", "RECURRENCE-ID:20261029T100000Z\r\nSUMMARY:", 1)
    stored(setup, only)
    organize(setup, deliver(setup, from_sam(meeting("REPLY", sam="ACCEPTED"))), "take")
    assert "PARTSTAT=ACCEPTED" in kept(setup)[0]


def test_a_delegators_answer_adds_the_delegate_as_not_answered_yet_and_takes_nothing_else() -> None:
    """Decision 5: their answer comes only from their own mail, whatever the delegator's mail says about them."""
    reply = meeting("REPLY", sam="DELEGATED").replace("PARTSTAT=DELEGATED", 'PARTSTAT=DELEGATED;DELEGATED-TO="mailto:kim@example.net"', 1)
    reply = reply.replace("END:VEVENT", 'ATTENDEE;CN=Kim;PARTSTAT=ACCEPTED;DELEGATED-FROM="mailto:sam@example.net":mailto:kim@example.net\r\nEND:VEVENT')
    text = with_attendee_answer(meeting(), reply, "sam@example.net")
    assert text is not None
    message = read_calendar_message(text)
    assert message is not None
    assert {person.address: person.status for person in message.attendees}["kim@example.net"] == "NEEDS-ACTION"
    assert {person.address: person.status for person in message.attendees}["sam@example.net"] == "DELEGATED"
    assert next(person.name for person in message.attendees if person.address == "kim@example.net") == "Kim"
    assert "DELEGATED-FROM" in text


def test_a_delegate_already_on_the_meeting_keeps_their_own_answer() -> None:
    reply = meeting("REPLY", sam="DELEGATED").replace("PARTSTAT=DELEGATED", 'PARTSTAT=DELEGATED;DELEGATED-TO="mailto:kim@example.net"', 1)
    kept = meeting().replace("END:VEVENT", "ATTENDEE;PARTSTAT=TENTATIVE:mailto:kim@example.net\r\nEND:VEVENT")
    text = with_attendee_answer(kept, reply, "sam@example.net")
    assert text is not None and text.count("kim@example.net") == 2 and "PARTSTAT=TENTATIVE:mailto:kim@example.net" in text.replace("\r\n ", "")  # the line, and DELEGATED-TO


def test_a_newer_answer_ends_an_earlier_delegation() -> None:
    delegated = meeting(sam="DELEGATED").replace("RSVP=TRUE", "DELEGATED-TO=\"mailto:kim@example.net\"", 1)
    text = with_attendee_answer(delegated, meeting("REPLY", sam="ACCEPTED"), "sam@example.net")
    assert text is not None and "DELEGATED-TO" not in text and "PARTSTAT=ACCEPTED" in text


def test_an_attendee_who_is_no_mail_address_stays_as_written() -> None:
    answer = meeting("REPLY", sam="ACCEPTED").replace("mailto:sam@example.net", "urn:uuid:12", 1)
    text = with_attendee_answer(meeting(), answer, "urn:uuid:12")
    assert text is not None and "urn:uuid:12" in text and "mailto:urn" not in text
    empty = meeting("REPLY", sam="ACCEPTED").replace("mailto:sam@example.net", "", 1)
    assert "mailto:\r\n" not in (with_attendee_answer(meeting(), empty, "sam@example.net") or "")


def test_a_proposal_for_a_series_and_one_of_its_dates_takes_both() -> None:
    """Step 3 review: the proposal for the date was dropped, and the meeting said moved."""
    date = changed_date("20261105T100000Z", "20261106T090000Z", "20261106T093000Z")
    text = countered(series(), with_date(proposal(), date), "me@example.com")
    assert text is not None and "RECURRENCE-ID:20261105T140000Z" in text and "DTSTART:20261106T090000Z" in text and "DTSTART:20261029T140000Z" in text


def test_the_new_place_of_a_changed_date_is_no_date_of_the_series() -> None:
    """Step 3 review: Sam's answer for the place 5 November moved to made that day a date of its own."""
    moved = with_date(series(), changed_date("20261105T100000Z", "20261106T100000Z", "20261106T103000Z"))
    assert not in_meeting(moved, datetime(2026, 11, 6, 10, tzinfo=UTC)) and in_meeting(moved, datetime(2026, 11, 5, 10, tzinfo=UTC))
    assert in_meeting(series(), datetime(2026, 11, 12, 10, tzinfo=UTC)) and not in_meeting(series(), datetime(2026, 11, 12, 11, tzinfo=UTC))


def test_a_date_of_a_repeating_task_is_one() -> None:
    task = series().replace("VEVENT", "VTODO").replace("DTEND:20261029T103000Z", "DUE:20261029T103000Z")
    assert in_meeting(task, datetime(2026, 11, 5, 10, tzinfo=UTC))


def test_a_far_date_on_a_frequent_rule_takes_no_time() -> None:
    """Step 3 review: a date in year 3000 on an hourly rule took 22 seconds at every display of the mail."""
    hourly = meeting().replace("SUMMARY:", "RRULE:FREQ=HOURLY\r\nSUMMARY:", 1)
    began = time.monotonic()
    assert not in_meeting(hourly, datetime(3000, 1, 1, 10, tzinfo=UTC))
    assert time.monotonic() - began < 1


def test_a_date_of_a_meeting_that_does_not_repeat_is_the_meeting() -> None:
    """Step 3 review: an answer naming the start of a single meeting left its attendee line unanswered."""
    text = with_attendee_answer(meeting(), for_date(meeting("REPLY", sam="ACCEPTED"), "20261029T100000Z"), "sam@example.net")
    assert text is not None and "RECURRENCE-ID" not in text and "PARTSTAT=ACCEPTED" in text


def test_a_proposal_from_a_later_version_moves_nothing(setup: Setup) -> None:
    """Step 3 review: a proposal with a higher SEQUENCE could be accepted again and again."""
    stored(setup, meeting())
    with pytest.raises(UsageError, match="do not have"):
        organize(setup, deliver(setup, from_sam(proposal().replace("SEQUENCE:0", "SEQUENCE:5"))), "accept_proposal")


def test_accept_proposal_takes_the_place_the_card_shows() -> None:
    other_place = meeting("COUNTER").replace("SUMMARY:", "LOCATION:Room 3\r\nSUMMARY:", 1)
    assert proposal_usable(meeting(), other_place)
    text = countered(meeting(), other_place, "me@example.com")
    assert text is not None and "LOCATION:Room 3" in text and "SEQUENCE:1" in text


def test_a_proposal_that_changes_nothing_cannot_be_accepted_but_can_be_declined(setup: Setup) -> None:
    from tests.test_engine_invitations import show

    stored(setup, meeting())
    message = deliver(setup, from_sam(meeting("COUNTER")))
    card = show(setup, message).invitation
    assert card is not None and not card.proposal_usable
    with pytest.raises(UsageError, match="changes nothing"):
        organize(setup, message, "accept_proposal")
    assert organize(setup, message, "decline_proposal").done == "declined"


def test_a_proposal_with_times_that_cannot_be_used_is_refused() -> None:
    assert not proposal_usable(meeting(), meeting("COUNTER", start="20261030T140000Z", end="20261030T130000Z"))
    assert not proposal_usable(meeting(), meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z").replace("DTSTART:20261030T140000Z", "DTSTART;VALUE=DATE:20261030"))


def test_a_proposal_that_moves_a_series_off_its_days_is_not_usable() -> None:
    thursdays = meeting().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;BYDAY=TH\r\nSUMMARY:", 1)
    friday = meeting("COUNTER", start="20261030T100000Z", end="20261030T103000Z")
    assert not proposal_usable(thursdays, friday)
    assert proposal_usable(thursdays, friday.replace("SUMMARY:", "RRULE:FREQ=WEEKLY;BYDAY=FR\r\nSUMMARY:", 1))


def test_an_answer_that_is_none_changes_nothing() -> None:
    for bad in ("ACCEPTED,DECLINED", "FOO"):
        text = with_attendee_answer(meeting(), meeting("REPLY", sam=bad), "sam@example.net")
        assert text is not None and "FOO" not in text and "[" not in text and "PARTSTAT=NEEDS-ACTION" in text


def test_a_refused_proposal_never_goes_to_yourself(setup: Setup) -> None:
    """Step 3 review: a proposal from an address not on its list, you first on it, made a draft to you."""
    stored(setup, meeting())
    you_first = meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z").replace("ATTENDEE;CN=Sam", "ATTENDEE;CN=Me:mailto:me@example.com\r\nATTENDEE;CN=Sam", 1).replace("sam@example.net", "kim@example.net", 1)
    organize(setup, deliver(setup, from_sam(you_first)), "decline_proposal")
    assert [address.address for address in parse_message(drafts(setup)[0]).to_addresses] == ["kim@example.net"]


def test_a_mail_a_server_sent_is_answered_to_the_attendee(setup: Setup) -> None:
    """Step 3 review: the mail's sender, a no-reply address of a server, got the draft."""
    stored(setup, meeting())
    mail = invitation_mail(meeting("REFRESH")).replace(b"From: Olivia Organizer <olivia@example.org>", b"From: Calendar <noreply@calendar.example.net>", 1)
    organize(setup, deliver(setup, mail), "send_latest")
    assert [address.address for address in parse_message(drafts(setup)[0]).to_addresses] == ["sam@example.net"]


def test_the_same_rule_in_other_words_still_moves_the_dates() -> None:
    """Step 3 review: INTERVAL=1 and WKST written out counted as a new rule, so a cancelled date came back."""
    thursdays = meeting().replace("SUMMARY:", "EXDATE:20261112T100000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=TH\r\nSUMMARY:", 1)
    text = countered(thursdays, proposal().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;INTERVAL=1;WKST=SU;BYDAY=TH\r\nSUMMARY:", 1), "me@example.com")
    assert text is not None and "EXDATE:20261112T140000Z" in text


def test_a_proposal_for_the_date_of_a_single_meeting_reaches_its_attendees(setup: Setup) -> None:
    """Step 3 review: the request lost the meeting's only item, and Accept said there was nobody to write to."""
    stored(setup, meeting())
    organize(setup, deliver(setup, from_sam(for_date(proposal(), "20261029T100000Z"))), "accept_proposal")
    assert "DTSTART:20261029T140000Z" in kept(setup)[0] and len(drafts(setup)) == 1


def test_a_last_moment_in_utc_of_a_floating_series_moves_too() -> None:
    floating = meeting(start="20261029T100000", end="20261029T103000").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;UNTIL=20261119T100000Z\r\nSUMMARY:", 1)
    text = countered(floating, meeting("COUNTER", start="20261029T110000", end="20261029T113000"), "me@example.com")
    assert text is not None and "UNTIL=20261119T110000Z" in text


def test_a_day_as_the_last_moment_of_a_timed_series_moves_too() -> None:
    weekly = meeting().replace("SUMMARY:", "RRULE:FREQ=WEEKLY;UNTIL=20261119\r\nSUMMARY:", 1)
    text = countered(weekly, meeting("COUNTER", start="20261030T100000Z", end="20261030T103000Z"), "me@example.com")
    assert text is not None and "UNTIL=20261120" in text


def test_another_item_in_the_same_message_is_left_alone() -> None:
    other = changed_date("20261105T100000Z", "20261106T100000Z", "20261106T103000Z").replace("meeting-9@", "other@")
    text = countered(series(), with_date(proposal(), other), "me@example.com")
    assert text is not None and "20261106" not in text


def test_who_passed_it_on_stays_so_when_the_meeting_moves() -> None:
    delegated = meeting(sam="DELEGATED").replace("RSVP=TRUE", "DELEGATED-TO=\"mailto:kim@example.net\"", 1)
    text = countered(delegated, proposal(), "me@example.com")
    assert text is not None and "PARTSTAT=DELEGATED" in text


def test_an_answer_to_the_series_goes_onto_the_dates_that_followed_it() -> None:
    kept_series = with_date(series(), changed_date("20261105T100000Z", "20261105T100000Z", "20261105T103000Z"))
    other = changed_date("20261112T100000Z", "20261112T100000Z", "20261112T103000Z").replace("PARTSTAT=ACCEPTED", "PARTSTAT=TENTATIVE")
    text = with_attendee_answer(with_date(kept_series, other), meeting("REPLY", sam="DECLINED"), "sam@example.net")
    assert text is not None and text.count("PARTSTAT=DECLINED") == 2 and text.count("PARTSTAT=TENTATIVE") == 1


def card(setup: Setup, message: str):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation


def test_a_card_knows_who_wrote_the_answer_also_with_another_line_first(setup: Setup) -> None:
    stored(setup, meeting())
    two = meeting("REPLY", sam="DELEGATED").replace("ATTENDEE;CN=Sam", "ATTENDEE;CN=Pat;PARTSTAT=ACCEPTED:mailto:pat@example.net\r\nATTENDEE;CN=Sam", 1)
    assert card(setup, deliver(setup, from_sam(two))).writer == "sam@example.net"


def test_your_copy_tells_whether_their_answer_is_in(setup: Setup) -> None:
    stored(setup, meeting())
    message = deliver(setup, from_sam(meeting("REPLY", sam="ACCEPTED")))
    assert dict(card(setup, message).kept_answers)["sam@example.net"] == "NEEDS-ACTION"
    organize(setup, message, "take")
    assert dict(card(setup, message).kept_answers)["sam@example.net"] == "ACCEPTED"


def test_a_declined_proposal_stays_answered_on_the_mail_and_goes_only_once(setup: Setup) -> None:
    stored(setup, meeting())
    message = deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z")))
    assert not card(setup, message).answered_on_mail
    organize(setup, message, "decline_proposal")
    assert card(setup, message).answered_on_mail
    with pytest.raises(UsageError, match="answered this message already"):
        organize(setup, message, "decline_proposal")
    assert len(drafts(setup)) == 1


def test_a_request_answered_stays_answered_on_the_mail_and_goes_only_once(setup: Setup) -> None:
    stored(setup, meeting(sequence=3))
    message = deliver(setup, from_sam(meeting("REFRESH")))
    organize(setup, message, "send_latest")
    assert card(setup, message).answered_on_mail
    with pytest.raises(UsageError, match="answered this message already"):
        organize(setup, message, "send_latest")
    assert len(drafts(setup)) == 1


def test_a_proposal_knows_the_times_your_meeting_has_now(setup: Setup) -> None:
    stored(setup, meeting())
    shown = card(setup, deliver(setup, from_sam(meeting("COUNTER", start="20261030T140000Z", end="20261030T143000Z"))))
    assert shown.kept_start == datetime(2026, 10, 29, 10, 0, tzinfo=UTC) and shown.kept_end == datetime(2026, 10, 29, 10, 30, tzinfo=UTC)


def test_a_proposal_for_one_date_compares_with_that_date_not_the_first_of_the_series(setup: Setup) -> None:
    stored(setup, series())
    proposal_text = for_date(meeting("COUNTER", start="20261105T140000Z", end="20261105T143000Z"), "20261105T100000Z")
    shown = card(setup, deliver(setup, from_sam(proposal_text)))
    assert shown.kept_start == datetime(2026, 11, 5, 10, 0, tzinfo=UTC) and shown.kept_end == datetime(2026, 11, 5, 10, 30, tzinfo=UTC)


def invited_meeting(sequence: int = 0, start: str = "20261029T100000Z", end: str = "20261029T103000Z", where: str = "") -> str:
    """A meeting someone else organizes, you accepted."""
    text = meeting("", sequence, start=start, end=end).replace("ORGANIZER;CN=Me:mailto:me@example.com", "ORGANIZER;CN=Olivia:mailto:olivia@example.org")
    return text.replace("ATTENDEE;CN=Sam;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:sam@example.net", "ATTENDEE;CN=Me;PARTSTAT=ACCEPTED:mailto:me@example.com").replace("SUMMARY:Planning", f"SUMMARY:Planning{where}")


def update(text: str) -> str:
    return text.replace("VERSION:2.0\r\n", "VERSION:2.0\r\nMETHOD:REQUEST\r\n").replace("PARTSTAT=ACCEPTED", "PARTSTAT=NEEDS-ACTION")


def test_an_update_that_moved_asks_your_answer_again_and_knows_the_old_time(setup: Setup) -> None:
    stored(setup, invited_meeting())
    shown = card(setup, deliver(setup, invitation_mail(update(invited_meeting(1, "20261029T110000Z", "20261029T113000Z")))))
    assert shown.answer_again and shown.kept_start == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


def test_an_update_of_the_title_leaves_your_answer_standing(setup: Setup) -> None:
    stored(setup, invited_meeting())
    assert not card(setup, deliver(setup, invitation_mail(update(invited_meeting(1, where=" again"))))).answer_again


def test_a_card_knows_it_is_about_one_date(setup: Setup) -> None:
    stored(setup, series())
    assert card(setup, deliver(setup, from_sam(for_date(meeting("REPLY", sam="ACCEPTED"), "20261105T100000Z")))).one_date


def test_the_card_carries_the_meetings_text_and_the_attendees_comment(setup: Setup) -> None:
    stored(setup, meeting())
    answer_text = meeting("REPLY", sam="DECLINED").replace("SUMMARY:Planning", "SUMMARY:Planning\r\nDESCRIPTION:The agenda\r\nCOMMENT:I am away")
    shown = card(setup, deliver(setup, from_sam(answer_text)))
    assert shown.description == "The agenda" and shown.comment == "I am away"



def test_every_block_of_a_mail_with_several_items_knows_who_wrote_it(setup: Setup, tmp_path: Path) -> None:
    def item(uid: str) -> str:
        return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261027T150900Z\r\nDTSTART:20261029T100000Z\r\nDTEND:20261029T103000Z\r\nSUMMARY:Item\r\n"
                "ORGANIZER:mailto:me@example.com\r\nATTENDEE:mailto:first@example.org\r\nATTENDEE:mailto:olivia@example.org\r\nEND:VEVENT\r\n")

    text = f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nMETHOD:COUNTER\r\n{item('one')}{item('two')}END:VCALENDAR\r\n"
    message = deliver(setup, invitation_mail(text))
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).invitation
    assert shown is not None and [part.writer for part in shown.parts] == ["olivia@example.org"] * 2
    blocks = call(setup.engine, "invitation.blocks", account=setup.account_id, folder="Inbox", message=message, first=0, count=2)
    assert [part.writer for part in blocks.parts] == ["olivia@example.org"] * 2
    file = tmp_path / "proposals.ics"
    file.write_text(text)
    in_file = call(setup.engine, "invitation.card", account="", folder="", message="", file=str(file))
    assert [part.writer for part in in_file.parts] == ["", ""]  # nobody sent a file
