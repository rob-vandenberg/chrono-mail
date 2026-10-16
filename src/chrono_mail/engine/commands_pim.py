from dataclasses import dataclass, field
from typing import Literal

from chrono_mail.config import TAG_KINDS, load_tags, save_tags
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, ConfigurationError, StorageError
from chrono_mail.engine.commands_tags import mail_tag_counts, rename_in_mail
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.pimaccess import account_name, ensure_local_pim, open_collection, open_pim, pim_account_ids
from chrono_mail.pim.edit import retag_item
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command

# --- Version ------------------------------------------------------------
__version__ = "commands_pim.py 0.5.156"

# --- Version history ----------------------------------------------------
# v0.5.156: Rename a tag everywhere is a big job: one "mailbox changed" per mailbox at its end (D-146)
# v0.5.135: tag.list in the person's order, with each tag's shortcut and what it is for
# v0.5.134: tag.list counts mail too; tag.rename renames in mail too
# v0.0.60: Initial commands for collections and tags

KindName = Literal["events", "tasks", "contacts"]


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    account: str
    account_name: str
    collection: str
    kind: str
    name: str
    colour: str
    read_only: bool
    items: int


def _summaries(engine: Engine, kind: CollectionKind | None = None) -> tuple[CollectionSummary, ...]:
    found: list[CollectionSummary] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for info in store.collections(kind):
                from chrono_mail.pimstore import ItemQuery

                found.append(
                    CollectionSummary(
                        account, account_name(engine, account), info.collection_id, info.kind.value, info.name, info.colour, info.read_only,
                        store.index.count(ItemQuery(collections=(info.collection_id,))),
                    )
                )
    return tuple(found)


def pim_enable(engine: Engine, parameters: NoParameters) -> tuple[CollectionSummary, ...]:
    """Make sure there is a calendar, a task list and an address book on this computer (Local Folders), and list all collections."""
    ensure_local_pim(engine)
    return _summaries(engine)


@dataclass(frozen=True, slots=True)
class ListParameters:
    kind: KindName | None = field(default=None, metadata=option("Only calendars (events), task lists (tasks) or address books (contacts)"))


def collection_list(engine: Engine, parameters: ListParameters) -> tuple[CollectionSummary, ...]:
    """The calendars, task lists and address books of all accounts."""
    return _summaries(engine, CollectionKind(parameters.kind) if parameters.kind else None)


@dataclass(frozen=True, slots=True)
class CreateParameters:
    kind: KindName = field(metadata=option("events (a calendar), tasks (a task list) or contacts (an address book)"))
    name: str = field(metadata=option("The name"))
    colour: str = field(default="", metadata=option("A colour as #rrggbb"))
    account: str = field(default=LOCAL_ACCOUNT_ID, metadata=option("The account (default: the local ones on this computer)"))


def collection_create(engine: Engine, parameters: CreateParameters) -> CollectionSummary:
    if parameters.account == LOCAL_ACCOUNT_ID:
        ensure_local_pim(engine)
    account = engine.resolve_account(parameters.account).account.account_id
    try:
        with open_pim(engine, account) as store:
            info = store.create_collection(CollectionKind(parameters.kind), parameters.name, parameters.colour)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return CollectionSummary(account, account_name(engine, account), info.collection_id, info.kind.value, info.name, info.colour, info.read_only, 0)


@dataclass(frozen=True, slots=True)
class EditParameters:
    collection: str = field(metadata=option("The collection id (see collection.list)"))
    name: str | None = field(default=None, metadata=option("A new name"))
    colour: str | None = field(default=None, metadata=option("A new colour (#rrggbb)"))


def collection_edit(engine: Engine, parameters: EditParameters) -> CollectionSummary:
    try:
        with open_collection(engine, parameters.collection) as (account, store, info):
            changed = store.update_collection(info.collection_id, parameters.name, parameters.colour)
            from chrono_mail.pimstore import ItemQuery

            return CollectionSummary(account, account_name(engine, account), changed.collection_id, changed.kind.value, changed.name, changed.colour, changed.read_only,
                                     store.index.count(ItemQuery(collections=(changed.collection_id,))))
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    collection: str = field(metadata=option("The collection id"))


@dataclass(frozen=True, slots=True)
class DeleteResult:
    collection: str
    items: int


def collection_delete(engine: Engine, parameters: DeleteParameters) -> DeleteResult:
    """Take a calendar, task list or address book away with all its items. The files are kept in the store's .deleted directory for 30 days."""
    from chrono_mail.pimstore import ItemQuery

    try:
        with open_collection(engine, parameters.collection) as (account, store, info):
            count = store.index.count(ItemQuery(collections=(info.collection_id,)))
            store.delete_collection(info.collection_id)
            return DeleteResult(info.collection_id, count)
    except StorageError as error:
        raise UsageError(str(error)) from None


# --- tags ---------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TagInfo:
    name: str
    colour: str
    contacts: int
    events: int
    tasks: int
    mails: int = 0
    shortcut: str = ""  # a key that gives or takes the tag (Ctrl+Alt+1 ...)
    kinds: tuple[str, ...] = TAG_KINDS  # what the tag is offered for: mail, events, contacts, tasks


def tag_list(engine: Engine, parameters: NoParameters) -> tuple[TagInfo, ...]:
    """Every tag in use in mail, contacts, events and tasks (and every tag with a colour), with its colour and how many carry it."""
    try:
        colours = load_tags(engine.directories.tags_file)
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    counts: dict[str, dict[CollectionKind, int]] = {}
    written: dict[str, str] = {}
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for kind in CollectionKind:
                for folded, (shown, count) in store.index.tags(kind).items():
                    written.setdefault(folded, shown)
                    counts.setdefault(folded, {}).setdefault(kind, 0)
                    counts[folded][kind] += count
    mail = mail_tag_counts(engine)
    for folded, (shown, _) in mail.items():
        written.setdefault(folded, shown)
    made = [entry.key for entry in colours.entries]  # the tags the person made, in the person's order; then the others by name
    names = made + sorted((set(counts) | set(mail)) - set(made))

    def info(name: str) -> TagInfo:
        entry = colours.entry(name)
        return TagInfo(
            entry.shown if entry is not None else written.get(name, name), colours.colour_of(name), counts.get(name, {}).get(CollectionKind.CONTACTS, 0),
            counts.get(name, {}).get(CollectionKind.EVENTS, 0), counts.get(name, {}).get(CollectionKind.TASKS, 0), mail.get(name, ("", 0))[1],
            entry.shortcut if entry is not None else "", entry.kinds if entry is not None else TAG_KINDS,
        )

    return tuple(info(name) for name in names)


@dataclass(frozen=True, slots=True)
class ColourParameters:
    name: str = field(metadata=option("The tag"))
    colour: str = field(default="", metadata=option("A colour as #rrggbb; empty takes the colour away"))


def tag_colour(engine: Engine, parameters: ColourParameters) -> tuple[TagInfo, ...]:
    try:
        save_tags(engine.directories.tags_file, load_tags(engine.directories.tags_file).with_colour(parameters.name, parameters.colour))
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return tag_list(engine, NoParameters())


@dataclass(frozen=True, slots=True)
class RenameParameters:
    name: str = field(metadata=option("The tag"))
    new_name: str = field(metadata=option("The new name (an existing tag of that name is joined)"))


@dataclass(frozen=True, slots=True)
class RenameResult:
    items: int


def tag_rename(engine: Engine, parameters: RenameParameters) -> RenameResult:
    """Give a tag another name in all mail and in every contact, event and task that has it, and keep its colour."""
    old, new = " ".join(parameters.name.split()), " ".join(parameters.new_name.split())
    if not old or not new:
        raise UsageError("A tag needs a name")
    from chrono_mail.pimstore import ItemQuery

    changed = 0
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for item in store.index.query(ItemQuery(tag=old, limit=100_000)):
                text = store.get(item.collection, item.item_id)
                updated = retag_item(item.kind, text, lambda tags: tuple(dict.fromkeys(new if tag.casefold() == old.casefold() else tag for tag in tags)))
                if updated != text:
                    store.put(item.collection, updated, item.item_id)
                    changed += 1
    try:
        changed += rename_in_mail(engine, old, new)
    except StorageError as error:
        raise UsageError(str(error)) from None
    try:
        save_tags(engine.directories.tags_file, load_tags(engine.directories.tags_file).renamed(old, new))
    except ConfigurationError as error:
        raise UsageError(str(error)) from None
    return RenameResult(changed)


def register(registry: CommandRegistry) -> None:
    registry.register("pim.enable", "Make sure there is a calendar, a task list and an address book on this computer, and list them", NoParameters, pim_enable)
    registry.register("collection.list", "List the calendars, task lists and address books", ListParameters, collection_list)
    registry.register("collection.create", "Make a calendar, a task list or an address book", CreateParameters, collection_create)
    registry.register("collection.edit", "Rename a calendar, task list or address book, or give it another colour", EditParameters, collection_edit)
    registry.register("collection.delete", "Take a calendar, task list or address book away with everything in it", DeleteParameters, collection_delete, destructive=True)
    registry.register("tag.list", "List the tags in use, with colours and counts", NoParameters, tag_list)
    registry.register("tag.colour", "Give a tag a colour", ColourParameters, tag_colour)
    registry.register("tag.rename", "Give a tag another name everywhere", RenameParameters, quiet_command(tag_rename))
