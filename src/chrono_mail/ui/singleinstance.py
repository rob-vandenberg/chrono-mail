import getpass
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# --- Version ------------------------------------------------------------
__version__ = "singleinstance.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: Initial: a second start asks the running program to show its window

REQUEST: bytes = b"show\n"
ANSWER: bytes = b"ok\n"
ANSWER_WAIT_MS: int = 2000  # how long a second start waits for the running program to answer


def server_name() -> str:
    """The name the running program listens under: one per user (a local socket on Linux, a named pipe on Windows)."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    return "chrono-mail-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user")


class ShowRequests(QObject):
    """Listens for a second start of the program; each request emits show_requested and is answered."""

    show_requested = Signal()

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # only this user may ask
        self._server.newConnection.connect(self._accept)
        self._name = name or server_name()

    def listen(self) -> bool:
        """Start listening. A socket file left by a program that crashed is removed first (Linux)."""
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def _accept(self) -> None:
        while (connection := self._server.nextPendingConnection()) is not None:
            connection.readyRead.connect(lambda connection=connection: self._read(connection))
            connection.disconnected.connect(connection.deleteLater)
            if connection.bytesAvailable():
                self._read(connection)

    def _read(self, connection: QLocalSocket) -> None:
        if connection.canReadLine() and bytes(connection.readLine().data()) == REQUEST:
            connection.write(ANSWER)
            connection.flush()
            self.show_requested.emit()


def ask_to_show(name: str | None = None, wait_ms: int = ANSWER_WAIT_MS) -> bool:
    """Ask the running program to show its window. True when it answered."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(wait_ms):
        return False
    socket.write(REQUEST)
    if not socket.waitForBytesWritten(wait_ms):
        return False
    answer = b""
    while len(answer) < len(ANSWER) and socket.waitForReadyRead(wait_ms):
        answer += bytes(socket.readAll().data())
    socket.disconnectFromServer()
    return answer == ANSWER
