from datetime import date, datetime

from chrono_mail.pim.conflictwords import Words, duration_words, reminder_words, rows, rule_words
from chrono_mail.pim.merge import SERVER

# --- Version ------------------------------------------------------------
__version__ = "test_pim_conflictwords.py 4.2.492"

# --- Version history ----------------------------------------------------
# v4.2.492: list entries of the same kind pair up in one row; one without a partner keeps its own (the owner, D-308)
# v4.2.481: Initial: every field of a sync conflict in words, never a raw calendar value (the owner, D-308)


def when(moment: date | datetime) -> str:
    return moment.strftime("%d-%m-%Y %H:%M") if isinstance(moment, datetime) else moment.strftime("%d-%m-%Y")


WORDS = Words(when, lambda colour_id: {"c1": "Red"}.get(colour_id, "Calendar colour"))


def card(*body: str) -> str:
    return "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", "UID:c1", "FN:Anna", *body, "END:VCARD"]) + "\r\n"


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


def test_an_entry_here_and_one_on_the_server_are_one_row() -> None:
    found = rows(card(), card("NICKNAME:Annie"), card("NICKNAME:Ans"), WORDS)
    (nickname,) = [row for row in found if row.label == "Nickname"]  # a pair is one row (the owner, D-308)
    assert (nickname.local, nickname.server, nickname.differs) == ("Annie", "Ans", True) and len(nickname.keys) == 2


def test_an_entry_without_a_partner_keeps_its_own_row() -> None:
    found = rows(card(), card("TEL;TYPE=WORK:111", "TEL;TYPE=CELL:222"), card("TEL;TYPE=WORK:333"), WORDS)
    phones = [(row.local, row.server, len(row.keys)) for row in found if row.label == "Phone"]
    assert phones == [("111 (work)", "333 (work)", 2), ("222 (cell)", "(empty)", 1)]


def test_work_and_home_are_not_paired() -> None:
    found = rows(card(), card("EMAIL;TYPE=WORK:anna@example.com"), card("EMAIL;TYPE=HOME:anna@example.org"), WORDS)
    mails = [(row.local, row.server, len(row.keys)) for row in found if row.label == "E-mail"]
    assert mails == [("anna@example.com (work)", "(empty)", 1), ("(empty)", "anna@example.org (home)", 1)]  # two rows, each its own choice
