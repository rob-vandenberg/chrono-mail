from pathlib import Path

from chrono_mail.core import AuthenticationError
from chrono_mail.core.errors import GMAIL_PASSWORD_ADVICE, refused_login
from chrono_mail.protocols.imap import FolderEntry
from chrono_mail.sync import ImapState
from chrono_mail.sync.imap_sync import gmail_all_mail_to_hide

# --- Version ------------------------------------------------------------
__version__ = "test_gmail.py 4.4.592"

# --- Version history ----------------------------------------------------
# v4.4.592: Initial tests: a login Gmail refused says how to make an app password; Gmail's All Mail starts hidden (D-335)

GMAIL = frozenset({"IMAP4REV1", "X-GM-EXT-1", "SPECIAL-USE"})
LISTED = [
    FolderEntry("INBOX", "/", frozenset()),
    FolderEntry("[Gmail]/All Mail", "/", frozenset({"\\all", "\\hasnochildren"})),
    FolderEntry("[Gmail]/Trash", "/", frozenset({"\\trash"})),
]


def test_a_login_gmail_refused_says_how_to_make_an_app_password() -> None:
    for host in ("imap.gmail.com", "SMTP.GMAIL.COM", "imap.googlemail.com"):
        error = refused_login(host, "[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        assert isinstance(error, AuthenticationError) and str(error) == f"{GMAIL_PASSWORD_ADVICE} (Gmail said: [AUTHENTICATIONFAILED] Invalid credentials (Failure))"
    assert "myaccount.google.com/apppasswords" in GMAIL_PASSWORD_ADVICE


def test_a_login_another_server_refused_says_what_that_server_said() -> None:
    for host in ("mail.example.com", "notgmail.com", "gmail.com.example.net"):
        assert str(refused_login(host, "Bad password")) == "The server refused the login: Bad password"


def test_gmails_all_mail_starts_hidden_only_on_gmail_and_only_while_nobody_chose(tmp_path: Path) -> None:
    state = ImapState(tmp_path / "state.log", durable=False)
    assert gmail_all_mail_to_hide(GMAIL, LISTED, state) == ["[Gmail]/All Mail"]
    assert gmail_all_mail_to_hide(frozenset({"IMAP4REV1", "SPECIAL-USE"}), LISTED, state) == []  # another server with an \All folder: as before
    state.set_hidden("[Gmail]/All Mail", False)  # the person ticked it in Edit account > Folders
    assert gmail_all_mail_to_hide(GMAIL, LISTED, state) == []
    again = ImapState(tmp_path / "state.log", durable=False)  # the choice is kept through a restart
    assert gmail_all_mail_to_hide(GMAIL, LISTED, again) == []


def test_an_all_mail_kept_in_step_before_is_never_hidden(tmp_path: Path) -> None:
    state = ImapState(tmp_path / "state.log", durable=False)
    state.set_server_name(("[Gmail]", "All Mail"), "[Gmail]/All Mail")  # synchronised here by an older version
    assert gmail_all_mail_to_hide(GMAIL, LISTED, state) == []
