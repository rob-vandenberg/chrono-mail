"""Folders deleted or renamed on the IMAP server, followed here (R-30, D-199). A folder that was synchronised before and is gone from the server's list
is asked about by its own name; only the server's answer that it is not there confirms it. A folder new on the server with the identity
(UIDVALIDITY) of one that went is that folder renamed: renamed here, nothing downloaded again. A deleted folder goes here too (its files kept in the
store's .deleted); one that holds changes here not yet on the server is a sync conflict; a standard folder stays here and is made again there.
A folder another program took off the server's list of folders to show (unsubscribed; R-34) is synchronised one last time, then taken away here
the same way; its mail stays on the server."""

from collections.abc import Callable
from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag, MessageRef
from chrono_mail.index import Index
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from chrono_mail.store import Store, StoredMessage
from chrono_mail.sync.announce import Announcer
from chrono_mail.sync.imap_state import ImapEntry, ImapState
from chrono_mail.sync.mailconflicts import FOLDER_GONE_CHANGED, FOLDER_GONE_WITH_MAIL, held_under, record
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "imap_folders.py 2.4.333"

# --- Version history ----------------------------------------------------
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.1.241: A folder taken off the server's list of folders to show is synchronised one last time, then taken away here (R-34)
# v1.1.237: Initial: folders deleted or renamed on the server followed here, only on the server's confirmation (R-30)

MarksChanged = Callable[[StoredMessage, ImapEntry], bool]


@dataclass(frozen=True, slots=True)
class Planned:
    """A server folder this run synchronises: its name there and its folder here (what plan_folders made of it)."""

    server_name: str
    local_path: tuple[str, ...]
    role: FolderRole


@dataclass(slots=True)
class FolderChanges:
    renamed: list[tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=list[tuple[tuple[str, ...], tuple[str, ...]]])
    deleted: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])
    conflicts: int = 0
    notes: list[str] = field(default_factory=list[str])


def follow_server_folders(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, planned: list[Planned], held_folders: set[tuple[str, ...]],
    local_only: frozenset[str], marks_changed: MarksChanged, announcer: Announcer,
) -> FolderChanges:
    """Follow the folders the server no longer lists (run before the folders of this run are made here)."""
    changes = FolderChanges()
    paths = {plan.local_path for plan in planned}
    existing = {folder.path: folder for folder in store.list_folders()}
    missing = sorted(
        (path for path in state.validity if path not in paths and path in existing and path[0] not in local_only and not held_under(held_folders, path)
         and state.server_names.get(path)),
        key=len,
    )
    if not missing:
        return changes
    if store.damaged_folders:
        changes.notes.append("Some folders are no longer listed by the server, but the store here looks damaged: nothing was changed")
        return changes
    candidates = [plan for plan in planned if plan.local_path not in state.validity and plan.local_path not in existing and plan.role is FolderRole.OTHER]
    validities: dict[str, int] = {}
    for path in missing:
        if path not in state.validity or path not in {folder.path for folder in store.list_folders()}:
            continue  # moved along with a folder above it, or taken away with it
        name = state.server_names[path]
        try:
            there = client.folder_exists(name)
        except ImapRefused as error:
            changes.notes.append(f"The folder '{name}' is no longer listed, but the server did not confirm it was deleted ({error}); it is kept and asked again at the next check")
            continue
        if there:
            continue  # still on the server (no longer subscribed, for one): not a deletion
        role = existing[path].role
        if role is FolderRole.OTHER and (target := _renamed_to(client, state.validity[path], candidates, validities)) is not None:
            _rename_here(store, index, state, path, target, name, announcer)
            candidates.remove(target)
            changes.renamed.append((path, target.local_path))
            continue
        _deleted_on_server(store, index, state, path, name, role, local_only, marks_changed, changes, announcer)
    return changes


def _renamed_to(client: ImapClient, validity: int, candidates: list[Planned], validities: dict[str, int]) -> Planned | None:
    """The new folder with the identity of the one that went (both servers tested keep UIDVALIDITY on a rename)."""
    for plan in candidates:
        if plan.server_name not in validities:
            try:
                validities[plan.server_name] = client.select(plan.server_name, read_only=True).uid_validity
            except ImapRefused:
                validities[plan.server_name] = 0
        if validities[plan.server_name] == validity:
            return plan
    return None


def _rename_here(store: Store, index: Index | None, state: ImapState, old: tuple[str, ...], target: Planned, name: str, announcer: Announcer) -> None:
    """Rename the folder (and those inside it) here as the server did: the store, the index, then what the sync knows (a crash in between is
    repaired by the next run, which matches the messages by their Message-ID)."""
    with announcer.changing():
        store.rename_folder(old, target.local_path)
        if index is not None:
            index.rename_folder(old, target.local_path)
        announcer.folder_renamed(old, target.local_path)
    state.rename_folder(old, target.local_path, {name: target.server_name})


def _subtree(store: Store, path: tuple[str, ...]) -> list[tuple[str, ...]]:
    return sorted((folder.path for folder in store.list_folders() if folder.path[: len(path)] == path), key=len, reverse=True)


def _deleted_on_server(
    store: Store, index: Index | None, state: ImapState, path: tuple[str, ...], name: str, role: FolderRole, local_only: frozenset[str],
    marks_changed: MarksChanged, changes: FolderChanges, announcer: Announcer,
) -> None:
    """The server confirmed the folder is gone. Nothing waiting here: it goes here too. Mail that exists only here, or marks changed here: a sync
    conflict (the folder stays, no longer tied to the server). A standard folder stays: the mail the server had goes, what exists only here stays,
    and the folder is made again on the server by the same run."""
    folders = _subtree(store, path)
    only_here, changed = _waiting(store, state, folders, marks_changed)
    if role is not FolderRole.OTHER:
        _drop_server_mail(store, index, state, path, marks_changed, announcer)
        state.forget_folder(path, name)
        changes.notes.append(f"The standard folder '{name}' was deleted on the server; it is kept here and made there again")
        return
    if only_here or changed:
        reason = FOLDER_GONE_WITH_MAIL if only_here else FOLDER_GONE_CHANGED
        record(store, reason, path, "", "/".join(path), f"{counted(only_here, 'message')} only here" if only_here else f"{counted(changed, 'message')} changed here")
        for folder in folders:
            state.forget_folder(folder, state.server_names.get(folder, ""))
        changes.conflicts += 1
        changes.notes.append(f"The folder '{name}' was deleted on the server while it had changes here not yet sent (see Sync conflicts)")
        return
    for folder in folders:  # deepest first; each folder's files go to the store's .deleted, never erased
        try:
            store.delete_folder(folder, require_empty=False)
            if index is not None:
                index.remove_folder(folder)
        except ChronoMailError as error:
            changes.notes.append(f"The folder '{'/'.join(folder)}' was deleted on the server but could not be taken away here: {error}")
            return
        state.forget_folder(folder, state.server_names.get(folder, ""))
        changes.deleted.append(folder)


def _waiting(store: Store, state: ImapState, folders: list[tuple[str, ...]], marks_changed: MarksChanged) -> tuple[int, int]:
    """(messages that exist only here, messages whose marks were changed here) in these folders."""
    only_here = changed = 0
    for folder in folders:
        mapped = {entry.store_id: entry for entry in state.folder_entries(folder).values()}
        for message in store.list_messages(folder):
            entry = mapped.get(message.ref.store_id)
            if entry is None:
                if message.ref.store_id not in state.uploaded:
                    only_here += 1
            elif marks_changed(message, entry):
                changed += 1
    return only_here, changed


def _drop_server_mail(store: Store, index: Index | None, state: ImapState, path: tuple[str, ...], marks_changed: MarksChanged, announcer: Announcer) -> None:
    """A standard folder deleted on the server: the messages it had there (unchanged here) go here as well; what exists only here stays and is
    uploaded when the folder is made there again."""
    stored = {message.ref.store_id: message for message in store.list_messages(path)}
    for uid, entry in state.folder_entries(path).items():
        message = stored.get(entry.store_id)
        if message is not None and not marks_changed(message, entry):
            reference = MessageRef(store.account_id, path, entry.store_id)
            try:
                with announcer.changing():
                    if index is not None:
                        index.remove(entry.store_id)
                    store.delete(reference)
                    announcer.removed(reference, MessageFlag.SEEN not in message.flags)
            except ChronoMailError:
                continue
        state.unmap(path, uid)


# --- taken off the server's list of folders to show (R-34) ------------------------------------------------------------------------------


def leaving_folders(
    store: Store, state: ImapState, listed: frozenset[str], subscribed: frozenset[str], planned: set[tuple[str, ...]], waiting: set[str], local_only: frozenset[str],
) -> list[Planned]:
    """Folders synchronised before that are still on the server but no longer on its list of folders to show, and that the person did not tick
    here: this run synchronises them one last time (what was changed here goes to the server first). A server without any list shows every
    folder, so nothing leaves it. Standard folders are never among them (they are always planned)."""
    if not subscribed:
        return []
    existing = {folder.path: folder for folder in store.list_folders()}
    leaving: list[Planned] = []
    for path, name in sorted(state.server_names.items()):
        if path in planned or path not in existing or path not in state.validity or path[0] in local_only:
            continue
        if name not in listed or name in subscribed or name in state.forced or name in state.hidden or name in waiting or name.upper() == "INBOX":
            continue
        leaving.append(Planned(name, path, existing[path].role))
    return leaving


def take_away_unlisted(
    store: Store, index: Index | None, state: ImapState, leaving: list[Planned], planned: set[tuple[str, ...]], held_folders: set[tuple[str, ...]],
    marks_changed: MarksChanged, notes: list[str],
) -> None:
    """After their last synchronisation: each leaving folder goes here (its files to the store's .deleted, never erased), deepest first. One that
    still holds something the server has not got stays until it is sent or its sync conflict is settled (Rule 4.1); one with a folder inside it
    that is still shown stays as well (a folder cannot be shown without the one it is in)."""
    remaining = set(planned)
    for plan in sorted(leaving, key=lambda leaving_plan: len(leaving_plan.local_path), reverse=True):
        path = plan.local_path
        if any(other != path and other[: len(path)] == path for other in remaining) or held_under(held_folders, path):
            continue
        only_here, changed = _waiting(store, state, [path], marks_changed)
        if only_here or changed:
            notes.append(f"The folder '{plan.server_name}' is no longer on the server's list of folders to show, but it has changes here not yet on the server; it stays until they are sent")
            continue
        try:
            store.delete_folder(path, require_empty=False)
            if index is not None:
                index.remove_folder(path)
        except ChronoMailError as error:
            notes.append(f"The folder '{plan.server_name}' is no longer on the server's list of folders to show but could not be taken away here: {error}")
            continue
        state.forget_folder(path, plan.server_name)
        remaining.discard(path)
        notes.append(f"The folder '{plan.server_name}' is no longer on the server's list of folders to show and was taken away here (its mail stays on the server)")
