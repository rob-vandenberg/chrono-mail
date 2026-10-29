from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QPushButton, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, SNOOZED_COLUMN, people_group, people_shown
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "columnsdialog.py 4.2.421"

# --- Version history ----------------------------------------------------
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
    standard: tuple[str, ...] = DEFAULT_COLUMNS  # what Standard columns gives: the columns the open kind of folder starts with


class ColumnPicker(QWidget):
    """The columns of the message list: tick the ones to show and put them in order (Move up, Move down, Standard columns)."""

    def __init__(self, shown: tuple[str, ...], parent: QWidget | None = None, standard: tuple[str, ...] = DEFAULT_COLUMNS) -> None:
        super().__init__(parent)
        self._standard: tuple[str, ...] = standard  # the columns the open kind of folder starts with
        form = forms.fill(self, "column_picker")  # the list and its buttons (D-276)
        self.list: QListWidget = part(form, QListWidget, "list")
        self.up_button: QPushButton = part(form, QPushButton, "moveUp")
        self.down_button: QPushButton = part(form, QPushButton, "moveDown")
        self.reset_button: QPushButton = part(form, QPushButton, "standardColumns")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.reset_button.clicked.connect(lambda: self.fill(self._standard))
        self.list.currentRowChanged.connect(self._selection_changed)
        self.fill(shown)

    def fill(self, shown: tuple[str, ...]) -> None:
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
        self.reset_button.setEnabled(active)
        self._selection_changed()

    def columns(self) -> tuple[str, ...]:
        """The ticked columns from left to right; the standard ones when none is ticked (at least one column is always kept)."""
        shown = tuple(
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.list.count())
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )
        return shown or self._standard

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


def _one_row(column_id: str, shown: tuple[str, ...]) -> bool:
    """Whether a column has a row of its own: every column but the ways of From and To other than the one shown (its standard way,
    the group's own column, while it is not shown)."""
    group = people_group(column_id)
    if group is None:
        return True
    return column_id == (people_shown(shown, group) or group)


class ColumnsDialog(QDialog):
    """Chooses how the message list looks: columns or cards, which columns in which order, where the reading pane is, the dates and the row height."""

    def __init__(self, current: ListChoice, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "message_list", {"picker": lambda parent: ColumnPicker(current.columns, parent, current.standard)})  # D-276
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
        self.up_button, self.down_button, self.reset_button = self.picker.up_button, self.picker.down_button, self.picker.reset_button
        box = part(form, QDialogButtonBox, "buttons")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.setMinimumHeight(wpx(460))
        forms.smallest(self, 520)  # its texts are never cut off, also at the largest size
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._layout_changed()

    def _layout_changed(self) -> None:
        self.picker.set_active(str(self.layout_box.currentData()) == "columns")

    def choice(self) -> ListChoice:
        """What was asked for. At least one column is always kept (the standard ones when none is ticked)."""
        return ListChoice(
            str(self.layout_box.currentData()), self.picker.columns(), str(self.pane_box.currentData()), str(self.date_box.currentData()), str(self.density_box.currentData()),
        )
