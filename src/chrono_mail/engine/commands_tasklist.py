from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.commands_calendar import _reminder
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.ical import Moment, Task, advance_task, complete_task, new_uid, parse_tasks, reopen_task, tasks_to_text
from chrono_mail.pim.times import format_moment, local_zone, parse_moment, zone_named
from chrono_mail.pimstore import IndexedItem, ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_tasklist.py 1.9.273"

# --- Version history ----------------------------------------------------
# v1.9.273: R-19: typed task.show, -add, -edit, -complete, -reopen, -delete, -move
# v1.1.225: Every change of an item goes through item_writer: on its server first when it has one (R-23, D-194)
# v1.1.205: task.places: where every task stands, from the index alone, never cut (R-02); task.list for several lists (lists) and for given tasks (items), open before done, ordered on the index and only the tasks of the answer read; typed (TASK_LIST, TASK_PLACES)
# v0.5.159: task.list for one task (item): its row and its place in what was asked for; importing tasks is a big job (one fact at its end)
# v0.0.63: The tasks section
# v0.0.60: Initial task commands

MAXIMUM_ROWS: int = 5_000  # the rows of one task.list answer by limit (the command line pages with offset)
EVERY: int = 10**6  # every item of one account (a list is never cut below this)
LIST_FIELDS: tuple[str, ...] = ("reminder", "tag")


@dataclass(frozen=True, slots=True)
class TaskRow:
    item: str
    account: str
    collection: str
    list_name: str
    colour: str
    summary: str
    due: str
    start: str
    priority: int
    percent: int
    status: str
    done: bool
    overdue: bool
    repeats: bool
    tags: tuple[str, ...]
    parent: str


@dataclass(frozen=True, slots=True)
class TaskInfo:
    item: str
    account: str
    collection: str
    uid: str
    summary: str
    description: str
    location: str
    start: str
    due: str
    completed: str
    percent: int
    status: str
    priority: int
    rrule: str
    tags: tuple[str, ...]
    reminders: tuple[int | None, ...]  # minutes before the due time (None: at a fixed time)
    parent: str
    url: str
    done: bool
    overdue: bool


def _overdue(task: Task) -> bool:
    if task.done or task.due is None:
        return False
    if isinstance(task.due, datetime):
        due = task.due if task.due.tzinfo else task.due.replace(tzinfo=local_zone())
        return due < datetime.now(local_zone())
    return task.due < date.today()


def _info(item: str, account: str, collection: str, task: Task) -> TaskInfo:
    return TaskInfo(
        item, account, collection, task.uid, task.summary, task.description, task.location, format_moment(task.start), format_moment(task.due), format_moment(task.completed),
        task.percent, task.status, task.priority, task.rrule, task.categories, tuple(r.minutes_before for r in task.reminders), task.parent, task.url, task.done, _overdue(task),
    )


def _single(text: str) -> Task:
    tasks = parse_tasks(text)
    if not tasks:
        raise UsageError("This task cannot be read (it is kept as it is)")
    return tasks[0]


# --- reading ------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParameters:
    list: str | None = field(default=None, metadata=option("Only this task list (default: all)"))
    lists: tuple[str, ...] = field(default=(), metadata=option("Only these task lists (repeat; default: all)"))
    state: Literal["open", "done", "all"] = field(default="open", metadata=option("open (not done), done or all"))
    due_before: str | None = field(default=None, metadata=option("Only tasks due before this day (2026-10-12), in your local time"))
    text: str = field(default="", metadata=option("Words to find"))
    tag: str = field(default="", metadata=option("Only tasks with this tag"))
    limit: int = field(default=500, metadata=option("How many at most"))
    offset: int = field(default=0, metadata=option("How many to skip"))
    item: str = field(default="", metadata=option("Only this task: its row and its place when it is among what was asked for (a list puts a changed task at its place)"))
    items: tuple[str, ...] = field(default=(), metadata=option("Only these tasks (repeat): their rows in the order of the list, those among what was asked for (a page of a list as it comes into view)"))


@dataclass(frozen=True, slots=True)
class TaskList:
    total: int
    tasks: tuple[TaskRow, ...]
    active: int  # open tasks in what was asked for
    completed: int
    position: int | None = None  # with item: its place in what was asked for (None when it is not there)


@dataclass(frozen=True, slots=True)
class PlacesParameters:
    list: str | None = field(default=None, metadata=option("Only this task list (default: all)"))
    lists: tuple[str, ...] = field(default=(), metadata=option("Only these task lists (repeat; default: all)"))
    state: Literal["open", "done", "all"] = field(default="open", metadata=option("open (not done), done or all"))
    due_before: str | None = field(default=None, metadata=option("Only tasks due before this day (2026-10-12), in your local time"))
    text: str = field(default="", metadata=option("Words to find"))
    tag: str = field(default="", metadata=option("Only tasks with this tag"))
    item: str = field(default="", metadata=option("Only this task: its place when it is among what was asked for (a list puts a changed task at its place)"))


@dataclass(frozen=True, slots=True)
class TaskPlace:
    """Where a task stands in a list, from the index alone (no task is read): enough to group, count, select and act on it.
    Its row is asked for (task.list with items) when it comes into view."""

    item: str
    account: str
    collection: str
    done: bool
    due_day: str  # the day it is due in this computer's time (2026-10-12), "" without a due date
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskPlaces:
    total: int
    places: tuple[TaskPlace, ...]  # every task asked for, in the order of the list: never cut
    active: int  # open tasks in what was asked for
    completed: int
    position: int | None = None  # with item: its place in what was asked for (None when it is not there)


type _Found = tuple[tuple[bool, int, int, str, str], str, IndexedItem]  # the order, the account, the item


def _order(item: IndexedItem) -> tuple[bool, int, int, str, str]:
    """The order of a list of tasks: open before done, then by due date (none last), priority (none as the lowest), summary."""
    return (item.done, item.due if item.due is not None else 10**12, item.priority if item.priority else 10, item.title.casefold(), item.item_id)


def _due_day(item: IndexedItem) -> str:
    """The day a task is due, in this computer's time. A date or a time without a zone is kept as if it were UTC (the index's
    floating times); the index has one floating mark for the due and the start together, so a zoned due beside a start
    without a zone is read as wall-clock time (at worst a day off near midnight)."""
    if item.due is None:
        return ""
    moment = datetime.fromtimestamp(item.due, UTC) if item.floating else datetime.fromtimestamp(item.due, local_zone())
    return moment.date().isoformat()


def _matching(engine: Engine, parameters: ListParameters | PlacesParameters) -> tuple[list[_Found], int, int]:
    """Every task asked for, in the order of the list, from the index alone (with items: only those tasks); and how many of
    the tasks asked for are open and done without the state asked for."""
    cutoff: int | None = None
    if parameters.due_before:
        try:
            moment = parse_moment(parameters.due_before)
        except ValueError as error:
            raise UsageError(f"due_before: {error}") from None
        day = moment.date() if isinstance(moment, datetime) else moment
        cutoff = int(datetime(day.year, day.month, day.day, tzinfo=local_zone()).timestamp())
    found: list[_Found] = []
    active = completed = 0
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            lists = [info.collection_id for info in store.collections(CollectionKind.TASKS)]
            if parameters.list is not None:
                chosen = (parameters.list,) if parameters.list in lists else ()
            else:
                chosen = tuple(collection for collection in lists if not parameters.lists or collection in parameters.lists)
            if not chosen:
                continue
            base = ItemQuery(CollectionKind.TASKS, chosen, parameters.text, parameters.tag, due_before=cutoff, limit=EVERY)
            active += store.index.count(replace(base, only_open=True))
            completed += store.index.count(replace(base, only_done=True))
            query = replace(base, only_open=parameters.state == "open", only_done=parameters.state == "done", items=parameters.items if isinstance(parameters, ListParameters) else ())
            found.extend((_order(item), account, item) for item in store.index.query(query))
    found.sort(key=lambda entry: entry[0])
    return found, active, completed


def _rows(engine: Engine, chosen: list[_Found]) -> list[TaskRow]:
    """The rows of these tasks, in the same order: only these tasks are read."""
    per_account: dict[str, list[IndexedItem]] = {}
    for _, account, item in chosen:
        per_account.setdefault(account, []).append(item)
    rows: dict[str, TaskRow] = {}
    for account, items in per_account.items():
        with open_pim(engine, account) as store:
            lists = {info.collection_id: info for info in store.collections(CollectionKind.TASKS)}
            for item in items:
                task = _single(store.get(item.collection, item.item_id))
                info = lists[item.collection]
                rows[item.item_id] = TaskRow(
                    item.item_id, account, item.collection, info.name, info.colour, task.summary, format_moment(task.due), format_moment(task.start), task.priority, task.percent,
                    task.status, task.done, _overdue(task), bool(task.rrule), task.categories, task.parent,
                )
    return [rows[item.item_id] for _, _, item in chosen if item.item_id in rows]


def task_list(engine: Engine, parameters: ListParameters) -> TaskList:
    """Tasks in the order of the list: open before done, then by due date (tasks without one last), then priority. Only the
    tasks of the answer are read."""
    found, active, completed = _matching(engine, parameters)
    if parameters.item:
        place = next((number for number, (_, _, item) in enumerate(found) if item.item_id == parameters.item), None)
        return TaskList(len(found), tuple(_rows(engine, [found[place]])) if place is not None else (), active, completed, place)
    if parameters.items:
        wanted = set(parameters.items)
        return TaskList(len(found), tuple(_rows(engine, [entry for entry in found if entry[2].item_id in wanted])), active, completed)
    limit = max(1, min(parameters.limit, MAXIMUM_ROWS))
    return TaskList(len(found), tuple(_rows(engine, found[parameters.offset : parameters.offset + limit])), active, completed)


def task_places(engine: Engine, parameters: PlacesParameters) -> TaskPlaces:
    """Where every task asked for stands in the list (from the index alone, never cut); the window asks for the rows as they
    come into view."""
    found, active, completed = _matching(engine, parameters)
    places = [TaskPlace(item.item_id, account, item.collection, item.done, _due_day(item), item.tags) for _, account, item in found]
    if parameters.item:
        place = next((number for number, entry in enumerate(places) if entry.item == parameters.item), None)
        return TaskPlaces(len(places), (places[place],) if place is not None else (), active, completed, place)
    return TaskPlaces(len(places), tuple(places), active, completed)


TASK_LIST: TypedCommand[ListParameters, TaskList] = TypedCommand("task.list", ListParameters, TaskList)
TASK_PLACES: TypedCommand[PlacesParameters, TaskPlaces] = TypedCommand("task.places", PlacesParameters, TaskPlaces)


@dataclass(frozen=True, slots=True)
class ItemParameters:
    item: str = field(metadata=option("The task id (see task.list)"))


def task_show(engine: Engine, parameters: ItemParameters) -> TaskInfo:
    with located(engine, parameters.item, CollectionKind.TASKS) as found:
        return _info(parameters.item, found.account, found.item.collection, _single(found.store.get(found.item.collection, parameters.item)))


# --- writing ------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskFields:
    """The fields of a task (for task.add and task.edit). A list given here replaces the list the task has."""

    summary: str | None = field(default=None, metadata=option("The title"))
    description: str | None = field(default=None, metadata=option("A description"))
    location: str | None = field(default=None, metadata=option("Where"))
    start: str | None = field(default=None, metadata=option("When to start: 2026-10-12 or 2026-10-12T09:00 (empty: no start)"))
    due: str | None = field(default=None, metadata=option("When it is due: 2026-10-12 or 2026-10-12T17:00 (empty: no due date)"))
    time_zone: str | None = field(default=None, metadata=option("The zone of times without an offset (default: this computer's)"))
    priority: int | None = field(default=None, metadata=option("0 none, 1 highest ... 9 lowest"))
    percent: int | None = field(default=None, metadata=option("How much is done, 0 to 100"))
    status: Literal["needs-action", "in-process", "completed", "cancelled"] | None = field(default=None, metadata=option("The status"))
    rrule: str | None = field(default=None, metadata=option("How it repeats, like FREQ=WEEKLY (empty: not at all)"))
    reminder: tuple[str, ...] = field(default=(), metadata=option("Reminders: minutes before the due time (15), or at:2026-10-01T09:00 (repeat)"))
    tag: tuple[str, ...] = field(default=(), metadata=option("Tags (repeat)"))
    parent: str | None = field(default=None, metadata=option("The uid of the task this is part of (empty: none)"))
    url: str | None = field(default=None, metadata=option("A web address"))


def _moment(text: str, zone_name: str, name: str) -> Moment | None:
    if not text.strip():
        return None
    try:
        return parse_moment(text, zone_named(zone_name) if zone_name else None)
    except ValueError as error:
        raise UsageError(f"{name}: {error}") from None


def _apply(task: Task, values: TaskFields) -> Task:
    zone_name = values.time_zone or ""
    result = task
    if values.summary is not None:
        result = replace(result, summary=values.summary.strip())
    if values.description is not None:
        result = replace(result, description=values.description)
    if values.location is not None:
        result = replace(result, location=values.location.strip())
    if values.start is not None:
        result = replace(result, start=_moment(values.start, zone_name, "start"))
    if values.due is not None:
        result = replace(result, due=_moment(values.due, zone_name, "due"))
    if values.priority is not None:
        if not 0 <= values.priority <= 9:
            raise UsageError("The priority is 0 (none) or 1 (highest) to 9 (lowest)")
        result = replace(result, priority=values.priority)
    if values.percent is not None:
        if not 0 <= values.percent <= 100:
            raise UsageError("The percentage is between 0 and 100")
        result = replace(result, percent=values.percent)
    if values.status is not None:
        result = replace(result, status=values.status.upper())
        if values.status == "completed" and result.completed is None:
            result = complete_task(result)
        elif values.status != "completed":
            result = replace(result, completed=None)
    if values.rrule is not None:
        rule = values.rrule.strip().removeprefix("RRULE:")
        if rule and "FREQ=" not in rule.upper():
            raise UsageError("A rule starts with FREQ=, like FREQ=WEEKLY")
        result = replace(result, rrule=rule)
    if values.reminder:
        result = replace(result, reminders=tuple(_reminder(text, zone_name) for text in values.reminder))
    if values.tag:
        result = replace(result, categories=tuple(dict.fromkeys(" ".join(tag.split()) for tag in values.tag if tag.strip())))
    if values.parent is not None:
        result = replace(result, parent=values.parent.strip())
    if values.url is not None:
        result = replace(result, url=values.url.strip())
    if isinstance(result.start, datetime) and isinstance(result.due, datetime) and result.due < result.start:
        raise UsageError("A task cannot be due before it starts")
    if not isinstance(result.start, datetime) and result.start is not None and not isinstance(result.due, datetime) and result.due is not None and result.due < result.start:
        raise UsageError("A task cannot be due before it starts")
    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class AddParameters(TaskFields):
    list: str = field(metadata=option("The task list id (see collection.list)"))


def task_add(engine: Engine, parameters: AddParameters) -> TaskInfo:
    if parameters.summary is None or not parameters.summary.strip():
        raise UsageError("A task needs a summary")
    task = _apply(Task(uid=new_uid()), parameters)
    try:
        with open_collection(engine, parameters.list, CollectionKind.TASKS) as (account, store, info), item_writer(engine, account, store) as writer:
            if info.read_only:
                raise UsageError("This task list is read-only")
            item = writer.put(info, tasks_to_text(task))
            return _info(item, account, info.collection_id, task)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditParameters(TaskFields):
    item: str = field(metadata=option("The task id"))
    clear: tuple[str, ...] = field(default=(), metadata=option("Lists to empty: reminder, tag (repeat)"))
    add_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to add (repeat)"))
    remove_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to remove (repeat)"))


def task_edit(engine: Engine, parameters: EditParameters) -> TaskInfo:
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            task = _single(original)
            for name in parameters.clear:
                if name == "reminder":
                    task = replace(task, reminders=())
                elif name == "tag":
                    task = replace(task, categories=())
                else:
                    raise UsageError(f"Only these can be cleared: {', '.join(LIST_FIELDS)}")
            task = _apply(task, parameters)
            if parameters.add_tag or parameters.remove_tag:
                task = replace(task, categories=changed_tags(task.categories, parameters.add_tag, parameters.remove_tag))
            writer.put(found.collection, tasks_to_text(task, original), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, task)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class CompleteResult:
    task: TaskInfo
    next_time: bool  # a repeating task was set to its next time instead of being closed


def task_complete(engine: Engine, parameters: ItemParameters) -> CompleteResult:
    """Mark a task done. A repeating task goes on to its next time (open again, dates moved) instead."""
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            task = _single(original)
            following = advance_task(task)
            result = following if following is not None else complete_task(task)
            writer.put(found.collection, tasks_to_text(result, original), parameters.item)
            return CompleteResult(_info(parameters.item, found.account, found.item.collection, result), following is not None)
    except StorageError as error:
        raise UsageError(str(error)) from None


def task_reopen(engine: Engine, parameters: ItemParameters) -> TaskInfo:
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            task = reopen_task(_single(original))
            writer.put(found.collection, tasks_to_text(task, original), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, task)
    except StorageError as error:
        raise UsageError(str(error)) from None


def task_delete(engine: Engine, parameters: ItemParameters) -> ItemParameters:
    """Delete a task. The file is kept in the store's .deleted directory for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            writer.delete(found.collection, parameters.item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return parameters


@dataclass(frozen=True, slots=True)
class MoveParameters:
    item: str = field(metadata=option("The task id"))
    list: str = field(metadata=option("The task list to move it to"))


def task_move(engine: Engine, parameters: MoveParameters) -> TaskInfo:
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found, item_writer(engine, found.account, found.store) as writer:
            with open_collection(engine, parameters.list, CollectionKind.TASKS) as (account, _, target):
                if account != found.account:
                    raise UsageError("A task can only be moved inside one account (copy it with export and import)")
                if target.read_only or found.collection.read_only:
                    raise UsageError("A read-only task list cannot take part in a move")
                new_id = writer.move(found.collection, parameters.item, target)
                return _info(new_id, account, target.collection_id, _single(found.store.get(target.collection_id, new_id)))
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class ImportParameters:
    list: str = field(metadata=option("The task list id"))
    text: str = field(metadata=option("The iCalendar text with tasks (VTODO)"))


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int
    updated: int
    items: tuple[str, ...]


def task_import(engine: Engine, parameters: ImportParameters) -> ImportResult:
    """Add the tasks of an iCalendar text. A task whose uid is in the list already replaces that task."""
    imported = updated = 0
    items: list[str] = []
    try:
        with open_collection(engine, parameters.list, CollectionKind.TASKS) as (account, store, info), item_writer(engine, account, store) as writer:
            if info.read_only:
                raise UsageError("This task list is read-only")
            tasks = parse_tasks(parameters.text)
            if not tasks:
                raise UsageError("No task could be read from this text")
            for task in tasks:
                known = store.index.find_uid(info.collection_id, task.uid)
                items.append(writer.put(info, tasks_to_text(task), known.item_id if known else None))
                if known:
                    updated += 1
                else:
                    imported += 1
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ImportResult(imported, updated, tuple(items))


@dataclass(frozen=True, slots=True)
class ExportParameters:
    item: str | None = field(default=None, metadata=option("The task id"))
    list: str | None = field(default=None, metadata=option("Or a whole task list"))


@dataclass(frozen=True, slots=True)
class ExportResult:
    tasks: int
    text: str


def task_export(engine: Engine, parameters: ExportParameters) -> ExportResult:
    if (parameters.item is None) == (parameters.list is None):
        raise UsageError("Give either a task or a task list")
    texts: list[str] = []
    try:
        if parameters.item is not None:
            with located(engine, parameters.item, CollectionKind.TASKS) as found:
                texts.append(found.store.get(found.item.collection, parameters.item))
        else:
            assert parameters.list is not None
            with open_collection(engine, parameters.list, CollectionKind.TASKS) as (_, store, info):
                texts.extend(store.get(info.collection_id, item) for item in store.item_ids(info.collection_id))
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ExportResult(len(texts), "".join(texts))


TASK_SHOW: TypedCommand[ItemParameters, TaskInfo] = TypedCommand("task.show", ItemParameters, TaskInfo)
TASK_ADD: TypedCommand[AddParameters, TaskInfo] = TypedCommand("task.add", AddParameters, TaskInfo)
TASK_EDIT: TypedCommand[EditParameters, TaskInfo] = TypedCommand("task.edit", EditParameters, TaskInfo)
TASK_COMPLETE: TypedCommand[ItemParameters, CompleteResult] = TypedCommand("task.complete", ItemParameters, CompleteResult)
TASK_REOPEN: TypedCommand[ItemParameters, TaskInfo] = TypedCommand("task.reopen", ItemParameters, TaskInfo)
TASK_DELETE: TypedCommand[ItemParameters, ItemParameters] = TypedCommand("task.delete", ItemParameters, ItemParameters)
TASK_MOVE: TypedCommand[MoveParameters, TaskInfo] = TypedCommand("task.move", MoveParameters, TaskInfo)


def register(registry: CommandRegistry) -> None:
    registry.register(TASK_LIST.id, "List tasks: open before done, then in order of their due date", ListParameters, task_list)
    registry.register(TASK_PLACES.id, "Where every task stands in the list (for a window that asks for the rows as they come into view)", PlacesParameters, task_places)
    registry.register(TASK_SHOW.id, "Show a task", ItemParameters, task_show)
    registry.register(TASK_ADD.id, "Add a task", AddParameters, task_add)
    registry.register(TASK_EDIT.id, "Change a task", EditParameters, task_edit)
    registry.register(TASK_COMPLETE.id, "Mark a task done (a repeating task goes on to its next time)", ItemParameters, task_complete)
    registry.register(TASK_REOPEN.id, "Make a done task open again", ItemParameters, task_reopen)
    registry.register(TASK_DELETE.id, "Delete a task", ItemParameters, task_delete, destructive=True)
    registry.register(TASK_MOVE.id, "Move a task to another task list of the same account", MoveParameters, task_move)
    registry.register("task.import", "Import tasks from iCalendar text", ImportParameters, quiet_command(task_import))
    registry.register("task.export", "Export a task or a task list as iCalendar text", ExportParameters, task_export)
