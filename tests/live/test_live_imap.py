import os
from pathlib import Path
from collections.abc import Generator
from contextlib import suppress

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ServerSettings
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from tests.live.account import LiveAccount

# --- Version ------------------------------------------------------------
__version__ = "test_live_imap.py 0.0.32"

# --- Version history ----------------------------------------------------
# v0.0.32: IMAP IDLE: new mail pushed to the window
# v0.0.30: IMAP sync, store to server
# v0.0.29: IMAP sync, server to store
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


def test_sync_against_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.core import FolderRole, MessageFlag
    from chrono_mail.crypto import generate_data_key
    from chrono_mail.index import Index
    from chrono_mail.store import Store
    from chrono_mail.sync import ImapState, sync_imap

    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    index = Index(tmp_path / "index.sqlite")
    state = ImapState(tmp_path / "state.log", durable=False)
    local = (folder_name,)
    imap.create_folder(folder_name)
    imap.subscribe(folder_name)  # folders that are not subscribed are not shown (D-049)
    try:
        imap.append(folder_name, MESSAGE, ["\\Seen"])
        imap.append(folder_name, MESSAGE.replace(b"<live-imap-test@", b"<live-imap-test-2@"))
        with connect(live_account, secure=True) as client:
            report = sync_imap(client, store, index, state)
        assert report.aborted is None and report.failed == ()
        stored = store.list_messages(local)
        assert len(stored) == 2 and sorted(message.flags == {MessageFlag.SEEN} for message in stored) == [False, True]
        assert {store.read(message.ref) for message in stored} == {MESSAGE, MESSAGE.replace(b"<live-imap-test@", b"<live-imap-test-2@")}
        imap.select(folder_name)
        first = imap.uid_search("ALL")[0]
        imap.uid_store(str(first), ["\\Flagged"], "add")
        with connect(live_account, secure=True) as client:
            again = sync_imap(client, store, index, state)
        assert any(item.folder == folder_name and item.flags_updated == 1 for item in again.folders)
        imap.uid_store(str(first), ["\\Deleted"], "add")
        imap.uid_expunge(str(first))
        with connect(live_account, secure=True) as client:
            last = sync_imap(client, store, index, state)
        assert any(item.folder == folder_name and item.removed == 1 for item in last.folders) and len(store.list_messages(local)) == 1
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        with suppress(Exception):
            imap.delete_folder(folder_name)
        index.close()


def test_local_changes_reach_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.core import FolderRole, MessageFlag
    from chrono_mail.crypto import generate_data_key
    from chrono_mail.index import Index
    from chrono_mail.store import Store
    from chrono_mail.sync import ImapState, sync_imap

    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    index = Index(tmp_path / "index.sqlite")
    state = ImapState(tmp_path / "state.log", durable=False)
    local = (folder_name,)
    other = (folder_name + "-b",)
    imap.create_folder(folder_name)
    imap.create_folder(other[0])
    imap.subscribe(folder_name)
    imap.subscribe(other[0])

    def sync() -> object:
        with connect(live_account, secure=True) as client:
            return sync_imap(client, store, index, state)

    try:
        imap.append(folder_name, MESSAGE)
        sync()
        (message,) = store.list_messages(local)
        store.set_flags(message.ref, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}))
        sync()
        imap.select(folder_name)
        flags = imap.uid_fetch("1:*")[0].flags
        assert "\\Seen" in flags and "\\Flagged" in flags
        store.move(message.ref, other)
        sync()
        imap.select(folder_name)
        assert imap.uid_search("ALL") == ()
        imap.select(other[0])
        assert len(imap.uid_search("ALL")) == 1 and len(store.list_messages(other)) == 1
        uploaded = store.deliver(other, MESSAGE.replace(b"<live-imap-test@", b"<live-imap-up@"), flags=frozenset({MessageFlag.SEEN}), verify=True)
        sync()
        imap.select(other[0])
        assert len(imap.uid_search("ALL")) == 2
        index.remove(uploaded.store_id)
        store.delete(uploaded)
        sync()
        imap.select(other[0])
        assert len(imap.uid_search("ALL")) == 1
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        for name in (folder_name, other[0]):
            with suppress(Exception):
                imap.delete_folder(name)
        index.close()


def test_an_imap_account_in_the_engine_against_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.engine import build_registry
    from tests.enginefactory import make_engine

    engine = make_engine(tmp_path)
    registry = build_registry()
    created = registry.run(
        engine, "account.add",
        {
            "kind": "imap", "display_name": "Live", "email_address": live_account.user, "username": live_account.user, "password": live_account.password,
            "incoming_host": live_account.host, "incoming_port": live_account.imap_ssl_port, "incoming_security": "tls",
            "outgoing_host": live_account.host, "outgoing_port": live_account.smtp_ssl_port, "outgoing_security": "tls",
        },
    )
    account = str(getattr(created, "account_id"))
    imap.create_folder(folder_name)
    imap.subscribe(folder_name)
    try:
        imap.append(folder_name, MESSAGE, ["\\Seen"])
        fetched = registry.run(engine, "mail.fetch", {"account": account})
        assert getattr(fetched, "aborted") is None and getattr(fetched, "downloaded") >= 1
        rows = getattr(registry.run(engine, "message.list", {"account": account, "folder": folder_name}), "messages")
        assert [row.subject for row in rows] == ["Chrono Mail live IMAP test"] and rows[0].flags == ("seen",)
        registry.run(engine, "message.flag", {"account": account, "folder": folder_name, "message": rows[0].message_id, "flagged": True})
        registry.run(engine, "mail.fetch", {"account": account})
        imap.select(folder_name)
        assert "\\Flagged" in imap.uid_fetch("1:*")[0].flags
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        with suppress(Exception):
            imap.delete_folder(folder_name)


def test_idle_sees_a_new_message_on_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str) -> None:
    import threading

    imap.create_folder(folder_name)
    imap.subscribe(folder_name)
    try:
        if "IDLE" not in imap.capabilities:
            pytest.skip("the server does not offer IDLE")
        other = connect(live_account, secure=True)

        def deliver() -> None:
            other.append(folder_name, MESSAGE)

        imap.select(folder_name)
        timer = threading.Timer(1.5, deliver)
        timer.start()
        try:
            assert imap.idle(30) is True
        finally:
            timer.join()
            other.close()
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        with suppress(Exception):
            imap.delete_folder(folder_name)
