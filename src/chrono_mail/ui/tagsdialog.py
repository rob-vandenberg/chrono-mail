"""Settings > Tags (Menu > Tags opens it): tabs Mails, Events, Contacts and Tasks; Add, Edit, Delete, Up and Down; a list
with the coloured tag, the name and the shortcut. The editor has Name, Colour (Change…), Shortcut and Use for. Changes are
made on a copy and sent as commands when the Settings window's OK is pressed; Cancel throws them away."""

import html

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from typing import override

from PySide6.QtCore import QEvent, QObject, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import TAG_COLOUR, TAG_LIST, TAG_RENAME, ColourParameters, RenameParameters, TagInfo
from chrono_mail.engine.commands_tags import TAG_CREATE, TAG_DELETE, TAG_EDIT, CreateParameters, DeleteParameters, EditParameters
from chrono_mail.core.colours import NEW_TAG, NEW_TAG_IN_TAGS_WINDOW
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.tagging import tag_icon
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "tagsdialog.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.271: R-19: commands gives typed steps; tag.list typed
# v1.1.193: Both column widths are set here (a stretched last column never shrank below 100 px: a drag past the edge gave a scroll range)
# v1.1.187: Name and Shortcut always fit the list (no horizontal scroll bar): half each at first, then the dragged share, remembered
# v1.1.186: The four tabs together are exactly as wide as the list under them (not over the buttons)
# v1.1.183: Add, Edit, Delete, Up and Down at the right of the list, as on the Accounts and Rules pages
# v1.1.182: The Tags window is a page of the Settings window (TagsPanel): no Save & Close; the Settings window's OK saves (D-165)
# v1.1.172: Colours from the colour file: a new tag's, a tag without one, the red of a problem (D-158)
# v0.5.135: Initial version: the Tags window and the tag editor

NAME_SHARE_KEY: str = "tags_name_share"  # the share of the Tags page list's width that the Name column has
MINIMUM_COLUMN: int = 50  # pixels: the smallest a column of that list may be dragged


def _share(value: object) -> float:
    try:
        share = float(str(value))
    except ValueError:
        return 0.5
    return min(max(share, 0.05), 0.95)


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


def rows_from(tags: tuple[TagInfo, ...]) -> list[TagRow]:
    rows: list[TagRow] = []
    for tag in tags:
        name, colour = tag.name, tag.colour or NEW_TAG
        shortcut, kinds = tag.shortcut, tuple(tag.kinds)
        carried = tag.mails + tag.contacts + tag.events + tag.tasks
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


class TagsPanel(QWidget):
    """The Tags page of the Settings window."""

    def __init__(self, backend: Backend, tags: tuple[TagInfo, ...], parent: QWidget | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self._backend = backend
        self.rows: list[TagRow] = rows_from(tags)
        self._first_order: list[str] = [row.name for row in self.rows]
        self.deleted: list[TagRow] = []
        layout = QVBoxLayout(self)  # as the Accounts and Rules pages: the list at the left, its buttons at the right
        body = QGridLayout()  # the tabs above the list and exactly as wide as it; the buttons at the right of the list
        self.tabs = QTabBar(self)
        self.tabs.setExpanding(True)  # the four tabs share the width of the list
        for _, label in TABS:
            self.tabs.addTab(label)
        body.addWidget(self.tabs, 0, 0)
        self.list = QTreeWidget(self)
        self.list.setHeaderLabels(["Name", "Shortcut"])
        self.list.setRootIsDecorated(False)
        # Name and Shortcut always fit the list, never a horizontal scroll bar: Name keeps its share of the width (half at
        # first, then where the person drags the line, remembered); Shortcut takes the rest.
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.list.header()
        header.setStretchLastSection(False)  # Qt never shrinks a stretched last column below its default size: both widths are set here
        header.setMinimumSectionSize(wpx(MINIMUM_COLUMN))
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # only the line between the two columns is dragged
        self._name_share: float = _share(settings.value(NAME_SHARE_KEY, 0.5) if settings is not None else 0.5)
        self._fitting: bool = False
        header.sectionResized.connect(self._column_dragged)
        self.list.viewport().installEventFilter(self)
        self._fit_columns()
        body.addWidget(self.list, 1, 0)
        body.setColumnStretch(0, 1)
        body.setRowStretch(1, 1)
        side = QVBoxLayout()
        self.add_button = QPushButton("Add", self)
        self.edit_button = QPushButton("Edit", self)
        self.delete_button = QPushButton("Delete", self)
        self.up_button = QPushButton("Up", self)
        self.down_button = QPushButton("Down", self)
        for button in (self.add_button, self.edit_button, self.delete_button, self.up_button, self.down_button):
            side.addWidget(button)
        side.addStretch(1)
        body.addLayout(side, 1, 1)
        layout.addLayout(body, 1)
        self.status = QLabel("", self)
        layout.addWidget(self.status)
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit_tag)
        self.delete_button.clicked.connect(self.delete)
        self.up_button.clicked.connect(lambda: self.move_tag(-1))
        self.down_button.clicked.connect(lambda: self.move_tag(1))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.list.currentItemChanged.connect(self._current_changed)
        self.refresh()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_columns()  # the list got wider or narrower: Name keeps its share
        return super().eventFilter(watched, event)

    def _fit_columns(self) -> None:
        """Name at its share of the width, never so wide that Shortcut drops below its smallest size."""
        width = self.list.viewport().width()
        name = min(max(round(width * self._name_share), wpx(MINIMUM_COLUMN)), max(wpx(MINIMUM_COLUMN), width - wpx(MINIMUM_COLUMN)))
        self._fitting = True
        self.list.setColumnWidth(0, name)
        self.list.setColumnWidth(1, max(wpx(MINIMUM_COLUMN), width - name))  # Shortcut takes exactly the rest
        self._fitting = False

    def _column_dragged(self, column: int, old: int, new: int) -> None:
        """The person dragged the line between Name and Shortcut: Name keeps that share (remembered), within the list."""
        if self._fitting or column != 0:
            return
        width = self.list.viewport().width()
        if width <= 2 * wpx(MINIMUM_COLUMN):
            return
        self._name_share = _share(min(new, width - wpx(MINIMUM_COLUMN)) / width)
        if self._settings is not None:
            self._settings.setValue(NAME_SHARE_KEY, self._name_share)
        self._fit_columns()  # Shortcut takes the rest; a drag past the room there is goes back to the most that fits

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
            self, "Delete tag", f"Delete the tag “{row.name}”? It is taken away from the {row.carried_by:,} messages and items that carry it. They stay."
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

    # --- saving (the Settings window's OK) -----------------------------------------------------

    def commands(self) -> list[AnyStep[object]]:
        """The commands that make the tags as this window shows them: deletes, renames, new tags, colours, details, order."""
        steps: list[AnyStep[object]] = [Step(TAG_DELETE, DeleteParameters(row.original)) for row in self.deleted if row.original is not None]
        for row in self.rows:
            if row.original is not None and row.original != row.name:
                steps.append(Step(TAG_RENAME, RenameParameters(row.original, row.name)))
        for row in self.rows:
            if row.original is None:
                steps.append(Step(TAG_CREATE, CreateParameters(row.name, row.colour)))
            elif row.colour != row.original_values[0]:
                steps.append(Step(TAG_COLOUR, ColourParameters(row.name, row.colour)))
        for position, row in enumerate(self.rows):
            steps.append(Step(TAG_EDIT, EditParameters(row.name, row.shortcut, tuple(row.kinds), "", position)))
        return steps

    def changed(self) -> bool:
        """Whether anything differs from the tags as they were when the page opened."""
        if self.deleted or [row.name for row in self.rows] != self._first_order:
            return True
        return any(row.original is None or row.original != row.name or (row.colour, row.shortcut, row.kinds) != row.original_values for row in self.rows)

    def save(self, saved: Callable[[], None], failed: Callable[[], None]) -> None:
        """Send the commands one after the other; saved when all are done, failed (with the error on the page) at the
        first one that fails: the steps after it are not sent. Nothing is sent when nothing changed."""
        if not self.changed():
            saved()
            return
        steps = self.commands()
        self.status.setText("Saving…")

        def next_step(result: object = None) -> None:
            if not steps:
                self.status.setText("")
                saved()
                return
            steps.pop(0).start(self._backend, next_step, stopped, owner=self)

        def stopped(error: ChronoMailError) -> None:
            message = f"<span style='{red_style()}'>Not saved: {html.escape(str(error))}. The page shows the tags as they are now.</span>"
            self.status.setText(message)

            def reloaded(result: tuple[TagInfo, ...]) -> None:  # what was saved before the error is the truth now, so OK never sends it twice
                self.rows = rows_from(result)
                self._first_order = [row.name for row in self.rows]
                self.deleted = []
                self.refresh()
                self.status.setText(message)

            self._backend.ask(TAG_LIST, NoParameters(), reloaded, lambda reload_error: None, owner=self)
            failed()

        next_step()
