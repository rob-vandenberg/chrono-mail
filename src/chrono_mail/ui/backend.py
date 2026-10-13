import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import CommandRegistry, Engine, build_registry

# --- Version ------------------------------------------------------------
__version__ = "backend.py 0.4.120"

# --- Version history ----------------------------------------------------
# v0.4.120: notice: what the engine wants the person to know (an IMAP change waits for the server)
# v0.0.32: commands that wait have a pool of their own
# v0.0.13: Initial UI backend

Success = Callable[[object], None]
Failure = Callable[[ChronoMailError], None]


@dataclass
class _Pending:
    on_success: Success
    on_error: Failure | None
    owner: QObject | None  # when this object is gone, the answer is dropped instead of delivered


class _Done(QObject):
    """Carries an answer from a worker thread to the GUI thread."""

    finished = Signal(int, object, object)  # request id, result, error


class _Task(QRunnable):
    def __init__(self, request: int, engine: Engine, registry: CommandRegistry, command: str, values: Mapping[str, object], done: _Done) -> None:
        super().__init__()
        self._request, self._engine, self._registry, self._command, self._values, self._done = request, engine, registry, command, values, done

    @override
    @Slot()
    def run(self) -> None:
        try:
            result = self._registry.run(self._engine, self._command, self._values)
        except ChronoMailError as error:
            self._done.finished.emit(self._request, None, error)
        except Exception as error:  # a bug must show up as an error message, never kill the worker silently
            self._done.finished.emit(self._request, None, ChronoMailError(f"Unexpected error: {type(error).__name__}: {error}"))
        else:
            self._done.finished.emit(self._request, result, None)


class Backend(QObject):
    """Runs engine commands on worker threads and calls back on the GUI thread.

    The interface contains no mail logic: everything it does goes through a command of the
    engine, the same commands the command line uses.
    """

    progress = Signal(str, int, object)  # what, done, total (None when unknown)
    busy_changed = Signal(int)  # number of commands running
    notice = Signal(str)  # something the person should know, from any thread

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.engine: Engine = engine
        self.registry: CommandRegistry = build_registry()
        self._pool: QThreadPool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._waiting_pool: QThreadPool = QThreadPool(self)  # for commands that wait for a long time (mail.wait): they never hold up the others
        self._waiting_pool.setMaxThreadCount(8)
        self._background: dict[int, _Pending] = {}
        self._done: _Done = _Done(self)
        self._done.finished.connect(self._deliver)
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        engine.progress = lambda what, done, total: self.progress.emit(what, done, total)
        engine.notice = self.notice.emit

    def call(
        self, command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False
    ) -> int:
        """Start a command. The callbacks run on the GUI thread; owner (if given) ends the interest in the answer.
        A command that only waits (waiting=True) has a pool of its own and does not count as busy."""
        request = next(self._ids)
        task = _Task(request, self.engine, self.registry, command, dict(values), self._done)
        if waiting:
            self._background[request] = _Pending(on_success, on_error, owner)
            self._waiting_pool.start(task)
            return request
        self._pending[request] = _Pending(on_success, on_error, owner)
        self.busy_changed.emit(len(self._pending))
        self._pool.start(task)
        return request

    def run_now(self, command: str, values: Mapping[str, object]) -> object:
        """Run a command in the calling thread (for the few places that cannot continue without the answer)."""
        return self.registry.run(self.engine, command, values)

    @Slot(int, object, object)
    def _deliver(self, request: int, result: object, error: object) -> None:
        pending = self._background.pop(request, None)
        if pending is None:
            pending = self._pending.pop(request, None)
            self.busy_changed.emit(len(self._pending))
        if pending is None:
            return
        if pending.owner is not None and not _alive(pending.owner):
            return
        if isinstance(error, ChronoMailError):
            if pending.on_error is not None:
                pending.on_error(error)
        else:
            pending.on_success(result)

    def wait_for_done(self, milliseconds: int = 10_000) -> bool:
        return self._pool.waitForDone(milliseconds) and self._waiting_pool.waitForDone(milliseconds)


def _alive(owner: QObject) -> bool:
    try:
        owner.objectName()
    except RuntimeError:  # the C++ object has been deleted
        return False
    return True
