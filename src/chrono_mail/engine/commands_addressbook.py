import base64
from dataclasses import dataclass, field, replace

from chrono_mail.core import CollectionKind, StorageError
from chrono_mail.engine.pimaccess import located, open_collection, open_pim, pim_account_ids
from chrono_mail.engine.registry import CommandRegistry, UsageError, option
from chrono_mail.engine.session import Engine, quiet_command
from chrono_mail.pim.edit import changed_tags
from chrono_mail.pim.contact import Address, Contact, Labeled, PartialDate, contact_to_text, parse_contacts
from chrono_mail.pimstore import ItemQuery

# --- Version ------------------------------------------------------------
__version__ = "commands_addressbook.py 0.5.159"

# --- Version history ----------------------------------------------------
# v0.5.159: contact.list for one contact (item): its row when it is in what was asked for; importing contacts is a big job (one fact at its end)
# v0.1.89: contact.addresses: every e-mail address of every contact, for the contact column of the message list
# v0.0.63: The tasks section
# v0.0.62: The contacts section
# v0.0.60: Initial contact commands

MAXIMUM_ROWS: int = 5_000
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


def _info(item: str, account: str, collection: str, contact: Contact) -> ContactInfo:
    def labeled(entries: tuple[Labeled, ...]) -> tuple[LabeledInfo, ...]:
        return tuple(LabeledInfo(entry.value, entry.types, entry.preferred) for entry in entries)

    return ContactInfo(
        item, account, collection, contact.uid, contact.full_name(), contact.display_name, contact.given_name, contact.family_name, contact.additional_names,
        contact.prefix, contact.suffix, contact.nicknames, labeled(contact.emails), labeled(contact.phones), labeled(contact.urls),
        tuple(AddressInfo(a.street, a.locality, a.region, a.postal_code, a.country, a.types, a.preferred, tuple(a.lines())) for a in contact.addresses),
        contact.organization, contact.department, contact.title, contact.role, contact.birthday.to_text() if contact.birthday else "",
        contact.anniversary.to_text() if contact.anniversary else "", contact.note, contact.categories,
        contact.photo.media_type if contact.photo else "", base64.b64encode(contact.photo.data).decode("ascii") if contact.photo else "", contact.revision,
    )


# --- reading ------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParameters:
    collection: str | None = field(default=None, metadata=option("Only this address book (default: all)"))
    text: str = field(default="", metadata=option("Words to find in names, addresses, phone numbers, organisations and notes (a part of a word is enough)"))
    tag: str = field(default="", metadata=option("Only contacts with this tag"))
    limit: int = field(default=200, metadata=option("How many contacts at most"))
    offset: int = field(default=0, metadata=option("How many to skip"))
    item: str = field(default="", metadata=option("Only this contact: its row when it is among what was asked for (a list puts a changed contact at its place)"))


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
            for indexed in store.index.query(ItemQuery(CollectionKind.CONTACTS, (), "", "", limit=10**6)):
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


def contact_list(engine: Engine, parameters: ListParameters) -> ContactList:
    """Contacts sorted by family name."""
    rows: list[ContactRow] = []
    total = 0
    limit = max(1, min(parameters.limit, MAXIMUM_ROWS))
    for account in pim_account_ids(engine):
        with open_pim(engine, account) as store:
            if parameters.collection is not None and not any(c.collection_id == parameters.collection for c in store.collections(CollectionKind.CONTACTS)):
                continue
            query = ItemQuery(CollectionKind.CONTACTS, (parameters.collection,) if parameters.collection else (), parameters.text, parameters.tag, limit=10**6)
            total += store.index.count(query)
            wanted = 10**6 if parameters.item else parameters.offset + limit  # one contact: wherever it stands
            rows.extend(ContactRow(i.item_id, account, i.collection, i.title, i.subtitle, i.tags, i.sort_key) for i in store.index.query(replace(query, limit=wanted)))
    rows.sort(key=lambda row: (row.sort_key, row.name.casefold()))  # one account: already in order; several: merged here
    if parameters.item:
        return ContactList(total, tuple(row for row in rows if row.item == parameters.item))
    return ContactList(total, tuple(rows[parameters.offset : parameters.offset + limit]))


@dataclass(frozen=True, slots=True)
class ItemParameters:
    item: str = field(metadata=option("The contact id (see contact.list)"))


def contact_show(engine: Engine, parameters: ItemParameters) -> ContactInfo:
    with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
        text = found.store.get(found.item.collection, parameters.item)
        contacts = parse_contacts(text)
        if not contacts:
            raise UsageError("This contact cannot be read (it is kept as it is)")
        return _info(parameters.item, found.account, found.item.collection, contacts[0])


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
        result = replace(result, emails=tuple(_labeled(entry) for entry in values.email if entry.strip()))
    if values.phone:
        result = replace(result, phones=tuple(_labeled(entry) for entry in values.phone if entry.strip()))
    if values.url:
        result = replace(result, urls=tuple(_labeled(entry) for entry in values.url if entry.strip()))
    if values.address:
        result = replace(result, addresses=tuple(_address(entry) for entry in values.address if entry.strip()))
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
        with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, store, info):
            if info.read_only:
                raise UsageError("This address book is read-only")
            item = store.put(info.collection_id, contact_to_text(contact))
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
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
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
            found.store.put(found.item.collection, contact_to_text(contact), parameters.item)
            return _info(parameters.item, found.account, found.item.collection, contact)
    except StorageError as error:
        raise UsageError(str(error)) from None


def contact_delete(engine: Engine, parameters: ItemParameters) -> ItemParameters:
    """Delete a contact. The file is kept in the store's .deleted directory for 30 days."""
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
            if found.collection.read_only:
                raise UsageError("This address book is read-only")
            found.store.delete(found.item.collection, parameters.item)
    except StorageError as error:
        raise UsageError(str(error)) from None
    return parameters


@dataclass(frozen=True, slots=True)
class MoveParameters:
    item: str = field(metadata=option("The contact id"))
    collection: str = field(metadata=option("The address book to move it to"))


def contact_move(engine: Engine, parameters: MoveParameters) -> ContactInfo:
    try:
        with located(engine, parameters.item, CollectionKind.CONTACTS) as found:
            with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (account, _, target):
                if account != found.account:
                    raise UsageError("A contact can only be moved inside one account (copy it with export and import)")
                if target.read_only or found.collection.read_only:
                    raise UsageError("A read-only address book cannot take part in a move")
                new_id = found.store.move(found.item.collection, parameters.item, target.collection_id)
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
        with open_collection(engine, parameters.collection, CollectionKind.CONTACTS) as (_, store, info):
            if info.read_only:
                raise UsageError("This address book is read-only")
            for contact in parse_contacts(parameters.text):
                if not contact.full_name():
                    skipped += 1
                    continue
                contact = contact.with_uid()
                known = store.index.find_uid(info.collection_id, contact.uid)
                items.append(store.put(info.collection_id, contact_to_text(contact), known.item_id if known else None))
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


def register(registry: CommandRegistry) -> None:
    registry.register("contact.list", "List contacts, sorted by family name", ListParameters, contact_list)
    registry.register("contact.show", "Show a contact", ItemParameters, contact_show)
    registry.register("contact.addresses", "List the e-mail addresses of all contacts", AddressesParameters, contact_addresses)
    registry.register("contact.add", "Add a contact", AddParameters, contact_add)
    registry.register("contact.edit", "Change a contact", EditParameters, contact_edit)
    registry.register("contact.delete", "Delete a contact", ItemParameters, contact_delete, destructive=True)
    registry.register("contact.move", "Move a contact to another address book of the same account", MoveParameters, contact_move)
    registry.register("contact.import", "Import contacts from vCard text", ImportParameters, quiet_command(contact_import))
    registry.register("contact.export", "Export a contact or an address book as vCard text", ExportParameters, contact_export)
