import json
import tomllib
import urllib.parse

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ServerSettings
from chrono_mail.core.errors import GMAIL_PASSWORD_ADVICE
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.smtp import check_login
from chrono_mail.config.google import google_client_codes
from chrono_mail.protocols.dav import DavClient
from chrono_mail.protocols.google import CALDAV_START, CARDDAV_START, GoogleClient, refresh_access
from tests.live.account import SETTINGS_DIRECTORY

# --- Version ------------------------------------------------------------
__version__ = "test_live_gmail.py 4.4.599"

# --- Version history ----------------------------------------------------
# v4.4.599: the labels of a Google calendar read with their names, one given to an event and taken off again, CalDAV carrying neither (D-338)
# v4.4.598: an event made through CalDAV gets a colour through the Calendar API, a changed date of a series too, and CalDAV still carries none (D-337)
# v4.4.597: the own Google calendar is the account's own; Google Calendar keeps calendars, Google Contacts address books (D-335)
# v4.4.593: Google sign-in's access code renewed and Google Calendar and Google Contacts reached with it (D-335)
# v4.4.592: Initial live tests on the Gmail test account: login with an app password, Gmail's folders and roles, a refused password's advice (D-335)

pytestmark = pytest.mark.live

GMAIL_FILE = SETTINGS_DIRECTORY / "gmail.local.toml"  # user and password (an app password) of a disposable Gmail account


def gmail_login() -> tuple[str, str]:
    if not GMAIL_FILE.exists():
        pytest.skip(f"{GMAIL_FILE} is missing (user and an app password of a disposable Gmail account)")
    login = tomllib.loads(GMAIL_FILE.read_text())
    return str(login["user"]), str(login["password"])


def test_gmail_takes_the_app_password_and_marks_all_mail_and_trash() -> None:
    user, password = gmail_login()
    with ImapClient(ServerSettings("imap.gmail.com", 993, ConnectionSecurity.TLS, user), password) as client:
        assert "X-GM-EXT-1" in client.capabilities
        folders = {entry.name: entry.attributes for entry in client.list_folders()}
    assert "\\all" in folders["[Gmail]/All Mail"] and "\\trash" in folders["[Gmail]/Trash"]
    check_login(ServerSettings("smtp.gmail.com", 465, ConnectionSecurity.TLS, user), password)


def test_a_wrong_password_on_gmail_says_how_to_make_an_app_password() -> None:
    user, _ = gmail_login()
    with pytest.raises(AuthenticationError) as refused:
        with ImapClient(ServerSettings("imap.gmail.com", 993, ConnectionSecurity.TLS, user), "not-the-app-password"):
            pass
    assert str(refused.value).startswith(GMAIL_PASSWORD_ADVICE + " (Gmail said: ")


GOOGLE_TOKEN_FILE = SETTINGS_DIRECTORY / "gmail-google-token.json"  # the test account's Google sign-in (scratchpad/gmail_live_check.py google)


def test_google_calendar_and_contacts_are_reached_with_the_renewed_access_code() -> None:
    user, _ = gmail_login()
    codes = google_client_codes()
    if codes is None or not GOOGLE_TOKEN_FILE.exists():
        pytest.skip("the Google client file or the test account's Google sign-in is missing")
    access, seconds = refresh_access(GoogleClient(*codes), json.loads(GOOGLE_TOKEN_FILE.read_text())["refresh_token"])
    assert seconds > 60
    with DavClient(CALDAV_START.format(address=urllib.parse.quote(user)), "", "", bearer=access) as calendars:
        assert any(collection.kind == "events" for collection in calendars.all_collections())
    with DavClient(CARDDAV_START, "", "", bearer=access) as contacts:
        assert any(collection.kind == "contacts" for collection in contacts.all_collections())


def test_googles_own_calendar_is_yours_and_each_google_account_keeps_its_own_kind() -> None:
    from chrono_mail.engine.google_access import google_kinds

    user, _ = gmail_login()
    codes = google_client_codes()
    if codes is None or not GOOGLE_TOKEN_FILE.exists():
        pytest.skip("the Google client file or the test account's Google sign-in is missing")
    access, _ = refresh_access(GoogleClient(*codes), json.loads(GOOGLE_TOKEN_FILE.read_text())["refresh_token"])
    calendars_url = CALDAV_START.format(address=urllib.parse.quote(user))
    with DavClient(calendars_url, "", "", bearer=access, kinds=google_kinds(calendars_url)) as calendars:
        found = calendars.all_collections()
    assert found and {collection.kind for collection in found} <= {"events", "tasks"}  # no address book in Google Calendar (D-335)
    assert not any(collection.shared for collection in found if collection.name == user)  # the account's own calendar is its own
    with DavClient(CARDDAV_START, "", "", bearer=access, kinds=google_kinds(CARDDAV_START)) as contacts:
        assert {collection.kind for collection in contacts.all_collections()} == {"contacts"}



EVENT_FOR_COLOUR = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Chrono Mail//Live test//EN\r
BEGIN:VEVENT\r
UID:{uid}\r
DTSTAMP:20301101T090000Z\r
DTSTART:20301118T140000Z\r
DTEND:20301118T150000Z\r
SUMMARY:Chrono Mail label test\r
END:VEVENT\r
END:VCALENDAR\r
"""

SERIES = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Chrono Mail//Live test//EN\r
BEGIN:VEVENT\r
UID:{uid}\r
DTSTAMP:20301101T090000Z\r
DTSTART:20301118T090000Z\r
DTEND:20301118T100000Z\r
RRULE:FREQ=DAILY;COUNT=3\r
SUMMARY:Chrono Mail colour test\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:{uid}\r
DTSTAMP:20301101T090000Z\r
RECURRENCE-ID:20301119T090000Z\r
DTSTART:20301119T110000Z\r
DTEND:20301119T120000Z\r
SUMMARY:Chrono Mail colour test, moved\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_googles_colours_of_an_event_and_a_changed_date_go_and_come_through_the_calendar_api() -> None:
    import uuid
    from datetime import datetime, timezone

    from chrono_mail.engine.google_colours import event_key
    from chrono_mail.protocols.google import calendar_id_of, read_colours, set_colour

    user, _ = gmail_login()
    codes = google_client_codes()
    if codes is None or not GOOGLE_TOKEN_FILE.exists():
        pytest.skip("the Google client file or the test account's Google sign-in is missing")
    access, _ = refresh_access(GoogleClient(*codes), json.loads(GOOGLE_TOKEN_FILE.read_text())["refresh_token"])
    with DavClient(CALDAV_START.format(address=urllib.parse.quote(user)), "", "", bearer=access) as calendars:
        own = next(collection for collection in calendars.all_collections() if collection.kind == "events" and collection.name == user)
        calendar = calendar_id_of(own.href)
        assert calendar == user  # the Calendar API knows the calendar by the part of its CalDAV address
        uid = f"chrono-mail-colour-{uuid.uuid4().hex}"
        href = own.href.rstrip("/") + f"/{uid}.ics"
        calendars.put(href, SERIES.format(uid=uid), "events", create=True)
        try:
            first = read_colours(access, calendar)
            ours = {event_key(event.uid) if not event.original_start else f"{event.uid} {event.original_start}": event for event in first.events if event.uid == uid}
            moved = event_key(uid, datetime(2030, 11, 19, 9, 0, tzinfo=timezone.utc))
            assert set(ours) == {uid, moved} and all(event.colour_id == "" for event in ours.values())  # the UID is CalDAV's, the changed date known by its old moment
            set_colour(access, calendar, ours[uid].event_id, "5")
            set_colour(access, calendar, ours[moved].event_id, "11")
            changes = {f"{event.uid} {event.original_start}" if event.original_start else event.uid: event.colour_id for event in read_colours(access, calendar, first.sync_token).events}
            assert changes.get(uid) == "5" and changes.get(moved) == "11"  # only the changes, with their colours
            found = calendars.get(href)
            assert found is not None and "COLOR" not in found[1]  # Google's CalDAV still carries no colour
            set_colour(access, calendar, ours[uid].event_id, "")
        finally:
            calendars.delete(href)


def test_a_label_of_the_calendar_comes_with_its_name_and_goes_onto_an_event() -> None:
    """D-338: the labels of a Google calendar (the colours a person makes there) are read with their names, and one can be given to an event."""
    import json as json_module
    import urllib.request
    import uuid

    from chrono_mail.protocols.google import LABEL_VERSION, calendar_id_of, read_colours, read_labels, set_colour, set_label

    user, _ = gmail_login()
    codes = google_client_codes()
    if codes is None or not GOOGLE_TOKEN_FILE.exists():
        pytest.skip("the Google client file or the test account's Google sign-in is missing")
    access, _ = refresh_access(GoogleClient(*codes), json.loads(GOOGLE_TOKEN_FILE.read_text())["refresh_token"])

    def set_labels(labels: tuple[tuple[str, str, str], ...]) -> None:
        """The calendar's whole list of labels (id, colour, name): Google replaces it with this one. Chrono Mail itself never writes it."""
        body = {"labelProperties": {"eventLabels": [{**({"id": label} if label else {}), "backgroundColor": colour, **({"name": name} if name else {})}
                                                    for label, colour, name in labels]}}  # a new label is sent without an id
        address = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(user)}?eventLabelVersion={LABEL_VERSION}"
        request = urllib.request.Request(address, data=json_module.dumps(body).encode(), method="PATCH",
                                         headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as answer:
            assert answer.status == 200

    before = read_labels(access, user)
    if any(label.name == "Chrono Mail test" for label in before):  # a run that broke off left it: cleaned here, never by hand
        set_labels(tuple((label.label_id, label.colour, label.name) for label in before if label.name != "Chrono Mail test"))
        before = read_labels(access, user)
    assert before and all(label.name != "Chrono Mail test" for label in before)
    with DavClient(CALDAV_START.format(address=urllib.parse.quote(user)), "", "", bearer=access) as calendars:
        own = next(collection for collection in calendars.all_collections() if collection.kind == "events" and collection.name == user)
        calendar = calendar_id_of(own.href)
        uid = f"chrono-mail-label-{uuid.uuid4().hex}"
        href = own.href.rstrip("/") + f"/{uid}.ics"
        set_labels((*((label.label_id, label.colour, "") for label in before), ("", "#ff0000", "Chrono Mail test")))
        try:
            named = next(label for label in read_labels(access, calendar) if label.name == "Chrono Mail test")
            assert named.colour == "#ff0000" and named.label_id  # read back with the name and the colour it was given
            calendars.put(href, EVENT_FOR_COLOUR.format(uid=uid), "events", create=True)
            first = read_colours(access, calendar)
            ours = next(event for event in first.events if event.uid == uid)
            assert ours.label_id == "" and ours.colour_id == ""  # a new event carries no label
            set_label(access, calendar, ours.event_id, named.label_id)
            changed = {event.uid: event for event in read_colours(access, calendar, first.sync_token).events}
            assert changed[uid].label_id == named.label_id and changed[uid].colour_id == ""  # the label itself, not one of the eleven colours
            found = calendars.get(href)
            assert found is not None and "COLOR" not in found[1] and "Chrono Mail test" not in found[1]  # CalDAV carries neither the colour nor the name
            set_colour(access, calendar, ours.event_id, "5")  # one of Google's eleven while it carries a label
            still = next(event for event in read_colours(access, calendar).events if event.uid == uid)
            assert still.colour_id == "5" and still.label_id != named.label_id  # Google puts the label of that colour there itself
            set_label(access, calendar, ours.event_id, "")
            after = next(event for event in read_colours(access, calendar).events if event.uid == uid)
            assert after.label_id == "" and after.colour_id == ""  # the label off takes the colour with it
        finally:
            calendars.delete(href)
            set_labels(tuple((label.label_id, label.colour, label.name) for label in before))
    after = read_labels(access, user)
    assert len(after) == len(before) and all(label.name != "Chrono Mail test" for label in after)  # the calendar's labels are as they were
