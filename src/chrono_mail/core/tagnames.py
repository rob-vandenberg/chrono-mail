"""The identifier of a tag (D-341). A tag the person makes is known by its name. A tag that comes from a Google calendar is known by its name, a
vertical bar and the calendar's Google id ("Rood|ann@gmail.com"), so two calendars' colours of one name stay two tags. The bar and the id are
never shown: on screen such a tag is "Rood (Esther)", the calendar's name in brackets."""

# --- Version ------------------------------------------------------------
__version__ = "tagnames.py 4.4.602"

# --- Version history ----------------------------------------------------
# v4.4.602: Initial version: a tag's identifier holds the Google calendar it belongs to (D-341)

BAR: str = "|"
BAR_REFUSED: str = "A tag's name cannot hold a vertical bar (|)."


def calendar_tag(name: str, calendar: str) -> str:
    """The identifier of the tag a Google calendar gives this name."""
    return f"{name}{BAR}{calendar}"


def is_calendar_tag(tag: str) -> bool:
    return BAR in tag


def tag_parts(tag: str) -> tuple[str, str]:
    """(the name, the calendar's Google id); the id is empty for a tag the person made. Split at the last bar: a calendar's id never holds one,
    a name Google gives may."""
    name, _, calendar = tag.rpartition(BAR)
    return (name, calendar) if BAR in tag else (tag, "")


def tag_name(tag: str) -> str:
    """The name alone, without the calendar it belongs to."""
    return tag_parts(tag)[0]


def shown_tag(name: str, calendar_name: str) -> str:
    """How a calendar's tag is shown: its name, then the calendar's name in brackets."""
    return f"{name} ({calendar_name})" if calendar_name else name
