from functools import partial

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QMenu, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.ui.colours import theme
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "tagbox.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: Initial Tags box of the appointment window: each tag a rounded label in its colour, a radio button chooses the tag that colours the appointment (D-329)

LONGEST_LABEL: int = 120  # the widest a tag's name is shown in the box, at the size factor of the windows
COLOUR_TIP: str = "This tag gives the appointment its colour"
CHOOSE_TIP: str = "Click to let this tag give the appointment its colour"


class TagBox(QScrollArea):
    """The tags of an appointment, one per line: a radio button and the tag as a rounded label in its colour. The checked radio button marks
    the colour tag (D-329): the first tag added gets it and keeps it; a click on another one moves it; when it is removed, the first tag left
    gets it. tags() gives the colour tag first, as the appointment keeps them."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._colours = TagColourBook()
        self._names: list[str] = []  # in the order they were added
        self._colour_tag: str = ""
        self._enabled: bool = True
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._inner = QWidget()
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
        self._show()

    def tags(self) -> list[str]:
        """The tags with the colour tag first (its place in the appointment says which one it is)."""
        return [self._colour_tag, *(name for name in self._names if name != self._colour_tag)] if self._colour_tag else []

    def names(self) -> list[str]:
        return list(self._names)

    def colour_tag(self) -> str:
        return self._colour_tag

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
        text = TagColourBook.text_colour(colour)
        row = QWidget(self._inner)
        row.setObjectName("tagRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(wpx(6))
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
        label = QLabel(name, row)
        shown = label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, wpx(LONGEST_LABEL))
        if shown != name:  # a long name ends in … and shows whole on hover; the box never scrolls sideways
            label.setText(shown)
            label.setToolTip(name)
        label.setStyleSheet(f"background: {colour.name()}; color: {text.name()}; border-radius: {wpx(9)}px; padding: {wpx(1)}px {wpx(8)}px;")
        label.setEnabled(True)  # its colour stays readable while the window is read only
        layout.addWidget(radio)
        layout.addWidget(label)
        layout.addStretch(1)
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(partial(self._menu, name, row))
        return row

    def _menu(self, name: str, row: QWidget, position: QPoint) -> None:
        menu = QMenu(self)
        remove = menu.addAction("Remove tag", lambda: self.remove(name))
        remove.setEnabled(self._enabled)
        menu.exec(row.mapToGlobal(position))

