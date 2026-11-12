"""Settings > Tags (Menu > Tags opens it): one list of the tags, each edited on its own line (D-332): the name in the tag's colour (a click on
the selected one or F2 renames it in place), a colour square that opens the colour picker, Show in Mail, Calendar, Contacts and Tasks, a shortcut
from a drop-down, and ✕ to delete it; Add tag adds a line; a line is dragged to its place. Changes are made on a copy and sent as commands when the
Settings window's OK is pressed; Cancel throws them away."""

import html

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from typing import override

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, QRect, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QDropEvent, QKeyEvent, QIcon, QPainter, QPixmap, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from chrono_mail.config import TAG_KINDS, TAG_SHORTCUTS
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import TAG_COLOUR, TAG_LIST, TAG_RENAME, ColourParameters, RenameParameters, TagInfo
from chrono_mail.engine.commands_tags import TAG_CREATE, TAG_DELETE, TAG_EDIT, CreateParameters, DeleteParameters, EditParameters
from chrono_mail.core.colours import NEW_TAG, NEW_TAG_IN_TAGS_WINDOW, SUGGESTED_EVENT_COLOURS
from chrono_mail.ui import forms
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.forms import part
from chrono_mail.ui.colours import red_style
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "tagsdialog.py 4.4.588"

# --- Version history ----------------------------------------------------
# v4.4.588: renamed: the renames the engine took at OK, for the Tags blocks to follow (D-334)
# v4.4.581: The colour first, then the name (the owner). One list of tags without tabs, edited in the list (the owner, D-332): the name in its colour (a click on the selected one or F2 renames it
#           in place), a colour square, Show in Mail, Calendar, Contacts and Tasks, a shortcut from a drop-down (Not assigned, or one no other tag has),
#           ✕ deletes, Add tag adds a line, a line is dragged to its place; the Add/Edit window and Up and Down are gone
# v4.4.580: The tabs are the same tab page as Settings > Sidebar: its frame and margins, the list and its buttons inside it (the owner, D-331)
# v4.4.578: The tabs Mail, Calendar, Contacts and Tasks as wide as their names from the left, as Settings > Sidebar (the owner, D-331)
# v4.0.377: the tag editor is built from its Qt Designer form (ui/forms/tag_editor.ui, D-276); its sizes follow the size factor
# v3.6.373: the Tags page is built from the form settings_tags.ui (parts, texts, layout; D-276); nothing visible changed
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

COLOUR, NAME, SHORTCUT, DELETE = 0, 1, 6, 7  # the columns of the list, the colour first (the owner); Show in is columns 2 to 5
SHOW_IN: tuple[tuple[str, int], ...] = (("mail", 2), ("events", 3), ("contacts", 4), ("tasks", 5))  # Mail, Calendar, Contacts and Tasks
NOT_ASSIGNED: str = "Not assigned"  # the shortcut drop-down's first choice: no shortcut
LONGEST_NAME: int = 100  # characters
PILL_PADDING: int = 8  # left and right of a tag's name, inside its colour
KEY_ROLE: int = Qt.ItemDataRole.UserRole + 1  # a line's own number: which tag it is, also after a drag


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


class TagList(QTreeWidget):
    """The list of tags: a line is dragged to its place among the others, never into another line."""

    reordered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        """F2 edits the name of the selected line, whichever of its cells has the keyboard (the others hold buttons and boxes)."""
        line = self.currentItem()
        if event.key() == Qt.Key.Key_F2 and line is not None:
            self.editItem(line, NAME)
            event.accept()
            return
        super().keyPressEvent(event)

    @override
    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt's name
        super().dropEvent(event)
        self.reordered.emit()


class NameDelegate(QStyledItemDelegate):
    """A tag's name drawn on a rounded label in its colour, as in the appointment window; edited in place as plain text."""

    def __init__(self, panel: "TagsPanel") -> None:
        super().__init__(panel)
        self._panel = panel

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        row = self._panel.row_at(index)
        plain = QStyleOptionViewItem(option)
        self.initStyleOption(plain, index)
        setattr(plain, "text", "")  # setattr: PySide's stubs give the fields of a style option no type
        widget = self._panel.list
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, plain, painter, widget)
        if row is None:
            return
        cell: object = getattr(option, "rect")  # getattr: as above
        if not isinstance(cell, QRect):
            return
        area = QRectF(cell)
        metrics = widget.fontMetrics()
        width = min(area.width() - 8, metrics.horizontalAdvance(row.name) + 2 * wpx(PILL_PADDING))
        height = metrics.height() + 2
        shape = QRectF(area.left() + 4, area.top() + (area.height() - height) / 2, width, height)
        colour = QColor(row.colour)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(shape, height / 2, height / 2)
        painter.setPen(TagColourBook.text_colour(colour))
        room = int(width) - 2 * wpx(PILL_PADDING)
        text = row.name if metrics.horizontalAdvance(row.name) <= room + 1 else metrics.elidedText(row.name, Qt.TextElideMode.ElideRight, room)  # cut only what does not fit
        painter.drawText(shape, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    @override
    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        row = self._panel.row_at(index)
        if isinstance(editor, QLineEdit) and row is not None:
            editor.setText(row.name)
            editor.setMaxLength(LONGEST_NAME)

    @override
    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex | QPersistentModelIndex) -> None:
        row = self._panel.row_at(index)
        if isinstance(editor, QLineEdit) and row is not None:
            self._panel.rename(row, editor.text())


class TagsPanel(QWidget):
    """The Tags page of the Settings window."""

    def __init__(self, backend: Backend, tags: tuple[TagInfo, ...], parent: QWidget | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self._backend = backend
        self.rows: list[TagRow] = rows_from(tags)
        self._first_order: list[str] = [row.name for row in self.rows]
        self.deleted: list[TagRow] = []
        self.renamed: dict[str, str] = {}  # the renames the engine took at OK: each first name and the name it has now (the Tags blocks follow them, D-334)
        form = forms.fill(self, "settings_tags", {"list": TagList})  # D-276
        self.list = part(form, TagList, "list")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)  # a line is dragged to its place (the owner)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.EditKeyPressed)  # as an appointment's title (D-324)
        self.list.setItemDelegateForColumn(NAME, NameDelegate(self))
        self.list.reordered.connect(self._reordered)
        header = self.list.header()
        header.setStretchLastSection(False)
        for column in range(self.list.columnCount()):  # the name takes the room left; the others are as wide as what they hold
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch if column == NAME else QHeaderView.ResizeMode.ResizeToContents)
            if column != NAME:
                self.list.headerItem().setTextAlignment(column, Qt.AlignmentFlag.AlignCenter)
        self.show_in = part(form, QLabel, "showIn")  # over the four Show in columns
        self.show_in.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.sectionResized.connect(self._place_show_in)
        header.geometriesChanged.connect(self._place_show_in)
        self.add_button = part(form, QPushButton, "addButton")
        self.add_button.clicked.connect(self.add)
        self.status = part(form, QLabel, "status")
        self.refresh()

    # --- the list -----------------------------------------------------------------------------

    def row_at(self, index: QModelIndex | QPersistentModelIndex) -> "TagRow | None":
        model = index.model()
        key = model.index(index.row(), NAME).data(KEY_ROLE) if model is not None else None
        return self.rows[key] if isinstance(key, int) and 0 <= key < len(self.rows) else None

    def line_of(self, row: "TagRow") -> QTreeWidgetItem | None:
        position = self.rows.index(row) if row in self.rows else -1
        return self.list.topLevelItem(position) if position >= 0 else None

    def refresh(self, keep: "TagRow | None" = None) -> None:
        """Every tag on its own line, in the order of the list; keep: the line selected afterwards."""
        self.list.clear()
        for position, row in enumerate(self.rows):
            item = QTreeWidgetItem(["", "", "", "", "", "", "", ""])
            item.setData(NAME, KEY_ROLE, position)
            item.setToolTip(NAME, row.name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled)  # never a drop into it
            self.list.addTopLevelItem(item)
            self.list.setItemWidget(item, COLOUR, self._colour_button(row))
            for kind, column in SHOW_IN:
                self.list.setItemWidget(item, column, self._tick(row, kind))
            self.list.setItemWidget(item, SHORTCUT, self._shortcut_box(row))
            self.list.setItemWidget(item, DELETE, self._delete_button(row))
            if row is keep:
                self.list.setCurrentItem(item)
        self._place_show_in()

    def _colour_button(self, row: "TagRow") -> QWidget:
        button = QToolButton()
        swatch = QPixmap(wpx(16), wpx(16))
        swatch.fill(QColor(row.colour))
        button.setIcon(QIcon(swatch))
        button.setAutoRaise(True)
        button.setToolTip("Change the colour")
        button.setAccessibleName(f"Colour of {row.name}")
        button.clicked.connect(partial(self.choose_colour, row))
        return _centred(button)

    def _tick(self, row: "TagRow", kind: str) -> QWidget:
        box = QCheckBox()
        box.setChecked(kind in row.kinds)
        only = row.kinds == (kind,)
        box.setEnabled(not only)  # the last section it is shown in stays: greyed, with why (OR-15)
        box.setToolTip("A tag is shown in at least one section" if only else "")
        box.setAccessibleName(f"Show {row.name} in {dict(SHOW_IN_WORDS)[kind]}")
        box.toggled.connect(partial(self._ticked, row, kind))
        return _centred(box)

    def _shortcut_box(self, row: "TagRow") -> QWidget:
        box = QComboBox()
        box.setAccessibleName(f"Shortcut of {row.name}")
        box.addItem(NOT_ASSIGNED, "")
        holders = {other.shortcut: other.name for other in self.rows if other is not row and other.shortcut}
        model = box.model()
        for key in TAG_SHORTCUTS:
            holder = holders.get(key)
            box.addItem(f"{key} — {holder}" if holder else key, key)
            if holder and isinstance(model, QStandardItemModel):
                found = model.item(box.count() - 1)
                if found is not None:
                    found.setEnabled(False)  # no two tags share a shortcut (the owner): the one another tag has is greyed, with its name
        box.setCurrentIndex(max(0, box.findData(row.shortcut)))
        box.activated.connect(partial(self._shortcut_chosen, row, box))
        return box

    def _delete_button(self, row: "TagRow") -> QWidget:
        button = QToolButton()
        button.setText("✕")
        button.setToolTip("Delete this tag")
        button.setAccessibleName(f"Delete {row.name}")
        button.clicked.connect(partial(self.delete, row))
        return _centred(button)

    def _place_show_in(self, *ignored: object) -> None:
        """Show in stands over its four columns."""
        header = self.list.header()
        left = self.list.frameWidth() + header.sectionViewportPosition(SHOW_IN[0][1])
        right = header.sectionViewportPosition(SHOW_IN[-1][1]) + header.sectionSize(SHOW_IN[-1][1]) + self.list.frameWidth()
        self.show_in.setContentsMargins(max(0, left), 0, max(0, self.list.width() - right), 0)

    # --- changes on the copy ------------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.status.setText(f"<span style='{red_style()}'>{html.escape(text)}</span>" if text else "")

    def rename(self, row: "TagRow", text: str) -> None:
        """The name typed in place: an empty or unchanged one changes nothing; one another tag has, or a too long one, is refused, with why."""
        name = " ".join(text.split())
        if not name or name == row.name:
            return
        if len(name) > LONGEST_NAME:
            self._say(f"A tag's name has at most {LONGEST_NAME} characters.")
        elif any(other is not row and other.name.casefold() == name.casefold() for other in self.rows):
            self._say(f"There is a tag named “{name}” already.")
        else:
            self._say("")
            row.name = name
            line = self.line_of(row)
            if line is not None:
                line.setToolTip(NAME, name)
            self.refresh(row)

    def choose_colour(self, row: "TagRow") -> None:
        chosen = QColorDialog.getColor(QColor(row.colour), self, f"Colour of {row.name}")
        if chosen.isValid() and chosen.name() != row.colour:
            row.colour = chosen.name()
            self.refresh(row)

    def _ticked(self, row: "TagRow", kind: str, checked: bool) -> None:
        wanted = tuple(each for each in TAG_KINDS if (each == kind and checked) or (each != kind and each in row.kinds))
        if wanted:
            row.kinds = wanted
        self.refresh(row)

    def _shortcut_chosen(self, row: "TagRow", box: QComboBox, position: int) -> None:
        row.shortcut = str(box.itemData(position) or "")
        self.refresh(row)

    def add(self) -> None:
        """Add tag: a line at the end, shown everywhere, in a colour no tag has yet, its name ready to type."""
        taken = {row.name.casefold() for row in self.rows}
        name, number = "New tag", 2
        while name.casefold() in taken:
            name, number = f"New tag {number}", number + 1
        used = {row.colour.casefold() for row in self.rows}
        colour = next((value for _, _, value in SUGGESTED_EVENT_COLOURS if value.casefold() not in used), NEW_TAG_IN_TAGS_WINDOW)
        row = TagRow(name, colour, "", TAG_KINDS)
        self.rows.append(row)
        self.refresh(row)
        line = self.line_of(row)
        if line is not None:
            self.list.editItem(line, NAME)

    def delete(self, row: "TagRow") -> None:
        if row not in self.rows:
            return
        if row.carried_by and QMessageBox.question(
            self, "Delete tag", f"Delete the tag “{row.name}”? It is taken away from the {row.carried_by:,} messages and items that carry it. They stay."
        ) != QMessageBox.StandardButton.Yes:
            return
        position = self.rows.index(row)
        self.rows.remove(row)
        if row.original is not None:
            self.deleted.append(row)
        self.refresh(self.rows[min(position, len(self.rows) - 1)] if self.rows else None)  # the line that took its place stays in view

    def move_tag(self, row: "TagRow", step: int) -> None:
        """One line up (-1) or down (1)."""
        position = self.rows.index(row)
        if 0 <= position + step < len(self.rows):
            self.rows[position], self.rows[position + step] = self.rows[position + step], row
            self.refresh(row)

    def _reordered(self) -> None:
        """A line was dragged to another place: the tags in the order the lines have now."""
        order: list[TagRow] = []
        for position in range(self.list.topLevelItemCount()):
            line = self.list.topLevelItem(position)
            key = line.data(NAME, KEY_ROLE) if line is not None else None
            if isinstance(key, int) and 0 <= key < len(self.rows) and self.rows[key] not in order:
                order.append(self.rows[key])
        order.extend(row for row in self.rows if row not in order)
        moved = self.list.currentItem()
        chosen_key = moved.data(NAME, KEY_ROLE) if moved is not None else None
        chosen = self.rows[chosen_key] if isinstance(chosen_key, int) and 0 <= chosen_key < len(self.rows) else None
        self.rows = order
        self.refresh(chosen)

    # --- saving (the Settings window's OK) -----------------------------------------------------

    def commands(self) -> list[AnyStep[object]]:
        """The commands that make the tags as this window shows them: deletes, renames, new tags, colours, details, order."""
        steps: list[AnyStep[object]] = [Step(TAG_DELETE, DeleteParameters(row.original)) for row in self.deleted if row.original is not None]
        for old, new in self._rename_order():
            steps.append(Step(TAG_RENAME, RenameParameters(old, new)))
        for row in self.rows:
            if row.original is None:
                steps.append(Step(TAG_CREATE, CreateParameters(row.name, row.colour)))
            elif row.colour != row.original_values[0]:
                steps.append(Step(TAG_COLOUR, ColourParameters(row.name, row.colour)))
        for position, row in enumerate(self.rows):
            steps.append(Step(TAG_EDIT, EditParameters(row.name, row.shortcut, tuple(row.kinds), "", position)))
        return steps

    def _rename_order(self) -> list[tuple[str, str]]:
        """The renames in an order that never gives a tag a name another tag still has (B to C before A to B; a rename into one's own name in
        other capitals is free); names that are swapped go through a passing name, so no two tags are joined."""
        pending = [(row.original, row.name) for row in self.rows if row.original is not None and row.original != row.name]
        taken = {row.name.casefold() for row in self.rows} | {old.casefold() for old, _ in pending}
        ordered: list[tuple[str, str]] = []
        while pending:
            held = [old.casefold() for old, _ in pending]
            ready = [pair for position, pair in enumerate(pending) if pair[1].casefold() not in held[:position] + held[position + 1:]]
            if ready:
                ordered.extend(ready)
                pending = [pair for pair in pending if pair not in ready]
                continue
            old, new = pending[0]  # names that are swapped: the first goes through a passing name
            passing, number = f"{old} (renaming)", 2
            while passing.casefold() in taken:
                passing, number = f"{old} (renaming {number})", number + 1
            taken.add(passing.casefold())
            ordered.append((old, passing))
            pending[0] = (passing, new)
        return ordered

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
        sent: list[AnyStep[object]] = []

        def next_step(result: object = None) -> None:
            if sent:
                self._note_rename(sent.pop())
            if not steps:
                self.status.setText("")
                saved()
                return
            sent.append(steps[0])
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

    def _note_rename(self, step: AnyStep[object]) -> None:
        """A step the engine took: a rename is noted under the tag's first name (a passing name of swapped names joins its chain)."""
        if not isinstance(step, Step) or step.command is not TAG_RENAME or not isinstance(step.parameters, RenameParameters):
            return
        old, new = step.parameters.name, step.parameters.new_name
        first = next((start for start, now in self.renamed.items() if now == old), old)
        self.renamed[first] = new


SHOW_IN_WORDS: tuple[tuple[str, str], ...] = (("mail", "Mail"), ("events", "Calendar"), ("contacts", "Contacts"), ("tasks", "Tasks"))


def _centred(widget: QWidget) -> QWidget:
    """A widget in the middle of its cell."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(1)
    layout.addWidget(widget)
    layout.addStretch(1)
    return holder
