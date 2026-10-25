import hashlib
from datetime import UTC, datetime, timedelta

from PySide6.QtGui import QColor

from chrono_mail.core import shown_name
from chrono_mail.pim.times import local_time
from chrono_mail.core.colours import AVATAR_HUES, AVATAR_LIGHTNESS, AVATAR_SATURATION

# --- Version ------------------------------------------------------------
__version__ = "formatting.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: dates on this computer's clock through the named zone: a mail dated before 1970 or after 3000 shows on Windows too (RV-201)
# v3.5.346: local_zone_now removed: a fixed offset of today; the calendar uses the named zone (RV-15)
# v1.1.172: The avatar colours' hues, saturation and lightness are in the colour file (D-158)
# v0.5.166: display_name uses the one rule of core (shown_name), the same as the index
# v0.2.100: coming_date: a time still to come (time today, weekday and time this week, else the date and time)
# v0.0.64: The calendar section
# v0.0.14: avatar colours from 72 hues with guaranteed contrast
# v0.0.13: Initial display helpers



def display_name(sender: str) -> str:
    """The name part of 'Name <address>', or the address when there is no name (the one rule the index uses too)."""
    return shown_name(sender)


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
    local = local_time(moment)
    current = local_time(now or datetime.now(UTC))
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
    local = local_time(moment)
    current = local_time(now or datetime.now(UTC))
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
    return "" if moment is None else local_time(moment).strftime("%A %d %B %Y, %H:%M")


def file_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
