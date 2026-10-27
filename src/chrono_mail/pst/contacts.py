import base64
import re
from collections.abc import Callable
from datetime import UTC

from chrono_mail.pim.vcard import ContentLine, escape, format_card
from chrono_mail.pst.headers import ATTACH_MIME_TAG, DISPLAY_NAME, SUBJECT, categories
from chrono_mail.pst.mail import bodies
from chrono_mail.pst.names import PSETID_ADDRESS
from chrono_mail.pst.properties import Message, Properties, nearest_date

# --- Version ------------------------------------------------------------
__version__ = "contacts.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: contacts as vCard 4.0 and distribution lists as groups with their members (MS-OXOCNTC, MS-OXCDATA 2.2.5)

MAXIMUM_PHOTO_BYTES: int = 2 * 1024 * 1024
MAXIMUM_MEMBERS: int = 10_000

GIVEN_NAME = 0x3A06
SURNAME = 0x3A11
MIDDLE_NAME = 0x3A44
PREFIX = 0x3A45
SUFFIX = 0x3A05
NICKNAME = 0x3A4F
COMPANY = 0x3A16
DEPARTMENT = 0x3A18
TITLE = 0x3A17
BIRTHDAY = 0x3A42
ANNIVERSARY = 0x3A41
BUSINESS_HOME_PAGE = 0x3A51
PERSONAL_HOME_PAGE = 0x3A50
CONTACT_PHOTO = 0x7FFF  # PR_ATTACHMENT_CONTACTPHOTO
# PSETID_Address
FILE_UNDER = 0x8005
HTML_PAGE = 0x802B
EMAILS = ((0x8083, 0x8082, 0x8084), (0x8093, 0x8092, 0x8094), (0x80A3, 0x80A2, 0x80A4))  # address, its kind, original display name
WORK_ADDRESS_NAMED = (0x804A, 0x8045, 0x8046, 0x8047, 0x8048, 0x8049)
ONE_OFF_MEMBERS = 0x8054
MEMBERS = 0x8055

# the PO box, street, city, region, postal code and country tags of each kind of postal address
_POSTAL: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...] = (
    ("home", (0x3A5E, 0x3A5D, 0x3A59, 0x3A5C, 0x3A5B, 0x3A5A)),
    ("work", (0x3A2B, 0x3A29, 0x3A27, 0x3A28, 0x3A2A, 0x3A26)),
    ("", (0x3A64, 0x3A63, 0x3A5F, 0x3A62, 0x3A61, 0x3A60)),
)
_PHONES: tuple[tuple[int, str], ...] = (
    (0x3A08, "work,voice"), (0x3A1B, "work,voice"), (0x3A09, "home,voice"), (0x3A2F, "home,voice"), (0x3A1C, "cell"), (0x3A24, "work,fax"),
    (0x3A25, "home,fax"), (0x3A23, "fax"), (0x3A1A, "voice"), (0x3A1F, "voice"), (0x3A21, "pager"), (0x3A1E, "voice"), (0x3A02, "voice"),
    (0x3A57, "work,voice"), (0x3A2E, "voice"), (0x3A2D, "voice"), (0x3A4B, "text"), (0x3A1D, "voice"),
)
_ONE_OFF_PROVIDER = bytes.fromhex("812B1FA4BEA310199D6E00DD010F5402")
_WRAPPED_PROVIDER = bytes.fromhex("C091ADD3519DCF11A4A900AA0047FAA4")
_ONE_OFF_UNICODE = 0x8000


def _line(name: str, value: str, types: str = "") -> ContentLine:
    params: tuple[tuple[str, tuple[str, ...]], ...] = (("TYPE", (types,)),) if types else ()
    return ContentLine(name=name, value=value, params=params)


def _email(props: Properties, address_lid: int, kind_lid: int, original_lid: int) -> str:
    address = props.text(props.named(PSETID_ADDRESS, address_lid)).strip()
    kind = props.text(props.named(PSETID_ADDRESS, kind_lid)).strip().upper()
    if "@" in address and kind in ("SMTP", ""):
        return address
    original = props.text(props.named(PSETID_ADDRESS, original_lid)).strip()
    if "(" in original and original.endswith(")"):
        original = original[original.rfind("(") + 1 : -1]
    return original if "@" in original and " " not in original else ""


def email_slots(props: Properties) -> tuple[str, ...]:
    """The contact's Email1, Email2 and Email3 in their places (empty where there is none), for group members that name one of them."""
    return tuple(_email(props, address_lid, kind_lid, original_lid) for address_lid, kind_lid, original_lid in EMAILS)


def contact_emails(props: Properties) -> list[str]:
    found: list[str] = []
    for address in email_slots(props):
        if address and address not in found:
            found.append(address)
    return found


def _photo(message: Message) -> ContentLine | None:
    for attachment in message.attachments:
        data = attachment.data
        if attachment.props.boolean(CONTACT_PHOTO) and data and len(data) <= MAXIMUM_PHOTO_BYTES:
            media = attachment.props.text(ATTACH_MIME_TAG).strip().lower() or "image/jpeg"
            if not re.fullmatch(r"image/[a-z0-9.+-]{1,64}", media):
                media = "image/jpeg"
            return ContentLine(name="PHOTO", value=f"data:{media};base64,{base64.b64encode(data).decode('ascii')}")
    return None


def build_contact(message: Message, uid: str) -> str:
    """The contact as one vCard 4.0."""
    props = message.props
    lines: list[ContentLine] = [ContentLine(name="UID", value=f"urn:uuid:{uid}")]
    names = [props.text(tag).strip() for tag in (SURNAME, GIVEN_NAME, MIDDLE_NAME, PREFIX, SUFFIX)]
    full = props.text(DISPLAY_NAME).strip() or " ".join(part for part in (names[3], names[1], names[2], names[0], names[4]) if part)
    full = full or props.text(props.named(PSETID_ADDRESS, FILE_UNDER)).strip() or props.text(SUBJECT).strip()
    emails = contact_emails(props)
    company = props.text(COMPANY).strip()
    full = full or company or (emails[0] if emails else "")
    lines.append(ContentLine(name="FN", value=escape(full)))
    lines.append(ContentLine(name="N", value=";".join(escape(part) for part in names)))
    if props.text(NICKNAME).strip():
        lines.append(ContentLine(name="NICKNAME", value=escape(props.text(NICKNAME).strip())))
    for index, address in enumerate(emails):
        lines.append(ContentLine(name="EMAIL", value=escape(address), params=(("PREF", ("1",)),) if index == 0 else ()))
    seen_phones: set[str] = set()
    for tag, types in _PHONES:
        number = props.text(tag).strip()
        if number and number not in seen_phones:
            seen_phones.add(number)
            lines.append(_line("TEL", escape(number), types))
    for kind, tags in _POSTAL:
        parts = [props.text(tag).strip() for tag in tags]
        if kind == "work" and not any(parts):
            parts = [props.text(props.named(PSETID_ADDRESS, lid)).strip() for lid in WORK_ADDRESS_NAMED]
        if any(parts):
            lines.append(_line("ADR", ";".join([escape(parts[0]), "", *(escape(part) for part in parts[1:])]), kind))
    department = props.text(DEPARTMENT).strip()
    if company or department:
        lines.append(ContentLine(name="ORG", value=escape(company) + (f";{escape(department)}" if department else "")))
    if props.text(TITLE).strip():
        lines.append(ContentLine(name="TITLE", value=escape(props.text(TITLE).strip())))
    for tag, name in ((BIRTHDAY, "BDAY"), (ANNIVERSARY, "ANNIVERSARY")):
        moment = props.time(tag)
        if moment is not None:
            lines.append(ContentLine(name=name, value=nearest_date(moment).strftime("%Y%m%d")))
    urls: list[str] = []
    for url in (props.text(BUSINESS_HOME_PAGE), props.text(PERSONAL_HOME_PAGE), props.text(props.named(PSETID_ADDRESS, HTML_PAGE))):
        url = url.strip()
        if any(ord(character) < 0x21 or ord(character) == 0x7F for character in url):
            continue  # a web address never holds spaces or control characters; one that does is damaged and could break the card
        if url and url not in urls:
            urls.append(url)
            lines.append(ContentLine(name="URL", value=escape(url)))
    plain, _ = bodies(props)
    if plain and plain.strip():
        lines.append(ContentLine(name="NOTE", value=escape(plain.strip())))
    found_categories = categories(props)
    if found_categories:
        lines.append(ContentLine(name="CATEGORIES", value=",".join(escape(category) for category in found_categories)))
    photo = _photo(message)
    if photo is not None:
        lines.append(photo)
    modified = props.time(0x3008) or props.time(0x3007)  # never the time of the import: the same contact gives the same card every time
    if modified is not None:
        lines.append(ContentLine(name="REV", value=modified.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")))
    return format_card(lines, version="4.0")


def _terminated(data: bytes, position: int, unicode: bool) -> tuple[str, int]:
    if unicode:
        end = position
        while end + 1 < len(data) and data[end : end + 2] != b"\x00\x00":
            end += 2
        return data[position:end].decode("utf-16-le", errors="replace"), end + 2
    end = data.find(b"\x00", position)
    end = len(data) if end < 0 else end
    return data[position:end].decode("cp1252", errors="replace"), end + 1


def one_off(entry: bytes) -> tuple[str, str] | None:
    """The (name, address) of a one-off entry id (MS-OXCDATA 2.2.5.1); None for any other entry id or one that is not an internet address."""
    if len(entry) < 24 or entry[4:20] != _ONE_OFF_PROVIDER:
        return None
    unicode = bool(int.from_bytes(entry[22:24], "little") & _ONE_OFF_UNICODE)
    name, position = _terminated(entry, 24, unicode)
    kind, position = _terminated(entry, position, unicode)
    address, _ = _terminated(entry, position, unicode)
    if kind.strip().upper() != "SMTP" or "@" not in address:
        return None
    return name.strip(), address.strip()


def wrapped_node(entry: bytes) -> tuple[int, int] | None:
    """The node id of the contact a wrapped entry id points to (MS-OXCDATA 2.2.5.3: the last four bytes of the entry id inside) and which
    of its e-mail addresses it means (the type byte's bits 4-6: 0 Email1, 1 Email2, 2 Email3; the fax numbers above that)."""
    if len(entry) < 21 + 24 or entry[4:20] != _WRAPPED_PROVIDER:  # flags, provider, type, then an entry id of at least 24 bytes
        return None
    return int.from_bytes(entry[-4:], "little"), (entry[20] >> 4) & 0x07


type ContactLookup = Callable[[int], tuple[str, tuple[str, ...]] | None]  # node id -> (name, its Email1-3 in their places)


def _from_contact(found: tuple[str, tuple[str, ...]] | None, slot: int) -> tuple[str, str] | None:
    if found is None:
        return None
    name, slots = found
    address = slots[slot] if slot < len(slots) and slots[slot] else ""
    if not address and slot > 2:
        address = next((value for value in slots if value), "")  # a member kept by another of the contact's numbers: its first address
    return (name, address) if address else None


def group_members(props: Properties, lookup: ContactLookup) -> tuple[tuple[tuple[str, str], ...], int]:
    """The members of a distribution list as (name, address), and how many could not be given an internet address. Members kept as a
    pointer to a contact are looked up in the data file itself, so the same file always gives the same group."""
    one_offs = props.binaries(props.named(PSETID_ADDRESS, ONE_OFF_MEMBERS))
    entries = props.binaries(props.named(PSETID_ADDRESS, MEMBERS))
    members: list[tuple[str, str]] = []
    missing = 0
    for index in range(min(max(len(one_offs), len(entries)), MAXIMUM_MEMBERS)):
        found = one_off(one_offs[index]) if index < len(one_offs) else None
        if found is None and index < len(entries):
            found = one_off(entries[index])
            if found is None:
                pointer = wrapped_node(entries[index])
                found = _from_contact(lookup(pointer[0]), pointer[1]) if pointer is not None else None
        if found is None:
            missing += 1
        elif found not in members:
            members.append(found)
    return tuple(members), missing


def group_name(props: Properties) -> str:
    return props.text(DISPLAY_NAME).strip() or props.text(SUBJECT).strip()
