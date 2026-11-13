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
__version__ = "test_live_gmail.py 4.4.597"

# --- Version history ----------------------------------------------------
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

