import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from chrono_mail.core import ChronoMailError, CollectionKind
from chrono_mail.core.jsontext import load_json
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_calendar import _declined, _key
from chrono_mail.engine.commands_mail import SendParameters, mail_send
from chrono_mail.engine.meetingmail import organizing_account, your_addresses_lower
from chrono_mail.engine.pimaccess import open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand
from chrono_mail.engine.session import Engine
from chrono_mail.pim.ical import Event, Moment, Reminder, occurrences, parse_events
from chrono_mail.pim.times import local_zone
from chrono_mail.pimstore.index import ItemQuery
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "commands_reminders.py 4.4.536"

# --- Version history ----------------------------------------------------
# v4.4.536: Initial version: reminders come due while Chrono Mail runs, as a notification or an e-mail to your own address (D-313)

LATE: timedelta = timedelta(minutes=5)  # a reminder older than this was missed while the program was closed: it still counts only for what has not begun
MOST_ITEMS: int = 1_000_000  # the query needs a number; every event that is still to come counts


@dataclass(frozen=True, slots=True)
class ReminderNote:
    """A reminder for the person at the screen."""

    summary: str
    when: str
    location: str


@dataclass(frozen=True, slots=True)
class ReminderCheck:
    notes: tuple[ReminderNote, ...] = ()
    mails: int = 0


@dataclass(frozen=True, slots=True)
class _Due:
    account: str
    reminder: Reminder
    note: ReminderNote


def _read_checked(engine: Engine) -> datetime | None:
    try:
        data = load_json(engine.directories.reminders_file.read_bytes())
        moment = datetime.fromisoformat(str(data.get("checked", ""))) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _write_checked(engine: Engine, moment: datetime) -> None:
    path = engine.directories.reminders_file
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_file_atomically(path, (json.dumps({"checked": moment.isoformat()}) + "\n").encode("utf-8"), engine.durable)


def _when(moment: Moment, zone: tzinfo) -> str:
    if not isinstance(moment, datetime):
        return moment.isoformat()
    return _key(moment, zone).astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _trigger(reminder: Reminder, start: datetime, end: datetime) -> datetime | None:
    """The moment of a reminder of one occurrence; a reminder at a fixed time is not one of an occurrence."""
    if reminder.minutes_before is None:
        return None
    return (end if reminder.relative_to_end else start) - timedelta(minutes=reminder.minutes_before)


def _counts(trigger: datetime, start: datetime, since: datetime, now: datetime) -> bool:
    return since < trigger <= now and (now - trigger <= LATE or start > now)


def _due_in(text: str, account: str, since: datetime, now: datetime, zone: tzinfo, yours: Collection[str]) -> list[_Due]:
    """The reminders of one calendar object that came due after `since`: per occurrence, a changed occurrence with its own reminders."""
    found: list[_Due] = []

    def due(event: Event, reminder: Reminder, start: Moment) -> None:
        if reminder.action in ("DISPLAY", "AUDIO", "EMAIL") and not _declined(event, yours):
            found.append(_Due(account, reminder, ReminderNote(event.summary, _when(start, zone), event.location)))

    events = [event for series in parse_events(text) for event in (series.master, *series.overrides)]
    lengths = [reminder.minutes_before for event in events for reminder in event.reminders if reminder.minutes_before is not None]
    fixed: set[tuple[datetime, str]] = set()  # a changed occurrence carries the alarms of its event: one moment reminds once
    for event in events:
        for reminder in event.reminders:
            if reminder.at is not None and (reminder.at, reminder.action) not in fixed and _counts(_key(reminder.at, zone), _key(event.start, zone), since, now):
                fixed.add((reminder.at, reminder.action))
                due(event, reminder, event.start)
    if lengths:
        for occurrence in occurrences([text], since + timedelta(minutes=min(lengths)), now + timedelta(minutes=max(lengths) + 1), zone):
            start, end = _key(occurrence.start, zone), _key(occurrence.end, zone)
            for reminder in occurrence.event.reminders:
                trigger = _trigger(reminder, start, end)
                if trigger is not None and _counts(trigger, start, since, now):
                    due(occurrence.event, reminder, occurrence.start)
    return found


def _send(engine: Engine, due: _Due) -> bool:
    """The e-mail of a reminder, to your own address, through the Outbox: offline it goes later. False: this computer leaves it to another."""
    own = organizing_account(engine, due.account)
    if own is None:
        raise UsageError("No account has an address to send a reminder to")
    if not own.sends_reminder_emails:
        return False
    note = due.note
    lines = [note.summary, f"When: {note.when}", *([f"Where: {note.location}"] if note.location else [])]
    mail_send(engine, SendParameters(account=own.account.account_id, to=(own.account.email_address,), subject=f"Reminder: {note.summary}", body="\n".join(lines) + "\n"))
    return True


def reminder_check(engine: Engine, _parameters: NoParameters) -> ReminderCheck:
    """What came due since the last look. A notification is for the window; an e-mail is sent here, and one that cannot go comes back as a notification,
    so the reminder is never lost. Only moments after the last look count, so nothing comes twice, also after a restart."""
    now = datetime.now(UTC)
    since = _read_checked(engine)
    if since is None or now <= since:
        _write_checked(engine, now)  # the first look ever, or a clock put back: from now on
        return ReminderCheck()
    zone = local_zone()
    yours = your_addresses_lower(engine)
    found: list[_Due] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            calendars = tuple(info.collection_id for info in store.collections(CollectionKind.EVENTS))
            if not calendars:
                continue
            for item in store.index.query(ItemQuery(CollectionKind.EVENTS, calendars, start=int((since - timedelta(days=1)).timestamp()), limit=MOST_ITEMS)):
                try:
                    text = store.get(item.collection, item.item_id)
                    if "VALARM" in text:
                        found.extend(_due_in(text, account, since, now, zone, yours))
                except Exception:  # noqa: BLE001 - a damaged item fails only itself, the reminders of all the others still come (OR-3)
                    continue
    _write_checked(engine, now)  # first: a reminder is rather missed than mailed twice after a crash
    notes: list[ReminderNote] = []
    mails = 0
    for due in found:
        if due.reminder.action != "EMAIL":
            notes.append(due.note)
            continue
        try:
            mails += _send(engine, due)
        except ChronoMailError:
            notes.append(due.note)
    return ReminderCheck(tuple(notes), mails)


REMINDER_CHECK: TypedCommand[NoParameters, ReminderCheck] = TypedCommand("reminder.check", NoParameters, ReminderCheck)


def register(registry: CommandRegistry) -> None:
    registry.register(REMINDER_CHECK.id, "Give the reminders that came due, and send their e-mails", NoParameters, reminder_check, doing="Looking for reminders")
