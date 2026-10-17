import json

from PySide6.QtCore import QSettings

# --- Version ------------------------------------------------------------
__version__ = "prefs.py 1.1.172"

# --- Version history ----------------------------------------------------
# v1.1.172: The setting "style" is gone (Fusion always, D-158); a saved value is ignored
# v0.5.145: The three settings of the Operations window (show on send and receive, hide when it completes, show on an error)
# v0.5.144: windows_scale, the size of the other windows and dialogs
# v0.5.135: show_tags, tag_text_colour
# v0.3.108: list_scale replaces list_font_size; reading_scale (a factor, for reading and writing) replaces reader_zoom (percent) and compose_font_size (points)
# v0.3.106: interface_scale (a factor) replaces ui_font_size (points)
# v0.0.82: Text size of the interface (8 to 48 pt, the system size at the first start) under Appearance; the Menu button uses the three-bar character U+2630
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.42: Initial preferences (the keys of the settings file and their standard values)

DEFAULTS: dict[str, object] = {
    # general
    "check_on_start": True,
    "auto_check": True,
    "auto_check_minutes": 5,
    "notifications": True,
    "tray": True,
    "close_to_tray": False,
    "operations_show_on_send_receive": True,  # the Operations window opens when a send and receive starts
    "operations_hide_when_done": True,  # it closes itself when everything is done (not after an error while operations_show_on_error is on)
    "operations_show_on_error": True,  # it opens for an error, and stays open after one
    # appearance
    "color_scheme": "system",  # system, light or dark
    "interface_scale": 1.0,  # the size of the main window: a factor of the system's size (scale.py); an older ui_font_size in points is converted
    "windows_scale": 1.0,  # the size of the other windows and dialogs: a factor (scale.py); when missing, the size of the main window (they shared it before)
    # message list
    "list_layout": "columns",
    "reading_pane": "right",
    "date_style": "short",
    "list_density": "normal",
    "list_scale": 1.0,  # the size of the message list: a factor (scale.py); an older list_font_size in points is converted
    "conversations": False,
    "show_favorites": True,
    "show_local_folders": True,
    "show_tags": True,  # the Tags at the bottom of the folder list
    "tag_text_colour": False,  # the text of a tagged message in the colour of its first tag
    # reading
    "mark_read": "delay",  # delay: after a moment, never: only when I mark it, window: only when opened in a window of its own
    "mark_read_delay_ms": 700,
    "reading_scale": 1.0,  # the size of the text of messages: a factor (scale.py); an older reader_zoom in percent is converted
    "prefer_plain": False,
    "remote_always": False,
    "remote_senders": "",
    "links_ask": True,
    # composing
    "default_account": "",
    "compose_html": True,
    "reply_quote": "above",  # above: my text above the quote, below: under it, none: no quote
    "autosave_seconds": 30,
    "warn_no_subject": True,
}
MARK_READ: tuple[str, ...] = ("delay", "never", "window")
REPLY_QUOTES: tuple[str, ...] = ("above", "below", "none")
DENSITIES: tuple[str, ...] = ("compact", "normal", "roomy")


def get_bool(settings: QSettings, key: str) -> bool:
    """QSettings may hand back the text 'true' or 'false' from an ini file."""
    value = settings.value(key, DEFAULTS[key])
    return value if isinstance(value, bool) else str(value).lower() == "true"


def get_int(settings: QSettings, key: str, low: int, high: int) -> int:
    try:
        value = int(str(settings.value(key, DEFAULTS[key])))
    except ValueError:
        value = int(str(DEFAULTS[key]))
    return max(low, min(high, value))


def get_text(settings: QSettings, key: str, allowed: tuple[str, ...] | None = None) -> str:
    value = str(settings.value(key, DEFAULTS[key]))
    if allowed is not None and value not in allowed:
        return str(DEFAULTS[key])
    return value


def allowed_senders(settings: QSettings) -> set[str]:
    """The addresses whose pictures and other remote content are shown without asking (lower case)."""
    return {line.strip().lower() for line in str(settings.value("remote_senders", "")).splitlines() if "@" in line}


def shortcuts(settings: QSettings) -> dict[str, str]:
    """The keys the person chose: action name -> key sequence text ('' for none)."""
    try:
        data = json.loads(str(settings.value("shortcuts", "{}")))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str) and len(value) <= 40}
