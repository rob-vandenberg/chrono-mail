from typing import override

from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QTreeView, QWidget

# --- Version ------------------------------------------------------------
__version__ = "startupchooser.py 1.1.195"

# --- Version history ----------------------------------------------------
# v1.1.195: A choice can be greyed: what the folder list does not show (Settings > Sidebar) is listed, greyed, and cannot be chosen
# v1.1.193: A heading folds on a press without asking where the pointer is (the tree handles its arrow itself and sends no press)
# v1.1.184: The drop-down's own popup holds the tree (it closes on a click outside, on the field and on Escape, keeping the
#           old choice, and never holds the desktop); a border; as high as the tree needs, at most VISIBLE_ROWS rows
# v1.1.182: Initial: Settings > General > Show on startup as a folded tree, at most VISIBLE_ROWS rows high

VISIBLE_ROWS: int = 15  # the list is never higher than this many rows; the rest scrolls
VALUE_ROLE: int = Qt.ItemDataRole.UserRole  # the role a drop-down's currentData() reads


class StartupChooser(QComboBox):
    """A drop-down whose list is a folded tree: at first only the top level shows (Last selected folder, Favorites, Tags,
    Local Folders, the sections, the accounts and groups); a heading unfolds with a click on it or on its arrow. A click on
    a folder or a view chooses it and closes the list. The list is the drop-down's own popup, so a click outside it, a
    click on the field and Escape close it and keep the choice as it was. currentData() is the value of the choice."""

    def __init__(self, choices: list[tuple[str, str, bool, int, bool]], parent: QWidget | None = None) -> None:
        """choices: (text, value, can be chosen, depth, shown), in the order of the tree; a row at depth n+1 belongs to the
        row at depth n above it. A row that is not shown is greyed (the palette's Disabled text)."""
        super().__init__(parent)
        self.tree_model = QStandardItemModel(self)
        parents: list[QStandardItem] = [self.tree_model.invisibleRootItem()]
        greyed = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        for text, value, choosable, depth, shown in choices:
            item = QStandardItem(text)
            item.setEditable(False)
            item.setData(value, VALUE_ROLE)
            item.setSelectable(choosable)  # a heading is never chosen: the popup stays open when it is clicked
            if not shown:
                item.setForeground(greyed)  # hidden in the folder list: greyed, still unfolds when it is a heading
            del parents[depth + 1:]
            parents[-1].appendRow(item)
            parents.append(item)
        self.tree = QTreeView(self)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(False)  # a heading folds with one click (see _pressed)
        self.setModel(self.tree_model)
        self.setView(self.tree)
        self.setMaxVisibleItems(VISIBLE_ROWS)
        # The list as a list under the field (not over it), so that it keeps to VISIBLE_ROWS; a border shows it lies on top.
        self.setStyleSheet("QComboBox { combobox-popup: 0; } QComboBox QAbstractItemView { border: 1px solid palette(dark); }")
        self.tree.pressed.connect(self._pressed)

    def choose_value(self, value: str) -> bool:
        """Make the row with this value the choice; False (and nothing changes) when no row that can be chosen has it."""
        found = self._find(QModelIndex(), value)
        if found is None:
            return False
        self.setRootModelIndex(found.parent())  # the field takes a row under one parent: the one of the choice
        self.setCurrentIndex(found.row())
        self.setRootModelIndex(QModelIndex())  # the list shows the whole tree again; the choice stays
        return True

    def _find(self, parent: QModelIndex, value: str) -> QModelIndex | None:
        for row in range(self.tree_model.rowCount(parent)):
            index = self.tree_model.index(row, 0, parent)
            item = self.tree_model.itemFromIndex(index)
            if item is not None and item.isSelectable() and str(index.data(VALUE_ROLE)) == value:
                return index
            deeper = self._find(index, value)
            if deeper is not None:
                return deeper
        return None

    def chosen_index(self) -> QModelIndex:
        """The row of the choice in the tree (the field may show a row deeper than the top level)."""
        found = self._find(QModelIndex(), str(self.currentData()))
        return found if found is not None else self.tree_model.index(0, 0)

    def _pressed(self, index: QModelIndex) -> None:
        """A press on a heading folds or unfolds it. A press on its arrow never gets here: the tree folds it itself."""
        item = self.tree_model.itemFromIndex(index)
        if item is None or item.isSelectable():
            return
        self.tree.setExpanded(index, not self.tree.isExpanded(index))

    def _rows_in_tree(self, parent: QModelIndex) -> int:
        count = 0
        for row in range(self.tree_model.rowCount(parent)):
            count += 1 + self._rows_in_tree(self.tree_model.index(row, 0, parent))
        return count

    @override
    def showPopup(self) -> None:
        """The tree, folded but open down to the choice. It is as high as the whole tree unfolded, at most VISIBLE_ROWS
        rows, so that unfolding a heading never makes the list scroll sooner than it must."""
        self.tree.collapseAll()
        chosen = self.chosen_index()
        parent = chosen.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
        super().showPopup()
        self.tree.setCurrentIndex(chosen)
        self.tree.scrollTo(chosen)
        popup = self.tree.window()  # the drop-down's own popup window around the tree
        rows = min(VISIBLE_ROWS, self._rows_in_tree(QModelIndex()))
        wanted = rows * max(self.tree.sizeHintForRow(0), 1) + (popup.height() - self.tree.viewport().height())
        if wanted <= popup.height():
            return
        top = popup.y()
        screen = self.screen()
        if screen is not None:
            room = screen.availableGeometry()
            wanted = min(wanted, room.height())
            below = popup.y() >= self.mapToGlobal(QPoint(0, self.height())).y() - 1  # it opened under the field
            top = min(top, room.bottom() - wanted + 1) if below else max(room.top(), popup.y() + popup.height() - wanted)
        popup.setGeometry(popup.x(), top, popup.width(), wanted)
