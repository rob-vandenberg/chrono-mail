from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.undo import MAIL_REDO, MAIL_UNDO, MAIL_UNDO_LABELS, UndoLabels, UndoResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "undoing.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: Initial: Undo and Redo of the actions on mail: each action numbered, Undo waits for the actions under way, the menu says what it would do (B-18)


class Undoing(QObject):
    """Undo and Redo of the actions on mail (B-18). The engine keeps what can be undone and does it; this part gives every action
    of the person its number (one key press is one action, however many commands it takes), holds Undo and Redo until the actions
    under way are done (so Ctrl+Z right after Delete undoes that Delete), and says what Undo and Redo would do."""

    labels_changed = Signal()  # undo_label, redo_label or busy changed: the window shows them

    def __init__(self, backend: Backend, status: StatusReport, messages: MessageListPane, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._status: StatusReport = status
        self._messages: MessageListPane = messages
        self._number: int = 0
        self._running: int = 0  # commands of actions (and of Undo and Redo) not answered yet
        self._wanted: list[bool] = []  # Undo (False) and Redo (True) pressed while something ran, in order
        self.undo_label: str = ""  # "Move to Trash (3 messages)": what Undo would do; "" for nothing
        self.redo_label: str = ""

    @property
    def busy(self) -> bool:
        """An action is under way: Undo can be pressed already and waits for it."""
        return self._running > 0 or bool(self._wanted)

    def new_action(self) -> int:
        """The number of a new action of the person: the commands it takes are one step of Undo."""
        self._number += 1
        return self._number

    def started(self) -> None:
        """A command of an action was given."""
        self._running += 1
        if self._running == 1:
            self.labels_changed.emit()

    def ended(self) -> None:
        """A command of an action was answered (done or not)."""
        self._running = max(0, self._running - 1)
        if self._running == 0:
            if self._wanted:
                self._next()
            else:
                self.refresh()

    def undo(self) -> None:
        self._wanted.append(False)
        self._next()

    def redo(self) -> None:
        self._wanted.append(True)
        self._next()

    def _next(self) -> None:
        """Undo or Redo, once nothing runs: each sees the facts of what came before it."""
        if self._running or not self._wanted:
            return
        redo = self._wanted.pop(0)
        self.started()

        def done(result: UndoResult) -> None:
            if result.not_done:
                count = result.not_done + len(result.messages)
                self._status.show(f"{'Redone' if redo else 'Undone'}: {result.action}. {result.not_done:,} of {count:,} stayed as they are: {result.reason}")
            else:
                self._status.finished(f"{'Redone' if redo else 'Undone'}: {result.action}")
            self._messages.select_messages(result.messages)
            self.ended()

        def failed(error: ChronoMailError) -> None:
            self._status.show(str(error))  # "Nothing to undo": not an error of the mail
            self.ended()

        self._backend.ask(MAIL_REDO if redo else MAIL_UNDO, NoParameters(), done, failed, owner=self)

    def refresh(self) -> None:
        """Ask the engine what Undo and Redo would do now."""

        def answered(labels: UndoLabels) -> None:
            self.undo_label, self.redo_label = labels.undo, labels.redo
            self.labels_changed.emit()

        self._backend.ask(MAIL_UNDO_LABELS, NoParameters(), answered, lambda error: None, owner=self)
