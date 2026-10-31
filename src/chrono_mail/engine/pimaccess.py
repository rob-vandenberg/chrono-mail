from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import cast

from chrono_mail.config import AccountSettings
from chrono_mail.config import AppConfig
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionInfo, CollectionKind, ConfigurationError, PaletteColour, StorageError, chosen_own_name, palette_of
from chrono_mail.core.colours import FIRST_ADDRESS_BOOK, FIRST_CALENDAR, FIRST_TASK_LIST
from chrono_mail.core.pim import MAXIMUM_PALETTE_COLOURS
from chrono_mail.engine.changes import Change, ChangeKind
from chrono_mail.engine.commands_accounts import shown_name
from chrono_mail.engine.local import SETUP_LOCK, ensure_local_folders
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import IndexedItem, PimStore

# --- Version ------------------------------------------------------------
__version__ = "pimaccess.py 4.2.438"

# --- Version history ----------------------------------------------------
# v4.2.438: timed_background_for; every calendar fact carries its account's choice (UR-02, D-297)
# v1.9.265: The fact of a calendar made or changed carries its palette (tell_look); a change of the account's palette tells it for each calendar (R-44)
# v1.1.221: The palette of appointment colours belongs to the account (palette_for); what calendars kept themselves is taken over once (D-191)
# v1.1.211: An account is named as everywhere in the window: its account name, else its address (shown_name, D-184)
# v1.1.172: The colours of the first calendar, task list and address book from the colour file (D-158)
# v0.5.159: an opened store tells its changes as facts of the engine (D-146 step 5)
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.66: A store can be made when an account is first synchronised
# v0.0.65: The CalDAV and CardDAV client; first-time setup of the local calendars is serialised
# v0.0.60: Initial access to the calendars, task lists and address books of the accounts

DEFAULT_COLLECTIONS: tuple[tuple[CollectionKind, str, str], ...] = (
    (CollectionKind.EVENTS, "Calendar", FIRST_CALENDAR),
    (CollectionKind.TASKS, "Tasks", FIRST_TASK_LIST),
    (CollectionKind.CONTACTS, "Contacts", FIRST_ADDRESS_BOOK),
)


@dataclass(frozen=True, slots=True)
class Located:
    account: str
    store: PimStore
    item: IndexedItem
    collection: CollectionInfo


def _settings(engine: Engine, account_id: str) -> AccountSettings:
    for settings in engine.config().accounts:
        if settings.account.account_id == account_id:
            return settings
    raise ConfigurationError("Unknown account")


def _open(engine: Engine, account_id: str, create: bool) -> PimStore:
    config = engine.config()
    settings = _settings(engine, account_id)
    key = engine.data_key(config) if settings.encrypted else None
    directory = engine.directories.pim_directory(account_id)
    store = PimStore.open_or_create(directory, key, durable=engine.durable) if create else PimStore.open(directory, key, durable=engine.durable)

    def report(kind: str, collection: str, item: str, to_collection: str, new_item: str, info: CollectionInfo | None) -> None:
        settings = _settings(engine, account_id) if info is not None else None
        palette = palette_for(settings, info) if settings is not None and info is not None else ()  # a calendar's colours, as the sidebar shows them
        engine.report(Change(
            cast(ChangeKind, kind), account_id, collection, item, to_folder=to_collection, new_message_id=new_item,  # the store names its changes as the facts do
            role=info.kind.value if info is not None else "", name=info.name if info is not None else "", colour=info.colour if info is not None else "",
            palette=palette, read_only=info.read_only if info is not None else False, timed_background=timed_background_for(settings, info),
        ))

    store.reporter = report
    if engine.first_time(f"take over the palettes of {account_id}"):
        _take_over_palettes(engine, account_id, store)
    return store


def tell_palettes(engine: Engine, account_id: str, store: PimStore) -> None:
    """The account's palette changed: every calendar of the account shows it, so each is told with its palette now (D-191, D-146)."""
    settings = _settings(engine, account_id)
    for info in store.collections(CollectionKind.EVENTS):
        engine.report(Change(
            "collection_edited", account_id, info.collection_id, role=info.kind.value, name=info.name, colour=info.colour, palette=palette_for(settings, info),
            read_only=info.read_only, timed_background=timed_background_for(settings, info),
        ))


def palette_for(settings: AccountSettings, info: CollectionInfo) -> tuple[PaletteColour, ...]:
    """A calendar's palette: its own colour first, then its account's appointment colours (D-191); empty for a task list or an address book."""
    if info.kind is not CollectionKind.EVENTS:
        return ()
    return palette_of(info.colour, settings.calendar_palette, settings.calendar_own_colour_name)


def timed_background_for(settings: AccountSettings | None, info: CollectionInfo | None) -> bool:
    """Whether Month shows a calendar's timed events in their colour (D-297): its account's choice, with its colours; never for other kinds."""
    return settings is not None and info is not None and info.kind is CollectionKind.EVENTS and settings.calendar_timed_background


def _take_over_palettes(engine: Engine, account_id: str, store: PimStore) -> None:
    """The palettes calendars kept themselves before the palette belonged to the account (1.1.219, 1.1.220) become the account's one palette, once:
    in the order of the calendars, a colour the account has already (by its id) stays as it is, a new one is added. Each calendar's copy is
    removed only after the account's palette is saved; a run cut short does it again the same way. What could not be kept is told."""
    old = store.old_palettes()
    if not old:
        return
    settings = _settings(engine, account_id)
    palette = list(settings.calendar_palette) if settings.calendar_palette is not None else None
    own_name = chosen_own_name(settings.calendar_own_colour_name)
    lost: list[str] = []
    for kept in old:
        kept_name = chosen_own_name(kept.own_name)  # the default's name was never chosen (D-297)
        if kept_name and kept_name != own_name:
            if own_name:
                lost.append(f"the name “{kept_name}” of {kept.name}")
            else:
                own_name = kept_name
        if kept.palette is None:
            continue  # it never changed the suggestions: nothing of its own
        if palette is None:
            palette = list(kept.palette)
            continue
        places = {entry.colour_id: number for number, entry in enumerate(palette)}
        for entry in kept.palette:
            if entry.colour_id not in places:
                if len(palette) < MAXIMUM_PALETTE_COLOURS:
                    palette.append(entry)
                else:
                    lost.append(f"“{entry.name}” of {kept.name}")
            elif palette[places[entry.colour_id]] != entry:
                lost.append(f"“{entry.name}” of {kept.name}")

    def change(fresh: AppConfig) -> AppConfig:
        current = fresh.find(account_id)
        return fresh.with_account(replace(current, calendar_palette=tuple(palette) if palette is not None else None, calendar_own_colour_name=own_name))

    engine.change_config(change)
    for kept in old:
        store.forget_old_palette(kept.collection_id)
    if lost:
        engine.notice(
            f"Appointment colours of {account_name(engine, account_id)}: its calendars share one palette now; the first calendar's was kept instead of "
            + ", ".join(lost)
        )


def pim_account_ids(engine: Engine) -> list[str]:
    """The accounts that have calendars, task lists or address books: Local first, then the others in the order of the settings."""
    found: list[str] = []
    for settings in engine.config().accounts:
        identifier = settings.account.account_id
        if (engine.directories.pim_directory(identifier) / "pim.json").exists():
            found.append(identifier)
    found.sort(key=lambda identifier: identifier != LOCAL_ACCOUNT_ID)
    return found


def account_name(engine: Engine, account_id: str) -> str:
    return shown_name(_settings(engine, account_id), engine.config().accounts)


@contextmanager
def open_pim(engine: Engine, account_id: str, create: bool = False) -> Generator[PimStore]:
    store = _open(engine, account_id, create=create)
    try:
        yield store
    finally:
        store.close()


def ensure_local_pim(engine: Engine) -> bool:
    """Make the calendars, task lists and address books of this computer if they are not there yet (a calendar, a task list and an address book,
    in the pseudo-account Local Folders). Returns whether something was made."""
    with SETUP_LOCK:
        ensure_local_folders(engine)
        made = False
        store = _open(engine, LOCAL_ACCOUNT_ID, create=True)
        try:
            if not store.collections():
                for kind, name, colour in DEFAULT_COLLECTIONS:
                    store.create_collection(kind, name, colour)
                made = True
        finally:
            store.close()
        return made


@contextmanager
def open_collection(engine: Engine, collection_id: str, kind: CollectionKind | None = None) -> Generator[tuple[str, PimStore, CollectionInfo]]:
    """The store and the description of a collection, wherever it is kept."""
    for account_id in pim_account_ids(engine):
        store = _open(engine, account_id, create=False)
        try:
            for info in store.collections():
                if info.collection_id == collection_id:
                    if kind is not None and info.kind is not kind:
                        raise UsageError(f"That is not a {_KIND_WORDS[kind]}")
                    yield account_id, store, info
                    return
        finally:
            store.close()
    raise UsageError("There is no such calendar, task list or address book")


_KIND_WORDS: dict[CollectionKind, str] = {CollectionKind.EVENTS: "calendar", CollectionKind.TASKS: "task list", CollectionKind.CONTACTS: "address book"}


@contextmanager
def located(engine: Engine, item_id: str, kind: CollectionKind | None = None) -> Generator[Located]:
    """The store and the index entry of an item, wherever it is kept (item ids are random and never repeat across accounts)."""
    if len(item_id) != 32 or any(character not in "0123456789abcdef" for character in item_id):
        raise UsageError("An item id is 32 lower-case hexadecimal characters (see the list commands)")
    for account_id in pim_account_ids(engine):
        store = _open(engine, account_id, create=False)
        try:
            item = store.index.get(item_id)
            if item is None:
                continue
            if kind is not None and item.kind is not kind:
                raise UsageError("That item is not a " + {CollectionKind.EVENTS: "event", CollectionKind.TASKS: "task", CollectionKind.CONTACTS: "contact"}[kind])
            yield Located(account_id, store, item, store.get_collection(item.collection))
            return
        except StorageError:
            raise
        finally:
            store.close()
    raise UsageError("There is no such item")
