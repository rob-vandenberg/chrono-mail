from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QPushButton, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, SNOOZED_COLUMN, people_group, people_shown
from chrono_mail.ui.columnsetup import KIND_NAMES
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "columnsdialog.py 4.2.436"

# --- Version history ----------------------------------------------------
# v4.2.436: Standard columns is replaced by Apply to all, Default and Reset (the open kind of folder's stored setup, its setup after a
#           fresh install), each with a hover text naming the kind; the picker tells every change at once (a tick, Move up, Move down,
#           the three buttons) and the Message list window its Layout, Reading pane, Dates and Row height, so the main window shows
#           them before OK (D-104, D-228); the list follows the folder open in the main window (follow)
# v4.2.421: one From row and one To row (D-291): each stands for the way the folder shows it, the header menu's choice; while not shown
#           its standard way (From: Name only, To: Name and email)
# v4.0.377: the column picker (also on Settings > Message list) and the Message list window are built from their Qt Designer forms
#           (ui/forms/column_picker.ui, message_list.ui, D-276); the window's sizes follow the size factor
# v2.0.280: Standard columns are those of the kind of folder that is open (B-16)
# v0.2.100: Snoozed until is not in the column list (it belongs to the Snoozed folder)
# v0.2.97: Row height (compact, normal, roomy) in the Message list window
# v0.2.96: The column list is a part of its own (ColumnPicker), also shown on the Message list page of Settings
# v0.0.41: Initial column editor

LAYOUTS: tuple[tuple[str, str], ...] = (("columns", "Columns (a table, one line per message)"), ("cards", "Cards (sender, subject and a preview)"))
READING_PANES: tuple[tuple[str, str], ...] = (("right", "At the right of the list"), ("bottom", "Below the list"), ("off", "Off (open a message in its own window)"))
DATE_STYLES: tuple[tuple[str, str], ...] = (("short", "Short (time today, weekday, date)"), ("full", "Date and time"), ("iso", "2026-10-11 09:30"))
DENSITY_LABELS: tuple[tuple[str, str], ...] = (("compact", "Compact"), ("normal", "Normal"), ("roomy", "Roomy"))


@dataclass(frozen=True, slots=True)
class ListChoice:
    layout: str
    columns: tuple[str, ...]
    reading_pane: str
    date_style: str
    density: str


@dataclass(frozen=True, slots=True)
class PickerSetup:
    """What the column picker needs besides the columns shown, all of the open kind of folder: its stored columns (Default), its
    columns after a fresh install (Reset), its folders as the hover texts name them, and whether Apply to all can be used (not
    while the list shows nothing at all)."""

    default: tuple[str, ...] = DEFAULT_COLUMNS
    factory: tuple[str, ...] = DEFAULT_COLUMNS
    kind_name: str = KIND_NAMES["incoming"]
    can_apply: bool = True


class ColumnPicker(QWidget):
    """The columns of the message list: tick the ones to show and put them in order (Move up, Move down), or take the open kind of
    folder's stored setup (Default), its setup after a fresh install (Reset), or make this one the stored setup (Apply to all). Every
    change is told at once, so the main window shows it before OK (D-104, D-228)."""

    columns_changed = Signal()  # a tick, Move up or Move down
    default_chosen = Signal()  # Default: the open folder takes its kind's stored setup (columns, order, widths, sort)
    reset_chosen = Signal()  # Reset: the open folder takes its kind's setup after a fresh install
    apply_to_all_chosen = Signal()  # Apply to all: the open folder's setup becomes its kind's stored setup

    def __init__(self, shown: tuple[str, ...], parent: QWidget | None = None, setup: PickerSetup | None = None) -> None:
        super().__init__(parent)
        self._default: tuple[str, ...] = DEFAULT_COLUMNS
        self._factory: tuple[str, ...] = DEFAULT_COLUMNS
        self._can_apply: bool = True
        self._filling: bool = False  # the list is being filled: its ticks are not the person's
        form = forms.fill(self, "column_picker")  # the list and its buttons (D-276)
        self.list: QListWidget = part(form, QListWidget, "list")
        self.up_button: QPushButton = part(form, QPushButton, "moveUp")
        self.down_button: QPushButton = part(form, QPushButton, "moveDown")
        self.apply_button: QPushButton = part(form, QPushButton, "applyToAll")
        self.default_button: QPushButton = part(form, QPushButton, "defaultColumns")
        self.reset_button: QPushButton = part(form, QPushButton, "resetColumns")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.apply_button.clicked.connect(self._apply_to_all)
        self.default_button.clicked.connect(self._load_default)
        self.reset_button.clicked.connect(self._load_factory)
        self.list.currentRowChanged.connect(self._selection_changed)
        self.list.itemChanged.connect(self._ticked)
        self.follow(shown, setup if setup is not None else PickerSetup())

    def follow(self, shown: tuple[str, ...], setup: PickerSetup) -> None:
        """Show the columns and choices of the folder open in the main window (also when another one opens while this list is shown)."""
        self._default, self._factory, self._can_apply = setup.default, setup.factory, setup.can_apply
        self.apply_button.setToolTip(f"Use this column configuration for all {setup.kind_name}.")
        self.default_button.setToolTip(f"Load the default configuration for {setup.kind_name}.")
        self.reset_button.setToolTip(f"Restore the factory default configuration for {setup.kind_name}.")
        self.fill(shown)
        self.set_active(self.list.isEnabled())

    def _apply_to_all(self) -> None:
        self._default = self.columns()  # Default gives this from now on
        self.apply_to_all_chosen.emit()

    def _load_default(self) -> None:
        self.fill(self._default)
        self.default_chosen.emit()

    def _load_factory(self) -> None:
        self.fill(self._factory)
        self.reset_chosen.emit()

    def _ticked(self) -> None:
        if not self._filling:
            self.columns_changed.emit()

    def fill(self, shown: tuple[str, ...]) -> None:
        self._filling = True
        try:
            self._fill(shown)
        finally:
            self._filling = False

    def _fill(self, shown: tuple[str, ...]) -> None:
        self.list.clear()
        ordered = [BY_ID[column_id] for column_id in shown if column_id in BY_ID] + [column for column in COLUMNS if column.column_id not in shown]
        ordered = [column for column in ordered if column.column_id != SNOOZED_COLUMN]  # chosen in the Snoozed folder itself
        ordered = [column for column in ordered if _one_row(column.column_id, shown)]  # From and To once each, the way they show (D-291)
        for column in ordered:
            item = QListWidgetItem(column.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if column.column_id in shown else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, column.column_id)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self._selection_changed()

    def set_active(self, active: bool) -> None:
        """Greyed out while the card layout is chosen (it has no columns)."""
        self.list.setEnabled(active)
        self.apply_button.setEnabled(active and self._can_apply)
        self.default_button.setEnabled(active)
        self.reset_button.setEnabled(active)
        self._selection_changed()

    def columns(self) -> tuple[str, ...]:
        """The ticked columns from left to right; the stored ones when none is ticked (at least one column is always kept)."""
        shown = tuple(
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.list.count())
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )
        return shown or self._default

    def _selection_changed(self) -> None:
        row, active = self.list.currentRow(), self.list.isEnabled()
        self.up_button.setEnabled(active and row > 0)
        self.down_button.setEnabled(active and 0 <= row < self.list.count() - 1)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        if not 0 <= row + step < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + step, item)
        self.list.setCurrentRow(row + step)
        self.columns_changed.emit()


def _one_row(column_id: str, shown: tuple[str, ...]) -> bool:
    """Whether a column has a row of its own: every column but the ways of From and To other than the one shown (its standard way,
    the group's own column, while it is not shown)."""
    group = people_group(column_id)
    if group is None:
        return True
    return column_id == (people_shown(shown, group) or group)


class ColumnsDialog(QDialog):
    """Chooses how the message list looks: columns or cards, which columns in which order, where the reading pane is, the dates and the row height.
    Every change shows in the main window at once; OK keeps it, Cancel puts it back (D-104)."""

    list_settings_tried = Signal(object)  # Layout, Reading pane, Dates or Row height changed: the choice as it is now (a ListChoice)

    def __init__(self, current: ListChoice, parent: QWidget | None = None, setup: PickerSetup | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "message_list", {"picker": lambda parent: ColumnPicker(current.columns, parent, setup)})  # D-276
        self.layout_box: QComboBox = part(form, QComboBox, "layoutBox")
        for value, label in LAYOUTS:
            self.layout_box.addItem(label, value)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(current.layout)))
        self.pane_box: QComboBox = part(form, QComboBox, "paneBox")
        for value, label in READING_PANES:
            self.pane_box.addItem(label, value)
        self.pane_box.setCurrentIndex(max(0, self.pane_box.findData(current.reading_pane)))
        self.date_box: QComboBox = part(form, QComboBox, "dateBox")
        for value, label in DATE_STYLES:
            self.date_box.addItem(label, value)
        self.date_box.setCurrentIndex(max(0, self.date_box.findData(current.date_style)))
        self.density_box: QComboBox = part(form, QComboBox, "densityBox")
        for value, label in DENSITY_LABELS:
            self.density_box.addItem(label, value)
        self.density_box.setCurrentIndex(max(0, self.density_box.findData(current.density)))
        self.picker: ColumnPicker = part(form, ColumnPicker, "picker")
        self.list = self.picker.list  # the tests and the older code know the list, and its buttons, by these names
        self.up_button, self.down_button = self.picker.up_button, self.picker.down_button
        box = part(form, QDialogButtonBox, "buttons")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.setMinimumHeight(wpx(460))
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._layout_changed()
        for box_of_choices in (self.layout_box, self.pane_box, self.date_box, self.density_box):
            box_of_choices.currentIndexChanged.connect(lambda: self.list_settings_tried.emit(self.choice()))

    def _layout_changed(self) -> None:
        self.picker.set_active(str(self.layout_box.currentData()) == "columns")

    def choice(self) -> ListChoice:
        """What was asked for. At least one column is always kept (the standard ones when none is ticked)."""
        return ListChoice(
            str(self.layout_box.currentData()), self.picker.columns(), str(self.pane_box.currentData()), str(self.date_box.currentData()), str(self.density_box.currentData()),
        )
