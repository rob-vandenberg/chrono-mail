from dataclasses import replace as changed

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QApplication, QHeaderView, QMenu, QWidget

from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.columnfit import MINIMUM_ICON, ColumnLayout, drag, fit, layout_from_pixels, read_layout
from chrono_mail.ui.columns import BY_ID, CARD, DEFAULT_COLUMNS, SAVED_LABEL, SNOOZED_COLUMN, keep_snoozed_column, with_new_columns, without_snoozed_column
from chrono_mail.ui.columnsetup import (
    FOLDER_TYPES, SORT_COLUMNS, STARTING, ColumnSetup, FolderType, fallback_sort, folder_type, read_setups, renamed_key, setup_key, setups_text,
)
from chrono_mail.ui.delegate import COLUMN_ROW_HEIGHTS
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelist import YES_NO_SORTS
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.prefs import InUse, Remembered

# --- Version ------------------------------------------------------------
__version__ = "messagecolumns.py 2.0.280"

# --- Version history ----------------------------------------------------
# v2.0.280: Columns per folder (B-16): every folder and view keeps its own columns, order, widths and sort, starting from the setup of its
#           kind of folder; Apply to all folders (header menu, Menu > Folder) gives the open setup to every folder of its kind and to those
#           made later; the sort falls back when its column goes; a renamed folder keeps its setup, a deleted one loses it; Sent is Saved in Drafts
# v1.9.264: R-17: the columns of the message list as a part of their own (out of ui/mainwindow.py): cards or columns, which columns
#           and in what order (one record, the list's own columns, D-179), their widths (column fit), the header's clicks, drags and menu,
#           the size of the list

LAYOUTS: tuple[tuple[str, str], ...] = (("columns", "Columns"), ("cards", "Cards"))


class MessageColumns(QObject):
    """How the message list is laid out: the owner of the layout, the columns, their widths and the sort of every folder and view (B-16),
    and the date style. The list pane owns the sort in use; this part gives it the open folder's sort and keeps what the person chooses."""

    def __init__(self, messages: MessageListPane, state: MailState, prefs_in_use: InUse, memory: Remembered, parent: QWidget) -> None:
        super().__init__(parent)
        self._messages: MessageListPane = messages
        self._state: MailState = state
        self._prefs: InUse = prefs_in_use
        self._memory: Remembered = memory
        self.columns_action: QAction | None = None  # the window's Message list…, at the bottom of the header's menu
        self.apply_action: QAction = QAction("Apply to all folders", parent)  # in the header's menu and in Menu > Folder
        self.apply_action.triggered.connect(self.apply_to_all)
        self._parent: QWidget = parent
        chosen = prefs_in_use.read()
        self.layout: str = chosen.list_layout if chosen.list_layout in ("columns", "cards") else "columns"
        self._folder_setups: dict[str, tuple[ColumnSetup, FolderType]] = read_setups(memory.folder_columns)
        self._type_setups: dict[FolderType, ColumnSetup] = {kind: setup for name, (setup, kind) in read_setups(memory.type_columns).items() if name == kind}
        self._take_over_earlier_setup()
        self._key: str | None = None  # the setup in use: the open folder's (None before a folder is open)
        self.folder_type: FolderType = "incoming"
        start = STARTING["incoming"]
        self.columns: tuple[str, ...] = start.columns
        self._column_layout: ColumnLayout | None = None  # None: the standard widths
        self._fitting: bool = False  # the program itself is setting the widths of the columns
        self.date_style: str = chosen.date_style
        self.layout_actions: dict[str, QAction] = {}
        view = messages.view
        messages.column_delegate.column_of = self.column_id
        view.column_of = self.column_id
        view.header().sectionClicked.connect(self.header_clicked)
        view.header().sectionMoved.connect(self._columns_moved)
        view.header().sectionResized.connect(self._column_resized)
        view.width_changed.connect(self.fit_columns)
        view.list_header.column_dragged.connect(self._column_line_dragged)
        view.header().customContextMenuRequested.connect(self.header_menu)
        messages.sort_changed.connect(self._sort_changed)
        messages.view_changed.connect(self.follow_view)  # a search in all folders has a setup of its own

    def fill_layout_menu(self, menu: QMenu) -> None:
        """View > Message list: Columns or Cards."""
        for value, label in LAYOUTS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.layout == value)
            action.triggered.connect(lambda checked=False, chosen=value: self.set_list_layout(chosen))
            self.layout_actions[value] = action

    def set_list_layout(self, layout: str) -> None:
        """View > Message list: the column layout or the card layout, kept in the settings as in the Message list window."""
        self.layout = layout if layout in ("columns", "cards") else "columns"
        self._prefs.keep(changed(self._prefs.read(), list_layout=self.layout))
        self.apply_list_layout()

    def take_in(self) -> None:
        """Settings OK changed the layout, the date style or the tag colours: in place, the rows and the selection stay."""
        chosen = self._prefs.read()
        self.layout = chosen.list_layout
        self.date_style = chosen.date_style
        self.apply_list_layout()

    # --- the setup of each folder (B-16) -----------------------------------------------------------------------

    def _take_over_earlier_setup(self) -> None:
        """Versions before 2.0.280 had one setup for every folder: what the person chose there becomes the setup incoming mail
        starts with, once; the outgoing kinds start with their own."""
        earlier = self._memory.earlier_setup()
        if earlier is None or "incoming" in self._type_setups:
            return
        columns, widths, sort = earlier
        start = STARTING["incoming"]
        if columns and not self._memory.column_added("contact_and_delete"):
            columns = with_new_columns(columns)
        chosen = without_snoozed_column(columns) if columns else start.columns
        wanted, oldest = sort if sort is not None else (start.sort, start.oldest_first)
        self._type_setups["incoming"] = ColumnSetup(chosen, fallback_sort(chosen, "date" if wanted == "snoozed" else wanted), oldest, widths)
        self._save_type_setups()

    def standard_setup(self, kind: FolderType | None = None) -> ColumnSetup:
        """The setup a folder of this kind (the open one's) starts with: the one Apply to all folders gave it, else the program's."""
        chosen = kind if kind is not None else self.folder_type
        return self._type_setups.get(chosen, STARTING[chosen])

    def follow_view(self) -> None:
        """Another folder or view is shown: its own setup (columns, widths, sort) is taken into use before the list asks for it;
        in the same one Snoozed until comes or goes."""
        view = self._state.view
        self.apply_action.setEnabled(view.account is not None)
        key, kind = setup_key(view), folder_type(view)
        if key == self._key and kind == self.folder_type:
            if self.layout == "columns" and self._messages.model.columns != self.list_columns():
                self.apply_list_layout()
            return
        self._key, self.folder_type = key, kind
        found = self._folder_setups.get(key)
        setup = found[0] if found is not None else self.standard_setup(kind)
        self.columns = setup.columns
        self._column_layout = read_layout(setup.widths)
        self._messages.model.set_column_labels({"sent": SAVED_LABEL} if kind == "drafts" else {})
        self._messages.use_sort(setup.sort, setup.oldest_first)
        self.apply_list_layout()

    def _current_setup(self) -> ColumnSetup:
        widths = self._column_layout.to_text() if self._column_layout is not None else ""
        return ColumnSetup(self.columns, self._messages.sort, self._messages.oldest_first, widths)

    def _keep(self) -> None:
        """The person changed the open folder's columns, widths or sort: kept for that folder only."""
        if self._key is None:
            return
        self._folder_setups[self._key] = (self._current_setup(), self.folder_type)
        self._memory.folder_columns = setups_text(self._folder_setups)

    def _save_type_setups(self) -> None:
        self._memory.type_columns = setups_text({kind: (setup, kind) for kind, setup in self._type_setups.items() if kind in FOLDER_TYPES})

    def apply_to_all(self) -> None:
        """Apply to all folders: the open setup for every folder and view of its kind, in every account, and for those made later.
        Their own setups go: they all use the kind's setup from now on."""
        if self._key is None:
            return
        kind = self.folder_type
        self._type_setups[kind] = self._current_setup()
        self._folder_setups = {key: found for key, found in self._folder_setups.items() if found[1] != kind}
        self._save_type_setups()
        self._memory.folder_columns = setups_text(self._folder_setups)

    def folder_renamed(self, account: str, old: str, new: str) -> None:
        """A folder (and what is below it) has a new name, here or on another computer: its setup goes along."""
        moved: dict[str, tuple[ColumnSetup, FolderType]] = {}
        for key, found in self._folder_setups.items():
            moved[renamed_key(key, account, old, new) or key] = found
        if self._key is not None:
            self._key = renamed_key(self._key, account, old, new) or self._key
        if moved != self._folder_setups:
            self._folder_setups = moved
            self._memory.folder_columns = setups_text(self._folder_setups)

    def folder_deleted(self, account: str, path: str) -> None:
        """A folder (and what is below it) is gone: its setup is forgotten."""
        kept = {key: found for key, found in self._folder_setups.items() if renamed_key(key, account, path, "") is None}
        if kept != self._folder_setups:
            self._folder_setups = kept
            self._memory.folder_columns = setups_text(self._folder_setups)

    def _follow_sort(self) -> None:
        """The column the folder is sorted by went: the sort moves to the first column it still shows (columnsetup.fallback_sort)."""
        sort = fallback_sort(self.columns, self._messages.sort)
        if sort != self._messages.sort:
            self._messages.set_sort(sort, self._messages.oldest_first)

    def apply_list_layout(self) -> None:
        cards = self.layout == "cards"
        for value, action in self.layout_actions.items():
            action.setChecked(value == self.layout)
        model, view = self._messages.model, self._messages.view
        model.date_style = self.date_style
        model.tag_text = self._prefs.read().tag_text_colour
        header = view.header()
        if cards:
            self._show_columns((CARD,))
            header.hide()
            view.setItemDelegate(self._messages.delegate)
            view.setUniformRowHeights(False)  # group headers are lower than cards; costs time on very large lists (D-095)
            self.fit_columns()
        else:
            self._show_columns(self.list_columns())
            header.show()
            view.setItemDelegate(self._messages.column_delegate)
            view.setUniformRowHeights(True)  # headers are as high as the rows: fast with any number of messages
            self.fit_columns()
            header.setSortIndicatorShown(True)
            self.show_sort_indicator()
        model.refresh_cells()

    def list_columns(self) -> tuple[str, ...]:
        """The columns of the list: Snoozed until (Send at) only in the Snoozed and the Scheduled folder."""
        return self.columns if self._state.in_timed() else without_snoozed_column(self.columns)

    def _show_columns(self, columns: tuple[str, ...]) -> None:
        """Show these columns: the list holds every column and moves the chosen ones to the front, the header hides the
        others; nothing is reset, so the selection stays (R-13)."""
        header = self._messages.view.header()
        self._fitting = True  # moving, hiding or showing a column resizes it: the window's doing, not a drag of the person
        try:
            self._messages.model.set_columns(columns)
            shown = len(self._messages.model.columns)
            for section in range(header.count()):
                if header.isSectionHidden(section) != (section >= shown):
                    header.setSectionHidden(section, section >= shown)
        finally:
            self._fitting = False

    def _sort_changed(self) -> None:
        """The person chose another sort: kept for the open folder."""
        self._keep()
        if self.layout == "columns":
            self.show_sort_indicator()

    def show_sort_indicator(self) -> None:
        header = self._messages.view.header()
        for position, column_id in enumerate(self._messages.model.columns):
            if column_id in SORT_COLUMNS.get(self._messages.effective_sort(), ()):
                header.setSortIndicator(position, Qt.SortOrder.AscendingOrder if self._messages.oldest_first else Qt.SortOrder.DescendingOrder)
                return
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def header_clicked(self, section: int) -> None:
        columns = self._messages.model.columns
        if self.layout != "columns" or not 0 <= section < len(columns):
            return
        sort = BY_ID[columns[section]].sort
        if sort is None:
            self.show_sort_indicator()  # Qt moved the arrow to the clicked column by itself: put it back
            return
        if sort == self._messages.effective_sort():
            self._messages.set_sort(sort, not self._messages.oldest_first)
        else:
            # text columns A to Z first, dates, sizes and conversations newest and largest first, the icon columns yes first, snoozed soonest first
            self._messages.set_sort(sort, sort not in ("date", "sent", "size", "conversation") and sort not in YES_NO_SORTS)
        self.show_sort_indicator()

    def _columns_moved(self, logical: int, old_visual: int, new_visual: int) -> None:
        """A column header was dragged to another place. The order of the columns has one record, the list's own columns:
        the header's move is undone and the column moves in the list instead, so a rebuild of the list (a column added, another
        folder) can never apply the drag a second time (D-179)."""
        model, header = self._messages.model, self._messages.view.header()
        if all(header.logicalIndex(visual) == visual for visual in range(header.count())):
            return  # the header shows the list's order: nothing is moved (the undo below arrives here too)
        header.moveSection(new_visual, old_visual)
        model.move_column(old_visual, min(new_visual, len(model.columns) - 1))  # never among the hidden columns
        columns = keep_snoozed_column(model.columns, self.columns)  # outside the Snoozed folder its column keeps its place
        if self.layout == "columns" and columns != self.columns:
            self.columns = columns
            self._keep()
        self.show_sort_indicator()  # the sorted column may have a new place
        self.fit_columns()  # the last column has no drag line at its right edge

    def _column_resized(self, section: int, old: int, new: int) -> None:
        """A column edge was dragged: the column changes, the text columns to its right take or give the difference (column fit)."""
        model, header = self._messages.model, self._messages.view.header()
        if self._fitting or self.layout != "columns" or not header.isVisible() or not 0 <= section < len(model.columns):
            return
        logical = self.visual_sections()
        order = [model.columns[index] for index in logical]
        icon = [BY_ID[column_id].symbol for column_id in order]
        position = logical.index(section)
        widths = [header.sectionSize(index) for index in logical]
        widths[position] = old
        self._apply_drag(logical, order, icon, drag(icon, widths, position, new))

    def _column_line_dragged(self, section: int, distance: int) -> None:
        """A column line is dragged with the mouse: all widths are worked out from the widths at the press and the whole distance moved."""
        model, view = self._messages.model, self._messages.view
        start = view.list_header.drag_start_sizes
        if self.layout != "columns" or len(start) != view.header().count() or not 0 <= section < len(model.columns):
            return
        logical = self.visual_sections()
        order = [model.columns[index] for index in logical]
        icon = [BY_ID[column_id].symbol for column_id in order]
        widths = [start[index] for index in logical]
        position = logical.index(section)
        self._apply_drag(logical, order, icon, drag(icon, widths, position, widths[position] + distance))

    def _apply_drag(self, logical: list[int], order: list[str], icon: list[bool], result: list[int]) -> None:
        self._set_column_widths(logical, result)
        self._column_layout = layout_from_pixels(order, icon, result)
        self._keep()

    def column_id(self, column: int) -> str:
        columns = self._messages.model.columns
        return columns[column] if 0 <= column < len(columns) else ""

    def visual_sections(self) -> list[int]:
        """The model columns shown, in the order they are shown (the hidden ones are left out)."""
        header = self._messages.view.header()
        return [section for section in (header.logicalIndex(visual) for visual in range(header.count())) if not header.isSectionHidden(section)]

    def fit_columns(self) -> None:
        """The columns fill exactly the width of the list: icon columns keep their pixels, text columns share the rest.
        In the card layout the one column is the width of the list."""
        model, view = self._messages.model, self._messages.view
        if model.columns == (CARD,):
            header = view.header()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(0, view.viewport().width())
            return
        if self.layout != "columns" or not model.columns:
            return
        logical = self.visual_sections()
        order = [model.columns[index] for index in logical if 0 <= index < len(model.columns)]
        if len(order) != len(logical):
            return
        icon = [BY_ID[column_id].symbol for column_id in order]
        if self._column_layout is None:
            self._column_layout = layout_from_pixels(order, icon, [BY_ID[column_id].width for column_id in order])
        widths = fit(order, icon, self._column_layout, [BY_ID[column_id].width for column_id in order], view.viewport().width())
        self._set_column_widths(logical, widths)

    def _set_column_widths(self, logical: list[int], widths: list[int]) -> None:
        header = self._messages.view.header()
        self._fitting = True
        try:
            for position, (section, width) in enumerate(zip(logical, widths)):
                last = position == len(logical) - 1
                header.setSectionResizeMode(section, QHeaderView.ResizeMode.Fixed if last else QHeaderView.ResizeMode.Interactive)  # no drag line at the right edge of the last column
                header.resizeSection(section, width)
        finally:
            self._fitting = False

    def header_menu(self, position: QPoint) -> None:
        menu = QMenu(self._parent)
        chosen = set(self.columns)
        for column_id, column in BY_ID.items():
            if column_id == SNOOZED_COLUMN and not self._state.in_timed():
                continue
            action = menu.addAction(column.label)
            action.setCheckable(True)
            action.setChecked(column_id in chosen)
            action.triggered.connect(lambda checked=False, wanted=column_id: self.toggle_column(wanted))
        menu.addSeparator()
        menu.addAction(self.apply_action)
        if self.columns_action is not None:
            menu.addAction(self.columns_action)
        menu.exec(self._messages.view.header().mapToGlobal(position))

    def toggle_column(self, column_id: str) -> None:
        columns = list(self.columns)
        if column_id in columns:
            if len(columns) > 1:
                columns.remove(column_id)
        else:
            columns.append(column_id)
        self.set_columns(tuple(columns))

    def set_columns(self, columns: tuple[str, ...]) -> None:
        """The open folder's columns (the header's menu, Settings > Message list): that folder only."""
        self.columns = columns or DEFAULT_COLUMNS
        self._keep()
        self.apply_list_layout()
        self._follow_sort()

    def choose(self, layout: str, columns: tuple[str, ...], date_style: str, density: str) -> None:
        """Message list… was confirmed: the layout, the date style and the row height for every folder, the columns for the open
        folder only (the reading pane is the reading pane's)."""
        self.layout, self.date_style = layout, date_style
        self._prefs.keep(changed(self._prefs.read(), list_layout=self.layout, date_style=self.date_style, list_density=density))
        self.apply_list_look()
        self.set_columns(keep_snoozed_column(columns, self.columns))  # Snoozed until is not offered there: it keeps its place

    def apply_list_look(self, factor: float | None = None) -> None:
        """The message list at its own size factor (a factor given here is tried out, not saved): its text is the
        system's size times the factor, and its rows, icons and columns follow (scale.lpx)."""
        app = QApplication.instance()
        system = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        scale.set_message_list(factor if factor is not None else prefs.saved_list(self._prefs.settings, system))
        font = QFont(QApplication.font())
        font.setPointSizeF(round(system * scale.message_list(), 2))
        view = self._messages.view
        view.setFont(font)
        view.header().setFont(font)
        view.header().setStyleSheet(f"QHeaderView::section {{ font-size: {font.pointSizeF()}pt; }}")  # the style rules of the header draw with their own font otherwise
        view.header().setMinimumSectionSize(scale.lpx(MINIMUM_ICON))  # icon columns may be narrower at a small size
        self._messages.column_delegate.row_height = COLUMN_ROW_HEIGHTS[self._prefs.read().list_density]
        view.doItemsLayout()
        self.fit_columns()
