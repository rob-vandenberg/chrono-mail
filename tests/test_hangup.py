import gc
import socket
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from chrono_mail.core import ChronoMailError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols import hangup
from chrono_mail.protocols.dav import DavClient
from chrono_mail.protocols.hangup import allow_connections, hang_up_all
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.pop3 import Pop3Client
from tests.enginefactory import make_engine
from tests.fakedav import FakeDav
from tests.fakepop3 import FakePop3Server
from tests.fakeserver import Conversation, ScriptedServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import call
from tests.test_engine_dav import add_account
from tests.test_engine_imap import World, mail, world  # noqa: F401 - fixture
from tests.test_sync_dav import event

# --- Version ------------------------------------------------------------
__version__ = "test_hangup.py 4.4.542"

# --- Version history ----------------------------------------------------
# v4.4.542: Initial: every wait for a server ends at once when the connection is hung up, and an interrupted check or sync loses nothing

ANYWAY: float = 5.0  # a wait of these tests ends by itself, so a wait that is not hung up fails its test instead of hanging


def outcome_of_hanging_up(work: Callable[[int], object]) -> BaseException | None:
    """This work waits for a server that accepts the connection and then says nothing; what it raised when that was hung up."""
    accepted, release = threading.Event(), threading.Event()
    raised: list[BaseException] = []

    def silent(conversation: Conversation) -> None:
        accepted.set()
        release.wait(ANYWAY)

    with ScriptedServer(silent) as server:

        def run() -> None:
            try:
                work(server.port)
            except BaseException as error:  # noqa: BLE001 - whatever it is, the test looks at it
                raised.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        assert accepted.wait(ANYWAY)
        hang_up_all()
        thread.join(ANYWAY / 2)
        woken = not thread.is_alive()
        release.set()
        thread.join()
    assert woken
    return raised[0] if raised else None


def imap(port: int, security: ConnectionSecurity = ConnectionSecurity.NONE) -> ImapClient:
    return ImapClient(ServerSettings("127.0.0.1", port, security, "user"), "secret")


def test_a_read_of_imap_ends_when_it_is_hung_up() -> None:
    assert isinstance(outcome_of_hanging_up(lambda port: imap(port).connect()), ProtocolError)


def test_setting_up_a_secure_connection_ends_when_it_is_hung_up() -> None:
    assert isinstance(outcome_of_hanging_up(lambda port: imap(port, ConnectionSecurity.TLS).connect()), ChronoMailError)


def test_a_read_of_pop3_ends_when_it_is_hung_up() -> None:
    assert isinstance(outcome_of_hanging_up(lambda port: Pop3Client(ServerSettings("127.0.0.1", port, ConnectionSecurity.NONE, "user"), "secret").connect()), ProtocolError)


def test_a_read_of_a_calendar_server_ends_when_it_is_hung_up() -> None:
    assert outcome_of_hanging_up(lambda port: DavClient(f"http://127.0.0.1:{port}/", "ann", "secret").options()) is not None


def test_no_new_connection_is_made_after_the_hang_up() -> None:
    accepted: list[int] = []
    with ScriptedServer(lambda conversation: accepted.append(1)) as server:
        hang_up_all()
        with pytest.raises(ProtocolError):
            imap(server.port).connect()
    assert accepted == []


def test_a_hang_up_just_before_the_connection_is_made_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect

    def hung_up_first(sock: socket.socket, address: tuple[str, int]) -> None:
        hang_up_all()
        real_connect(sock, address)

    def listens(conversation: Conversation) -> None:
        conversation.read_line()

    with ScriptedServer(listens) as server:
        monkeypatch.setattr(socket.socket, "connect", hung_up_first)
        with pytest.raises(ProtocolError):
            imap(server.port).connect()
    assert not hangup._held


def test_a_closed_connection_leaves_nothing_held() -> None:
    def greets(conversation: Conversation) -> None:
        conversation.send_line("* PREAUTH ready")
        conversation.read_line()

    with ScriptedServer(greets) as server:
        client = imap(server.port)
        client._read_capabilities = lambda: frozenset({"IMAP4REV1"})
        client.connect()
        assert len(hangup._held) == 1
        client.close()
    assert not hangup._held


def test_a_client_dropped_without_closing_leaves_nothing_held() -> None:
    def greets(conversation: Conversation) -> None:
        conversation.send_line("* PREAUTH ready")
        conversation.read_line()

    with ScriptedServer(greets) as server:
        client = imap(server.port)
        client._read_capabilities = lambda: frozenset({"IMAP4REV1"})
        client.connect()
        del client
        gc.collect()
    assert not hangup._held


def test_an_imap_check_that_is_hung_up_loses_nothing_and_the_next_check_gets_all(world: World) -> None:  # noqa: F811
    world.imap.add("INBOX", mail(1))
    world.imap.add("INBOX", mail(2))
    stuck = threading.Event()

    def silent_on_fetch(verb: str, count: int) -> bool:
        if verb != "UID" or stuck.is_set():
            return False
        stuck.set()
        hang_up_all()
        return True

    world.imap._fault = silent_on_fetch
    try:
        world.fetch()
    except ChronoMailError:
        pass  # a lost connection: as an error or inside the result, both are fine
    assert stuck.is_set()
    allow_connections()
    world.imap._fault = None
    world.fetch()
    assert sorted(row.subject for row in world.rows("Inbox")) == ["Message 1", "Message 2"]
    assert len(world.imap.uids("INBOX")) == 2


def test_a_pop3_check_that_is_hung_up_loses_nothing_and_the_next_check_gets_all(tmp_path: Path) -> None:
    stuck = threading.Event()

    def silent_on_the_second_message(verb: str, count: int) -> bool:
        if verb != "RETR" or count != 2:
            return False
        stuck.set()
        hang_up_all()
        return True

    messages = {f"uid{number}": raw_message(subject=f"Message {number}", message_id=f"<m{number}@x>") for number in (1, 2, 3)}
    engine = make_engine(tmp_path)
    with FakePop3Server(messages, fault=silent_on_the_second_message) as server:
        added = call(
            engine, "account.add", display_name="Me", email_address="me@example.com", username="user", password="secret",
            incoming_host="127.0.0.1", incoming_port=server.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=1, outgoing_security="none",
        )
        account = str(added.account_id)
        try:
            call(engine, "mail.fetch", account=account)
        except ChronoMailError:
            pass  # a lost connection
        assert stuck.is_set()
        allow_connections()
        server._fault = None
        call(engine, "mail.fetch", account=account)
        listed = call(engine, "message.list", account=account, folder="Inbox").messages
        assert sorted(row.subject for row in listed) == ["Message 1", "Message 2", "Message 3"]
        assert set(server.messages) == set(messages)


def test_a_calendar_sync_that_is_hung_up_loses_nothing_and_the_next_sync_gets_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account = add_account(tmp_path)
    with FakeDav() as server:
        server.add("events", "e1.ics", event("e1", "Meeting"))
        call(engine, "account.set-dav", account=account, url=server.url)
        call(engine, "dav.sync", account=account)
        server.add("events", "e2.ics", event("e2", "Lunch"))
        real_request = DavClient.request
        done: list[int] = []

        def hung_up_at_the_first_request(client: DavClient, *arguments: object, **options: object) -> object:
            if not done:
                done.append(1)
                hang_up_all()
            return real_request(client, *arguments, **options)  # pyrefly: ignore

        monkeypatch.setattr(DavClient, "request", hung_up_at_the_first_request)
        try:
            call(engine, "dav.sync", account=account)
        except ChronoMailError:
            pass  # a lost connection
        allow_connections()
        monkeypatch.setattr(DavClient, "request", real_request)
        (result,) = call(engine, "dav.sync", account=account)
        assert result.errors == () and len(server.collections["events"].items) == 2
        assert result.pulled == 1
