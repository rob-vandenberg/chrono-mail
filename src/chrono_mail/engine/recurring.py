import calendar
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from chrono_mail.core import StorageError
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.schedule import describe_when

# --- Version ------------------------------------------------------------
__version__ = "recurring.py 0.0.67"

# --- Version history ----------------------------------------------------
# v0.0.67: Initial schedule of messages that are sent again and again

RECURRING_FILE: str = "recurring.json"
MAXIMUM_MONTHS: int = 120
MAXIMUM_WEEKS: int = 520
MAXIMUM_DAYS: int = 3650
MAXIMUM_SLOTS: int = 200_000  # how far a catch-up looks; a daily message over 500 years
MAXIMUM_ATTEMPTS: int = 3


@dataclass(frozen=True, slots=True)
class Repeat:
    """A message that is sent at a start time and then again after every interval, until an end time (or for ever).

    The times of the sends are start + k intervals, counted from the start (not from the last send), so they do not drift and a month
    that is too short gives its last day. `index` is the next send that has not been done."""

    start: int  # seconds since 1970
    end: int | None
    months: int = 0
    weeks: int = 0
    days: int = 0
    index: int = 0
    sent: int = 0
    paused: bool = False
    attempts: int = 0  # failed tries of the send at `index`
    last_error: str = ""
    last_sent: int = 0

    def __post_init__(self) -> None:
        if min(self.months, self.weeks, self.days) < 0 or self.months > MAXIMUM_MONTHS or self.weeks > MAXIMUM_WEEKS or self.days > MAXIMUM_DAYS:
            raise UsageError(f"The interval is at most {MAXIMUM_MONTHS} months, {MAXIMUM_WEEKS} weeks and {MAXIMUM_DAYS} days")
        if self.months + self.weeks + self.days == 0:
            raise UsageError("The interval is at least one day")
        if self.end is not None and self.end < self.start:
            raise UsageError("The end is before the start")


def add_months(moment: datetime, months: int) -> datetime:
    total = moment.year * 12 + moment.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def slot_time(repeat: Repeat, number: int) -> int | None:
    """The time of send number `number` (0 is the start), or None when it is beyond what can be written."""
    try:
        base = datetime.fromtimestamp(repeat.start)
        moment = add_months(base, number * repeat.months) + timedelta(days=number * (7 * repeat.weeks + repeat.days))
        return int(moment.astimezone().timestamp())
    except (OverflowError, ValueError, OSError):
        return None


def next_due(repeat: Repeat) -> int | None:
    """When the next send is, or None when the series is over."""
    moment = slot_time(repeat, repeat.index)
    if moment is None or (repeat.end is not None and moment > repeat.end):
        return None
    return moment


def index_after(repeat: Repeat, now: float) -> int:
    """The number of the first send after now (at or after `index`): sends that were missed are skipped, never made up."""
    number = repeat.index
    for _ in range(MAXIMUM_SLOTS):
        moment = slot_time(repeat, number)
        if moment is None or moment > now or (repeat.end is not None and moment > repeat.end):
            return number
        number += 1
    return number


def describe_repeat(repeat: Repeat) -> str:
    parts: list[str] = []
    for count, word in ((repeat.months, "month"), (repeat.weeks, "week"), (repeat.days, "day")):
        if count:
            parts.append(f"{count} {word}{'s' if count != 1 else ''}")
    text = "every " + " and ".join(parts)
    return text + (f", until {describe_when(repeat.end)}" if repeat.end is not None else "")


def advance(repeat: Repeat, now: float) -> Repeat:
    """The schedule once the send at `index` is being done: it moves to the first send after now."""
    return replace(repeat, index=index_after(repeat, now), attempts=0)


def _from_data(data: object) -> Repeat | None:
    if not isinstance(data, dict):
        return None
    try:
        end = data.get("end")
        return Repeat(
            int(data["start"]), int(end) if end is not None else None, int(data.get("months", 0)), int(data.get("weeks", 0)), int(data.get("days", 0)),
            int(data.get("index", 0)), int(data.get("sent", 0)), bool(data.get("paused", False)), int(data.get("attempts", 0)), str(data.get("last_error", "")),
            int(data.get("last_sent", 0)),
        )
    except (KeyError, TypeError, ValueError, UsageError):
        return None


def load_repeats(path: Path) -> dict[str, Repeat]:
    """store id of the message that is sent again and again -> its schedule. A missing or damaged file is an empty list."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found: dict[str, Repeat] = {}
    for key, value in data.items():
        repeat = _from_data(value)
        if isinstance(key, str) and repeat is not None:
            found[key] = repeat
    return found


def save_repeats(path: Path, repeats: dict[str, Repeat]) -> None:
    data = {
        key: {
            "start": item.start, "end": item.end, "months": item.months, "weeks": item.weeks, "days": item.days, "index": item.index, "sent": item.sent,
            "paused": item.paused, "attempts": item.attempts, "last_error": item.last_error, "last_sent": item.last_sent,
        }
        for key, item in repeats.items()
    }
    temporary = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, json.dumps(data).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError(f"The schedule of repeated messages could not be saved: {error.strerror}") from None
