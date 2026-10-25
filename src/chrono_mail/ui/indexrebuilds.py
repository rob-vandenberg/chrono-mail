from collections.abc import Callable
from typing import override

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core import ChronoMailError, RebuildAborted
from chrono_mail.engine.changes import RebuildStep
from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters
from chrono_mail.engine.commands_folders import INDEX_ABORT, INDEX_CHECK, INDEX_FILL_SENT, INDEX_PREPARE, INDEX_REBUILD, IndexState
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "indexrebuilds.py 3.5.348"

# --- Version history ----------------------------------------------------
# v3.5.348: The question Abort rebuilding shows plain text (RV-167, without a filter on the whole program)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v2.0.303: show_window: the window comes back after Run in background (a click on the bar in the status line, D-229)
# v2.0.281: Initial version (D-229): at start every index is checked; the ones that cannot be used are rebuilt one after the other before
#           the mail is shown, then the Sent dates of upgraded ones are filled in; a window with the account, a wide bar and how many of how
#           many; Run in background while the old index stays usable; Abort after a question (closes the program when nothing usable is left)

EXPLANATION: str = (
    "Chrono Mail is updating its index of your mail, so your messages can be listed, sorted and searched. Your mail itself is not changed."
)
ABORT_TITLE: str = "Abort rebuilding?"
ABORT_KEEPS: str = "The rebuild stops and Chrono Mail keeps using the index it has. You can start it again in Settings."
ABORT_CLOSES: str = (
    "Are you sure you want to abort rebuilding the index? Without a complete index Chrono Mail cannot show the mail of this account, "
    "so it will close now and start the rebuild again the next time you open it. Your mail itself is safe."
)
BACKGROUND_TIP: str = "Only while Chrono Mail can keep using the index it has: this account's mail cannot be shown until the rebuild is done."


def ask_abort(parent: QWidget, usable: bool) -> bool:
    """Abort rebuilding?, with Continue rebuilding as the default: whether the person chose to abort."""
    box = QMessageBox(QMessageBox.Icon.Warning, ABORT_TITLE, ABORT_KEEPS if usable else ABORT_CLOSES, QMessageBox.StandardButton.NoButton, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)  # never markup (RV-167)
    abort = box.addButton("Abort" if usable else "Abort and close", QMessageBox.ButtonRole.DestructiveRole)
    keep_going = box.addButton("Continue rebuilding", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep_going)
    box.setEscapeButton(keep_going)
    box.exec()
    return box.clickedButton() is abort


class RebuildWindow(QDialog):
    """What a rebuild of the index is doing: why, for which account (2 of 3), a wide bar and how many of how many."""

    background_chosen = Signal()
    abort_chosen = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rebuilding the index")
        self.setModal(True)  # above every window, also Settings; Run in background lets go of it
        layout = QVBoxLayout(self)
        explanation = QLabel(EXPLANATION, self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.account_label: QLabel = QLabel("", self)
        layout.addWidget(self.account_label)
        self.bar: QProgressBar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setMinimumWidth(wpx(520))
        layout.addWidget(self.bar)
        self.count_label: QLabel = QLabel("", self)
        layout.addWidget(self.count_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.background_button: QPushButton = QPushButton("Run in background", self)
        self.abort_button: QPushButton = QPushButton("Abort", self)
        buttons.addWidget(self.background_button)
        buttons.addWidget(self.abort_button)
        layout.addLayout(buttons)
        self.background_button.clicked.connect(self.background_chosen)
        self.abort_button.clicked.connect(self.abort_chosen)

    def show_step(self, position: int, count: int, name: str, done: int, total: int, usable: bool) -> None:
        self.account_label.setText(f"Account {position} of {count}: {name}")
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(min(done, total))
        self.count_label.setText(f"{done:,} of {total:,}")
        self.background_button.setEnabled(usable)  # greyed, never hidden
        self.background_button.setToolTip("" if usable else BACKGROUND_TIP)

    @override
    def reject(self) -> None:
        """Escape: into the background where that is possible; the rebuild never stops without its question."""
        if self.background_button.isEnabled():
            self.background_chosen.emit()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.reject()


Job = tuple[str, str]  # (what: "prepare", "fill" or "rebuild", the place's account)


class IndexRebuilds(QObject):
    """The owner of rebuilding and filling in the indexes (D-229): at start every place where mail is kept is checked; the indexes that
    cannot be used are rebuilt one after the other before the mail is shown, then the Sent dates of upgraded ones are filled in while
    the person works. Settings > Rebuild the search index runs here too. The window, the status line and the paused mail checks follow."""

    running_changed = Signal(bool)  # an index is rebuilt or filled in: the automatic mail checks wait
    quit_requested = Signal()  # aborted while nothing usable was left: the program closes

    def __init__(self, backend: Backend, status: StatusReport, name_of: Callable[[str], str], parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._status: StatusReport = status
        self._name_of: Callable[[str], str] = name_of
        self._parent: QWidget = parent
        self.window: RebuildWindow | None = None
        self._names: dict[str, str] = {}
        self._plan: list[str] = []  # the places of this round, in order (Account 2 of 3)
        self._jobs: list[Job] = []
        self._later: list[Job] = []  # what may run while the person works (the Sent dates filled in)
        self._then: list[Callable[[], None]] = []  # what waits for the rebuilds that come first (the mail shown at start)
        self._report: Callable[[list[str]], None] | None = None  # Settings: a line per place, when all are done
        self._lines: list[str] = []
        self._current: str | None = None
        self._usable: bool = True
        self._background: bool = False
        self._last_step: tuple[int, int, str, int, int, bool] | None = None  # what the window showed last: it comes back with it
        self._aborted: bool = False
        self.running: bool = False
        backend.rebuilding.connect(self._step)

    # --- the rounds ------------------------------------------------------------------------------------------

    def start(self, then: Callable[[], None]) -> None:
        """At start: rebuild what cannot be used (then show the mail), then fill in the Sent dates of upgraded indexes."""

        def checked(states: tuple[IndexState, ...]) -> None:
            rebuild = [state.account for state in states if state.need == "rebuild"]
            fill = [state.account for state in states if state.need == "fill"]
            self._begin(states, [("prepare", account) for account in rebuild], then, [("fill", account) for account in fill])

        def failed(error: ChronoMailError) -> None:
            self._status.report("Checking the index", error)
            then()

        self._backend.ask(INDEX_CHECK, NoParameters(), checked, failed, owner=self)

    def rebuild_all(self, report: Callable[[list[str]], None]) -> None:
        """Settings > Rebuild the search index: every place, one after the other, while the old indexes stay in use."""

        def checked(states: tuple[IndexState, ...]) -> None:
            self._report = report
            self._begin(states, [("rebuild", state.account) for state in states], None, [])

        self._backend.ask(INDEX_CHECK, NoParameters(), checked, lambda error: report([str(error)]), owner=self)

    def _begin(self, states: tuple[IndexState, ...], first: list[Job], then: Callable[[], None] | None, later: list[Job]) -> None:
        self._names.update({state.account: state.name for state in states})
        self._plan = [account for _, account in (*first, *later)]
        self._jobs = list(first)
        self._then = [then] if then is not None else []
        self._later = list(later)
        self._background = self._aborted = False
        self._lines = []
        if self.window is not None:  # a new window for each round, above whatever window is in front now (Settings)
            self.window.deleteLater()
            self.window = None
        self._next()

    def _next(self) -> None:
        if not self._jobs and self._then:
            for then in self._then:
                then()
            self._then = []
        if not self._jobs and self._later and not self._aborted:
            self._jobs = self._later
            self._later = []
        if not self._jobs or self._aborted:
            self._finish()
            return
        what, account = self._jobs.pop(0)
        self._current = account
        self._usable = what != "prepare"
        self._set_running(True)
        command = {"prepare": INDEX_PREPARE, "rebuild": INDEX_REBUILD}.get(what)
        parameters = AccountParameters(account)
        if command is None:
            self._backend.ask(INDEX_FILL_SENT, parameters, lambda result: self._done(account, None), lambda error: self._done(account, error), owner=self)
        else:
            self._backend.ask(command, parameters, lambda result: self._done(account, f"{counted(result.messages, 'message')} indexed"), lambda error: self._done(account, error), owner=self)

    def _done(self, account: str, outcome: str | ChronoMailError | None) -> None:
        if isinstance(outcome, ChronoMailError) and not isinstance(outcome, RebuildAborted):
            self._status.report(f"Rebuilding the index of {self._name(account)}", outcome)
        if self._report is not None:
            self._lines.append(f"{self._name(account)}: {'aborted' if isinstance(outcome, RebuildAborted) else outcome}")
        self._current = None
        self._next()

    def _finish(self) -> None:
        if self.window is not None:
            self.window.hide()
        if self.running:
            self._status.show("Rebuilding the index was aborted." if self._aborted else "The index is ready.")
        report, self._report = self._report, None
        self._set_running(False)
        if report is not None:
            report(self._lines)

    def _set_running(self, running: bool) -> None:
        if running != self.running:
            self.running = running
            self.running_changed.emit(running)

    def _name(self, account: str) -> str:
        return self._names.get(account) or self._name_of(account)

    # --- the reports of the engine ---------------------------------------------------------------------------

    def _step(self, step: object) -> None:
        if not isinstance(step, RebuildStep):
            return
        if step.ended:
            if self._current is None and not self._jobs and self.running:
                self._finish()  # a rebuild nobody planned is over
            return
        if step.total == 0:
            return  # an empty store is indexed in no time: no window for it
        if step.account not in self._plan:  # a rebuild nobody planned (an index that went missing while the program runs)
            self._plan.append(step.account)
        self._usable = step.usable
        name = self._name(step.account)
        self._status.rebuild_progress(name, step.done, step.total)
        if self._current is None:
            self._set_running(True)
        self._last_step = (self._plan.index(step.account) + 1, len(self._plan), name, step.done, step.total, step.usable)
        if self._background:
            return
        window = self._window()
        window.show_step(*self._last_step)
        if not window.isVisible():
            window.show()

    def _window(self) -> RebuildWindow:
        if self.window is None:
            self.window = RebuildWindow(QApplication.activeModalWidget() or self._parent)
            self.window.background_chosen.connect(self._into_background)
            self.window.abort_chosen.connect(self._ask_abort)
        return self.window

    def show_window(self) -> None:
        """Bring the window back after Run in background (a click on the bar in the status line), at the point the work has reached."""
        if not self.running or self._last_step is None:
            return
        self._background = False
        window = self._window()
        window.show_step(*self._last_step)
        window.show()
        window.raise_()
        window.activateWindow()

    def _into_background(self) -> None:
        self._background = True
        if self.window is not None:
            self.window.hide()

    def _ask_abort(self) -> None:
        usable = self._usable
        if not ask_abort(self.window or self._parent, usable):
            return
        self._aborted = True
        self._jobs.clear()
        self._later.clear()
        if self._current is not None:
            self._backend.ask(INDEX_ABORT, AccountParameters(self._current), lambda result: None, owner=self)
        if not usable:
            if self.window is not None:
                self.window.hide()
            self.quit_requested.emit()
