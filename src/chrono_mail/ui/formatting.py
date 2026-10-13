import hashlib
from datetime import UTC, datetime, timedelta, tzinfo

from PySide6.QtGui import QColor

# --- Version ------------------------------------------------------------
__version__ = "formatting.py 0.2.100"

# --- Version history ----------------------------------------------------
# v0.2.100: coming_date: a time still to come (time today, weekday and time this week, else the date and time)
# v0.0.64: The calendar section
# v0.0.14: avatar colours from 72 hues with guaranteed contrast
# v0.0.13: Initial display helpers

AVATAR_HUES: int = 72  # one colour per 5 degrees of the colour wheel
AVATAR_SATURATION: float = 0.55
AVATAR_LIGHTNESS: float = 0.30  # dark enough for white initials: contrast 4.7 or better on every hue


def display_name(sender: str) -> str:
    """The name part of 'Name <address>', or the address when there is no name."""
    if "<" in sender and sender.rstrip().endswith(">"):
        name = sender[: sender.rindex("<")].strip().strip('"')
        return name or sender[sender.rindex("<") + 1 : -1].strip()
    return sender.strip()


def initials(name: str) -> str:
    """One or two capital letters for an avatar."""
    words = [word for word in "".join(character if character.isalnum() else " " for character in name).split() if word]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def avatar_colour(text: str) -> QColor:
    """The same colour for the same sender every time, from 72 hues that all take white initials well."""
    digest = hashlib.sha256(text.lower().encode("utf-8", "replace")).digest()
    hue = int.from_bytes(digest[:2], "big") % AVATAR_HUES
    return QColor.fromHslF(hue / AVATAR_HUES, AVATAR_SATURATION, AVATAR_LIGHTNESS)


def coming_date(moment: datetime | None, now: datetime | None = None) -> str:
    """A time still to come, short: the time today, the weekday and time in the next six days, otherwise the date and time; local time."""
    if moment is None:
        return ""
    local = moment.astimezone()
    current = (now or datetime.now(UTC)).astimezone()
    if local.date() <= current.date():
        return local.strftime("%H:%M")
    if local.date() - current.date() < timedelta(days=7):
        return local.strftime("%a %H:%M")
    if local.year == current.year:
        return local.strftime("%d %b %H:%M")
    return local.strftime("%d %b %Y %H:%M")


def short_date(moment: datetime | None, now: datetime | None = None) -> str:
    """Time today, 'Yesterday', the weekday this week, otherwise the date; always in local time."""
    if moment is None:
        return ""
    local = moment.astimezone()
    current = (now or datetime.now(UTC)).astimezone()
    if local.date() == current.date():
        return local.strftime("%H:%M")
    if local.date() == (current - timedelta(days=1)).date():
        return "Yesterday"
    if timedelta(0) < current - local < timedelta(days=6):
        return local.strftime("%A")
    if local.year == current.year:
        return local.strftime("%d %b")
    return local.strftime("%d %b %Y")


def long_date(moment: datetime | None) -> str:
    return "" if moment is None else moment.astimezone().strftime("%A %d %B %Y, %H:%M")


def file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def local_zone_now() -> tzinfo:
    """The time zone of this computer as it is now (for views that place events on a clock)."""
    return datetime.now().astimezone().tzinfo or UTC
