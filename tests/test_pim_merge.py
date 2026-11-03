from chrono_mail.pim.merge import LOCAL, SERVER, merge

# --- Version ------------------------------------------------------------
__version__ = "test_pim_merge.py 4.2.497"

# --- Version history ----------------------------------------------------
# v4.2.497: a group member taken out on one side stays out when the other only rewrote it; changed on both sides counts for the whole item with its occurrences; an occurrence on one side only is a choice without an
#           agreed version, and when both sides changed; an attendee's answer, a hidden reminder detail and another way of writing the same
#           never clash; capitals in a value and "\;" in a company count; Role and Anniversary clash; one number of two kinds stays twice;
#           a comma list's value with a colon is shown whole (the owner); an occurrence only rewritten there and deleted here is removed;
#           an attendee taken from here keeps the server's answer; another zone differs in a series; kinds in any order and capitals are the same
#           two lines of one version with the same value and kinds both stay; a reminder's length however written; a date with hyphens
#           two changed occurrences of one start both stay; a setting such as ROLE in any capitals; a reminder without ACTION
#           a line that starts with the format's separator never stops a merge; a second copy of the item itself is named so
#           an empty value reads as no line; a start that ends in "#2" never takes a second copy's key
# v4.2.496: tags are a jar: put in or taken out on one side stays so, never a choice; a strange digit never raises; a damaged agreed
#           version is none (the owner)
# v4.2.490: the same text written another valid way (\\,) is no difference (the owner)
# v4.2.483: an item changed on both sides is the person's to settle, every visible difference a choice, lists too (the owner)
# v4.2.481: only what a person enters or sees clashes; reminders by when and how; an occurrence is chosen apart (D-308)
# v1.1.225: Initial: the three-way comparison field by field, every rule the owner chose (F1 to F5, D-194)


def calendar(*body: str, component: str = "VEVENT", extra: tuple[str, ...] = ()) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", f"BEGIN:{component}", "UID:u1", "DTSTAMP:20261020T100000Z", *body, f"END:{component}", *extra, "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


BASE = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")


def test_an_item_changed_on_both_sides_is_the_person_s_to_settle_field_by_field() -> None:  # the owner, 4.2.483: also different fields
    ours = calendar("SUMMARY:Team meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Room 2", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    merged = merge(BASE, ours, theirs)
    assert merged.text is None and [clash.label for clash in merged.clashes] == ["Title", "Location"]
    chosen = merge(BASE, ours, theirs, {"SUMMARY": LOCAL, "LOCATION": SERVER}).text or ""
    assert "SUMMARY:Team meeting" in chosen and "LOCATION:Room 2" in chosen and chosen.endswith("\r\n")
    assert "LOCATION:Room 2" in (merge(BASE, BASE, theirs).text or "")  # changed on the server only: followed by itself


def test_the_same_field_changed_to_the_same_value_is_no_clash() -> None:
    both = calendar("SUMMARY:Meeting", "LOCATION:Room 9", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    assert merge(BASE, both, both.replace("DTSTAMP:20261020T100000Z", "DTSTAMP:20261020T120000Z")).clashes == ()


def test_the_same_field_changed_differently_is_a_clash_settled_by_a_choice() -> None:
    ours = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T100000Z", "DESCRIPTION:Agenda")
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T110000Z", "DESCRIPTION:Agenda")
    merged = merge(BASE, ours, theirs)
    assert merged.text is None and [(c.label, c.local, c.server) for c in merged.clashes] == [("Starts", "20261021T100000Z", "20261021T110000Z")]
    key = merged.clashes[0].key
    assert "DTSTART:20261021T110000Z" in (merge(BASE, ours, theirs, {key: SERVER}).text or "")
    assert "DTSTART:20261021T100000Z" in (merge(BASE, ours, theirs, {key: LOCAL}).text or "")


def test_each_changed_occurrence_is_an_item_of_its_own() -> None:  # F1
    def occurrence(day: str, location: str) -> tuple[str, ...]:
        return ("BEGIN:VEVENT", "UID:u1", f"RECURRENCE-ID:202610{day}T090000Z", f"DTSTART:202610{day}T100000Z", f"LOCATION:{location}", "END:VEVENT")
    base = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261021T090000Z")
    ours = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261021T090000Z", extra=occurrence("28", "Here"))
    theirs = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261021T090000Z").replace("END:VEVENT\r\n", "END:VEVENT\r\n" + "\r\n".join(occurrence("21", "There")) + "\r\n", 1)
    merged = merge(base, ours, theirs)  # each side put in an occurrence: changed on both sides, so each is the person's to keep (D-194)
    assert [clash.label for clash in merged.clashes] == ["The whole item (the occurrence of 28 October 2026, 09:00) (only here)",
                                                         "The whole item (the occurrence of 21 October 2026, 09:00) (only on the server)"]
    kept = merge(base, ours, theirs, {clash.key: LOCAL if "only here" in clash.label else SERVER for clash in merged.clashes}).text or ""
    assert "LOCATION:Here" in kept and "LOCATION:There" in kept
    assert "LOCATION:Here" in (merge(base, ours, base).text or "")  # put in here only: followed by itself


def test_lists_are_compared_entry_by_entry() -> None:  # F2
    base = calendar("SUMMARY:x", "CATEGORIES:Work", "EXDATE:20261028T090000Z", "ATTENDEE:mailto:ann@example.com")
    ours = calendar("SUMMARY:x", "CATEGORIES:Work,Home", "EXDATE:20261028T090000Z,20261104T090000Z", "ATTENDEE:mailto:ann@example.com", "ATTENDEE:mailto:bob@example.com")
    theirs = calendar("SUMMARY:x", "CATEGORIES:Club", "EXDATE:20261028T090000Z", "ATTENDEE:mailto:ann@example.com", "ATTENDEE:mailto:cee@example.com")
    assert merge(base, base, theirs).clashes == ()  # changed on the server only: followed by itself
    merged = merge(base, ours, theirs)
    assert merged.text is None and {clash.label for clash in merged.clashes} == {"Attendee", "Skipped date"}  # each entry a choice; tags no
    kept = merge(base, ours, theirs, {clash.key: LOCAL for clash in merged.clashes}).text or ""
    assert "bob@example.com" in kept and "cee@example.com" not in kept
    assert "CATEGORIES:Home,Club" in kept  # tags are a jar: Home put in here, Club put in and Work taken out there, all as done (the owner)
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Ann\r\n{}END:VCARD\r\n"
    merged_card = merge(card.format("TEL:111\r\n"), card.format("TEL:111\r\n"), card.format("TEL:111\r\nTEL:333\r\n"))
    assert merged_card.clashes == () and all(number in (merged_card.text or "") for number in ("111", "333"))


def test_the_alarms_of_an_item_are_one_field() -> None:  # F3
    def alarm(trigger: str) -> tuple[str, ...]:
        return ("BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trigger}", "END:VALARM")
    base = calendar("SUMMARY:x", *alarm("-PT30M"))
    merged = merge(base, calendar("SUMMARY:x", *alarm("-PT15M")), calendar("SUMMARY:x", *alarm("-PT1H")))
    assert [(c.label, c.local, c.server) for c in merged.clashes] == [("Reminder", "-PT15M", "-PT1H")]
    assert merge(base, calendar("SUMMARY:x", *alarm("-PT15M")), base).text is not None


def test_a_text_is_one_field_never_merged_inside() -> None:  # F4
    merged = merge(BASE, BASE.replace("DESCRIPTION:Agenda", "DESCRIPTION:Agenda\\nand coffee"), BASE.replace("DESCRIPTION:Agenda", "DESCRIPTION:Agenda\\nand tea"))
    assert [c.label for c in merged.clashes] == ["Description"]


def test_a_property_of_another_program_is_kept_and_compared_by_its_name() -> None:  # F5
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda", "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC")
    ours = BASE.replace("SUMMARY:Meeting", "SUMMARY:Renamed")
    merged = merge(BASE, ours, theirs)
    assert merged.clashes == () and "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC" in (merged.text or "") and "SUMMARY:Renamed" in (merged.text or "")
    clash = merge(BASE, BASE + "", theirs.replace("AUTOMATIC", "DISABLED"))
    assert clash.clashes == ()  # changed on one side only


def test_without_an_agreed_version_every_difference_is_a_clash() -> None:
    ours = BASE.replace("SUMMARY:Meeting", "SUMMARY:Ours")
    theirs = BASE.replace("LOCATION:Room 1", "LOCATION:Theirs")
    assert {c.label for c in merge(None, ours, theirs).clashes} == {"Title", "Location"}


def test_a_text_that_cannot_be_read_is_one_field() -> None:
    assert [c.label for c in merge("garbage", "garbage 1", "garbage 2").clashes] == ["The whole item"]
    assert merge("garbage", "garbage 1", "garbage").text == "garbage 1"


def test_a_removed_occurrence_against_a_changed_one_is_a_clash() -> None:
    occurrence = "BEGIN:VEVENT\r\nUID:u1\r\nRECURRENCE-ID:20261028T090000Z\r\nLOCATION:{}\r\nEND:VEVENT\r\n"
    base = BASE.replace("END:VCALENDAR", occurrence.format("A") + "END:VCALENDAR")
    ours = BASE  # the changed occurrence removed here
    theirs = BASE.replace("END:VCALENDAR", occurrence.format("B") + "END:VCALENDAR")
    merged = merge(base, ours, theirs)
    assert len(merged.clashes) == 1 and merged.clashes[0].key.startswith("unit")
    assert merge(base, ours, base).text is not None and "RECURRENCE-ID" not in (merge(base, ours, base).text or "")


def test_a_field_a_person_never_sees_never_clashes_and_takes_the_server_s() -> None:
    ours = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda", "STATUS:TENTATIVE")
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda", "STATUS:CANCELLED")
    merged = merge(BASE, ours, theirs)
    assert merged.clashes == () and "STATUS:CANCELLED" in (merged.text or "")  # D-308


def test_reminders_that_differ_only_in_what_is_not_shown_never_clash() -> None:
    alarm = ("BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY")
    ours = calendar("SUMMARY:Meeting", *alarm, "UID:ours", "END:VALARM")
    theirs = calendar("SUMMARY:Meeting", *alarm, "UID:theirs", "X-APPLE-DEFAULT-ALARM:TRUE", "END:VALARM")
    merged = merge(calendar("SUMMARY:Meeting"), ours, theirs)
    assert merged.clashes == () and "UID:theirs" in (merged.text or "")


def test_reminders_at_another_time_clash_and_an_occurrence_is_chosen_apart() -> None:
    occurrence = ("END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20261028T090000Z", "SUMMARY:Meeting")
    ours = calendar("SUMMARY:Meeting", "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "END:VALARM", *occurrence, "LOCATION:Room 1")
    theirs = calendar("SUMMARY:Meeting", "BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", "END:VALARM", *occurrence, "LOCATION:Room 2")
    merged = merge(calendar("SUMMARY:Meeting", *occurrence), ours, theirs)
    assert [clash.label for clash in merged.clashes] == ["Reminder", "Location (the occurrence of 28 October 2026, 09:00)"]
    assert merged.clashes[0].key != merged.clashes[1].key and merged.clashes[1].key.endswith("@20261028T090000Z")


def test_the_same_text_written_another_valid_way_is_no_difference() -> None:
    ours = calendar("SUMMARY:Meeting", "LOCATION:Practice Smit, room 2", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Practice Smit\\, room 2", "DTSTART:20261021T090000Z", "DESCRIPTION:New agenda")
    merged = merge(BASE, ours, theirs)
    assert [clash.label for clash in merged.clashes] == ["Description"]  # the location reads the same: no choice (the owner, 4.2.490)


def test_a_strange_digit_in_an_occurrence_never_raises() -> None:
    from chrono_mail.pim.merge import occurrence_words

    assert occurrence_words("2026\u00b2812T090000Z") == "2026\u00b2812T090000Z"  # shown as written (OR-3)
    occurrence = ["END:VEVENT", "BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:2026\u00b2812T090000Z", "SUMMARY:Moved"]
    base = calendar("SUMMARY:x", "RRULE:FREQ=DAILY", *occurrence)
    assert merge(base, calendar("SUMMARY:y", "RRULE:FREQ=DAILY", *occurrence), calendar("SUMMARY:z", "RRULE:FREQ=DAILY", *occurrence)).clashes


def test_a_damaged_agreed_version_is_no_agreed_version() -> None:
    from chrono_mail.core import StorageError
    from chrono_mail.sync.davconflicts import base_of

    class Damaged:
        def read_private(self, name: str) -> str | None:
            raise StorageError("A file of the synchronisation could not be read")

    assert base_of(Damaged(), "c", "i") is None  # type: ignore[arg-type]  # every difference the person's choice, never a stopped sync


OCCURRENCE = ("BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20261028T090000Z", "DTSTART:20261028T100000Z", "LOCATION:Room 1", "END:VEVENT")
WEEKLY = ("RRULE:FREQ=WEEKLY", "DTSTART:20261021T090000Z")


def test_changed_on_both_sides_counts_for_the_whole_item_with_its_occurrences() -> None:  # D-194
    base = calendar("SUMMARY:Weekly", *WEEKLY, extra=OCCURRENCE)
    ours = calendar("SUMMARY:Team weekly", *WEEKLY, extra=OCCURRENCE)  # the series changed here
    theirs = calendar("SUMMARY:Weekly", *WEEKLY, extra=tuple(line.replace("Room 1", "Room 2") for line in OCCURRENCE))  # an occurrence there
    merged = merge(base, ours, theirs)
    assert [clash.label for clash in merged.clashes] == ["Title", "Location (the occurrence of 28 October 2026, 09:00)"]
    assert merge(base, ours, base).clashes == () and merge(base, base, theirs).clashes == ()  # one side only: followed by itself


def test_without_an_agreed_version_an_occurrence_on_one_side_only_is_kept_or_left_out() -> None:
    ours = calendar("SUMMARY:Weekly", *WEEKLY, extra=OCCURRENCE)
    theirs = calendar("SUMMARY:Weekly", *WEEKLY)
    merged = merge(None, ours, theirs)
    assert [(c.label, c.local, c.server) for c in merged.clashes] == [
        ("The whole item (the occurrence of 28 October 2026, 09:00) (only here)", "kept", "removed")]
    left_out = merge(None, ours, theirs, {merged.clashes[0].key: SERVER}).text
    assert left_out is not None and "RECURRENCE-ID" not in left_out


def test_an_attendee_s_answer_is_not_compared_and_the_server_s_is_taken() -> None:  # D-308
    base = calendar("SUMMARY:Meeting", "ATTENDEE;CN=Ann;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:ann@example.com")
    ours = calendar("SUMMARY:Team meeting", "ATTENDEE;CN=Ann;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:ann@example.com")
    theirs = calendar("SUMMARY:Meeting", "ATTENDEE;CN=Ann;PARTSTAT=ACCEPTED:mailto:ann@example.com")
    merged = merge(base, ours, theirs)
    assert merged.clashes == () and "PARTSTAT=ACCEPTED" in (merged.text or "") and "SUMMARY:Team meeting" in (merged.text or "")


def test_a_hidden_reminder_detail_here_and_a_new_time_there_is_no_clash() -> None:
    def alarm(trigger: str, *more: str) -> tuple[str, ...]:
        return ("BEGIN:VALARM", "UID:a1", "ACTION:DISPLAY", f"TRIGGER:{trigger}", *more, "END:VALARM")
    base = calendar("SUMMARY:x", *alarm("-PT30M"))
    merged = merge(base, calendar("SUMMARY:x", *alarm("-PT30M", "ACKNOWLEDGED:20261020T090000Z")), calendar("SUMMARY:x", *alarm("-PT1H")))
    assert merged.clashes == () and "TRIGGER:-PT1H" in (merged.text or "")  # when and how changed there only: followed by itself


def test_the_same_value_written_another_valid_way_is_the_same() -> None:  # the owner
    base = calendar("SUMMARY:x", "DTSTART;TZID=Europe/Amsterdam:20261021T110000", "EXDATE;TZID=Europe/Amsterdam:20261028T110000",
                    "ATTENDEE;ROLE=CHAIR;CN=Ann:mailto:ann@example.com")
    ours = base.replace("SUMMARY:x", "SUMMARY:Ours")
    theirs = calendar("SUMMARY:x", "DTSTART;VALUE=DATE-TIME:20261021T090000Z", "EXDATE:20261028T100000Z",  # the same moments, in UTC
                      'attendee;cn="Ann";role=CHAIR:mailto:ann@example.com')
    merged = merge(base, ours, theirs)
    assert merged.clashes == () and "SUMMARY:Ours" in (merged.text or "") and (merged.text or "").count("EXDATE") == 1
    day = calendar("SUMMARY:x", "DTSTART;VALUE=DATE:20261021")
    assert merge(day, day.replace("SUMMARY:x", "SUMMARY:y"), day.replace(";VALUE=DATE", "")).clashes == ()


def test_a_value_of_a_comma_list_with_a_colon_is_shown_whole() -> None:
    base = calendar("SUMMARY:x", "RDATE:20261101T090000Z")
    merged = merge(base, calendar("SUMMARY:Ours", "RDATE:20261101T090000Z,Due: Friday"), calendar("SUMMARY:Theirs", "RDATE:20261101T090000Z"))
    assert ("Extra date", "Due: Friday", "") in [(c.label, c.local, c.server) for c in merged.clashes]


def test_capitals_in_a_value_and_a_semicolon_inside_a_part_count() -> None:
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nORG:Smith\\; Sons\r\n")
    merged = merge(base, base.replace("FN:Ann", "FN:Ann Smith"), base.replace("\\;", ";"))  # a company and a department there
    assert [clash.label for clash in merged.clashes] == ["Name", "Company"]
    event = calendar("SUMMARY:x", "ATTENDEE;CN=ann smith:mailto:ann@example.com")
    theirs = calendar("SUMMARY:Theirs", "ATTENDEE;CN=Ann Smith:mailto:ann@example.com")
    clashes = merge(event, event.replace("SUMMARY:x", "SUMMARY:Ours"), theirs).clashes
    chosen = merge(event, event.replace("SUMMARY:x", "SUMMARY:Ours"), theirs, {clash.key: SERVER for clash in clashes}).text or ""
    assert len(clashes) == 2 and "CN=Ann Smith" in chosen  # the person's Server is honoured


def test_role_and_anniversary_are_seen_and_clash() -> None:
    card = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nROLE:Engineer\r\nANNIVERSARY:20100612\r\n")
    theirs = base.replace("Engineer", "Manager").replace("20100612", "20110612")
    assert [clash.label for clash in merge(base, base.replace("FN:Ann", "FN:Ann Smith"), theirs).clashes] == ["Name", "Role", "Anniversary"]


def test_one_number_of_two_kinds_stays_twice() -> None:
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nTEL;TYPE=WORK:111\r\nTEL;TYPE=HOME:111\r\n")
    merged = merge(base, base.replace("FN:Ann", "FN:Ann Smith"), base)
    assert merged.clashes == () and "TEL;TYPE=WORK:111" in (merged.text or "") and "TEL;TYPE=HOME:111" in (merged.text or "")


def test_an_occurrence_only_rewritten_on_the_server_and_deleted_here_is_removed() -> None:  # D-308
    base = calendar("SUMMARY:Weekly", *WEEKLY, extra=OCCURRENCE)
    rewritten = tuple(line.replace("DTSTART:", "DTSTART;VALUE=DATE-TIME:") for line in OCCURRENCE[:-1]) + ("X-OTHER-PROGRAM:1", "END:VEVENT")
    merged = merge(base, calendar("SUMMARY:Weekly", *WEEKLY), calendar("SUMMARY:Weekly", *WEEKLY, extra=rewritten))
    assert merged.clashes == () and "RECURRENCE-ID" not in (merged.text or "RECURRENCE-ID")


def test_an_attendee_taken_from_here_keeps_the_server_s_answer() -> None:  # D-308
    base = calendar("SUMMARY:x", "ATTENDEE;CN=Ann;PARTSTAT=NEEDS-ACTION:mailto:ann@example.com")
    ours = calendar("SUMMARY:x", "ATTENDEE;CN=Ann Smith;PARTSTAT=NEEDS-ACTION:mailto:ann@example.com")
    theirs = calendar("SUMMARY:x", "ATTENDEE;CN=Ann;PARTSTAT=ACCEPTED:mailto:ann@example.com")
    assert "ATTENDEE;CN=Ann Smith;PARTSTAT=ACCEPTED:mailto:ann@example.com" in (merge(base, ours, theirs).text or "")  # changed here only
    both = theirs.replace("SUMMARY:x", "SUMMARY:Theirs")
    chosen = merge(base, ours.replace("SUMMARY:x", "SUMMARY:Ours"), both, {clash.key: LOCAL for clash in merge(base, ours, both).clashes}).text
    assert "ATTENDEE;CN=Ann Smith;PARTSTAT=ACCEPTED:mailto:ann@example.com" in (chosen or "")  # Chrono Mail chosen: still the server's answer


def test_another_zone_in_a_series_is_another_moment() -> None:  # it moves later occurrences across summer time
    base = calendar("SUMMARY:x", "RRULE:FREQ=WEEKLY", "DTSTART;TZID=Europe/Amsterdam:20261021T110000")
    theirs = calendar("SUMMARY:x", "RRULE:FREQ=WEEKLY", "DTSTART:20261021T090000Z")  # the same first moment, in UTC
    assert [clash.label for clash in merge(base, base.replace("SUMMARY:x", "SUMMARY:Ours"), theirs).clashes] == ["Title", "Starts"]


def test_kinds_in_another_order_and_capitals_are_the_same() -> None:
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nTEL;TYPE=work,cell:111\r\n")
    assert merge(base, base.replace("FN:Ann", "FN:Ann Smith"), base.replace("TYPE=work,cell", "TYPE=CELL,WORK")).clashes == ()


def test_two_lines_of_one_version_with_the_same_value_and_kinds_both_stay() -> None:  # OR-2
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nIMPP;X-SERVICE-TYPE=Skype:x:annb\r\nIMPP;X-SERVICE-TYPE=AIM:x:annb\r\n")
    assert (merge(base, base.replace("FN:Ann", "FN:Ann B"), base).text or "").count("IMPP") == 2
    event = calendar("SUMMARY:a", "X-FOO;A=1:v", "X-FOO;A=2:V")
    assert (merge(event, event.replace("SUMMARY:a", "SUMMARY:b"), event).text or "").count("X-FOO") == 2


def test_a_reminder_s_time_is_its_length_however_written() -> None:
    def alarm(trigger: str) -> tuple[str, ...]:
        return ("BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trigger}", "END:VALARM")
    base = calendar("SUMMARY:a", *alarm("-PT30M"))
    merged = merge(base, calendar("SUMMARY:b", *alarm("-PT30M")), calendar("SUMMARY:c", *alarm("-P0DT0H30M0S")))
    assert [clash.label for clash in merged.clashes] == ["Title"]


def test_a_date_with_hyphens_reads_as_without_them() -> None:
    card = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("FN:Ann\r\nBDAY:19800101\r\nANNIVERSARY:20100612\r\n")
    theirs = base.replace("19800101", "1980-01-01").replace("20100612", "2010-06-12")
    assert merge(base, base.replace("FN:Ann", "FN:Ann B"), theirs).clashes == ()
    event = calendar("SUMMARY:a", "DTSTART:20261021T090000Z")
    assert merge(event, event.replace("SUMMARY:a", "SUMMARY:b"), event.replace("20261021T090000Z", "2026-10-21T09:00:00Z")).clashes == ()


def test_two_changed_occurrences_of_one_start_in_one_version_both_stay() -> None:  # OR-2
    def occurrence(start: str, summary: str) -> tuple[str, ...]:
        return ("BEGIN:VEVENT", "UID:u1", start, f"SUMMARY:{summary}", "END:VEVENT")
    for second in ("RECURRENCE-ID;TZID=Europe/Amsterdam:20261104T100000", "RECURRENCE-ID:20261104T090000Z"):  # another zone, or written alike
        base = calendar("SUMMARY:m", "RRULE:FREQ=DAILY", extra=(*occurrence("RECURRENCE-ID:20261104T090000Z", "A"), *occurrence(second, "B")))
        merged = merge(base, base, base.replace("SUMMARY:m", "SUMMARY:n")).text or ""
        assert "SUMMARY:A" in merged and "SUMMARY:B" in merged and "SUMMARY:n" in merged


def test_a_setting_reads_the_same_in_any_capitals_and_a_reminder_without_action_is_a_message() -> None:
    from chrono_mail.pim.merge import same

    assert same("ATTENDEE;ROLE=chair:mailto:a@example.com", "ATTENDEE;ROLE=CHAIR:mailto:a@example.com")
    assert not same("ATTENDEE;CN=ann:mailto:a@example.com", "ATTENDEE;CN=Ann:mailto:a@example.com")  # a name is free text: capitals count
    assert same("BEGIN:VALARM\nTRIGGER:-PT5M\nEND:VALARM", "BEGIN:VALARM\nTRIGGER:-PT5M\nACTION:DISPLAY\nEND:VALARM")


def test_a_line_that_starts_with_the_separator_never_stops_a_merge() -> None:  # OR-3
    base = calendar("SUMMARY:a", "CATEGORIES:Work")
    merged = merge(base, base.replace("SUMMARY:a", "SUMMARY:b"), base.replace("CATEGORIES:Work", "CATEGORIES:Work\r\n\x1fX-A:b"))
    assert merged.clashes == () and "\x1fX-A:b" in (merged.text or "") and "CATEGORIES:Work" in (merged.text or "")


def test_a_second_copy_of_the_item_itself_is_named_so() -> None:  # OR-2
    def copy(summary: str) -> tuple[str, ...]:
        return ("BEGIN:VEVENT", "UID:u1", f"SUMMARY:{summary}", "END:VEVENT")
    base = calendar("SUMMARY:A", extra=copy("B"))
    ours, theirs = calendar("SUMMARY:A1", extra=copy("B1")), calendar("SUMMARY:A2", extra=copy("B2"))
    assert [clash.label for clash in merge(base, ours, theirs).clashes] == ["Title", "Title (second copy)"]


def test_an_empty_value_reads_as_no_line() -> None:  # Chrono Mail writes N:;;;; for a contact without name parts
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\n{}END:VCARD\r\n"
    base = card.format("N:;;;;\r\nFN:Anna\r\nNOTE:\r\n")
    merged = merge(base, card.format("N:;;;;\r\nFN:Anna B\r\nNOTE:\r\n"), card.format("FN:Ann\r\n"))
    assert [clash.label for clash in merged.clashes] == ["Name"]  # FN only: no clash for the empty name parts or the empty note
    kept = merge(base, card.format("N:;;;;\r\nFN:Anna B\r\nNOTE:\r\n"), card.format("FN:Ann\r\n"), {"FN": "local"}).text or ""
    assert "N:;;;;" in kept and "FN:Anna B" in kept  # the empty line that exists stays: vCard 3.0 needs N
    assert "N:;;;;" in (merge(base, card.format("FN:Ann\r\n"), card.format("N:;;;;\r\nFN:Ann\r\n")).text or "")  # also from the server


def test_a_start_that_ends_like_a_copy_never_takes_a_second_copy_s_key() -> None:  # OR-2
    def occurrence(start: str, summary: str) -> tuple[str, ...]:
        return ("BEGIN:VEVENT", "UID:u1", f"RECURRENCE-ID:{start}", f"SUMMARY:{summary}", "END:VEVENT")
    ours = calendar("SUMMARY:m", "RRULE:FREQ=DAILY", extra=(*occurrence("20261104T090000Z", "A"), *occurrence("20261104T090000Z", "B")))
    theirs = calendar("SUMMARY:m", "RRULE:FREQ=DAILY", extra=(*occurrence("20261104T090000Z", "A"), *occurrence("20261104T090000Z #2", "C")))
    merged = merge(None, ours, theirs)  # the garbled start is an occurrence of its own, never paired with the second copy here
    assert sorted(clash.label.rpartition(" (")[2] for clash in merged.clashes) == ["only here)", "only on the server)"]


def test_a_member_taken_out_on_one_side_stays_out_when_the_other_only_rewrote_it() -> None:  # D-194, the jar
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:g1\r\nFN:Team\r\nX-ADDRESSBOOKSERVER-KIND:group\r\n{}END:VCARD\r\n"
    base = card.format("X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:A1\r\n")
    rewritten = card.format("X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:a1\r\n")
    for local, server in ((rewritten, card.format("")), (card.format(""), rewritten)):
        merged = merge(base, local, server)
        assert merged.clashes == () and "urn:uuid:a1" not in (merged.text or "").lower()  # out, in both directions
