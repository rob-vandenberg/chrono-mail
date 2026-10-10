import os
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.engine.commands_mail import MessageParameters, _check_id, _shown
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.index import html_to_text
from chrono_mail.mime import parse_message

# --- Version ------------------------------------------------------------
__version__ = "commands_export.py 0.0.22"

# --- Version history ----------------------------------------------------
# v0.0.22: Initial export, source and plain text commands

MAXIMUM_SOURCE_CHARACTERS: int = 2_000_000
MAXIMUM_TEXT_CHARACTERS: int = 5_000_000


@dataclass(frozen=True, slots=True)
class ExportParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the message is in"))
    message: str = field(metadata=option("The message id"))
    path: str = field(metadata=option("The file to write, usually ending in .eml"))
    overwrite: bool = field(default=False, metadata=option("Replace an existing file"))


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: str
    size: int


def message_export(engine: Engine, parameters: ExportParameters) -> ExportResult:
    """Write the message, exactly as it was received, to a file (a .eml file other mail programs can open).
    The file is not encrypted: it is a copy for use outside Chrono Mail."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
    target = Path(parameters.path)
    if target.exists() and not parameters.overwrite:
        raise UsageError("The file exists; use overwrite to replace it")
    temporary = target.with_name(f".{target.name}.{os.urandom(4).hex()}.part")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(descriptor, raw)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary.replace(target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise UsageError(f"The file cannot be written: {error.strerror}") from None
    return ExportResult(str(target), len(raw))


@dataclass(frozen=True, slots=True)
class SourceResult:
    text: str
    size: int
    truncated: bool


def message_source(engine: Engine, parameters: MessageParameters) -> SourceResult:
    """The message as it is stored: all headers and the body in their raw form, for finding out why a message looks the way it does."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        raw = mailbox.store.read(reference, hint=mailbox.hint(reference.store_id))
    text = raw[: MAXIMUM_SOURCE_CHARACTERS * 4].decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\x00", "�")
    return SourceResult(text[:MAXIMUM_SOURCE_CHARACTERS], len(raw), len(text) > MAXIMUM_SOURCE_CHARACTERS or len(raw) > MAXIMUM_SOURCE_CHARACTERS * 4)


@dataclass(frozen=True, slots=True)
class TextResult:
    subject: str
    sender: tuple[str, ...]
    to: tuple[str, ...]
    cc: tuple[str, ...]
    date: str
    text: str
    attachments: tuple[str, ...]


def message_text(engine: Engine, parameters: MessageParameters) -> TextResult:
    """The message as plain text (for printing): the text part, or the visible text of the HTML part. Nothing is loaded from the internet."""
    with open_mailbox(engine, parameters.account) as mailbox:
        reference = mailbox.reference(mailbox.folder(parameters.folder), _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
    body = parsed.text_body or (html_to_text(parsed.html_body, keep_lines=True) if parsed.html_body else "")
    names = tuple(attachment.filename or attachment.content_type for attachment in parsed.attachments if not attachment.inline)
    return TextResult(
        parsed.subject, _shown(parsed.from_addresses), _shown(parsed.to_addresses), _shown(parsed.cc_addresses),
        parsed.date.astimezone().strftime("%A %d %B %Y, %H:%M") if parsed.date else "", body[:MAXIMUM_TEXT_CHARACTERS], names,
    )


def register(registry: CommandRegistry) -> None:
    registry.register("message.export", "Save a message as an .eml file", ExportParameters, message_export)
    registry.register("message.source", "Show the raw source of a message", MessageParameters, message_source)
    registry.register("message.text", "A message as plain text (used for printing)", MessageParameters, message_text)
