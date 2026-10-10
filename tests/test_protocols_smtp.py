import ssl
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols.smtp import SendResult, SmtpLimits, send_message
from tests.certs import make_certificate
from tests.fakeserver import Conversation, ScriptedServer
from tests.fakesmtp import FakeSmtpServer

# --- Version ------------------------------------------------------------
__version__ = "test_protocols_smtp.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial SMTP client tests

MESSAGE = b"From: a@example.com\r\nTo: b@example.com\r\nSubject: hi\r\n\r\nHello.\r\n.A line that starts with a dot\r\n"
FAST = SmtpLimits(timeout_seconds=1.5)


def settings(port: int, security: ConnectionSecurity = ConnectionSecurity.NONE, username: str = "user") -> ServerSettings:
    return ServerSettings("127.0.0.1", port, security, username)


def send(server: ScriptedServer, security: ConnectionSecurity = ConnectionSecurity.NONE, password: str = "secret", recipients: tuple[str, ...] = ("b@example.com",), raw: bytes = MESSAGE, context: ssl.SSLContext | None = None, username: str = "user", limits: SmtpLimits = FAST) -> SendResult:
    return send_message(settings(server.port, security, username), password, "a@example.com", recipients, raw, limits, context, "client.example")


def test_plain_connection() -> None:
    with FakeSmtpServer() as server:
        result = send(server)
        assert result.accepted == ("b@example.com",) and result.refused == ()
        assert server.received[0].sender == "a@example.com" and server.received[0].recipients == ("b@example.com",)
        assert server.received[0].data == MESSAGE  # including the line that starts with a dot


def test_starttls_with_a_trusted_certificate(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakeSmtpServer(starttls_context=server_context) as server:
        send(server, ConnectionSecurity.STARTTLS, context=client_context)
        assert "STARTTLS" in server.commands and server.received[0].data == MESSAGE
        assert server.commands.index("STARTTLS") < server.commands.index("AUTH ***")  # never the password in the clear


def test_implicit_tls_with_a_trusted_certificate(tmp_path: Path) -> None:
    server_context, client_context = make_certificate(tmp_path)
    with FakeSmtpServer(implicit_tls=server_context) as server:
        send(server, ConnectionSecurity.TLS, context=client_context)
        assert server.received[0].data == MESSAGE


def test_untrusted_certificate_is_refused_before_the_login(tmp_path: Path) -> None:
    server_context, _ = make_certificate(tmp_path)
    with FakeSmtpServer(starttls_context=server_context) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            send(server, ConnectionSecurity.STARTTLS)
        assert not any(command.startswith(("AUTH", "MAIL")) for command in server.commands)
    with FakeSmtpServer(implicit_tls=server_context) as server:
        with pytest.raises(ProtocolError, match="certificate"):
            send(server, ConnectionSecurity.TLS)
        assert server.commands == []


def test_starttls_not_offered_is_refused_instead_of_sending_in_the_clear() -> None:
    with FakeSmtpServer() as server:
        with pytest.raises(ProtocolError, match="STARTTLS"):
            send(server, ConnectionSecurity.STARTTLS)
        assert not any(command.startswith(("AUTH", "MAIL")) for command in server.commands)


def test_wrong_password_is_an_authentication_error_without_the_password() -> None:
    with FakeSmtpServer(password="right") as server:
        with pytest.raises(AuthenticationError) as raised:
            send(server, password="wrong-password-123")
        assert "wrong-password-123" not in str(raised.value)
        assert server.received == []


def test_no_username_means_no_login() -> None:
    with FakeSmtpServer(offer_auth=False) as server:
        send(server, username="")
        assert len(server.received) == 1 and not any(command.startswith("AUTH") for command in server.commands)


def test_server_without_authentication_when_credentials_are_set() -> None:
    with FakeSmtpServer(offer_auth=False) as server, pytest.raises(ProtocolError, match="authentication"):
        send(server)


def test_partly_refused_recipients_are_reported() -> None:
    with FakeSmtpServer(refuse=frozenset({"bad@example.com"})) as server:
        result = send(server, recipients=("b@example.com", "bad@example.com", "c@example.com"))
        assert result.accepted == ("b@example.com", "c@example.com")
        assert [(item.address, item.code) for item in result.refused] == [("bad@example.com", 550)]
        assert server.received[0].recipients == ("b@example.com", "c@example.com")


def test_all_recipients_refused_is_an_error() -> None:
    with FakeSmtpServer(refuse=frozenset({"bad@example.com"})) as server:
        with pytest.raises(ProtocolError, match="every recipient"):
            send(server, recipients=("bad@example.com",))
        assert server.received == []


def test_message_larger_than_the_server_accepts_is_not_sent() -> None:
    with FakeSmtpServer(size_limit=50) as server:
        with pytest.raises(ProtocolError, match="larger"):
            send(server)
        assert not any(command.startswith("DATA") for command in server.commands)


def test_message_larger_than_our_own_limit_is_not_sent() -> None:
    with FakeSmtpServer() as server:
        with pytest.raises(ProtocolError, match="larger"):
            send(server, limits=SmtpLimits(timeout_seconds=1.5, maximum_message_bytes=10))
        assert server.connections == 0


def test_eight_bit_message() -> None:
    raw = "Subject: café\r\n\r\ncafé\r\n".encode()
    with FakeSmtpServer() as server:
        send(server, raw=raw)
        assert server.received[0].data == raw
    with FakeSmtpServer(eight_bit=False) as server, pytest.raises(ProtocolError, match="8-bit"):
        send(server, raw=raw)


@pytest.mark.parametrize("recipient", ["", "nobody", "a@b@c", "a b@example.com", "<a@example.com>", "a@example.com\r\nRCPT TO:<evil@example.com>", "a@exämple.com", "a,b@example.com", "a@example.com\x00"])
def test_invalid_recipient_addresses_never_reach_the_network(recipient: str) -> None:
    with FakeSmtpServer() as server:
        with pytest.raises(ProtocolError):
            send(server, recipients=(recipient,))
        assert server.connections == 0


def test_no_recipient_and_invalid_sender() -> None:
    with FakeSmtpServer() as server:
        with pytest.raises(ProtocolError):
            send(server, recipients=())
        with pytest.raises(ProtocolError):
            send_message(settings(server.port), "secret", "bad sender", ("b@example.com",), MESSAGE, FAST)
        assert server.connections == 0


# --- hostile and broken servers -----------------------------------------------------------------


def script_of(*lines: bytes, then: Callable[[Conversation], None] | None = None) -> Callable[[Conversation], None]:
    def script(conversation: Conversation) -> None:
        for line in lines:
            conversation.send(line)
        if then is not None:
            then(conversation)
        else:
            conversation.read_line()
    return script


@pytest.mark.parametrize("greeting", [b"", b"hello there\r\n", b"554 no service\r\n", b"220" + b"A" * 20_000, b"\x00\x01\r\n", b"999 what\r\n"])
def test_bad_greetings(greeting: bytes) -> None:
    with ScriptedServer(script_of(greeting)) as server, pytest.raises(ProtocolError):
        send(server)


def test_endless_multiline_reply_is_cut_off() -> None:
    def endless(conversation: Conversation) -> None:
        conversation.send_line("220 hello")
        conversation.read_line()
        for _ in range(100_000):
            conversation.send(b"250-more and more\r\n")

    with ScriptedServer(endless) as server:
        started = time.monotonic()
        with pytest.raises(ProtocolError, match="too long|closed"):
            send(server)
        assert time.monotonic() - started < 3


def test_reply_line_that_is_too_long() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send_line("220 hello")
        conversation.read_line()
        conversation.send(b"250 " + b"A" * 20_000 + b"\r\n")

    with ScriptedServer(script) as server, pytest.raises(ProtocolError):
        send(server)


def test_silent_server_times_out() -> None:
    with ScriptedServer(lambda conversation: conversation.sleep(5)) as server:
        started = time.monotonic()
        with pytest.raises(ProtocolError):
            send(server)
        assert time.monotonic() - started < 4


def test_connection_dropped_in_the_middle_of_the_data() -> None:
    def script(conversation: Conversation) -> None:
        conversation.send_line("220 hi")
        while (line := conversation.read_line()) is not None:
            verb = line.split(b" ")[0].upper()
            if verb == b"EHLO":
                conversation.send(b"250-hi\r\n250 AUTH PLAIN\r\n")
            elif verb == b"AUTH":
                conversation.send(b"235 ok\r\n")
            elif verb in (b"MAIL", b"RCPT"):
                conversation.send(b"250 ok\r\n")
            elif verb == b"DATA":
                conversation.send(b"354 go\r\n")
                conversation.read_line()
                return  # hang up before the end of the message

    with ScriptedServer(script) as server, pytest.raises(ProtocolError):
        send(server)


@pytest.mark.parametrize("failing", [b"MAIL", b"DATA", b"EHLO"])
@pytest.mark.parametrize("answer", [b"421 4.3.2 Shutting down\r\n", b"451 4.3.0 Local error\r\n", b"554 5.7.1 Rejected\r\n", b"garbage\r\n"])
def test_error_answers_at_every_step(failing: bytes, answer: bytes) -> None:
    def script(conversation: Conversation) -> None:
        conversation.send_line("220 hi")
        while (line := conversation.read_line()) is not None:
            verb = line.split(b" ")[0].upper()
            if verb == failing:
                conversation.send(answer)
            elif verb == b"EHLO":
                conversation.send(b"250-hi\r\n250 AUTH PLAIN\r\n")
            elif verb == b"AUTH":
                conversation.send(b"235 ok\r\n")
            elif verb in (b"MAIL", b"RCPT"):
                conversation.send(b"250 ok\r\n")
            elif verb == b"DATA":
                conversation.send(b"354 go\r\n")

    with ScriptedServer(script) as server, pytest.raises(ProtocolError):
        send(server)


def test_error_text_from_the_server_is_made_safe() -> None:
    with ScriptedServer(script_of(b"554 \x1b[31mred\x00 \xff text\r\n")) as server:
        with pytest.raises(ProtocolError) as raised:
            send(server)
        assert all(32 <= ord(character) < 127 for character in str(raised.value))


def test_connection_refused_and_unknown_host() -> None:
    with pytest.raises(ProtocolError):
        send_message(settings(1), "x", "a@example.com", ("b@example.com",), MESSAGE, FAST)
    with pytest.raises(ProtocolError):
        send_message(ServerSettings("no-such-host.invalid", 25, ConnectionSecurity.NONE, "u"), "x", "a@example.com", ("b@example.com",), MESSAGE, FAST)
