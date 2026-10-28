from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.undo import MAIL_REDO, MAIL_UNDO, MAIL_UNDO_LABELS, UndoLabels, UndoResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "undoing.py 4.2.396"

# --- Version history ----------------------------------------------------
# v4.2.396: Undo brings back the messages a Mark as read took out of Unread, Redo takes them out again (D-282)
# v4.2.394: a held Ctrl+Z (Ctrl+Y) stops the moment it is let go: its repeats that come while an action is under way are thrown away;
#           a separate press always counts (held_while_busy, also for the marks and tags, D-266)
# v4.1.383: the messages an Undo or Redo brings back are placed in one step when it ends, not a few at a time while it runs; what waits
#           behind it goes on once they have their places (D-266)
# v4.1.382: an Undo or Redo without an answer after a second says "Preparing to undo…" until the engine counts its messages (D-266)
# v3.6.350: Undo waits in turn in the message list's queue, right after the action it is meant for (RV-206): it goes with that action
#           when the action is not done or gives no command; what comes after it waits until it is answered, and is let go however the
#           answer ends
# v2.2.313: Initial: Undo and Redo of the actions on mail: each action numbered, Undo waits for the actions under way, the menu says what it would do (B-18)

PREPARING_AFTER_MS: int = 1000  # an Undo not answered by then says it is under way (a shorter one shows only its last line, D-266)


class Undoing(QObject):
    """Undo and Redo of the actions on mail (B-18). The engine keeps what can be undone and does it; this part gives every action
    of the person its number (one key press is one action, however many commands it takes), holds Undo and Redo until the actions
    under way are done (so Ctrl+Z right after Delete undoes that Delete), and says what Undo and Redo would do. While actions wait their
    turn in the message list (a large view's order on its way, rows being fetched), a press waits in the same queue, right after the
    action it is meant for, and goes with it when that action gives no command."""

    labels_changed = Signal()  # undo_label, redo_label or busy changed: the window shows them

    def __init__(self, backend: Backend, status: StatusReport, messages: MessageListPane, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._status: StatusReport = status
        self._messages: MessageListPane = messages
        self._number: int = 0
        self._running: int = 0  # commands of actions (and of Undo and Redo) not answered yet
        self._wanted: list[tuple[bool, Callable[[], None]]] = []  # Undo (False) and Redo (True) waiting for the commands under way, in
        # order, each with what lets the list's queue go on once it is answered
        messages.waiting_ended.connect(self._waiting_ended)
        messages.waiting_started.connect(self.labels_changed)  # Undo can be pressed already: it waits in turn
        messages.actions = lambda: self._number  # whether an action that waited gave a command
        self.undo_label: str = ""  # "Move to Trash (3 messages)": what Undo would do; "" for nothing
        self.redo_label: str = ""
        self._preparing = QTimer(self)  # "Preparing to undo…" once an Undo has run a second without a count from the engine
        self._preparing.setSingleShot(True)
        self._preparing.timeout.connect(self._say_preparing)
        self._preparing_redo: bool = False
        backend.progress.connect(self._counting)
        self.is_repeat: Callable[[], bool] = lambda: False  # whether the action acting now was set off by a held key's repeat (the window tells, once)

    @property
    def busy(self) -> bool:
        """An action is under way: Undo can be pressed already and waits for it."""
        return self._running > 0 or bool(self._wanted) or self._messages.waiting

    def held_while_busy(self) -> bool:
        """The key press being handled is the automatic repeat of a held key while an action is still under way: it is thrown away, so
        a held key stops the moment it is let go; a separate press always counts (D-266)."""
        return self.is_repeat() and self.busy

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

    def _waiting_ended(self) -> None:
        if self._wanted:
            self._next()
        else:
            self.refresh()

    def undo(self) -> None:
        self._press(False)

    def redo(self) -> None:
        self._press(True)

    def _press(self, redo: bool) -> None:
        """In turn: behind what waits in the message list (right after the action it is meant for), else behind the commands under way;
        either way an action made after it waits until it is answered (it never undoes an action made after the press). A held key's
        repeat while an action is under way is thrown away: the undoing stops the moment the key is let go (D-266)."""
        if self.held_while_busy():
            return
        if self._messages.queued:
            self._messages.after_waiting(lambda: self._take(redo, self._messages.hold()), undo=True)
            self.labels_changed.emit()
            return
        self._take(redo, self._messages.hold())

    def _take(self, redo: bool, release: Callable[[], None]) -> None:
        self._wanted.append((redo, release))
        self._next()

    def _next(self) -> None:
        """Undo or Redo, once the commands before it are answered: each sees the facts of what came before it."""
        if self._running or not self._wanted:
            return
        redo, release = self._wanted.pop(0)
        self.started()
        self._preparing_redo = redo
        self._preparing.start(PREPARING_AFTER_MS)
        let_in = self._messages.model.hold_arrivals()  # the messages it brings back are placed in one step when it ends

        def done(result: UndoResult) -> None:
            self._preparing.stop()
            try:
                if result.not_done:
                    count = result.not_done + len(result.messages)
                    self._status.show(f"{'Redone' if redo else 'Undone'}: {result.action}. {result.not_done:,} of {count:,} stayed as they are: {result.reason}")
                else:
                    self._status.finished(f"{'Redone' if redo else 'Undone'}: {result.action}")
                self._messages.model.undone(result.messages, redo)  # those a Mark as read took out of Unread (D-282)
                self._messages.select_messages(result.messages)
            finally:  # always: what waits behind this Undo goes on, once the messages it brought back have their places in the list
                let_in()  # after select_messages: they are selected as they take their places
                self._messages.model.after_arrivals(lambda: self._done_with(release))

        def failed(error: ChronoMailError) -> None:
            self._preparing.stop()
            try:
                self._status.show(str(error))  # "Nothing to undo": not an error of the mail
            finally:
                let_in()
                self.ended()
                release()

        self._backend.ask(MAIL_REDO if redo else MAIL_UNDO, NoParameters(), done, failed, owner=self)

    def _done_with(self, release: Callable[[], None]) -> None:
        try:
            self.ended()
        finally:
            release()

    def _say_preparing(self) -> None:
        self._status.show(f"Preparing to {'redo' if self._preparing_redo else 'undo'}…", running=True)

    def _counting(self, what: str, done: int, total: object) -> None:
        """The engine counts the messages of the Undo under way: its count follows (the status line shows it), not "Preparing"."""
        if what.startswith(("Undoing ", "Redoing ")):
            self._preparing.stop()

    def refresh(self) -> None:
        """Ask the engine what Undo and Redo would do now."""

        def answered(labels: UndoLabels) -> None:
            self.undo_label, self.redo_label = labels.undo, labels.redo
            self.labels_changed.emit()

        self._backend.ask(MAIL_UNDO_LABELS, NoParameters(), answered, lambda error: None, owner=self)
