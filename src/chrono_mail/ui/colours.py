from PySide6.QtGui import QColor, QGuiApplication, QPalette

from chrono_mail.core.colours import DARK, LIGHT, ROLES, Theme, with_accent

# --- Version ------------------------------------------------------------
__version__ = "colours.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.182: Each theme has its own accent colour (set_accents, theme_of, D-164)
# v1.1.172: No colour of its own any more: the colours are in core/colours.py (the one colour file); this module makes Qt palettes from a theme and gives the colours of the theme in use (D-158)
# v0.5.149: The program's own blue (BLUE_ON_LIGHT, BLUE_ON_DARK) in one place; with_program_blue puts it in a palette for every window state
# v0.2.95: The background of a group header in the message list
# v0.0.88: Grey of a selection in a list without the focus; black separator lines
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.51: Initial colours for messages and marks that suit both a light and a dark scheme

GROUPS: tuple[QPalette.ColorGroup, ...] = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled)


def palette_of(theme: Theme) -> QPalette:
    """The whole palette of a theme: every role in every window state, nothing left to the platform."""
    palette = QPalette()
    for name in ROLES:
        role = getattr(QPalette.ColorRole, name)  # getattr: Qt's colour roles named in the theme table
        for group, value in zip(GROUPS, theme.roles[name], strict=True):
            palette.setColor(group, role, QColor(value))
    return palette


def menu_palette_of(theme: Theme) -> QPalette:
    """The palette for dropdown menus: Fusion draws a menu 8% lighter than its list colour, so it is given the theme's
    menu colour, and the menus come out in the colour of the menu bar."""
    menu = palette_of(theme)
    for group in GROUPS:
        menu.setColor(group, QPalette.ColorRole.Base, QColor(theme.menu_base))
        menu.setColor(group, QPalette.ColorRole.AlternateBase, QColor(theme.menu_base))
    return menu


def is_dark(palette: QPalette | None = None) -> bool:
    """True when the palette has light text on a dark window."""
    current = palette if palette is not None else QGuiApplication.palette()
    return current.color(QPalette.ColorRole.Window).lightnessF() < current.color(QPalette.ColorRole.WindowText).lightnessF()


_accents: dict[bool, str] = {}  # dark or not: the accent the person picked for that theme ("" or missing: its blue)


def set_accents(dark: str, light: str) -> None:
    """The accent colours of the dark and the light theme, from the settings ("": the theme's own blue)."""
    _accents[True] = dark
    _accents[False] = light


def theme_of(dark: bool) -> Theme:
    """The dark or the light theme, with the accent the person picked for it."""
    return with_accent(DARK if dark else LIGHT, _accents.get(dark, ""))


def theme(palette: QPalette | None = None) -> Theme:
    """The theme in use (the one whose palette the program has), or the one that suits the palette given."""
    return theme_of(is_dark(palette))


def red(palette: QPalette | None = None) -> str:
    """The colour of errors and warnings in text, readable on the window of the theme in use."""
    return theme(palette).red


def green(palette: QPalette | None = None) -> str:
    """The colour of 'it worked' marks."""
    return theme(palette).green


def selection_grey(palette: QPalette | None = None) -> str:
    return theme(palette).selection_grey


def group_header_colour(palette: QPalette | None = None) -> str:
    return theme(palette).group_header


def red_style(palette: QPalette | None = None) -> str:
    return f"color:{red(palette)}"


def green_style(palette: QPalette | None = None) -> str:
    return f"color:{green(palette)}"


def css(value: str) -> str:
    """A colour of the colour file as a style sheet writes it ("#aarrggbb" becomes rgba(), which style sheets understand)."""
    colour = QColor(value)
    if colour.alpha() == 255:
        return colour.name()
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {colour.alphaF():.2f})"


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: QColor) -> float:
    return 0.2126 * _channel(colour.redF()) + 0.7152 * _channel(colour.greenF()) + 0.0722 * _channel(colour.blueF())


def contrast(first: QColor, second: QColor) -> float:
    """The WCAG contrast ratio of two colours: 4.5 is needed for text, 3 for large text and marks."""
    one, two = luminance(first), luminance(second)
    high, low = max(one, two), min(one, two)
    return (high + 0.05) / (low + 0.05)
