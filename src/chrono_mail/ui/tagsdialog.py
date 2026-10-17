"""Menu > Tags: the reference client's Tags window. Tabs Mails, Events, Contacts and Tasks; Save & Close, Add, Edit,
Delete, Up and Down; a list with the coloured tag, the name and the shortcut. The editor has Name, Colour (Change…),
Shortcut and Use for. Changes are made on a copy and sent as commands by Save & Close."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.config import TAG_KINDS, TAG_SHORTCUTS
from chrono_mail.core import ChronoMailError
from chrono_mail.core.colours import NEW_TAG, NEW_TAG_IN_TAGS_WINDOW
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.tagging import tag_icon

# --- Version ------------------------------------------------------------
__version__ = "tagsdialog.py 1.1.172"

# --- Version history ----------------------------------------------------
# v1.1.172: Colours from the colour file: a new tag's, a tag without one, the red of a problem (D-158)
# v0.5.135: Initial version: the Tags window and the tag editor

TABS: tuple[tuple[str, str], ...] = (("mail", "Mails"), ("events", "Events"), ("contacts", "Contacts"), ("tasks", "Tasks"))
KIND_NAMES: dict[str, str] = {"mail": "Mails", "events": "Events", "tasks": "Tasks", "contacts": "Contacts"}


@dataclass
class TagRow:
    name: str
    colour: str
    shortcut: str = ""
    kinds: tuple[str, ...] = TAG_KINDS
    original: str | None = None  # the name it had when the window opened; None for a new tag
    carried_by: int = 0  # mail, contacts, events and tasks that carry it (for the question before deleting)
    original_values: tuple[str, str, tuple[str, ...]] = field(default=("", "", ()))  # colour, shortcut, kinds when the window opened


def rows_from(tags: tuple[object, ...]) -> list[TagRow]:
    rows: list[TagRow] = []
    for tag in tags:
        name, colour = str(getattr(tag, "name")), str(getattr(tag, "colour")) or NEW_TAG
        shortcut, kinds = str(getattr(tag, "shortcut", "")), tuple(getattr(tag, "kinds", TAG_KINDS))
        carried = sum(int(getattr(tag, kind, 0)) for kind in ("mails", "contacts", "events", "tasks"))
        rows.append(TagRow(name, colour, shortcut, kinds, name, carried, (colour, shortcut, kinds)))
    return rows


class TagEditor(QDialog):
    """Name, Colour with Change…, Shortcut and Use for."""

    def __init__(self, row: TagRow, taken: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tags")
        self._colour = row.colour
        self._taken = taken
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(row.name, self)
        form.addRow("Name:", self.name)
        colour_row = QHBoxLayout()
        self.swatch = QLabel(self)
        self.swatch.setFixedSize(20, 20)
        self.change = QPushButton("Change…", self)
        self.change.clicked.connect(self._choose_colour)
        colour_row.addWidget(self.swatch)
        colour_row.addWidget(self.change)
        colour_row.addSpacing(24)
        colour_row.addWidget(QLabel("Shortcut:", self))
        self.shortcut = QComboBox(self)
        self.shortcut.addItem("", "")
        for key in TAG_SHORTCUTS:
            self.shortcut.addItem(key, key)
        self.shortcut.setCurrentIndex(max(0, self.shortcut.findData(row.shortcut)))
        colour_row.addWidget(self.shortcut, 1)
        form.addRow("Color:", colour_row)
        layout.addLayout(form)
        group = QGroupBox("Use for", self)
        group_layout = QVBoxLayout(group)
        self.kinds: dict[str, QCheckBox] = {}
        for kind in ("mail", "events", "tasks", "contacts"):
            box = QCheckBox(KIND_NAMES[kind], group)
            box.setChecked(kind in row.kinds)
            group_layout.addWidget(box)
            self.kinds[kind] = box
        layout.addWidget(group)
        self.problem = QLabel("", self)
        self.problem.setStyleSheet(red_style())
        layout.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._show_colour()

    def _show_colour(self) -> None:
        self.swatch.setStyleSheet(f"background-color: {self._colour}; border: 1px solid palette(mid)")

    def _choose_colour(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._colour), self, "Color")
        if chosen.isValid():
            self._colour = chosen.name()
            self._show_colour()

    def _check(self) -> None:
        name = " ".join(self.name.text().split())
        if not name or len(name) > 100:
            self.problem.setText("A tag needs a name of up to 100 characters.")
        elif name.casefold() in self._taken:
            self.problem.setText("There is a tag of that name already.")
        elif not any(box.isChecked() for box in self.kinds.values()):
            self.problem.setText("Use the tag for at least one kind of item.")
        else:
            self.accept()

    def result_row(self, row: TagRow) -> TagRow:
        return replace(
            row, name=" ".join(self.name.text().split()), colour=self._colour, shortcut=str(self.shortcut.currentData() or ""),
            kinds=tuple(kind for kind in TAG_KINDS if self.kinds[kind].isChecked()),
        )


class TagsDialog(QDialog):
    def __init__(self, backend: Backend, tags: tuple[object, ...], on_saved: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._on_saved = on_saved
        self.rows: list[TagRow] = rows_from(tags)
        self.deleted: list[TagRow] = []
        self.setWindowTitle("Tags")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.save_button = QPushButton("Save && Close", self)
        self.save_button.setDefault(True)
        self.add_button = QPushButton("Add", self)
        self.edit_button = QPushButton("Edit", self)
        self.delete_button = QPushButton("Delete", self)
        self.up_button = QPushButton("Up", self)
        self.down_button = QPushButton("Down", self)
        for button in (self.save_button, self.add_button, self.edit_button, self.delete_button, self.up_button, self.down_button):
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.tabs = QTabBar(self)
        for _, label in TABS:
            self.tabs.addTab(label)
        layout.addWidget(self.tabs)
        self.list = QTreeWidget(self)
        self.list.setHeaderLabels(["Name", "Shortcut"])
        self.list.setRootIsDecorated(False)
        self.list.setColumnWidth(0, 380)
        layout.addWidget(self.list, 1)
        self.status = QLabel("", self)
        layout.addWidget(self.status)
        self.save_button.clicked.connect(self.save)
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit_tag)
        self.delete_button.clicked.connect(self.delete)
        self.up_button.clicked.connect(lambda: self.move_tag(-1))
        self.down_button.clicked.connect(lambda: self.move_tag(1))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.list.currentItemChanged.connect(self._current_changed)
        self.refresh()

    def _tab_changed(self, index: int) -> None:
        self.refresh()

    def _double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        self.edit_tag()

    def _current_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        self._enable()

    # --- the list -----------------------------------------------------------------------------

    def kind(self) -> str:
        return TABS[max(0, self.tabs.currentIndex())][0]

    def shown(self) -> list[TagRow]:
        return [row for row in self.rows if self.kind() in row.kinds]

    def refresh(self, keep: str | None = None) -> None:
        self.list.clear()
        for row in self.shown():
            item = QTreeWidgetItem([row.name, row.shortcut])
            item.setIcon(0, tag_icon(QColor(row.colour)))
            self.list.addTopLevelItem(item)
            if keep is not None and row.name.casefold() == keep.casefold():
                self.list.setCurrentItem(item)
        self._enable()

    def _current(self) -> TagRow | None:
        item = self.list.currentItem()
        if item is None:
            return None
        name = item.text(0)
        return next((row for row in self.rows if row.name == name), None)

    def _enable(self) -> None:
        chosen = self._current() is not None
        for button in (self.edit_button, self.delete_button, self.up_button, self.down_button):
            button.setEnabled(chosen)

    # --- changes on the copy ------------------------------------------------------------------

    def _taken(self, but: TagRow | None) -> set[str]:
        return {row.name.casefold() for row in self.rows if row is not but}

    def add(self) -> None:
        editor = TagEditor(TagRow("", NEW_TAG_IN_TAGS_WINDOW, kinds=(self.kind(),) if self.kind() != "mail" else TAG_KINDS), self._taken(None), self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            row = editor.result_row(TagRow("", NEW_TAG_IN_TAGS_WINDOW))
            self._take_shortcut(row)
            self.rows.append(row)
            self.refresh(row.name)

    def edit_tag(self) -> None:
        row = self._current()
        if row is None:
            return
        editor = TagEditor(row, self._taken(row), self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            changed = editor.result_row(row)
            self._take_shortcut(changed)
            self.rows[self.rows.index(row)] = changed
            self.refresh(changed.name)

    def _take_shortcut(self, row: TagRow) -> None:
        """A shortcut belongs to one tag: the tag that had it loses it."""
        if row.shortcut:
            for other in self.rows:
                if other is not row and other.shortcut == row.shortcut:
                    other.shortcut = ""

    def delete(self) -> None:
        row = self._current()
        if row is None:
            return
        if row.carried_by and QMessageBox.question(
            self, "Delete tag", f"Delete the tag “{row.name}”? It is taken away from the {row.carried_by} messages and items that carry it. They stay."
        ) != QMessageBox.StandardButton.Yes:
            return
        self.rows.remove(row)
        if row.original is not None:
            self.deleted.append(row)
        self.refresh()

    def move_tag(self, step: int) -> None:
        row = self._current()
        if row is None:
            return
        shown = self.shown()
        position = shown.index(row)
        if not 0 <= position + step < len(shown):
            return
        other = shown[position + step]
        first, second = self.rows.index(row), self.rows.index(other)
        self.rows[first], self.rows[second] = other, row
        self.refresh(row.name)

    # --- Save & Close -------------------------------------------------------------------------

    def commands(self) -> list[tuple[str, dict[str, object]]]:
        """The commands that make the tags as this window shows them: deletes, renames, new tags, colours, details, order."""
        steps: list[tuple[str, dict[str, object]]] = [("tag.delete", {"name": row.original}) for row in self.deleted if row.original is not None]
        for row in self.rows:
            if row.original is not None and row.original != row.name:
                steps.append(("tag.rename", {"name": row.original, "new_name": row.name}))
        for row in self.rows:
            if row.original is None:
                steps.append(("tag.create", {"name": row.name, "colour": row.colour}))
            elif row.colour != row.original_values[0]:
                steps.append(("tag.colour", {"name": row.name, "colour": row.colour}))
        for position, row in enumerate(self.rows):
            steps.append(("tag.edit", {"name": row.name, "shortcut": row.shortcut, "use_for": list(row.kinds), "position": position}))
        return steps

    def save(self) -> None:
        steps = self.commands()
        self.save_button.setEnabled(False)
        self.status.setText("Saving…")

        def next_step(result: object = None) -> None:
            if not steps:
                self._on_saved()
                self.accept()
                return
            command, values = steps.pop(0)
            self._backend.call(command, values, next_step, failed, owner=self)

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.status.setText(f"Not saved: {error}")
            self._on_saved()  # what was saved before the error is shown

        next_step()
