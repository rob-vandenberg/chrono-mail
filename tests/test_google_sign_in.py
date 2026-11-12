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
__version__ = "test_google_sign_in.py 4.4.593"

# --- Version history ----------------------------------------------------
# v4.4.593: Initial tests: Google sign-in, its access codes, the two Google accounts, signing in again, Bearer for DAV (D-335)

CLIENT = GoogleClient("id-123.apps.googleusercontent.com", "GOCSPX-test")


def id_token(address: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"email": address}).encode()).decode().rstrip("=")
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


def signs_in(monkeypatch: pytest.MonkeyPatch, address: str, refresh: str) -> None:
    """The person signs in on Google's page as address."""

    class Waiter:
        def __init__(self, client: GoogleClient, hint: str = "") -> None:
            self.url = "https://accounts.google.com/"

        def wait(self, seconds: float, stop: object = None) -> SignedIn:
            return SignedIn(refresh, "access-1", 3600, address)

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
    assert sorted(settings.account_name for settings in accounts) == ["Google Calendar (ann@gmail.com)", "Google Contacts (ann@gmail.com)"]
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
    names = sorted(settings.account_name for settings in engine.config().accounts if settings.google)
    assert len(again.account_ids) == 1 and names == ["Google Calendar (ann@gmail.com)", "Google Contacts (ann@gmail.com)"]
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

