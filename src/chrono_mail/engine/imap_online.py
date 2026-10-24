from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, AuthenticationError, ConfigurationError, FolderRole, MessageFlag, ProtocolError
from chrono_mail.engine.changes import Change
from chrono_mail.engine.registry import UsageError
from chrono_mail.engine.session import Engine, ImapLink
from chrono_mail.protocols.imap import ImapClient, ImapRefused, keyword_to_tag, tag_to_keyword
from chrono_mail.sync import ImapState, plan_folders, planned_anyway
from chrono_mail.sync.imap_sync import LOCAL_ONLY  # first parts of the local folders that never exist on a server

# --- Version ------------------------------------------------------------
__version__ = "imap_online.py 3.0.339"

# --- Version history ----------------------------------------------------
# v3.0.339: a folder is deleted on the server only when it holds no mail this computer never had (the server deletes it with its mail; RV-108)
# v2.4.330: a folder that is on the server but not subscribed is used, never created again ("Mailbox already exists", W7)
# v2.0.284: A folder made on the server is known to the sync at once (its server name and how the server numbers it), so a rename or delete
#           before the next check, on the server or here offline, is followed and never makes it again (the Windows computer's F1)
# v1.1.247: Many messages at once: prepare_flags, prepare_moves, prepare_copies and prepare_deletes do a group of a folder's messages on the
#           server in one command per 500 UIDs (their presence asked with one UID SEARCH); each message's own step then takes what the server
#           did for it (ready) instead of asking again (R-08)
# v1.1.241: Standard folders and the ones ticked here are planned whether or not they are on the server's list of folders to show (R-34)
# v1.1.240: A folder renamed or deleted while the server cannot be reached is done here at once and remembered; the next check does it there (R-31)
# v1.1.238: A move uses the client's one way of moving (uid_move), the same as the sync's (R-35)
# v1.1.227: An action done here only (the server not reached) marks the account as having changes waiting and tells the window (D-195)
# v1.1.200: LOCAL_ONLY is the sync's own list (with Scheduled)
# v0.5.138: set_tags: tags as keywords on the server first; a change of flags keeps the keywords in the sync state
# v0.5.123: folders too: make, rename (the old names unsubscribed), delete, empty and mark all read on the server first; a message gone from the server is refused (D-118)
# v0.4.120: Initial: the actions of an IMAP account are done on the server first, and here only when the server confirmed (D-117)

# IMAP is the server's mailbox: an action (marks, move, copy, delete, a draft, the Sent copy, folders) is done on the server first,
# and only when the server confirmed it is the same done here, and the sync state updated at once, so that the next sync
# has nothing left to do. A refusal of the server is an error and nothing changes here. When the server cannot be reached
# the action is done here only and the next sync sends it (the comparison of D-050); a delete for good is noted so that the
# safety rules of the sync never hold it back. Every step names messages by UID: nothing else in a folder is touched.

OFFLINE_NOTICE: str = "The server of {account} could not be reached: the change is made there at the next mail check"
LEAVE_OUT_UNREACHABLE: str = "The server of {account} cannot be reached: a folder is only left out while the server can be reached"
BATCH: int = 500  # UIDs per command, so that a command line stays short


class GoneFromServer(ImapRefused):
    """The message is no longer on the server (deleted there by another program): the action is not done here either."""

    def __init__(self, local: tuple[str, ...], uid: int) -> None:
        super().__init__("This message is no longer on the server; the next mail check removes it here too")
        self.local: tuple[str, ...] = local
        self.uid: int = uid


def _uid_batches(uids: tuple[int, ...]) -> list[str]:
    return [",".join(str(uid) for uid in uids[start : start + BATCH]) for start in range(0, len(uids), BATCH)]


def is_imap(settings: AccountSettings) -> bool:
    return settings.account.kind is AccountKind.IMAP


def flag_names(flags: frozenset[MessageFlag]) -> frozenset[str]:
    """The flags as the sync state keeps them (lower-case IMAP names)."""
    return frozenset(flag.imap_name.lower() for flag in flags)


SYSTEM_NAMES: frozenset[str] = flag_names(frozenset(MessageFlag))


Commit = Callable[[], None]  # records what the server did in the sync state; called once the same was done here
CommitNew = Callable[[str], None]  # the same for a step that makes a new message here (its store id)


def _nothing() -> None:
    """Nothing to record: the message is not on the server, or the server could not be reached."""


def _nothing_new(store_id: str) -> None:
    """Nothing to record for a new message (the next sync puts it on the server)."""


@dataclass
class Ready:
    """What a step done for a group (prepare_*) did on the server for one message, kept until the message's own step takes it."""

    kind: str  # "flags", "move", "copy", "delete"
    local: tuple[str, ...]
    uid: int
    gone: bool = False  # the server no longer has the message
    refused: ImapRefused | None = None  # the server said no for its group
    new_uid: int | None = None  # a move or copy: its UID in the target folder (COPYUID)
    validity: int | None = None  # ... and that folder's UIDVALIDITY
    expunged: bool = True  # a delete: removed there (with UIDPLUS), or only marked deleted


@dataclass
class ServerSession:
    """One action's hold on an IMAP account: its open connection and its sync state, read fresh under the link's lock."""

    engine: Engine
    settings: AccountSettings
    link: ImapLink
    state: ImapState
    offline: bool = False
    ready: dict[str, Ready] = field(default_factory=dict[str, Ready])  # what a group step did per message (R-08)

    # --- the connection --------------------------------------------------------------------------------------------

    def _client(self, notice: bool = True) -> ImapClient | None:
        """The open connection (made or made again when needed); None when the server cannot be reached."""
        if self.offline:
            return None
        link = self.link
        if link.client is not None:
            try:
                link.client.noop()
                return link.client
            except ProtocolError:
                link.close()  # the server closed an idle connection: a new one is made below
        try:
            password = self.engine.passwords.get(self.settings.account.account_id, ServerRole.INCOMING)
            if password is None:
                raise ConfigurationError("No password is stored for this account")
            client = ImapClient(self.settings.account.incoming, password, ssl_context=self.engine.tls_context(self.settings))
            client.connect()
        except (ProtocolError, AuthenticationError, ConfigurationError):
            self._go_offline(notice)
            return None
        link.client = client
        return client

    def _go_offline(self, notice: bool = True) -> None:
        self.offline = True
        self.link.close()
        if notice:
            self.engine.notice(OFFLINE_NOTICE.format(account=self.settings.account.email_address))

    def _unreachable(self, text: str) -> ProtocolError:
        """For a step that cannot wait for the next check (leaving a folder out): an error, and nothing changes here."""
        self._go_offline(notice=False)
        return ProtocolError(text.format(account=self.settings.account.email_address))

    def _later(self, commit: Commit) -> Commit:
        """The server cannot be reached: done here, remembered by the commit, done there by the next check; told and counted as waiting."""
        self._go_offline()
        return commit

    def _lost(self, error: ProtocolError) -> None:
        """A step failed: a refusal of the server is the person's error; anything else means the server cannot be reached."""
        if isinstance(error, ImapRefused):
            raise error
        self._go_offline()

    # --- where things are on the server ----------------------------------------------------------------------------

    def _folders(self, client: ImapClient, fresh: bool = False) -> dict[tuple[str, ...], str]:
        """Local folder -> server folder, planned exactly as the sync plans them."""
        if self.link.folders is None or fresh:
            listed = client.list_folders()
            subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
            plans, _ = plan_folders(listed, subscribed, planned_anyway(self.state), dict(self.state.roles))
            self.link.folders = {plan.local_path: plan.server_name for plan in plans if plan.server_name not in self.state.hidden}
            self.link.delimiter = next((entry.delimiter for entry in listed if entry.delimiter), "/")
        return self.link.folders

    def server_folder(self, client: ImapClient, local: tuple[str, ...], create: bool) -> str | None:
        """The server folder of a local folder; made on the server when create and it is not there yet (as the sync does)."""
        if local[0] in LOCAL_ONLY:
            return None
        folders = self._folders(client)
        if local not in folders:
            folders = self._folders(client, fresh=True)  # made since the folders were last listed
        if local in folders or not create:
            return folders.get(local)
        name = self._server_name(local)
        if name is None:
            return None
        if not client.folder_exists(name):  # a folder the server has but does not show is used, never made again (W7)
            client.create_folder(name)
            client.subscribe(name)
        folders[local] = name
        self.state.set_server_name(local, name)  # known to the sync at once, as when a check makes it (F1)
        self._known(client, local, name)
        return name

    def _server_name(self, local: tuple[str, ...]) -> str | None:
        """The name a new local folder gets on the server (as the sync makes it); None when it cannot be written there."""
        delimiter = self.link.delimiter
        if delimiter in "".join(local):
            return None
        return delimiter.join(("INBOX" if local[0] == "Inbox" else local[0], *local[1:]))

    def _leave(self, client: ImapClient, server: str) -> None:
        """Close a server folder before it is renamed or deleted (some servers refuse that for the open folder)."""
        if client.selected == server:
            client.select("INBOX", read_only=True)
        self.link.validity.pop(server, None)

    def _select(self, client: ImapClient, local: tuple[str, ...], server: str) -> bool:
        """Open a server folder; False when the server numbers its messages differently than the sync state knows."""
        if client.selected != server or server not in self.link.validity or server not in self.link.keywords:
            info = client.select(server)
            self.link.validity[server] = info.uid_validity
            self.link.keywords[server] = info.keeps_keywords
        return self.state.validity.get(local) == self.link.validity[server]

    def _known(self, client: ImapClient, local: tuple[str, ...], server: str) -> None:
        """Learn how a server folder numbers its messages when the sync state does not know it yet (a folder never
        synchronised, or made just now), so that what lands there can be recorded. Safe: the sync still matches by Message-ID."""
        if self.state.validity.get(local) is None:
            self.link.validity[server] = client.select(server).uid_validity
            self.state.set_validity(local, self.link.validity[server])

    def _place(self, client: ImapClient, store_id: str) -> tuple[tuple[str, ...], int, str] | None:
        """Where a stored message is on the server (local folder, UID, server folder), with that folder opened; None when
        the server does not have it (a message made here and not yet sent there) or the sync must match it first."""
        found = self.state.find(store_id)
        if found is None:
            return None
        local, uid = found
        server = self.server_folder(client, local, create=False)
        if server is None or not self._select(client, local, server):
            return None
        if not client.uid_fetch(str(uid), "(UID)"):
            raise GoneFromServer(local, uid)
        return local, uid, server

    # --- many messages at once (R-08): one command per group of a folder, each message's own step takes its result ----------------------------

    def _present(self, client: ImapClient, store_ids: list[str]) -> dict[tuple[tuple[str, ...], str], list[tuple[str, int]]]:
        """The messages the server has, by folder (local, server folder), each with its UID; one UID SEARCH per 500. A message the server no
        longer has is ready as gone. Messages the sync must match first (a folder renumbered, not on the server) are left to their own step."""
        groups: dict[tuple[tuple[str, ...], str], list[tuple[str, int]]] = {}
        for store_id in store_ids:
            found = self.state.find(store_id)
            if found is None:
                continue
            local, uid = found
            server = self.server_folder(client, local, create=False)
            if server is not None:
                groups.setdefault((local, server), []).append((store_id, uid))
        present: dict[tuple[tuple[str, ...], str], list[tuple[str, int]]] = {}
        for (local, server), members in groups.items():
            if not self._select(client, local, server):
                continue
            there: set[int] = set()
            for batch in _uid_batches(tuple(uid for _, uid in members)):
                there.update(client.uid_search(f"UID {batch}"))
            for store_id, uid in members:
                if uid not in there:
                    self.ready[store_id] = Ready("", local, uid, gone=True)
            present[(local, server)] = [(store_id, uid) for store_id, uid in members if uid in there]
        return present

    def _grouped(self, kind: str, store_ids: list[str], step: Callable[[ImapClient, tuple[str, ...], str, list[tuple[str, int]]], None]) -> None:
        """Run a group step per folder; a refusal holds for the messages of that group, a lost connection leaves the rest to their own step."""
        client = self._client()
        if client is None:
            return
        try:
            for (local, server), members in self._present(client, store_ids).items():
                if not self._select(client, local, server):
                    continue
                for start in range(0, len(members), BATCH):
                    part = members[start : start + BATCH]
                    try:
                        step(client, local, server, part)
                    except ImapRefused as refused:
                        for store_id, uid in part:
                            self.ready[store_id] = Ready(kind, local, uid, refused=refused)
            for item in self.ready.values():
                if not item.kind:
                    item.kind = kind
        except ImapRefused:
            raise
        except ProtocolError as error:
            self._lost(error)

    def prepare_flags(self, wanted: dict[str, frozenset[MessageFlag]]) -> None:
        """Give many messages their marks: one UID STORE per kind of change and 500 messages."""

        def step(client: ImapClient, local: tuple[str, ...], server: str, part: list[tuple[str, int]]) -> None:
            changes: dict[tuple[tuple[str, ...], tuple[str, ...]], list[tuple[str, int]]] = {}
            for store_id, uid in part:
                known = self.state.folder_entries(local)[uid].flags
                flags = wanted[store_id]
                added = tuple(sorted(flag.imap_name for flag in flags if flag.imap_name.lower() not in known))
                removed = tuple(sorted(flag.imap_name for flag in MessageFlag if flag.imap_name.lower() in known and flag not in flags))
                changes.setdefault((added, removed), []).append((store_id, uid))
            for (added, removed), members in changes.items():
                uids = ",".join(str(uid) for _, uid in members)
                if added:
                    client.uid_store(uids, list(added), "add")
                if removed:
                    client.uid_store(uids, list(removed), "remove")
                for store_id, uid in members:
                    self.ready[store_id] = Ready("flags", local, uid)

        self._grouped("flags", list(wanted), step)

    def prepare_moves(self, store_ids: list[str], target: tuple[str, ...]) -> None:
        """Move many messages to a folder: one UID MOVE per folder and 500 messages (without MOVE: copied, then removed by their UIDs)."""
        client = self._client()
        if client is None:
            return
        try:
            destination = self.server_folder(client, target, create=True)
            if destination is None:
                return
            self._known(client, target, destination)
        except ImapRefused:
            raise
        except ProtocolError as error:
            self._lost(error)
            return

        def step(client: ImapClient, local: tuple[str, ...], server: str, part: list[tuple[str, int]]) -> None:
            result = client.uid_move(",".join(str(uid) for _, uid in part), destination)
            for store_id, uid in part:
                new = result.uids.get(uid, result.uid if len(part) == 1 else None)
                self.ready[store_id] = Ready("move", local, uid, new_uid=new, validity=result.uid_validity)

        self._grouped("move", store_ids, step)

    def prepare_copies(self, store_ids: list[str], target: tuple[str, ...]) -> None:
        """Copy many messages to a folder: one UID COPY per folder and 500 messages."""
        client = self._client()
        if client is None:
            return
        try:
            destination = self.server_folder(client, target, create=True)
            if destination is None:
                return
            self._known(client, target, destination)
        except ImapRefused:
            raise
        except ProtocolError as error:
            self._lost(error)
            return

        def step(client: ImapClient, local: tuple[str, ...], server: str, part: list[tuple[str, int]]) -> None:
            result = client.uid_copy(",".join(str(uid) for _, uid in part), destination)
            for store_id, uid in part:
                new = result.uids.get(uid, result.uid if len(part) == 1 else None)
                self.ready[store_id] = Ready("copy", local, uid, new_uid=new, validity=result.uid_validity)

        self._grouped("copy", store_ids, step)

    def prepare_deletes(self, store_ids: list[str]) -> None:
        """Delete many messages for good: marked deleted and removed by their UIDs, one command each per folder and 500 messages."""

        def step(client: ImapClient, local: tuple[str, ...], server: str, part: list[tuple[str, int]]) -> None:
            uids = ",".join(str(uid) for _, uid in part)
            client.uid_store(uids, ["\\Deleted"], "add")
            expunged = "UIDPLUS" in client.capabilities
            if expunged:
                client.uid_expunge(uids)
            for store_id, uid in part:
                self.ready[store_id] = Ready("delete", local, uid, expunged=expunged)

        self._grouped("delete", store_ids, step)

    def _taken(self, store_id: str, kind: str) -> Ready | None:
        """What a group step did for this message, if one did; its refusal or its being gone is raised as its own step would."""
        item = self.ready.get(store_id)
        if item is None or item.kind != kind:
            return None
        del self.ready[store_id]
        if item.refused is not None:
            raise item.refused
        if item.gone and kind != "delete":
            raise GoneFromServer(item.local, item.uid)
        return item

    # --- the steps --------------------------------------------------------------------------------------------------

    def set_flags(self, store_id: str, flags: frozenset[MessageFlag]) -> Commit:
        done = self._taken(store_id, "flags")
        if done is not None:
            local, uid = done.local, done.uid
            names = flag_names(flags) | frozenset(name for name in self.state.folder_entries(local)[uid].flags if name not in SYSTEM_NAMES)
            return lambda: self.state.set_flags(local, uid, names)
        client = self._client()
        if client is None:
            return _nothing
        try:
            place = self._place(client, store_id)
            if place is None:
                return _nothing
            local, uid, _ = place
            known = self.state.folder_entries(local)[uid].flags
            wanted = flag_names(flags)
            added = [flag.imap_name for flag in flags if flag.imap_name.lower() not in known]
            removed = [flag.imap_name for flag in MessageFlag if flag.imap_name.lower() in known and flag not in flags]
            if added:
                client.uid_store(str(uid), added, "add")
            if removed:
                client.uid_store(str(uid), removed, "remove")
        except ProtocolError as error:
            self._lost(error)
            return _nothing
        wanted |= frozenset(name for name in known if name not in SYSTEM_NAMES)  # the keywords (tags) stay as they are
        return lambda: self.state.set_flags(local, uid, wanted)

    def set_tags(self, store_id: str, tags: tuple[str, ...]) -> Commit:
        """Give a message exactly these tags on the server, as keywords. A folder where the server keeps no keywords: the
        tags stay here only. Offline: here first, the next sync sends them."""
        client = self._client()
        if client is None:
            return _nothing
        try:
            place = self._place(client, store_id)
            if place is None:
                return _nothing
            local, uid, server = place
            if not self.link.keywords.get(server, False):
                return _nothing
            fetched = client.uid_fetch(str(uid), "(UID FLAGS)")
            on_server = fetched[0].flags if fetched else frozenset[str]()
            have = {keyword.lower(): keyword for keyword in on_server if keyword_to_tag(keyword) is not None}
            wanted = {tag_to_keyword(tag).lower(): tag_to_keyword(tag) for tag in tags}
            added = [keyword for name, keyword in wanted.items() if name not in have]
            removed = [keyword for name, keyword in have.items() if name not in wanted]  # as the server writes them
            if added:
                client.uid_store(str(uid), added, "add")
            if removed:
                client.uid_store(str(uid), removed, "remove")
        except ProtocolError as error:
            self._lost(error)
            return _nothing
        known = self.state.folder_entries(local)[uid].flags
        recorded = frozenset(name for name in known if name in SYSTEM_NAMES) | frozenset(wanted)
        return lambda: self.state.set_flags(local, uid, recorded)

    def move(self, store_id: str, target: tuple[str, ...], flags: frozenset[MessageFlag]) -> Commit:
        """Move a message to another folder on the server. Without MOVE: copy, then remove the original by its UID only."""
        done = self._taken(store_id, "move")
        if done is not None:
            return self._moved(store_id, target, flags, done.local, done.uid, done.new_uid, done.validity)
        client = self._client()
        if client is None:
            return _nothing
        try:
            destination = self.server_folder(client, target, create=True)
            if destination is None:
                return _nothing  # a folder that cannot exist on the server: the sync deals with it
            self._known(client, target, destination)
            place = self._place(client, store_id)  # last: the folder of the message must be the one that is open
            if place is None:
                return _nothing
            local, uid, _ = place
            result = client.uid_move(str(uid), destination)  # without MOVE: copied first; the original removed only where it can be alone
        except ProtocolError as error:
            self._lost(error)
            return _nothing

        return self._moved(store_id, target, flags, local, uid, result.uid, result.uid_validity)

    def _moved(self, store_id: str, target: tuple[str, ...], flags: frozenset[MessageFlag], local: tuple[str, ...], uid: int, new: int | None, validity: int | None) -> Commit:
        def commit() -> None:
            self.state.unmap(local, uid)
            if new is not None and validity is not None and self.state.validity.get(target) == validity:
                self.state.map(target, new, store_id, flag_names(flags))  # else the next sync finds it by its Message-ID

        return commit

    def copy(self, store_id: str, target: tuple[str, ...], flags: frozenset[MessageFlag]) -> CommitNew:
        done = self._taken(store_id, "copy")
        if done is not None:
            return self._copied(target, flags, done.new_uid, done.validity)
        client = self._client()
        if client is None:
            return _nothing_new
        try:
            destination = self.server_folder(client, target, create=True)
            if destination is None:
                return _nothing_new
            self._known(client, target, destination)
            place = self._place(client, store_id)  # last: the folder of the message must be the one that is open
            if place is None:
                return _nothing_new
            _, uid, _ = place
            result = client.uid_copy(str(uid), destination)
        except ProtocolError as error:
            self._lost(error)
            return _nothing_new

        return self._copied(target, flags, result.uid, result.uid_validity)

    def _copied(self, target: tuple[str, ...], flags: frozenset[MessageFlag], new: int | None, validity: int | None) -> CommitNew:
        def commit(new_store_id: str) -> None:
            if new is not None and validity is not None and self.state.validity.get(target) == validity:
                self.state.map(target, new, new_store_id, flag_names(flags))
            else:
                self.state.mark_uploaded(new_store_id)  # the server has it already: matched by its Message-ID, never sent twice

        return commit

    def delete(self, store_id: str) -> Commit:
        """Delete a message for good on the server: marked deleted and removed by its UID only (never the whole folder)."""
        done = self._taken(store_id, "delete")
        if done is not None:
            local, uid = done.local, done.uid
            if done.gone or done.expunged:
                return lambda: self.state.unmap(local, uid)  # gone there already, or removed now: what the person wanted
            return lambda: self._note_deleted(store_id)  # only marked there: the sync removes it when it can
        client = self._client()
        if client is None:
            return lambda: self._note_deleted(store_id)
        try:
            place = self._place(client, store_id)
            if place is None:
                return lambda: self._note_deleted(store_id)
            local, uid, _ = place
            client.uid_store(str(uid), ["\\Deleted"], "add")
            if "UIDPLUS" not in client.capabilities:
                return lambda: self._note_deleted(store_id)  # only marked there: the sync removes it when it can, never held back
            client.uid_expunge(str(uid))
        except GoneFromServer as gone:
            where, number = gone.local, gone.uid
            return lambda: self.state.unmap(where, number)  # deleted there already: what the person wanted
        except ProtocolError as error:
            self._lost(error)
            return lambda: self._note_deleted(store_id)
        return lambda: self.state.unmap(local, uid)

    def _note_deleted(self, store_id: str) -> None:
        if self.state.find(store_id) is not None:
            self.state.note_deleted(store_id)

    def append(self, local: tuple[str, ...], raw: bytes, flags: frozenset[MessageFlag]) -> CommitNew:
        """Put a new message (a draft, the Sent copy) into a server folder."""
        client = self._client()
        if client is None:
            return _nothing_new
        try:
            server = self.server_folder(client, local, create=True)
            if server is None:
                return _nothing_new
            self._known(client, local, server)
            result = client.append(server, raw, [flag.imap_name for flag in flags])
        except ProtocolError as error:
            self._lost(error)
            return _nothing_new

        def commit(new_store_id: str) -> None:
            if result.uid is not None and result.uid_validity is not None and self.state.validity.get(local) == result.uid_validity:
                self.state.map(local, result.uid, new_store_id, flag_names(flags))
            else:
                self.state.mark_uploaded(new_store_id)

        return commit

    # --- folders ----------------------------------------------------------------------------------------------------

    def synchronised_folders(self) -> dict[str, tuple[tuple[str, ...], FolderRole]]:
        """Server folder -> (local folder, role) of every folder that is synchronised now. Only while the server can be reached."""
        client = self._client(notice=False)
        if client is None:
            raise self._unreachable(LEAVE_OUT_UNREACHABLE)
        try:
            listed = client.list_folders()
            subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
        except ImapRefused:
            raise
        except ProtocolError:
            raise self._unreachable(LEAVE_OUT_UNREACHABLE) from None
        plans, _ = plan_folders(listed, subscribed, planned_anyway(self.state), dict(self.state.roles))
        return {plan.server_name: (plan.local_path, plan.role) for plan in plans if plan.server_name not in self.state.hidden}

    def make_folder(self, local: tuple[str, ...]) -> None:
        """Make a folder on the server (and subscribe to it). When the server cannot be reached the next sync makes it."""
        client = self._client()
        if client is None:
            return
        try:
            self.server_folder(client, local, create=True)
        except ProtocolError as error:
            self._lost(error)

    def rename_folder(self, old: tuple[str, ...], new: tuple[str, ...]) -> Commit:
        """Rename a folder on the server; the server renames the folders below it too. When the server cannot be reached it is renamed here and
        the next check renames it there (D-193 1.3, R-31)."""
        client = self._client(notice=False)
        if client is None:
            return self._later(lambda: self.state.rename_later(old, new))
        try:
            folders = self._folders(client, fresh=True)
            old_server = folders.get(old)
            if old_server is None:
                return _nothing  # not on the server (made here while offline): the next sync makes it under its new name
            new_server = self._server_name(new)
            if new_server is None:
                raise ImapRefused("The new name cannot be used on the server")
            below = {path: name for path, name in folders.items() if path[: len(old)] == old and name.startswith(old_server)}
            renamed = {name: new_server + name[len(old_server) :] for name in below.values()}
            for name in below.values():
                self._leave(client, name)
            client.rename_folder(old_server, new_server)
        except ImapRefused:
            raise
        except ProtocolError:
            return self._later(lambda: self.state.rename_later(old, new))
        self.link.folders = None
        for before, after in renamed.items():  # a subscription is a name: most servers leave it on the old one
            try:
                client.subscribe(after)
            except ProtocolError:
                pass  # the folder is renamed; a missing subscription only hides it until the person shows it again
            try:
                client.unsubscribe(before)
            except ProtocolError:
                pass  # a leftover subscription of a name that no longer exists does no harm

        def commit() -> None:
            self.state.rename_folder(old, new, renamed)

        return commit

    def delete_folder(self, local: tuple[str, ...]) -> Commit:
        """Delete one folder on the server, with its mail. When the server cannot be reached it is deleted here and the next check deletes it
        there, unless the server changed it meanwhile (D-193 1.3, R-31)."""
        client = self._client(notice=False)
        if client is None:
            return self._later(lambda: self.state.delete_later(local))
        try:
            server = self._folders(client, fresh=True).get(local)
            if server is None:
                return _nothing  # not on the server: only here
            if self._has_unseen_mail(client, local, server):
                raise UsageError(
                    f"The folder '{'/'.join(local)}' has new mail on the server that is not here yet. Nothing was deleted: check for mail first, "
                    "then look at the folder again."
                )
            self._leave(client, server)
            client.delete_folder(server)
        except ImapRefused:
            raise
        except ProtocolError:
            return self._later(lambda: self.state.delete_later(local))
        self.link.folders = None
        return lambda: self.state.forget_folder(local, server)

    def _has_unseen_mail(self, client: ImapClient, local: tuple[str, ...], server: str) -> bool:
        """Does the server folder hold mail this computer never had? A server deletes a folder with all its mail, so mail that arrived
        after the last check would be gone unseen (RV-108; the same rule as a delete made offline, D-200)."""
        info = client.select(server, read_only=True)
        known = set(self.state.entries.get(local, {}))
        on_server = set(client.uid_search("ALL")) if info.exists else set[int]()
        self._leave(client, server)  # opened read-only to look: never left open for the person's next action there
        if info.uid_validity != self.state.validity.get(local, 0):
            return bool(on_server)  # numbered differently than the sync knows: any message there may be unseen
        return bool(on_server - known)

    def empty_folder(self, local: tuple[str, ...]) -> Commit | None:
        """Delete every message of a server folder for good, also those not downloaded yet. None when that cannot be done
        for the folder as a whole (offline, no UIDPLUS, numbering unknown): then each message is deleted on its own."""
        client = self._client()
        if client is None:
            return None
        try:
            server = self.server_folder(client, local, create=False)
            if server is None or "UIDPLUS" not in client.capabilities or not self._select(client, local, server):
                return None
            uids = client.uid_search("ALL")
            for batch in _uid_batches(uids):
                client.uid_store(batch, ["\\Deleted"], "add")
                client.uid_expunge(batch)  # these UIDs only: a message that arrived meanwhile stays
        except ProtocolError as error:
            self._lost(error)
            return None

        def commit() -> None:
            for uid in self.state.folder_entries(local):
                self.state.unmap(local, uid)

        return commit

    def mark_folder_read(self, local: tuple[str, ...]) -> Commit | None:
        """Mark every message of a server folder as read, also those not downloaded yet. None when that cannot be done for
        the folder as a whole (offline, numbering unknown): then each message is marked on its own."""
        client = self._client()
        if client is None:
            return None
        try:
            server = self.server_folder(client, local, create=False)
            if server is None or not self._select(client, local, server):
                return None
            for batch in _uid_batches(client.uid_search("UNSEEN")):
                client.uid_store(batch, ["\\Seen"], "add")
        except ProtocolError as error:
            self._lost(error)
            return None

        def commit() -> None:
            for uid, entry in self.state.folder_entries(local).items():
                if "\\seen" not in entry.flags:
                    self.state.set_flags(local, uid, entry.flags | {"\\seen"})

        return commit


@contextmanager
def server_session(engine: Engine, settings: AccountSettings) -> Generator[ServerSession]:
    """Hold an IMAP account for one action: its connection and its sync state; no sync of the account runs meanwhile."""
    account_id = settings.account.account_id
    link = engine.imap_link(account_id)
    with link.lock:
        session = ServerSession(engine, settings, link, ImapState(engine.directories.imap_state_file(account_id), durable=engine.durable))
        yield session
    if session.offline:  # done here only: it waits for the next mail check, and the window counts it (D-195)
        waiting_marker(engine, account_id).touch()
        engine.report(Change("waiting_changed", account_id))


def waiting_marker(engine: Engine, account_id: str) -> Path:
    """A file that says an IMAP account has changes made here while its server could not be reached; it goes when nothing waits any more (D-195).
    It spares counting what waits (a look at every message) for the accounts that have nothing waiting."""
    return engine.directories.imap_state_file(account_id).with_name("imap-waiting")
