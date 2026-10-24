from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime

from chrono_mail.config import AccountSettings, load_sidebar, save_sidebar
from chrono_mail.core import (
    ChronoMailError,
    ConfigurationError,
    Folder,
    FolderRole,
    MessageFlag,
    MessageRef,
    StorageError,
)
from chrono_mail.engine.changes import NUMBERED_KINDS, Change, FolderNumbers, MessageRow, flag_names
from chrono_mail.engine.imap_online import LOCAL_ONLY, GoneFromServer, ServerSession, is_imap, server_session
from chrono_mail.engine.old_files import settle_old_files
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.schedule import SCHEDULED_FOLDER, SNOOZED_FOLDER
from chrono_mail.engine.session import OUTBOX, Engine
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.store import MessageNotFoundError, Store, clean_tags
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.sync.imap_sync import imap_waiting
from chrono_mail.sync.mailconflicts import MailConflict
from chrono_mail.sync.mailconflicts import forget as forget_mail_conflict
from chrono_mail.sync.mailconflicts import keep_here as keep_mail_conflict_here
from chrono_mail.sync.mailconflicts import listed as listed_mail_conflicts
from chrono_mail.sync.mailconflicts import load as load_mail_conflicts

# --- Version ------------------------------------------------------------
__version__ = "mailbox.py 2.5.336"

# --- Version history ----------------------------------------------------
# v2.5.336: a move, copy or delivery takes the place from the store instead of looking for the message again in its whole new folder (W13, D-248)
# v2.4.324: restore_here: a message from a backup, kept here only under its own id (an IMAP account sends it at the next check, D-244)
# v2.2.314: the Outbox is timed too (a message there may wait for its moment, B-21); tell_changed: a row shows its new Send at
# v2.0.280: row_of: the row's sent moment (the Sent column, B-16)
# v1.1.252: A renamed folder takes its pins in Favorites along (and those of the folders inside it), before the rename is told (R-41)
# v1.1.250: Every fact about mail carries the new numbers of the folders it touched, read from the index under the same lock as the change (R-10)
# v1.1.247: together() keeps one server session for an action on many messages; prepare_flags, prepare_moves, prepare_copies and
#           prepare_deletes do the group on the server first, each message's own step then takes what the server did for it (R-08)
# v1.1.240: Settling a folder renamed or deleted offline: renamed back here, the waiting change given up, or a delete done anyway (R-31)
# v1.1.239: The mailbox's sync conflicts and what waits for the server are asked and settled through it (sync_conflicts, forget_sync_conflict,
#           keep_sync_conflict_here, waiting_for_server), so no command touches its store (R-37)
# v1.1.237: report_folder_renamed: a mail check renamed a folder as the server did (R-30)
# v1.1.227: take_server_marks and move_back: a refused offline change settled with the server's version, here only (D-195)
# v1.1.207: replace_waiting tells the fact changed with the new row (D-177)
# v1.1.206: Tags and snooze times from the messages' properties files; replace_waiting: a new version of a message waiting in Scheduled (D-176)
# v1.1.200: timed(): when each message leaves the Snoozed or the Scheduled folder (rows carry it); a move between two local-only folders never asks the server
# v0.5.164: a change holds the mailbox's place lock from its index write until its fact is told; reading and changing for questions and mail checks (D-151)
# v0.5.163: report_removed, report_flags, report_tags, report_folder_made, report_folder_deleted: what a mail check changed is told as facts
# v0.5.161: current_flags reads the flags from the name of the message's file instead of listing the whole folder
# v0.5.158: report_added and report_moved: sending stores its copies past the mailbox's operations and reports them here (D-146 step 4)
# v0.5.156: an action that names a message in a folder where it no longer is gets refused (it never hides the message)
# v0.5.155: every change reports its fact right after it succeeded (for IMAP after the server confirmed); Empty folder and Mark all as read report one fact each (D-146)
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
    _held: int = 0  # how often this mailbox holds its place lock: from an index write until the fact of the change is told
    _session: ServerSession | None = None  # the server session an action on many messages keeps open (together, R-08)

    @contextmanager
    def _server(self) -> Generator[ServerSession]:
        """The IMAP server session of this action: the one together() keeps open, else one for this step alone."""
        if self._session is not None:
            yield self._session
            return
        with server_session(self.engine, self.settings) as session:
            self._session = session
            try:
                yield session
            finally:
                self._session = None

    @contextmanager
    def together(self) -> Generator[None]:
        """An action on many messages: one server session (one connection, one hold on the account) for all of them."""
        if not is_imap(self.settings):
            yield
            return
        with self._server():
            yield

    def _checked(self, refs: list[MessageRef]) -> list[MessageRef]:
        """The messages a group step may do on the server: still where they are named and here (the checks each message's own step makes
        first); the others are left to their own step, which says why."""
        fit: list[MessageRef] = []
        for ref in refs:
            try:
                self._still_there(ref)
                self.store.locate(ref, self.hint(ref.store_id))
            except ChronoMailError:
                continue
            fit.append(ref)
        return fit

    def prepare_flags(self, wanted: dict[MessageRef, frozenset[MessageFlag]]) -> None:
        """Many messages get their marks on the server in one command per kind of change and 500 messages; then set_flags for each."""
        if is_imap(self.settings):
            with self._server() as server:
                server.prepare_flags({ref.store_id: wanted[ref] for ref in self._checked(list(wanted))})

    def prepare_moves(self, refs: list[MessageRef], target: tuple[str, ...]) -> None:
        """Many messages move on the server in one command per folder and 500 messages; then move for each."""
        if is_imap(self.settings):
            fit = [ref for ref in self._checked(refs) if not (ref.folder_path[0] in LOCAL_ONLY and target[0] in LOCAL_ONLY) and ref.folder_path != target]
            with self._server() as server:
                server.prepare_moves([ref.store_id for ref in fit], target)

    def prepare_copies(self, refs: list[MessageRef], target: tuple[str, ...]) -> None:
        if is_imap(self.settings):
            with self._server() as server:
                server.prepare_copies([ref.store_id for ref in self._checked(refs)], target)

    def prepare_deletes(self, refs: list[MessageRef], permanent: bool) -> None:
        """Many messages of one folder deleted: moved to Trash together, or removed for good together, as delete decides for each."""
        if not is_imap(self.settings) or not refs:
            return
        try:
            trash = self.role_folder(FolderRole.TRASH)
        except StorageError:
            trash = ()
        if permanent or not trash or refs[0].folder_path == trash:
            with self._server() as server:
                server.prepare_deletes([ref.store_id for ref in self._checked(refs)])
        else:
            self.prepare_moves(refs, trash)

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

    # --- the facts of each change (D-146) -------------------------------------------------------

    def row_of(self, store_id: str) -> MessageRow | None:
        """What a list shows of a message, from the index (None when the index does not have it)."""
        message = self.index.get(store_id)
        if message is None:
            return None
        moment = (self.timed(message.folder) or {}).get(store_id)
        snoozed_until = datetime.fromtimestamp(moment).astimezone() if moment is not None else None
        size = self.index.thread_sizes([message.thread_key]).get(message.thread_key, 1)
        return MessageRow(
            message.store_id, "/".join(message.folder), message.subject, message.sender, message.date, message.size, flag_names(message.flags),
            message.has_attachments, message.preview, message.recipients, size, account=self.store.account_id, snoozed_until=snoozed_until, tags=message.tags,
            sent=message.sent,
        )

    def timed(self, folder: tuple[str, ...] | None) -> dict[str, int] | None:
        """When each message leaves this folder (store id -> seconds): in Snoozed when it comes back, in Scheduled when it is
        sent (a repeating message: its next send). None for any other folder; for None (all folders) both, together."""
        found: dict[str, int] = {}
        if folder in (None, (SNOOZED_FOLDER,)):
            found.update({store_id: until for store_id, until in self.snoozed().items()})
        if folder in (None, (SCHEDULED_FOLDER,)):  # a repeating message carries its next send like any scheduled one
            found.update({store_id: moment for store_id, place, moment in self.index.scheduled() if place == (SCHEDULED_FOLDER,)})
        if folder in (None, OUTBOX):
            found.update(self.held_back())
        return found if folder is None or folder in ((SNOOZED_FOLDER,), (SCHEDULED_FOLDER,), OUTBOX) else None

    def held_back(self) -> dict[str, int]:
        """The messages in the Outbox that may not be sent before a moment (store id -> seconds): a delayed send counts down (B-21)."""
        try:
            here = [message.ref.store_id for message in self.store.list_messages(OUTBOX)]
        except StorageError:
            return {}
        return {store_id: found.send_not_before for store_id, found in self.store.properties.readable(here).items() if found.send_not_before is not None}

    def tell_changed(self, ref: MessageRef) -> None:
        """A message changed where it is, the message itself unchanged (its waiting time in the Outbox): the fact carries the row as a list shows it now."""
        self._tell(Change("changed", self._place, "/".join(ref.folder_path), ref.store_id, row=self.row_of(ref.store_id), role=self._role_of(ref.folder_path)))

    def snoozed(self) -> dict[str, int]:
        """The messages in the Snoozed folder that know when they come back (store id -> seconds), from their properties files."""
        try:
            here = [message.ref.store_id for message in self.store.list_messages((SNOOZED_FOLDER,))]
        except StorageError:
            return {}  # there is no Snoozed folder (yet)
        return {store_id: found.snoozed_until for store_id, found in self.store.properties.readable(here).items() if found.snoozed_until is not None}

    def replace_waiting(self, ref: MessageRef, raw: bytes) -> None:
        """A message that waits to be sent (in the Scheduled folder, local only) gets a new version of itself under the same id:
        a repeating message records its progress in itself, and an edited one its new content. The index follows, and the
        fact "changed" carries the row as a list shows it now (subject, people, Send at)."""
        if ref.folder_path != (SCHEDULED_FOLDER,):
            raise UsageError("Only a message waiting in the Scheduled folder is ever changed")
        self.store.replace(ref, raw, hint=self.hint(ref.store_id))
        path = self.store.locate(ref, self.hint(ref.store_id))
        flags = self.current_flags(ref)
        tags = self.tags_of(ref.store_id)
        self._index(lambda: self.index.add(replace(entry_from_raw(ref, path, flags, len(raw), raw), tags=tags)))
        self._tell(Change("changed", self._place, "/".join(ref.folder_path), ref.store_id, row=self.row_of(ref.store_id), role=self._role_of(ref.folder_path)))

    def _pins_follow(self, old: str, new: str) -> None:
        """A folder of this mailbox was renamed (here, on the server first, or followed from the server): its pins in Favorites point at
        the new name before the fact is told, so the window finds them there (R-41). A sidebar file that cannot be read or written is
        reported when it is next used (sidebar.show), as for a removed account; the rename itself has happened."""
        try:
            with self.engine.sidebar_lock:
                sidebar = load_sidebar(self.engine.directories.sidebar_file)
                followed = sidebar.pins_followed(self._place, old, new)
                if followed != sidebar:
                    save_sidebar(self.engine.directories.sidebar_file, followed)
        except ConfigurationError:
            pass

    def _numbers(self, folder: str) -> tuple[int, int]:
        """A folder's numbers now, from the index (still under the place lock of the change, D-151); (0, 0) when the index cannot say."""
        try:
            return self.index.numbers(tuple(folder.split("/")))
        except ChronoMailError:
            return 0, 0

    def _was_unread(self, store_id: str) -> bool:
        message = self.index.get(store_id)
        return message is not None and MessageFlag.SEEN not in message.flags

    def _tell(self, change: Change) -> None:
        try:
            if change.kind == "folder_renamed":
                self._pins_follow(change.folder, change.to_folder)
            if change.kind in NUMBERED_KINDS and not self.engine.is_quiet():  # a quiet big job tells only "mailbox changed" at its end
                with self.engine.place_lock(self._place):  # the numbers belong to this fact: no other change of the mailbox comes between (D-151)
                    folders = dict.fromkeys(folder for folder in (change.folder, change.to_folder if change.kind == "moved" else "") if folder)
                    self.engine.report(replace(change, numbers=tuple(FolderNumbers(folder, *self._numbers(folder)) for folder in folders)))
                return
            self.engine.report(change)
        finally:
            self.let_go()

    @property
    def _place(self) -> str:
        """The mailbox the facts are about: the account whose store holds the mail."""
        return self.store.account_id

    def _still_there(self, ref: MessageRef) -> None:
        """An action names a message in a folder: refuse it when the message is no longer there but somewhere else (an
        action given before an earlier one moved it). Without this, a missing file there would count as deleted and its
        line would leave the index, which hides the message where it really is."""
        message = self.index.get(ref.store_id)
        if message is None or message.folder == ref.folder_path:
            return
        try:
            self.store.locate(ref)
        except MessageNotFoundError:
            raise UsageError(f"This message is no longer in {'/'.join(ref.folder_path)}; it is in {'/'.join(message.folder)}") from None

    # --- changes that keep store and index in step ---------------------------------------------

    def set_flags(self, ref: MessageRef, flags: frozenset[MessageFlag]) -> frozenset[MessageFlag]:
        self._still_there(ref)
        if not is_imap(self.settings):
            return self._set_flags_here(ref, flags)
        self.store.locate(ref, self.hint(ref.store_id))  # the message must be here before the server is asked
        with self._server() as server:
            commit = server.set_flags(ref.store_id, flags)
            self._set_flags_here(ref, flags)
            commit()
        return flags

    # --- sync conflicts (D-195): this computer's own list, never mail; kept beside the store ---------------------------------------------------

    def sync_conflicts(self, kept_too: bool = False) -> list[MailConflict]:
        """The open sync conflicts of this mailbox (with kept_too also those kept here only)."""
        return load_mail_conflicts(self.store) if kept_too else listed_mail_conflicts(self.store)

    def forget_sync_conflict(self, conflict_id: str) -> None:
        """A conflict settled or released: no longer listed or held."""
        forget_mail_conflict(self.store, conflict_id)

    def keep_sync_conflict_here(self, conflict_id: str) -> None:
        """Keep here only: what it is about stays on this computer and is never sent."""
        keep_mail_conflict_here(self.store, conflict_id)

    def waiting_for_server(self) -> tuple[list[str], int]:
        """What of this IMAP mailbox was changed here and waits for the server: (messages, how many deleted here)."""
        return imap_waiting(self.store, ImapState(self.engine.directories.imap_state_file(self.account_id), durable=False))

    def undo_folder_rename(self, now: tuple[str, ...], before: tuple[str, ...]) -> None:
        """A rename made offline that the server refused: renamed back here (its server name never changed), the waiting rename given up."""
        with self.engine.imap_link(self.account_id).lock:  # never in the middle of a check of this account
            state = ImapState(self.engine.directories.imap_state_file(self.account_id), durable=self.engine.durable)
            self.store.rename_folder(now, before)
            self.index.rename_folder(now, before)
            state.rename_folder(now, before, {})
            state.pending_done(now)
        self._tell(Change("folder_renamed", self._place, "/".join(now), to_folder="/".join(before)))

    def give_up_folder_change(self, path: tuple[str, ...]) -> None:
        """A delete made offline is given up: the next check brings the folder back from the server."""
        with self.engine.imap_link(self.account_id).lock:
            ImapState(self.engine.directories.imap_state_file(self.account_id), durable=self.engine.durable).pending_done(path)

    def delete_folder_anyway(self, path: tuple[str, ...]) -> None:
        """A delete made offline goes to the server at the next check, although the server changed the folder (the person chose)."""
        with self.engine.imap_link(self.account_id).lock:
            ImapState(self.engine.directories.imap_state_file(self.account_id), durable=self.engine.durable).force_delete(path)

    def take_server_marks(self, ref: MessageRef, flags: frozenset[MessageFlag]) -> frozenset[MessageFlag]:
        """Settling a refused change of marks (D-195): the server's marks here, and only here (the server has them already)."""
        return self._set_flags_here(ref, flags)

    def move_back(self, ref: MessageRef, folder: tuple[str, ...]) -> MessageRef:
        """Settling a refused move (D-195): the message back here to the folder the server keeps it in, and only here."""
        return self._move_here(ref, folder)

    def _set_flags_here(self, ref: MessageRef, flags: frozenset[MessageFlag]) -> frozenset[MessageFlag]:
        was_unread = self._was_unread(ref.store_id)
        path = self.store.set_flags(ref, flags, hint=self.hint(ref.store_id))
        self._index(lambda: self.index.update_flags(ref.store_id, flags, path))
        self._tell(Change(
            "flags", self._place, "/".join(ref.folder_path), ref.store_id, was_unread=was_unread, unread=MessageFlag.SEEN not in flags, flags=flag_names(flags),
        ))
        return flags

    def current_flags(self, ref: MessageRef) -> frozenset[MessageFlag]:
        self._still_there(ref)
        return self.store.flags(ref, self.hint(ref.store_id))  # from the name of its file: the cost of one message, not of its folder

    def move(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        self._still_there(ref)
        if not is_imap(self.settings) or (ref.folder_path[0] in LOCAL_ONLY and target[0] in LOCAL_ONLY):  # the server has neither folder
            return self._move_here(ref, target)
        flags = self.current_flags(ref)  # the message must be here before the server is asked
        with self._server() as server:
            commit = server.move(ref.store_id, target, flags)
            moved = self._move_here(ref, target)
            commit()
        return moved

    def _move_here(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        was_unread = self._was_unread(ref.store_id)
        placed = self.store.move_placed(ref, target, hint=self.hint(ref.store_id))
        moved, path = placed.ref, placed.place
        self._index(lambda: self.index.move(moved.store_id, target, path))
        row = self.row_of(moved.store_id)
        self._tell(Change(
            "moved", self._place, "/".join(ref.folder_path), ref.store_id, to_folder="/".join(target), new_message_id=moved.store_id, row=row,
            was_unread=was_unread, unread=row is not None and "seen" not in row.flags, role=self._role_of(target),
        ))
        return moved

    def deliver(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> MessageRef:
        """Store a message (verified) and index it; in an IMAP account it is put into the server folder first."""
        if not is_imap(self.settings):
            return self._deliver_here(folder, raw, flags)
        with self._server() as server:
            commit = server.append(folder, raw, flags)
            reference = self._deliver_here(folder, raw, flags)
            commit(reference.store_id)
        return reference

    def restore_here(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag], store_id: str) -> MessageRef:
        """A message from a backup that is not here: stored and indexed here only, under the id it had (its properties keep fitting).
        In an IMAP account it is a message made here: the next check puts it on the server, as one made offline (D-193)."""
        placed = self.store.deliver_placed(folder, raw, flags=flags, verify=True, store_id=store_id)
        reference, path = placed.ref, placed.place
        self._index(lambda: self.index.add(entry_from_raw(reference, path, flags, len(raw), raw)))
        self._tell_added(reference)
        return reference

    def _deliver_here(self, folder: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> MessageRef:
        placed = self.store.deliver_placed(folder, raw, flags=flags, verify=True)
        reference, path = placed.ref, placed.place
        self._index(lambda: self.index.add(entry_from_raw(reference, path, flags, len(raw), raw)))
        self._tell_added(reference)
        return reference

    def _tell_added(self, reference: MessageRef) -> None:
        row = self.row_of(reference.store_id)
        self._tell(Change(
            "added", self._place, "/".join(reference.folder_path), reference.store_id, row=row, unread=row is not None and "seen" not in row.flags,
            role=self._role_of(reference.folder_path),
        ))

    def report_added(self, reference: MessageRef) -> None:
        """Sending stored a message past the mailbox's operations (sync/send.py): it is told as added where it is now."""
        self._tell_added(reference)

    def report_moved(self, before: MessageRef, now: MessageRef) -> None:
        """Sending moved a stored message past the mailbox's operations (from the Outbox to Sent): it is told as moved."""
        row = self.row_of(now.store_id)
        unread = row is not None and "seen" not in row.flags
        self._tell(Change(
            "moved", self._place, "/".join(before.folder_path), before.store_id, to_folder="/".join(now.folder_path), new_message_id=now.store_id, row=row,
            was_unread=unread, unread=unread, role=self._role_of(now.folder_path),
        ))

    def report_removed(self, reference: MessageRef, was_unread: bool) -> None:
        """A mail check removed a message past the mailbox's operations (gone from the server): it is told as removed."""
        self._tell(Change("removed", self._place, "/".join(reference.folder_path), reference.store_id, was_unread=was_unread))

    def report_flags(self, reference: MessageRef, flags: frozenset[MessageFlag], was_unread: bool) -> None:
        """A mail check took over the flags of a message from the server: they are told as changed."""
        self._tell(Change(
            "flags", self._place, "/".join(reference.folder_path), reference.store_id, was_unread=was_unread, unread=MessageFlag.SEEN not in flags,
            flags=flag_names(flags),
        ))

    def report_tags(self, reference: MessageRef, tags: tuple[str, ...]) -> None:
        """A mail check took over the tags of a message from the server (its keywords)."""
        self._tell(Change("tags", self._place, "/".join(reference.folder_path), reference.store_id, tags=tags))

    def report_folder_made(self, folder: Folder) -> None:
        """A mail check made a folder (the server has a new one)."""
        self._tell(Change("folder_made", self._place, "/".join(folder.path), role=folder.role.value))

    def report_folder_renamed(self, old: tuple[str, ...], new: tuple[str, ...]) -> None:
        """A mail check renamed a folder here (the server renamed it)."""
        self._tell(Change("folder_renamed", self._place, "/".join(old), to_folder="/".join(new)))

    def report_folder_deleted(self, path: tuple[str, ...]) -> None:
        """A mail check removed a folder here (the server no longer has it)."""
        self._tell(Change("folder_deleted", self._place, "/".join(path)))

    def _role_of(self, path: tuple[str, ...]) -> str:
        return next((folder.role.value for folder in self.store.list_folders() if folder.path == path), FolderRole.OTHER.value)

    def copy(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        self._still_there(ref)
        if not is_imap(self.settings):
            return self._copy_here(ref, target)
        flags = self.current_flags(ref)
        with self._server() as server:
            commit = server.copy(ref.store_id, target, flags)
            copied = self._copy_here(ref, target)
            commit(copied.store_id)
        return copied

    def _copy_here(self, ref: MessageRef, target: tuple[str, ...]) -> MessageRef:
        placed = self.store.copy_placed(ref, target, hint=self.hint(ref.store_id))  # the store gives the copy the tags of the original
        copied, path = placed.ref, placed.place
        raw = self.store.read(copied, hint=path)
        flags = self.store.flags(copied, path)  # the copy is not in the index yet: its place comes from the store
        tags = self.tags_of(copied.store_id)
        self._index(lambda: self.index.add(replace(entry_from_raw(copied, path, flags, len(raw), raw), tags=tags)))
        self._tell_added(copied)
        return copied

    def tags_of(self, store_id: str) -> tuple[str, ...]:
        try:
            return self.store.properties.tags_of(store_id)
        except StorageError:
            return ()

    def rename_tag(self, old: str, new: str | None) -> int:
        """Rename a tag in every message of this mailbox (None removes it). Returns how many messages changed.
        In an IMAP account message by message, on the server first."""
        if is_imap(self.settings):
            changed_here = 0
            wanted = old.casefold()
            for store_id, tags in self.store.properties.tags().items():
                if not any(tag.casefold() == wanted for tag in tags):
                    continue
                row = self.index.get(store_id)
                if row is None:
                    continue
                renamed = tuple(new if tag.casefold() == wanted else tag for tag in tags if tag.casefold() != wanted or new is not None)
                self.set_tags(MessageRef(self.store.account_id, row.folder, store_id), tuple(tag for tag in renamed if tag is not None))
                changed_here += 1
            return changed_here
        changed = self.store.properties.rename_tag(old, new)
        for store_id in changed:
            tags = self.tags_of(store_id)
            self._index(self._tags_updater(store_id, tags))
            message = self.index.get(store_id)
            self._tell(Change("tags", self._place, "/".join(message.folder) if message is not None else "", store_id, tags=tags))
        return len(changed)

    def _tags_updater(self, store_id: str, tags: tuple[str, ...]) -> Callable[[], None]:
        return lambda: self.index.update_tags(store_id, tags)

    def set_tags(self, ref: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        """Give a message exactly these tags (its properties file is the truth, the index copies it). Returns them as kept.
        The message must be here."""
        self._still_there(ref)
        self.store.locate(ref, self.hint(ref.store_id))
        if not is_imap(self.settings):
            return self._set_tags_here(ref, tags)
        cleaned = clean_tags(tags)
        with self._server() as server:
            commit = server.set_tags(ref.store_id, cleaned)
            kept = self._set_tags_here(ref, cleaned)
            commit()
        return kept

    def _set_tags_here(self, ref: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        kept = self.store.properties.set_tags(ref.store_id, tags)
        self._index(lambda: self.index.update_tags(ref.store_id, kept))
        self._tell(Change("tags", self._place, "/".join(ref.folder_path), ref.store_id, tags=kept))
        return kept

    def delete(self, ref: MessageRef, permanent: bool) -> str:
        """Move to Trash, or remove for good when permanent or when it is already in Trash. Returns what happened.
        A message whose file is gone is deleted already: only its line in the index is removed, and the answer is "gone"."""
        self._still_there(ref)
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
                    with self._server() as server:
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
        self._removed(ref)
        return "deleted"

    def _removed(self, ref: MessageRef) -> None:
        """The message left the mailbox for good: its line leaves the index, and that is told."""
        was_unread = self._was_unread(ref.store_id)
        self._index(lambda: self.index.remove(ref.store_id))
        self._tell(Change("removed", self._place, "/".join(ref.folder_path), ref.store_id, was_unread=was_unread))

    def _delete_on_server_first(self, ref: MessageRef) -> str:
        with self._server() as server:
            commit = server.delete(ref.store_id)  # also when the file is gone here: a missing file is a deleted message
            try:
                self.store.delete(ref, hint=self.hint(ref.store_id))
            except MessageNotFoundError:
                self.forget(ref)
                commit()
                return "gone"
            self._removed(ref)
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
            with self._server() as server:
                server.append(ref.folder_path, raw, flags)(ref.store_id)
        except ChronoMailError:
            pass  # the message was sent and is safe here: the next sync puts the copy on the server

    def forget(self, ref: MessageRef) -> None:
        """Remove the line of a message whose file is gone from the index."""
        self._removed(ref)

    def _index(self, change: Callable[[], None]) -> None:
        """The store is the truth: if the index cannot be updated, the next reconcile repairs it. The mailbox's place lock
        is taken first and kept until the change is told (_tell), so no question reads the index in between (D-151); a big
        job that runs quietly tells its changes only at its end, so it does not hold the lock."""
        if not self.engine.is_quiet():
            self.engine.place_lock(self._place).acquire()
            self._held += 1
        try:
            change()
        except ChronoMailError:
            pass

    def let_go(self) -> None:
        """The changes written to the index are told (or failed): the place lock is free again."""
        lock = self.engine.place_lock(self._place)
        while self._held:
            self._held -= 1
            lock.release()

    @contextmanager
    def reading(self) -> Generator[None]:
        """A question about the messages of this mailbox: it reads the index while no change is between its write and its
        fact (D-151)."""
        with self.engine.place_lock(self._place):
            yield

    @contextmanager
    def changing(self) -> Generator[None]:
        """A change made past the mailbox's operations (a mail check): its index write and its fact in one step."""
        with self.engine.place_lock(self._place):
            yield

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
        self._tell(Change("folder_deleted", self._place, "/".join(path)))

    def ensure_folder(self, path: tuple[str, ...], role: FolderRole = FolderRole.OTHER) -> tuple[Folder, bool]:
        """The folder with this path, made when there is none (and whether it was made): on the server first."""
        if self._on_server(path) and path not in {folder.path for folder in self.store.list_folders()}:
            Folder(self.store.account_id, path, role)  # a name that cannot be used is refused before the server is asked
            with self._server() as server:
                server.make_folder(path)  # when the server cannot be reached the next sync makes it there
                return self._made(self.store.ensure_folder(path, role))
        return self._made(self.store.ensure_folder(path, role))

    def _made(self, result: tuple[Folder, bool]) -> tuple[Folder, bool]:
        folder, made = result
        if made:
            self._tell(Change("folder_made", self._place, "/".join(folder.path), role=folder.role.value))
        return result

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
            self._tell(Change("folder_renamed", self._place, "/".join(old), to_folder="/".join(new)))
            return
        known = self.store.list_folders()
        if any(folder.path[: len(old)] == old and folder.role is not FolderRole.OTHER for folder in known):
            raise UsageError("The standard folders cannot be renamed in an IMAP account")
        Folder(self.store.account_id, new, FolderRole.OTHER)  # a name that cannot be used is refused before the server is asked
        if new[0] in LOCAL_ONLY or any(folder.path[: len(new)] == new for folder in known):
            raise StorageError("A folder with the new path already exists")
        with self._server() as server:
            commit = server.rename_folder(old, new)
            self.store.rename_folder(old, new)
            self.index.rename_folder(old, new)
            commit()
        self._tell(Change("folder_renamed", self._place, "/".join(old), to_folder="/".join(new)))

    def delete_folder(self, path: tuple[str, ...], require_empty: bool) -> int:
        """Take one folder out of the store (its files are kept in .deleted); in an IMAP account it is deleted on the
        server first, with its mail, and only while the server can be reached. Returns the messages removed from the index."""
        if not self._on_server(path):
            self.store.delete_folder(path, require_empty=require_empty)
            removed = self.index.remove_folder(path)
        else:
            with self._server() as server:
                commit = server.delete_folder(path)
                self.store.delete_folder(path, require_empty=require_empty)
                removed = self.index.remove_folder(path)
                commit()
        self._tell(Change("folder_deleted", self._place, "/".join(path)))
        return removed

    def empty_folder(self, path: tuple[str, ...]) -> int:
        """Delete every message of the folder for good; in an IMAP account the server folder as a whole first. Told as one
        fact (folder emptied), not one per message."""
        with self.engine.quietly(summary=False):
            removed = self._empty_folder(path)
        self._tell(Change("folder_emptied", self._place, "/".join(path), count=removed))
        return removed

    def _empty_folder(self, path: tuple[str, ...]) -> int:
        if self._on_server(path):
            with self._server() as server:
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
        """Mark every message of the folder as read; in an IMAP account the server folder as a whole first. Told as one
        fact (folder read), not one per message."""
        with self.engine.quietly(summary=False):
            marked = self._mark_folder_read(path)
        self._tell(Change("folder_read", self._place, "/".join(path), count=marked))
        return marked

    def _mark_folder_read(self, path: tuple[str, ...]) -> int:
        unread = [message for message in self.store.list_messages(path) if MessageFlag.SEEN not in message.flags]
        if self._on_server(path):
            with self._server() as server:
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
    settle_old_files(engine, storage, store, index)  # once per run: the side files of before 1.1.206 move into the messages and their properties
    mailbox = Mailbox(engine, settings, store, index)
    try:
        yield mailbox
    finally:
        mailbox.let_go()  # a change that failed between its index write and its fact never keeps the lock
        index.close()
