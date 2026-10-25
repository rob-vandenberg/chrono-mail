import threading
from datetime import datetime, timedelta

from chrono_mail.pim.times import local_time, moment_at
from chrono_mail.engine.registry import UsageError
from chrono_mail.sync.imap_sync import SCHEDULED

# --- Version ------------------------------------------------------------
__version__ = "schedule.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: REPEAT_LOCK (was commands_schedule._REPEAT_LOCK), re-entrant: what replaces a scheduled message takes it too (D-262)
# v3.6.349: the group of a snooze or send time without the platform's conversion too; a send time is shown without the platform's conversion, which Windows refuses past 3000 (RV-204)
# v2.2.314: the Outbox is a timed folder too: a delayed send waits there for its moment (B-21)
# v1.1.206: snoozed.json is gone: a snoozed message's time and origin are in its properties file (D-176)
# v1.1.200: SCHEDULED_FOLDER, where scheduled and repeating messages wait (made when first used); TIMED_FOLDERS
# v0.5.165: SNOOZED_FILE_LOCK around reading and replacing snoozed.json: every read, also the ones outside a snooze or wake (a fact, a list), never meets a replace (Windows said "Access is denied")
# v0.2.101: SNOOZED_LOCK: the record of snoozed messages is read, changed and written by one command at a time
# v0.2.100: SNOOZED_FOLDER and SNOOZED_FILE moved here; snooze_group for the group headers of the Snoozed folder
# v0.0.86: Ready-made snooze times (tomorrow, later this week, this weekend, next week)
# v0.0.34: Initial times for sending later and snoozing

SNOOZED_FOLDER: str = "Snoozed"
SCHEDULED_FOLDER: str = SCHEDULED  # scheduled and repeating messages wait here for their time; local only, made when first used
TIMED_FOLDERS: tuple[str, ...] = (SNOOZED_FOLDER, SCHEDULED_FOLDER, "Outbox")  # each message leaves at a time: the list shows and sorts by it (the Outbox: a delayed send)
SNOOZED_LOCK = threading.RLock()  # snooze and wake change the snooze records (in the properties files): one command at a time
REPEAT_LOCK = threading.RLock()  # two checks at the same moment must not send the same message twice; what replaces a scheduled message
# takes it too, before the Outbox lock (a repeating send holds it from its timetable to its send); held again by the same command (Unschedule)
SNOOZE_GROUPS: tuple[tuple[str, str], ...] = (("today", "Today"), ("tomorrow", "Tomorrow"), ("this_week", "This week"), ("next_week", "Next week"), ("later", "Later"))
TOLERANCE_SECONDS: int = 60
MAXIMUM_SECONDS_AHEAD: int = 5 * 366 * 86_400
SNOOZE_HOUR: int = 9  # the ready-made snooze times are at 09:00
SATURDAY: int = 5  # datetime.weekday(): Monday is 0


def snooze_presets(now: datetime) -> list[tuple[str, datetime]]:
    """The ready-made snooze times from a local moment, each at 09:00: tomorrow; later this week (two days ahead, left out
    when that is Saturday or later); this weekend (the coming Saturday, left out on Saturday and Sunday); next week (the coming Monday)."""
    day = now.replace(hour=SNOOZE_HOUR, minute=0, second=0, microsecond=0)
    weekday = now.weekday()
    presets = [("Tomorrow", day + timedelta(days=1))]
    if weekday + 2 < SATURDAY:
        presets.append(("Later this week", day + timedelta(days=2)))
    if weekday < SATURDAY:
        presets.append(("This weekend", day + timedelta(days=SATURDAY - weekday)))
    presets.append(("Next week", day + timedelta(days=7 - weekday)))
    return presets


def snooze_group(until: int | None, now: datetime) -> str:
    """The group of a snoozed message by when it comes back (local time): today (also a time that has passed), tomorrow,
    this week (to Sunday), next week, later. A message without a record is later."""
    if until is None:
        return "later"
    today = local_time(now).date()
    day = moment_at(until).date()  # a send time read from a message: never the platform's conversion (RV-204)
    next_monday = today + timedelta(days=7 - today.weekday())
    if day <= today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if day < next_monday:
        return "this_week"
    if day < next_monday + timedelta(days=7):
        return "next_week"
    return "later"


def parse_when(text: str, now: float) -> int:
    """A local time such as 2026-10-11T09:00 (or with a space) as seconds since 1970. It must not be in the past."""
    try:
        moment = datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        raise UsageError("A time is written like 2026-10-11T09:00") from None
    epoch = int(moment.astimezone().timestamp())
    if epoch < now - TOLERANCE_SECONDS:
        raise UsageError("That time has passed already")
    if epoch > now + MAXIMUM_SECONDS_AHEAD:
        raise UsageError("That time is too far ahead")
    return epoch


def describe_when(epoch: int) -> str:
    return moment_at(epoch).strftime("%Y-%m-%d %H:%M")  # never the platform's conversion: Windows refuses times past 3000 (RV-204)
