import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from chrono_mail.core import LOOK_FOLLOWS, LOOK_KEPT, LOOK_WAITING, AuthenticationError, CollectionInfo, CollectionKind, CollectionLook, ProtocolError, StorageError
from chrono_mail.pimstore import PimStore
from chrono_mail.pim.merge import Clash, merge
from chrono_mail.pimstore.extract import extract
from chrono_mail.protocols.dav import (
    DavClient,
    DavConflict,
    DavError,
    RemoteChanges,
    RemoteCollection,
    RemoteItem,
    canonical_href,
    canonical_key,
    folder_key,
    passing_failure,
)
from chrono_mail.sync.davcreates import forget as forget_create
from chrono_mail.sync.davcreates import load_waiting as load_creates
from chrono_mail.sync.davcreates import make_on_server, tie_for
from chrono_mail.sync.davdeletes import changed_on_server, load_waiting
from chrono_mail.sync.davdeletes import forget as forget_delete
from chrono_mail.sync.davconflicts import (
    CHANGED_ON_SERVER,
    CLASH,
    COLLECTION_CHANGED_ON_SERVER,
    COLLECTION_CREATE_REFUSED,
    COLLECTION_DELETE_REFUSED,
    COLLECTION_DELETED_ON_SERVER,
    COLLECTION_LOOK_CLASH,
    COLLECTION_LOOK_REFUSED,
    DELETE_REFUSED,
    DELETED_ON_SERVER,
    LOOK_REASONS,
    REFUSED,
    base_of,
    drop_base,
    load_conflicts,
    new_conflict,
    record,
)
from chrono_mail.sync.davconflicts import forget as forget_conflict
from chrono_mail.store.atomic import replace_file_atomically

# --- Version ------------------------------------------------------------
__version__ = "davsync.py 2.0.286"

# --- Version history ----------------------------------------------------
# v2.0.286: A collection's name or colour changed here and on the server, or a name the server refused for good, is a sync conflict (kept in
#           step with every synchronisation, gone once both sides agree), no longer an error line at every synchronisation (D-192, the Windows
#           computer's F3)
# v1.10.279: An item deleted here offline that the server changed meanwhile is a sync conflict (changed on the server), as online; it was saved
#            here again without a word (the owner)
# v1.10.278: The server's item is read with DavClient.read (the multiget first): Kerio answers a GET of an existing contact with 404, which made a
#            contact changed on both sides a false "deleted on the server" conflict, and a contact deleted here offline and changed there no conflict
# v1.1.236: A collection made here offline for a server account is made on the server first, then its items are sent in the same
#           synchronisation; a lasting refusal is a sync conflict; a colour the server does not keep is kept with the account (R-29)
# v1.1.235: A collection deleted here offline is deleted on the server (unless the server changed it meanwhile: a sync conflict; refused for good: a
#           sync conflict) and is never made here again while it waits (R-28)
# v1.1.234: A collection missing from the server's list goes here too, but only after the server itself confirmed it is gone (asked about that
#           collection; no answer or an error confirms nothing, it is kept and asked again); with changes here not yet sent it is a sync conflict
#           instead, its tie kept in the conflict; a list the server could not give stops the synchronisation (R-25)
# v1.1.225: A change on both sides is merged field by field or becomes a sync conflict (no more "[Conflict]" copies); changed here and deleted
#           on the server, a refusal of the server, a delete refused or the item changed meanwhile: a conflict; an item with an open conflict is
#           not sent; the agreed version is dropped once the server has the change (R-23, R-24, D-194)
# v1.1.221: Every sync follows the server's name and colour of a collection; a name or colour changed here waits in the account's looks and is
#           sent when the server still has what it had; a clash or a refusal is told and nothing is sent (D-192, R-26, R-27)
# v1.1.220: A listing the guard refused is trusted when the server gives exactly the same one at the next synchronisation (a real mass delete settles; the Windows computer's report)
# v1.1.218: A listing that would remove more than half of at least 10 items here removes nothing and is asked again; items that could not be fetched are told in one line (the Windows computer's report)
# v1.1.217: Addresses compared and remembered in one form (canonical_href), also those remembered before
# v1.1.210: The public folders too; the folders the account leaves out are skipped (D-183); unsent_changes
# v0.0.66: Initial synchronisation of the calendars, task lists and address books with a CalDAV and CardDAV server

STATE_VERSION: int = 1
SAFE_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9._@-]{1,100}")
PUBLIC_SUFFIX: str = " (public)"
VANISH_MINIMUM: int = 10  # the guard against a listing that lost most items: as for mail (D-049), more than half of at least this many


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
    refused: str = ""  # the digest of a complete listing the guard refused; the same listing at the next synchronisation is trusted


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
                state = CollectionState(str(description.get("token", "")), str(description.get("ctag", "")), refused=str(description.get("refused", "")))
                for href, link in description.get("links", {}).items():
                    state.links[canonical_href(str(href))] = ItemLink(str(link["id"]), str(link["etag"]), str(link["digest"]))
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
                    "refused": state.refused,
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
    removed: int = 0  # collections the server confirmed deleted, taken away here too (R-25)
    made_on_server: int = 0  # collections made here offline and now made on the server (R-29)
    pulled: int = 0  # items added or replaced here
    pushed: int = 0  # items added or replaced on the server
    deleted_here: int = 0
    deleted_on_server: int = 0
    conflicts: int = 0  # sync conflicts found or brought up to date (D-194)
    errors: list[str] = field(default_factory=list[str])
    looks: dict[str, CollectionLook | None] = field(default_factory=dict[str, CollectionLook | None])  # by folder_key: what the account keeps now (None: nothing)


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _component_present(kind: CollectionKind, text: str) -> bool:
    """Whether a text from a mixed calendar belongs in a collection of this kind."""
    if kind is CollectionKind.EVENTS:
        return "BEGIN:VEVENT" in text
    if kind is CollectionKind.TASKS:
        return "BEGIN:VTODO" in text
    return "BEGIN:VCARD" in text


def address_for(remote: RemoteCollection, uid: str, item_id: str) -> str:
    name = uid if SAFE_NAME.fullmatch(uid) else item_id
    return canonical_href(remote.href.rstrip("/") + "/" + urllib.parse.quote(name, safe="@") + (".vcf" if remote.kind == "contacts" else ".ics"))


def _matching(store: PimStore, remote: RemoteCollection) -> CollectionInfo | None:
    for info in store.collections():
        if canonical_href(info.server_value("href")) == remote.href and info.server_value("component") == remote.component:
            return info
    return None


# --- the name and colour of a collection (D-192) -------------------------------------------------------------------------------------------

DONE: str = "done"  # the server kept it
REFUSED: str = "refused"  # a lasting "no" of the server
LATER: str = "later"  # a passing failure: tried again at the next synchronisation
PART_STATUS_DEPENDENT: int = 424  # "not done because another part of the same request failed"


def send_look(client: DavClient, href: str, name: str | None, colour: str | None) -> tuple[dict[str, str], str]:
    """Give a collection on the server this name and/or colour: per part ("name", "colour") DONE, REFUSED or LATER, and the reason of a failure.
    A part the server left undone only because the other part failed is asked again on its own."""
    wanted = {part: value for part, value in (("name", name), ("colour", colour)) if value is not None}
    if not wanted:
        return {}, ""
    try:
        statuses = client.set_collection(href, name, colour)
        if len(wanted) == 2 and PART_STATUS_DEPENDENT in statuses.values():
            for part, status in list(statuses.items()):
                if status == PART_STATUS_DEPENDENT:
                    statuses.update(client.set_collection(href, wanted[part] if part == "name" else None, wanted[part] if part == "colour" else None))
    except ProtocolError as error:
        return {part: LATER if passing_failure(error) else REFUSED for part in wanted}, str(error)
    outcomes = {part: DONE if 200 <= statuses.get(part, 0) < 300 else REFUSED for part in wanted}
    refused = [f"{part} {statuses.get(part, 0)}" for part, outcome in outcomes.items() if outcome == REFUSED]
    return outcomes, ("the server answered " + ", ".join(refused)) if refused else ""


def _settle(value: str, state: str, base: str, server: str) -> tuple[str, str, str, str]:
    """One name or colour, as kept (value, state, base) against the server's now: (value, state, base, what to do: "", "send" or "clash")."""
    if state == LOOK_FOLLOWS or value == server:
        return server, LOOK_FOLLOWS, "", ""  # as the server has it (also: both sides made the same change)
    if server != base:
        return value, state, base, "clash"  # changed on the server too: nothing is sent, Chrono Mail keeps its own
    return value, state, base, "send" if state == LOOK_WAITING else ""


def server_name(local: CollectionInfo) -> str:
    """A collection's name in the server's form (without " (public)")."""
    public = local.server_value("public") == "1" or (not local.server_value("followed") and local.name.endswith(PUBLIC_SUFFIX))
    return local.name[: -len(PUBLIC_SUFFIX)] if public and local.name.endswith(PUBLIC_SUFFIX) else local.name


def _first_look(local: CollectionInfo, remote: RemoteCollection, report: DavReport) -> CollectionLook:
    """A collection made before 1.1.221 and synchronised for the first time since: what differs from the server was made here (names and colours were
    kept only here then). A colour is sent as a change waiting for the server; a name is kept and told, because a rename on the server was never
    followed before, so which side is newer cannot be known."""
    look = CollectionLook(folder_key(remote))
    name = server_name(local)
    if name != remote.name:
        look = replace(look, name=name, name_state=LOOK_KEPT, base_name=remote.name)
        report.errors.append(f"{local.name}: it is called “{remote.name}” on the server; Chrono Mail keeps “{name}”")
    if local.colour and local.colour != remote.colour:
        look = replace(look, colour=local.colour, colour_state=LOOK_WAITING if remote.kind != "contacts" else LOOK_KEPT, base_colour=remote.colour)
    return look


def _follow(client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, kept: CollectionLook | None, report: DavReport) -> CollectionInfo:
    """Bring a collection's name and colour here in line with the server and the account's kept look: the server's when nothing is kept; a change
    waiting here is sent when the server still has what it had then; a clash, or a name the server refused, is a sync conflict and nothing is sent
    (D-192); a colour the server does not take is kept here (D-201)."""
    key = folder_key(remote)
    look = kept if kept is not None else (_first_look(local, remote, report) if local.server_value("followed") != "1" else CollectionLook(key))
    name, name_state, base_name, name_step = _settle(look.name, look.name_state, look.base_name, remote.name)
    colour, colour_state, base_colour, colour_step = _settle(look.colour, look.colour_state, look.base_colour, remote.colour)
    if colour_step == "send" and remote.kind == "contacts":
        colour_state, colour_step = LOOK_KEPT, ""  # an address book has no colour on the server: it is kept here
    clashes = ((Clash("name", "Name", name, remote.name),) if name_step == "clash" else ()) + (
        (Clash("colour", "Colour", colour, remote.colour),) if colour_step == "clash" else ()
    )
    refused = ""
    if name_step == "send" or colour_step == "send":
        outcomes, reason = send_look(client, remote.href, name if name_step == "send" else None, colour if colour_step == "send" else None)
        if outcomes.get("name") == DONE:
            name_state, base_name = LOOK_FOLLOWS, ""
        elif outcomes.get("name") == REFUSED:
            name_state = LOOK_KEPT
            refused = reason or "the server did not take the name"
        if outcomes.get("colour") == DONE:
            colour_state, base_colour = LOOK_FOLLOWS, ""
        elif outcomes.get("colour") == REFUSED:
            colour_state = LOOK_KEPT
            report.errors.append(f"{local.name}: the server did not take its colour ({reason}); Chrono Mail keeps it on this computer")
    _look_conflict(store, local, remote, clashes, refused, name_state == LOOK_KEPT and name != remote.name, name, report)
    settled = CollectionLook(key, name if name_state != LOOK_FOLLOWS else "", name_state, base_name, colour if colour_state != LOOK_FOLLOWS else "", colour_state, base_colour)
    now: CollectionLook | None = None if settled.empty else settled
    if now != kept:
        report.looks[key] = now
    shown_name = name + (PUBLIC_SUFFIX if remote.public else "")
    server = {**dict(local.server), "followed": "1", "public": "1" if remote.public else ""}
    if shown_name != local.name or colour != local.colour or server != dict(local.server):
        local = store.update_collection(local.collection_id, name=shown_name, colour=colour, server=server)
    return local


def _look_conflict(
    store: PimStore, local: CollectionInfo, remote: RemoteCollection, clashes: tuple[Clash, ...], refused: str, still_refused: bool, name: str, report: DavReport,
) -> None:
    """Keep a collection's name and colour conflict in Sync conflicts (D-192, D-194): a clash (changed here and on the server), or a name the server
    refused for good (while Chrono Mail keeps its name and the server has another). Updated, never doubled; gone once both sides agree."""
    open_now = next((c for c in load_conflicts(store) if c.collection == local.collection_id and c.reason in LOOK_REASONS), None)
    if clashes:
        reason, found, detail = COLLECTION_LOOK_CLASH, clashes, ""
    elif refused or still_refused:  # refused now, or earlier (also when the person renamed it online) and Chrono Mail still keeps its name
        earlier = open_now.detail if open_now is not None and open_now.reason == COLLECTION_LOOK_REFUSED else ""
        reason, found, detail = COLLECTION_LOOK_REFUSED, (Clash("name", "Name", name, remote.name),), refused or earlier
    else:
        if open_now is not None:
            forget_conflict(store, open_now.conflict_id)  # both sides agree again (or the person's choice was taken)
            report.conflicts += 1
        return
    if open_now is not None and open_now.reason == reason and open_now.clashes == found and open_now.href == remote.href:
        return  # nothing new since the last synchronisation
    record(store, new_conflict(local.collection_id, remote.href, "", local.kind.value, reason, local.name, "", "", found, detail=detail))
    report.conflicts += 1


def sync_dav(
    client: DavClient, store: PimStore, state_path: Path, durable: bool = True, push: bool = True, left_out: frozenset[str] = frozenset(),
    looks: Mapping[str, CollectionLook] | None = None,
) -> DavReport:
    """Bring the calendars, task lists and address books of an account in line with its server, both ways. A server that cannot be reached or does not
    accept the login stops it; trouble with one collection or item is reported and the rest goes on. Nothing is deleted on either side unless the other
    side shows it was deleted there (or, for the server side, the item was deleted here and the server still has the version we knew). The folders the
    account leaves out (their folder_key) are not looked at; every other folder, also one made on the server later, is kept in step (D-183)."""
    report = DavReport()
    state = DavState(state_path, durable)
    if push:
        _send_creates(client, store, report)  # made on the server first, so the listing below has them and their items go in this round
    remotes = client.all_collections()
    left_out = frozenset(canonical_key(key) for key in left_out)
    wanted_keys: set[tuple[str, str]] = set()
    deleting = {canonical_href(item.href) for item in load_waiting(store)}  # deleted here, waiting for the server: never made here again
    for remote in remotes:
        wanted_keys.add((remote.href, remote.component))
        if folder_key(remote) in left_out or canonical_href(remote.href) in deleting:
            continue
        local = _matching(store, remote)
        try:
            if local is None:
                local = store.create_collection(
                    CollectionKind(remote.kind), remote.name + (PUBLIC_SUFFIX if remote.public else ""), remote.colour, remote.read_only,
                    {"href": remote.href, "component": remote.component, "followed": "1", "public": "1" if remote.public else ""},
                )
                report.created += 1
            elif local.read_only != remote.read_only:
                local = store.update_collection(local.collection_id, read_only=remote.read_only)
            key = folder_key(remote)
            local = _follow(client, store, local, remote, report.looks[key] if key in report.looks else (looks or {}).get(key), report)
            report.collections += 1
            _sync_collection(client, store, local, remote, state, report, push)
        except AuthenticationError:
            raise
        except (ProtocolError, StorageError) as error:
            report.errors.append(f"{remote.name}: {error}")
        finally:
            state.save()
    for info in store.collections():
        href = canonical_href(info.server_value("href"))
        if href and (href, info.server_value("component")) not in wanted_keys:
            _gone_from_list(client, store, info, state, looks or {}, report)
    state.save()
    if push and deleting:
        _send_deletes(client, store, {canonical_href(remote.href) for remote in remotes}, report)
    return report


def _send_creates(client: DavClient, store: PimStore, report: DavReport) -> None:
    """Make on the server the collections made here for this account while it could not be reached (R-29). A lasting refusal: a sync conflict
    (nothing of it is sent while it is open); a passing failure: tried again next time. A colour the server does not keep is kept with the account."""
    waiting = load_creates(store)
    if not waiting:
        return
    held = {c.collection for c in load_conflicts(store) if c.reason == COLLECTION_CREATE_REFUSED}
    for item in waiting:
        if item.collection in held:
            continue
        try:
            info = store.get_collection(item.collection)
        except StorageError:
            forget_create(store, item.collection)  # deleted here meanwhile: nothing to make
            continue
        try:
            remote = make_on_server(client, info.kind.value, info.name, info.colour)
        except AuthenticationError:
            raise
        except ProtocolError as error:
            if passing_failure(error):
                report.errors.append(f"{info.name}: the server could not make it now ({error}); it is tried again at the next synchronisation")
                return
            record(store, new_conflict(info.collection_id, "", "", info.kind.value, COLLECTION_CREATE_REFUSED, info.name, "", "", detail=str(error), whole=True))
            report.conflicts += 1
            continue
        store.update_collection(info.collection_id, server=tie_for(remote))
        forget_create(store, item.collection)
        report.made_on_server += 1
        if info.colour and info.kind is not CollectionKind.CONTACTS and remote.colour != info.colour:
            key = folder_key(remote)  # the server keeps no colour (or another one): the person's is kept with the account (D-192)
            report.looks[key] = CollectionLook(key, colour=info.colour, colour_state=LOOK_KEPT, base_colour=remote.colour)


def _send_deletes(client: DavClient, store: PimStore, listed: set[str], report: DavReport) -> None:
    """Delete on the server the collections deleted here while it could not be reached (R-28). Gone there already: done. Changed there since it was
    last synchronised: nothing deleted, a sync conflict. A lasting refusal: a sync conflict. A passing failure: tried again next time. While a
    conflict about one is open it is not sent."""
    held = {c.href for c in load_conflicts(store) if c.reason in (COLLECTION_CHANGED_ON_SERVER, COLLECTION_DELETE_REFUSED)}
    for waiting in load_waiting(store):
        href = canonical_href(waiting.href)
        if href in held:
            continue
        try:
            if href not in listed and not client.exists(waiting.href):
                forget_delete(store, href)  # the server confirms it is not there: done
                continue
            try:
                changed = changed_on_server(client, waiting.href, waiting.kind, dict(waiting.etags))
            except DavError as error:
                if error.status not in (404, 410):
                    raise
                forget_delete(store, href)
                continue
            if changed:
                record(store, new_conflict(waiting.collections[0], href, "", waiting.kind, COLLECTION_CHANGED_ON_SERVER, waiting.name, "", "", whole=True))
                report.conflicts += 1
                continue
            client.delete(waiting.href)
            forget_delete(store, href)
            report.deleted_on_server += 1
        except AuthenticationError:
            raise
        except ProtocolError as error:
            if passing_failure(error):
                report.errors.append(f"{waiting.name}: the server could not delete it now ({error}); it is tried again at the next synchronisation")
                return
            record(store, new_conflict(waiting.collections[0], href, "", waiting.kind, COLLECTION_DELETE_REFUSED, waiting.name, "", "", detail=str(error), whole=True))
            report.conflicts += 1


def _gone_from_list(
    client: DavClient, store: PimStore, info: CollectionInfo, state: DavState, looks: Mapping[str, CollectionLook], report: DavReport,
) -> None:
    """A collection here that the server no longer lists. It goes only on the server's confirmation that it was deleted: the server is asked about
    that collection itself, and only "not there" (404, 410) confirms it; no answer or any other answer confirms nothing, so it stays and is asked
    again at the next synchronisation (the owner, R-25). Confirmed and nothing changed here: it goes here too (to the deleted items, 30 days).
    Confirmed but changed here and not sent yet: a sync conflict; the collection stays here without its tie, kept in the conflict (D-194)."""
    href = info.server_value("href")
    key = f"{info.server_value('component')} {canonical_href(href)}"
    try:
        there = client.exists(href)
    except AuthenticationError:
        raise
    except ProtocolError as error:
        report.errors.append(f"{info.name}: no longer in the server's list, but the server did not confirm it was deleted ({error}); it is kept and asked again at the next synchronisation")
        return
    if there:
        report.errors.append(f"{info.name}: no longer in the server's list, but the server says it is still there; it is kept")
        return
    look = looks.get(canonical_key(key))
    changed_look = look is not None and LOOK_WAITING in (look.name_state, look.colour_state)
    if changed_look or _unsent(store, info, state):
        record(store, new_conflict(info.collection_id, canonical_href(href), "", info.kind.value, COLLECTION_DELETED_ON_SERVER, server_name(info), "", "",
                                   whole=True, tie=info.server))
        store.update_collection(info.collection_id, server={})  # this computer's only until the person chooses: its changes are written here
        report.conflicts += 1
    else:
        store.delete_collection(info.collection_id)
        report.removed += 1
    state.collections.pop(info.collection_id, None)
    if look is not None:
        report.looks[canonical_key(key)] = None


def unsent_changes(store: PimStore, local: CollectionInfo, state_path: Path) -> bool:
    """Whether a collection tied to a server has changes here that are not on the server yet: an item made, changed or deleted here since the last
    synchronisation. A read-only collection never sends anything, so it has none."""
    return _unsent(store, local, DavState(state_path, durable=False))


def _unsent(store: PimStore, local: CollectionInfo, state: DavState) -> bool:
    if local.read_only:
        return False
    links = state.collections.get(local.collection_id, CollectionState()).links
    existing = set(store.index.item_ids(local.collection_id))
    linked = {link.item_id: link for link in links.values()}
    if existing - set(linked) or set(linked) - existing:
        return True
    return any(digest_of(store.get(local.collection_id, item_id)) != link.digest for item_id, link in linked.items())


def forget_collection(state_path: Path, collection_id: str, durable: bool = True) -> None:
    """Drop what was remembered of a collection that is no longer kept in step."""
    state = DavState(state_path, durable)
    if state.collections.pop(collection_id, None) is not None:
        state.save()


def matching_collection(store: PimStore, key: str) -> CollectionInfo | None:
    """The collection here that is tied to the server folder with this folder_key."""
    component, _, href = canonical_key(key).partition(" ")
    return next((info for info in store.collections() if canonical_href(info.server_value("href")) == href and info.server_value("component") == component), None)


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
    trusted = True  # whether the listing may be remembered as seen (its token or ctag kept)
    if complete:
        present = {item.href for item in items}
        removed = [href for href in links if href not in present]
        if len(links) >= VANISH_MINIMUM and len(removed) * 2 > len(links):  # a cut-short or empty listing must never empty the calendar at once
            seen = digest_of("\n".join(sorted(present)))
            if collection_state.refused == seen:
                collection_state.refused = ""  # the server gave exactly the same listing twice in a row: a real delete, done here too
            else:
                collection_state.refused = seen
                report.errors.append(
                    f"{remote.name}: the server listed {len(links) - len(removed)} of the {len(links)} items known here; nothing was removed here, "
                    "and it is asked again at the next synchronisation"
                )
                removed, trusted = list[str](), False
        else:
            collection_state.refused = ""
    wanted = [item.href for item in items if item.href not in links or not item.etag or links[item.href].etag != item.etag]
    fetched: dict[str, tuple[str, str]] = client.fetch(remote, wanted) if wanted else {}
    complete_fetch = trusted
    missing = [href for href in wanted if href not in fetched]
    if missing:
        report.errors.append(f"{remote.name}: {len(missing)} item(s) could not be fetched; they are asked for again at the next synchronisation")
        complete_fetch = False
    for href in wanted:
        if href not in fetched:
            continue
        etag, text = fetched[href]
        if not _component_present(local.kind, text):
            continue  # an item of the other kind in a calendar that holds both
        try:
            _receive(client, store, local, collection_state, href, etag, text, report)
        except StorageError as error:
            report.errors.append(f"{remote.name}: {error}")
            complete_fetch = False
    for href in removed:
        link = links.pop(href, None)
        if link is None:
            continue
        try:
            current = store.get(local.collection_id, link.item_id)
        except StorageError:
            continue  # already gone here
        if digest_of(current) == link.digest:
            store.delete(local.collection_id, link.item_id)
            drop_base(store, local.collection_id, link.item_id)
            report.deleted_here += 1
        else:  # changed here, deleted on the server: the person decides (D-194)
            record(store, new_conflict(local.collection_id, href, link.item_id, local.kind.value, DELETED_ON_SERVER, title_of(local.kind, current), "", ""))
            report.conflicts += 1
    if complete_fetch:
        if remote.sync_token:
            collection_state.token = marker
        else:
            collection_state.ctag = marker


def title_of(kind: CollectionKind, text: str) -> str:
    return extract(kind, text).title or "(no title)"


def weigh(
    client: DavClient, store: PimStore, local: CollectionInfo, collection_state: CollectionState, href: str, item: str, ours: str, etag: str, theirs: str,
) -> str:
    """Chrono Mail's version of an item against a newer one of the server: merged field by field with the version both last agreed on (sent and
    kept here: "done"; the server changed again meanwhile: "waiting"), or a sync conflict ("conflict", D-194). Saving the state is the caller's."""
    base = base_of(store, local.collection_id, item)
    merged = merge(base, ours, theirs)
    if merged.text is None:
        record(store, new_conflict(local.collection_id, href, item, local.kind.value, CLASH, title_of(local.kind, ours), etag, theirs, merged.clashes,
                                   whole=base is None))
        collection_state.links[href] = ItemLink(item, etag, collection_state.links[href].digest if href in collection_state.links else "")
        return "conflict"
    if merged.text == theirs:
        store.put(local.collection_id, theirs, item)
        collection_state.links[href] = ItemLink(item, etag, digest_of(theirs))
        drop_base(store, local.collection_id, item)
        return "done"
    try:
        new_etag = client.put(href, merged.text, local.kind.value, etag=etag)
    except DavConflict:
        return "waiting"  # changed on the server once more: weighed again at the next synchronisation
    store.put(local.collection_id, merged.text, item)
    collection_state.links[href] = ItemLink(item, new_etag or client.etag_of(href), digest_of(merged.text))
    drop_base(store, local.collection_id, item)
    return "done"


def _receive(
    client: DavClient, store: PimStore, local: CollectionInfo, collection_state: CollectionState, href: str, etag: str, text: str, report: DavReport,
) -> None:
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
    if link is not None and current is None and link.item_id not in store.item_ids(collection):
        if digest_of(text) == link.digest:
            return  # unchanged there (a server that lists no version tags): the push sends the delete
        # deleted here (offline) and changed on the server since: the person decides, as for a delete made online (D-194); nothing is made here,
        # and the delete is not sent while the conflict is open (a damaged item here is not "deleted": it is not in this branch)
        record(store, new_conflict(collection, href, "", local.kind.value, CHANGED_ON_SERVER, title_of(local.kind, text), etag, text))
        report.conflicts += 1
        return
    if item_id is not None and current is not None and current != text and (known_digest is None or digest_of(current) != known_digest):
        # changed here as well: merged field by field, or a sync conflict the person settles (D-194)
        if weigh(client, store, local, collection_state, href, item_id, current, etag, text) == "conflict":
            report.conflicts += 1
        else:
            report.pulled += 1
        return
    if current is not None and current != text:
        item_id = store.put(collection, text, item_id)
        report.pulled += 1
    elif current is None:
        item_id = store.put(collection, text)
        report.pulled += 1
    if item_id is None:
        return
    links[href] = ItemLink(item_id, etag, digest_of(text))
    drop_base(store, collection, item_id)


def _push(client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, collection_state: CollectionState, report: DavReport) -> None:
    """Send what was changed here (offline, or before the server answered); an item with an open sync conflict waits for the person. The server
    changed an item meanwhile: weighed field by field. A lasting "no" of the server, or a delete of an item it changed meanwhile: a conflict (D-194).
    A passing failure ends the push: everything is tried again at the next synchronisation."""
    collection = local.collection_id
    links = collection_state.links
    existing = store.index.item_ids(collection)
    held = [(c.href, c.item) for c in load_conflicts(store) if c.collection == collection]
    held_hrefs = {href for href, _ in held}
    held_items = {item for _, item in held if item}
    for href, link in list(links.items()):
        if href in held_hrefs or link.item_id in held_items:
            continue
        try:
            if link.item_id not in existing:  # deleted here
                _push_delete(client, store, local, remote, collection_state, href, link, report)
                continue
            text = store.get(collection, link.item_id)
            if digest_of(text) == link.digest:
                continue
            try:
                etag = client.put(href, text, remote.kind, etag=link.etag)
            except DavConflict:  # changed on the server meanwhile (or deleted there)
                fetched = client.read(remote, href)
                if fetched is None:
                    record(store, new_conflict(collection, href, link.item_id, local.kind.value, DELETED_ON_SERVER, title_of(local.kind, text), "", ""))
                    report.conflicts += 1
                elif weigh(client, store, local, collection_state, href, link.item_id, text, *fetched) == "conflict":
                    report.conflicts += 1
                else:
                    report.pushed += 1
                continue
            except ProtocolError as error:
                if passing_failure(error):
                    raise
                fetched = client.read(remote, href)
                record(store, new_conflict(collection, href, link.item_id, local.kind.value, REFUSED, title_of(local.kind, text),
                                           fetched[0] if fetched else link.etag, fetched[1] if fetched else "", detail=str(error)))
                report.conflicts += 1
                continue
            links[href] = ItemLink(link.item_id, etag or client.etag_of(href), digest_of(text))
            drop_base(store, collection, link.item_id)
            report.pushed += 1
        except AuthenticationError:
            raise
        except (ProtocolError, StorageError) as error:
            report.errors.append(f"{remote.name}: {error}")
            if isinstance(error, ProtocolError) and passing_failure(error):
                return  # the server cannot be reached for now: the rest waits for the next synchronisation
    linked = {link.item_id for link in links.values()}
    for item_id in sorted(existing - linked - held_items):
        try:
            text = store.get(collection, item_id)
            uid = extract(local.kind, text).uid
            href = address_for(remote, uid, item_id)
            try:
                try:
                    etag = client.put(href, text, remote.kind, create=True)
                except DavConflict:  # that name is taken by another item
                    href = address_for(remote, "", item_id)
                    etag = client.put(href, text, remote.kind, create=True)
            except ProtocolError as error:
                if passing_failure(error):
                    raise
                record(store, new_conflict(collection, href, item_id, local.kind.value, REFUSED, title_of(local.kind, text), "", "", detail=str(error)))
                report.conflicts += 1
                continue
            links[href] = ItemLink(item_id, etag or client.etag_of(href), digest_of(text))
            drop_base(store, collection, item_id)
            report.pushed += 1
        except AuthenticationError:
            raise
        except (ProtocolError, StorageError) as error:
            report.errors.append(f"{remote.name}: {error}")
            if isinstance(error, ProtocolError) and passing_failure(error):
                return


def _push_delete(
    client: DavClient, store: PimStore, local: CollectionInfo, remote: RemoteCollection, collection_state: CollectionState, href: str, link: ItemLink,
    report: DavReport,
) -> None:
    """An item deleted here (offline): deleted on the server; changed there meanwhile, or the delete refused for good: a conflict."""
    collection = local.collection_id
    try:
        client.delete(href, link.etag)
        report.deleted_on_server += 1
        del collection_state.links[href]
    except DavConflict:
        fetched = client.read(remote, href)
        if fetched is None:
            del collection_state.links[href]
        else:
            record(store, new_conflict(collection, href, "", local.kind.value, CHANGED_ON_SERVER, title_of(local.kind, fetched[1]), fetched[0], fetched[1]))
            report.conflicts += 1
    except ProtocolError as error:
        if passing_failure(error):
            raise
        fetched = client.read(remote, href)
        record(store, new_conflict(collection, href, "", local.kind.value, DELETE_REFUSED, title_of(local.kind, fetched[1]) if fetched else "",
                                   fetched[0] if fetched else link.etag, fetched[1] if fetched else "", detail=str(error)))
        report.conflicts += 1
    drop_base(store, collection, link.item_id)
