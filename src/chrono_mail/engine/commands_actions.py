from dataclasses import dataclass, field

from chrono_mail.core import Folder, FolderRole, MessageFlag
from chrono_mail.engine.commands_mail import MessageParameters, _check_id
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine

# --- Version ------------------------------------------------------------
__version__ = "commands_actions.py 0.2.101"

# --- Version history ----------------------------------------------------
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
    return mailbox.store.ensure_folder((ARCHIVE_NAME,), FolderRole.ARCHIVE)  # archiving several messages at once makes one folder


def message_archive(engine: Engine, parameters: MessageParameters) -> ArchiveResult:
    """Move a message to the Archive folder (which is created the first time)."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        target, created = archive_folder(mailbox)
        if reference.folder_path == target.path:
            raise UsageError("The message is already in the archive")
        moved = mailbox.move(reference, target.path)
        return ArchiveResult(moved.store_id, "/".join(target.path), created)


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
    registry.register("folder.mark-read", "Mark every message of a folder as read", FolderActionParameters, folder_mark_read)
    registry.register("folder.empty", "Delete everything in Trash or Junk for good", FolderActionParameters, folder_empty, destructive=True)
