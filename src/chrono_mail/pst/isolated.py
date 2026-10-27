import multiprocessing
import os
import sys
import time
from collections.abc import Callable, Iterator
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Protocol

from chrono_mail.pst.model import PstContact, PstError, PstEvent, PstFailure, PstGroup, PstItem, PstMail, PstStore, PstTask
from chrono_mail.pst.reading import CONTACTS_PARTIAL, Progress, read_items, read_store

# --- Version ------------------------------------------------------------
__version__ = "isolated.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the data file read in a child process, so a crash of the reading library costs one item, never the program

ITEM_SECONDS: float = 1800.0  # the longest one item may take, signs of life or not
SILENCE_SECONDS: float = 120.0  # a child that sends nothing this long on one item has hung: it is stopped and the item counts as failed
STORE_SECONDS: float = 600.0  # opening the file and reading its folders (also the wait for a child's first word)
MAXIMUM_SILENT_DEATHS: int = 2  # children in a row that died before they reached an item: the reading stops, to be tried again later

type ItemReader = Callable[[Path, int, Progress | None], Iterator[tuple[int, PstItem]]]
type StoreReader = Callable[[Path], PstStore]

DAMAGED = "could not be read: the data file is damaged there"
_ITEM_TYPES = (PstMail, PstEvent, PstTask, PstContact, PstGroup, PstFailure)
_ONCE = frozenset({CONTACTS_PARTIAL})


class _Sending(Protocol):
    """A pipe's sending end (Connection on Linux, PipeConnection on Windows)."""

    def send(self, obj: object) -> None: ...

    def close(self) -> None: ...


class _Receiving(Protocol):
    def poll(self, timeout: float | None = ...) -> bool: ...

    def recv(self) -> object: ...

    def close(self) -> None: ...


class _ChildGone(Exception):
    """The child died, hung or sent something it should not have."""


def _quiet() -> None:
    """A child never writes to the terminal or a log: an uncaught error there could carry what a message holds (AGENTS.md 4.6.5)."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 2)
        os.dup2(null, 1)
        os.close(null)
    except OSError:
        pass
    try:
        sys.stderr = sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - open for the whole life of the child
    except OSError:
        pass


class _Reporter:
    def __init__(self, connection: _Sending) -> None:
        self.connection = connection

    def total(self, count: int) -> None:
        self.connection.send(("total", count))

    def at(self, number: int, folder: tuple[str, ...]) -> None:
        self.connection.send(("at", number, folder))

    def alive(self) -> None:
        self.connection.send(("alive",))


def _store_child(connection: _Sending, path: Path, reader: StoreReader) -> None:
    _quiet()
    try:
        connection.send(("store", reader(path)))
    except PstError as error:
        connection.send(("error", str(error)))
    except Exception as error:  # noqa: BLE001 - the kind of fault only, never its text: it could carry what the file holds
        connection.send(("error", f"{path.name} cannot be read ({type(error).__name__})"))
    finally:
        connection.close()


def _items_child(connection: _Sending, path: Path, start: int, reader: ItemReader) -> None:
    _quiet()
    try:
        for number, item in reader(path, start, _Reporter(connection)):
            connection.send(("item", number, item))
        connection.send(("done",))
    except PstError as error:
        connection.send(("error", str(error)))
    except Exception as error:  # noqa: BLE001 - the kind of fault only; the parent counts it as the child gone on the item it was on
        connection.send(("crash", type(error).__name__))
    finally:
        connection.close()


def _receive(connection: _Receiving, seconds: float) -> tuple[object, ...]:
    """The next message of the child. A pipe (not a queue) carries them: what a child wrote before it died can still be read."""
    if not connection.poll(seconds):
        raise _ChildGone("silent")
    try:
        message: object = connection.recv()
    except Exception as error:  # noqa: BLE001 - died (EOFError), or sent something that cannot be read back: either way it is gone
        raise _ChildGone("died") from error
    if not isinstance(message, tuple) or not message:
        raise _ChildGone("garbled")
    return tuple(message)


def _stop(process: BaseProcess) -> None:
    if process.is_alive():
        process.kill()
    process.join(10)


def _error_text(message: tuple[object, ...], path: Path) -> str:
    return str(message[1]) if len(message) == 2 else f"{path.name} cannot be read"


def store_in_child(path: Path, reader: StoreReader = read_store, seconds: float = STORE_SECONDS) -> PstStore:
    """The data file's name and folders, read in a child process; PstError with a plain-English reason when it cannot be read."""
    context = multiprocessing.get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(target=_store_child, args=(sending, path, reader), daemon=True)
    process.start()
    sending.close()
    try:
        message = _receive(receiving, seconds)
    except _ChildGone:
        raise PstError(f"{path.name} cannot be read: it is damaged or not a data file of Outlook") from None
    finally:
        receiving.close()
        _stop(process)
    if message[0] == "store" and len(message) == 2 and isinstance(message[1], PstStore):
        return message[1]
    raise PstError(_error_text(message, path))


def items_in_child(
    path: Path, start: int = 0, reader: ItemReader = read_items, seconds: float = SILENCE_SECONDS, opening_seconds: float = STORE_SECONDS,
    item_seconds: float = ITEM_SECONDS,
) -> Iterator[tuple[int, PstItem]]:
    """Every item to import, numbered as read_items numbers them (from start on), read in a child process. When the child dies or hangs
    on an item, that item is a failure and a new child goes on after it. PstError when the file cannot be opened, and when children die
    twice in a row before they reach an item: the reading is then not finished, and a later import goes on from the next number."""
    context = multiprocessing.get_context("spawn")
    next_number = start
    total: int | None = None
    silent_deaths = 0
    said: set[str] = set()  # failures about the whole file, said once even when a new child says them again
    while total is None or next_number < total:
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(target=_items_child, args=(sending, path, next_number, reader), daemon=True)
        process.start()
        sending.close()
        current: tuple[int, tuple[str, ...]] | None = None
        item_deadline = 0.0
        reached = False
        try:
            while True:
                wait = seconds if reached else opening_seconds  # a child first opens the file and its folders
                if current is not None:
                    wait = min(wait, item_deadline - time.monotonic())  # signs of life never stretch one item past its limit
                message = _receive(receiving, max(0.0, wait))
                kind = message[0]
                if kind == "alive":
                    continue  # long work on one item goes on: the wait starts again
                if kind == "total" and len(message) == 2 and isinstance(message[1], int):
                    total = message[1]
                elif kind == "at" and len(message) == 3 and isinstance(message[1], int) and isinstance(message[2], tuple):
                    current = (message[1], tuple(str(part) for part in message[2]))
                    item_deadline = time.monotonic() + item_seconds
                    reached = True
                elif kind == "item" and len(message) == 3 and isinstance(message[1], int) and isinstance(message[2], _ITEM_TYPES):
                    item = message[2]
                    if not (isinstance(item, PstFailure) and item.what in _ONCE and item.what in said):
                        if isinstance(item, PstFailure) and item.what in _ONCE:
                            said.add(item.what)
                        yield message[1], item
                    next_number = max(next_number, message[1] + 1)
                    current = None
                    reached = True
                elif kind == "done":
                    return
                elif kind == "error":
                    raise PstError(_error_text(message, path))
                else:
                    raise _ChildGone("crashed or garbled")
        except _ChildGone:
            if current is not None:
                number, folder = current
                yield number, PstFailure(folder, f"an item that {DAMAGED}")
                next_number = number + 1
                silent_deaths = 0
            elif total is not None and next_number >= total:
                return  # it died while closing the file, after its last item
            else:
                silent_deaths = 0 if reached else silent_deaths + 1
                if silent_deaths >= MAXIMUM_SILENT_DEATHS:
                    raise PstError(f"{path.name} could not be read from item {next_number + 1} on; a later import tries again") from None
        finally:
            receiving.close()
            _stop(process)
