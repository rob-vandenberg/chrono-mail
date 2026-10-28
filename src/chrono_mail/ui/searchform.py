from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
import itertools

# --- Version ------------------------------------------------------------
__version__ = "searchform.py 4.2.412"

# --- Version history ----------------------------------------------------
# v4.2.412: the parts and kinds of a line in lower case: a line reads as a sentence (the owner)
# v4.2.411: the match choices are shown as ALL and ANY (the owner)
# v4.2.410: Initial: what Advanced search holds as one value (SearchForm): where to search, all or any of the lines, each line (where in
#           the message, how the words count, the words), the date choices (a span counted from today, or fixed days) and the filters;
#           the search words the lines make; the days a date choice stands for; to and from what the saved searches file keeps (D-289)

class SearchScope(StrEnum):
    """Where a search of a folder looks."""

    FOLDER = "folder"  # the folder only
    SUBFOLDERS = "subfolders"  # the folder and every folder inside it, at any depth
    ALL = "all"  # every folder of the account


# The choices in the search field's menu and in Advanced search, in this order (D-287)
SCOPE_CHOICES: tuple[tuple[str, SearchScope], ...] = (
    ("This folder only", SearchScope.FOLDER), ("This folder + subfolders", SearchScope.SUBFOLDERS), ("All folders", SearchScope.ALL),
)
DEFAULT_SCOPE: SearchScope = SearchScope.ALL  # where a search looks at every start, until the person chooses another (D-287)

# Where in the message a line looks, and how its words count (value, the text the drop-down shows), in the order shown
PARTS: tuple[tuple[str, str], ...] = (("anywhere", "anywhere"), ("from", "from"), ("to", "to"), ("subject", "subject"), ("body", "body"))
KINDS: tuple[tuple[str, str], ...] = (
    ("all", "all of these words"), ("any", "any of these words"), ("exact", "this exact phrase"), ("none", "none of these words"),
)
MATCHES: tuple[tuple[str, str], ...] = (("all", "ALL"), ("any", "ANY"))  # Find messages that match [ALL/ANY] of these conditions

# The date choices: of what was (Received, Sent) and of what is to come (Snoozed until, Send at); between, before and after have days
PAST_CHOICES: tuple[tuple[str, str], ...] = (
    ("any", "Any time"), ("today", "Today"), ("yesterday", "Yesterday"), ("last7", "Last 7 days"), ("last30", "Last 30 days"),
    ("this_month", "This month"), ("last_month", "Last month"), ("this_year", "This year"), ("last_year", "Last year"),
    ("between", "Between…"), ("before", "Before…"), ("after", "After…"),
)
FUTURE_CHOICES: tuple[tuple[str, str], ...] = (
    ("any", "Any time"), ("today", "Today"), ("tomorrow", "Tomorrow"), ("next7", "Next 7 days"), ("next30", "Next 30 days"),
    ("between", "Between…"), ("before", "Before…"), ("after", "After…"),
)
WITH_DAYS: tuple[str, ...] = ("between", "before", "after")  # the choices that show fixed days

MAXIMUM_LINES: int = 50  # what a saved search file may hold: more is cut off (hostile or damaged input, AGENTS 4.2)
MAXIMUM_TEXT: int = 1000  # characters of one line's words, of a name
MAXIMUM_WAYS: int = 256  # alternatives the lines may make (Any lines under all multiply): more would hold the window and the search
MAXIMUM_QUERY: int = 8000  # characters of the search words: the search field holds them whole
FIRST_DAY: date = date(2, 1, 1)  # the days a search can ask for (the engine refuses others, D-259)
LAST_DAY: date = date(9998, 12, 31)


@dataclass(frozen=True, slots=True)
class SearchLine:
    """One line: where in the message it looks, how its words count, the words."""

    part: str = "anywhere"
    kind: str = "all"
    text: str = ""


@dataclass(frozen=True, slots=True)
class DateChoice:
    """A date row: a span counted from today, or fixed days (YYYY-MM-DD) with between (first and last), before (last), after (first)."""

    choice: str = "any"
    first: str | None = None
    last: str | None = None


@dataclass(frozen=True, slots=True)
class SearchForm:
    """Everything Advanced search holds, as one value: what a saved search keeps and what the window opens with again."""

    scope: SearchScope = DEFAULT_SCOPE
    match: str = "all"
    lines: tuple[SearchLine, ...] = (SearchLine(),)
    received: DateChoice = field(default_factory=DateChoice)
    sent: DateChoice = field(default_factory=DateChoice)
    minimum_kb: int = 0
    maximum_kb: int = 0
    unread: bool = False
    flagged: bool = False
    attachments: bool = False
    tag: str | None = None
    snoozed: DateChoice = field(default_factory=DateChoice)
    send_at: DateChoice = field(default_factory=DateChoice)


def _words(text: str, phrase: bool = False) -> list[str]:
    """The words of a line: quotes and a minus in front are dropped (the line's kind says what the words mean), the search words OR,
    NOT and | too (in a phrase they are words like any other). A word with a colon (10:30, a typed from: or tag:) is text, never a
    field (the line's part says where it looks): quoted, it is found as written, as the search field finds it; inside a phrase the
    colon becomes a space, which the index reads the same."""
    if phrase:
        return [word for word in text.replace('"', " ").replace(":", " ").split() if word]
    words = [word.lstrip("-") for word in text.replace('"', " ").split()]
    return [f'"{word}"' if ":" in word else word for word in words if word and word not in ("OR", "NOT", "|")]


def _line_terms(line: SearchLine) -> tuple[list[list[str]], list[str]]:
    """What one line asks: the ways it can match (each a group of terms that must all match) and the terms it leaves out."""
    words = _words(line.text, phrase=line.kind == "exact")
    if not words:
        return [], []
    prefix = "" if line.part == "anywhere" else f"{line.part}:"
    if line.kind == "exact":
        return [[f'{prefix}"{" ".join(words)}"']], []
    if line.kind == "none":
        return [], [f"-{prefix}{word}" for word in words]
    if line.kind == "any":
        return [[f"{prefix}{word}"] for word in words], []
    return [[f"{prefix}{word}" for word in words]], []


def ways_count(lines: Sequence[SearchLine], match: str = "all") -> int:
    """How many alternatives the lines make (each Any line under all multiplies them)."""
    counts = [len(ways) for ways, _out in (_line_terms(line) for line in lines) if ways]
    if not counts:
        return 0
    return sum(counts) if match == "any" else _product(counts)


def _product(counts: Sequence[int]) -> int:
    total = 1
    for count in counts:
        total = min(total * count, MAXIMUM_WAYS + 1)  # counted only as far as the limit
    return total


def too_big(lines: Sequence[SearchLine], match: str = "all") -> bool:
    """Whether the lines make more than a search can take: too many alternatives, or words longer than the search field holds. Worked
    out by counting, never by building them (it runs at every key press)."""
    if ways_count(lines, match) > MAXIMUM_WAYS:
        return True
    found = [_line_terms(line) for line in lines]
    asked = [ways for ways, _out in found if ways]
    length = sum(len(term) + 1 for _ways, out in found for term in out)
    sizes = [sum(len(" ".join(way)) + 4 for way in ways) for ways in asked]  # each way with its " OR "
    if match == "any":
        length += sum(sizes)
    else:
        counts = [len(ways) for ways in asked]
        total = _product(counts)
        length += sum(size * (total // count) for size, count in zip(sizes, counts, strict=True))  # each line's ways in every alternative
    return length > MAXIMUM_QUERY


def build_query(lines: Sequence[SearchLine], match: str = "all") -> str:
    """The search words for the lines (D-040's language): with all, every line must match (a line with any of its words gives a
    choice inside it); with any, one line is enough; a None line always leaves its words out. Empty lines count for nothing."""
    if ways_count(lines, match) > MAXIMUM_WAYS:
        return ""  # never built: too_big says so first
    found = [_line_terms(line) for line in lines]
    excluded = [term for _ways, out in found for term in out]
    asked = [ways for ways, _out in found if ways]
    if not asked:
        ways: list[list[str]] = []
    elif match == "any":
        ways = [way for line_ways in asked for way in line_ways]
    else:
        ways = [[term for group in combination for term in group] for combination in itertools.product(*asked)]
    query = " OR ".join(" ".join(way) for way in ways)
    return " ".join(part for part in (query, " ".join(excluded)) if part)


def example(part: str, kind: str) -> str:
    """The grey example text of an empty line, which follows its choices."""
    if part in ("from", "to"):
        return "e.g. ann or ann@example.com" if part == "from" else "e.g. bob or bob@example.com"
    return {"any": "e.g. invoice receipt", "exact": "e.g. monthly invoice", "none": "e.g. newsletter"}.get(kind, "e.g. invoice march")


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def days(chosen: DateChoice, today: date) -> tuple[str | None, str | None]:
    """The first and the last day (YYYY-MM-DD, both counted) a date choice stands for, worked out from today for a span; None: no limit."""
    first, last = None, None
    choice = chosen.choice
    if choice in ("today", "yesterday", "tomorrow"):
        first = last = today + timedelta(days={"today": 0, "yesterday": -1, "tomorrow": 1}[choice])
    elif choice in ("last7", "last30"):
        first, last = today - timedelta(days=6 if choice == "last7" else 29), today
    elif choice in ("next7", "next30"):
        first, last = today, today + timedelta(days=6 if choice == "next7" else 29)
    elif choice == "this_month":
        first, last = today.replace(day=1), today
    elif choice == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        first = last.replace(day=1)
    elif choice == "this_year":
        first, last = today.replace(month=1, day=1), today
    elif choice == "last_year":
        first, last = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif choice == "between":
        first, last = _day(chosen.first), _day(chosen.last)
    elif choice == "before":  # the days before it, not the day itself
        day = _day(chosen.last)
        last = day - timedelta(days=1) if day is not None and day > date.min else None
    elif choice == "after":  # the days after it, not the day itself
        day = _day(chosen.first)
        first = day + timedelta(days=1) if day is not None and day < date.max else None
    if first is not None:
        first = min(max(first, FIRST_DAY), LAST_DAY)  # a day the search can ask for: After the last day finds nothing, never everything
    if last is not None:
        last = min(max(last, FIRST_DAY), LAST_DAY)
    if choice == "after" and first is None and _day(chosen.first) is not None:
        first = LAST_DAY
    if choice == "before" and last is None and _day(chosen.last) is not None:
        last, first = FIRST_DAY, LAST_DAY  # before the first day: nothing
    return (first.isoformat() if first else None), (last.isoformat() if last else None)


# --- what the saved searches file keeps ---------------------------------------------------------------------------------


def _choice_data(chosen: DateChoice) -> dict[str, object]:
    return {"choice": chosen.choice, "first": chosen.first, "last": chosen.last}


def form_data(form: SearchForm) -> dict[str, object]:
    """The form as plain values for the file."""
    return {
        "scope": form.scope.value, "match": form.match,
        "lines": [{"part": line.part, "kind": line.kind, "text": line.text} for line in form.lines],
        "received": _choice_data(form.received), "sent": _choice_data(form.sent), "minimum_kb": form.minimum_kb, "maximum_kb": form.maximum_kb,
        "unread": form.unread, "flagged": form.flagged, "attachments": form.attachments, "tag": form.tag,
        "snoozed": _choice_data(form.snoozed), "send_at": _choice_data(form.send_at),
    }


def _text(value: object) -> str:
    return value[:MAXIMUM_TEXT] if isinstance(value, str) else ""


def _one_of(value: object, choices: Sequence[tuple[str, str]], default: str) -> str:
    return value if isinstance(value, str) and value in (key for key, _label in choices) else default


def _choice_from(value: object, choices: Sequence[tuple[str, str]]) -> DateChoice:
    data: Mapping[str, object] = value if isinstance(value, Mapping) else {}
    first, last = data.get("first"), data.get("last")
    return DateChoice(
        _one_of(data.get("choice"), choices, "any"),
        first if isinstance(first, str) and _day(first) else None, last if isinstance(last, str) and _day(last) else None,
    )


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000 else 0


def form_from(value: object) -> SearchForm:
    """A form from what the file holds: anything unknown, missing or out of range takes its default (the file may be damaged, AGENTS 4.2)."""
    data: Mapping[str, object] = value if isinstance(value, Mapping) else {}
    scope = data.get("scope")
    given = data.get("lines")
    lines = [
        SearchLine(_one_of(line.get("part"), PARTS, "anywhere"), _one_of(line.get("kind"), KINDS, "all"), _text(line.get("text")))
        for line in (given if isinstance(given, list) else []) if isinstance(line, Mapping)
    ][:MAXIMUM_LINES]
    tag = data.get("tag")
    return SearchForm(
        scope=SearchScope(scope) if isinstance(scope, str) and scope in tuple(SearchScope) else DEFAULT_SCOPE,
        match=_one_of(data.get("match"), MATCHES, "all"),
        lines=tuple(lines) or (SearchLine(),),
        received=_choice_from(data.get("received"), PAST_CHOICES), sent=_choice_from(data.get("sent"), PAST_CHOICES),
        minimum_kb=_number(data.get("minimum_kb")), maximum_kb=_number(data.get("maximum_kb")),
        unread=data.get("unread") is True, flagged=data.get("flagged") is True, attachments=data.get("attachments") is True,
        tag=_text(tag) or None if isinstance(tag, str) else None,
        snoozed=_choice_from(data.get("snoozed"), FUTURE_CHOICES), send_at=_choice_from(data.get("send_at"), FUTURE_CHOICES),
    )
