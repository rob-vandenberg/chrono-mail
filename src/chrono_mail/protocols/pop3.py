import socket
import ssl
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from chrono_mail.core import AuthenticationError, ConnectionSecurity, ProtocolError, ServerSettings

# --- Version ------------------------------------------------------------
__version__ = "pop3.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: Initial POP3 client

# The standard library's poplib refuses every line longer than 2,048 bytes, message bodies included,
# which real mail exceeds. This client has its own, generous and explicit, caps (D-027).


class Pop3Refused(ProtocolError):
    """The server answered a command with -ERR."""


@dataclass(frozen=True, slots=True)
class Pop3Limits:
    maximum_line_bytes: int = 1024 * 1024
    maximum_message_bytes: int = 256 * 1024 * 1024
    maximum_messages: int = 1_000_000
    connect_timeout_seconds: float = 20.0
    read_timeout_seconds: float = 60.0


def _printable(text: bytes, limit: int = 200) -> str:
    """Server text made safe to show: ASCII only, no control characters, cut short."""
    return "".join(chr(byte) if 32 <= byte < 127 else "?" for byte in text[:limit])


class Pop3Client:
    """A POP3 client over plain, STLS or implicit TLS. Certificates are always verified."""

    def __init__(
        self,
        settings: ServerSettings,
        password: str,
        limits: Pop3Limits = Pop3Limits(),
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._settings: ServerSettings = settings
        self._password: str = password
        self._limits: Pop3Limits = limits
        self._context: ssl.SSLContext = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._socket: socket.socket | None = None
        self._buffer: bytearray = bytearray()
        self.capabilities: frozenset[str] = frozenset()

    # --- connection ----------------------------------------------------------------------------

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self.close()  # without QUIT: deletions that were never committed are dropped by the server

    def connect(self) -> None:
        """Connect, secure the connection and log in."""
        settings = self._settings
        try:
            raw = socket.create_connection((settings.host, settings.port), timeout=self._limits.connect_timeout_seconds)
            raw.settimeout(self._limits.read_timeout_seconds)
            if settings.security is ConnectionSecurity.TLS:
                raw = self._context.wrap_socket(raw, server_hostname=settings.host)
            self._socket = raw
            self._expect_ok(self._read_line(), "greeting")
            self.capabilities = self._read_capabilities()
            if settings.security is ConnectionSecurity.STARTTLS:
                if "STLS" not in self.capabilities:
                    raise ProtocolError("The server does not offer STLS")
                self._command("STLS")
                if self._buffer:
                    raise ProtocolError("The server sent data before the secure connection was set up")
                self._socket = self._context.wrap_socket(raw, server_hostname=settings.host)
                self.capabilities = self._read_capabilities()
            self._login()
        except ProtocolError:
            self.close()
            raise
        except ssl.SSLCertVerificationError as error:
            self.close()
            raise ProtocolError(f"The server's certificate could not be verified: {error.verify_message}") from None
        except (OSError, ssl.SSLError) as error:
            self.close()
            raise ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}") from None

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _login(self) -> None:
        try:
            self._command(f"USER {self._settings.username}")
            self._command(f"PASS {self._password}")
        except Pop3Refused as error:
            raise AuthenticationError(f"The server refused the login: {error}") from None

    # --- low level -----------------------------------------------------------------------------

    def _send(self, line: str) -> None:
        if self._socket is None:
            raise ProtocolError("Not connected")
        if any(character in line for character in "\r\n\x00"):
            raise ProtocolError("A command may not contain a line break")  # no command injection from a user name or a password
        try:
            self._socket.sendall(line.encode("utf-8") + b"\r\n")
        except socket.timeout:
            raise ProtocolError("The server stopped responding (timeout)") from None
        except OSError as error:
            raise ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}") from None

    def _fill(self) -> None:
        if self._socket is None:
            raise ProtocolError("Not connected")
        try:
            chunk = self._socket.recv(65536)
        except socket.timeout:
            raise ProtocolError("The server stopped responding (timeout)") from None
        except OSError as error:
            raise ProtocolError(f"Connection failed: {error.strerror or type(error).__name__}") from None
        if not chunk:
            raise ProtocolError("The server closed the connection unexpectedly")
        self._buffer += chunk

    def _read_line(self) -> bytes:
        """One line without its line ending. A line above the cap is an error, never buffered further."""
        limit = self._limits.maximum_line_bytes
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
            self._fill()

    def _expect_ok(self, line: bytes, what: str) -> str:
        if line.startswith(b"+OK"):
            return _printable(line[3:]).strip()
        if line.startswith(b"-ERR"):
            raise Pop3Refused(f"The server refused {what}: {_printable(line[4:]).strip()}")
        raise ProtocolError(f"The server sent an invalid response to {what}")

    def _command(self, line: str) -> str:
        self._send(line)
        return self._expect_ok(self._read_line(), line.split(" ", 1)[0])

    def _read_multiline(self, maximum_bytes: int) -> list[bytes]:
        """The lines of a multi-line response, with dot-stuffing removed, up to the final '.'."""
        lines: list[bytes] = []
        total = 0
        while True:
            line = self._read_line()
            if line == b".":
                return lines
            if line.startswith(b".."):
                line = line[1:]
            total += len(line) + 2
            if total > maximum_bytes:
                raise ProtocolError("The server's response is larger than allowed")
            lines.append(line)

    def _read_capabilities(self) -> frozenset[str]:
        try:
            self._send("CAPA")
            self._expect_ok(self._read_line(), "CAPA")
            return frozenset(_printable(item).split(" ", 1)[0].upper() for item in self._read_multiline(64 * 1024))
        except Pop3Refused:
            return frozenset()

    # --- commands --------------------------------------------------------------------------------

    def _number(self, text: bytes, what: str, minimum: int = 0, maximum: int | None = None) -> int:
        if not text.isdigit() or len(text) > 12:
            raise ProtocolError(f"The server sent an invalid {what}")
        value = int(text)
        if value < minimum or (maximum is not None and value > maximum):
            raise ProtocolError(f"The server sent an out-of-range {what}")
        return value

    def stat(self) -> tuple[int, int]:
        """(number of messages, total size in bytes)."""
        self._send("STAT")
        line = self._read_line()
        self._expect_ok(line, "STAT")
        parts = line.split()
        if len(parts) < 3:
            raise ProtocolError("The server sent an invalid response to STAT")
        return self._number(parts[1], "message count", maximum=self._limits.maximum_messages), self._number(parts[2], "mailbox size")

    def list_sizes(self) -> dict[int, int]:
        """message number -> size in bytes."""
        self._command("LIST")
        sizes: dict[int, int] = {}
        for line in self._read_multiline(self._limits.maximum_messages * 40):
            parts = line.split()
            if len(parts) < 2:
                raise ProtocolError("The server sent an invalid LIST line")
            sizes[self._number(parts[0], "message number", 1, self._limits.maximum_messages)] = self._number(parts[1], "message size")
            if len(sizes) > self._limits.maximum_messages:
                raise ProtocolError("The server lists too many messages")
        return sizes

    def uidl(self) -> list[tuple[int, str]]:
        """(message number, unique id) for every message, in the server's order."""
        self._command("UIDL")
        found: list[tuple[int, str]] = []
        for line in self._read_multiline(self._limits.maximum_messages * 100):
            parts = line.split()
            if len(parts) != 2 or not 1 <= len(parts[1]) <= 70 or any(not 33 <= byte <= 126 for byte in parts[1]):
                raise ProtocolError("The server sent an invalid UIDL line")
            found.append((self._number(parts[0], "message number", 1, self._limits.maximum_messages), parts[1].decode("ascii")))
            if len(found) > self._limits.maximum_messages:
                raise ProtocolError("The server lists too many messages")
        return found

    def retr(self, number: int) -> bytes:
        """The complete raw message. A message larger than the limit is an error, not an allocation."""
        self._command(f"RETR {number}")
        lines = self._read_multiline(self._limits.maximum_message_bytes)
        return b"\r\n".join(lines) + (b"\r\n" if lines else b"")

    def top(self, number: int, body_lines: int = 0) -> bytes:
        self._command(f"TOP {number} {body_lines}")
        lines = self._read_multiline(self._limits.maximum_message_bytes)
        return b"\r\n".join(lines) + (b"\r\n" if lines else b"")

    def dele(self, number: int) -> None:
        """Mark a message for deletion. The server removes it when quit() succeeds."""
        self._command(f"DELE {number}")

    def noop(self) -> None:
        self._command("NOOP")

    def reset(self) -> None:
        self._command("RSET")

    def quit(self) -> None:
        """End the session; the server now removes the messages marked with dele()."""
        try:
            self._command("QUIT")
        finally:
            self.close()
