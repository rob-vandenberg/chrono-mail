"""The first day of the week, as Settings > Calendar's Start week on chooses it: the calendar's views and every date picker follow it (D-352)."""

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import QDateTimeEdit

# --- Version ------------------------------------------------------------
__version__ = "weekstart.py 4.4.616"

# --- Version history ----------------------------------------------------
# v4.4.616: Initial: Start week on (System default, Saturday, Sunday, Monday) for the calendar's views and every date picker (D-352)

WEEK_STARTS: tuple[str, ...] = ("system", "saturday", "sunday", "monday")  # the choices of Start week on, as the settings file keeps them
WEEK_START_NAMES: dict[str, str] = {"system": "System default", "saturday": "Saturday", "sunday": "Sunday", "monday": "Monday"}
_DAYS: dict[str, Qt.DayOfWeek] = {"saturday": Qt.DayOfWeek.Saturday, "sunday": Qt.DayOfWeek.Sunday, "monday": Qt.DayOfWeek.Monday}


class _Chosen:
    """The choice in use in this program: one for every window, set at start and by Settings OK."""

    choice: str = "system"


def choose(choice: str) -> None:
    """Take this choice into use; an unknown one is the system's default."""
    _Chosen.choice = choice if choice in WEEK_STARTS else "system"


def first_day() -> Qt.DayOfWeek:
    """The first day of the week in use: the chosen day, or the one the system's language and region give."""
    return _DAYS.get(_Chosen.choice, QLocale().firstDayOfWeek())


def first_weekday() -> int:
    """The same as the views count it: 0 is Monday ... 6 is Sunday."""
    return first_day().value - 1


def follow(edit: QDateTimeEdit) -> None:
    """A date field gets its calendar, which opens with its weeks starting on the day in use (Qt makes the calendar with the popup)."""
    edit.setCalendarPopup(True)
    calendar = edit.calendarWidget()
    if calendar is not None:
        calendar.setFirstDayOfWeek(first_day())
