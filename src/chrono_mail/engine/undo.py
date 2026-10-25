"""Undo and Redo of the person's actions on mail (B-18). An action given with a number notes how each message was before and after
(UndoRecorder); Undo makes each message as it was before, Redo as it was after. Both are new actions through the mailbox, so on
IMAP the server first and offline sent later (D-193, D-117); a message that is no longer where the action left it is left alone."""

from dataclasses import dataclass, replace

from chrono_mail.core import ChronoMailError, MessageRef
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.junkfilter import set_verdicts
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.engine.undohistory import Notes, UndoStep
from chrono_mail.engine.undorecord import folded
from chrono_mail.store import clean_tags

# --- Version ------------------------------------------------------------
__version__ = "undo.py 2.2.313"

# --- Version history ----------------------------------------------------
# v2.2.313: Initial: the steps of an action done again or backwards, mail.undo, mail.redo and mail.undo-labels (B-18)

CHANGED_SINCE: str = "it was moved or deleted since"


# --- doing the steps ---------------------------------------------------------------------------------------------------------------------------


def _write_notes(mailbox: Mailbox, store_id: str, notes: Notes) -> None:
    mailbox.store.properties.change(
        store_id, lambda current: replace(current, snoozed_until=notes.snoozed_until, snoozed_from=notes.snoozed_from, archived_from=notes.archived_from),
    )


def _sets_notes(step: UndoStep) -> bool:
    """Notes that say something are written before the move (Snoozed holds no message without its record), notes cleared after it."""
    return step.notes is not None and any(value is not None for value in (step.notes[1].snoozed_until, step.notes[1].snoozed_from, step.notes[1].archived_from))


def _do_changes(mailbox: Mailbox, steps: list[UndoStep], failed: list[str]) -> list[UndoStep]:
    done: list[UndoStep] = []
    moves: dict[tuple[tuple[str, ...], tuple[str, ...]], list[UndoStep]] = {}
    for step in steps:
        moves.setdefault((step.before, step.after), []).append(step)
    for (before, after), members in moves.items():
        ready: list[UndoStep] = []
        for step in members:
            try:
                if _sets_notes(step) and step.notes is not None:
                    _write_notes(mailbox, step.message, step.notes[1])
            except ChronoMailError as error:
                failed.append(str(error))
                continue
            ready.append(step)
        refs = {step.message: mailbox.reference(before, step.message) for step in ready}
        if before != after and ready:
            try:
                mailbox.ensure_folder(after)  # a folder deleted meanwhile is made again, as Unarchive does
                mailbox.prepare_moves(list(refs.values()), after)
            except ChronoMailError as error:  # the server said no for the whole group: nothing was done there or here
                failed.extend(str(error) for _ in ready)
                continue
        for step in ready:
            try:
                reference = mailbox.move(refs[step.message], after) if before != after else refs[step.message]
                _marks_and_tags(mailbox, reference, step)
                if step.notes is not None and not _sets_notes(step):
                    _write_notes(mailbox, step.message, step.notes[1])
            except ChronoMailError as error:
                failed.append(str(error))
                continue
            done.append(step)
    verdicts = [(mailbox.reference(step.after, step.message), step.verdict[1]) for step in done if step.verdict is not None]
    set_verdicts(mailbox, verdicts)
    return done


def _marks_and_tags(mailbox: Mailbox, reference: MessageRef, step: UndoStep) -> None:
    if step.flags_added or step.flags_removed:
        current = mailbox.current_flags(reference)
        wanted = (current | step.flags_added) - step.flags_removed
        if wanted != current:
            mailbox.set_flags(reference, wanted)
    if step.tags_added or step.tags_removed:
        current_tags = mailbox.tags_of(step.message)
        removed = folded(step.tags_removed)
        wanted_tags = clean_tags([*(tag for tag in current_tags if tag.casefold() not in removed), *step.tags_added])
        if folded(wanted_tags) != folded(current_tags):
            mailbox.set_tags(reference, wanted_tags)


def _do_copies(mailbox: Mailbox, steps: list[UndoStep], failed: list[str]) -> list[UndoStep]:
    """Copies made again (Redo of Copy): each gets a new id."""
    done: list[UndoStep] = []
    for step in steps:
        try:
            copy = mailbox.copy(mailbox.reference(step.before, step.original), step.after)
        except ChronoMailError as error:
            failed.append(str(error))
            continue
        done.append(replace(step, message=copy.store_id))
    return done


def _do_uncopies(mailbox: Mailbox, steps: list[UndoStep], failed: list[str]) -> list[UndoStep]:
    """Copies removed for good (Undo of Copy): the original was never touched."""
    refs = [mailbox.reference(step.before, step.message) for step in steps]
    mailbox.prepare_deletes(refs, permanent=True)
    done: list[UndoStep] = []
    for step, reference in zip(steps, refs, strict=True):
        try:
            mailbox.delete(reference, permanent=True)
        except ChronoMailError as error:
            failed.append(str(error))
            continue
        done.append(step)
    return done


def _still_before(mailbox: Mailbox, step: UndoStep) -> bool:
    """The message is still where the step starts from (for a copy made again: the original)."""
    row = mailbox.index.get(step.original if step.kind == "copy" else step.message)
    return row is not None and row.folder == step.before


def _do(engine: Engine, steps: list[UndoStep]) -> tuple[list[UndoStep], list[str]]:
    """Do these steps, account by account, the server first; returns the steps done (as done) and a reason for each one not done."""
    done: list[UndoStep] = []
    failed: list[str] = []
    accounts: dict[str, list[UndoStep]] = {}
    for step in steps:
        accounts.setdefault(step.account, []).append(step)
    for account, members in accounts.items():
        try:
            with open_mailbox(engine, account) as mailbox, mailbox.together():
                ready = [step for step in members if _still_before(mailbox, step)]
                failed.extend(CHANGED_SINCE for _ in range(len(members) - len(ready)))
                done.extend(_do_uncopies(mailbox, [step for step in ready if step.kind == "uncopy"], failed))
                done.extend(_do_copies(mailbox, [step for step in ready if step.kind == "copy"], failed))
                done.extend(_do_changes(mailbox, [step for step in ready if step.kind == "change"], failed))
        except ChronoMailError as error:
            failed.extend(str(error) for step in members if step not in done)
    return done, failed


# --- the commands --------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UndoResult:
    action: str  # what was undone or redone: "Move to Trash (3 messages)"
    messages: tuple[str, ...]  # the messages done, by their ids now (a copy removed is not among them)
    not_done: int = 0  # how many could not be done (each stays as it is)
    reason: str = ""  # why (the first reason)


@dataclass(frozen=True, slots=True)
class UndoLabels:
    undo: str  # what Undo would do now ("" for nothing)
    redo: str


def _run(engine: Engine, redo: bool) -> UndoResult:
    action = engine.undo.take(redo)
    if action is None:
        raise UsageError("Nothing to redo" if redo else "Nothing to undo")
    label = action.label()
    try:
        done, failed = _do(engine, [step if redo else step.reversed() for step in action.steps])
    except BaseException:
        engine.undo.put(action, redo=redo)  # a fault of the program: the action stays where it was
        raise
    action.steps = [step if redo else step.reversed() for step in done]
    engine.undo.put(action, redo=not redo)
    return UndoResult(label, tuple(step.message for step in done if step.kind != "uncopy"), len(failed), failed[0] if failed else "")


def mail_undo(engine: Engine, parameters: NoParameters) -> UndoResult:
    """Undo the last action on mail: every message as it was before (moved back, its marks, tags and notes as they were)."""
    return _run(engine, redo=False)


def mail_redo(engine: Engine, parameters: NoParameters) -> UndoResult:
    """Do again what was undone last."""
    return _run(engine, redo=True)


def mail_undo_labels(engine: Engine, parameters: NoParameters) -> UndoLabels:
    """What Undo and Redo would do now."""
    undo, redo = engine.undo.labels()
    return UndoLabels(undo, redo)


MAIL_UNDO: TypedCommand[NoParameters, UndoResult] = TypedCommand("mail.undo", NoParameters, UndoResult)
MAIL_REDO: TypedCommand[NoParameters, UndoResult] = TypedCommand("mail.redo", NoParameters, UndoResult)
MAIL_UNDO_LABELS: TypedCommand[NoParameters, UndoLabels] = TypedCommand("mail.undo-labels", NoParameters, UndoLabels)


def register(registry: CommandRegistry) -> None:
    registry.register(MAIL_UNDO.id, "Undo the last action on mail", NoParameters, mail_undo, doing="Undoing an action")
    registry.register(MAIL_REDO.id, "Do again the action on mail undone last", NoParameters, mail_redo, doing="Redoing an action")
    registry.register(MAIL_UNDO_LABELS.id, "What Undo and Redo would do now", NoParameters, mail_undo_labels, doing="Looking what can be undone")
