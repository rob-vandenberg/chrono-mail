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

from chrono_mail.ui.columns import BY_ID, COLUMNS, DEFAULT_COLUMNS

# --- Version ------------------------------------------------------------
__version__ = "columnsdialog.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial column editor

LAYOUTS: tuple[tuple[str, str], ...] = (("columns", "Columns (a table, one line per message)"), ("cards", "Cards (sender, subject and a preview)"))
READING_PANES: tuple[tuple[str, str], ...] = (("right", "At the right of the list"), ("bottom", "Below the list"), ("off", "Off (open a message in its own window)"))
DATE_STYLES: tuple[tuple[str, str], ...] = (("short", "Short (time today, weekday, date)"), ("full", "Date and time"), ("iso", "2026-10-11 09:30"))


@dataclass(frozen=True, slots=True)
class ListChoice:
    layout: str
    columns: tuple[str, ...]
    reading_pane: str
    date_style: str


class ColumnsDialog(QDialog):
    """Chooses how the message list looks: columns or cards, which columns in which order, and where the reading pane is."""

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
        form.addRow("Layout", self.layout_box)
        form.addRow("Reading pane", self.pane_box)
        form.addRow("Dates", self.date_box)
        layout.addLayout(form)
        layout.addWidget(QLabel("Columns (tick the ones to show; the order is the order from left to right):", self))
        body = QHBoxLayout()
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
        layout.addLayout(body, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.reset_button.clicked.connect(lambda: self._fill(DEFAULT_COLUMNS))
        self.list.currentRowChanged.connect(self._selection_changed)
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._fill(current.columns)
        self._layout_changed()

    def _fill(self, shown: tuple[str, ...]) -> None:
        self.list.clear()
        ordered = [BY_ID[column_id] for column_id in shown if column_id in BY_ID] + [column for column in COLUMNS if column.column_id not in shown]
        for column in ordered:
            item = QListWidgetItem(column.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if column.column_id in shown else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, column.column_id)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self._selection_changed()

    def _layout_changed(self) -> None:
        columns = str(self.layout_box.currentData()) == "columns"
        self.list.setEnabled(columns)
        for button in (self.up_button, self.down_button, self.reset_button):
            button.setEnabled(columns)
        self._selection_changed()

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

    def choice(self) -> ListChoice:
        """What was asked for. At least one column is always kept (the standard ones when none is ticked)."""
        shown = tuple(
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.list.count())
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )
        return ListChoice(str(self.layout_box.currentData()), shown or DEFAULT_COLUMNS, str(self.pane_box.currentData()), str(self.date_box.currentData()))
