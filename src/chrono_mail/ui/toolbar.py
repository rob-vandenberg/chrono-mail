from typing import override

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSizePolicy, QStackedWidget, QToolBar, QToolButton, QWidget

from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.scale import px
from chrono_mail.ui.searchfield import SearchField

# --- Version ------------------------------------------------------------
__version__ = "toolbar.py 0.5.170"

# --- Version history ----------------------------------------------------
# v0.5.170: One toolbar for the window (WindowToolbar) that shows the row of the section on screen; a section has a row, not a toolbar of its own (D-156)
# v0.5.167: Initial folding toolbar of every section: Menu and the search field always shown, the other buttons fold into More from the right (D-154)

FULL: int = 0
MERGED: int = 1
FOLDED: int = 2


class _Unit:
    """One place on the toolbar that folds as one: a button, or the view buttons of the calendar with the one button
    they merge into."""

    def __init__(self, buttons: list[QToolButton], merged: QToolButton | None = None) -> None:
        self.buttons: list[QToolButton] = buttons
        self.merged: QToolButton | None = merged

    def widgets(self) -> list[QToolButton]:
        return [*self.buttons, *([self.merged] if self.merged is not None else [])]

    def present(self) -> bool:
        """An action that is switched off (hidden) has no place on the toolbar and none in More."""
        action = self.buttons[0].defaultAction()
        return action is None or action.isVisible()

    def width(self, merged: bool, spacing: int) -> int:
        if merged and self.merged is not None:
            return self.merged.sizeHint().width()
        return sum(button.sizeHint().width() for button in self.buttons) + spacing * (len(self.buttons) - 1)

    def show(self, state: int) -> None:
        merged = state == MERGED and self.merged is not None
        for button in self.buttons:
            button.setVisible(state != FOLDED and not merged)
        if self.merged is not None:
            self.merged.setVisible(merged)

    def add_to_more(self, more: QMenu) -> None:
        """Its entry in More: the action itself, or a menu with what its button's menu holds."""
        button = self.merged if self.merged is not None else self.buttons[0]
        action = button.defaultAction()
        menu = button.menu()
        if menu is None:
            if action is not None:
                more.addAction(action)
            return
        split = button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
        title = action.iconText() if action is not None else button.text()
        icon = action.icon() if action is not None else button.icon()
        proxy = more.addMenu(icon, title)
        proxy.menuAction().setEnabled(button.isEnabled() and (action is None or action.isEnabled()))
        proxy.aboutToShow.connect(lambda: _fill_proxy(proxy, menu, action if split else None))


def _fill_proxy(proxy: QMenu, menu: QMenu, action: QAction | None) -> None:
    """A folded button with a menu: its own action first (a split button does it when clicked), then its menu's entries,
    filled as the button fills them."""
    proxy.clear()
    if action is not None:
        proxy.addAction(action)
        proxy.addSeparator()
    menu.aboutToShow.emit()
    for entry in menu.actions():
        proxy.addAction(entry)


class ToolbarRow(QWidget):
    """What a section's toolbar holds, on one row: the Menu button and the buttons that start the section's work at the
    left (New, Refresh), the other buttons centred, the search field at the right. When the row is too narrow, things give
    way in this order: the search field's "This folder" shows only its ▾; the view buttons of the calendar merge into one;
    the buttons fold into More, the right-most first; the search field shrinks to its minimum. Menu and the search field
    are always shown. Widths are worked out from what each part would need, never by trying, so the row never flickers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.line: QHBoxLayout = QHBoxLayout(self)  # the row's parts in their order
        self.line.setContentsMargins(0, 0, 0, 0)
        self.line.setSpacing(px(2))
        self.menu_button: QToolButton | None = None
        self.search: SearchField | None = None
        self.left: list[_Unit] = []
        self.center: list[_Unit] = []
        self.buttons: dict[QAction, QToolButton] = {}
        self.more_extras: list[QAction] = []
        self.more_menu = QMenu(self)
        self.more_menu.aboutToShow.connect(self._fill_more)
        self.more = QToolButton(self)
        self.more.setObjectName("moreButton")
        self.more.setText("More")
        self.more.setIcon(theme_icon("overflow-menu"))
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.setMenu(self.more_menu)
        self.more.hide()
        self.line.addStretch(1)  # after the left buttons
        self.line.addStretch(1)  # after the centred buttons
        self.line.addStretch(1)
        self.icon_size: QSize | None = None  # the toolbar's, once the row is in it
        self.folded: list[_Unit] = []
        self.compact: bool = False
        self.merged: bool = False

    # --- filling ------------------------------------------------------------------------------------------------

    def set_menu_button(self, button: QToolButton) -> None:
        self.menu_button = button
        button.setParent(self)
        self.line.insertWidget(0, button)

    def button(self, item: QAction | QToolButton) -> QToolButton:
        """The button for an action (made here, like a toolbar makes it), or a button made by the section."""
        if isinstance(item, QToolButton):
            item.setParent(self)
            return self._sized(item)
        button = QToolButton(self)
        button.setDefaultAction(item)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons[item] = button
        return self._sized(button)

    def _sized(self, button: QToolButton) -> QToolButton:
        if self.icon_size is not None:
            button.setIconSize(self.icon_size)
        return button

    def add_left(self, item: QAction | QToolButton) -> QToolButton:
        button = self.button(item)
        self.line.insertWidget(self._stretch(0), button)
        self.left.append(_Unit([button]))
        self.arrange()
        return button

    def add_center(self, item: QAction | QToolButton) -> QToolButton:
        button = self.button(item)
        self.line.insertWidget(self._stretch(1), button)
        self.center.append(_Unit([button]))
        self.arrange()
        return button

    def add_views(self, actions: list[QAction], merged: QToolButton) -> list[QToolButton]:
        """Buttons that choose one of several views (the calendar): side by side, and merged into one button with a menu
        when the row gets narrow. The chosen view has a line under it."""
        buttons: list[QToolButton] = []
        for action in actions:
            button = self.button(action)
            button.setObjectName("viewButton")
            self.line.insertWidget(self._stretch(1), button)
            buttons.append(button)
        merged.setParent(self)
        self._sized(merged)
        merged.hide()
        self.line.insertWidget(self._stretch(1), merged)
        self.center.append(_Unit(buttons, merged))
        self.arrange()
        return buttons

    def set_search(self, field: SearchField) -> None:
        self.search = field
        field.setParent(self)
        self.line.addWidget(field)
        self.arrange()

    def set_more_extras(self, actions: list[QAction]) -> None:
        """Entries that always live in More (below a line, after what is folded): More is then always shown."""
        self.more_extras = actions
        self.arrange()

    def button_for(self, action: QAction) -> QToolButton:
        return self.buttons[action]

    def widgets_in_order(self) -> list[QWidget]:
        """The parts of the row from left to right (the stretches left out)."""
        found: list[QWidget] = []
        for index in range(self.line.count()):
            item = self.line.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                found.append(widget)
        return found

    def _stretch(self, number: int) -> int:
        """Where the first (after the left buttons) or the second stretch (after the centred buttons) is now."""
        stretches = [index for index in range(self.line.count()) if (item := self.line.itemAt(index)) is not None and item.spacerItem() is not None]
        return stretches[number]

    def apply_scale(self) -> None:
        """The spacing and the search field at the size factor of the interface."""
        self.line.setSpacing(px(2))
        if self.search is not None:
            self.search.apply_scale()
        self.arrange()

    def set_icon_size(self, size: QSize) -> None:
        self.icon_size = size
        for unit in (*self.left, *self.center):
            for button in unit.widgets():
                button.setIconSize(size)
        self.more.setIconSize(size)
        self.arrange()

    # --- giving way ----------------------------------------------------------------------------------------------

    def _units(self) -> list[_Unit]:
        return [unit for unit in (*self.left, *self.center) if unit.present()]

    def _needed(self, units: list[_Unit], compact: bool, merged: bool, shown: int) -> tuple[int, int]:
        """The width the row needs in this state, and the part of it that is the search field."""
        spacing = self.line.spacing()
        widths: list[int] = []
        if self.menu_button is not None:
            widths.append(self.menu_button.sizeHint().width())
        widths.extend(unit.width(merged, spacing) for unit in units[:shown])
        if shown < len(units) or self.more_extras:
            widths.append(self.more.sizeHint().width())
        search = self.search.preferred_width(compact) if self.search is not None else 0
        if self.search is not None:
            widths.append(search)
        return sum(widths) + spacing * max(0, len(widths) - 1), search

    def _states(self, units: list[_Unit]) -> list[tuple[bool, bool, int]]:
        """Every state in the order things give way: (compact search, views merged, number of units shown)."""
        compact, merged = False, False
        states = [(compact, merged, len(units))]
        if self.search is not None and self.search.has_scope:
            compact = True
            states.append((compact, merged, len(units)))
        if any(unit.merged is not None for unit in units):
            merged = True
            states.append((compact, merged, len(units)))
        states.extend((compact, merged, shown) for shown in range(len(units) - 1, -1, -1))
        return states

    def arrange(self) -> None:
        """Show what fits in the width the row has now."""
        units = self._units()
        states = self._states(units)
        available = self.width()
        chosen = states[-1]
        for state in states:
            if self._needed(units, *state)[0] <= available:
                chosen = state
                break
        compact, merged, shown = chosen
        self.compact, self.merged = compact, merged
        for unit in (*self.left, *self.center):
            if not unit.present():
                unit.show(FOLDED)
        for number, unit in enumerate(units):
            unit.show(FOLDED if number >= shown else MERGED if merged else FULL)
        self.folded = units[shown:]
        needs_more = bool(self.folded) or bool(self.more_extras)
        if needs_more:  # right after the last button shown
            last: QWidget | None = units[shown - 1].widgets()[-1] if shown else self.menu_button
            after = self.line.indexOf(last) if last is not None else -1
            if self.line.indexOf(self.more) != after + 1:
                self.line.removeWidget(self.more)
                self.line.insertWidget(self.line.indexOf(last) + 1 if last is not None else 0, self.more)
        self.more.setVisible(needs_more)
        if self.search is not None:
            self.search.set_compact(compact)
            needed, search = self._needed(units, compact, merged, shown)
            width = max(self.search.minimum_width(), min(search, available - (needed - search)))
            self.search.setFixedWidth(width)

    def _fill_more(self) -> None:
        self.more_menu.clear()
        for unit in self.folded:
            unit.add_to_more(self.more_menu)
        if self.more_extras:
            if self.folded:
                self.more_menu.addSeparator()
            for action in self.more_extras:
                self.more_menu.addAction(action)

    @override
    def minimumSizeHint(self) -> QSize:
        """Menu, More and the search field at its minimum: the window can never be narrower, so the search field stays."""
        spacing = self.line.spacing()
        widths = [self.more.sizeHint().width()]
        if self.menu_button is not None:
            widths.append(self.menu_button.sizeHint().width())
        if self.search is not None:
            widths.append(self.search.minimum_width())
        return QSize(sum(widths) + spacing * (len(widths) - 1), super().minimumSizeHint().height())

    @override
    def sizeHint(self) -> QSize:
        units = self._units()
        return QSize(self._needed(units, False, False, len(units))[0], super().sizeHint().height())

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.arrange()

    @override
    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:  # a button changed its size (a new text, a new size factor)
            self.arrange()
        return handled


class WindowToolbar(QToolBar):
    """The window's one toolbar. Every section (Mail, Calendar, Contacts, Tasks) has its own row of buttons (ToolbarRow);
    the toolbar holds the rows on top of each other and shows the row of the section on screen. Showing another section
    swaps the row in one step, so the window never sees two toolbars, nor none, and keeps its size."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.setMovable(False)
        self.setProperty("folding", True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.rows = QStackedWidget(self)
        self.rows.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(self.rows)
        self.iconSizeChanged.connect(self._icon_size_changed)

    def add_row(self, row: ToolbarRow) -> None:
        self.rows.addWidget(row)
        row.set_icon_size(self.iconSize())

    def show_row(self, row: ToolbarRow) -> None:
        self.rows.setCurrentWidget(row)

    def _icon_size_changed(self, size: QSize) -> None:
        for index in range(self.rows.count()):
            row = self.rows.widget(index)
            if isinstance(row, ToolbarRow):
                row.set_icon_size(size)

    def apply_scale(self) -> None:
        """Every row at the size factor of the interface: spacing, icons, the search field."""
        for index in range(self.rows.count()):
            row = self.rows.widget(index)
            if isinstance(row, ToolbarRow):
                row.set_icon_size(self.iconSize())
                row.apply_scale()


def view_button(parent: QWidget | None = None) -> QToolButton:
    """The one button the calendar's views merge into: the chosen view's icon and name, the views in its menu."""
    button = QToolButton(parent)
    button.setObjectName("mergedViews")
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button
