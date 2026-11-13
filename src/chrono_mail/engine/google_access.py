import threading
import urllib.parse
import time

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.config.google import GOOGLE_CLIENT_FILE, google_client_codes
from chrono_mail.core import ConfigurationError
from chrono_mail.engine.session import Engine
from chrono_mail.protocols.google import CALDAV_START, CARDDAV_START, GoogleClient, refresh_access

# --- Version ------------------------------------------------------------
__version__ = "google_access.py 4.4.597"

# --- Version history ----------------------------------------------------
# v4.4.597: google_kinds: Google Calendar keeps only calendars, Google Contacts only address books
# v4.4.593: Initial: the access code of a Google account, renewed from its refresh token when it runs out (D-335)

MARGIN_SECONDS: int = 120  # an access code this close to its end is renewed first
_LOCK = threading.Lock()
_CODES: dict[str, tuple[str, str, float]] = {}  # account id -> (refresh token it came from, access code, when it ends); in memory only


def google_client() -> GoogleClient:
    """The Google project Chrono Mail signs in through. Raises ConfigurationError when its private file is missing or unreadable."""
    codes = google_client_codes()
    if codes is None:
        raise ConfigurationError(f"Google sign-in is not set up on this computer: {GOOGLE_CLIENT_FILE} is missing or cannot be read")
    return GoogleClient(*codes)


def google_bearer(engine: Engine, settings: AccountSettings) -> str:
    """A valid access code for a Google account, renewed from the refresh token kept as its password when needed. Raises AuthenticationError
    when Google ended the sign-in (the person signs in again), ConfigurationError when nothing is stored."""
    account_id = settings.account.account_id
    refresh = engine.passwords.get(account_id, ServerRole.INCOMING)
    if not refresh:
        raise ConfigurationError("This Google account is not signed in: Settings > Accounts, the account, Sign in with Google")
    with _LOCK:
        known = _CODES.get(account_id)
        if known is not None and known[0] == refresh and known[2] - MARGIN_SECONDS > time.monotonic():
            return known[1]
    access, seconds = refresh_access(google_client(), refresh)
    with _LOCK:
        _CODES[account_id] = (refresh, access, time.monotonic() + max(seconds, 0))
    return access


def google_kinds(address: str) -> frozenset[str]:
    """What a Google account keeps (D-335): Google Calendar its calendars, Google Contacts its address books. Google's calendar server also
    leads to the address book, which then came twice."""
    host = (urllib.parse.urlsplit(address).hostname or "").lower()
    if host == urllib.parse.urlsplit(CALDAV_START).hostname:
        return frozenset({"events", "tasks"})
    if host == urllib.parse.urlsplit(CARDDAV_START).hostname:
        return frozenset({"contacts"})
    return frozenset()


def forget_access(account_id: str) -> None:
    """A new sign-in or a removed account: its access code is not used again."""
    with _LOCK:
        _CODES.pop(account_id, None)
