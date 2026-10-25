import socket

from chrono_mail.core import ProtocolError

# --- Version ------------------------------------------------------------
__version__ = "lineconnection.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: R-16: the one copy of sending to a mail server and reading its lines (was in protocols/imap.py and protocols/pop3.py)

_RECEIVE_BYTES: int = 65536


def _failed(error: OSError) -> ProtocolError:
    return ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}")


class LineConnection:
    """The connection to a line-based mail server (IMAP, POP3): what was sent and what came back, with a cap on every line.

    The client sets `socket` once connected (again after STARTTLS); every network failure is a ProtocolError.
    """

    def __init__(self, maximum_line_bytes: int, read_timeout_seconds: float) -> None:
        self.socket: socket.socket | None = None
        self._maximum_line_bytes: int = maximum_line_bytes
        self._read_timeout_seconds: float = read_timeout_seconds
        self._buffer: bytearray = bytearray()

    def close(self) -> None:
        sock, self.socket = self.socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def waiting(self) -> bool:
        """Whether the server sent data that was not read yet."""
        return bool(self._buffer)

    def line_waiting(self) -> bool:
        """Whether a whole line came in that was not read yet."""
        return b"\n" in self._buffer

    def send(self, data: bytes) -> None:
        if self.socket is None:
            raise ProtocolError("Not connected")
        try:
            self.socket.sendall(data)
        except socket.timeout:
            raise ProtocolError("The server stopped responding (timeout)") from None
        except OSError as error:
            raise _failed(error) from None

    def fill(self) -> None:
        """Wait for more data, up to the read timeout."""
        if self.socket is None:
            raise ProtocolError("Not connected")
        try:
            chunk = self.socket.recv(_RECEIVE_BYTES)
        except socket.timeout:
            raise ProtocolError("The server stopped responding (timeout)") from None
        except OSError as error:
            raise _failed(error) from None
        if not chunk:
            raise ProtocolError("The server closed the connection unexpectedly")
        self._buffer += chunk

    def fill_within(self, seconds: float) -> bool:
        """Wait up to this long for more data. False when none came; the connection is not harmed by that."""
        if self.socket is None:
            raise ProtocolError("Not connected")
        self.socket.settimeout(seconds)
        try:
            chunk = self.socket.recv(_RECEIVE_BYTES)
        except socket.timeout:
            return False
        except OSError as error:
            raise _failed(error) from None
        finally:
            if self.socket is not None:
                self.socket.settimeout(self._read_timeout_seconds)
        if not chunk:
            raise ProtocolError("The server closed the connection unexpectedly")
        self._buffer += chunk
        return True

    def read_line(self) -> bytes:
        """One line without its line ending. A line above the cap is an error, never buffered further."""
        limit = self._maximum_line_bytes
        searched = 0
        while True:
            position = self._buffer.find(b"\n", searched)
            if position != -1:
                line = bytes(self._buffer[:position])
                del self._buffer[: position + 1]
                if len(line) > limit:
                    raise ProtocolError("The server sent a line that is too long")
                return line[:-1] if line.endswith(b"\r") else line
            if len(self._buffer) > limit:
                raise ProtocolError("The server sent a line that is too long")
            searched = len(self._buffer)
            self.fill()

    def read_exactly(self, count: int) -> bytes:
        """Exactly this many bytes (the caller has checked the count against its own cap)."""
        while len(self._buffer) < count:
            self.fill()
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data
