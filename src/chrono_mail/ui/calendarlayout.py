from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from chrono_mail.engine.commands_calendar import OccurrenceInfo

# --- Version ------------------------------------------------------------
__version__ = "calendarlayout.py 2.0.307"

# --- Version history ----------------------------------------------------
# v2.0.307: A condition that gave the same last day either way removed (SonarQube Cloud, D-237)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v0.0.64: Initial layout of events in days, weeks, months and lists (no drawing, no Qt)

DAY: str = "day"
WORK_WEEK: str = "work_week"
WEEK: str = "week"
UPCOMING: str = "upcoming"
MONTH: str = "month"
AGENDA: str = "agenda"
VIEWS: tuple[tuple[str, str], ...] = (
    (DAY, "Day"), (WORK_WEEK, "Work Week"), (WEEK, "Week"), (UPCOMING, "Upcoming"), (MONTH, "Month"), (AGENDA, "Agenda"),
)
MINUTES: int = 24 * 60
MINIMUM_MINUTES: int = 25  # a very short event is drawn at least this long, to be seen and clicked
AGENDA_DAYS: int = 60


@dataclass(frozen=True, slots=True)
class Entry:
    """An event (or one occurrence of a repeating event) placed in time, in the local zone."""

    row: OccurrenceInfo  # the occurrence it came from
    summary: str
    start: datetime  # all-day events: midnight of their first day
    end: datetime  # exclusive; all-day events: midnight after their last day
    all_day: bool
    colour: str = ""

    @property
    def first_day(self) -> date:
        return self.start.date()

    @property
    def last_day(self) -> date:
        """The last day it touches (an event that ends at midnight does not touch that day)."""
        last = self.end - timedelta(microseconds=1) if self.end > self.start else self.start
        return last.date()


def parse_moment(text: str, zone: tzinfo, all_day: bool) -> datetime:
    """An ISO date or time from the commands, as a time in `zone` (floating times are taken to be in it)."""
    if len(text) == 10:
        moment = datetime.fromisoformat(text)
        return moment.replace(tzinfo=zone)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=zone)
    return moment.astimezone(zone)


def entries_from(rows: list[OccurrenceInfo], zone: tzinfo) -> list[Entry]:
    """Entries for OccurrenceInfo rows; rows that cannot be read are left out."""
    found: list[Entry] = []
    for row in rows:
        try:
            all_day = row.all_day
            start = parse_moment(row.start, zone, all_day)
            end = parse_moment(row.end, zone, all_day)
        except ValueError:
            continue
        if end < start:
            end = start
        found.append(Entry(row, row.summary or "(no title)", start, end, all_day, row.colour))
    found.sort(key=lambda entry: (entry.start, entry.end, entry.summary.casefold()))
    return found


# --- the views --------------------------------------------------------------------------------------------------------------------------


def week_start(day: date, first_weekday: int) -> date:
    """The first day of the week this day is in (first_weekday: 0 is Monday ... 6 is Sunday)."""
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def days_of(view: str, anchor: date, first_weekday: int = 0, work_days: tuple[int, ...] = (0, 1, 2, 3, 4), today: date | None = None) -> list[date]:
    """The days that are shown as columns (day, week views) or in the grid (month: six weeks)."""
    if view == DAY:
        return [anchor]
    if view in (WEEK, WORK_WEEK):
        first = week_start(anchor, first_weekday)
        days = [first + timedelta(days=offset) for offset in range(7)]
        return [day for day in days if day.weekday() in work_days] if view == WORK_WEEK and work_days else days
    if view == UPCOMING:
        start = today or anchor
        return [start + timedelta(days=offset) for offset in range(7)]
    if view == MONTH:
        first = week_start(date(anchor.year, anchor.month, 1), first_weekday)
        return [first + timedelta(days=offset) for offset in range(42)]
    return [anchor + timedelta(days=offset) for offset in range(AGENDA_DAYS)]


def range_of(view: str, anchor: date, first_weekday: int = 0, work_days: tuple[int, ...] = (0, 1, 2, 3, 4), today: date | None = None) -> tuple[date, date]:
    """The days to ask the commands for: from the first day, up to (not including) the day after the last."""
    days = days_of(view, anchor, first_weekday, work_days, today)
    return days[0], days[-1] + timedelta(days=1)


def step(view: str, anchor: date, direction: int) -> date:
    """The anchor after pressing next (1) or previous (-1)."""
    if view == DAY:
        return anchor + timedelta(days=direction)
    if view in (WEEK, WORK_WEEK, UPCOMING):
        return anchor + timedelta(days=7 * direction)
    if view == MONTH:
        month = anchor.month - 1 + direction
        year = anchor.year + month // 12
        month = month % 12 + 1
        last = (date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)).day
        return date(year, month, min(anchor.day, last))
    return anchor + timedelta(days=AGENDA_DAYS * direction)


def title_for(view: str, anchor: date, days: list[date]) -> str:
    """The text above the views: 'October 2026', 'Oct 12 – 18, 2026', 'Tuesday, October 13, 2026'."""
    if view == MONTH:
        return f"{anchor.strftime('%B')} {anchor.year}"
    first, last = days[0], days[-1]
    if view == DAY:
        return f"{first.strftime('%A')}, {first.strftime('%B')} {first.day}, {first.year}"
    if view == AGENDA:
        return f"From {first.strftime('%b')} {first.day}, {first.year}"
    if first.year == last.year and first.month == last.month:
        return f"{first.strftime('%b')} {first.day} – {last.day}, {last.year}"
    if first.year == last.year:
        return f"{first.strftime('%b')} {first.day} – {last.strftime('%b')} {last.day}, {last.year}"
    return f"{first.strftime('%b')} {first.day}, {first.year} – {last.strftime('%b')} {last.day}, {last.year}"


# --- one day: timed events side by side -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    """The part of an event that is on one day, in minutes from midnight."""

    entry: Entry
    start: int
    end: int
    continues_before: bool
    continues_after: bool
    column: int = 0
    columns: int = 1


def day_segments(entries: list[Entry], day: date, zone: tzinfo) -> list[Segment]:
    """The timed events (not the all-day ones) on a day, each cut to the day, with the column it is drawn in and how many columns its group has, so that
    events that overlap in time stand side by side."""
    begin = datetime(day.year, day.month, day.day, tzinfo=zone)
    finish = begin + timedelta(days=1)
    parts: list[Segment] = []
    for entry in entries:
        if entry.all_day or entry.end <= begin and entry.end != entry.start or entry.start >= finish:
            continue
        if entry.end == entry.start and not begin <= entry.start < finish:
            continue
        start = max(entry.start, begin)
        end = min(entry.end, finish)
        first = int((start - begin).total_seconds() // 60)
        last = int((end - begin).total_seconds() // 60)
        last = max(last, first + MINIMUM_MINUTES)
        parts.append(Segment(entry, first, min(last, MINUTES + MINIMUM_MINUTES), entry.start < begin, entry.end > finish))
    return pack(parts)


def pack(segments: list[Segment]) -> list[Segment]:
    """Give overlapping segments columns: events that touch each other in time form a group; inside a group each takes the first column that is free."""
    ordered = sorted(segments, key=lambda s: (s.start, -s.end, s.entry.summary.casefold()))
    result: list[Segment] = []
    group: list[Segment] = []
    group_end = -1

    def close(members: list[Segment]) -> None:
        if not members:
            return
        columns: list[int] = []  # the end of the last segment in each column
        placed: list[tuple[Segment, int]] = []
        for segment in members:
            for index, end in enumerate(columns):
                if end <= segment.start:
                    columns[index] = segment.end
                    placed.append((segment, index))
                    break
            else:
                columns.append(segment.end)
                placed.append((segment, len(columns) - 1))
        for segment, index in placed:
            result.append(Segment(segment.entry, segment.start, segment.end, segment.continues_before, segment.continues_after, index, len(columns)))

    for segment in ordered:
        if group and segment.start >= group_end:
            close(group)
            group, group_end = list[Segment](), -1
        group.append(segment)
        group_end = max(group_end, segment.end)
    close(group)
    return result


# --- all-day events: bars over several days ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bar:
    """An all-day (or multi-day) event as a bar over the days [first, last] (indexes of the days shown), in a row of the all-day area."""

    entry: Entry
    first: int
    last: int
    row: int
    continues_before: bool = False
    continues_after: bool = False


def bars(entries: list[Entry], days: list[date], include_timed_spanning: bool = True) -> list[Bar]:
    """All-day events, and (for the month view) timed events that run over more than one day, as bars over the days shown, packed into rows."""
    if not days:
        return []
    chosen: list[tuple[Entry, int, int, bool, bool]] = []
    for entry in entries:
        if not entry.all_day and not (include_timed_spanning and entry.last_day > entry.first_day):
            continue
        first_day, last_day = entry.first_day, entry.last_day
        if last_day < days[0] or first_day > days[-1]:
            continue
        first = max(0, (first_day - days[0]).days)
        last = min(len(days) - 1, (last_day - days[0]).days)
        chosen.append((entry, first, last, first_day < days[0], last_day > days[-1]))
    chosen.sort(key=lambda item: (item[1], -(item[2] - item[1]), item[0].summary.casefold()))
    rows: list[int] = []  # the last day used in each row
    result: list[Bar] = []
    for entry, first, last, before, after in chosen:
        for index, end in enumerate(rows):
            if end < first:
                rows[index] = last
                result.append(Bar(entry, first, last, index, before, after))
                break
        else:
            rows.append(last)
            result.append(Bar(entry, first, last, len(rows) - 1, before, after))
    return result


# --- the month: events of a day, and the lists --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cell:
    day: date
    entries: tuple[Entry, ...] = field(default=())


def month_cells(entries: list[Entry], days: list[date]) -> list[Cell]:
    """For each day of the month grid, the events that touch it: all-day and multi-day ones first, then the timed ones by time."""
    cells: list[Cell] = []
    for day in days:
        touching = [entry for entry in entries if entry.first_day <= day <= entry.last_day]
        touching.sort(key=lambda entry: (not (entry.all_day or entry.last_day > entry.first_day), entry.start, entry.summary.casefold()))
        cells.append(Cell(day, tuple(touching)))
    return cells


def agenda_groups(entries: list[Entry], days: list[date], hide_past: bool, now: datetime) -> list[tuple[date, list[Entry]]]:
    """The events for the agenda: for each day that has some, the day and its events (events that are over are left out when asked)."""
    groups: list[tuple[date, list[Entry]]] = []
    for day in days:
        touching = [entry for entry in entries if entry.first_day <= day <= entry.last_day and not (hide_past and entry.end <= now and not entry.all_day)]
        if hide_past and day < now.date():
            touching = list[Entry]()
        if touching:
            touching.sort(key=lambda entry: (not entry.all_day, entry.start, entry.summary.casefold()))
            groups.append((day, touching))
    return groups


def time_text(entry: Entry, day: date | None = None) -> str:
    """'09:00 – 10:30', 'All day', '09:00' (a start that is on the day, an end that is on the day)."""
    if entry.all_day:
        return "All day"
    start = entry.start.strftime("%H:%M")
    if entry.end == entry.start:
        return start
    return f"{start} – {entry.end.strftime('%H:%M')}"
