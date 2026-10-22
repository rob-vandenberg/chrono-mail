from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS, SNOOZED_COLUMN

# --- Version ------------------------------------------------------------
__version__ = "columnsdialog.py 2.0.280"

# --- Version history ----------------------------------------------------
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


COLUMNS_TEXT = "Columns (tick the ones to show; the order is the order from left to right):"


class ColumnPicker(QWidget):
    """The columns of the message list: tick the ones to show and put them in order (Move up, Move down, Standard columns)."""

    def __init__(self, shown: tuple[str, ...], parent: QWidget | None = None, standard: tuple[str, ...] = DEFAULT_COLUMNS) -> None:
        super().__init__(parent)
        self._standard: tuple[str, ...] = standard  # the columns the open kind of folder starts with
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget(self)
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        self.up_button = QPushButton("Move up", self)
        self.down_button = QPushButton("Move down", self)
        self.reset_button = QPushButton("Standard columns", self)
        for button in (self.up_button, self.down_button, self.reset_button):
            side.addWidget(button)
        side.addStretch(1)
        body.addLayout(side)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.reset_button.clicked.connect(lambda: self.fill(self._standard))
        self.list.currentRowChanged.connect(self._selection_changed)
        self.fill(shown)

    def fill(self, shown: tuple[str, ...]) -> None:
        self.list.clear()
        ordered = [BY_ID[column_id] for column_id in shown if column_id in BY_ID] + [column for column in COLUMNS if column.column_id not in shown]
        ordered = [column for column in ordered if column.column_id != SNOOZED_COLUMN]  # chosen in the Snoozed folder itself
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


class ColumnsDialog(QDialog):
    """Chooses how the message list looks: columns or cards, which columns in which order, where the reading pane is, the dates and the row height."""

    def __init__(self, current: ListChoice, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Message list")
        self.setMinimumSize(520, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.layout_box = QComboBox(self)
        for value, label in LAYOUTS:
            self.layout_box.addItem(label, value)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(current.layout)))
        self.pane_box = QComboBox(self)
        for value, label in READING_PANES:
            self.pane_box.addItem(label, value)
        self.pane_box.setCurrentIndex(max(0, self.pane_box.findData(current.reading_pane)))
        self.date_box = QComboBox(self)
        for value, label in DATE_STYLES:
            self.date_box.addItem(label, value)
        self.date_box.setCurrentIndex(max(0, self.date_box.findData(current.date_style)))
        self.density_box = QComboBox(self)
        for value, label in DENSITY_LABELS:
            self.density_box.addItem(label, value)
        self.density_box.setCurrentIndex(max(0, self.density_box.findData(current.density)))
        form.addRow("Layout", self.layout_box)
        form.addRow("Reading pane", self.pane_box)
        form.addRow("Dates", self.date_box)
        form.addRow("Row height", self.density_box)
        layout.addLayout(form)
        layout.addWidget(QLabel(COLUMNS_TEXT, self))
        self.picker = ColumnPicker(current.columns, self, current.standard)
        self.list = self.picker.list  # the tests and the older code know the list, and its buttons, by these names
        self.up_button, self.down_button, self.reset_button = self.picker.up_button, self.picker.down_button, self.picker.reset_button
        layout.addWidget(self.picker, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._layout_changed()

    def _layout_changed(self) -> None:
        self.picker.set_active(str(self.layout_box.currentData()) == "columns")

    def choice(self) -> ListChoice:
        """What was asked for. At least one column is always kept (the standard ones when none is ticked)."""
        return ListChoice(
            str(self.layout_box.currentData()), self.picker.columns(), str(self.pane_box.currentData()), str(self.date_box.currentData()), str(self.density_box.currentData()),
        )
