from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from chrono_mail.core import AccountKind, ChronoMailError, Folder, FolderRole, MessageFlag, MessageRef, StorageError
from chrono_mail.engine.commands_mail import MessageParameters, _check_id, reply_account
from chrono_mail.engine.commands_rules import apply_to_new
from chrono_mail.engine.junkfilter import learn
from chrono_mail.engine.mailbox import Mailbox, open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.engine.undorecord import UndoRecorder
from chrono_mail.mime import parse_message
from chrono_mail.store import MessageNotFoundError

# --- Version ------------------------------------------------------------
__version__ = "commands_actions.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: Mark all as read with undo: the messages it marked are noted, so Undo makes them unread again (B-18)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.1.308: Junk and Not junk teach the junk filter; Not junk runs the rules on the message, so it goes where they would have put it (D-238)
# v1.9.270: R-19: typed folder.mark-read, folder.empty
# v1.1.247: archive_one, unarchive_target, unarchive_one, junk_one, not_junk_one: one message's step, also for many at once (R-08)
# v1.1.206: The folder an archived message came from is kept in its properties file (archived.log is gone), whichever account archived it (D-176)
# v0.5.123: Empty folder and Mark all as read do the server folder as a whole first; the Archive folder is made on the server first (D-118)
# v0.3.112: Moving a message whose file is gone to Junk removes it from the index (gone)
# v0.2.102: Archiving notes the folder a message came from (archived.log); message.unarchive moves it back there (the Inbox when unknown)
# v0.2.101: The Archive folder is made safely (one folder when several messages are archived at once)
# v0.0.34: junk and not junk
# v0.0.17: Initial bulk actions: archive, mark a whole folder as read, empty Trash or Junk

ARCHIVE_NAME: str = "Archive"
@dataclass(frozen=True, slots=True)
class ArchiveResult:
    message_id: str
    folder: str
    created_folder: bool


def archive_folder(mailbox: Mailbox) -> tuple[Folder, bool]:
    """The account's archive folder: the one with the archive role, else a folder called Archive; created when there is none."""
    folders = mailbox.folders()
    for folder in folders:
        if folder.role is FolderRole.ARCHIVE:
            return folder, False
    for folder in folders:
        if folder.path == (ARCHIVE_NAME,):
            return folder, False
    return mailbox.ensure_folder((ARCHIVE_NAME,), FolderRole.ARCHIVE)  # archiving several messages at once makes one folder


def message_archive(engine: Engine, parameters: MessageParameters) -> ArchiveResult:
    """Move a message to the Archive folder (which is created the first time)."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        target, created = archive_folder(mailbox)
        return archive_one(mailbox, reference, target, created)


def archive_one(mailbox: Mailbox, reference: MessageRef, target: Folder, created: bool) -> ArchiveResult:
    """One message to the archive, its origin noted first (also for many messages at once, R-08)."""
    if reference.folder_path == target.path:
        raise UsageError("The message is already in the archive")
    origin = "/".join(reference.folder_path)
    mailbox.store.properties.change(reference.store_id, lambda current: replace(current, archived_from=origin))  # before the move
    moved = mailbox.move(reference, target.path)
    return ArchiveResult(moved.store_id, "/".join(target.path), created)


def message_unarchive(engine: Engine, parameters: MessageParameters) -> ArchiveResult:
    """Move an archived message back to the folder it was archived from (the Inbox when that is not known). Read or
    unread stays as it is; a folder that no longer exists is made again."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        return unarchive_one(mailbox, reference, *unarchive_target(mailbox, reference))


def unarchive_target(mailbox: Mailbox, reference: MessageRef) -> tuple[tuple[str, ...], bool]:
    """Where an archived message goes back to (made again when it no longer exists), and whether it was made."""
    archive, _ = archive_folder(mailbox)
    if reference.folder_path != archive.path:
        raise UsageError("The message is not in the archive")
    try:
        origin = mailbox.store.properties.of(reference.store_id).archived_from
    except StorageError:
        origin = None  # its properties file is damaged (kept as .bad): back to the Inbox
    target = parse_folder(origin) if origin else mailbox.role_folder(FolderRole.INBOX)
    if target == archive.path:
        return mailbox.role_folder(FolderRole.INBOX), False
    _, created = mailbox.ensure_folder(target)
    return target, created


def unarchive_one(mailbox: Mailbox, reference: MessageRef, target: tuple[str, ...], created: bool) -> ArchiveResult:
    moved = mailbox.move(reference, target)
    try:
        mailbox.store.properties.change(moved.store_id, lambda current: replace(current, archived_from=None))  # it is no longer archived
    except StorageError:
        pass  # the message is back; a stale origin is only read in the Archive folder, and archiving again writes it anew
    return ArchiveResult(moved.store_id, "/".join(target), created)


@dataclass(frozen=True, slots=True)
class JunkResult:
    message_id: str
    folder: str
    gone: bool = False  # the file of the message no longer existed: it was only removed from the index


def message_junk(engine: Engine, parameters: MessageParameters) -> JunkResult:
    """Move a message to the Junk folder; the junk filter learns it as junk."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        result = junk_one(mailbox, reference, mailbox.role_folder(FolderRole.JUNK))
        learn(mailbox, junk_places(mailbox, [result]), junk=True)
        return result


def junk_places(mailbox: Mailbox, results: Iterable[JunkResult]) -> list[MessageRef]:
    """Where the messages of these answers are now (the ones whose file was gone are not anywhere)."""
    return [mailbox.reference(parse_folder(result.folder), result.message_id) for result in results if not result.gone]


def junk_one(mailbox: Mailbox, reference: MessageRef, junk: tuple[str, ...]) -> JunkResult:
    if reference.folder_path == junk:
        raise UsageError("The message is in the Junk folder already")
    try:
        moved = mailbox.move(reference, junk)
    except MessageNotFoundError:
        mailbox.forget(reference)  # a missing file is a deleted message
        return JunkResult(reference.store_id, "/".join(junk), gone=True)
    return JunkResult(moved.store_id, "/".join(junk))


def message_not_junk(engine: Engine, parameters: MessageParameters) -> JunkResult:
    """Take a message out of the Junk folder: to the Inbox, then the rules, as if it had just come in. The junk filter learns it as good."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        result = not_junk_one(mailbox, reference)
        learn(mailbox, junk_places(mailbox, [result]), junk=False)
        return result


def _arrived_for(mailbox: Mailbox, reference: MessageRef) -> Mailbox:
    """The mailbox as the account the message came to: in Local Folders, the POP3 account it was addressed to (D-060), so the rules
    limited to that account apply as they did when it arrived."""
    if mailbox.settings.account.kind is not AccountKind.LOCAL:
        return mailbox
    try:
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
        account = reply_account(mailbox.engine, mailbox.settings, parse_message(raw), raw)
        settings = mailbox.engine.resolve_account(account) if account else None
    except ChronoMailError:
        return mailbox
    return replace(mailbox, settings=settings) if settings is not None and settings.storage == "local" else mailbox


def not_junk_one(mailbox: Mailbox, reference: MessageRef) -> JunkResult:
    if reference.folder_path != mailbox.role_folder(FolderRole.JUNK):
        raise UsageError("The message is not in the Junk folder")
    moved = mailbox.move(reference, mailbox.role_folder(FolderRole.INBOX))
    arrived = _arrived_for(mailbox, moved)
    try:
        ruled = apply_to_new(mailbox.engine, arrived, [moved])  # where the rules would have put it when it came in (D-238)
    finally:
        if arrived is not mailbox:
            arrived.let_go()  # open_mailbox lets go of its own mailbox only
    for problem in ruled.failed:
        mailbox.engine.notice(problem)
    placed = ruled.placed.get(moved.store_id, moved)
    return JunkResult(placed.store_id, "/".join(placed.folder_path))


@dataclass(frozen=True, slots=True)
class FolderActionParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder"))
    undo: int = field(default=0, metadata=option("Mark all as read: the number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class MarkReadResult:
    folder: str
    changed: int


def folder_mark_read(engine: Engine, parameters: FolderActionParameters) -> MarkReadResult:
    """Mark every unread message of a folder as read."""
    with open_mailbox(engine, parameters.account) as mailbox:
        path = mailbox.folder(parameters.folder)
        recorder = UndoRecorder(mailbox, parameters.account, parameters.undo)
        unread = [message.ref.store_id for message in mailbox.store.list_messages(path) if MessageFlag.SEEN not in message.flags] if parameters.undo > 0 else []
        recorder.before(unread)
        try:
            return MarkReadResult("/".join(path), mailbox.mark_folder_read(path))
        finally:
            recorder.after(unread)
            recorder.keep("Mark all as read")


@dataclass(frozen=True, slots=True)
class EmptyResult:
    folder: str
    removed: int


def folder_empty(engine: Engine, parameters: FolderActionParameters) -> EmptyResult:
    """Delete every message in Trash or Junk for good. Other folders cannot be emptied this way."""
    with open_mailbox(engine, parameters.account) as mailbox:
        path = mailbox.folder(parameters.folder)
        role = next((folder.role for folder in mailbox.folders() if folder.path == path), FolderRole.OTHER)
        if role not in (FolderRole.TRASH, FolderRole.JUNK):
            raise UsageError("Only the Trash and Junk folders can be emptied")
        return EmptyResult("/".join(path), mailbox.empty_folder(path))


FOLDER_MARK_READ: TypedCommand[FolderActionParameters, MarkReadResult] = TypedCommand("folder.mark-read", FolderActionParameters, MarkReadResult)
FOLDER_EMPTY: TypedCommand[FolderActionParameters, EmptyResult] = TypedCommand("folder.empty", FolderActionParameters, EmptyResult)


def register(registry: CommandRegistry) -> None:
    registry.register("message.junk", "Move a message to the Junk folder", MessageParameters, message_junk, doing="Moving a message to Junk")
    registry.register("message.not-junk", "Move a message from the Junk folder back to the Inbox", MessageParameters, message_not_junk, doing="Taking a message out of Junk")
    registry.register("message.archive", "Move a message to the Archive folder", MessageParameters, message_archive, doing="Archiving a message")
    registry.register("message.unarchive", "Move an archived message back to the folder it came from (the Inbox when unknown)", MessageParameters, message_unarchive, doing="Moving a message out of the archive")
    registry.register(FOLDER_MARK_READ.id, "Mark every message of a folder as read", FolderActionParameters, folder_mark_read, doing="Marking a folder as read")
    registry.register(FOLDER_EMPTY.id, "Delete everything in Trash or Junk for good", FolderActionParameters, folder_empty, destructive=True, doing="Emptying a folder")
