import hashlib
import json
import re
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from chrono_mail.core import AuthenticationError, CollectionInfo, CollectionKind, ProtocolError, StorageError
from chrono_mail.pimstore import PimStore
from chrono_mail.pimstore.extract import extract
from chrono_mail.protocols.dav import DavClient, DavConflict, RemoteChanges, RemoteCollection, RemoteItem
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "davsync.py 0.0.66"

# --- Version history ----------------------------------------------------
# v0.0.66: Initial synchronisation of the calendars, task lists and address books with a CalDAV and CardDAV server

STATE_VERSION: int = 1
SAFE_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9._@-]{1,100}")
CONFLICT_PREFIX: str = "[Conflict] "


@dataclass(frozen=True, slots=True)
class ItemLink:
    """The tie between an item here and the item on the server, as they were the last time they agreed."""

    item_id: str
    etag: str
    digest: str  # of the text of the item here at that time: another digest now means it was changed here


@dataclass(slots=True)
class CollectionState:
    token: str = ""
    ctag: str = ""
    links: dict[str, ItemLink] = field(default_factory=dict[str, ItemLink])  # by address on the server


class DavState:
    """What was synchronised, kept next to the data. It can be lost without harm: the next synchronisation then compares everything again
    (items are matched by their UID, so nothing is doubled)."""

    def __init__(self, path: Path, durable: bool = True) -> None:
        self.path: Path = path
        self._durable: bool = durable
        self.collections: dict[str, CollectionState] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if data.get("version") != STATE_VERSION:
                return
            for collection, description in data["collections"].items():
                state = CollectionState(str(description.get("token", "")), str(description.get("ctag", "")))
                for href, link in description.get("links", {}).items():
                    state.links[str(href)] = ItemLink(str(link["id"]), str(link["etag"]), str(link["digest"]))
                self.collections[str(collection)] = state
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.collections = {}  # damaged or not there: start again

    def get(self, collection_id: str) -> CollectionState:
        return self.collections.setdefault(collection_id, CollectionState())

    def save(self) -> None:
        data = {
            "version": STATE_VERSION,
            "collections": {
                identifier: {
                    "token": state.token,
                    "ctag": state.ctag,
                    "links": {href: {"id": link.item_id, "etag": link.etag, "digest": link.digest} for href, link in state.links.items()},
                }
                for identifier, state in self.collections.items()
            },
        }
        replace_file_atomically(self.path, json.dumps(data, indent=1).encode("utf-8"), self._durable)


@dataclass(slots=True)
class DavReport:
    collections: int = 0
    created: int = 0  # collections made here for collections on the server
    pulled: int = 0  # items added or replaced here
    pushed: int = 0  # items added or replaced on the server
    deleted_here: int = 0
    deleted_on_server: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list[str])


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _component_present(kind: CollectionKind, text: str) -> bool:
    """Whether a text from a mixed calendar belongs in a collection of this kind."""
    if kind is CollectionKind.EVENTS:
        return "BEGIN:VEVENT" in text
    if kind is CollectionKind.TASKS:
        return "BEGIN:VTODO" in text
    return "BEGIN:VCARD" in text


def conflict_copy(kind: CollectionKind, text: str) -> str:
    """The text of an item with a new UID and a marked title, so that a version that lost a conflict is kept as an item of its own."""
    new_uid = f"{uuid.uuid4().hex}@chrono-mail"
    lines = text.replace("\r\n", "\n").split("\n")
    title = "FN" if kind is CollectionKind.CONTACTS else "SUMMARY"
    seen_uid = seen_title = False
    result: list[str] = []
    for line in lines:
        if not seen_uid and line.upper().startswith("UID:"):
            result.append(f"UID:{new_uid}")
            seen_uid = True
        elif not seen_title and re.match(rf"{title}[:;]", line, re.IGNORECASE):
            head, _, value = line.partition(":")
            result.append(f"{head}:{CONFLICT_PREFIX}{value}")
            seen_title = True
        elif line.startswith((" ", "\t")) or line:
            result.append(line)
    if not seen_uid:
        insertion = next((index for index, line in enumerate(result) if line.upper().startswith(("BEGIN:VEVENT", "BEGIN:VTODO", "BEGIN:VCARD"))), None)
        if insertion is not None:
            result.insert(insertion + 1, f"UID:{new_uid}")
    return "\r\n".join(result) + "\r\n"


def _address_for(remote: RemoteCollection, uid: str, item_id: str) -> str:
    name = uid if SAFE_NAME.fullmatch(uid) else item_id
    return remote.href.rstrip("/") + "/" + urllib.parse.quote(name, safe="@") + (".vcf" if remote.kind == "contacts" else ".ics")


def _matching(store: PimStore, remote: RemoteCollection) -> CollectionInfo | None:
    for info in store.collections():
        if info.server_value("href") == remote.href and info.server_value("component") == remote.component:
            return info
    return None


def sync_dav(client: DavClient, store: PimStore, state_path: Path, durable: bool = True, push: bool = True) -> DavReport:
    """Bring the calendars, task lists and address books of an account in line with its server, both ways. A server that cannot be reached or does not
    accept the login stops it; trouble with one collection or item is reported and the rest goes on. Nothing is deleted on either side unless the other
    side shows it was deleted there (or, for the server side, the item was deleted here and the server still has the version we knew)."""
    report = DavReport()
    state = DavState(state_path, durable)
    calendar_home, address_home = client.homes()
    remotes = client.collections(calendar_home, address_home)
    wanted_keys: set[tuple[str, str]] = set()
    for remote in remotes:
        wanted_keys.add((remote.href, remote.component))
        local = _matching(store, remote)
        try:
            if local is None:
                local = store.create_collection(
                    CollectionKind(remote.kind), remote.name, remote.colour, remote.read_only, {"href": remote.href, "component": remote.component}
                )
                report.created += 1
            elif local.read_only != remote.read_only:
                local = store.update_collection(local.collection_id, read_only=remote.read_only)
            report.collections += 1
            _sync_collection(client, store, local, remote, state, report, push)
        except AuthenticationError:
            raise
        except (ProtocolError, StorageError) as error:
            report.errors.append(f"{remote.name}: {error}")
        finally:
            state.save()
    if remotes:  # collections that are gone from the server stay here, no longer tied to it
        for info in store.collections():
            href = info.server_value("href")
            if href and (href, info.server_value("component")) not in wanted_keys:
                store.update_collection(info.collection_id, server={})
                state.collections.pop(info.collection_id, None)
        state.save()
    return report


def _sync_collection(client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, state: DavState, report: DavReport, push: bool) -> None:
    collection_state = state.get(local.collection_id)
    _pull(client, store, local, remote, collection_state, report)
    state.save()
    if push and not remote.read_only:
        _push(client, store, local, remote, collection_state, report)


def _listing(client: DavClient, remote: RemoteCollection, collection_state: CollectionState) -> tuple[list[RemoteItem], list[str], str, bool] | None:
    """What to look at: (items to look at, addresses removed, the sync token or ctag to remember, whether this is the complete list), or None when
    nothing changed."""
    changes: RemoteChanges | None = None
    if remote.sync_token:
        try:
            changes = client.changes(remote, collection_state.token)
        except DavConflict:
            changes = client.changes(remote, "")  # the server forgot our token: everything again
            collection_state.token = ""
        if changes is not None:
            if collection_state.token:
                return list(changes.changed), list(changes.removed), changes.token, False
            return list(changes.changed), [], changes.token, True
    if remote.ctag and remote.ctag == collection_state.ctag:
        return None
    return client.items(remote), [], remote.ctag, True


def _pull(client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, collection_state: CollectionState, report: DavReport) -> None:
    listing = _listing(client, remote, collection_state)
    if listing is None:
        return
    items, removed, marker, complete = listing
    links = collection_state.links
    if complete:
        present = {item.href for item in items}
        removed = [href for href in links if href not in present]
    wanted = [item.href for item in items if item.href not in links or not item.etag or links[item.href].etag != item.etag]
    fetched: dict[str, tuple[str, str]] = client.fetch(remote, wanted) if wanted else {}
    complete_fetch = True
    for href in wanted:
        if href not in fetched:
            report.errors.append(f"{remote.name}: an item could not be fetched")
            complete_fetch = False
            continue
        etag, text = fetched[href]
        if not _component_present(local.kind, text):
            continue  # an item of the other kind in a calendar that holds both
        try:
            _receive(store, local, collection_state, href, etag, text, report)
        except StorageError as error:
            report.errors.append(f"{remote.name}: {error}")
            complete_fetch = False
    for href in removed:
        link = links.pop(href, None)
        if link is None:
            continue
        try:
            if digest_of(store.get(local.collection_id, link.item_id)) == link.digest:
                store.delete(local.collection_id, link.item_id)
                report.deleted_here += 1
        except StorageError:
            pass  # already gone here
    if complete_fetch:
        if remote.sync_token:
            collection_state.token = marker
        else:
            collection_state.ctag = marker


def _receive(store: PimStore, local: CollectionInfo, collection_state: CollectionState, href: str, etag: str, text: str, report: DavReport) -> None:
    links = collection_state.links
    collection = local.collection_id
    link = links.get(href)
    item_id: str | None = None
    known_digest: str | None = None
    if link is not None:
        item_id, known_digest = link.item_id, link.digest
    else:
        uid = extract(local.kind, text).uid
        earlier = store.index.find_uid(collection, uid) if uid else None
        if earlier is not None:
            item_id = earlier.item_id
    current: str | None = None
    if item_id is not None:
        try:
            current = store.get(collection, item_id)
        except StorageError:
            current = None
    if current is not None and current != text:
        if known_digest is None or digest_of(current) != known_digest:  # it was changed here as well: the server wins, our version is kept as a copy
            store.put(collection, conflict_copy(local.kind, current))
            report.conflicts += 1
        item_id = store.put(collection, text, item_id)
        report.pulled += 1
    elif current is None:
        item_id = store.put(collection, text)
        report.pulled += 1
    if item_id is None:
        return
    links[href] = ItemLink(item_id, etag, digest_of(text))


def _push(client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, collection_state: CollectionState, report: DavReport) -> None:
    collection = local.collection_id
    links = collection_state.links
    existing = store.index.item_ids(collection)
    for href, link in list(links.items()):
        try:
            if link.item_id not in existing:  # deleted here
                try:
                    client.delete(href, link.etag)
                    report.deleted_on_server += 1
                except DavConflict:
                    pass  # changed on the server meanwhile: it comes back with the next synchronisation
                del links[href]
                continue
            text = store.get(collection, link.item_id)
            if digest_of(text) == link.digest:
                continue
            try:
                etag = client.put(href, text, remote.kind, etag=link.etag)
            except DavConflict:
                continue  # changed on the server meanwhile: the next synchronisation settles it
            links[href] = ItemLink(link.item_id, etag or client.etag_of(href), digest_of(text))
            report.pushed += 1
        except (ProtocolError, StorageError) as error:
            if isinstance(error, AuthenticationError):
                raise
            report.errors.append(f"{remote.name}: {error}")
    linked = {link.item_id for link in links.values()}
    for item_id in sorted(existing - linked):
        try:
            text = store.get(collection, item_id)
            uid = extract(local.kind, text).uid
            href = _address_for(remote, uid, item_id)
            try:
                etag = client.put(href, text, remote.kind, create=True)
            except DavConflict:  # that name is taken by another item
                href = _address_for(remote, "", item_id)
                etag = client.put(href, text, remote.kind, create=True)
            links[href] = ItemLink(item_id, etag or client.etag_of(href), digest_of(text))
            report.pushed += 1
        except AuthenticationError:
            raise
        except (ProtocolError, StorageError) as error:
            report.errors.append(f"{remote.name}: {error}")
