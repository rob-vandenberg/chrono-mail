import ssl
import sqlite3
import threading
import time
import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Concatenate

from chrono_mail.config import (
    AccountSettings,
    AppConfig,
    AppDirectories,
    PasswordStore,
    fingerprints_for,
    load_config,
    save_config,
)
from chrono_mail.core import AccountKind, ChronoMailError, ConfigurationError, CryptoError, FolderRole, StorageError
from chrono_mail.core.folder import OWN_FOLDERS
from chrono_mail.crypto import DataKey, KeyStore
from chrono_mail.engine.changes import Change, RebuildStep
from chrono_mail.engine.junkwords import JunkHolder
from chrono_mail.engine.receiving import Receiver
from chrono_mail.engine.undohistory import UndoHistory
from chrono_mail.index import Index
from chrono_mail.protocols.imap import ImapClient
from chrono_mail.protocols.releases import RELEASES_ADDRESS
from chrono_mail.protocols.tls import context_for
from chrono_mail.store import Store
from chrono_mail.sync import RebuildReport, rebuild_index

# --- Version ------------------------------------------------------------
__version__ = "session.py 4.2.419"

# --- Version history ----------------------------------------------------
# v4.2.419: releases_address, stopped_downloads and verified_updates: where the releases are read, the downloads Cancel stopped, the updates checked (D-293)
# v4.2.418: the receiver of every index records each message's account in its properties, once (D-292)
# v4.2.417: every index opened or rebuilt is told which account received each message it is given and how each account is named (name_receivers, D-292)
# v4.2.403: mail kept on this computer: a folder that is only the parent of others becomes a folder of its own when the store opens (D-285)
# v3.6.354: Chrono Mail's own folders known by their role, their names written once (core OWN_FOLDERS) (CR-36)
# v3.6.353: IndexGate keeps one connection to the index open between commands, let go before a replace or removal: closing the last one wrote the whole change log back, 18 ms of every 23 ms Delete (D-266)
# v3.6.350: sent_unfiled: the messages the server took in this run whose move to Sent is left (never sent again, also on a full disk)
# v3.6.349: the restore's refusal says to try again when it is done (RV-174); a wait that is stopped is WaitStopped, told as a cancelled restore (RV-175)
# v3.0.339: server_work: a restore waits until the checks, sends and calendar syncs that run have ended and holds new ones back (RV-60)
# v2.2.314: outbox_lock: a message is put in the Outbox with its waiting time, and checked and sent, one at a time (never sent twice, B-21)
# v2.2.313: undo: the person's last actions on mail, for Undo and Redo while the program runs (B-18)
# v2.1.308: junk: the junk filter's words, loaded once per session (D-238)
# v2.0.281: IndexGate: commands hold the index they read, a new index replaces it only when none does (so the old one stays usable while
#           a new one is built); a rebuild reports how far it is (rebuilding, RebuildStep) and can be aborted (abort_rebuild) (D-229)
# v1.9.276: open_index: no send times to fill in after an upgrade any more (every older index is rebuilt, D-226)
# v1.1.252: sidebar_lock: every read-change-save of the sidebar file (a rename moves pins while the window changes the sidebar, R-41)
# v1.1.226: conflicts_changed and waiting_changed pass through a quiet big job as they are (D-194)
# v1.1.212: An account of calendars and contacts only has no place for mail: left out of storage_groups, refused by storage_settings (D-185)
# v1.1.200: the send times are filled in for the Scheduled folder too
# v1.1.196: open_index fills in the send times of the Outbox after the upgrade to index format 9; first_time(): a problem is told once per run
# v0.5.165: every fact is numbered per mailbox and the messages that came in are remembered with their number (arrived_after, D-152)
# v0.5.164: place_lock: one lock per mailbox, held by a change from its index write until its fact is told and by a question while it reads (D-151); is_quiet
# v0.5.159: a quiet big job tells "collection_changed" for calendars, task lists and address books, "mailbox_changed" for mail
# v0.5.155: changed and report: the facts of every change of mail (D-146); quietly: a big job reports one "mailbox changed" per mailbox at its end
# v0.5.154: open_index opens the index only inside its lock: on Windows a command that opened it while another replaced its files made one of them fail
# v0.5.145: activity: the steps of a mail check or a send, per account (for the Operations window)
# v0.5.144: The settings file is read and written by one command at a time; change_config applies a change to a fresh read
# v0.5.138: ImapLink.keywords: per server folder, whether the server keeps keywords (tags)
# v0.4.120: One open IMAP connection per account for the actions done on the server first (ImapLink), closed when the program ends; notice
# v0.4.118: An index is rebuilt by one command at a time (index_lock); the others wait and open the new one
# v0.2.101: A store with two folders of the same path is repaired when it is opened
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.43: tls_context for accepted certificates
# v0.0.32: stop_event for long waits
# v0.0.13: key cache, safe for concurrent commands
# v0.0.12: Initial engine session

OUTBOX: tuple[str, ...] = OWN_FOLDERS[FolderRole.OUTBOX]
STANDARD_FOLDERS: tuple[tuple[tuple[str, ...], FolderRole], ...] = (
    (("Inbox",), FolderRole.INBOX),
    (("Sent",), FolderRole.SENT),
    (("Drafts",), FolderRole.DRAFTS),
    (("Trash",), FolderRole.TRASH),
    (("Junk",), FolderRole.JUNK),
    (OUTBOX, FolderRole.OUTBOX),
)
Progress = Callable[[str, int, int | None], None]
Activity = Callable[[str, str, str, int | None, int | None], None]  # account id, "receive" or "send", step, done, total


def _no_activity(account_id: str, direction: str, step: str, done: int | None, total: int | None) -> None:
    """Step reports of mail checks and sends are optional."""


def _no_progress(what: str, done: int, total: int | None) -> None:
    """Progress reports are optional."""


def _no_rebuilding(step: RebuildStep) -> None:
    """Reports of a rebuild are optional (the command line tool shows none)."""


def _no_change(change: Change) -> None:
    """Reports of changes are optional (the command line tool has no views to update)."""


@dataclass
class _Quiet:
    """Per thread: how deep inside quietly() it is, the mailboxes a big job touched, and whether it ends with a summary."""

    depth: int = 0
    touched: list[tuple[str, str]] = field(default_factory=list)  # (account or mailbox, "mailbox_changed" or "collection_changed")
    summary: bool = True


def _no_notice(text: str) -> None:
    """Notices (an action that waits for the server) are optional."""


def quiet_command[P, R](handler: Callable[["Engine", P], R]) -> Callable[["Engine", P], R]:
    """A command that is a big job: its changes are told as one "mailbox changed" per mailbox at its end (Engine.quietly)."""

    def run(engine: "Engine", parameters: P) -> R:
        with engine.quietly():
            return handler(engine, parameters)

    return run


@dataclass
class ImapLink:
    """The open connection of one IMAP account for the actions that are done on the server first. Its lock is held for
    a whole action and for a whole sync of the account, so the two never change the same messages at once."""

    lock: threading.RLock = field(default_factory=threading.RLock)
    client: ImapClient | None = None
    folders: dict[tuple[str, ...], str] | None = None  # local folder -> server folder, as the sync plans them
    delimiter: str = "/"  # the server's separator in folder names
    validity: dict[str, int] = field(default_factory=dict)  # server folder -> UIDVALIDITY seen when it was selected
    keywords: dict[str, bool] = field(default_factory=dict)  # server folder -> the server keeps keywords there (PERMANENTFLAGS with \\*)

    def close(self) -> None:
        client, self.client = self.client, None
        self.folders = None
        self.validity.clear()
        if client is not None:
            client.logout()



_INDEX_LOCKS: dict[str, threading.Lock] = {}  # index file -> held while it is rebuilt
class ServerWorkPaused(ChronoMailError):
    """A check, a send or a calendar sync asked for while a backup is restored."""


class ServerWork:
    """The work that talks to the servers and changes the mail here (checks, sends, calendar syncs), against a restore that sets this
    computer's data aside: the restore waits until the work that runs has ended and holds new work back until it is done. A check that
    deleted mail on a POP3 server while its only copy was set aside lost that mail (RV-60)."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._running = 0
        self._paused = False
        self._depth = threading.local()  # work inside work (a check that sends) counts once

    @contextmanager
    def running(self) -> Generator[None]:
        depth: int = getattr(self._depth, "value", 0)  # getattr: a thread-local is empty in a new thread
        if depth == 0:
            with self._condition:
                if self._paused:
                    raise ServerWorkPaused("A backup is being restored: try again when it is done")  # RV-174: nothing is kept for later
                self._running += 1
        self._depth.value = depth + 1
        try:
            yield
        finally:
            self._depth.value = depth
            if depth == 0:
                with self._condition:
                    self._running -= 1
                    self._condition.notify_all()

    @contextmanager
    def paused(self, cancelled: Callable[[], bool]) -> Generator[None]:
        """Hold new work back, wait for the work that runs to end (or the restore to be cancelled), then give the restore its turn."""
        with self._condition:
            self._paused = True
            while self._running and not cancelled():
                self._condition.wait(0.2)
            if self._running:
                self._paused = False
                raise WaitStopped("The restore was cancelled")  # the restore tells it as cancelled (RV-175)
        try:
            yield
        finally:
            with self._condition:
                self._paused = False


class WaitStopped(ServerWorkPaused):
    """The restore was cancelled, or the program is ending, while it waited for the work that runs."""


def talks_to_servers[**P, R](handler: Callable[Concatenate["Engine", P], R]) -> Callable[Concatenate["Engine", P], R]:
    """A command that talks to the servers and changes the mail here: it runs only while no restore holds such work back (RV-60)."""

    @functools.wraps(handler)
    def run(engine: "Engine", *args: P.args, **kwargs: P.kwargs) -> R:
        with engine.server_work.running():
            return handler(engine, *args, **kwargs)

    return run


_INDEX_LOCKS_GUARD: threading.Lock = threading.Lock()


def index_lock(path: Path) -> threading.Lock:
    """The lock of one index file: two rebuilds of the same file would build the same work file at the same time."""
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(str(path.resolve()), threading.Lock())

class IndexGate:
    """Who has one index file open. A command reads the index while it is open; a new index replaces the file only when no
    command has it open, and no command opens it meanwhile (Windows refuses to replace an open file; on Linux a command would
    keep writing into the old one). A thread that has it open already may open it again, so nested opens never wait for themselves."""

    def __init__(self) -> None:
        self._condition: threading.Condition = threading.Condition()
        self._readers: dict[int, int] = {}  # thread -> how many times it has the index open
        self._replacing: bool = False
        self._kept: sqlite3.Connection | None = None  # held open between commands: closing a command's own is then never the last close (D-266)

    def keep(self, path: Path) -> None:
        """Hold one connection to the index open from now on. SQLite writes its whole change log back into the file when the last
        connection closes: every command paid that (about 18 ms of a 23 ms Delete) when it closed the index."""
        with self._condition:
            if self._kept is not None or self._replacing:
                return
            try:
                kept = sqlite3.connect(path, check_same_thread=False)
                kept.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()  # takes part in the change log (WAL) from now on
            except sqlite3.Error:
                return  # only a speed-up: a command opens the index as before
            self._kept = kept

    def let_go(self) -> None:
        """Close the kept connection: the file is about to be replaced or removed (Windows refuses to while it is open)."""
        with self._condition:
            kept, self._kept = self._kept, None
        if kept is not None:
            kept.close()

    def enter(self) -> int:
        me = threading.get_ident()
        with self._condition:
            while self._replacing and me not in self._readers:
                self._condition.wait()
            self._readers[me] = self._readers.get(me, 0) + 1
        return me

    def leave(self, reader: int) -> None:
        with self._condition:
            left = self._readers.get(reader, 0) - 1
            if left > 0:
                self._readers[reader] = left
            else:
                self._readers.pop(reader, None)
            self._condition.notify_all()

    @contextmanager
    def replacing(self) -> Generator[None]:
        """Held while a new file takes the index's place: waits until no command has it open."""
        with self._condition:
            self._replacing = True
        self.let_go()
        with self._condition:
            while self._readers:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                self._replacing = False
                self._condition.notify_all()


_INDEX_GATES: dict[str, IndexGate] = {}
_REBUILD_ABORTS: dict[str, threading.Event] = {}  # index file -> set when the person aborts its rebuild or filling


def index_gate(path: Path) -> IndexGate:
    with _INDEX_LOCKS_GUARD:
        return _INDEX_GATES.setdefault(str(path.resolve()), IndexGate())


def let_go_kept_indexes() -> None:
    """Close every kept index connection: account files are about to be removed or replaced (an account removed, a restore). The next
    command that opens an index keeps one again."""
    with _INDEX_LOCKS_GUARD:
        gates = list(_INDEX_GATES.values())
    for gate in gates:
        gate.let_go()


def rebuild_abort(path: Path) -> threading.Event:
    """Set when the person aborts the rebuild (or the filling in) of this index; cleared when one starts."""
    with _INDEX_LOCKS_GUARD:
        return _REBUILD_ABORTS.setdefault(str(path.resolve()), threading.Event())


REMEMBERED_ARRIVALS: int = 100_000  # per mailbox: the messages that came in, with the number of their fact


@dataclass
class Engine:
    """One session of the application: where its files are, how secrets are kept, and the clock.

    An Engine can run commands from several threads at once (a UI does): it keeps no per-command
    state except the key cache, which is locked. Every command receives it, and everything a
    command does goes through it, so a test (or an agent) can run the whole application
    without a screen, with in-memory key and password stores.
    """

    directories: AppDirectories
    passwords: PasswordStore
    keys: KeyStore
    clock: Callable[[], float] = time.time
    durable: bool = True
    progress: Progress = field(default=_no_progress)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)  # set when the program is ending: long waits stop
    notice: Callable[[str], None] = field(default=_no_notice)  # tells the person something they should know (an action waits for the server)
    activity: Activity = field(default=_no_activity)  # the steps of a mail check or a send of one account (connecting, listing, downloading …)
    changed: Callable[[Change], None] = field(default=_no_change)  # every fact of a change of mail, as it happens (D-146)
    rebuilding: Callable[[RebuildStep], None] = field(default=_no_rebuilding)  # how far an index is rebuilt or filled in (D-229)
    _quiet: threading.local = field(default_factory=threading.local, init=False, repr=False)
    _place_locks: dict[str, threading.RLock] = field(default_factory=dict, init=False, repr=False)
    _told: dict[str, int] = field(default_factory=dict, init=False, repr=False)  # per mailbox: how many facts were told
    _arrived: dict[str, OrderedDict[str, int]] = field(default_factory=dict, init=False, repr=False)  # per mailbox: store id -> the number of the fact it came with
    _horizon: dict[str, int] = field(default_factory=dict, init=False, repr=False)  # per mailbox: below this number, what came in is not known
    _report_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)  # re-entrant: a listener in the same thread may cause a fact of its own
    _place_locks_guard: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    sidebar_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)  # every read-change-save of the sidebar file (R-41)
    _imap_links: dict[str, ImapLink] = field(default_factory=dict, init=False, repr=False)
    _imap_links_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _cached_keys: dict[bytes, DataKey] = field(default_factory=dict, init=False, repr=False)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _config_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)  # held while config.json is read or written
    _told_once: set[str] = field(default_factory=set, init=False, repr=False)  # problems already told in this run (first_time)
    _told_once_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    junk: JunkHolder = field(default_factory=JunkHolder, init=False, repr=False)  # the junk filter's words, loaded once (D-238)
    undo: UndoHistory = field(default_factory=UndoHistory, init=False, repr=False)  # what Undo and Redo can do, while the program runs (B-18)
    outbox_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)  # one message at a time is put in, taken out or sent (B-21)
    server_work: ServerWork = field(default_factory=ServerWork, init=False, repr=False)  # checks and sends against a restore (RV-60)
    sent_unfiled: set[str] = field(default_factory=set, init=False, repr=False)  # store ids the server took in this run whose move to Sent is
    # left: never sent again, also when no mark could be written on the disk (full)
    releases_address: str = field(default=RELEASES_ADDRESS, repr=False)  # where the releases of Chrono Mail are read (a test's own server)
    stopped_downloads: set[str] = field(default_factory=set, init=False, repr=False)  # the downloads of updates Cancel stopped, by the number the window gave each
    verified_updates: dict[str, str] = field(default_factory=dict, init=False, repr=False)  # a downloaded installer -> the fingerprint it was checked against

    def first_time(self, problem: str) -> bool:
        """True the first time this problem is named in this run of the program, False after that: a problem that is met
        again at every check (a scheduled message that cannot be read) is told once, not every minute."""
        with self._told_once_lock:
            if problem in self._told_once:
                return False
            self._told_once.add(problem)
            return True

    def report(self, change: Change) -> None:
        """A change of mail happened: tell it, unless a big job of this thread runs quietly (then its mailbox is noted)."""
        quiet = self._quiet_state()
        if quiet.depth == 0 or change.kind in ("conflicts_changed", "waiting_changed"):  # these say only that a list is to be asked again (D-194)
            self._tell(change)
            return
        summary = "collection_changed" if change.kind.startswith(("item_", "collection_")) else "mailbox_changed"
        if (change.mailbox, summary) not in quiet.touched:
            quiet.touched.append((change.mailbox, summary))

    def _tell(self, change: Change) -> None:
        """Number the fact among the facts of its mailbox and tell it, in that order (one at a time, so the numbers are told
        in the order they were given). A message that came into a folder is remembered with its number; after a big job
        (one "mailbox changed") what came in before it is no longer known one by one."""
        with self._report_lock:
            number = self._told.get(change.mailbox, 0) + 1
            self._told[change.mailbox] = number
            arrived = self._arrived.setdefault(change.mailbox, OrderedDict())
            came = change.new_message_id if change.kind == "moved" else change.message_id if change.kind == "added" else ""
            if came:
                arrived[came] = number
                arrived.move_to_end(came)
                while len(arrived) > REMEMBERED_ARRIVALS:
                    _, forgotten = arrived.popitem(last=False)
                    self._horizon[change.mailbox] = max(self._horizon.get(change.mailbox, 0), forgotten + 1)
            if change.kind == "mailbox_changed":
                self._horizon[change.mailbox] = number + 1
            self.changed(replace(change, sequence=number))

    def arrived_after(self, place: str, heard: int) -> set[str] | None:
        """The messages that came into this mailbox with a fact numbered after heard (they are not in a list that heard up
        to there), or None when that is not known any more (too long ago, or a big job came after it)."""
        with self._report_lock:
            if heard + 1 < self._horizon.get(place, 0):
                return None
            arrived = self._arrived.get(place)
            return set() if arrived is None else {store_id for store_id, number in arrived.items() if number > heard}

    def place_lock(self, place: str) -> threading.RLock:
        """The lock of one mailbox (a place where mail is kept): a change holds it from writing the index until its fact is
        told, and a question about the mailbox's messages holds it while it reads the index. So an answer never counts a
        change that has not been told yet, and the list can trust that it describes what it knows (D-151)."""
        with self._place_locks_guard:
            return self._place_locks.setdefault(place, threading.RLock())

    def is_quiet(self) -> bool:
        """Whether this thread runs a big job quietly (its changes are told as one "mailbox changed" at its end)."""
        return self._quiet_state().depth > 0

    def mailbox_changed(self, mailbox: str) -> None:
        """A big job changed this mailbox without going through the mailbox's operations (a sync, a rebuilt index)."""
        self.report(Change("mailbox_changed", mailbox))

    @contextmanager
    def quietly(self, summary: bool = True) -> Generator[None]:
        """A big job: its single changes are not told one by one; at its end one "mailbox changed" per mailbox it touched
        (summary False: nothing, because the caller reports one fact of its own, as Empty folder does)."""
        quiet = self._quiet_state()
        if quiet.depth == 0:  # inside a big job, a part of it (Empty folder during an import) adds to the job's summary
            quiet.touched.clear()
            quiet.summary = summary
        quiet.depth += 1
        try:
            yield
        finally:
            quiet.depth -= 1
            if quiet.depth == 0:
                touched = list(quiet.touched)
                quiet.touched.clear()
                if quiet.summary:
                    for mailbox, what in touched:
                        self._tell(Change("mailbox_changed" if what == "mailbox_changed" else "collection_changed", mailbox))

    def _quiet_state(self) -> _Quiet:
        state = getattr(self._quiet, "state", None)
        if not isinstance(state, _Quiet):
            state = _Quiet()
            self._quiet.state = state
        return state

    def imap_link(self, account_id: str) -> ImapLink:
        """The open connection of an IMAP account (made when it is first needed)."""
        with self._imap_links_lock:
            return self._imap_links.setdefault(account_id, ImapLink())

    def close_imap_links(self) -> None:
        """The program ends: log out of every open IMAP connection."""
        with self._imap_links_lock:
            links = list(self._imap_links.values())
        for link in links:
            with link.lock:
                link.close()

    def tls_context(self, settings: AccountSettings, outgoing: bool = False) -> ssl.SSLContext | None:
        """The TLS context for the incoming (or outgoing) server of an account; None means the usual checks apply to it,
        which is so unless a certificate was accepted for that very server."""
        server = settings.account.outgoing if outgoing else settings.account.incoming
        return context_for(fingerprints_for(settings.trusted_certificates, server.host, server.port))

    # --- configuration -------------------------------------------------------------------------

    def config(self) -> AppConfig:
        with self._config_lock:  # never while another command replaces the file (Windows refuses to replace a file that is open)
            return load_config(self.directories.config_file)

    def save_config(self, config: AppConfig) -> None:
        with self._config_lock:
            save_config(self.directories.config_file, config)

    def change_config(self, change: Callable[[AppConfig], AppConfig]) -> AppConfig:
        """Read the settings, apply the change and save them, one command at a time, so that changes made at the same
        moment are all kept. The change gets the settings as they are now; long work belongs before this call. Returns the new settings."""
        with self._config_lock:
            changed = change(load_config(self.directories.config_file))
            save_config(self.directories.config_file, changed)
            return changed

    def resolve_account(self, identifier: str, config: AppConfig | None = None) -> AccountSettings:
        """An account by its id, by the start of its id (at least 6 characters), or by its e-mail address."""
        current = config if config is not None else self.config()
        wanted = identifier.strip().lower()
        matches = [
            settings
            for settings in current.accounts
            if settings.account.account_id == wanted
            or (len(wanted) >= 6 and settings.account.account_id.startswith(wanted))
            or settings.account.email_address.lower() == wanted
        ]
        if len(matches) != 1:
            raise ConfigurationError("Unknown account" if not matches else "More than one account matches; use the account id")
        return matches[0]

    # --- keys ----------------------------------------------------------------------------------

    def data_key(self, config: AppConfig) -> DataKey:
        """The data key. It is asked from the credential store once and then kept for the session."""
        if config.key_id is None:
            raise CryptoError("No data key has been created yet")
        key_id = bytes.fromhex(config.key_id)
        with self._cache_lock:
            cached = self._cached_keys.get(key_id)
        if cached is not None:
            return cached
        key = self.keys.load(key_id)
        with self._cache_lock:
            self._cached_keys[key_id] = key
        return key

    def forget_keys(self) -> None:
        """Drop the cached key (it is fetched from the credential store again when it is next needed)."""
        with self._cache_lock:
            self._cached_keys.clear()

    # --- opening an account --------------------------------------------------------------------

    def store_directory(self, settings: AccountSettings) -> Path:
        return self.directories.store_directory(settings.account.account_id)

    def storage_settings(self, settings: AccountSettings, config: AppConfig | None = None) -> AccountSettings:
        """Where an account's mail is kept: the account itself, or Local Folders for a POP3 account that shares them."""
        if settings.account.kind is AccountKind.DAV:
            raise ConfigurationError("This account has calendars, contacts and tasks only: it has no mail")
        if settings.storage != "local":
            return settings
        local = (config if config is not None else self.config()).local_folders()
        if local is None:
            raise ConfigurationError("This account keeps its mail in Local Folders, but there is no Local Folders mailbox")
        return local

    def storage_groups(self, config: AppConfig | None = None) -> dict[str, list[AccountSettings]]:
        """The accounts that share a place for their mail, by the id of that place: every account on its own, and the POP3
        accounts that keep their mail in Local Folders together with Local Folders itself."""
        current = config if config is not None else self.config()
        groups: dict[str, list[AccountSettings]] = {}
        for item in current.accounts:
            if item.account.kind is AccountKind.DAV:
                continue  # calendars and contacts only: no place for mail
            try:
                place = self.storage_settings(item, current).account.account_id
            except ConfigurationError:
                place = item.account.account_id  # Local Folders is missing: the account is shown on its own (and says so when opened)
            groups.setdefault(place, []).append(item)
        return groups

    def open_store(self, settings: AccountSettings, config: AppConfig | None = None) -> Store:
        current = config if config is not None else self.config()
        key = self.data_key(current) if settings.encrypted else None
        store = Store.open(self.store_directory(settings), key, durable=self.durable)
        if store.damaged_folders:
            store.merge_duplicate_folders()  # folders made twice by commands that ran at the same time (before 0.2.101)
        if settings.account.kind is AccountKind.IMAP:
            store.make_own_parents()  # Recovered only the parent of the recovered folders: a folder of its own, with its role (CR-36)
        else:
            store.make_parents_real()  # mail kept here: every folder that is only a parent becomes a folder of its own (D-285); the server decides for IMAP
        return store

    def open_index(self, settings: AccountSettings, store: Store) -> Index:
        """The account's index, held open until it is closed (IndexGate: no new index replaces it meanwhile). A missing or
        damaged index, or one of a format that cannot be upgraded in place, is rebuilt from the store first: it is never the truth."""
        path = self.directories.index_file(settings.account.account_id)
        gate = index_gate(path)
        while True:
            reader = gate.enter()
            if path.exists():
                try:
                    index = Index(path)  # format 11 is upgraded in place here (D-229)
                except StorageError:
                    pass
                else:
                    self.name_receivers(index, settings, store)
                    index.when_closed(lambda: gate.leave(reader))
                    gate.keep(path)
                    return index
            gate.leave(reader)
            with index_lock(path):  # commands that need the same rebuild at once (accounts in Local Folders) wait for the one that runs
                if not self.index_usable(path):
                    self.rebuild(settings.account.account_id, store, path, usable=False)[0].close()  # nothing can use it until then
            # and the index is opened again, held like any other

    @staticmethod
    def index_usable(path: Path) -> bool:
        """Whether the index file can be used as it is (also after an upgrade in place); only asked while no new one can replace it."""
        if not path.exists():
            return False
        try:
            Index(path).close()
        except StorageError:
            return False
        return True

    def name_receivers(self, index: Index, place: AccountSettings, store: Store) -> None:
        """Tell an index of this place which account received each message it is given (D-292) and the name each account is shown with."""
        from chrono_mail.engine.commands_accounts import shown_name  # it builds on this module

        accounts = self.config().accounts
        index.receiver = Receiver(place, accounts, self.directories.pop3_state_file, store)  # records each message's account once (D-292)
        index.account_names = {item.account.account_id: shown_name(item, accounts) for item in accounts if item.account.kind not in (AccountKind.LOCAL, AccountKind.DAV)}

    def _receiver(self, account_id: str, store: Store) -> Receiver | None:
        try:
            place = self.config().find(account_id)
        except ConfigurationError:
            return None  # a place no longer in the settings: its messages get their account from the next filling in
        return Receiver(place, self.config().accounts, self.directories.pop3_state_file, store)

    def rebuild(self, account_id: str, store: Store, path: Path, usable: bool) -> tuple[Index, RebuildReport]:
        """Build a new index from every message of the store and put it in place of the old one once no command has that open; the
        caller holds index_lock. It reports how far it is (rebuilding, the status line) and stops when the person aborts it
        (RebuildAborted: the old index stays, D-229)."""
        aborted = rebuild_abort(path)
        aborted.clear()

        def progress(done: int, total: int) -> None:
            self.rebuilding(RebuildStep(account_id, done, total, usable))

        try:
            index, report = rebuild_index(store, path, progress, aborted.is_set, index_gate(path).replacing, self._receiver(account_id, store))
            try:
                self.name_receivers(index, self.config().find(account_id), store)
            except ConfigurationError:
                pass
            return index, report
        finally:
            self.rebuilding(RebuildStep(account_id, 0, 0, usable, ended=True))

    def abort_rebuild(self, settings: AccountSettings) -> None:
        """The person aborted rebuilding (or filling in) this account's index: it stops at its next step (D-229)."""
        rebuild_abort(self.directories.index_file(settings.account.account_id)).set()
