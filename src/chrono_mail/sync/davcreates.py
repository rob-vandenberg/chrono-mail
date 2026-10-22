"""Calendars, task lists and address books made here for a server account while the server could not be reached (R-29, D-198). Made here at once
(not tied to the server yet, so their items are written here) and remembered, encrypted, on this computer only; the next synchronisation makes
them on the server first and then sends their items. A lasting refusal is a sync conflict (Keep here only moves it to the local ones)."""

import json
import secrets
import time
from dataclasses import dataclass

from chrono_mail.core import StorageError
from chrono_mail.pimstore import PimStore
from chrono_mail.protocols.dav import DavClient, DavError, RemoteCollection

# --- Version ------------------------------------------------------------
__version__ = "davcreates.py 1.1.236"

# --- Version history ----------------------------------------------------
# v1.1.236: Initial: collections waiting to be made on the server; made at a new place in the right home (R-29)

WAITING_FILE: str = "collection-creates"


@dataclass(frozen=True, slots=True)
class WaitingCreate:
    collection: str  # the collection here
    when: float


def load_waiting(store: PimStore) -> list[WaitingCreate]:
    text = store.read_private(WAITING_FILE)
    if text is None:
        return []
    try:
        return [WaitingCreate(str(entry["collection"]), float(entry["when"])) for entry in json.loads(text)["creates"]]
    except (ValueError, KeyError, TypeError) as error:
        raise StorageError(f"The list of calendars waiting to be made on the server is damaged: {error}") from None


def _save(store: PimStore, waiting: list[WaitingCreate]) -> None:
    if not waiting:
        store.drop_private(WAITING_FILE)
        return
    store.write_private(WAITING_FILE, json.dumps({"creates": [{"collection": item.collection, "when": item.when} for item in waiting]}))


def remember(store: PimStore, collection: str) -> None:
    _save(store, [item for item in load_waiting(store) if item.collection != collection] + [WaitingCreate(collection, time.time())])


def forget(store: PimStore, collection: str) -> None:
    _save(store, [item for item in load_waiting(store) if item.collection != collection])


def make_on_server(client: DavClient, kind: str, name: str, colour: str) -> RemoteCollection:
    """Make a collection on the server in the home of its kind, at a new place of its own; returned as the server lists it. Raises ProtocolError
    (DavError for a refusal, see passing_failure)."""
    calendar_home, address_home = client.homes()
    home = address_home if kind == "contacts" else calendar_home
    if not home:
        raise DavError(f"The server has no place for {'address books' if kind == 'contacts' else 'calendars'}", 403)
    return client.make_collection(home.rstrip("/") + f"/chrono-mail-{secrets.token_hex(8)}/", kind, name, colour if kind != "contacts" else "")


def tie_for(remote: RemoteCollection) -> dict[str, str]:
    """What a collection here keeps of its place on the server (as the synchronisation ties the ones it makes here)."""
    return {"href": remote.href, "component": remote.component, "followed": "1", "public": ""}


def waiting_ids(store: PimStore) -> set[str]:
    return {item.collection for item in load_waiting(store)}

