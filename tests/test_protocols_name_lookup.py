import socket

import pytest

from chrono_mail.core import ConnectionSecurity, ProtocolError, ServerSettings
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.pop3 import Pop3Client
from chrono_mail.protocols.smtp import send_message
from chrono_mail.protocols.tls import NAME_NOT_FOUND

# --- Version ------------------------------------------------------------
__version__ = "test_protocols_name_lookup.py 4.6.649"

# --- Version history ----------------------------------------------------
# v4.6.649: Initial: a server name that could not be looked up is said in plain words by IMAP, POP3 and SMTP (D-366)

SERVER = ServerSettings("mail.example.invalid", 993, ConnectionSecurity.TLS, "user")


@pytest.fixture(autouse=True)
def no_name_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No internet: every name lookup fails, with the text Windows gives."""

    def failed(*_arguments: object, **_named: object) -> list[object]:
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", failed)


def test_imap_says_the_server_name_could_not_be_found() -> None:
    with pytest.raises(ProtocolError) as raised:
        ImapClient(SERVER, "x").connect()
    assert str(raised.value) == NAME_NOT_FOUND == "The server's name could not be found (no internet connection?)"


def test_pop3_says_the_server_name_could_not_be_found() -> None:
    with pytest.raises(ProtocolError) as raised:
        Pop3Client(SERVER, "x").connect()
    assert str(raised.value) == NAME_NOT_FOUND


def test_smtp_says_the_server_name_could_not_be_found() -> None:
    with pytest.raises(ProtocolError) as raised:
        send_message(ServerSettings("mail.example.invalid", 465, ConnectionSecurity.TLS, "user"), "x", "me@example.com", ["you@example.com"], b"Subject: x\r\n\r\nx\r\n")
    assert str(raised.value) == NAME_NOT_FOUND
