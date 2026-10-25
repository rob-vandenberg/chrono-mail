from collections.abc import Callable, Sequence
from pathlib import Path
from typing import override

from PySide6.QtCore import QItemSelectionModel, QMimeData, QModelIndex, QPersistentModelIndex, QPoint, QRect, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_addressbook import ContactRow
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR, NEW_TAG
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_addressbook import (
    CONTACT_DELETE, CONTACT_EDIT, CONTACT_EXPORT, CONTACT_IMPORT, CONTACT_LIST, CONTACT_MOVE, CONTACT_SHOW, ContactInfo, ContactList,
)
from chrono_mail.engine.commands_addressbook import EditParameters as ContactEdit
from chrono_mail.engine.commands_addressbook import ExportParameters as ContactExport
from chrono_mail.engine.commands_addressbook import ExportResult as ContactExportResult
from chrono_mail.engine.commands_addressbook import ImportParameters as ContactImport
from chrono_mail.engine.commands_addressbook import ImportResult as ContactImportResult
from chrono_mail.engine.commands_addressbook import ItemParameters as ContactItem
from chrono_mail.engine.commands_addressbook import MoveParameters as ContactMove
from chrono_mail.engine.commands_groups import (
    CONTACT_GROUP_ADD_MEMBERS, CONTACT_GROUP_ADDRESSES, CONTACT_GROUP_CREATE, CONTACT_GROUP_DELETE, CONTACT_GROUP_LIST, CONTACT_GROUP_REMOVE_MEMBERS,
    CONTACT_GROUP_RENAME, GroupAddresses, GroupCreateParameters, GroupItem, GroupListParameters, GroupMembersParameters, GroupRenameParameters, GroupRow,
)
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_addressbook import ListParameters as ContactListParameters
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.syncmarks import CONFLICT, MARK_SIZE, MARK_TIPS, SyncMarks, paint_mark
from chrono_mail.ui.collectionpanel import ROLE_GROUP, colour_icon
from chrono_mail.ui.contactdetail import ContactDetail, avatar_pixmap
from chrono_mail.ui.contacteditor import ContactEditor
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.dragdata import CONTACTS_MIME_TYPE, contacts_mime_data
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimlist import MarkDelegate, PimItemModel, PimListPage
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.tagchips import TagColourBook, paint_chips
from chrono_mail.ui.tagging import tag_icon
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "contactsview.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v1.10.277: Contact groups (R-39): the groups under each address book, a group's members as the list, New group…, Rename…, Delete… and Write an
#            e-mail in a group's menu, Add to group (ticks) and Remove from group, contacts dragged onto a group; every change in place (D-146)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: every engine call typed; delete and move are typed steps
# v1.1.245: Delete, Move to, New tag…, the greying, the search delay, the error line and the triangle are the shared ones of pimlist (R-07)
# v1.1.244: The tag list follows in place (only the numbers that changed, a tag that came or went); the chosen tag stays; a failure is told (R-06)
# v1.1.229: A click on a contact's triangle opens its conflict; hovering a mark says what it is (the owner)
# v1.1.226: A contact's sync mark before its name (D-194)
# v1.1.215: The separator between the list and the details is the program's one separator (D-188)
# v1.1.205: every contact of the address book (no cut of 5,000; the rows come from the index alone, R-02); contact.list typed
# v1.1.172: The Tag button's colour from the colour file (D-158)
# v0.5.167: the toolbar folds like Mail's; Tag and Move to have icons; Import and Export live in More, which folded buttons join; the search field looks like Mail's (D-154)
# v0.5.159: the list follows the facts (D-146 step 5): one contact in, out or changed at its place; the selection moves at the key press of a delete or move; contacts with an action under way are greyed; no reload after an action
# v0.1.89: show_contact: one contact is shown, from the Contact column of the message list
# v0.0.62: Initial contacts section: list, detail pane, editor, tags, move, import and export

ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
PENDING_ROLE: int = Qt.ItemDataRole.UserRole + 2  # true on a contact with an action under way (drawn greyed)
ROW_HEIGHT: int = 56
AVATAR_SIZE: int = 36
ALL_TAGS: str = ""


class ContactListModel(PimItemModel):
    """Every contact of the address book that is chosen, sorted by family name (rows from the index alone, never cut)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[ContactRow] = []
        self.total: int = 0

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

    def put(self, row: ContactRow) -> None:
        """One contact comes into the list or changes: at its place in the order of family names. A contact that was there
        keeps its row (changed in place, or moved to its new place), so its selection stays."""
        item = row.item
        key = (row.sort_key, row.name.casefold())
        old = self.position_of(item)
        others = [known for known in self.rows if known.item != item]
        position = next((number for number, known in enumerate(others) if (known.sort_key, known.name.casefold()) > key), len(others))
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

    def load(self, rows: Sequence[ContactRow], total: int) -> None:
        self.beginResetModel()
        self.rows = list(rows)
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
            return row.name
        if role == ROW_ROLE:
            return row
        if role == PENDING_ROLE:
            return row.item in self.pending
        return None

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else flags

    @override
    def mimeTypes(self) -> list[str]:
        return [CONTACTS_MIME_TYPE]

    @override
    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        """The contacts dragged (onto a group, R-39): their address book and ids; those with an action under way stay behind."""
        rows = [self.rows[index.row()] for index in indexes if 0 <= index.row() < len(self.rows) and self.rows[index.row()].item not in self.pending]
        return contacts_mime_data(rows[0].collection if rows else "", list(dict.fromkeys(row.item for row in rows)))

    def follow(self, rows: Sequence[ContactRow]) -> None:
        """The list as it is now, changed in place: a row that went is taken out, one that came or changed is put at its place; the rest and the
        selection stay (D-146)."""
        wanted = {row.item: row for row in rows}
        for known in [row for row in self.rows if row.item not in wanted]:
            self.remove_item(known.item)
        shown = {row.item: row for row in self.rows}
        for row in rows:
            if shown.get(row.item) != row:
                self.put(row)

    @override
    def position_of(self, item: str) -> int:
        return next((position for position, row in enumerate(self.rows) if row.item == item), -1)

    @override
    def item_at(self, position: int) -> str:
        return self.rows[position].item if 0 <= position < len(self.rows) else ""


class ContactDelegate(MarkDelegate):
    """A contact as a row: the avatar, the name, the organisation or the address under it, and the tags as coloured labels."""

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(0, ROW_HEIGHT)

    @override
    def _mark_area(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> tuple[QRect | None, str, str]:
        """Where a contact's sync mark is drawn, its state and its item (no area without a mark): the same place paint uses."""
        row = index.data(ROW_ROLE)
        model = index.model()
        if row is None or not isinstance(model, ContactListModel):
            return None, "", ""
        item = str(row.item)
        state = model.mark_of(item)
        if not state:
            return None, "", item
        rect = _rect(option)
        strong = QFontMetrics(_typed(option, "font", QFont))
        centre = rect.top() + 8 + strong.height() // 2
        return QRect(rect.left() + 10 + AVATAR_SIZE + 10, centre - MARK_SIZE // 2, MARK_SIZE, MARK_SIZE), state, item

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
        name = str(row.name)
        painter.drawPixmap(rect.left() + 10, rect.top() + (rect.height() - AVATAR_SIZE) // 2, avatar_pixmap(name, AVATAR_SIZE))
        left = rect.left() + 10 + AVATAR_SIZE + 10
        tags = tuple(row.tags)
        chips = paint_chips(painter, QRect(left, rect.top(), rect.right() - left - 10, rect.height()), tags, self._colours, _typed(option, "font", QFont)) if tags else 0
        width = rect.right() - left - 10 - chips
        font = painter.font()
        strong = QFontMetrics(font)
        painter.setPen(text)
        model = index.model()
        mark = model.mark_of(str(row.item)) if isinstance(model, ContactListModel) else ""
        mark_room = paint_mark(painter, left, rect.top() + 8 + strong.height() // 2, mark)
        title_rect = QRect(left + mark_room, rect.top() + 8, width - mark_room, strong.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, strong.elidedText(name, Qt.TextElideMode.ElideRight, width - mark_room))
        subtitle = str(row.subtitle)
        if subtitle:
            painter.setPen(quiet)
            painter.drawText(QRect(left, rect.top() + 8 + strong.height() + 2, width, strong.height()), Qt.AlignmentFlag.AlignVCenter, strong.elidedText(subtitle, Qt.TextElideMode.ElideRight, width))
        line = QColor(text)
        line.setAlpha(28)
        painter.setPen(line)
        painter.drawLine(rect.left() + 10, rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class ContactsPage(PimListPage):
    """Address books on the left (with the tags to filter by), the contacts in the middle, and one contact on the right."""

    kind = "contacts"
    multiple = False
    new_label = "New contact"
    new_icon = "contact-new"
    section_name = "Contacts"
    item_word = "contact"

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
        self.list.setDragEnabled(True)  # onto a group in the sidebar (R-39)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        pane_layout.addWidget(self.list, 1)
        self.detail = ContactDetail(backend, self.colours)
        splitter = PaneSplitter(Qt.Orientation.Horizontal)
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
        self.act_edit = QAction("Edit", self.toolbar)
        self.act_edit.setIcon(theme_icon("document-edit"))
        self.act_edit.triggered.connect(self.edit_selected)
        self.act_delete = QAction("Delete", self.toolbar)
        self.act_delete.setIcon(theme_icon("edit-delete"))
        self.act_delete.triggered.connect(self.delete_selected)
        self.tag_button = QToolButton()
        self.tag_button.setText("Tag")
        self.tag_button.setIcon(tag_icon(QColor(NEW_TAG)))
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
        self.group_button = QToolButton()
        self.group_button.setText("Add to group")
        self.group_button.setIcon(theme_icon("system-users"))
        self.group_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.group_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.group_menu = QMenu(self.group_button)
        self.group_menu.aboutToShow.connect(self._fill_group_menu)
        self.group_button.setMenu(self.group_menu)
        self.act_remove_from_group = QAction("Remove from group", self.toolbar)
        self.act_remove_from_group.triggered.connect(self.remove_from_group)
        self.act_import = QAction("Import vCard file…", self.toolbar)
        self.act_import.triggered.connect(self.import_file)
        self.act_export = QAction("Export this address book…", self.toolbar)
        self.act_export.triggered.connect(self.export_file)
        self.toolbar.add_center(self.act_edit)
        self.toolbar.add_center(self.tag_button)
        self.toolbar.add_center(self.group_button)
        self.toolbar.add_center(self.act_remove_from_group)
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
        self.panel.group_actions = self._group_actions
        self.panel.book_actions = self._book_actions
        self.panel.group_delete = self.delete_group
        self.panel.contacts_dropped.connect(self._dropped)
        self.panel.reloaded.connect(self._panel_reloaded)
        self._group_after_reload: str = ""  # the group chosen before the sidebar was filled again: chosen again when it is still there
        self._listed_view: tuple[str, str] | None = None  # the address book and group whose list has arrived (the count line is only theirs)
        self._update_group_actions()

    def _tag_chosen(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.reload_list()

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
    def selection_changed(self) -> None:
        """An address book was chosen: its contacts, and the tags in use."""
        if not self._tags_loaded:
            self._tags_loaded = True
            self.reload_tags()
        group = self.panel.current_group()
        self._group_after_reload = group.item if group is not None else ""
        self._update_group_actions()
        self.reload_list()

    def reload_tags(self) -> None:
        def listed(result: tuple[TagInfo, ...]) -> None:
            tags = [tag for tag in result if tag.contacts > 0]
            self.colours.update(list(result))
            self._follow_tags([(tag.name, tag.contacts) for tag in tags])
            self.list.viewport().update()

        self._backend.ask(TAG_LIST, NoParameters(), listed, lambda error: self.status(f"The tags of the contacts could not be read: {error}"), owner=self)

    def _follow_tags(self, tags: list[tuple[str, int]]) -> None:
        """The tag list as it is now, changed in place: a number that changed is written again, a tag that came is put at its place, one with
        no contacts left goes; the rest, and the tag chosen, stay as they are (D-146). The chosen tag going chooses All contacts."""
        if self.tag_list.count() == 0 or str(self.tag_list.item(0).data(ROW_ROLE)) != ALL_TAGS:
            everyone = QListWidgetItem("All contacts")
            everyone.setData(ROW_ROLE, ALL_TAGS)
            self.tag_list.insertItem(0, everyone)
            if self.tag_list.currentItem() is None:
                self.tag_list.blockSignals(True)  # the first filling: All contacts is chosen as it was, nothing to load again
                self.tag_list.setCurrentItem(everyone)
                self.tag_list.blockSignals(False)
        wanted = {name.casefold() for name, _ in tags}
        for row in range(self.tag_list.count() - 1, 0, -1):
            if str(self.tag_list.item(row).data(ROW_ROLE)).casefold() not in wanted:
                self.tag_list.takeItem(row)
        for number, (name, contacts) in enumerate(tags, start=1):
            text = f"{name}  ({contacts})"
            item = self.tag_list.item(number)
            if item is None or str(item.data(ROW_ROLE)).casefold() != name.casefold():
                elsewhere = next((row for row in range(number + 1, self.tag_list.count()) if str(self.tag_list.item(row).data(ROW_ROLE)).casefold() == name.casefold()), None)
                if elsewhere is not None:  # its place in the order changed (a rename)
                    chosen = self.tag_list.currentRow() == elsewhere
                    item = self.tag_list.takeItem(elsewhere)
                    self.tag_list.insertItem(number, item)
                    if chosen:
                        self.tag_list.blockSignals(True)
                        self.tag_list.setCurrentItem(item)  # the same tag stays chosen: nothing to load again
                        self.tag_list.blockSignals(False)
                else:
                    item = QListWidgetItem(text)
                    item.setData(ROW_ROLE, name)
                    self.tag_list.insertItem(number, item)
            if item.text() != text:
                item.setText(text)
            item.setIcon(colour_icon(self.colours.colour(name).name() or COLLECTION_WITHOUT_COLOUR))

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

    @override
    def reload_list(self, keep: str = "") -> None:
        keep = keep or self._show_item
        collection = self.panel.current_id()
        if not collection:
            self.model.load([], 0)
            self.count_label.setText("")
            return
        self._generation += 1
        generation = self._generation
        self._listed_view = None
        selected = [keep] if keep else self._selected_items()  # what is selected stays selected (several contacts at once, as for tags)
        parameters = ContactListParameters(collection=collection, text=self.search.text().strip(), tag=self._tag(), limit=0, group=self._group())  # every contact: from the index alone

        def arrived(result: ContactList) -> None:
            if generation != self._generation:
                return
            rows = result.contacts
            self.model.load(rows, result.total)
            self._listed_view = (collection, parameters.group)
            self._counted()
            self.status(counted(self.model.total, "contact"))
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
                self.detail.show_nothing(self._empty_text() if not self.search.text().strip() else "No contact matches")

        self._backend.ask(CONTACT_LIST, parameters, arrived, self._failed, owner=self)

    def _empty_text(self) -> str:
        return "This group has no members yet" if self.panel.current_group() is not None else "There are no contacts here yet"

    @override
    def _selected_items(self) -> list[str]:
        """The contacts selected now, without those that have an action under way (they get no second one)."""
        return [
            (self.model.rows[index.row()].item) for index in self.list.selectionModel().selectedRows()
            if 0 <= index.row() < len(self.model.rows) and (self.model.rows[index.row()].item) not in self.model.pending
        ]

    def _selection_changed(self, selected: object = None, deselected: object = None) -> None:
        items = self._selected_items()
        if len(items) == 1:
            self._backend.ask(CONTACT_SHOW, ContactItem(items[0]), lambda result: self._show_if_current(items[0], result), self._failed, owner=self)
        elif not items:
            self.detail.show_nothing(self._empty_text() if self.model.rowCount() == 0 and not self.search.text().strip() else "Select a contact")
        else:
            self.detail.show_nothing(f"{len(items)} contacts selected")
        self._update_group_actions()

    def _show_if_current(self, item: str, result: ContactInfo) -> None:
        if self._selected_items() == [item]:
            self.detail.set_contact(result)

    # --- changing -----------------------------------------------------------------------------

    def _books(self) -> list[CollectionSummary]:
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

    @override
    def edit_selected(self) -> None:
        items = self._selected_items()
        if len(items) != 1:
            return

        def opened(result: ContactInfo) -> None:
            editor = ContactEditor(self._backend, self._books(), self._tag_names(), result, result.collection, self.content)
            editor.saved.connect(self._after_save)
            editor.exec()

        self._backend.ask(CONTACT_SHOW, ContactItem(items[0]), opened, self._failed, owner=self)

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
    def apply_item(self, change: Change) -> None:
        """A contact was added, changed, deleted or moved: only that row changes (D-146)."""
        kind, collection, item = str(change.kind), change.folder, change.message_id
        shown = self.panel.current_id()
        if kind in ("item_removed", "item_moved") and collection == shown:
            self.model.pending.discard(item)
            self.model.remove_item(item)
            self._counted()
        wanted = change.new_message_id if kind == "item_moved" else item
        target = change.to_folder if kind == "item_moved" else collection
        groups_before = {row.item for row in self._groups_shown(collection)} | {row.item for row in self._groups_shown(target)}
        for book in dict.fromkeys((collection, target)):
            if book and self.panel.info(book) is not None:
                self.reload_groups(book, then=lambda rows, book=book: self._group_changed(book, wanted, item, groups_before, rows))
        if kind in ("item_added", "item_changed", "item_moved") and target == shown:
            parameters = ContactListParameters(collection=shown, text=self.search.text().strip(), tag=self._tag(), item=wanted, group=self._group())

            def arrived(result: ContactList) -> None:
                if self.panel.current_id() != shown:
                    return
                rows = result.contacts
                if rows:
                    self.model.put(rows[0])
                else:
                    self.model.remove_item(wanted)  # it no longer matches the search or the tag
                self._counted()
                self._select_now()
                if self._selected_items() == [wanted]:
                    self._selection_changed()  # the contact on the right changed: shown again

            self._backend.ask(CONTACT_LIST, parameters, arrived, self._failed, owner=self)

    def _counted(self) -> None:
        group = self.panel.current_group()
        if self._listed_view != (self.panel.current_id(), group.item if group is not None else ""):
            return  # the list of what is chosen has not arrived yet: its numbers are not known
        if group is None:
            self.count_label.setText(f"Contacts ({self.model.total})")
            self.count_label.setToolTip("")
            return
        self.count_label.setText(f"Members ({self.model.total})" + (f", and {group.others} more without a contact here" if group.others else ""))
        self.count_label.setToolTip("Plain addresses, and contacts the address book does not have; a message to the group goes to the plain addresses too" if group.others else "")

    # --- groups (R-39) ------------------------------------------------------------------------

    def _group(self) -> str:
        group = self.panel.current_group()
        return group.item if group is not None else ""

    def _groups_shown(self, collection: str) -> list[GroupRow]:
        """The groups of an address book as the sidebar shows them."""
        return [row for row in (child.data(0, ROLE_GROUP) for child in self.panel.group_entries(collection)) if isinstance(row, GroupRow)] if collection else []

    def reload_groups(self, collection: str | None = None, choose: str = "", then: Callable[[tuple[GroupRow, ...]], None] | None = None) -> None:
        """The groups of one address book (None: of all of them) as they are now, put in the sidebar in place."""

        def listed(rows: tuple[GroupRow, ...]) -> None:
            books = [collection] if collection is not None else [info.collection for info in self.panel.collections]
            for book in books:
                self.panel.set_groups(book, [row for row in rows if row.collection == book], choose)
            self._counted()
            self._update_group_actions()
            if then is not None:
                then(rows)

        self._backend.ask(CONTACT_GROUP_LIST, GroupListParameters(collection), listed, self._failed, owner=self)

    def _panel_reloaded(self) -> None:
        """The sidebar was filled again (its entries are new): the groups of every address book, the group chosen before chosen again."""
        self.reload_groups(choose=self._group_after_reload)

    def _group_changed(self, collection: str, wanted: str, item: str, before: set[str], rows: tuple[GroupRow, ...]) -> None:
        """A fact about an item of an address book whose groups were just fetched: when that item is (or was) a group, who is in which group
        changed, so the list follows in place: the members of the group shown, or the tick marks of the address book shown."""
        if collection != self.panel.current_id() or not ({wanted, item} & (before | {row.item for row in rows})):
            return
        group = self._group()
        parameters = ContactListParameters(collection=collection, text=self.search.text().strip(), tag=self._tag(), limit=0, group=group)

        def arrived(result: ContactList) -> None:
            if self.panel.current_id() != collection or self._group() != group:
                return
            self.model.follow(result.contacts)
            self._counted()

        self._backend.ask(CONTACT_LIST, parameters, arrived, self._failed, owner=self)

    def _update_group_actions(self) -> None:
        group = self.panel.current_group()
        self.act_remove_from_group.setEnabled(group is not None and not group.read_only and bool(self._selected_items()))

    def _book_actions(self, menu: QMenu, book: CollectionSummary) -> None:
        menu.addAction("New group…", lambda: self.new_group(book.collection)).setEnabled(not book.read_only)

    def _group_actions(self, menu: QMenu, group: GroupRow) -> None:
        """The menu of a group in the sidebar."""
        menu.addAction("Write an e-mail", lambda: self.write_to_group(group)).setEnabled(bool(group.members or group.others))
        menu.addSeparator()
        menu.addAction("New group…", lambda: self.new_group(group.collection)).setEnabled(not group.read_only)
        menu.addAction("Rename…", lambda: self.rename_group(group)).setEnabled(not group.read_only)
        menu.addAction("Delete…", lambda: self.delete_group(group)).setEnabled(not group.read_only)

    def new_group(self, collection: str, contacts: tuple[str, ...] = ()) -> None:
        """A new group in this address book (with these contacts in it); it is chosen when it is there."""
        name, ok = QInputDialog.getText(self.content, "New group", "Name:")
        if not ok or not name.strip():
            return
        self._backend.ask(
            CONTACT_GROUP_CREATE, GroupCreateParameters(collection, name.strip(), contacts),
            lambda row: self.reload_groups(row.collection, choose="" if contacts else row.item), self._failed, owner=self,
        )

    def rename_group(self, group: GroupRow) -> None:
        name, ok = QInputDialog.getText(self.content, "Rename", "New name:", text=group.name)
        if ok and name.strip() and name.strip() != group.name:
            self._backend.ask(CONTACT_GROUP_RENAME, GroupRenameParameters(group.item, name.strip()), lambda row: None, self._failed, owner=self)  # the fact renames it

    def delete_group(self, group: GroupRow) -> None:
        """Delete a group (it asks first): its card only, never its members."""
        book = self.panel.info(group.collection)
        text = f"Delete the group “{group.name}”?\n\nIts contacts stay in the address book."
        if book is not None and book.server:
            text += "\n\nEvery program that synchronises with the server will remove the group too."
        if QMessageBox.question(self.content, "Delete", text) == QMessageBox.StandardButton.Yes:
            self._backend.ask(CONTACT_GROUP_DELETE, GroupItem(group.item), lambda result: None, self._failed, owner=self)  # the fact takes it away

    def write_to_group(self, group: GroupRow) -> None:
        """A new message to every member of the group (their preferred addresses in To)."""

        def got(result: GroupAddresses) -> None:
            if not result.addresses:
                self.status(f"No member of “{result.name}” has an e-mail address")
                return
            self.compose_requested.emit(", ".join(result.addresses))
            if result.without_address:
                self.status(f"Left out, without an e-mail address: {counted(result.without_address, 'member')} of “{result.name}”")

        self._backend.ask(CONTACT_GROUP_ADDRESSES, GroupItem(group.item), got, self._failed, owner=self)

    def _fill_group_menu(self) -> None:
        """Add to group: a tick at each group of the address book that every selected contact is in; a click puts them all in, or takes them all out."""
        self.group_menu.clear()
        items = self._selected_items()
        collection = self.panel.current_id()
        if not items:
            self.group_menu.addAction("(select contacts first)").setEnabled(False)
            return
        groups = self._groups_shown(collection)
        if not groups:
            self.group_menu.addAction("(this address book has no groups)").setEnabled(False)
        selected = [self.model.rows[index.row()] for index in self.list.selectionModel().selectedRows() if 0 <= index.row() < len(self.model.rows)]
        for group in groups:
            action = self.group_menu.addAction(group.name)
            action.setCheckable(True)
            action.setChecked(all(group.item in row.groups for row in selected))
            action.setEnabled(not group.read_only)
            action.triggered.connect(lambda checked=False, chosen=group.item, on=not action.isChecked(): self._set_group(chosen, on))
        self.group_menu.addSeparator()
        book = self.panel.info(collection)
        self.group_menu.addAction("New group…", lambda: self.new_group(collection, tuple(items))).setEnabled(book is not None and not book.read_only)

    def _set_group(self, group: str, on: bool, items: list[str] | None = None) -> None:
        contacts = tuple(items if items is not None else self._selected_items())
        if contacts:
            command = CONTACT_GROUP_ADD_MEMBERS if on else CONTACT_GROUP_REMOVE_MEMBERS
            self._backend.ask(command, GroupMembersParameters(group, contacts), lambda row: None, self._failed, owner=self)  # the facts follow

    def remove_from_group(self) -> None:
        """The selected contacts out of the group shown; they stay in the address book."""
        group = self.panel.current_group()
        if group is not None and not group.read_only:
            self._set_group(group.item, False)

    def _dropped(self, group: str, items: list[str]) -> None:
        """Contacts dropped on a group in the sidebar: put in it."""
        self._set_group(group, True, items)

    def _fill_tag_menu(self) -> None:
        self.tag_menu.clear()
        items = self._selected_items()
        if not items:
            self.tag_menu.addAction("(select contacts first)").setEnabled(False)
            return
        have: dict[str, int] = {}
        for index in self.list.selectionModel().selectedRows():
            for tag in self.model.rows[index.row()].tags:
                have[tag.casefold()] = have.get(tag.casefold(), 0) + 1
        for name in self._tag_names():
            action = self.tag_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(have.get(name.casefold(), 0) == len(items))
            action.triggered.connect(lambda checked=False, tag=name, on=not action.isChecked(): self._set_tag(tag, on))
        self.tag_menu.addSeparator()
        self.tag_menu.addAction("New tag…", self._new_tag)
        self.tag_menu.addAction("Remove all tags", lambda: self._edit_selected(clear=("tag",)))

    @override
    def _delete_step(self, item: str) -> AnyStep[object]:
        return Step(CONTACT_DELETE, ContactItem(item))

    @override
    def _move_step(self, item: str, target: str) -> AnyStep[object]:
        return Step(CONTACT_MOVE, ContactMove(item, target))

    @override
    def _items(self) -> PimItemModel:
        return self.model

    @override
    def _view(self) -> QListView:
        return self.list

    @override
    def _marks_delegate(self) -> MarkDelegate:
        return self.delegate

    @override
    def _search_box(self) -> QLineEdit:
        return self.search

    @override
    def _nothing_left(self) -> None:
        self.detail.show_nothing("Select a contact")

    @override
    def _set_tag(self, name: str, on: bool) -> None:
        self._edit_selected(add_tag=(name,)) if on else self._edit_selected(remove_tag=(name,))

    def _edit_selected(self, add_tag: tuple[str, ...] = (), remove_tag: tuple[str, ...] = (), clear: tuple[str, ...] = ()) -> None:
        items = self._selected_items()
        remaining = {"count": len(items)}

        def done(result: ContactInfo) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self.reload_tags()  # the rows followed their facts; the tags list shows the new numbers

        for item in items:
            self._backend.ask(CONTACT_EDIT, ContactEdit(item=item, add_tag=add_tag, remove_tag=remove_tag, clear=clear), done, self._failed, owner=self)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        items = self._selected_items()
        current = next((info for info in self._books() if info.collection == self.panel.current_id()), None)
        others = [info for info in self._books() if current is not None and info.account == current.account and info is not current]
        if not items or not others:
            self.move_menu.addAction("(no other address book)").setEnabled(False)
            return
        for info in others:
            target = info.collection
            self.move_menu.addAction(info.name, lambda checked=False, chosen=target: self._move_selected(chosen))

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

        def done(result: ContactImportResult) -> None:
            self.status(f"Imported {counted(result.imported, 'contact')}, updated {result.updated:,}, skipped {result.skipped:,}")  # its fact fetches the list again

        self._backend.ask(CONTACT_IMPORT, ContactImport(collection, text), done, self._failed, owner=self)

    def export_file(self) -> None:
        collection = self.panel.current_id()
        if not collection:
            return
        path, _ = QFileDialog.getSaveFileName(self.content, "Export contacts", "contacts.vcf", "vCard files (*.vcf)")
        if not path:
            return

        def done(result: ContactExportResult) -> None:
            try:
                Path(path).write_text(result.text, "utf-8")
                self.status(f"Exported {counted(result.contacts, 'contact')}")
            except OSError as error:
                self.status(f"The file could not be written: {error.strerror}")

        self._backend.ask(CONTACT_EXPORT, ContactExport(collection=collection), done, self._failed, owner=self)

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self.content)
        items = self._selected_items()
        menu.addAction("New contact", self.new_item)
        if items:
            menu.addSeparator()
            if len(items) == 1:
                menu.addAction("Edit", self.edit_selected)
                row = self.model.rows[self.list.selectionModel().selectedRows()[0].row()]
                subtitle = row.subtitle
                if "@" in subtitle:
                    menu.addAction("Write an e-mail", lambda: self.compose_requested.emit(subtitle))
            self._fill_tag_menu()
            menu.addMenu(self.tag_menu).setText("Tag")
            self._fill_group_menu()
            menu.addMenu(self.group_menu).setText("Add to group")
            if self.panel.current_group() is not None:
                menu.addAction(self.act_remove_from_group)
            self._fill_move_menu()
            menu.addMenu(self.move_menu).setText("Move to address book")
            menu.addAction("Delete", self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(position))

