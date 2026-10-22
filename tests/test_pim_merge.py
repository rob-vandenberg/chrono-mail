from chrono_mail.pim.merge import LOCAL, SERVER, merge

# --- Version ------------------------------------------------------------
__version__ = "test_pim_merge.py 1.1.225"

# --- Version history ----------------------------------------------------
# v1.1.225: Initial: the three-way comparison field by field, every rule the owner chose (F1 to F5, D-194)


def calendar(*body: str, component: str = "VEVENT", extra: tuple[str, ...] = ()) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", f"BEGIN:{component}", "UID:u1", "DTSTAMP:20261020T100000Z", *body, f"END:{component}", *extra, "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


BASE = calendar("SUMMARY:Meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")


def test_changes_to_different_fields_are_both_kept() -> None:
    ours = calendar("SUMMARY:Team meeting", "LOCATION:Room 1", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    theirs = calendar("SUMMARY:Meeting", "LOCATION:Room 2", "DTSTART:20261021T090000Z", "DESCRIPTION:Agenda")
    merged = merge(BASE, ours, theirs)
    assert merged.clashes == () and merged.text is not None
    assert "SUMMARY:Team meeting" in merged.text and "LOCATION:Room 2" in merged.text and merged.text.endswith("\r\n")


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
    merged = merge(base, ours, theirs)
    assert merged.clashes == () and merged.text is not None and "LOCATION:Here" in merged.text and "LOCATION:There" in merged.text


def test_lists_are_merged_entry_by_entry() -> None:  # F2
    base = calendar("SUMMARY:x", "CATEGORIES:Work", "EXDATE:20261028T090000Z", "ATTENDEE:mailto:ann@example.com")
    ours = calendar("SUMMARY:x", "CATEGORIES:Work,Home", "EXDATE:20261028T090000Z,20261104T090000Z", "ATTENDEE:mailto:ann@example.com", "ATTENDEE:mailto:bob@example.com")
    theirs = calendar("SUMMARY:x", "CATEGORIES:Club", "EXDATE:20261028T090000Z", "ATTENDEE:mailto:ann@example.com", "ATTENDEE:mailto:cee@example.com")
    merged = merge(base, ours, theirs)
    assert merged.clashes == () and merged.text is not None
    assert "CATEGORIES:Home,Club" in merged.text  # added on both sides; Work removed on the server
    assert "EXDATE:20261028T090000Z,20261104T090000Z" in merged.text
    assert "bob@example.com" in merged.text and "cee@example.com" in merged.text and "ann@example.com" in merged.text
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:c1\r\nFN:Ann\r\n{}END:VCARD\r\n"
    merged_card = merge(card.format("TEL:111\r\n"), card.format("TEL:111\r\nTEL:222\r\n"), card.format("TEL:111\r\nTEL:333\r\n"))
    assert merged_card.clashes == () and all(number in (merged_card.text or "") for number in ("111", "222", "333"))


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
