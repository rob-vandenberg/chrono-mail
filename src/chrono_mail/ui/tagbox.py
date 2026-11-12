from functools import partial
from typing import override

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.ui.colours import theme
from chrono_mail.ui.deletekey import DeleteKey
from chrono_mail.ui.paletteeditor import circle_icon
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "tagbox.py 4.4.581"

# --- Version history ----------------------------------------------------
# v4.4.581: The box without radio buttons for contacts and tasks (a tag colours neither); TagEditing: Add tag…, New tag… and Remove tag of a
#           window, the same in every window that has the box (the owner, D-332)
# v4.4.576: The coloured part of a tag 2 pixels lower: the pixel above and below the name moved outside the colour (the owner)
# v4.4.574: A click on a tag's line selects it (highlighted), Delete removes it; the box has the dark field colour of the attendee list (D-329)
# v4.4.573: Initial Tags box of the appointment window: each tag a rounded label in its colour, a radio button chooses the tag that colours the appointment (D-329)

PILL_PADDING: int = 8  # left and right of a tag's name, inside its colour
PILL_GAP: int = 1  # above and below a tag's colour, inside its line: the colour 2 pixels lower than the line (the owner)
LONGEST_LABEL: int = 120  # the widest a tag's name is shown in the box, at the size factor of the windows
COLOUR_TIP: str = "This tag gives the appointment its colour"
CHOOSE_TIP: str = "Click to let this tag give the appointment its colour"


class TagRow(QWidget):
    """One tag's line: a click on it (not on its radio button) selects it."""

    pressed = Signal()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit()
        super().mousePressEvent(event)


class TagPill(QLabel):
    """A tag's name on a rounded shape in its colour, drawn here: the shape is 2 pixels lower than the line, a pixel free above and below it
    (the owner), with the name in white or black, whichever reads better."""

    def __init__(self, text: str, colour: QColor, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._colour = QColor(colour)

    @override
    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 2 * wpx(PILL_PADDING), metrics.height() + 2 * wpx(PILL_GAP))

    @override
    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    @override
    def paintEvent(self, arg__1: QPaintEvent, /) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QRectF(self.rect()).adjusted(0, wpx(PILL_GAP), 0, -wpx(PILL_GAP))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        painter.drawRoundedRect(shape, shape.height() / 2, shape.height() / 2)
        painter.setPen(TagColourBook.text_colour(self._colour))
        painter.setFont(self.font())
        painter.drawText(shape, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class TagBox(QScrollArea):
    """The tags of an appointment, one per line: a radio button and the tag as a rounded label in its colour. The checked radio button marks
    the colour tag (D-329): the first tag added gets it and keeps it; a click on another one moves it; when it is removed, the first tag left
    gets it. tags() gives the colour tag first, as the appointment keeps them."""

    changed = Signal()
    selection_changed = Signal()  # another tag's line was selected, or none

    def __init__(self, parent: QWidget | None = None, colour_choice: bool = True) -> None:
        super().__init__(parent)
        self._colour_choice = colour_choice  # the radio buttons: only where a tag gives the item its colour (appointments, D-329)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._colours = TagColourBook()
        self._names: list[str] = []  # in the order they were added
        self._colour_tag: str = ""
        self._enabled: bool = True
        self._selected: str = ""  # the tag whose line is selected ('' for none)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._delete_key = DeleteKey(self, self.remove_selected)  # Delete removes the selected tag while the box has the keyboard (OR-16)
        self.viewport().setBackgroundRole(QPalette.ColorRole.Base)  # the dark field colour of the attendee list, never the window's grey
        self.viewport().setAutoFillBackground(True)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._inner = QWidget()
        self._inner.setAutoFillBackground(False)
        self._inner.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._inner.setObjectName("tagLines")
        self._inner.setStyleSheet("QWidget#tagLines { background: transparent; }")  # only the holder of the lines: the field shows through
        self._rows = QVBoxLayout(self._inner)
        self._rows.setContentsMargins(wpx(6), wpx(4), wpx(6), wpx(4))
        self._rows.setSpacing(wpx(3))
        self._rows.addStretch(1)
        self.setWidget(self._inner)
        self.setMinimumHeight(wpx(60))

    def set_colours(self, colours: TagColourBook) -> None:
        self._colours = colours
        self._show()

    def set_tags(self, tags: list[str]) -> None:
        """The appointment's tags as it keeps them: its colour tag first."""
        self._names = list(dict.fromkeys(tags))
        self._colour_tag = self._names[0] if self._names else ""
        if self._selected not in self._names:
            self._selected = ""
            self.selection_changed.emit()
        self._show()

    def tags(self) -> list[str]:
        """The tags with the colour tag first (its place in the appointment says which one it is)."""
        return [self._colour_tag, *(name for name in self._names if name != self._colour_tag)] if self._colour_tag else []

    def names(self) -> list[str]:
        return list(self._names)

    def colour_tag(self) -> str:
        return self._colour_tag

    def selected(self) -> str:
        """The tag whose line is selected ('' for none)."""
        return self._selected

    def select(self, name: str) -> None:
        if name != self._selected and (not name or name in self._names):
            self._selected = name
            self._show()
            self.selection_changed.emit()

    def remove_selected(self) -> None:
        """Remove tag, or Delete: the selected tag goes."""
        if self._selected and self._enabled:
            self.remove(self._selected)

    def add(self, name: str) -> None:
        if any(name.casefold() == known.casefold() for known in self._names):
            return
        self._names.append(name)
        if not self._colour_tag:
            self._colour_tag = name  # the first tag added gives the colour, and keeps it
        self._show()
        self.changed.emit()

    def remove(self, name: str) -> None:
        if name not in self._names:
            return
        self._names.remove(name)
        if name == self._selected:
            self._selected = ""
            self.selection_changed.emit()
        if name == self._colour_tag:
            self._colour_tag = self._names[0] if self._names else ""  # the first tag left gives the colour now
        self._show()
        self.changed.emit()

    def choose(self, name: str) -> None:
        """This tag gives the appointment its colour (a click on its radio button)."""
        if name in self._names and name != self._colour_tag:
            self._colour_tag = name
            self._show()
            self.changed.emit()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._show()

    def _show(self) -> None:
        for button in self._group.buttons():
            self._group.removeButton(button)
        while self._rows.count() > 1:
            taken = self._rows.takeAt(0)
            widget = taken.widget() if taken is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)  # gone at once, never left lying under the new lines until Qt deletes it
                widget.deleteLater()
        for position, name in enumerate(self._names):
            self._rows.insertWidget(position, self._row(name))

    def _row(self, name: str) -> QWidget:
        colour = self._colours.colour(name)
        row = TagRow(self._inner)
        row.setObjectName("tagRow")
        if name == self._selected:  # highlighted like a selected line elsewhere
            row.setAutoFillBackground(True)
            row.setBackgroundRole(QPalette.ColorRole.Highlight)
        row.pressed.connect(partial(self._row_pressed, name))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(wpx(6))
        label = TagPill(name, colour, row)
        shown = label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, wpx(LONGEST_LABEL))
        if shown != name:  # a long name ends in … and shows whole on hover; the box never scrolls sideways
            label.setText(shown)
            label.setToolTip(name)
        label.setEnabled(True)  # its colour stays readable while the window is read only
        if not self._colour_choice:
            layout.addWidget(label)
            layout.addStretch(1)
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(partial(self._menu, name, row))
            return row
        radio = QRadioButton(row)
        radio.setChecked(name == self._colour_tag)
        radio.setEnabled(self._enabled)
        radio.setToolTip(COLOUR_TIP if name == self._colour_tag else CHOOSE_TIP)
        radio.setAccessibleName(f"{name} gives the colour")
        size, edge = wpx(12), max(1, wpx(2))
        radio.setStyleSheet(
            f"QRadioButton::indicator {{ width: {size}px; height: {size}px; border-radius: {size // 2 + edge}px; border: {edge}px solid {theme().task_grey}; }}"
            f"QRadioButton::indicator:checked {{ background: {colour.name()}; border-color: {colour.name()}; }}"
        )
        radio.clicked.connect(lambda _checked=False, chosen=name: self.choose(chosen))
        self._group.addButton(radio)
        layout.addWidget(radio)
        layout.addWidget(label)
        layout.addStretch(1)
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(partial(self._menu, name, row))
        return row

    def _row_pressed(self, name: str) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.select(name)

    def _menu(self, name: str, row: QWidget, position: QPoint) -> None:
        menu = QMenu(self)
        remove = menu.addAction("Remove tag", lambda: self.remove(name))
        remove.setEnabled(self._enabled)
        menu.exec(row.mapToGlobal(position))



class TagEditing(QObject):
    """The tags part of a window, the same in each window that has it (D-332): the box, Add tag… with the tags shown in this section that the
    item does not have yet (a click adds one and the list closes) and New tag…, Remove tag for the tag selected in the box (greyed until one is)."""

    def __init__(self, box: TagBox, add_button: QPushButton, remove_button: QPushButton, known: list[str], colours: TagColourBook, parent: QWidget) -> None:
        super().__init__(parent)
        self.box = box
        self._add_button = add_button
        self._remove_button = remove_button
        self.known = list(known)  # the tags shown in this section (Show in), in the person's order
        self._colours = colours
        self._window = parent
        self._reading = False
        self.menu = QMenu(add_button)
        add_button.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._fill)
        remove_button.clicked.connect(box.remove_selected)
        box.selection_changed.connect(self._show_remove)
        box.set_colours(colours)
        self._show_remove()

    def set_reading(self, reading: bool) -> None:
        """Only to read: nothing to add or remove."""
        self._reading = reading
        self._add_button.setEnabled(not reading)
        self.box.set_enabled(not reading)
        self._show_remove()

    def _fill(self) -> None:
        self.menu.clear()
        chosen = {name.casefold() for name in self.box.names()}
        for name in (name for name in self.known if name.casefold() not in chosen):
            self.menu.addAction(circle_icon(self._colours.colour(name).name()), name, partial(self.box.add, name))
        if not self.menu.isEmpty():
            self.menu.addSeparator()
        self.menu.addAction("New tag…", self._new_tag)

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self._window, "New tag", "Name of the new tag:")
        if ok and " ".join(name.split()):
            self.box.add(" ".join(name.split()))

    def _show_remove(self) -> None:
        """Remove tag works on the selected tag: greyed, with why, until one is selected (OR-15)."""
        usable = bool(self.box.selected()) and not self._reading
        self._remove_button.setEnabled(usable)
        self._remove_button.setToolTip("" if usable or self._reading else "Click a tag in the list first")
