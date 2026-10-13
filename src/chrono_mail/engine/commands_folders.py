from dataclasses import dataclass, field

from chrono_mail.core import StorageError
from chrono_mail.engine.commands_accounts import AccountParameters
from chrono_mail.engine.mailbox import open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine, index_lock
from chrono_mail.sync import rebuild_index, reconcile_index

# --- Version ------------------------------------------------------------
__version__ = "commands_folders.py 0.5.123"

# --- Version history ----------------------------------------------------
# v0.5.123: Create, rename and delete go through the mailbox: in an IMAP account on the server first (D-118)
# v0.4.118: The lock of an index file is shared with the rebuild when an account is opened (session.index_lock)
# v0.3.111: Two rebuilds of the same index wait for each other
# v0.0.45: maintenance works on the storage of an account
# v0.0.13: Changes for the user interface (see DEVLOG.md)
# v0.0.12: Initial folder and maintenance commands


@dataclass(frozen=True, slots=True)
class FolderInfo:
    path: str
    role: str
    messages: int
    unread: int


def folder_list(engine: Engine, parameters: AccountParameters) -> tuple[FolderInfo, ...]:
    with open_mailbox(engine, parameters.account) as mailbox:
        return tuple(
            FolderInfo("/".join(folder.path), folder.role.value, mailbox.index.count(folder.path), mailbox.index.count(folder.path, unread_only=True))
            for folder in mailbox.folders()
        )


@dataclass(frozen=True, slots=True)
class FolderParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder, for example Archive/2026"))


@dataclass(frozen=True, slots=True)
class FolderResult:
    folder: str


def folder_create(engine: Engine, parameters: FolderParameters) -> FolderResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        created = mailbox.create_folder(parse_folder(parameters.folder))
        return FolderResult("/".join(created.path))


@dataclass(frozen=True, slots=True)
class FolderRenameParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder to rename (with everything below it)"))
    new_name: str = field(metadata=option("Its new path, for example Archive/Old"))


def folder_rename(engine: Engine, parameters: FolderRenameParameters) -> FolderResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        old, new = mailbox.folder(parameters.folder), parse_folder(parameters.new_name)
        mailbox.rename_folder(old, new)
        return FolderResult("/".join(new))


@dataclass(frozen=True, slots=True)
class FolderDeleteParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder to delete"))
    with_messages: bool = field(default=False, metadata=option("Also delete the folder when it still holds messages"))
    include_subfolders: bool = field(default=False, metadata=option("Delete the folders below it as well"))


@dataclass(frozen=True, slots=True)
class FolderDeleteResult:
    folder: str
    messages_removed: int
    kept_in: str
    folders_removed: int = 1


def folder_delete(engine: Engine, parameters: FolderDeleteParameters) -> FolderDeleteResult:
    """Take the folder (and with include_subfolders everything below it) out of the store.
    The files are kept in the store's .deleted directory, not erased."""
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        if folder[0].lower() in ("inbox", "sent", "drafts", "trash", "junk", "outbox") and len(folder) == 1:
            raise UsageError("The standard folders cannot be deleted")
        targets = [folder]
        if parameters.include_subfolders:
            targets = sorted((info.path for info in mailbox.folders() if info.path[: len(folder)] == folder), key=len, reverse=True)
        if not parameters.with_messages:
            for path in targets:
                if mailbox.store.list_messages(path):
                    raise StorageError("The folder is not empty")
        removed = 0
        for path in targets:  # the deepest folders first, so that no folder is ever left without its parent
            removed += mailbox.delete_folder(path, require_empty=not parameters.with_messages)
        return FolderDeleteResult("/".join(folder), removed, str(mailbox.store.root / ".deleted"), len(targets))


# --- maintenance -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexResult:
    messages: int
    unreadable: int
    seconds: float


def index_rebuild(engine: Engine, parameters: AccountParameters) -> IndexResult:
    settings = engine.storage_settings(engine.resolve_account(parameters.account))
    store = engine.open_store(settings)
    path = engine.directories.index_file(settings.account.account_id)
    with index_lock(path):  # accounts in Local Folders share one index: a second rebuild would build the same file at the same time
        index, report = rebuild_index(store, path, lambda done: engine.progress("Rebuilding the index", done, None))
        index.close()
    return IndexResult(report.indexed, report.unreadable, report.seconds)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    added: int
    removed: int
    corrected: int


def index_reconcile(engine: Engine, parameters: AccountParameters) -> ReconcileResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        report = reconcile_index(mailbox.store, mailbox.index)
        return ReconcileResult(report.added, report.removed, report.corrected)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    checked: int
    damaged: tuple[str, ...]


def store_verify(engine: Engine, parameters: AccountParameters) -> VerifyResult:
    """Read and authenticate every message file; list the ones that are damaged."""
    store = engine.open_store(engine.storage_settings(engine.resolve_account(parameters.account)))
    checked = 0
    damaged: list[str] = []
    for folder in store.list_folders():
        for message in store.list_messages(folder.path):
            checked += 1
            try:
                store.verify(message.ref, hint=message.relative_path)
            except Exception:  # CryptoError or StorageError: one damaged message must not stop the check
                damaged.append(f"{'/'.join(folder.path)}: {message.ref.store_id}")
            if checked % 1000 == 0:
                engine.progress("Verifying", checked, None)
    return VerifyResult(checked, tuple(damaged))


@dataclass(frozen=True, slots=True)
class RecoverResult:
    temporary_files_removed: int
    abandoned_folders_removed: int
    damaged_folders: tuple[str, ...]


def store_recover(engine: Engine, parameters: AccountParameters) -> RecoverResult:
    store = engine.open_store(engine.storage_settings(engine.resolve_account(parameters.account)))
    report = store.recover()
    return RecoverResult(report.temporary_files_removed, report.abandoned_folders_removed, report.damaged_folders)


def register(registry: CommandRegistry) -> None:
    registry.register("folder.list", "List the folders with their message counts", AccountParameters, folder_list)
    registry.register("folder.create", "Create a folder", FolderParameters, folder_create)
    registry.register("folder.rename", "Rename a folder with everything below it", FolderRenameParameters, folder_rename)
    registry.register("folder.delete", "Delete a folder (its files are kept in .deleted)", FolderDeleteParameters, folder_delete, destructive=True)
    registry.register("index.rebuild", "Rebuild the search index from the message store", AccountParameters, index_rebuild)
    registry.register("index.reconcile", "Repair the index after an interruption", AccountParameters, index_reconcile)
    registry.register("store.verify", "Check that every stored message is intact", AccountParameters, store_verify)
    registry.register("store.recover", "Clean up leftovers of an interrupted run", AccountParameters, store_recover)
