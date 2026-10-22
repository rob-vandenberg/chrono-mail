import ssl
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols.pop3 import Pop3Client, Pop3Limits
from tests.certs import make_certificate
from tests.fakepop3 import FakePop3Server
from tests.fakeserver import Conversation, ScriptedServer

# --- Version ------------------------------------------------------------
__version__ = "test_protocols_pop3.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial POP3 client tests

MESSAGE_A = b"From: a@example.com\r\nSubject: one\r\n\r\nfirst body\r\n"
MESSAGE_B = b"From: b@example.com\r\nSubject: two\r\n\r\n.starts with a dot\r\n..two dots\r\nlast\r\n"
FAST = Pop3Limits(maximum_line_bytes=4096, maximum_message_bytes=20_000, maximum_messages=50, connect_timeout_seconds=3, read_timeout_seconds=1.0)


def settings(port: int, security: ConnectionSecurity = ConnectionSecurity.NONE, host: str = "127.0.0.1") -> ServerSettings:
    return ServerSettings(host, port, security, "user")


def client(server: ScriptedServer, limits: Pop3Limits = FAST, security: ConnectionSecurity = ConnectionSecurity.NONE, context: ssl.SSLContext | None = None, password: str = "secret") -> Pop3Client:
    return Pop3Client(settings(server.port, security), password, limits, context)


# --- the happy path against a small fake server (the real Dovecot is covered by the live tests) ---


def test_session_with_every_command() -> None:
    with FakePop3Server({"uid-a": MESSAGE_A, "uid-b": MESSAGE_B}) as server, client(server) as pop3:
        assert "UIDL" in pop3.capabilities and "TOP" in pop3.capabilities
        assert pop3.stat() == (2, len(MESSAGE_A) + len(MESSAGE_B))
        assert pop3.list_sizes() == {1: len(MESSAGE_A), 2: len(MESSAGE_B)}
        assert pop3.uidl() == [(1, "uid-a"), (2, "uid-b")]
        assert pop3.retr(1) == MESSAGE_A
        assert pop3.retr(2) == MESSAGE_B  # dot-stuffing is removed exactly
        assert pop3.top(2) == b"From: b@example.com\r\nSubject: two\r\n\r\n"
        pop3.noop()
        pop3.dele(1)
        pop3.reset()
        pop3.dele(2)
        pop3.quit()
        assert server.messages == {"uid-a": MESSAGE_A}


def test_deletions_only_happen_on_quit() -> None:
    with FakePop3Server({"uid-a": MESSAGE_A}) as server:
        with client(server) as pop3:
            pop3.dele(1)
        assert server.messages == {"uid-a": MESSAGE_A}  # closed without QUIT: nothing deleted
        with client(server) as pop3:
            pop3.dele(1)
            pop3.quit()
        assert server.messages == {}


def test_lines_longer_than_poplib_allows_are_received() -> None:
    long_line = b"X-Long: " + b"A" * 3000 + b"\r\n"
    raw = b"From: a@example.com\r\n" + long_line + b"\r\nbody " + b"B" * 3500 + b"\r\n"
    with FakePop3Server({"u": raw}) as server, client(server) as pop3:
        assert pop3.retr(1) == raw


def test_message_of_several_megabytes() -> None:
    raw = b"Subject: big\r\n\r\n" + b"".join(b"line %d of a large message\r\n" % number for number in range(150_000))
    limits = Pop3Limits(maximum_message_bytes=10 * 1024 * 1024)
    with FakePop3Server({"u": raw}) as server, client(server, limits) as pop3:
        assert pop3.retr(1) == raw


def test_empty_mailbox() -> None:
    with FakePop3Server({}) as server, client(server) as pop3:
        assert pop3.stat() == (0, 0) and pop3.list_sizes() == {} and pop3.uidl() == []


def test_wrong_password_is_an_authentication_error_without_the_password() -> None:
    with FakePop3Server({}, password="right") as server:
        with pytest.raises(AuthenticationError) as raised:
            client(server, password="wrong-password-123").connect()
        assert "wrong-password-123" not in str(raised.value)
        assert not any("wrong-password-123" in command for command in server.commands)


def test_commands_before_login_are_refused() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send_line("+OK hello")
        while (line := conversation.read_line()) is not None:
            conversation.send_line("+OK" if line.startswith(b"CAPA") else "-ERR [AUTH] nope")
            if line.startswith(b"CAPA"):
                conversation.send_line(".")

    with ScriptedServer(script) as server, pytest.raises(AuthenticationError):
        client(server).connect()


# --- TLS ---------------------------------------------------------------------------------------


def test_implicit_tls_with_a_trusted_certificate(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakePop3Server({"u": MESSAGE_A}, implicit_tls=server_context) as server:
        with client(server, security=ConnectionSecurity.TLS, context=client_context) as pop3:
            assert pop3.retr(1) == MESSAGE_A


def test_stls_with_a_trusted_certificate(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakePop3Server({"u": MESSAGE_A}, stls_context=server_context) as server:
        with client(server, security=ConnectionSecurity.STARTTLS, context=client_context) as pop3:
            assert pop3.retr(1) == MESSAGE_A
        assert "STLS" in server.commands


def test_untrusted_certificate_is_refused_and_nothing_is_sent_after(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakePop3Server({"u": MESSAGE_A}, implicit_tls=server_context) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            client(server, security=ConnectionSecurity.TLS).connect()  # the default context does not trust it
        assert server.commands == []  # no user name and no password were sent


def test_untrusted_certificate_with_stls_is_refused_before_login(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakePop3Server({"u": MESSAGE_A}, stls_context=server_context) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            client(server, security=ConnectionSecurity.STARTTLS).connect()
        assert not any(command.startswith(("USER", "PASS")) for command in server.commands)


def test_stls_not_offered_is_refused_instead_of_falling_back_to_plain() -> None:
    with FakePop3Server({}) as server:
        with pytest.raises(ProtocolError, match="STLS"):
            client(server, security=ConnectionSecurity.STARTTLS).connect()
        assert not any(command.startswith(("USER", "PASS")) for command in server.commands)


def test_plain_server_for_a_tls_connection_is_an_error() -> None:
    with FakePop3Server({}) as server:
        with pytest.raises(ProtocolError):
            client(server, security=ConnectionSecurity.TLS).connect()


def test_data_injected_before_the_handshake_is_refused(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)

    def script(conversation: Conversation) -> None:
        conversation.send_line("+OK hello")
        while (line := conversation.read_line()) is not None:
            if line.startswith(b"CAPA"):
                conversation.send(b"+OK\r\nSTLS\r\n.\r\n")
            elif line.startswith(b"STLS"):
                conversation.send(b"+OK go ahead\r\n-ERR injected in the clear\r\n")  # extra data before TLS
                return
    with ScriptedServer(script) as server, pytest.raises(ProtocolError, match="before the secure"):
        client(server, security=ConnectionSecurity.STARTTLS, context=client_context).connect()


# --- hostile and broken servers -----------------------------------------------------------------


def greeting_then(*responses: bytes) -> Callable[[Conversation], None]:
    def script(conversation: Conversation) -> None:
        conversation.send_line("+OK hello")
        replies = list(responses)
        while (line := conversation.read_line()) is not None:
            if line.upper().startswith(b"CAPA"):
                conversation.send(b"-ERR no capa\r\n")
            elif line.upper().startswith((b"USER", b"PASS")):
                conversation.send(b"+OK\r\n")
            elif replies:
                conversation.send(replies.pop(0))
                if not replies:
                    return  # the scripted answers are used up: hang up, like a server that dies
            else:
                return
    return script


@pytest.mark.parametrize(
    "greeting",
    [b"", b"hello without a status\r\n", b"-ERR go away\r\n", b"+OKAY? \r\n", b"\x00\x01\x02\r\n", b"+" + b"A" * 10_000 + b"\r\n", b"A" * 10_000],
)
def test_bad_greetings(greeting: bytes) -> None:
    def script(conversation: Conversation) -> None:
        conversation.send(greeting)
        conversation.read_line()

    with ScriptedServer(script) as server, pytest.raises(ProtocolError):
        client(server).connect()


def test_silent_server_times_out() -> None:
    def script(conversation: Conversation) -> None:
        conversation.sleep(5)

    with ScriptedServer(script) as server:
        started = time.monotonic()
        with pytest.raises(ProtocolError, match="timeout"):
            client(server).connect()
        assert time.monotonic() - started < 4


def test_server_that_stalls_in_the_middle_of_a_message_times_out() -> None:
    def script(conversation: Conversation) -> None:
        greeting_then(b"+OK\r\nSubject: x\r\n\r\npartial")(conversation)
        conversation.sleep(5)

    with ScriptedServer(script) as server, client(server) as pop3, pytest.raises(ProtocolError, match="timeout|closed"):
        pop3.retr(1)


def test_connection_closed_in_the_middle_of_a_message() -> None:
    with ScriptedServer(greeting_then(b"+OK\r\nSubject: x\r\n\r\ncut off here")) as server, client(server) as pop3:
        with pytest.raises(ProtocolError, match="closed"):
            pop3.retr(1)


def test_status_line_without_end_of_line_is_cut_at_the_cap() -> None:
    with ScriptedServer(greeting_then(b"+OK " + b"A" * 100_000)) as server, client(server) as pop3:
        with pytest.raises(ProtocolError, match="too long"):
            pop3.noop()


def test_message_line_longer_than_the_cap() -> None:
    with ScriptedServer(greeting_then(b"+OK\r\n" + b"A" * 5000 + b"\r\n.\r\n")) as server, client(server) as pop3:
        with pytest.raises(ProtocolError, match="too long"):
            pop3.retr(1)


def test_endless_response_stops_at_the_size_limit() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send_line("+OK hello")
        while (line := conversation.read_line()) is not None:
            if line.upper().startswith(b"CAPA"):
                conversation.send_line("-ERR no capa")
            elif line.upper().startswith(b"RETR"):
                conversation.send_line("+OK")
                for _ in range(10_000):  # far more than the 20,000 byte limit, and never a final dot
                    conversation.send(b"x" * 100 + b"\r\n")
            else:
                conversation.send_line("+OK")

    with ScriptedServer(script) as server, client(server) as pop3:
        with pytest.raises(ProtocolError, match="larger than allowed"):
            pop3.retr(1)


@pytest.mark.parametrize(
    "reply",
    [b"+OK\r\n", b"+OK abc def\r\n", b"+OK -1 5\r\n", b"+OK 1\r\n", b"+OK 99999999999999999999 5\r\n", b"+OK 51 5\r\n", b"+OK 1 -5\r\n", b"-ERR busy\r\n", b"garbage\r\n", b"\x00\r\n"],
)
def test_invalid_stat_responses(reply: bytes) -> None:
    with ScriptedServer(greeting_then(reply)) as server, client(server) as pop3, pytest.raises(ProtocolError):
        pop3.stat()


@pytest.mark.parametrize(
    "lines",
    [
        [b"abc"], [b"1"], [b"0 5"], [b"1 x"], [b"1 -5"], [b"-1 5"], [b"51 5"], [b"1 5 5 5 junk", b"x y"], [b"1 " + b"9" * 40], [b""],
    ],
)
def test_invalid_list_lines(lines: list[bytes]) -> None:
    body = b"+OK\r\n" + b"".join(line + b"\r\n" for line in lines) + b".\r\n"
    with ScriptedServer(greeting_then(body)) as server, client(server) as pop3:
        try:
            result = pop3.list_sizes()
        except ProtocolError:
            return
        assert all(1 <= number <= 50 for number in result)  # tolerated forms still stay within the limits


@pytest.mark.parametrize(
    "lines",
    [[b"1"], [b"1 a b"], [b"0 id"], [b"x id"], [b"1 " + b"a" * 71], [b"1 id\x01"], [b"1 \xff\xfe"], [b"99 id"]],
)
def test_invalid_uidl_lines(lines: list[bytes]) -> None:
    body = b"+OK\r\n" + b"".join(line + b"\r\n" for line in lines) + b".\r\n"
    with ScriptedServer(greeting_then(body)) as server, client(server) as pop3, pytest.raises(ProtocolError):
        pop3.uidl()


def test_too_many_messages_listed() -> None:
    body = b"+OK\r\n" + b"".join(b"%d id%d\r\n" % (number, number) for number in range(1, 80)) + b".\r\n"
    with ScriptedServer(greeting_then(body)) as server, client(server) as pop3, pytest.raises(ProtocolError):
        pop3.uidl()


def test_lone_line_feeds_and_nul_bytes_are_tolerated() -> None:
    with ScriptedServer(greeting_then(b"+OK\nSubject: x\n\nbody\x00more\n.\n")) as server, client(server) as pop3:
        assert pop3.retr(1) == b"Subject: x\r\n\r\nbody\x00more\r\n"


def test_error_responses_are_protocol_errors_with_safe_text() -> None:
    with ScriptedServer(greeting_then(b"-ERR \x1b[31mred\x00 text \xff\r\n")) as server, client(server) as pop3:
        with pytest.raises(ProtocolError) as raised:
            pop3.retr(7)
        assert all(32 <= ord(character) < 127 for character in str(raised.value))


def test_connection_refused_and_unreachable_host_are_protocol_errors() -> None:
    with pytest.raises(ProtocolError):
        Pop3Client(settings(1), "x", FAST).connect()  # nothing listens on port 1
    with pytest.raises(ProtocolError):
        Pop3Client(ServerSettings("no-such-host.invalid", 110, ConnectionSecurity.NONE, "u"), "x", FAST).connect()


def test_using_a_closed_client_is_an_error_not_a_crash() -> None:
    with FakePop3Server({}) as server:
        pop3 = client(server)
        pop3.connect()
        pop3.close()
        with pytest.raises(ProtocolError):
            pop3.noop()
        pop3.close()  # closing twice is harmless


@pytest.mark.parametrize("username", ["user\r\nDELE 1", "user\nDELE 1", "us\x00er"])
def test_a_line_break_in_the_user_name_cannot_inject_commands(username: str) -> None:
    with FakePop3Server({"u": MESSAGE_A}) as server:
        with pytest.raises(ProtocolError, match="line break"):
            Pop3Client(ServerSettings("127.0.0.1", server.port, ConnectionSecurity.NONE, username), "secret", FAST).connect()
        assert not any(command.startswith("DELE") for command in server.commands)
        assert server.messages == {"u": MESSAGE_A}


def test_a_line_break_in_the_password_cannot_inject_commands() -> None:
    with FakePop3Server({"u": MESSAGE_A}) as server:
        with pytest.raises(ProtocolError, match="line break"):
            client(server, password="secret\r\nDELE 1").connect()
        assert not any(command.startswith("DELE") for command in server.commands)
