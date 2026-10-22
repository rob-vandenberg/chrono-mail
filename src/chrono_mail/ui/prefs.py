import json
from dataclasses import dataclass, field, fields

from PySide6.QtCore import QSettings

from chrono_mail.core.colours import accent_value
from chrono_mail.ui.columns import valid_columns
from chrono_mail.ui.scale import STANDARD, clamp

# --- Version ------------------------------------------------------------
__version__ = "prefs.py 2.0.280"

# --- Version history ----------------------------------------------------
# v2.0.280: Columns per folder (B-16): the columns are no longer one setting of the Settings window; Remembered keeps the setup of each folder
#           and of each kind of folder (folder_columns, type_columns) and hands the one setup of earlier versions over once (earlier_setup)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.264: InUse: the settings a window has in use, read fresh and kept, shared by the parts of the main window (R-17)
# v1.2.253: Every setting is read and written here only (R-12): Preferences (what the Settings window edits, typed, each with its default,
#           its allowed values or limits, checked the same way at start and after OK), StartupView (Show on startup, read once), Remembered
#           (what the window keeps by itself: the last folder and section, the sort, the column layout, the open branches, the search
#           history, the pane sizes, Show groups); the settings file keeps its names and values
# v1.1.182: accent_dark and accent_light (D-164)
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
    "accent_dark": "",  # the accent colour of the dark theme, #rrggbb ("": the theme's blue, D-164)
    "accent_light": "",  # the same for the light theme
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
    "startup_view": "",  # Show on startup (StartupView): "" the last selected folder, "mail\taccount\tkind\tpath" or "section\tname"
    "shortcuts": "{}",  # the keys the person chose, JSON: action name -> key sequence text
}
COLOR_SCHEMES: tuple[str, ...] = ("system", "light", "dark")
LIST_LAYOUTS: tuple[str, ...] = ("columns", "cards")
READING_PANES: tuple[str, ...] = ("right", "bottom", "off")
DATE_STYLES: tuple[str, ...] = ("short", "full", "iso")
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



# --- the sizes: read with the conversions of the sizes older versions saved -------------------------------------------------------------

def saved_factor(settings: QSettings, system_points: float) -> float:
    """The factor in the settings; a text size in points saved by an older version becomes the factor that gives it."""
    if settings.contains("interface_scale"):
        try:
            return clamp(float(str(settings.value("interface_scale"))))
        except ValueError:
            return STANDARD
    if settings.contains("ui_font_size") and system_points > 0:
        try:
            return clamp(float(str(settings.value("ui_font_size"))) / system_points)
        except ValueError:
            return STANDARD
    return STANDARD


def _stored(settings: QSettings, key: str) -> float | None:
    if not settings.contains(key):
        return None
    try:
        return clamp(float(str(settings.value(key))))
    except ValueError:
        return STANDARD


def saved_windows(settings: QSettings, system_points: float) -> float:
    """The size of the other windows and dialogs; before it had a slider of its own they followed the interface, so that size."""
    stored = _stored(settings, "windows_scale")
    return stored if stored is not None else saved_factor(settings, system_points)


def saved_reading(settings: QSettings) -> float:
    """The size of the text of messages; a zoom in percent of an older version (reader_zoom) becomes the factor."""
    stored = _stored(settings, "reading_scale")
    if stored is not None:
        return stored
    if settings.contains("reader_zoom"):
        try:
            return clamp(float(str(settings.value("reader_zoom"))) / 100)
        except ValueError:
            return STANDARD
    return STANDARD


def saved_list(settings: QSettings, system_points: float) -> float:
    """The size of the message list; a text size in points of an older version (list_font_size, 0 the system's) becomes the factor."""
    stored = _stored(settings, "list_scale")
    if stored is not None:
        return stored
    if settings.contains("list_font_size") and system_points > 0:
        try:
            points = float(str(settings.value("list_font_size")))
        except ValueError:
            return STANDARD
        return clamp(points / system_points) if points > 0 else STANDARD
    return STANDARD


def _accent(value: object) -> str:
    """An accent colour as #rrggbb, or "" (the theme's blue) for anything else."""
    return accent_value(str(value or "")) or ""


def _factor(settings: QSettings, key: str) -> float:
    try:
        return float(str(settings.value(key, DEFAULTS[key])))
    except ValueError:
        return float(str(DEFAULTS[key]))


@dataclass(frozen=True, slots=True)
class StartupView:
    """What Show on startup opens (Settings > General), read once from its saved text."""

    kind: str = ""  # "": the last selected folder; "mail": an entry of the folder list; "section": Calendar, Contacts or Tasks
    account: str = ""  # mail: the entry's account ("" for Favorites and Tags)
    entry_kind: str = ""  # mail: folder, favorite, pinlink, tagview, account, unread, flagged …
    path: str = ""  # mail: the folder, favorite, pin's folder or tag
    section: str = ""  # section: its name

    @classmethod
    def parse(cls, text: str) -> "StartupView":
        parts = text.split("\t")
        if parts[0] == "mail" and len(parts) == 4:
            return cls("mail", parts[1], parts[2], parts[3])
        if parts[0] == "section" and len(parts) == 2 and parts[1]:
            return cls("section", section=parts[1])
        return cls()

    @property
    def text(self) -> str:
        if self.kind == "mail":
            return "\t".join(("mail", self.account, self.entry_kind, self.path))
        if self.kind == "section":
            return f"section\t{self.section}"
        return ""


@dataclass(frozen=True, slots=True)
class Preferences:
    """Every setting the Settings window edits, as the program uses it: typed, with its default, and checked the same way wherever it is
    read (a damaged or unknown value gives the default). Read with read_preferences, saved with save_preferences; the sizes are read with
    their conversions of older versions in scale.py and only saved from here."""

    check_on_start: bool = True
    auto_check: bool = True
    auto_check_minutes: int = 5
    notifications: bool = True
    tray: bool = True
    close_to_tray: bool = False
    operations_show_on_send_receive: bool = True
    operations_hide_when_done: bool = True
    operations_show_on_error: bool = True
    color_scheme: str = "system"
    accent_dark: str = ""
    accent_light: str = ""
    interface_scale: float = 1.0
    windows_scale: float = 1.0
    list_layout: str = "columns"
    reading_pane: str = "right"
    date_style: str = "short"
    list_density: str = "normal"
    list_scale: float = 1.0
    conversations: bool = False
    show_favorites: bool = True
    show_local_folders: bool = True
    show_tags: bool = True
    tag_text_colour: bool = False
    mark_read: str = "delay"
    mark_read_delay_ms: int = 700
    reading_scale: float = 1.0
    prefer_plain: bool = False
    remote_always: bool = False
    remote_senders: tuple[str, ...] = ()  # lower case, sorted
    links_ask: bool = True
    default_account: str = ""
    compose_html: bool = True
    reply_quote: str = "above"
    autosave_seconds: int = 30
    warn_no_subject: bool = True
    startup: StartupView = field(default_factory=StartupView)
    shortcuts: dict[str, str] = field(default_factory=dict[str, str])  # action name -> key sequence text ("" for none)


_LIMITS: dict[str, tuple[int, int]] = {"auto_check_minutes": (1, 240), "mark_read_delay_ms": (0, 10_000), "autosave_seconds": (0, 600)}
_CHOICES: dict[str, tuple[str, ...]] = {
    "color_scheme": COLOR_SCHEMES, "list_layout": LIST_LAYOUTS, "reading_pane": READING_PANES, "date_style": DATE_STYLES, "list_density": DENSITIES,
    "mark_read": MARK_READ, "reply_quote": REPLY_QUOTES,
}
_KEY_OF: dict[str, str] = {"startup": "startup_view"}  # the field and its name in the settings file, where they differ


def read_preferences(settings: QSettings) -> Preferences:
    """Every setting from the settings file, each checked as its field says (a damaged or unknown value gives its default)."""
    def choice(key: str) -> str:
        return get_text(settings, key, _CHOICES[key])

    def number(key: str) -> int:
        return get_int(settings, key, *_LIMITS[key])

    return Preferences(
        check_on_start=get_bool(settings, "check_on_start"),
        auto_check=get_bool(settings, "auto_check"),
        auto_check_minutes=number("auto_check_minutes"),
        notifications=get_bool(settings, "notifications"),
        tray=get_bool(settings, "tray"),
        close_to_tray=get_bool(settings, "close_to_tray"),
        operations_show_on_send_receive=get_bool(settings, "operations_show_on_send_receive"),
        operations_hide_when_done=get_bool(settings, "operations_hide_when_done"),
        operations_show_on_error=get_bool(settings, "operations_show_on_error"),
        color_scheme=choice("color_scheme"),
        accent_dark=_accent(settings.value("accent_dark", "")),
        accent_light=_accent(settings.value("accent_light", "")),
        interface_scale=_factor(settings, "interface_scale"),
        windows_scale=_factor(settings, "windows_scale"),
        list_layout=choice("list_layout"),
        reading_pane=choice("reading_pane"),
        date_style=choice("date_style"),
        list_density=choice("list_density"),
        list_scale=_factor(settings, "list_scale"),
        conversations=get_bool(settings, "conversations"),
        show_favorites=get_bool(settings, "show_favorites"),
        show_local_folders=get_bool(settings, "show_local_folders"),
        show_tags=get_bool(settings, "show_tags"),
        tag_text_colour=get_bool(settings, "tag_text_colour"),
        mark_read=choice("mark_read"),
        mark_read_delay_ms=number("mark_read_delay_ms"),
        reading_scale=_factor(settings, "reading_scale"),
        prefer_plain=get_bool(settings, "prefer_plain"),
        remote_always=get_bool(settings, "remote_always"),
        remote_senders=tuple(sorted(allowed_senders(settings))),
        links_ask=get_bool(settings, "links_ask"),
        default_account=str(settings.value("default_account", "") or ""),
        compose_html=get_bool(settings, "compose_html"),
        reply_quote=choice("reply_quote"),
        autosave_seconds=number("autosave_seconds"),
        warn_no_subject=get_bool(settings, "warn_no_subject"),
        startup=StartupView.parse(str(settings.value("startup_view", "") or "")),
        shortcuts=shortcuts(settings),
    )


def _saved_form(name: str, value: object) -> object:
    """A value as the settings file keeps it (the same names and forms as always)."""
    if isinstance(value, StartupView):
        return value.text
    if name == "remote_senders" and isinstance(value, tuple):
        return "\n".join(str(sender) for sender in value)
    if name == "shortcuts" and isinstance(value, dict):
        return json.dumps(value)
    return value


def save_preferences(settings: QSettings, chosen: Preferences) -> None:
    """Write the settings that differ from what the file gives now (nothing else is touched)."""
    now = read_preferences(settings)
    for item in fields(Preferences):
        value = getattr(chosen, item.name)  # getattr: every setting compared field by field
        if value != getattr(now, item.name):  # getattr: every setting compared field by field; a setting not in the file and left at its default stays out of it
            settings.setValue(_KEY_OF.get(item.name, item.name), _saved_form(item.name, value))


class InUse:
    """The settings as one window has them in use: read fresh from the file, and a change the window makes itself (a menu, the column
    header) saved and taken as in use at once; Settings OK compares the file with what is in use and applies what differs (R-13)."""

    def __init__(self, settings: QSettings) -> None:
        self.settings: QSettings = settings
        self.applied: Preferences = read_preferences(settings)

    def read(self) -> Preferences:
        """The settings as they are in the settings file now, each checked."""
        return read_preferences(self.settings)

    def keep(self, chosen: Preferences) -> None:
        """Save a change of the settings made in the window itself."""
        save_preferences(self.settings, chosen)
        self.applied = read_preferences(self.settings)

    def take_in(self) -> tuple[Preferences, Preferences]:
        """Settings OK: what was in use and what the file holds now, which is in use from now on."""
        before, self.applied = self.applied, read_preferences(self.settings)
        return before, self.applied


class Remembered:
    """What the main window keeps by itself between starts (not in the Settings window), each with its one default: read and written here
    only, under the names the settings file has always had."""

    def __init__(self, settings: QSettings) -> None:
        self._settings: QSettings = settings

    def _text(self, key: str, default: str = "") -> str:
        return str(self._settings.value(key, default) or default)

    def _flag(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        return value if isinstance(value, bool) else str(value).lower() == "true"

    @property
    def last_account(self) -> str:
        return self._text("last_account")

    @property
    def last_folder(self) -> str:
        return self._text("last_folder")

    @property
    def last_kind(self) -> str:
        return self._text("last_kind", "folder")

    def remember_view(self, account: str, folder: str, kind: str) -> None:
        self._settings.setValue("last_account", account)
        self._settings.setValue("last_folder", folder)
        self._settings.setValue("last_kind", kind)

    @property
    def last_section(self) -> str:
        return self._text("last_section")

    @last_section.setter
    def last_section(self, section: str) -> None:
        self._settings.setValue("last_section", section)

    @property
    def folder_columns(self) -> str:
        """The setup of every folder and view whose columns or sort the person changed (columnsetup.read_setups)."""
        return self._text("folder_columns")

    @folder_columns.setter
    def folder_columns(self, text: str) -> None:
        self._settings.setValue("folder_columns", text)

    @property
    def type_columns(self) -> str:
        """The setup each kind of folder starts with, where Apply to all folders set one (columnsetup.read_setups)."""
        return self._text("type_columns")

    @type_columns.setter
    def type_columns(self, text: str) -> None:
        self._settings.setValue("type_columns", text)

    def earlier_setup(self) -> tuple[tuple[str, ...], str, tuple[str, bool] | None] | None:
        """The one setup of versions before 2.0.280, once, then gone: the columns the person chose (() for none), the column
        layout ("" for none) and the sort ((sort, oldest first), None when none was saved). None when there is nothing to hand over."""
        names = ("columns", "column_layout", "sort", "oldest_first")
        if not any(self._settings.contains(name) for name in names):
            return None
        columns = [part for part in self._text("columns").split(",") if part]
        sort = (self._text("sort", "date"), self._flag("oldest_first", False)) if self._settings.contains("sort") else None
        found = (valid_columns(columns) if columns else (), self._text("column_layout"), sort)
        for name in names:
            self._settings.remove(name)
        return found

    @property
    def show_groups(self) -> bool:
        return self._flag("show_groups", True)

    @show_groups.setter
    def show_groups(self, on: bool) -> None:
        self._settings.setValue("show_groups", on)

    def column_added(self, which: str) -> bool:
        """Whether a column that later versions added (contact_and_delete, snoozed) was put into the person's columns already."""
        return self._flag(f"columns_{which}_added", False)

    def remember_column_added(self, which: str) -> None:
        self._settings.setValue(f"columns_{which}_added", True)

    @property
    def tree_state(self) -> dict[str, bool]:
        """Which headings, accounts and folders the person opened or closed in the folder list."""
        try:
            data = json.loads(self._text("tree_state", "{}"))
        except ValueError:
            return {}
        return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, bool)} if isinstance(data, dict) else {}

    @tree_state.setter
    def tree_state(self, state: dict[str, bool]) -> None:
        self._settings.setValue("tree_state", json.dumps(state))

    @property
    def search_history(self) -> str:
        return self._text("search_history")

    @search_history.setter
    def search_history(self, text: str) -> None:
        self._settings.setValue("search_history", text)

    def sizes(self, key: str) -> object:
        """The saved sizes of a pane splitter (splitter2, content_splitter), as Qt saved them, or None."""
        return self._settings.value(key)

    def remember_sizes(self, key: str, sizes: list[int]) -> None:
        self._settings.setValue(key, sizes)
