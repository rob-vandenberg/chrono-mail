import json
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import pytest

from chrono_mail.core import ProtocolError
from chrono_mail.core.colours import GOOGLE_EVENT_COLOURS, google_colour_nearest
from chrono_mail.engine import Engine, google_access, google_colours
from chrono_mail.protocols import google
from chrono_mail.protocols.google import ColourRead, GoogleClient, calendar_id_of, utc_key
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call
from tests.test_google_sign_in import CLIENT, signs_in

# --- Version ------------------------------------------------------------
__version__ = "test_google_colours.py 4.4.598"

# --- Version history ----------------------------------------------------
# v4.4.598: Initial tests: Google's event colours read at each synchronisation, shown on events without tags, a changed colour tag sent to Google first (D-337)

CALENDAR = "ann@gmail.com"
EVENT = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Google Inc//Google Calendar 70.9054//EN\r
BEGIN:VEVENT\r
UID:{uid}\r
DTSTAMP:20301101T090000Z\r
DTSTART:20301120T090000Z\r
DTEND:20301120T100000Z\r
SUMMARY:{summary}\r
{extra}END:VEVENT\r
END:VCALENDAR\r
"""


class FakeCalendarApi:
    """Google's Calendar API for the colours of one calendar's events, on this computer: lists with sync tokens, and PATCH of colorId."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, object]] = {}  # Google's id -> iCalUID, colorId, status (and a changed date's recurringEventId, originalStartTime)
        self.changed_at: dict[str, int] = {}  # Google's id -> the sync token number of its last change
        self.counter: int = 1
        self.forgets_tokens: bool = False  # a sync token is answered with 410 Gone
        self.busy: bool = False  # every request is answered with 503
        self.limited: bool = False  # a PATCH is answered with Google's 403 for too many requests
        self.patches: list[tuple[str, str]] = []  # (Google's id, the colorId sent; "" for none)
        self.lists: list[str] = []  # the sync token of each list ("" for a whole read)
        self.asked: int = 0  # every list request, also one Google refuses
        self._server: ThreadingHTTPServer | None = None

    def add(self, event_id: str, uid: str, colour: str = "", series: str = "", original: str = "") -> None:
        self.counter += 1
        self.events[event_id] = {"iCalUID": uid, "colorId": colour, "status": "confirmed"}
        if series:
            self.events[event_id].update({"recurringEventId": series, "originalStartTime": {"dateTime": original}})
        self.changed_at[event_id] = self.counter

    def delete(self, event_id: str) -> None:
        """Deleted on Google: a read by sync token gives it as cancelled, with only its id (as Google may)."""
        self.counter += 1
        self.events[event_id] = {"status": "cancelled"}
        self.changed_at[event_id] = self.counter

    def colour(self, event_id: str, colour: str) -> None:
        self.counter += 1
        self.events[event_id]["colorId"] = colour
        self.changed_at[event_id] = self.counter

    def __enter__(self) -> "FakeCalendarApi":
        api = self

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                api.answer_list(self)

            def do_PATCH(self) -> None:  # noqa: N802
                api.answer_patch(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exception: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}"

    def send(self, handler: BaseHTTPRequestHandler, status: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def answer_list(self, handler: BaseHTTPRequestHandler) -> None:
        parts = urllib.parse.urlsplit(handler.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parts.query).items()}
        self.asked += 1
        if self.busy:
            self.send(handler, 503, {"error": {"message": "Backend Error"}})
            return
        if handler.headers.get("Authorization") != "Bearer access-1":
            self.send(handler, 401, {"error": {"message": "Invalid Credentials"}})
            return
        if urllib.parse.unquote(parts.path.split("/calendars/", 1)[1].split("/", 1)[0]) != CALENDAR:
            self.send(handler, 404, {"error": {"message": "Not Found"}})
            return
        token = query.get("syncToken", "")
        self.lists.append(token)
        if token and self.forgets_tokens:
            self.send(handler, 410, {"error": {"message": "Sync token is no longer valid, a full sync is required."}})
            return
        since = int(token[1:]) if token else 0
        items = [{"id": key, **{name: value for name, value in event.items() if value}} for key, event in self.events.items() if self.changed_at[key] > since]
        items = [item for item in items if token or item.get("status") != "cancelled"]  # a whole read leaves out what was deleted
        self.send(handler, 200, {"items": items, "nextSyncToken": f"t{self.counter}"})

    def answer_patch(self, handler: BaseHTTPRequestHandler) -> None:
        if self.limited:
            self.send(handler, 403, {"error": {"code": 403, "message": "Calendar usage limits exceeded.",
                                               "errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded", "message": "Calendar usage limits exceeded."}]}})
            return
        if self.busy:
            self.send(handler, 503, {"error": {"message": "Backend Error"}})
            return
        event_id = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path.rsplit("/", 1)[1])
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", "0"))))
        if event_id not in self.events or self.events[event_id].get("status") == "cancelled":
            self.send(handler, 404, {"error": {"message": "Not Found"}})
            return
        colour = body.get("colorId") or ""
        self.patches.append((event_id, colour))
        self.colour(event_id, colour)
        self.send(handler, 200, {"id": event_id})


class GoogleDav(FakeDav):
    """Google's CalDAV: a new event is known to its Calendar API at once, without a colour."""

    api: "FakeCalendarApi | None" = None

    @override
    def put(self, handler: BaseHTTPRequestHandler, path: str, body: str) -> None:
        super().put(handler, path, body)
        if self.api is not None and handler.headers.get("If-None-Match") == "*" and "UID:" in body:  # made, not changed
            uid = body.split("UID:", 1)[1].splitlines()[0].strip()
            self.api.add(f"g-{uid}", uid)


@pytest.fixture
def google_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Engine, str, FakeDav, FakeCalendarApi]]:
    """A Google Calendar account signed in, its CalDAV server and its Calendar API, both on this computer."""
    calendars, contacts = GoogleDav(), FakeDav()
    with FakeCalendarApi() as api:
        calendars.api = api
        for server in (calendars, contacts):
            server.bearer = "access-1"
            server.__enter__()
        try:
            from chrono_mail.engine import commands_google

            monkeypatch.setattr(google_access, "google_client_codes", lambda: (CLIENT.client_id, CLIENT.client_secret))
            def renew(client: GoogleClient, refresh: str) -> tuple[str, int]:
                return "access-1", 3600

            def calendar_of(href: str) -> str:
                return CALENDAR if href.rstrip("/").endswith("/events") else ""

            monkeypatch.setattr(google_access, "refresh_access", renew)
            monkeypatch.setattr(commands_google, "PARTS", (("Google Calendar", f"http://127.0.0.1:{calendars.port}/dav/"),
                                                           ("Google Contacts", f"http://localhost:{contacts.port}/dav/")))
            monkeypatch.setattr(google, "CALENDAR_API", api.url)
            monkeypatch.setattr(google_colours, "calendar_id_of", calendar_of)
            engine = make_engine(tmp_path)
            signs_in(monkeypatch, CALENDAR, "refresh-1")
            account = next(each for each in call(engine, "google.sign-in").account_ids if engine.config().find(each).account_name.startswith("Google Calendar"))
            yield engine, account, calendars, api
        finally:
            google_access.forget_access("")
            for server in (calendars, contacts):
                server.__exit__(None, None, None)


def on_both(calendars: FakeDav, api: FakeCalendarApi, uid: str, summary: str, colour: str = "", extra: str = "") -> None:
    calendars.add("events", f"{uid}.ics", EVENT.format(uid=uid, summary=summary, extra=extra))
    api.add(f"g-{uid}", uid, colour)


def shown(engine: Engine) -> dict[str, str]:
    """Each event's title -> the colour another program gives it, as the calendar shows it without a tag."""
    rows = call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-21").events
    return {row.summary: row.other_colour for row in rows}


def item_of(engine: Engine, summary: str) -> str:
    return next(row.item for row in call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-21").events if row.summary == summary)


def test_an_answer_that_breaks_off_is_a_passing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client

    def broken(request: object, timeout: float = 0) -> object:
        raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(google.urllib.request, "urlopen", broken)
    with pytest.raises(ProtocolError, match="could not be reached") as caught:
        google.read_colours("access-1", CALENDAR)
    assert type(caught.value) is ProtocolError  # passing: tried again, never given up


def test_the_calendar_api_id_is_the_calendar_part_of_googles_caldav_address() -> None:
    assert calendar_id_of("https://apidata.googleusercontent.com/caldav/v2/ann%40gmail.com/events/") == "ann@gmail.com"
    assert calendar_id_of("https://apidata.googleusercontent.com/caldav/v2/family123%40group.calendar.google.com/events") == "family123@group.calendar.google.com"
    assert calendar_id_of("https://dav.example.org/caldav/v2/ann%40example.org/events/") == ""  # not Google's
    assert calendar_id_of("https://apidata.googleusercontent.com/caldav/v2/") == ""
    assert calendar_id_of("/caldav/v2/ann@gmail.com/events/") == "ann@gmail.com"  # as Google lists it: a path only (seen live)
    assert calendar_id_of("/caldav/v2/en.dutch#holiday@group.v.calendar.google.com/events/") == "en.dutch#holiday@group.v.calendar.google.com"  # a "#" in the id


def test_a_changed_date_is_known_by_the_moment_it_had_in_utc() -> None:
    assert utc_key("2030-11-20T10:00:00+01:00", "") == "20301120T090000Z" and utc_key("", "2030-11-20") == "20301120"
    assert utc_key("2030-11-20T10:00:00", "") == ""  # no zone: never guessed


def test_each_google_colour_is_nearest_to_itself() -> None:
    assert all(google_colour_nearest(colour) == number for number, colour in GOOGLE_EVENT_COLOURS.items()) and len(GOOGLE_EVENT_COLOURS) == 11


def test_an_event_without_tags_shows_the_colour_google_gives_it(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    on_both(calendars, api, "u1", "Dentist", "11")
    on_both(calendars, api, "u2", "Lunch")
    summary = call(engine, "dav.sync", account=account)[0]
    assert summary.recoloured == 1 and summary.colour_errors == ()
    assert shown(engine) == {"Dentist": GOOGLE_EVENT_COLOURS["11"], "Lunch": ""}  # Lunch has the calendar's own colour
    api.colour("g-u2", "2")
    api.colour("g-u1", "")
    assert call(engine, "dav.sync", account=account)[0].recoloured == 2
    assert shown(engine) == {"Dentist": "", "Lunch": GOOGLE_EVENT_COLOURS["2"]}  # a colour changed or taken away on Google follows here
    assert api.lists == ["", "t3"]  # after the first time only the changes since are asked
    assert "COLOR" not in calendars.collections["events"].items["u1.ics"][1]  # nothing is written into the event's text


def test_an_event_deleted_on_google_loses_its_colour_here(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    api.delete("g-u1")  # CalDAV still lists it for a moment; the colour read already says it is gone
    call(engine, "dav.sync", account=account)
    assert shown(engine) == {"Dentist": ""}


def test_a_changed_date_of_a_series_shows_its_own_colour_and_the_other_dates_the_series_colour(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi],
) -> None:
    engine, account, calendars, api = google_world
    rule = "RRULE:FREQ=DAILY;COUNT=3\r\n"
    changed = "BEGIN:VEVENT\r\nUID:u5\r\nDTSTAMP:20301101T090000Z\r\nRECURRENCE-ID:20301121T090000Z\r\nDTSTART:20301121T110000Z\r\nDTEND:20301121T120000Z\r\nSUMMARY:Standup, moved\r\nEND:VEVENT\r\n"
    calendars.add("events", "u5.ics", EVENT.format(uid="u5", summary="Standup", extra=rule).replace("END:VCALENDAR", changed + "END:VCALENDAR"))
    api.add("g-u5", "u5", "5")
    api.add("g-u5_20301121T090000Z", "u5", "11", series="g-u5", original="2030-11-21T10:00:00+01:00")
    call(engine, "dav.sync", account=account)
    rows = call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-23").events
    assert sorted((row.summary, row.other_colour) for row in rows) == [
        ("Standup", GOOGLE_EVENT_COLOURS["5"]), ("Standup", GOOGLE_EVENT_COLOURS["5"]), ("Standup, moved", GOOGLE_EVENT_COLOURS["11"]),
    ]


def test_a_forgotten_sync_token_reads_everything_again(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    api.forgets_tokens = True
    del api.events["g-u1"]  # gone from the Calendar API while the token was forgotten: the whole read no longer has it
    summary = call(engine, "dav.sync", account=account)[0]
    assert summary.colour_errors == () and api.lists[-1] == "" and shown(engine)["Dentist"] == ""  # its colour went with it


def test_a_changed_colour_tag_goes_to_google_and_tags_given_before_never_repaint_it(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    call(engine, "tag.create", name="Private", colour="#d00010")  # nearest to Tomato
    on_both(calendars, api, "u1", "Dentist", "5", extra="CATEGORIES:Work\r\n")
    on_both(calendars, api, "u2", "Lunch", "3")
    call(engine, "dav.sync", account=account)
    assert api.patches == []  # a tag the event had before this version never changes Google's colour
    call(engine, "event.edit", item=item_of(engine, "Lunch"), add_tag=("Private",))
    assert api.patches == [("g-u2", "11")] and "Private" in calendars.collections["events"].items["u2.ics"][1]  # the event, then its colour
    call(engine, "event.edit", item=item_of(engine, "Dentist"), clear=("tag",))
    assert api.patches[-1] == ("g-u1", "")  # no tag: the calendar's own colour on Google again
    call(engine, "dav.sync", account=account)
    assert len(api.patches) == 2  # in step: nothing sent again


def test_a_colour_tag_changed_while_google_is_busy_is_sent_at_the_next_synchronisation(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    api.busy = True
    info = call(engine, "event.edit", item=item_of(engine, "Dentist"), add_tag=("Work",))
    assert info.tags == ("Work",) and api.patches == []  # the save itself is done; the colour waits
    summary = call(engine, "dav.sync", account=account)[0]
    assert summary.colour_errors and api.patches == []  # said once the synchronisation runs, never lost
    api.busy = False
    call(engine, "dav.sync", account=account)
    assert api.patches == [("g-u1", "10")]


def test_a_new_event_made_here_with_a_tag_gives_google_its_colour(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    calendar = next(row.collection for row in call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-21").events)
    call(engine, "event.add", calendar=calendar, summary="Budget", start="2030-11-20T14:00", end="2030-11-20T15:00", tag=("Work",))
    assert [colour for _, colour in api.patches] == ["10"]  # sent right after the server took it
    api.patches.clear()
    on_both(calendars, api, "u3", "Party", "3", extra="CATEGORIES:Work\r\n")  # tagged elsewhere and coloured on Google already
    call(engine, "dav.sync", account=account)
    assert api.patches == []  # Google's colour of an event that came from Google stays


def test_a_full_disk_never_fails_the_save_or_the_synchronisation(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)

    def full(path: Path, data: bytes, durable: bool = True) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(google_colours, "replace_file_atomically", full)
    info = call(engine, "event.edit", item=item_of(engine, "Dentist"), add_tag=("Work",))
    assert info.tags == ("Work",) and api.patches == [("g-u1", "10")]  # saved, and its colour sent
    summary = call(engine, "dav.sync", account=account)[0]
    assert summary.errors == () and any("could not be kept" in text for text in summary.colour_errors)


def test_googles_usage_limit_is_passing_and_the_colour_is_sent_later(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    api.limited = True
    call(engine, "event.edit", item=item_of(engine, "Dentist"), add_tag=("Work",))
    assert api.patches == []
    api.limited = False
    call(engine, "dav.sync", account=account)
    assert api.patches == [("g-u1", "10")]  # never given up: too much at once is asked again


def test_an_event_gone_from_google_is_not_sent_again(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    on_both(calendars, api, "u1", "Dentist", "5")
    call(engine, "dav.sync", account=account)
    del api.events["g-u1"]  # deleted through the Calendar API while CalDAV still lists it
    call(engine, "event.edit", item=item_of(engine, "Dentist"), add_tag=("Work",))
    summary = call(engine, "dav.sync", account=account)[0]
    assert summary.colour_errors == () and api.patches == []


def test_a_calendar_the_calendar_api_does_not_know_has_no_colours_and_no_error(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, account, calendars, api = google_world

    def holiday(href: str) -> str:
        return "cln2sp3l@virtual" if href.rstrip("/").endswith("/events") else ""  # a holiday calendar: CalDAV's id is not the API's (seen live)

    monkeypatch.setattr(google_colours, "calendar_id_of", holiday)
    on_both(calendars, api, "u1", "Holiday", "5")
    first = call(engine, "dav.sync", account=account)[0]
    second = call(engine, "dav.sync", account=account)[0]
    assert first.colour_errors == () == second.colour_errors and api.asked == 1 and shown(engine) == {"Holiday": ""}  # asked once, never again


def test_another_account_never_asks_google(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    asked: list[str] = []
    def read(access: str, calendar: str, sync_token: str = "") -> ColourRead:
        asked.append("read")
        return ColourRead((), "t1", True)

    monkeypatch.setattr(google_colours, "read_colours", read)
    with FakeDav() as server:
        account = call(engine, "account.add-dav", account_name="Work calendars", url=server.url, username="ann", password="secret").account_id
        summary = call(engine, "dav.sync", account=account)[0]
    assert asked == [] and summary.recoloured == 0 and summary.colour_errors == ()
