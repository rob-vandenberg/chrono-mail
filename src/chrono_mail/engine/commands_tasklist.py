from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Literal

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.commands_calendar import _reminder
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.ical import Moment, Task, advance_task, complete_task, new_uid, parse_tasks, reopen_task, tasks_to_text
from chrono_mail.pim.times import format_moment, local_zone, parse_moment, zone_named
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_tasklist.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: The tasks section
# v0.0.60: Initial task commands

MAXIMUM_ROWS: int = 5_000
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
    state: Literal["open", "done", "all"] = field(default="open", metadata=option("open (not done), done or all"))
    due_before: str | None = field(default=None, metadata=option("Only tasks due before this day (2026-10-12), in your local time"))
    text: str = field(default="", metadata=option("Words to find"))
    tag: str = field(default="", metadata=option("Only tasks with this tag"))
    limit: int = field(default=500, metadata=option("How many at most"))
    offset: int = field(default=0, metadata=option("How many to skip"))


@dataclass(frozen=True, slots=True)
class TaskList:
    total: int
    tasks: tuple[TaskRow, ...]
    active: int  # open tasks in what was asked for
    completed: int


def task_list(engine: Engine, parameters: ListParameters) -> TaskList:
    """Tasks in order of their due date (tasks without one last), then priority."""
    cutoff: int | None = None
    if parameters.due_before:
        try:
            moment = parse_moment(parameters.due_before)
        except ValueError as error:
            raise UsageError(f"due_before: {error}") from None
        day = moment.date() if isinstance(moment, datetime) else moment
        cutoff = int(datetime(day.year, day.month, day.day, tzinfo=local_zone()).timestamp())
    rows: list[tuple[tuple[int, int, str], TaskRow]] = []
    total = active = completed = 0
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            lists = {info.collection_id: info for info in store.collections(CollectionKind.TASKS)}
            if parameters.list is not None and parameters.list not in lists:
                continue
            chosen = (parameters.list,) if parameters.list else tuple(lists)
            base = ItemQuery(CollectionKind.TASKS, chosen, parameters.text, parameters.tag, due_before=cutoff, limit=10**6)
            active += store.index.count(replace(base, only_open=True))
            completed += store.index.count(replace(base, only_done=True))
            query = replace(base, only_open=parameters.state == "open", only_done=parameters.state == "done", sort="due")
            total += store.index.count(query)
            for item in store.index.query(replace(query, limit=parameters.offset + max(1, min(parameters.limit, MAXIMUM_ROWS)))):
                task = _single(store.get(item.collection, item.item_id))
                info = lists[item.collection]
                key = (item.due if item.due is not None else 10**12, item.priority if item.priority else 10, task.summary.casefold())
                rows.append((key, TaskRow(
                    item.item_id, account, item.collection, info.name, info.colour, task.summary, format_moment(task.due), format_moment(task.start), task.priority, task.percent,
                    task.status, task.done, _overdue(task), bool(task.rrule), task.categories, task.parent,
                )))
    rows.sort(key=lambda pair: pair[0])
    limit = max(1, min(parameters.limit, MAXIMUM_ROWS))
    return TaskList(total, tuple(row for _, row in rows[parameters.offset : parameters.offset + limit]), active, completed)


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
        with open_collection(engine, parameters.list, CollectionKind.TASKS) as (account, store, info):
            if info.read_only:
                raise UsageError("This task list is read-only")
            item = store.put(info.collection_id, tasks_to_text(task))
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
        with located(engine, parameters.item, CollectionKind.TASKS) as found:
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
            found.store.put(found.item.collection, tasks_to_text(task, original), parameters.item)
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
        with located(engine, parameters.item, CollectionKind.TASKS) as found:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            task = _single(original)
            following = advance_task(task)
            result = following if following is not None else complete_task(task)
            found.store.put(found.item.collection, tasks_to_text(result, original), parameters.item)
            return CompleteResult(_info(parameters.item, found.account, found.item.collection, result), following is not None)
    except StorageError as error:
        raise UsageError(str(error)) from None


def task_reopen(engine: Engine, parameters: ItemParameters) -> TaskInfo:
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            original = found.store.get(found.item.collection, parameters.item)
            task = reopen_task(_single(original))
            found.store.put(found.item.collection, tasks_to_text(task, original), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, task)
    except StorageError as error:
        raise UsageError(str(error)) from None


def task_delete(engine: Engine, parameters: ItemParameters) -> ItemParameters:
    """Delete a task. The file is kept in the store's .deleted directory for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found:
            if found.collection.read_only:
                raise UsageError("This task list is read-only")
            found.store.delete(found.item.collection, parameters.item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return parameters


@dataclass(frozen=True, slots=True)
class MoveParameters:
    item: str = field(metadata=option("The task id"))
    list: str = field(metadata=option("The task list to move it to"))


def task_move(engine: Engine, parameters: MoveParameters) -> TaskInfo:
    try:
        with located(engine, parameters.item, CollectionKind.TASKS) as found:
            with open_collection(engine, parameters.list, CollectionKind.TASKS) as (account, _, target):
                if account != found.account:
                    raise UsageError("A task can only be moved inside one account (copy it with export and import)")
                if target.read_only or found.collection.read_only:
                    raise UsageError("A read-only task list cannot take part in a move")
                new_id = found.store.move(found.item.collection, parameters.item, target.collection_id)
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
        with open_collection(engine, parameters.list, CollectionKind.TASKS) as (_, store, info):
            if info.read_only:
                raise UsageError("This task list is read-only")
            tasks = parse_tasks(parameters.text)
            if not tasks:
                raise UsageError("No task could be read from this text")
            for task in tasks:
                known = store.index.find_uid(info.collection_id, task.uid)
                items.append(store.put(info.collection_id, tasks_to_text(task), known.item_id if known else None))
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


def register(registry: CommandRegistry) -> None:
    registry.register("task.list", "List tasks in order of their due date", ListParameters, task_list)
    registry.register("task.show", "Show a task", ItemParameters, task_show)
    registry.register("task.add", "Add a task", AddParameters, task_add)
    registry.register("task.edit", "Change a task", EditParameters, task_edit)
    registry.register("task.complete", "Mark a task done (a repeating task goes on to its next time)", ItemParameters, task_complete)
    registry.register("task.reopen", "Make a done task open again", ItemParameters, task_reopen)
    registry.register("task.delete", "Delete a task", ItemParameters, task_delete, destructive=True)
    registry.register("task.move", "Move a task to another task list of the same account", MoveParameters, task_move)
    registry.register("task.import", "Import tasks from iCalendar text", ImportParameters, task_import)
    registry.register("task.export", "Export a task or a task list as iCalendar text", ExportParameters, task_export)
