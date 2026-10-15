from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from chrono_mail.config import AccountSettings
from chrono_mail.core import (
    ChronoMailError,
    Folder,
    FolderRole,
    MessageFlag,
    MessageRef,
    StorageError,
)
from chrono_mail.engine.imap_online import LOCAL_ONLY, GoneFromServer, is_imap, server_session
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.store import MessageNotFoundError, Store, clean_tags

# --- Version ------------------------------------------------------------
__version__ = "mailbox.py 0.5.138"

# --- Version history ----------------------------------------------------
# v0.5.138: set_tags in an IMAP account: on the server first, as keywords
# v0.5.134: Tags: set_tags (here only for now; an IMAP account's server learns them in a later step), a copy keeps the tags in the index too
# v0.5.123: folders (make, rename, delete, empty, mark all read) on the server first; a message gone from the server (D-118); the copy of a folder left out of the synchronisation (D-120)
# v0.4.120: IMAP accounts: every change is done on the server first and here only when the server confirmed (imap_online, D-117)
# v0.3.112: Such a delete reports "gone"; forget
# v0.3.110: Deleting a message whose file is gone removes it from the index and counts as deleted
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.16: deliver
# v0.0.12: Initial mailbox helper


def parse_folder(text: str) -> tuple[str, ...]:
    """'Archive/2026' -> ('Archive', '2026'). A folder name cannot contain '/' at this level."""
    path = tuple(segment.strip() for segment in text.split("/"))
    if not text.strip() or any(not segment for segment in path):
        raise UsageError("A folder is written like Inbox or Archive/2026")
    return path


@dataclass
class Mailbox:
    """The store and the index of one account, changed together so they never disagree for long."""

    engine: Engine
    settings: AccountSettings
    store: Store
    index: Index

    @property
    def account_id(self) -> str:
        return self.settings.account.account_id

    def folder(self, text: str) -> tuple[str, ...]:
        """An existing folder by its path; the standard folder names also work in any letter case."""
        path = parse_folder(text)
        known = {folder.path for folder in self.store.list_folders()}
        if path in known:
            return path
        lowered = [candidate for candidate in known if tuple(part.lower() for part in candidate) == tuple(part.lower() for part in path)]
        if len(lowered) == 1:
            return lowered[0]
        raise UsageError(f"There is no folder '{text}'")

    def role_folder(self, role: FolderRole) -> tuple[str, ...]:
        for folder in self.store.list_folders():
            if folder.role is role:
                return folder.path
        raise StorageError(f"The account has no {role.value} folder")

    def reference(self, folder: tuple[str, ...], message_id: str) -> MessageRef:
        return MessageRef(self.store.account_id, folder, message_id)

    def hint(self, message_id: str) -> str | None:
        row = self.index.get(message_id)
        return None if row is None else row.path

    # --- changes that keep store and index in step ---------------------------------------------

    def set_flags(self, ref: MessageRef, flags: frozenset[MessageFlag]) -> frozenset[MessageFlag]:
        if not is_imap(self.settings):
            return self._set_flags_here(ref, flags)
        self.store.locate(ref, self.hint(ref.store_id))  # the message must be here before the server is asked
        with server_session(self.engine, self.settings) as server:
            commit = server.set_flags(ref.store_id, flags)
            self._set_flags_here(ref, flags)
            commit()
        return flags

    def _set_flags_here(self, ref: MessageRef, flags: frozenset[MessageFlag]) -> frozenset[MessageFlag]:
        path = self.store.set_flags(ref, flags, hint=self.hint(ref.store_id))
        self._index(lambda: self.index.update_flags(ref.store_id, flags, path))
        return flags

    def current_flags(self, ref: MessageRef) -> frozenset[MessageFlag]:
        path = self.store.locate(ref, self.hint(ref.store_id))
        for message in self.store.list_messages(ref.folder_path):
            if message.ref.store_id == ref.store_id:
                return message.flags
        raise StorageError(f"Message not found ({path})")

    def move(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        if not is_imap(self.settings):
            return self._move_here(ref, target)
        flags = self.current_flags(ref)  # the message must be here before the server is asked
        with server_session(self.engine, self.settings) as server:
            commit = server.move(ref.store_id, target, flags)
            moved = self._move_here(ref, target)
            commit()
        return moved

    def _move_here(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        moved = self.store.move(ref, target, hint=self.hint(ref.store_id))
        path = self.store.locate(moved)
        self._index(lambda: self.index.move(moved.store_id, target, path))
        return moved

    def deliver(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> MessageRef:
        """Store a message (verified) and index it; in an IMAP account it is put into the server folder first."""
        if not is_imap(self.settings):
            return self._deliver_here(folder, raw, flags)
        with server_session(self.engine, self.settings) as server:
            commit = server.append(folder, raw, flags)
            reference = self._deliver_here(folder, raw, flags)
            commit(reference.store_id)
        return reference

    def _deliver_here(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> MessageRef:
        reference = self.store.deliver(folder, raw, flags=flags, verify=True)
        path = self.store.locate(reference)
        self._index(lambda: self.index.add(entry_from_raw(reference, path, flags, len(raw), raw)))
        return reference

    def copy(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        if not is_imap(self.settings):
            return self._copy_here(ref, target)
        flags = self.current_flags(ref)
        with server_session(self.engine, self.settings) as server:
            commit = server.copy(ref.store_id, target, flags)
            copied = self._copy_here(ref, target)
            commit(copied.store_id)
        return copied

    def _copy_here(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        copied = self.store.copy(ref, target, hint=self.hint(ref.store_id))  # the store gives the copy the tags of the original
        path = self.store.locate(copied)
        raw = self.store.read(copied, hint=path)
        flags = self.current_flags(copied)
        tags = self.tags_of(copied.store_id)
        self._index(lambda: self.index.add(replace(entry_from_raw(copied, path, flags, len(raw), raw), tags=tags)))
        return copied

    def tags_of(self, store_id: str) -> tuple[str, ...]:
        try:
            return self.store.tags.of(store_id)
        except StorageError:
            return ()

    def rename_tag(self, old: str, new: str | None) -> int:
        """Rename a tag in every message of this mailbox (None removes it). Returns how many messages changed.
        In an IMAP account message by message, on the server first."""
        if is_imap(self.settings):
            changed_here = 0
            wanted = old.casefold()
            for store_id, tags in self.store.tags.all().items():
                if not any(tag.casefold() == wanted for tag in tags):
                    continue
                row = self.index.get(store_id)
                if row is None:
                    continue
                renamed = tuple(new if tag.casefold() == wanted else tag for tag in tags if tag.casefold() != wanted or new is not None)
                self.set_tags(MessageRef(self.store.account_id, row.folder, store_id), tuple(tag for tag in renamed if tag is not None))
                changed_here += 1
            return changed_here
        changed = self.store.tags.rename(old, new)
        for store_id in changed:
            self._index(self._tags_updater(store_id, self.tags_of(store_id)))
        return len(changed)

    def _tags_updater(self, store_id: str, tags: tuple[str, ...]) -> Callable[[], None]:
        return lambda: self.index.update_tags(store_id, tags)

    def set_tags(self, ref: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        """Give a message exactly these tags (the store's tags file is the truth, the index copies it). Returns them as kept.
        The message must be here."""
        self.store.locate(ref, self.hint(ref.store_id))
        if not is_imap(self.settings):
            return self._set_tags_here(ref, tags)
        cleaned = clean_tags(tags)
        with server_session(self.engine, self.settings) as server:
            commit = server.set_tags(ref.store_id, cleaned)
            kept = self._set_tags_here(ref, cleaned)
            commit()
        return kept

    def _set_tags_here(self, ref: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        kept = self.store.tags.set(ref.store_id, tags)
        self._index(lambda: self.index.update_tags(ref.store_id, kept))
        return kept

    def delete(self, ref: MessageRef, permanent: bool) -> str:
        """Move to Trash, or remove for good when permanent or when it is already in Trash. Returns what happened.
        A message whose file is gone is deleted already: only its line in the index is removed, and the answer is "gone"."""
        try:
            trash = self.role_folder(FolderRole.TRASH)
        except StorageError:
            trash = ()
        if is_imap(self.settings) and (permanent or not trash or ref.folder_path == trash):
            return self._delete_on_server_first(ref)
        if is_imap(self.settings):
            try:
                self.move(ref, trash)
            except GoneFromServer:
                return self._delete_on_server_first(ref)  # deleted on the server already: here too
            except MessageNotFoundError:  # gone here, still on the server: it goes to Trash there, and comes back here with the next sync
                try:
                    with server_session(self.engine, self.settings) as server:
                        commit = server.move(ref.store_id, trash, frozenset())
                        self.forget(ref)
                        commit()
                except GoneFromServer:
                    return self._delete_on_server_first(ref)
                return "gone"
            return "moved to Trash"
        try:
            if permanent or not trash or ref.folder_path == trash:
                self.store.delete(ref, hint=self.hint(ref.store_id))
            else:
                self.move(ref, trash)
                return "moved to Trash"
        except MessageNotFoundError:
            self.forget(ref)  # the index pointed at nothing
            return "gone"
        self._index(lambda: self.index.remove(ref.store_id))
        return "deleted"

    def _delete_on_server_first(self, ref: MessageRef) -> str:
        with server_session(self.engine, self.settings) as server:
            commit = server.delete(ref.store_id)  # also when the file is gone here: a missing file is a deleted message
            try:
                self.store.delete(ref, hint=self.hint(ref.store_id))
            except MessageNotFoundError:
                self.forget(ref)
                commit()
                return "gone"
            self._index(lambda: self.index.remove(ref.store_id))
            commit()
            return "deleted"

    def put_on_server(self, ref: MessageRef) -> None:
        """A message that is already here goes into the same folder on the server at once (the Sent copy after sending).
        When the server cannot be reached, the next sync puts it there."""
        if not is_imap(self.settings):
            return
        try:
            raw = self.store.read(ref, hint=self.hint(ref.store_id))
            flags = self.current_flags(ref)
            with server_session(self.engine, self.settings) as server:
                server.append(ref.folder_path, raw, flags)(ref.store_id)
        except ChronoMailError:
            pass  # the message was sent and is safe here: the next sync puts the copy on the server

    def forget(self, ref: MessageRef) -> None:
        """Remove the line of a message whose file is gone from the index."""
        self._index(lambda: self.index.remove(ref.store_id))

    def _index(self, change: Callable[[], None]) -> None:
        """The store is the truth: if the index cannot be updated, the next reconcile repairs it."""
        try:
            change()
        except ChronoMailError:
            pass

    def folders(self) -> list[Folder]:
        return self.store.list_folders()

    # --- folders: in an IMAP account on the server first -----------------------------------------

    def _on_server(self, path: tuple[str, ...]) -> bool:
        return is_imap(self.settings) and path[0] not in LOCAL_ONLY

    def drop_unsynchronised_folder(self, path: tuple[str, ...]) -> None:
        """The person left an IMAP folder out of the synchronisation: its copy here is no longer shown. Nothing changes on
        the server, where the mail stays; the files are kept in the store's .deleted directory, not erased. The only change
        of folders made here alone: the caller checked that the folder holds nothing the server has not got."""
        self.store.delete_folder(path, require_empty=False)
        self.index.remove_folder(path)

    def ensure_folder(self, path: tuple[str, ...], role: FolderRole = FolderRole.OTHER) -> tuple[Folder, bool]:
        """The folder with this path, made when there is none (and whether it was made): on the server first."""
        if self._on_server(path) and path not in {folder.path for folder in self.store.list_folders()}:
            Folder(self.store.account_id, path, role)  # a name that cannot be used is refused before the server is asked
            with server_session(self.engine, self.settings) as server:
                server.make_folder(path)  # when the server cannot be reached the next sync makes it there
                return self.store.ensure_folder(path, role)
        return self.store.ensure_folder(path, role)

    def create_folder(self, path: tuple[str, ...]) -> Folder:
        if path in {folder.path for folder in self.store.list_folders()}:
            raise StorageError("A folder with this path already exists")
        folder, _ = self.ensure_folder(path)
        return folder

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        """Rename a folder and everything below it; in an IMAP account on the server first, and only while it can be reached."""
        if not self._on_server(old):
            self.store.rename_folder(old, new)
            self.index.rename_folder(old, new)
            return
        known = self.store.list_folders()
        if any(folder.path[: len(old)] == old and folder.role is not FolderRole.OTHER for folder in known):
            raise UsageError("The standard folders cannot be renamed in an IMAP account")
        Folder(self.store.account_id, new, FolderRole.OTHER)  # a name that cannot be used is refused before the server is asked
        if new[0] in LOCAL_ONLY or any(folder.path[: len(new)] == new for folder in known):
            raise StorageError("A folder with the new path already exists")
        with server_session(self.engine, self.settings) as server:
            commit = server.rename_folder(old, new)
            self.store.rename_folder(old, new)
            self.index.rename_folder(old, new)
            commit()

    def delete_folder(self, path: tuple[str, ...], require_empty: bool) -> int:
        """Take one folder out of the store (its files are kept in .deleted); in an IMAP account it is deleted on the
        server first, with its mail, and only while the server can be reached. Returns the messages removed from the index."""
        if not self._on_server(path):
            self.store.delete_folder(path, require_empty=require_empty)
            return self.index.remove_folder(path)
        with server_session(self.engine, self.settings) as server:
            commit = server.delete_folder(path)
            self.store.delete_folder(path, require_empty=require_empty)
            removed = self.index.remove_folder(path)
            commit()
            return removed

    def empty_folder(self, path: tuple[str, ...]) -> int:
        """Delete every message of the folder for good; in an IMAP account the server folder as a whole first."""
        if self._on_server(path):
            with server_session(self.engine, self.settings) as server:
                commit = server.empty_folder(path)
                if commit is not None:
                    removed = 0
                    for message in self.store.list_messages(path):
                        try:
                            self.store.delete(message.ref, hint=message.relative_path)
                        except MessageNotFoundError:
                            pass
                        self._index(lambda: self.index.remove(message.ref.store_id))
                        removed += 1
                    commit()
                    return removed
        removed = 0
        for message in self.store.list_messages(path):  # one by one: each on the server first, or noted while offline
            self.delete(message.ref, permanent=True)
            removed += 1
        return removed

    def mark_folder_read(self, path: tuple[str, ...]) -> int:
        """Mark every message of the folder as read; in an IMAP account the server folder as a whole first."""
        unread = [message for message in self.store.list_messages(path) if MessageFlag.SEEN not in message.flags]
        if self._on_server(path):
            with server_session(self.engine, self.settings) as server:
                commit = server.mark_folder_read(path)
                if commit is not None:
                    for message in unread:
                        self._set_flags_here(message.ref, message.flags | {MessageFlag.SEEN})
                    commit()
                    return len(unread)
        for message in unread:  # one by one: each on the server first, or sent at the next check while offline
            self.set_flags(message.ref, message.flags | {MessageFlag.SEEN})
        return len(unread)


@contextmanager
def open_mailbox(engine: Engine, account: str) -> Generator[Mailbox]:
    settings = engine.resolve_account(account)
    storage = engine.storage_settings(settings)  # the account's own folders, or the shared Local Folders
    store = engine.open_store(storage)
    index = engine.open_index(storage, store)
    try:
        yield Mailbox(engine, settings, store, index)
    finally:
        index.close()
