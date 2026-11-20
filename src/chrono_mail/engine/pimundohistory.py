"""What Undo and Redo can do in Calendar, Tasks and Contacts (B-18 step 3, D-368): the person's last actions on appointments, tasks and
contacts, each item's text before and after. Only the record; doing it is in pimundo.py. A command of the person's action runs inside
`recording`; every write of an ItemWriter in it is noted (pim_server.py), so whatever the action changed is known, however it was done."""
import functools
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Protocol

from chrono_mail.core import CollectionKind
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "pimundohistory.py 4.7.656"

# --- Version history ----------------------------------------------------
# v4.7.656: Initial: the record of Undo and Redo for appointments, tasks and contacts, one list per section (B-18 step 3, D-368)

MAXIMUM_PIM_ACTIONS: int = 100  # kept per section for Undo; the oldest go first


@dataclass(frozen=True, slots=True)
class PimSide:
    """An item on one side of an action: where it is and what it says. item "" (and text ""): it does not exist on this side."""
    collection: str
    item: str = ""
    text: str = ""
    google: str = ""  # an appointment of a Google calendar: the calendar tag that is its colour on Google (D-341), "" for none


@dataclass(frozen=True, slots=True)
class PimStep:
    """One item's change by an action, from before to after. Doing the step makes it so; its reverse undoes it."""
    account: str
    before: PimSide
    after: PimSide

    def reversed(self) -> "PimStep":
        return replace(self, before=self.after, after=self.before)


@dataclass(slots=True)
class PimAction:
    """One action of the person (one key press), however many commands and items it took."""
    number: int  # the window's count of actions: their order
    name: str  # "Delete"
    word: str  # what its items are called: "appointment", "task", "contact", "contact group"
    steps: list[PimStep] = field(default_factory=list[PimStep])

    def label(self) -> str:
        """"Delete (2 tasks)"."""
        return f"{self.name} ({counted(len(self.steps), self.word)})"


@dataclass
class PimUndoHistory:
    """The actions of one section that can be undone (oldest first) and those that can be redone (the last undone last). Thread-safe."""
    _done: list[PimAction] = field(default_factory=list[PimAction])
    _undone: list[PimAction] = field(default_factory=list[PimAction])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, number: int, name: str, word: str, steps: list[PimStep]) -> None:
        """Steps of the action with this number (one action can take several commands). A new action ends what can be redone."""
        if number <= 0 or not steps:
            return
        with self._lock:
            action = next((action for action in self._done if action.number == number), None)
            if action is None:
                action = PimAction(number, name, word)
                self._done.append(action)
                self._done.sort(key=lambda kept: kept.number)
                self._undone.clear()
                del self._done[:-MAXIMUM_PIM_ACTIONS]
            action.steps.extend(steps)

    def take(self, redo: bool) -> PimAction | None:
        """The action to undo (or redo) next, taken out of the list while it is done."""
        with self._lock:
            kept = self._undone if redo else self._done
            return kept.pop() if kept else None

    def put(self, action: PimAction, redo: bool) -> None:
        """An action undone goes to Redo (redo=True), one redone back to Undo; without steps left it is gone."""
        if not action.steps:
            return
        with self._lock:
            (self._undone if redo else self._done).append(action)
            if not redo:
                del self._done[:-MAXIMUM_PIM_ACTIONS]

    def labels(self) -> tuple[str, str]:
        """What Undo and Redo would do now ("" for nothing)."""
        with self._lock:
            return (self._done[-1].label() if self._done else "", self._undone[-1].label() if self._undone else "")


def pim_histories() -> dict[CollectionKind, PimUndoHistory]:
    """One history per section: Ctrl+Z in Calendar undoes the Calendar's last action (OR-16)."""
    return {kind: PimUndoHistory() for kind in CollectionKind}


# --- noting an action while it runs ----------------------------------------------------------------------------------------------------------------

@dataclass(slots=True)
class _Recording:
    number: int
    name: str
    word: str
    kind: CollectionKind | None = None
    steps: dict[tuple[str, str], PimStep] = field(default_factory=dict[tuple[str, str], PimStep])  # by the place the item has after it


_RECORDING: ContextVar[_Recording | None] = ContextVar("pim_recording", default=None)  # the action the command of this thread runs for


def noting() -> bool:
    """Whether a write now belongs to an action of the person that can be undone."""
    return _RECORDING.get() is not None


def note(account: str, kind: CollectionKind, before: PimSide, after: PimSide) -> None:
    """One write of the action: an item from before to after. Several writes of one item are one step, from its first before to its last after."""
    recording = _RECORDING.get()
    if recording is None:
        return
    recording.kind = kind
    earlier = recording.steps.pop((before.collection, before.item), None) if before.item else None
    first = earlier.before if earlier is not None else before
    key = (after.collection, after.item) if after.item else ("", f"gone {before.collection} {before.item} {len(recording.steps)}")
    recording.steps[key] = PimStep(account, first, after)


@contextmanager
def recording(histories: dict[CollectionKind, PimUndoHistory], number: int, name: str, word: str) -> Generator[None]:
    """The command runs one action of the person (number > 0): what it changes is kept for Undo, also what it did before it failed.
    Without a number nothing is noted: only what the person did is undone, never a synchronisation, an import or the command line."""
    if number <= 0:
        yield
        return
    kept = _Recording(number, name, word)
    token = _RECORDING.set(kept)
    try:
        yield
    finally:
        _RECORDING.reset(token)
        steps = [step for step in kept.steps.values() if step.before != step.after]
        if kept.kind is not None and steps:
            histories[kept.kind].record(number, name, word, steps)


UNDO_OPTION: str = "The number of the person's action, so Undo can put it back (0: it is not undone)"


class _Numbered(Protocol):
    @property
    def undo(self) -> int: ...


class _Keeping(Protocol):
    @property
    def pim_undo(self) -> dict[CollectionKind, PimUndoHistory]: ...


def undoable[E: _Keeping, P: _Numbered, R](handler: Callable[[E, P], R], name: str, word: str) -> Callable[[E, P], R]:
    """A command that is one action of the person when given a number: what it changes is kept for Undo (D-368)."""
    @functools.wraps(handler)
    def run(engine: E, parameters: P) -> R:
        with recording(engine.pim_undo, parameters.undo, name, word):
            return handler(engine, parameters)
    return run
