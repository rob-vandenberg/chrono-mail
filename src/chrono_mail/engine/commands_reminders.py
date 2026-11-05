import json
import threading
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import islice

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
from chrono_mail.pim.ical import MAXIMUM_OCCURRENCES, Event, Moment, Reminder, Task, occurrences_from, parse_events, parse_tasks, task_occurrences_from
from chrono_mail.pim.times import local_zone
from chrono_mail.pimstore import DueReminder
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "commands_reminders.py 4.4.550"

# --- Version history ----------------------------------------------------
# v4.4.550: a cancelled event or a cancelled date of a series gives no reminder, the rest of the series does (D-316)
# v4.4.545: a task reminds too, through the same look: minutes before its due time or at a fixed time; a finished or cancelled task does not (D-316)
# v4.4.544: the look asks the index which reminders are due and reads only those; no limit in days remains, an event of any age reminds (D-316); a floating time west of Greenwich no longer falls before the period looked at
# v4.4.537: the look reads no event that ended over a month ago: the start of the query counted only with an end, so it read every event ever at every look
# v4.4.536: Initial version: reminders come due while Chrono Mail runs, as a notification or an e-mail to your own address (D-313)

LATE: timedelta = timedelta(minutes=5)  # a reminder older than this was missed while the program was closed: it still counts only for what has not begun
_LOOKING = threading.Lock()  # one look at a time: the first look after many events were stored takes longer than the time to the next one, and two looks would give every reminder twice
FIRED: tuple[str, ...] = ("DISPLAY", "AUDIO", "EMAIL")  # the kinds of alarm Chrono Mail gives; any other in an event from elsewhere stays in it and gives nothing


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


@dataclass(frozen=True, slots=True)
class _Part:
    """What an event or a task, or a changed occurrence of one, holds for the check. `begins`: when a reminder of it counts no more after it was missed:
    the start of an event, the due time of a task. None: a task without a due time, which is never past."""

    summary: str
    location: str
    begins: Moment | None
    reminders: tuple[Reminder, ...]
    silent: bool = False  # it gives nothing, yet its moments stay as they are: a date you declined


@dataclass(frozen=True, slots=True)
class _Time:
    """One time a part happens: an occurrence of an event, a due time of a task. `order` is what the times come sorted by: the start, also of a task."""

    part: _Part
    moment: Moment
    order: datetime
    start: datetime
    end: datetime


def _event_part(event: Event, yours: Collection[str]) -> _Part:
    return _Part(event.summary, event.location, event.start, () if event.inert else event.reminders, _declined(event, yours))


def _task_part(task: Task) -> _Part:
    return _Part(task.summary, task.location, task.due, () if task.inert else task.reminders)


def _parts(text: str, kind: CollectionKind, yours: Collection[str] = ()) -> list[_Part]:
    if kind is CollectionKind.TASKS:
        return [_task_part(task) for task in parse_tasks(text)]
    return [_event_part(event, yours) for series in parse_events(text) for event in (series.master, *series.overrides)]


def _times(text: str, kind: CollectionKind, after: datetime, zone: tzinfo, yours: Collection[str] = ()) -> Iterator[_Time]:
    """Each time a calendar object happens from a moment on, in the order of `order`. Minutes before a task always count from its due time, what the task
    editor says, also in a reminder from elsewhere that names the start. A floating time is read in the zone of the moment given."""
    if kind is CollectionKind.TASKS:
        for task in task_occurrences_from(text, after.astimezone(zone)):
            if task.due is not None:
                due = _key(task.due, zone)
                yield _Time(_task_part(task), task.due, _key(task.start or task.due, zone), due, due)
        return
    for occurrence in occurrences_from(text, after.astimezone(zone)):
        start = _key(occurrence.start, zone)
        yield _Time(_event_part(occurrence.event, yours), occurrence.start, start, start, _key(occurrence.end, zone))


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


def _when(moment: Moment | None, zone: tzinfo) -> str:
    if moment is None:
        return ""
    if not isinstance(moment, datetime):
        return moment.isoformat()
    return _key(moment, zone).astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _trigger(reminder: Reminder, start: datetime, end: datetime) -> datetime | None:
    """The moment of a reminder of one occurrence; a reminder at a fixed time is not one of an occurrence."""
    if reminder.minutes_before is None:
        return None
    return (end if reminder.relative_to_end else start) - timedelta(minutes=reminder.minutes_before)


def _counts(trigger: datetime, begins: datetime | None, since: datetime, now: datetime) -> bool:
    return since < trigger <= now and (now - trigger <= LATE or begins is None or begins > now)


def _due_in(text: str, account: str, since: datetime, now: datetime, zone: tzinfo, yours: Collection[str], kind: CollectionKind = CollectionKind.EVENTS) -> list[_Due]:
    """The reminders of one calendar object that came due after `since`: per occurrence, a changed occurrence with its own reminders."""
    found: list[_Due] = []

    def due(part: _Part, reminder: Reminder, moment: Moment | None) -> None:
        if reminder.action in FIRED and not part.silent:
            found.append(_Due(account, reminder, ReminderNote(part.summary, _when(moment, zone), part.location)))

    parts = _parts(text, kind, yours)
    lengths = [reminder.minutes_before for part in parts for reminder in part.reminders if reminder.minutes_before is not None]
    fixed: set[tuple[datetime, str]] = set()  # a changed occurrence carries the alarms of its event: one moment reminds once
    for part in parts:
        begins = None if part.begins is None else _key(part.begins, zone)
        for reminder in part.reminders:
            if reminder.at is not None and (reminder.at, reminder.action) not in fixed and _counts(_key(reminder.at, zone), begins, since, now):
                fixed.add((reminder.at, reminder.action))
                due(part, reminder, part.begins)
    if lengths:
        until = now + timedelta(minutes=max(lengths) + 1)
        for time in islice(_times(text, kind, since + timedelta(minutes=min(lengths)), zone, yours), MAXIMUM_OCCURRENCES):
            if time.order >= until:
                break  # this one and every later one come after the look
            for reminder in time.part.reminders:
                trigger = _trigger(reminder, time.start, time.end)
                if trigger is not None and _counts(trigger, time.start, since, now):
                    due(time.part, reminder, time.moment)
    return found


def _next_moment(text: str, after: datetime, zone: tzinfo, kind: CollectionKind = CollectionKind.EVENTS) -> datetime | None:
    """When the index has to give this calendar object to the check again: no reminder of it comes between `after` and that moment. None: none comes any
    more. A series without an end stops at the first reminder found. One that goes on without any (only a changed occurrence holds reminders) is
    walked MAXIMUM_OCCURRENCES at a time: the moment is then where the walk goes on, so no reminder however far ahead is lost and no look takes long."""
    reminders = [reminder for part in _parts(text, kind) for reminder in part.reminders if reminder.action in FIRED]
    moments = [_key(reminder.at, zone) for reminder in reminders if reminder.at is not None]
    best = min((moment for moment in moments if moment > after), default=None)
    lengths = [reminder.minutes_before for reminder in reminders if reminder.minutes_before is not None]
    if not lengths:
        return best
    earliest = timedelta(minutes=max(lengths))  # no reminder of an occurrence comes longer than this before its start
    walked = 0
    for time in _times(text, kind, after + timedelta(minutes=min(lengths)), zone):
        if best is not None and time.order - earliest >= best:
            break  # the times come in the order of their start: this one and every later one remind after `best`
        for reminder in time.part.reminders:
            trigger = _trigger(reminder, time.start, time.end) if reminder.action in FIRED else None
            if trigger is not None and trigger > after and (best is None or trigger < best):
                best = trigger
        walked += 1
        if walked >= MAXIMUM_OCCURRENCES:
            return best if best is not None else time.order - earliest
    return best


def _send(engine: Engine, due: _Due) -> bool:
    """The e-mail of a reminder, to your own address, through the Outbox: offline it goes later. False: this computer leaves it to another."""
    own = organizing_account(engine, due.account)
    if own is None:
        raise UsageError("No account has an address to send a reminder to")
    if not own.sends_reminder_emails:
        return False
    note = due.note
    lines = [note.summary, *([f"When: {note.when}"] if note.when else []), *([f"Where: {note.location}"] if note.location else [])]
    mail_send(engine, SendParameters(account=own.account.account_id, to=(own.account.email_address,), subject=f"Reminder: {note.summary}", body="\n".join(lines) + "\n"))
    return True


def reminder_check(engine: Engine, _parameters: NoParameters) -> ReminderCheck:
    if not _LOOKING.acquire(blocking=False):
        return ReminderCheck()  # the look before this one is still at it: what comes due meanwhile is for the next
    try:
        return _look(engine)
    finally:
        _LOOKING.release()


def _look(engine: Engine) -> ReminderCheck:
    """What came due since the last look. A notification is for the window; an e-mail is sent here, and one that cannot go comes back as a notification,
    so the reminder is never lost. Only moments after the last look count, so nothing comes twice, also after a restart. The index says which events and
    tasks to read: those with a reminder due and those stored since the last look; for each it then gets the next moment, from the item itself."""
    now = datetime.now(UTC)
    since = _read_checked(engine)
    if since is None or now <= since:
        _write_checked(engine, now)  # the first look ever, or a clock put back: from now on
        return ReminderCheck()
    zone = local_zone()
    yours = your_addresses_lower(engine)
    found: list[_Due] = []
    worked_out: dict[str, list[tuple[DueReminder, int | None]]] = {}
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            store.index.keep_reminder_zone(str(zone))
            for item in store.index.reminders_due(int(now.timestamp())):
                if engine.stop_event.is_set():
                    return ReminderCheck()  # the program ends and waits for nobody: nothing is written yet, the next start finds all of it again
                try:
                    text = store.get(item.collection, item.item_id)
                    moment = None
                    if "VALARM" in text:
                        found.extend(_due_in(text, account, since, now, zone, yours, item.kind))
                        moment = _next_moment(text, now, zone, item.kind)
                    worked_out.setdefault(account, []).append((item, int(moment.timestamp()) if moment is not None else None))
                except Exception:  # noqa: BLE001 - a damaged item fails only itself, the reminders of all the others still come (OR-3)
                    continue
    _write_checked(engine, now)  # first: a reminder is rather missed than mailed twice after a crash
    for account, moments in worked_out.items():  # after the last look moved: read again after a crash, what came due then counts no more
        try:
            with open_pim(engine, account) as store:
                store.index.set_reminders(moments)
        except Exception:  # noqa: BLE001 - these stay due and are read again at the next look; the reminders found still come
            continue
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
