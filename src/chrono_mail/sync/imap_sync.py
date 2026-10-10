from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from chrono_mail.core import ChronoMailError, Folder, FolderRole, MessageFlag, MessageRef, ProtocolError
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.mime import parse_message
from chrono_mail.protocols.imap import FetchedItem, FolderEntry, ImapClient, ImapRefused
from chrono_mail.store import Store
from chrono_mail.sync.imap_state import ImapEntry, ImapState

# --- Version ------------------------------------------------------------
__version__ = "imap_sync.py 0.0.29"

# --- Version history ----------------------------------------------------
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
    failed: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ImapReport:
    folders: tuple[FolderReport, ...] = ()
    notes: tuple[str, ...] = ()
    aborted: str | None = None  # the connection failed part-way; what was done up to then is kept

    @property
    def downloaded(self) -> int:
        return sum(item.downloaded for item in self.folders)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(reason for item in self.folders for reason in item.failed)


def kept_flags(server_flags: frozenset[str]) -> frozenset[str]:
    """The server flags we keep (the five that have a counterpart in the store), as lower case names."""
    return frozenset(flag.lower() for flag in server_flags if flag.lower() in KEPT_FLAGS)


def message_flags(names: frozenset[str]) -> frozenset[MessageFlag]:
    return frozenset(KEPT_FLAGS[name] for name in names if name in KEPT_FLAGS)


def plan_folders(entries: Sequence[FolderEntry], subscribed: frozenset[str] = frozenset()) -> tuple[list[FolderPlan], list[str]]:
    """Which server folder goes to which local folder. The standard roles go to the standard folders (the server's own
    names do not matter); every other folder keeps its path, with INBOX written as Inbox. Returns the plans and notes."""
    plans: list[FolderPlan] = []
    notes: list[str] = []
    used: set[tuple[str, ...]] = set()
    choices = [entry for entry in entries if "\\noselect" not in entry.attributes and "\\nonexistent" not in entry.attributes]
    if subscribed:
        choices = [entry for entry in choices if entry.name in subscribed or entry.name.upper() == "INBOX"]
    claimed: set[FolderRole] = set()
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
    failed: list[str] = field(default_factory=list)
    note: str | None = None

    def report(self, folder: str) -> FolderReport:
        return FolderReport(folder, self.downloaded, self.adopted, self.flags_updated, self.removed, self.recovered, tuple(self.failed), self.note)


def sync_imap(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions = ImapSyncOptions(), progress: Progress | None = None
) -> ImapReport:
    """Bring the store in line with the server, folder by folder: new messages are downloaded and verified before they are
    entered in the state, flags that changed on the server are applied unless the local flags changed too, and messages
    that are gone from the server are removed locally. A failure with one message is reported and the rest goes on; a
    failure of the connection ends the run and keeps everything done so far."""
    reports: list[FolderReport] = []
    notes: list[str] = []
    try:
        listed = client.list_folders()
        subscribed = frozenset(entry.name for entry in client.list_folders(subscribed_only=True))
        plans, planning_notes = plan_folders(listed, subscribed)
        notes.extend(planning_notes)
        existing = {folder.path for folder in store.list_folders()}
        for plan in plans:
            if plan.local_path not in existing:
                try:
                    store.create_folder(plan.local_path, plan.role)
                    existing.add(plan.local_path)
                except ChronoMailError as error:
                    notes.append(f"The local folder for '{_printable(plan.server_name)}' could not be made: {error}")
        for plan in plans:
            if plan.local_path not in existing:
                continue
            counts = _Counts()
            try:
                _sync_folder(client, store, index, state, options, plan, counts, progress)
            except ImapRefused as error:
                counts.note = f"the server would not open the folder: {error}"
            reports.append(counts.report("/".join(plan.local_path)))
        state.compact_if_needed()
    except ProtocolError as error:
        return ImapReport(tuple(reports), tuple(notes), str(error))
    return ImapReport(tuple(reports), tuple(notes))


def _sync_folder(
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, plan: FolderPlan, counts: _Counts, progress: Progress | None
) -> None:
    local = plan.local_path
    info = client.select(plan.server_name, read_only=True)
    previous = state.validity.get(local)
    orphans: dict[str, str | None] = {}
    if previous is not None and previous != info.uid_validity:
        for entry in state.folder_entries(local).values():
            row = index.get(entry.store_id) if index is not None else None
            orphans[entry.store_id] = row.message_id if row is not None else None
        counts.note = "the server renumbered the folder; the messages are matched again by their Message-ID"
    if previous != info.uid_validity:
        state.set_validity(local, info.uid_validity)
    on_server: dict[int, FetchedItem] = {}
    if info.exists > 0:
        on_server = {item.uid: item for item in client.uid_fetch("1:*", "(UID FLAGS RFC822.SIZE)")}
    locations = store.locate_all()
    mapped = state.folder_entries(local)
    _remove_gone(store, index, state, local, mapped, on_server, locations, counts)
    mapped = state.folder_entries(local)
    _update_flags(store, index, state, local, mapped, on_server, locations, counts)
    new = sorted(uid for uid in on_server if uid not in mapped)
    _download(client, store, index, state, options, local, plan.server_name, new, on_server, orphans, counts, progress)
    if orphans:
        _recover(store, index, local, orphans, counts)


def _remove_gone(
    store: Store, index: Index | None, state: ImapState, local: tuple[str, ...], mapped: dict[int, ImapEntry], on_server: dict[int, FetchedItem],
    locations: dict[str, tuple[tuple[str, ...], str]], counts: _Counts,
) -> None:
    gone = [uid for uid in mapped if uid not in on_server]
    if not gone:
        return
    if len(mapped) >= REMOVAL_RATIO_MINIMUM_COUNT and len(gone) / len(mapped) > REMOVAL_RATIO_LIMIT:
        counts.note = "most messages are gone from the server; nothing was removed here"
        return
    for uid in gone:
        entry = mapped[uid]
        where = locations.get(entry.store_id)
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


def _update_flags(
    store: Store, index: Index | None, state: ImapState, local: tuple[str, ...], mapped: dict[int, ImapEntry], on_server: dict[int, FetchedItem],
    locations: dict[str, tuple[tuple[str, ...], str]], counts: _Counts,
) -> None:
    current: dict[str, frozenset[MessageFlag]] = {}  # the flags of the stored messages, filled folder by folder when needed
    loaded: set[tuple[str, ...]] = set()
    for uid, entry in mapped.items():
        item = on_server.get(uid)
        where = locations.get(entry.store_id)
        if item is None or where is None:
            continue
        server_now = kept_flags(item.flags)
        if server_now == entry.flags:
            continue
        reference = MessageRef(store.account_id, where[0], entry.store_id)
        if where[0] not in loaded:
            loaded.add(where[0])
            for message in store.list_messages(where[0]):
                current[message.ref.store_id] = message.flags
        local_flags = current.get(entry.store_id)
        if local_flags is None or local_flags != message_flags(entry.flags):
            continue  # changed here too: the local change is kept and will be sent (it wins until then)
        wanted = message_flags(server_now)
        try:
            path = store.set_flags(reference, wanted, hint=where[1])
            if index is not None:
                index.update_flags(entry.store_id, wanted, path)
        except ChronoMailError as error:
            counts.failed.append(f"UID {uid}: the flags could not be changed here: {error}")
            continue
        state.set_flags(local, uid, server_now)
        counts.flags_updated += 1


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
    client: ImapClient, store: Store, index: Index | None, state: ImapState, options: ImapSyncOptions, local: tuple[str, ...], server_name: str,
    new: list[int], on_server: dict[int, FetchedItem], orphans: dict[str, str | None], counts: _Counts, progress: Progress | None,
) -> None:
    eligible: list[int] = []
    for uid in new:
        size = on_server[uid].size
        if size is not None and size > options.maximum_message_bytes:
            counts.failed.append(f"UID {uid}: the message is larger than the limit")
        else:
            eligible.append(uid)
    done = 0
    for batch in _batches(eligible, on_server, options):
        fetched = {item.uid: item for item in client.uid_fetch(",".join(str(uid) for uid in batch), "(UID FLAGS BODY.PEEK[])")}
        for uid in batch:
            done += 1
            if progress is not None:
                progress("/".join(local), done, len(eligible))
            item = fetched.get(uid)
            if item is None or item.data is None:
                counts.failed.append(f"UID {uid}: the server did not send the message")
                continue
            _store_one(store, index, state, local, uid, item, orphans, counts)


def _store_one(store: Store, index: Index | None, state: ImapState, local: tuple[str, ...], uid: int, item: FetchedItem, orphans: dict[str, str | None], counts: _Counts) -> None:
    raw = item.data
    assert raw is not None
    server_now = kept_flags(item.flags)
    if orphans:
        message_id = parse_message(raw).message_id
        match = next((store_id for store_id, known in orphans.items() if message_id and known == message_id), None)
        if match is not None:
            del orphans[match]
            state.map(local, uid, match, server_now)  # the local copy is the same message: it gets its place on the server again
            counts.adopted += 1
            return
    try:
        reference = store.deliver(local, raw, flags=message_flags(server_now), verify=True)
    except ChronoMailError as error:
        counts.failed.append(f"UID {uid}: it could not be stored safely: {error}")
        return
    state.map(local, uid, reference.store_id, server_now)
    counts.downloaded += 1
    if index is not None:
        try:
            index.add(entry_from_raw(reference, store.locate(reference), message_flags(server_now), len(raw), raw))
        except ChronoMailError:
            counts.failed.append(f"UID {uid}: it is stored but not yet in the index (the next repair of the index adds it)")


def _recover(store: Store, index: Index | None, local: tuple[str, ...], orphans: dict[str, str | None], counts: _Counts) -> None:
    """Local messages that the renumbered server folder no longer has: kept, in a folder of their own."""
    target = (RECOVERED, *local)
    try:
        if target not in {folder.path for folder in store.list_folders()}:
            store.create_folder(target, FolderRole.OTHER)
    except ChronoMailError as error:
        counts.failed.append(f"The folder for recovered messages could not be made: {error}")
        return
    locations = store.locate_all()
    for store_id in orphans:
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
