import base64
from datetime import UTC, datetime

import pytest

from chrono_mail.core import CollectionKind
from chrono_mail.engine.compose import suggest_filename
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.pim.itip import is_newer, read_calendar_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_engine_invitations.py 4.4.509"

# --- Version history ----------------------------------------------------
# v4.4.509: a calendar part in the character set the mail states (D-310)
# v4.4.504: Initial tests: a calendar message in a mail is read for its card, its part is not offered as a file, an empty text gives way
#           to the card, the copy in a calendar is found by its uid with your answer, an older message is told apart (D-310); a part
#           without a name gets its extension only from Chrono Mail's own list (D-311)


def calendar_text(method: str = "REQUEST", sequence: int = 0, stamp: str = "20261027T150900Z", you: str = "me@example.com",
                  answer: str = "NEEDS-ACTION", title: str = "Choice of colours") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Example//EN", *([f"METHOD:{method}"] if method else []), "BEGIN:VEVENT",
             "UID:meeting-1@example.com", f"SEQUENCE:{sequence}", f"DTSTAMP:{stamp}", "DTSTART:20261029T100000Z", "DTEND:20261029T103000Z",
             f"SUMMARY:{title}", "LOCATION:Main Street 1, Springfield", "ORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org",
             f"ATTENDEE;CN=Me;PARTSTAT={answer};RSVP=TRUE:mailto:{you}", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def invitation_mail(calendar: str, html: str = "<html><body><p>&nbsp;</p></body></html>", text: str = " ") -> bytes:
    """A mail as the owner's: a text, an HTML text and the calendar part in one multipart/alternative; the calendar part has no name."""
    encoded = base64.b64encode(calendar.encode()).decode()
    return (
        "From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation: Choice of colours\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/alternative; boundary=ALT\r\n\r\n"
        f"--ALT\r\nContent-Type: text/plain; charset=iso-8859-1\r\n\r\n{text}\r\n"
        f"--ALT\r\nContent-Type: text/html; charset=iso-8859-1\r\n\r\n{html}\r\n"
        f"--ALT\r\nContent-Type: text/calendar; charset=utf-8; method=REQUEST\r\nContent-Transfer-Encoding: base64\r\n\r\n{encoded}\r\n--ALT--\r\n"
    ).encode()


def deliver(setup: Setup, raw: bytes) -> str:
    call(setup.engine, "mail.fetch", account=setup.account_id)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset())
    rows = call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages
    return next(row.message_id for row in rows if row.subject == "Invitation: Choice of colours")


def show(setup: Setup, message: str):  # noqa: ANN201 - the command's own answer
    return call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=message)


def test_an_invitation_is_read_for_its_card_and_its_part_is_not_offered_as_a_file(setup: Setup) -> None:
    """The owner's mail of 27 October 2026: an empty text, an HTML text with only layout, and the invitation; the reader showed a white page
    and offered the invitation as a .bin file."""
    shown = show(setup, deliver(setup, invitation_mail(calendar_text())))
    card = shown.invitation
    assert card is not None and card.method == "REQUEST" and card.kind == "event" and card.title == "Choice of colours"
    assert card.location == "Main Street 1, Springfield" and card.start == datetime(2026, 10, 29, 10, tzinfo=UTC) and not card.all_day
    assert card.organizer is not None and card.organizer.name == "Olivia Organizer" and not card.organizer.you
    assert [(person.address, person.answer, person.you) for person in card.attendees] == [("me@example.com", "NEEDS-ACTION", True)]
    assert card.stored_in == "" and card.version == "new" and card.your_answer == "" and card.text_shows_nothing
    (part,) = [each for each in shown.attachments if each.content_type == "text/calendar"]
    assert part.card and part.suggested_name == "attachment-1.ics" and shown.attachments.index(part) == card.position


def test_a_mail_with_text_keeps_its_text_below_the_card(setup: Setup) -> None:
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html="<p>Please bring the samples.</p>")))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing


def test_a_calendar_file_that_asks_nothing_stays_a_file_without_a_card(setup: Setup) -> None:
    """Only a calendar message (with a method, RFC 6047) is a card; a calendar file sent along stays an ordinary attachment."""
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(method=""))))
    assert shown.invitation is None and not any(each.card for each in shown.attachments)


def test_a_damaged_calendar_part_shows_the_mail_as_it_is(setup: Setup) -> None:
    shown = show(setup, deliver(setup, invitation_mail("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nDTSTART:garbage\r\n")))
    assert shown.invitation is None and not any(each.card for each in shown.attachments)


def stored(setup: Setup, text: str) -> None:
    ensure_local_pim(setup.engine)
    with open_pim(setup.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        store.put(calendar.collection_id, text)


def test_the_copy_in_a_calendar_is_found_by_its_uid_with_your_answer(setup: Setup) -> None:
    stored(setup, calendar_text(method="", answer="ACCEPTED"))
    card = show(setup, deliver(setup, invitation_mail(calendar_text()))).invitation
    assert card is not None and card.stored_in.startswith("Calendar (") and card.version == "same" and card.your_answer == "ACCEPTED"


def test_a_message_older_than_the_copy_in_a_calendar_is_told_apart(setup: Setup) -> None:
    stored(setup, calendar_text(method="", sequence=2))
    card = show(setup, deliver(setup, invitation_mail(calendar_text(sequence=1)))).invitation
    assert card is not None and card.version == "older"


def test_the_version_of_a_calendar_message_follows_sequence_then_the_stamp() -> None:
    """RFC 5546 section 2.1.5: the higher SEQUENCE replaces every lower one; of the same SEQUENCE the later DTSTAMP."""
    message = read_calendar_message(calendar_text(sequence=1, stamp="20261027T150900Z"))
    assert message is not None and message.method == "REQUEST" and message.uid == "meeting-1@example.com" and message.sequence == 1
    assert is_newer(message, 0, None) is True and is_newer(message, 2, None) is False
    assert is_newer(message, 1, datetime(2026, 10, 27, 15, 0, tzinfo=UTC)) is True
    assert is_newer(message, 1, datetime(2026, 10, 28, tzinfo=UTC)) is False and is_newer(message, 1, message.stamp) is None
    assert read_calendar_message(calendar_text(method="SOMETHING")).method == ""  # type: ignore[union-attr] - an unknown method asks nothing
    assert read_calendar_message(b"\xff\xfe not a calendar") is None and read_calendar_message("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n") is None


def test_a_part_without_a_name_gets_its_extension_only_from_chrono_mails_own_list() -> None:
    """D-311: text/calendar gave .bin where the computer's own list has no entry for it; an unknown type gets no extension, never .bin."""
    assert suggest_filename(None, "text/calendar", 0) == "attachment-1.ics"
    assert suggest_filename("", "text/calendar; method=REQUEST", 1) == "attachment-2.ics"
    assert suggest_filename(None, "TEXT/VCARD", 0) == "attachment-1.vcf" and suggest_filename(None, "message/rfc822", 0) == "attachment-1.eml"
    assert suggest_filename(None, "application/x-unknown-kind", 2) == "attachment-3"
    assert suggest_filename(None, "application/octet-stream", 0) == "attachment-1"


def test_a_copy_that_cannot_be_read_never_stops_the_mail_from_showing(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of 4.4.504: a damaged stored item raised CryptoError (no StorageError) out of message.show."""
    from chrono_mail.core import CryptoError
    from chrono_mail.pimstore import PimStore

    stored(setup, calendar_text(method="", answer="ACCEPTED"))

    def damaged(*_: object) -> str:
        raise CryptoError("The item cannot be decrypted")

    monkeypatch.setattr(PimStore, "get", damaged)
    card = show(setup, deliver(setup, invitation_mail(calendar_text()))).invitation
    assert card is not None and card.stored_in == "" and card.version == "new"


def test_a_copy_changed_here_is_the_same_version_with_your_answer(setup: Setup) -> None:
    """Review of 4.4.504: a change made here rewrites the copy's DTSTAMP; the card said a newer version was in the calendar."""
    stored(setup, calendar_text(method="", answer="ACCEPTED", stamp="20261102T080000Z"))
    card = show(setup, deliver(setup, invitation_mail(calendar_text(stamp="20261027T150900Z")))).invitation
    assert card is not None and card.version == "same" and card.your_answer == "ACCEPTED"


def test_a_text_of_only_a_picture_is_shown_below_the_card(setup: Setup) -> None:
    """Review of 4.4.504: an HTML text of only a picture counted as showing nothing, and the picture was hidden."""
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html='<p><img src="https://example.com/logo.png"></p>')))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing


def test_the_card_takes_uid_and_contents_from_the_same_readable_item() -> None:
    """Review of 4.4.504: a damaged first event gave its uid with the second event's title."""
    text = calendar_text().replace("BEGIN:VEVENT", "BEGIN:VEVENT\r\nUID:bad@example.com\r\nDTSTART:garbage\r\nEND:VEVENT\r\nBEGIN:VEVENT", 1)
    message = read_calendar_message(text)
    assert message is not None and message.uid == "meeting-1@example.com" and message.event is not None and message.event.summary == "Choice of colours"
    task = read_calendar_message("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VTODO\r\nSUMMARY:Bring samples\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    assert task is not None and task.uid == "" and task.task is not None and task.task.uid == ""  # never a made-up uid


def test_a_message_about_one_occurrence_is_compared_with_that_occurrence(setup: Setup) -> None:
    """Review round 2 of 4.4.504: a message for one occurrence was compared with the series' main event."""
    series = calendar_text(method="").replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1)
    moved = calendar_text(method="", sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1).split("BEGIN:VEVENT", 1)[1]
    stored(setup, series.replace("END:VCALENDAR", "BEGIN:VEVENT" + moved.split("END:VCALENDAR")[0] + "END:VCALENDAR", 1).replace("END:VEVENT\r\nEND:VEVENT", "END:VEVENT", 1))
    occurrence = calendar_text(sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    card = show(setup, deliver(setup, invitation_mail(occurrence))).invitation
    assert card is not None and card.version == "same"  # the moved occurrence is already in the calendar
    other = calendar_text(sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261112T100000Z", 1)
    message = read_calendar_message(other)
    assert message is not None and message.occurrence == datetime(2026, 11, 12, 10, tzinfo=UTC)


def test_an_answer_without_a_start_still_gets_its_card() -> None:
    """Review round 2 of 4.4.504: an answer, a cancellation or a request for the latest version may leave out the start; it got no card."""
    reply = ("BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:meeting-1@example.com\r\nDTSTAMP:20261028T090000Z\r\nSUMMARY:Choice of colours\r\n"
             "ORGANIZER:mailto:me@example.com\r\nATTENDEE;CN=Sam;PARTSTAT=ACCEPTED:mailto:sam@example.net\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    message = read_calendar_message(reply)
    assert message is not None and message.method == "REPLY" and message.event is None and message.title == "Choice of colours"
    assert [(person.address, person.status) for person in message.attendees] == [("sam@example.net", "ACCEPTED")]


def test_a_copy_of_only_one_occurrence_is_found_and_versions_follow_the_series(setup: Setup) -> None:
    """Review round 3 of 4.4.504: a copy holding only one changed occurrence was "in none of your calendars"; an occurrence not changed in
    the copy has the version of its series."""
    only = calendar_text(method="", sequence=2).replace("SEQUENCE:2", "SEQUENCE:2\r\nRECURRENCE-ID:20261105T100000Z", 1)
    stored(setup, only)
    occurrence = calendar_text(sequence=2).replace("SEQUENCE:2", "SEQUENCE:2\r\nRECURRENCE-ID:20261105T100000Z", 1)
    card = show(setup, deliver(setup, invitation_mail(occurrence))).invitation
    assert card is not None and card.stored_in.startswith("Calendar (") and card.version == "same"


def test_an_old_message_about_one_occurrence_is_older_than_the_series(setup: Setup) -> None:
    stored(setup, calendar_text(method="", sequence=3).replace("SUMMARY:", "RRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:", 1))
    occurrence = calendar_text(sequence=1).replace("SEQUENCE:1", "SEQUENCE:1\r\nRECURRENCE-ID:20261105T100000Z", 1)
    card = show(setup, deliver(setup, invitation_mail(occurrence))).invitation
    assert card is not None and card.version == "older"


def test_an_all_day_invitation_can_be_written_as_json(setup: Setup) -> None:
    """Review round 4 of 4.4.504: the JSON output stopped on a whole day (a date), which it could not write."""
    from chrono_mail.engine.registry import to_data

    all_day = calendar_text().replace("DTSTART:20261029T100000Z", "DTSTART;VALUE=DATE:20261029").replace("DTEND:20261029T103000Z", "DTEND;VALUE=DATE:20261030")
    shown = show(setup, deliver(setup, invitation_mail(all_day)))
    data = to_data(shown)
    assert isinstance(data, dict) and data["invitation"]["start"] == "2026-10-29" and data["invitation"]["all_day"] is True  # type: ignore[index, call-overload]


def test_a_text_of_only_invisible_characters_shows_nothing(setup: Setup) -> None:
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html="<p>&#8203;&#65279;</p>")))
    assert shown.invitation is not None and shown.invitation.text_shows_nothing


def test_a_text_after_an_unclosed_head_is_never_hidden_behind_the_card(setup: Setup) -> None:
    """Review round 5 of 4.4.504: the index's reading skips what follows an unclosed <head>; the agenda was hidden behind the card."""
    html = "<html><head><meta charset=utf-8><body><p>Agenda: the budget</p>"
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html=html)))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing and shown.html_body is not None


def test_an_empty_html_text_gives_way_to_the_plain_text_below_the_card(setup: Setup) -> None:
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html="<p>&shy;&#8206;</p>", text="Please bring the samples.")))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing and shown.html_body is None


def test_an_organizer_written_twice_or_empty_shows_no_garbled_address() -> None:
    twice = calendar_text().replace("ORGANIZER;", "ORGANIZER:mailto:second@example.org\r\nORGANIZER;", 1)
    message = read_calendar_message(twice)
    assert message is not None and message.organizer is not None and "[" not in message.organizer.address
    empty = read_calendar_message(calendar_text().replace("ORGANIZER;CN=Olivia Organizer:mailto:olivia@example.org", "ORGANIZER:", 1))
    assert empty is not None and empty.organizer is None


def test_a_title_written_twice_shows_its_first_value() -> None:
    """Review round 6 of 4.4.504: a doubled SUMMARY, LOCATION or UID showed as a list."""
    twice = calendar_text().replace("SUMMARY:", "SUMMARY:First\r\nSUMMARY:", 1).replace("UID:meeting-1@example.com", "UID:meeting-1@example.com\r\nUID:x@example.com", 1)
    message = read_calendar_message(twice)
    assert message is not None and message.title == "First" and message.uid == "meeting-1@example.com"


def test_a_page_title_alone_shows_nothing(setup: Setup) -> None:
    html = "<html><head><title>Invitation</title></head><body><p>&nbsp;</p></body></html>"
    shown = show(setup, deliver(setup, invitation_mail(calendar_text(), html=html)))
    assert shown.invitation is not None and shown.invitation.text_shows_nothing


def test_a_logo_the_html_never_shows_keeps_the_plain_text_below_the_card(setup: Setup) -> None:
    """Review round 7 of 4.4.504: any picture with a Content-ID counted as shown, so the empty HTML page stayed instead of the plain text."""
    calendar = base64.b64encode(calendar_text().encode()).decode()
    raw = (
        "From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation: Choice of colours\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=MIX\r\n\r\n--MIX\r\nContent-Type: multipart/alternative; boundary=ALT\r\n\r\n"
        "--ALT\r\nContent-Type: text/plain\r\n\r\nAgenda: the budget\r\n--ALT\r\nContent-Type: text/html\r\n\r\n<p></p>\r\n"
        f"--ALT\r\nContent-Type: text/calendar; method=REQUEST\r\nContent-Transfer-Encoding: base64\r\n\r\n{calendar}\r\n--ALT--\r\n"
        "--MIX\r\nContent-Type: image/png\r\nContent-ID: <logo@example.org>\r\nContent-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n--MIX--\r\n"
    ).encode()
    shown = show(setup, deliver(setup, raw))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing and shown.html_body is None


def test_a_picture_the_html_shows_as_a_background_counts_as_shown(setup: Setup) -> None:
    """Review round 8 of 4.4.504: a picture shown only as a background (cid:) was shown nowhere."""
    calendar = base64.b64encode(calendar_text().encode()).decode()
    raw = (
        "From: Olivia Organizer <olivia@example.org>\r\nTo: me@example.com\r\nSubject: Invitation: Choice of colours\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/related; boundary=REL\r\n\r\n--REL\r\nContent-Type: multipart/alternative; boundary=ALT\r\n\r\n"
        "--ALT\r\nContent-Type: text/plain\r\n\r\nAgenda\r\n--ALT\r\nContent-Type: text/html\r\n\r\n<table background=\"cid:logo@example.org\"><tr><td></td></tr></table>\r\n"
        f"--ALT\r\nContent-Type: text/calendar; method=REQUEST\r\nContent-Transfer-Encoding: base64\r\n\r\n{calendar}\r\n--ALT--\r\n"
        "--REL\r\nContent-Type: image/png\r\nContent-ID: <logo@example.org>\r\nContent-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n--REL--\r\n"
    ).encode()
    shown = show(setup, deliver(setup, raw))
    assert shown.invitation is not None and not shown.invitation.text_shows_nothing and shown.html_body is not None


def test_a_remote_background_picture_counts_as_shown() -> None:
    """Review round 9 of 4.4.504: a remote background was taken for nothing, so the remote content line could never ask for it."""
    from chrono_mail.engine.invitations import shows_text

    assert shows_text('<td background="https://example.com/a.png" height=300></td>', None) == (True, False)
    assert shows_text('<div style="background:url(https://example.com/a.png);height:300px"></div>', None) == (True, False)
    assert shows_text("<style>body { background-image: url(cid:x) }</style><p></p>", None) == (True, False)
    assert shows_text("<style>p { color: red }</style><p>&nbsp;</p>", " ") == (False, False)


def test_a_calendar_part_is_read_in_the_character_set_the_mail_states(setup: Setup) -> None:
    calendar = calendar_text().replace("Choice of colours", "Caf\u00e9 ouvert").encode("iso-8859-1")
    mail = invitation_mail("X").replace(b"charset=utf-8; method=REQUEST", b"charset=iso-8859-1; method=REQUEST").replace(base64.b64encode(b"X"), base64.b64encode(calendar))
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=deliver(setup, mail))
    assert shown.invitation.title == "Caf\u00e9 ouvert"


@pytest.mark.parametrize("name", ["nonsense", "undefined", "idna"])
def test_an_unusable_character_set_is_read_as_utf_8(setup: Setup, name: str) -> None:
    mail = invitation_mail(calendar_text()).replace(b"charset=utf-8; method=REQUEST", f"charset={name}; method=REQUEST".encode())
    shown = call(setup.engine, "message.show", account=setup.account_id, folder="Inbox", message=deliver(setup, mail))
    assert shown.invitation.title == "Choice of colours"
