import socket
from collections.abc import Generator

import pytest

from chrono_mail.core import ProtocolError
from chrono_mail.protocols.lineconnection import LineConnection

# --- Version ------------------------------------------------------------
__version__ = "test_protocols_lineconnection.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: Initial tests of the one copy of sending to a mail server and reading its lines (R-16)


@pytest.fixture
def pair() -> Generator[tuple[LineConnection, socket.socket]]:
    ours, server = socket.socketpair()
    ours.settimeout(1.0)
    connection = LineConnection(maximum_line_bytes=64, read_timeout_seconds=1.0)
    connection.socket = ours
    yield connection, server
    connection.close()
    server.close()


def test_lines_come_without_their_endings_and_bytes_exactly(pair: tuple[LineConnection, socket.socket]) -> None:
    connection, server = pair
    server.sendall(b"first\r\nsecond\nABCDEF")
    assert connection.read_line() == b"first"
    assert connection.line_waiting()
    assert connection.read_line() == b"second"
    assert not connection.line_waiting() and connection.waiting()
    assert connection.read_exactly(4) == b"ABCD"
    connection.send(b"NOOP\r\n")
    assert server.recv(100) == b"NOOP\r\n"


def test_a_line_above_the_cap_is_refused_and_never_buffered_further(pair: tuple[LineConnection, socket.socket]) -> None:
    connection, server = pair
    server.sendall(b"x" * 200)
    with pytest.raises(ProtocolError, match="too long"):
        connection.read_line()


def test_silence_hangup_and_no_connection_are_protocol_errors(pair: tuple[LineConnection, socket.socket]) -> None:
    connection, server = pair
    assert connection.fill_within(0.05) is False  # nothing came: no harm done
    assert connection.socket is not None and connection.socket.gettimeout() == 1.0
    server.close()
    with pytest.raises(ProtocolError, match="closed the connection"):
        connection.read_line()
    connection.close()
    with pytest.raises(ProtocolError, match="Not connected"):
        connection.send(b"NOOP\r\n")
    with pytest.raises(ProtocolError, match="Not connected"):
        connection.fill()
