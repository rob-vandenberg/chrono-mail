"""What Undo and Redo can do (B-18): the person's last actions on mail, each message's change from before to after. Only the
record; doing it is in undo.py. Kept with the engine for as long as the program runs."""

import threading
from dataclasses import dataclass, field, replace
from typing import Literal

from chrono_mail.core import MessageFlag

# --- Version ------------------------------------------------------------
__version__ = "undohistory.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: Initial: the steps of Undo and Redo for mail, the actions kept until the program quits (B-18)

MAXIMUM_ACTIONS: int = 100  # kept for Undo; the oldest go first


@dataclass(frozen=True, slots=True)
class Notes:
    """What the properties file says of a message besides its tags: when it comes back from Snoozed and where to, where it was archived from."""

    snoozed_until: int | None = None
    snoozed_from: str | None = None
    archived_from: str | None = None


@dataclass(frozen=True, slots=True)
class UndoStep:
    """One message's change by an action, from before to after. Doing the step makes it so; its reverse undoes it.
    kind: "change" (moved, marks, tags, notes, verdict), "copy" (a copy of original made), "uncopy" (that copy removed for good)."""

    account: str  # the account the action was given for (the mailbox it opens)
    message: str  # its store id, the same in every folder (a copy: the copy's)
    before: tuple[str, ...]  # the folder it was in (copy: the original's folder; uncopy: the copy's)
    after: tuple[str, ...]  # the folder it is in after (copy: the copy's folder; uncopy: the original's)
    kind: Literal["change", "copy", "uncopy"] = "change"
    flags_added: frozenset[MessageFlag] = frozenset()
    flags_removed: frozenset[MessageFlag] = frozenset()
    tags_added: tuple[str, ...] = ()
    tags_removed: tuple[str, ...] = ()
    notes: tuple[Notes, Notes] | None = None  # (before, after) when the action changed them
    verdict: tuple[str | None, str | None] | None = None  # the junk filter's verdict (before, after) when the action changed it
    original: str = ""  # copy and uncopy: the message it is a copy of

    def reversed(self) -> "UndoStep":
        """The step that undoes this one."""
        kind: Literal["change", "copy", "uncopy"] = "uncopy" if self.kind == "copy" else "copy" if self.kind == "uncopy" else "change"
        return replace(
            self, before=self.after, after=self.before, kind=kind, flags_added=self.flags_removed, flags_removed=self.flags_added,
            tags_added=self.tags_removed, tags_removed=self.tags_added, notes=None if self.notes is None else (self.notes[1], self.notes[0]),
            verdict=None if self.verdict is None else (self.verdict[1], self.verdict[0]),
        )


@dataclass(slots=True)
class UndoAction:
    """One action of the person (one key press), however many commands it took: its name and its steps, as done."""

    number: int  # the window's count of actions: their order
    name: str  # "Move to Trash"
    steps: list[UndoStep] = field(default_factory=list[UndoStep])

    def label(self) -> str:
        """"Move to Trash (3 messages)"."""
        count = len(self.steps)
        return f"{self.name} ({count:,} message{'' if count == 1 else 's'})"


@dataclass
class UndoHistory:
    """The actions that can be undone (oldest first) and those that can be redone (the last undone last). Thread-safe."""

    _done: list[UndoAction] = field(default_factory=list[UndoAction])
    _undone: list[UndoAction] = field(default_factory=list[UndoAction])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, number: int, name: str, steps: list[UndoStep]) -> None:
        """Steps of the action with this number (an action on several folders comes in several commands). A new action ends what can be redone."""
        if number <= 0 or not steps:
            return
        with self._lock:
            action = next((action for action in self._done if action.number == number), None)
            if action is None:
                action = UndoAction(number, name)
                self._done.append(action)
                self._done.sort(key=lambda kept: kept.number)
                self._undone.clear()
                del self._done[:-MAXIMUM_ACTIONS]
            action.steps.extend(steps)

    def take(self, redo: bool) -> UndoAction | None:
        """The action to undo (or redo) next, taken out of the list while it is done."""
        with self._lock:
            kept = self._undone if redo else self._done
            return kept.pop() if kept else None

    def put(self, action: UndoAction, redo: bool) -> None:
        """An action undone goes to Redo (redo=True), one redone back to Undo; without steps left it is gone."""
        if not action.steps:
            return
        with self._lock:
            (self._undone if redo else self._done).append(action)
            if not redo:
                del self._done[:-MAXIMUM_ACTIONS]

    def labels(self) -> tuple[str, str]:
        """What Undo and Redo would do now ("" for nothing)."""
        with self._lock:
            return (self._done[-1].label() if self._done else "", self._undone[-1].label() if self._undone else "")
