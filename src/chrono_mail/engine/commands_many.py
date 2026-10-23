"""Actions on many messages of one folder at once (R-08): delete, move, copy, marks, archive, unarchive, junk, not junk, snooze. On an IMAP server
the group goes in one command per 500 messages (Mailbox.prepare_*), then each message's own step runs as it would alone and tells its fact; a
message that fails is named with its reason and the others go on. The answer says which were done and which not."""

from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, Folder, FolderRole, MessageFlag, MessageRef
from chrono_mail.engine.changes import flag_names
from chrono_mail.engine.commands_actions import JunkResult, archive_folder, archive_one, junk_one, junk_places, not_junk_one, unarchive_one, unarchive_target
from chrono_mail.engine.commands_mail import _check_id, flags_wanted
from chrono_mail.engine.commands_schedule import snooze_one
from chrono_mail.engine.junkfilter import learn, learn_from_move
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.schedule import SNOOZED_FOLDER, parse_when
from chrono_mail.engine.session import Engine
from chrono_mail.engine.undorecord import UndoRecorder, Verdicts
from chrono_mail.store import MessageNotFoundError

# --- Version ------------------------------------------------------------
__version__ = "commands_many.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: undo: an action given with a number notes each message before and after, so Undo can put it back (B-18)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v2.1.308: Junk, Not junk and moves into or out of Junk teach the junk filter, once for the whole group (D-238)
# v1.9.265: NotDone.missing: the message was not done because its file is gone here, so the window warns about it (D-109; lost in 1.1.247)
# v1.1.247: Initial: many messages of a folder in one command, on the server in groups (R-08)

MAXIMUM_MESSAGES: int = 100_000  # in one command


@dataclass(frozen=True, slots=True)
class ManyParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class ManyTargetParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    target: str = field(metadata=option("The folder to move or copy them to"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class ManyDeleteParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    permanent: bool = field(default=False, metadata=option("Remove for good instead of moving to Trash"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class ManyFlagParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    seen: bool | None = field(default=None, metadata=option("Mark read (true) or unread (false)"))
    flagged: bool | None = field(default=None, metadata=option("Flag (true) or unflag (false)"))
    replied: bool | None = field(default=None, metadata=option("Mark as replied to (true) or not (false)"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class ManySnoozeParameters:
    account: str = field(metadata=option("The account"))
    folder: str = field(metadata=option("The folder the messages are in"))
    until: str = field(metadata=option("When they come back, a local time like 2026-10-11T09:00"))
    messages: tuple[str, ...] = field(default=(), metadata=option("A message id (repeat for more)"))
    undo: int = field(default=0, metadata=option("The number of the action, so Undo can put it back (0: it is not undone)"))


@dataclass(frozen=True, slots=True)
class NotDone:
    message: str
    reason: str
    missing: bool = False  # its file is gone here (it is only in the index): the window warns and advises repairing the index (D-109)


def _not_done(message: str, error: ChronoMailError) -> NotDone:
    return NotDone(message, str(error), isinstance(error, MessageNotFoundError))


@dataclass(frozen=True, slots=True)
class ManyResult:
    done: tuple[str, ...]  # the messages that were done (by the ids they were given with)
    failed: tuple[NotDone, ...] = ()  # the ones that were not, each with its reason; nothing changed for them
    gone: tuple[str, ...] = ()  # done ones whose file was gone here (they only left the index)
    flags: tuple[tuple[str, tuple[str, ...]], ...] = ()  # marks: each done message with its marks now


def _each(
    engine: Engine, account: str, folder: str, messages: tuple[str, ...], prepare: Callable[[Mailbox, list[MessageRef]], None],
    act: Callable[[Mailbox, MessageRef], object], after: Callable[[Mailbox, list[tuple[MessageRef, object]]], Verdicts] | None = None,
    undo: tuple[int, str] = (0, ""),
) -> tuple[ManyResult, list[tuple[MessageRef, object]]]:
    """The group: the messages named, prepared on the server together, then each one's own step; a failure is that message's alone.
    after: what follows for the ones done, once for the group (the junk filter learns them, and says what it learned).
    undo: the action's number and name: each message is noted before and after, so Undo can put it back (B-18)."""
    if len(messages) > MAXIMUM_MESSAGES:
        raise UsageError(f"At most {MAXIMUM_MESSAGES:,} messages at once")
    failed: list[NotDone] = []
    answers: list[tuple[MessageRef, object]] = []
    with open_mailbox(engine, account) as mailbox:
        source = mailbox.folder(folder)
        refs: list[MessageRef] = []
        for message in dict.fromkeys(messages):
            try:
                refs.append(mailbox.reference(source, _check_id(message)))
            except ChronoMailError as error:
                failed.append(_not_done(message, error))
        recorder = UndoRecorder(mailbox, account, undo[0])
        recorder.before(ref.store_id for ref in refs)
        with mailbox.together():
            try:
                prepare(mailbox, refs)
            except ChronoMailError as error:  # the server said no for the whole group: nothing was done there or here
                return ManyResult((), (*failed, *(NotDone(ref.store_id, str(error)) for ref in refs))), []
            for ref in refs:
                try:
                    answers.append((ref, act(mailbox, ref)))
                except ChronoMailError as error:
                    failed.append(_not_done(ref.store_id, error))
        verdicts = after(mailbox, answers) if after is not None and answers else {}
        copies = [(ref, answer) for ref, answer in answers if isinstance(answer, MessageRef) and answer.store_id != ref.store_id]  # a copy has an id of its own
        for original, copy in copies:
            recorder.copied(original, copy)
        copied = {original.store_id for original, _ in copies}
        recorder.after((ref.store_id for ref, _ in answers if ref.store_id not in copied), verdicts)
        recorder.keep(undo[1])
    return ManyResult(tuple(ref.store_id for ref, _ in answers), tuple(failed)), answers


def message_delete_many(engine: Engine, parameters: ManyDeleteParameters) -> ManyResult:
    """Move many messages of a folder to Trash, or remove them for good (permanent, or in Trash)."""
    result, answers = _each(
        engine, parameters.account, parameters.folder, parameters.messages, lambda mailbox, refs: mailbox.prepare_deletes(refs, parameters.permanent),
        lambda mailbox, ref: mailbox.delete(ref, parameters.permanent), undo=(parameters.undo, "Move to Trash"),
    )
    return ManyResult(result.done, result.failed, tuple(ref.store_id for ref, outcome in answers if outcome == "gone"))


def message_move_many(engine: Engine, parameters: ManyTargetParameters) -> ManyResult:
    target: tuple[str, ...] = ()

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        nonlocal target
        target = mailbox.folder(parameters.target)
        mailbox.prepare_moves(refs, target)

    def taught(mailbox: Mailbox, answers: list[tuple[MessageRef, object]]) -> Verdicts:  # into or out of Junk (D-238)
        return learn_from_move(mailbox, answers[0][0].folder_path, [moved for _, moved in answers if isinstance(moved, MessageRef)])

    return _each(
        engine, parameters.account, parameters.folder, parameters.messages, prepare, lambda mailbox, ref: mailbox.move(ref, target), taught,
        undo=(parameters.undo, f"Move to {parameters.target}"),
    )[0]


def message_copy_many(engine: Engine, parameters: ManyTargetParameters) -> ManyResult:
    target: tuple[str, ...] = ()

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        nonlocal target
        target = mailbox.folder(parameters.target)
        mailbox.prepare_copies(refs, target)

    return _each(
        engine, parameters.account, parameters.folder, parameters.messages, prepare, lambda mailbox, ref: mailbox.copy(ref, target),
        undo=(parameters.undo, f"Copy to {parameters.target}"),
    )[0]


def message_flag_many(engine: Engine, parameters: ManyFlagParameters) -> ManyResult:
    """Mark many messages read or unread, flagged or not, replied or not."""
    wanted: dict[str, frozenset[MessageFlag]] = {}

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        marks: dict[MessageRef, frozenset[MessageFlag]] = {}
        for ref in refs:
            try:
                marks[ref] = flags_wanted(mailbox.current_flags(ref), parameters.seen, parameters.flagged, parameters.replied)
            except ChronoMailError:
                continue  # its own step says why
        wanted.update((ref.store_id, flags) for ref, flags in marks.items())
        mailbox.prepare_flags(marks)

    def act(mailbox: Mailbox, ref: MessageRef) -> frozenset[MessageFlag]:
        flags = wanted.get(ref.store_id) or flags_wanted(mailbox.current_flags(ref), parameters.seen, parameters.flagged, parameters.replied)
        return mailbox.set_flags(ref, flags)

    result, answers = _each(engine, parameters.account, parameters.folder, parameters.messages, prepare, act, undo=(parameters.undo, _marks_name(parameters)))
    marks = tuple((ref.store_id, flag_names(flags)) for ref, flags in answers if isinstance(flags, frozenset))
    return ManyResult(result.done, result.failed, flags=marks)


def _marks_name(parameters: ManyFlagParameters) -> str:
    """What a change of marks is called in Undo: "Mark as read", "Flag", or "Change the marks" for more at once."""
    names: list[str] = []
    if parameters.seen is not None:
        names.append("Mark as read" if parameters.seen else "Mark as unread")
    if parameters.flagged is not None:
        names.append("Flag" if parameters.flagged else "Unflag")
    if parameters.replied is not None:
        names.append("Mark as replied" if parameters.replied else "Mark as not replied")
    return names[0] if len(names) == 1 else "Change the marks"


def message_archive_many(engine: Engine, parameters: ManyParameters) -> ManyResult:
    archive: list[tuple[Folder, bool]] = []  # the archive folder, found (or made) once

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        archive.append(archive_folder(mailbox))
        mailbox.prepare_moves(refs, archive[0][0].path)

    def act(mailbox: Mailbox, ref: MessageRef) -> object:
        return archive_one(mailbox, ref, *archive[0])

    return _each(engine, parameters.account, parameters.folder, parameters.messages, prepare, act, undo=(parameters.undo, "Archive"))[0]


def message_unarchive_many(engine: Engine, parameters: ManyParameters) -> ManyResult:
    """Back to the folder each came from: grouped by that folder on the server."""
    targets: dict[str, tuple[tuple[str, ...], bool]] = {}

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        groups: dict[tuple[str, ...], list[MessageRef]] = {}
        for ref in refs:
            try:
                targets[ref.store_id] = unarchive_target(mailbox, ref)
            except ChronoMailError:
                continue
            groups.setdefault(targets[ref.store_id][0], []).append(ref)
        for target, members in groups.items():
            mailbox.prepare_moves(members, target)

    def act(mailbox: Mailbox, ref: MessageRef) -> object:
        return unarchive_one(mailbox, ref, *(targets.get(ref.store_id) or unarchive_target(mailbox, ref)))

    return _each(engine, parameters.account, parameters.folder, parameters.messages, prepare, act, undo=(parameters.undo, "Unarchive"))[0]


def _junk_results(answers: list[tuple[MessageRef, object]]) -> list[JunkResult]:
    return [answer for _, answer in answers if isinstance(answer, JunkResult)]


def message_junk_many(engine: Engine, parameters: ManyParameters) -> ManyResult:
    result, answers = _each(
        engine, parameters.account, parameters.folder, parameters.messages,
        lambda mailbox, refs: mailbox.prepare_moves(refs, mailbox.role_folder(FolderRole.JUNK)),
        lambda mailbox, ref: junk_one(mailbox, ref, mailbox.role_folder(FolderRole.JUNK)),
        lambda mailbox, answers: learn(mailbox, junk_places(mailbox, _junk_results(answers)), junk=True), undo=(parameters.undo, "Move to Junk"),
    )
    return ManyResult(result.done, result.failed, tuple(ref.store_id for ref, answer in answers if getattr(answer, "gone", False)))


def message_not_junk_many(engine: Engine, parameters: ManyParameters) -> ManyResult:
    return _each(
        engine, parameters.account, parameters.folder, parameters.messages,
        lambda mailbox, refs: mailbox.prepare_moves(refs, mailbox.role_folder(FolderRole.INBOX)), not_junk_one,
        lambda mailbox, answers: learn(mailbox, junk_places(mailbox, _junk_results(answers)), junk=False), undo=(parameters.undo, "Not junk"),
    )[0]


def message_snooze_many(engine: Engine, parameters: ManySnoozeParameters) -> ManyResult:
    until = parse_when(parameters.until, engine.clock())

    def prepare(mailbox: Mailbox, refs: list[MessageRef]) -> None:
        mailbox.ensure_folder((SNOOZED_FOLDER,))
        mailbox.prepare_moves(refs, (SNOOZED_FOLDER,))

    return _each(
        engine, parameters.account, parameters.folder, parameters.messages, prepare, lambda mailbox, ref: snooze_one(mailbox, ref, until), undo=(parameters.undo, "Snooze"),
    )[0]


MESSAGE_DELETE_MANY: TypedCommand[ManyDeleteParameters, ManyResult] = TypedCommand("message.delete-many", ManyDeleteParameters, ManyResult)
MESSAGE_MOVE_MANY: TypedCommand[ManyTargetParameters, ManyResult] = TypedCommand("message.move-many", ManyTargetParameters, ManyResult)
MESSAGE_COPY_MANY: TypedCommand[ManyTargetParameters, ManyResult] = TypedCommand("message.copy-many", ManyTargetParameters, ManyResult)
MESSAGE_FLAG_MANY: TypedCommand[ManyFlagParameters, ManyResult] = TypedCommand("message.flag-many", ManyFlagParameters, ManyResult)
MESSAGE_ARCHIVE_MANY: TypedCommand[ManyParameters, ManyResult] = TypedCommand("message.archive-many", ManyParameters, ManyResult)
MESSAGE_UNARCHIVE_MANY: TypedCommand[ManyParameters, ManyResult] = TypedCommand("message.unarchive-many", ManyParameters, ManyResult)
MESSAGE_JUNK_MANY: TypedCommand[ManyParameters, ManyResult] = TypedCommand("message.junk-many", ManyParameters, ManyResult)
MESSAGE_NOT_JUNK_MANY: TypedCommand[ManyParameters, ManyResult] = TypedCommand("message.not-junk-many", ManyParameters, ManyResult)
MESSAGE_SNOOZE_MANY: TypedCommand[ManySnoozeParameters, ManyResult] = TypedCommand("message.snooze-many", ManySnoozeParameters, ManyResult)


def register(registry: CommandRegistry) -> None:
    registry.register(MESSAGE_DELETE_MANY.id, "Move many messages of a folder to Trash, or delete them for good", ManyDeleteParameters, message_delete_many, doing="Deleting messages")
    registry.register(MESSAGE_MOVE_MANY.id, "Move many messages of a folder to another folder", ManyTargetParameters, message_move_many, doing="Moving messages")
    registry.register(MESSAGE_COPY_MANY.id, "Copy many messages of a folder to another folder", ManyTargetParameters, message_copy_many, doing="Copying messages")
    registry.register(MESSAGE_FLAG_MANY.id, "Mark many messages of a folder read or unread, flagged or not", ManyFlagParameters, message_flag_many, doing="Changing the marks of messages")
    registry.register(MESSAGE_ARCHIVE_MANY.id, "Move many messages of a folder to the Archive folder", ManyParameters, message_archive_many, doing="Archiving messages")
    registry.register(MESSAGE_UNARCHIVE_MANY.id, "Move many archived messages back to where each came from", ManyParameters, message_unarchive_many, doing="Moving messages out of the archive")
    registry.register(MESSAGE_JUNK_MANY.id, "Move many messages of a folder to the Junk folder", ManyParameters, message_junk_many, doing="Moving messages to Junk")
    registry.register(MESSAGE_NOT_JUNK_MANY.id, "Move many messages out of the Junk folder to the Inbox", ManyParameters, message_not_junk_many, doing="Taking messages out of Junk")
    registry.register(MESSAGE_SNOOZE_MANY.id, "Put many messages of a folder away until a time", ManySnoozeParameters, message_snooze_many, doing="Snoozing messages")
