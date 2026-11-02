from datetime import date, datetime

from chrono_mail.pim.conflictwords import Words, duration_words, reminder_words, rows, rule_words
from chrono_mail.pim.merge import SERVER

# --- Version ------------------------------------------------------------
__version__ = "test_pim_conflictwords.py 4.2.481"

# --- Version history ----------------------------------------------------
# v4.2.481: Initial: every field of a sync conflict in words, never a raw calendar value (the owner, D-308)


def when(moment: date | datetime) -> str:
    return moment.strftime("%d-%m-%Y %H:%M") if isinstance(moment, datetime) else moment.strftime("%d-%m-%Y")


WORDS = Words(when, lambda colour_id: {"c1": "Red"}.get(colour_id, "Calendar colour"))


def event(*body: str) -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:u1", *body, "END:VEVENT", "END:VCALENDAR"]) + "\r\n"


def test_a_duration_and_a_repeat_read_as_words() -> None:
    assert duration_words("-PT1H") == ("1 hour", True) and duration_words("P1DT2H30M") == ("1 day 2 hours 30 minutes", False)
    assert rule_words("FREQ=WEEKLY;BYDAY=MO", WORDS) == "Every week on Monday"
    assert rule_words("FREQ=MONTHLY;INTERVAL=2;BYDAY=-1FR;COUNT=10", WORDS) == "Every 2 months on the last Friday, 10 times"


def test_reminders_read_as_when_and_how() -> None:
    text = "BEGIN:VALARM\nTRIGGER:-PT1H\nACTION:DISPLAY\nEND:VALARM\nBEGIN:VALARM\nTRIGGER;VALUE=DATE-TIME:19760401T005545Z\nACTION:NONE\nEND:VALARM"
    words = reminder_words(text, False, WORDS)
    assert words.startswith("1 hour before the start, message on screen; On 01-04-1976") and words.endswith("no alert")
    assert "PT1H" not in words and "19760401T" not in words


def test_every_field_is_shown_in_words_and_only_a_visible_difference_counts() -> None:
    base = event("SUMMARY:Dentist", "DTSTART:20260812T090000Z", "DESCRIPTION:Card")
    ours = event("SUMMARY:Dentist", "DTSTART:20260812T090000Z", "DESCRIPTION:Card", "X-CHRONO-MAIL-COLOR-ID:c1", "SEQUENCE:4")
    theirs = event("SUMMARY:Dentist", "DTSTART:20260812T093000Z", "DESCRIPTION:Card", "X-CHRONO-MAIL-COLOR-ID:c1", "SEQUENCE:9")
    found = {row.label: row for row in rows(base, ours, theirs, WORDS)}
    assert set(found) == {"Title", "Colour", "Starts", "Description"}  # never the sequence or the id
    assert found["Colour"].local == "Red" and not found["Title"].differs
    starts = found["Starts"]
    assert starts.differs and starts.taken == SERVER and "T09" not in starts.local + starts.server  # only the server changed it
