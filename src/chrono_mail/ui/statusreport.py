from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QLabel, QMessageBox, QStatusBar, QToolTip, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.store import MessageNotFoundError
from chrono_mail.ui.backend import RunningJob
from chrono_mail.ui.colours import red
from chrono_mail.ui.linkhint import StatusLine
from chrono_mail.ui.operations import ClickableBar

# --- Version ------------------------------------------------------------
__version__ = "statusreport.py 4.2.392"

# --- Version history ----------------------------------------------------
# v4.2.392: progress_ended: the count of a command that ended (an import: done, stopped or failed) leaves the status bar at once
# v3.6.365: the busy bar moves only for work that has run for a second; it stops as soon as nothing runs
# v3.6.364: a line has its own clock: a minute after it came, whatever else runs (the busy bar plays no part); a line about work still running stays
# v3.6.363: an informative line is cleared a minute after it came, or a minute after the work that ran; warnings and errors stay
# v3.6.352: the busy bar is never shown or hidden: it keeps its place and is empty while nothing runs (D-266)
# v3.4.344: the status line shows plain text: folder names, subjects and server replies are never markup (RV-167)
# v2.4.322: backup_hint: while a backup runs in the background, a click on the busy bar brings the Back up window back (D-244)
# v2.1.311: the busy bar's hover text always names what runs (every command's doing, with its accounts), then what a click does; it follows
#           while shown; learning_junk is gone (the general rule covers it)
# v2.1.310: learning_junk: the bar's hover text says the junk filter is learning from the mail kept (D-238)
# v2.0.303: bar_clicked: what a click on the bar does is decided by the window (the index window while an index is updated, D-229); bar_hint
# v2.0.281: rebuild_progress: Rebuilding the index of an account… 18,000 of 140,567 (D-229)
# v1.9.264: R-17: the status line of the main window as a part of its own (out of ui/mainwindow.py): what is said, an error in red,
#           the busy bar, and one warning for messages whose file is gone

CLICK_OPERATIONS: str = "Click to show the Operations window"
CLICK_INDEX: str = "Click to show the index progress"
CLICK_BACKUP: str = "Click to show the backup progress"
CLEAR_AFTER_MS: int = 60_000  # an informative line is cleared a minute after it came, by its own clock; a warning, an error or work still running stays
BUSY_AFTER_MS: int = 1000  # the busy bar moves only for work that runs longer than a moment (a minute check of 36 ms never shows)
MISSING_WARNING: str = "Some messages no longer exist on disk. Go to Settings > Security and storage and click Repair the search index."


class StatusReport(QObject):
    """The status line of the main window: one text (red for an error), where a link leads while the pointer is on it (D-215), and
    the busy bar, whose click asks for the Operations window."""

    bar_clicked = Signal()  # the busy bar: the index window while an index is updated, else the Operations window

    def __init__(self, parent: QWidget, account_name: Callable[[str], str]) -> None:
        super().__init__(parent)
        self._parent: QWidget = parent
        self._account_name: Callable[[str], str] = account_name  # how an account is named in the busy bar's hover text
        self._jobs: tuple[RunningJob, ...] = ()
        self.label: QLabel = QLabel("", parent)
        self.label.setTextFormat(Qt.TextFormat.PlainText)  # it shows folder names, subjects and server replies: never markup (RV-167)
        self.progress_bar: ClickableBar = ClickableBar(parent)
        self.progress_bar.clicked.connect(self.bar_clicked)
        self.progress_bar.setToolTip(CLICK_OPERATIONS)
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setTextVisible(False)
        self.line: StatusLine = StatusLine(self.label, parent)  # while the pointer is on a link in the reading pane: where it leads (D-215)
        self._is_error: bool = False
        self._index_running: bool = False
        self._backup_running: bool = False
        self._missing_timer: QTimer = QTimer(self)  # the answers of an action come one message at a time: one warning for all of them
        self._missing_timer.setSingleShot(True)
        self._missing_timer.setInterval(300)
        self._missing_timer.timeout.connect(self._show_missing_warning)
        self._clear_timer: QTimer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._clear)
        self._busy_timer: QTimer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.timeout.connect(lambda: self.progress_bar.set_busy(True))

    def install(self, bar: QStatusBar) -> None:
        bar.addWidget(self.line, 1)
        bar.addPermanentWidget(self.progress_bar)

    def show(self, text: str, error: bool = False, running: bool = False) -> None:
        """A line: an informative one goes a minute after it came, by its own clock (whatever else runs meanwhile); a warning or an error
        (red) and one about work still running (running: "Preparing to …", a count) stay until the next line."""
        self.label.setText(text)
        self._is_error = error
        self.restyle()
        if error or running or not text:
            self._clear_timer.stop()
        else:
            self._clear_timer.start(CLEAR_AFTER_MS)

    def _clear(self) -> None:
        if not self._is_error:
            self.label.setText("")

    def restyle(self) -> None:
        """The red of an error follows the colours."""
        self.label.setStyleSheet(f"color: {red()}" if self._is_error else "")

    def finished(self, text: str, keep_error: bool = False) -> None:
        """An action is done: say so, unless an error of it is shown (keep_error). The rows already follow the facts (D-146)."""
        if not keep_error:
            self.show(text)

    def report(self, title: str, error: ChronoMailError) -> None:
        self.show(f"{title}: {error}", error=True)
        if isinstance(error, MessageNotFoundError):
            self.warn_missing()

    def warn_missing(self) -> None:
        """A message's file is gone (it only left the index): one warning, also when many answers say so."""
        self._missing_timer.start()

    def _show_missing_warning(self) -> None:
        self.show(MISSING_WARNING, error=True)
        QMessageBox.warning(self._parent, "Messages missing", MISSING_WARNING)

    def bar_hint(self, index_running: bool) -> None:
        """While an index is updated, a click on the bar shows its progress, and its line comes first."""
        self._index_running = index_running
        self._bar_tip()

    def backup_hint(self, backup_running: bool) -> None:
        """While a backup runs (also in the background), a click on the bar shows its window (unless an index is updated)."""
        self._backup_running = backup_running
        self._bar_tip()

    def jobs_changed(self, jobs: object) -> None:
        """The commands running now (the backend's RunningJob tuple): the hover text names them."""
        self._jobs = tuple(job for job in jobs if isinstance(job, RunningJob)) if isinstance(jobs, tuple) else ()
        self._bar_tip()

    def bar_text(self) -> str:
        """What runs, one line per kind of work with the accounts it is for, then what a click does."""
        accounts: dict[str, list[str]] = {}
        for job in self._jobs:
            names = accounts.setdefault(job.doing, [])
            name = self._account_name(job.account) if job.account else ""
            if name and name not in names:
                names.append(name)
        lines = [f"{doing}: {', '.join(names)}" if names else doing for doing, names in accounts.items()]
        if self._index_running:
            lines.sort(key=lambda line: "index" not in line)  # the index's own line first (stable: the rest keep their order)
        return "\n".join([*lines, CLICK_INDEX if self._index_running else CLICK_BACKUP if self._backup_running else CLICK_OPERATIONS])

    def _bar_tip(self) -> None:
        text = self.bar_text()
        self.progress_bar.setToolTip(text)
        if QToolTip.isVisible() and self.progress_bar.underMouse():
            if self.progress_bar.busy:
                QToolTip.showText(QCursor.pos(), text, self.progress_bar)  # the hover text being read follows what runs
            else:
                QToolTip.hideText()  # nothing runs: the empty place says nothing

    def busy_changed(self, count: int) -> None:
        """Work runs: the bar moves once it has run for a moment, and stops as soon as nothing runs. Never shown or hidden: that laid
        the whole window out again at every action (D-266)."""
        if count <= 0:
            self._busy_timer.stop()
            self.progress_bar.set_busy(False)
        elif not self.progress_bar.busy and not self._busy_timer.isActive():
            self._busy_timer.start(BUSY_AFTER_MS)

    def notice(self, text: str) -> None:
        """Something the engine wants the person to know (an IMAP change waits for the server)."""
        self.show(text, error=True)

    def progress(self, what: str, done: int, total: object) -> None:
        self.show(f"{what}… {done:,}" + (f" of {total:,}" if isinstance(total, int) and total else f" of {total}" if total else ""), running=True)

    def progress_ended(self, what: str) -> None:
        """The command that posted this progress line has ended: its count (\"Importing mail… 139,593 of 139,593\") goes at once, so nothing
        looks as if it still runs. Another line shown since then stays."""
        if not self._is_error and self.label.text().startswith(f"{what}… "):
            self.show("")

    def rebuild_progress(self, name: str, done: int, total: int) -> None:
        """How far the index of one account is rebuilt (D-229)."""
        self.show(f"Rebuilding the index of {name}… {done:,} of {total:,}", running=True)

    def load_failed(self, text: str) -> None:
        self.show(f"Could not load the messages: {text}", error=True)
