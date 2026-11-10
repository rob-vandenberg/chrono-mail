from collections.abc import Callable
from datetime import datetime, time, timedelta

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication

# --- Version ------------------------------------------------------------
__version__ = "dayheadings.py 4.4.563"

# --- Version history ----------------------------------------------------
# v4.4.563: Initial: the local day is watched; when it changes, the message list asks its day headings again (D-321)

DAY_SORTS: frozenset[str] = frozenset({"date", "sent", "snoozed"})  # the sorts whose headings name a day (Today, Yesterday; Tomorrow for Snoozed)
LONGEST_WAIT_MS: int = 5 * 60 * 1000  # a look at least this often: a timer does not count the time the computer slept
PAST_MIDNIGHT_MS: int = 1000  # the first look of a new day, just after its midnight


def local_now() -> datetime:
    return datetime.now().astimezone()


class DayWatch(QObject):
    """Says when the local day changed: just after midnight, and after the computer slept past it (a look every five minutes at most,
    and when the program becomes active again). Between looks it costs nothing (D-266). The day is the one of the zone the program
    started in, as everywhere in the program."""

    day_changed: Signal = Signal(str)  # the new day, YYYY-MM-DD
    looked: Signal = Signal(str)  # every look, with the day (a list whose headings are of another day asks them again)

    def __init__(self, parent: QObject | None = None, now: Callable[[], datetime] = local_now) -> None:
        super().__init__(parent)
        self._now: Callable[[], datetime] = now
        self._day: str = now().date().isoformat()
        self._timer: QTimer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.look)
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            application.applicationStateChanged.connect(self._state_changed)
        self._arm()

    @property
    def day(self) -> str:
        return self._day

    def look(self) -> None:
        """Compare the day with the one seen last; tell a change once."""
        today = self._now().date().isoformat()
        if today != self._day:
            self._day = today
            self.day_changed.emit(today)
        self.looked.emit(today)
        self._arm()

    def _state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.look()

    def _arm(self) -> None:
        moment = self._now()
        midnight = datetime.combine(moment.date() + timedelta(days=1), time.min).astimezone()  # the next midnight, also across summer time
        wait = int((midnight - moment).total_seconds() * 1000) + PAST_MIDNIGHT_MS
        self._timer.start(max(PAST_MIDNIGHT_MS, min(wait, LONGEST_WAIT_MS)))
