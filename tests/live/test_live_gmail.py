import tomllib

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ServerSettings
from chrono_mail.core.errors import GMAIL_PASSWORD_ADVICE
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.smtp import check_login
from tests.live.account import SETTINGS_DIRECTORY

# --- Version ------------------------------------------------------------
__version__ = "test_live_gmail.py 4.4.592"

# --- Version history ----------------------------------------------------
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
