import os
from dataclasses import dataclass
from typing import cast, override

from PySide6.QtCore import QEvent, QObject, QSettings, Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from chrono_mail.ui import prefs, scale
from chrono_mail.ui.prefs import COLOR_SCHEMES, read_preferences
from chrono_mail.ui.colours import menu_palette_of, palette_of, set_accents, theme_of

# --- Version ------------------------------------------------------------
__version__ = "appearance.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: a tooltip takes the size of the window the mouse is over, also while another window is active (TooltipSize, D-367)
# v4.6.655: while the program gets a new style no part of the main window is given a font (scale.ScaledStyle.switching, D-367)
# v4.4.623: compose_family and compose_points: the default font of new messages, chosen or the program's own (D-354)
# v1.2.253: The colour scheme and the accents come from the one settings object (R-12)
# v1.1.182: The accent colour of each theme comes from the settings (accent_dark, accent_light, D-164)
# v1.1.172: Every colour is the theme's (core/colours.py): "System" only picks the dark or the light theme; Fusion on every platform; the style is no longer a setting (D-158)
# v0.5.149: The program's own blue in every colour scheme (also System) and every window state, never the system's accent colour
# v0.5.144: The text of the program at the size of the other windows and dialogs; the main window and the menus and popups opened from it at the size of the main window (main_font; the style gives it to the menus)
# v0.3.106: The size of the interface is a factor (scale.py): the text, and through ScaledStyle the icons and tick boxes, follow it; it can be tried out before it is saved
# v0.0.82: Text size of the interface (8 to 48 pt, the system size at the first start) under Appearance; the Menu button uses the three-bar character U+2630
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.51: Initial colour scheme (system, light, dark) and style (platform, Fusion)

MINIMUM_FONT_POINTS: int = 8
MAXIMUM_FONT_POINTS: int = 48
FALLBACK_FONT_POINTS: int = 10  # for a platform font that is sized in pixels
SCHEMES: tuple[str, ...] = COLOR_SCHEMES  # the one list (ui/prefs.py)
FUSION: str = "Fusion"  # the one style, on every platform: it draws with the palette it is given, so every colour is the theme's

_SCHEME_OF: dict[str, Qt.ColorScheme] = {"system": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}


@dataclass
class _State:
    settings: QSettings | None = None
    busy: bool = False
    system_font: QFont | None = None  # the font the platform gave, before we changed its size


_state = _State()


LIGHT_PAGES_FLAG: str = "--blink-settings=preferredColorScheme=1"  # 1 is light: mail is written for a white page


def keep_web_pages_light() -> None:
    """Tell the web engine that web pages are always shown in the light scheme, so that a mail with styles for dark screens
    does not put dark text on the white page. Must be done before the application is made."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if LIGHT_PAGES_FLAG not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} {LIGHT_PAGES_FLAG}".strip()


def system_font_size(app: QApplication) -> int:
    """The size in points of the font the platform gave, between the smallest and the largest size the settings allow."""
    if _state.system_font is None:
        _state.system_font = QFont(app.font())
    points = _state.system_font.pointSizeF()
    return max(MINIMUM_FONT_POINTS, min(MAXIMUM_FONT_POINTS, round(points) if points > 0 else FALLBACK_FONT_POINTS))


def compose_family(chosen: str) -> str:
    """The font new messages are written and sent in (Settings > Writing > Default font): the one chosen, else the program's own font."""
    if chosen.strip():
        return chosen.strip()
    app = QApplication.instance()
    if isinstance(app, QApplication):
        system_font_size(app)  # remembers the font the platform gave
    return _state.system_font.family() if _state.system_font is not None else "Sans Serif"


def compose_points(chosen: int) -> int:
    """Its size in points: the one chosen, else the system's size."""
    if chosen > 0:
        return chosen
    app = QApplication.instance()
    return system_font_size(app) if isinstance(app, QApplication) else FALLBACK_FONT_POINTS


def _colour_the_menus(app: QApplication, dark: bool) -> None:
    """Dropdown menus are drawn in the 'base' colour, the colour of the lists, so they run into the folder list. Give them
    the colour of the menu bar."""
    # The stub of setPalette says bytes for the class name, but the call wants a str.
    app.setPalette(menu_palette_of(theme_of(dark)), cast("bytes", "QMenu"))


def system_is_dark(app: QApplication) -> bool:
    """Whether the system is set to dark: the one thing "Follow the system" takes from it."""
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def start(app: QApplication, settings: QSettings) -> None:
    """Remember what the platform chose, apply the settings, and follow the system when it switches between light and dark."""
    _state.settings = settings
    _state.system_font = QFont(app.font())

    def changed(scheme: Qt.ColorScheme) -> None:
        apply(app, settings)

    app.styleHints().colorSchemeChanged.connect(changed)
    apply(app, settings)


def _font_at(app: QApplication, factor: float) -> QFont:
    font = QFont(_state.system_font if _state.system_font is not None else app.font())
    font.setPointSizeF(round(system_font_size(app) * factor, 2))
    return font


def _apply_font_size(app: QApplication) -> None:
    """The text of the program: the system's size times the factor of the other windows and dialogs (the main window
    sets its own, main_font)."""
    font = _font_at(app, scale.windows())
    if app.font() != font:
        app.setFont(font)


def main_font(app: QApplication) -> QFont:
    """The text of the main window and of the menus and popups opened from it: the system's size times its factor."""
    return _font_at(app, scale.interface())


def tooltip_font_for(widget: QWidget | None) -> None:
    """Qt has one tooltip font for the whole program: the size of the window this widget is in, the main window's or the other windows'."""
    app = QApplication.instance()
    if isinstance(app, QApplication):
        QToolTip.setFont(main_font(app) if scale.in_main_window(widget) else app.font())


class TooltipSize(QObject):
    """Watches the main window only (a filter on the whole program crashed PySide6 while it wrapped objects still being made): when the
    mouse comes in, the tooltips take the main window's size; when it leaves, the size of the window it went to (D-367). The active window
    alone said it wrongly: over the main window while another window was active, a tooltip took that window's size."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter and isinstance(watched, QWidget):
            tooltip_font_for(watched)
        elif event.type() == QEvent.Type.Leave:
            tooltip_font_for(QApplication.widgetAt(QCursor.pos()))  # another window, a menu of the main window, or nothing of this program
        return False  # the window goes on as before


def apply(app: QApplication, settings: QSettings, interface_factor: float | None = None, windows_factor: float | None = None) -> None:
    """Put the colour scheme, the style and the sizes of the main window and of the other windows of the settings in
    use, at once. A factor given here is tried out (the Settings window while its slider moves) and not saved."""
    if _state.busy:
        return
    _state.busy = True
    try:
        chosen = read_preferences(settings)
        scheme = chosen.color_scheme
        app.styleHints().setColorScheme(_SCHEME_OF[scheme])  # for the parts the platform draws itself (title bars) and for web pages
        dark = system_is_dark(app) if scheme == "system" else scheme == "dark"
        scale.set_interface(interface_factor if interface_factor is not None else prefs.saved_factor(settings, system_font_size(app)))
        scale.set_windows(windows_factor if windows_factor is not None else prefs.saved_windows(settings, system_font_size(app)))
        current = app.style()
        if not (
            isinstance(current, scale.ScaledStyle) and current.base_name.lower() == FUSION.lower()
            and current.factor == scale.interface() and current.windows_factor == scale.windows()
        ):
            new_style = scale.ScaledStyle(FUSION, scale.interface(), scale.windows())
            new_style.switching = True  # no font is set while every part gets the style (scale.follow_fonts does it after, D-367)
            app.setStyle(new_style)  # the icons and tick boxes at the factor of their window
            new_style.switching = False
        style = app.style()
        if isinstance(style, scale.ScaledStyle):
            style.main_font = main_font(app)  # for the menus and popups of the main window
        # Every colour is the theme's (core/colours.py), never the platform's: "System" only decides which of the two.
        set_accents(chosen.accent_dark, chosen.accent_light)
        app.setPalette(palette_of(theme_of(dark)))
        _colour_the_menus(app, dark)
        _apply_font_size(app)
    finally:
        _state.busy = False
