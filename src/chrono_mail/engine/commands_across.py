"""Moving messages to a folder of another account (D-272). There is no server command for it, so each message goes in three steps, and
the next message only after the last step of this one: it is read with its marks and tags in its own account, stored in the other (in an
IMAP account on the server first; offline it is stored here and the next check puts it there) and read back, and only then removed from
its own account for good (on an IMAP server first; offline the next check does it). Stopped at any moment, a message is in one account or
in both, never in neither (AGENTS.md 4.1). The two accounts are never open at the same time, so two moves the other way round never wait
for each other; messages go in groups, each account opened once per group."""

from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.commands_mail import _check_id
from chrono_mail.engine.commands_many import MAXIMUM_MESSAGES, ManyResult, NotDone, _not_done
from chrono_mail.engine.junkfilter import learn
from chrono_mail.engine.receiving import WAITING_ROLES
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.engine.undohistory import UndoStep

# --- Version ------------------------------------------------------------
__version__ = "commands_across.py 4.2.418"

# --- Version history ----------------------------------------------------
# v4.2.418: a message moved to another account keeps the account that received it (D-292)
# v3.6.366: Initial: many messages of a folder moved to a folder of another account, message by message, never lost when stopped (D-272)

GROUP: int = 50  # messages per opening of each account
NOT_THERE: frozenset[FolderRole] = frozenset({FolderRole.OUTBOX, FolderRole.SCHEDULED})  # on this computer only, for its own account's sending


@dataclass(frozen=True, slots=True)
class AcrossParameters:
    account: str = field(metadata=option("The account the messages are in"))
    folder: str = field(metadata=option("The folder the messages are in"))
    target_account: str = field(metadata=option("The account to move them to"))
    target: str = field(metadata=option("The folder of that account to move them to"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class _Taken:
    ref: MessageRef
    raw: bytes
    flags: frozenset[MessageFlag]
    tags: tuple[str, ...]
    received_by: str  # the account that received it: it goes along, never changed (D-292); "" when not known


def _roles(mailbox: Mailbox) -> dict[tuple[str, ...], FolderRole]:
    return {folder.path: folder.role for folder in mailbox.store.list_folders()}


def _receiver_of(mailbox: Mailbox, store_id: str, waiting: bool) -> str:
    """The account recorded as having received the message, else the one its list shows; "" for a message not sent yet (its From
    decides, D-292)."""
    if waiting:
        return ""
    try:
        recorded = mailbox.store.received_by.of(store_id)
    except ChronoMailError:
        recorded = None
    return recorded if recorded is not None else mailbox.index.receiver_of(store_id)


def _take(engine: Engine, account: str, folder: str, messages: list[str], failed: list[NotDone]) -> tuple[tuple[str, ...], bool, list[_Taken]]:
    """Step 1, in the account they are in: each message with its marks and tags (one that cannot be read stays where it is)."""
    taken: list[_Taken] = []
    with open_mailbox(engine, account) as source:
        path = source.folder(folder)
        role = _roles(source).get(path)
        if role in NOT_THERE:
            raise UsageError("Messages of the Outbox or Scheduled stay in their own account")
        for message in messages:
            try:
                ref = source.reference(path, _check_id(message))
                raw = source.store.read(ref, hint=source.hint(ref.store_id))
                taken.append(_Taken(ref, raw, source.current_flags(ref), source.tags_of(ref.store_id), _receiver_of(source, ref.store_id, role in WAITING_ROLES)))
            except ChronoMailError as error:
                failed.append(_not_done(message, error))
    return path, role is FolderRole.JUNK, taken


def _store(engine: Engine, account: str, target: str, taken: list[_Taken], from_junk: bool, failed: list[NotDone]) -> tuple[tuple[str, ...], list[_Taken]]:
    """Step 2, in the other account: each message stored under its own id and read back (on an IMAP server first), with its tags. The junk
    filter learns as for a move within one account: into Junk is junk, out of Junk (not to Trash) is good."""
    stored: list[_Taken] = []
    with open_mailbox(engine, account) as destination, destination.together():
        path = destination.folder(target)
        role = _roles(destination).get(path)
        if role in NOT_THERE:
            raise UsageError("The Outbox and Scheduled take only their own account's messages")
        for item in taken:
            try:
                if item.received_by:  # written before the message arrives, so its row shows it at once
                    try:
                        destination.store.received_by.record(item.ref.store_id, item.received_by)
                    except ChronoMailError:
                        pass  # the index's receiver records it; the move goes on
                arrived = destination.deliver_moved(path, item.raw, item.flags, item.ref.store_id)
            except ChronoMailError as error:
                failed.append(_not_done(item.ref.store_id, error))
                continue
            stored.append(item)
            if item.tags:
                try:
                    destination.set_tags(arrived, item.tags)
                except ChronoMailError:
                    pass  # the message is safe there; its tags are a matter of its next change
        references = [destination.reference(path, item.ref.store_id) for item in stored]
        if role is FolderRole.JUNK and not from_junk:
            learn(destination, references, junk=True)
        elif from_junk and role not in (FolderRole.JUNK, FolderRole.TRASH):
            learn(destination, references, junk=False)
    return path, stored


def _remove(engine: Engine, account: str, stored: list[_Taken], failed: list[NotDone]) -> list[_Taken]:
    """Step 3, in the account they came from: the originals removed for good (on an IMAP server first). One that cannot be removed is
    in both accounts now, never in neither: said so."""
    removed: list[_Taken] = []
    if not stored:
        return removed
    with open_mailbox(engine, account) as source, source.together():
        try:
            source.prepare_deletes([item.ref for item in stored], permanent=True)
        except ChronoMailError as error:  # the server said no for the group: each copy stays in both accounts
            failed.extend(NotDone(item.ref.store_id, f"It is in both accounts now: {error}") for item in stored)
            return removed
        for item in stored:
            try:
                source.delete(item.ref, permanent=True)
            except ChronoMailError as error:
                failed.append(NotDone(item.ref.store_id, f"It is in both accounts now: {error}"))
                continue
            removed.append(item)
    return removed


def move_across(engine: Engine, account: str, folder: str, target_account: str, target: str, messages: tuple[str, ...]) -> tuple[ManyResult, list[UndoStep]]:
    """The messages named moved to a folder of another account, group by group; the steps of what was done (for Undo)."""
    if account == target_account:
        raise UsageError("Both folders are in the same account: that is an ordinary move")
    if len(messages) > MAXIMUM_MESSAGES:
        raise UsageError(f"At most {MAXIMUM_MESSAGES:,} messages at once")
    wanted = list(dict.fromkeys(messages))
    failed: list[NotDone] = []
    steps: list[UndoStep] = []
    for start in range(0, len(wanted), GROUP):
        source_path, from_junk, taken = _take(engine, account, folder, wanted[start:start + GROUP], failed)
        target_path, stored = _store(engine, target_account, target, taken, from_junk, failed)
        for item in _remove(engine, account, stored, failed):
            steps.append(UndoStep(account, item.ref.store_id, source_path, target_path, kind="across", other=target_account))
    return ManyResult(tuple(step.message for step in steps), tuple(failed)), steps


def message_move_across(engine: Engine, parameters: AcrossParameters) -> ManyResult:
    """Move many messages of a folder to a folder of another account."""
    result, steps = move_across(engine, parameters.account, parameters.folder, parameters.target_account, parameters.target, parameters.messages)
    engine.undo.record(parameters.undo, f"Move to {parameters.target}", steps)
    return result


MESSAGE_MOVE_ACROSS: TypedCommand[AcrossParameters, ManyResult] = TypedCommand("message.move-across", AcrossParameters, ManyResult)


def register(registry: CommandRegistry) -> None:
    registry.register(MESSAGE_MOVE_ACROSS.id, "Move many messages of a folder to a folder of another account", AcrossParameters, message_move_across, doing="Moving messages to another account")
