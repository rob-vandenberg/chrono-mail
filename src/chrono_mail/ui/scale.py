import re
from typing import overload, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleHintReturn, QStyleOption, QWidget

from chrono_mail.ui.icons import bundled_icon

# --- Version ------------------------------------------------------------
__version__ = "scale.py 4.6.655"

# --- Version history ----------------------------------------------------
# v4.6.655: follow_fonts: every part of the main window takes the size of the part it is in (the style sheet keeps Qt from passing it on),
#           also a part made later (polish); a part with a slider of its own (OWN_SIZE_PROPERTY) is left alone (D-367)
# v4.6.652: a size factor for each of Calendar, Contacts and Tasks, the part of the view right of the sidebar (view, set_view, vpx, D-367)
# v4.4.615: The buttons of dialogs and message boxes always have icons, and these (and the icons of message boxes) are the program's own
#           Breeze copies in the colour of button text, on every computer and in every theme; never the desktop's icon theme (D-087, D-158)
#           also Yes to all, No to all, Save all, Abort, Retry and Ignore (document-save-all, process-stop and go-next copied from Breeze)
# v1.2.253: The saved sizes (saved_factor, saved_windows, saved_reading, saved_list, with their conversions of older versions) moved to ui/prefs.py, where every setting is read (R-12)
# v0.5.144: A size factor of its own for the other windows and dialogs (windows, wpx, saved_windows); icons and tick boxes follow the factor of the window they are in
# v0.3.108: The size factor of the message list (lpx, saved_list); saved_reading converts the older zoom of messages (percent); it is the size of reading and writing
# v0.3.106: Initial size factor of the interface (a magnifying glass): text, icons, spacing and fixed sizes grow and shrink together

MINIMUM: float = 0.75
MAXIMUM: float = 2.00
STEP: float = 0.05
STANDARD: float = 1.00
ICON_METRICS: frozenset[QStyle.PixelMetric] = frozenset({
    QStyle.PixelMetric.PM_SmallIconSize, QStyle.PixelMetric.PM_LargeIconSize, QStyle.PixelMetric.PM_ToolBarIconSize,
    QStyle.PixelMetric.PM_ButtonIconSize, QStyle.PixelMetric.PM_ListViewIconSize, QStyle.PixelMetric.PM_IconViewIconSize,
    QStyle.PixelMetric.PM_TabBarIconSize, QStyle.PixelMetric.PM_MessageBoxIconSize,
    QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight,
    QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight,
})
# The program's own icon for each standard icon of dialog buttons and message boxes: the names the desktop theme would be asked for
# (Breeze's dialog-ok is a link to dialog-ok-apply).
STANDARD_ICONS: dict[QStyle.StandardPixmap, str] = {
    QStyle.StandardPixmap.SP_DialogOkButton: "dialog-ok-apply",
    QStyle.StandardPixmap.SP_DialogCancelButton: "dialog-cancel",
    QStyle.StandardPixmap.SP_DialogSaveButton: "document-save",
    QStyle.StandardPixmap.SP_DialogCloseButton: "window-close",
    QStyle.StandardPixmap.SP_DialogApplyButton: "dialog-ok-apply",
    QStyle.StandardPixmap.SP_DialogYesButton: "dialog-ok-apply",
    QStyle.StandardPixmap.SP_DialogNoButton: "dialog-cancel",
    QStyle.StandardPixmap.SP_DialogResetButton: "edit-clear",
    QStyle.StandardPixmap.SP_DialogDiscardButton: "edit-delete",
    QStyle.StandardPixmap.SP_DialogHelpButton: "help-contents",
    QStyle.StandardPixmap.SP_DialogOpenButton: "folder-open",
    # Fusion asks no name for these six (on its own it shows no icon): the nearest Breeze icon, so none reaches the desktop theme (D-087)
    QStyle.StandardPixmap.SP_DialogYesToAllButton: "dialog-ok-apply",
    QStyle.StandardPixmap.SP_DialogNoToAllButton: "dialog-cancel",
    QStyle.StandardPixmap.SP_DialogSaveAllButton: "document-save-all",
    QStyle.StandardPixmap.SP_DialogAbortButton: "process-stop",
    QStyle.StandardPixmap.SP_DialogRetryButton: "view-refresh",
    QStyle.StandardPixmap.SP_DialogIgnoreButton: "go-next",
    QStyle.StandardPixmap.SP_MessageBoxInformation: "dialog-information",
    QStyle.StandardPixmap.SP_MessageBoxWarning: "dialog-warning",
    QStyle.StandardPixmap.SP_MessageBoxCritical: "dialog-error",
    QStyle.StandardPixmap.SP_MessageBoxQuestion: "dialog-question",
}
MAIN_WINDOW_PROPERTY: str = "chronoMainWindow"  # set on the main window: it and its menus follow the factor of the main window
OWN_SIZE_PROPERTY: str = "chronoOwnSize"  # a part with a slider of its own (the message list, Calendar, Contacts, Tasks, the content of a message):
# the Main window slider leaves it alone, and the parts inside it follow it (D-367)
FONT_SHARE_PROPERTY: str = "chronoFontShare"  # a part a fixed share larger than the part it is in (a subject 1.35): it keeps that share
SIZE_RESOLVED: int = 0x0002  # QFont::SizeResolved: the font has a size of its own (PySide does not name it)
_PIXELS = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)px")


class _State:
    factor: float = STANDARD
    list_factor: float = STANDARD
    windows_factor: float = STANDARD


_state = _State()


def interface() -> float:
    """The size factor of the main window (the interface) now (1.00 is the system's normal size)."""
    return _state.factor


def set_interface(factor: float) -> None:
    _state.factor = clamp(factor)


def windows() -> float:
    """The size factor of the other windows and dialogs now (everything but the main window and its menus)."""
    return _state.windows_factor


def set_windows(factor: float) -> None:
    _state.windows_factor = clamp(factor)


def wpx(pixels: float) -> int:
    """A size in pixels at the factor of the other windows and dialogs; never below 1."""
    return max(1, round(pixels * _state.windows_factor))


def owning_window(widget: QWidget | None) -> QWidget | None:
    """The window a widget belongs to: a menu or a popup belongs to the window it was opened from."""
    window = widget.window() if widget is not None else None
    while window is not None and window.windowType() == Qt.WindowType.Popup and window.parentWidget() is not None:
        parent = window.parentWidget()
        window = parent.window() if parent is not None else None
    return window


def in_main_window(widget: QWidget | None) -> bool:
    window = owning_window(widget)
    return window is not None and bool(window.property(MAIN_WINDOW_PROPERTY))


def follow_parent_font(widget: QWidget) -> None:
    """A part of the main window at the size of the part it is in (times its share), with its own bold, italic and typeface. With the
    theme's style sheet Qt gives every part the program's font (the size of the other windows) instead (D-367). A window, and a part with a
    slider of its own, are left alone."""
    parent = widget.parentWidget()
    if parent is None or widget.isWindow() or widget.property(OWN_SIZE_PROPERTY):
        return
    share = widget.property(FONT_SHARE_PROPERTY)
    size = round(parent.font().pointSizeF() * (float(share) if isinstance(share, (int, float)) else 1.0), 2)
    font = QFont(widget.font())
    own = bool(font.resolveMask() & SIZE_RESOLVED)
    if size > 0 and (font.pointSizeF() != size or not own):  # also when it matches: a size of its own, or the program's font would replace it
        font.setPointSizeF(size)
        widget.setFont(font)


def follow_fonts(root: QWidget) -> None:
    """Every part inside root (the main window) follows the part it is in, each part before the parts inside it; other windows are left
    alone (D-367)."""
    waiting = [child for child in root.children() if isinstance(child, QWidget)]
    while waiting:
        widget = waiting.pop()
        if widget.isWindow():
            continue
        follow_parent_font(widget)
        waiting.extend(child for child in widget.children() if isinstance(child, QWidget))


def factor_for(widget: QWidget | None) -> float:
    """The factor of the main window for the main window and its menus, the factor of the other windows for everything else."""
    return _state.factor if in_main_window(widget) else _state.windows_factor


def px_for(widget: QWidget | None, pixels: float) -> int:
    """A size in pixels at the factor of the window the widget is in; never below 1."""
    return max(1, round(pixels * factor_for(widget)))


def message_list() -> float:
    """The size factor of the message list now."""
    return _state.list_factor


def set_message_list(factor: float) -> None:
    _state.list_factor = clamp(factor)


def lpx(pixels: float) -> int:
    """A size in pixels at the factor of the message list; never below 1."""
    return max(1, round(pixels * _state.list_factor))


VIEWS: tuple[str, ...] = ("calendar", "contacts", "tasks")  # the views with a size slider of their own (D-367)
_views: dict[str, float] = dict.fromkeys(VIEWS, STANDARD)


def view(name: str) -> float:
    """The size factor of a view now: what is right of the sidebar and below the top bar (Calendar, Contacts or Tasks, D-367)."""
    return _views.get(name, STANDARD)


def set_view(name: str, factor: float) -> None:
    _views[name] = clamp(factor)


def vpx(name: str, pixels: float) -> int:
    """A size in pixels at the factor of that view; never below 1."""
    return max(1, round(pixels * view(name)))


def clamp(factor: float) -> float:
    """Within Small and Large, on a step of the slider."""
    steps = round((min(MAXIMUM, max(MINIMUM, factor)) - MINIMUM) / STEP)
    return round(MINIMUM + steps * STEP, 2)


def px(pixels: float) -> int:
    """A size in pixels at the factor of the main window (the interface); never below 1."""
    return max(1, round(pixels * _state.factor))


def scale_css(css: str, factor: float | None = None) -> str:
    """A stylesheet with every size in pixels at a factor (the main window's when none is given). Lines of 1 pixel stay 1 pixel."""
    used = _state.factor if factor is None else factor

    def scaled(match: re.Match[str]) -> str:
        value = float(match.group(1))
        return match.group(0) if value <= 1 else f"{max(1, round(value * used))}px"

    return _PIXELS.sub(scaled, css)


def slider_position(factor: float) -> int:
    return round((clamp(factor) - MINIMUM) / STEP)


def factor_at(position: int) -> float:
    return clamp(MINIMUM + position * STEP)


SLIDER_STEPS: int = slider_position(MAXIMUM)


class ScaledStyle(QProxyStyle):
    """The style of the platform (or the one chosen) with its icons and tick boxes at the factor of the window they are in:
    the main window's for the main window and its menus, the other windows' for everything else."""

    def __init__(self, name: str, factor: float, windows_factor: float) -> None:
        super().__init__(name)
        self.factor: float = factor
        self.windows_factor: float = windows_factor
        self.main_font: QFont | None = None  # the text of the main window: its menus and popups get it when they are polished
        self.switching: bool = False  # the program is being given this style: a font set now would crash Qt; the main window sizes every part after it
        self.base_name: str = name
        base = self.baseStyle()
        self.setObjectName(base.objectName() if base is not None else name)

    @overload
    def polish(self, app: QApplication, /) -> None: ...
    @overload
    def polish(self, widget: QWidget, /) -> None: ...
    @overload
    def polish(self, arg__1: Qt.GlobalColor | QColor | QPalette, /) -> None: ...
    @override
    def polish(self, target: QApplication | QWidget | Qt.GlobalColor | QColor | QPalette, /) -> None:
        """A menu or popup is a window of its own and takes the program's text size; one opened from the main window gets the
        main window's (once, when it is first shown or when the style changes)."""
        if isinstance(target, QApplication):
            super().polish(target)
        elif isinstance(target, QWidget):
            super().polish(target)
            if self.main_font is not None and target.isWindow() and target.windowType() == Qt.WindowType.Popup and in_main_window(target):
                target.setFont(self.main_font)
            elif not self.switching and not target.isWindow() and in_main_window(target):
                follow_parent_font(target)  # a part of the main window made later: the size of the part it is in (D-367)
        else:
            super().polish(target)

    @override
    def pixelMetric(self, metric: QStyle.PixelMetric, option: QStyleOption | None = None, widget: QWidget | None = None) -> int:
        value = super().pixelMetric(metric, option, widget)
        if metric not in ICON_METRICS:
            return value
        return max(1, round(value * (self.factor if in_main_window(widget) else self.windows_factor)))

    @override
    def styleHint(self, hint: QStyle.StyleHint, option: QStyleOption | None = None, widget: QWidget | None = None, returnData: QStyleHintReturn | None = None) -> int:  # noqa: N802, N803 - Qt's names
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 1  # the program's own icons on dialog buttons, on every computer (the platform decides it otherwise)
        return super().styleHint(hint, option, widget, returnData)

    @override
    def standardIcon(self, standardIcon: QStyle.StandardPixmap, option: QStyleOption | None = None, widget: QWidget | None = None) -> QIcon:  # noqa: N802, N803 - Qt's names
        name = STANDARD_ICONS.get(standardIcon)
        icon = bundled_icon(name) if name is not None else QIcon()
        return icon if not icon.isNull() else super().standardIcon(standardIcon, option, widget)
