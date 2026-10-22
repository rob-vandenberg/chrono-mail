import os
from pathlib import Path
from collections.abc import Generator
from contextlib import suppress

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ServerSettings
from chrono_mail.protocols.imap import ImapClient, ImapLimits
from chrono_mail.protocols.tls import context_for
from tests.live.account import LiveAccount
from tests.live.conftest import IMAP_SERVERS, imap_account

# --- Version ------------------------------------------------------------
__version__ = "test_live_imap.py 1.1.248"

# --- Version history ----------------------------------------------------
# v1.1.248: the checks after the first ask only what changed where the real server can tell it (Dovecot), the full way where not (Kerio) (R-09)
# v1.1.247: many messages marked, moved and deleted for good in groups on the real server, each new UID recorded (R-08)
# v1.1.241: A folder unsubscribed on the real server goes here after one last sync (a flag set here reaches the server); subscribed again, it comes back (R-34)
# v1.1.240: A folder renamed and deleted here offline is renamed and deleted on the real server at the next check (R-31)
# v1.1.237: A folder renamed on the real server is renamed here without a new download; deleted there, it goes here (R-30)
# v0.5.123: Against both servers (DirectAdmin and Kerio); an accepted certificate
# v0.0.43: Certificate exceptions
# v0.0.32: IMAP IDLE: new mail pushed to the window
# v0.0.30: IMAP sync, store to server
# v0.0.29: IMAP sync, server to store
# v0.0.28: Initial live IMAP tests

pytestmark = pytest.mark.live

LIMITS = ImapLimits(connect_timeout_seconds=20, read_timeout_seconds=30)
MESSAGE = b"From: live@example.com\r\nTo: live@example.com\r\nSubject: Chrono Mail live IMAP test\r\nMessage-ID: <live-imap-test@example.com>\r\n\r\nHello from the test suite.\r\n"


def connect(account: LiveAccount, secure: bool, password: str | None = None) -> ImapClient:
    settings = ServerSettings(account.host, account.imap_ssl_port if secure else account.imap_port, ConnectionSecurity.TLS if secure else ConnectionSecurity.STARTTLS, account.user)
    client = ImapClient(settings, password or account.password, LIMITS, ssl_context=context_for(frozenset({account.fingerprint}) if account.fingerprint else frozenset()))
    client.connect()
    return client


@pytest.fixture(params=IMAP_SERVERS)
def live_account(request: pytest.FixtureRequest) -> LiveAccount:
    """Every IMAP test runs against both servers."""
    return imap_account(str(request.param))


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
            tells_changes = "CONDSTORE" in client.enabled
        assert report.aborted is None and report.failed == ()
        assert (state.checkpoints[local].modseq is not None) == tells_changes  # the next checks ask only what changed, where the server can tell (R-09)
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
            "trusted_certificate": (live_account.fingerprint,) if live_account.fingerprint else (),
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


def test_many_messages_go_in_groups_on_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
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
            "trusted_certificate": (live_account.fingerprint,) if live_account.fingerprint else (),
        },
    )
    account = str(getattr(created, "account_id"))
    other = folder_name + "-b"
    for name in (folder_name, other):
        imap.create_folder(name)
        imap.subscribe(name)
    try:
        for number in range(5):
            imap.append(folder_name, MESSAGE.replace(b"<live-imap-test@", f"<live-many-{number}@".encode()))
        registry.run(engine, "mail.fetch", {"account": account})
        rows = getattr(registry.run(engine, "message.list", {"account": account, "folder": folder_name}), "messages")
        messages = tuple(row.message_id for row in rows)
        assert len(messages) == 5
        marked = registry.run(engine, "message.flag-many", {"account": account, "folder": folder_name, "messages": messages, "flagged": True})
        assert len(getattr(marked, "done")) == 5
        imap.select(folder_name)
        assert all("\\Flagged" in item.flags for item in imap.uid_fetch("1:*"))
        moved = registry.run(engine, "message.move-many", {"account": account, "folder": folder_name, "target": other, "messages": messages})
        assert len(getattr(moved, "done")) == 5 and getattr(moved, "failed") == ()
        imap.select(other)
        assert len(imap.uid_search("ALL")) == 5
        again = registry.run(engine, "mail.fetch", {"account": account})
        assert getattr(again, "downloaded") == 0  # each new UID was recorded: nothing came again
        rows = getattr(registry.run(engine, "message.list", {"account": account, "folder": other}), "messages")
        gone = registry.run(engine, "message.delete-many", {"account": account, "folder": other, "messages": tuple(row.message_id for row in rows), "permanent": True})
        assert len(getattr(gone, "done")) == 5
        imap.select(other)
        assert [uid for uid in imap.uid_search("ALL") if "\\Deleted" not in imap.uid_fetch(str(uid))[0].flags] == []
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        for name in (folder_name, other):
            with suppress(Exception):
                imap.delete_folder(name)


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


def test_the_real_servers_certificate_is_trusted_by_the_usual_checks(live_account: LiveAccount) -> None:
    from chrono_mail.protocols.tls import inspect_certificate, valid_fingerprint

    if live_account.fingerprint:
        pytest.skip("this server has a certificate of its own, accepted by an exception")

    for kind, port, security in (("imap", live_account.imap_ssl_port, ConnectionSecurity.TLS), ("imap", live_account.imap_port, ConnectionSecurity.STARTTLS), ("pop3", live_account.pop3_ssl_port, ConnectionSecurity.TLS), ("smtp", live_account.smtp_ssl_port, ConnectionSecurity.TLS)):
        info = inspect_certificate(live_account.host, port, security, kind)
        assert info.verified and info.problem is None and not info.expired and valid_fingerprint(info.fingerprint), (kind, port)


def test_a_folder_renamed_and_deleted_on_the_real_server_follows_here(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.core import FolderRole
    from chrono_mail.crypto import generate_data_key
    from chrono_mail.index import Index
    from chrono_mail.store import Store
    from chrono_mail.sync import ImapState, sync_imap

    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    index = Index(tmp_path / "index.sqlite")
    state = ImapState(tmp_path / "state.log", durable=False)
    renamed = folder_name + "-renamed"
    imap.create_folder(folder_name)
    imap.subscribe(folder_name)
    try:
        imap.append(folder_name, MESSAGE)
        with connect(live_account, secure=True) as client:
            assert sync_imap(client, store, index, state).aborted is None
        (before,) = store.list_messages((folder_name,))
        imap.rename_folder(folder_name, renamed)  # as webmail does: renamed and subscribed under the new name
        imap.subscribe(renamed)
        with suppress(Exception):
            imap.unsubscribe(folder_name)
        with connect(live_account, secure=True) as client:
            report = sync_imap(client, store, index, state)
        paths = {folder.path for folder in store.list_folders()}
        assert report.aborted is None and (folder_name,) not in paths and (renamed,) in paths
        assert [message.ref.store_id for message in store.list_messages((renamed,))] == [before.ref.store_id]
        assert not any(item.folder == renamed and item.downloaded for item in report.folders)
        imap.delete_folder(renamed)
        with connect(live_account, secure=True) as client:
            gone = sync_imap(client, store, index, state)
        assert gone.aborted is None and (renamed,) not in {folder.path for folder in store.list_folders()}
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        for name in (folder_name, renamed):
            with suppress(Exception):
                imap.delete_folder(name)
        index.close()


def test_a_folder_renamed_and_deleted_offline_follows_on_the_real_server(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.core import FolderRole
    from chrono_mail.crypto import generate_data_key
    from chrono_mail.index import Index
    from chrono_mail.store import Store
    from chrono_mail.sync import ImapState, sync_imap

    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    index = Index(tmp_path / "index.sqlite")
    state = ImapState(tmp_path / "state.log", durable=False)
    renamed = folder_name + "-offline"
    imap.create_folder(folder_name)
    imap.subscribe(folder_name)
    try:
        imap.append(folder_name, MESSAGE)
        with connect(live_account, secure=True) as client:
            assert sync_imap(client, store, index, state).aborted is None
        store.rename_folder((folder_name,), (renamed,))  # what the engine does while the server cannot be reached
        index.rename_folder((folder_name,), (renamed,))
        state.rename_later((folder_name,), (renamed,))
        with connect(live_account, secure=True) as client:
            report = sync_imap(client, store, index, state)
        names = {entry.name for entry in imap.list_folders()}
        assert report.aborted is None and renamed in names and folder_name not in names and state.pending == {}
        assert len(store.list_messages((renamed,))) == 1 and not any(item.downloaded for item in report.folders)
        store.delete_folder((renamed,), require_empty=False)
        index.remove_folder((renamed,))
        state.delete_later((renamed,))
        with connect(live_account, secure=True) as client:
            gone = sync_imap(client, store, index, state)
        assert gone.aborted is None and renamed not in {entry.name for entry in imap.list_folders()} and state.pending == {}
        assert (renamed,) not in {folder.path for folder in store.list_folders()}
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        for name in (folder_name, renamed):
            with suppress(Exception):
                imap.delete_folder(name)
        index.close()


def test_a_folder_taken_off_the_real_servers_list_goes_here_and_comes_back(live_account: LiveAccount, imap: ImapClient, folder_name: str, tmp_path: Path) -> None:
    from chrono_mail.core import FolderRole, MessageFlag
    from chrono_mail.crypto import generate_data_key
    from chrono_mail.index import Index
    from chrono_mail.store import Store
    from chrono_mail.sync import ImapState, sync_imap

    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    index = Index(tmp_path / "index.sqlite")
    state = ImapState(tmp_path / "state.log", durable=False)
    imap.create_folder(folder_name)
    imap.subscribe(folder_name)
    try:
        imap.append(folder_name, MESSAGE)
        with connect(live_account, secure=True) as client:
            assert sync_imap(client, store, index, state).aborted is None
        (message,) = store.list_messages((folder_name,))
        store.set_flags(message.ref, frozenset({MessageFlag.FLAGGED}))  # changed here, not sent yet
        imap.unsubscribe(folder_name)  # as another program does
        with connect(live_account, secure=True) as client:
            report = sync_imap(client, store, index, state)
        assert report.aborted is None and (folder_name,) not in {folder.path for folder in store.list_folders()}
        imap.select(folder_name, read_only=True)
        (uid,) = imap.uid_search("ALL")
        assert "\\Flagged" in {flag for item in imap.uid_fetch(str(uid)) for flag in item.flags}  # sent before the folder went here
        imap.select("INBOX", read_only=True)
        imap.subscribe(folder_name)
        with connect(live_account, secure=True) as client:
            assert sync_imap(client, store, index, state).aborted is None
        assert len(store.list_messages((folder_name,))) == 1
    finally:
        with suppress(Exception):
            imap.select("INBOX", read_only=True)
        with suppress(Exception):
            imap.delete_folder(folder_name)
        with suppress(Exception):
            imap.unsubscribe(folder_name)
        index.close()
