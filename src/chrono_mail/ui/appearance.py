import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from chrono_mail.ui import prefs
from chrono_mail.ui.colours import dark_palette, light_palette, menu_palette

# --- Version ------------------------------------------------------------
__version__ = "appearance.py 0.0.56"

# --- Version history ----------------------------------------------------
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.51: Initial colour scheme (system, light, dark) and style (platform, Fusion)

SCHEMES: tuple[str, ...] = ("system", "light", "dark")
STYLES: tuple[str, ...] = ("default", "fusion")
CANNOT_DRAW_DARK: frozenset[str] = frozenset({"windowsvista", "windows"})  # the classic Windows styles ignore the dark scheme
FUSION: str = "Fusion"

_SCHEME_OF: dict[str, Qt.ColorScheme] = {"system": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}


@dataclass
class _State:
    platform_style: str | None = None  # the style the platform chose, before we changed anything
    style_given: bool = False  # -style on the command line (or QT_STYLE_OVERRIDE): then the style is not ours to choose
    settings: QSettings | None = None
    busy: bool = False


_state = _State()


LIGHT_PAGES_FLAG: str = "--blink-settings=preferredColorScheme=1"  # 1 is light: mail is written for a white page


def keep_web_pages_light() -> None:
    """Tell the web engine that web pages are always shown in the light scheme, so that a mail with styles for dark screens
    does not put dark text on the white page. Must be done before the application is made."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if LIGHT_PAGES_FLAG not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} {LIGHT_PAGES_FLAG}".strip()


def style_given(arguments: Sequence[str]) -> bool:
    """True when the user chose the style on the command line or in the environment."""
    return any(argument in ("-style", "--style") or argument.startswith(("-style=", "--style=")) for argument in arguments) or bool(os.environ.get("QT_STYLE_OVERRIDE"))


def style_to_use(choice: str, dark: bool, platform_style: str) -> str:
    """The style that gives what was asked for: Fusion when it is chosen or when the platform's own style cannot draw dark."""
    if choice == "fusion" or (dark and platform_style.lower() in CANNOT_DRAW_DARK):
        return FUSION
    return platform_style


def _colour_the_menus(app: QApplication) -> None:
    """Dropdown menus are drawn in the 'base' colour, the colour of the lists, so they run into the folder list. Give them the colour of the menu bar."""
    # The stub of setPalette says bytes for the class name, but the call wants a str.
    class_name = cast("bytes", "QMenu")
    if app.style().objectName().lower() == FUSION.lower():
        app.setPalette(menu_palette(app.palette()), class_name)
    else:
        app.setPalette(QPalette(), class_name)  # the other styles draw their menus well themselves


def start(app: QApplication, arguments: Sequence[str], settings: QSettings) -> None:
    """Remember what the platform chose, apply the settings, and follow the system when it switches between light and dark."""
    _state.platform_style = app.style().objectName()
    _state.style_given = style_given(arguments)
    _state.settings = settings

    def changed(scheme: Qt.ColorScheme) -> None:
        apply(app, settings)

    app.styleHints().colorSchemeChanged.connect(changed)
    apply(app, settings)


def apply(app: QApplication, settings: QSettings) -> None:
    """Put the colour scheme and the style of the settings in use, at once."""
    if _state.busy:
        return
    _state.busy = True
    try:
        if _state.platform_style is None:  # started without start(), as in tests
            _state.platform_style = app.style().objectName()
        scheme = prefs.get_text(settings, "color_scheme", SCHEMES)
        app.styleHints().setColorScheme(_SCHEME_OF[scheme])  # for the parts the platform draws itself (title bars) and for web pages
        dark = (app.styleHints().colorScheme() == Qt.ColorScheme.Dark) if scheme == "system" else scheme == "dark"
        if not _state.style_given:
            wanted = style_to_use(prefs.get_text(settings, "style", STYLES), dark, _state.platform_style)
            if app.style().objectName().lower() != wanted.lower():
                app.setStyle(wanted)
        # A platform does not always do what it is asked (Linux desktops mostly do not), so a chosen scheme brings its own colours.
        app.setPalette(QPalette() if scheme == "system" else (dark_palette() if dark else light_palette()))
        _colour_the_menus(app)
    finally:
        _state.busy = False
