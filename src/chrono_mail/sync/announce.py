from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

from chrono_mail.core import Folder, MessageFlag, MessageRef

# --- Version ------------------------------------------------------------
__version__ = "announce.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: Initial: a mail check tells each change the moment it is stored, in one step with its index write (D-151)


class Announcer(Protocol):
    """Who is told about each change a mail check makes here, at the moment it makes it. changing() is held around the
    index write and the telling together, so nobody reads the index in between (D-151)."""

    def changing(self) -> AbstractContextManager[object]: ...
    def added(self, reference: MessageRef) -> None: ...
    def removed(self, reference: MessageRef, was_unread: bool) -> None: ...
    def flags(self, reference: MessageRef, flags: frozenset[MessageFlag], was_unread: bool) -> None: ...
    def tags(self, reference: MessageRef, tags: tuple[str, ...]) -> None: ...
    def moved(self, before: MessageRef, now: MessageRef) -> None: ...
    def folder_made(self, folder: Folder) -> None: ...


class _Silent:
    """Nobody is told (the sync tests, and code that has no one to tell)."""

    def changing(self) -> AbstractContextManager[object]:
        return nullcontext()

    def added(self, reference: MessageRef) -> None:
        pass

    def removed(self, reference: MessageRef, was_unread: bool) -> None:
        pass

    def flags(self, reference: MessageRef, flags: frozenset[MessageFlag], was_unread: bool) -> None:
        pass

    def tags(self, reference: MessageRef, tags: tuple[str, ...]) -> None:
        pass

    def moved(self, before: MessageRef, now: MessageRef) -> None:
        pass

    def folder_made(self, folder: Folder) -> None:
        pass


SILENT: Announcer = _Silent()
