import dataclasses
import itertools
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast, override

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, Signal, Slot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import CommandRegistry, Engine, TypedCommand, build_registry

# --- Version ------------------------------------------------------------
__version__ = "backend.py 2.0.295"

# --- Version history ----------------------------------------------------
# v2.0.295: A job answers exactly once however it ends (also an error that is no Exception), so a window never waits for ever
# v2.0.294: Every job started is held here until its answer is delivered: Qt's pool only borrows it, so nothing can throw it away before
#           it runs (a contact's save stayed on "Saving…" for ever in about 1 test run in 4 on the Windows computer)
# v2.0.281: rebuilding: the engine's reports of an index being rebuilt or filled in, from any thread (D-229)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: carried (the fields of one parameter object into another kind) lives here, for every window
# v1.9.270: ask takes waiting and checking too; Step: a typed call worked out now and started later (a list of steps of different commands) (R-19)
# v1.1.247: ask takes in_order too; a command for many messages (no single message) waits for the ones given earlier for the same account (R-08)
# v1.1.205: ask: a typed command with its parameter object, the answer given as its own type (R-02, R-19)
# v0.5.155: changed: the facts of every change of mail, on the GUI thread in the order each worker reported them (D-146)
# v0.5.145: activity: the steps of a mail check or a send per account, from any thread (for the Operations window)
# v0.5.133: checking: mail checks have places of their own, so the person's actions never wait behind them
# v0.5.126: in_order: the commands that change the same message run one after the other, in the order they were given
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
        except BaseException as error:  # noqa: BLE001 - however a job ends, it answers exactly once (the window never waits for ever)
            self._done.finished.emit(self._request, None, ChronoMailError(f"The command ended without an answer: {type(error).__name__}"))
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
    activity = Signal(str, str, str, object, object)  # account id, "receive" or "send", step, done, total (None when unknown)
    changed = Signal(object)  # a Change of the engine: one fact of a change of mail (D-146)
    rebuilding = Signal(object)  # a RebuildStep of the engine: how far an index is rebuilt or filled in (D-229)

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.engine: Engine = engine
        self.registry: CommandRegistry = build_registry()
        self._pool: QThreadPool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._waiting_pool: QThreadPool = QThreadPool(self)  # for commands that wait for a long time (mail.wait): they never hold up the others
        self._waiting_pool.setMaxThreadCount(8)
        self._checking_pool: QThreadPool = QThreadPool(self)  # mail checks (mail.fetch): many accounts at once never hold up the person's actions
        self._checking_pool.setMaxThreadCount(4)
        self._background: dict[int, _Pending] = {}
        self._done: _Done = _Done(self)
        self._done.finished.connect(self._deliver)
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._queued: dict[str, list[_Task]] = {}  # per message changed: the commands waiting for the one that runs
        self._queue_of: dict[int, str] = {}  # request -> its message
        self._jobs: dict[int, _Task] = {}  # every job started, held until its answer is delivered (the pools only borrow it)
        engine.progress = lambda what, done, total: self.progress.emit(what, done, total)
        engine.notice = self.notice.emit
        engine.activity = lambda account_id, direction, step, done, total: self.activity.emit(account_id, direction, step, done, total)
        engine.changed = self.changed.emit
        engine.rebuilding = self.rebuilding.emit

    def call(
        self, command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        """Start a command. The callbacks run on the GUI thread; owner (if given) ends the interest in the answer.
        A command that only waits (waiting=True) has a pool of its own and does not count as busy. Commands that change a
        message (in_order=True) wait for the ones given earlier for the same message, so the last one given is what stays (a command
        for many messages, which names no single one, waits for the earlier ones of its account that name none either);
        commands for different messages still run at the same time. A mail check (checking=True) runs in a lane of its own:
        it counts as busy, but the person's actions do not wait behind it."""
        request = next(self._ids)
        task = _Task(request, self.engine, self.registry, command, dict(values), self._done)
        self._jobs[request] = task
        if waiting:
            self._background[request] = _Pending(on_success, on_error, owner)
            self._waiting_pool.start(task)
            return request
        self._pending[request] = _Pending(on_success, on_error, owner)
        self.busy_changed.emit(len(self._pending))
        if checking:
            self._checking_pool.start(task)
            return request
        if in_order:
            key = f"{values.get('account', '')}\t{values.get('message', '')}"
            self._queue_of[request] = key
            if key in self._queued:
                self._queued[key].append(task)  # started when the one before it is done
                return request
            self._queued[key] = []
        self._pool.start(task)
        return request

    def ask[P, R](
        self, command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        """Start a typed command: the parameters as their dataclass, the answer as its own type (the type checker checks both).
        An answer of another type is a fault of the program and goes to on_error."""

        def answered(result: object) -> None:
            try:
                answer = command.answer(result)
            except ChronoMailError as error:
                if on_error is not None:
                    on_error(error)
                return
            on_success(answer)

        return self.call(command.id, command.values(parameters), answered, on_error, owner, waiting=waiting, in_order=in_order, checking=checking)

    def run_now(self, command: str, values: Mapping[str, object]) -> object:
        """Run a command in the calling thread (for the few places that cannot continue without the answer)."""
        return self.registry.run(self.engine, command, values)

    @Slot(int, object, object)
    def _deliver(self, request: int, result: object, error: object) -> None:
        self._jobs.pop(request, None)  # answered: the job may go now
        key = self._queue_of.pop(request, None)
        if key is not None:
            waiting = self._queued.get(key, [])
            if waiting:
                self._pool.start(waiting.pop(0))  # the next change of the same message
            else:
                self._queued.pop(key, None)
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
        """Wait for the running commands, and for the changes still waiting their turn (their start comes with the answer
        of the one before, so the answers are delivered meanwhile)."""
        deadline = time.monotonic() + milliseconds / 1000
        while True:
            left = max(0, int((deadline - time.monotonic()) * 1000))
            finished = self._pool.waitForDone(left) and self._checking_pool.waitForDone(max(0, int((deadline - time.monotonic()) * 1000)))
            QCoreApplication.processEvents()  # delivers the answers, which start what waits for them
            if not self._queued or not finished or time.monotonic() >= deadline:
                break
        return finished and not self._queued and self._waiting_pool.waitForDone(max(0, int((deadline - time.monotonic()) * 1000)))


def _alive(owner: QObject) -> bool:
    try:
        owner.objectName()
    except RuntimeError:  # the C++ object has been deleted
        return False
    return True


class Step[P, R]:
    """A typed call worked out now and started later: the steps of a list may be different commands with one kind of answer
    (Settings > Sidebar's changes, each answered by the new sidebar)."""

    def __init__(self, command: TypedCommand[P, R], parameters: P) -> None:
        self.command: TypedCommand[P, R] = command
        self.parameters: P = parameters

    def start(self, backend: Backend, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None) -> int:
        return backend.ask(self.command, self.parameters, on_success, on_error, owner)


class AnyStep[R](Protocol):
    """A Step whatever its parameters: what a list of steps with one kind of answer holds."""

    def start(self, backend: Backend, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None) -> int: ...


def carried[T](source: object, kind: type[T], **more: object) -> T:
    """The parameters of kind with every field of source that kind has too, plus more (R-19): the order of a view and the places
    in it share what the view holds; what an editor holds becomes the add or the edit of its item."""
    wanted = {kind_field.name for kind_field in dataclasses.fields(cast(type, kind))}
    values = {source_field.name: getattr(source, source_field.name) for source_field in dataclasses.fields(cast(type, type(source))) if source_field.name in wanted and source_field.name not in more}  # getattr: every field of a parameter dataclass, by its name
    return kind(**values, **more)
