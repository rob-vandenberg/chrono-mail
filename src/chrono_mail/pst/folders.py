from collections.abc import Iterator
from dataclasses import dataclass, field

from chrono_mail.core.folder import FolderRole
from chrono_mail.core.pim import CollectionKind
from chrono_mail.pst.model import PstFolder
from chrono_mail.pst.properties import Properties

# --- Version ------------------------------------------------------------
__version__ = "folders.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial version: the data file's folders, their kinds and their roles, known by the data file's own pointers (OR-26)

MAXIMUM_DEPTH: int = 100
MAXIMUM_FOLDERS: int = 100_000

DISPLAY_NAME = 0x3001
CONTAINER_CLASS = 0x3613
HIDDEN = 0x10F4
# the message store's pointers (MS-OXOSFLD)
IPM_SUBTREE = 0x35E0
OUTBOX = 0x35E2
WASTEBASKET = 0x35E3
SENT_ITEMS = 0x35E4
# the Inbox's (and the root's) pointers
CALENDAR = 0x36D0
CONTACTS = 0x36D1
TASKS = 0x36D4
DRAFTS = 0x36D7
ADDITIONAL = 0x36D8  # PR_ADDITIONAL_REN_ENTRYIDS; the fifth is the junk folder
_INBOX_MARKS = (CALENDAR, CONTACTS, 0x36D2, 0x36D3, TASKS, DRAFTS, ADDITIONAL)
_JUNK_INDEX = 4
NO_NAME = "Unnamed folder"


@dataclass
class FolderNode:
    """One folder as read from the data file; handle is the reading library's own object, kept for reading its items."""

    node: int
    name: str
    props: Properties
    items: int
    handle: object = None
    children: list["FolderNode"] = field(default_factory=list["FolderNode"])


@dataclass(frozen=True, slots=True)
class PlacedFolder:
    folder: PstFolder
    node: FolderNode

    @property
    def readable(self) -> bool:
        return not self.folder.unsupported


def node_of(entry_id: bytes | None) -> int | None:
    """The node id an entry id points to: its last four bytes (MS-PST 2.2.2.8)."""
    if entry_id is None or len(entry_id) < 24:
        return None
    return int.from_bytes(entry_id[-4:], "little")


def kind_of(container_class: str) -> tuple[CollectionKind | None, str]:
    """The kind of a folder and, for one Chrono Mail has no place for, what it holds."""
    upper = container_class.strip().upper()
    if upper.startswith("IPF.APPOINTMENT"):
        return CollectionKind.EVENTS, ""
    if upper.startswith("IPF.CONTACT"):
        return CollectionKind.CONTACTS, ""
    if upper.startswith("IPF.TASK"):
        return CollectionKind.TASKS, ""
    if upper.startswith("IPF.STICKYNOTE"):
        return None, "notes"
    if upper.startswith("IPF.JOURNAL"):
        return None, "journal entries"
    return None, ""


def _walk(node: FolderNode, depth: int = 0) -> Iterator[FolderNode]:
    yield node
    if depth < MAXIMUM_DEPTH:
        for child in node.children:
            yield from _walk(child, depth + 1)


def _shown(top: FolderNode, depth: int = 0) -> Iterator[FolderNode]:
    """The folders below the top the person sees: hidden ones are left out with what is below them."""
    if depth < MAXIMUM_DEPTH:
        for child in top.children:
            if not child.props.boolean(HIDDEN):
                yield child
                yield from _shown(child, depth + 1)


def _find(root: FolderNode, wanted: int | None) -> FolderNode | None:
    if wanted is None:
        return None
    return next((node for node in _walk(root) if node.node == wanted), None)


# The names the office suite's mail program gives its standard folders in a data file it exports, in English and Dutch. The owner's
# exception to OR-26 (D-278): its own Export writes the file without the pointers to Sent, Drafts, Outbox, Junk and the main Calendar,
# Contacts and Tasks (only Inbox and Deleted Items keep theirs). For a data file only, and only for a role the file has no pointer for, a
# folder directly below the top takes that role by its name. Compared without case, after trimming.
STANDARD_NAMES: dict[str, FolderRole | CollectionKind] = {
    "inbox": FolderRole.INBOX, "postvak in": FolderRole.INBOX,
    "sent items": FolderRole.SENT, "verzonden items": FolderRole.SENT,
    "drafts": FolderRole.DRAFTS, "concepten": FolderRole.DRAFTS,
    "deleted items": FolderRole.TRASH, "verwijderde items": FolderRole.TRASH,
    "junk email": FolderRole.JUNK, "junk e-mail": FolderRole.JUNK, "ongewenste e-mail": FolderRole.JUNK,
    "outbox": FolderRole.OUTBOX, "postvak uit": FolderRole.OUTBOX,
    "calendar": CollectionKind.EVENTS, "agenda": CollectionKind.EVENTS,
    "contacts": CollectionKind.CONTACTS, "contactpersonen": CollectionKind.CONTACTS,
    "tasks": CollectionKind.TASKS, "taken": CollectionKind.TASKS,
}


def _holds_mail(node: FolderNode) -> bool:
    container = node.props.text(CONTAINER_CLASS).strip().upper()
    return container in ("", "IPF.NOTE") or container.startswith("IPF.NOTE.")


def _by_name(top: FolderNode, roles: dict[int, FolderRole], mains: dict[int, CollectionKind]) -> None:
    """Roles the file has no pointer for (or a pointer to no folder), given by name to the first fitting folder directly below the top
    (D-278). A pointer always wins; a folder with a role by pointer never takes another by name."""
    shown = list(_shown(top))  # a pointer to a hidden folder (the program's own settings) points nowhere the person can see
    taken_roles = {roles[node.node] for node in shown if node.node in roles and _holds_mail(node)}
    taken_kinds = {mains[node.node] for node in shown if node.node in mains and kind_of(node.props.text(CONTAINER_CLASS))[0] == mains[node.node]}
    for child in top.children:
        in_effect = (child.node in roles and _holds_mail(child)) or (
            child.node in mains and kind_of(child.props.text(CONTAINER_CLASS))[0] == mains[child.node])  # a pointer of the wrong kind gives nothing
        if child.props.boolean(HIDDEN) or in_effect:
            continue
        wanted = STANDARD_NAMES.get((child.name or "").strip().lower())
        if isinstance(wanted, FolderRole):
            if wanted not in taken_roles and _holds_mail(child):
                roles[child.node] = wanted
                taken_roles.add(wanted)
        elif isinstance(wanted, CollectionKind):
            if wanted not in taken_kinds and kind_of(child.props.text(CONTAINER_CLASS))[0] == wanted:
                mains[child.node] = wanted
                taken_kinds.add(wanted)


def place(store: Properties, root: FolderNode) -> list[PlacedFolder]:
    """The folders the person sees, below the top the store points to, depth first in the data file's order, each with its path, kind
    and role. Hidden folders (the program's own settings) are left out with what is below them."""
    top = _find(root, node_of(store.binary(IPM_SUBTREE))) or root
    roles: dict[int, FolderRole] = {}
    mains: dict[int, CollectionKind] = {}
    for tag, role in ((OUTBOX, FolderRole.OUTBOX), (WASTEBASKET, FolderRole.TRASH), (SENT_ITEMS, FolderRole.SENT)):
        target = node_of(store.binary(tag))
        if target is not None:
            roles.setdefault(target, role)
    inbox = next((node for node in _walk(top) if node is not top and any(node.props.has(tag) for tag in _INBOX_MARKS)), None)
    pointers = inbox.props if inbox is not None else root.props
    if inbox is not None:
        roles.setdefault(inbox.node, FolderRole.INBOX)
    for source in (pointers, root.props):
        drafts = node_of(source.binary(DRAFTS))
        if drafts is not None:
            roles.setdefault(drafts, FolderRole.DRAFTS)
        additional = source.binaries(ADDITIONAL)
        junk = node_of(additional[_JUNK_INDEX]) if len(additional) > _JUNK_INDEX else None
        if junk is not None:
            roles.setdefault(junk, FolderRole.JUNK)
        for tag, kind in ((CALENDAR, CollectionKind.EVENTS), (CONTACTS, CollectionKind.CONTACTS), (TASKS, CollectionKind.TASKS)):
            target = node_of(source.binary(tag))
            if target is not None:
                mains.setdefault(target, kind)
    _by_name(top, roles, mains)
    placed: list[PlacedFolder] = []

    def visit(node: FolderNode, path: tuple[str, ...], depth: int) -> None:
        for child in node.children:
            if len(placed) >= MAXIMUM_FOLDERS or child.props.boolean(HIDDEN):
                continue
            child_path = (*path, child.name or NO_NAME)
            kind, unsupported = kind_of(child.props.text(CONTAINER_CLASS))
            role = roles.get(child.node) if kind is None and not unsupported and _holds_mail(child) else None  # one test of a mail folder, as in _by_name
            main = kind is not None and mains.get(child.node) == kind
            placed.append(PlacedFolder(PstFolder(child_path, kind, role, main, unsupported, child.items), child))
            if depth < MAXIMUM_DEPTH:
                visit(child, child_path, depth + 1)

    visit(top, (), 0)
    return placed
