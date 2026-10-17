from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from chrono_mail.config import AccountSettings
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, CollectionInfo, CollectionKind, ConfigurationError, StorageError
from chrono_mail.core.colours import FIRST_ADDRESS_BOOK, FIRST_CALENDAR, FIRST_TASK_LIST
from chrono_mail.engine.changes import Change, ChangeKind
from chrono_mail.engine.commands_accounts import server_label
from chrono_mail.engine.local import SETUP_LOCK, ensure_local_folders
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import IndexedItem, PimStore

# --- Version ------------------------------------------------------------
__version__ = "pimaccess.py 1.1.172"

# --- Version history ----------------------------------------------------
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
        engine.report(Change(
            cast(ChangeKind, kind), account_id, collection, item, to_folder=to_collection, new_message_id=new_item,  # the store names its changes as the facts do
            role=info.kind.value if info is not None else "", name=info.name if info is not None else "", colour=info.colour if info is not None else "",
        ))

    store.reporter = report
    return store


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
    config = engine.config()
    settings = _settings(engine, account_id)
    shared = any(
        other.account.account_id != account_id and other.account.kind is not AccountKind.LOCAL and other.account.display_name.lower() == settings.account.display_name.lower()
        for other in config.accounts
    )
    return f"{settings.account.display_name} - {server_label(settings)}" if shared and settings.account.kind is not AccountKind.LOCAL else settings.account.display_name


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
