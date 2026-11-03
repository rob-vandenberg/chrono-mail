"""Changes the person makes to an item of a server's collection, done on the server first and here when it took them (D-193, D-194). Offline (or the
server failing for now) the change is made here and sent at the next synchronisation, with the version both sides agreed on kept beside it. The
server changed the item meanwhile: merged field by field when nothing clashes, else a sync conflict. A lasting "no" of the server is an error and
nothing changes here (the data stays the same on both sides)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from chrono_mail.core import CollectionInfo, CollectionKind, ProtocolError, StorageError
from chrono_mail.pim.contact import UUID_PREFIX
from chrono_mail.pim.ical import new_uid
from chrono_mail.pim.merge import LOCAL, SERVER, copied, merge, reading
from chrono_mail.pimstore import PimStore, new_id
from chrono_mail.pimstore.extract import extract
from chrono_mail.protocols.dav import DavClient, DavConflict, RemoteCollection, canonical_href, passing_failure
from chrono_mail.sync.davconflicts import (
    CHANGED_ON_SERVER,
    CLASH,
    DELETED_ON_SERVER,
    MADE,
    REFUSED,
    TAKING,
    SyncConflict,
    base_of,
    drop_base,
    forget,
    keep_base,
    new_conflict,
    open_conflict,
    record,
    unfinished,
)
from chrono_mail.sync.davsync import CollectionState, DavState, ItemLink, address_for, digest_of, title_of, weigh

# --- Version ------------------------------------------------------------
__version__ = "davitems.py 4.3.498"

# --- Version history ----------------------------------------------------
# v4.3.498: a contact's change reads the server's version first: a server that ignores If-Match (Kerio) never has a change there written
#           over; the copy Keep both writes is read back and kept as the server stored it (its fingerprint too), so a server's own
#           rewriting is no change of the person's; a copy that reads otherwise, also from a write not finished, is the person's;
#           the copy an interrupted Keep both left goes when the conflict is settled another way, unless the person changed it (drop_copy);
#           Keep both (B-30, the owner): Chrono Mail's version made a separate item on the server first, the original takes the server's;
#           safe to try again after an interruption at any step: the copy is kept in the conflict first, found again, never made twice
# v4.2.496: Use Chrono Mail on a conflict changed on both sides settles every difference field by field: what is not shown keeps the
#           server's changes (D-308), never a whole side's text sent over them
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
    copy: str = ""  # Keep both: the copy here of Chrono Mail's version (its status is `copied`)
    copied: str = ""


def on_server(info: CollectionInfo) -> bool:
    return bool(info.server_value("href"))


def remote_of(info: CollectionInfo) -> RemoteCollection:
    """Where a collection here is on its server, as much as is needed to write to it."""
    return RemoteCollection(canonical_href(info.server_value("href")), info.name, info.kind.value, info.server_value("component"), "", info.read_only, "", "")


def written_digest(text: str) -> str:
    """A copy as Keep both compares it: as a person sees it (merge.reading), so a server that writes it its own way (its PRODID, other line
    ends or folding) leaves it the same; only what a person sees or sets changes it."""
    seen = reading(text)
    return digest_of(seen if seen is not None else text)


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
            if conflict.copy_stage == TAKING and digest_of(self.store.get(info.collection_id, item)) == conflict.copy_source:
                # a Keep both that had not yet given the original the server's version: the person's newer version goes into the copy first
                conflict = record(self.store, replace(conflict, copy_stage="", copy_taken=""), carried=False)
            self.store.put(info.collection_id, text, item)
            return self._weigh(info, conflict, item, text)
        if found is not None and item is not None:
            href, link = found
            _agreed(self.store, info, item, link)
            if info.kind == CollectionKind.CONTACTS:  # a server may take a contact's PUT with an old version without a word (Kerio ignores
                # If-Match there, measured 2026-11-03): its version is read first, so a change on the server meanwhile is never written over
                try:
                    current = self.client.read(remote_of(info), href)
                except ProtocolError as error:
                    if passing_failure(error):
                        return self._not_sent(info, text, item, error)
                    current = (link.etag, "")  # the read refused, the write may not be: sent as before, its If-Match the only check
                if current is None or (link.etag and current[0] != link.etag):  # an unknown version: sent as before
                    return self._against(info, item, href, text, current)
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
        return self._against(info, item, href, text, self.client.read(remote_of(info), href))

    def _against(self, info: CollectionInfo, item: str, href: str, text: str, fetched: tuple[str, str] | None) -> Outcome:
        """Ours against the server's item as read (None: deleted there): merged and sent, or a conflict."""
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
        if merged.text is not None and not merged.clashes and not unfinished(conflict):  # a Keep both under way waits for the person
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
        if side == LOCAL and conflict.reason == CLASH and ours is not None and conflict.server_text:  # changed on both sides: Chrono Mail's every
            # difference, field by field, so what is not shown keeps the server's changes (D-308); Use Server data stays the server's whole version,
            # the way out that always works (no network, a server that refuses); a refusal or a delete stays one side's whole version
            open_clashes = merge(base_of(self.store, info.collection_id, item), ours, conflict.server_text).clashes
            merged = merge(base_of(self.store, info.collection_id, item), ours, conflict.server_text, {clash.key: LOCAL for clash in open_clashes})
            if merged.text is not None:
                side, choices, ours = "", None, merged.text  # sent as merged
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

    def keep_both(self, info: CollectionInfo, conflict: SyncConflict, joined: Callable[[str, str], None] | None = None) -> Outcome:
        """Keep both (B-30, the owner), in this order: Chrono Mail's version as it is now becomes an item of its own, with a uid of its own, made
        (or brought up to date) on the server first (offline: here, sent at the next synchronisation); `joined` (the original's id here, the
        copy's) puts the copy where the original is (its groups); then the item here takes the server's version, which keeps its place there
        (nothing is sent for it); last the conflict is forgotten. Only for an item changed on both sides. A lasting "no" of the server raises
        ServerRefused and nothing more changes here.

        Safe to try again after an interruption at any step, also with a synchronisation, the check at start or a change on either side in
        between (AGENTS.md 4.1.4). The conflict keeps where it is (davconflicts.record carries it over): the copy's uid before anything is made, so
        the copy is found by it and never made twice; TAKING, with the text the copy was written from, before the original is touched. From then
        on the copy is the one carrier of Chrono Mail's version and is never written from the original again: the original takes the server's
        version only while it is still that text or a version of the server's (the one it was given, or the newest); changed since (a synchronisation merged into it, or the person),
        it is never overwritten: the copy is done, and the original stays a conflict of its own (CONFLICT, with `copy`)."""
        item = conflict.item
        ours = self.store.get(info.collection_id, item) if item else None
        if conflict.reason != CLASH or ours is None or not conflict.server_text:
            return Outcome(item, CONFLICT, conflict)
        if conflict.copy_stage == TAKING:  # the copy and its groups were done by an earlier try
            found = self.store.index.find_uid(info.collection_id, conflict.copy_uid)
            copy, made = (found.item_id if found is not None else conflict.copy), DONE
            if joined is not None and found is not None:
                joined(item, copy)  # done already by that try: changes nothing, or puts the copy where it is still missing
        else:
            if not conflict.copy_uid:  # the first try: the copy's uid chosen and kept with the conflict before anything is made
                old = extract(info.kind, ours).uid.strip()
                if info.kind == CollectionKind.CONTACTS:  # a card's uid in the form of the original (urn:uuid: or bare)
                    uid = (UUID_PREFIX if old.lower().startswith(UUID_PREFIX) else "") + str(uuid.uuid4())
                else:
                    uid = new_uid()
                conflict = record(self.store, replace(conflict, copy=new_id(), copy_uid=uid))
            text = copied(ours, conflict.copy_uid)  # Chrono Mail's version as it is at this try
            if text is None:
                return Outcome(item, CONFLICT, conflict)  # a text that cannot be read cannot be copied: a side is chosen instead
            intended = replace(conflict, copy_written=",".join(filter(None, (conflict.copy_written, written_digest(text)))),  # kept before the copy
                               copy_pending=digest_of(ours))  # is written: what it held and what it gets, both Keep both's until the write is done

            def writing() -> None:
                record(self.store, intended)

            copy, made, written = self._made(info, conflict, text, writing)
            if written:  # what Keep both wrote to the copy now, and from which text of the original
                conflict = record(self.store, replace(conflict, copy_written=written, copy_source=digest_of(ours), copy_pending=""))
            elif not conflict.copy_source and conflict.copy_pending == digest_of(ours):  # the copy (the person's now) came from its one write,
                conflict = record(self.store, replace(conflict, copy_source=digest_of(ours), copy_pending=""))  # which was of this very text
            if joined is not None:
                joined(item, copy)
            if digest_of(ours) == conflict.copy_source:  # the copy has the original's text: from now on the copy is its one carrier
                conflict = record(self.store, replace(conflict, copy_stage=TAKING, copy_taken=digest_of(conflict.server_text)))
                # kept before the original is touched
        link = self.state.get(info.collection_id).links.get(conflict.href)
        if link is not None and link.digest == digest_of(ours) and digest_of(ours) != conflict.copy_source:  # a synchronisation since put it
            forget(self.store, conflict.conflict_id)  # in step with the server: the original is the server's, nothing is left to do
            return Outcome(item, DONE, copy=copy, copied=made)
        if digest_of(ours) not in (conflict.copy_source, conflict.copy_taken, digest_of(conflict.server_text)):  # changed since (a synchronisation
            # merged into it, or the person): the copy keeps Chrono Mail's version; the original is never overwritten, it stays a conflict
            merged = merge(base_of(self.store, info.collection_id, item), ours, conflict.server_text)
            clashes = merged.clashes
            done = replace(conflict, clashes=clashes, copy_made=MADE)  # the copy stays known (never a second one), and the person's: they were
            # told it is made; the person settles the original
            if merged.text is not None and not clashes:  # the original merges with the server's: merged here, the next synchronisation sends it
                self.store.put(info.collection_id, merged.text, item)  # with the server's changes in it: never ours alone over them
                forget(self.store, conflict.conflict_id)
                return Outcome(item, DONE, copy=copy, copied=made)
            return Outcome(item, CONFLICT, record(self.store, done), copy=copy, copied=made)
        original = self.store.put(info.collection_id, conflict.server_text, item)  # the copy's text, or a server's version: the server's now
        etag = conflict.server_etag
        if not etag:
            try:
                etag = self.client.etag_of(conflict.href)
            except ProtocolError:
                etag = ""  # offline: the next synchronisation compares the server's version with this one
        self.state.get(info.collection_id).links[conflict.href] = ItemLink(original, etag, digest_of(conflict.server_text))
        drop_base(self.store, info.collection_id, original)
        self.state.save()
        forget(self.store, conflict.conflict_id)
        return Outcome(original, DONE, copy=copy, copied=made)

    def drop_copy(self, info: CollectionInfo, conflict: SyncConflict) -> tuple[str, str]:
        """The copy an interrupted Keep both left, removed when the person settles the conflict another way: on the server first (offline: here,
        the next synchronisation deletes it there), like any delete. A copy the person changed since Keep both last wrote it (it no longer
        reads as that, merge.reading), or one Keep both told the person it made (MADE), stays. Returns the copy's uid and DONE or WAITING when
        it was removed, ("", "") when there was none or it stays. A server that cannot be reached for a copy not kept here: it stays (nothing
        is known of it here)."""
        if not conflict.copy_uid or conflict.copy_made == MADE:
            return "", ""
        written = [digest for digest in conflict.copy_written.split(",") if digest]
        collection, uid = info.collection_id, conflict.copy_uid
        here = self.store.index.find_uid(collection, uid)
        if here is not None:
            if written_digest(self.store.get(collection, here.item_id)) not in written:  # also from a write not finished: only what reads as
                return "", ""  # Keep both wrote it (or the server stored it, read back) is Keep both's; else the person's now, never lost (OR-2)
            return uid, self.delete(info, here.item_id).status
        remote = remote_of(info)
        href = address_for(remote, uid, conflict.copy)
        try:
            found = self.client.read(remote, href)  # made there by a try that ended before it was kept here
        except ProtocolError as error:
            if not passing_failure(error):
                raise ServerRefused(str(error)) from None
            return "", ""
        if found is None or extract(info.kind, found[1]).uid != uid or written_digest(found[1]) not in written:
            return "", ""  # it reads otherwise than Keep both wrote it: the person's (another computer), it stays
        self.store.put(collection, found[1], conflict.copy)
        self._linked(info, conflict.copy, href, found[0], found[1])
        return uid, self.delete(info, conflict.copy).status

    def _sent(self, info: CollectionInfo, text: str, item: str) -> tuple[str, str]:
        """The copy of Keep both written on the server first, then read back: what the server stored is the copy here and its fingerprint, so a
        server that writes it its own way (its own parameters, lines or reminders) leaves it Keep both's (for every server). Returns the status
        and the digest (written_digest) of what the copy now is. Not sent (offline) or not read back: what was written, as before."""
        status = self.save(info, text, item).status
        found = _link_of(self.state.get(info.collection_id), item) if status == DONE else None
        if found is None:
            return status, written_digest(text)
        try:
            back = self.client.read(remote_of(info), found[0])
        except ProtocolError:
            return status, written_digest(text)  # it is on the server; read back at the next synchronisation, as any item
        if back is None or extract(info.kind, back[1]).uid != extract(info.kind, text).uid:
            return status, written_digest(text)
        self.store.put(info.collection_id, back[1], item)
        self._linked(info, item, found[0], back[0], back[1])
        return status, written_digest(back[1])

    def _made(self, info: CollectionInfo, conflict: SyncConflict, text: str, writing: Callable[[], None]) -> tuple[str, str, str]:
        """The copy of Keep both made with this text, or found where an earlier try left it and brought up to date: its id here; DONE when the
        server has it, WAITING when it waits here for the next synchronisation; and the digest of what was written to it now ("" when it was
        left as it is). Found here by its uid (also when a synchronisation brought it under an id of its own), else on the server at its place
        (a try the server answered that ended before it was kept here). A copy the person changed since Keep both last wrote it is theirs: it
        is never overwritten (then the original, changed since too, stays a conflict of its own). `writing` keeps what is about to be written
        before each write, so a try that ends half-way leaves a copy that is still known as Keep both's."""
        collection, uid, copy = info.collection_id, conflict.copy_uid, conflict.copy
        state = self.state.get(collection)
        here = self.store.index.find_uid(collection, uid)
        if here is not None:
            current = written_digest(self.store.get(collection, here.item_id))
            linked = _link_of(state, here.item_id) is not None
            status = DONE if linked else WAITING
            if current == written_digest(text):
                return here.item_id, status, current
            if current not in conflict.copy_written.split(","):  # changed since Keep both wrote it (or not known): the person's, never overwritten
                return here.item_id, status, ""
            writing()
            if linked:  # the original changed since: the copy gets the newer version, on the server first
                return here.item_id, *self._sent(info, text, here.item_id)
            self.store.put(collection, text, here.item_id)
            return here.item_id, WAITING, written_digest(text)  # still waits: the next synchronisation sends the newer version
        remote = remote_of(info)
        href = address_for(remote, uid, copy)
        try:
            found = self.client.read(remote, href)
        except ProtocolError as error:
            if not passing_failure(error):
                raise ServerRefused(str(error)) from None
            found = None
        if found is not None and extract(info.kind, found[1]).uid == uid:  # made there by an earlier try: kept here as it is there
            self.store.put(collection, found[1], copy)
            self._linked(info, copy, href, found[0], found[1])
            there = written_digest(found[1])
            if there == written_digest(text):
                return copy, DONE, there
            if there not in conflict.copy_written.split(","):  # changed there since (or not known as Keep both's): the person's, never overwritten
                return copy, DONE, ""
            writing()
            return copy, *self._sent(info, text, copy)
        writing()
        return copy, *self._sent(info, text, copy)


__all__ = ["CONFLICT", "DONE", "LOCAL", "SERVER", "WAITING", "ItemGateway", "Outcome", "ServerRefused", "on_server", "remote_of"]
