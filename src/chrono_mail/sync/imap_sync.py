from collections.abc import Callable, Sequence
from dataclasses import replace as replace_entry
from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, Folder, FolderRole, MessageFlag, MessageRef, ProtocolError
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import parse_message
from chrono_mail.protocols.imap import FetchedItem, FolderChanges, FolderEntry, ImapClient, ImapRefused, SelectInfo, keyword_to_tag, tag_to_keyword
from chrono_mail.store import Store, StoredMessage
from chrono_mail.sync.announce import SILENT, Announcer
from chrono_mail.sync.imap_state import Checkpoint, ImapEntry, ImapState
from chrono_mail.sync.imap_folders import Planned, follow_server_folders, leaving_folders, take_away_unlisted
from chrono_mail.sync.imap_pending import send_pending
from chrono_mail.sync.mailconflicts import FOLDER_REFUSED, MARKS_REFUSED, MESSAGE_REFUSED, MOVE_REFUSED, held, held_under, record

# --- Version ------------------------------------------------------------
__version__ = "imap_sync.py 2.4.330"

# --- Version history ----------------------------------------------------
# v2.4.330: a standard folder the server marks (special-use) or has under its standard name is used whether or not it is subscribed:
#           a Junk folder not subscribed made every move to Junk fail with "Mailbox already exists" (D-202, the Windows computer's W7)
# v2.0.285: After sending a message's marks the check knows the server's marks as they are now (as read, plus what it added, minus what it
#           removed), so a mark the server changed on that message meanwhile is shown in the same check, not the next (the Windows computer's F2)
# v1.1.249: A message removed here because the server no longer has it leaves the folder's list of this check too, so it is not taken for mail
#           made here and tried as an upload (a false "could not be read for sending" line, R-40)
# v1.1.248: A check does work for what changed, not for the size of the mailbox (R-09): this computer's mail and its properties folder are read
#           once per check and kept current by the check's own changes (before: every folder of the store again for every folder); a folder
#           checked before without anything left open asks the server only what changed since (CONDSTORE, QRESYNC: nothing at all when its
#           change counter stayed), without them only the marks of the messages known and the sizes of the new ones; anything that does not
#           add up asks for everything as before; the marks of a message to send are asked first (exact spelling of a tag)
# v1.1.241: The server's list of folders to show is followed: a folder taken off it is synchronised one last time, then taken away here; a folder
#           ticked here that the list now has follows it from then on; standard folders are always planned (planned_anyway, R-34)
# v1.1.240: Folder renames and deletes made offline are done on the server first; a folder waiting for one is not planned (it is not made here
#           again under its old name); they count as waiting (R-31)
# v1.1.237: Folders deleted or renamed on the server are followed here, only on the server's confirmation (imap_folders, R-30); each folder's server
#           name is remembered; a folder inside a held one is held too
# v1.1.227: A lasting refusal of the server (marks, a move, a message or a folder made offline) is a sync conflict for the person, and what it
#           is about is not sent again while it is open, instead of a line at every check (R-24, D-195)
# v1.1.206: The tags of a folder's messages come from their properties files, only those of this folder (D-176)
# v1.1.200: Scheduled is a local-only folder; a server folder whose name is a local-only folder (Outbox, Recovered, Scheduled) is not synchronised (it would mix server mail into it, and its messages onto the server)
# v0.5.164: each change made here is told the moment it is made, in one step with its index write (announcer, D-151); the report no longer lists them
# v0.5.163: the report names each message removed here, each change of flags or tags taken over from the server, and each message kept in Recovered (the engine reports them as facts)
# v0.5.145: step: the steps of a sync (listing the folders, each folder), for the Operations window
# v0.5.138: Tags as keywords: compared, sent, taken over, downloaded and uploaded with the flags; a folder that keeps no keywords keeps its tags here
# v0.4.120: Messages the person deleted here while offline are always deleted on the server; only unexplained losses can hold a folder back
# v0.4.119: A folder blocked by a safety rule is reported (FolderReport.blocked); the person's answer for it (ImapSyncOptions.confirmed) deletes on the server, brings the messages back, removes them here or uploads them again
# v0.3.114: Messages gone from the server and from the store are forgotten and do not count for the removal safety rule
# v0.2.101: Local folders are made safely (ensure_folder: one folder when two syncs run at the same time)
# v0.0.44: the person's choices of folders and roles
# v0.0.33: Rules
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.30: IMAP sync, store to server
# v0.0.31: folders made here are made on the server, folders deleted here are hidden (D-051)
# v0.0.30: store to server (D-050)
# v0.0.29: Initial IMAP sync, server to store (D-049)

Progress = Callable[[str, int, int], None]
Step = Callable[[str], None]  # a step of the sync, named for the person ("Synchronising Inbox")

KEPT_FLAGS: dict[str, MessageFlag] = {flag.imap_name.lower(): flag for flag in MessageFlag}
SPECIAL_USE: dict[str, FolderRole] = {
    "\\sent": FolderRole.SENT, "\\drafts": FolderRole.DRAFTS, "\\trash": FolderRole.TRASH, "\\junk": FolderRole.JUNK, "\\archive": FolderRole.ARCHIVE,
}
USUAL_NAMES: dict[str, FolderRole] = {
    "sent": FolderRole.SENT, "sent items": FolderRole.SENT, "sent messages": FolderRole.SENT, "sent mail": FolderRole.SENT,
    "drafts": FolderRole.DRAFTS, "draft": FolderRole.DRAFTS,
    "trash": FolderRole.TRASH, "deleted items": FolderRole.TRASH, "deleted messages": FolderRole.TRASH, "bin": FolderRole.TRASH,
    "junk": FolderRole.JUNK, "spam": FolderRole.JUNK, "junk e-mail": FolderRole.JUNK, "junk email": FolderRole.JUNK,
    "archive": FolderRole.ARCHIVE, "archives": FolderRole.ARCHIVE,
}
STANDARD_PATH: dict[FolderRole, tuple[str, ...]] = {
    FolderRole.INBOX: ("Inbox",), FolderRole.SENT: ("Sent",), FolderRole.DRAFTS: ("Drafts",), FolderRole.TRASH: ("Trash",),
    FolderRole.JUNK: ("Junk",), FolderRole.ARCHIVE: ("Archive",),
}
RECOVERED: str = "Recovered"
SCHEDULED: str = "Scheduled"  # messages that wait for the time they are to be sent (engine.schedule.SCHEDULED_FOLDER)
LOCAL_ONLY: frozenset[str] = frozenset({"Outbox", RECOVERED, SCHEDULED})  # folders that exist only here, never on a server
REMOVAL_RATIO_LIMIT: float = 0.5
REMOVAL_RATIO_MINIMUM_COUNT: int = 10


@dataclass(frozen=True, slots=True)
class ImapSyncOptions:
    maximum_message_bytes: int = 256 * 1024 * 1024
    batch_bytes: int = 4 * 1024 * 1024
    batch_messages: int = 50
    # The person's answer for a folder that a safety rule blocked: (local folder, choice). Choices: "delete_on_server" or
    # "restore_here" when most of it is missing here, "remove_here" or "keep_here" when most of it is gone from the server.
    confirmed: tuple[tuple[tuple[str, ...], str], ...] = ()


BLOCKED_MISSING_HERE: str = "missing_here"  # most mapped messages of the folder are missing from the store
BLOCKED_GONE_FROM_SERVER: str = "gone_from_server"  # most mapped messages of the folder are gone from the server


@dataclass(frozen=True, slots=True)
class FolderPlan:
    server_name: str
    local_path: tuple[str, ...]
    role: FolderRole


@dataclass(frozen=True, slots=True)
class FolderReport:
    folder: str
    downloaded: int = 0
    adopted: int = 0
    flags_updated: int = 0
    removed: int = 0
    recovered: int = 0
    flags_sent: int = 0
    expunged: int = 0
    uploaded: int = 0
    failed: tuple[str, ...] = ()
    note: str | None = None
    new_references: tuple[MessageRef, ...] = ()  # the messages downloaded into this folder
    blocked: str | None = None  # a safety rule held the folder back (BLOCKED_...): the person is asked
    blocked_count: int = 0  # how many messages it is about


@dataclass(frozen=True, slots=True)
class ImapReport:
    folders: tuple[FolderReport, ...] = ()
    notes: tuple[str, ...] = ()
    aborted: str | None = None  # the connection failed part-way; what was done up to then is kept
    moved: int = 0

    @property
    def downloaded(self) -> int:
        return sum(item.downloaded for item in self.folders)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(reason for item in self.folders for reason in item.failed)

    @property
    def new_references(self) -> tuple[MessageRef, ...]:
        return tuple(reference for item in self.folders for reference in item.new_references)


def kept_flags(server_flags: frozenset[str]) -> frozenset[str]:
    """The server flags we keep (the five that have a counterpart in the store, and the keywords that are tags), as lower case names."""
    return frozenset(flag.lower() for flag in server_flags if flag.lower() in KEPT_FLAGS or keyword_to_tag(flag) is not None)


def system_flags(names: frozenset[str]) -> frozenset[str]:
    return frozenset(name for name in names if name in KEPT_FLAGS)


def server_tags(server_flags: frozenset[str]) -> tuple[str, ...]:
    """The tags a message has on the server (its keywords that are tags), in a fixed order."""
    found = (keyword_to_tag(flag) for flag in sorted(server_flags, key=str.casefold))
    return tuple(dict.fromkeys(tag for tag in found if tag is not None))


def local_names(flags: frozenset[MessageFlag], tags: tuple[str, ...], keywords: bool) -> frozenset[str]:
    """What a message here is in the server's terms: its flags, and its tags as keywords when the folder keeps keywords."""
    names = {flag.imap_name.lower() for flag in flags}
    if keywords:
        names |= {tag_to_keyword(tag).lower() for tag in tags}
    return frozenset(names)


def _same_tags(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    return {tag.casefold() for tag in first} == {tag.casefold() for tag in second}


def message_flags(names: frozenset[str]) -> frozenset[MessageFlag]:
    return frozenset(KEPT_FLAGS[name] for name in names if name in KEPT_FLAGS)


def planned_anyway(state: ImapState) -> frozenset[str]:
    """Server folders synchronised whether or not they are on the server's list of folders to show: the ones the person ticked here, and the
    standard folders synchronised before (they always stay, R-34)."""
    standard = set(STANDARD_PATH.values())
    return frozenset(state.forced) | frozenset(name for path, name in state.server_names.items() if path in standard)


def plan_folders(
    entries: Sequence[FolderEntry], subscribed: frozenset[str] = frozenset(), forced: frozenset[str] = frozenset(), roles: dict[str, str] | None = None
) -> tuple[list[FolderPlan], list[str]]:
    """Which server folder goes to which local folder. The standard roles go to the standard folders (the server's own
    names do not matter); every other folder keeps its path, with INBOX written as Inbox. Returns the plans and notes."""
    plans: list[FolderPlan] = []
    notes: list[str] = []
    used: set[tuple[str, ...]] = set()
    choices = [entry for entry in entries if "\\noselect" not in entry.attributes and "\\nonexistent" not in entry.attributes]
    if subscribed:  # the server's own claim of a standard folder (special-use) counts whether or not it is on the list to show (D-202, W7)
        choices = [entry for entry in choices if entry.name in subscribed or entry.name in forced or _role_of(entry, 0) is not None]
    claimed: set[FolderRole] = set()
    for entry in choices:  # the person's own choices first
        wanted = (roles or {}).get(entry.name, "")
        chosen = next((role for role in STANDARD_PATH if role.value == wanted and role is not FolderRole.INBOX), None)
        if chosen is not None and chosen not in claimed and entry.name.upper() != "INBOX":
            claimed.add(chosen)
            used.add(STANDARD_PATH[chosen])
            plans.append(FolderPlan(entry.name, STANDARD_PATH[chosen], chosen))
    # special-use attributes first, then the usual names, so that an explicit claim always wins
    for pass_number in (0, 1, 2):
        for entry in choices:
            if any(plan.server_name == entry.name for plan in plans):
                continue
            role = _role_of(entry, pass_number)
            if role is None or role in claimed or role not in STANDARD_PATH:
                continue
            claimed.add(role)
            used.add(STANDARD_PATH[role])
            plans.append(FolderPlan(entry.name, STANDARD_PATH[role], role))
    for entry in choices:
        if any(plan.server_name == entry.name for plan in plans):
            continue
        path = _split(entry)
        if path is None:
            notes.append(f"The folder '{_printable(entry.name)}' has a name that cannot be used and is not synchronised")
        elif path in used:
            notes.append(f"The folder '{_printable(entry.name)}' would share its place with another folder and is not synchronised")
        elif path[0] in LOCAL_ONLY:  # its mail would mix with mail that is never meant for a server (waiting, unsent, recovered)
            notes.append(f"The folder '{_printable(entry.name)}' has the name of a folder Chrono Mail keeps only on this computer and is not synchronised")
        else:
            used.add(path)
            plans.append(FolderPlan(entry.name, path, FolderRole.OTHER))
    return plans, notes


MARK_WORDS: dict[str, str] = {"\\seen": "read", "\\flagged": "flagged", "\\answered": "replied", "\\draft": "draft", "\\deleted": "deleted"}


def marks_in_words(names: frozenset[str]) -> str:
    """A message's marks as the person reads them ("read, flagged, tag Invoice"; "unread" when not read)."""
    lowered = {name.lower() for name in names}
    words = [MARK_WORDS[name] for name in MARK_WORDS if name in lowered] or []
    if "\\seen" not in lowered:
        words.insert(0, "unread")
    tags = sorted(tag for name in names if (tag := keyword_to_tag(name)) is not None)
    return ", ".join([*words, *(f"tag {tag}" for tag in tags)])


def imap_waiting(store: Store, state: ImapState) -> tuple[list[str], int]:
    """What of an IMAP mailbox was changed here and waits for the server (D-195): the messages whose marks differ from the agreed ones, that were
    moved to another folder, or were made here; and how many were deleted here. Messages with an open sync conflict are not counted. Looks at every
    message of the synchronised folders: asked only for an account that had changes made offline."""
    held_messages, _ = held(store)
    locations = store.locate_all()
    waiting: list[str] = []
    deleted = 0
    for folder in sorted(state.validity):
        if folder[0] in LOCAL_ONLY:
            continue
        stored = {message.ref.store_id: message for message in store.list_messages(folder)}
        mapped = state.folder_entries(folder)
        mapped_ids = {entry.store_id for entry in mapped.values()}
        for entry in mapped.values():
            if entry.store_id in held_messages:
                continue
            where = locations.get(entry.store_id)
            if where is None:
                deleted += 1
            elif where[0] != folder:
                waiting.append(entry.store_id)
            elif (message := stored.get(entry.store_id)) is not None and system_flags(local_names(message.flags, (), False)) != system_flags(entry.flags):
                waiting.append(entry.store_id)
        waiting.extend(store_id for store_id in stored if store_id not in mapped_ids and store_id not in state.uploaded and store_id not in held_messages)
    held_folders = held(store)[1]
    deleted += sum(1 for change in state.pending.values() if not held_under(held_folders, change.path))  # folders renamed or deleted offline (R-31)
    return list(dict.fromkeys(waiting)), deleted


@dataclass
class _Here:
    """This computer's copy of the account's mail, read once per check (R-09): the messages of every folder, where every message is, and which
    messages have a properties file. The check's own changes keep the places current; another program's change waits for the next check, and
    a place is only taken away when the check itself removed that message (so nothing on the server is ever deleted because of this picture)."""

    folders: dict[tuple[str, ...], dict[str, StoredMessage]]
    locations: dict[str, tuple[tuple[str, ...], str]]
    with_properties: set[str] | None  # None: the properties folder could not be listed (the tags then wait, the marks go on)

    @classmethod
    def read(cls, store: Store) -> "_Here":
        folders = {folder.path: {message.ref.store_id: message for message in store.list_messages(folder.path)} for folder in store.list_folders()}
        locations = {store_id: (path, message.relative_path) for path, messages in folders.items() for store_id, message in messages.items()}
        try:
            with_properties: set[str] | None = store.properties.stored_ids()
        except ChronoMailError:
            with_properties = None
        return cls(folders, locations, with_properties)


def _subject(index: Index | None, store_id: str) -> str:
    found = index.get(store_id) if index is not None else None
    return (found.subject or "(no subject)") if found is not None else "(a message)"


def _printable(text: str) -> str:
    return "".join(character if character.isprintable() else "?" for character in text[:80])


def _role_of(entry: FolderEntry, pass_number: int) -> FolderRole | None:
    if pass_number == 0:
        return FolderRole.INBOX if entry.name.upper() == "INBOX" else next((SPECIAL_USE[attribute] for attribute in entry.attributes if attribute in SPECIAL_USE), None)
    if pass_number == 1:
        return USUAL_NAMES.get(entry.name.lower()) if entry.delimiter is None or entry.delimiter not in entry.name else None
    return None


def _split(entry: FolderEntry) -> tuple[str, ...] | None:
    parts = entry.name.split(entry.delimiter) if entry.delimiter else [entry.name]
    if parts and parts[0].upper() == "INBOX":
        parts[0] = "Inbox"
    if any(ord(character) < 32 or ord(character) == 127 for part in parts for character in part):
        return None  # control characters (the index separates folder names with one) have no place in a folder name
    try:
        Folder("check", tuple(parts), FolderRole.OTHER)
    except (ChronoMailError, ValueError):
        return None
    return tuple(parts)


@dataclass
class _Counts:
    downloaded: int = 0
    adopted: int = 0
    flags_updated: int = 0
    removed: int = 0
    recovered: int = 0
    flags_sent: int = 0
    expunged: int = 0
    uploaded: int = 0
    failed: list[str] = field(default_factory=list)
    note: str | None = None
    new: list[MessageRef] = field(default_factory=list)
    blocked: str | None = None
    blocked_count: int = 0
    announcer: Announcer = SILENT  # told about each change made here, at the moment it is made

    def report(self, folder: str) -> FolderReport:
        return FolderReport(
            folder, self.downloaded, self.adopted, self.flags_updated, self.removed, self.recovered, self.flags_sent, self.expunged, self.uploaded,
            tuple(self.failed), self.note, tuple(self.new), self.blocked, self.blocked_count,
        )


@dataclass
class _Folder:
    """What is known about one folder during a run."""

    plan: FolderPlan
    stored: dict[str, StoredMessage]  # the messages that are in the local folder now
    on_server: dict[int, FetchedItem]
    locations: dict[str, tuple[tuple[str, ...], str]]  # where every stored message is, in all folders
    tags: dict[str, tuple[str, ...]] = field(default_factory=dict)  # store id -> its tags here
    keywords: bool = True  # the folder keeps keywords on the server (PERMANENTFLAGS with \\*): tags go there too
    held: set[str] = field(default_factory=set)  # messages with an open sync conflict: not sent again while it is open (D-195)
    told: set[int] | None = None  # the UIDs whose marks the server told in this check (None: all); the others hold the agreed marks (R-09)
    unsettled: bool = False  # something of the server's was left for later: the next check asks for everything again


def sync_imap(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions = ImapSyncOptions(), progress: Progress | None = None,
    step: Step | None = None, announcer: Announcer = SILENT,
) -> ImapReport:
    """Make server and store agree, in both directions, folder by folder.

    Local changes are found by comparing the store with what the state remembers (nothing is queued, so nothing can be lost):
    moved messages are moved on the server, messages deleted for good are expunged there, changed flags are sent, and local
    messages the server does not have are uploaded. Server changes come in the same run: new messages (matched with local ones
    by Message-ID before anything is downloaded twice), changed flags, and removals. A message is entered in the state only
    after it is stored and verified or accepted by the server. A failure with one message is reported and the rest goes on;
    a failure of the connection ends the run and keeps everything done so far."""
    reports: list[FolderReport] = []
    notes: list[str] = []
    moved = 0
    try:
        if step is not None:
            step("Listing folders")
        client.enable_changes()  # the server tells what changed in a folder, where it can (R-09)
        listed = client.list_folders()
        if state.pending and send_pending(client, store, state, listed, notes):  # renamed or deleted here offline: on the server first (R-31)
            listed = client.list_folders()
        subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
        for name in sorted(state.forced & subscribed):
            state.follow_list(name)  # ticked here, and the server's list has it now: it follows that list from now on (R-34)
        plans, planning_notes = plan_folders(listed, subscribed, planned_anyway(state), dict(state.roles))
        notes.extend(planning_notes)
        waiting = {change.server_name for change in state.pending.values()}  # still waiting (a conflict): never made here again under that name
        plans = [plan for plan in plans if plan.server_name not in state.hidden and plan.server_name not in waiting]
        selectable = frozenset(entry.name for entry in listed if "\\noselect" not in entry.attributes and "\\nonexistent" not in entry.attributes)
        leaving = leaving_folders(store, state, selectable, subscribed, {plan.local_path for plan in plans}, waiting, LOCAL_ONLY)
        plans.extend(FolderPlan(plan.server_name, plan.local_path, plan.role) for plan in leaving)  # one last time (R-34)
        held_messages, held_folders = held(store)
        followed = follow_server_folders(  # before anything is made here: a renamed folder is renamed, not made anew (R-30)
            client, store, index, state, [Planned(plan.server_name, plan.local_path, plan.role) for plan in plans], held_folders, LOCAL_ONLY,
            _marks_changed, announcer,
        )
        notes.extend(followed.notes)
        existing = {folder.path for folder in store.list_folders()}
        plans = _forget_deleted_folders(store, state, plans, existing, notes)
        for plan in plans:
            if plan.local_path not in existing:
                try:
                    folder, made = store.ensure_folder(plan.local_path, plan.role)
                    existing.add(plan.local_path)
                    if made:
                        announcer.folder_made(folder)
                except ChronoMailError as error:
                    notes.append(f"The local folder for '{_printable(plan.server_name)}' could not be made: {error}")
        plans = [plan for plan in plans if plan.local_path in existing]
        held_messages, held_folders = held(store)
        for plan in plans:
            state.set_server_name(plan.local_path, plan.server_name)
        plans.extend(_create_on_server(client, store, state, listed, plans, notes, held_folders))
        here = _Here.read(store)  # once per check (R-09)
        moved = _move_pass(client, store, index, state, plans, notes, held_messages, here.locations)
        for plan in plans:
            counts = _Counts(announcer=announcer)
            if step is not None:
                step(f"Synchronising {'/'.join(plan.local_path)}")
            try:
                _sync_folder(client, store, index, state, options, plan, counts, progress, held_messages, here)
            except ImapRefused as error:
                counts.note = f"the server would not open the folder: {error}"
            reports.append(counts.report("/".join(plan.local_path)))
        if leaving:
            leaving_paths = {plan.local_path for plan in leaving}
            take_away_unlisted(
                store, index, state, leaving, {plan.local_path for plan in plans if plan.local_path not in leaving_paths}, held(store)[1], _marks_changed, notes,
            )
        state.compact_if_needed()
    except ProtocolError as error:
        return ImapReport(tuple(reports), tuple(notes), str(error), moved)
    return ImapReport(tuple(reports), tuple(notes), None, moved)


def _forget_deleted_folders(store: Store, state: ImapState, plans: list[FolderPlan], existing: set[tuple[str, ...]], notes: list[str]) -> list[FolderPlan]:
    """A folder that was synchronised before and is gone from the store was deleted here: it is hidden from now on (its
    mail on the server stays). When the store looks damaged that is not concluded: the folder is left out of this run."""
    kept: list[FolderPlan] = []
    for plan in plans:
        if plan.local_path not in existing and state.validity.get(plan.local_path) is not None:
            if store.damaged_folders:
                notes.append(f"The folder '{_printable(plan.server_name)}' is missing here and the store looks damaged: it was left out of this run")
            else:
                state.hide_folder(plan.server_name, plan.local_path)
                notes.append(f"The folder '{_printable(plan.server_name)}' was deleted here and is no longer synchronised (it is still on the server)")
            continue
        kept.append(plan)
    return kept


def _create_on_server(
    client: ImapClient, store: Store, state: ImapState, listed: Sequence[FolderEntry], plans: list[FolderPlan], notes: list[str], held_folders: set[tuple[str, ...]],
) -> list[FolderPlan]:
    """Local folders the server does not have (made here) are made there, so that their mail can follow."""
    delimiter = next((entry.delimiter for entry in listed if entry.delimiter), "/")
    planned = {plan.local_path for plan in plans}
    taken = {entry.name for entry in listed}
    created: list[FolderPlan] = []
    for folder in store.list_folders():
        path = folder.path
        if path in planned or path[0] in LOCAL_ONLY or path == ("Inbox",) or state.validity.get(path) is not None or held_under(held_folders, path):
            continue
        name = delimiter.join(("INBOX" if path[0] == "Inbox" else path[0], *path[1:]))
        if name in taken and STANDARD_PATH.get(folder.role) == path and name not in state.hidden:
            created.append(FolderPlan(name, path, folder.role))  # a standard folder the server has but does not show: used, never made twice (W7)
            state.set_server_name(path, name)
            continue
        if name in taken or name in state.hidden or delimiter in "".join(path):
            continue  # the server has it already (not shown) or the name cannot be written on the server
        try:
            client.create_folder(name)
            client.subscribe(name)
        except ImapRefused as error:  # a lasting "no": a sync conflict for the person, not a line at every check (D-195)
            if record(store, FOLDER_REFUSED, path, "", "/".join(path), str(error)):
                notes.append(f"The folder '{_printable(name)}' could not be made on the server: {error} (see Settings > Sync conflicts)")
            continue
        created.append(FolderPlan(name, path, folder.role))
        state.set_server_name(path, name)
    return created


def _marks_changed(message: StoredMessage, entry: ImapEntry) -> bool:
    """Whether a message's marks here differ from the ones both sides last agreed on (changed here, not yet sent)."""
    return system_flags(local_names(message.flags, (), False)) != system_flags(entry.flags)


def _move_pass(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, plans: list[FolderPlan], notes: list[str], held_messages: set[str],
    locations: dict[str, tuple[tuple[str, ...], str]],
) -> int:
    """Messages that were moved to another folder here are moved on the server."""
    server_of = {plan.local_path: plan.server_name for plan in plans}
    moved = 0
    for plan in plans:
        pending: list[tuple[int, ImapEntry, tuple[str, ...]]] = []
        for uid, entry in state.folder_entries(plan.local_path).items():
            where = locations.get(entry.store_id)
            if where is not None and where[0] != plan.local_path and where[0] in server_of and entry.store_id not in held_messages:
                pending.append((uid, entry, where[0]))
        if not pending:
            continue
        try:
            client.select(plan.server_name)
            still_there = {item.uid for item in client.uid_fetch(",".join(str(uid) for uid, _, _ in pending), "(UID)")}
        except ImapRefused as error:
            notes.append(f"Messages moved here could not be moved on the server: {error}")
            continue
        for uid, entry, target in pending:
            if uid in still_there:
                try:
                    result = client.uid_move(str(uid), server_of[target])
                except ImapRefused as error:  # a lasting "no": a sync conflict; until it is settled the message is not moved again (D-195)
                    record(store, MOVE_REFUSED, target, entry.store_id, _subject(index, entry.store_id), str(error), server_folder=plan.local_path)
                    held_messages.add(entry.store_id)
                    continue
                moved += 1
                state.unmap(plan.local_path, uid)
                if result.uid is not None and result.uid_validity == state.validity.get(target):
                    state.map(target, result.uid, entry.store_id, entry.flags)  # otherwise the next run matches it by Message-ID
            else:
                state.unmap(plan.local_path, uid)  # it was gone from there already
    return moved


def _sync_folder(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, plan: FolderPlan, counts: _Counts, progress: Progress | None,
    held_messages: set[str] | None = None, here: _Here | None = None,
) -> None:
    local = plan.local_path
    here = here if here is not None else _Here.read(store)
    info = client.select(plan.server_name)
    previous = state.validity.get(local)
    orphans: set[str] = set()
    if previous is not None and previous != info.uid_validity:
        orphans = {entry.store_id for entry in state.folder_entries(local).values()}
        counts.note = "the server renumbered the folder; the messages are matched again by their Message-ID"
    if previous != info.uid_validity:
        state.set_validity(local, info.uid_validity)  # forgets the checkpoint too: everything is asked
    on_server, told = _read_server(client, state, local, info)
    stored = dict(here.folders.get(local, {}))
    tags: dict[str, tuple[str, ...]] = {}  # the properties could not be listed: the flags still go on; the tags wait
    if here.with_properties is not None:
        try:
            tags = store.properties.tags(stored, here.with_properties)  # only the messages of this folder; a damaged file leaves out that one message
        except ChronoMailError:
            pass
    view = _Folder(
        plan, stored, on_server, here.locations, tags=tags, keywords=info.keeps_keywords, held=held_messages if held_messages is not None else set(), told=told,
    )
    if not view.keywords and any(store_id in tags for store_id in view.stored) and counts.note is None:
        counts.note = "the server keeps no tags for this folder; its tags stay on this computer"
    choice = dict(options.confirmed).get(local)
    _remove_gone(store, index, state, view, counts, choice)
    _push_expunges(client, store, state, view, counts, choice)
    _push_flags(client, store, index, state, view, counts)
    _update_flags(store, index, state, view, counts)
    pool = _unmapped(store, index, state, view)
    new = sorted(uid for uid in on_server if uid not in state.folder_entries(local))
    _download(client, store, index, state, options, view, new, pool, counts, progress)
    leftovers = [store_id for store_id in pool if store_id in orphans]
    if leftovers:
        _recover(store, index, local, leftovers, counts, view.locations)
    _upload(client, store, index, state, options, view, counts)
    if not counts.failed and counts.blocked is None and not view.unsettled:  # nothing of the server's left for later (R-09)
        state.set_checkpoint(local, Checkpoint(info.uid_next, info.highest_modseq if "CONDSTORE" in client.enabled else None))


GROUP: int = 500  # UIDs per command when the marks or sizes of single messages are asked


def _read_server(client: ImapClient, state: ImapState, local: tuple[str, ...], info: SelectInfo) -> tuple[dict[int, FetchedItem], set[int] | None]:
    """What the selected server folder has: every UID with its marks, and the size of every message not known here yet. Returns those and the
    UIDs the server told about in this check (None: all of them).

    A folder whose last check left nothing open has a checkpoint (R-09). With a change counter (CONDSTORE) only what changed since is asked,
    nothing at all when the counter stayed; the other messages keep the marks both sides agreed on. With QRESYNC the server also names the
    messages removed; without it the count shows whether any went. Without a change counter the marks of every message are asked, the size
    only of the new ones. Whatever does not add up (the count differs, the counter went back) asks for everything, as without a checkpoint."""
    if info.exists == 0:
        return {}, None
    checkpoint = state.checkpoints.get(local)
    mapped = state.folder_entries(local)
    on_server: dict[int, FetchedItem] | None = None
    told: set[int] | None = None
    if checkpoint is None or checkpoint.uid_next > info.uid_next:
        pass
    elif checkpoint.modseq is not None and info.highest_modseq is not None and "CONDSTORE" in client.enabled:
        if info.highest_modseq >= checkpoint.modseq:
            changes = FolderChanges((), frozenset()) if info.highest_modseq == checkpoint.modseq else client.uid_changes(checkpoint.modseq)
            gone = changes.vanished or frozenset()
            on_server = {uid: FetchedItem(uid, entry.flags) for uid, entry in mapped.items() if uid not in gone}
            on_server.update((item.uid, item) for item in changes.items)
            told = {item.uid for item in changes.items}
            if len(on_server) != info.exists:  # removals not told, or the state out of step: everything is asked
                on_server, told = None, None
    elif checkpoint.modseq is None and info.highest_modseq is None:
        known = client.uid_fetch(f"1:{checkpoint.uid_next - 1}", "(UID FLAGS)") if checkpoint.uid_next > 1 else ()
        on_server = {item.uid: item for item in known if item.uid < checkpoint.uid_next}
        on_server.update((item.uid, item) for item in client.uid_fetch(f"{checkpoint.uid_next}:*", "(UID FLAGS RFC822.SIZE)") if item.uid >= checkpoint.uid_next)
    if on_server is None:
        return {item.uid: item for item in client.uid_fetch("1:*", "(UID FLAGS RFC822.SIZE)")}, None
    unsized = sorted(uid for uid, item in on_server.items() if uid not in mapped and item.size is None)
    for start in range(0, len(unsized), GROUP):  # the size decides whether a new message is downloaded at all
        for item in client.uid_fetch(",".join(str(uid) for uid in unsized[start : start + GROUP]), "(UID FLAGS RFC822.SIZE)"):
            if item.uid in on_server:
                on_server[item.uid] = item
    return on_server, told


def _remove_gone(store: Store, index: Index | None, state: ImapState, view: _Folder, counts: _Counts, choice: str | None = None) -> None:
    local = view.plan.local_path
    mapped = state.folder_entries(local)
    gone = [uid for uid in mapped if uid not in view.on_server]
    if not gone:
        return
    if not store.damaged_folders and store.list_folders():  # gone on both sides: nothing is left to remove anywhere, only the memory of it
        for uid in [uid for uid in gone if mapped[uid].store_id not in view.locations]:
            state.unmap(local, uid)
        mapped = state.folder_entries(local)
        gone = [uid for uid in mapped if uid not in view.on_server]
        if not gone:
            return
    if choice == "keep_here":  # the person keeps them: forgotten as server copies, so they are uploaded again as local messages
        for uid in gone:
            state.unmap(local, uid)
        return
    if len(mapped) >= REMOVAL_RATIO_MINIMUM_COUNT and len(gone) / len(mapped) > REMOVAL_RATIO_LIMIT and choice != "remove_here":
        counts.note = "most messages are gone from the server; nothing was removed here"
        counts.blocked, counts.blocked_count = BLOCKED_GONE_FROM_SERVER, len(gone)
        return
    for uid in gone:
        entry = mapped[uid]
        where = view.locations.get(entry.store_id)
        stored = view.stored.get(entry.store_id)
        try:
            if where is not None:
                gone = MessageRef(store.account_id, where[0], entry.store_id)
                with counts.announcer.changing():
                    if index is not None:
                        index.remove(entry.store_id)
                    store.delete(gone, hint=where[1])
                    view.locations.pop(entry.store_id, None)
                    view.stored.pop(entry.store_id, None)  # gone here too: never taken for mail made here that the server has not got (R-40)
                    counts.announcer.removed(gone, stored is not None and MessageFlag.SEEN not in stored.flags)
        except ChronoMailError as error:
            counts.failed.append(f"UID {uid}: it could not be removed here: {error}")
            continue
        state.unmap(local, uid)  # last: a crash before this line is repaired by the next run
        counts.removed += 1


def _push_expunges(client: ImapClient, store: Store, state: ImapState, view: _Folder, counts: _Counts, choice: str | None = None) -> None:
    """Messages that were deleted for good here are removed on the server. This is the one step that destroys mail on
    the server, so it refuses to run when the store looks wrong or when most of a folder seems to have disappeared without
    the person deleting it. Messages the person deleted (noted while the server could not be reached) always go."""
    local = view.plan.local_path
    mapped = state.folder_entries(local)
    missing = [uid for uid, entry in mapped.items() if entry.store_id not in view.locations and uid in view.on_server]
    if not missing:
        return
    if store.damaged_folders or not store.list_folders():
        counts.note = "the store looks damaged or empty; nothing was deleted on the server"
        return
    unexplained = [uid for uid in missing if mapped[uid].store_id not in state.deleted_here]  # not deleted by the person
    if choice == "restore_here":  # the person wants them back: forgotten as mapped, so they are downloaded again in this run
        for uid in unexplained:
            state.unmap(local, uid)
        missing = [uid for uid in missing if uid not in unexplained]
    elif len(mapped) >= REMOVAL_RATIO_MINIMUM_COUNT and len(unexplained) / len(mapped) > REMOVAL_RATIO_LIMIT and choice != "delete_on_server":
        counts.note = "most messages are missing from the store; nothing was deleted on the server"
        counts.blocked, counts.blocked_count = BLOCKED_MISSING_HERE, len(unexplained)
        missing = [uid for uid in missing if uid not in unexplained]  # the person's own deletes still go
    if not missing:
        return
    uids = ",".join(str(uid) for uid in missing)
    client.uid_store(uids, ["\\Deleted"], "add")
    if "UIDPLUS" in client.capabilities:
        client.uid_expunge(uids)
    else:
        counts.note = "the server cannot remove single messages: they are marked as deleted there"
    for uid in missing:
        state.unmap(local, uid)
        view.on_server.pop(uid, None)
        counts.expunged += 1


def _push_flags(client: ImapClient, store: Store, index: Index | None, state: ImapState, view: _Folder, counts: _Counts) -> None:
    local = view.plan.local_path
    sending: list[tuple[int, ImapEntry, StoredMessage, tuple[str, ...], frozenset[str], frozenset[str]]] = []
    for uid, entry in state.folder_entries(local).items():
        message = view.stored.get(entry.store_id)
        if message is None or uid not in view.on_server or entry.store_id in view.held:
            continue
        tags = view.tags.get(entry.store_id, ())
        mine = local_names(message.flags, tags, view.keywords)
        known = entry.flags if view.keywords else system_flags(entry.flags)
        if mine != known:
            sending.append((uid, entry, message, tags, mine, known))
    if view.told is not None:  # the server did not tell their marks in this check: asked now, for the exact spelling of a tag (R-09)
        asked = sorted(uid for uid, *_ in sending if uid not in view.told)
        for start in range(0, len(asked), GROUP):
            group = asked[start : start + GROUP]
            answered = {item.uid: item for item in client.uid_fetch(",".join(str(uid) for uid in group), "(UID FLAGS)")}
            for uid in group:
                if uid in answered:
                    view.on_server[uid] = answered[uid]
                    view.told.add(uid)
                else:  # gone from the server since the check began: the next check removes it here
                    view.on_server.pop(uid, None)
                    view.unsettled = True
    for uid, entry, message, tags, mine, known in sending:
        if uid not in view.on_server:
            continue
        spelling = {flag.lower(): flag for flag in view.on_server[uid].flags}  # a keyword is removed as the server writes it
        added = [flag.imap_name for flag in message.flags if flag.imap_name.lower() not in known]
        added += [tag_to_keyword(tag) for tag in tags if view.keywords and tag_to_keyword(tag).lower() not in known]
        removed = [spelling.get(name, KEPT_FLAGS[name].imap_name if name in KEPT_FLAGS else name) for name in sorted(known - mine)]
        try:
            if added:
                client.uid_store(str(uid), added, "add")
            if removed:
                client.uid_store(str(uid), removed, "remove")
        except ImapRefused as error:  # a lasting "no": a sync conflict; until it is settled the marks are not sent again (D-195)
            server_now = kept_flags(view.on_server[uid].flags) if view.keywords else system_flags(kept_flags(view.on_server[uid].flags))
            record(store, MARKS_REFUSED, local, entry.store_id, _subject(index, entry.store_id), str(error), local_marks=marks_in_words(mine),
                   server_marks=marks_in_words(server_now), server_flags=tuple(sorted(server_now)))
            view.held.add(entry.store_id)
            continue
        state.set_flags(local, uid, mine)
        taken = {name.lower() for name in removed}
        kept = [flag for flag in view.on_server[uid].flags if flag.lower() not in taken]
        view.on_server[uid] = replace_entry(view.on_server[uid], flags=frozenset(kept + [flag for flag in added if flag.lower() not in spelling]))  # the server's marks now
        counts.flags_sent += 1


def _update_flags(store: Store, index: Index | None, state: ImapState, view: _Folder, counts: _Counts) -> None:
    local = view.plan.local_path
    for uid, entry in state.folder_entries(local).items():
        item = view.on_server.get(uid)
        message = view.stored.get(entry.store_id)
        if item is None or message is None:
            continue
        server_now = kept_flags(item.flags) if view.keywords else system_flags(kept_flags(item.flags))
        if server_now == entry.flags:
            continue
        current_tags = view.tags.get(entry.store_id, ())
        if local_names(message.flags, current_tags, view.keywords) != (entry.flags if view.keywords else system_flags(entry.flags)):
            view.unsettled = True  # the server's change waits until this one is settled: the next check asks for it again (R-09)
            continue  # changed here too: the local change is kept and was or will be sent (it wins until then)
        wanted = message_flags(server_now)
        try:
            if wanted != message.flags:
                with counts.announcer.changing():
                    path = store.set_flags(message.ref, wanted, hint=message.relative_path)
                    view.locations[entry.store_id] = (local, path)
                    if index is not None:
                        index.update_flags(entry.store_id, wanted, path)
                    counts.announcer.flags(message.ref, wanted, MessageFlag.SEEN not in message.flags)
            new_tags = server_tags(item.flags)
            if view.keywords and not _same_tags(new_tags, current_tags):
                with counts.announcer.changing():
                    kept = store.properties.set_tags(entry.store_id, new_tags)
                    view.tags[entry.store_id] = kept
                    if index is not None:
                        index.update_tags(entry.store_id, kept)
                    counts.announcer.tags(message.ref, kept)
        except ChronoMailError as error:
            counts.failed.append(f"UID {uid}: the flags could not be changed here: {error}")
            continue
        state.set_flags(local, uid, server_now)
        counts.flags_updated += 1


def _unmapped(store: Store, index: Index | None, state: ImapState, view: _Folder) -> dict[str, str | None]:
    """The local messages of the folder that the server does not know yet (by store id: their Message-ID, if any)."""
    mapped_ids = {entry.store_id for entry in state.folder_entries(view.plan.local_path).values()}
    pool: dict[str, str | None] = {}
    for store_id, message in view.stored.items():
        if store_id in mapped_ids:
            continue
        row = index.get(store_id) if index is not None else None
        if row is not None:
            pool[store_id] = row.message_id
        else:
            try:
                pool[store_id] = parse_message(store.read(message.ref, hint=message.relative_path)).message_id
            except ChronoMailError:
                pool[store_id] = None
    return pool


def _batches(uids: list[int], on_server: dict[int, FetchedItem], options: ImapSyncOptions) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for uid in uids:
        wanted = on_server[uid].size or 0
        if current and (size + wanted > options.batch_bytes or len(current) >= options.batch_messages):
            batches.append(current)
            current = []
            size = 0
        current.append(uid)
        size += wanted
    if current:
        batches.append(current)
    return batches


def _download(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, view: _Folder, new: list[int],
    pool: dict[str, str | None], counts: _Counts, progress: Progress | None,
) -> None:
    local = view.plan.local_path
    eligible: list[int] = []
    for uid in new:
        size = view.on_server[uid].size
        if size is not None and size > options.maximum_message_bytes:
            counts.failed.append(f"UID {uid}: the message is larger than the limit")
        else:
            eligible.append(uid)
    done = 0
    for batch in _batches(eligible, view.on_server, options):
        fetched = {item.uid: item for item in client.uid_fetch(",".join(str(uid) for uid in batch), "(UID FLAGS BODY.PEEK[])")}
        for uid in batch:
            done += 1
            if progress is not None:
                progress("/".join(local), done, len(eligible))
            item = fetched.get(uid)
            if item is None or item.data is None:
                counts.failed.append(f"UID {uid}: the server did not send the message")
                continue
            _store_one(store, index, state, view, uid, item, pool, counts)


def _store_one(store: Store, index: Index | None, state: ImapState, view: _Folder, uid: int, item: FetchedItem, pool: dict[str, str | None], counts: _Counts) -> None:
    local = view.plan.local_path
    raw = item.data
    assert raw is not None
    server_now = kept_flags(item.flags)
    if pool:
        message_id = parse_message(raw).message_id
        match = next((store_id for store_id, known in pool.items() if message_id and known == message_id), None)
        if match is not None:
            del pool[match]
            # the local copy is the same message: it gets its place on the server again; where its flags differ from the server's
            # the local ones are sent in the next run (they may be newer: for example a message moved here after being read)
            state.map(local, uid, match, server_now)
            counts.adopted += 1
            return
    try:
        reference = store.deliver(local, raw, flags=message_flags(server_now), verify=True)
    except ChronoMailError as error:
        counts.failed.append(f"UID {uid}: it could not be stored safely: {error}")
        return
    tags = server_tags(item.flags) if view.keywords else ()
    if tags:
        try:
            tags = store.properties.set_tags(reference.store_id, tags)
        except ChronoMailError:
            tags = ()  # the message is safe; its tags come again with the next comparison
    if not view.keywords:
        server_now = system_flags(server_now)
    state.map(local, uid, reference.store_id, server_now if tags or not view.keywords else system_flags(server_now))
    counts.downloaded += 1
    counts.new.append(reference)
    place = store.locate(reference)
    view.locations[reference.store_id] = (local, place)
    with counts.announcer.changing():
        if index is not None:
            try:
                index.add(replace_entry(entry_from_raw(reference, place, message_flags(server_now), len(raw), raw), tags=tags))
            except ChronoMailError:
                counts.failed.append(f"UID {uid}: it is stored but not yet in the index (the next repair of the index adds it)")
        counts.announcer.added(reference)


def _recover(
    store: Store, index: Index | None, local: tuple[str, ...], store_ids: list[str], counts: _Counts, locations: dict[str, tuple[tuple[str, ...], str]],
) -> None:
    """Local messages that the renumbered server folder no longer has: kept, in a folder of their own."""
    target = (RECOVERED, *local)
    try:
        store.ensure_folder(target, FolderRole.OTHER)
    except ChronoMailError as error:
        counts.failed.append(f"The folder for recovered messages could not be made: {error}")
        return
    for store_id in store_ids:
        where = locations.get(store_id)
        if where is None:
            continue
        try:
            before = MessageRef(store.account_id, where[0], store_id)
            with counts.announcer.changing():
                moved = store.move(before, target, hint=where[1])
                place = store.locate(moved)
                locations[store_id] = (target, place)
                if index is not None:
                    index.move(store_id, target, place)
                counts.announcer.moved(before, moved)
            counts.recovered += 1
        except ChronoMailError as error:
            counts.failed.append(f"A message could not be kept in {RECOVERED}: {error}")


def _upload(client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, view: _Folder, counts: _Counts) -> None:
    """Local messages the server has not got (written or moved in here, matched by nothing on the server) are sent to it."""
    local = view.plan.local_path
    mapped_ids = {entry.store_id for entry in state.folder_entries(local).values()}
    for store_id, message in view.stored.items():
        if store_id in mapped_ids or store_id in state.uploaded or store_id in view.held:
            continue
        if message.size > options.maximum_message_bytes:
            counts.failed.append("A local message is larger than the limit and was not sent to the server")
            continue
        try:
            raw = store.read(message.ref, hint=message.relative_path)
            tags = view.tags.get(store_id, ()) if view.keywords else ()
            result = client.append(view.plan.server_name, raw, [flag.imap_name for flag in message.flags] + [tag_to_keyword(tag) for tag in tags])
        except ImapRefused as error:  # a lasting "no": a sync conflict (kept here only when the person says so); not sent again meanwhile (D-195)
            record(store, MESSAGE_REFUSED, local, store_id, _subject(index, store_id), str(error))
            view.held.add(store_id)
            continue
        except ChronoMailError as error:
            if isinstance(error, ProtocolError):
                raise
            counts.failed.append(f"A local message could not be read for sending: {error}")
            continue
        state.mark_uploaded(store_id)
        counts.uploaded += 1
        if result.uid is not None and result.uid_validity == state.validity.get(local):
            state.map(local, result.uid, store_id, local_names(message.flags, tags, view.keywords))
