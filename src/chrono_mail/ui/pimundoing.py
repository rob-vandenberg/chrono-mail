"""Undo and Redo in Calendar, Tasks and Contacts (B-18 step 3, D-368): one per section, so Ctrl+Z acts on the section that is shown (OR-16).
The engine keeps what can be undone and does it (pim.undo, pim.redo); this part gives every action of the person its number (one key
press is one action, however many commands it takes), holds Undo and Redo until the actions under way in its section are answered (so
Ctrl+Z right after Delete undoes that Delete), and says what Undo and Redo would do."""
import itertools
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.engine.pimundo import PIM_REDO, PIM_UNDO, PIM_UNDO_LABELS, PimUndoLabels, PimUndoResult, SectionParameters
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "pimundoing.py 4.7.656"

# --- Version history ----------------------------------------------------
# v4.7.656: Initial: Undo and Redo of the actions in Calendar, Tasks and Contacts, each section its own (B-18 step 3, D-368)

_NUMBERS = itertools.count(1)  # one count for every section: an answer from Calendar can change a task list, and never joins a task's action


class SectionUndo(QObject):
    """Undo and Redo of one section ("events", "tasks" or "contacts")."""

    labels_changed = Signal()  # undo_label, redo_label or busy changed: the window shows them

    def __init__(self, backend: Backend, section: str, say: Callable[[str], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._section: str = section
        self._say: Callable[[str], None] = say
        self._running: int = 0  # commands of actions (and of Undo and Redo) not answered yet
        self._wanted: list[bool] = []  # Undo (False) and Redo (True) waiting for the commands under way, in order
        self.undo_label: str = ""  # "Delete (2 tasks)": what Undo would do; "" for nothing
        self.redo_label: str = ""
        self.items_back: Callable[[tuple[str, ...]], None] = lambda items: None  # the page selects what an Undo or Redo brought back
        self.is_repeat: Callable[[], bool] = lambda: False  # whether the key press acting now is a held key's repeat (the window tells)

    @property
    def busy(self) -> bool:
        """An action is under way: Undo can be pressed already and waits for it."""
        return self._running > 0 or bool(self._wanted)

    def new_action(self) -> int:
        """The number of a new action of the person: the commands it takes are one step of Undo."""
        return next(_NUMBERS)

    def track[R](self, done: Callable[[R], None], failed: Callable[[ChronoMailError], None]) -> tuple[Callable[[R], None], Callable[[ChronoMailError], None]]:
        """A command of an action is given: an Undo pressed now waits until it is answered, done or not."""
        self._running += 1
        if self._running == 1:
            self.labels_changed.emit()

        def answered(result: R) -> None:
            try:
                done(result)
            finally:
                self._ended()

        def refused(error: ChronoMailError) -> None:
            try:
                failed(error)
            finally:
                self._ended()

        return answered, refused

    def ask[P, R](self, command: TypedCommand[P, R], parameters: P, done: Callable[[R], None], failed: Callable[[ChronoMailError], None], owner: QObject) -> None:
        """Give a command of an action (its parameters carry the action's number): an Undo pressed now waits for its answer."""
        answered, refused = self.track(done, failed)
        self._backend.ask(command, parameters, answered, refused, owner=owner)

    def _ended(self) -> None:
        self._running = max(0, self._running - 1)
        if self._running == 0:
            if self._wanted:
                self._next()
            else:
                self.refresh()

    def undo(self) -> None:
        self._press(False)

    def redo(self) -> None:
        self._press(True)

    def _press(self, redo: bool) -> None:
        """In turn, behind the commands under way. A held key's repeat while an action is under way is thrown away: the undoing stops the
        moment the key is let go (D-266)."""
        if self.is_repeat() and self.busy:
            return
        self._wanted.append(redo)
        self._next()

    def _next(self) -> None:
        if self._running or not self._wanted:
            return
        redo = self._wanted.pop(0)

        def done(result: PimUndoResult) -> None:
            if result.not_done:
                count = result.not_done + len(result.items)
                self._say(f"{'Redone' if redo else 'Undone'}: {result.action}. {result.not_done:,} of {count:,} stayed as they are: {result.reason}")
            else:
                self._say(f"{'Redone' if redo else 'Undone'}: {result.action}")
            self.items_back(result.items)

        def failed(error: ChronoMailError) -> None:
            self._say(str(error))  # "Nothing to undo"

        answered, refused = self.track(done, failed)  # the Undo itself runs as an action: what is pressed after it waits for its answer
        self._backend.ask(PIM_REDO if redo else PIM_UNDO, SectionParameters(self._section), answered, refused, owner=self)

    def refresh(self) -> None:
        """Ask the engine what Undo and Redo would do now in this section."""
        def answered(labels: PimUndoLabels) -> None:
            self.undo_label, self.redo_label = labels.undo, labels.redo
            self.labels_changed.emit()

        self._backend.ask(PIM_UNDO_LABELS, SectionParameters(self._section), answered, lambda error: None, owner=self)
