"""Changes the person makes to an item of a server's collection, done on the server first and here when it took them (D-193, D-194). Offline (or the
server failing for now) the change is made here and sent at the next synchronisation, with the version both sides agreed on kept beside it. The
server changed the item meanwhile: merged field by field when nothing clashes, else a sync conflict. A lasting "no" of the server is an error and
nothing changes here (the data stays the same on both sides)."""

from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import CollectionInfo, ProtocolError, StorageError
from chrono_mail.pim.merge import LOCAL, SERVER, merge
from chrono_mail.pimstore import PimStore, new_id
from chrono_mail.pimstore.extract import extract
from chrono_mail.protocols.dav import DavClient, DavConflict, RemoteCollection, canonical_href, passing_failure
from chrono_mail.sync.davconflicts import (
    CHANGED_ON_SERVER,
    DELETED_ON_SERVER,
    REFUSED,
    SyncConflict,
    base_of,
    drop_base,
    forget,
    keep_base,
    new_conflict,
    open_conflict,
    record,
)
from chrono_mail.sync.davsync import CollectionState, DavState, ItemLink, address_for, digest_of, title_of, weigh

# --- Version ------------------------------------------------------------
__version__ = "davitems.py 1.10.278"

# --- Version history ----------------------------------------------------
# v1.10.278: The server's item is read with DavClient.read (the multiget first): Kerio answers a GET of an existing contact with 404, which made a
#            contact changed on both sides a false "deleted on the server" conflict
# v1.1.225: Initial: save, delete and move an item on the server first; the server's change meanwhile merged or a conflict; conflicts settled (D-194)

DONE: str = "done"  # the server has it, and so has this computer
WAITING: str = "waiting"  # made here; sent at the next synchronisation
CONFLICT: str = "conflict"  # made here; the server changed the same field meanwhile: the person settles it


class ServerRefused(ProtocolError):
    """The server said a lasting "no" to a change: nothing changed here."""


@dataclass(frozen=True, slots=True)
class Outcome:
    item: str  # the item here ("" when it is gone here)
    status: str
    conflict: SyncConflict | None = None


def on_server(info: CollectionInfo) -> bool:
    return bool(info.server_value("href"))


def remote_of(info: CollectionInfo) -> RemoteCollection:
    """Where a collection here is on its server, as much as is needed to write to it."""
    return RemoteCollection(canonical_href(info.server_value("href")), info.name, info.kind.value, info.server_value("component"), "", info.read_only, "", "")


def _link_of(collection_state: CollectionState, item: str) -> tuple[str, ItemLink] | None:
    return next(((href, link) for href, link in collection_state.links.items() if link.item_id == item), None)


def _agreed(store: PimStore, info: CollectionInfo, item: str, link: ItemLink | None) -> None:
    """Before an item is changed here: keep the version both sides agreed on, when it still is that version."""
    if link is None:
        return
    try:
        current = store.get(info.collection_id, item)
    except StorageError:
        return
    if digest_of(current) == link.digest:
        keep_base(store, info.collection_id, item, current)


class ItemGateway:
    """The server-first writes of one account's synchronised collections. One per action, used under the account's synchronisation lock."""

    def __init__(self, client: DavClient, store: PimStore, state_path: Path, durable: bool = True) -> None:
        self.client: DavClient = client
        self.store: PimStore = store
        self.state: DavState = DavState(state_path, durable)

    def _linked(self, info: CollectionInfo, item: str, href: str, etag: str, text: str) -> None:
        self.state.get(info.collection_id).links[href] = ItemLink(item, etag or self.client.etag_of(href), digest_of(text))
        drop_base(self.store, info.collection_id, item)
        self.state.save()

    def save(self, info: CollectionInfo, text: str, item: str | None = None) -> Outcome:
        """Write an item (new when item is None) on the server first, then here."""
        collection_state = self.state.get(info.collection_id)
        found = _link_of(collection_state, item) if item is not None else None
        conflict = open_conflict(self.store, info.collection_id, href=found[0] if found else "", item=item or "")
        if conflict is not None and item is not None:  # the person changed it again while it waits: the conflict is weighed again
            self.store.put(info.collection_id, text, item)
            return self._weigh(info, conflict, item, text)
        if found is not None and item is not None:
            href, link = found
            _agreed(self.store, info, item, link)
            try:
                etag = self.client.put(href, text, info.kind.value, etag=link.etag)
            except DavConflict:
                return self._changed_meanwhile(info, item, href, text)
            except ProtocolError as error:
                return self._not_sent(info, text, item, error)
            item = self.store.put(info.collection_id, text, item)
            self._linked(info, item, href, etag, text)
            return Outcome(item, DONE)
        remote = remote_of(info)
        item = item or new_id()  # its id here is chosen now: a name on the server may be made from it
        href = address_for(remote, extract(info.kind, text).uid, item)
        try:
            try:
                etag = self.client.put(href, text, info.kind.value, create=True)
            except DavConflict:  # that name is taken by another item
                href = address_for(remote, "", item)
                etag = self.client.put(href, text, info.kind.value, create=True)
        except ProtocolError as error:
            return self._not_sent(info, text, item, error)
        item = self.store.put(info.collection_id, text, item)
        self._linked(info, item, href, etag, text)
        return Outcome(item, DONE)

    def _not_sent(self, info: CollectionInfo, text: str, item: str | None, error: ProtocolError) -> Outcome:
        if not passing_failure(error):
            raise ServerRefused(str(error)) from None
        return Outcome(self.store.put(info.collection_id, text, item), WAITING)

    def _changed_meanwhile(self, info: CollectionInfo, item: str, href: str, text: str) -> Outcome:
        """The server's item is not the version agreed on: merged with ours field by field, or a conflict (or deleted there: a conflict)."""
        fetched = self.client.read(remote_of(info), href)
        self.store.put(info.collection_id, text, item)
        if fetched is None:
            kept = record(self.store, new_conflict(info.collection_id, href, item, info.kind.value, DELETED_ON_SERVER, title_of(info.kind, text), "", ""))
            return Outcome(item, CONFLICT, kept)
        etag, theirs = fetched
        return self._merge(info, item, href, text, etag, theirs)

    def _merge(self, info: CollectionInfo, item: str, href: str, ours: str, etag: str, theirs: str) -> Outcome:
        """Ours (kept here already) against the server's newer version: merged and sent, or a conflict (D-194)."""
        try:
            outcome = weigh(self.client, self.store, info, self.state.get(info.collection_id), href, item, ours, etag, theirs)
        except ProtocolError as error:
            if passing_failure(error):
                return Outcome(item, WAITING)
            kept = record(self.store, new_conflict(info.collection_id, href, item, info.kind.value, REFUSED, title_of(info.kind, ours), etag, theirs,
                                                   detail=str(error)))
            return Outcome(item, CONFLICT, kept)
        self.state.save()
        if outcome == "conflict":
            return Outcome(item, CONFLICT, open_conflict(self.store, info.collection_id, href=href))
        return Outcome(item, DONE if outcome == "done" else WAITING)

    def _weigh(self, info: CollectionInfo, conflict: SyncConflict, item: str, text: str) -> Outcome:
        """A conflict's item changed here again: compared again with the server's version it is about (nothing is sent while it is open)."""
        if not conflict.server_text:
            return Outcome(item, CONFLICT, record(self.store, replace(conflict, name=title_of(info.kind, text))))
        merged = merge(base_of(self.store, info.collection_id, item), text, conflict.server_text)
        if merged.text is not None and not merged.clashes:
            forget(self.store, conflict.conflict_id)
            return self._merge(info, item, conflict.href, merged.text, conflict.server_etag, conflict.server_text)
        kept = record(self.store, replace(conflict, name=title_of(info.kind, text), clashes=merged.clashes))
        return Outcome(item, CONFLICT, kept)

    def delete(self, info: CollectionInfo, item: str) -> Outcome:
        """Delete an item on the server first, then here."""
        collection_state = self.state.get(info.collection_id)
        found = _link_of(collection_state, item)
        conflict = open_conflict(self.store, info.collection_id, href=found[0] if found else "", item=item)
        if found is None or conflict is not None:
            self.store.delete(info.collection_id, item)  # never on the server (or settled through its conflict)
            if conflict is not None:
                record(self.store, replace(conflict, item="", reason=CHANGED_ON_SERVER if conflict.server_text else conflict.reason))
            return Outcome("", DONE if conflict is None else CONFLICT)
        href, link = found
        _agreed(self.store, info, item, link)
        try:
            self.client.delete(href, link.etag)
        except DavConflict:
            fetched = self.client.read(remote_of(info), href)
            self.store.delete(info.collection_id, item)
            if fetched is None:
                del collection_state.links[href]
                drop_base(self.store, info.collection_id, item)
                self.state.save()
                return Outcome("", DONE)
            etag, theirs = fetched
            kept = record(self.store, new_conflict(info.collection_id, href, "", info.kind.value, CHANGED_ON_SERVER, title_of(info.kind, theirs), etag, theirs))
            return Outcome("", CONFLICT, kept)
        except ProtocolError as error:
            if not passing_failure(error):
                raise ServerRefused(str(error)) from None
            self.store.delete(info.collection_id, item)
            return Outcome("", WAITING)  # the link stays: the next synchronisation deletes it on the server
        self.store.delete(info.collection_id, item)
        del collection_state.links[href]
        drop_base(self.store, info.collection_id, item)
        self.state.save()
        return Outcome("", DONE)

    def move(self, source: CollectionInfo, item: str, target: CollectionInfo) -> Outcome:
        """Move an item to another collection of the account: made in the new place on the server, taken from the old place, then moved here."""
        text = self.store.get(source.collection_id, item)
        source_state = self.state.get(source.collection_id)
        found = _link_of(source_state, item)
        remote = remote_of(target)
        href = address_for(remote, extract(target.kind, text).uid, item)
        try:
            etag = self.client.put(href, text, target.kind.value, create=True)
        except ProtocolError as error:
            if not passing_failure(error):
                raise ServerRefused(str(error)) from None
            return Outcome(self.store.move(source.collection_id, item, target.collection_id), WAITING)  # both places are settled at the next sync
        new_item = self.store.move(source.collection_id, item, target.collection_id)
        self._linked(target, new_item, href, etag, text)
        if found is not None:
            old_href, link = found
            try:
                self.client.delete(old_href, link.etag)
                del source_state.links[old_href]
            except ProtocolError:
                pass  # the old place stays linked to a missing item: the next synchronisation deletes it there (or makes it a conflict)
            drop_base(self.store, source.collection_id, item)
            self.state.save()
        return Outcome(new_item, DONE)

    def settle(self, info: CollectionInfo, conflict: SyncConflict, side: str = "", choices: dict[str, str] | None = None) -> Outcome:
        """Settle a conflict: SERVER writes the server's version here; LOCAL sends Chrono Mail's to the server; choices pick per field. The server
        changed the item again meanwhile: the conflict is weighed again and stays."""
        item = conflict.item
        ours = self.store.get(info.collection_id, item) if item else None
        collection_state = self.state.get(info.collection_id)
        if side == SERVER:
            if conflict.server_text:
                item = self.store.put(info.collection_id, conflict.server_text, item or None)
                self._linked(info, item, conflict.href, conflict.server_etag, conflict.server_text)
            else:
                if item:
                    self.store.delete(info.collection_id, item)
                collection_state.links.pop(conflict.href, None)
                drop_base(self.store, info.collection_id, item)
                self.state.save()
                item = ""
            forget(self.store, conflict.conflict_id)
            return Outcome(item, DONE)
        if choices and ours is not None and conflict.server_text:
            merged = merge(base_of(self.store, info.collection_id, item), ours, conflict.server_text, choices)
            if merged.text is None:
                return Outcome(item, CONFLICT, conflict)  # a field was not chosen
            ours = merged.text
        try:
            if ours is None:  # deleted here: deleted on the server too
                self.client.delete(conflict.href, conflict.server_etag)
                collection_state.links.pop(conflict.href, None)
                self.state.save()
            elif not conflict.server_text:  # deleted on the server: made there again
                etag = self.client.put(conflict.href, ours, info.kind.value, create=True)
                self.store.put(info.collection_id, ours, item)
                self._linked(info, item, conflict.href, etag, ours)
            else:
                etag = self.client.put(conflict.href, ours, info.kind.value, etag=conflict.server_etag)
                self.store.put(info.collection_id, ours, item)
                self._linked(info, item, conflict.href, etag, ours)
        except DavConflict:
            fetched = self.client.read(remote_of(info), conflict.href)
            changed = replace(conflict, server_etag=fetched[0] if fetched else "", server_text=fetched[1] if fetched else "")
            return Outcome(item, CONFLICT, record(self.store, changed))
        forget(self.store, conflict.conflict_id)
        drop_base(self.store, info.collection_id, item)
        return Outcome(item, DONE)


__all__ = ["CONFLICT", "DONE", "LOCAL", "SERVER", "WAITING", "ItemGateway", "Outcome", "ServerRefused", "on_server", "remote_of"]
