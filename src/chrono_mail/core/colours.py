import re
from dataclasses import dataclass, replace

# --- Version ------------------------------------------------------------
__version__ = "colours.py 1.1.219"

# --- Version history ----------------------------------------------------
# v1.1.219: The colours a new calendar suggests (Google Calendar's eleven event colours) and the CSS colour names of RFC 7986 COLOR (D-191)
# v1.1.215: pane_separator and pane_separator_dots: the one separator between the panes (D-188)
# v1.1.191: One blue for both themes (#2a6fc4, the owner's choice); the text on the accent is white unless white's contrast is below 3:1
# v1.1.182: The accent colour: each theme's blue can be replaced by a colour the person picks (with_accent, D-164)
# v1.1.172: Initial: every colour of the program in one file: the dark and the light theme, and the colours that are not part of a theme (D-158)

# THE COLOUR FILE. Every colour of Chrono Mail is written here and nowhere else (a test fails when a colour is written in
# any other file). To change a colour, change it here. Colours are written as "#rrggbb", or "#aarrggbb" when they are
# partly see-through (aa: 00 is invisible, ff is solid).
#
# The colours a person chooses (the colour of a tag, of a calendar) are theirs: they are kept with their settings and are
# not in this file. Only the colours such things start with are here (at the end of the file).


# --- The roles of a theme -------------------------------------------------------------------------------------------------
# Qt draws every window, list, button and menu from these roles. Each role has three colours: for a window that is active,
# for one that is not, and for something greyed out (disabled), in that order.

ROLES: tuple[str, ...] = (
    "Window", "WindowText", "Base", "AlternateBase", "Text", "Button", "ButtonText", "BrightText", "Light", "Midlight",
    "Mid", "Dark", "Shadow", "Highlight", "HighlightedText", "Link", "LinkVisited", "ToolTipBase", "ToolTipText",
    "PlaceholderText", "Accent", "NoRole",
)


@dataclass(frozen=True, slots=True)
class Theme:
    """One complete set of colours. Every field is set in both themes, so nothing is ever taken from the system."""

    name: str
    dark: bool
    roles: dict[str, tuple[str, str, str]]  # role: (active, inactive, disabled)
    blue: str  # the program's own blue: the selection with the focus, the Menu button, the unread dot, bars, links
    on_blue: str  # text and marks on the blue
    selection_grey: str  # a selection in the folder list or the message list without the focus
    group_header: str  # the bar of a group heading in the message list ("Today", "Yesterday")
    group_header_line: str  # the thin line under a group heading
    separator: str  # the thin lines between the column headers
    pane_separator: str  # the bar between the panes (D-188): the window's colour, as Qt drew it
    pane_separator_dots: str  # the dots in the middle of that bar
    hover: str  # the veil under the pointer on toolbar buttons, folders and rows
    hover_strong: str  # the same on the banner's buttons
    menu_base: str  # what menus are given as their list colour: Fusion draws a menu 8% lighter, so this is the window colour 8% darker
    search_active: str  # the border of the search field while a search is active
    red: str  # errors and warnings in text
    green: str  # "it worked" marks
    amber: str  # a task to do
    task_grey: str  # a cancelled task
    now_line: str  # the line of the present moment in the calendar
    on_colour: str  # marks and initials on a coloured circle (avatars, tasks, calendar swatches)
    swatch_selected: str  # the ring around a calendar colour swatch while it is selected
    drag_badge: str  # the "N messages" badge while messages are dragged
    chip_text_light: str  # text on a dark tag colour
    chip_text_dark: str  # text on a light tag colour


# --- The dark theme -------------------------------------------------------------------------------------------------------
# The colours the program showed on Linux (KDE Breeze Dark) up to 1.0.171, with the program's own blue.

DARK: Theme = Theme(
    name="dark",
    dark=True,
    roles={
        "Window": ("#202326", "#202326", "#292c30"),
        "WindowText": ("#fcfcfc", "#fcfcfc", "#525860"),
        "Base": ("#141618", "#141618", "#292c30"),  # the message list, the other lists, text fields
        "AlternateBase": ("#1d1f22", "#1d1f22", "#1d1f22"),
        "Text": ("#fcfcfc", "#fcfcfc", "#525860"),
        "Button": ("#292c30", "#292c30", "#292c30"),
        "ButtonText": ("#fcfcfc", "#fcfcfc", "#525860"),
        "BrightText": ("#4b4b4b", "#4b4b4b", "#ffffff"),
        "Light": ("#151618", "#151618", "#151618"),
        "Midlight": ("#1f2124", "#1f2124", "#1f2124"),
        "Mid": ("#373b40", "#373b40", "#373b40"),
        "Dark": ("#525860", "#525860", "#525860"),
        "Shadow": ("#191919", "#191919", "#252525"),
        "Highlight": ("#2a6fc4", "#2a6fc4", "#2a6fc4"),  # the program's blue
        "HighlightedText": ("#ffffff", "#ffffff", "#ffffff"),
        "Link": ("#1d99f3", "#1d99f3", "#1d99f3"),
        "LinkVisited": ("#9b59b6", "#9b59b6", "#9b59b6"),
        "ToolTipBase": ("#292c30", "#292c30", "#292c30"),
        "ToolTipText": ("#fcfcfc", "#fcfcfc", "#fcfcfc"),
        "PlaceholderText": ("#a1a9b1", "#a1a9b1", "#a1a9b1"),
        "Accent": ("#308cc6", "#308cc6", "#919191"),
        "NoRole": ("#000000", "#000000", "#000000"),
    },
    blue="#2a6fc4",
    on_blue="#ffffff",
    selection_grey="#4a4a4a",
    group_header="#2a2a2a",
    group_header_line="#3a3d42",
    separator="#000000",
    pane_separator="#202326",
    pane_separator_dots="#6f7173",
    hover="#38808080",
    hover_strong="#66808080",
    menu_base="#1e2023",
    search_active="#f0a030",
    red="#ff8077",
    green="#4cc26b",
    amber="#e0a000",
    task_grey="#8a8a8a",
    now_line="#d93025",
    on_colour="#ffffff",
    swatch_selected="#000000",
    drag_badge="#e6285aa0",
    chip_text_light="#ffffff",
    chip_text_dark="#000000",
)


# --- The light theme ------------------------------------------------------------------------------------------------------
# White, as in the owner's reference screenshot of 2026-10-17, with the program's own blue.

LIGHT: Theme = Theme(
    name="light",
    dark=False,
    roles={
        "Window": ("#ffffff", "#ffffff", "#f5f5f5"),  # the toolbar, the sidebar, dialogs
        "WindowText": ("#1e1e1e", "#1e1e1e", "#a0a0a0"),
        "Base": ("#ffffff", "#ffffff", "#f5f5f5"),  # the message list, the other lists, text fields
        "AlternateBase": ("#f7f7f7", "#f7f7f7", "#f7f7f7"),
        "Text": ("#1e1e1e", "#1e1e1e", "#a0a0a0"),
        "Button": ("#f5f5f5", "#f5f5f5", "#f5f5f5"),
        "ButtonText": ("#1e1e1e", "#1e1e1e", "#a0a0a0"),
        "BrightText": ("#ffffff", "#ffffff", "#ffffff"),
        "Light": ("#ffffff", "#ffffff", "#ffffff"),
        "Midlight": ("#f5f5f5", "#f5f5f5", "#f5f5f5"),
        "Mid": ("#c8c8c8", "#c8c8c8", "#c8c8c8"),
        "Dark": ("#a0a0a0", "#a0a0a0", "#a0a0a0"),
        "Shadow": ("#787878", "#787878", "#787878"),
        "Highlight": ("#2a6fc4", "#2a6fc4", "#2a6fc4"),  # the program's blue
        "HighlightedText": ("#ffffff", "#ffffff", "#ffffff"),
        "Link": ("#2a6fc4", "#2a6fc4", "#2a6fc4"),
        "LinkVisited": ("#2a6fc4", "#2a6fc4", "#2a6fc4"),
        "ToolTipBase": ("#ffffff", "#ffffff", "#ffffff"),
        "ToolTipText": ("#1e1e1e", "#1e1e1e", "#1e1e1e"),
        "PlaceholderText": ("#8e8e8e", "#8e8e8e", "#8e8e8e"),
        "Accent": ("#2a6fc4", "#2a6fc4", "#a0a0a0"),
        "NoRole": ("#000000", "#000000", "#000000"),
    },
    blue="#2a6fc4",  # the same blue as the dark theme's: the owner's choice (2026-10-20), not a rule
    on_blue="#ffffff",
    selection_grey="#ebebeb",
    group_header="#e6e6e6",
    group_header_line="#d0d0d0",
    separator="#000000",
    pane_separator="#ffffff",
    pane_separator_dots="#c3c3c3",
    hover="#38808080",
    hover_strong="#66808080",
    menu_base="#ececec",
    search_active="#f0a030",
    red="#c0392b",
    green="#17703c",
    amber="#e0a000",
    task_grey="#8a8a8a",
    now_line="#d93025",
    on_colour="#ffffff",
    swatch_selected="#000000",
    drag_badge="#e6285aa0",
    chip_text_light="#ffffff",
    chip_text_dark="#000000",
)


THEMES: dict[str, Theme] = {DARK.name: DARK, LIGHT.name: LIGHT}


# --- The accent colour ----------------------------------------------------------------------------------------------------
# Each theme has its own accent: its blue above, unless the person picked another one for that theme (Settings >
# Appearance > Accent colour). The picked colour replaces the blue everywhere the blue is used; text and marks on it are
# white or black, whichever reads better on it.

ON_ACCENT_LIGHT: str = "#ffffff"  # text on a dark accent
ON_ACCENT_DARK: str = "#000000"  # text on a light accent
_ACCENT_FORM = re.compile(r"#[0-9a-fA-F]{6}")


def accent_value(text: str) -> str | None:
    """The colour written as #RRGGBB (lower case), or None when the text is not such a colour."""
    value = text.strip()
    return value.lower() if _ACCENT_FORM.fullmatch(value) else None


def _channel(value: int) -> float:
    part = value / 255
    return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    red, green, blue = (int(colour[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


WHITE_TEXT_LEAST_CONTRAST: float = 3.0  # below this contrast of white on the accent, the text on it is black (3:1: large text and marks)


def text_on_accent(accent: str) -> str:
    """White, as on the program's blue; black only when white is hard to read on the accent (a very light colour)."""
    on_white = 1.05 / (_luminance(accent) + 0.05)
    return ON_ACCENT_LIGHT if on_white >= WHITE_TEXT_LEAST_CONTRAST else ON_ACCENT_DARK


def with_accent(theme: Theme, accent: str) -> Theme:
    """The theme with the accent in place of its blue: the blue itself, every role that has the blue, the Accent role
    (greyed out it stays grey) and the text on the blue. An accent that is not #rrggbb leaves the theme as it is."""
    value = accent_value(accent)
    if value is None or value == theme.blue:
        return theme
    on = text_on_accent(value)
    roles = {name: tuple(value if colour == theme.blue else colour for colour in colours) for name, colours in theme.roles.items()}
    roles["Accent"] = (value, value, theme.roles["Accent"][2])
    roles["HighlightedText"] = (on, on, on)
    return replace(theme, roles={name: (colours[0], colours[1], colours[2]) for name, colours in roles.items()}, blue=value, on_blue=on)


# --- Colours that are not part of a theme ---------------------------------------------------------------------------------

MAIL_PAGE: str = "#ffffff"  # the page under a mail: mail is written for a white page, whatever the theme
NEW_TAG: str = "#808080"  # a tag made without a colour (by a rule, the Tag menu, an import)
NEW_TAG_IN_TAGS_WINDOW: str = "#3b82f6"  # what Add in the Tags window offers
NEW_CALENDAR: str = "#3b82f6"  # an event whose calendar has no colour
COLLECTION_WITHOUT_COLOUR: str = "#7f8c8d"  # a calendar, task list or address book without a colour, in the sidebar
FIRST_CALENDAR: str = "#3b82f6"  # the calendar, task list and address book made the first time
FIRST_TASK_LIST: str = "#f59e0b"
FIRST_ADDRESS_BOOK: str = "#10b981"

# The circle of a sender (avatar): one of AVATAR_HUES hues, picked from the address, at this saturation and lightness
# (white initials on it keep a contrast of 4.5 or more on every hue).
AVATAR_HUES: int = 72
AVATAR_SATURATION: float = 0.55
AVATAR_LIGHTNESS: float = 0.30

# --- The colours of appointments (D-191) ------------------------------------------------------------------------------------

# What every calendar suggests in its palette at first, after its own colour: Google Calendar's eleven event colours under their names, in the
# order its colour list shows them (the "modern" values of Google's colours API). Each keeps the id Google gives it, so a palette that was never
# changed is the same every time it is read.
SUGGESTED_EVENT_COLOURS: tuple[tuple[str, str, str], ...] = (
    ("g11", "Tomato", "#d50000"), ("g4", "Flamingo", "#e67c73"), ("g6", "Tangerine", "#f4511e"), ("g5", "Banana", "#f6bf26"), ("g2", "Sage", "#33b679"),
    ("g10", "Basil", "#0b8043"), ("g7", "Peacock", "#039be5"), ("g9", "Blueberry", "#3f51b5"), ("g1", "Lavender", "#7986cb"), ("g3", "Grape", "#8e24aa"),
    ("g8", "Graphite", "#616161"),
)

# The CSS colour names an appointment's COLOR property (RFC 7986) is written in, so that other programs show something close.
CSS_COLOURS: dict[str, str] = {
    "aliceblue": "#f0f8ff", "antiquewhite": "#faebd7", "aqua": "#00ffff", "aquamarine": "#7fffd4", "azure": "#f0ffff", "beige": "#f5f5dc",
    "bisque": "#ffe4c4", "black": "#000000", "blanchedalmond": "#ffebcd", "blue": "#0000ff", "blueviolet": "#8a2be2", "brown": "#a52a2a",
    "burlywood": "#deb887", "cadetblue": "#5f9ea0", "chartreuse": "#7fff00", "chocolate": "#d2691e", "coral": "#ff7f50", "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc", "crimson": "#dc143c", "cyan": "#00ffff", "darkblue": "#00008b", "darkcyan": "#008b8b", "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9", "darkgreen": "#006400", "darkgrey": "#a9a9a9", "darkkhaki": "#bdb76b", "darkmagenta": "#8b008b",
    "darkolivegreen": "#556b2f", "darkorange": "#ff8c00", "darkorchid": "#9932cc", "darkred": "#8b0000", "darksalmon": "#e9967a",
    "darkseagreen": "#8fbc8f", "darkslateblue": "#483d8b", "darkslategray": "#2f4f4f", "darkslategrey": "#2f4f4f", "darkturquoise": "#00ced1",
    "darkviolet": "#9400d3", "deeppink": "#ff1493", "deepskyblue": "#00bfff", "dimgray": "#696969", "dimgrey": "#696969", "dodgerblue": "#1e90ff",
    "firebrick": "#b22222", "floralwhite": "#fffaf0", "forestgreen": "#228b22", "fuchsia": "#ff00ff", "gainsboro": "#dcdcdc",
    "ghostwhite": "#f8f8ff", "gold": "#ffd700", "goldenrod": "#daa520", "gray": "#808080", "green": "#008000", "greenyellow": "#adff2f",
    "grey": "#808080", "honeydew": "#f0fff0", "hotpink": "#ff69b4", "indianred": "#cd5c5c", "indigo": "#4b0082", "ivory": "#fffff0",
    "khaki": "#f0e68c", "lavender": "#e6e6fa", "lavenderblush": "#fff0f5", "lawngreen": "#7cfc00", "lemonchiffon": "#fffacd", "lightblue": "#add8e6",
    "lightcoral": "#f08080", "lightcyan": "#e0ffff", "lightgoldenrodyellow": "#fafad2", "lightgray": "#d3d3d3", "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3", "lightpink": "#ffb6c1", "lightsalmon": "#ffa07a", "lightseagreen": "#20b2aa", "lightskyblue": "#87cefa",
    "lightslategray": "#778899", "lightslategrey": "#778899", "lightsteelblue": "#b0c4de", "lightyellow": "#ffffe0", "lime": "#00ff00",
    "limegreen": "#32cd32", "linen": "#faf0e6", "magenta": "#ff00ff", "maroon": "#800000", "mediumaquamarine": "#66cdaa", "mediumblue": "#0000cd",
    "mediumorchid": "#ba55d3", "mediumpurple": "#9370db", "mediumseagreen": "#3cb371", "mediumslateblue": "#7b68ee", "mediumspringgreen": "#00fa9a",
    "mediumturquoise": "#48d1cc", "mediumvioletred": "#c71585", "midnightblue": "#191970", "mintcream": "#f5fffa", "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5", "navajowhite": "#ffdead", "navy": "#000080", "oldlace": "#fdf5e6", "olive": "#808000", "olivedrab": "#6b8e23",
    "orange": "#ffa500", "orangered": "#ff4500", "orchid": "#da70d6", "palegoldenrod": "#eee8aa", "palegreen": "#98fb98", "paleturquoise": "#afeeee",
    "palevioletred": "#db7093", "papayawhip": "#ffefd5", "peachpuff": "#ffdab9", "peru": "#cd853f", "pink": "#ffc0cb", "plum": "#dda0dd",
    "powderblue": "#b0e0e6", "purple": "#800080", "red": "#ff0000", "rosybrown": "#bc8f8f", "royalblue": "#4169e1", "saddlebrown": "#8b4513",
    "salmon": "#fa8072", "sandybrown": "#f4a460", "seagreen": "#2e8b57", "seashell": "#fff5ee", "sienna": "#a0522d", "silver": "#c0c0c0",
    "skyblue": "#87ceeb", "slateblue": "#6a5acd", "slategray": "#708090", "slategrey": "#708090", "snow": "#fffafa", "springgreen": "#00ff7f",
    "steelblue": "#4682b4", "tan": "#d2b48c", "teal": "#008080", "thistle": "#d8bfd8", "tomato": "#ff6347", "turquoise": "#40e0d0",
    "violet": "#ee82ee", "wheat": "#f5deb3", "white": "#ffffff", "whitesmoke": "#f5f5f5", "yellow": "#ffff00", "yellowgreen": "#9acd32",
}


def css_name_nearest(colour: str) -> str:
    """The CSS colour name closest to a colour written #rrggbb (by the distance of the red, green and blue parts)."""
    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))

    def distance(item: tuple[str, str]) -> int:
        value = item[1]
        return (int(value[1:3], 16) - red) ** 2 + (int(value[3:5], 16) - green) ** 2 + (int(value[5:7], 16) - blue) ** 2

    return min(CSS_COLOURS.items(), key=distance)[0]


def css_colour(name: str) -> str:
    """The colour (#rrggbb) of a CSS colour name, or empty for a name that is not one (or a value written #rrggbb, which is taken as it is)."""
    text = name.strip().lower()
    if len(text) == 7 and text.startswith("#") and all(character in "0123456789abcdef" for character in text[1:]):
        return text
    return CSS_COLOURS.get(text, "")
