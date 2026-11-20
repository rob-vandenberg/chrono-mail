"""Undo and Redo of the folders the person renamed or deleted (B-18 step 2, D-369). Noted by folder.rename and folder.delete as steps
of the mail's undo history; done here again or backwards, on an IMAP server first (D-193): a rename back or again, a deleted folder
brought back from the store's .deleted with its mail and the rules its delete switched off, or deleted again. A folder that is no
longer where the action left it is left alone, and the status line says why."""

from dataclasses import replace

from chrono_mail.config import load_rules, save_rules
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.mailbox import Mailbox, open_mailbox
from chrono_mail.engine.session import Engine
from chrono_mail.engine.undohistory import UndoStep

# --- Version ------------------------------------------------------------
__version__ = "folderundo.py 4.7.657"

# --- Version history ----------------------------------------------------
# v4.7.657: Initial: a folder renamed back or again, a deleted folder brought back with its mail and its rules, or deleted again (D-369)

RENAMED_SINCE: str = "the folder was renamed, moved or deleted since"
TAKEN_NOW: str = "a folder with that name exists now"


# --- the rules a delete switched off -----------------------------------------------------------------------------------------------------------


def rules_on(engine: Engine) -> dict[str, bool]:
    """Every rule's id with whether it is on; empty when the rules cannot be read (then nothing is switched on again later)."""
    try:
        return {rule.rule_id: rule.enabled for rule in load_rules(engine.directories.rules_file).rules}
    except ChronoMailError:
        return {}


def switched_off(before: dict[str, bool], after: dict[str, bool]) -> tuple[str, ...]:
    """The rules a delete switched off: on before and off after, or made by it off (a rule split for this account, D-302)."""
    return tuple(rule for rule, on in after.items() if not on and before.get(rule, True))


def _switch_on(engine: Engine, rules: tuple[str, ...]) -> tuple[str, ...]:
    """The rules a delete switched off are on again, those that are still there and still off; one line each for the status line."""
    if not rules:
        return ()
    with engine.rules_lock:  # one read-change-save at a time (D-302)
        try:
            kept = load_rules(engine.directories.rules_file)
        except ChronoMailError:
            return ()
        again = [rule for rule in kept.rules if rule.rule_id in rules and not rule.enabled]
        if not again:
            return ()
        try:
            save_rules(engine.directories.rules_file, replace(kept, rules=tuple(replace(rule, enabled=True) if rule in again else rule for rule in kept.rules)))
        except ChronoMailError:
            return ()
    return tuple(f"Rule “{rule.name}” is on again" for rule in again)


# --- doing the steps ---------------------------------------------------------------------------------------------------------------------------


def _paths(mailbox: Mailbox) -> set[tuple[str, ...]]:
    return {folder.path for folder in mailbox.folders()}


def _rename(mailbox: Mailbox, step: UndoStep) -> UndoStep:
    paths = _paths(mailbox)
    if step.before not in paths:
        raise ChronoMailError(RENAMED_SINCE)
    if step.after in paths:
        raise ChronoMailError(TAKEN_NOW)
    mailbox.rename_folder(step.before, step.after)
    return step


def _delete(engine: Engine, mailbox: Mailbox, step: UndoStep) -> UndoStep:
    """Deleted again (Redo), with its mail; its files are kept in .deleted once more, under a new name, and the rules it switches off are noted again."""
    if step.before not in _paths(mailbox):
        raise ChronoMailError(RENAMED_SINCE)
    before = rules_on(engine)
    _, kept = mailbox.delete_folder(step.before, require_empty=False)
    return replace(step, kept=kept, rules=switched_off(before, rules_on(engine)))


def do_folder_steps(engine: Engine, steps: list[UndoStep], failed: list[str]) -> tuple[list[UndoStep], list[str]]:
    """Do the folder steps of one action (one account): a folder brought back before those inside it, one deleted after them, a tree
    looked at whole before any of it is deleted again (RV-176). Returns the steps done (a delete done again with its new place in .deleted)
    and what the rules did; a reason for each step not done goes to failed."""
    done: list[UndoStep] = []
    notes: list[str] = []
    if not steps:
        return done, notes
    deleting = any(step.kind == "folder_delete" for step in steps)
    ordered = sorted(steps, key=lambda step: len(step.before or step.after), reverse=deleting)
    handled = 0
    brought = False
    left_out: dict[tuple[str, ...], str] = {}  # a folder not brought back, with why: the folders inside it stay away too (never into another)
    try:
        with open_mailbox(engine, steps[0].account) as mailbox, mailbox.together():
            if deleting:
                mailbox.refuse_unseen([step.before for step in ordered if step.before in _paths(mailbox)])
            for step in ordered:
                above = next((reason for path, reason in left_out.items() if step.after[: len(path)] == path), None) if step.kind == "folder_back" else None
                try:
                    if above is not None:
                        raise ChronoMailError(above)
                    if step.kind == "folder_rename":
                        done.append(_rename(mailbox, step))
                    elif step.kind == "folder_delete":
                        done.append(_delete(engine, mailbox, step))
                    else:
                        mailbox.bring_back_folder(step.after, step.kept)
                        done.append(step)
                        notes.extend(_switch_on(engine, step.rules))
                        brought = True
                except ChronoMailError as error:
                    failed.append(str(error))
                    if step.kind == "folder_back":
                        left_out[step.after] = str(error)
                handled += 1
            notes[:0] = mailbox.rule_notes
            if brought:
                engine.mailbox_changed(mailbox.store.account_id)  # its numbers in the folder list, the tags' counts: counted again once
    except ChronoMailError as error:  # the account could not be opened, or the server refused the tree: the rest stays as it is
        failed.extend(str(error) for _ in range(len(steps) - handled))
    return done, notes
