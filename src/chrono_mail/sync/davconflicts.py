"""The sync conflicts of one account's calendars, task lists and address books, and the agreed versions of items changed here (D-194). Both are
kept beside the items, encrypted like them, on this computer only (a conflict belongs to the computer that has it). A conflict is known by its
collection and its place on the server, so a new change on either side updates it, never makes a second one."""

import json
import os
import time
from dataclasses import dataclass, replace

from chrono_mail.core import StorageError
from chrono_mail.pim.merge import Clash
from chrono_mail.pimstore import PimStore

# --- Version ------------------------------------------------------------
__version__ = "davconflicts.py 1.1.236"

# --- Version history ----------------------------------------------------
# v1.1.236: A collection made here (offline) that the server refused to make (R-29)
# v1.1.235: A collection deleted here (offline) that the server changed meanwhile, or whose delete the server refused (R-28)
# v1.1.234: A whole calendar, task list or address book deleted on the server with changes here not yet sent (COLLECTION_DELETED_ON_SERVER);
#           a conflict keeps the collection's tie to the server it had (tie), so Use Chrono Mail can make it there again (R-25)
# v1.1.225: Initial: sync conflicts and agreed versions kept per account (R-23, R-24, D-194)

CONFLICTS_FILE: str = "sync-conflicts"
MAXIMUM_CONFLICTS: int = 10_000

# Why the two sides differ:
CLASH: str = "clash"  # the same field changed on both sides
REFUSED: str = "refused"  # a change made here (offline) that the server refused for good
DELETED_ON_SERVER: str = "deleted_on_server"  # changed here, deleted on the server
CHANGED_ON_SERVER: str = "changed_on_server"  # deleted here, changed on the server
DELETE_REFUSED: str = "delete_refused"  # deleted here (offline), the server refused the delete
COLLECTION_DELETED_ON_SERVER: str = "collection_deleted_on_server"  # a whole collection the server confirmed deleted, with changes here not yet sent
COLLECTION_CHANGED_ON_SERVER: str = "collection_changed_on_server"  # a whole collection deleted here (offline) that the server changed meanwhile
COLLECTION_DELETE_REFUSED: str = "collection_delete_refused"  # a whole collection deleted here (offline), the server refused the delete
COLLECTION_CREATE_REFUSED: str = "collection_create_refused"  # a whole collection made here (offline), the server refused to make it
COLLECTION_REASONS: frozenset[str] = frozenset({COLLECTION_DELETED_ON_SERVER, COLLECTION_CHANGED_ON_SERVER, COLLECTION_DELETE_REFUSED, COLLECTION_CREATE_REFUSED})
REASONS: frozenset[str] = frozenset({CLASH, REFUSED, DELETED_ON_SERVER, CHANGED_ON_SERVER, DELETE_REFUSED}) | COLLECTION_REASONS


@dataclass(frozen=True, slots=True)
class SyncConflict:
    """One item that differs between Chrono Mail and the server and waits for the person (D-194). While it is open the item keeps Chrono Mail's
    version and is not sent."""

    conflict_id: str
    collection: str  # the collection here
    href: str  # where the item is (or was) on the server
    item: str  # the item here; "" when it was deleted here
    kind: str  # events, tasks or contacts
    reason: str
    name: str  # what the person knows it by (its title or name)
    detail: str  # the server's own words for a refusal
    server_etag: str  # the server's version the conflict is about ("" when deleted there)
    server_text: str  # that version ("" when deleted there)
    clashes: tuple[Clash, ...]
    whole: bool  # no agreed version was known: only a whole side can be chosen
    when: float
    tie: tuple[tuple[str, str], ...] = ()  # a collection's conflict: the tie to the server it had (its server values), for making it there again


def base_name(collection: str, item: str) -> str:
    return f"base-{collection}-{item}"


def keep_base(store: PimStore, collection: str, item: str, agreed: str) -> None:
    """Keep the version both sides last agreed on before the item is changed here, once (a second change keeps the first agreed version)."""
    if store.read_private(base_name(collection, item)) is None:
        store.write_private(base_name(collection, item), agreed)


def base_of(store: PimStore, collection: str, item: str) -> str | None:
    return store.read_private(base_name(collection, item)) if item else None


def drop_base(store: PimStore, collection: str, item: str) -> None:
    if item:
        store.drop_private(base_name(collection, item))


def load_conflicts(store: PimStore) -> list[SyncConflict]:
    text = store.read_private(CONFLICTS_FILE)
    if text is None:
        return []
    try:
        found: list[SyncConflict] = []
        for entry in json.loads(text)["conflicts"]:
            clashes = tuple(Clash(str(c[0]), str(c[1]), str(c[2]), str(c[3])) for c in entry["clashes"])
            found.append(SyncConflict(
                str(entry["id"]), str(entry["collection"]), str(entry["href"]), str(entry["item"]), str(entry["kind"]), str(entry["reason"]),
                str(entry["name"]), str(entry["detail"]), str(entry["server_etag"]), str(entry["server_text"]), clashes, bool(entry["whole"]),
                float(entry["when"]), tuple((str(pair[0]), str(pair[1])) for pair in entry.get("tie", [])),
            ))
        return found
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise StorageError(f"The list of sync conflicts is damaged: {error}") from None


def save_conflicts(store: PimStore, conflicts: list[SyncConflict]) -> None:
    if not conflicts:
        store.drop_private(CONFLICTS_FILE)
        return
    data = {
        "conflicts": [
            {
                "id": item.conflict_id, "collection": item.collection, "href": item.href, "item": item.item, "kind": item.kind, "reason": item.reason,
                "name": item.name, "detail": item.detail, "server_etag": item.server_etag, "server_text": item.server_text,
                "clashes": [[c.key, c.label, c.local, c.server] for c in item.clashes], "whole": item.whole, "when": item.when,
                "tie": [list(pair) for pair in item.tie],
            }
            for item in conflicts[-MAXIMUM_CONFLICTS:]
        ]
    }
    store.write_private(CONFLICTS_FILE, json.dumps(data))


def open_conflict(store: PimStore, collection: str, href: str = "", item: str = "") -> SyncConflict | None:
    """The open conflict of an item, by its place on the server or its id here."""
    return next(
        (c for c in load_conflicts(store) if c.collection == collection and ((href and c.href == href) or (item and c.item == item))), None,
    )


def record(store: PimStore, conflict: SyncConflict) -> SyncConflict:
    """Keep a conflict: one that is already open for the same item is updated (it keeps its id), never doubled. Returns what was kept."""
    conflicts = load_conflicts(store)
    for number, known in enumerate(conflicts):
        if known.collection == conflict.collection and known.href == conflict.href:
            kept = replace(conflict, conflict_id=known.conflict_id)
            conflicts[number] = kept
            save_conflicts(store, conflicts)
            return kept
    conflicts.append(conflict)
    save_conflicts(store, conflicts)
    return conflict


def forget(store: PimStore, conflict_id: str) -> None:
    save_conflicts(store, [c for c in load_conflicts(store) if c.conflict_id != conflict_id])


def new_conflict(
    collection: str, href: str, item: str, kind: str, reason: str, name: str, server_etag: str, server_text: str, clashes: tuple[Clash, ...] = (),
    detail: str = "", whole: bool = False, tie: tuple[tuple[str, str], ...] = (),
) -> SyncConflict:
    return SyncConflict(os.urandom(8).hex(), collection, href, item, kind, reason, name, detail, server_etag, server_text, clashes, whole, time.time(), tie)
