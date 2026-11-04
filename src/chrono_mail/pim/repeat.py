from dataclasses import dataclass
from datetime import UTC, date, datetime, time, tzinfo

# --- Version ------------------------------------------------------------
__version__ = "repeat.py 4.4.535"

# --- Version history ----------------------------------------------------
# v4.4.535: Initial version: the repeat choices that follow an event's date, and a Custom repeat as the standard rule of the event (round 4)

WEEKDAYS: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
DAY_NAMES: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
WEEK_OF_MONTH: tuple[str, ...] = ("first", "second", "third", "fourth")
UNITS: tuple[tuple[str, str], ...] = (("DAILY", "day"), ("WEEKLY", "week"), ("MONTHLY", "month"), ("YEARLY", "year"))
MOST_DIGITS: int = 9  # Python refuses a text of thousands of digits as a number
EVERY_WEEKDAY: str = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
SHOWN_PARTS: tuple[str, ...] = ("FREQ", "INTERVAL", "COUNT", "UNTIL")  # what a Custom repeat sets itself; every other part of a rule stays while the unit stays


@dataclass(frozen=True, slots=True)
class RepeatChoice:
    rule: str
    label: str


@dataclass(frozen=True, slots=True)
class CustomRepeat:
    """What the Custom window shows: every so many units, on these days of the week, until a date or so many times."""

    unit: str = "WEEKLY"
    every: int = 1
    days: tuple[str, ...] = ()
    until: date | None = None
    count: int | None = None


def _weekday(day: date) -> str:
    return WEEKDAYS[day.weekday()]


def repeat_choices(day: date) -> tuple[RepeatChoice, ...]:
    """The list as it follows the event's date. The fifth Tuesday of a month is its last: a fifth one is not there every month."""
    week = (day.day - 1) // 7
    position, word = (week + 1, WEEK_OF_MONTH[week]) if week < len(WEEK_OF_MONTH) else (-1, "last")
    name = DAY_NAMES[day.weekday()]
    return (
        RepeatChoice("", "Does not repeat"),
        RepeatChoice("FREQ=DAILY", "Daily"),
        RepeatChoice(f"FREQ=WEEKLY;BYDAY={_weekday(day)}", f"Weekly on {name}"),
        RepeatChoice(f"FREQ=MONTHLY;BYDAY={position}{_weekday(day)}", f"Monthly on the {word} {name}"),
        RepeatChoice("FREQ=YEARLY", f"Annually on {MONTH_NAMES[day.month - 1]} {day.day}"),
        RepeatChoice(EVERY_WEEKDAY, "Every weekday (Monday to Friday)"),
    )


def rule_parts(rule: str) -> dict[str, str]:
    """The parts of a rule by name, in their order."""
    found: dict[str, str] = {}
    for piece in rule.removeprefix("RRULE:").split(";"):
        name, _, value = piece.partition("=")
        if name.strip():
            found[name.strip().upper()] = value.strip()
    return found


def _rule_text(parts: dict[str, str]) -> str:
    return ";".join(f"{name}={value}" for name, value in parts.items())


def _meaning(rule: str, day: date) -> dict[str, str]:
    """A rule as it works from this date: what it leaves out is what it means (a weekly rule without days repeats on the day of the event)."""
    parts = rule_parts(rule)
    if parts.get("INTERVAL") == "1":
        del parts["INTERVAL"]
    if parts.get("FREQ") == "WEEKLY" and "BYDAY" not in parts:
        parts["BYDAY"] = _weekday(day)
    if "BYDAY" in parts:
        parts["BYDAY"] = ",".join(sorted(parts["BYDAY"].split(",")))
    return parts


def same_rule(first: str, second: str, day: date) -> bool:
    return _meaning(first, day) == _meaning(second, day)


def choice_of(rule: str, day: date) -> int:
    """Which of the choices this rule is on this date; -1: none of them, it is a Custom repeat."""
    return next((index for index, choice in enumerate(repeat_choices(day)) if same_rule(choice.rule, rule, day)), -1)


def _until_day(text: str, zone: tzinfo) -> date | None:
    try:
        if len(text) == 8:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC).date()
        moment = datetime.strptime(text.removesuffix("Z"), "%Y%m%dT%H%M%S")
        return moment.replace(tzinfo=UTC).astimezone(zone).date() if text.endswith("Z") else moment.date()
    except ValueError:
        return None


def _until_text(day: date, all_day: bool, zone: tzinfo) -> str:
    """The last day as the rule writes it: a date for an all-day event, else the end of that day here, in UTC (RFC 5545 asks both)."""
    if all_day:
        return day.strftime("%Y%m%d")
    return datetime.combine(day, time(23, 59, 59), zone).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _number(text: str) -> int | None:
    """A number of a rule from elsewhere: only the digits 0 to 9, and short enough to be a number at all (OR-3)."""
    return int(text) if text.isascii() and text.isdigit() and len(text) <= MOST_DIGITS and int(text) > 0 else None


def custom_of(rule: str, day: date, zone: tzinfo) -> CustomRepeat:
    """The Custom window for a rule; without one it starts at every week on the event's day."""
    parts = rule_parts(rule)
    unit = parts.get("FREQ", "WEEKLY")
    days = tuple(name for name in parts.get("BYDAY", "").split(",") if name in WEEKDAYS)
    return CustomRepeat(
        unit if unit in dict(UNITS) else "WEEKLY", _number(parts.get("INTERVAL", "")) or 1, days or (_weekday(day),),
        _until_day(parts.get("UNTIL", ""), zone), _number(parts.get("COUNT", "")),
    )


def rule_of(custom: CustomRepeat, before: str, day: date, all_day: bool, zone: tzinfo) -> str:
    """The standard rule of what the Custom window holds. What that window does not show of the rule it had stays, as long as the unit stays."""
    old = rule_parts(before)
    parts: dict[str, str] = {"FREQ": custom.unit}
    if custom.every > 1:
        parts["INTERVAL"] = str(custom.every)
    if custom.unit == "WEEKLY":
        parts["BYDAY"] = ",".join(name for name in WEEKDAYS if name in custom.days) or _weekday(day)
    if old.get("FREQ") == custom.unit:
        parts.update({name: value for name, value in old.items() if name not in SHOWN_PARTS and name not in parts})
    if custom.until is not None:
        parts["UNTIL"] = _until_text(custom.until, all_day, zone)
    elif custom.count is not None:
        parts["COUNT"] = str(custom.count)
    return _rule_text(parts)


def fitted(rule: str, all_day: bool, zone: tzinfo) -> str:
    """The last day of a rule in the form of the event: an event that became all day, or stopped being, writes it the other way."""
    parts = rule_parts(rule)
    until = parts.get("UNTIL", "")
    if not until or (len(until) == 8) == all_day:
        return rule
    last = _until_day(until, zone)
    if last is None:
        return rule
    parts["UNTIL"] = _until_text(last, all_day, zone)
    return _rule_text(parts)


def describe(rule: str, day: date, zone: tzinfo) -> str:
    """A rule in words, for the list. Parts the words cannot say are shown as they are, so nothing looks simpler than it is."""
    custom = custom_of(rule, day, zone)
    word = dict(UNITS)[custom.unit]
    text = f"Every {custom.every} {word}s" if custom.every > 1 else f"Every {word}"
    if custom.unit == "WEEKLY":
        text += " on " + ", ".join(DAY_NAMES[WEEKDAYS.index(name)] for name in WEEKDAYS if name in custom.days)
    if custom.until is not None:
        text += f", until {custom.until.isoformat()}"
    elif custom.count is not None:
        text += f", {custom.count} times"
    parts = rule_parts(rule)
    other = {name: value for name, value in parts.items() if name not in SHOWN_PARTS and not (name == "BYDAY" and custom.unit == "WEEKLY")}
    return f"{text} ({_rule_text(other)})" if other else text
