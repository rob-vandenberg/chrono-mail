"""Contact groups (R-39): what the commands of contacts and of groups share. A group is a card of its address book (vCard 4's KIND:group or Apple's
form) that names its members by their uid (urn:uuid:<uid>) or by an address (mailto:); it is kept, sent and followed like every card (D-193)."""

from collections.abc import Callable
from dataclasses import replace
from email.utils import formataddr
from typing import Protocol

from chrono_mail.core import CollectionInfo, CollectionKind, ProtocolError, StorageError
from chrono_mail.pim.contact import Contact, bare_uid, contact_to_text, member_reference, member_uid, parse_contacts
from chrono_mail.pimstore import IndexedItem, ItemQuery, PimStore

# --- Version ------------------------------------------------------------
__version__ = "groups.py 4.3.498"

# --- Version history ----------------------------------------------------
# v4.3.498: leave_groups writes through any CardWriter (also the copy of an interrupted Keep both that goes); join_groups: a contact's copy (Keep both, B-30) put in every group of its address book the original is in (the owner)
# v1.10.277: Initial: the groups of an address book, their members, a contact taken out of its groups, a group's addresses (R-39)

EVERY: int = 10**6
UID_KEY: str = "uid:"
MAILTO_KEY: str = "mailto:"


def groups_of_book(store: PimStore, collection: str) -> list[IndexedItem]:
    """The groups of one address book, by name."""
    found = store.index.query(ItemQuery(CollectionKind.CONTACTS, (collection,), groups=True, limit=EVERY))
    return sorted(found, key=lambda group: (group.title.casefold(), group.item_id))


def contacts_of_book(store: PimStore, collection: str) -> list[IndexedItem]:
    """Every contact of one address book (no groups), in the order of the list."""
    return store.index.query(ItemQuery(CollectionKind.CONTACTS, (collection,), groups=False, limit=EVERY))


def member_uids(group: IndexedItem) -> set[str]:
    """The uids (bare, see pim.contact.bare_uid) of the contacts a group names."""
    return {member[len(UID_KEY):] for member in group.members if member.startswith(UID_KEY)}


def uid_key(uid: str) -> str:
    """A contact's uid as the index keeps a member (see pimstore.extract)."""
    return UID_KEY + member_uid(uid) if uid.strip() else ""


def read_card(store: PimStore, collection: str, item: str) -> Contact | None:
    """The card of an item, or None when it cannot be read."""
    try:
        contacts = parse_contacts(store.get(collection, item))
    except StorageError:
        return None
    return contacts[0] if contacts else None


class CardWriter(Protocol):
    """What writes a group's card: on its server first like any card (engine.pim_server.ItemWriter, or Sync conflicts' own writer)."""

    def put(self, info: CollectionInfo, text: str, item: str | None = None) -> str: ...


def leave_groups(writer: CardWriter, store: PimStore, info: CollectionInfo, uid: str) -> int:
    """A contact that leaves an address book (deleted, moved away) is taken out of every group of it, each group changed on its server first like
    any card. Returns how many groups were changed."""
    key = uid_key(uid)
    if not key:
        return 0
    changed = 0
    for group in groups_of_book(store, info.collection_id):
        if key not in group.members:
            continue
        card = read_card(store, info.collection_id, group.item_id)
        if card is None:
            continue
        kept = tuple(reference for reference in card.members if uid_key(reference) != key)
        if kept != card.members:
            writer.put(info, contact_to_text(replace(card, members=kept)), group.item_id)
            changed += 1
    return changed


REFUSED_GROUP: str = "refused"  # join_groups: a group whose server said a lasting "no"


def join_groups(save: Callable[[CollectionInfo, str, str], str], store: PimStore, info: CollectionInfo, uid: str, new_uid: str) -> list[str]:
    """A contact's copy (Keep both, B-30) put in every group of its address book that has the contact of `uid`, each group saved through `save`
    (on its server first, like any card; it returns how it went: done, waiting). Returns that for each group changed, REFUSED_GROUP for one
    the server would not take or that could not be stored (that group stays as it was)."""
    key, new_key = uid_key(uid), uid_key(new_uid)
    if not key or not new_key:
        return []
    statuses: list[str] = []
    for group in groups_of_book(store, info.collection_id):
        if key not in group.members or new_key in group.members:
            continue
        card = read_card(store, info.collection_id, group.item_id)
        if card is None:
            continue
        try:
            statuses.append(save(info, contact_to_text(replace(card, members=(*card.members, member_reference(new_uid)))), group.item_id))
        except (ProtocolError, StorageError):  # the server's lasting "no", or the group could not be stored here: it stays as it was
            statuses.append(REFUSED_GROUP)
    return statuses


def preferred_address(contact: Contact) -> str:
    """The address a message to this contact goes to: the preferred one, else the first."""
    chosen = next((entry for entry in contact.emails if entry.preferred and entry.value), None) or next((entry for entry in contact.emails if entry.value), None)
    return chosen.value if chosen is not None else ""


def group_addresses(store: PimStore, group: IndexedItem) -> tuple[list[str], int]:
    """What a message to a group goes to: every member's preferred address as Name <address>, and the plain addresses the group names, each once;
    and how many members have no address (a contact without one, or one this address book does not have)."""
    addresses: dict[str, str] = {}
    without = 0
    wanted = member_uids(group)
    found: set[str] = set()
    for contact in contacts_of_book(store, group.collection):
        uid = bare_uid(contact.uid)
        if not uid or uid not in wanted:
            continue
        card = read_card(store, group.collection, contact.item_id)
        address = preferred_address(card) if card is not None else ""
        found.add(uid)
        if not address:
            without += 1
            continue
        addresses.setdefault(address.lower(), formataddr((card.full_name(), address)) if card is not None and card.full_name() != address else address)
    without += len(wanted - found)
    for member in group.members:
        if member.startswith(MAILTO_KEY) and member[len(MAILTO_KEY):]:
            addresses.setdefault(member[len(MAILTO_KEY):], member[len(MAILTO_KEY):])
    return list(addresses.values()), without
