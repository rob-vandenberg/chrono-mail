from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from chrono_mail.core import AccountKind, ChronoMailError, MessageRef, RebuildAborted, StorageError
from chrono_mail.engine.changes import RebuildStep
from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters, account_label
from chrono_mail.engine.mailbox import open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, index_lock, rebuild_abort
from chrono_mail.index import Index
from chrono_mail.mime import parse_message
from chrono_mail.sync import reconcile_index

# --- Version ------------------------------------------------------------
__version__ = "commands_folders.py 2.0.281"

# --- Version history ----------------------------------------------------
# v2.0.281: index.check (which places need a rebuild or their Sent dates filled in), index.prepare (rebuild one before it is used),
#           index.fill-sent, index.abort; index.rebuild keeps the old index usable while the new one is built, then catches up (D-229)
# v1.9.271: R-19: typed index.rebuild, index.reconcile, store.verify
# v1.9.270: R-19: typed folder.create, folder.rename, folder.delete
# v1.1.250: folder.list reads each folder's numbers from the numbers the index keeps (one lookup per folder) and is typed (FOLDER_LIST, R-10)
# v0.5.155: a rebuilt or repaired index reports its mailbox as changed (D-146)
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
        return tuple(FolderInfo("/".join(folder.path), folder.role.value, *mailbox.index.numbers(folder.path)) for folder in mailbox.folders())


FOLDER_LIST: TypedCommand[AccountParameters, tuple[FolderInfo, ...]] = TypedCommand("folder.list", AccountParameters, tuple)  # every element is a FolderInfo


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
    """Rebuild the index while the old one stays in use: the new one takes its place once no command has it open, then what came
    in or changed meanwhile is caught up from the store (the truth). Aborted: the old index stays (D-229)."""
    settings = engine.storage_settings(engine.resolve_account(parameters.account))
    store = engine.open_store(settings)
    path = engine.directories.index_file(settings.account.account_id)
    with index_lock(path):  # accounts in Local Folders share one index: a second rebuild would build the same file at the same time
        index, report = engine.rebuild(settings.account.account_id, store, path, usable=True)
        try:
            reconcile_index(store, index)  # mail delivered, moved or marked while the new index was built
        finally:
            index.close()
    engine.mailbox_changed(settings.account.account_id)
    return IndexResult(report.indexed, report.unreadable, report.seconds)


IndexNeed = Literal["", "rebuild", "fill"]


@dataclass(frozen=True, slots=True)
class IndexState:
    account: str  # the place's account: Local Folders for the accounts kept there
    name: str
    need: IndexNeed  # "": ready; rebuild: it cannot be used until rebuilt; fill: usable, its Sent dates are still to be filled in


def _index_need(engine: Engine, account_id: str, has_mail: bool) -> IndexNeed:
    path = engine.directories.index_file(account_id)
    with index_lock(path):  # never while a rebuild replaces the file
        if not path.exists():
            return "rebuild" if has_mail else ""  # an empty new account gets its index when first used, in no time
        try:
            index = Index(path)  # format 11 is upgraded in place here
        except StorageError:
            return "rebuild"
        try:
            return "fill" if index.sent_filling() is not None else ""
        finally:
            index.close()


def index_check(engine: Engine, parameters: NoParameters) -> tuple[IndexState, ...]:
    """Every place where mail is kept, and whether its index needs work before or while it is used (at start, D-229)."""
    config = engine.config()
    found: dict[str, IndexState] = {}
    for item in config.accounts:
        if item.account.kind is AccountKind.DAV:
            continue
        storage = engine.storage_settings(item, config)
        account_id = storage.account.account_id
        if account_id in found:
            continue
        has_mail = False
        if engine.store_directory(storage).exists():
            store = engine.open_store(storage, config)
            has_mail = any(store.list_messages(folder.path) for folder in store.list_folders())
        found[account_id] = IndexState(account_id, account_label(storage, config.accounts), _index_need(engine, account_id, has_mail))
    return tuple(found.values())


def index_prepare(engine: Engine, parameters: AccountParameters) -> IndexResult:
    """Make the index of this place usable before anything uses it: rebuilt when it cannot be used (aborted: RebuildAborted)."""
    settings = engine.storage_settings(engine.resolve_account(parameters.account))
    store = engine.open_store(settings)
    engine.open_index(settings, store).close()
    return IndexResult(0, 0, 0.0)


FILL_BATCH: int = 500


@dataclass(frozen=True, slots=True)
class FillResult:
    filled: int


def index_fill_sent(engine: Engine, parameters: AccountParameters) -> FillResult:
    """After the upgrade from format 11: read the moment each message carries (its Date line) into the index, a batch at a time,
    from where it stopped; the index stays in use meanwhile. Aborted: what is filled stays, the rest follows next time (D-229)."""
    settings = engine.storage_settings(engine.resolve_account(parameters.account))
    account_id = settings.account.account_id
    aborted = rebuild_abort(engine.directories.index_file(account_id))
    aborted.clear()
    store = engine.open_store(settings)
    filled = 0
    try:
        while True:
            if aborted.is_set():
                raise RebuildAborted("Filling in the index was aborted")
            index = engine.open_index(settings, store)  # opened per batch: a rebuild never waits long for it
            try:
                state = index.sent_filling()
                if state is None:
                    break
                rows = index.sent_to_fill(FILL_BATCH)
                moments: list[tuple[int, datetime | None]] = []
                for row_id, store_id, folder, relative in rows:
                    try:
                        moments.append((row_id, parse_message(store.read(MessageRef(account_id, folder, store_id), hint=relative)).date))
                    except ChronoMailError:
                        moments.append((row_id, None))  # cannot be read: no Sent date, as in a rebuild
                finished = len(rows) < FILL_BATCH
                index.fill_sent(moments, rows[-1][0] if rows else 0, finished)
            finally:
                index.close()
            filled += len(rows)
            engine.rebuilding(RebuildStep(account_id, state[0] + len(rows), state[1], usable=True))
            if finished:
                break
    finally:
        engine.rebuilding(RebuildStep(account_id, 0, 0, usable=True, ended=True))
    if filled:
        engine.mailbox_changed(account_id)  # the Sent column fills in
    return FillResult(filled)


@dataclass(frozen=True, slots=True)
class AbortResult:
    account: str


def index_abort(engine: Engine, parameters: AccountParameters) -> AbortResult:
    """Abort rebuilding or filling in this place's index: it stops at its next step; the mail is never touched (D-229)."""
    settings = engine.storage_settings(engine.resolve_account(parameters.account))
    engine.abort_rebuild(settings)
    return AbortResult(settings.account.account_id)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    added: int
    removed: int
    corrected: int


def index_reconcile(engine: Engine, parameters: AccountParameters) -> ReconcileResult:
    with open_mailbox(engine, parameters.account) as mailbox:
        report = reconcile_index(mailbox.store, mailbox.index)
        if report.added or report.removed or report.corrected:
            engine.mailbox_changed(mailbox.store.account_id)
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


FOLDER_CREATE: TypedCommand[FolderParameters, FolderResult] = TypedCommand("folder.create", FolderParameters, FolderResult)
FOLDER_RENAME: TypedCommand[FolderRenameParameters, FolderResult] = TypedCommand("folder.rename", FolderRenameParameters, FolderResult)
FOLDER_DELETE: TypedCommand[FolderDeleteParameters, FolderDeleteResult] = TypedCommand("folder.delete", FolderDeleteParameters, FolderDeleteResult)


INDEX_REBUILD: TypedCommand[AccountParameters, IndexResult] = TypedCommand("index.rebuild", AccountParameters, IndexResult)
INDEX_CHECK: TypedCommand[NoParameters, tuple[IndexState, ...]] = TypedCommand("index.check", NoParameters, tuple)  # every element is an IndexState
INDEX_PREPARE: TypedCommand[AccountParameters, IndexResult] = TypedCommand("index.prepare", AccountParameters, IndexResult)
INDEX_FILL_SENT: TypedCommand[AccountParameters, FillResult] = TypedCommand("index.fill-sent", AccountParameters, FillResult)
INDEX_ABORT: TypedCommand[AccountParameters, AbortResult] = TypedCommand("index.abort", AccountParameters, AbortResult)
INDEX_RECONCILE: TypedCommand[AccountParameters, ReconcileResult] = TypedCommand("index.reconcile", AccountParameters, ReconcileResult)
STORE_VERIFY: TypedCommand[AccountParameters, VerifyResult] = TypedCommand("store.verify", AccountParameters, VerifyResult)


def register(registry: CommandRegistry) -> None:
    registry.register(FOLDER_LIST.id, "List the folders with their message counts", AccountParameters, folder_list)
    registry.register(FOLDER_CREATE.id, "Create a folder", FolderParameters, folder_create)
    registry.register(FOLDER_RENAME.id, "Rename a folder with everything below it", FolderRenameParameters, folder_rename)
    registry.register(FOLDER_DELETE.id, "Delete a folder (its files are kept in .deleted)", FolderDeleteParameters, folder_delete, destructive=True)
    registry.register(INDEX_REBUILD.id, "Rebuild the search index from the message store", AccountParameters, index_rebuild)
    registry.register(INDEX_CHECK.id, "Tell which indexes must be rebuilt or filled in", NoParameters, index_check)
    registry.register(INDEX_PREPARE.id, "Make an index usable, rebuilding it when it cannot be used", AccountParameters, index_prepare)
    registry.register(INDEX_FILL_SENT.id, "Fill in the Sent dates of an index upgraded from format 11", AccountParameters, index_fill_sent)
    registry.register(INDEX_ABORT.id, "Abort rebuilding or filling in an index", AccountParameters, index_abort)
    registry.register(INDEX_RECONCILE.id, "Repair the index after an interruption", AccountParameters, index_reconcile)
    registry.register(STORE_VERIFY.id, "Check that every stored message is intact", AccountParameters, store_verify)
    registry.register("store.recover", "Clean up leftovers of an interrupted run", AccountParameters, store_recover)
