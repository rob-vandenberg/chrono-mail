import base64
import hashlib
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import override

from chrono_mail.core import AuthenticationError, ProtocolError

# --- Version ------------------------------------------------------------
__version__ = "google.py 4.4.593"

# --- Version history ----------------------------------------------------
# v4.4.593: Initial Google sign-in (each connection to the answer address served on its own, an idle one closed after 10 s) (OAuth with PKCE, the answer on this computer's own address) and its access codes (D-335)

AUTHORIZE_URL: str = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: str = "https://oauth2.googleapis.com/token"
SCOPES: str = "openid email https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/carddav"  # never the mail scope (D-335)
CALDAV_START: str = "https://apidata.googleusercontent.com/caldav/v2/{address}/user"  # Google's calendars, per Google address
CARDDAV_START: str = "https://www.googleapis.com/.well-known/carddav"  # Google's contacts
MAXIMUM_ANSWER_BYTES: int = 64 * 1024
SIGN_IN_AGAIN: str = "Google asks you to sign in again (in Testing mode every 7 days): Settings > Accounts, the Google account, Sign in with Google."


@dataclass(frozen=True, slots=True)
class GoogleClient:
    """The Google project Chrono Mail signs in through: its client ID and secret (a desktop program's secret is not secret, Google says)."""

    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class SignedIn:
    refresh_token: str
    access_token: str
    expires_in: int
    address: str  # the Google account that signed in, from its ID token


class SignInWaiter:
    """The answer of Google's sign-in page, on this computer's own address (127.0.0.1) at a free port. Open `url` in the person's browser,
    then `wait`. Closed after one answer or when the time is up."""

    def __init__(self, client: GoogleClient, hint: str = "") -> None:
        self._client = client
        self._verifier = secrets.token_urlsafe(64)
        self._state = secrets.token_urlsafe(16)
        self._answer: dict[str, str] = {}
        self._arrived = threading.Event()
        self._cancelled: bool = False
        self._closed: bool = False
        waiter = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 10  # a connection that sends nothing (a browser's spare one) is closed, never waited for

            def do_GET(self) -> None:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if query.get("state", [""])[0] == waiter._state:
                    waiter._answer.update({key: values[0] for key, values in query.items()})
                    waiter._arrived.set()
                    text = "You are signed in to Google for Chrono Mail. You can close this tab."
                else:
                    text = "This is not an answer Chrono Mail is waiting for."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(text.encode())

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass  # never log: the answer holds the code

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # each connection on its own: an idle one blocks no other
        self._server.daemon_threads = True
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_port}"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode()).digest()).rstrip(b"=").decode()
        query = {
            "client_id": client.client_id, "redirect_uri": self.redirect_uri, "response_type": "code", "scope": SCOPES, "code_challenge": challenge,
            "code_challenge_method": "S256", "state": self._state, "access_type": "offline", "prompt": "consent",
        }
        if hint:
            query["login_hint"] = hint
        self.url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"
        self._thread = threading.Thread(target=self._server.serve_forever, name="google-sign-in", daemon=True)
        self._thread.start()

    def wait(self, seconds: float, stop: threading.Event | None = None) -> SignedIn:
        """Wait for the browser's answer, then exchange its code. Raises AuthenticationError when the person said no, stopped it (cancel, or
        stop set: the program ends) or the time ran out."""
        deadline = time.monotonic() + seconds
        try:
            while not self._arrived.wait(min(0.2, max(0.0, deadline - time.monotonic()))):
                if stop is not None and stop.is_set():
                    self.cancel()
                elif time.monotonic() >= deadline:
                    raise AuthenticationError("No answer came from Google's sign-in page in time: try Sign in with Google again")
        finally:
            self.close()
        self.check()
        if "error" in self._answer or "code" not in self._answer:
            raise AuthenticationError("Google did not give Chrono Mail access" + (" (access was not allowed)" if self._answer.get("error") == "access_denied" else ""))
        tokens = _token_request(self._client, {
            "code": self._answer["code"], "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": self._verifier,
        })
        refresh = str(tokens.get("refresh_token", ""))
        if not refresh:
            raise ProtocolError("Google answered the sign-in without the code that keeps it signed in")
        return SignedIn(refresh, str(tokens.get("access_token", "")), int(str(tokens.get("expires_in", "0")) or 0), _address_of(str(tokens.get("id_token", ""))))

    def cancel(self) -> None:
        """The person stopped waiting (Cancel), or the program ends: wait ends at once, and check refuses from then on, so nothing is added."""
        self._cancelled = True
        self._arrived.set()

    def check(self) -> None:
        """Raises AuthenticationError when the sign-in was stopped: called again right before anything is kept."""
        if self._cancelled:
            raise AuthenticationError("The sign-in with Google was stopped")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()


def refresh_access(client: GoogleClient, refresh_token: str) -> tuple[str, int]:
    """A fresh access code and its life in seconds. Raises AuthenticationError when Google ended the sign-in (SIGN_IN_AGAIN)."""
    tokens = _token_request(client, {"refresh_token": refresh_token, "grant_type": "refresh_token"})
    access = str(tokens.get("access_token", ""))
    if not access:
        raise ProtocolError("Google answered without an access code")
    return access, int(str(tokens.get("expires_in", "0")) or 0)


def _token_request(client: GoogleClient, fields: dict[str, str]) -> dict[str, object]:
    body = urllib.parse.urlencode({"client_id": client.client_id, "client_secret": client.client_secret, **fields}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAXIMUM_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as error:
        reason = _error_of(error.read(MAXIMUM_ANSWER_BYTES))
        if reason in ("invalid_grant", "unauthorized_client"):
            raise AuthenticationError(SIGN_IN_AGAIN) from None
        raise ProtocolError(f"Google refused: {reason or error.code}") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        raise ProtocolError(f"Google could not be reached: {getattr(error, 'reason', error)}") from None
    if len(raw) > MAXIMUM_ANSWER_BYTES:
        raise ProtocolError("Google's answer is far too large")
    try:
        answer = json.loads(raw)
    except ValueError:
        raise ProtocolError("Google's answer could not be read") from None
    if not isinstance(answer, dict):
        raise ProtocolError("Google's answer could not be read")
    return {str(key): value for key, value in answer.items()}


def _error_of(raw: bytes) -> str:
    try:
        answer = json.loads(raw)
    except ValueError:
        return ""
    return str(answer.get("error", "")) if isinstance(answer, dict) else ""


def _address_of(id_token: str) -> str:
    """The e-mail address in Google's ID token (it came straight from Google over TLS, so its signature needs no check here)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        return ""
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return ""
    return str(claims.get("email", "")).strip().lower() if isinstance(claims, dict) else ""
