import base64
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from chrono_mail.pim.vcard import (
    ContentLine,
    decode_binary,
    escape,
    format_card,
    listed,
    parse_cards,
    structured,
    unescape,
)

# --- Version ------------------------------------------------------------
__version__ = "contact.py 1.10.277"

# --- Version history ----------------------------------------------------
# v1.10.277: Contact groups (R-39): a group card in either form (vCard 4 KIND:group with MEMBER, Apple's X-ADDRESSBOOKSERVER-KIND and -MEMBER),
#            written back in its own form; a card keeps its own version (a vCard 4 card was written back as 3.0)
# v0.0.57: Initial contact model: from and to vCard, keeping what is not understood

KNOWN: frozenset[str] = frozenset(
    {"N", "FN", "NICKNAME", "EMAIL", "TEL", "ADR", "URL", "ORG", "TITLE", "ROLE", "BDAY", "ANNIVERSARY", "NOTE", "CATEGORIES", "UID", "REV", "PHOTO"}
)
MAXIMUM_ITEMS: int = 500  # emails, phones, addresses ... of one contact: more is damage or an attack, and the rest is dropped
MAXIMUM_MEMBERS: int = 20_000  # of one group (a card has at most 20,000 lines anyway)
UUID_PREFIX: str = "urn:uuid:"
MAILTO_PREFIX: str = "mailto:"


class GroupForm(StrEnum):
    """How a card says it is a group: vCard 4's KIND:group with MEMBER lines, or Apple's X-ADDRESSBOOKSERVER-KIND and -MEMBER (vCard 3; Kerio
    takes this form as a group of its own)."""

    VCARD4 = "vcard4"
    APPLE = "apple"


_GROUP_LINES: dict[GroupForm, tuple[str, str]] = {GroupForm.VCARD4: ("KIND", "MEMBER"), GroupForm.APPLE: ("X-ADDRESSBOOKSERVER-KIND", "X-ADDRESSBOOKSERVER-MEMBER")}


def bare_uid(uid: str) -> str:
    """A uid as compared with a member reference: without urn:uuid: (vCard 4 uids are often written with it, members always)."""
    text = uid.strip()
    return text[len(UUID_PREFIX):] if text.lower().startswith(UUID_PREFIX) else text


def member_reference(uid: str) -> str:
    """How a group names a contact: urn:uuid:<uid> (a uid that is a urn already is used as it is)."""
    text = uid.strip()
    return text if text.lower().startswith("urn:") else UUID_PREFIX + text


def member_address(reference: str) -> str:
    """The e-mail address of a member given as an address (mailto:), else ''."""
    text = reference.strip()
    return text[len(MAILTO_PREFIX):].strip() if text.lower().startswith(MAILTO_PREFIX) else ""


def member_uid(reference: str) -> str:
    """The uid of the contact a member names, compared as bare_uid; '' for a member given as an address."""
    return "" if member_address(reference) or not reference.strip() else bare_uid(reference)


@dataclass(frozen=True, slots=True)
class PartialDate:
    """A birthday or anniversary; the year may be unknown (--0415)."""

    month: int
    day: int
    year: int | None = None

    def to_text(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}" if self.year else f"--{self.month:02d}{self.day:02d}"

    def to_date(self, fallback_year: int = 2000) -> date:
        return date(self.year or fallback_year, self.month, self.day)


@dataclass(frozen=True, slots=True)
class Labeled:
    """An e-mail address, a phone number or a web address, with its kinds (work, home, cell ...)."""

    value: str
    types: tuple[str, ...] = ()
    preferred: bool = False
    params: tuple[tuple[str, tuple[str, ...]], ...] = ()  # parameters we do not use, kept
    group: str = ""


@dataclass(frozen=True, slots=True)
class Address:
    street: str = ""
    locality: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    post_box: str = ""
    extended: str = ""
    types: tuple[str, ...] = ()
    preferred: bool = False
    params: tuple[tuple[str, tuple[str, ...]], ...] = ()
    group: str = ""

    def lines(self) -> list[str]:
        """The address as lines as it is written on an envelope."""
        place = " ".join(part for part in (self.postal_code, self.locality) if part)
        return [part for part in (self.post_box, self.extended, self.street, place, self.region, self.country) if part]


@dataclass(frozen=True, slots=True)
class Photo:
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Contact:
    uid: str = ""
    display_name: str = ""
    family_name: str = ""
    given_name: str = ""
    additional_names: str = ""
    prefix: str = ""
    suffix: str = ""
    nicknames: tuple[str, ...] = ()
    emails: tuple[Labeled, ...] = ()
    phones: tuple[Labeled, ...] = ()
    urls: tuple[Labeled, ...] = ()
    addresses: tuple[Address, ...] = ()
    organization: str = ""
    department: str = ""
    title: str = ""
    role: str = ""
    birthday: PartialDate | None = None
    anniversary: PartialDate | None = None
    note: str = ""
    categories: tuple[str, ...] = ()
    photo: Photo | None = None
    revision: str = ""
    extra: tuple[ContentLine, ...] = field(default=(), repr=False)  # properties we do not understand, kept as they were
    version: str = ""  # as the card said ("" for a new one)
    group_form: GroupForm | None = None  # a group card, and the form it says so in
    members: tuple[str, ...] = ()  # a group's members as written: urn:uuid:<uid>, mailto:<address>, or another reference

    def full_name(self) -> str:
        """The name to show: the formatted name when there is one, else the parts, else the organisation, else the first address."""
        if self.display_name.strip():
            return self.display_name.strip()
        parts = " ".join(part for part in (self.prefix, self.given_name, self.additional_names, self.family_name, self.suffix) if part.strip())
        if parts.strip():
            return parts.strip()
        if self.organization.strip():
            return self.organization.strip()
        return self.emails[0].value if self.emails else ""

    def sort_name(self) -> str:
        """Family name first, for sorting."""
        family = " ".join(part for part in (self.family_name, self.given_name) if part)
        return (family or self.full_name()).casefold()

    def with_uid(self) -> "Contact":
        return self if self.uid else replace(self, uid=str(uuid.uuid4()))


# --- from a card -------------------------------------------------------------------------------------------------------------------------


def _labeled(line: ContentLine, value: str) -> Labeled:
    types = line.types()
    others = tuple((key, values) for key, values in line.params if key not in ("TYPE", "PREF"))
    return Labeled(value, tuple(item for item in types if item != "pref"), "pref" in types or bool(line.param("PREF")), others, line.group)


def _partial_date(text: str) -> PartialDate | None:
    value = text.strip().split("T", 1)[0].replace("-", "")
    try:
        if text.strip().startswith("--"):
            digits = value
            return PartialDate(int(digits[0:2]), int(digits[2:4])) if len(digits) >= 4 else None
        if len(value) >= 8:
            parsed = date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
            return PartialDate(parsed.month, parsed.day, parsed.year)
    except ValueError:
        return None
    return None


def contact_from_lines(lines: list[ContentLine]) -> Contact:
    """A contact from the content lines of one card. A missing or odd property gives an empty field; nothing raises."""
    c = Contact()
    emails: list[Labeled] = []
    phones: list[Labeled] = []
    urls: list[Labeled] = []
    addresses: list[Address] = []
    nicknames: list[str] = []
    categories: list[str] = []
    extra: list[ContentLine] = []
    group_form: GroupForm | None = None
    member_lines: dict[str, list[ContentLine]] = {"MEMBER": [], "X-ADDRESSBOOKSERVER-MEMBER": []}
    for line in lines:
        name = line.name
        if name == "VERSION":
            c = replace(c, version=line.value.strip())
            continue
        if name in ("KIND", "X-ADDRESSBOOKSERVER-KIND"):
            if line.value.strip().lower() == "group" and group_form is None:
                group_form = GroupForm.VCARD4 if name == "KIND" else GroupForm.APPLE
            else:
                extra.append(line)  # an individual, an organisation, a second kind: kept as it was
            continue
        if name in member_lines:
            member_lines[name].append(line)
            continue
        if name not in KNOWN:
            extra.append(line)
            continue
        if name == "N":
            parts = (structured(line.value) + [""] * 5)[:5]
            c = replace(c, family_name=parts[0], given_name=parts[1], additional_names=parts[2].replace(",", " "), prefix=parts[3], suffix=parts[4])
        elif name == "FN":
            c = replace(c, display_name=unescape(line.value).strip())
        elif name == "NICKNAME":
            nicknames.extend(listed(line.value))
        elif name == "EMAIL" and len(emails) < MAXIMUM_ITEMS:
            emails.append(_labeled(line, unescape(line.value).strip()))
        elif name == "TEL" and len(phones) < MAXIMUM_ITEMS:
            phones.append(_labeled(line, unescape(line.value).strip().removeprefix("tel:")))
        elif name == "URL" and len(urls) < MAXIMUM_ITEMS:
            urls.append(_labeled(line, unescape(line.value).strip()))
        elif name == "ADR" and len(addresses) < MAXIMUM_ITEMS:
            p = (structured(line.value) + [""] * 7)[:7]
            labeled = _labeled(line, "")
            addresses.append(Address(p[2], p[3], p[4], p[5], p[6], p[0], p[1], labeled.types, labeled.preferred, labeled.params, labeled.group))
        elif name == "ORG":
            parts = structured(line.value)
            c = replace(c, organization=parts[0] if parts else "", department=" / ".join(part for part in parts[1:] if part))
        elif name == "TITLE":
            c = replace(c, title=unescape(line.value).strip())
        elif name == "ROLE":
            c = replace(c, role=unescape(line.value).strip())
        elif name == "BDAY":
            c = replace(c, birthday=_partial_date(line.value))
        elif name == "ANNIVERSARY":
            c = replace(c, anniversary=_partial_date(line.value))
        elif name == "NOTE":
            c = replace(c, note=unescape(line.value))
        elif name == "CATEGORIES":
            categories.extend(listed(line.value))
        elif name == "UID":
            c = replace(c, uid=unescape(line.value).strip())
        elif name == "REV":
            c = replace(c, revision=line.value.strip())
        elif name == "PHOTO":
            decoded = decode_binary(line)
            if decoded is not None:
                c = replace(c, photo=Photo(decoded[0], decoded[1]))
            else:
                extra.append(line)  # a photo given as a link stays as it is
    members: list[str] = []
    for name, found in member_lines.items():
        if group_form is not None and name == _GROUP_LINES[group_form][1]:
            members.extend(line.value.strip() for line in found[:MAXIMUM_MEMBERS] if line.value.strip())
        else:
            extra.extend(found)  # members of the other form, or of a card that is no group: kept as they were
    return replace(
        c, nicknames=tuple(nicknames), emails=tuple(emails), phones=tuple(phones), urls=tuple(urls), addresses=tuple(addresses),
        categories=tuple(dict.fromkeys(categories)), extra=tuple(extra), group_form=group_form, members=tuple(dict.fromkeys(members)),
    )


def parse_contacts(text: str | bytes) -> list[Contact]:
    """Every card in a text as a contact."""
    return [contact_from_lines(lines) for lines in parse_cards(text)]


# --- to a card ---------------------------------------------------------------------------------------------------------------------------


def _params(item: Labeled | Address) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: list[tuple[str, tuple[str, ...]]] = []
    types = tuple(item.types) + (("pref",) if item.preferred else ())
    if types:
        result.append(("TYPE", tuple(value.upper() if value.islower() and len(value) <= 5 else value for value in types)))
    result.extend(item.params)
    return tuple(result)


def contact_to_lines(contact: Contact, version: str = "3.0") -> list[ContentLine]:
    """The content lines of a card for this contact: the properties we know, written afresh, then the ones we did not understand, as they were."""
    lines: list[ContentLine] = []
    lines.append(ContentLine("UID", escape(contact.uid or "")))
    name_value = ";".join(escape(part) for part in (contact.family_name, contact.given_name, contact.additional_names, contact.prefix, contact.suffix))
    lines.append(ContentLine("N", name_value))
    lines.append(ContentLine("FN", escape(contact.full_name())))
    if contact.group_form is not None:
        kind, member = _GROUP_LINES[contact.group_form]
        lines.append(ContentLine(kind, "group"))
        lines.extend(ContentLine(member, reference) for reference in contact.members)
    if contact.nicknames:
        lines.append(ContentLine("NICKNAME", ",".join(escape(item) for item in contact.nicknames)))
    for kind, items in (("EMAIL", contact.emails), ("TEL", contact.phones), ("URL", contact.urls)):
        for item in items:
            lines.append(ContentLine(kind, escape(item.value), _params(item), item.group))
    for address in contact.addresses:
        value = ";".join(escape(part) for part in (address.post_box, address.extended, address.street, address.locality, address.region, address.postal_code, address.country))
        lines.append(ContentLine("ADR", value, _params(address), address.group))
    if contact.organization or contact.department:
        lines.append(ContentLine("ORG", ";".join(escape(part) for part in (contact.organization, contact.department)).rstrip(";")))
    if contact.title:
        lines.append(ContentLine("TITLE", escape(contact.title)))
    if contact.role:
        lines.append(ContentLine("ROLE", escape(contact.role)))
    if contact.birthday is not None:
        lines.append(ContentLine("BDAY", contact.birthday.to_text()))
    if contact.anniversary is not None:
        lines.append(ContentLine("ANNIVERSARY", contact.anniversary.to_text()))
    if contact.note:
        lines.append(ContentLine("NOTE", escape(contact.note)))
    if contact.categories:
        lines.append(ContentLine("CATEGORIES", ",".join(escape(item) for item in contact.categories)))
    if contact.photo is not None:
        encoded = base64.b64encode(contact.photo.data).decode("ascii")
        if version.startswith("4"):
            lines.append(ContentLine("PHOTO", f"data:{contact.photo.media_type};base64,{encoded}"))
        else:
            lines.append(ContentLine("PHOTO", encoded, (("ENCODING", ("b",)), ("TYPE", (contact.photo.media_type.split("/", 1)[-1].upper(),)))))
    if contact.revision:
        lines.append(ContentLine("REV", contact.revision))
    lines.extend(contact.extra)
    return lines


def card_version(contact: Contact) -> str:
    """The version a contact is written in: its own (3.0 or 4.0); 3.0 for one of 2.1 (which is not written) and for a new one, except a new
    group of vCard 4's form."""
    if contact.version in ("3.0", "4.0"):
        return contact.version
    return "4.0" if not contact.version and contact.group_form is GroupForm.VCARD4 else "3.0"


def contact_to_text(contact: Contact, version: str = "") -> str:
    """The vCard text of a contact (a uid is made when there is none), in its own version unless one is given."""
    chosen = version or card_version(contact)
    return format_card(contact_to_lines(contact.with_uid(), chosen), chosen)
