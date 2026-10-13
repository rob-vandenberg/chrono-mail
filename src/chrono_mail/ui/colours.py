from PySide6.QtGui import QColor, QGuiApplication, QPalette

# --- Version ------------------------------------------------------------
__version__ = "colours.py 0.0.88"

# --- Version history ----------------------------------------------------
# v0.0.88: Grey of a selection in a list without the focus; black separator lines
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.51: Initial colours for messages and marks that suit both a light and a dark scheme

RED_ON_LIGHT: str = "#c0392b"
RED_ON_DARK: str = "#ff8077"
GREEN_ON_LIGHT: str = "#17703c"
GREEN_ON_DARK: str = "#4cc26b"
SELECTION_GREY_ON_LIGHT: str = "#d6d6d6"  # a selection in a folder list or message list that does not have the focus
SELECTION_GREY_ON_DARK: str = "#4a4a4a"
SEPARATOR: str = "#000000"  # the thin lines between the panes and between the column headers, in both schemes


def _palette(window: str, base: str, text: str, button: str, highlight: str, highlighted_text: str, link: str, disabled: str, shades: tuple[str, str, str, str]) -> QPalette:
    palette = QPalette()
    roles = QPalette.ColorRole
    light, midlight, mid, dark = shades
    for role, value in (
        (roles.Window, window), (roles.WindowText, text), (roles.Base, base), (roles.AlternateBase, window), (roles.Text, text), (roles.Button, button),
        (roles.ButtonText, text), (roles.BrightText, "#ff4040"), (roles.ToolTipBase, base), (roles.ToolTipText, text), (roles.PlaceholderText, disabled),
        (roles.Highlight, highlight), (roles.HighlightedText, highlighted_text), (roles.Link, link), (roles.LinkVisited, link),
        (roles.Light, light), (roles.Midlight, midlight), (roles.Mid, mid), (roles.Dark, dark), (roles.Shadow, "#000000"),
    ):
        palette.setColor(role, QColor(value))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled))
    return palette


def dark_palette() -> QPalette:
    """The dark colours, for when the platform cannot be asked for them."""
    return _palette("#2d2d2d", "#1e1e1e", "#e8e8e8", "#3a3a3a", "#2a6fc4", "#ffffff", "#6cb0ff", "#8a8a8a", ("#505050", "#444444", "#6a6a6a", "#1a1a1a"))


def light_palette() -> QPalette:
    """The light colours, for when the platform cannot be asked for them."""
    return _palette("#efefef", "#ffffff", "#1a1a1a", "#e4e4e4", "#2f7fcf", "#ffffff", "#1a5fb4", "#8a8a8a", ("#ffffff", "#f5f5f5", "#b8b8b8", "#9a9a9a"))


FUSION_MENU_LIGHTENING: int = 108  # Fusion draws a menu 8% lighter than the 'base' colour it is given (measured; a test guards it)


def menu_palette(palette: QPalette) -> QPalette:
    """The palette for dropdown menus: they come out in the colour of the menu bar (the window colour), where they would
    otherwise be the colour of the lists."""
    menu = QPalette(palette)
    base = palette.color(QPalette.ColorRole.Window).darker(FUSION_MENU_LIGHTENING)
    menu.setColor(QPalette.ColorRole.Base, base)
    menu.setColor(QPalette.ColorRole.AlternateBase, base)
    return menu


def is_dark(palette: QPalette | None = None) -> bool:
    """True when the palette has light text on a dark window (the dark scheme, whatever the style)."""
    current = palette if palette is not None else QGuiApplication.palette()
    return current.color(QPalette.ColorRole.Window).lightnessF() < current.color(QPalette.ColorRole.WindowText).lightnessF()


def red(palette: QPalette | None = None) -> str:
    """The colour of errors and warnings in text, readable on the window of the scheme that is in use."""
    return RED_ON_DARK if is_dark(palette) else RED_ON_LIGHT


def green(palette: QPalette | None = None) -> str:
    """The colour of 'it worked' marks."""
    return GREEN_ON_DARK if is_dark(palette) else GREEN_ON_LIGHT


def selection_grey(palette: QPalette | None = None) -> str:
    return SELECTION_GREY_ON_DARK if is_dark(palette) else SELECTION_GREY_ON_LIGHT


def red_style(palette: QPalette | None = None) -> str:
    return f"color:{red(palette)}"


def green_style(palette: QPalette | None = None) -> str:
    return f"color:{green(palette)}"


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: QColor) -> float:
    return 0.2126 * _channel(colour.redF()) + 0.7152 * _channel(colour.greenF()) + 0.0722 * _channel(colour.blueF())


def contrast(first: QColor, second: QColor) -> float:
    """The WCAG contrast ratio of two colours: 4.5 is needed for text, 3 for large text and marks."""
    one, two = luminance(first), luminance(second)
    high, low = max(one, two), min(one, two)
    return (high + 0.05) / (low + 0.05)
