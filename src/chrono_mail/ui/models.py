from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
from typing import override

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QObject, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from chrono_mail.engine.commands_folders import FolderInfo
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.config import LOCAL_FOLDER_KINDS
from chrono_mail.ui.icons import folder_icon
from chrono_mail.ui.tagging import tag_icon

# --- Version ------------------------------------------------------------
__version__ = "models.py 2.0.289"

# --- Version history ----------------------------------------------------
# v2.0.289: Unread, Inbox, Flagged, then the standard folders in the owner's order (D-232)
# v2.0.288: One definition of the standard folders (standard_kind, in the order of LOCAL_FOLDER_KINDS) for the order of the list, Settings >
#           Sidebar and the icons: every standard folder, Snoozed and Recovered too, before every folder the person made (D-232; the owner)
# v1.9.274: R-19 round 4: the accounts and folders of the folder list are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.269: Drafts, Outbox, Scheduled and Snoozed show how many messages they hold (not sent or delivered yet), the other folders their unread messages (D-225)
# v1.6.261: pending_trees gone: the folder list is laid out once at start, with every place's folders
# v1.6.260: The message list moved to messagelist.py (R-15); this file holds the folder list
# v1.3.255: The message list always holds every column; the chosen ones come first, in their order, and the window hides the rest (R-13):
#           set_columns moves columns into place instead of a reset, so the rows, the selection, the current message and the scroll stay
# v1.1.251: The folder list is built once and never again (R-11): FolderTreeModel is a model of its own; populate() is given what should
#           be there (plan(), TreeEntry) and adds, removes, moves and changes only the entries that differ, with Qt's own signals, so the
#           selection, the open branches and the scroll stay; an entry is known by its key in the whole list (a pin by its id)
# v1.1.250: The folder list takes the window's folders as the engine typed them (any mapping of sequences, R-10)
# v1.1.242: Favorites and Tags views send the filters too (unread, flagged, attachments, Tagged with, the days and sizes of Advanced search);
#           the Tags view's own tag is combined_tag, tag is always Filter > Tagged with (R-03)
# v1.1.209: move_column: a dragged column moves in the list's own columns, the one record of their order (D-179)
# v1.1.208: SCHEDULED_TARGET_ROLES: where a scheduled message may be moved (D-178)
# v1.1.207: The fact changed: the row of a message changed in place shows what it is now, at its place (D-177)
# v1.1.204: Standard folders of Local Folders can be hidden (Settings > Sidebar) with the folders inside them, also when made later; the folder Show on startup chose stays shown
# v1.1.202: follow_used_favorites: an entry shown only once used comes or goes in place when its folder is made or deleted; the Favorites entry Scheduled (right after Outbox), shown only once a mailbox has a Scheduled folder, like Snoozed and Archive
# v1.1.200: The Scheduled folder: right after the Outbox, its own icon; like the Outbox it is never in the Unread view
# v1.1.192: pending_trees: at start, the accounts whose folders have not arrived are drawn only at the final build
# v1.1.191: Favorites in the owner's order with Snoozed, Unreplied and Archive (Snoozed and Archive only once used); account trees can be hidden; what Show on startup chose is always shown with its parent
# v1.1.188: the standard folders stay together at the top (Inbox, Drafts, Sent, Outbox, Archive, Junk, Trash), the own folders follow by name
# v1.1.181: the Tags section comes right after Favorites, before the account groups and the accounts
# v0.5.165: one question for places at a time, telling how far the list has heard (the engine counts only that); an arrival never makes an answer useless, so mail that streams in is placed while it comes (D-152)
# v0.5.164: an answer is used only when no fact about the view came after it was asked (_mark, D-151); every row says it never has children; the index override is gone
# v0.5.163: messages that come in at once are taken in with one question (message.places with headings); messages that went from a part not loaded give one rearrange, never a reset
# v0.5.162: a message read, unflagged or untagged in a view that shows only such messages stays there until the view is left, also after a sort or a fetch (the engine is told which, D-149)
# v0.5.161: header_rows_added: only the rows where new group headers came are announced (the window spans those, not all headers after every change)
# v0.5.160: rearrange: a new sort, and fetching the same view again, change the order in one layout change instead of a reset; Qt's selection moves with each message
# v0.5.158: the folder list counts again from its per-folder numbers without being rebuilt (recount), and adds, renames or removes one folder (D-146 step 4)
# v0.5.157: messages with an action under way (pending): greyed (PENDING_ROLE), let go when the fact arrives or the action ends; load_through for a search or Favorites view
# v0.5.156: the list applies the facts of the engine to its own rows (D-146 step 2): a folder keeps its rows as one ordered list with empty places for what is not loaded; an answer asked before a change that moved places is asked again
# v0.5.153: load_position: fetch the page of a message whose place is known (the selection kept after a sort)
# v0.5.139: A reload keeps the rows on screen until the new headers and first rows are there, then swaps them in at once (the list no longer goes blank after a delete)
# v0.5.135: Tags: the Tag column (drawn by the delegate), the tag colour as the text colour, replace_tags; a combined view of one tag; a list filtered by a tag; the Tags in the folder list
# v0.5.125: A list with messages that is reloaded does not say for a moment that it is empty
# v0.5.123: The Favorites heading cannot be selected; settled: the view has finished loading (D-119)
# v0.3.109: No group headers for the yes/no sorts (unread, flagged, attachment, replied, contact); names of the folder, conversation and preview groups
# v0.2.99: message_count (how many messages the view holds)
# v0.2.96: The rows of the group headers are kept in a list (the card layout measures every row)
# v0.2.94: Group headers in the message list (by the sort order), folded and unfolded; search and combined views in any sort order; sorting by To
# v0.1.89: The contact of a row (for the Contact column); grey icons in the headers of Contact and Delete
# v0.0.87: Folder list: the name without the number and never bold; the number of unread messages in its own role (drawn by the tree); Local Folders uses the home icon
# v0.0.85: A folder item keeps its plain name (for the window title)
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.54: A flag change redraws every column of the row
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.41: columns
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.17: messages can be dragged out of the list
# v0.0.14: icons in the folder tree
# v0.0.13: Initial folder and message list models

FOLDER_PATH_ROLE: int = Qt.ItemDataRole.UserRole + 2
ACCOUNT_ROLE: int = Qt.ItemDataRole.UserRole + 3
KIND_ROLE: int = Qt.ItemDataRole.UserRole + 4
UNREAD_ROLE: int = Qt.ItemDataRole.UserRole + 5
FOLDER_ROLE_ROLE: int = Qt.ItemDataRole.UserRole + 6
GROUP_ROLE: int = Qt.ItemDataRole.UserRole + 7
PIN_ROLE: int = Qt.ItemDataRole.UserRole + 8
NAME_ROLE: int = Qt.ItemDataRole.UserRole + 9  # the folder's name without the number of unread messages
COUNT_ROLE: int = Qt.ItemDataRole.UserRole + 10  # the number of unread messages the folder list draws at the right edge (0: none)


FAVORITES: tuple[tuple[str, str, str], ...] = (  # view id (message.combined), label, icon
    ("inboxes", "All Inboxes", "inbox"),  # the owner's order (2026-10-20)
    ("unread", "Unread", "unread"),
    ("outbox", "Outbox", "outbox"),
    ("scheduled", "Scheduled", "scheduled"),  # what waits to go out stays together (2026-10-20)
    ("sent", "Sent", "sent"),
    ("drafts", "Drafts", "drafts"),
    ("flagged", "Flagged", "flagged"),
    ("snoozed", "Snoozed", "snoozed"),
    ("unreplied", "Unreplied", "unreplied"),
    ("archive", "Archive", "archive"),
    ("junk", "Junk", "junk"),
    ("trash", "Trash", "trash"),
)
USED_ONLY: frozenset[str] = frozenset({"scheduled", "snoozed", "archive"})  # shown only when a mailbox has that folder (it is made when first used)
_FAVORITE_ROLE: dict[str, str] = {"inboxes": "inbox", "sent": "sent", "drafts": "drafts", "archive": "archive", "junk": "junk", "trash": "trash"}


ICON_NAME_ROLE: int = Qt.ItemDataRole.UserRole + 40  # what an entry's icon shows ("inbox", "tag:#rrggbb"): icons are compared by it, never as objects
TreeKey = tuple[str, str, str, str]  # kind, account, path, more (a group's or a pin's id): one entry of the folder list, unique in the whole list


@dataclass(eq=False)
class TreeEntry:
    """What one entry of the folder list should be: worked out from the accounts, their folders and the sidebar, never shown itself
    (FolderTreeModel.populate brings the list in line with it, R-11)."""

    key: TreeKey
    values: dict[int, object]
    selectable: bool = True
    children: list["TreeEntry"] = field(default_factory=list["TreeEntry"])

    def data(self, role: int) -> object:
        return self.values.get(role)

    def append(self, child: "TreeEntry") -> "TreeEntry":
        self.children.append(child)
        return child


class FolderNode:
    """One entry the folder list shows. It reads like a QStandardItem (data, text, index, parent, child …), so the window and
    the tests ask it the same way; only the model changes it."""

    def __init__(self, model: "FolderTreeModel", identity: int, parent: "FolderNode | None", entry: TreeEntry | None) -> None:
        self._model: FolderTreeModel = model
        self.identity: int = identity
        self.parent_node: FolderNode | None = parent
        self.children: list[FolderNode] = []
        self.key: TreeKey = entry.key if entry is not None else ("", "", "", "")
        self.values: dict[int, object] = dict(entry.values) if entry is not None else {}
        self.selectable: bool = entry.selectable if entry is not None else False

    def data(self, role: int = Qt.ItemDataRole.UserRole + 1) -> object:
        return self.values.get(role)

    def text(self) -> str:
        return str(self.values.get(Qt.ItemDataRole.DisplayRole, ""))

    def isSelectable(self) -> bool:  # noqa: N802 - the name QStandardItem gives it
        return self.selectable

    def hasChildren(self) -> bool:  # noqa: N802
        return bool(self.children)

    def rowCount(self) -> int:  # noqa: N802
        return len(self.children)

    def child(self, row: int) -> "FolderNode | None":
        return self.children[row] if 0 <= row < len(self.children) else None

    def parent(self) -> "FolderNode | None":
        """The entry above it; None at the top, as QStandardItem."""
        return self.parent_node if self.parent_node is not None and self.parent_node.parent_node is not None else None

    def row(self) -> int:
        return self.parent_node.children.index(self) if self.parent_node is not None else 0

    def index(self) -> QModelIndex:
        return self._model.index_of(self)


class FolderTreeModel(QAbstractItemModel):
    """Favorites, and then the accounts with their folders and the number of unread messages.

    Built once and never again (R-11): populate() is given what should be there and changes only the entries that differ, in place
    (added, removed, moved, their text and numbers), with Qt's own signals for each, so the selection, the open branches and the
    scroll position stay by themselves. An entry is known by its key everywhere in the list, so one that moves (a pin moved up, an
    account into a group, a renamed folder) is moved, never taken away and made again."""

    show_favorites: bool = True
    show_local_folders: bool = True
    show_tags: bool = True
    leaving = Signal(QModelIndex, int, int)  # these rows of this parent are about to go: told before Qt hears it (its selection moves on its own then)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tags: list[tuple[str, str]] = []  # (name, #rrggbb) of the tags for mail, in the person's order: the Tags at the bottom
        self.pins: list[tuple[str, str, str, str]] = []  # pin id, account, folder, name
        self.groups: list[tuple[str, str, tuple[str, ...]]] = []  # group id, name, account ids
        self.hidden: set[str] = set()  # standard favorites that are switched off
        self.hidden_trees: set[str] = set()  # accounts whose own folder tree is switched off (Settings > Sidebar)
        # What Show on startup chose is always shown, with its parent, whatever is switched off (Settings > Sidebar):
        self.forced: set[str] = set()  # a standard favorite
        self.force_favorites: bool = False
        self.force_tags: bool = False
        self.forced_trees: set[str] = set()  # an account (or Local Folders) whose folder or view was chosen
        self.hidden_local: set[str] = set()  # standard folders of Local Folders that are switched off (LOCAL_FOLDER_KINDS)
        self.forced_folders: set[tuple[str, str]] = set()  # (account, folder) chosen by Show on startup: shown with its parents
        self.mailbox_ids: list[str] = []  # the places where mail is kept that the list shows (for the Favorites numbers)
        self._local_id: str = ""  # Local Folders, as the last populate found it
        self._next_identity: int = 1
        self._root: FolderNode = FolderNode(self, 0, None, None)
        self._nodes: dict[int, FolderNode] = {0: self._root}  # by identity: a QModelIndex carries the identity, never the node itself

    # --- the Qt model -----------------------------------------------------------------------------------------------------------------

    def _node(self, index: QModelIndex | QPersistentModelIndex) -> FolderNode:
        return self._nodes.get(index.internalId(), self._root) if index.isValid() else self._root

    def itemFromIndex(self, index: QModelIndex | QPersistentModelIndex) -> FolderNode | None:  # noqa: N802 - as QStandardItemModel names it
        node = self._nodes.get(index.internalId()) if index.isValid() else None
        return node if node is not self._root else None

    def index_of(self, node: FolderNode) -> QModelIndex:
        if node is self._root or node.parent_node is None:
            return QModelIndex()
        return self.createIndex(node.row(), 0, node.identity)

    @override
    def index(self, row: int, column: int = 0, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:
        holder = self._node(parent)
        if column != 0 or not 0 <= row < len(holder.children):
            return QModelIndex()
        return self.createIndex(row, 0, holder.children[row].identity)

    @override
    def parent(self, child: QModelIndex | QPersistentModelIndex) -> QModelIndex:  # type: ignore[override]  # Qt's other parent() is QObject's
        node = self._nodes.get(child.internalId()) if child.isValid() else None
        if node is None or node.parent_node is None or node.parent_node is self._root:
            return QModelIndex()
        return self.index_of(node.parent_node)

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self._node(parent).children)

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 1

    @override
    def hasChildren(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> bool:
        return bool(self._node(parent).children)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        node = self._nodes.get(index.internalId()) if index.isValid() else None
        return None if node is None else node.values.get(role)

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        node = self._nodes.get(index.internalId()) if index.isValid() else None
        if node is None:
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
        return flags | Qt.ItemFlag.ItemIsSelectable if node.selectable else flags

    def item(self, row: int) -> FolderNode | None:
        """The entry at this row of the top level (as QStandardItemModel.item)."""
        return self._root.child(row)

    def findItems(self, text: str, flags: Qt.MatchFlag = Qt.MatchFlag.MatchExactly, column: int = 0) -> list[FolderNode]:  # noqa: N802
        """The entries with exactly this text (with MatchRecursive at every depth, else at the top level only)."""
        found: list[FolderNode] = []
        waiting = list(self._root.children)
        while waiting:
            node = waiting.pop(0)
            if node.text() == text:
                found.append(node)
            if flags & Qt.MatchFlag.MatchRecursive:
                waiting.extend(node.children)
        return found

    # --- what should be there ---------------------------------------------------------------------------------------------------------

    def plan(self, accounts: Sequence[AccountSummary], folders: Mapping[str, Sequence[FolderInfo]], everything: bool = False) -> list[TreeEntry]:
        """The entries of the top level, with everything below them, as the list should show them. everything: as it would be with
        nothing switched off in Settings > Sidebar (for Show on startup, which also lists what is hidden)."""
        top = TreeEntry(("", "", "", ""), {})
        local_id = next((account.account_id for account in accounts if account.kind == "local"), "")
        hidden_tops = set() if everything else {
            info.path for info in folders.get(local_id, []) if self._hides(local_id, info.path, info.role)
        }
        hidden_trees = set() if everything else self.hidden_trees - self.forced_trees
        # Accounts that keep their mail in Local Folders have no folders of their own: Local Folders shows them (and counts them once).
        places = [account for account in accounts if account.storage != "local"]
        places.sort(key=lambda account: account.kind == "local")  # Local Folders last
        if not everything and not self.show_local_folders:
            places = [account for account in places if account.kind != "local" or account.account_id in self.forced_trees]
        mailboxes = places
        mailbox_ids = [account.account_id for account in mailboxes]
        # A hidden account tree hides only the tree: its mail is still in Favorites and Tags.
        places = [account for account in places if account.account_id not in hidden_trees]
        if mailboxes and (everything or self.show_favorites or self.force_favorites):
            top.append(self._favorites(mailbox_ids, folders, everything))
        if mailboxes and (everything or self.show_tags or self.force_tags) and self.tags:
            top.append(self._tags_entry())  # right after Favorites
        members = {member for _, _, accounts_of in self.groups for member in accounts_of}
        shown = {account.account_id: account for account in places}
        known = {account.account_id for account in accounts}
        for group_id, name, member_ids in self.groups:
            present = [shown[member] for member in member_ids if member in shown]
            if not present and not any(member in known for member in member_ids):
                continue
            if not present and any(member in hidden_trees for member in member_ids):
                continue  # every account of the group is hidden: so is the group
            top.append(self._group(group_id, name, present, member_ids, folders, local_id, hidden_tops))
        for account in places:
            if account.account_id not in members:
                top.append(self._account_entry(account, folders, local_id, hidden_tops))
        return top.children

    @staticmethod
    def _heading(title: str, kind: str, key: TreeKey, selectable: bool = True) -> TreeEntry:
        font = QFont()
        font.setBold(True)
        values: dict[int, object] = {Qt.ItemDataRole.DisplayRole: title, KIND_ROLE: kind, ACCOUNT_ROLE: "", Qt.ItemDataRole.FontRole: font}
        return TreeEntry(key, values, selectable)

    def _tags_entry(self) -> TreeEntry:
        """Tags: one entry per tag, showing the mail of every mailbox that carries it (the combined view "tagged")."""
        section = self._heading("Tags", "section", ("section", "", "Tags", ""), selectable=False)
        for name, colour in self.tags:
            section.append(TreeEntry(("tagview", "", name, ""), {
                Qt.ItemDataRole.DisplayRole: name, NAME_ROLE: name, COUNT_ROLE: 0, Qt.ItemDataRole.DecorationRole: tag_icon(QColor(colour)), ICON_NAME_ROLE: f"tag:{colour}",
                KIND_ROLE: "tagview", ACCOUNT_ROLE: "", FOLDER_PATH_ROLE: name,
            }))
        return section

    def _group(
        self, group_id: str, name: str, present: list[AccountSummary], member_ids: tuple[str, ...], folders: Mapping[str, Sequence[FolderInfo]], local_id: str,
        hidden_tops: set[str],
    ) -> TreeEntry:
        """A named set of accounts: one combined inbox, then the accounts."""
        node = self._heading(name, "group", ("group", "", "", group_id))
        node.values[GROUP_ROLE] = group_id
        node.values[Qt.ItemDataRole.DecorationRole], node.values[ICON_NAME_ROLE] = folder_icon("account"), "account"
        unread = sum(info.unread for member in member_ids for info in folders.get(member, []) if info.role == "inbox")
        node.append(TreeEntry(("groupinbox", "", group_id, group_id), {
            Qt.ItemDataRole.DisplayRole: "All Inboxes", NAME_ROLE: "All Inboxes", COUNT_ROLE: unread, Qt.ItemDataRole.DecorationRole: folder_icon("inbox"), ICON_NAME_ROLE: "inbox",
            KIND_ROLE: "groupinbox", ACCOUNT_ROLE: "", FOLDER_PATH_ROLE: group_id, GROUP_ROLE: group_id, FOLDER_ROLE_ROLE: "inbox",  # the path tells the groups' inboxes apart
        }))
        for account in present:
            node.append(self._account_entry(account, folders, local_id, hidden_tops))
        return node

    def _account_entry(self, account: AccountSummary, folders: Mapping[str, Sequence[FolderInfo]], local_id: str, hidden_tops: set[str]) -> TreeEntry:
        account_id = account.account_id
        local = account.kind == "local"
        top = TreeEntry(("account", account_id, "", ""), {
            Qt.ItemDataRole.DisplayRole: "Local Folders" if local else account.shown_name,
            Qt.ItemDataRole.DecorationRole: folder_icon("local" if local else "account"), ICON_NAME_ROLE: "local" if local else "account",
            KIND_ROLE: "account", ACCOUNT_ROLE: account_id,
        })
        unread, flagged = (
            TreeEntry((kind, account_id, "", ""), {Qt.ItemDataRole.DisplayRole: label, Qt.ItemDataRole.DecorationRole: folder_icon(kind), ICON_NAME_ROLE: kind, KIND_ROLE: kind, ACCOUNT_ROLE: account_id})
            for kind, label in (("unread", "Unread"), ("flagged", "Flagged"))
        )
        top.append(unread)
        nodes: dict[tuple[str, ...], TreeEntry] = {}
        ordered = sorted(folders.get(account_id, []), key=lambda info: (_order(info.role, info.path), info.path.lower()))
        for info in ordered:
            path = tuple(info.path.split("/"))
            if account_id == local_id and path[0] in hidden_tops:
                continue  # switched off in Settings > Sidebar, with the folders inside it
            for depth in range(1, len(path)):  # parents that exist only as part of a path
                if path[:depth] not in nodes:
                    nodes[path[:depth]] = (nodes[path[:depth - 1]] if depth > 1 else top).append(self._folder_entry(account_id, path[:depth], "other", 0, 0))
            role, unread = info.role, info.unread
            entry = self._folder_entry(account_id, path, role, unread, shown_number(info))
            if path in nodes:  # made earlier as a parent: it takes its real role and number now
                nodes[path].values.update(entry.values)
            else:
                nodes[path] = (nodes[path[:-1]] if len(path) > 1 else top).append(entry)
        inbox = next((number for number, child in enumerate(top.children) if child.data(FOLDER_ROLE_ROLE) == "inbox"), 0)
        top.children.insert(inbox + 1, flagged)  # Unread, Inbox, Flagged (the owner, D-232); without an Inbox right after Unread
        return top

    def _favorites(self, mailbox_ids: list[str], folders: Mapping[str, Sequence[FolderInfo]], everything: bool) -> TreeEntry:
        """The same folder of every mailbox in one list: all inboxes, all unread messages and so on."""
        section = self._heading("Favorites", "section", ("section", "", "Favorites", ""), selectable=False)  # a heading: the list never shows it
        for view, label, icon in FAVORITES:
            if not everything and view in self.hidden and view not in self.forced:
                continue
            if view in USED_ONLY and not favorite_in_use(view, mailbox_ids, folders):
                continue
            section.append(self._favorite_entry(view, label, icon, mailbox_ids, folders))
        known = set(mailbox_ids)
        for pin_id, account_id, folder, name in self.pins:
            if account_id not in known:
                continue
            info = next((i for i in folders.get(account_id, []) if i.path == folder), None)
            if info is None:
                continue  # the folder is gone: the pin stays in the file, but there is nothing to show
            unread, role = info.unread, info.role
            shown = name or folder.rsplit("/", 1)[-1]
            section.append(TreeEntry(("pinlink", "", "", pin_id), {  # known by the pin: a renamed folder keeps its entry
                Qt.ItemDataRole.DisplayRole: shown, NAME_ROLE: shown, COUNT_ROLE: shown_number(info), UNREAD_ROLE: unread,
                Qt.ItemDataRole.DecorationRole: folder_icon(_icon_kind(folder, role)), ICON_NAME_ROLE: _icon_kind(folder, role), KIND_ROLE: "pinlink", ACCOUNT_ROLE: account_id,
                FOLDER_PATH_ROLE: folder, FOLDER_ROLE_ROLE: role, PIN_ROLE: pin_id,
            }))
        return section

    @staticmethod
    def _favorite_entry(view: str, label: str, icon: str, mailbox_ids: list[str], folders: Mapping[str, Sequence[FolderInfo]]) -> TreeEntry:
        unread = favorite_unread(view, mailbox_ids, folders)
        return TreeEntry(("favorite", "", view, ""), {
            Qt.ItemDataRole.DisplayRole: label, NAME_ROLE: label, COUNT_ROLE: favorite_number(view, mailbox_ids, folders), UNREAD_ROLE: unread, Qt.ItemDataRole.DecorationRole: folder_icon(icon),
            ICON_NAME_ROLE: icon, KIND_ROLE: "favorite", ACCOUNT_ROLE: "", FOLDER_PATH_ROLE: view, FOLDER_ROLE_ROLE: _FAVORITE_ROLE.get(view, "other"),
        })

    @staticmethod
    def _folder_entry(account_id: str, path: tuple[str, ...], role: str, unread: int, count: int) -> TreeEntry:
        joined = "/".join(path)
        return TreeEntry(("folder", account_id, joined, ""), {
            Qt.ItemDataRole.DisplayRole: path[-1], NAME_ROLE: path[-1], COUNT_ROLE: count, UNREAD_ROLE: unread,  # the number is drawn at the right edge, in bold (D-225)
            Qt.ItemDataRole.DecorationRole: folder_icon(_icon_kind(joined, role)), ICON_NAME_ROLE: _icon_kind(joined, role), KIND_ROLE: "folder", ACCOUNT_ROLE: account_id,
            FOLDER_PATH_ROLE: joined, FOLDER_ROLE_ROLE: role,
        })

    def _hides(self, local_id: str, path: str, role: str) -> bool:
        """Whether this folder of Local Folders is one of its standard folders that is switched off, and Show on startup did not
        choose it or a folder inside it."""
        if local_folder_kind(path, role) not in self.hidden_local:
            return False
        return not any(account == local_id and (chosen == path or chosen.startswith(path + "/")) for account, chosen in self.forced_folders)

    # --- bringing the list in line, in place --------------------------------------------------------------------------------------------

    def populate(self, accounts: Sequence[AccountSummary], folders: Mapping[str, Sequence[FolderInfo]]) -> list[FolderNode]:
        """Bring the list in line with the accounts, their folders and the sidebar: only what differs changes (R-11). Returns the
        entries that are new (with everything below them), so the window can open them as the person left them."""
        self._local_id = next((account.account_id for account in accounts if account.kind == "local"), "")
        places = [account for account in accounts if account.storage != "local"]
        if not self.show_local_folders:
            places = [account for account in places if account.kind != "local" or account.account_id in self.forced_trees]
        self.mailbox_ids = [account.account_id for account in places]
        wanted = self.plan(accounts, folders)
        known: dict[TreeKey, FolderNode] = {}
        waiting = list(self._root.children)
        while waiting:
            node = waiting.pop()
            known[node.key] = node
            waiting.extend(node.children)
        placed: set[int] = set()
        new: list[FolderNode] = []
        self._line_up(self._root, wanted, known, placed, new)
        for node in self._every_node():  # what is no longer wanted anywhere goes, deepest first
            if node.identity not in placed:
                self._take_away(node)
        return new

    def _line_up(self, holder: FolderNode, wanted: list[TreeEntry], known: dict[TreeKey, FolderNode], placed: set[int], new: list[FolderNode]) -> None:
        for position, entry in enumerate(wanted):
            node = known.get(entry.key)
            if node is not None and node.identity in self._nodes and node.identity not in placed and node.parent_node is not None:
                source = node.parent_node
                row = source.children.index(node)
                if source is not holder or row != position:
                    if source is holder and row < position:  # cannot happen: the rows before position are placed already
                        raise AssertionError("folder list out of order")
                    target = position if source is holder else min(position, len(holder.children))
                    self.beginMoveRows(self.index_of(source), row, row, self.index_of(holder), target)
                    source.children.pop(row)
                    node.parent_node = holder
                    holder.children.insert(target, node)
                    self.endMoveRows()
                self._take_values(node, entry)
                placed.add(node.identity)
                self._line_up(node, entry.children, known, placed, new)
            else:  # new: made empty, then its entries lined up like any other (one that exists elsewhere moves in, never made twice)
                target = min(position, len(holder.children))
                self.beginInsertRows(self.index_of(holder), target, target)
                made = FolderNode(self, self._next_identity, holder, entry)
                self._next_identity += 1
                self._nodes[made.identity] = made
                holder.children.insert(target, made)
                self.endInsertRows()
                placed.add(made.identity)
                new.append(made)
                self._line_up(made, entry.children, known, placed, new)

    @staticmethod
    def _same(first: dict[int, object], second: dict[int, object]) -> bool:
        """The same entry as shown: every value but the icon object (compared by its name) and the font (headings only, always bold)."""
        skipped = (Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.FontRole)
        return first.keys() == second.keys() and all(first[role] == second[role] for role in first if role not in skipped)

    def _take_values(self, node: FolderNode, entry: TreeEntry) -> None:
        if not self._same(node.values, entry.values) or node.selectable != entry.selectable:
            node.values, node.selectable = dict(entry.values), entry.selectable
            index = self.index_of(node)
            self.dataChanged.emit(index, index)

    def _take_away(self, node: FolderNode) -> None:
        holder = node.parent_node
        if holder is None or node.identity not in self._nodes:
            return
        row = holder.children.index(node)
        self.leaving.emit(self.index_of(holder), row, row)
        self.beginRemoveRows(self.index_of(holder), row, row)
        holder.children.pop(row)
        self.endRemoveRows()
        waiting = [node]
        while waiting:
            gone = waiting.pop()
            self._nodes.pop(gone.identity, None)
            waiting.extend(gone.children)

    def _every_node(self) -> list[FolderNode]:
        """Every entry, the ones below an entry before it (so taking away goes deepest first)."""
        found: list[FolderNode] = []

        def walk(node: FolderNode) -> None:
            for child in node.children:
                walk(child)
                found.append(child)

        walk(self._root)
        return found

    def _all_items(self) -> list[FolderNode]:
        return [node for node in self._every_node() if node.identity in self._nodes]

    def find_item(self, account_id: str, kind: str, path: str) -> FolderNode | None:
        return next((node for node in self._all_items() if node.values.get(KIND_ROLE) == kind and str(node.values.get(ACCOUNT_ROLE) or "") == account_id
                     and str(node.values.get(FOLDER_PATH_ROLE) or "") == path), None)

    def rename_folder(self, account_id: str, old: str, new: str) -> None:
        """A folder got another name (or another place): its entry and those below it take the new names now, so the next populate
        moves them where they belong instead of making them again (the selection and the open branches stay with them)."""
        for node in self._all_items():
            path = str(node.values.get(FOLDER_PATH_ROLE) or "")
            if node.key[0] != "folder" or node.key[1] != account_id or not (path == old or path.startswith(old + "/")):
                continue
            renamed = new + path[len(old):]
            node.key = ("folder", account_id, renamed, "")
            node.values[FOLDER_PATH_ROLE] = renamed
            node.values[Qt.ItemDataRole.DisplayRole] = node.values[NAME_ROLE] = renamed.rsplit("/", 1)[-1]
            index = self.index_of(node)
            self.dataChanged.emit(index, index)

    def recount(self, folders: Mapping[str, Sequence[FolderInfo]]) -> None:
        """Every number of the list again from the numbers per folder, as plan() works them out; nothing else changes."""
        numbers_of = {(account_id, info.path): (info.unread, shown_number(info)) for account_id, infos in folders.items() for info in infos}
        for node in self._all_items():
            kind = str(node.values.get(KIND_ROLE) or "")
            if kind in ("folder", "pinlink"):
                unread, count = numbers_of.get((str(node.values.get(ACCOUNT_ROLE) or ""), str(node.values.get(FOLDER_PATH_ROLE) or "")), (0, 0))
            elif kind == "favorite":
                view = str(node.values.get(FOLDER_PATH_ROLE) or "")
                unread, count = favorite_unread(view, self.mailbox_ids, folders), favorite_number(view, self.mailbox_ids, folders)
            elif kind == "groupinbox":
                members = next((accounts for group_id, _, accounts in self.groups if group_id == node.values.get(GROUP_ROLE)), ())
                unread = count = sum(info.unread for member in members for info in folders.get(member, []) if info.role == "inbox")
            else:
                continue
            if node.values.get(COUNT_ROLE) != count or node.values.get(UNREAD_ROLE) != unread:
                node.values[COUNT_ROLE], node.values[UNREAD_ROLE] = count, unread
                index = self.index_of(node)
                self.dataChanged.emit(index, index)


def favorite_in_use(view: str, mailbox_ids: list[str], folders: Mapping[str, Sequence[FolderInfo]]) -> bool:
    """Whether a favorite that is shown only once used (Scheduled, Snoozed, Archive) has a folder in one of these mailboxes."""
    for account_id in mailbox_ids:
        for info in folders.get(account_id, []):
            role, path = info.role, info.path
            if (view == "scheduled" and path == "Scheduled") or (view == "snoozed" and path == "Snoozed") or (view == "archive" and role == "archive"):
                return True
    return False


def favorite_unread(view: str, mailbox_ids: list[str], folders: Mapping[str, Sequence[FolderInfo]], number: str = "unread") -> int:
    """The unread messages (or, with number "messages", all messages) of a standard favorite's kind of folder in every mailbox shown."""
    found = 0
    for account_id in mailbox_ids:
        for info in folders.get(account_id, []):
            role, path = info.role, info.path
            if (view in _FAVORITE_ROLE and role == _FAVORITE_ROLE[view]) or (view in LOCAL_PLACES.values() and LOCAL_PLACES.get(path) == view) or (view == "snoozed" and path == "Snoozed"):
                found += info.messages if number == "messages" else info.unread
            elif view == "unread" and role not in ("trash", "junk") and path not in LOCAL_PLACES:
                found += info.messages if number == "messages" else info.unread
    return found


# Folders of outgoing messages: every message in them waits to be sent or delivered again (a snoozed one waits to be delivered
# to its folder again, the owner), so the folder list shows how many they hold; every other folder shows its unread messages (D-225).
OUTGOING_FAVORITES: frozenset[str] = frozenset({"drafts", "outbox", "scheduled", "snoozed"})


def holds_outgoing(path: str, role: str) -> bool:
    """Whether a folder holds outgoing messages (D-225): Drafts, Outbox, Scheduled, Snoozed."""
    return role in ("drafts", "outbox") or path in ("Outbox", "Scheduled", "Snoozed")


def shown_number(info: FolderInfo) -> int:
    """The number the folder list shows for a folder (D-225): the messages not sent yet in a folder of outgoing messages, else
    the unread messages."""
    outgoing = holds_outgoing(info.path, info.role)
    return info.messages if outgoing else info.unread


def favorite_number(view: str, mailbox_ids: list[str], folders: Mapping[str, Sequence[FolderInfo]]) -> int:
    """The number a standard favorite shows (D-225): of Drafts, Outbox, Scheduled and Snoozed every message not sent yet, of the
    others the unread messages."""
    return favorite_unread(view, mailbox_ids, folders, "messages" if view in OUTGOING_FAVORITES else "unread")


SCHEDULED_TARGET_ROLES: tuple[str, ...] = ("drafts", "trash")  # where a scheduled message may be moved: back to drafts, or away (D-178)
LOCAL_PLACES: dict[str, str] = {"Outbox": "outbox", "Scheduled": "scheduled"}  # folders that exist only here, with no role on a server


# The folders the program makes itself without a role, known by their place (a server gives them none)
_KIND_BY_PATH: dict[str, str] = {"Outbox": "outbox", "Scheduled": "scheduled", "Snoozed": "snoozed", "Recovered": "recovered"}


def standard_kind(path: str, role: str) -> str:
    """Which standard folder this is (one of LOCAL_FOLDER_KINDS), or "" for a folder the person made: by its role, or by its place for
    the folders the program makes without a role. The one definition the order of the list, Settings > Sidebar and the icons use (D-232)."""
    kind = _KIND_BY_PATH.get(path, role)
    return kind if kind in LOCAL_FOLDER_KINDS else ""


def _order(role: str, path: str = "") -> int:
    """Every standard folder first, in the order of LOCAL_FOLDER_KINDS (Flagged goes right after the Inbox); the person's own folders follow by name (D-232)."""
    kind = standard_kind(path, role)
    return LOCAL_FOLDER_KINDS.index(kind) if kind else len(LOCAL_FOLDER_KINDS)


def local_folder_kind(path: str, role: str) -> str:
    """Which standard folder of Local Folders this is (one of LOCAL_FOLDER_KINDS), or "" for the person's own folders and
    every folder inside another."""
    return "" if "/" in path else standard_kind(path, role)


def _icon_kind(path: str, role: str) -> str:
    return standard_kind(path, role) or role
