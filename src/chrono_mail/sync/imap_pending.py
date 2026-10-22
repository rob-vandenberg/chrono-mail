"""Folder renames and deletes made here while the IMAP server could not be reached, done there by the next check (R-31, D-200). They run first, before
the folders are planned, so the sync never takes the old name for a new folder or downloads its mail again. A delete waits when the server changed the
folder meanwhile (mail the person never saw): a sync conflict. A lasting "no" of the server is a sync conflict; a passing failure waits."""

from chrono_mail.protocols.imap import FolderEntry, ImapClient, ImapRefused
from chrono_mail.store import Store
from chrono_mail.sync.imap_state import PendingFolder, ImapState
from chrono_mail.sync.mailconflicts import FOLDER_DELETE_REFUSED, FOLDER_DELETED_HERE_CHANGED, FOLDER_RENAME_REFUSED, held, held_under, record

# --- Version ------------------------------------------------------------
__version__ = "imap_pending.py 1.1.240"

# --- Version history ----------------------------------------------------
# v1.1.240: Initial: folder renames and deletes made offline done on the server at the next check (R-31)


def server_name_of(path: tuple[str, ...], delimiter: str) -> str | None:
    """The name a local folder has on the server (as the sync makes it); None when it cannot be written there."""
    if delimiter in "".join(path):
        return None
    return delimiter.join(("INBOX" if path[0] == "Inbox" else path[0], *path[1:]))


def send_pending(client: ImapClient, store: Store, state: ImapState, listed: tuple[FolderEntry, ...], notes: list[str]) -> bool:
    """Do on the server what was renamed or deleted here offline. Returns whether the server's folders changed (they are listed again then). A
    broken connection ends the run (raises) and keeps what is left for the next check."""
    _, held_folders = held(store)
    delimiter = next((entry.delimiter for entry in listed if entry.delimiter), "/")
    names = {entry.name for entry in listed}
    changed = False
    for change in list(state.pending.values()):
        if held_under(held_folders, change.path):
            continue  # an open conflict: the person decides first
        try:
            if change.kind == "rename":
                changed = _rename(client, store, state, change, delimiter, names, notes) or changed
            else:
                changed = _delete(client, store, state, change, names, notes) or changed
        except ImapRefused as error:  # a lasting "no": a sync conflict, not a line at every check (D-195)
            reason = FOLDER_RENAME_REFUSED if change.kind == "rename" else FOLDER_DELETE_REFUSED
            if record(store, reason, change.path, "", "/".join(change.path), str(error), server_folder=change.old_path):
                notes.append(f"The folder '{'/'.join(change.path)}' could not be {'renamed' if change.kind == 'rename' else 'deleted'} on the server: {error} (see Sync conflicts)")
    return changed


def _rename(client: ImapClient, store: Store, state: ImapState, change: PendingFolder, delimiter: str, names: set[str], notes: list[str]) -> bool:
    new_name = server_name_of(change.path, delimiter)
    if new_name is None:
        raise ImapRefused("The new name cannot be used on the server")
    if change.server_name not in names:
        state.pending_done(change.path)  # the server has no folder of that name any more: the check follows the server (R-30)
        return False
    if client.selected == change.server_name:
        client.select("INBOX", read_only=True)  # some servers refuse to rename the open folder
    client.rename_folder(change.server_name, new_name)
    below = {path: name for path, name in state.server_names.items() if path[: len(change.path)] == change.path}
    for path, name in below.items():
        after = new_name + name[len(change.server_name):] if name.startswith(change.server_name) else name
        state.set_server_name(path, after)
        for step in (lambda: client.subscribe(after), lambda: client.unsubscribe(name)):
            try:
                step()  # a subscription is a name: most servers leave it on the old one
            except ImapRefused:
                pass
    state.pending_done(change.path)
    return True


def _delete(client: ImapClient, store: Store, state: ImapState, change: PendingFolder, names: set[str], notes: list[str]) -> bool:
    if change.server_name not in names:
        state.pending_done(change.path)  # gone there already
        return False
    if not change.force:
        info = client.select(change.server_name, read_only=True)
        arrived = set(client.uid_search("ALL")) - change.uids if info.uid_validity == change.validity else {0}
        if arrived:  # the server changed it meanwhile: mail the person never saw is never deleted unasked
            if record(store, FOLDER_DELETED_HERE_CHANGED, change.path, "", "/".join(change.path), "", server_folder=change.path):
                notes.append(f"The folder '{'/'.join(change.path)}' was deleted here, but it has new mail on the server (see Sync conflicts)")
            return False
    if client.selected == change.server_name:
        client.select("INBOX", read_only=True)
    client.delete_folder(change.server_name)
    try:
        client.unsubscribe(change.server_name)
    except ImapRefused:
        pass
    state.pending_done(change.path)
    return True
