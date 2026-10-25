from collections.abc import Callable
from dataclasses import dataclass, field, replace

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.groups import contacts_of_book, group_addresses, groups_of_book, member_uids, read_card, uid_key
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine
from chrono_mail.pim.contact import Contact, GroupForm, bare_uid, contact_to_text, member_reference
from chrono_mail.pimstore import IndexedItem, PimStore

# --- Version ------------------------------------------------------------
__version__ = "commands_groups.py 2.1.311"

# --- Version history ----------------------------------------------------
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.10.277: Initial contact groups (contact-group.*, R-39): list, create, rename, delete, add and remove members, the addresses of a group; every change on the
#            server first like every card (D-193)

MAXIMUM_NAME: int = 200


@dataclass(frozen=True, slots=True)
class GroupRow:
    item: str
    account: str
    collection: str
    name: str
    members: int  # the contacts of the address book it names
    others: int  # the members without a contact here: plain addresses, and contacts the address book does not have
    read_only: bool  # its address book is read only


@dataclass(frozen=True, slots=True)
class GroupListParameters:
    collection: str | None = field(default=None, metadata=option("Only the groups of this address book (default: every address book)"))


def _row(account: str, read_only: bool, group: IndexedItem, uids: set[str]) -> GroupRow:
    wanted = member_uids(group)
    found = len(wanted & uids)
    return GroupRow(group.item_id, account, group.collection, group.title, found, len(group.members) - found, read_only)


def _rows_of_book(account: str, store: PimStore, collection: str, read_only: bool) -> list[GroupRow]:
    uids = {bare_uid(contact.uid) for contact in contacts_of_book(store, collection) if contact.uid.strip()}
    return [_row(account, read_only, group, uids) for group in groups_of_book(store, collection)]


def group_list(engine: Engine, parameters: GroupListParameters) -> tuple[GroupRow, ...]:
    """The groups of the address books, by name, each with its number of members. Read from the index alone."""
    rows: list[GroupRow] = []
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for info in store.collections(CollectionKind.CONTACTS):
                if parameters.collection is None or info.collection_id == parameters.collection:
                    rows.extend(_rows_of_book(account, store, info.collection_id, info.read_only))
    return tuple(rows)


CONTACT_GROUP_LIST: TypedCommand[GroupListParameters, tuple[GroupRow, ...]] = TypedCommand("contact-group.list", GroupListParameters, tuple)  # every element is a GroupRow


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > MAXIMUM_NAME:
        raise UsageError(f"A group needs a name of up to {MAXIMUM_NAME} characters")
    return cleaned


def _check_free(store: PimStore, collection: str, name: str, item: str = "") -> None:
    if any(group.title.casefold() == name.casefold() and group.item_id != item for group in groups_of_book(store, collection)):
        raise UsageError(f"This address book has a group “{name}” already")


def _form_of_book(store: PimStore, collection: str) -> GroupForm:
    """The form a new group is written in: the one the address book's groups use, else Apple's (Kerio takes it as a group of its own)."""
    for group in groups_of_book(store, collection):
        card = read_card(store, collection, group.item_id)
        if card is not None and card.group_form is not None:
            return card.group_form
    return GroupForm.APPLE


def _row_of(engine: Engine, item: str) -> GroupRow:
    with located(engine, item, CollectionKind.CONTACTS) as found:
        row = next((row for row in _rows_of_book(found.account, found.store, found.collection.collection_id, found.collection.read_only) if row.item == item), None)
    if row is None:
        raise UsageError("That is not a group")
    return row


@dataclass(frozen=True, slots=True)
class GroupCreateParameters:
    collection: str = field(metadata=option("The address book id (see collection.list)"))
    name: str = field(metadata=option("The name of the group"))
    contacts: tuple[str, ...] = field(default=(), metadata=option("Contacts of that address book to put in it (repeat)"))


def group_create(engine: Engine, parameters: GroupCreateParameters) -> GroupRow:
    """A new group, made on the server first when the address book has one."""
    name = _clean_name(parameters.name)
    try:
        with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, store, info), item_writer(engine, account, store) as writer:
            if info.read_only:
                raise UsageError("This address book is read-only")
            _check_free(store, info.collection_id, name)
            form = _form_of_book(store, info.collection_id)
            references = _references(store, info.collection_id, parameters.contacts)
            card = Contact(display_name=name, family_name=name if form is GroupForm.APPLE else "", group_form=form, members=references).with_uid()
            item = writer.put(info, contact_to_text(card))
    except StorageError as error:
        raise UsageError(str(error)) from None
    return _row_of(engine, item)


def _references(store: PimStore, collection: str, contacts: tuple[str, ...]) -> tuple[str, ...]:
    """How a group names these contacts. A member must be a contact of the group's own address book (the standard ties them by uid there)."""
    references: list[str] = []
    for item in contacts:
        indexed = store.index.get(item)
        if indexed is None or indexed.kind is not CollectionKind.CONTACTS or indexed.is_group:
            raise UsageError("Only contacts can be put in a group")
        if indexed.collection != collection:
            raise UsageError("A group holds contacts of its own address book only")
        if not indexed.uid.strip():
            raise UsageError(f"“{indexed.title}” has no id, so no group can name it")
        references.append(member_reference(indexed.uid))
    return tuple(dict.fromkeys(references))


@dataclass(frozen=True, slots=True)
class GroupRenameParameters:
    item: str = field(metadata=option("The group id (see contact-group.list)"))
    name: str = field(metadata=option("The new name"))


def _change(engine: Engine, item: str, change: Callable[[PimStore, str, Contact], Contact]) -> GroupRow:
    """A group's card changed by `change` (given the store, the address book's id and the card), on the server first."""
    try:
        with located(engine, item, CollectionKind.CONTACTS) as found, item_writer(engine, found.account, found.store) as writer:
            if not found.item.is_group:
                raise UsageError("That is not a group")
            if found.collection.read_only:
                raise UsageError("This address book is read-only")
            card = read_card(found.store, found.collection.collection_id, item)
            if card is None or card.group_form is None:
                raise UsageError("This group cannot be read, so it cannot be changed")
            changed = change(found.store, found.collection.collection_id, card)
            if changed != card:
                writer.put(found.collection, contact_to_text(changed), item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return _row_of(engine, item)


def group_rename(engine: Engine, parameters: GroupRenameParameters) -> GroupRow:
    name = _clean_name(parameters.name)

    def renamed(store: PimStore, collection: str, card: Contact) -> Contact:
        _check_free(store, collection, name, parameters.item)
        family = name if card.family_name == card.display_name or (card.group_form is GroupForm.APPLE and not card.given_name) else card.family_name
        return replace(card, display_name=name, family_name=family)

    return _change(engine, parameters.item, renamed)


@dataclass(frozen=True, slots=True)
class GroupMembersParameters:
    item: str = field(metadata=option("The group id (see contact-group.list)"))
    contacts: tuple[str, ...] = field(metadata=option("Contacts of the group's address book (repeat)"))


def group_add_members(engine: Engine, parameters: GroupMembersParameters) -> GroupRow:
    """Contacts put in a group (one that is in it already stays once)."""

    def added(store: PimStore, collection: str, card: Contact) -> Contact:
        have = {uid_key(reference) for reference in card.members}
        new = tuple(reference for reference in _references(store, collection, parameters.contacts) if uid_key(reference) not in have)
        return replace(card, members=card.members + new) if new else card

    return _change(engine, parameters.item, added)


def group_remove_members(engine: Engine, parameters: GroupMembersParameters) -> GroupRow:
    """Contacts taken out of a group; the contacts themselves stay."""

    def removed(store: PimStore, collection: str, card: Contact) -> Contact:
        gone: set[str] = set()
        for item in parameters.contacts:
            indexed = store.index.get(item)
            if indexed is not None and indexed.uid.strip():
                gone.add(uid_key(indexed.uid))
        return replace(card, members=tuple(reference for reference in card.members if uid_key(reference) not in gone))

    return _change(engine, parameters.item, removed)


@dataclass(frozen=True, slots=True)
class GroupItem:
    item: str = field(metadata=option("The group id (see contact-group.list)"))


def group_delete(engine: Engine, parameters: GroupItem) -> GroupItem:
    """Delete a group: its card only, never its members. The file is kept in the store's .deleted directory for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found, item_writer(engine, found.account, found.store) as writer:
            if not found.item.is_group:
                raise UsageError("That is not a group")
            if found.collection.read_only:
                raise UsageError("This address book is read-only")
            writer.delete(found.collection, parameters.item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return parameters


@dataclass(frozen=True, slots=True)
class GroupAddresses:
    name: str
    addresses: tuple[str, ...]  # as they go into an address field: Name <address>
    without_address: int  # members a message cannot go to (no address, or not in the address book)


def group_addresses_command(engine: Engine, parameters: GroupItem) -> GroupAddresses:
    """What a message to the group goes to: every member's preferred address."""
    with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
        if not found.item.is_group:
            raise UsageError("That is not a group")
        addresses, without = group_addresses(found.store, found.item)
        return GroupAddresses(found.item.title, tuple(addresses), without)


CONTACT_GROUP_CREATE: TypedCommand[GroupCreateParameters, GroupRow] = TypedCommand("contact-group.create", GroupCreateParameters, GroupRow)
CONTACT_GROUP_RENAME: TypedCommand[GroupRenameParameters, GroupRow] = TypedCommand("contact-group.rename", GroupRenameParameters, GroupRow)
CONTACT_GROUP_DELETE: TypedCommand[GroupItem, GroupItem] = TypedCommand("contact-group.delete", GroupItem, GroupItem)
CONTACT_GROUP_ADD_MEMBERS: TypedCommand[GroupMembersParameters, GroupRow] = TypedCommand("contact-group.add-members", GroupMembersParameters, GroupRow)
CONTACT_GROUP_REMOVE_MEMBERS: TypedCommand[GroupMembersParameters, GroupRow] = TypedCommand("contact-group.remove-members", GroupMembersParameters, GroupRow)
CONTACT_GROUP_ADDRESSES: TypedCommand[GroupItem, GroupAddresses] = TypedCommand("contact-group.addresses", GroupItem, GroupAddresses)


def register(registry: CommandRegistry) -> None:
    registry.register(CONTACT_GROUP_LIST.id, "List the contact groups of the address books", GroupListParameters, group_list, doing="Reading the contact groups")
    registry.register(CONTACT_GROUP_CREATE.id, "Make a contact group", GroupCreateParameters, group_create, doing="Making a contact group")
    registry.register(CONTACT_GROUP_RENAME.id, "Rename a contact group", GroupRenameParameters, group_rename, doing="Renaming a contact group")
    registry.register(CONTACT_GROUP_DELETE.id, "Delete a contact group (its members stay)", GroupItem, group_delete, destructive=True, doing="Deleting a contact group")
    registry.register(CONTACT_GROUP_ADD_MEMBERS.id, "Put contacts in a group", GroupMembersParameters, group_add_members, doing="Putting contacts in a group")
    registry.register(CONTACT_GROUP_REMOVE_MEMBERS.id, "Take contacts out of a group (they stay in the address book)", GroupMembersParameters, group_remove_members, doing="Taking contacts out of a group")
    registry.register(CONTACT_GROUP_ADDRESSES.id, "The addresses a message to a group goes to", GroupItem, group_addresses_command, doing="Reading the addresses of a group")
