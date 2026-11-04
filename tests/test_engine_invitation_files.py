import base64
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, FolderRole
from chrono_mail.engine.invitations import card_copies, is_calendar_part, own_mail
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.commands_invitations import MAXIMUM_INVITATION_BYTES, SourceParameters, invitation_card
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.registry import UsageError
from chrono_mail.mime import Attachment
from chrono_mail.pim.itip import read_calendar_message, text_items
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_engine_invitation_answers import drafts, kept
from tests.test_engine_invitation_tasks import kept_tasks, stored_task, task_list, task_text
from tests.test_engine_invitations import calendar_text, deliver, stored

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitation_files.py 4.4.529"

# --- Version history ----------------------------------------------------
# v4.4.529: The clash warning on a series names how many and when; invitations and .ics files up to 1 MB (D-310)
# v4.4.527: what you have at that time, one invitation shown once (D-310)
# v4.4.525: Add all skips what is kept in any calendar; how many are left; a mail in Sent or Drafts (D-310)
# v4.4.515: every item of a text comes as a block, step by step, however many it holds (D-310)
# v4.4.514: an export read back identical, every time zone kept, several calendars in one text (D-310)
# v4.4.508: a text with several items: each its own card and answer, of every kind; invitation.add with Replace and Skip (D-310)
# v4.4.507: Initial tests: an .ics attachment or file as a card, every command on it, several items added each on its own (D-310 step 4)


def two_events(method: str = "") -> str:
    def event(number: int) -> list[str]:
        return ["BEGIN:VEVENT", f"UID:item-{number}@example.com", "DTSTAMP:20261028T090000Z", f"DTSTART:2026110{number}T100000Z", f"DTEND:2026110{number}T110000Z", f"SUMMARY:Item {number}", "END:VEVENT"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", *([f"METHOD:{method}"] if method else []), *event(1), *event(2), "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def mail_with_file(calendar: str, name: str = "agenda.ics") -> bytes:
    encoded = base64.b64encode(calendar.encode()).decode()
    return (
        "From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation: Choice of colours\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=MIX\r\n\r\n--MIX\r\nContent-Type: text/plain\r\n\r\nSee the file.\r\n"
        f'--MIX\r\nContent-Type: application/octet-stream; name="{name}"\r\nContent-Disposition: attachment; filename="{name}"\r\n'
        f"Content-Transfer-Encoding: base64\r\n\r\n{encoded}\r\n--MIX--\r\n"
    ).encode()


def calendar_id(setup: Setup) -> str:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        return calendar.collection_id


def file_with(tmp_path: Path, text: str | bytes, name: str = "agenda.ics") -> str:
    path = tmp_path / name
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return str(path)


def on_file(setup: Setup, command: str, path: str, **more: object):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, command, account="", folder="", message="", file=path, **more)


def test_an_attachment_without_a_method_is_a_card_offered_to_keep(setup: Setup) -> None:
    message = deliver(setup, mail_with_file(calendar_text(method="")))
    position = next(index for index, each in enumerate(call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).attachments) if each.filename == "agenda.ics")
    card = call(setup.engine, "invitation.card", account=setup.account_id, folder="Inbox", message=message, part=position)
    assert card.method == "PUBLISH" and card.plain and card.items == 1 and card.position == position and card.title == "Choice of colours"


def test_a_file_with_a_method_works_as_the_mail_would_and_the_answer_is_a_mail_of_its_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text())
    calendar = calendar_id(setup)
    card = on_file(setup, "invitation.card", path)
    assert card.method == "REQUEST" and not card.plain and card.position == -1
    result = on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar, mail=True)
    assert result.stored_in and result.draft_message and "PARTSTAT=ACCEPTED" in kept(setup)[0]
    assert b"In-Reply-To" not in drafts(setup)[0]


def test_a_file_that_asks_nothing_is_added_as_it_is(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text(method=""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar_id(setup), mail=True)
    assert "UID:meeting-1@example.com" in kept(setup)[0] and drafts(setup) == []
    card = on_file(setup, "invitation.card", path)
    assert card.stored_in  # the card says it is kept now


def test_every_item_of_a_text_comes_as_a_block_step_by_step(setup: Setup, tmp_path: Path) -> None:  # noqa: F811
    events = "".join(f"BEGIN:VEVENT\r\nUID:big-{number}@example.com\r\nDTSTAMP:20261028T090000Z\r\nDTSTART:20261110T100000Z\r\nSUMMARY:Item {number}\r\nEND:VEVENT\r\n" for number in range(130))
    path = tmp_path / "big.ics"
    path.write_text(f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{events}END:VCALENDAR\r\n", newline="")
    card = call(setup.engine, "invitation.card", account="", folder="", message="", file=str(path))
    assert card.items == 130 and 0 < len(card.parts) < 130
    seen = [part.title for part in card.parts]
    while len(seen) < 130:
        seen += [part.title for part in call(setup.engine, "invitation.blocks", account="", folder="", message="", file=str(path), first=len(seen), count=40).parts]
    assert seen == [f"Item {number}" for number in range(130)]
    assert call(setup.engine, "invitation.blocks", account="", folder="", message="", file=str(path), first=130, count=40).parts == ()


def test_several_items_each_become_an_item_of_their_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events())
    calendar = calendar_id(setup)
    card = on_file(setup, "invitation.card", path)
    assert card.items == 2 and [each.title for each in card.parts] == ["Item 1", "Item 2"] and [each.item for each in card.parts] == [0, 1]
    assert on_file(setup, "invitation.add", path, calendar=calendar).added == 2
    assert sorted("Item 1" in text for text in kept(setup)) == [False, True] and len(kept(setup)) == 2


def test_a_shared_message_with_several_items_is_never_put_in_one_object(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events("PUBLISH"))
    on_file(setup, "invitation.add", path, calendar=calendar_id(setup))
    assert len(kept(setup)) == 2 and all(text.count("BEGIN:VEVENT") == 1 for text in kept(setup))


def test_a_command_about_one_item_needs_to_know_which(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events())
    with pytest.raises(UsageError, match="Choose one of these items"):
        on_file(setup, "invitation.answer", path, answer="DECLINED", calendar=calendar_id(setup), mail=False)
    with pytest.raises(UsageError, match="Choose one of these items"):
        on_file(setup, "invitation.apply", path)
    with pytest.raises(UsageError, match="no such item"):
        on_file(setup, "invitation.apply", path, item=2)


def test_the_calendar_list_for_all_events_knows_of_no_one_copy(setup: Setup, tmp_path: Path) -> None:
    calendar = calendar_id(setup)
    path = file_with(tmp_path, two_events())
    on_file(setup, "invitation.add", path, calendar=calendar)
    assert on_file(setup, "invitation.choices", path, kind="event").stored == ""


def mixed(method: str = "") -> str:
    task = task_text(method="").split("BEGIN:VTODO")[1].split("END:VTODO")[0]
    journal = "BEGIN:VJOURNAL\r\nUID:diary-1@example.com\r\nDTSTAMP:20261028T090000Z\r\nSUMMARY:Diary\r\nEND:VJOURNAL\r\n"
    return two_events(method).replace("END:VCALENDAR", f"BEGIN:VTODO{task}END:VTODO\r\n{journal}END:VCALENDAR")


def test_a_file_with_events_tasks_and_a_journal_entry_says_what_it_holds(setup: Setup, tmp_path: Path) -> None:
    card = on_file(setup, "invitation.card", file_with(tmp_path, mixed()))
    assert (card.items, card.events, card.tasks, card.journals) == (3, 2, 1, 1)
    assert [each.kind for each in card.parts] == ["event", "event", "task"]


def test_every_item_goes_where_it_belongs_each_its_own_object(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, mixed())
    assert on_file(setup, "invitation.add", path, calendar=calendar_id(setup), task_list=task_list(setup)).added == 3
    assert len(kept(setup)) == 2 and len(kept_tasks(setup)) == 1
    assert all("VTODO" not in text and "VJOURNAL" not in text for text in kept(setup)) and "VEVENT" not in kept_tasks(setup)[0]


def test_a_file_whose_first_item_is_a_journal_entry_still_shows_the_others(setup: Setup, tmp_path: Path) -> None:
    text = calendar_text(method="").replace("BEGIN:VEVENT", "BEGIN:VJOURNAL\r\nUID:diary-1@example.com\r\nEND:VJOURNAL\r\nBEGIN:VEVENT", 1)
    card = on_file(setup, "invitation.card", file_with(tmp_path, text))
    assert card.title == "Choice of colours" and card.journals == 1 and card.items == 1 and not card.parts


def test_a_task_in_a_mixed_file_is_added_on_its_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, mixed())
    on_file(setup, "invitation.answer", path, item=2, answer="ACCEPTED", calendar=task_list(setup), mail=False)
    assert len(kept_tasks(setup)) == 1 and "VEVENT" not in kept_tasks(setup)[0]
    card = on_file(setup, "invitation.card", path)
    assert [bool(each.stored_in) for each in card.parts] == [False, False, True]


def test_an_invitation_among_several_is_answered_on_its_own(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events("REQUEST").replace("END:VEVENT", "ORGANIZER:mailto:olivia@example.org\r\nATTENDEE:mailto:me@example.com\r\nEND:VEVENT"))
    card = on_file(setup, "invitation.card", path)
    assert [each.method for each in card.parts] == ["REQUEST", "REQUEST"]
    with pytest.raises(UsageError, match="ask for an answer"):
        on_file(setup, "invitation.add", path, calendar=calendar_id(setup))
    deliver(setup, mail_with_file(calendar_text(method="")))  # an account to answer with
    message = deliver(setup, mail_with_file(two_events("REQUEST").replace("END:VEVENT", "ORGANIZER:mailto:olivia@example.org\r\nATTENDEE:mailto:me@example.com\r\nEND:VEVENT")))
    position = next(index for index, each in enumerate(call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message).attachments) if each.filename == "agenda.ics")
    call(setup.engine, "invitation.answer", account=setup.account_id, folder="Inbox", message=message, part=position, item=1, answer="ACCEPTED", calendar=calendar_id(setup), mail=False)
    assert len(kept(setup)) == 1 and "Item 2" in kept(setup)[0] and "PARTSTAT=ACCEPTED" in kept(setup)[0]


def test_an_item_in_a_calendar_is_handled_so_add_all_skips_it_and_writes_nothing_over_it(setup: Setup, tmp_path: Path) -> None:
    calendar = calendar_id(setup)
    on_file(setup, "invitation.add", file_with(tmp_path, two_events()), calendar=calendar)
    changed = two_events().replace("SUMMARY:Item 1", "SUMMARY:Item one").replace("SUMMARY:Item 2", "SUMMARY:Item two")
    path = file_with(tmp_path, changed.replace("END:VCALENDAR", "BEGIN:VEVENT\r\nUID:item-3@example.com\r\nDTSTAMP:20261028T090000Z\r\nDTSTART:20261103T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR"), "new.ics")
    assert on_file(setup, "invitation.card", path).left == 1
    result = on_file(setup, "invitation.add", path, calendar=calendar)
    assert (result.added, result.skipped) == (1, 2) and not any("Item one" in text for text in kept(setup)) and len(kept(setup)) == 3
    assert on_file(setup, "invitation.card", path).left == 0


def test_an_item_in_another_calendar_is_skipped_too_and_never_there_twice(setup: Setup, tmp_path: Path) -> None:
    calendar = calendar_id(setup)
    path = file_with(tmp_path, two_events())
    on_file(setup, "invitation.add", path, calendar=calendar)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        second = store.create_collection(CollectionKind.EVENTS, "Second").collection_id
    assert on_file(setup, "invitation.add", path, calendar=second).skipped == 2
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        assert sum(len(store.index.item_ids(each.collection_id)) for each in store.collections(CollectionKind.EVENTS)) == 2


def test_the_blocks_say_how_many_items_are_left_to_add(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events())
    assert on_file(setup, "invitation.card", path).left == 2
    on_file(setup, "invitation.answer", path, item=0, answer="ACCEPTED", calendar=calendar_id(setup))
    assert on_file(setup, "invitation.blocks", path, first=0, count=1).left == 1


def test_a_mail_in_sent_or_drafts_is_your_own_and_one_in_the_inbox_is_not(setup: Setup) -> None:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        assert own_mail(mailbox, mailbox.role_folder(FolderRole.SENT)) == "sent" and own_mail(mailbox, mailbox.role_folder(FolderRole.DRAFTS)) == "draft"
        assert own_mail(mailbox, ("Inbox",)) == ""


def timed(uid: str, method: str = "", start: str = "20261103T100000Z", end: str = "20261103T110000Z", more: str = "") -> str:
    head = f"METHOD:{method}\r\n" if method else ""
    people = "ORGANIZER:mailto:olivia@example.org\r\nATTENDEE:mailto:me@example.com\r\n" if method else ""
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n{head}BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261028T090000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
            f"SUMMARY:Item {uid}\r\n{people}{more}END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.mark.parametrize(("name", "invitation", "kept_more", "found"), [
    ("the same time", timed("new", "REQUEST"), "", ("Item old",)),
    ("partly the same time", timed("new", "REQUEST", "20261103T103000Z", "20261103T113000Z"), "", ("Item old",)),
    ("right after it", timed("new", "REQUEST", "20261103T110000Z", "20261103T120000Z"), "", ()),
    ("a time that is not blocked", timed("new", "REQUEST"), "TRANSP:TRANSPARENT\r\n", ()),
])
def test_the_choices_say_what_you_already_have_at_that_time(setup: Setup, tmp_path: Path, name: str, invitation: str, kept_more: str, found: tuple[str, ...]) -> None:
    on_file(setup, "invitation.answer", file_with(tmp_path, timed("old", more=kept_more), "old.ics"), answer="ACCEPTED", calendar=calendar_id(setup))
    assert on_file(setup, "invitation.choices", file_with(tmp_path, invitation, "new.ics")).clashes == found


def test_a_series_names_every_date_it_clashes_with_in_the_coming_year_and_no_past_one(setup: Setup, tmp_path: Path) -> None:
    def stamp(moment: date, hour: int) -> str:
        return f"{moment:%Y%m%d}T{hour:02}0000Z"

    today = datetime.now(UTC).date()
    soon, far, past = today + timedelta(days=7), today + timedelta(days=7 * 60), today - timedelta(days=7)
    calendar = calendar_id(setup)
    for name, day in (("soon", soon), ("far", far), ("past", past)):
        on_file(setup, "invitation.answer", file_with(tmp_path, timed(name, start=stamp(day, 10), end=stamp(day, 11)), f"{name}.ics"), answer="ACCEPTED", calendar=calendar)
    series = timed("new", "REQUEST", stamp(past, 10), stamp(past, 11), "RRULE:FREQ=WEEKLY\r\n")
    choices = on_file(setup, "invitation.choices", file_with(tmp_path, series, "new.ics"))
    assert [each.title for each in choices.series_clashes] == ["Item soon"] and choices.series_clashes[0].start.startswith(soon.isoformat())
    assert on_file(setup, "invitation.choices", file_with(tmp_path, timed("one", "REQUEST", stamp(soon, 10), stamp(soon, 11)), "one.ics")).series_clashes == ()


def test_one_invitation_is_one_card_also_when_the_mail_carries_it_again_as_a_file() -> None:
    text = timed("one", "REQUEST").encode()
    parts = [Attachment(None, "text/calendar", None, False, text), Attachment("invite.ics", "application/ics", None, False, text),
             Attachment("other", "application/octet-stream", None, False, timed("two").encode()), Attachment("report.pdf", "application/pdf", None, False, b"%PDF")]
    assert card_copies(parts, 0) == {0, 1}
    newer = timed("one", "REQUEST", "20261104T100000Z", "20261104T110000Z").encode()
    assert card_copies([parts[0], Attachment("invite.ics", "text/calendar", None, False, newer)], 0) == {0}  # another version stays a file
    assert is_calendar_part(parts[2]) and not is_calendar_part(parts[3])


def test_add_needs_a_place_for_each_kind_the_file_holds_and_writes_nothing_without(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, mixed())
    with pytest.raises(UsageError, match="Choose the task list"):
        on_file(setup, "invitation.add", path, calendar=calendar_id(setup))
    assert kept(setup) == []


def test_two_items_without_a_uid_are_two_items_that_are_found_again(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, two_events().replace("UID:item-1@example.com\r\n", "").replace("UID:item-2@example.com\r\n", ""))
    calendar = calendar_id(setup)
    assert on_file(setup, "invitation.add", path, calendar=calendar).added == 2
    assert on_file(setup, "invitation.add", path, calendar=calendar).skipped == 2
    assert all(each.stored_in for each in on_file(setup, "invitation.card", path).parts)


def test_a_damaged_item_is_left_out_and_counted() -> None:
    damaged = two_events().replace("DTSTART:20261102T100000Z", "DTSTART:nonsense")
    found = text_items(damaged)
    assert len(found.texts) == 1 and found.damaged == 1


def test_an_item_keeps_the_time_zone_it_uses_and_no_other() -> None:
    zones = "".join(f"BEGIN:VTIMEZONE\r\nTZID:{zone}\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:+0100\r\nTZOFFSETTO:+0100\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n" for zone in ("Here/One", "Here/Two"))
    text = two_events().replace("BEGIN:VEVENT", zones + "BEGIN:VEVENT", 1).replace("DTSTART:20261101T100000Z", "DTSTART;TZID=Here/One:20261101T100000")
    first = text_items(text).texts[0]
    assert "TZID:Here/One" in first and "TZID:Here/Two" not in first and "Item 2" not in first
    assert read_calendar_message(first) is not None and "Item 2" in text_items(text).texts[1] and "Item 1" not in text_items(text).texts[1]


ZONE: str = "BEGIN:VTIMEZONE\r\nTZID:{name}\r\nBEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:{offset}\r\nTZOFFSETTO:{offset}\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"


def kept_event(uid: str, zone: str, offset: str, more: str = "") -> str:
    """A calendar object as a calendar holds it: everything a plain import would drop or rewrite is in it."""
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//Example//EN\r\n" + ZONE.format(name=zone, offset=offset) +
            f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261028T090000Z\r\nCREATED:20260101T090000Z\r\nSUMMARY:Item {uid}\r\nDTSTART;TZID={zone}:20261101T100000\r\n"
            f"DTEND;TZID={zone}:20261101T110000\r\nRRULE:FREQ=WEEKLY;COUNT=5\r\nEXDATE;TZID={zone}:20261108T100000\r\nCLASS:PRIVATE\r\nTRANSP:TRANSPARENT\r\nPRIORITY:3\r\n"
            "URL:https://example.com/agenda\r\nGEO:52.1;5.1\r\nCONTACT:Sam\r\nCATEGORIES:Work,Home\r\nX-OWN-MARK:kept\r\nORGANIZER;CN=Olivia:mailto:olivia@example.org\r\n"
            "ATTENDEE;CN=Me;PARTSTAT=ACCEPTED;ROLE=OPT-PARTICIPANT:mailto:me@example.com\r\n"
            f"BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Soon\r\nTRIGGER;VALUE=DATE-TIME:20261101T090000Z\r\nEND:VALARM\r\nEND:VEVENT\r\n{more}END:VCALENDAR\r\n")


def changed_date(uid: str, zone: str) -> str:
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261028T090000Z\r\nRECURRENCE-ID;TZID={zone}:20261115T100000\r\nSUMMARY:Moved\r\n"
            f"DTSTART;TZID={zone}:20261115T120000\r\nDTEND;TZID={zone}:20261115T130000\r\nEND:VEVENT\r\n")


def test_an_export_read_back_gives_every_item_identical(setup: Setup, tmp_path: Path) -> None:
    """Decision 1: Chrono Mail reads back what it exported, complete and unchanged; here with one zone name for two zones, as one program writes for any zone."""
    calendar = calendar_id(setup)
    objects = [kept_event("one@example.com", "Custom", "+0100", changed_date("one@example.com", "Custom")), kept_event("two@example.com", "Custom", "+0500"),
               kept_event("three@example.com", "Here/Other", "-0300")]
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        for each in objects:
            store.put(calendar, each)
        second = store.create_collection(CollectionKind.EVENTS, "Second").collection_id
    exported = call(setup.engine, "event.export", calendar=calendar)
    path = file_with(tmp_path, exported.text)
    assert on_file(setup, "invitation.card", path).items == 3
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:  # what is still in a calendar is handled: a restore brings back what is gone
        for item in store.item_ids(calendar):
            store.delete(calendar, item)
    assert on_file(setup, "invitation.add", path, calendar=second).added == 3
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        came_back = [store.get(second, item) for item in store.item_ids(second)]
    def lines(text: str) -> list[str]:
        return sorted(text.replace("\r\n ", "").split("\r\n"))
    assert sorted(map(lines, came_back)) == sorted(map(lines, objects))


def test_an_exported_task_list_comes_back_too(setup: Setup, tmp_path: Path) -> None:
    objects = [task_text(method="").replace("BEGIN:VTODO", "BEGIN:VTODO\r\nX-OWN-MARK:kept").replace("UID:", f"UID:{number}-") for number in range(2)]
    path = file_with(tmp_path, "".join(objects))
    assert on_file(setup, "invitation.card", path).tasks == 2
    place = task_list(setup)
    assert on_file(setup, "invitation.add", path, task_list=place).added == 2
    assert all("X-OWN-MARK:kept" in each for each in kept_tasks(setup))


def test_every_date_keeps_its_time_zone_also_one_used_only_in_a_reminder_or_a_skipped_date() -> None:
    """Decision 3: an item takes along every time zone used anywhere inside it."""
    zones = "".join(ZONE.format(name=name, offset="+0100") for name in ("Here/Alarm", "Here/Skipped", "Here/Unused"))
    alarm = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Soon\r\nTRIGGER;VALUE=DATE-TIME;TZID=Here/Alarm:20261101T090000\r\nEND:VALARM\r\n"
    text = two_events().replace("BEGIN:VEVENT", zones + "BEGIN:VEVENT", 1).replace("END:VEVENT", f"RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE;TZID=Here/Skipped:20261102T100000\r\n{alarm}END:VEVENT", 1)
    first = text_items(text).texts[0]
    assert "TZID:Here/Alarm" in first and "TZID:Here/Skipped" in first and "TZID:Here/Unused" not in first


def test_a_file_of_several_calendars_reads_as_every_item_also_for_one_item_after_a_calendar_with_none() -> None:
    empty = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//Example//EN\r\nEND:VCALENDAR\r\n"
    assert len(text_items(two_events() + two_events().replace("item-", "more-")).texts) == 4
    message = read_calendar_message(empty + two_events())
    assert message is not None and message.uid == "item-1@example.com"


def test_a_file_of_tasks_goes_to_a_task_list(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, task_text(method=""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=task_list(setup), mail=False)
    assert len(kept_tasks(setup)) == 1


def test_a_newer_file_updates_the_kept_copy(setup: Setup, tmp_path: Path) -> None:
    stored_task(setup, task_text(method="", sequence=1))
    assert on_file(setup, "invitation.apply", file_with(tmp_path, task_text(method="", sequence=2, due="20261108T170000Z"))).done == "updated"
    assert "DUE:20261108T170000Z" in kept_tasks(setup)[0]


def test_an_item_without_a_uid_gets_one_from_the_import(setup: Setup, tmp_path: Path) -> None:
    path = file_with(tmp_path, calendar_text(method="").replace("UID:meeting-1@example.com\r\n", ""))
    on_file(setup, "invitation.answer", path, answer="ACCEPTED", calendar=calendar_id(setup), mail=False)
    assert "UID:" in kept(setup)[0]


def test_a_file_with_a_byte_order_mark_is_read(setup: Setup, tmp_path: Path) -> None:
    assert on_file(setup, "invitation.card", file_with(tmp_path, b"\xef\xbb\xbf" + calendar_text().encode())).title == "Choice of colours"


@pytest.mark.parametrize("text", ["not a calendar", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                                  "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VJOURNAL\r\nUID:a\r\nEND:VJOURNAL\r\nEND:VCALENDAR\r\n"], ids=["text", "empty", "journal"])
def test_a_text_with_nothing_to_show_is_refused(setup: Setup, tmp_path: Path, text: str) -> None:
    with pytest.raises(UsageError, match="no calendar item"):
        on_file(setup, "invitation.card", file_with(tmp_path, text))


def test_a_file_that_is_gone_or_too_large_is_refused(setup: Setup, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="could not be read"):
        on_file(setup, "invitation.card", str(tmp_path / "gone.ics"))
    big = tmp_path / "big.ics"
    with big.open("wb") as file:
        file.truncate(MAXIMUM_INVITATION_BYTES + 1)
    with pytest.raises(UsageError, match="larger than 1 MB"):
        invitation_card(setup.engine, SourceParameters("", "", "", file=str(big)))


def test_an_attachment_position_that_does_not_exist_is_refused(setup: Setup) -> None:
    message = deliver(setup, mail_with_file(calendar_text(method="")))
    with pytest.raises(UsageError, match="no such attachment"):
        call(setup.engine, "invitation.card", account=setup.account_id, folder="Inbox", message=message, part=9)


def test_the_kept_copy_of_a_single_file_is_shown_on_the_card(setup: Setup, tmp_path: Path) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    card = on_file(setup, "invitation.card", file_with(tmp_path, calendar_text(method="")))
    assert card.stored_in and card.your_answer == "ACCEPTED"


def test_the_items_of_a_text_are_counted_by_their_uid() -> None:
    series = calendar_text(method="").replace("END:VEVENT", "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:meeting-1@example.com\r\nRECURRENCE-ID:20261105T100000Z\r\nDTSTART:20261105T110000Z\r\nEND:VEVENT")
    assert len(text_items(series).texts) == 1 and len(text_items(two_events()).texts) == 2 and text_items("nothing").texts == ()
