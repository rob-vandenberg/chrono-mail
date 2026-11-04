import socket
import threading
import weakref

# --- Version ------------------------------------------------------------
__version__ = "hangup.py 4.4.542"

# --- Version history ----------------------------------------------------
# v4.4.542: Initial: a connection to a server can be hung up from another thread, so nothing that waits for a server holds the program's end up

_lock: threading.Lock = threading.Lock()
_held: weakref.WeakSet["Held"] = weakref.WeakSet()  # weak: a client dropped without closing still lets its connection go
_hung_up: bool = False  # the program ends: no new connection


def _refuse_when_hung_up() -> None:
    if _hung_up:
        raise ConnectionAbortedError("Chrono Mail is closing")


class Held:
    """A second handle to the socket of one connection, for hanging it up. It stays good when TLS takes the socket over (that empties the
    socket object itself), and it keeps the connection open: release it when the connection closes."""

    def __init__(self, handle: socket.socket) -> None:
        self._handle: socket.socket = handle
        try:
            with _lock:
                _refuse_when_hung_up()
                _held.add(self)
        except OSError:
            handle.close()
            raise

    def release(self) -> None:
        with _lock:
            _held.discard(self)
        self._handle.close()

    def hang_up(self) -> None:
        try:
            self._handle.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # not connected (yet), or gone already


def connect(address: tuple[str, int], timeout: float | None, source: tuple[str, int] | None = None) -> tuple[socket.socket, Held]:
    """As socket.create_connection, with the handle that hangs the connection up, also while it is being made."""
    failure: OSError | None = None
    for family, kind, protocol, _, target in socket.getaddrinfo(address[0], address[1], 0, socket.SOCK_STREAM):
        sock = socket.socket(family, kind, protocol)
        try:
            held = Held(sock.dup())
        except OSError:
            sock.close()
            raise
        try:
            sock.settimeout(timeout)
            if source is not None:
                sock.bind(source)
            sock.connect(target)
            _refuse_when_hung_up()  # a hang-up before the connection was made did not reach it
        except OSError as error:
            held.release()
            sock.close()
            failure = error
            continue
        return sock, held
    raise failure if failure is not None else OSError(f"No address was found for {address[0]}")


def hang_up_all() -> None:
    """The program ends: every connection made here is hung up, so what waits for its server gets a lost connection at once, and no new one
    is made. Sending does not come through here: a mail being sent is never cut off, or it could go twice."""
    global _hung_up
    with _lock:
        _hung_up = True
        held = list(_held)
    for connection in held:
        connection.hang_up()


def allow_connections() -> None:
    """For the next program in this process: only the tests have one."""
    global _hung_up
    with _lock:
        _hung_up = False
