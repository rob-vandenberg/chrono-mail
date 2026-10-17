from typing import override

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QResizeEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton, QWidget

from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.scale import px

# --- Version ------------------------------------------------------------
__version__ = "searchfield.py 0.5.168"

# --- Version history ----------------------------------------------------
# v0.5.168: a field without "This folder" is as wide as Mail's field without that part
# v0.5.167: "This folder / All folders" is a button that can show only its ▾ (ScopeButton); a field without it that searches while typing, for Calendar, Contacts and Tasks; the toolbar sets its width (preferred and minimum width)
# v0.3.106: The magnifying glass, the spacing and the widths follow the size factor of the interface (apply_scale)
# v0.2.97: The corners are rounded by half the height of the field, so the ends stay round at every text size
# v0.1.93: Initial search field: grey while not in use, blue while clicked in, orange while a search is active


IDLE_HINT = "Search (type '?' for help)"
OWN_LISTS: tuple[str, ...] = ("searchHistory", "searchHelp")  # the lists under the field: while they have the keyboard, the field is still "clicked in"
FOCUSED_HINT = "Start search"
PREFERRED_WIDTH: int = 360  # at the size factor 1.00, with "This folder" written out
MINIMUM_WIDTH: int = 200  # when nothing else on the toolbar can give way any more


class ScopeButton(QToolButton):
    """Where a search looks: "This folder ▾" or "All folders ▾", a menu to choose. Compact, only the ▾ is shown."""

    changed = Signal()  # the person chose the other one

    CHOICES: tuple[tuple[str, str], ...] = (("This folder", "folder"), ("All folders", "all"))

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("searchScope")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.compact: bool = False
        self._index: int = 0
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self.choice_actions: list[QAction] = []
        for number, (label, _data) in enumerate(self.CHOICES):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(number == 0)
            action.triggered.connect(lambda checked=False, chosen=number: self._chosen(chosen))
            group.addAction(action)
            self.choice_actions.append(action)
        self.setMenu(menu)
        self.setToolTip(self.CHOICES[0][0])
        self.full_width: int = 0  # measured: the width with the name written out
        self.compact_width: int = 0  # measured: the width of the ▾ alone
        self.measure()

    def current_index(self) -> int:
        return self._index

    def current_data(self) -> str:
        return self.CHOICES[self._index][1]

    def set_current_index(self, index: int) -> None:
        """Choose by number, as if the person chose it (blockSignals keeps it quiet)."""
        if index != self._index:
            self._chosen(index)

    def _chosen(self, index: int) -> None:
        changed = index != self._index
        self._index = index
        self.choice_actions[index].setChecked(True)
        self.setToolTip(self.CHOICES[index][0])
        self.measure()
        if changed:
            self.changed.emit()

    def set_compact(self, compact: bool) -> None:
        if compact != self.compact:
            self.compact = compact
            self.setText("" if compact else self.CHOICES[self._index][0])

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.measure()

    def measure(self) -> None:
        """Both widths, the name written out and only the ▾, so that the toolbar can tell what each costs without trying it."""
        self.ensurePolished()  # measured with the window's style, also while hidden
        self.setText("")
        self.compact_width = self.sizeHint().width()
        self.setText(self.CHOICES[self._index][0])
        self.full_width = self.sizeHint().width()
        if self.compact:
            self.setText("")


class SearchField(QFrame):
    """The rounded search field, in Mail with "This folder / All folders" at its end.

    Not in use: a magnifying glass at the start and the hint in italics. Clicked in: a blue border, the magnifying glass at the
    end. A search is active (after Enter, until it is cleared): an orange border and a red ✕ at the end that clears it.
    Without the scope (Calendar, Contacts, Tasks) the field searches while typing: it is active while it holds text, and the
    red ✕ empties it."""

    clicked = Signal()  # a mouse press in the text
    cleared = Signal()  # the red ✕ was clicked

    def __init__(self, parent: QWidget | None = None, hint: str = IDLE_HINT, focused_hint: str = FOCUSED_HINT, scope: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("searchPill")
        self.hint: str = hint
        self.focused_hint: str = focused_hint
        self.line_edit = QLineEdit(self)
        self.has_scope: bool = scope
        self.scope = ScopeButton(self)
        self.start_icon = QLabel(self)
        self.end_button = QToolButton(self)
        self.end_button.setObjectName("searchEnd")
        self.end_button.setAutoRaise(True)
        self.end_button.clicked.connect(self._end_clicked)
        self.separator = QFrame(self)
        self.separator.setObjectName("searchSeparator")
        self.separator.setFixedWidth(1)
        self._layout = QHBoxLayout(self)
        layout = self._layout
        layout.addWidget(self.start_icon)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.end_button)
        if scope:
            layout.addWidget(self.separator)
            layout.addWidget(self.scope)
        else:
            self.separator.hide()
            self.scope.hide()
            self.line_edit.textChanged.connect(self._typed)  # searching while typing
        self.apply_scale()
        self.focused: bool = False
        self.active: bool = False
        self.corner_radius: int = 0  # half the height, set at every change of height
        self.line_edit.installEventFilter(self)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._focus_changed)
        self.line_edit.textChanged.connect(self._update_look)
        self._update_look()

    def apply_scale(self) -> None:
        """The magnifying glass, the spacing and the widths at the size factor of the interface."""
        self.start_icon.setPixmap(theme_icon("edit-find").pixmap(px(16), px(16)))
        self._layout.setContentsMargins(px(10), 1, px(6), 1)
        self._layout.setSpacing(px(4))
        self.line_edit.setMinimumWidth(px(80))
        self.scope.measure()

    def preferred_width(self, compact: bool) -> int:
        """The width the field takes while the toolbar has room: "This folder" written out, or (compact) only its ▾.
        A field without "This folder" (Calendar, Contacts, Tasks) is Mail's field without that part: the thin line, the
        button and the room between them."""
        if not self.has_scope:
            self.scope.measure()  # hidden here, so measuring changes nothing on screen
            return px(PREFERRED_WIDTH) - (self.scope.full_width + self.separator.width() + 2 * self._layout.spacing())
        saved = self.scope.full_width - self.scope.compact_width if compact else 0
        return px(PREFERRED_WIDTH) - saved

    def minimum_width(self) -> int:
        return px(MINIMUM_WIDTH)

    def set_compact(self, compact: bool) -> None:
        if self.has_scope:
            self.scope.set_compact(compact)

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

    def _typed(self, text: str) -> None:
        self.set_active(bool(text))

    def _end_clicked(self) -> None:
        if self.active:
            if not self.has_scope:
                self.line_edit.clear()
            self.cleared.emit()

    def _update_look(self) -> None:
        look = self.look()
        self.start_icon.setVisible(look == "idle")
        self.end_button.setVisible(look != "idle")
        self.end_button.setIcon(theme_icon("edit-delete-remove" if self.active else "edit-find"))
        self.end_button.setToolTip("Clear the search" if self.active else "")
        self.line_edit.setPlaceholderText(self.focused_hint if self.focused else self.hint)
        font = self.line_edit.font()
        font.setItalic(look == "idle" and not self.line_edit.text())
        self.line_edit.setFont(font)
        if self.property("look") != look:
            self.setProperty("look", look)
            self.style().unpolish(self)
            self.style().polish(self)
