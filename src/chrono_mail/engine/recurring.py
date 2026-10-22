import calendar
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from pathlib import Path
from urllib.parse import quote, unquote

from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.schedule import describe_when

# --- Version ------------------------------------------------------------
__version__ = "recurring.py 1.1.206"

# --- Version history ----------------------------------------------------
# v1.1.206: The timetable is carried by the message itself (repeat_text, repeat_from_text); recurring.json is only read once to move it in (D-176)
# v0.0.67: Initial schedule of messages that are sent again and again

OLD_RECURRING_FILE: str = "recurring.json"  # before 1.1.206 the timetables were kept apart from their messages; read once to move them in
MAXIMUM_MONTHS: int = 120
MAXIMUM_WEEKS: int = 520
MAXIMUM_DAYS: int = 3650
MAXIMUM_SLOTS: int = 200_000  # how far a catch-up looks; a daily message over 500 years
MAXIMUM_ATTEMPTS: int = 3
MAXIMUM_ERROR_TEXT: int = 300  # the last error, as written in the message (a header value is short)


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


def load_old_repeats(path: Path) -> dict[str, Repeat]:
    """The file of before 1.1.206: store id of the repeating message -> its timetable. A missing or damaged file is an empty list."""
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


def repeat_text(repeat: Repeat) -> str:
    """The timetable as the message carries it (X-Chrono-Mail-Repeat): short printable text without spaces, so it is never folded."""
    error = quote(repeat.last_error, safe="")[:MAXIMUM_ERROR_TEXT]
    error = error[: error.rfind("%")] if "%" in error[-2:] else error  # never a cut-off %XX
    fields = (
        ("start", str(repeat.start)), ("end", "" if repeat.end is None else str(repeat.end)), ("months", str(repeat.months)), ("weeks", str(repeat.weeks)),
        ("days", str(repeat.days)), ("index", str(repeat.index)), ("sent", str(repeat.sent)), ("paused", "1" if repeat.paused else "0"),
        ("attempts", str(repeat.attempts)), ("last_sent", str(repeat.last_sent)), ("error", error),
    )
    return ";".join(f"{name}={value}" for name, value in fields)


def repeat_from_text(text: str) -> Repeat | None:
    """The timetable a message carries, or None when it is not one (or damaged). The header may have been folded and encoded
    when the message was first built (a long value): that is undone first."""
    try:
        text = str(make_header(decode_header(" ".join(text.split()))))
    except (HeaderParseError, LookupError, UnicodeDecodeError, ValueError):
        return None
    values: dict[str, object] = {}
    for part in text.strip().split(";"):
        name, separator, value = part.partition("=")
        if not separator:
            return None
        values[name] = value
    end = values.get("end")
    try:
        data: dict[str, object] = {**values, "end": int(str(end)) if end else None, "paused": values.get("paused") == "1", "last_error": unquote(str(values.get("error", "")))}
    except ValueError:
        return None
    return _from_data(data)
