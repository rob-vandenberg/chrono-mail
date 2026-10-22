import secrets
from dataclasses import dataclass, field, replace
from typing import Literal

from chrono_mail.config import TAG_KINDS, AppConfig, load_tags, save_tags
from chrono_mail.core import LOCAL_ACCOUNT_ID, OWN_COLOUR_ID, CollectionInfo, CollectionKind, ConfigurationError, PaletteColour, StorageError, palette_problem
from chrono_mail.engine.collection_create import create_collection
from chrono_mail.engine.collection_delete import delete_collection, together
from chrono_mail.engine.collection_looks import change_look
from chrono_mail.engine.commands_tags import mail_tag_counts, rename_in_mail
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.pimaccess import account_name, ensure_local_pim, open_collection, open_pim, palette_for, pim_account_ids, tell_palettes
from chrono_mail.pim.edit import retag_item
from chrono_mail.pimstore import PimStore
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command

# --- Version ------------------------------------------------------------
__version__ = "commands_pim.py 1.9.273"

# --- Version history ----------------------------------------------------
# v1.9.273: R-19: typed pim.enable, collection.list, -edit, -delete
# v1.9.271: R-19: typed tag.rename, tag.colour
# v1.9.270: R-19: typed tag.list
# v1.9.265: collection.set-palette tells every calendar of the account its palette now (the sidebars never ask for every calendar, R-44)
# v1.1.236: collection.create in a server's account makes it on the server first, offline later (R-29, D-198); collection.accounts: where one
#           can be made
# v1.1.235: collection.delete of a server's collection deletes it on the server first, offline later (R-28, D-197); a summary says whether it is
#           a server's and which collections here go with it (together)
# v1.1.225: a tag renamed in the items goes through item_writer: on their server first (R-23, D-194)
# v1.1.221: collection.edit of a server's collection on the server first (D-192); collection.set-palette keeps the colours with the account (D-191)
# v1.1.219: A calendar's palette in its summary (palette); collection.set-palette (D-191)
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
    palette: tuple[PaletteColour, ...] = ()  # a calendar's colours: its own, then its account's (D-191); empty for other kinds
    server: bool = False  # kept in step with a server: Delete deletes it there too (D-197)
    together: tuple[str, ...] = ()  # the names of the other collections here at its place on the server, which a Delete takes too


def _palette(engine: Engine, account: str, info: CollectionInfo) -> tuple[PaletteColour, ...]:
    return palette_for(engine.config().find(account), info)


def _summary(engine: Engine, account: str, store: PimStore, info: CollectionInfo) -> CollectionSummary:
    from chrono_mail.pimstore import ItemQuery

    return CollectionSummary(
        account, account_name(engine, account), info.collection_id, info.kind.value, info.name, info.colour, info.read_only,
        store.index.count(ItemQuery(collections=(info.collection_id,))), _palette(engine, account, info),
        bool(info.server_value("href")), tuple(other.name for other in together(store, info)[1:]),
    )


def _summaries(engine: Engine, kind: CollectionKind | None = None) -> tuple[CollectionSummary, ...]:
    found: list[CollectionSummary] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for info in store.collections(kind):
                found.append(_summary(engine, account, store, info))
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
        with open_pim(engine, account, create=True) as store:
            info = create_collection(engine, account, store, CollectionKind(parameters.kind), parameters.name, parameters.colour)
            return _summary(engine, account, store, info)
    except StorageError as error:
        raise UsageError(str(error)) from None


COLLECTION_CREATE: TypedCommand[CreateParameters, CollectionSummary] = TypedCommand("collection.create", CreateParameters, CollectionSummary)


@dataclass(frozen=True, slots=True)
class PlaceSummary:
    account: str
    name: str  # as the sections show the account


@dataclass(frozen=True, slots=True)
class PlaceList:
    places: tuple[PlaceSummary, ...]


def collection_accounts(engine: Engine, parameters: NoParameters) -> PlaceList:
    """Where a calendar, task list or address book can be made: the local ones on this computer first, then every account with a calendar and
    contacts server (D-198)."""
    ensure_local_pim(engine)  # the local ones always exist (as pim.enable makes them at start)
    found = [PlaceSummary(LOCAL_ACCOUNT_ID, account_name(engine, LOCAL_ACCOUNT_ID))]
    for settings in engine.config().accounts:
        identifier = settings.account.account_id
        if identifier != LOCAL_ACCOUNT_ID and settings.dav_url:
            found.append(PlaceSummary(identifier, account_name(engine, identifier)))
    return PlaceList(tuple(found))


COLLECTION_ACCOUNTS: TypedCommand[NoParameters, PlaceList] = TypedCommand("collection.accounts", NoParameters, PlaceList)


@dataclass(frozen=True, slots=True)
class EditParameters:
    collection: str = field(metadata=option("The collection id (see collection.list)"))
    name: str | None = field(default=None, metadata=option("A new name"))
    colour: str | None = field(default=None, metadata=option("A new colour (#rrggbb)"))


def collection_edit(engine: Engine, parameters: EditParameters) -> CollectionSummary:
    """Rename or recolour a collection: one of a server on the server first, then here (offline: here, sent at the next synchronisation, D-192)."""
    try:
        with open_collection(engine, parameters.collection) as (account, store, info):
            changed = change_look(engine, account, store, info, parameters.name, parameters.colour)
            return _summary(engine, account, store, changed)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class PaletteParameters:
    collection: str = field(metadata=option("The calendar id (see collection.list)"))
    own_name: str = field(default="", metadata=option("The name of the calendar's own colour (empty: Calendar colour)"))
    own_colour: str = field(default="", metadata=option("The calendar's own colour, #rrggbb (empty: as it is)"))
    colours: tuple[str, ...] = field(default=(), metadata=option("The other colours in order, each as id<TAB>name<TAB>#rrggbb; an empty id makes a new colour (repeat)"))


def collection_set_palette(engine: Engine, parameters: PaletteParameters) -> CollectionSummary:
    """Keep the appointment colours of the calendar's account as given (D-191): the name of a calendar's own colour and the calendar's own colour
    (on its server first, as a recolour), then the account's other colours in this order, shared by all its calendars. A colour left out is gone:
    its appointments show their calendar's own colour again. The calendar's own colour cannot be left out."""
    entries: list[PaletteColour] = []
    for text in parameters.colours:
        parts = text.split("\t")
        if len(parts) != 3:
            raise UsageError("A colour is given as id<TAB>name<TAB>#rrggbb")
        colour_id, name, colour = parts[0].strip(), " ".join(parts[1].split()), parts[2].strip().lower()
        if colour_id == OWN_COLOUR_ID:
            raise UsageError("The calendar's own colour is given with own_name and own_colour")
        entries.append(PaletteColour(colour_id or secrets.token_hex(4), name, colour))
    own_name = " ".join(parameters.own_name.split())
    if problem := palette_problem(tuple(entries), own_name):
        raise UsageError(problem)
    try:
        with open_collection(engine, parameters.collection, CollectionKind.EVENTS) as (account, store, info):
            if info.read_only:
                raise UsageError("This calendar is read-only")

            def change(fresh: AppConfig) -> AppConfig:
                current = fresh.find(account)
                return fresh.with_account(replace(current, calendar_palette=tuple(entries), calendar_own_colour_name=own_name))

            engine.change_config(change)
            # the calendar's own colour as a recolour (on its server first); then every calendar of the account is told its palette now
            changed = change_look(engine, account, store, info, None, parameters.own_colour.strip().lower() or None)
            tell_palettes(engine, account, store)
            return _summary(engine, account, store, changed)
    except StorageError as error:
        raise UsageError(str(error)) from None


COLLECTION_SET_PALETTE: TypedCommand[PaletteParameters, CollectionSummary] = TypedCommand("collection.set-palette", PaletteParameters, CollectionSummary)


@dataclass(frozen=True, slots=True)
class DeleteParameters:
    collection: str = field(metadata=option("The collection id"))


@dataclass(frozen=True, slots=True)
class DeleteResult:
    collection: str
    items: int


def collection_delete(engine: Engine, parameters: DeleteParameters) -> DeleteResult:
    """Take a calendar, task list or address book away with all its items. One of a server is deleted on the server first, for every program that
    synchronises with it (offline: here at once, there at the next synchronisation, D-197). The files here are kept in the store's .deleted
    directory for 30 days."""
    from chrono_mail.pimstore import ItemQuery

    try:
        with open_collection(engine, parameters.collection) as (account, store, info):
            count = store.index.count(ItemQuery(collections=(info.collection_id,)))
            delete_collection(engine, account, store, info)
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
        with open_pim(engine, account) as store, item_writer(engine, account, store) as writer:
            for item in store.index.query(ItemQuery(tag=old, limit=100_000)):
                text = store.get(item.collection, item.item_id)
                updated = retag_item(item.kind, text, lambda tags: tuple(dict.fromkeys(new if tag.casefold() == old.casefold() else tag for tag in tags)))
                if updated != text:
                    writer.put(store.get_collection(item.collection), updated, item.item_id)
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


TAG_LIST: TypedCommand[NoParameters, tuple[TagInfo, ...]] = TypedCommand("tag.list", NoParameters, tuple)  # every element is a TagInfo


TAG_COLOUR: TypedCommand[ColourParameters, tuple[TagInfo, ...]] = TypedCommand("tag.colour", ColourParameters, tuple)  # every element is a TagInfo
TAG_RENAME: TypedCommand[RenameParameters, RenameResult] = TypedCommand("tag.rename", RenameParameters, RenameResult)


PIM_ENABLE: TypedCommand[NoParameters, tuple[CollectionSummary, ...]] = TypedCommand("pim.enable", NoParameters, tuple)  # every element is a CollectionSummary
COLLECTION_LIST: TypedCommand[ListParameters, tuple[CollectionSummary, ...]] = TypedCommand("collection.list", ListParameters, tuple)  # every element is a CollectionSummary
COLLECTION_EDIT: TypedCommand[EditParameters, CollectionSummary] = TypedCommand("collection.edit", EditParameters, CollectionSummary)
COLLECTION_DELETE: TypedCommand[DeleteParameters, DeleteResult] = TypedCommand("collection.delete", DeleteParameters, DeleteResult)


def register(registry: CommandRegistry) -> None:
    registry.register(PIM_ENABLE.id, "Make sure there is a calendar, a task list and an address book on this computer, and list them", NoParameters, pim_enable)
    registry.register(COLLECTION_LIST.id, "List the calendars, task lists and address books", ListParameters, collection_list)
    registry.register(COLLECTION_CREATE.id, "Make a calendar, a task list or an address book (in a server's account: on the server first)", CreateParameters, collection_create)
    registry.register(COLLECTION_ACCOUNTS.id, "List where a calendar, task list or address book can be made", NoParameters, collection_accounts)
    registry.register(COLLECTION_EDIT.id, "Rename a calendar, task list or address book, or give it another colour", EditParameters, collection_edit)
    registry.register(COLLECTION_SET_PALETTE.id, "Keep a calendar's colours: its own, then the others in order", PaletteParameters, collection_set_palette)
    registry.register(COLLECTION_DELETE.id, "Take a calendar, task list or address book away with everything in it", DeleteParameters, collection_delete, destructive=True)
    registry.register(TAG_LIST.id, "List the tags in use, with colours and counts", NoParameters, tag_list)
    registry.register(TAG_COLOUR.id, "Give a tag a colour", ColourParameters, tag_colour)
    registry.register(TAG_RENAME.id, "Give a tag another name everywhere", RenameParameters, quiet_command(tag_rename))
