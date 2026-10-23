from dataclasses import dataclass, field
from typing import Literal

from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag
from chrono_mail.engine.commands_mail import _addresses, _attachment, _check_id, _shown, kept_attachments
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.index import html_to_text
from chrono_mail.mime import Address, OutgoingMessage, build_message, parse_message

# --- Version ------------------------------------------------------------
__version__ = "commands_drafts.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.9.270: R-19: typed draft.open, draft.save
# v0.0.86: A draft can be a forward as attachment
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.24: Rich text composing
# v0.0.16: Initial draft commands

DraftKind = Literal["new", "reply", "reply_all", "forward", "forward_attachment"]
DRAFT_FLAGS: frozenset[MessageFlag] = frozenset({MessageFlag.DRAFT, MessageFlag.SEEN})


@dataclass(frozen=True, slots=True)
class DraftSaveParameters:
    account: str = field(metadata=option("The account the draft belongs to"))
    to: tuple[str, ...] = field(default=(), metadata=option("A recipient (repeat for more)"))
    cc: tuple[str, ...] = field(default=(), metadata=option("A copy recipient (repeat for more)"))
    bcc: tuple[str, ...] = field(default=(), metadata=option("A blind copy recipient (repeat for more)"))
    subject: str = field(default="", metadata=option("The subject"))
    body: str = field(default="", metadata=option("The text"))
    html: str | None = field(default=None, metadata=option("An HTML version of the body (a rich text draft)"))
    attach: tuple[str, ...] = field(default=(), metadata=option("A file to attach (repeat for more)"))
    keep_attachments: tuple[str, ...] = field(default=(), metadata=option("The position of an attachment of the earlier version of this draft to keep (repeat for more)"))
    kind: DraftKind = field(default="new", metadata=option("What the draft is: new, reply, reply_all, forward or forward_attachment"))
    in_reply_to: str | None = field(default=None, metadata=option("The Message-ID this answers"))
    references: tuple[str, ...] = field(default=(), metadata=option("A Message-ID of the conversation (repeat for more)"))
    source_folder: str | None = field(default=None, metadata=option("The folder of the message this answers or forwards"))
    source_message: str | None = field(default=None, metadata=option("The id of the message this answers or forwards"))
    replace_folder: str | None = field(default=None, metadata=option("The folder of the earlier version of this draft"))
    replace_message: str | None = field(default=None, metadata=option("The id of the earlier version of this draft; it is removed once the new one is stored"))


@dataclass(frozen=True, slots=True)
class DraftSaveResult:
    message_id: str
    folder: str


def draft_save(engine: Engine, parameters: DraftSaveParameters) -> DraftSaveResult:
    """Store the message being written in the Drafts folder. A later save replaces the earlier version, but
    only after the new one is safely stored, so a failure never loses the draft."""
    with open_mailbox(engine, parameters.account) as mailbox:
        settings = mailbox.settings
        drafts = mailbox.role_folder(FolderRole.DRAFTS)
        previous = None
        previous_raw: bytes | None = None
        if parameters.replace_message:
            previous = mailbox.reference(mailbox.folder(parameters.replace_folder or "Drafts"), _check_id(parameters.replace_message))
            previous_raw = mailbox.store.read(previous, hint=mailbox.hint(previous.store_id))
        notes: list[tuple[str, str]] = [("X-Chrono-Mail-Kind", parameters.kind)]
        if parameters.source_folder and parameters.source_message:
            notes.append(("X-Chrono-Mail-Source", f"{parameters.source_folder}/{_check_id(parameters.source_message)}"))
        outgoing = OutgoingMessage(
            from_address=Address(settings.account.display_name, settings.account.email_address),
            to_addresses=_addresses(parameters.to),
            cc_addresses=_addresses(parameters.cc),
            bcc_addresses=_addresses(parameters.bcc),
            subject=parameters.subject,
            text_body=parameters.body,
            html_body=parameters.html,
            attachments=(*kept_attachments(previous_raw, parameters.keep_attachments), *(_attachment(path) for path in parameters.attach)),
            in_reply_to=parameters.in_reply_to,
            references=parameters.references,
            extra_headers=tuple(notes),
        )
        saved = mailbox.deliver(drafts, build_message(outgoing, draft=True), DRAFT_FLAGS)
        if previous is not None and previous != saved:
            try:
                mailbox.delete(previous, permanent=True)
            except ChronoMailError:
                pass  # two versions are better than none; the older one can be deleted by hand
        return DraftSaveResult(saved.store_id, "/".join(drafts))


@dataclass(frozen=True, slots=True)
class DraftOpenParameters:
    account: str = field(metadata=option("The account"))
    message: str = field(metadata=option("The id of the draft (or of a message in the Outbox that is to be edited and sent again)"))
    folder: str = field(default="Drafts", metadata=option("The folder it is in"))


@dataclass(frozen=True, slots=True)
class DraftAttachment:
    position: int
    name: str
    size: int


@dataclass(frozen=True, slots=True)
class DraftOpenResult:
    message_id: str
    folder: str
    kind: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[DraftAttachment, ...]
    in_reply_to: str | None
    references: tuple[str, ...]
    source_folder: str | None
    source_message: str | None
    html: str | None = None  # the HTML version, when the draft is a rich text message
    from_address: str = ""  # the address the message is written from


def draft_open(engine: Engine, parameters: DraftOpenParameters) -> DraftOpenResult:
    """The content of a draft, for editing it further."""
    with open_mailbox(engine, parameters.account) as mailbox:
        folder = mailbox.folder(parameters.folder)
        reference = mailbox.reference(folder, _check_id(parameters.message))
        parsed = parse_message(mailbox.store.read(reference, hint=mailbox.hint(reference.store_id)))
        notes = dict(parsed.chrono_headers)
        kind = notes.get("x-chrono-mail-kind", "new")
        source_folder: str | None = None
        source_message: str | None = None
        source = notes.get("x-chrono-mail-source", "")
        if "/" in source:
            source_folder, _, source_message = source.rpartition("/")
        body = parsed.text_body or (html_to_text(parsed.html_body, keep_lines=True) if parsed.html_body else "")
        return DraftOpenResult(
            reference.store_id,
            "/".join(folder),
            kind if kind in ("new", "reply", "reply_all", "forward", "forward_attachment") else "new",
            _shown(parsed.to_addresses),
            _shown(parsed.cc_addresses),
            _shown(parsed.bcc_addresses),
            parsed.subject,
            body,
            tuple(DraftAttachment(position, attachment.filename or f"attachment-{position + 1}", len(attachment.data)) for position, attachment in enumerate(parsed.attachments) if not attachment.inline),
            parsed.in_reply_to,
            parsed.references,
            source_folder,
            source_message,
            parsed.html_body,
            parsed.from_addresses[0].address if parsed.from_addresses else "",
        )


DRAFT_OPEN: TypedCommand[DraftOpenParameters, DraftOpenResult] = TypedCommand("draft.open", DraftOpenParameters, DraftOpenResult)
DRAFT_SAVE: TypedCommand[DraftSaveParameters, DraftSaveResult] = TypedCommand("draft.save", DraftSaveParameters, DraftSaveResult)


def register(registry: CommandRegistry) -> None:
    registry.register(DRAFT_SAVE.id, "Save the message being written in the Drafts folder (replacing its earlier version)", DraftSaveParameters, draft_save, doing="Saving a draft")
    registry.register(DRAFT_OPEN.id, "Read a draft, to go on editing it", DraftOpenParameters, draft_open, doing="Opening a draft")
