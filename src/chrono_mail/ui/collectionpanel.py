import dataclasses
from collections.abc import Callable
from typing import Literal

from typing import override

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.changes import Change
from chrono_mail.core import ChronoMailError
from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR
from chrono_mail.engine.commands_groups import GroupRow
from chrono_mail.engine.commands_pim import COLLECTION_CREATE, COLLECTION_DELETE, COLLECTION_EDIT, COLLECTION_LIST, CollectionSummary, CreateParameters, ListParameters
from chrono_mail.engine.commands_pim import DeleteParameters as CollectionDelete
from chrono_mail.engine.commands_pim import EditParameters as CollectionEdit
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.deletekey import DeleteKey
from chrono_mail.ui.dragdata import read_contacts_mime_data
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.newcollection import NewCollectionDialog
from chrono_mail.ui.syncmarks import CONFLICT, mark_icon
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "collectionpanel.py 4.2.438"

# --- Version history ----------------------------------------------------
# v4.2.438: a calendar fact brings its timed_background (UR-02, D-297)
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.10.277: Contact groups under their address book (R-39): kept in step in place (set_groups), their menu and Delete key given by the Contacts
#            page, contacts dropped on a group; the chosen group's address book is the one chosen (current_id)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: every engine call typed
# v1.9.265: A collection made or changed takes its palette and read-only state from the fact (Change.palette, read_only); the whole list is never asked again for one change (take_infos is gone, R-44, D-146)
# v1.7.262: Delete and Shift+Delete in the list delete the calendar, address book or task list selected (it asks first), never its items (R-43)
# v1.1.236: New … asks the name and the Account, set to the account right-clicked (made on its server first, R-29, D-198)
# v1.1.235: Delete of a server's collection asks whether to delete it on the server (every program that synchronises with it follows) and names what goes with it; greyed for a read-only one, with a hover text (R-28)
# v1.1.229: The Sync conflicts entry at the top, under the heading (the owner)
# v1.1.226: The Sync conflicts entry under the list, only while there are any (D-194)
# v1.1.221: Rename… and Colour… greyed for a read-only collection of a server (D-192)
# v1.1.219: Edit colours… for a calendar; a calendar's palette is taken in again when it changed (infos_changed, D-191)
# v1.1.212: Add account… at the top of the menu (D-185)
# v1.1.172: The colour of a collection without one from the colour file (D-158)
# v0.5.159: follows the facts (D-146 step 5): a collection made, renamed, recoloured or deleted is that one entry; the numbers of items follow; no reload after its own actions
# v0.1.89: A collection can be asked for before the list is loaded (to open a contact from the message list)
# v0.0.61: Initial panel with the calendars, task lists or address books of all accounts

ROLE_COLLECTION: int = Qt.ItemDataRole.UserRole + 1
ROLE_INFO: int = Qt.ItemDataRole.UserRole + 2
ROLE_ACCOUNT: int = Qt.ItemDataRole.UserRole + 3  # on an account's heading: its id
ROLE_GROUP: int = Qt.ItemDataRole.UserRole + 4  # on a contact group's entry (under its address book): its GroupRow
KIND_WORDS: dict[str, tuple[str, str]] = {"events": ("Calendar", "calendar"), "tasks": ("Tasks", "task list"), "contacts": ("Contacts", "address book")}


def colour_icon(colour: str, size: int = 12) -> QIcon:
    """A small square in the colour of a collection."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(colour or COLLECTION_WITHOUT_COLOUR))
    return QIcon(pixmap)


def collection_items(tree: QTreeWidget) -> list[QTreeWidgetItem]:
    """The items of the calendars, task lists or address books (under the accounts)."""
    found: list[QTreeWidgetItem] = []
    for top in range(tree.topLevelItemCount()):
        account = tree.topLevelItem(top)
        if account is None:
            continue
        for child in range(account.childCount()):
            item = account.child(child)
            if item is not None:
                found.append(item)
    return found


class CollectionPanel(QWidget):
    """The left side of a section: its title, and under each account the calendars, task lists or address books. Calendars and task lists have a tick
    (what is ticked is shown); address books are chosen one at a time. What is not ticked is remembered in the settings."""

    changed = Signal()  # what is ticked or chosen changed
    reloaded = Signal()
    add_account_requested = Signal()  # Add account…: a calendar server for a mail account, or an account of calendars and contacts only
    infos_changed = Signal()  # what is known of the collections (a calendar's colours) was taken in again: the section redraws in their colours
    conflicts_requested = Signal()  # the Sync conflicts entry was clicked: Settings > Sync conflicts (D-194)
    contacts_dropped = Signal(str, list)  # contacts were dropped on a group: the group's id, the contacts' ids (R-39)

    def __init__(self, backend: Backend, settings: QSettings, kind: str, multiple: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.prefer: str = ""  # the collection to choose at the next load, when nothing else is asked for
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self.kind: str = kind
        self._multiple: bool = multiple
        self._key: str = f"hidden_{kind}"
        self._loading: bool = False
        self.collections: list[CollectionSummary] = []
        self.group_actions: Callable[[QMenu, GroupRow], None] | None = None  # the menu of a contact group's entry (the Contacts page)
        self.book_actions: Callable[[QMenu, CollectionSummary], None] | None = None  # what the Contacts page adds to an address book's menu
        self.group_delete: Callable[[GroupRow], None] | None = None  # Delete on a contact group's entry (it asks first)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel(KIND_WORDS[kind][0], self)
        font = self.title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.1)
        self.title.setFont(font)
        self.title.setContentsMargins(10, 8, 10, 6)
        layout.addWidget(self.title)
        self.tree = QTreeWidget(self)
        self._delete_key = DeleteKey(self.tree, self._delete_pressed)  # Delete here acts on the calendar, address book or task list, never on its items
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.conflicts_entry = QToolButton(self)  # "Sync conflicts (n)" with the triangle, only while there are any, at the top (D-194, the owner)
        self.conflicts_entry.setObjectName("syncConflictsEntry")
        self.conflicts_entry.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.conflicts_entry.setAutoRaise(True)
        self.conflicts_entry.setIcon(mark_icon(CONFLICT))
        self.conflicts_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.conflicts_entry.setStyleSheet("QToolButton#syncConflictsEntry { text-align: left; padding: 4px 10px; }")
        self.conflicts_entry.clicked.connect(self.conflicts_requested.emit)
        self.conflicts_entry.setToolTip("Open the sync conflicts")
        self.conflicts_entry.hide()
        layout.addWidget(self.conflicts_entry)
        layout.addWidget(self.tree, 1)
        if kind == "contacts":
            self.tree.setAcceptDrops(True)
            self.tree.viewport().installEventFilter(self)  # contacts dropped on a group (R-39)
        self.tree.itemChanged.connect(self._ticked)
        self.tree.currentItemChanged.connect(self._chosen)
        self.tree.customContextMenuRequested.connect(self._menu)

    def show_conflicts(self, count: int) -> None:
        """The Sync conflicts entry: shown with its count while there are any, gone when there are none."""
        self.conflicts_entry.setText(f"Sync conflicts ({count})")
        self.conflicts_entry.setVisible(count > 0)

    # --- what is shown ------------------------------------------------------------------------

    def _hidden(self) -> set[str]:
        value = self._settings.value(self._key, [])
        return {str(item) for item in value} if isinstance(value, list) else ({str(value)} if value else set())

    def visible_ids(self) -> list[str]:
        """The collections that are ticked (calendars, task lists), or the one that is chosen (address books)."""
        if not self._multiple:
            current = self.current_id()
            return [current] if current else []
        return [str(item.data(0, ROLE_COLLECTION)) for item in collection_items(self.tree) if item.checkState(0) == Qt.CheckState.Checked]

    def current_id(self) -> str:
        """The collection chosen; with a contact group chosen, its address book."""
        item = self.tree.currentItem()
        if item is not None and isinstance(item.data(0, ROLE_GROUP), GroupRow):
            item = item.parent()
        return str(item.data(0, ROLE_COLLECTION) or "") if item is not None else ""

    def current_group(self) -> GroupRow | None:
        """The contact group chosen, or None when an address book (or nothing) is."""
        item = self.tree.currentItem()
        found = item.data(0, ROLE_GROUP) if item is not None else None
        return found if isinstance(found, GroupRow) else None

    def group_entries(self, collection: str) -> list[QTreeWidgetItem]:
        entry = self._entry(collection)
        return [child for number in range(entry.childCount()) if (child := entry.child(number)) is not None] if entry is not None else []

    def set_groups(self, collection: str, rows: list[GroupRow], choose: str = "") -> None:
        """The groups of an address book as they are now, under its entry, changed in place: a group that went is taken away, one that came is put
        at its place (by name), a changed one is written again; the group chosen stays chosen (D-146). The chosen group going chooses its address
        book; choose chooses that group once it is there."""
        entry = self._entry(collection)
        if entry is None:
            return
        chosen = self.current_group()
        had_none = entry.childCount() == 0
        wanted = {row.item for row in rows}
        self._loading = True  # taking and putting entries moves the current one: not a choice of the person
        for number in range(entry.childCount() - 1, -1, -1):
            child = entry.child(number)
            row = child.data(0, ROLE_GROUP) if child is not None else None
            if child is not None and (not isinstance(row, GroupRow) or row.item not in wanted):
                entry.removeChild(child)
        for position, row in enumerate(rows):
            child = entry.child(position)
            shown = child.data(0, ROLE_GROUP) if child is not None else None
            if child is None or not isinstance(shown, GroupRow) or shown.item != row.item:
                elsewhere = next((number for number in range(position + 1, entry.childCount()) if (other := entry.child(number)) is not None
                                  and isinstance(found := other.data(0, ROLE_GROUP), GroupRow) and found.item == row.item), None)
                taken = entry.takeChild(elsewhere) if elsewhere is not None else None  # its place in the order changed (a rename)
                if taken is None:
                    taken = QTreeWidgetItem()
                    taken.setIcon(0, theme_icon("system-users"))
                    taken.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                entry.insertChild(position, taken)
                child = taken
            child.setData(0, ROLE_GROUP, row)
            text = f"{row.name}  ({row.members})"
            if child.text(0) != text:
                child.setText(0, text)
            child.setToolTip(0, f"{counted(row.others, 'more member')} without a contact in this address book" if row.others else "")
        if had_none and rows:
            entry.setExpanded(True)
        kept = self._group_entry(chosen.item) if chosen is not None else None
        if kept is not None:
            self.tree.setCurrentItem(kept)  # the same group stays chosen: nothing to load again
        self._loading = False
        if chosen is not None and kept is None and chosen.collection == collection:  # the chosen group went: its address book is shown
            if self.tree.currentItem() is entry:
                self.changed.emit()  # Qt made it current already while the entry went (quietly): shown now
            else:
                self.tree.setCurrentItem(entry)
        wished = self._group_entry(choose) if choose else None
        if wished is not None:
            self.tree.setCurrentItem(wished)

    def _group_entry(self, group: str) -> QTreeWidgetItem | None:
        for item in collection_items(self.tree):
            for number in range(item.childCount()):
                child = item.child(number)
                found = child.data(0, ROLE_GROUP) if child is not None else None
                if isinstance(found, GroupRow) and found.item == group:
                    return child
        return None

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Contacts dragged over the list: taken only by a group of their own address book that is not read only (R-39)."""
        if watched is self.tree.viewport() and event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop) and isinstance(event, QDropEvent):
            dragged = read_contacts_mime_data(event.mimeData())
            item = self.tree.itemAt(event.position().toPoint())
            group = item.data(0, ROLE_GROUP) if item is not None else None
            fits = dragged is not None and isinstance(group, GroupRow) and group.collection == dragged[0] and not group.read_only
            if dragged is not None and event.type() == QEvent.Type.DragEnter:
                event.acceptProposedAction()  # the drag is followed over the list; each place says whether it takes it
            elif fits and isinstance(group, GroupRow) and dragged is not None:
                event.acceptProposedAction()
                if event.type() == QEvent.Type.Drop:
                    self.contacts_dropped.emit(group.item, dragged[1])
            else:
                event.ignore()
            return True
        return super().eventFilter(watched, event)

    def info(self, collection_id: str) -> CollectionSummary | None:
        return next((item for item in self.collections if item.collection == collection_id), None)

    def reload(self, then_choose: str = "") -> None:
        def listed(result: tuple[CollectionSummary, ...]) -> None:
            self.collections = list(result)
            keep = then_choose or self.prefer or self.current_id()
            self.prefer = ""
            hidden = self._hidden()
            self._loading = True
            self.tree.clear()
            accounts: dict[str, QTreeWidgetItem] = {}
            chosen: QTreeWidgetItem | None = None
            for info in self.collections:
                account = info.account
                if account not in accounts:
                    node = QTreeWidgetItem([info.account_name])
                    node.setData(0, ROLE_ACCOUNT, account)
                    node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    font = node.font(0)
                    font.setBold(True)
                    node.setFont(0, font)
                    self.tree.addTopLevelItem(node)
                    node.setExpanded(True)
                    accounts[account] = node
                item = QTreeWidgetItem([info.name])
                collection = info.collection
                item.setData(0, ROLE_COLLECTION, collection)
                item.setData(0, ROLE_INFO, info)
                item.setIcon(0, colour_icon(info.colour))
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if self._multiple:
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                    item.setCheckState(0, Qt.CheckState.Unchecked if collection in hidden else Qt.CheckState.Checked)
                item.setFlags(flags)
                accounts[account].addChild(item)
                if collection == keep or (chosen is None and not self._multiple and not keep):
                    chosen = item
            if chosen is not None:
                self.tree.setCurrentItem(chosen)
            self._loading = False
            self.reloaded.emit()
            self.changed.emit()

        self._backend.ask(COLLECTION_LIST, ListParameters(self._kind()), listed, lambda error: None, owner=self)

    def _ticked(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading or not self._multiple:
            return
        hidden = {str(entry.data(0, ROLE_COLLECTION)) for entry in collection_items(self.tree) if entry.checkState(0) != Qt.CheckState.Checked}
        self._settings.setValue(self._key, sorted(hidden))
        self.changed.emit()

    def _chosen(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if not self._loading and not self._multiple:
            self.changed.emit()

    # --- changing -----------------------------------------------------------------------------

    def _failed(self, error: ChronoMailError) -> None:
        QMessageBox.warning(self, KIND_WORDS[self.kind][0], str(error))

    def new_collection(self, account: str = "") -> None:
        """A new calendar, task list or address book: its name, and the account it is made in (the one right-clicked; the local ones when none)."""
        dialog = NewCollectionDialog(self._backend, f"New {KIND_WORDS[self.kind][1]}", account, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.name:
            self._backend.ask(COLLECTION_CREATE, CreateParameters(self._kind(), dialog.name, account=dialog.account), self._made, self._failed, owner=self)

    def _kind(self) -> Literal["events", "tasks", "contacts"]:
        """This panel's kind as the engine's value."""
        return "contacts" if self.kind == "contacts" else "tasks" if self.kind == "tasks" else "events"

    def rename_collection(self, collection_id: str) -> None:
        info = self.info(collection_id)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=info.name if info is not None else "")
        if ok and name.strip():
            self._backend.ask(COLLECTION_EDIT, CollectionEdit(collection_id, name=name.strip()), lambda result: None, self._failed, owner=self)  # the fact renames the entry

    def choose_colour(self, collection_id: str) -> None:
        info = self.info(collection_id)
        colour = QColorDialog.getColor(QColor((info.colour if info is not None else "") or COLLECTION_WITHOUT_COLOUR), self, "Colour")
        if colour.isValid():
            self._backend.ask(COLLECTION_EDIT, CollectionEdit(collection_id, colour=colour.name()), lambda result: None, self._failed, owner=self)  # the fact recolours the entry

    def _delete_pressed(self) -> None:
        """Delete (or Shift+Delete) while this list has the keyboard: the collection selected, as its right-click Delete does (it asks
        first); nothing on a heading or on one the server will not delete (read only, greyed in the menu)."""
        item = self.tree.currentItem()
        group = self.current_group()
        if group is not None:
            if not group.read_only and self.group_delete is not None:
                self.group_delete(group)
            return
        collection = str(item.data(0, ROLE_COLLECTION) or "") if item is not None else ""
        info = self.info(collection) if collection else None
        if collection and not (info is not None and info.read_only):
            self.delete_collection(collection)

    def delete_collection(self, collection_id: str) -> None:
        info = self.info(collection_id)
        count = info.items if info is not None else 0
        word = KIND_WORDS[self.kind][1]
        name = f"Delete the {word} “{info.name if info is not None else ''}”" + (f" and its {counted(count, 'item')}" if count else "")
        if info is not None and info.server:  # deleted on the server first: every program that synchronises with it follows (D-197)
            others = tuple(other for other in info.together)
            text = (
                f"{name} from the server?\n\nEvery program that synchronises with the server will remove it too."
                + "".join(f"\n\nIt also holds “{other}”, which goes with it." for other in others)
                + "\n\nThe copy here is kept in the store's .deleted directory for 30 days."
            )
        else:
            text = f"{name}?\n\nThe files are kept in the store's .deleted directory for 30 days."
        if QMessageBox.question(self, "Delete", text) == QMessageBox.StandardButton.Yes:
            self._backend.ask(COLLECTION_DELETE, CollectionDelete(collection_id), lambda result: None, self._failed, owner=self)  # the fact takes the entry away

    # --- following the facts (D-146) -----------------------------------------------------------

    def _entry(self, collection_id: str) -> QTreeWidgetItem | None:
        return next((item for item in collection_items(self.tree) if str(item.data(0, ROLE_COLLECTION)) == collection_id), None)

    def _made(self, result: CollectionSummary) -> None:
        """The person made a collection: its entry is there already (the fact added it); it is chosen."""
        entry = self._entry(result.collection)
        if entry is None:
            self.add(result)
            entry = self._entry(result.collection)
        if entry is not None:
            self.tree.setCurrentItem(entry)

    def add(self, info: CollectionSummary) -> None:
        """One collection more: its entry under its account (a new account's heading too)."""
        if self._entry(info.collection) is not None:
            return
        account = info.account
        node = next((self.tree.topLevelItem(top) for top in range(self.tree.topLevelItemCount()) if (found := self.tree.topLevelItem(top)) is not None and str(found.data(0, ROLE_ACCOUNT)) == account), None)
        if node is None:
            node = QTreeWidgetItem([info.account_name])
            node.setData(0, ROLE_ACCOUNT, account)
            node.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = node.font(0)
            font.setBold(True)
            node.setFont(0, font)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
        self.collections.append(info)
        item = QTreeWidgetItem([info.name])
        item.setData(0, ROLE_COLLECTION, info.collection)
        item.setData(0, ROLE_INFO, info)
        item.setIcon(0, colour_icon(info.colour))
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        self._loading = True
        if self._multiple:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(0, Qt.CheckState.Checked)
        item.setFlags(flags)
        node.addChild(item)
        self._loading = False

    def apply(self, change: Change) -> None:
        """A fact about a collection of this kind, or about its items (their number)."""
        kind, collection = str(change.kind), change.folder
        if kind == "collection_made":
            template = next((info for info in self.collections if info.account == change.mailbox), None)
            if template is None or not dataclasses.is_dataclass(template) or isinstance(template, type):
                self.reload(self.current_id())  # an account with no collection shown yet: its heading's name comes from the engine
                return
            self.add(dataclasses.replace(
                template, collection=collection, name=change.name, colour=change.colour, items=0,
                read_only=change.read_only,
                palette=tuple(change.palette), timed_background=change.timed_background,
            ))
        elif kind == "collection_edited":
            self._replace(collection, lambda info: dataclasses.replace(info, name=change.name, colour=change.colour, palette=change.palette, read_only=change.read_only,
                                                                        timed_background=change.timed_background))
            self.infos_changed.emit()  # its colours are in the fact: the section redraws in them
        elif kind == "collection_removed":
            entry = self._entry(collection)
            self.collections = [info for info in self.collections if info.collection != collection]
            if entry is not None and (parent := entry.parent()) is not None:
                was_current = self.tree.currentItem() is entry
                parent.removeChild(entry)
                if parent.childCount() == 0:
                    self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(parent))
                if was_current or self._multiple:
                    left = collection_items(self.tree)
                    if was_current and left:
                        self.tree.setCurrentItem(left[0])
                    self.changed.emit()
        elif kind in ("item_added", "item_removed", "item_moved"):
            if kind != "item_removed":
                target = change.to_folder if kind == "item_moved" else collection
                self._count(target, 1)
            if kind != "item_added":
                self._count(collection, -1)

    def _count(self, collection: str, delta: int) -> None:
        info = next((info for info in self.collections if info.collection == collection), None)
        if info is not None:
            self._replace(collection, lambda found: dataclasses.replace(found, items=max(0, found.items + delta)))

    def _replace(self, collection: str, change: Callable[[CollectionSummary], CollectionSummary]) -> None:
        """One collection as the fact leaves it (change gives its new summary), in the list and in its entry."""
        for number, info in enumerate(self.collections):
            if info.collection == collection:
                changed = change(info)
                self.collections[number] = changed
                entry = self._entry(collection)
                if entry is not None:
                    self._loading = True  # the entry's own update is not a tick: nothing is shown again
                    entry.setData(0, ROLE_INFO, changed)
                    entry.setText(0, changed.name)
                    entry.setIcon(0, colour_icon(changed.colour))
                    self._loading = False

    def edit_colours(self, collection_id: str) -> None:
        """The palette editor of a calendar; what it saves comes back as a fact."""
        from chrono_mail.ui.paletteeditor import PaletteEditor

        info = self.info(collection_id)
        if info is not None:
            PaletteEditor(self._backend, info, self).exec()

    def _menu(self, position: QPoint) -> None:
        self.menu_at(position).exec(self.tree.viewport().mapToGlobal(position))

    def menu_at(self, position: QPoint) -> QMenu:
        """The menu of the sidebar at a place in its list: Add account…, New …, and for a collection Rename, Colour and Delete."""
        menu = QMenu(self)
        item = self.tree.itemAt(position)
        collection = str(item.data(0, ROLE_COLLECTION) or "") if item is not None else ""
        group = item.data(0, ROLE_GROUP) if item is not None else None
        menu.addAction("Add account…", self.add_account_requested.emit)
        menu.addSeparator()
        account = ""  # the account right-clicked: its heading, or the account of the collection (or group) under the pointer
        heading = item
        while heading is not None and heading.parent() is not None:
            heading = heading.parent()
        if heading is not None:
            account = str(heading.data(0, ROLE_ACCOUNT) or "")
        if isinstance(group, GroupRow):
            if self.group_actions is not None:
                self.group_actions(menu, group)
            return menu
        menu.addAction(f"New {KIND_WORDS[self.kind][1]}…", lambda: self.new_collection(account))
        book = self.info(collection) if collection else None
        if book is not None and self.book_actions is not None:
            self.book_actions(menu, book)
        if collection:
            menu.addSeparator()
            shown = self.info(collection)
            read_only = shown is not None and shown.read_only  # its name and colour are the server's: greyed, not hidden
            menu.addAction("Rename…", lambda: self.rename_collection(collection)).setEnabled(not read_only)
            menu.addAction("Colour…", lambda: self.choose_colour(collection)).setEnabled(not read_only)
            if self.kind == "events":
                menu.addAction("Edit colours…", lambda: self.edit_colours(collection)).setEnabled(not read_only)
            delete = menu.addAction("Delete…", lambda: self.delete_collection(collection))
            delete.setEnabled(not read_only)  # the server will not delete it: greyed, never hidden
            if read_only:
                delete.setToolTip(f"This {KIND_WORDS[self.kind][1]} is read only on its server. To stop keeping it here, untick it in the account (Settings > Accounts > Edit > Calendars and contacts)")
                menu.setToolTipsVisible(True)
        return menu
