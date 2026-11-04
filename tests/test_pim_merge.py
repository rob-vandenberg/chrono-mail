from chrono_mail.pim.merge import BOTH, LOCAL, SERVER, copied, merge, reading

# --- Version ------------------------------------------------------------
__version__ = "test_pim_merge.py 4.4.537"

# --- Version history ----------------------------------------------------
# v4.4.537: a placeholder alarm (ACTION:NONE) reads as no reminder
# v4.3.498: a grouped line (item1.EMAIL) is its field: changed on both sides it clashes, chosen here it keeps its group, reading sees it;
#           reading leaves out an attendee line for the organizer, which a server adds (Kerio); a grouped label stays tied to its entry,
#           also through Use Chrono Mail, Keep both and a server renumbering the groups; grouped lines not changed stay as written;
#           an entry the server moved into a group that grew here stays there;
#           Keep both: an entry of a field that holds several values chosen both keeps each side's entry, a field of one value never, one
#           entry written two ways on both sides never twice; a skipped date in another zone is the same date, also in a series; a
#           copy has a uid of its own in every part and its attendees and organizer are scheduled by the client (B-30, B-31, the owner)
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


PLACEHOLDER = ("BEGIN:VALARM", "TRIGGER;VALUE=DATE-TIME:19760401T005545Z", "ACTION:NONE", "END:VALARM")


def test_a_reminder_here_and_the_server_s_placeholder_there_is_no_clash() -> None:
    merged = merge(calendar("SUMMARY:Meeting"), calendar("SUMMARY:Meeting", "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "END:VALARM"), calendar("SUMMARY:Meeting", *PLACEHOLDER))
    assert merged.clashes == () and "TRIGGER:-PT1H" in (merged.text or "")


def test_a_placeholder_is_never_shown_as_a_reminder_in_a_clash() -> None:
    def alarm(trigger: str) -> tuple[str, ...]:
        return ("BEGIN:VALARM", f"TRIGGER:{trigger}", "ACTION:DISPLAY", "END:VALARM")

    merged = merge(calendar("SUMMARY:Meeting", *PLACEHOLDER), calendar("SUMMARY:Meeting", *PLACEHOLDER, *alarm("-PT1H")), calendar("SUMMARY:Meeting", *PLACEHOLDER, *alarm("-PT2H")))
    assert [(clash.local, clash.server) for clash in merged.clashes] == [("-PT1H", "-PT2H")]


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


def contact(*body: str) -> str:
    return "\r\n".join(["BEGIN:VCARD", "VERSION:3.0", "UID:c1", "FN:Anna", *body, "END:VCARD"]) + "\r\n"


def test_both_keeps_the_entry_of_each_side_of_a_field_that_holds_several_values() -> None:  # B-31, the owner
    base = contact("EMAIL;TYPE=work:anna@old.example", "BDAY:19800412")
    ours = contact("EMAIL;TYPE=work:anna@here.example", "BDAY:19800412")
    theirs = contact("EMAIL;TYPE=work:anna@there.example", "BDAY:19800412")
    keys = [clash.key for clash in merge(base, ours, theirs).clashes]
    assert len(keys) == 2 and all(key.startswith("EMAIL") for key in keys)
    merged = merge(base, ours, theirs, {key: BOTH for key in keys})
    assert merged.text is not None and "anna@here.example" in merged.text and "anna@there.example" in merged.text and "anna@old" not in merged.text


def test_both_chooses_nothing_of_a_field_of_one_value() -> None:  # a person has one birthday (the owner)
    base, ours, theirs = contact("BDAY:19800412"), contact("BDAY:19800413"), contact("BDAY:19810513")
    merged = merge(base, ours, theirs, {"BDAY": BOTH})
    assert merged.text is None and [clash.key for clash in merged.clashes] == ["BDAY"]


def test_a_copy_has_a_uid_of_its_own_and_sends_no_invitations() -> None:  # B-30, the owner: the attendees were invited with the original
    series = calendar(
        "SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "ORGANIZER;CN=Ann:mailto:ann@example.com",
        "ATTENDEE;CN=Bob;SCHEDULE-AGENT=SERVER;PARTSTAT=ACCEPTED:mailto:bob@example.com", "BEGIN:VALARM", "ACTION:EMAIL",
        "ATTENDEE:mailto:ann@example.com", "END:VALARM",
        extra=("BEGIN:VEVENT", "UID:u1", "RECURRENCE-ID:20261028T090000Z", "SUMMARY:Moved", "END:VEVENT"),
    )
    written = copied(series, "copy@chrono-mail")
    text = (written or "").replace("\r\n ", "")  # long lines unfolded
    assert written is not None and "UID:u1" not in text and text.count("UID:copy@chrono-mail") == 2  # the series and its changed occurrence
    assert "ORGANIZER;CN=Ann;SCHEDULE-AGENT=CLIENT:mailto:ann@example.com" in text
    assert "ATTENDEE;CN=Bob;PARTSTAT=ACCEPTED;SCHEDULE-AGENT=CLIENT:mailto:bob@example.com" in text and "SCHEDULE-AGENT=SERVER" not in text
    assert "ATTENDEE:mailto:ann@example.com" in text  # a reminder's e-mail address is no invitation: as written
    assert "RRULE:FREQ=WEEKLY" in text and "SUMMARY:Moved" in text and "DTSTAMP:20261020T100000Z" in text
    card = copied(contact("NOTE:Call back"), "urn:uuid:5f0c")
    assert card is not None and "UID:urn:uuid:5f0c" in card and "UID:c1" not in card and "NOTE:Call back" in card
    assert copied("not a calendar", "x") is None


def test_both_keeps_the_skipped_dates_of_each_side() -> None:  # the owner: every visible field that holds several values
    base = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261102T100000Z")
    ours = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261102T100000Z", "EXDATE:20261109T100000Z")
    theirs = calendar("SUMMARY:Weekly", "RRULE:FREQ=WEEKLY", "DTSTART:20261102T100000Z", "EXDATE:20261116T100000Z")
    keys = [clash.key for clash in merge(base, ours, theirs).clashes]
    assert len(keys) == 2 and all(key.startswith("EXDATE") for key in keys)
    merged = merge(base, ours, theirs, {key: BOTH for key in keys})
    assert merged.text is not None and "EXDATE:20261109T100000Z,20261116T100000Z" in merged.text  # both skipped, on one line


def test_both_on_one_entry_written_two_ways_keeps_it_once_and_stays_a_choice() -> None:  # one entry: nothing to keep twice (review)
    for before, ours, theirs in (
        ("ATTENDEE:mailto:bob@example.com", "ATTENDEE;ROLE=OPT-PARTICIPANT:mailto:bob@example.com", "ATTENDEE;ROLE=CHAIR:mailto:bob@example.com"),
        ("ATTENDEE:mailto:ann@example.com", "ATTENDEE:mailto:Ann@Example.com", "ATTENDEE;RSVP=TRUE:mailto:ann@Example.com"),
    ):
        base = calendar("SUMMARY:Meeting", before)
        keys = [clash.key for clash in merge(base, calendar("SUMMARY:Ours", ours), calendar("SUMMARY:Theirs", theirs)).clashes if clash.key.startswith("ATTENDEE")]
        assert len(keys) == 1  # the same entry on both sides: one choice
        again = merge(base, calendar("SUMMARY:Ours", ours), calendar("SUMMARY:Theirs", theirs), {keys[0]: BOTH, "SUMMARY": LOCAL})
        assert again.text is None and [clash.key for clash in again.clashes] == keys  # never written twice: still the person's choice


def test_a_skipped_date_in_another_zone_is_the_same_date_also_in_a_series() -> None:  # D-308: written another valid way reads the same
    def weekly(*body: str) -> str:
        return calendar("DTSTART;TZID=Europe/Amsterdam:20261102T110000", "RRULE:FREQ=WEEKLY", *body)

    base = weekly("SUMMARY:Weekly")
    ours, theirs = weekly("SUMMARY:Ours", "EXDATE;TZID=Europe/Amsterdam:20261109T110000"), weekly("SUMMARY:Theirs", "EXDATE:20261109T100000Z")
    assert [clash.key for clash in merge(base, ours, theirs).clashes] == ["SUMMARY"]  # one date, the same on both sides: nothing to choose
    merged = merge(base, ours, theirs, {"SUMMARY": LOCAL})
    assert merged.text is not None and merged.text.count("EXDATE") == 1 and "EXDATE:20261109T100000Z" in merged.text  # once, as the server wrote it


def test_a_grouped_line_is_its_field_and_keeps_its_group() -> None:  # Apple writes item1.EMAIL with item1.X-ABLabel
    def grouped(address: str) -> str:
        return contact(f"item1.EMAIL;type=INTERNET:{address}", "item1.X-ABLabel:_$!<Other>!$_")

    base, ours, theirs = grouped("anna@old.example"), grouped("anna@here.example"), grouped("anna@there.example")
    keys = [clash.key for clash in merge(base, ours, theirs).clashes]
    assert keys and all(key.startswith("EMAIL") for key in keys)  # never silently the server's
    merged = merge(base, ours, theirs, {key: LOCAL for key in keys})
    assert merged.text is not None and "item1.EMAIL;type=INTERNET:anna@here.example" in merged.text and "anna@there" not in merged.text
    assert "item1.X-ABLabel:_$!<Other>!$_" in merged.text
    assert reading(ours) != reading(base) and reading(base) == reading(grouped("anna@old.example"))


def test_reading_leaves_out_an_attendee_line_for_the_organizer() -> None:  # Kerio adds one to a copied appointment (ROLE=CHAIR)
    def meeting(*people: str) -> str:
        return calendar("SUMMARY:Meeting", "ORGANIZER;CN=Ann;SCHEDULE-AGENT=CLIENT:mailto:ann@example.com", *people)

    bob = "ATTENDEE;CN=Bob;SCHEDULE-AGENT=CLIENT:mailto:bob@example.com"
    written = meeting(bob)
    assert reading(meeting(bob, "ATTENDEE;CN=Ann;ROLE=CHAIR;PARTSTAT=ACCEPTED:MAILTO:Ann@Example.com")) == reading(written)
    assert reading(meeting(bob, "ATTENDEE;CN=Cas:mailto:cas@example.com")) != reading(written)  # another attendee is a change
    assert reading(meeting()) != reading(written)  # and one taken out too


def test_a_grouped_label_stays_tied_to_its_entry() -> None:  # Apple's item1.X-ABLabel belongs to item1.EMAIL (review)
    base = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work")
    ours = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Home")
    theirs = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Private")
    merged = merge(base, ours, theirs, {clash.key: LOCAL for clash in merge(base, ours, theirs).clashes if clash.key.startswith("EMAIL")})
    text = merged.text or ""
    assert "item1.X-ABLabel:Private" in text and "item1.X-ABLabel:Work" not in text and "item2.X-ABLabel:Home" in text  # one label each
    both = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work", "item2.EMAIL:c@x.example", "item2.X-ABLabel:Home")
    assert any(clash.key.startswith("EMAIL") for clash in merge(base, ours, both).clashes)  # a different address added on each side


def groups_of(text: str) -> dict[str, tuple[str, str]]:
    """A card's grouped e-mail addresses: address -> (its group, its label)."""
    lines = text.replace("\r\n ", "").split("\r\n")
    labels = {line.split(".", 1)[0].lower(): line.split(":", 1)[1] for line in lines if ".X-ABLabel:" in line}
    return {line.split(":", 1)[1]: (line.split(".", 1)[0].lower(), labels.get(line.split(".", 1)[0].lower(), "")) for line in lines if ".EMAIL" in line}


def test_a_label_goes_with_its_address_whatever_group_each_side_gives_it() -> None:  # Apple's item2.X-ABLabel (review)
    def card(*entries: tuple[str, str, str]) -> str:
        return contact(*(line for group, address, label in entries for line in (f"{group}.EMAIL:{address}", f"{group}.X-ABLabel:{label}")))

    base = card(("item1", "a@x.example", "Home"), ("item2", "b@x.example", "Work"))
    ours = card(("item1", "a@x.example", "Home"), ("item2", "b2@x.example", "Work"))  # here: b changed
    theirs = card(("item1", "a@x.example", "Home"), ("item2", "c@x.example", "Other"))  # there: b taken out, c put in, in item2 too
    keys = [clash.key for clash in merge(base, ours, theirs).clashes]
    kept = groups_of(merge(base, ours, theirs, {key: LOCAL for key in keys}).text or "")
    assert kept == {"a@x.example": ("item1", "Home"), "b2@x.example": ("item2", "Work")}  # never Work lost, never Other on b2
    both = groups_of(merge(base, ours, theirs, {key: BOTH for key in keys}).text or "")
    assert both["b2@x.example"][1] == "Work" and both["c@x.example"][1] == "Other" and both["b2@x.example"][0] != both["c@x.example"][0]
    new_here = card(("item1", "a@x.example", "Home"), ("item2", "b@x.example", "Work"), ("item3", "d@x.example", "Mobile"))
    new_there = card(("item1", "a@x.example", "Home"), ("item2", "b@x.example", "Work"), ("item3", "e@x.example", "School"))
    added = groups_of(merge(base, new_here, new_there, {key: BOTH for key in (c.key for c in merge(base, new_here, new_there).clashes)}).text or "")
    assert added["d@x.example"][1] == "Mobile" and added["e@x.example"][1] == "School" and added["d@x.example"][0] != added["e@x.example"][0]


def test_a_label_changed_here_survives_the_server_renumbering_its_group() -> None:  # the server took out item1, b became item1
    base = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Home", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Work")
    ours = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Home", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Office")
    theirs = contact("item1.EMAIL:b@x.example", "item1.X-ABLabel:Work")
    merged = merge(base, ours, theirs, {clash.key: SERVER for clash in merge(base, ours, theirs).clashes})
    assert merged.text is not None and groups_of(merged.text) == {"b@x.example": ("item1", "Office")}  # the change here kept, in b's group


def test_grouped_lines_that_did_not_change_stay_exactly_as_written() -> None:  # a group may hold several entries; its capitals stay (review)
    grouped = ("ITEM1.EMAIL:a@x.example", "item1.TEL:123", "ITEM1.X-ABLabel:Work", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Home",
               "Item3.URL:https://x.example", "Item3.X-ABLabel:Site", "item4.X-ABDATE:2020-01-01", "item4.X-ABLabel:_$!<Anniversary>!$_")
    base = theirs = contact(*grouped)
    ours = contact(*grouped, "NOTE:hi")
    merged = merge(base, ours, theirs)
    assert merged.text is not None and "NOTE:hi" in merged.text
    assert all(line in merged.text.split("\r\n") for line in grouped)  # byte for byte: no item2.TEL, no item1.X-ABLabel
    assert not any(line.lower().startswith("item5") for line in merged.text.split("\r\n"))


def test_a_group_with_two_entries_of_one_side_keeps_them_together() -> None:  # review: never item2.TEL
    lines = ("item1.EMAIL:a@x.example", "item1.TEL:123", "item1.X-ABLabel:Work")
    merged = merge(contact(*lines), contact(*lines, "NOTE:hi"), contact(*lines))
    assert merged.text is not None and all(line in merged.text.split("\r\n") for line in lines)


def test_a_label_keeps_the_capitals_of_its_group() -> None:  # review: ITEM1.X-ABLabel never becomes item1.X-ABLabel
    lines = ("ITEM1.EMAIL:a@x.example", "ITEM1.X-ABLabel:Work")
    merged = merge(contact(*lines), contact(*lines, "NOTE:hi"), contact(*lines))
    assert merged.text is not None and "ITEM1.X-ABLabel:Work" in merged.text.split("\r\n")


def test_an_entry_the_server_moved_into_a_group_that_grew_here_stays_there() -> None:  # review: never item3 without its label
    base = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Home")
    ours = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work", "item1.TEL:9", "item2.EMAIL:b@x.example", "item2.X-ABLabel:Home")
    theirs = contact("item1.EMAIL:a@x.example", "item1.X-ABLabel:Work", "item1.EMAIL:b@x.example")  # b moved into a's group
    merged = merge(base, ours, theirs)
    lines = (merged.text or "").split("\r\n")
    assert merged.text is not None and {"item1.EMAIL:a@x.example", "item1.EMAIL:b@x.example", "item1.TEL:9", "item1.X-ABLabel:Work"} <= set(lines)
    assert not any(line.lower().startswith(("item2", "item3")) for line in lines)  # the server's move kept, b in a's group
