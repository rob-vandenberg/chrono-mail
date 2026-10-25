"""Calendars, task lists and address books deleted here that their server has not deleted yet (R-28, D-197). A delete made while the server could
not be reached is done here at once and remembered here, encrypted like the items, on this computer only; the next synchronisation deletes it on
the server, unless the server changed it meanwhile (a sync conflict). While it waits the synchronisation never makes it here again."""

import json
import time
from dataclasses import dataclass

from chrono_mail.core import StorageError
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient, RemoteCollection, canonical_href
from chrono_mail.core.jsontext import load_json

# --- Version ------------------------------------------------------------
__version__ = "davdeletes.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: JSON is read with core.jsontext.load_json: nested without end it is damaged, never a crash
# v1.1.235: Initial: deletes of whole collections waiting for the server, and whether the server changed one since it was last synchronised (R-28)

WAITING_FILE: str = "collection-deletes"
MAXIMUM_WAITING: int = 1_000


@dataclass(frozen=True, slots=True)
class WaitingDelete:
    """A collection deleted here, to be deleted on the server: its place there, the collections here it was (a calendar may hold events and tasks,
    two collections here), and the version of every item as both sides last agreed (to see whether the server changed it meanwhile)."""

    href: str
    collections: tuple[str, ...]
    kind: str  # events, tasks or contacts (of the first)
    name: str
    etags: tuple[tuple[str, str], ...]  # item address on the server -> version tag
    when: float


def load_waiting(store: PimStore) -> list[WaitingDelete]:
    text = store.read_private(WAITING_FILE)
    if text is None:
        return []
    try:
        return [
            WaitingDelete(
                str(entry["href"]), tuple(str(item) for item in entry["collections"]), str(entry["kind"]), str(entry["name"]),
                tuple((str(pair[0]), str(pair[1])) for pair in entry["etags"]), float(entry["when"]),
            )
            for entry in load_json(text)["deletes"]
        ]
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise StorageError(f"The list of deletes waiting for the server is damaged: {error}") from None


def save_waiting(store: PimStore, waiting: list[WaitingDelete]) -> None:
    if not waiting:
        store.drop_private(WAITING_FILE)
        return
    data = {
        "deletes": [
            {"href": item.href, "collections": list(item.collections), "kind": item.kind, "name": item.name, "etags": [list(pair) for pair in item.etags], "when": item.when}
            for item in waiting[-MAXIMUM_WAITING:]
        ]
    }
    store.write_private(WAITING_FILE, json.dumps(data))


def remember(store: PimStore, href: str, collections: tuple[str, ...], kind: str, name: str, etags: dict[str, str]) -> None:
    """Keep a delete for the next synchronisation (one per place on the server)."""
    waiting = [item for item in load_waiting(store) if canonical_href(item.href) != canonical_href(href)]
    waiting.append(WaitingDelete(canonical_href(href), collections, kind, name, tuple(sorted(etags.items())), time.time()))
    save_waiting(store, waiting)


def forget(store: PimStore, href: str) -> None:
    save_waiting(store, [item for item in load_waiting(store) if canonical_href(item.href) != canonical_href(href)])


def waiting_for(store: PimStore, href: str) -> WaitingDelete | None:
    return next((item for item in load_waiting(store) if canonical_href(item.href) == canonical_href(href)), None)


def changed_on_server(client: DavClient, href: str, kind: str, etags: dict[str, str]) -> int:
    """How many items the server has that are new or another version than both sides last agreed on: what a delete would take away unseen. An
    item gone from the server is no change that matters here (it would be deleted anyway). An error raises (it confirms nothing)."""
    listed = client.items(RemoteCollection(href, "", kind, "", "", False, "", ""))
    known = {canonical_href(address): etag for address, etag in etags.items()}
    return sum(1 for item in listed if known.get(canonical_href(item.href)) != item.etag)
