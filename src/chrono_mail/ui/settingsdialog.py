import json
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QPainter, QPaintEvent, QPalette, QPen, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from chrono_mail.core import ChronoMailError
from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import DEFAULT_COLUMNS, keep_snoozed_column, valid_columns
from chrono_mail.ui.columnsdialog import COLUMNS_TEXT, DATE_STYLES, DENSITY_LABELS, LAYOUTS, READING_PANES, ColumnPicker
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.rulespanel import RulesPanel
from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.colours import red

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.5.144"

# --- Version history ----------------------------------------------------
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
STYLE_LABELS: tuple[tuple[str, str], ...] = (("default", "The style of the platform"), ("fusion", "Fusion"))


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


class SettingsDialog(QDialog):
    """The settings window: pages for how the program behaves, how the list and the messages look, writing, the accounts,
    the safety of the stored mail and the keyboard. Nothing is changed until OK; OK writes everything to the settings."""

    accounts_changed = Signal()
    add_account_requested = Signal()
    restore_key_requested = Signal()
    interface_scale_tried = Signal(float)  # the Main window slider moved: the main window shows that size at once (not saved yet)
    windows_scale_tried = Signal(float)  # the same for the other windows and dialogs (this one too)
    list_scale_tried = Signal(float)  # the same for the message list
    reading_scale_tried = Signal(float)  # the same for the text of messages, read and written

    def __init__(
        self, backend: Backend, settings: QSettings, tray_available: bool = True, shortcut_actions: dict[str, tuple[str, str]] | None = None, parent: QWidget | None = None,
        accounts: list[object] | None = None, folders: Callable[[], list[str]] | None = None, current_folder: Callable[[], tuple[str, str] | None] | None = None,
        startup_choices: list[tuple[str, str, bool, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._startup_choices: list[tuple[str, str, bool, int]] = startup_choices or [("Last selected folder", "", True, 0)]  # text, value, can be chosen, depth
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._columns: tuple[str, ...] = valid_columns([part for part in str(settings.value("columns", "")).split(",") if part])
        self._shortcut_actions: dict[str, tuple[str, str]] = shortcut_actions or {}
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
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._add_page("General", self._general_page(tray_available))
        self._add_page("Appearance", self._appearance_page())
        self._add_page("Message list", self._list_page())
        self._add_page("Reading", self._reading_page())
        self._add_page("Writing", self._writing_page())
        self._add_page("Accounts", self._accounts_page())
        self.rules_panel = RulesPanel(backend, accounts or [], folders or (lambda: []), current_folder or (lambda: None), self)
        self._add_page("Rules", self.rules_panel)
        self.scheduled_panel = ScheduledPanel(backend, self)
        self._add_page("Scheduled messages", self.scheduled_panel)
        self._add_page("Security and storage", self._security_page())
        self._add_page("Keyboard", self._keyboard_page())
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.reload()
        restore_geometry(self, settings, "settings_geometry")

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
        self.startup_box = QComboBox(page)
        for text, value, choosable, depth in self._startup_choices:
            self.startup_box.addItem("    " * depth + text, value)
            choices = self.startup_box.model()
            if not choosable and isinstance(choices, QStandardItemModel):
                heading = choices.item(self.startup_box.count() - 1)
                if heading is not None:
                    heading.setEnabled(False)  # an account or group: its folders are below it
        self.startup_box.setCurrentIndex(max(0, self.startup_box.findData(str(s.value("startup_view", "") or ""))))  # a folder that is gone: the last selected one
        form.addRow("Show on startup:", self.startup_box)
        self.check_on_start = QCheckBox("Check for new mail when the program starts", page)
        self.check_on_start.setChecked(prefs.get_bool(s, "check_on_start"))
        self.auto_check = QCheckBox("Check for new mail automatically", page)
        self.auto_check.setChecked(prefs.get_bool(s, "auto_check"))
        self.interval = QSpinBox(page)
        self.interval.setRange(1, 240)
        self.interval.setSuffix(" minutes")
        self.interval.setValue(prefs.get_int(s, "auto_check_minutes", 1, 240))
        self.interval.setEnabled(self.auto_check.isChecked())
        self.notifications = QCheckBox("Show a notification when new mail arrives (only the number, never the content)", page)
        self.notifications.setChecked(prefs.get_bool(s, "notifications"))
        self.tray = QCheckBox("Show an icon in the notification area", page)
        self.tray.setChecked(prefs.get_bool(s, "tray") and tray_available)
        self.close_to_tray = QCheckBox("Closing the window keeps Chrono Mail running in the notification area", page)
        self.close_to_tray.setChecked(prefs.get_bool(s, "close_to_tray") and tray_available)
        for widget in (self.check_on_start, self.auto_check):
            form.addRow(widget)
        form.addRow("Check every", self.interval)
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
        self.scheme_box = _combo(page, SCHEME_LABELS, prefs.get_text(s, "color_scheme", appearance.SCHEMES))
        self.style_box = _combo(page, STYLE_LABELS, prefs.get_text(s, "style", appearance.STYLES))
        form.addRow("Colours", self.scheme_box)
        form.addRow("Style", self.style_box)
        note = QLabel(
            "Dark needs a style that can draw it. When the style of the platform cannot (the classic Windows style), Fusion is used while the colours are dark. "
            "Messages in HTML are always shown on a white page: they are written for one. A style given on the command line (-style) is not changed here.",
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
        self.list_scale_saved: float = scale.saved_list(s, self.system_points)
        self.list_slider, self.list_reset, row = self._size_row(page, self.list_scale_saved)
        self.list_slider.valueChanged.connect(self._list_slider_moved)
        form.addRow("Message list", row)
        self.windows_slider, self.windows_reset, row = self._size_row(page, self.windows_scale_saved)
        self.windows_slider.valueChanged.connect(self._windows_slider_moved)
        form.addRow("Other windows and dialogs", row)
        self.reading_scale_saved: float = scale.saved_reading(s)
        self.reading_slider, self.reading_reset, row = self._size_row(page, self.reading_scale_saved)
        self.reading_slider.valueChanged.connect(self._reading_slider_moved)
        form.addRow("Text of messages", row)  # reading and writing alike
        return page

    def _list_page(self) -> QWidget:
        s = self._settings
        page = QWidget(self)
        form = QFormLayout(page)
        self.layout_box = _combo(page, LAYOUTS, prefs.get_text(s, "list_layout", ("columns", "cards")))
        self.pane_box = _combo(page, READING_PANES, prefs.get_text(s, "reading_pane", ("right", "bottom", "off")))
        self.date_box = _combo(page, DATE_STYLES, prefs.get_text(s, "date_style", ("short", "full", "iso")))
        self.density_box = _combo(page, DENSITY_LABELS, prefs.get_text(s, "list_density", prefs.DENSITIES))
        self.conversations = QCheckBox("Show messages grouped as conversations", page)
        self.conversations.setChecked(prefs.get_bool(s, "conversations"))
        self.show_favorites = QCheckBox("Show Favorites at the top of the folder list (all inboxes, unread, flagged, sent …)", page)
        self.show_favorites.setChecked(prefs.get_bool(s, "show_favorites"))
        self.show_local_folders = QCheckBox("Show Local Folders in the folder list", page)
        self.show_local_folders.setChecked(prefs.get_bool(s, "show_local_folders"))
        self.show_tags = QCheckBox("Show Tags in the folder list", page)
        self.show_tags.setChecked(prefs.get_bool(s, "show_tags"))
        self.tag_text_colour = QCheckBox("Use the tag's colour for the text of tagged messages", page)
        self.tag_text_colour.setChecked(prefs.get_bool(s, "tag_text_colour"))
        self.columns_picker = ColumnPicker(self._columns, page)
        form.addRow("Layout", self.layout_box)
        form.addRow("Reading pane", self.pane_box)
        form.addRow("Dates", self.date_box)
        form.addRow("Row height", self.density_box)
        form.addRow(self.conversations)
        form.addRow(self.show_favorites)
        form.addRow(self.show_local_folders)
        form.addRow(self.show_tags)
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
        self.mark_read_box = _combo(page, MARK_READ_LABELS, prefs.get_text(s, "mark_read", prefs.MARK_READ))
        self.mark_read_delay = QSpinBox(page)
        self.mark_read_delay.setRange(0, 10_000)
        self.mark_read_delay.setSingleStep(100)
        self.mark_read_delay.setSuffix(" ms")
        self.mark_read_delay.setValue(prefs.get_int(s, "mark_read_delay_ms", 0, 10_000))
        self.prefer_plain = QCheckBox("Show the plain text of a message that has both text and HTML", page)
        self.prefer_plain.setChecked(prefs.get_bool(s, "prefer_plain"))
        self.links_ask = QCheckBox("Ask before opening a link in the web browser", page)
        self.links_ask.setChecked(prefs.get_bool(s, "links_ask"))
        form.addRow("Mark a message as read", self.mark_read_box)
        form.addRow("…after", self.mark_read_delay)
        form.addRow(self.prefer_plain)
        form.addRow(self.links_ask)
        layout.addLayout(form)
        privacy = QGroupBox("Pictures and other content from the internet", page)
        privacy_layout = QVBoxLayout(privacy)
        self.remote_always = QCheckBox("Always show it, for every sender (tells senders that you read their message)", privacy)
        self.remote_always.setChecked(prefs.get_bool(s, "remote_always"))
        privacy_layout.addWidget(self.remote_always)
        privacy_layout.addWidget(QLabel("Senders whose content is always shown (one address per line):", privacy))
        self.remote_senders = QPlainTextEdit(privacy)
        self.remote_senders.setPlainText("\n".join(sorted(prefs.allowed_senders(s))))
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
        self.compose_html.setCurrentIndex(0 if prefs.get_bool(s, "compose_html") else 1)
        self.reply_quote_box = _combo(page, QUOTE_LABELS, prefs.get_text(s, "reply_quote", prefs.REPLY_QUOTES))
        self.autosave = QSpinBox(page)
        self.autosave.setRange(0, 600)
        self.autosave.setSpecialValueText("Never")
        self.autosave.setSuffix(" seconds")
        self.autosave.setValue(prefs.get_int(s, "autosave_seconds", 0, 600))
        self.warn_no_subject = QCheckBox("Ask before sending a message without a subject", page)
        self.warn_no_subject.setChecked(prefs.get_bool(s, "warn_no_subject"))
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
        self.edit_button = QPushButton("Change…", page)
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
        self.verify_button.clicked.connect(lambda: self._maintain("store.verify", "Checking"))
        self.repair_button.clicked.connect(lambda: self._maintain("index.reconcile", "Repairing"))
        self.rebuild_button.clicked.connect(lambda: self._maintain("index.rebuild", "Rebuilding"))
        self._backend.call("key.status", {}, self._key_status, lambda error: self.key_label.setText(str(error)), owner=self)
        return page

    def _key_status(self, result: object) -> None:
        if getattr(result, "key_id") is None:
            self.key_label.setText("No account uses an encryption key.")
        elif getattr(result, "in_credential_store"):
            self.key_label.setText("The encryption key is in this computer's credential store. Keep a copy of the recovery code somewhere safe: without the key the mail cannot be read.")
        else:
            self.key_label.setText(f"<span style='color:{red()}'>The encryption key is NOT in the credential store. Use “Restore the key…”.</span>")

    def _maintain(self, command: str, verb: str) -> None:
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

        def listed(result: object) -> None:
            # Accounts that keep their mail in Local Folders share its store and index: the Local Folders line covers them
            accounts = [str(getattr(account, "account_id")) for account in (result if isinstance(result, (tuple, list)) else []) if getattr(account, "storage", "own") != "local"]
            names = {str(getattr(account, "account_id")): str(getattr(account, "label")) for account in (result if isinstance(result, (tuple, list)) else [])}
            if not accounts:
                finished()
                return
            remaining = list(accounts)

            def make_done(account: str) -> Callable[[object], None]:
                def done(outcome: object) -> None:
                    if command == "store.verify":
                        damaged = tuple(getattr(outcome, "damaged"))
                        report.append(f"{names[account]}: {getattr(outcome, 'checked')} messages checked, " + (f"<span style='color:{red()}'>{len(damaged)} damaged</span>" if damaged else "all can be read"))
                    elif command == "index.reconcile":
                        changes = f"{getattr(outcome, 'removed')} removed (no file), {getattr(outcome, 'added')} added, {getattr(outcome, 'corrected')} corrected"
                        report.append(f"{names[account]}: {changes}")
                    else:
                        report.append(f"{names[account]}: {getattr(outcome, 'messages')} messages indexed")
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
                self._backend.call(command, {"account": account}, make_done(account), make_failed(account), owner=self)

        self._backend.call("account.list", {}, listed, lambda error: finished(), owner=self)

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
        chosen = prefs.shortcuts(self._settings)
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

        def listed(result: object) -> None:
            self.accounts.clear()
            keep = str(self.default_account.currentData() or self._settings.value("default_account", ""))
            self.default_account.clear()
            self.default_account.addItem("The account of the folder I am in", "")
            for account in list(result) if isinstance(result, (tuple, list)) else []:
                if str(getattr(account, "kind", "pop3")) == "local":
                    continue  # Local Folders is not an account: it is shown through the accounts that keep their mail there
                item = QListWidgetItem(f"{getattr(account, 'display_name')}  <{getattr(account, 'email_address')}>  - {getattr(account, 'server_label')}")
                item.setData(ROLE_ACCOUNT, account)
                self.accounts.addItem(item)
                self.default_account.addItem(str(getattr(account, "label")), str(getattr(account, "account_id")))
                if str(getattr(account, "account_id")) == selected:
                    self.accounts.setCurrentItem(item)
            self.default_account.setCurrentIndex(max(0, self.default_account.findData(keep)))
            if self.accounts.count() and self.accounts.currentRow() < 0:
                self.accounts.setCurrentRow(0)
            self._selection_changed()

        self._backend.call("account.list", {}, listed, lambda error: self.status.setText(f"The accounts could not be listed: {error}"), owner=self)

    def _selected(self) -> object | None:
        item = self.accounts.currentItem()
        return None if item is None else item.data(ROLE_ACCOUNT)

    def _selected_id(self) -> str | None:
        account = self._selected()
        return None if account is None else str(getattr(account, "account_id"))

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
            f"Remove the account {getattr(account, 'email_address')} from Chrono Mail?\n\n"
            "The program forgets the account and its passwords. The mail stored on this computer is not deleted, "
            "and nothing is removed from the server.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result: object) -> None:
            self.status.setText("Account removed")
            self.reload()
            self.accounts_changed.emit()

        def failed(error: ChronoMailError) -> None:
            QMessageBox.warning(self, "Remove account", f"The account could not be removed: {error}")

        self._backend.call("account.remove", {"account": str(getattr(account, "account_id"))}, done, failed, owner=self)

    # --- saving -----------------------------------------------------------------------------------------------------

    def values(self) -> dict[str, object]:
        """Every setting of the dialog under its key in the settings file."""
        senders = sorted({line.strip().lower() for line in self.remote_senders.toPlainText().splitlines() if "@" in line})
        return {
            "startup_view": str(self.startup_box.currentData() or ""), "check_on_start": self.check_on_start.isChecked(), "auto_check": self.auto_check.isChecked(), "auto_check_minutes": self.interval.value(),
            "notifications": self.notifications.isChecked(), "tray": self.tray.isChecked(), "close_to_tray": self.close_to_tray.isChecked(),
            "color_scheme": str(self.scheme_box.currentData()), "style": str(self.style_box.currentData()), "interface_scale": scale.factor_at(self.interface_slider.value()), "windows_scale": scale.factor_at(self.windows_slider.value()),
            "list_layout": str(self.layout_box.currentData()), "reading_pane": str(self.pane_box.currentData()), "date_style": str(self.date_box.currentData()),
            "list_density": str(self.density_box.currentData()), "list_scale": scale.factor_at(self.list_slider.value()), "conversations": self.conversations.isChecked(),
            "show_favorites": self.show_favorites.isChecked(), "show_local_folders": self.show_local_folders.isChecked(),
            "show_tags": self.show_tags.isChecked(), "tag_text_colour": self.tag_text_colour.isChecked(),
            "columns": ",".join(keep_snoozed_column(self.columns_picker.columns() or DEFAULT_COLUMNS, self._columns)),
            "mark_read": str(self.mark_read_box.currentData()), "mark_read_delay_ms": self.mark_read_delay.value(), "reading_scale": scale.factor_at(self.reading_slider.value()),
            "prefer_plain": self.prefer_plain.isChecked(), "remote_always": self.remote_always.isChecked(), "remote_senders": "\n".join(senders),
            "links_ask": self.links_ask.isChecked(),
            "default_account": str(self.default_account.currentData() or ""), "compose_html": bool(self.compose_html.currentData()),
            "reply_quote": str(self.reply_quote_box.currentData()), "autosave_seconds": self.autosave.value(),
            "warn_no_subject": self.warn_no_subject.isChecked(), 
            "shortcuts": json.dumps({action_id: keys for action_id, keys in self.shortcut_values().items() if keys != self._shortcut_actions[action_id][1]}),
        }

    def save(self) -> None:
        for key, value in self.values().items():
            self._settings.setValue(key, value)

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
        super().accept()
