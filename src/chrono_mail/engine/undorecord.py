"""Noting what the person's action on mail changed (B-18): each message before and after, kept as the steps of one action in the
engine's undo history. Kept apart from undo.py (which does the steps) so the commands that note can be imported by everything."""

from collections.abc import Iterable
from dataclasses import dataclass

from chrono_mail.core import ChronoMailError, MessageFlag, MessageRef
from chrono_mail.engine.mailbox import Mailbox
from chrono_mail.engine.undohistory import Notes, UndoStep

# --- Version ------------------------------------------------------------
__version__ = "undorecord.py 4.2.396"

# --- Version history ----------------------------------------------------
# v4.2.396: unchanged_too: a message the action left as it was is noted too, when the action took it out of the person's list (D-282)
# v2.2.313: Initial: UndoRecorder notes the messages of one action before and after it (B-18)

type Verdicts = dict[str, tuple[str | None, str | None]]  # store id -> the junk filter's verdict (before, after), as junkfilter.learn says


@dataclass(frozen=True, slots=True)
class _State:
    """A message as it is: its folder, marks, tags and notes."""

    folder: tuple[str, ...]
    flags: frozenset[MessageFlag]
    tags: tuple[str, ...]
    notes: Notes


def _state(mailbox: Mailbox, store_id: str) -> _State | None:
    """Where the message is and how; None when it is not in the mailbox (deleted for good) or cannot be read."""
    row = mailbox.index.get(store_id)
    if row is None:
        return None
    try:
        flags = mailbox.store.flags(mailbox.reference(row.folder, store_id), row.path)
        properties = mailbox.store.properties.of(store_id)
    except ChronoMailError:
        return None
    return _State(row.folder, flags, properties.tags, Notes(properties.snoozed_until, properties.snoozed_from, properties.archived_from))


def folded(tags: Iterable[str]) -> set[str]:
    return {tag.casefold() for tag in tags}


def _step(account: str, store_id: str, was: _State, now: _State, verdict: tuple[str | None, str | None] | None, unchanged_too: bool = False) -> UndoStep | None:
    """What the action changed of one message; None when nothing (unless unchanged_too: then a step that changes nothing)."""
    step = UndoStep(
        account, store_id, was.folder, now.folder, flags_added=now.flags - was.flags, flags_removed=was.flags - now.flags,
        tags_added=tuple(tag for tag in now.tags if tag.casefold() not in folded(was.tags)),
        tags_removed=tuple(tag for tag in was.tags if tag.casefold() not in folded(now.tags)),
        notes=None if was.notes == now.notes else (was.notes, now.notes), verdict=verdict,
    )
    changed = step.before != step.after or step.flags_added or step.flags_removed or step.tags_added or step.tags_removed or step.notes or step.verdict
    return step if changed or unchanged_too else None


class UndoRecorder:
    """Notes the messages of one action before and after it, and keeps the action for Undo. Without a number (0) it notes nothing:
    only what the person did is undone, never what the rules, the junk filter or a timer did. unchanged_too: a message the action left
    as it was is noted too (the action took it out of the person's list, D-282): its Undo and Redo name it, so the list brings it back."""

    def __init__(self, mailbox: Mailbox, account: str, number: int, unchanged_too: bool = False) -> None:
        self._unchanged_too: bool = unchanged_too
        self._mailbox: Mailbox = mailbox
        self._account: str = account
        self._number: int = number
        self._before: dict[str, _State] = {}
        self._steps: list[UndoStep] = []

    def before(self, store_ids: Iterable[str]) -> None:
        if self._number <= 0:
            return
        for store_id in store_ids:
            state = _state(self._mailbox, store_id)
            if state is not None:
                self._before[store_id] = state

    def after(self, store_ids: Iterable[str], verdicts: Verdicts | None = None) -> None:
        if self._number <= 0:
            return
        for store_id in store_ids:
            was = self._before.get(store_id)
            now = _state(self._mailbox, store_id)
            if was is None or now is None:
                continue  # gone for good, or never known: nothing to put back
            step = _step(self._account, store_id, was, now, (verdicts or {}).get(store_id), self._unchanged_too)
            if step is not None:
                self._steps.append(step)

    def copied(self, original: MessageRef, copy: MessageRef) -> None:
        if self._number > 0:
            self._steps.append(UndoStep(self._account, copy.store_id, original.folder_path, copy.folder_path, kind="copy", original=original.store_id))

    def keep(self, name: str) -> None:
        """The action is done: it can be undone (one action of several commands is joined by its number)."""
        self._mailbox.engine.undo.record(self._number, name, self._steps)
