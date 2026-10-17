from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "colours.py 1.1.172"

# --- Version history ----------------------------------------------------
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
    separator: str  # the thin lines between the panes and between the column headers
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
        "Highlight": ("#2f7fcf", "#2f7fcf", "#2f7fcf"),  # the program's blue
        "HighlightedText": ("#ffffff", "#ffffff", "#ffffff"),
        "Link": ("#2f7fcf", "#2f7fcf", "#2f7fcf"),
        "LinkVisited": ("#2f7fcf", "#2f7fcf", "#2f7fcf"),
        "ToolTipBase": ("#ffffff", "#ffffff", "#ffffff"),
        "ToolTipText": ("#1e1e1e", "#1e1e1e", "#1e1e1e"),
        "PlaceholderText": ("#8e8e8e", "#8e8e8e", "#8e8e8e"),
        "Accent": ("#2f7fcf", "#2f7fcf", "#a0a0a0"),
        "NoRole": ("#000000", "#000000", "#000000"),
    },
    blue="#2f7fcf",
    on_blue="#ffffff",
    selection_grey="#ebebeb",
    group_header="#e6e6e6",
    group_header_line="#d0d0d0",
    separator="#000000",
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
