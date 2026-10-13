from PySide6.QtCore import QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "collectionpanel.py 0.1.89"

# --- Version history ----------------------------------------------------
# v0.1.89: A collection can be asked for before the list is loaded (to open a contact from the message list)
# v0.0.61: Initial panel with the calendars, task lists or address books of all accounts

ROLE_COLLECTION: int = Qt.ItemDataRole.UserRole + 1
ROLE_INFO: int = Qt.ItemDataRole.UserRole + 2
KIND_WORDS: dict[str, tuple[str, str]] = {"events": ("Calendar", "calendar"), "tasks": ("Tasks", "task list"), "contacts": ("Contacts", "address book")}
DEFAULT_COLOUR: str = "#7f8c8d"


def colour_icon(colour: str, size: int = 12) -> QIcon:
    """A small square in the colour of a collection."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(colour or DEFAULT_COLOUR))
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

    def __init__(self, backend: Backend, settings: QSettings, kind: str, multiple: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.prefer: str = ""  # the collection to choose at the next load, when nothing else is asked for
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self.kind: str = kind
        self._multiple: bool = multiple
        self._key: str = f"hidden_{kind}"
        self._loading: bool = False
        self.collections: list[object] = []
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
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.tree, 1)
        self.tree.itemChanged.connect(self._ticked)
        self.tree.currentItemChanged.connect(self._chosen)
        self.tree.customContextMenuRequested.connect(self._menu)

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
        item = self.tree.currentItem()
        return str(item.data(0, ROLE_COLLECTION) or "") if item is not None else ""

    def info(self, collection_id: str) -> object | None:
        return next((item for item in self.collections if str(getattr(item, "collection")) == collection_id), None)

    def reload(self, then_choose: str = "") -> None:
        def listed(result: object) -> None:
            self.collections = list(result) if isinstance(result, (tuple, list)) else []
            keep = then_choose or self.prefer or self.current_id()
            self.prefer = ""
            hidden = self._hidden()
            self._loading = True
            self.tree.clear()
            accounts: dict[str, QTreeWidgetItem] = {}
            chosen: QTreeWidgetItem | None = None
            for info in self.collections:
                account = str(getattr(info, "account"))
                if account not in accounts:
                    node = QTreeWidgetItem([str(getattr(info, "account_name"))])
                    node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    font = node.font(0)
                    font.setBold(True)
                    node.setFont(0, font)
                    self.tree.addTopLevelItem(node)
                    node.setExpanded(True)
                    accounts[account] = node
                item = QTreeWidgetItem([str(getattr(info, "name"))])
                collection = str(getattr(info, "collection"))
                item.setData(0, ROLE_COLLECTION, collection)
                item.setData(0, ROLE_INFO, info)
                item.setIcon(0, colour_icon(str(getattr(info, "colour"))))
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

        self._backend.call("collection.list", {"kind": self.kind}, listed, lambda error: None, owner=self)

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

    def new_collection(self) -> None:
        name, ok = QInputDialog.getText(self, f"New {KIND_WORDS[self.kind][1]}", "Name:")
        if ok and name.strip():
            self._backend.call("collection.create", {"kind": self.kind, "name": name.strip()}, lambda result: self.reload(str(getattr(result, "collection"))), self._failed, owner=self)

    def rename_collection(self, collection_id: str) -> None:
        info = self.info(collection_id)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=str(getattr(info, "name", "")))
        if ok and name.strip():
            self._backend.call("collection.edit", {"collection": collection_id, "name": name.strip()}, lambda result: self.reload(), self._failed, owner=self)

    def choose_colour(self, collection_id: str) -> None:
        info = self.info(collection_id)
        colour = QColorDialog.getColor(QColor(str(getattr(info, "colour", "")) or DEFAULT_COLOUR), self, "Colour")
        if colour.isValid():
            self._backend.call("collection.edit", {"collection": collection_id, "colour": colour.name()}, lambda result: self.reload(), self._failed, owner=self)

    def delete_collection(self, collection_id: str) -> None:
        info = self.info(collection_id)
        count = int(getattr(info, "items", 0))
        word = KIND_WORDS[self.kind][1]
        text = f"Delete the {word} “{getattr(info, 'name', '')}”" + (f" and its {count} item(s)?" if count else "?") + "\n\nThe files are kept in the store's .deleted directory for 30 days."
        if QMessageBox.question(self, "Delete", text) == QMessageBox.StandardButton.Yes:
            self._backend.call("collection.delete", {"collection": collection_id}, lambda result: self.reload(), self._failed, owner=self)

    def _menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        item = self.tree.itemAt(position)
        collection = str(item.data(0, ROLE_COLLECTION) or "") if item is not None else ""
        menu.addAction(f"New {KIND_WORDS[self.kind][1]}…", self.new_collection)
        if collection:
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self.rename_collection(collection))
            menu.addAction("Colour…", lambda: self.choose_colour(collection))
            menu.addAction("Delete…", lambda: self.delete_collection(collection))
        menu.exec(self.tree.viewport().mapToGlobal(position))
