import getpass
import json
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# --- Version ------------------------------------------------------------
__version__ = "singleinstance.py 4.4.507"

# --- Version history ----------------------------------------------------
# v4.4.507: a second start with a calendar file hands it to the running program (ask_to_open, file_requested); only an .ics file is taken (D-310 step 4)
# v0.5.144: Initial: a second start asks the running program to show its window

REQUEST: bytes = b"show\n"
OPEN_REQUEST: bytes = b"open "  # followed by the path as one line of JSON, so that no character of a path can end the line
ANSWER: bytes = b"ok\n"
ANSWER_WAIT_MS: int = 2000  # how long a second start waits for the running program to answer


def server_name() -> str:
    """The name the running program listens under: one per user (a local socket on Linux, a named pipe on Windows)."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    return "chrono-mail-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user")


def requested_file(line: bytes) -> str:
    """The calendar file a request line asks to open; "": it asks none. Whatever another program writes here, only an .ics file is taken."""
    if not line.startswith(OPEN_REQUEST):
        return ""
    try:
        path = json.loads(line[len(OPEN_REQUEST):])
    except ValueError:
        return ""
    return path if isinstance(path, str) and path.lower().endswith(".ics") else ""


class ShowRequests(QObject):
    """Listens for a second start of the program; each request emits show_requested or file_requested and is answered."""

    show_requested = Signal()
    file_requested = Signal(str)  # a second start was given this calendar file

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
        if not connection.canReadLine():
            return
        line = bytes(connection.readLine().data())
        path = requested_file(line)
        if line != REQUEST and not path:
            return
        connection.write(ANSWER)
        connection.flush()
        if path:
            self.file_requested.emit(path)
        else:
            self.show_requested.emit()


def ask_to_show(name: str | None = None, wait_ms: int = ANSWER_WAIT_MS) -> bool:
    """Ask the running program to show its window. True when it answered."""
    return _ask(REQUEST, name, wait_ms)


def ask_to_open(path: str, name: str | None = None, wait_ms: int = ANSWER_WAIT_MS) -> bool:
    """Hand a calendar file to the running program. True when it answered."""
    return _ask(OPEN_REQUEST + json.dumps(path).encode("ascii") + b"\n", name, wait_ms)


def _ask(request: bytes, name: str | None, wait_ms: int) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(wait_ms):
        return False
    socket.write(request)
    if not socket.waitForBytesWritten(wait_ms):
        return False
    answer = b""
    while len(answer) < len(ANSWER) and socket.waitForReadyRead(wait_ms):
        answer += bytes(socket.readAll().data())
    socket.disconnectFromServer()
    return answer == ANSWER
