from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpacerItem,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_pim import TagInfo
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, ACCOUNT_REMOVE, KEY_STATUS, AccountParameters, AccountRemoveResult, AccountSummary, KeyStatus, NoParameters
from chrono_mail.engine.commands_folders import INDEX_RECONCILE, STORE_VERIFY, ReconcileResult, VerifyResult
from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.prefs import Preferences, StartupView, read_preferences, save_preferences
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import DEFAULT_COLUMNS, keep_snoozed_column
from chrono_mail.ui.columnsdialog import COLUMNS_TEXT, DATE_STYLES, DENSITY_LABELS, LAYOUTS, READING_PANES, ColumnPicker
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.rulespanel import RulesPanel
from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.sidebarpage import SidebarPage
from chrono_mail.ui.startupchooser import StartupChooser
from chrono_mail.ui.tagsdialog import TagsPanel
from chrono_mail.core.colours import DARK, LIGHT, text_on_accent
from chrono_mail.ui.colours import is_dark, red

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 2.2.314"

# --- Version history ----------------------------------------------------
# v2.2.314: General: Scheduled refresh (Send and Receive, or Receive only), the two delays of sending, the warning on Quit (B-21, B-20)
# v2.0.281: Rebuild the search index goes to the main window (rebuild_requested): one place after the other with the rebuild window, while the
#           old indexes stay in use (D-229)
# v2.0.280: Message list page: the columns are those of the folder open in the main window (SettingsContext.columns, chosen_columns, B-16)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.271: R-19: every engine call typed; the accounts are AccountSummary; the maintenance report lines per job
# v1.3.255: What the window has to tell this window comes in one typed piece, SettingsContext, instead of 16 loose values (R-13)
# v1.2.253: The settings are read once when the window opens (Preferences) and OK hands them back typed (chosen(), saved by save_preferences);
#           no setting is read here by its name any more (R-12)
# v1.1.229: Sync conflicts left Settings for a window of its own (the owner: it is not a setting); the page order is that of 1.1.225 again
# v1.1.226: The Sync conflicts page, after Scheduled messages (D-194)
# v1.1.214: Nothing of the account editor is saved on Cancel any more: no synchronisation after it
# v1.1.212: Accounts of calendars and contacts only: listed with their server, never a default account, no storage check (D-185)
# v1.1.211: Settings > Accounts names an account by its account name when it has one (D-184)
# v1.1.210: Calendars kept in step from the account editor are taken into use also when that editor is cancelled
# v1.1.204: The standard folders of Local Folders and which are hidden, for Settings > Sidebar
# v1.1.195: Show on startup choices carry whether the folder list shows them (hidden ones are greyed)
# v1.1.194: The Sidebar page sits between Appearance and Message list
# v1.1.193: values() has both accent settings (the other theme's as it was)
# v1.1.191: The Sidebar page replaces Favorites (with Show Favorites, Show Tags and Show Local Folders from the Message list page); it follows Show on startup
# v1.1.187: The Tags page is given the settings (it remembers the width of its Name column)
# v1.1.185: Accent colour as the owner's mockup: a button Sample in the accent (white or black text) as wide as Small and the slider, Reset under the other Reset buttons
# v1.1.184: The accent swatch is square (72 x 72 px) with the 4 px corners of the buttons
# v1.1.183: Accent colour below the sliders: a swatch (the picker has the #RRGGBB field) and Reset; no field or note of its own
# v1.1.182: Pages Favorites, Tags and Operations window (the three tick boxes off General); Accent colour on Appearance; Show on startup is a folded tree; OK saves changed tags first (D-164 to D-166)
# v1.1.174: Accounts: the button Change… is called Edit…
# v1.1.172: Appearance: the Style choice is gone (Fusion always draws, with the program's own colours, D-158)
# v0.5.145: General: the section Operations window (show on send and receive, hide when it completes, show on an error)
# v0.5.144: Four size sliders: Main window, Message list, Other windows and dialogs (new), Text of messages; this window follows Other windows and dialogs
# v0.5.135: Message list: Show Tags in the folder list; the tag's colour for the text
# v0.5.124: General: Show on startup (D-121)
# v0.3.115: The rows of the page list are 2 points higher than their text needs, times the interface factor
# v0.3.113: The page list has its own text size: the desktop's size plus 2 points, times the interface factor
# v0.3.112: Repair the search index (index.reconcile: only what differs from the stored files)
# v0.3.111: Maintenance works once per place where mail is kept (accounts in Local Folders are covered by its line); its report can be selected and copied
# v0.3.108: The message list is a size slider too (Message list)
# v0.3.108: Writing: new messages are written in Rich text or Plain text, chosen in a list (was a tick box); a blank line above Sizes
# v0.3.108: The text of messages (read and written) is a size slider too (one part for both, _size_row)
# v0.3.107: The ticks of the size slider stand above its track, under the handle
# v0.3.106: Appearance has a Sizes section: a slider from Small to Large for the interface (text and icons, applied when the handle is let go, at once with the keys) and the text sizes of the message list, messages and writing (moved here from their pages)
# v0.2.100: Snoozed until keeps its place when the columns are chosen here (it is not offered here)
# v0.2.97: The row height choices come from the Message list window (one list for both)
# v0.2.96: Message list page: the columns are chosen right on the page (no Choose the columns button)
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.82: Text size of the interface (8 to 48 pt, the system size at the first start) under Appearance; the Menu button uses the three-bar character U+2630
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: links from the settings
# v0.0.42: pages for the message list, reading, composing, security and storage and the keyboard
# v0.0.31: the account type is shown
# v0.0.24: Rich text composing
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Initial settings window

ROLE_ACCOUNT: int = 256
MARK_READ_LABELS: tuple[tuple[str, str], ...] = (
    ("delay", "A moment after I select it"), ("window", "Only when I open it in a window of its own"), ("never", "Never by itself (I mark messages)"),
)
QUOTE_LABELS: tuple[tuple[str, str], ...] = (("above", "My answer above the quoted text"), ("below", "My answer below the quoted text"), ("none", "Do not quote the original"))
SCHEME_LABELS: tuple[tuple[str, str], ...] = (("system", "Follow the system"), ("light", "Light"), ("dark", "Dark"))


def _combo(parent: QWidget, choices: tuple[tuple[str, str], ...], current: str) -> QComboBox:
    box = QComboBox(parent)
    for value, label in choices:
        box.addItem(label, value)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


SIDEBAR_EXTRA_POINTS: float = 2.0  # the page list: the desktop's font size plus this, times the size factor of the interface
SIDEBAR_ROW_EXTRA_POINTS: float = 2.0  # and its rows this much higher than the text needs (half above, half below)
SLIDER_WIDTH: int = 200  # pixels at the standard size: the smallest width of the size slider
MINIMUM_WIDTH: int = 760  # the smallest size of this window at the standard size
MINIMUM_HEIGHT: int = 520
TICKS: tuple[float, ...] = (0.75, 1.00, 1.25, 1.50, 1.75, 2.00)  # under the size slider; the one at the standard size is taller
TICK_LENGTH: int = 4  # pixels at the standard size; the tick at the standard size is twice as long


class SizeSlider(QSlider):
    """The size slider: above its track a tick every 0.25, a taller one at the standard size (1.00); the handle is drawn over them."""

    def _groove(self) -> QRect:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)

    def tick_x(self, factor: float) -> int:
        """Where the middle of the handle is at this factor."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        offset = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), scale.slider_position(factor), span)
        return groove.left() + handle.width() // 2 + offset

    @override
    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        return QSize(size.width(), max(size.height(), self._room()))

    @override
    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        return QSize(size.width(), max(size.height(), self._room()))

    def _room(self) -> int:
        """Height enough for the tall tick above the track (the track is in the middle)."""
        return 2 * (wpx(2 * TICK_LENGTH) + 1) + self._groove().height()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)  # the ticks first: the track and the handle are drawn over them
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1))
        bottom = self.tick_bottom()
        for factor in TICKS:
            length = wpx(2 * TICK_LENGTH if factor == scale.STANDARD else TICK_LENGTH)
            x = self.tick_x(factor)
            painter.drawLine(x, bottom - length + 1, x, bottom)
        painter.end()
        super().paintEvent(event)

    def tick_bottom(self) -> int:
        """The lowest row of the ticks: the top of the track's area, so they touch the track (a style that draws its track
        from that row on covers the last pixel, which touches it as well)."""
        return self._groove().top()


class TallerRows(QStyledItemDelegate):
    """Rows that are a number of pixels higher than their text needs; the text stays in the middle."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.extra: int = 0

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), size.height() + self.extra)


def _no_folders() -> list[str]:
    return []


def _no_current_folder() -> tuple[str, str] | None:
    return None


@dataclass(frozen=True, slots=True)
class SettingsContext:
    """What the main window tells the Settings window about itself, in one piece (R-13): what its pages show besides the
    settings (accounts, tags, Favorites and the folder list's choices) and what it may offer."""

    tray_available: bool = True  # the desktop can show a tray icon
    shortcut_actions: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])  # action id -> (label, standard keys)
    accounts: tuple[AccountSummary, ...] = ()  # the mail accounts, for the rules
    folders: Callable[[], list[str]] = _no_folders  # the folders a rule may name
    current_folder: Callable[[], tuple[str, str] | None] = _no_current_folder  # the folder open in the window (account, path), for Run rules
    startup_choices: tuple[tuple[str, str, bool, int, bool], ...] = ()  # Show on startup: text, value, can be chosen, depth, shown
    tags: tuple[TagInfo, ...] = ()
    favorites: tuple[tuple[str, str], ...] = ()  # the standard Favorites in use: (view, label)
    hidden_favorites: frozenset[str] = frozenset()
    pins: tuple[tuple[str, str], ...] = ()  # (pin id, "name - account")
    has_tags: bool = False
    local_id: str = ""  # the id of Local Folders
    tree_accounts: tuple[tuple[str, str], ...] = ()  # accounts with a folder tree of their own: (id, name)
    hidden_trees: frozenset[str] = frozenset()
    local_folders: tuple[tuple[str, str], ...] = ()  # the standard folders of Local Folders: (kind, folder)
    hidden_local: frozenset[str] = frozenset()
    columns: tuple[str, ...] = DEFAULT_COLUMNS  # the columns of the folder open in the window (B-16)
    standard_columns: tuple[str, ...] = DEFAULT_COLUMNS  # the columns its kind of folder starts with (Standard columns)


def verify_line(outcome: VerifyResult) -> str:
    """Check the messages: one account's line of the report."""
    damaged = f"<span style='color:{red()}'>{len(outcome.damaged)} damaged</span>" if outcome.damaged else "all can be read"
    return f"{outcome.checked} messages checked, {damaged}"


def reconcile_line(outcome: ReconcileResult) -> str:
    """Repair the search index: one account's line of the report."""
    return f"{outcome.removed} removed (no file), {outcome.added} added, {outcome.corrected} corrected"


class SettingsDialog(QDialog):
    """The settings window: pages for how the program behaves, how the list and the messages look, writing, the accounts,
    the safety of the stored mail and the keyboard. Nothing is changed until OK; OK writes everything to the settings."""

    accounts_changed = Signal()
    add_account_requested = Signal()
    restore_key_requested = Signal()
    rebuild_requested = Signal(object)  # Rebuild the search index: the main window rebuilds and calls this back with a line per place (D-229)
    interface_scale_tried = Signal(float)  # the Main window slider moved: the main window shows that size at once (not saved yet)
    windows_scale_tried = Signal(float)  # the same for the other windows and dialogs (this one too)
    list_scale_tried = Signal(float)  # the same for the message list
    reading_scale_tried = Signal(float)  # the same for the text of messages, read and written

    def __init__(
        self, backend: Backend, settings: QSettings, context: SettingsContext | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        known = context if context is not None else SettingsContext()
        self._startup_choices: list[tuple[str, str, bool, int, bool]] = list(known.startup_choices) or [("Last selected folder", "", True, 0, True)]  # text, value, can be chosen, depth, shown
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._chosen: Preferences = read_preferences(settings)  # the settings as they are when the window opens (R-12)
        self._columns: tuple[str, ...] = known.columns or DEFAULT_COLUMNS  # the open folder's (B-16)
        self._standard_columns: tuple[str, ...] = known.standard_columns
        self._shortcut_actions: dict[str, tuple[str, str]] = dict(known.shortcut_actions)
        self.setWindowTitle("Settings")
        self.setMinimumSize(wpx(MINIMUM_WIDTH), wpx(MINIMUM_HEIGHT))
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        self.sidebar = QListWidget(self)
        self.sidebar.setFixedWidth(wpx(190))
        self.sidebar_rows = TallerRows(self.sidebar)
        self.sidebar.setItemDelegate(self.sidebar_rows)
        self._size_sidebar_text(scale.windows())
        self.pages = QStackedWidget(self)
        body.addWidget(self.sidebar)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self._buttons: QDialogButtonBox = buttons
        self._saving_tags: bool = False
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._add_page("General", self._general_page(known.tray_available))
        self._add_page("Appearance", self._appearance_page())
        self.sidebar_page = SidebarPage(
            known.favorites, set(known.hidden_favorites), list(known.pins), self._chosen.show_favorites, self._chosen.show_tags,
            self._chosen.show_local_folders, known.has_tags, known.local_id, list(known.tree_accounts), set(known.hidden_trees),
            str(self.startup_box.currentData() or ""), self, local_folders=list(known.local_folders), hidden_local=set(known.hidden_local),
        )
        self._add_page("Sidebar", self.sidebar_page)
        self.startup_box.currentIndexChanged.connect(self._startup_changed)
        self._add_page("Message list", self._list_page())
        self._add_page("Reading", self._reading_page())
        self._add_page("Writing", self._writing_page())
        self._add_page("Accounts", self._accounts_page())
        self.rules_panel = RulesPanel(backend, list(known.accounts), known.folders, known.current_folder, self)
        self._add_page("Rules", self.rules_panel)
        self.tags_panel = TagsPanel(backend, known.tags, self, settings)
        self._add_page("Tags", self.tags_panel)
        self.tags_saved: bool = False  # OK sent changed tags (the window then loads them again)
        self.scheduled_panel = ScheduledPanel(backend, self)
        self._add_page("Scheduled messages", self.scheduled_panel)
        self._add_page("Operations window", self._operations_page())
        self._add_page("Security and storage", self._security_page())
        self._add_page("Keyboard", self._keyboard_page())
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.reload()
        restore_geometry(self, settings, "settings_geometry")

    def _startup_changed(self, row: int) -> None:
        """Show on startup changed while Settings is open: the Sidebar page greys what the new choice keeps visible."""
        self.sidebar_page.set_startup(str(self.startup_box.currentData() or ""))

    def _size_sidebar_text(self, factor: float) -> None:
        """The page list is a little larger than the rest: the desktop's size plus SIDEBAR_EXTRA_POINTS, times the factor;
        its rows SIDEBAR_ROW_EXTRA_POINTS higher than the text needs, times the factor."""
        app = QApplication.instance()
        system = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        font = QFont(self.sidebar.font())
        font.setPointSizeF(round((system + SIDEBAR_EXTRA_POINTS) * factor, 2))
        self.sidebar_rows.extra = round(SIDEBAR_ROW_EXTRA_POINTS * factor * self.logicalDpiY() / 72)  # points to pixels
        self.sidebar.setFont(font)
        self.sidebar.doItemsLayout()  # the rows take their new height at once

    def _add_page(self, title: str, page: QWidget) -> None:
        self.sidebar.addItem(QListWidgetItem(title))
        self.pages.addWidget(page)

    def show_page(self, title: str) -> None:
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).text() == title:
                self.sidebar.setCurrentRow(row)

    # --- the pages --------------------------------------------------------------------------------------------------

    def _general_page(self, tray_available: bool) -> QWidget:
        s = self._settings
        page = QWidget(self)
        form = QFormLayout(page)
        self.startup_box = StartupChooser(self._startup_choices, page)  # a folded tree: headings unfold, folders and views are chosen
        if not self.startup_box.choose_value(self._chosen.startup.text):
            self.startup_box.choose_value("")  # a folder that is gone: the last selected one
        form.addRow("Show on startup:", self.startup_box)
        self.check_on_start = QCheckBox("Check for new mail when the program starts", page)
        self.check_on_start.setChecked(self._chosen.check_on_start)
        self.auto_check = QCheckBox("Check for new mail automatically", page)
        self.auto_check.setChecked(self._chosen.auto_check)
        self.interval = QSpinBox(page)
        self.interval.setRange(1, 240)
        self.interval.setSuffix(" minutes")
        self.interval.setValue(self._chosen.auto_check_minutes)
        self.interval.setEnabled(self.auto_check.isChecked())
        self.scheduled_refresh = QComboBox(page)  # what a refresh the person did not click does; the Refresh button always sends and receives
        for text, value in (("Send and Receive", "send_receive"), ("Receive only", "receive")):
            self.scheduled_refresh.addItem(text, value)
        self.scheduled_refresh.setCurrentIndex(max(0, self.scheduled_refresh.findData(self._chosen.scheduled_refresh)))
        self.send_delay = QCheckBox("Delay sending a message by", page)
        self.send_delay.setChecked(self._chosen.send_delay)
        self.send_delay_seconds = QSpinBox(page)
        self.send_delay_seconds.setRange(1, 999)
        self.send_delay_seconds.setSuffix(" seconds")
        self.send_delay_seconds.setValue(self._chosen.send_delay_seconds)
        self.send_delay_seconds.setEnabled(self.send_delay.isChecked())
        self.send_on_refresh = QCheckBox("Delay sending messages until the next refresh", page)
        self.send_on_refresh.setChecked(self._chosen.send_on_refresh)
        self.warn_outbox_on_quit = QCheckBox("Warn when quitting with messages in the Outbox", page)
        self.warn_outbox_on_quit.setChecked(self._chosen.warn_outbox_on_quit)
        self.notifications = QCheckBox("Show a notification when new mail arrives (only the number, never the content)", page)
        self.notifications.setChecked(self._chosen.notifications)
        self.tray = QCheckBox("Show an icon in the notification area", page)
        self.tray.setChecked(self._chosen.tray and tray_available)
        self.close_to_tray = QCheckBox("Closing the window keeps Chrono Mail running in the notification area", page)
        self.close_to_tray.setChecked(self._chosen.close_to_tray and tray_available)
        for widget in (self.check_on_start, self.auto_check):
            form.addRow(widget)
        form.addRow("Check every", self.interval)
        form.addRow("Scheduled refresh:", self.scheduled_refresh)
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.send_delay)
        delay_row.addWidget(self.send_delay_seconds)
        delay_row.addStretch(1)
        form.addRow(delay_row)
        for widget in (self.send_on_refresh, self.warn_outbox_on_quit):
            form.addRow(widget)
        for widget in (self.notifications, self.tray, self.close_to_tray):
            form.addRow(widget)
        if not tray_available:
            for widget in (self.tray, self.close_to_tray, self.notifications):
                widget.setEnabled(False)
            form.addRow(QLabel("This desktop has no notification area, so there are no icon and no notifications.", page))
        form.addRow(QLabel("Language: the program is in English; dates and times follow the language of the system.", page))
        self.tray.toggled.connect(self._tray_toggled)
        self._tray_toggled(self.tray.isChecked())
        self.auto_check.toggled.connect(self.interval.setEnabled)
        self.send_delay.toggled.connect(self.send_delay_seconds.setEnabled)
        return page

    def _operations_page(self) -> QWidget:
        """When the Operations window (the progress of every send and receive) shows itself and hides again."""
        s = self._settings
        page = QWidget(self)
        form = QFormLayout(page)
        self.operations_show = QCheckBox("Show window on send and receive", page)
        self.operations_show.setChecked(self._chosen.operations_show_on_send_receive)
        self.operations_hide = QCheckBox("Hide window when send and receive completes", page)
        self.operations_hide.setChecked(self._chosen.operations_hide_when_done)
        self.operations_error = QCheckBox("Show window when an error occurs", page)
        self.operations_error.setChecked(self._chosen.operations_show_on_error)
        for widget in (self.operations_show, self.operations_hide, self.operations_error):
            form.addRow(widget)
        return page

    def _tray_toggled(self, on: bool) -> None:
        if not on:
            self.close_to_tray.setChecked(False)
        self.close_to_tray.setEnabled(on and self.tray.isEnabled())

    def _size_row(self, page: QWidget, factor: float) -> tuple[SizeSlider, QPushButton, QHBoxLayout]:
        """A size slider from Small to Large with its Reset button. While the handle is dragged nothing changes (a window
        that grows would move the slider under the pointer); keys and clicks apply each step at once."""
        slider = SizeSlider(Qt.Orientation.Horizontal, page)
        slider.setRange(0, scale.SLIDER_STEPS)
        slider.setPageStep(4)
        slider.setTracking(False)
        slider.setMinimumWidth(wpx(SLIDER_WIDTH))  # never squeezed down to its handle
        slider.setValue(scale.slider_position(factor))
        reset = QPushButton("Reset", page)
        reset.clicked.connect(lambda: slider.setValue(scale.slider_position(scale.STANDARD)))
        row = QHBoxLayout()
        row.addWidget(QLabel("Small", page))
        row.addWidget(slider, 1)
        row.addWidget(QLabel("Large", page))
        row.addWidget(reset)
        return slider, reset, row

    def _list_slider_moved(self, position: int) -> None:
        self.list_scale_tried.emit(scale.factor_at(position))

    def _reading_slider_moved(self, position: int) -> None:
        self.reading_scale_tried.emit(scale.factor_at(position))

    def _interface_slider_moved(self, position: int) -> None:
        self.interface_scale_tried.emit(scale.factor_at(position))

    def _windows_slider_moved(self, position: int) -> None:
        factor = scale.factor_at(position)
        self.windows_scale_tried.emit(factor)
        self.sidebar.setFixedWidth(wpx(190))  # this window follows too
        self._size_sidebar_text(factor)
        for slider in (self.interface_slider, self.list_slider, self.windows_slider, self.reading_slider):
            slider.setMinimumWidth(wpx(SLIDER_WIDTH))
        self._grow_with(factor)

    def _needed_size(self) -> QSize:
        """The smallest size at which the page that is shown fits: the page list, the page, the status line and the buttons."""
        outer = self.layout()
        page = self.pages.currentWidget()
        if outer is None or page is None:
            return QSize(0, 0)
        margins = outer.contentsMargins()
        body = outer.itemAt(0)
        body_layout = body.layout() if body is not None else None
        gap = body_layout.spacing() if body_layout is not None else 0
        wanted = page.minimumSizeHint()
        width = margins.left() + margins.right() + self.sidebar.width() + max(0, gap) + wanted.width()
        below = 0
        for index in range(1, outer.count()):
            item = outer.itemAt(index)
            if item is not None:
                below += item.sizeHint().height() + max(0, outer.spacing())
        return QSize(width, margins.top() + margins.bottom() + wanted.height() + below)

    def _grow_with(self, factor: float) -> None:
        """This window grows and shrinks with the size of the other windows and dialogs, in proportion, never beyond the screen, around the same centre."""
        if factor == self._window_factor:
            return
        if self._size_at_standard is None:  # the size it had when the slider first moved, at the standard size
            self._size_at_standard = (self.width() / self._window_factor, self.height() / self._window_factor)
        self._window_factor = factor
        standard_width, standard_height = self._size_at_standard
        self.setMinimumSize(round(MINIMUM_WIDTH * factor), round(MINIMUM_HEIGHT * factor))  # the smallest size follows too
        centre = self.geometry().center()
        needed = self._needed_size()  # never smaller than the page that is shown needs: its parts would overlap
        width, height = max(round(standard_width * factor), needed.width()), max(round(standard_height * factor), needed.height())
        screen = self.screen()
        if screen is not None:
            room = screen.availableGeometry()
            width, height = min(width, room.width()), min(height, room.height())
            self.setMinimumSize(min(self.minimumWidth(), room.width()), min(self.minimumHeight(), room.height()))
            left = min(max(centre.x() - width // 2, room.left()), room.right() - width + 1)
            top = min(max(centre.y() - height // 2, room.top()), room.bottom() - height + 1)
            self.setGeometry(left, top, width, height)
        else:
            self.resize(width, height)

    def _appearance_page(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        s = self._settings
        self.scheme_box = _combo(page, SCHEME_LABELS, self._chosen.color_scheme)
        form.addRow("Colours", self.scheme_box)
        note = QLabel(
            "Follow the system takes the light or the dark colours as the system is set. Messages in HTML are always shown on a white page: they are written for one.",
            page,
        )
        note.setWordWrap(True)
        form.addRow(note)
        form.addItem(QSpacerItem(0, wpx(18), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))  # a blank line above the sizes
        sizes = QLabel("Sizes", page)
        sizes.setStyleSheet("font-weight: bold;")  # the size keeps following the interface
        form.addRow(sizes)
        app = QApplication.instance()
        self.system_points: int = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        self.interface_scale_saved: float = scale.interface()  # what Cancel puts back
        self.windows_scale_saved: float = scale.windows()
        self._window_factor: float = self.windows_scale_saved
        self._size_at_standard: tuple[float, float] | None = None
        self.interface_slider, self.interface_standard, row = self._size_row(page, self.interface_scale_saved)
        self.interface_slider.valueChanged.connect(self._interface_slider_moved)
        form.addRow("Main window", row)
        self.list_scale_saved: float = prefs.saved_list(s, self.system_points)
        self.list_slider, self.list_reset, row = self._size_row(page, self.list_scale_saved)
        self.list_slider.valueChanged.connect(self._list_slider_moved)
        form.addRow("Message list", row)
        self.windows_slider, self.windows_reset, row = self._size_row(page, self.windows_scale_saved)
        self.windows_slider.valueChanged.connect(self._windows_slider_moved)
        form.addRow("Other windows and dialogs", row)
        self.reading_scale_saved: float = prefs.saved_reading(s)
        self.reading_slider, self.reading_reset, row = self._size_row(page, self.reading_scale_saved)
        self.reading_slider.valueChanged.connect(self._reading_slider_moved)
        form.addRow("Text of messages", row)  # reading and writing alike
        form.addItem(QSpacerItem(0, wpx(18), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed))  # a blank line above the accent
        self._accent_row(page, form)
        return page

    def _accent_row(self, page: QWidget, form: QFormLayout) -> None:
        """Accent colour: the one of the theme in use (each theme has its own; the other one is changed while it is in use).
        A button "Sample" in the accent, as wide as Small and the slider of the size rows above it, which opens the colour
        picker (that has the #RRGGBB field), and Reset under the Reset buttons of the size rows, which brings back the
        theme's own blue. Its text is white or black, whichever reads better, as everywhere on the accent."""
        self.accent_dark_theme: bool = is_dark()
        self.accent_key: str = "accent_dark" if self.accent_dark_theme else "accent_light"
        self.accent_standard: str = (DARK if self.accent_dark_theme else LIGHT).blue
        self.accent: str = (self._chosen.accent_dark if self.accent_dark_theme else self._chosen.accent_light) or self.accent_standard
        self.accent_swatch = QPushButton("Sample", page)
        self.accent_swatch.setToolTip("Pick a colour")
        self.accent_reset = QPushButton("Reset", page)
        spacer = QLabel("Large", page)  # takes the room of "Large" in the size rows, unseen, so Reset lines up with theirs
        keep = spacer.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        spacer.setSizePolicy(keep)
        spacer.hide()
        row = QHBoxLayout()
        row.addWidget(self.accent_swatch, 1)
        row.addWidget(spacer)
        row.addWidget(self.accent_reset)
        form.addRow("Accent colour", row)
        self.accent_swatch.clicked.connect(self._pick_accent)
        self.accent_reset.clicked.connect(lambda: self._set_accent(self.accent_standard))
        self._set_accent(self.accent)

    def _accents(self) -> tuple[str, str]:
        """Both accents (dark, light): the one of the theme in use as set here, the other one as it was."""
        mine = "" if self.accent == self.accent_standard else self.accent
        return (mine, self._chosen.accent_light) if self.accent_dark_theme else (self._chosen.accent_dark, mine)

    def _set_accent(self, value: str) -> None:
        self.accent = value
        self.accent_swatch.setStyleSheet(
            f"QPushButton {{ background: {value}; color: {text_on_accent(value)}; border: 1px solid palette(mid); border-radius: 4px; padding: 4px 12px; }}"
        )
        self.accent_reset.setEnabled(value != self.accent_standard)

    def _pick_accent(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.accent), self, "Accent colour")
        if chosen.isValid():
            self._set_accent(chosen.name())

    def _list_page(self) -> QWidget:
        s = self._settings
        page = QWidget(self)
        form = QFormLayout(page)
        self.layout_box = _combo(page, LAYOUTS, self._chosen.list_layout)
        self.pane_box = _combo(page, READING_PANES, self._chosen.reading_pane)
        self.date_box = _combo(page, DATE_STYLES, self._chosen.date_style)
        self.density_box = _combo(page, DENSITY_LABELS, self._chosen.list_density)
        self.conversations = QCheckBox("Show messages grouped as conversations", page)
        self.conversations.setChecked(self._chosen.conversations)
        self.tag_text_colour = QCheckBox("Use the tag's colour for the text of tagged messages", page)
        self.tag_text_colour.setChecked(self._chosen.tag_text_colour)
        self.columns_picker = ColumnPicker(self._columns, page, self._standard_columns)
        form.addRow("Layout", self.layout_box)
        form.addRow("Reading pane", self.pane_box)
        form.addRow("Dates", self.date_box)
        form.addRow("Row height", self.density_box)
        form.addRow(self.conversations)
        form.addRow(self.tag_text_colour)
        columns_text = QLabel(COLUMNS_TEXT, page)
        columns_text.setWordWrap(True)
        form.addRow(columns_text)
        form.addRow(self.columns_picker)
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._layout_changed()
        return page

    def _layout_changed(self) -> None:
        self.columns_picker.set_active(str(self.layout_box.currentData()) == "columns")

    def _reading_page(self) -> QWidget:
        s = self._settings
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.mark_read_box = _combo(page, MARK_READ_LABELS, self._chosen.mark_read)
        self.mark_read_delay = QSpinBox(page)
        self.mark_read_delay.setRange(0, 10_000)
        self.mark_read_delay.setSingleStep(100)
        self.mark_read_delay.setSuffix(" ms")
        self.mark_read_delay.setValue(self._chosen.mark_read_delay_ms)
        self.prefer_plain = QCheckBox("Show the plain text of a message that has both text and HTML", page)
        self.prefer_plain.setChecked(self._chosen.prefer_plain)
        self.links_ask = QCheckBox("Ask before opening a link in the web browser", page)
        self.links_ask.setChecked(self._chosen.links_ask)
        form.addRow("Mark a message as read", self.mark_read_box)
        form.addRow("…after", self.mark_read_delay)
        form.addRow(self.prefer_plain)
        form.addRow(self.links_ask)
        layout.addLayout(form)
        privacy = QGroupBox("Pictures and other content from the internet", page)
        privacy_layout = QVBoxLayout(privacy)
        self.remote_always = QCheckBox("Always show it, for every sender (tells senders that you read their message)", privacy)
        self.remote_always.setChecked(self._chosen.remote_always)
        privacy_layout.addWidget(self.remote_always)
        privacy_layout.addWidget(QLabel("Senders whose content is always shown (one address per line):", privacy))
        self.remote_senders = QPlainTextEdit(privacy)
        self.remote_senders.setPlainText("\n".join(sorted(self._chosen.remote_senders)))
        privacy_layout.addWidget(self.remote_senders)
        self.forget_senders_button = QPushButton("Forget all allowed senders", privacy)
        privacy_layout.addWidget(self.forget_senders_button)
        self.forget_senders_button.clicked.connect(self.remote_senders.clear)
        layout.addWidget(privacy, 1)
        self.mark_read_box.currentIndexChanged.connect(self._mark_read_changed)
        self._mark_read_changed()
        return page

    def _mark_read_changed(self) -> None:
        self.mark_read_delay.setEnabled(str(self.mark_read_box.currentData()) == "delay")

    def _writing_page(self) -> QWidget:
        s = self._settings
        page = QWidget(self)
        form = QFormLayout(page)
        self.default_account = QComboBox(page)
        self.default_account.addItem("The account of the folder I am in", "")
        self.compose_html = QComboBox(page)
        self.compose_html.addItem("Rich text (bold, lists, links; sent as text and HTML)", True)
        self.compose_html.addItem("Plain text", False)
        self.compose_html.setCurrentIndex(0 if self._chosen.compose_html else 1)
        self.reply_quote_box = _combo(page, QUOTE_LABELS, self._chosen.reply_quote)
        self.autosave = QSpinBox(page)
        self.autosave.setRange(0, 600)
        self.autosave.setSpecialValueText("Never")
        self.autosave.setSuffix(" seconds")
        self.autosave.setValue(self._chosen.autosave_seconds)
        self.warn_no_subject = QCheckBox("Ask before sending a message without a subject", page)
        self.warn_no_subject.setChecked(self._chosen.warn_no_subject)
        form.addRow("New messages are sent from", self.default_account)
        form.addRow("New messages are written in", self.compose_html)
        form.addRow("Replies", self.reply_quote_box)
        form.addRow("Save a draft every", self.autosave)
        form.addRow(self.warn_no_subject)
        form.addRow(QLabel("The signature of each account is set under Accounts.", page))
        return page

    def _accounts_page(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        self.accounts = QListWidget(page)
        layout.addWidget(self.accounts, 1)
        side = QVBoxLayout()
        self.add_button = QPushButton("Add…", page)
        self.edit_button = QPushButton("Edit…", page)
        self.remove_button = QPushButton("Remove…", page)
        for button in (self.add_button, self.edit_button, self.remove_button):
            side.addWidget(button)
        side.addStretch(1)
        layout.addLayout(side)
        self.add_button.clicked.connect(self.add_account_requested)
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.accounts.itemDoubleClicked.connect(self._double_clicked)
        self.accounts.currentItemChanged.connect(self._selection_changed)
        return page

    def _security_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        directories = self._backend.engine.directories
        self.key_label = QLabel("", page)
        self.key_label.setWordWrap(True)
        layout.addWidget(QLabel(f"Your mail is stored encrypted in:<br><b>{directories.data_directory}</b><br>Settings: <b>{directories.config_directory}</b>", page))
        layout.addWidget(self.key_label)
        row = QHBoxLayout()
        self.recovery_button = QPushButton("Show the recovery code…", page)
        self.restore_button = QPushButton("Restore the key…", page)
        row.addWidget(self.recovery_button)
        row.addWidget(self.restore_button)
        row.addStretch(1)
        layout.addLayout(row)
        maintenance = QGroupBox("Maintenance", page)
        maintenance_layout = QVBoxLayout(maintenance)
        self.verify_button = QPushButton("Check that all stored mail can be read", maintenance)
        self.repair_button = QPushButton("Repair the search index", maintenance)
        self.rebuild_button = QPushButton("Rebuild the search index", maintenance)
        self.maintenance_label = QLabel("", maintenance)
        self.maintenance_label.setWordWrap(True)
        self.maintenance_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.verify_button, self.repair_button, self.rebuild_button, self.maintenance_label):
            maintenance_layout.addWidget(widget)
        layout.addWidget(maintenance)
        layout.addStretch(1)
        self.recovery_button.clicked.connect(lambda: RecoveryDialog(self._backend, self).exec())
        self.restore_button.clicked.connect(self.restore_key_requested)
        self.verify_button.clicked.connect(lambda: self._maintain("verify", "Checking"))
        self.repair_button.clicked.connect(lambda: self._maintain("reconcile", "Repairing"))
        self.rebuild_button.clicked.connect(self._rebuild)
        self._backend.ask(KEY_STATUS, NoParameters(), self._key_status, lambda error: self.key_label.setText(str(error)), owner=self)
        return page

    def _key_status(self, result: KeyStatus) -> None:
        if result.key_id is None:
            self.key_label.setText("No account uses an encryption key.")
        elif result.in_credential_store:
            self.key_label.setText("The encryption key is in this computer's credential store. Keep a copy of the recovery code somewhere safe: without the key the mail cannot be read.")
        else:
            self.key_label.setText(f"<span style='color:{red()}'>The encryption key is NOT in the credential store. Use “Restore the key…”.</span>")

    def _rebuild(self) -> None:
        """Rebuild the search index: done by the main window, with the rebuild window; the lines come back here when all are done."""
        for button in (self.verify_button, self.repair_button, self.rebuild_button):
            button.setEnabled(False)
        self.maintenance_label.setText("Rebuilding…")

        def finished(lines: list[str]) -> None:
            for button in (self.verify_button, self.repair_button, self.rebuild_button):
                button.setEnabled(True)
            self.maintenance_label.setText("<br>".join(lines) or "No accounts.")

        self.rebuild_requested.emit(finished)

    def _maintain(self, job: Literal["verify", "reconcile"], verb: str) -> None:
        self.verify_button.setEnabled(False)
        self.repair_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self.maintenance_label.setText(f"{verb}…")
        report: list[str] = []

        def finished() -> None:
            self.verify_button.setEnabled(True)
            self.repair_button.setEnabled(True)
            self.rebuild_button.setEnabled(True)
            self.maintenance_label.setText("<br>".join(report) or "No accounts.")

        def listed(result: tuple[AccountSummary, ...]) -> None:
            # Accounts that keep their mail in Local Folders share its store and index: the Local Folders line covers them
            accounts = [account.account_id for account in result if account.storage != "local" and account.kind != "dav"]
            names = {account.account_id: account.label for account in result}
            if not accounts:
                finished()
                return
            remaining = list(accounts)

            def make_done(account: str) -> Callable[[str], None]:
                def done(line: str) -> None:
                    report.append(f"{names[account]}: {line}")
                    remaining.remove(account)
                    if not remaining:
                        finished()

                return done

            def make_failed(account: str) -> Callable[[ChronoMailError], None]:
                def failed(error: ChronoMailError) -> None:
                    report.append(f"{names[account]}: <span style='color:{red()}'>{error}</span>")
                    remaining.remove(account)
                    if not remaining:
                        finished()

                return failed

            for account in accounts:
                done, failed = make_done(account), make_failed(account)
                if job == "verify":
                    self._backend.ask(STORE_VERIFY, AccountParameters(account), lambda outcome, done=done: done(verify_line(outcome)), failed, owner=self)
                else:
                    self._backend.ask(INDEX_RECONCILE, AccountParameters(account), lambda outcome, done=done: done(reconcile_line(outcome)), failed, owner=self)

        self._backend.ask(ACCOUNT_LIST, NoParameters(), listed, lambda error: finished(), owner=self)

    def _keyboard_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Click a key field and press the keys you want; Backspace or Delete empties it.", page))
        self.shortcut_table = QTableWidget(len(self._shortcut_actions), 2, page)
        self.shortcut_table.setHorizontalHeaderLabels(["Action", "Keys"])
        self.shortcut_table.verticalHeader().hide()
        self.shortcut_table.horizontalHeader().setStretchLastSection(True)
        self.shortcut_table.setColumnWidth(0, 300)
        self.shortcut_edits: dict[str, QKeySequenceEdit] = {}
        chosen = dict(self._chosen.shortcuts)
        for row, (action_id, (label, default)) in enumerate(sorted(self._shortcut_actions.items(), key=lambda item: item[1][0].lower())):
            item = QTableWidgetItem(label.replace("&", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setData(Qt.ItemDataRole.UserRole, action_id)
            self.shortcut_table.setItem(row, 0, item)
            edit = QKeySequenceEdit(QKeySequence(chosen.get(action_id, default)), self.shortcut_table)
            self.shortcut_table.setCellWidget(row, 1, edit)
            self.shortcut_edits[action_id] = edit
        layout.addWidget(self.shortcut_table, 1)
        self.shortcut_note = QLabel("", page)
        layout.addWidget(self.shortcut_note)
        self.reset_keys_button = QPushButton("Standard keys", page)
        layout.addWidget(self.reset_keys_button)
        self.reset_keys_button.clicked.connect(self._reset_keys)
        return page

    def _reset_keys(self) -> None:
        for action_id, edit in self.shortcut_edits.items():
            edit.setKeySequence(QKeySequence(self._shortcut_actions[action_id][1]))

    def shortcut_values(self) -> dict[str, str]:
        return {action_id: edit.keySequence().toString() for action_id, edit in self.shortcut_edits.items()}

    def shortcut_conflicts(self) -> list[str]:
        """Keys given to more than one action."""
        used: dict[str, list[str]] = {}
        for action_id, keys in self.shortcut_values().items():
            if keys:
                used.setdefault(keys, []).append(self._shortcut_actions[action_id][0].replace("&", ""))
        return [f"{keys}: {', '.join(names)}" for keys, names in used.items() if len(names) > 1]

    # --- accounts -----------------------------------------------------------------------------------------

    def reload(self) -> None:
        """Show the accounts as they are now."""
        selected = self._selected_id()

        def listed(result: tuple[AccountSummary, ...]) -> None:
            self.accounts.clear()
            keep = str(self.default_account.currentData() or self._chosen.default_account)
            self.default_account.clear()
            self.default_account.addItem("The account of the folder I am in", "")
            for account in result:
                if account.kind == "local":
                    continue  # Local Folders is not an account: it is shown through the accounts that keep their mail there
                name = account.account_name or account.display_name
                calendars_only = account.kind == "dav"  # no address and no mail: never the default account
                text = f"{name}  - {account.server_label}" if calendars_only else f"{name}  <{account.email_address}>  - {account.server_label}"
                item = QListWidgetItem(text)
                item.setData(ROLE_ACCOUNT, account)
                self.accounts.addItem(item)
                if not calendars_only:
                    self.default_account.addItem(account.label, account.account_id)
                if account.account_id == selected:
                    self.accounts.setCurrentItem(item)
            self.default_account.setCurrentIndex(max(0, self.default_account.findData(keep)))
            if self.accounts.count() and self.accounts.currentRow() < 0:
                self.accounts.setCurrentRow(0)
            self._selection_changed()

        self._backend.ask(ACCOUNT_LIST, NoParameters(), listed, lambda error: self.status.setText(f"The accounts could not be listed: {error}"), owner=self)

    def _selected(self) -> AccountSummary | None:
        item = self.accounts.currentItem()
        account = None if item is None else item.data(ROLE_ACCOUNT)
        return account if isinstance(account, AccountSummary) else None

    def _selected_id(self) -> str | None:
        account = self._selected()
        return None if account is None else account.account_id

    def _selection_changed(self) -> None:
        has = self._selected() is not None
        self.edit_button.setEnabled(has)
        self.remove_button.setEnabled(has)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.edit_selected()

    def edit_selected(self) -> None:
        account = self._selected()
        if account is None:
            return
        dialog = AccountEditDialog(self._backend, account, self)
        if dialog.exec() == AccountEditDialog.DialogCode.Accepted:
            self.status.setText("Account saved")
            self.reload()
            self.accounts_changed.emit()

    def remove_selected(self) -> None:
        account = self._selected()
        if account is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove account",
            f"Remove the account {account.email_address} from Chrono Mail?\n\n"
            "The program forgets the account and its passwords. The mail stored on this computer is not deleted, "
            "and nothing is removed from the server.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result: AccountRemoveResult) -> None:
            self.status.setText("Account removed")
            self.reload()
            self.accounts_changed.emit()

        def failed(error: ChronoMailError) -> None:
            QMessageBox.warning(self, "Remove account", f"The account could not be removed: {error}")

        self._backend.ask(ACCOUNT_REMOVE, AccountParameters(account.account_id), done, failed, owner=self)

    # --- saving -----------------------------------------------------------------------------------------------------

    def chosen(self) -> Preferences:
        """Every setting of the window as it is set now (R-12: the typed settings, saved by save())."""
        senders = tuple(sorted({line.strip().lower() for line in self.remote_senders.toPlainText().splitlines() if "@" in line}))
        sidebar = self.sidebar_page.values()
        accent_dark, accent_light = self._accents()
        return Preferences(
            check_on_start=self.check_on_start.isChecked(), auto_check=self.auto_check.isChecked(), auto_check_minutes=self.interval.value(),
            scheduled_refresh=str(self.scheduled_refresh.currentData()), send_delay=self.send_delay.isChecked(), send_delay_seconds=self.send_delay_seconds.value(),
            send_on_refresh=self.send_on_refresh.isChecked(), warn_outbox_on_quit=self.warn_outbox_on_quit.isChecked(),
            notifications=self.notifications.isChecked(), tray=self.tray.isChecked(), close_to_tray=self.close_to_tray.isChecked(),
            operations_show_on_send_receive=self.operations_show.isChecked(), operations_hide_when_done=self.operations_hide.isChecked(),
            operations_show_on_error=self.operations_error.isChecked(),
            color_scheme=str(self.scheme_box.currentData()), accent_dark=accent_dark, accent_light=accent_light,
            interface_scale=scale.factor_at(self.interface_slider.value()), windows_scale=scale.factor_at(self.windows_slider.value()),
            list_layout=str(self.layout_box.currentData()), reading_pane=str(self.pane_box.currentData()), date_style=str(self.date_box.currentData()),
            list_density=str(self.density_box.currentData()), list_scale=scale.factor_at(self.list_slider.value()), conversations=self.conversations.isChecked(),
            show_favorites=bool(sidebar["show_favorites"]), show_local_folders=bool(sidebar["show_local_folders"]), show_tags=bool(sidebar["show_tags"]),
            tag_text_colour=self.tag_text_colour.isChecked(),
            mark_read=str(self.mark_read_box.currentData()), mark_read_delay_ms=self.mark_read_delay.value(), reading_scale=scale.factor_at(self.reading_slider.value()),
            prefer_plain=self.prefer_plain.isChecked(), remote_always=self.remote_always.isChecked(), remote_senders=senders, links_ask=self.links_ask.isChecked(),
            default_account=str(self.default_account.currentData() or ""), compose_html=bool(self.compose_html.currentData()),
            reply_quote=str(self.reply_quote_box.currentData()), autosave_seconds=self.autosave.value(), warn_no_subject=self.warn_no_subject.isChecked(),
            startup=StartupView.parse(str(self.startup_box.currentData() or "")),
            shortcuts={action_id: keys for action_id, keys in self.shortcut_values().items() if keys != self._shortcut_actions[action_id][1]},
        )

    def chosen_columns(self) -> tuple[str, ...]:
        """The columns chosen for the folder open in the window (Snoozed until, not offered here, keeps its place)."""
        return keep_snoozed_column(self.columns_picker.columns() or self._standard_columns, self._columns)

    def save(self) -> None:
        save_preferences(self._settings, self.chosen())

    @override
    def done(self, result: int) -> None:
        if result != QDialog.DialogCode.Accepted:
            self._grow_with(self.interface_scale_saved)  # Cancel: the size it had, before it is remembered
        save_geometry(self, self._settings, "settings_geometry")  # whether the window was closed with OK or with Cancel
        super().done(result)

    @override
    def accept(self) -> None:
        conflicts = self.shortcut_conflicts()
        if conflicts:
            self.show_page("Keyboard")
            self.shortcut_note.setText(f"<span style='color:{red()}'>The same keys are used twice: " + "; ".join(conflicts) + "</span>")
            return
        if self._saving_tags:
            return  # OK was pressed again while the tags are being saved
        if not self.tags_panel.changed():
            super().accept()
            return
        self._saving_tags = True
        self._buttons.setEnabled(False)

        def saved() -> None:
            self._saving_tags = False
            self._buttons.setEnabled(True)
            self.tags_saved = True
            super(SettingsDialog, self).accept()

        def failed() -> None:  # the window stays open on the Tags page with the error; nothing more is saved
            self._saving_tags = False
            self._buttons.setEnabled(True)
            self.tags_saved = True  # what was saved before the error is shown in the main window too
            self.show_page("Tags")

        self.tags_panel.save(saved, failed)
