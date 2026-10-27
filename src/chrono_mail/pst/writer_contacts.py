"""Contacts and contact groups as items of a data file (MS-OXOCNTC): names, three e-mail addresses, phones, postal addresses, company,
dates, notes and categories; a group as a distribution list of one-off members. The mirror of contacts.py."""

from datetime import UTC, datetime

from chrono_mail.pim.contact import Contact, PartialDate
from chrono_mail.pst.names import PSETID_ADDRESS
from chrono_mail.pst.writer_ltp import Prop, binaries, binary, integer, integers, text, time
from chrono_mail.pst.writer_mail import Item, categories, one_off
from chrono_mail.pst.writer_names import NameRegistry
from chrono_mail.pst.writer_ndb import crc

# --- Version ------------------------------------------------------------
__version__ = "writer_contacts.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial version: contacts and contact groups as items of a data file

_EMAIL_SLOTS = ((0x8080, 0x8082, 0x8083, 0x8084, 0x8085), (0x8090, 0x8092, 0x8093, 0x8094, 0x8095), (0x80A0, 0x80A2, 0x80A3, 0x80A4, 0x80A5))
# PO box, street, city, region, postal code, country
_HOME = (0x3A5E, 0x3A5D, 0x3A59, 0x3A5C, 0x3A5B, 0x3A5A)
_WORK = (0x3A2B, 0x3A29, 0x3A27, 0x3A28, 0x3A2A, 0x3A26)
_OTHER = (0x3A64, 0x3A63, 0x3A5F, 0x3A62, 0x3A61, 0x3A60)
_WORK_NAMED = (0x804A, 0x8045, 0x8046, 0x8047, 0x8048, 0x8049)


def _phone_tags(types: tuple[str, ...]) -> tuple[int, ...]:
    kinds = {kind.lower() for kind in types}
    if "cell" in kinds:
        return (0x3A1C,)
    if "pager" in kinds:
        return (0x3A21,)
    if "fax" in kinds:
        return (0x3A24,) if "work" in kinds else (0x3A25,) if "home" in kinds else (0x3A23,)
    if "work" in kinds:
        return (0x3A08, 0x3A1B)
    if "home" in kinds:
        return (0x3A09, 0x3A2F)
    return (0x3A1A, 0x3A1F)


def _day(value: PartialDate | None) -> datetime | None:
    if value is None or value.year is None:
        return None
    try:
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    except ValueError:
        return None


def contact_item(contact: Contact, names: NameRegistry) -> Item:
    """The item of a contact."""
    address_set = PSETID_ADDRESS
    full = contact.full_name() or contact.organization
    props: list[Prop] = [text(0x001A, "IPM.Contact"), text(0x3001, full), text(0x0037, full), integer(0x0E07, 1)]
    for tag, value in ((0x3A06, contact.given_name), (0x3A11, contact.family_name), (0x3A44, contact.additional_names), (0x3A45, contact.prefix),
                       (0x3A05, contact.suffix), (0x3A16, contact.organization), (0x3A18, contact.department), (0x3A17, contact.title)):
        if value.strip():
            props.append(text(tag, value.strip()))
    if contact.nicknames:
        props.append(text(0x3A4F, contact.nicknames[0]))
    file_under = ", ".join(part for part in (contact.family_name.strip(), contact.given_name.strip()) if part) or full
    props.append(text(names.id_of(address_set, 0x8005), file_under))
    slots: list[int] = []
    for number, email in enumerate(contact.emails[:3]):
        address = email.value.strip()
        if not address:
            continue
        shown, kind, value, original, entry = _EMAIL_SLOTS[number]
        props += [text(names.id_of(address_set, shown), f"{full} ({address})" if full else address), text(names.id_of(address_set, kind), "SMTP"),
                  text(names.id_of(address_set, value), address), text(names.id_of(address_set, original), address),
                  binary(names.id_of(address_set, entry), one_off(full, address))]
        slots.append(number)
    if slots:
        props += [integers(names.id_of(address_set, 0x8028), slots), integer(names.id_of(address_set, 0x8029), sum(1 << slot for slot in slots))]
    used: set[int] = set()
    for phone in contact.phones:
        number = phone.value.strip()
        tag = next((tag for tag in _phone_tags(phone.types) if tag not in used), None)
        if number and tag is not None:
            used.add(tag)
            props.append(text(tag, number))
    placed: set[str] = set()
    for address in contact.addresses:
        kinds = {kind.lower() for kind in address.types}
        place = "work" if "work" in kinds else "home" if "home" in kinds else "other"
        if place in placed:
            continue
        placed.add(place)
        values = (address.post_box, address.street, address.locality, address.region, address.postal_code, address.country)
        tags = _WORK if place == "work" else _HOME if place == "home" else _OTHER
        props += [text(tag, value) for tag, value in zip(tags, values, strict=True) if value.strip()]
        if place == "work":
            props += [text(names.id_of(address_set, lid), value) for lid, value in zip(_WORK_NAMED, values, strict=True) if value.strip()]
    for tag, value in ((0x3A42, contact.birthday), (0x3A41, contact.anniversary)):
        moment = _day(value)
        if moment is not None:
            props.append(time(tag, moment))
    for number, url in enumerate(contact.urls[:2]):
        props.append(text(0x3A51 if number == 0 else 0x3A50, url.value.strip()))
    if contact.note.strip():
        props.append(text(0x1000, contact.note.strip()))
    props.append(integer(0x1080, 0x200))
    props += categories(names, contact.categories)
    return Item(props, [], [])


def group_item(name: str, members: list[tuple[str, str]], names: NameRegistry) -> Item:
    """The item of a contact group: a distribution list whose members are one-off addresses."""
    entries = [one_off(member_name, address) for member_name, address in members]
    props: list[Prop] = [
        text(0x001A, "IPM.DistList"), text(0x3001, name), text(0x0037, name), integer(0x0E07, 1), text(names.id_of(PSETID_ADDRESS, 0x8053), name),
        text(names.id_of(PSETID_ADDRESS, 0x8005), name), integer(0x1080, 0x202),
    ]
    if entries:
        props += [binaries(names.id_of(PSETID_ADDRESS, 0x8055), entries), binaries(names.id_of(PSETID_ADDRESS, 0x8054), entries),
                  integer(names.id_of(PSETID_ADDRESS, 0x804C), crc(b"".join(entries)))]
    return Item(props, [], [])
