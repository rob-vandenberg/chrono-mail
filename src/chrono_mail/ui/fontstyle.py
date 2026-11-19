"""Font Style, the A of the message editor's bar (D-363): the font and its size, Text Color and Background Color, each with a grid, Custom
color… and, for the background, No color. It acts on the selected text, or on what is typed next; the current values are ticked.

The colours of the grids are in core/colours.py (OR-9); a colour chosen is the person's and is never themed."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import QColorDialog, QComboBox, QFontComboBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QTextEdit, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from chrono_mail.core.colours import TEXT_COLOUR_ROWS, text_on_accent

# --- Version ------------------------------------------------------------
__version__ = "fontstyle.py 4.6.640"

# --- Version history ----------------------------------------------------
# v4.6.640: Initial version: the font, its size, Text Color and Background Color of the selected text (D-363)

SIZES: tuple[int, ...] = (8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72)
SMALLEST: int = 1  # a size typed in the box is used only from this many points
LARGEST: int = 400  # up to this many
SWATCH: int = 18  # the side of one colour in a grid, in pixels
TICK: str = "✓"


def size_of(text: str) -> int | None:
    """The size typed or chosen in the size box, in points; None for anything that is not a whole number in range."""
    try:
        points = int(text.strip())
    except ValueError:
        return None
    return points if SMALLEST <= points <= LARGEST else None


class ColourGrid(QWidget):
    """One grid of colours with Custom color… under it (and No color for the background); the colour in use is ticked."""

    def __init__(self, title: str, with_none: bool, chosen: Callable[[QColor | None], None], parent: QWidget | None = None,
                 before_picker: Callable[[], object] | None = None) -> None:
        super().__init__(parent)
        self._chosen = chosen
        self._before_picker = before_picker  # the menu closes before the colour picker opens
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel(title, self))
        grid = QGridLayout()
        grid.setSpacing(2)
        self.swatches: dict[str, QToolButton] = {}
        for row, colours in enumerate(TEXT_COLOUR_ROWS):
            for column, colour in enumerate(colours):
                swatch = QToolButton(self)
                swatch.setFixedSize(SWATCH, SWATCH)
                swatch.setToolTip(colour)
                swatch.setStyleSheet(f"QToolButton {{ background: {colour}; color: {text_on_accent(colour)}; border: 1px solid {colour}; padding: 0; }}")
                swatch.clicked.connect(lambda checked=False, picked=colour: self._chosen(QColor(picked)))
                grid.addWidget(swatch, row, column)
                self.swatches[colour] = swatch
        layout.addLayout(grid)
        self.custom = QToolButton(self)
        self.custom.setText("Custom color…")
        self.custom.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.custom.clicked.connect(self._ask_colour)
        layout.addWidget(self.custom)
        self.none: QToolButton | None = None
        if with_none:
            self.none = QToolButton(self)
            self.none.setText("No color")
            self.none.clicked.connect(lambda: self._chosen(None))
            layout.addWidget(self.none)
        self._current: QColor | None = None

    def show_current(self, colour: QColor | None) -> None:
        """Tick the colour in use: its swatch, else Custom color…; No color when there is none."""
        self._current = colour
        name = colour.name() if colour is not None else ""
        for value, swatch in self.swatches.items():
            swatch.setText(TICK if value == name else "")
        in_grid = name in self.swatches
        self.custom.setText(f"{TICK} Custom color…" if colour is not None and not in_grid else "Custom color…")
        if self.none is not None:
            self.none.setText(f"{TICK} No color" if colour is None else "No color")

    def _ask_colour(self) -> None:
        if self._before_picker is not None:
            self._before_picker()
        picked = QColorDialog.getColor(self._current if self._current is not None else QColor(TEXT_COLOUR_ROWS[0][0]), self, self._title)  # the grid's black to start
        if picked.isValid():
            self._chosen(picked)


class FontStyle(QToolButton):
    """The A of the bar: its menu holds the font and size boxes and the two colour grids, acting on the editor's text."""

    def __init__(self, edit: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit = edit
        self.setText("A")
        bold = QFont(self.font())
        bold.setBold(True)
        self.setFont(bold)
        self.setToolTip("Font Style")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.panel_menu = QMenu(self)
        panel = QWidget(self.panel_menu)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        boxes = QHBoxLayout()
        self.family_box = QFontComboBox(panel)
        self.size_box = QComboBox(panel)
        self.size_box.setEditable(True)
        self.size_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # a size typed is used, never added to the list
        self.size_box.addItems([str(points) for points in SIZES])
        boxes.addWidget(self.family_box, 1)
        boxes.addWidget(self.size_box)
        layout.addLayout(boxes)
        self.text_colour = ColourGrid("Text Color", False, self._set_text_colour, panel, self.panel_menu.close)
        self.background = ColourGrid("Background Color", True, self._set_background, panel, self.panel_menu.close)
        layout.addWidget(self.text_colour)
        layout.addWidget(self.background)
        action = QWidgetAction(self.panel_menu)
        action.setDefaultWidget(panel)
        self.panel_menu.addAction(action)
        self.setMenu(self.panel_menu)
        self.panel_menu.aboutToShow.connect(self.show_current)
        self.family_box.activated.connect(self._family_chosen)
        self.size_box.activated.connect(self._size_chosen)  # a size picked from the list
        line = self.size_box.lineEdit()
        if line is not None:  # a size typed: with nothing added to the list the box does not report Enter itself
            line.returnPressed.connect(lambda: self._set_size(self.size_box.currentText()))

    def show_current(self) -> None:
        """The font, size and colours of the text at the cursor, shown as the menu opens."""
        current = self._edit.currentCharFormat()
        families = current.fontFamilies()
        family = str(families[0]) if isinstance(families, list) and families else self._edit.font().family()
        self.family_box.blockSignals(True)
        self.family_box.setCurrentFont(QFont(family))
        self.family_box.blockSignals(False)
        points = current.fontPointSize() or self._edit.font().pointSizeF()
        self.size_box.setEditText(str(round(points)) if points > 0 else "")
        self.text_colour.show_current(current.foreground().color() if current.hasProperty(QTextCharFormat.Property.ForegroundBrush) else None)
        has_background = current.hasProperty(QTextCharFormat.Property.BackgroundBrush) and current.background().style() != Qt.BrushStyle.NoBrush
        self.background.show_current(current.background().color() if has_background else None)

    def _family_chosen(self, index: int) -> None:
        self._set_family(self.family_box.currentFont().family())

    def _size_chosen(self, index: int) -> None:
        self._set_size(self.size_box.currentText())

    def _merge(self, change: QTextCharFormat, close: bool = True) -> None:
        self._edit.mergeCurrentCharFormat(change)  # the selection, or what is typed next
        if close:
            self.panel_menu.close()
            self._edit.setFocus()

    def _set_family(self, family: str) -> None:
        change = QTextCharFormat()
        change.setFontFamilies([family])
        self._merge(change)

    def _set_size(self, text: str) -> None:
        points = size_of(text)
        if points is None:  # not a size: the box shows the size in use again
            self.show_current()
            return
        change = QTextCharFormat()
        change.setFontPointSize(points)
        self._merge(change)

    def _set_text_colour(self, colour: QColor | None) -> None:
        if colour is None:
            return
        change = QTextCharFormat()
        change.setForeground(QBrush(colour))
        self._merge(change)

    def _set_background(self, colour: QColor | None) -> None:
        change = QTextCharFormat()
        change.setBackground(QBrush(colour) if colour is not None else QBrush(Qt.BrushStyle.NoBrush))
        self._merge(change)
