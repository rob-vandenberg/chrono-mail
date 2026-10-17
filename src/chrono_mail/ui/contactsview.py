from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QAbstractListModel, QItemSelectionModel, QModelIndex, QPersistentModelIndex, QPoint, QRect, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.collectionpanel import DEFAULT_COLOUR, colour_icon
from chrono_mail.ui.contactdetail import ContactDetail, avatar_pixmap
from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.tagchips import TagColourBook, paint_chips
from chrono_mail.ui.tagging import tag_icon

# --- Version ------------------------------------------------------------
__version__ = "contactsview.py 0.5.167"

# --- Version history ----------------------------------------------------
# v0.5.167: the toolbar folds like Mail's; Tag and Move to have icons; Import and Export live in More, which folded buttons join; the search field looks like Mail's (D-154)
# v0.5.159: the list follows the facts (D-146 step 5): one contact in, out or changed at its place; the selection moves at the key press of a delete or move; contacts with an action under way are greyed; no reload after an action
# v0.1.89: show_contact: one contact is shown, from the Contact column of the message list
# v0.0.62: Initial contacts section: list, detail pane, editor, tags, move, import and export

ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
PENDING_ROLE: int = Qt.ItemDataRole.UserRole + 2  # true on a contact with an action under way (drawn greyed)
ROW_HEIGHT: int = 56
AVATAR_SIZE: int = 36
LIST_LIMIT: int = 5000
ALL_TAGS: str = ""


class ContactListModel(QAbstractListModel):
    """The contacts of the address book that is chosen (up to a few thousand, sorted by family name)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[object] = []
        self.total: int = 0
        self.pending: set[str] = set()

    def remove_item(self, item: str) -> bool:
        """One contact goes from the list (the others and the selection stay)."""
        position = self.position_of(item)
        if position < 0:
            return False
        self.beginRemoveRows(QModelIndex(), position, position)
        del self.rows[position]
        self.total = max(0, self.total - 1)
        self.endRemoveRows()
        return True

    def put(self, row: object) -> None:
        """One contact comes into the list or changes: at its place in the order of family names. A contact that was there
        keeps its row (changed in place, or moved to its new place), so its selection stays."""
        item = str(getattr(row, "item"))
        key = (str(getattr(row, "sort_key")), str(getattr(row, "name")).casefold())
        old = self.position_of(item)
        others = [known for known in self.rows if str(getattr(known, "item")) != item]
        position = next((number for number, known in enumerate(others) if (str(getattr(known, "sort_key")), str(getattr(known, "name")).casefold()) > key), len(others))
        if old < 0:
            self.beginInsertRows(QModelIndex(), position, position)
            self.rows.insert(position, row)
            self.total += 1
            self.endInsertRows()
            return
        if position != old:
            self.beginMoveRows(QModelIndex(), old, old, QModelIndex(), position + 1 if position > old else position)  # Qt counts the place before the move
            self.rows = others[:position] + [row] + others[position:]
            self.endMoveRows()
        else:
            self.rows[old] = row
        self.dataChanged.emit(self.index(position), self.index(position))

    def redraw(self, item: str) -> None:
        position = self.position_of(item)
        if position >= 0:
            self.dataChanged.emit(self.index(position), self.index(position))

    def load(self, rows: list[object], total: int) -> None:
        self.beginResetModel()
        self.rows = rows
        self.total = total
        self.endResetModel()

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(getattr(row, "name"))
        if role == ROW_ROLE:
            return row
        if role == PENDING_ROLE:
            return str(getattr(row, "item")) in self.pending
        return None

    def position_of(self, item: str) -> int:
        return next((position for position, row in enumerate(self.rows) if str(getattr(row, "item")) == item), -1)


class ContactDelegate(QStyledItemDelegate):
    """A contact as a row: the avatar, the name, the organisation or the address under it, and the tags as coloured labels."""

    def __init__(self, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colours: TagColourBook = colours

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(0, ROW_HEIGHT)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        rect = _rect(option)
        palette = _typed(option, "palette", QPalette)
        selected = _is_selected(option)
        painter.save()
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight) if selected else palette.color(QPalette.ColorRole.Base))
        text = palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        if index.data(PENDING_ROLE):
            text = palette.color(QPalette.ColorRole.PlaceholderText)  # an action is under way: greyed until it ends
        quiet = QColor(text)
        quiet.setAlpha(255 if selected else 170)
        name = str(getattr(row, "name"))
        painter.drawPixmap(rect.left() + 10, rect.top() + (rect.height() - AVATAR_SIZE) // 2, avatar_pixmap(name, AVATAR_SIZE))
        left = rect.left() + 10 + AVATAR_SIZE + 10
        tags = tuple(getattr(row, "tags"))
        chips = paint_chips(painter, QRect(left, rect.top(), rect.right() - left - 10, rect.height()), tags, self._colours, _typed(option, "font", QFont)) if tags else 0
        width = rect.right() - left - 10 - chips
        font = painter.font()
        strong = QFontMetrics(font)
        painter.setPen(text)
        title_rect = QRect(left, rect.top() + 8, width, strong.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, strong.elidedText(name, Qt.TextElideMode.ElideRight, width))
        subtitle = str(getattr(row, "subtitle"))
        if subtitle:
            painter.setPen(quiet)
            painter.drawText(QRect(left, rect.top() + 8 + strong.height() + 2, width, strong.height()), Qt.AlignmentFlag.AlignVCenter, strong.elidedText(subtitle, Qt.TextElideMode.ElideRight, width))
        line = QColor(text)
        line.setAlpha(28)
        painter.setPen(line)
        painter.drawLine(rect.left() + 10, rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class ContactsPage(PimPage):
    """Address books on the left (with the tags to filter by), the contacts in the middle, and one contact on the right."""

    kind = "contacts"
    multiple = False
    new_label = "New contact"
    new_icon = "contact-new"

    compose_requested = Signal(str)

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(backend, settings, parent)
        self.colours = TagColourBook()
        self.mail_accounts_source: Callable[[], list[str]] = lambda: []
        self._generation: int = 0
        self._tags_loaded: bool = False
        self._show_item: str = ""  # the contact to select when the list of its address book has loaded
        self._select_when_there: str = ""  # a contact just saved: selected when its row has come in
        self._show_collection: str = ""
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addWidget(self.panel, 3)
        self.tag_title = QLabel("Tags", side)
        font = self.tag_title.font()
        font.setBold(True)
        self.tag_title.setFont(font)
        self.tag_title.setContentsMargins(10, 6, 10, 4)
        side_layout.addWidget(self.tag_title)
        self.tag_list = QListWidget(side)
        self.tag_list.setFrameShape(QListWidget.Shape.NoFrame)
        side_layout.addWidget(self.tag_list, 2)
        self.side = side

        self.model = ContactListModel()
        self.delegate = ContactDelegate(self.colours)
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)
        self.count_label = QLabel("", pane)
        self.count_label.setContentsMargins(10, 8, 10, 6)
        pane_layout.addWidget(self.count_label)
        self.list = QListView(pane)
        self.list.setModel(self.model)
        self.list.setItemDelegate(self.delegate)
        self.list.setFrameShape(QListView.Shape.NoFrame)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setUniformItemSizes(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane_layout.addWidget(self.list, 1)
        self.detail = ContactDetail(backend, self.colours)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(pane)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([340, 560])
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(splitter)
        self.content = content

        self.search_field = SearchField(hint="Search contacts", focused_hint="Search contacts", scope=False)
        self.search = self.search_field.line_edit
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.reload_list)
        self.act_edit = QAction("Edit", self.toolbar)
        self.act_edit.setIcon(theme_icon("document-edit"))
        self.act_edit.triggered.connect(self.edit_selected)
        self.act_delete = QAction("Delete", self.toolbar)
        self.act_delete.setIcon(theme_icon("edit-delete"))
        self.act_delete.triggered.connect(self.delete_selected)
        self.tag_button = QToolButton()
        self.tag_button.setText("Tag")
        self.tag_button.setIcon(tag_icon(QColor("#808080")))
        self.tag_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_menu.aboutToShow.connect(self._fill_tag_menu)
        self.tag_button.setMenu(self.tag_menu)
        self.move_button = QToolButton()
        self.move_button.setText("Move to address book")
        self.move_button.setIcon(theme_icon("go-parent-folder"))
        self.move_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_button)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_button.setMenu(self.move_menu)
        self.act_import = QAction("Import vCard file…", self.toolbar)
        self.act_import.triggered.connect(self.import_file)
        self.act_export = QAction("Export this address book…", self.toolbar)
        self.act_export.triggered.connect(self.export_file)
        self.toolbar.add_center(self.act_edit)
        self.toolbar.add_center(self.tag_button)
        self.toolbar.add_center(self.move_button)
        self.toolbar.add_center(self.act_delete)
        self.toolbar.set_more_extras([self.act_import, self.act_export])  # always in More, below what is folded
        self.toolbar.set_search(self.search_field)

        self.search.textChanged.connect(self._search_changed)
        self.tag_list.currentItemChanged.connect(self._tag_chosen)
        self.list.selectionModel().selectionChanged.connect(self._selection_changed)
        self.list.doubleClicked.connect(self._double_clicked)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.detail.compose_requested.connect(self.compose_requested)

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()

    def _tag_chosen(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.reload_list()

    def _double_clicked(self, index: QModelIndex) -> None:
        self.edit_selected()

    # --- showing ------------------------------------------------------------------------------

    @override
    def on_show(self) -> None:
        self.detail.mail_accounts = self.mail_accounts_source()
        super().on_show()

    @override
    def refresh(self) -> None:
        self.panel.reload()
        self.reload_tags()

    @override
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    @override
    def selection_changed(self) -> None:
        """An address book was chosen: its contacts, and the tags in use."""
        if not self._tags_loaded:
            self._tags_loaded = True
            self.reload_tags()
        self.reload_list()

    def reload_tags(self) -> None:
        def listed(result: object) -> None:
            tags = [tag for tag in (list(result) if isinstance(result, (tuple, list)) else []) if int(getattr(tag, "contacts")) > 0]
            self.colours.update(list(result) if isinstance(result, (tuple, list)) else [])
            keep = self._tag()
            self.tag_list.blockSignals(True)
            self.tag_list.clear()
            everyone = QListWidgetItem("All contacts")
            everyone.setData(ROW_ROLE, ALL_TAGS)
            self.tag_list.addItem(everyone)
            chosen = everyone
            for tag in tags:
                name = str(getattr(tag, "name"))
                item = QListWidgetItem(f"{name}  ({getattr(tag, 'contacts')})")
                item.setData(ROW_ROLE, name)
                item.setIcon(colour_icon(self.colours.colour(name).name() or DEFAULT_COLOUR))
                self.tag_list.addItem(item)
                if name.casefold() == keep.casefold() and keep:
                    chosen = item
            self.tag_list.setCurrentItem(chosen)
            self.tag_list.blockSignals(False)
            self.list.viewport().update()

        self._backend.call("tag.list", {}, listed, lambda error: None, owner=self)

    def _tag(self) -> str:
        item = self.tag_list.currentItem()
        return str(item.data(ROW_ROLE) or "") if item is not None else ""

    def show_contact(self, item: str, collection: str) -> None:
        """Show this contact: its address book is chosen, the search and the tag filter are cleared, and the contact is selected."""
        self._show_item, self._show_collection = item, collection
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.tag_list.blockSignals(True)
        self.tag_list.setCurrentRow(-1)
        self.tag_list.blockSignals(False)
        self.panel.prefer = collection
        if self._enabled:  # otherwise the first load of the address books chooses it
            self.panel.reload(then_choose=collection)

    def reload_list(self, keep: str = "") -> None:
        keep = keep or self._show_item
        collection = self.panel.current_id()
        if not collection:
            self.model.load([], 0)
            self.count_label.setText("")
            return
        self._generation += 1
        generation = self._generation
        selected = [keep] if keep else self._selected_items()  # what is selected stays selected (several contacts at once, as for tags)
        values: dict[str, object] = {"collection": collection, "text": self.search.text().strip(), "limit": LIST_LIMIT}
        if self._tag():
            values["tag"] = self._tag()

        def arrived(result: object) -> None:
            if generation != self._generation:
                return
            rows = list(getattr(result, "contacts"))
            self.model.load(rows, int(getattr(result, "total")))
            self.count_label.setText(f"Contacts ({self.model.total})")
            self.status(f"{self.model.total} contact(s)")
            positions = [position for position in (self.model.position_of(item) for item in selected) if position >= 0]
            if collection == self._show_collection:
                self._show_item = self._show_collection = ""
            if positions:
                self.list.selectionModel().clear()
                for position in positions:
                    self.list.selectionModel().select(self.model.index(position), QItemSelectionModel.SelectionFlag.Select)
                self.list.selectionModel().setCurrentIndex(self.model.index(positions[0]), QItemSelectionModel.SelectionFlag.NoUpdate)
                self._selection_changed()
            elif rows:
                self.list.setCurrentIndex(self.model.index(0))
            else:
                self.detail.show_nothing("There are no contacts here yet" if not self.search.text().strip() else "No contact matches")

        self._backend.call("contact.list", values, arrived, self._failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        self.status(f"Contacts: {error}")

    def _selected_items(self) -> list[str]:
        """The contacts selected now, without those that have an action under way (they get no second one)."""
        return [
            str(getattr(self.model.rows[index.row()], "item")) for index in self.list.selectionModel().selectedRows()
            if 0 <= index.row() < len(self.model.rows) and str(getattr(self.model.rows[index.row()], "item")) not in self.model.pending
        ]

    def _selection_changed(self, selected: object = None, deselected: object = None) -> None:
        items = self._selected_items()
        if len(items) == 1:
            self._backend.call("contact.show", {"item": items[0]}, lambda result: self._show_if_current(items[0], result), self._failed, owner=self)
        elif not items:
            self.detail.show_nothing("There are no contacts here yet" if self.model.rowCount() == 0 and not self.search.text().strip() else "Select a contact")
        else:
            self.detail.show_nothing(f"{len(items)} contacts selected")

    def _show_if_current(self, item: str, result: object) -> None:
        if self._selected_items() == [item]:
            self.detail.set_contact(result)

    # --- changing -----------------------------------------------------------------------------

    def _books(self) -> list[object]:
        return [info for info in self.panel.collections]

    def _tag_names(self) -> list[str]:
        return [str(self.tag_list.item(row).data(ROW_ROLE)) for row in range(self.tag_list.count()) if str(self.tag_list.item(row).data(ROW_ROLE))]

    @override
    def new_item(self) -> None:
        if not self.panel.current_id():
            return
        editor = ContactEditor(self._backend, self._books(), self._tag_names(), None, self.panel.current_id(), self.content)
        editor.saved.connect(self._after_save)
        editor.exec()

    def edit_selected(self) -> None:
        items = self._selected_items()
        if len(items) != 1:
            return

        def opened(result: object) -> None:
            editor = ContactEditor(self._backend, self._books(), self._tag_names(), result, str(getattr(result, "collection")), self.content)
            editor.saved.connect(self._after_save)
            editor.exec()

        self._backend.call("contact.show", {"item": items[0]}, opened, self._failed, owner=self)

    def _after_save(self, item: str) -> None:
        """A contact was saved: its row followed the fact; it is selected; the tags list shows the new numbers."""
        self.reload_tags()
        if item:
            self._select_when_there = item
            self._select_now()

    def _select_now(self) -> None:
        position = self.model.position_of(self._select_when_there) if self._select_when_there else -1
        if position >= 0:
            self._select_when_there = ""
            self.list.setCurrentIndex(self.model.index(position))
            self.list.scrollTo(self.model.index(position))

    @override
    def apply_item(self, change: object) -> None:
        """A contact was added, changed, deleted or moved: only that row changes (D-146)."""
        kind, collection, item = str(getattr(change, "kind")), str(getattr(change, "folder")), str(getattr(change, "message_id"))
        shown = self.panel.current_id()
        if kind in ("item_removed", "item_moved") and collection == shown:
            self.model.pending.discard(item)
            self.model.remove_item(item)
            self._counted()
        wanted = str(getattr(change, "new_message_id")) if kind == "item_moved" else item
        target = str(getattr(change, "to_folder")) if kind == "item_moved" else collection
        if kind in ("item_added", "item_changed", "item_moved") and target == shown:
            values: dict[str, object] = {"collection": shown, "text": self.search.text().strip(), "item": wanted}
            if self._tag():
                values["tag"] = self._tag()

            def arrived(result: object) -> None:
                if self.panel.current_id() != shown:
                    return
                rows = list(getattr(result, "contacts"))
                if rows:
                    self.model.put(rows[0])
                else:
                    self.model.remove_item(wanted)  # it no longer matches the search or the tag
                self._counted()
                self._select_now()
                if self._selected_items() == [wanted]:
                    self._selection_changed()  # the contact on the right changed: shown again

            self._backend.call("contact.list", values, arrived, self._failed, owner=self)

    def _counted(self) -> None:
        self.count_label.setText(f"Contacts ({self.model.total})")

    def _take_away(self, items: list[str]) -> None:
        """At the key press of a delete or a move: greyed, and the selection moves at once to the next contact."""
        self.model.pending.update(items)
        for item in items:
            self.model.redraw(item)
        taken = set(items)
        rows = [self.model.position_of(item) for item in items]
        rows = [row for row in rows if row >= 0]
        if not rows:
            return
        after = next((row for row in range(max(rows) + 1, self.model.rowCount()) if str(getattr(self.model.rows[row], "item")) not in taken | self.model.pending), None)
        before = next((row for row in range(min(rows) - 1, -1, -1) if str(getattr(self.model.rows[row], "item")) not in taken | self.model.pending), None)
        target = after if after is not None else before
        if target is None:
            self.list.selectionModel().clear()
            self.detail.show_nothing("Select a contact")
            return
        self.list.setCurrentIndex(self.model.index(target))
        self.list.scrollTo(self.model.index(target))

    def _ended(self, item: str) -> None:
        self.model.pending.discard(item)
        self.model.redraw(item)

    @override
    def delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        word = "this contact" if len(items) == 1 else f"these {len(items)} contacts"
        if QMessageBox.question(self.content, "Delete", f"Delete {word}?\n\nThe files are kept in the store's .deleted directory for 30 days.") != QMessageBox.StandardButton.Yes:
            return
        self._take_away(items)
        for item in items:
            self._backend.call("contact.delete", {"item": item}, lambda result, chosen=item: self._ended(chosen), lambda error, chosen=item: self._failed_for(chosen, error), owner=self)

    def _failed_for(self, item: str, error: ChronoMailError) -> None:
        self._ended(item)
        self._failed(error)

    def _fill_tag_menu(self) -> None:
        self.tag_menu.clear()
        items = self._selected_items()
        if not items:
            self.tag_menu.addAction("(select contacts first)").setEnabled(False)
            return
        have: dict[str, int] = {}
        for index in self.list.selectionModel().selectedRows():
            for tag in getattr(self.model.rows[index.row()], "tags"):
                have[tag.casefold()] = have.get(tag.casefold(), 0) + 1
        for name in self._tag_names():
            action = self.tag_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(have.get(name.casefold(), 0) == len(items))
            action.triggered.connect(lambda checked=False, tag=name, on=not action.isChecked(): self._set_tag(tag, on))
        self.tag_menu.addSeparator()
        self.tag_menu.addAction("New tag…", self._new_tag)
        self.tag_menu.addAction("Remove all tags", lambda: self._edit_selected({"clear": ["tag"]}))

    def _set_tag(self, name: str, on: bool) -> None:
        self._edit_selected({"add_tag": [name]} if on else {"remove_tag": [name]})

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self.content, "New tag", "Name of the tag:")
        if ok and name.strip():
            self._set_tag(name.strip(), True)

    def _edit_selected(self, values: dict[str, object]) -> None:
        items = self._selected_items()
        remaining = {"count": len(items)}

        def done(result: object) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self.reload_tags()  # the rows followed their facts; the tags list shows the new numbers

        for item in items:
            self._backend.call("contact.edit", {"item": item, **values}, done, self._failed, owner=self)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        items = self._selected_items()
        current = next((info for info in self._books() if str(getattr(info, "collection")) == self.panel.current_id()), None)
        others = [info for info in self._books() if current is not None and str(getattr(info, "account")) == str(getattr(current, "account")) and info is not current]
        if not items or not others:
            self.move_menu.addAction("(no other address book)").setEnabled(False)
            return
        for info in others:
            target = str(getattr(info, "collection"))
            self.move_menu.addAction(str(getattr(info, "name")), lambda checked=False, chosen=target: self._move_selected(chosen))

    def _move_selected(self, target: str) -> None:
        items = self._selected_items()
        self._take_away(items)
        for item in items:
            self._backend.call("contact.move", {"item": item, "collection": target}, lambda result, chosen=item: self._ended(chosen), lambda error, chosen=item: self._failed_for(chosen, error), owner=self)

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.content, "Import contacts", "", "vCard files (*.vcf *.vcard);;All files (*)")
        collection = self.panel.current_id()
        if not path or not collection:
            return
        try:
            text = Path(path).read_text("utf-8", errors="replace")
        except OSError as error:
            self.status(f"The file could not be read: {error.strerror}")
            return

        def done(result: object) -> None:
            self.status(f"Imported {getattr(result, 'imported')} contact(s), updated {getattr(result, 'updated')}, skipped {getattr(result, 'skipped')}")  # its fact fetches the list again

        self._backend.call("contact.import", {"collection": collection, "text": text}, done, self._failed, owner=self)

    def export_file(self) -> None:
        collection = self.panel.current_id()
        if not collection:
            return
        path, _ = QFileDialog.getSaveFileName(self.content, "Export contacts", "contacts.vcf", "vCard files (*.vcf)")
        if not path:
            return

        def done(result: object) -> None:
            try:
                Path(path).write_text(str(getattr(result, "text")), "utf-8")
                self.status(f"Exported {getattr(result, 'contacts')} contact(s)")
            except OSError as error:
                self.status(f"The file could not be written: {error.strerror}")

        self._backend.call("contact.export", {"collection": collection}, done, self._failed, owner=self)

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self.content)
        items = self._selected_items()
        menu.addAction("New contact", self.new_item)
        if items:
            menu.addSeparator()
            if len(items) == 1:
                menu.addAction("Edit", self.edit_selected)
                row = self.model.rows[self.list.selectionModel().selectedRows()[0].row()]
                subtitle = str(getattr(row, "subtitle"))
                if "@" in subtitle:
                    menu.addAction("Write an e-mail", lambda: self.compose_requested.emit(subtitle))
            self._fill_tag_menu()
            menu.addMenu(self.tag_menu).setText("Tag")
            self._fill_move_menu()
            menu.addMenu(self.move_menu).setText("Move to address book")
            menu.addAction("Delete", self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(position))
