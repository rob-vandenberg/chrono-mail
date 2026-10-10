from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

from chrono_mail.config import AccountSettings
from chrono_mail.core import (
    ChronoMailError,
    Folder,
    FolderRole,
    MessageFlag,
    MessageRef,
    StorageError,
)
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.store import Store

# --- Version ------------------------------------------------------------
__version__ = "mailbox.py 0.0.45"

# --- Version history ----------------------------------------------------
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
        moved = self.store.move(ref, target, hint=self.hint(ref.store_id))
        path = self.store.locate(moved)
        self._index(lambda: self.index.move(moved.store_id, target, path))
        return moved

    def deliver(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> MessageRef:
        """Store a message (verified) and index it."""
        reference = self.store.deliver(folder, raw, flags=flags, verify=True)
        path = self.store.locate(reference)
        self._index(lambda: self.index.add(entry_from_raw(reference, path, flags, len(raw), raw)))
        return reference

    def copy(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        copied = self.store.copy(ref, target, hint=self.hint(ref.store_id))
        path = self.store.locate(copied)
        raw = self.store.read(copied, hint=path)
        flags = self.current_flags(copied)
        self._index(lambda: self.index.add(entry_from_raw(copied, path, flags, len(raw), raw)))
        return copied

    def delete(self, ref: MessageRef, permanent: bool) -> str:
        """Move to Trash, or remove for good when permanent or when it is already in Trash. Returns what happened."""
        try:
            trash = self.role_folder(FolderRole.TRASH)
        except StorageError:
            trash = ()
        if permanent or not trash or ref.folder_path == trash:
            self.store.delete(ref, hint=self.hint(ref.store_id))
            self._index(lambda: self.index.remove(ref.store_id))
            return "deleted"
        self.move(ref, trash)
        return "moved to Trash"

    def _index(self, change: Callable[[], None]) -> None:
        """The store is the truth: if the index cannot be updated, the next reconcile repairs it."""
        try:
            change()
        except ChronoMailError:
            pass

    def folders(self) -> list[Folder]:
        return self.store.list_folders()


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
