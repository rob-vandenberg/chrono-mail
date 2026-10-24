import base64
from dataclasses import dataclass, field, replace

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.groups import groups_of_book, leave_groups, member_uids, uid_key
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.pim_server import item_writer
from chrono_mail.engine.registry import CommandRegistry, TypedCommand, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.contact import Address, Contact, Labeled, PartialDate, bare_uid, contact_to_text, parse_contacts
from chrono_mail.pimstore import IndexedItem, ItemQuery, PimStore

# --- Version ------------------------------------------------------------
__version__ = "commands_addressbook.py 3.2.342"

# --- Version history ----------------------------------------------------
# v3.2.342: an entry given again with the same value and first kind keeps its other kinds, preferred mark and details (RV-20)
# v2.1.311: every command says what it is doing (doing=): the busy bar names what runs
# v1.10.277: Contact groups (R-39): the list leaves groups out and gives one group's members (group); a contact shows its groups; a contact deleted
#            or moved away is taken out of the groups of its address book; a card keeps its own vCard version
# v1.9.273: R-19: typed contact.show, -add, -edit, -delete, -move, -import, -export
# v1.9.270: R-19: typed contact.addresses
# v1.1.225: Every change of an item goes through item_writer: on its server first when it has one (R-23, D-194)
# v1.1.205: contact.list with limit 0 gives every contact (the window's list is never cut, R-02); typed (CONTACT_LIST)
# v0.5.159: contact.list for one contact (item): its row when it is in what was asked for; importing contacts is a big job (one fact at its end)
# v0.1.89: contact.addresses: every e-mail address of every contact, for the contact column of the message list
# v0.0.63: The tasks section
# v0.0.62: The contacts section
# v0.0.60: Initial contact commands

MAXIMUM_ROWS: int = 5_000  # the rows of one contact.list answer by limit (the command line pages with offset)
EVERY: int = 10**6  # every contact of one account (a list is never cut below this)
LIST_FIELDS: tuple[str, ...] = ("nickname", "email", "phone", "url", "address", "tag")


@dataclass(frozen=True, slots=True)
class ContactRow:
    item: str
    account: str
    collection: str
    name: str
    subtitle: str  # the organisation, or else the first address
    tags: tuple[str, ...]
    sort_key: str  # what the contacts are sorted by (family name first, without accents)
    groups: tuple[str, ...] = ()  # the ids of the groups of its address book it is in (R-39)


@dataclass(frozen=True, slots=True)
class ContactList:
    total: int
    contacts: tuple[ContactRow, ...]


@dataclass(frozen=True, slots=True)
class LabeledInfo:
    value: str
    types: tuple[str, ...]
    preferred: bool


@dataclass(frozen=True, slots=True)
class AddressInfo:
    street: str
    locality: str
    region: str
    postal_code: str
    country: str
    types: tuple[str, ...]
    preferred: bool
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContactInfo:
    item: str
    account: str
    collection: str
    uid: str
    name: str  # the name to show
    display_name: str
    given_name: str
    family_name: str
    additional_names: str
    prefix: str
    suffix: str
    nicknames: tuple[str, ...]
    emails: tuple[LabeledInfo, ...]
    phones: tuple[LabeledInfo, ...]
    urls: tuple[LabeledInfo, ...]
    addresses: tuple[AddressInfo, ...]
    organization: str
    department: str
    title: str
    role: str
    birthday: str
    anniversary: str
    note: str
    tags: tuple[str, ...]
    photo_media_type: str
    photo_base64: str
    revision: str
    groups: tuple[str, ...] = ()  # the names of the groups of its address book it is in


def _info(item: str, account: str, collection: str, contact: Contact, groups: tuple[str, ...] = ()) -> ContactInfo:
    def labeled(entries: tuple[Labeled, ...]) -> tuple[LabeledInfo, ...]:
        return tuple(LabeledInfo(entry.value, entry.types, entry.preferred) for entry in entries)

    return ContactInfo(
        item, account, collection, contact.uid, contact.full_name(), contact.display_name, contact.given_name, contact.family_name, contact.additional_names,
        contact.prefix, contact.suffix, contact.nicknames, labeled(contact.emails), labeled(contact.phones), labeled(contact.urls),
        tuple(AddressInfo(a.street, a.locality, a.region, a.postal_code, a.country, a.types, a.preferred, tuple(a.lines())) for a in contact.addresses),
        contact.organization, contact.department, contact.title, contact.role, contact.birthday.to_text() if contact.birthday else "",
        contact.anniversary.to_text() if contact.anniversary else "", contact.note, contact.categories,
        contact.photo.media_type if contact.photo else "", base64.b64encode(contact.photo.data).decode("ascii") if contact.photo else "", contact.revision,
        groups,
    )


# --- reading ------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParameters:
    collection: str | None = field(default=None, metadata=option("Only this address book (default: all)"))
    text: str = field(default="", metadata=option("Words to find in names, addresses, phone numbers, organisations and notes (a part of a word is enough)"))
    tag: str = field(default="", metadata=option("Only contacts with this tag"))
    limit: int = field(default=200, metadata=option("How many contacts at most (0: every one)"))
    offset: int = field(default=0, metadata=option("How many to skip"))
    item: str = field(default="", metadata=option("Only this contact: its row when it is among what was asked for (a list puts a changed contact at its place)"))
    group: str = field(default="", metadata=option("Only the members of this group (see contact-group.list)"))


@dataclass(frozen=True, slots=True)
class AddressesParameters:
    pass


@dataclass(frozen=True, slots=True)
class ContactAddress:
    address: str  # in lower case
    item: str
    collection: str


def contact_addresses(engine: Engine, parameters: AddressesParameters) -> tuple[ContactAddress, ...]:
    """Every e-mail address of every contact, in lower case, each once (the first contact that has it). A contact that cannot be read is left out."""
    found: dict[str, ContactAddress] = {}
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            for indexed in store.index.query(ItemQuery(CollectionKind.CONTACTS, (), "", "", limit=10**6, groups=False)):
                try:
                    contacts = parse_contacts(store.get(indexed.collection, indexed.item_id))
                except StorageError:
                    continue
                for contact in contacts[:1]:
                    for email in contact.emails:
                        address = email.value.strip().lower()
                        if address and address not in found:
                            found[address] = ContactAddress(address, indexed.item_id, indexed.collection)
    return tuple(found[address] for address in sorted(found))


def _memberships(store: PimStore, items: list[IndexedItem]) -> dict[str, tuple[str, ...]]:
    """For each contact (by item id), the ids of the groups of its address book that name it."""
    groups_by_uid: dict[tuple[str, str], list[str]] = {}
    for collection in {item.collection for item in items}:
        for group in groups_of_book(store, collection):
            for uid in member_uids(group):
                groups_by_uid.setdefault((collection, uid), []).append(group.item_id)
    if not groups_by_uid:
        return {}
    return {item.item_id: tuple(groups_by_uid.get((item.collection, bare_uid(item.uid)), ())) for item in items if item.uid.strip()}


def _rows(account: str, store: PimStore, items: list[IndexedItem]) -> list[ContactRow]:
    memberships = _memberships(store, items)
    return [ContactRow(i.item_id, account, i.collection, i.title, i.subtitle, i.tags, i.sort_key, memberships.get(i.item_id, ())) for i in items]


def contact_list(engine: Engine, parameters: ListParameters) -> ContactList:
    """Contacts sorted by family name (groups are not contacts: see contact-group.list), or the members of one group. Read from the index alone (no
    contact is read), so a window takes every contact at once (limit 0); the command line pages with limit and offset."""
    rows: list[ContactRow] = []
    total = 0
    limit = EVERY if parameters.limit == 0 else max(1, min(parameters.limit, MAXIMUM_ROWS))
    group_found = False
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            if parameters.collection is not None and not any(c.collection_id == parameters.collection for c in store.collections(CollectionKind.CONTACTS)):
                continue
            query = ItemQuery(CollectionKind.CONTACTS, (parameters.collection,) if parameters.collection else (), parameters.text, parameters.tag, limit=EVERY, groups=False)
            if parameters.group:
                group = store.index.get(parameters.group)
                if group is None or not group.is_group:
                    continue
                group_found = True
                members = member_uids(group)
                found = [i for i in store.index.query(replace(query, collections=(group.collection,))) if i.uid.strip() and bare_uid(i.uid) in members]
                total += len(found)
                rows.extend(_rows(account, store, found))
                continue
            total += store.index.count(query)
            wanted = EVERY if parameters.item else parameters.offset + limit  # one contact: wherever it stands
            rows.extend(_rows(account, store, store.index.query(replace(query, limit=wanted))))
    if parameters.group and not group_found:
        raise UsageError("There is no such group")
    rows.sort(key=lambda row: (row.sort_key, row.name.casefold()))  # one account: already in order; several: merged here
    if parameters.item:
        return ContactList(total, tuple(row for row in rows if row.item == parameters.item))
    return ContactList(total, tuple(rows[parameters.offset : parameters.offset + limit]))


CONTACT_LIST: TypedCommand[ListParameters, ContactList] = TypedCommand("contact.list", ListParameters, ContactList)


@dataclass(frozen=True, slots=True)
class ItemParameters:
    item: str = field(metadata=option("The contact id (see contact.list)"))


def contact_show(engine: Engine, parameters: ItemParameters) -> ContactInfo:
    with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
        text = found.store.get(found.item.collection, parameters.item)
        contacts = parse_contacts(text)
        if not contacts:
            raise UsageError("This contact cannot be read (it is kept as it is)")
        key = uid_key(contacts[0].uid)
        groups = tuple(group.title for group in groups_of_book(found.store, found.item.collection) if key and key in group.members)
        return _info(parameters.item, found.account, found.item.collection, contacts[0], groups)


# --- writing ------------------------------------------------------------------------------------------------------------------------------


def _labeled(entry: str) -> Labeled:
    """'work,pref|john@example.com' or just 'john@example.com'."""
    kinds, separator, value = entry.partition("|")
    if not separator:
        return Labeled(entry.strip())
    types = tuple(part.strip().lower() for part in kinds.split(",") if part.strip())
    return Labeled(value.strip(), tuple(part for part in types if part != "pref"), "pref" in types)


def _address(entry: str) -> Address:
    """'home|street|locality|region|postal code|country'."""
    parts = [part.strip() for part in entry.split("|")] + [""] * 6
    types = tuple(part.strip().lower() for part in parts[0].split(",") if part.strip())
    return Address(parts[1], parts[2], parts[3], parts[4], parts[5], types=tuple(part for part in types if part != "pref"), preferred="pref" in types)


def _first_kind(types: tuple[str, ...]) -> str:
    return next((kind for kind in types if kind not in ("internet", "pref")), "")


def _labeled_kept(before: tuple[Labeled, ...], entries: tuple[str, ...]) -> tuple[Labeled, ...]:
    """The entries as given; one that was there with the same value and the same first kind keeps everything else it had (more kinds, the
    preferred mark, parameters, its group): the window shows only the value and the first kind (RV-20)."""
    result: list[Labeled] = []
    for entry in entries:
        if not entry.strip():
            continue
        given = _labeled(entry)
        earlier = next((item for item in before if item.value == given.value and _first_kind(item.types) == _first_kind(given.types)), None)
        result.append(earlier if earlier is not None and not given.preferred else given)
    return tuple(result)


def _addresses_kept(before: tuple[Address, ...], entries: tuple[str, ...]) -> tuple[Address, ...]:
    """As _labeled_kept, for postal addresses: an address whose shown parts are all the same keeps its P.O. box, extended part, kinds and the rest."""
    result: list[Address] = []
    for entry in entries:
        if not entry.strip():
            continue
        given = _address(entry)
        shown = (given.street, given.locality, given.region, given.postal_code, given.country, _first_kind(given.types))
        earlier = next((item for item in before if (item.street, item.locality, item.region, item.postal_code, item.country, _first_kind(item.types)) == shown), None)
        result.append(earlier if earlier is not None and not given.preferred else given)
    return tuple(result)


def _date(text: str, name: str) -> PartialDate | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        if cleaned.startswith("--"):
            return PartialDate(int(cleaned[2:4]), int(cleaned[4:6]))
        parts = cleaned.replace("-", "")
        return PartialDate(int(parts[4:6]), int(parts[6:8]), int(parts[0:4]))
    except (ValueError, IndexError):
        raise UsageError(f"{name} must be written 1980-04-15, or --0415 without a year") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldParameters:
    """The fields of a contact (for contact.add and contact.edit). A list given here replaces the list the contact has."""

    name: str | None = field(default=None, metadata=option("The name to show"))
    given: str | None = field(default=None, metadata=option("Given name"))
    family: str | None = field(default=None, metadata=option("Family name"))
    additional: str | None = field(default=None, metadata=option("Middle names"))
    prefix: str | None = field(default=None, metadata=option("Title before the name (Dr.)"))
    suffix: str | None = field(default=None, metadata=option("After the name (Jr.)"))
    nickname: tuple[str, ...] = field(default=(), metadata=option("Nicknames (repeat)"))
    email: tuple[str, ...] = field(default=(), metadata=option("E-mail addresses, as 'work|a@example.com' or 'a@example.com' (repeat); adding 'pref' to the kinds marks the preferred one"))
    phone: tuple[str, ...] = field(default=(), metadata=option("Phone numbers, as 'cell|+31 6 1234' (repeat)"))
    url: tuple[str, ...] = field(default=(), metadata=option("Web addresses (repeat)"))
    address: tuple[str, ...] = field(default=(), metadata=option("Addresses as 'home|street|town|region|postal code|country' (repeat)"))
    organization: str | None = field(default=None, metadata=option("Organisation"))
    department: str | None = field(default=None, metadata=option("Department"))
    title: str | None = field(default=None, metadata=option("Job title"))
    role: str | None = field(default=None, metadata=option("Role"))
    birthday: str | None = field(default=None, metadata=option("Birthday, 1980-04-15 or --0415 (empty removes it)"))
    anniversary: str | None = field(default=None, metadata=option("Anniversary"))
    note: str | None = field(default=None, metadata=option("A note"))
    tag: tuple[str, ...] = field(default=(), metadata=option("Tags (repeat)"))


def _apply(contact: Contact, values: FieldParameters) -> Contact:
    result = contact
    if values.name is not None:
        result = replace(result, display_name=values.name.strip())
    if values.given is not None:
        result = replace(result, given_name=values.given.strip())
    if values.family is not None:
        result = replace(result, family_name=values.family.strip())
    if values.additional is not None:
        result = replace(result, additional_names=values.additional.strip())
    if values.prefix is not None:
        result = replace(result, prefix=values.prefix.strip())
    if values.suffix is not None:
        result = replace(result, suffix=values.suffix.strip())
    if values.organization is not None:
        result = replace(result, organization=values.organization.strip())
    if values.department is not None:
        result = replace(result, department=values.department.strip())
    if values.title is not None:
        result = replace(result, title=values.title.strip())
    if values.role is not None:
        result = replace(result, role=values.role.strip())
    if values.note is not None:
        result = replace(result, note=values.note)
    if values.nickname:
        result = replace(result, nicknames=tuple(item.strip() for item in values.nickname if item.strip()))
    if values.email:
        result = replace(result, emails=_labeled_kept(result.emails, values.email))
    if values.phone:
        result = replace(result, phones=_labeled_kept(result.phones, values.phone))
    if values.url:
        result = replace(result, urls=_labeled_kept(result.urls, values.url))
    if values.address:
        result = replace(result, addresses=_addresses_kept(result.addresses, values.address))
    if values.tag:
        result = replace(result, categories=tuple(dict.fromkeys(" ".join(tag.split()) for tag in values.tag if tag.strip())))
    if values.birthday is not None:
        result = replace(result, birthday=_date(values.birthday, "The birthday"))
    if values.anniversary is not None:
        result = replace(result, anniversary=_date(values.anniversary, "The anniversary"))
    return result


def _clear(contact: Contact, names: tuple[str, ...]) -> Contact:
    result = contact
    for name in names:
        if name == "nickname":
            result = replace(result, nicknames=())
        elif name == "email":
            result = replace(result, emails=())
        elif name == "phone":
            result = replace(result, phones=())
        elif name == "url":
            result = replace(result, urls=())
        elif name == "address":
            result = replace(result, addresses=())
        elif name == "tag":
            result = replace(result, categories=())
        else:
            raise UsageError(f"Only these can be cleared: {', '.join(LIST_FIELDS)}")
    return result


def _check_named(contact: Contact) -> None:
    if not contact.full_name():
        raise UsageError("A contact needs a name, an organisation or an e-mail address")


@dataclass(frozen=True, slots=True, kw_only=True)
class AddParameters(FieldParameters):
    collection: str = field(metadata=option("The address book id (see collection.list)"))


def contact_add(engine: Engine, parameters: AddParameters) -> ContactInfo:
    contact = _apply(Contact(), parameters).with_uid()
    _check_named(contact)
    try:
        with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, store, info), item_writer(engine, account, store) as writer:
            if info.read_only:
                raise UsageError("This address book is read-only")
            item = writer.put(info, contact_to_text(contact))
            return _info(item, account, info.collection_id, contact)
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditParameters(FieldParameters):
    item: str = field(metadata=option("The contact id"))
    clear: tuple[str, ...] = field(default=(), metadata=option("Lists to empty: nickname, email, phone, url, address, tag (repeat)"))
    add_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to add (repeat)"))
    remove_tag: tuple[str, ...] = field(default=(), metadata=option("Tags to remove (repeat)"))


def contact_edit(engine: Engine, parameters: EditParameters) -> ContactInfo:
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This address book is read-only")
            contacts = parse_contacts(found.store.get(found.item.collection, parameters.item))
            if not contacts:
                raise UsageError("This contact cannot be read, so it cannot be changed")
            contact = _clear(contacts[0], parameters.clear)
            contact = _apply(contact, parameters)
            if parameters.add_tag or parameters.remove_tag:
                contact = replace(contact, categories=changed_tags(contact.categories, parameters.add_tag, parameters.remove_tag))
            _check_named(contact)
            writer.put(found.collection, contact_to_text(contact), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, contact)
    except StorageError as error:
        raise UsageError(str(error)) from None


def contact_delete(engine: Engine, parameters: ItemParameters) -> ItemParameters:
    """Delete a contact. The file is kept in the store's .deleted directory for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found, item_writer(engine, found.account, found.store) as writer:
            if found.collection.read_only:
                raise UsageError("This address book is read-only")
            writer.delete(found.collection, parameters.item)
            leave_groups(writer, found.store, found.collection, found.item.uid)  # no group of its address book names it any more (R-39)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return parameters


@dataclass(frozen=True, slots=True)
class MoveParameters:
    item: str = field(metadata=option("The contact id"))
    collection: str = field(metadata=option("The address book to move it to"))


def contact_move(engine: Engine, parameters: MoveParameters) -> ContactInfo:
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found, item_writer(engine, found.account, found.store) as writer:
            with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, _, target):
                if account != found.account:
                    raise UsageError("A contact can only be moved inside one account (copy it with export and import)")
                if target.read_only or found.collection.read_only:
                    raise UsageError("A read-only address book cannot take part in a move")
                new_id = writer.move(found.collection, parameters.item, target)
                leave_groups(writer, found.store, found.collection, found.item.uid)  # a group holds contacts of its own address book only (R-39)
                contacts = parse_contacts(found.store.get(target.collection_id, new_id))
                return _info(new_id, account, target.collection_id, contacts[0] if contacts else Contact())
    except StorageError as error:
        raise UsageError(str(error)) from None


@dataclass(frozen=True, slots=True)
class ImportParameters:
    collection: str = field(metadata=option("The address book id"))
    text: str = field(metadata=option("The vCard text (one or more cards)"))


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int
    updated: int
    skipped: int  # cards that could not be read, or that have no name
    items: tuple[str, ...]


def contact_import(engine: Engine, parameters: ImportParameters) -> ImportResult:
    """Add the cards of a vCard text. A card whose uid is in the address book already replaces that contact."""
    imported = updated = skipped = 0
    items: list[str] = []
    try:
        with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, store, info), item_writer(engine, account, store) as writer:
            if info.read_only:
                raise UsageError("This address book is read-only")
            for contact in parse_contacts(parameters.text):
                if not contact.full_name():
                    skipped += 1
                    continue
                contact = contact.with_uid()
                known = store.index.find_uid(info.collection_id, contact.uid)
                items.append(writer.put(info, contact_to_text(contact), known.item_id if known else None))
                if known:
                    updated += 1
                else:
                    imported += 1
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ImportResult(imported, updated, skipped, tuple(items))


@dataclass(frozen=True, slots=True)
class ExportParameters:
    item: str | None = field(default=None, metadata=option("The contact id"))
    collection: str | None = field(default=None, metadata=option("Or a whole address book"))


@dataclass(frozen=True, slots=True)
class ExportResult:
    contacts: int
    text: str


def contact_export(engine: Engine, parameters: ExportParameters) -> ExportResult:
    """The vCard text of a contact, or of every contact of an address book."""
    if (parameters.item is None) == (parameters.collection is None):
        raise UsageError("Give either a contact or an address book")
    texts: list[str] = []
    try:
        if parameters.item is not None:
            with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
                texts.append(found.store.get(found.item.collection, parameters.item))
        else:
            assert parameters.collection is not None
            with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (_, store, info):
                texts.extend(store.get(info.collection_id, item) for item in store.item_ids(info.collection_id))
    except StorageError as error:
        raise UsageError(str(error)) from None
    return ExportResult(len(texts), "".join(texts))


CONTACT_ADDRESSES: TypedCommand[AddressesParameters, tuple[ContactAddress, ...]] = TypedCommand("contact.addresses", AddressesParameters, tuple)  # every element is a ContactAddress


CONTACT_SHOW: TypedCommand[ItemParameters, ContactInfo] = TypedCommand("contact.show", ItemParameters, ContactInfo)
CONTACT_ADD: TypedCommand[AddParameters, ContactInfo] = TypedCommand("contact.add", AddParameters, ContactInfo)
CONTACT_EDIT: TypedCommand[EditParameters, ContactInfo] = TypedCommand("contact.edit", EditParameters, ContactInfo)
CONTACT_DELETE: TypedCommand[ItemParameters, ItemParameters] = TypedCommand("contact.delete", ItemParameters, ItemParameters)
CONTACT_MOVE: TypedCommand[MoveParameters, ContactInfo] = TypedCommand("contact.move", MoveParameters, ContactInfo)
CONTACT_IMPORT: TypedCommand[ImportParameters, ImportResult] = TypedCommand("contact.import", ImportParameters, ImportResult)
CONTACT_EXPORT: TypedCommand[ExportParameters, ExportResult] = TypedCommand("contact.export", ExportParameters, ExportResult)


def register(registry: CommandRegistry) -> None:
    registry.register(CONTACT_LIST.id, "List contacts, sorted by family name", ListParameters, contact_list, doing="Loading the contacts")
    registry.register(CONTACT_SHOW.id, "Show a contact", ItemParameters, contact_show, doing="Loading a contact")
    registry.register(CONTACT_ADDRESSES.id, "List the e-mail addresses of all contacts", AddressesParameters, contact_addresses, doing="Reading the addresses of the contacts")
    registry.register(CONTACT_ADD.id, "Add a contact", AddParameters, contact_add, doing="Adding a contact")
    registry.register(CONTACT_EDIT.id, "Change a contact", EditParameters, contact_edit, doing="Saving a contact")
    registry.register(CONTACT_DELETE.id, "Delete a contact", ItemParameters, contact_delete, destructive=True, doing="Deleting a contact")
    registry.register(CONTACT_MOVE.id, "Move a contact to another address book of the same account", MoveParameters, contact_move, doing="Moving a contact")
    registry.register(CONTACT_IMPORT.id, "Import contacts from vCard text", ImportParameters, quiet_command(contact_import), doing="Importing contacts")
    registry.register(CONTACT_EXPORT.id, "Export a contact or an address book as vCard text", ExportParameters, contact_export, doing="Exporting contacts")
