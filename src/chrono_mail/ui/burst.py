from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

# --- Version ------------------------------------------------------------
__version__ = "burst.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: Initial: work asked for many times in one moment (a fact per message of a large delete) runs at once the first time and then
#           at most once per interval while the asks keep coming (16,000 deleted messages laid the lists out 16,000 times)

BURST_MS: int = 100  # a large delete changes the lists ten times a second, a single action at once


class Burst(QObject):
    """Runs its work at once when asked in a quiet moment; asked again within the interval, it runs once more when the interval ends (and
    keeps doing so while the asks keep coming)."""

    def __init__(self, run: Callable[[], None], parent: QObject, interval_ms: int = BURST_MS) -> None:
        super().__init__(parent)
        self._run: Callable[[], None] = run
        self._due: bool = False
        self._timer: QTimer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._ended)

    @property
    def busy(self) -> bool:
        """Within the interval after a run: what is asked now waits for its end."""
        return self._timer.isActive()

    def ask(self) -> None:
        if self._timer.isActive():
            self._due = True
            return
        self._timer.start()
        self._run()

    def wait_for_end(self) -> None:
        """Asked within the interval: it runs when the interval ends (the caller keeps what it should do)."""
        self._due = True
        if not self._timer.isActive():
            self._timer.start()

    def _ended(self) -> None:
        if self._due:
            self._due = False
            self._timer.start()
            self._run()
