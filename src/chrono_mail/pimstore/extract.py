import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from chrono_mail.core import CollectionKind
from chrono_mail.pim.contact import parse_contacts
from chrono_mail.pim.ical import Moment, parse_events, parse_tasks

# --- Version ------------------------------------------------------------
__version__ = "extract.py 0.0.60"

# --- Version history ----------------------------------------------------
# v0.0.60: Commands for collections, tags, contacts, events and tasks
# v0.0.59: Initial extraction of what the index holds from an item

_UNTIL = re.compile(r"UNTIL=(\d{8})(?:T(\d{6}))?Z?", re.IGNORECASE)
MAXIMUM_TEXT: int = 20_000  # of the searchable text of one item


@dataclass(frozen=True, slots=True)
class Extract:
    """What the index keeps of an item: enough for lists, views and search; the item itself stays the truth."""

    uid: str = ""
    title: str = ""
    subtitle: str = ""
    sort_key: str = ""
    start: int | None = None
    end: int | None = None
    due: int | None = None
    floating: bool = False  # the times have no zone (or are dates): they are wall-clock times, kept as if they were UTC
    repeats: bool = False
    until: int | None = None
    status: str = ""
    done: bool = False
    priority: int = 0
    percent: int = 0
    tags: tuple[str, ...] = ()
    text: str = ""


def epoch(moment: Moment | None) -> tuple[int | None, bool]:
    """Seconds since 1970 for a moment, and whether it is floating (a date, or a time without a zone: its wall-clock time is used as if it were UTC)."""
    if moment is None:
        return None, False
    try:
        if isinstance(moment, datetime):
            if moment.tzinfo is not None and moment.utcoffset() is not None:
                return int(moment.timestamp()), False
            return calendar.timegm(moment.timetuple()), True
        return calendar.timegm(date(moment.year, moment.month, moment.day).timetuple()), True
    except (OverflowError, ValueError, OSError):
        return None, False


def _until(rule: str) -> int | None:
    found = _UNTIL.search(rule)
    if found is None:
        return None
    try:
        day = calendar.timegm(datetime.strptime(found.group(1), "%Y%m%d").timetuple())
    except ValueError:
        return None
    return day + (int(found.group(2)[0:2]) * 3600 + int(found.group(2)[2:4]) * 60 + int(found.group(2)[4:6]) if found.group(2) else 86399)


def fold(text: str) -> str:
    """A text for sorting: lower case, without accents (Åberg sorts with the As)."""
    return "".join(character for character in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(character))


def _tag_text(tags: tuple[str, ...], folded: bool) -> str:
    """The tags as one text that can be searched for a whole tag: each between separators (in lower case when folded)."""
    return "".join(f"\x1f{tag.casefold() if folded else tag}" for tag in tags) + ("\x1f" if tags else "")


def extract(kind: CollectionKind, text: str) -> Extract:
    """What to index for an item. An item that cannot be read gets an empty extract: it stays stored and shows up as unreadable."""
    if kind is CollectionKind.EVENTS:
        series = parse_events(text)
        if not series:
            return Extract()
        event = series[0].master
        start, floating = epoch(event.start)
        end, _ = epoch(event.end)
        body = " ".join([event.description, event.location, *event.categories, *(person.address + " " + person.name for person in event.attendees)])[:MAXIMUM_TEXT]
        return Extract(
            event.uid, event.summary, event.location, str(start or 0).zfill(12), start, end, None, floating, event.repeats, _until(event.rrule) if event.rrule else None,
            event.status, False, 0, 0, event.categories, body,
        )
    if kind is CollectionKind.TASKS:
        tasks = parse_tasks(text)
        if not tasks:
            return Extract()
        task = tasks[0]
        due, floating = epoch(task.due)
        start, start_floating = epoch(task.start)
        body = " ".join([task.description, task.location, *task.categories])[:MAXIMUM_TEXT]
        return Extract(
            task.uid, task.summary, task.location, str(due if due is not None else 10**11).zfill(12), start, None, due, floating or start_floating, bool(task.rrule), None,
            task.status, task.done, task.priority, task.percent, task.categories, body,
        )
    contacts = parse_contacts(text)
    if not contacts:
        return Extract()
    contact = contacts[0]
    body = " ".join(
        [
            *(item.value for item in contact.emails), *(item.value for item in contact.phones), *(item.value for item in contact.urls), contact.organization,
            contact.department, contact.title, contact.role, contact.note, *contact.nicknames, *contact.categories,
            *(part for address in contact.addresses for part in address.lines()),
        ]
    )[:MAXIMUM_TEXT]
    subtitle = contact.organization or (contact.emails[0].value if contact.emails else "")
    return Extract(contact.uid, contact.full_name(), subtitle, fold(contact.sort_name()), None, None, None, False, False, None, "", False, 0, 0, contact.categories, body)


def tag_text(tags: tuple[str, ...], folded: bool = False) -> str:
    return _tag_text(tags, folded)
