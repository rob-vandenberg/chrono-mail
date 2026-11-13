import base64
from email.message import Message
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from chrono_mail.config import ServerRole
from chrono_mail.config.google import google_client_codes
from chrono_mail.core import AuthenticationError, ConfigurationError
from chrono_mail.engine import Engine, UsageError, commands_google, google_access
from chrono_mail.protocols import google
from chrono_mail.protocols.dav import DavClient
from chrono_mail.protocols.google import SIGN_IN_AGAIN, GoogleClient, SignedIn, SignInWaiter, refresh_access
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_google_sign_in.py 4.4.597"

# --- Version history ----------------------------------------------------
# v4.4.597: a calendar owned by either of Google's two names for you is your own; each Google account keeps its own kind (D-335)
# v4.4.593: Initial tests: Google sign-in, its access codes, the two Google accounts, signing in again, Bearer for DAV (D-335)

CLIENT = GoogleClient("id-123.apps.googleusercontent.com", "GOCSPX-test")


def id_token(address: str, name: str = "") -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"email": address, **({"name": name} if name else {})}).encode()).decode().rstrip("=")
    return f"head.{claims}.signature"


class FakeAnswer:
    """What urlopen gives: read() in a with block."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, limit: int = -1) -> bytes:
        return self._data if limit < 0 else self._data[:limit]

    def __enter__(self) -> "FakeAnswer":
        return self

    def __exit__(self, *exception: object) -> None:
        pass


def google_answers(monkeypatch: pytest.MonkeyPatch, answer: dict[str, object] | None = None, error: str = "") -> list[dict[str, str]]:
    """Google's token address answers this (or refuses with error); what was asked is noted."""
    asked: list[dict[str, str]] = []

    def urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeAnswer:
        data = request.data if isinstance(request.data, bytes) else b""
        asked.append({key: values[0] for key, values in urllib.parse.parse_qs(data.decode()).items()})
        if error:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", Message(), io.BytesIO(json.dumps({"error": error}).encode()))
        return FakeAnswer(json.dumps(answer or {}).encode())

    monkeypatch.setattr(google.urllib.request, "urlopen", urlopen)
    return asked


def test_the_sign_in_page_asks_for_calendar_and_contacts_with_pkce_never_mail() -> None:
    waiter = SignInWaiter(CLIENT, "ann@gmail.com")
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(waiter.url).query)
    finally:
        waiter.close()
    assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) >= 43
    assert query["redirect_uri"][0].startswith("http://127.0.0.1:") and query["login_hint"] == ["ann@gmail.com"]
    scopes = query["scope"][0].split()
    assert "https://www.googleapis.com/auth/calendar" in scopes and "https://www.googleapis.com/auth/carddav" in scopes
    assert "profile" in scopes  # the person's name on Google (D-346)
    assert not any("mail.google.com" in scope or "gmail" in scope for scope in scopes)  # the mail scope needs Google's yearly review (D-335)


def test_the_browsers_answer_is_exchanged_for_a_lasting_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    waiter = SignInWaiter(CLIENT)
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(waiter.url).query)["state"][0]
    urllib.request.urlopen(f"{waiter.redirect_uri}/?state=wrong&code=forged", timeout=5).read()  # not the answer it waits for
    urllib.request.urlopen(f"{waiter.redirect_uri}/?state={state}&code=the-code", timeout=5).read()
    asked = google_answers(monkeypatch, {"refresh_token": "refresh-1", "access_token": "access-1", "expires_in": 3599, "id_token": id_token("Ann@Gmail.com")})
    signed = waiter.wait(5)
    assert signed == SignedIn("refresh-1", "access-1", 3599, "ann@gmail.com")
    assert asked[0]["code"] == "the-code" and asked[0]["grant_type"] == "authorization_code" and len(asked[0]["code_verifier"]) >= 43


def test_an_idle_connection_to_the_answer_address_never_holds_up_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    waiter = SignInWaiter(CLIENT)
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(waiter.url).query)["state"][0]
    port = int(waiter.redirect_uri.rsplit(":", 1)[1])
    idle = socket.create_connection(("127.0.0.1", port))  # a browser's spare connection that sends nothing
    try:
        urllib.request.urlopen(f"{waiter.redirect_uri}/?state={state}&code=the-code", timeout=5).read()
        google_answers(monkeypatch, {"refresh_token": "refresh-1", "access_token": "access-1", "expires_in": 3599, "id_token": id_token("ann@gmail.com")})
        assert waiter.wait(5).refresh_token == "refresh-1"
    finally:
        idle.close()


def test_no_answer_in_time_says_so_calmly() -> None:
    waiter = SignInWaiter(CLIENT)
    with pytest.raises(AuthenticationError, match="No answer came"):
        waiter.wait(0.1)


def test_google_ending_the_sign_in_asks_to_sign_in_again(monkeypatch: pytest.MonkeyPatch) -> None:
    google_answers(monkeypatch, error="invalid_grant")
    with pytest.raises(AuthenticationError) as ended:
        refresh_access(CLIENT, "refresh-1")
    assert str(ended.value) == SIGN_IN_AGAIN


def test_the_client_file_is_read_and_a_bad_one_offers_no_sign_in(tmp_path: Path) -> None:
    path = tmp_path / "google-client.json"
    assert google_client_codes(path) is None
    path.write_text("not json")
    assert google_client_codes(path) is None
    path.write_text(json.dumps({"client_id": " id ", "client_secret": "secret"}))
    assert google_client_codes(path) == ("id", "secret")


# --- the engine ------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeDav, FakeDav]]:
    """Google's calendar server and contacts server, each taking only the access code 'access-1'."""
    calendars, contacts = FakeDav(), FakeDav()
    for server in (calendars, contacts):
        server.bearer = "access-1"
        server.__enter__()
    monkeypatch.setattr(google_access, "google_client_codes", lambda: (CLIENT.client_id, CLIENT.client_secret))
    monkeypatch.setattr(commands_google, "PARTS", (("Google Calendar", f"http://127.0.0.1:{calendars.port}/dav/"),
                                                   ("Google Contacts", f"http://localhost:{contacts.port}/dav/")))
    yield calendars, contacts
    for server in (calendars, contacts):
        server.__exit__(None, None, None)


def signs_in(monkeypatch: pytest.MonkeyPatch, address: str, refresh: str, name: str = "") -> None:
    """The person signs in on Google's page as address."""

    class Waiter:
        def __init__(self, client: GoogleClient, hint: str = "") -> None:
            self.url = "https://accounts.google.com/"

        def wait(self, seconds: float, stop: object = None) -> SignedIn:
            return SignedIn(refresh, "access-1", 3600, address, name)

        def check(self) -> None:
            pass

        def cancel(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(commands_google, "SignInWaiter", Waiter)
    def opened(url: str) -> bool:
        return True

    monkeypatch.setattr(commands_google.webbrowser, "open", opened)


def test_signing_in_adds_google_calendar_and_google_contacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav]) -> None:
    engine = make_engine(tmp_path)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-1")
    result = call(engine, "google.sign-in")
    accounts = [settings for settings in engine.config().accounts if settings.google]
    assert result.address == "ann@gmail.com" and len(result.account_ids) == 2 == len(accounts)
    assert sorted(settings.dav_url for settings in accounts) == sorted(url for _, url in commands_google.PARTS)
    assert all(settings.account_name == "" for settings in accounts)  # no Shown as name made up: the person chooses one (D-346)
    assert all(engine.passwords.get(settings.account.account_id, ServerRole.INCOMING) == "refresh-1" for settings in accounts)
    with pytest.raises(UsageError):
        call(engine, "google.sign-in")  # the same Google account twice


def test_signing_in_again_takes_the_new_sign_in_only_for_the_same_google_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine = make_engine(tmp_path)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-1")
    first = call(engine, "google.sign-in").account_ids[0]
    signs_in(monkeypatch, "bob@gmail.com", "refresh-2")
    with pytest.raises(UsageError, match="but this account belongs to ann@gmail.com"):
        call(engine, "google.sign-in", account=first)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-3")
    again = call(engine, "google.sign-in", account=first)
    assert len(again.account_ids) == 2 and all(engine.passwords.get(each, ServerRole.INCOMING) == "refresh-3" for each in again.account_ids)


def test_a_google_account_synchronises_with_its_access_code_renewed_when_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine: Engine = make_engine(tmp_path)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-1")
    account = call(engine, "google.sign-in").account_ids[0]
    renewed: list[str] = []
    def renew(client: GoogleClient, refresh: str) -> tuple[str, int]:
        renewed.append(refresh)
        return "access-1", 3600

    monkeypatch.setattr(google_access, "refresh_access", renew)
    call(engine, "dav.sync", account=account)
    call(engine, "dav.sync", account=account)
    assert renewed == ["refresh-1"]  # renewed once, then kept while it is valid
    google_access.forget_access(account)
    def ended(client: GoogleClient, refresh: str) -> tuple[str, int]:
        raise AuthenticationError(SIGN_IN_AGAIN)

    monkeypatch.setattr(google_access, "refresh_access", ended)
    with pytest.raises(AuthenticationError, match="sign in again"):
        call(engine, "dav.sync", account=account)


def test_without_the_client_file_sign_in_says_it_is_not_set_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_access, "google_client_codes", lambda: None)
    with pytest.raises(ConfigurationError, match="Google sign-in is not set up on this computer"):
        call(make_engine(tmp_path), "google.sign-in")


def test_a_refused_access_code_asks_to_sign_in_again() -> None:
    with FakeDav() as server:
        server.bearer = "the-right-one"
        with DavClient(server.url, "", "", bearer="an-old-one") as client, pytest.raises(AuthenticationError) as refused:
            client.homes()
    assert str(refused.value) == SIGN_IN_AGAIN


def test_cancel_stops_the_wait_for_the_browser_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setattr(google_access, "google_client_codes", lambda: (CLIENT.client_id, CLIENT.client_secret))

    def opened(url: str) -> bool:
        return True

    monkeypatch.setattr(commands_google.webbrowser, "open", opened)
    engine = make_engine(tmp_path)
    outcome: list[BaseException] = []

    def sign_in() -> None:
        try:
            call(engine, "google.sign-in")
        except AuthenticationError as error:
            outcome.append(error)

    worker = threading.Thread(target=sign_in)
    worker.start()
    for _ in range(100):
        if commands_google._waiting:  # pyright: ignore[reportPrivateUsage]
            break
        worker.join(0.05)
    call(engine, "google.cancel-sign-in")  # no ticket: every sign-in under way
    worker.join(5)
    assert not worker.is_alive() and "stopped" in str(outcome[0])
    assert not any(settings.google for settings in engine.config().accounts)


def test_without_a_web_browser_the_sign_in_says_so_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_access, "google_client_codes", lambda: (CLIENT.client_id, CLIENT.client_secret))

    def not_opened(url: str) -> bool:
        return False

    monkeypatch.setattr(commands_google.webbrowser, "open", not_opened)
    with pytest.raises(UsageError, match="could not open your web browser"):
        call(make_engine(tmp_path), "google.sign-in")


def test_a_removed_google_calendar_comes_back_with_a_new_sign_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav]) -> None:
    engine = make_engine(tmp_path)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-1")
    calendar, contacts = call(engine, "google.sign-in").account_ids
    call(engine, "account.remove", account=calendar)
    again = call(engine, "google.sign-in")
    urls = sorted(settings.dav_url for settings in engine.config().accounts if settings.google)
    assert len(again.account_ids) == 1 and urls == sorted(url for _, url in commands_google.PARTS)
    assert contacts not in again.account_ids


def test_a_stop_before_the_sign_in_starts_and_a_stop_after_the_answer_both_keep_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    engine = make_engine(tmp_path)
    call(engine, "google.cancel-sign-in", ticket="early")  # Cancel came before the sign-in began its work
    monkeypatch.setattr(google_access, "google_client_codes", lambda: (CLIENT.client_id, CLIENT.client_secret))
    with pytest.raises(AuthenticationError, match="stopped"):
        call(engine, "google.sign-in", ticket="early")

    class Late:
        """Signs in, then the person presses Cancel while Google is being asked."""

        def __init__(self, client: GoogleClient, hint: str = "") -> None:
            self.url, self.stopped = "https://accounts.google.com/", False

        def wait(self, seconds: float, stop: object = None) -> SignedIn:
            call(engine, "google.cancel-sign-in", ticket="late")
            return SignedIn("refresh-1", "access-1", 3600, "ann@gmail.com")

        def check(self) -> None:
            if self.stopped:
                raise AuthenticationError("The sign-in with Google was stopped")

        def cancel(self) -> None:
            self.stopped = True

        def close(self) -> None:
            pass

    def opened(url: str) -> bool:
        return True

    monkeypatch.setattr(commands_google, "SignInWaiter", Late)
    monkeypatch.setattr(commands_google.webbrowser, "open", opened)
    with pytest.raises(AuthenticationError, match="stopped"):
        call(engine, "google.sign-in", ticket="late")
    assert not any(settings.google for settings in engine.config().accounts)


def test_the_program_ending_stops_a_sign_in_that_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    stop = threading.Event()
    waiter = SignInWaiter(CLIENT)
    threading.Timer(0.3, stop.set).start()
    with pytest.raises(AuthenticationError, match="stopped"):
        waiter.wait(30, stop)



def test_a_calendar_owned_by_either_of_googles_two_names_for_you_is_your_own() -> None:
    from xml.etree import ElementTree

    from chrono_mail.engine.google_access import google_kinds
    from chrono_mail.protocols.dav import DavEntry, _collections_of, _place  # pyright: ignore[reportPrivateUsage]

    def calendar(owner: str) -> DavEntry:
        kinds = ElementTree.fromstring('<d:resourcetype xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:collection/><c:calendar/></d:resourcetype>')
        holder = ElementTree.fromstring(f'<d:owner xmlns:d="DAV:"><d:href>{owner}</d:href></d:owner>')
        return DavEntry("/caldav/v2/ann/events/", 200, {"{DAV:}resourcetype": kinds, "{DAV:}owner": holder})

    mine = frozenset(_place(name) for name in ("/caldav/v2/ann%40gmail.com/user", "/carddav/v1/principals/ann@gmail.com"))  # as homes() keeps them
    assert not _collections_of(calendar("/caldav/v2/ann%40gmail.com/user"), principals=mine)[0].shared
    assert _collections_of(calendar("/caldav/v2/esther%40gmail.com/user"), principals=mine)[0].shared  # someone else's stays someone else's
    assert google_kinds("https://apidata.googleusercontent.com/caldav/v2/ann/user") == {"events", "tasks"}
    assert google_kinds("https://www.googleapis.com/.well-known/carddav") == {"contacts"} and google_kinds("https://dav.example.com/") == frozenset()


def test_a_place_that_names_your_homes_but_no_principal_is_taken_as_you(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Iterable
    from xml.etree import ElementTree

    from chrono_mail.protocols.dav import DavEntry

    home = ElementTree.fromstring('<c:calendar-home-set xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:href>/caldav/v2/ann/</d:href></c:calendar-home-set>')

    def propfind(self: DavClient, path: str, properties: Iterable[tuple[str, str]], depth: int = 0) -> list[DavEntry]:
        return [DavEntry("/caldav/v2/ann%40gmail.com/user", 207, {"{urn:ietf:params:xml:ns:caldav}calendar-home-set": home})]  # as Google answers

    monkeypatch.setattr(DavClient, "propfind", propfind)
    with DavClient("https://apidata.googleusercontent.com/caldav/v2/ann%40gmail.com/user", "", "", bearer="x") as client:
        assert client.homes()[0] == "/caldav/v2/ann/"
        assert client._principals  # pyright: ignore[reportPrivateUsage]  # Google's calendar address is the user: the own calendar is never someone else's (D-335)


def test_a_kind_the_account_no_longer_keeps_goes_here_once_and_stays_on_the_server(tmp_path: Path) -> None:
    from chrono_mail.core import CollectionKind
    from chrono_mail.pimstore import PimStore
    from chrono_mail.sync.davsync import sync_dav
    from tests.test_sync_dav import fill

    with FakeDav() as server:
        fill(server)
        store = PimStore.create(tmp_path / "pim", None, durable=False)
        state = tmp_path / "pim" / "dav-state.json"
        sync_dav(DavClient(server.url, "ann", "secret"), store, state, durable=False)  # as 4.4.596 did: the address book came too
        assert store.collections(CollectionKind.CONTACTS)
        report = sync_dav(DavClient(server.url, "ann", "secret", kinds=frozenset({"events", "tasks"})), store, state, durable=False)
        assert not store.collections(CollectionKind.CONTACTS) and store.collections(CollectionKind.EVENTS)  # gone here (D-335)
        assert report.errors == [] and "c1.vcf" in server.collections["contacts"].items  # no error line, and the server keeps it


def test_the_sign_in_gives_the_person_s_name_on_google(monkeypatch: pytest.MonkeyPatch) -> None:
    waiter = SignInWaiter(CLIENT)
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(waiter.url).query)["state"][0]
    urllib.request.urlopen(f"{waiter.redirect_uri}/?state={state}&code=the-code", timeout=5).read()
    google_answers(monkeypatch, {"refresh_token": "refresh-1", "access_token": "access-1", "expires_in": 3599, "id_token": id_token("ann@gmail.com", " Ann Lee ")})
    assert waiter.wait(5) == SignedIn("refresh-1", "access-1", 3599, "ann@gmail.com", "Ann Lee")


def test_signing_in_keeps_google_s_name_apart_from_shown_as_and_signing_in_again_renews_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servers: tuple[FakeDav, FakeDav],
) -> None:
    """D-346: no Shown as name is made up; the person's name on Google is kept apart, and a new sign-in renews it."""
    engine = make_engine(tmp_path)
    signs_in(monkeypatch, "ann@gmail.com", "refresh-1", "Ann Lee")
    added = call(engine, "google.sign-in").account_ids
    assert {(engine.config().find(each).account_name, engine.config().find(each).google_name) for each in added} == {("", "Ann Lee")}
    signs_in(monkeypatch, "ann@gmail.com", "refresh-2", "Ann Stone")
    call(engine, "google.sign-in", account=added[0])
    assert {engine.config().find(each).google_name for each in added} == {"Ann Stone"}
