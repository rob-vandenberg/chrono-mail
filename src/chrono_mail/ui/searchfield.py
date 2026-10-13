from typing import override

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "searchfield.py 0.2.97"

# --- Version history ----------------------------------------------------
# v0.2.97: The corners are rounded by half the height of the field, so the ends stay round at every text size
# v0.1.93: Initial search field: grey while not in use, blue while clicked in, orange while a search is active


IDLE_HINT = "Search (type '?' for help)"
OWN_LISTS: tuple[str, ...] = ("searchHistory", "searchHelp")  # the lists under the field: while they have the keyboard, the field is still "clicked in"
FOCUSED_HINT = "Start search"


class SearchField(QFrame):
    """The rounded search field with "This folder / All folders" at its end.

    Not in use: a magnifying glass at the start and the hint in italics. Clicked in: a blue border, the magnifying glass at the
    end. A search is active (after Enter, until it is cleared): an orange border and a red ✕ at the end that clears it."""

    clicked = Signal()  # a mouse press in the text
    cleared = Signal()  # the red ✕ was clicked

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("searchPill")
        self.line_edit = QLineEdit(self)
        self.line_edit.setMinimumWidth(220)
        self.scope = QComboBox(self)
        self.scope.addItem("This folder", "folder")
        self.scope.addItem("All folders", "all")
        self.start_icon = QLabel(self)
        self.start_icon.setPixmap(theme_icon("edit-find").pixmap(16, 16))
        self.end_button = QToolButton(self)
        self.end_button.setObjectName("searchEnd")
        self.end_button.setAutoRaise(True)
        self.end_button.clicked.connect(self._end_clicked)
        self.separator = QFrame(self)
        self.separator.setObjectName("searchSeparator")
        self.separator.setFixedWidth(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 1, 6, 1)
        layout.setSpacing(4)
        layout.addWidget(self.start_icon)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.end_button)
        layout.addWidget(self.separator)
        layout.addWidget(self.scope)
        self.setMinimumWidth(360)
        self.focused: bool = False
        self.active: bool = False
        self.corner_radius: int = 0  # half the height, set at every change of height
        self.line_edit.installEventFilter(self)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._focus_changed)
        self.line_edit.textChanged.connect(self._update_look)
        self._update_look()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        radius = self.height() // 2  # more than half the height and Qt draws square corners
        if radius != self.corner_radius:
            self.corner_radius = radius
            self.setStyleSheet(f"QFrame#searchPill {{ border-radius: {radius}px; }}")

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.line_edit:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.clicked.emit()
        return super().eventFilter(watched, event)

    def _focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        focused = new is self.line_edit or (new is not None and new.window().objectName() in OWN_LISTS)
        if focused != self.focused:
            self.focused = focused
            self._update_look()

    def set_active(self, active: bool) -> None:
        self.active = active
        self._update_look()

    def look(self) -> str:
        """idle, focused or active: what the stylesheet draws the border by."""
        return "active" if self.active else "focused" if self.focused else "idle"

    def _end_clicked(self) -> None:
        if self.active:
            self.cleared.emit()

    def _update_look(self) -> None:
        look = self.look()
        self.start_icon.setVisible(look == "idle")
        self.end_button.setVisible(look != "idle")
        self.end_button.setIcon(theme_icon("edit-delete-remove" if self.active else "edit-find"))
        self.end_button.setToolTip("Clear the search" if self.active else "")
        self.line_edit.setPlaceholderText(FOCUSED_HINT if self.focused else IDLE_HINT)
        font = self.line_edit.font()
        font.setItalic(look == "idle" and not self.line_edit.text())
        self.line_edit.setFont(font)
        if self.property("look") != look:
            self.setProperty("look", look)
            self.style().unpolish(self)
            self.style().polish(self)
