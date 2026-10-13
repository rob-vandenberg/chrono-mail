import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.core import Folder, FolderRole, MessageFlag, StorageError
from chrono_mail.engine.commands_mail import MessageParameters, _check_id
from chrono_mail.engine.mailbox import Mailbox, open_mailbox, parse_folder
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_actions.py 0.2.102"

# --- Version history ----------------------------------------------------
# v0.2.102: Archiving notes the folder a message came from (archived.log); message.unarchive moves it back there (the Inbox when unknown)
# v0.2.101: The Archive folder is made safely (one folder when several messages are archived at once)
# v0.0.34: junk and not junk
# v0.0.17: Initial bulk actions: archive, mark a whole folder as read, empty Trash or Junk

ARCHIVE_NAME: str = "Archive"
ARCHIVED_FILE: str = "archived.log"  # one line per archived message: store id, a tab, the folder it came from
_ARCHIVED_LOCK = threading.Lock()


def note_archived(path: Path, store_id: str, folder: str) -> None:
    """Append the folder an archived message came from. Appending keeps archiving fast with any number of messages; a
    line cut off by a crash is ignored when read."""
    line = f"{store_id}\t{folder}\n".encode()
    with _ARCHIVED_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StorageError(f"The archive note could not be written: {error.strerror}") from None


def archived_from(path: Path, store_id: str) -> str | None:
    """The folder this message was last archived from, or None when it is not known."""
    found: str | None = None
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    for line in text.split("\n"):
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] == store_id and parts[1]:
            found = parts[1]
    return found


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
    return mailbox.store.ensure_folder((ARCHIVE_NAME,), FolderRole.ARCHIVE)  # archiving several messages at once makes one folder


def message_archive(engine: Engine, parameters: MessageParameters) -> ArchiveResult:
    """Move a message to the Archive folder (which is created the first time)."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        target, created = archive_folder(mailbox)
        if reference.folder_path == target.path:
            raise UsageError("The message is already in the archive")
        note_archived(engine.directories.account_directory(mailbox.account_id) / ARCHIVED_FILE, reference.store_id, "/".join(reference.folder_path))  # before the move
        moved = mailbox.move(reference, target.path)
        return ArchiveResult(moved.store_id, "/".join(target.path), created)


def message_unarchive(engine: Engine, parameters: MessageParameters) -> ArchiveResult:
    """Move an archived message back to the folder it was archived from (the Inbox when that is not known). Read or
    unread stays as it is; a folder that no longer exists is made again."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        archive, _ = archive_folder(mailbox)
        if reference.folder_path != archive.path:
            raise UsageError("The message is not in the archive")
        origin = archived_from(engine.directories.account_directory(mailbox.account_id) / ARCHIVED_FILE, reference.store_id)
        target = parse_folder(origin) if origin else mailbox.role_folder(FolderRole.INBOX)
        created = False
        if target == archive.path:
            target = mailbox.role_folder(FolderRole.INBOX)
        else:
            _, created = mailbox.store.ensure_folder(target)
        moved = mailbox.move(reference, target)
        return ArchiveResult(moved.store_id, "/".join(target), created)


@dataclass(frozen=True, slots=True)
class JunkResult:
    message_id: str
    folder: str


def message_junk(engine: Engine, parameters: MessageParameters) -> JunkResult:
    """Move a message to the Junk folder."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        junk = mailbox.role_folder(FolderRole.JUNK)
        if reference.folder_path == junk:
            raise UsageError("The message is in the Junk folder already")
        moved = mailbox.move(reference, junk)
        return JunkResult(moved.store_id, "/".join(junk))


def message_not_junk(engine: Engine, parameters: MessageParameters) -> JunkResult:
    """Take a message out of the Junk folder: back to the Inbox."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        if reference.folder_path != mailbox.role_folder(FolderRole.JUNK):
            raise UsageError("The message is not in the Junk folder")
        inbox = mailbox.role_folder(FolderRole.INBOX)
        moved = mailbox.move(reference, inbox)
        return JunkResult(moved.store_id, "/".join(inbox))


@dataclass(frozen=True, slots=True)
class FolderActionParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder"))


@dataclass(frozen=True, slots=True)
class MarkReadResult:
    folder: str
    changed: int


def folder_mark_read(engine: Engine, parameters: FolderActionParameters) -> MarkReadResult:
    """Mark every unread message of a folder as read."""
    with open_mailbox(engine, parameters.account) as mailbox:
        path = mailbox.folder(parameters.folder)
        changed = 0
        for message in mailbox.store.list_messages(path):
            if MessageFlag.SEEN not in message.flags:
                mailbox.set_flags(message.ref, message.flags | {MessageFlag.SEEN})
                changed += 1
        return MarkReadResult("/".join(path), changed)


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
        removed = 0
        for message in mailbox.store.list_messages(path):
            mailbox.delete(message.ref, permanent=True)
            removed += 1
        return EmptyResult("/".join(path), removed)


def register(registry: CommandRegistry) -> None:
    registry.register("message.junk", "Move a message to the Junk folder", MessageParameters, message_junk)
    registry.register("message.not-junk", "Move a message from the Junk folder back to the Inbox", MessageParameters, message_not_junk)
    registry.register("message.archive", "Move a message to the Archive folder", MessageParameters, message_archive)
    registry.register("message.unarchive", "Move an archived message back to the folder it came from (the Inbox when unknown)", MessageParameters, message_unarchive)
    registry.register("folder.mark-read", "Mark every message of a folder as read", FolderActionParameters, folder_mark_read)
    registry.register("folder.empty", "Delete everything in Trash or Junk for good", FolderActionParameters, folder_empty, destructive=True)
