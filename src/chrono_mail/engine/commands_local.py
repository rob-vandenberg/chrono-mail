from dataclasses import dataclass, field, replace
from typing import Literal

from chrono_mail.config import AccountSettings
from chrono_mail.core import AccountKind, ChronoMailError, FolderRole, MessageRef, StorageError
from chrono_mail.engine.commands_accounts import AccountSummary, NoParameters, _summary
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import OUTBOX, STANDARD_FOLDERS, Engine
from chrono_mail.index import Index
from chrono_mail.store import Store
from chrono_mail.sync import Pop3State, reconcile_index

# --- Version ------------------------------------------------------------
__version__ = "commands_local.py 0.5.144"

# --- Version history ----------------------------------------------------
# v0.5.144: The storage choice is saved on a fresh read of the settings, one command at a time (change_config)
# v0.2.101: Folders are made safely (ensure_folder)
# v0.0.45: Initial Local Folders commands and the move of an account between its own folders and Local Folders

STANDARD_PATHS: dict[FolderRole, tuple[str, ...]] = {role: path for path, role in STANDARD_FOLDERS if role is not FolderRole.OTHER}


def local_enable(engine: Engine, parameters: NoParameters) -> AccountSummary:
    """Make the Local Folders mailbox (one tree of folders that POP3 accounts can share, and a place to keep mail of your own)."""
    config, _ = ensure_local_folders(engine)
    local = config.local_folders()
    assert local is not None
    return _summary(local)


@dataclass(frozen=True, slots=True)
class StorageParameters:
    account: str = field(metadata=option("The POP3 account"))
    storage: Literal["own", "local"] = field(metadata=option("own: folders of its own; local: the shared Local Folders"))


@dataclass(frozen=True, slots=True)
class StorageResult:
    storage: str
    copied: int  # messages copied to the new place in this run
    already_there: int  # messages that an interrupted earlier run had copied already
    removed_from_old: int  # originals removed after their copies were verified
    left_behind: int  # messages that stay where they are (see notes)
    notes: tuple[str, ...]


def _target_path(source: Store, path: tuple[str, ...]) -> tuple[tuple[str, ...], FolderRole]:
    """Where a folder of one store goes in the other: the standard folders to the standard folders, any other folder keeps its path."""
    role = next((folder.role for folder in source.list_folders() if folder.path == path), FolderRole.OTHER)
    if role in STANDARD_PATHS:
        return STANDARD_PATHS[role], role
    if path == OUTBOX:
        return OUTBOX, FolderRole.OTHER
    return path, FolderRole.OTHER


def _ensure_folder(store: Store, path: tuple[str, ...], role: FolderRole) -> None:
    store.ensure_folder(path, role)


def _same(a: Store, b: Store, store_id: str, a_where: tuple[tuple[str, ...], str], b_where: tuple[tuple[str, ...], str]) -> bool:
    """Is the message in both stores, byte for byte?"""
    try:
        return a.read(MessageRef(a.account_id, a_where[0], store_id), hint=a_where[1]) == b.read(MessageRef(b.account_id, b_where[0], store_id), hint=b_where[1])
    except ChronoMailError:
        return False


def _copy(engine: Engine, source: Store, target: Store, wanted: set[str] | None) -> tuple[int, int]:
    """Copy messages (all, or those with these ids) to the other store, keeping their ids. Returns (copied, already there)."""
    present = target.locate_all()
    copied = already = 0
    for folder in source.list_folders():
        destination, role = _target_path(source, folder.path)
        for message in source.list_messages(folder.path):
            store_id = message.ref.store_id
            if wanted is not None and store_id not in wanted:
                continue
            if store_id in present:
                already += 1
                continue
            _ensure_folder(target, destination, role)
            raw = source.read(message.ref, hint=message.relative_path)
            target.deliver(destination, raw, flags=message.flags, verify=True, store_id=store_id)  # read back and compared before it counts
            copied += 1
            engine.progress("Moving the mail", copied + already, None)
    return copied, already


def _remove_verified(source: Store, source_index: Index | None, target: Store, wanted: set[str] | None) -> int:
    """Remove from the old place the messages that are in the new place byte for byte. Returns how many."""
    removed = 0
    there = target.locate_all()
    for folder in source.list_folders():
        for message in source.list_messages(folder.path):
            store_id = message.ref.store_id
            if (wanted is not None and store_id not in wanted) or store_id not in there:
                continue
            if _same(source, target, store_id, (folder.path, message.relative_path), there[store_id]):
                if source_index is not None:
                    source_index.remove(store_id)
                source.delete(message.ref, hint=message.relative_path)
                removed += 1
    return removed


def account_set_storage(engine: Engine, parameters: StorageParameters) -> StorageResult:
    """Keep a POP3 account's mail in its own folders or in the shared Local Folders, and move what is there now.

    The mail is copied first and every copy is read back and compared; only then is the setting changed (the commit point);
    only then are the originals removed, and only those whose copy is identical. An interruption at any point leaves the mail
    in one place or both, never nowhere: running the command again completes the job."""
    settings = engine.resolve_account(parameters.account)
    if settings.account.kind is not AccountKind.POP3:
        raise UsageError("Only a POP3 account can keep its mail in Local Folders")
    return _to_local(engine, settings) if parameters.storage == "local" else _to_own(engine, settings)


def _to_local(engine: Engine, settings: AccountSettings) -> StorageResult:
    config, _ = ensure_local_folders(engine)
    local = config.local_folders()
    assert local is not None
    account_id = settings.account.account_id
    own_directory = engine.directories.store_directory(account_id)
    local_store = engine.open_store(local, config)
    copied = already = removed = 0
    own_settings = replace(settings, storage="own")
    if own_directory.exists():
        own_store = engine.open_store(own_settings, config)
        copied, already = _copy(engine, own_store, local_store, None)
        local_index = engine.open_index(local, local_store)
        try:
            reconcile_index(local_store, local_index)  # the copies are found by the index
        finally:
            local_index.close()
    if settings.storage != "local":
        engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(account_id), storage="local")))  # the commit point
    if own_directory.exists():
        removed = _remove_verified(engine.open_store(own_settings, config), None, local_store, None)
        engine.directories.index_file(account_id).unlink(missing_ok=True)  # the index of the old folders is of no use any more
    return StorageResult("local", copied, already, removed, 0, ())


def _to_own(engine: Engine, settings: AccountSettings) -> StorageResult:
    config = engine.config()
    local = config.local_folders()
    if local is None:
        raise StorageError("There is no Local Folders mailbox")
    account_id = settings.account.account_id
    state = Pop3State(engine.directories.pop3_state_file(account_id), durable=engine.durable)
    wanted = {record.store_id for record in state.records.values()}  # what this account downloaded: the only mail that can be told apart
    own_directory = engine.directories.store_directory(account_id)
    own_settings = replace(settings, storage="own")
    local_store = engine.open_store(local, config)
    if not own_directory.exists():
        created = Store.create(own_directory, account_id, engine.data_key(config) if settings.encrypted else None, durable=engine.durable)
        for path, role in STANDARD_FOLDERS:
            created.create_folder(path, role)
    own_store = engine.open_store(own_settings, config)
    copied, already = _copy(engine, local_store, own_store, wanted)
    own_index = engine.open_index(own_settings, own_store)
    try:
        reconcile_index(own_store, own_index)
    finally:
        own_index.close()
    if settings.storage != "own":
        engine.change_config(lambda fresh: fresh.with_account(replace(fresh.find(account_id), storage="own")))  # the commit point
    local_index = engine.open_index(local, local_store)
    try:
        removed = _remove_verified(local_store, local_index, own_store, wanted)
    finally:
        local_index.close()
    left = sum(len(local_store.list_messages(folder.path)) for folder in local_store.list_folders())
    note = "Mail that you wrote or filed in Local Folders yourself, and mail of other accounts, stays there."
    return StorageResult("own", copied, already, removed, left, (note,))


def register(registry: CommandRegistry) -> None:
    registry.register("local.enable", "Make the Local Folders mailbox", NoParameters, local_enable)
    registry.register("account.set-storage", "Keep a POP3 account's mail in its own folders or in the shared Local Folders (moves the mail)", StorageParameters, account_set_storage)
