import io
import json
import socket
import time
from collections.abc import Callable, Generator
from email.utils import make_msgid
from pathlib import Path

import pytest

from chrono_mail.cli import main
from chrono_mail.config import AppDirectories, InMemoryPasswordStore, Pop3DeletePolicy, Pop3Options
from chrono_mail.core import AuthenticationError, ConnectionSecurity, FolderRole, MessageRef, ProtocolError, ServerSettings
from chrono_mail.crypto import InMemoryKeyStore, generate_data_key
from chrono_mail.engine import Engine
from chrono_mail.index import Index
from chrono_mail.mime import Address, OutgoingMessage, build_message, envelope_recipients, parse_message
from chrono_mail.protocols.pop3 import Pop3Client
from chrono_mail.protocols.smtp import send_message
from chrono_mail.store import Store
from chrono_mail.sync import Pop3State, fetch_pop3, send_and_file
from tests.live.account import LiveAccount

# --- Version ------------------------------------------------------------
__version__ = "test_live_clients.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial live client tests

pytestmark = pytest.mark.live

WAIT_SECONDS = 60.0
INBOX = ("Inbox",)


def pop3_settings(account: LiveAccount, security: ConnectionSecurity) -> ServerSettings:
    port = account.pop3_ssl_port if security is ConnectionSecurity.TLS else account.pop3_port
    return ServerSettings(account.host, port, security, account.user)


def smtp_settings(account: LiveAccount, security: ConnectionSecurity) -> ServerSettings:
    ports = {ConnectionSecurity.TLS: account.smtp_ssl_port, ConnectionSecurity.STARTTLS: account.smtp_starttls_port, ConnectionSecurity.NONE: account.smtp_plain_port}
    return ServerSettings(account.host, ports[security], security, account.user)


def message_to_self(account: LiveAccount, token: str) -> OutgoingMessage:
    return OutgoingMessage(Address("Chrono Mail test", account.user), (Address("", account.user),), subject=f"Chrono Mail live client test {token}", text_body=f"Token: {token}\n")


def remove_from_server(account: LiveAccount, token: str) -> int:
    """Delete every message on the server whose subject contains the token (cleanup after a test)."""
    removed = 0
    with Pop3Client(pop3_settings(account, ConnectionSecurity.TLS), account.password) as client:
        for number, _ in client.uidl():
            if token.encode() in client.top(number, 0):
                client.dele(number)
                removed += 1
        client.quit()
    return removed


@pytest.fixture
def token(live_account: LiveAccount) -> Generator[str]:
    value = make_msgid(domain="chrono-mail.test")
    yield value
    remove_from_server(live_account, value)


def wait_for(action: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if action():
            return True
        time.sleep(2)
    return False


# --- the POP3 client against the real Dovecot ---------------------------------------------------------


@pytest.mark.parametrize("security", [ConnectionSecurity.TLS, ConnectionSecurity.STARTTLS], ids=["tls-995", "stls-110"])
def test_pop3_client_logs_in_and_lists(live_account: LiveAccount, security: ConnectionSecurity) -> None:
    with Pop3Client(pop3_settings(live_account, security), live_account.password) as client:
        count, size = client.stat()
        assert count >= 0 and size >= 0
        assert len(client.uidl()) == count == len(client.list_sizes())
        assert "UIDL" in client.capabilities


def test_pop3_client_wrong_password(live_account: LiveAccount) -> None:
    if live_account.wrong_password_attempts < 1:
        pytest.skip("wrong_password_attempts is 0 (disabled by configuration)")
    for _ in range(live_account.wrong_password_attempts):
        with pytest.raises(AuthenticationError) as raised:
            Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password + "-wrong").connect()
        assert live_account.password not in str(raised.value)


def test_pop3_client_refuses_the_ip_address_certificate(live_account: LiveAccount) -> None:
    address = socket.gethostbyname(live_account.host)
    settings = ServerSettings(address, live_account.pop3_ssl_port, ConnectionSecurity.TLS, live_account.user)
    with pytest.raises(ProtocolError, match="certificate"):
        Pop3Client(settings, live_account.password).connect()


# --- SMTP and POP3 together ----------------------------------------------------------------------------------


@pytest.mark.parametrize("security", [ConnectionSecurity.STARTTLS, ConnectionSecurity.TLS], ids=["starttls-587", "tls-465"])
def test_send_with_our_client_and_receive_with_our_client(live_account: LiveAccount, security: ConnectionSecurity, token: str) -> None:
    outgoing = message_to_self(live_account, token)
    raw = build_message(outgoing)
    result = send_message(smtp_settings(live_account, security), live_account.password, live_account.user, envelope_recipients(outgoing), raw)
    assert result.accepted == (live_account.user,) and result.refused == ()
    received: list[bytes] = []

    def arrived() -> bool:
        with Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password) as client:
            for number, _ in client.uidl():
                if token.encode() in client.top(number, 0):
                    received.append(client.retr(number))
                    return True
        return False

    assert wait_for(arrived), "the message did not arrive in time"
    parsed = parse_message(received[0])
    assert parsed.subject == outgoing.subject and parsed.text_body == outgoing.text_body
    assert parsed.defects == ()


def test_smtp_client_wrong_password(live_account: LiveAccount) -> None:
    if live_account.wrong_password_attempts < 1:
        pytest.skip("wrong_password_attempts is 0 (disabled by configuration)")
    outgoing = message_to_self(live_account, "never-sent")
    for _ in range(live_account.wrong_password_attempts):
        with pytest.raises(AuthenticationError):
            send_message(smtp_settings(live_account, ConnectionSecurity.STARTTLS), "wrong", live_account.user, envelope_recipients(outgoing), build_message(outgoing))


# --- the whole chain: send_and_file, fetch_pop3, store, index --------------------------------------------------


def test_send_file_fetch_verify_and_delete_on_the_server(live_account: LiveAccount, tmp_path: Path, token: str) -> None:
    store = Store.create(tmp_path / "store", "live", generate_data_key(), durable=False)
    for path, role in (("Inbox", FolderRole.INBOX), ("Sent", FolderRole.SENT), ("Outbox", FolderRole.OTHER)):
        store.create_folder((path,), role)
    index = Index(tmp_path / "index.sqlite")
    state = Pop3State(tmp_path / "state.log", durable=False)
    outcome = send_and_file(store, index, smtp_settings(live_account, ConnectionSecurity.STARTTLS), live_account.password, message_to_self(live_account, token), ("Outbox",), ("Sent",))
    assert outcome.sent and outcome.error is None and outcome.reference.folder_path == ("Sent",)

    def fetched() -> bool:
        with Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password) as client:
            report = fetch_pop3(client, store, index, state, INBOX, Pop3Options(Pop3DeletePolicy.KEEP), 50 * 1024 * 1024)
            assert report.aborted is None and report.failed == ()
        return any(token in row.subject for row in index.search("live client test", folder=INBOX))

    assert wait_for(fetched), "the message did not arrive in time"
    (row,) = [row for row in index.search("live client test", folder=INBOX) if token in row.subject]
    assert store.read(store.list_messages(INBOX)[0].ref).count(token.encode()) >= 1
    assert len(index.search("live client test", folder=("Sent",))) >= 1
    # the second run knows what it has; then the delete policy removes it from the server, but only because it is verified
    with Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password) as client:
        again = fetch_pop3(client, store, index, state, INBOX, Pop3Options(Pop3DeletePolicy.DELETE_AFTER_DOWNLOAD), 50 * 1024 * 1024)
    assert again.downloaded == 0 and again.already_known >= 1 and again.deleted_on_server >= 1
    with Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password) as client:
        assert not any(token.encode() in client.top(number, 0) for number, _ in client.uidl())
    assert token.encode() in store.read(MessageRef(store.account_id, INBOX, row.store_id))  # and the local copy is still there


# --- the command line, from an empty installation to a message read -----------------------------------------------


def test_command_line_session_against_the_real_server(live_account: LiveAccount, tmp_path: Path, token: str) -> None:
    passwords, keys = InMemoryPasswordStore(), InMemoryKeyStore()

    def run(*arguments: str, stdin: str = "") -> object:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["--json", "--config-dir", str(tmp_path / "c"), "--data-dir", str(tmp_path / "d"), *arguments],
            lambda directories: Engine(directories, passwords, keys, durable=False),
            io.StringIO(stdin), out, err,
        )
        assert code == 0, (arguments, out.getvalue(), err.getvalue())
        return json.loads(out.getvalue())

    created = run(
        "account.add", "--display-name", "Live test", "--email-address", live_account.user, "--username", live_account.user, "--password-stdin",
        "--incoming-host", live_account.host, "--incoming-port", str(live_account.pop3_ssl_port), "--incoming-security", "tls",
        "--outgoing-host", live_account.host, "--outgoing-port", str(live_account.smtp_starttls_port), "--outgoing-security", "starttls",
        "--delete-policy", "delete_after_download", stdin=live_account.password + "\n",
    )
    assert isinstance(created, dict)
    account = str(created["account_id"])
    sent = run("mail.send", "--account", account, "--to", live_account.user, "--subject", f"Chrono Mail live client test {token}", "--body", f"Token: {token}")
    assert isinstance(sent, dict) and sent["sent"] is True

    def arrived() -> bool:
        report = run("mail.fetch", "--account", account)
        assert isinstance(report, dict) and report["aborted"] is None and report["failed"] == []
        return bool(run("message.search", "--account", account, "--query", "live client test", "--folder", "Inbox"))

    assert wait_for(arrived), "the message did not arrive in time"
    found = run("message.search", "--account", account, "--query", "live client test", "--folder", "Inbox")
    assert isinstance(found, list)
    ours = [row for row in found if isinstance(row, dict) and token in str(row["subject"])]
    assert len(ours) == 1
    shown = run("message.show", "--account", account, "--folder", "Inbox", "--message", str(ours[0]["message_id"]))
    assert isinstance(shown, dict) and token in str(shown["text_body"]) and shown["problems"] == []
    verified = run("store.verify", "--account", account)
    assert isinstance(verified, dict) and verified["checked"] >= 2 and verified["damaged"] == []
    with Pop3Client(pop3_settings(live_account, ConnectionSecurity.TLS), live_account.password) as client:
        assert not any(token.encode() in client.top(number, 0) for number, _ in client.uidl())  # the delete policy removed it
