import os
from collections.abc import Generator
from contextlib import suppress

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ServerSettings
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from tests.live.account import LiveAccount

# --- Version ------------------------------------------------------------
__version__ = "test_live_imap.py 0.0.28"

# --- Version history ----------------------------------------------------
# v0.0.28: Initial live IMAP tests

pytestmark = pytest.mark.live

LIMITS = ImapLimits(connect_timeout_seconds=20, read_timeout_seconds=30)
MESSAGE = b"From: live@example.com\r\nTo: live@example.com\r\nSubject: Chrono Mail live IMAP test\r\nMessage-ID: <live-imap-test@example.com>\r\n\r\nHello from the test suite.\r\n"


def connect(account: LiveAccount, secure: bool, password: str | None = None) -> ImapClient:
    settings = ServerSettings(account.host, account.imap_ssl_port if secure else account.imap_port, ConnectionSecurity.TLS if secure else ConnectionSecurity.STARTTLS, account.user)
    client = ImapClient(settings, password or account.password, LIMITS)
    client.connect()
    return client


@pytest.fixture
def folder_name() -> str:
    return f"ChronoMailTest-{os.urandom(4).hex()}"


@pytest.fixture
def imap(live_account: LiveAccount) -> Generator[ImapClient]:
    client = connect(live_account, secure=True)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("secure", [True, False], ids=["imaps-993", "starttls-143"])
def test_login_list_and_select_the_inbox(live_account: LiveAccount, secure: bool) -> None:
    client = connect(live_account, secure)
    try:
        assert "IMAP4REV1" in client.capabilities
        names = {entry.name for entry in client.list_folders()}
        assert "INBOX" in names
        info = client.select("INBOX", read_only=True)
        assert info.uid_validity > 0 and info.exists >= 0 and info.uid_next >= 1
    finally:
        client.logout()


def test_wrong_password_is_rejected(live_account: LiveAccount) -> None:
    if live_account.wrong_password_attempts < 1:
        pytest.skip("wrong_password_attempts is 0 (disabled by configuration)")
    with pytest.raises(AuthenticationError):
        connect(live_account, secure=True, password="definitely-not-the-password")


def test_a_message_goes_through_a_folder_of_its_own(imap: ImapClient, folder_name: str) -> None:
    imap.create_folder(folder_name)
    try:
        appended = imap.append(folder_name, MESSAGE, ["\\Seen"])
        info = imap.select(folder_name)
        assert info.exists == 1
        uids = imap.uid_search("ALL")
        assert len(uids) == 1 and (appended.uid is None or appended.uid == uids[0])
        uid = str(uids[0])
        (summary,) = imap.uid_fetch(uid)
        assert "\\Seen" in summary.flags and summary.size == len(MESSAGE)
        (full,) = imap.uid_fetch(uid, "(UID BODY.PEEK[])")
        assert full.data == MESSAGE
        imap.uid_store(uid, ["\\Flagged"], "add")
        assert "\\Flagged" in imap.uid_fetch(uid)[0].flags
        imap.uid_store(uid, ["\\Seen"], "remove")
        flags = imap.uid_fetch(uid)[0].flags
        assert "\\Flagged" in flags and "\\Seen" not in flags  # the server may add its own marks (\\Recent, $HasNoAttachment)
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        with suppress(Exception):
            imap.delete_folder(folder_name)


def test_copy_move_and_expunge_in_our_own_folders(imap: ImapClient, folder_name: str) -> None:
    other = folder_name + "-b"
    imap.create_folder(folder_name)
    imap.create_folder(other)
    try:
        imap.append(folder_name, MESSAGE)
        imap.select(folder_name)
        uid = str(imap.uid_search("ALL")[0])
        imap.uid_copy(uid, other)
        imap.uid_move(uid, other)
        assert imap.uid_search("ALL") == ()
        imap.select(other)
        assert len(imap.uid_search("ALL")) == 2
        imap.uid_store("1:*", ["\\Deleted"], "add")
        imap.uid_expunge("1:*")
        assert imap.uid_search("ALL") == ()
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        for name in (folder_name, other):
            with suppress(Exception):
                imap.delete_folder(name)


def test_unicode_folder_names_and_renaming(imap: ImapClient, folder_name: str) -> None:
    unicode_name = f"{folder_name}-Überprüfung & mehr"
    renamed = f"{folder_name}-日本語"
    imap.create_folder(unicode_name)
    try:
        assert unicode_name in {entry.name for entry in imap.list_folders()}
        imap.rename_folder(unicode_name, renamed)
        assert renamed in {entry.name for entry in imap.list_folders()}
        unicode_name = renamed
    finally:
        with suppress(Exception):
            imap.delete_folder(unicode_name)
