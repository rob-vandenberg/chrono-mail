import socket
import ssl
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Self

# --- Version ------------------------------------------------------------
__version__ = "fakeserver.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.10: Initial scripted test server


class Conversation:
    """One client connection, as seen by a scripted server."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection: socket.socket = connection
        self._buffer: bytearray = bytearray()
        self.connection.settimeout(10)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # many small writes must not wait for an ACK (Nagle)

    def send(self, data: bytes) -> None:
        self.connection.sendall(data)

    def send_line(self, text: str) -> None:
        self.connection.sendall(text.encode("utf-8") + b"\r\n")

    def read_line(self) -> bytes | None:
        """The next line from the client without its line ending, or None when the client is gone."""
        while b"\n" not in self._buffer:
            try:
                chunk = self.connection.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self._buffer += chunk
        position = self._buffer.index(b"\n")
        line = bytes(self._buffer[:position])
        del self._buffer[: position + 1]
        return line.rstrip(b"\r")

    def start_tls(self, context: ssl.SSLContext) -> None:
        self.connection = context.wrap_socket(self.connection, server_side=True)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        try:
            self.connection.close()
        except OSError:
            pass


class ScriptedServer:
    """A TCP server on 127.0.0.1 that runs a script for every connection. Used as a context manager."""

    def __init__(self, script: Callable[[Conversation], None], implicit_tls: ssl.SSLContext | None = None) -> None:
        self._script: Callable[[Conversation], None] = script
        self._implicit_tls: ssl.SSLContext | None = implicit_tls
        self._listener: socket.socket = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port: int = self._listener.getsockname()[1]
        self._running: bool = True
        self._threads: list[threading.Thread] = []
        self.connections: int = 0

    def __enter__(self) -> Self:
        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()
        self._threads.append(thread)
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None) -> None:
        self._running = False
        try:
            self._listener.shutdown(socket.SHUT_RDWR)  # wakes the thread blocked in accept(); close() alone does not
        except OSError:
            pass
        try:
            self._listener.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while self._running:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            self.connections += 1
            thread = threading.Thread(target=self._serve, args=(connection,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _serve(self, connection: socket.socket) -> None:
        try:
            if self._implicit_tls is not None:
                connection = self._implicit_tls.wrap_socket(connection, server_side=True)
            conversation = Conversation(connection)
        except (OSError, ssl.SSLError):
            connection.close()
            return
        try:
            self._script(conversation)
        except (OSError, ssl.SSLError):
            pass  # the client went away: normal in tests of hostile behaviour
        finally:
            conversation.close()
