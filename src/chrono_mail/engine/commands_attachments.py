import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_mail import _check_id
from chrono_mail.engine.compose import suggest_filename
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.mime import parse_message

# --- Version ------------------------------------------------------------
__version__ = "commands_attachments.py 2.0.302"

# --- Version history ----------------------------------------------------
# v2.0.302: Initial message.attachment.open (a private copy to open in another program; whether it is a program) and
#           message.attachment.clean-opened (D-236)

# Files that run when they are opened: the person is asked first (AGENTS.md 4.6, D-236)
PROGRAM_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".msi", ".msp", ".scr", ".pif", ".cpl", ".hta", ".lnk", ".reg", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".jar", ".sh", ".bash", ".run", ".bin", ".desktop", ".appimage", ".deb", ".rpm", ".py", ".pyw", ".pl", ".command",
    ".app", ".pkg", ".dmg", ".scpt", ".iso", ".img",
})


@dataclass(frozen=True, slots=True)
class AttachmentOpenParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    index: int = field(metadata=option("Which attachment: its position in the list shown by message.show, starting at 0"))


@dataclass(frozen=True, slots=True)
class AttachmentOpenResult:
    path: str  # the copy to open, readable by the person only
    program: bool  # a file that runs when opened: ask first


def is_program(name: str) -> bool:
    return PurePath(name.lower()).suffix in PROGRAM_EXTENSIONS


def message_attachment_open(engine: Engine, parameters: AttachmentOpenParameters) -> AttachmentOpenResult:
    """A copy of one attachment in Chrono Mail's own folder for opened files, to open in the program the computer uses for its kind of
    file (the store is encrypted, so the other program cannot read it there); each copy in a folder of its own, so names never clash."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
    if not 0 <= parameters.index < len(parsed.attachments):
        raise UsageError("The message has no such attachment")
    attachment = parsed.attachments[parameters.index]
    name = suggest_filename(attachment.filename, attachment.content_type, parameters.index)
    opened = engine.directories.opened_directory
    try:
        opened.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(opened, 0o700)
        folder = opened / os.urandom(8).hex()
        folder.mkdir(mode=0o700)
        target = folder / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)  # getattr: O_BINARY exists on Windows only
        with os.fdopen(descriptor, "wb") as file:
            file.write(attachment.data)
    except OSError as error:
        raise UsageError(f"The attachment cannot be written for opening: {error.strerror}") from None
    return AttachmentOpenResult(str(target), is_program(name))


@dataclass(frozen=True, slots=True)
class CleanResult:
    removed: int


def message_attachment_clean_opened(engine: Engine, parameters: NoParameters) -> CleanResult:
    """Remove the copies of attachments opened in an earlier run (at start, D-236). A copy another program still holds open stays."""
    opened = engine.directories.opened_directory
    removed = 0
    for folder in opened.iterdir() if opened.is_dir() else ():
        try:
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)
            else:
                folder.unlink()
            removed += 1
        except OSError:
            continue
    return CleanResult(removed)


MESSAGE_ATTACHMENT_OPEN: TypedCommand[AttachmentOpenParameters, AttachmentOpenResult] = TypedCommand("message.attachment.open", AttachmentOpenParameters, AttachmentOpenResult)
MESSAGE_ATTACHMENT_CLEAN_OPENED: TypedCommand[NoParameters, CleanResult] = TypedCommand("message.attachment.clean-opened", NoParameters, CleanResult)


def register(registry: CommandRegistry) -> None:
    registry.register(MESSAGE_ATTACHMENT_OPEN.id, "Copy one attachment to a private folder, to open it in another program", AttachmentOpenParameters, message_attachment_open)
    registry.register(MESSAGE_ATTACHMENT_CLEAN_OPENED.id, "Remove the copies of attachments opened in an earlier run", NoParameters, message_attachment_clean_opened)
