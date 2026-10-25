"""Contact groups (R-39): what the commands of contacts and of groups share. A group is a card of its address book (vCard 4's KIND:group or Apple's
form) that names its members by their uid (urn:uuid:<uid>) or by an address (mailto:); it is kept, sent and followed like every card (D-193)."""

from dataclasses import replace
from email.utils import formataddr

from chrono_mail.core import CollectionInfo, CollectionKind, StorageError
from chrono_mail.engine.pim_server import ItemWriter
from chrono_mail.pim.contact import Contact, bare_uid, contact_to_text, member_uid, parse_contacts
from chrono_mail.pimstore import IndexedItem, ItemQuery, PimStore

# --- Version ------------------------------------------------------------
__version__ = "groups.py 1.10.277"

# --- Version history ----------------------------------------------------
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


def leave_groups(writer: ItemWriter, store: PimStore, info: CollectionInfo, uid: str) -> int:
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
