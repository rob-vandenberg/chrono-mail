from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, Folder, FolderRole, MessageFlag, MessageRef, ProtocolError
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import parse_message
from chrono_mail.protocols.imap import FetchedItem, FolderEntry, ImapClient, ImapRefused
from chrono_mail.store import Store, StoredMessage
from chrono_mail.sync.imap_state import ImapEntry, ImapState

# --- Version ------------------------------------------------------------
__version__ = "imap_sync.py 0.3.114"

# --- Version history ----------------------------------------------------
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
LOCAL_ONLY: frozenset[str] = frozenset({"Outbox", RECOVERED})  # folders that exist only here
REMOVAL_RATIO_LIMIT: float = 0.5
REMOVAL_RATIO_MINIMUM_COUNT: int = 10


@dataclass(frozen=True, slots=True)
class ImapSyncOptions:
    maximum_message_bytes: int = 256 * 1024 * 1024
    batch_bytes: int = 4 * 1024 * 1024
    batch_messages: int = 50


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
    """The server flags we keep (the five that have a counterpart in the store), as lower case names."""
    return frozenset(flag.lower() for flag in server_flags if flag.lower() in KEPT_FLAGS)


def message_flags(names: frozenset[str]) -> frozenset[MessageFlag]:
    return frozenset(KEPT_FLAGS[name] for name in names if name in KEPT_FLAGS)


def plan_folders(
    entries: Sequence[FolderEntry], subscribed: frozenset[str] = frozenset(), forced: frozenset[str] = frozenset(), roles: dict[str, str] | None = None
) -> tuple[list[FolderPlan], list[str]]:
    """Which server folder goes to which local folder. The standard roles go to the standard folders (the server's own
    names do not matter); every other folder keeps its path, with INBOX written as Inbox. Returns the plans and notes."""
    plans: list[FolderPlan] = []
    notes: list[str] = []
    used: set[tuple[str, ...]] = set()
    choices = [entry for entry in entries if "\\noselect" not in entry.attributes and "\\nonexistent" not in entry.attributes]
    if subscribed:
        choices = [entry for entry in choices if entry.name in subscribed or entry.name in forced or entry.name.upper() == "INBOX"]
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
        else:
            used.add(path)
            plans.append(FolderPlan(entry.name, path, FolderRole.OTHER))
    return plans, notes


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

    def report(self, folder: str) -> FolderReport:
        return FolderReport(
            folder, self.downloaded, self.adopted, self.flags_updated, self.removed, self.recovered, self.flags_sent, self.expunged, self.uploaded,
            tuple(self.failed), self.note, tuple(self.new),
        )


@dataclass
class _Folder:
    """What is known about one folder during a run."""

    plan: FolderPlan
    stored: dict[str, StoredMessage]  # the messages that are in the local folder now
    on_server: dict[int, FetchedItem]
    locations: dict[str, tuple[tuple[str, ...], str]]  # where every stored message is, in all folders
    pushed: set[int] = field(default_factory=set)  # UIDs whose flags we just sent: the summary we hold is out of date for them


def sync_imap(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions = ImapSyncOptions(), progress: Progress | None = None
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
        listed = client.list_folders()
        subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
        plans, planning_notes = plan_folders(listed, subscribed, frozenset(state.forced), dict(state.roles))
        notes.extend(planning_notes)
        plans = [plan for plan in plans if plan.server_name not in state.hidden]
        existing = {folder.path for folder in store.list_folders()}
        plans = _forget_deleted_folders(store, state, plans, existing, notes)
        for plan in plans:
            if plan.local_path not in existing:
                try:
                    store.ensure_folder(plan.local_path, plan.role)
                    existing.add(plan.local_path)
                except ChronoMailError as error:
                    notes.append(f"The local folder for '{_printable(plan.server_name)}' could not be made: {error}")
        plans = [plan for plan in plans if plan.local_path in existing]
        plans.extend(_create_on_server(client, store, state, listed, plans, notes))
        moved = _move_pass(client, store, index, state, plans, notes)
        for plan in plans:
            counts = _Counts()
            try:
                _sync_folder(client, store, index, state, options, plan, counts, progress)
            except ImapRefused as error:
                counts.note = f"the server would not open the folder: {error}"
            reports.append(counts.report("/".join(plan.local_path)))
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


def _create_on_server(client: ImapClient, store: Store, state: ImapState, listed: Sequence[FolderEntry], plans: list[FolderPlan], notes: list[str]) -> list[FolderPlan]:
    """Local folders the server does not have (made here) are made there, so that their mail can follow."""
    delimiter = next((entry.delimiter for entry in listed if entry.delimiter), "/")
    planned = {plan.local_path for plan in plans}
    taken = {entry.name for entry in listed}
    created: list[FolderPlan] = []
    for folder in store.list_folders():
        path = folder.path
        if path in planned or path[0] in LOCAL_ONLY or path == ("Inbox",) or state.validity.get(path) is not None:
            continue
        name = delimiter.join(("INBOX" if path[0] == "Inbox" else path[0], *path[1:]))
        if name in taken or name in state.hidden or delimiter in "".join(path):
            continue  # the server has it already (not shown) or the name cannot be written on the server
        try:
            client.create_folder(name)
            client.subscribe(name)
        except ImapRefused as error:
            notes.append(f"The folder '{_printable(name)}' could not be made on the server: {error}")
            continue
        created.append(FolderPlan(name, path, folder.role))
    return created


def _move_pass(client: ImapClient, store: Store, index: Index | None, state: ImapState, plans: list[FolderPlan], notes: list[str]) -> int:
    """Messages that were moved to another folder here are moved on the server."""
    server_of = {plan.local_path: plan.server_name for plan in plans}
    locations = store.locate_all()
    moved = 0
    for plan in plans:
        pending: list[tuple[int, ImapEntry, tuple[str, ...]]] = []
        for uid, entry in state.folder_entries(plan.local_path).items():
            where = locations.get(entry.store_id)
            if where is not None and where[0] != plan.local_path and where[0] in server_of:
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
                except ImapRefused as error:
                    notes.append(f"A message could not be moved on the server to '{_printable(server_of[target])}': {error}")
                    continue
                moved += 1
                state.unmap(plan.local_path, uid)
                if result.uid is not None and result.uid_validity == state.validity.get(target):
                    state.map(target, result.uid, entry.store_id, entry.flags)  # otherwise the next run matches it by Message-ID
            else:
                state.unmap(plan.local_path, uid)  # it was gone from there already
    return moved


def _sync_folder(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, plan: FolderPlan, counts: _Counts, progress: Progress | None
) -> None:
    local = plan.local_path
    info = client.select(plan.server_name)
    previous = state.validity.get(local)
    orphans: set[str] = set()
    if previous is not None and previous != info.uid_validity:
        orphans = {entry.store_id for entry in state.folder_entries(local).values()}
        counts.note = "the server renumbered the folder; the messages are matched again by their Message-ID"
    if previous != info.uid_validity:
        state.set_validity(local, info.uid_validity)
    on_server: dict[int, FetchedItem] = {}
    if info.exists > 0:
        on_server = {item.uid: item for item in client.uid_fetch("1:*", "(UID FLAGS RFC822.SIZE)")}
    view = _Folder(plan, {message.ref.store_id: message for message in store.list_messages(local)}, on_server, store.locate_all())
    _remove_gone(store, index, state, view, counts)
    _push_expunges(client, store, state, view, counts)
    _push_flags(client, state, view, counts)
    _update_flags(store, index, state, view, counts)
    pool = _unmapped(store, index, state, view)
    new = sorted(uid for uid in on_server if uid not in state.folder_entries(local))
    _download(client, store, index, state, options, view, new, pool, counts, progress)
    leftovers = [store_id for store_id in pool if store_id in orphans]
    if leftovers:
        _recover(store, index, local, leftovers, counts)
    _upload(client, store, state, options, view, counts)


def _remove_gone(store: Store, index: Index | None, state: ImapState, view: _Folder, counts: _Counts) -> None:
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
    if len(mapped) >= REMOVAL_RATIO_MINIMUM_COUNT and len(gone) / len(mapped) > REMOVAL_RATIO_LIMIT:
        counts.note = "most messages are gone from the server; nothing was removed here"
        return
    for uid in gone:
        entry = mapped[uid]
        where = view.locations.get(entry.store_id)
        try:
            if where is not None:
                if index is not None:
                    index.remove(entry.store_id)
                store.delete(MessageRef(store.account_id, where[0], entry.store_id), hint=where[1])
        except ChronoMailError as error:
            counts.failed.append(f"UID {uid}: it could not be removed here: {error}")
            continue
        state.unmap(local, uid)  # last: a crash before this line is repaired by the next run
        counts.removed += 1


def _push_expunges(client: ImapClient, store: Store, state: ImapState, view: _Folder, counts: _Counts) -> None:
    """Messages that were deleted for good here are removed on the server. This is the one step that destroys mail on
    the server, so it refuses to run when the store looks wrong or when most of a folder seems to have disappeared."""
    local = view.plan.local_path
    mapped = state.folder_entries(local)
    missing = [uid for uid, entry in mapped.items() if entry.store_id not in view.locations and uid in view.on_server]
    if not missing:
        return
    if store.damaged_folders or not store.list_folders():
        counts.note = "the store looks damaged or empty; nothing was deleted on the server"
        return
    if len(mapped) >= REMOVAL_RATIO_MINIMUM_COUNT and len(missing) / len(mapped) > REMOVAL_RATIO_LIMIT:
        counts.note = "most messages are missing from the store; nothing was deleted on the server"
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


def _push_flags(client: ImapClient, state: ImapState, view: _Folder, counts: _Counts) -> None:
    local = view.plan.local_path
    for uid, entry in state.folder_entries(local).items():
        message = view.stored.get(entry.store_id)
        if message is None or uid not in view.on_server:
            continue
        known = message_flags(entry.flags)
        if message.flags == known:
            continue
        added = [flag.imap_name for flag in message.flags - known]
        removed = [flag.imap_name for flag in known - message.flags]
        try:
            if added:
                client.uid_store(str(uid), added, "add")
            if removed:
                client.uid_store(str(uid), removed, "remove")
        except ImapRefused as error:
            counts.failed.append(f"UID {uid}: the flags could not be changed on the server: {error}")
            continue
        state.set_flags(local, uid, frozenset(flag.imap_name.lower() for flag in message.flags))
        view.pushed.add(uid)
        counts.flags_sent += 1


def _update_flags(store: Store, index: Index | None, state: ImapState, view: _Folder, counts: _Counts) -> None:
    local = view.plan.local_path
    for uid, entry in state.folder_entries(local).items():
        item = view.on_server.get(uid)
        message = view.stored.get(entry.store_id)
        if item is None or message is None or uid in view.pushed:
            continue
        server_now = kept_flags(item.flags)
        if server_now == entry.flags:
            continue
        if message.flags != message_flags(entry.flags):
            continue  # changed here too: the local change is kept and was or will be sent (it wins until then)
        wanted = message_flags(server_now)
        try:
            path = store.set_flags(message.ref, wanted, hint=message.relative_path)
            if index is not None:
                index.update_flags(entry.store_id, wanted, path)
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
    state.map(local, uid, reference.store_id, server_now)
    counts.downloaded += 1
    counts.new.append(reference)
    if index is not None:
        try:
            index.add(entry_from_raw(reference, store.locate(reference), message_flags(server_now), len(raw), raw))
        except ChronoMailError:
            counts.failed.append(f"UID {uid}: it is stored but not yet in the index (the next repair of the index adds it)")


def _recover(store: Store, index: Index | None, local: tuple[str, ...], store_ids: list[str], counts: _Counts) -> None:
    """Local messages that the renumbered server folder no longer has: kept, in a folder of their own."""
    target = (RECOVERED, *local)
    try:
        store.ensure_folder(target, FolderRole.OTHER)
    except ChronoMailError as error:
        counts.failed.append(f"The folder for recovered messages could not be made: {error}")
        return
    locations = store.locate_all()
    for store_id in store_ids:
        where = locations.get(store_id)
        if where is None:
            continue
        try:
            moved = store.move(MessageRef(store.account_id, where[0], store_id), target, hint=where[1])
            if index is not None:
                index.move(store_id, target, store.locate(moved))
            counts.recovered += 1
        except ChronoMailError as error:
            counts.failed.append(f"A message could not be kept in {RECOVERED}: {error}")


def _upload(client: ImapClient, store: Store, state: ImapState, options: ImapSyncOptions, view: _Folder, counts: _Counts) -> None:
    """Local messages the server has not got (written or moved in here, matched by nothing on the server) are sent to it."""
    local = view.plan.local_path
    mapped_ids = {entry.store_id for entry in state.folder_entries(local).values()}
    for store_id, message in view.stored.items():
        if store_id in mapped_ids or store_id in state.uploaded:
            continue
        if message.size > options.maximum_message_bytes:
            counts.failed.append("A local message is larger than the limit and was not sent to the server")
            continue
        try:
            raw = store.read(message.ref, hint=message.relative_path)
            result = client.append(view.plan.server_name, raw, [flag.imap_name for flag in message.flags])
        except ImapRefused as error:
            counts.failed.append(f"A local message could not be sent to the server: {error}")
            continue
        except ChronoMailError as error:
            if isinstance(error, ProtocolError):
                raise
            counts.failed.append(f"A local message could not be read for sending: {error}")
            continue
        state.mark_uploaded(store_id)
        counts.uploaded += 1
        if result.uid is not None and result.uid_validity == state.validity.get(local):
            state.map(local, result.uid, store_id, frozenset(flag.imap_name.lower() for flag in message.flags))
