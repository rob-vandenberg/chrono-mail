"""Undo and Redo in Calendar, Tasks and Contacts (B-18 step 3, D-368). An action of the person noted each item's text before and after
(pimundohistory.py); Undo makes each item as it was before, Redo as it was after. Both are new actions through the ItemWriter, so on a
server's calendar the server first and offline sent later (D-193, D-194). An item that changed since is left alone and told. Nobody is
mailed: what was sent stays sent, the item itself is put back as quietly as the server allows (D-310)."""
from dataclasses import dataclass, field, replace

from chrono_mail.core import ChronoMailError, CollectionInfo, CollectionKind, StorageError
from chrono_mail.engine.google_colours import google_colour_choose, google_colour_off
from chrono_mail.engine.meetingmail import copy_for, delete_copy, organizing_account, quiet, who_mails
from chrono_mail.engine.pim_server import ItemWriter, item_writer
from chrono_mail.engine.pimaccess import open_pim
from chrono_mail.engine.pimundohistory import PimSide, PimStep
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pimstore import PimStore

# --- Version ------------------------------------------------------------
__version__ = "pimundo.py 4.7.656"

# --- Version history ----------------------------------------------------
# v4.7.656: Initial: pim.undo, pim.redo and pim.undo-labels: the person's last action in Calendar, Tasks or Contacts undone and done again (B-18 step 3, D-368)

CHANGED_SINCE: str = "it was changed or deleted since"
READ_ONLY: str = "its calendar, task list or address book is read-only"


class _Skipped(ChronoMailError):
    """This item is left as it is: the reason is told."""


@dataclass(frozen=True, slots=True)
class SectionParameters:
    section: str = field(metadata=option("events (Calendar), tasks or contacts"))


@dataclass(frozen=True, slots=True)
class PimUndoResult:
    action: str  # what was undone or redone: "Delete (2 tasks)"
    items: tuple[str, ...]  # the items as they are now, by their ids (an item taken away is not among them)
    not_done: int = 0  # how many could not be done (each stays as it is)
    reason: str = ""  # why (the first reason)


@dataclass(frozen=True, slots=True)
class PimUndoLabels:
    undo: str  # what Undo would do now ("" for nothing)
    redo: str


def _kind(section: str) -> CollectionKind:
    try:
        return CollectionKind(section)
    except ValueError:
        raise UsageError("The section is events, tasks or contacts") from None


# --- doing one step ----------------------------------------------------------------------------------------------------------------------------

def _address(engine: Engine, account: str) -> str:
    you = organizing_account(engine, account)
    return you.account.email_address.strip().lower() if you is not None else ""


def _write(engine: Engine, writer: ItemWriter, account: str, info: CollectionInfo, text: str, item: str | None) -> str:
    """Save the text, telling no one: an appointment as quietly as its server allows (as a title edited in place, D-324, D-310)."""
    if info.kind is not CollectionKind.EVENTS:
        return writer.put(info, text, item)
    mails = quiet(who_mails(engine, (account, info), account, _address(engine, account), answers=False))
    if mails.bare:
        return writer.put_organizer_last(info, text, item)
    return writer.put(info, copy_for(text, mails), item)


def _take_away(engine: Engine, writer: ItemWriter, account: str, info: CollectionInfo, item: str, text: str) -> None:
    if info.kind is not CollectionKind.EVENTS:
        writer.delete(info, item)
        return
    delete_copy(writer, info, item, text, quiet(who_mails(engine, (account, info), account, _address(engine, account), item, answers=False)))


def _collection(store: PimStore, collection: str) -> CollectionInfo:
    try:
        info = store.get_collection(collection)
    except StorageError:
        raise _Skipped("its calendar, task list or address book is gone") from None
    if info.read_only:
        raise _Skipped(READ_ONLY)
    return info


def _google(engine: Engine, account: str, info: CollectionInfo, store: PimStore, item: str, was: str, wanted: str) -> None:
    """An appointment of a Google calendar gets its colour on Google back (D-341)."""
    if was.casefold() == wanted.casefold() or info.kind is not CollectionKind.EVENTS:
        return
    found = store.index.get(item)
    if found is None:
        return
    if wanted:
        google_colour_choose(engine, account, info.collection_id, found.uid, None, wanted, "")
    else:
        google_colour_off(engine, account, info.collection_id, found.uid)


def _do_step(engine: Engine, writer: ItemWriter, store: PimStore, step: PimStep) -> PimStep:
    """Make the item as step.after says, from step.before; returns the step as done (the item's id and text now)."""
    source, target = step.before, step.after
    if source.item:
        found = store.index.get(source.item)
        if found is None or found.collection != source.collection:
            raise _Skipped(CHANGED_SINCE)
        try:
            same = store.get(source.collection, source.item) == source.text
        except StorageError:
            same = False
        if not same:
            raise _Skipped(CHANGED_SINCE)
    if not target.item:  # it goes
        info = _collection(store, source.collection)
        _take_away(engine, writer, step.account, info, source.item, source.text)
        return replace(step, after=PimSide(source.collection))
    info = _collection(store, target.collection)
    item = source.item
    if not item:  # it comes back, with the same UID
        item = _write(engine, writer, step.account, info, target.text, None)
    else:
        if source.collection != target.collection:
            item = writer.move(_collection(store, source.collection), item, info)
        if store.get(info.collection_id, item) != target.text:
            item = _write(engine, writer, step.account, info, target.text, item)
    _google(engine, step.account, info, store, item, source.google, target.google)
    try:
        text = store.get(info.collection_id, item)
    except StorageError:
        text = target.text
    return replace(step, after=PimSide(info.collection_id, item, text, target.google))


def _do(engine: Engine, steps: list[PimStep]) -> tuple[list[PimStep], list[str]]:
    """Do these steps, account by account, the server first; returns the steps done (as done) and a reason for each one not done."""
    done: list[PimStep] = []
    failed: list[str] = []
    accounts: dict[str, list[PimStep]] = {}
    for step in steps:
        accounts.setdefault(step.account, []).append(step)
    for account, members in accounts.items():
        handled = 0  # the steps of this account done or told already
        try:
            with open_pim(engine, account) as store, item_writer(engine, account, store) as writer:
                for step in members:
                    try:
                        done.append(_do_step(engine, writer, store, step))
                    except ChronoMailError as error:  # a refusal of the server, or changed since: this item stays as it is
                        failed.append(str(error))
                    handled += 1
        except ChronoMailError as error:  # the account could not be opened, or its connection failed: the steps not handled stay as they are
            failed.extend(str(error) for _ in range(len(members) - handled))
    return done, failed


# --- the commands --------------------------------------------------------------------------------------------------------------------------------

def _run(engine: Engine, parameters: SectionParameters, redo: bool) -> PimUndoResult:
    history = engine.pim_undo[_kind(parameters.section)]
    action = history.take(redo)
    if action is None:
        raise UsageError("Nothing to redo" if redo else "Nothing to undo")
    label = action.label()
    try:
        done, failed = _do(engine, [step if redo else step.reversed() for step in action.steps])
    except BaseException:
        history.put(action, redo=redo)  # a fault of the program: the action stays where it was
        raise
    action.steps = [step if redo else step.reversed() for step in done]
    history.put(action, redo=not redo)
    items = tuple(step.after.item for step in done if step.after.item)
    return PimUndoResult(label, items, len(failed), failed[0] if failed else "")


def pim_undo(engine: Engine, parameters: SectionParameters) -> PimUndoResult:
    """Undo the last action in Calendar, Tasks or Contacts: every item as it was before."""
    return _run(engine, parameters, redo=False)


def pim_redo(engine: Engine, parameters: SectionParameters) -> PimUndoResult:
    """Do again what was undone last in this section."""
    return _run(engine, parameters, redo=True)


def pim_undo_labels(engine: Engine, parameters: SectionParameters) -> PimUndoLabels:
    """What Undo and Redo would do now in this section."""
    undo, redo = engine.pim_undo[_kind(parameters.section)].labels()
    return PimUndoLabels(undo, redo)


PIM_UNDO: TypedCommand[SectionParameters, PimUndoResult] = TypedCommand("pim.undo", SectionParameters, PimUndoResult)
PIM_REDO: TypedCommand[SectionParameters, PimUndoResult] = TypedCommand("pim.redo", SectionParameters, PimUndoResult)
PIM_UNDO_LABELS: TypedCommand[SectionParameters, PimUndoLabels] = TypedCommand("pim.undo-labels", SectionParameters, PimUndoLabels)


def register(registry: CommandRegistry) -> None:
    registry.register(PIM_UNDO.id, "Undo the last action in Calendar, Tasks or Contacts", SectionParameters, pim_undo, doing="Undoing an action")
    registry.register(PIM_REDO.id, "Do again the action undone last in Calendar, Tasks or Contacts", SectionParameters, pim_redo, doing="Redoing an action")
    registry.register(PIM_UNDO_LABELS.id, "What Undo and Redo would do now in Calendar, Tasks or Contacts", SectionParameters, pim_undo_labels, doing="Looking what can be undone")
