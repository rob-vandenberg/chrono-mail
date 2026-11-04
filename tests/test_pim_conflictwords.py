from datetime import date, datetime

from chrono_mail.pim.conflictwords import Words, duration_words, reminder_words, rows, rule_words, value_words
from chrono_mail.pim.merge import SERVER, merge

# --- Version ------------------------------------------------------------
__version__ = "test_pim_conflictwords.py 4.4.537"

# --- Version history ----------------------------------------------------
# v4.4.537: a placeholder alarm (ACTION:NONE) is not in the words
# v4.3.498: a grouped line (item1.EMAIL) is shown and chosen as its field; reminders read the same in every time zone;
#           a row that pairs two entries of a field that holds several values can be split into both (B-31, the owner); one entry written
#           two ways on both sides cannot, nor a skipped date in another zone
# v4.2.497: an occurrence's fields follow the person's keep or leave out; a member taken out on a side shows as going; a row has its choice exactly where merge finds a clash: an occurrence on one side only without an agreed version, never
#           reminders in another order or a hidden reminder detail; Role and Anniversary are shown; one number of two kinds is two rows (the owner)
#           a number too long to read is shown as written; every kind of an entry shown and paired; a skipped date the same on both sides
#           is one row when only one side repeats
#           a text that cannot be read is one choice, the whole item; a "\;" inside a name or a company; a choice never reads the same
#           a second copy of a changed occurrence shows its own start and is named so, here and in merge's labels
#           a second copy of the item itself is named so; values that differ only in spaces are shown in quotes
#           an empty name and no name are no difference
# v4.2.496: a strange digit never raises; a jar's entry and an occurrence on one side only are no field choice (the owner)
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
    assert words == "1 hour before the start, message on screen"  # the placeholder a server adds is no reminder
    assert reminder_words("BEGIN:VALARM\nTRIGGER;VALUE=DATE-TIME:19760401T005545Z\nACTION:NONE\nEND:VALARM", False, WORDS) == reminder_words("", False, WORDS)
    assert "PT1H" not in words and "19760401T" not in words


def test_every_field_is_shown_in_words_and_only_a_visible_difference_counts() -> None:
    base = event("SUMMARY:Dentist", "DTSTART:20260812T090000Z", "DESCRIPTION:Card")
    ours = event("SUMMARY:Dentist", "DTSTART:20260812T090000Z", "DESCRIPTION:Card", "X-CHRONO-MAIL-COLOR-ID:c1", "SEQUENCE:4")
    theirs = event("SUMMARY:Dentist", "DTSTART:20260812T093000Z", "DESCRIPTION:Card", "X-CHRONO-MAIL-COLOR-ID:c1", "SEQUENCE:9")
    found = {row.label: row for row in rows(base, ours, theirs, WORDS)}
    assert set(found) == {"Title", "Colour", "Starts", "Description"}  # never the sequence or the id
    assert found["Colour"].local == "Red" and not found["Title"].differs
    starts = found["Starts"]
    assert starts.differs and starts.choice and "T09" not in starts.local + starts.server  # both changed (the colour here): the person's (D-194)


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


def test_a_strange_digit_in_a_repeat_is_shown_as_written() -> None:
    for rule in ("FREQ=MONTHLY;BYDAY=\u00b2MO", "FREQ=MONTHLY;BYDAY=+-1MO", "FREQ=MONTHLY;BYDAY=" + "1" * 5000 + "MO"):
        assert "Every month on" in rule_words(rule, WORDS)  # never a ValueError (OR-3)


def test_tags_and_members_are_a_jar_and_no_choice() -> None:
    found = rows(event("SUMMARY:x", "CATEGORIES:Work"), event("SUMMARY:x", "CATEGORIES:Work,Home"), event("SUMMARY:x", "CATEGORIES:Work,Club"), WORDS)
    tags = [(row.local, row.server, row.choice) for row in found if row.label == "Tag" and row.differs]
    assert tags == [("Home", "(empty)", False), ("(empty)", "Club", False)]  # both stay: no pair, no choice (the owner)


def test_an_occurrence_on_one_side_only_has_one_choice_keep_or_leave_out() -> None:
    occurrence = ["END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20260105T100000", "SUMMARY:Moved", "DTSTART:20260105T120000Z"]
    base = event("SUMMARY:Weekly", "DTSTART:20260101T100000Z", "RRULE:FREQ=WEEKLY", *occurrence)
    ours = event("SUMMARY:Weekly", "DTSTART:20260101T100000Z", "RRULE:FREQ=WEEKLY", *occurrence[:-1], "DTSTART:20260105T130000Z")
    theirs = event("SUMMARY:Weekly", "DTSTART:20260101T100000Z", "RRULE:FREQ=WEEKLY")
    found = rows(base, ours, theirs, WORDS)
    chosen = [(row.label, row.local, row.server) for row in found if row.choice]
    assert chosen == [("The occurrence of 05-01-2026 10:00", "Kept", "Left out")]  # floating: the same in every zone  # what merge reads: the occurrence as a whole
    assert not any(row.choice for row in found if "(the occurrence of" in row.label)


def test_without_an_agreed_version_an_occurrence_on_one_side_only_is_one_choice() -> None:
    occurrence = ["END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20260105T100000", "SUMMARY:Moved", "DTSTART:20260105T120000"]
    found = rows(None, event("SUMMARY:Weekly", "DTSTART:20260101T100000", "RRULE:FREQ=WEEKLY", *occurrence),
                 event("SUMMARY:Weekly", "DTSTART:20260101T100000", "RRULE:FREQ=WEEKLY"), WORDS)
    assert [(row.label, row.local, row.server) for row in found if row.choice] == [("The occurrence of 05-01-2026 10:00", "Kept", "Left out")]


def test_reminders_in_another_order_or_with_a_hidden_detail_are_no_choice() -> None:
    first, second = ("BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "END:VALARM"), ("BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", "END:VALARM")
    base = event("SUMMARY:x", "LOCATION:Room 1", *first, *second)
    found = rows(base, event("SUMMARY:Ours", "LOCATION:Room 1", *second, *first), event("SUMMARY:x", "LOCATION:Room 2", *first, *second), WORDS)
    (reminders,) = [row for row in found if row.label == "Reminder"]
    assert not reminders.differs and not reminders.choice  # the same reminders: no circles, as merge reads them
    acknowledged = (*first[:-1], "ACKNOWLEDGED:20261020T090000Z", "END:VALARM")
    found = rows(event("SUMMARY:x", *first), event("SUMMARY:x", *acknowledged), event("SUMMARY:x", *second), WORDS)
    (reminders,) = [row for row in found if row.label == "Reminder"]
    assert reminders.differs and not reminders.choice and reminders.taken == SERVER  # only the server changed when it goes off


def test_role_and_anniversary_are_shown() -> None:
    found = {row.label: row for row in rows(card(), card("ROLE:Engineer", "ANNIVERSARY:--0612"), card("ROLE:Manager"), WORDS)}
    assert (found["Role"].local, found["Role"].server) == ("Engineer", "Manager") and found["Anniversary"].local == "12 June"


def test_one_number_of_two_kinds_is_two_rows() -> None:
    both = card("TEL;TYPE=WORK:111", "TEL;TYPE=HOME:111")
    assert [(row.local, row.server) for row in rows(both, both, both, WORDS) if row.label == "Phone"] == [("111 (work)", "111 (work)"), ("111 (home)", "111 (home)")]


def test_a_number_too_long_to_read_is_shown_as_written() -> None:  # OR-3: never a ValueError
    big = "9" * 5000
    assert rule_words(f"FREQ=DAILY;COUNT={big}", WORDS) == f"FREQ=DAILY;COUNT={big}" and duration_words(f"PT{big}H") == ("", False)
    for body in (f"RRULE:FREQ=DAILY;INTERVAL={big}", f"RRULE:FREQ=MONTHLY;BYMONTH={big}", f"DURATION:PT{big}H"):
        assert rows(None, event("SUMMARY:a", body), event("SUMMARY:b"), WORDS)
    task = "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t\r\nPRIORITY:{}\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    assert [row.local for row in rows(None, task.format(big), task.format("1"), WORDS) if row.label == "Priority"] == [big]


def test_every_kind_of_an_entry_is_shown_and_paired_as_merge_reads_it() -> None:
    found = rows(card(), card("TEL;TYPE=work;TYPE=cell:1"), card("TEL;TYPE=work,cell:2"), WORDS)
    assert [(row.local, row.server, len(row.keys)) for row in found if row.label == "Phone"] == [("1 (work, cell)", "2 (work, cell)", 2)]


def test_a_skipped_date_the_same_on_both_sides_is_one_row_when_one_side_no_longer_repeats() -> None:
    base = event("SUMMARY:a", "DTSTART;TZID=Europe/Amsterdam:20261028T100000", "RRULE:FREQ=WEEKLY", "EXDATE;TZID=Europe/Amsterdam:20261104T100000")
    found = rows(base, base.replace("SUMMARY:a", "SUMMARY:b"), base.replace("RRULE:FREQ=WEEKLY\r\n", ""), WORDS)
    assert [row.label for row in found if row.label == "Skipped date"] == ["Skipped date"] and [row.label for row in found if row.differs] == ["Title", "Repeat"]


def test_a_text_that_cannot_be_read_is_one_choice_the_whole_item() -> None:
    unreadable = "BEGIN:VCARD\r\nFN:Anna C\r\nEND:VCALENDAR\r\n"
    found = rows(card(), card("NOTE:x"), unreadable, WORDS)
    assert [(row.key, row.label, row.choice) for row in found] == [("item", "The whole item", True)]  # what merge asks, and only that


def test_a_semicolon_inside_a_part_of_a_name_or_a_company_stays() -> None:
    assert value_words("N", "N:Jansen\\;Sr;Jan;;", "N", False, WORDS) == "Jan Jansen;Sr"
    assert value_words("ORG", "ORG:Acme\\;Sales", "ORG", False, WORDS) == "Acme;Sales"


def test_a_choice_never_reads_the_same_on_both_sides() -> None:  # D-308
    series = event("SUMMARY:a", "RRULE:FREQ=DAILY", "DTSTART:20261028T090000Z")
    found = {row.label: row for row in rows(series, series.replace("SUMMARY:a", "SUMMARY:b").replace(
        "DTSTART:20261028T090000Z", "DTSTART;TZID=Europe/Amsterdam:20261028T100000"), series.replace("SUMMARY:a", "SUMMARY:c"), WORDS)}
    assert found["Starts"].choice and found["Starts"].local.endswith("(Europe/Amsterdam)") and found["Starts"].server.endswith("(UTC)")
    meeting = event("SUMMARY:a", "ATTENDEE;CN=Ann:mailto:ann@example.com")
    ours = meeting.replace("SUMMARY:a", "SUMMARY:b").replace("CN=Ann", "CN=Ann;ROLE=CHAIR")
    (attendee,) = [row for row in rows(meeting, ours, meeting.replace("SUMMARY:a", "SUMMARY:c"), WORDS) if row.label == "Attendee"]
    assert attendee.choice and (attendee.local, attendee.server) == ("Ann <ann@example.com> (ROLE=CHAIR)", "Ann <ann@example.com>")


def test_a_second_copy_of_a_changed_occurrence_shows_its_start_and_is_named_so() -> None:  # OR-2
    def occurrence(summary: str) -> list[str]:
        return ["END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20261019T090000Z", "DTSTART:20261019T090000Z", f"SUMMARY:{summary}"]
    def item(first: str, second: str) -> str:
        return event("SUMMARY:M", "DTSTART:20261012T090000Z", "RRULE:FREQ=WEEKLY", *occurrence(first), *occurrence(second))
    base, ours, theirs = item("A", "B"), item("A1", "B1"), item("A2", "B2")
    titles = [row.label for row in rows(base, ours, theirs, WORDS) if row.label.startswith("Title (")]
    assert len(titles) == 2 and titles[1] == titles[0][:-1] + " (second copy))"  # the same start, in the same zone, told apart
    labels = [clash.label for clash in merge(base, ours, theirs).clashes]
    assert labels == ["Title (the occurrence of 19 October 2026, 09:00)", "Title (the occurrence of 19 October 2026, 09:00 (second copy))"]


def test_a_second_copy_of_the_item_itself_is_named_so_in_its_rows() -> None:  # OR-2
    def item(first: str, second: str) -> str:
        return event(f"SUMMARY:{first}", "END:VEVENT", "BEGIN:VEVENT", "UID:u1", f"SUMMARY:{second}")
    titles = [row.label for row in rows(item("A", "B"), item("A1", "B1"), item("A2", "B2"), WORDS) if row.choice]
    assert titles == ["Title", "Title (second copy)"]


def test_values_that_differ_only_in_spaces_are_shown_in_quotes() -> None:  # D-308: a choice never reads the same
    base = event("SUMMARY:a", "LOCATION:Lunch")
    found = rows(base, base.replace("SUMMARY:a", "SUMMARY:b"), base.replace("SUMMARY:a", "SUMMARY:c").replace("LOCATION:Lunch", "LOCATION:Lunch "), WORDS)
    (place,) = [row for row in found if row.label == "Location"]
    assert place.choice and (place.local, place.server) == ('Lunch ("Lunch")', 'Lunch ("Lunch ")')


def test_an_empty_name_and_no_name_are_no_difference() -> None:  # N:;;;; is what Chrono Mail writes without name parts
    found = rows(card("N:;;;;"), card("N:;;;;").replace("FN:Anna", "FN:Anna B"), card().replace("FN:Anna", "FN:Ann"), WORDS)
    assert [row.label for row in found if row.choice] == ["Display name"] and not any(row.differs for row in found if row.label == "Name")


def test_a_member_taken_out_on_one_side_shows_as_going_when_the_other_only_rewrote_it() -> None:  # D-194, as merge
    def card(*body: str) -> str:
        return "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", "UID:g1", "FN:Team", "X-ADDRESSBOOKSERVER-KIND:group", *body, "END:VCARD"]) + "\r\n"

    base = card("X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:A1")
    found = rows(base, card("X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:a1"), card(), WORDS)
    (member,) = [row for row in found if row.label == "Group member" and row.differs]
    assert (member.taken, member.choice) == (SERVER, False)  # the server took it out: it goes


def test_the_fields_of_an_occurrence_the_person_keeps_or_leaves_out_are_taken_by_nothing() -> None:
    occurrence = ["END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20260105T100000", "SUMMARY:Moved"]
    base = event("SUMMARY:Weekly", "DTSTART:20260101T100000", "RRULE:FREQ=WEEKLY", *occurrence)
    ours = event("SUMMARY:Weekly", "DTSTART:20260101T100000", "RRULE:FREQ=WEEKLY")
    theirs = event("SUMMARY:Weekly all", "DTSTART:20260101T100000", "RRULE:FREQ=WEEKLY", *occurrence[:-1], "SUMMARY:Moved again")
    found = rows(base, ours, theirs, WORDS)
    assert [row.taken for row in found if "(the occurrence of" in row.label and row.differs] == [""]  # its fate is the person's choice


def test_a_row_of_a_field_with_several_values_and_a_value_on_each_side_can_be_split() -> None:  # B-31, the owner
    base = card("EMAIL;TYPE=work:anna@old.example", "BDAY:19800412", "NICKNAME:Anna")
    ours = card("EMAIL;TYPE=work:anna@here.example", "BDAY:19800413", "NICKNAME:Annie", "TEL;TYPE=cell:+31 6 1")
    theirs = card("EMAIL;TYPE=work:anna@there.example", "BDAY:19810513", "NICKNAME:Ans")
    found = {row.label: row for row in rows(base, ours, theirs, WORDS)}
    assert found["E-mail"].split and found["Nickname"].split  # a pair: Chrono Mail's and the server's, both can stay
    assert not found["Birthday"].split and found["Birthday"].choice  # one value only
    assert not found["Phone"].split and found["Phone"].choice  # on one side only: nothing to keep twice
    assert not found["Display name"].split  # the same on both sides


def test_a_row_of_skipped_dates_on_both_sides_can_be_split() -> None:  # the owner: every visible field that holds several values
    base = event("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261102T100000Z")
    (row,) = [row for row in rows(base, base.replace("END:VEVENT", "EXDATE:20261109T100000Z\r\nEND:VEVENT"),
                                  base.replace("END:VEVENT", "EXDATE:20261116T100000Z\r\nEND:VEVENT"), WORDS) if row.differs]
    assert row.label == "Skipped date" and row.split and len(row.keys) == 2  # a pair, both can stay


def test_one_entry_written_two_ways_on_both_sides_is_no_split() -> None:  # one entry: nothing to keep twice (review)
    base = card("EMAIL:ann@example.com", "BDAY:19800412")
    found = {row.label: row for row in rows(base, card("EMAIL:Ann@Example.com", "BDAY:19800413"), card("EMAIL;PREF=1:ann@example.com", "BDAY:19800414"), WORDS)}
    assert found["E-mail"].choice and not found["E-mail"].split


def test_a_skipped_date_in_another_zone_is_no_split() -> None:  # D-308: the same moment written another valid way
    base = event("SUMMARY:Weekly", "DTSTART;TZID=Europe/Amsterdam:20261102T110000", "RRULE:FREQ=WEEKLY")
    found = rows(base, base.replace("SUMMARY:Weekly", "SUMMARY:Ours").replace("END:VEVENT", "EXDATE;TZID=Europe/Amsterdam:20261109T110000\r\nEND:VEVENT"),
                 base.replace("SUMMARY:Weekly", "SUMMARY:Theirs").replace("END:VEVENT", "EXDATE:20261109T100000Z\r\nEND:VEVENT"), WORDS)
    assert [row.label for row in found if row.split] == [] and [row.label for row in found if row.differs] == ["Title"]


def test_a_grouped_line_is_shown_and_chosen_as_its_field() -> None:  # Apple writes item1.EMAIL
    base, ours, theirs = (card(f"item1.EMAIL;type=INTERNET;type=WORK:{address}", "item1.X-ABLabel:Office")
                          for address in ("anna@old.example", "anna@here.example", "anna@there.example"))
    found = [row for row in rows(base, ours, theirs, WORDS) if row.label == "E-mail"]
    assert len(found) == 1 and found[0].choice and found[0].local == "anna@here.example (work)" and found[0].server == "anna@there.example (work)"
