from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

from chrono_mail.config import AccountSettings, ServerRole
from chrono_mail.core import AccountKind, AuthenticationError, ConfigurationError, MessageFlag, ProtocolError
from chrono_mail.engine.session import OUTBOX, Engine, ImapLink
from chrono_mail.protocols.imap import ImapClient, ImapRefused
from chrono_mail.sync import ImapState, plan_folders

# --- Version ------------------------------------------------------------
__version__ = "imap_online.py 0.4.120"

# --- Version history ----------------------------------------------------
# v0.4.120: Initial: the actions of an IMAP account are done on the server first, and here only when the server confirmed (D-117)

# IMAP is the server's mailbox: an action (marks, move, copy, delete, a draft, the Sent copy) is done on the server first,
# and only when the server confirmed it is the same done here, and the sync state updated at once, so that the next sync
# has nothing left to do. A refusal of the server is an error and nothing changes here. When the server cannot be reached
# the action is done here only and the next sync sends it (the comparison of D-050); a delete for good is noted so that the
# safety rules of the sync never hold it back. Every step names messages by UID: nothing else in a folder is touched.

LOCAL_ONLY: frozenset[str] = frozenset({OUTBOX[0], "Recovered"})  # first parts of the local folders that never exist on a server
OFFLINE_NOTICE: str = "The server of {account} could not be reached: the change is made there at the next mail check"


def is_imap(settings: AccountSettings) -> bool:
    return settings.account.kind is AccountKind.IMAP


def flag_names(flags: frozenset[MessageFlag]) -> frozenset[str]:
    """The flags as the sync state keeps them (lower-case IMAP names)."""
    return frozenset(flag.imap_name.lower() for flag in flags)


Commit = Callable[[], None]  # records what the server did in the sync state; called once the same was done here
CommitNew = Callable[[str], None]  # the same for a step that makes a new message here (its store id)


def _nothing() -> None:
    """Nothing to record: the message is not on the server, or the server could not be reached."""


def _nothing_new(store_id: str) -> None:
    """Nothing to record for a new message (the next sync puts it on the server)."""


@dataclass
class ServerSession:
    """One action's hold on an IMAP account: its open connection and its sync state, read fresh under the link's lock."""

    engine: Engine
    settings: AccountSettings
    link: ImapLink
    state: ImapState
    offline: bool = False

    # --- the connection --------------------------------------------------------------------------------------------

    def _client(self) -> ImapClient | None:
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
            self._go_offline()
            return None
        link.client = client
        return client

    def _go_offline(self) -> None:
        self.offline = True
        self.link.close()
        self.engine.notice(OFFLINE_NOTICE.format(account=self.settings.account.email_address))

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
            plans, _ = plan_folders(listed, subscribed, frozenset(self.state.forced), dict(self.state.roles))
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
        delimiter = self.link.delimiter
        if delimiter in "".join(local):
            return None
        name = delimiter.join(("INBOX" if local[0] == "Inbox" else local[0], *local[1:]))
        client.create_folder(name)
        client.subscribe(name)
        folders[local] = name
        return name

    def _select(self, client: ImapClient, local: tuple[str, ...], server: str) -> bool:
        """Open a server folder; False when the server numbers its messages differently than the sync state knows."""
        if client.selected != server or server not in self.link.validity:
            self.link.validity[server] = client.select(server).uid_validity
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
        return local, uid, server

    # --- the steps --------------------------------------------------------------------------------------------------

    def set_flags(self, store_id: str, flags: frozenset[MessageFlag]) -> Commit:
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
        return lambda: self.state.set_flags(local, uid, wanted)

    def move(self, store_id: str, target: tuple[str, ...], flags: frozenset[MessageFlag]) -> Commit:
        """Move a message to another folder on the server. Without MOVE: copy, then remove the original by its UID only."""
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
            if "MOVE" in client.capabilities:
                result = client.uid_move(str(uid), destination)
            else:
                result = client.uid_copy(str(uid), destination)  # the copy is there (the server said OK) before the original goes
                client.uid_store(str(uid), ["\\Deleted"], "add")
                if "UIDPLUS" in client.capabilities:
                    client.uid_expunge(str(uid))
        except ProtocolError as error:
            self._lost(error)
            return _nothing

        def commit() -> None:
            self.state.unmap(local, uid)
            if result.uid is not None and result.uid_validity is not None and self.state.validity.get(target) == result.uid_validity:
                self.state.map(target, result.uid, store_id, flag_names(flags))  # else the next sync finds it by its Message-ID

        return commit

    def copy(self, store_id: str, target: tuple[str, ...], flags: frozenset[MessageFlag]) -> CommitNew:
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

        def commit(new_store_id: str) -> None:
            if result.uid is not None and result.uid_validity is not None and self.state.validity.get(target) == result.uid_validity:
                self.state.map(target, result.uid, new_store_id, flag_names(flags))
            else:
                self.state.mark_uploaded(new_store_id)  # the server has it already: matched by its Message-ID, never sent twice

        return commit

    def delete(self, store_id: str) -> Commit:
        """Delete a message for good on the server: marked deleted and removed by its UID only (never the whole folder)."""
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


@contextmanager
def server_session(engine: Engine, settings: AccountSettings) -> Generator[ServerSession]:
    """Hold an IMAP account for one action: its connection and its sync state; no sync of the account runs meanwhile."""
    link = engine.imap_link(settings.account.account_id)
    with link.lock:
        yield ServerSession(engine, settings, link, ImapState(engine.directories.imap_state_file(settings.account.account_id), durable=engine.durable))
