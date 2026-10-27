import uuid

from chrono_mail.pst.calendar import build_event, build_task
from chrono_mail.pst.contacts import ContactLookup, build_contact, group_members, group_name
from chrono_mail.pst.headers import MESSAGE_CLASS, categories, flags
from chrono_mail.pst.mail import rebuild
from chrono_mail.pst.model import PstContact, PstEvent, PstFailure, PstGroup, PstItem, PstMail, PstTask
from chrono_mail.pst.properties import Message

# --- Version ------------------------------------------------------------
__version__ = "convert.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: one item of the data file as the import's plain data, by its message class

_NAMESPACE = uuid.UUID("5d7f3c1e-8a52-4c1b-9f0e-2b6f0a4c9d31")  # this import's own: the same item gets the same id on every import
_MAIL_CLASSES = ("IPM.NOTE", "IPM.POST", "IPM.SCHEDULE.", "REPORT.", "IPM.SHARING", "IPM.TASKREQUEST", "IPM.RECALL", "IPM.OUTLOOK.RECALL", "IPM.DOCUMENT")
_NOT_HERE: dict[str, str] = {"IPM.STICKYNOTE": "a note", "IPM.ACTIVITY": "a journal entry"}


def item_uid(message: Message, store_key: bytes, position: int = 0) -> str:
    """A stable id for an event, task or contact: made from the data file's own key and the item's node id, which is unique in the file
    and the same on every import. Never from the search key or a meeting's global id: a copy keeps those, and two copies with one id
    would make the import keep only one."""
    seed = message.node.to_bytes(8, "little") if message.node else b"position" + position.to_bytes(8, "little")  # no node id read: its place
    return str(uuid.uuid5(_NAMESPACE, (store_key + seed).hex()))


def kind_of_class(message_class: str) -> str:
    """mail, event, task, contact, group, or what the item is when Chrono Mail has no place for it."""
    upper = message_class.strip().upper()
    if upper.startswith("IPM.APPOINTMENT"):
        return "event"
    if upper == "IPM.TASK" or upper.startswith("IPM.TASK."):
        return "task"
    if upper.startswith("IPM.CONTACT"):
        return "contact"
    if upper.startswith("IPM.DISTLIST"):
        return "group"
    if upper in ("", "IPM") or upper.startswith(_MAIL_CLASSES):
        return "mail"
    for prefix, what in _NOT_HERE.items():
        if upper.startswith(prefix):
            return what
    return f"an item of a kind Chrono Mail cannot import: {message_class.strip()[:80]}"


_WORDS = {"mail": "a message", "event": "an event", "task": "a task", "contact": "a contact", "group": "a contact group"}


def _no_contacts(node: int) -> tuple[str, tuple[str, ...]] | None:
    return None


def convert(
    message: Message, folder: tuple[str, ...], store_key: bytes, lookup: ContactLookup = _no_contacts, position: int = 0,
) -> list[PstItem]:
    """The item as the import's data: one item, followed by a failure when part of it could not be read. Never raises."""
    kind = kind_of_class(message.props.text(MESSAGE_CLASS))
    try:
        items = _convert(message, kind, folder, store_key, lookup, position)
    except Exception as error:  # noqa: BLE001 - one bad item is a failure for that item only (AGENTS.md 4.2); the reason names the kind, never its contents
        return [PstFailure(folder, f"{_WORDS.get(kind, 'an item')} that could not be read ({type(error).__name__})")]
    if message.unread_attachments and items and not isinstance(items[0], PstFailure):
        items.append(PstFailure(folder, f"{_WORDS.get(kind, 'an item')} of which {message.unread_attachments} attachment(s) could not be read"))
    return items


def _convert(message: Message, kind: str, folder: tuple[str, ...], store_key: bytes, lookup: ContactLookup, position: int) -> list[PstItem]:
    props = message.props
    if kind == "mail":
        raw, notes = rebuild(message)
        mail: list[PstItem] = [PstMail(folder, raw, flags(props), categories(props))]
        return mail + [PstFailure(folder, f"a message kept without part of it: {note}") for note in notes]
    if kind == "event":
        return [PstEvent(folder, build_event(message, item_uid(message, store_key, position)), categories(props))]
    if kind == "task":
        return [PstTask(folder, build_task(message, item_uid(message, store_key, position)), categories(props))]
    if kind == "contact":
        return [PstContact(folder, build_contact(message, item_uid(message, store_key, position)), categories(props))]
    if kind == "group":
        members, missing = group_members(props, lookup)
        items: list[PstItem] = [PstGroup(folder, group_name(props), members)]
        if missing:
            items.append(PstFailure(folder, f"a contact group of which {missing} member(s) have no internet address that could be read"))
        return items
    return [PstFailure(folder, kind)]
