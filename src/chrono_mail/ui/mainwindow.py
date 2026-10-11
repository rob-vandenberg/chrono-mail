import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QStyledItemDelegate,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError, program_version
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import BY_ID, CARD, DEFAULT_COLUMNS, column_widths, valid_columns
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui import appearance, prefs
from chrono_mail.ui.colours import red
from chrono_mail.ui.composer import ComposeOptions, ComposeWindow
from chrono_mail.ui.conversationbar import ConversationBar
from chrono_mail.ui.delegate import MessageDelegate
from chrono_mail.ui.foldertree import FolderTree
from chrono_mail.ui.messagelistview import MessageListView
from chrono_mail.ui.icons import application_icon, theme_icon
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.models import (
    ACCOUNT_ROLE,
    FOLDER_PATH_ROLE,
    FOLDER_ROLE_ROLE,
    GROUP_ROLE,
    KIND_ROLE,
    PIN_ROLE,
    UNREAD_ROLE,
    FolderTreeModel,
    MessageListModel,
    ViewSpec,
)
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.restorekeydialog import RestoreKeyDialog
from chrono_mail.ui.rulespanel import RulesPanel
from chrono_mail.ui.printing import print_message
from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.sourcedialog import SourceDialog
from chrono_mail.ui.tray import TrayController
from chrono_mail.ui.whendialog import WhenDialog

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.56"

# --- Version history ----------------------------------------------------
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.53: Standard keyboard shortcuts
# v0.0.52: Ctrl+Q marks as read, Quit is Ctrl+Shift+Q
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.48: Dragging messages shows a small badge, not the selected rows
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.45: Local Folders: POP3 accounts can keep their mail in a shared mailbox
# v0.0.44: Folder choices, roles and check interval per account
# v0.0.42: Settings window with pages for everything
# v0.0.41: Message list as columns, column editor, reading pane position, message windows
# v0.0.35: Send later and snooze
# v0.0.34: Junk and unread navigation
# v0.0.33: Rules
# v0.0.32: IMAP IDLE: new mail pushed to the window
# v0.0.26: Version number in the About dialog
# v0.0.25: No other product named in the project
# v0.0.24: Rich text composing
# v0.0.22: Save as .eml, view source, print
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Settings window, account editing, signatures
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.17: Message actions: archive, mark all as read, empty folder, drag and drop
# v0.0.16: Drafts
# v0.0.15: the encryption key is checked at the start and can be restored
# v0.0.14: window icon, shared icon helpers
# v0.0.13: Initial main window

AUTO_CHECK_MINUTES: int = 5
DUE_CHECK_MS: int = 60_000
WATCH_SECONDS: int = 300
WATCH_RETRY_MS: int = 60_000
SORT_CHOICES: tuple[tuple[str, str], ...] = (("date", "Date"), ("subject", "Subject"), ("sender", "Sender"), ("size", "Size"))
_ICONS: dict[str, str] = {"inbox": "mail-folder-inbox", "sent": "mail-folder-outbox", "drafts": "document-edit", "trash": "user-trash", "junk": "mail-mark-junk", "archive": "folder-archive", "other": "folder"}


SIDEBAR_KEY: str = "sidebar"  # stands for the sidebar answer among the folder lists that are awaited


def bool_setting(settings: QSettings, key: str, default: bool) -> bool:
    """QSettings may hand back the text 'true' or 'false' from an ini file."""
    value = settings.value(key, default)
    return value if isinstance(value, bool) else str(value).lower() == "true"


class MainWindow(QMainWindow):
    """The three-pane window: folders, the messages of a folder, and the message being read.

    It contains no mail logic. Everything it does is a command of the engine, run on a worker thread.
    """

    closed_for_good = Signal()  # the window was closed and not merely hidden in the tray: the program should end

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend: Backend = backend
        self.settings: QSettings = settings
        self.setWindowTitle("Chrono Mail")
        self.setWindowIcon(application_icon())
        self.resize(1280, 800)
        self._accounts: list[object] = []
        self._folders: dict[str, list[object]] = {}
        self._account_id: str | None = None
        self._folder: str | None = None
        self._folder_role: str = "other"
        self._tree_kind: str = "folder"
        self._tree_key_kind: str = "folder"  # the kind of the item in the tree: "inboxlink" is a folder shown under the Inboxes heading
        self._status_is_error: bool = False
        self._folder_popup: QMenu | None = None
        self._tree_path: str = ""  # the folder, favorite or group of the item that is selected in the tree
        self._combined_accounts: tuple[str, ...] = ()  # the accounts of the group whose inbox is shown
        self._sidebar: object | None = None  # the answer of sidebar.show: pinned folders and groups
        self._combined: str | None = None  # the favorite that is shown (all inboxes, unread, ...), or None
        self._expanded: set[str] | None = None  # accounts whose folders are open in the tree
        self._shown: tuple[str, str, str] | None = None  # account, folder, message id of what the reader shows
        self._show_request: int = 0
        self._updating_tree: bool = False
        self._advanced: AdvancedSearch | None = None
        self._watching: set[str] = set()  # accounts with a wait for new mail in progress
        self._no_push: set[str] = set()  # accounts whose server cannot tell about new mail
        self._quitting_for_good: bool = False
        self._first_unread_on_load: int = 0  # after the next folder has loaded: select its first (or last) unread message
        self._sort: str = str(settings.value("sort", "date"))
        self._oldest_first: bool = bool_setting(settings, "oldest_first", False)
        self._reselect: str | None = None
        self._composers: list[ComposeWindow] = []
        self._checking: bool = False
        self._build_widgets()
        self._build_actions()
        self._default_shortcuts: dict[str, str] = {name: action.shortcut().toString() for name, action in vars(self).items() if name.startswith("act_") and isinstance(action, QAction)}
        self._build_toolbar()
        self._build_menus()
        self._build_status()
        self._apply_list_layout()
        self._apply_list_look()
        self._apply_reading_pane()
        self._apply_reader_preferences()
        self._apply_shortcuts()
        self._wire()
        self._restore_state()
        self._quitting: bool = False
        self.tray = TrayController(self)
        self.tray.toggle_requested.connect(self.toggle_visibility)
        self.tray.check_requested.connect(self.check_mail)
        self.tray.settings_requested.connect(self._settings_from_tray)
        self.tray.quit_requested.connect(self.quit_program)
        self.tray.set_visible(bool_setting(settings, "tray", True))
        self._due_timer = QTimer(self)
        self._due_timer.timeout.connect(self.run_due)
        self._due_timer.start(DUE_CHECK_MS)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_on_general_interval)
        self._account_timers: dict[str, QTimer] = {}  # accounts that have a check interval of their own
        if bool_setting(settings, "auto_check", True):
            self._timer.start(self._auto_minutes() * 60 * 1000)
        self._mark_read_timer = QTimer(self)
        self._mark_read_timer.setSingleShot(True)
        self._mark_read_timer.timeout.connect(self._mark_shown_as_read)

    # --- construction ---------------------------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.tree_model = FolderTreeModel(self)
        self.tree = FolderTree(self)
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setMinimumWidth(180)

        self.list_model = MessageListModel(self.backend, self)
        self.delegate = MessageDelegate()
        self.plain_delegate = QStyledItemDelegate(self)
        self._layout: str = str(self.settings.value("list_layout", "columns"))
        if self._layout not in ("columns", "cards"):
            self._layout = "columns"
        self._columns: tuple[str, ...] = valid_columns([part for part in str(self.settings.value("columns", "")).split(",") if part])
        self._widths: dict[str, int] = column_widths(str(self.settings.value("column_widths", "")))
        self._reading_pane: str = str(self.settings.value("reading_pane", "right"))
        if self._reading_pane not in ("right", "bottom", "off"):
            self._reading_pane = "right"
        self._date_style: str = str(self.settings.value("date_style", "short"))
        self._message_windows: list[MessageWindow] = []
        self.list_view = MessageListView(self)
        self.list_view.setModel(self.list_model)
        self.list_view.setRootIsDecorated(False)
        self.list_view.setItemsExpandable(False)
        self.list_view.setUniformRowHeights(True)
        self.list_view.setAllColumnsShowFocus(True)
        self.list_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.setDragEnabled(True)
        self.list_view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.list_view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_view.setFrameShape(QTreeView.Shape.NoFrame)
        self.list_view.header().setSectionsMovable(True)
        self.list_view.header().setStretchLastSection(True)
        self.list_view.header().setSectionsClickable(True)
        self.list_view.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        list_pane = QWidget(self)
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        header = QWidget(list_pane)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 10, 6)
        self.sort_button = QToolButton(header)
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_button.setAutoRaise(True)
        self.sort_menu = QMenu(self.sort_button)
        self.sort_button.setMenu(self.sort_menu)
        self.filter_unread = self._filter_button("Unread", header)
        self.filter_flagged = self._filter_button("Flagged", header)
        self.filter_attachments = self._filter_button("Attachments", header)
        self.count_label = QLabel("", header)
        self.filter_chip = QToolButton(header)
        self.filter_chip.setAutoRaise(True)
        self.filter_chip.setText("Advanced filters ✕")
        self.filter_chip.setToolTip("Click to remove the dates and sizes of the advanced search")
        self.filter_chip.hide()
        header_layout.addWidget(self.sort_button)
        header_layout.addStretch(1)
        header_layout.addWidget(self.filter_unread)
        header_layout.addWidget(self.filter_flagged)
        header_layout.addWidget(self.filter_attachments)
        header_layout.addWidget(self.filter_chip)
        header_layout.addWidget(self.count_label)
        list_layout.addWidget(header)
        list_layout.addWidget(self.list_view, 1)

        self.reader = ReaderWidget(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, self)  # the list and the reading pane, side by side or one above the other
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.content_splitter)
        self.content_splitter.addWidget(list_pane)
        self.conversation_bar = ConversationBar(self)
        self.reading_pane = QWidget(self)
        reading_layout = QVBoxLayout(self.reading_pane)
        reading_layout.setContentsMargins(0, 0, 0, 0)
        reading_layout.setSpacing(0)
        reading_layout.addWidget(self.conversation_bar)
        reading_layout.addWidget(self.reader, 1)
        self.content_splitter.addWidget(self.reading_pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([240, 1040])
        self.content_splitter.setSizes([400, 640])
        self.splitter.setChildrenCollapsible(False)
        self.content_splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)
        self._update_sort_menu()

    @staticmethod
    def _filter_button(text: str, parent: QWidget) -> QToolButton:
        button = QToolButton(parent)
        button.setText(text)
        button.setCheckable(True)
        button.setAutoRaise(True)
        return button

    def _action(self, text: str, handler: Callable[..., object], shortcut: str | None = None, icon: str | None = None) -> QAction:
        action = QAction(text, self)
        if icon:
            action.setIcon(theme_icon(icon))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(handler)
        return action

    def _build_actions(self) -> None:
        self.act_new = self._action("New", self.new_message, "Ctrl+N", "mail-message-new")
        self.act_refresh = self._action("Refresh", self.check_mail, "F5", "view-refresh")
        self.act_reply = self._action("Reply", lambda: self.compose_from_selection("reply"), "Ctrl+R", "mail-reply-sender")
        self.act_reply_all = self._action("Reply all", lambda: self.compose_from_selection("reply_all"), "Ctrl+Shift+R", "mail-reply-all")
        self.act_forward = self._action("Forward", lambda: self.compose_from_selection("forward"), "Ctrl+F", "mail-forward")
        self.act_edit_draft = self._action("Edit draft", self.edit_draft, None, "document-edit")
        self.act_delete = self._action("Delete", self.delete_selected, "Delete", "edit-delete")
        self.act_save_as = self._action("Save as…", self.save_selected_as, "Ctrl+Shift+S", "document-save-as")
        self.act_source = self._action("View source", self.view_source, "Ctrl+Alt+U")
        self.act_print = self._action("Print…", self.print_selected, "Ctrl+P", "document-print")
        self.act_open = self._action("Open", self.open_selected, "Return", "document-open")
        self.act_columns = self._action("Message list…", self.edit_columns)
        self.act_junk = self._action("Move to Junk / back to Inbox", lambda: self.junk_selected(self._folder_role != "junk"), "Ctrl+J", "mail-mark-junk")  # one key does both, as the folder decides
        self.act_not_junk = self._action("Not junk", lambda: self.junk_selected(False), None, "mail-mark-notjunk")
        self.act_not_junk.setEnabled(False)
        self.act_next_unread = self._action("Next unread message", lambda: self.go_to_unread(1), "N")
        self.act_previous_unread = self._action("Previous unread message", lambda: self.go_to_unread(-1), "P")
        self.act_archive = self._action("Archive", self.archive_selected, "A", "mail-archive")
        self.act_mark_all_read = self._action("Mark all as read", self.mark_folder_read, icon="mail-mark-read")
        self.act_empty_folder = self._action("Empty folder…", self.empty_folder, icon="edit-clear")
        self.act_empty_folder.setEnabled(False)
        self.act_read = self._action("Mark as read", lambda: self.mark_selected(seen=True), "Ctrl+Q")
        self.act_next_message = self._action("Next message", lambda: self._step_message(1), "F")
        self.act_previous_message = self._action("Previous message", lambda: self._step_message(-1), "B")
        self.act_toggle_read = self._action("Toggle read / unread", self.toggle_read, "R")
        self.act_toggle_flag = self._action("Toggle flag", self.toggle_flag, "Insert")
        self.act_delete_forever = self._action("Delete permanently…", self.delete_permanently, "Shift+Delete", "edit-delete")
        self.act_move_to = self._action("Move to folder…", lambda: self._popup_folders("move"), "Ctrl+Shift+V")
        self.act_quick_move = self._action("Quick move", lambda: self._popup_folders("move"), "M")
        self.act_copy_to = self._action("Copy to folder…", lambda: self._popup_folders("copy"), "Ctrl+Shift+C")
        self.act_quick_copy = self._action("Quick copy", lambda: self._popup_folders("copy"), "C")
        self.act_go_to_folder = self._action("Go to folder…", self.go_to_folder, "G")
        self.act_toggle_folders = self._action("Folder list", self._toggle_folder_list, "F9")
        self.act_toggle_folders.setCheckable(True)
        self.act_toggle_folders.setChecked(True)
        self.act_unread = self._action("Mark as unread", lambda: self.mark_selected(seen=False), "Ctrl+U")
        self.act_flag = self._action("Flag", lambda: self.mark_selected(flagged=True), icon="mail-mark-important")
        self.act_unflag = self._action("Remove flag", lambda: self.mark_selected(flagged=False))
        self.act_add_account = self._action("Add an account…", lambda: self.add_account(), icon="list-add")
        self.act_recovery = self._action("Back up the encryption key…", self.show_recovery)
        self.act_restore_key = self._action("Restore the encryption key…", self.restore_key)
        self.act_settings = self._action("Settings…", lambda: self.open_settings(), "Ctrl+,", "configure")
        self.act_scheduled = self._action("Scheduled and snoozed messages…", self.open_scheduled)
        self.act_snooze = self._action("Snooze…", self.snooze_selected, "S", "appointment-soon")
        self.act_rules = self._action("Rules…", self.open_rules, None, "view-filter")
        self.act_quit = self._action("Quit", self.quit_program, "Ctrl+Shift+Q", "application-exit")
        self.act_search = self._action("Search", self._focus_search, "Ctrl+E")
        self.act_advanced = self._action("Advanced search…", self.advanced_search, "Ctrl+Shift+F")
        self.act_auto = QAction("Check for mail automatically", self)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool_setting(self.settings, "auto_check", True))
        self.act_auto.toggled.connect(self._toggle_auto_check)
        self.act_conversations = QAction("Show conversations", self)
        self.act_conversations.setCheckable(True)
        self.act_conversations.setChecked(bool_setting(self.settings, "conversations", False))
        self.act_new_folder = self._action("New folder…", self.new_folder)
        self.act_rename_folder = self._action("Rename folder…", self.rename_folder)
        self.act_delete_folder = self._action("Delete folder…", self.delete_folder)
        self.act_about = self._action("About Chrono Mail", self._about)

    def _build_toolbar(self) -> None:
        self.toolbar = QToolBar("Main", self)
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(self.toolbar)
        for action in (self.act_new, self.act_refresh):
            self.toolbar.addAction(action)
        self.toolbar.addSeparator()
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            self.toolbar.addAction(action)
        self.toolbar.addSeparator()
        self.mark_button = QToolButton(self)
        self.mark_button.setText("Mark")
        self.mark_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mark_menu = QMenu(self.mark_button)
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            mark_menu.addAction(action)
        self.mark_button.setMenu(mark_menu)
        self.toolbar.addWidget(self.mark_button)
        self.move_button = QToolButton(self)
        self.move_button.setText("Move to")
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_button)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_button.setMenu(self.move_menu)
        self.toolbar.addWidget(self.move_button)
        self.toolbar.addAction(self.act_delete)
        spacer = QWidget(self)
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy().Preferred)
        self.toolbar.addWidget(spacer)
        self.scope = QComboBox(self)
        self.scope.addItem("This folder", "folder")
        self.scope.addItem("All folders", "all")
        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Search (from:  to:  subject:  body:  OR  -word)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(300)
        self.toolbar.addWidget(self.scope)
        self.toolbar.addWidget(self.search_box)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_refresh)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        file_menu.addAction(self.act_recovery)
        file_menu.addAction(self.act_restore_key)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        message_menu = bar.addMenu("&Message")
        message_menu.addAction(self.act_open)
        message_menu.addAction(self.act_edit_draft)
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            message_menu.addAction(action)
        message_menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            message_menu.addAction(action)
        message_menu.addSeparator()
        message_menu.addAction(self.act_toggle_read)
        message_menu.addAction(self.act_toggle_flag)
        message_menu.addSeparator()
        for action in (self.act_move_to, self.act_quick_move, self.act_copy_to, self.act_quick_copy):
            message_menu.addAction(action)
        message_menu.addAction(self.act_archive)
        message_menu.addAction(self.act_snooze)
        message_menu.addAction(self.act_junk)
        message_menu.addAction(self.act_not_junk)
        message_menu.addAction(self.act_delete)
        message_menu.addAction(self.act_delete_forever)
        message_menu.addSeparator()
        for action in (self.act_next_message, self.act_previous_message, self.act_next_unread, self.act_previous_unread, self.act_go_to_folder):
            message_menu.addAction(action)
        message_menu.addSeparator()
        for action in (self.act_save_as, self.act_print, self.act_source):
            message_menu.addAction(action)
        folder_menu = bar.addMenu("F&older")
        for action in (self.act_new_folder, self.act_rename_folder, self.act_delete_folder):
            folder_menu.addAction(action)
        folder_menu.addSeparator()
        folder_menu.addAction(self.act_mark_all_read)
        folder_menu.addAction(self.act_empty_folder)
        view_menu = bar.addMenu("&View")
        view_menu.addAction(self.act_search)
        view_menu.addAction(self.act_toggle_folders)
        view_menu.addAction(self.act_columns)
        pane_menu = view_menu.addMenu("Reading pane")
        self.pane_actions: dict[str, QAction] = {}
        for value, label in (('right', 'At the right'), ('bottom', 'At the bottom'), ('off', 'Off')):
            action = pane_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._reading_pane == value)
            action.triggered.connect(lambda checked=False, chosen=value: self.set_reading_pane(chosen))
            self.pane_actions[value] = action
        view_menu.addAction(self.act_advanced)
        view_menu.addAction(self.act_conversations)
        view_menu.addAction(self.act_auto)
        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _build_status(self) -> None:
        self.status_label = QLabel("", self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _wire(self) -> None:
        self.tree.selectionModel().currentChanged.connect(self._tree_changed)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.messages_dropped.connect(self._messages_dropped)
        self.act_conversations.toggled.connect(self._toggle_conversations)
        self.conversation_bar.message_chosen.connect(self._show_conversation_member)
        self.list_view.selectionModel().currentChanged.connect(self._row_changed)
        self.list_view.customContextMenuRequested.connect(self._list_menu)
        self.list_view.doubleClicked.connect(self._row_double_clicked)
        self.list_view.header().sectionClicked.connect(self._header_clicked)
        self.list_view.header().sectionMoved.connect(self._columns_moved)
        self.list_view.header().sectionResized.connect(self._column_resized)
        self.reader.sender_allowed.connect(self._sender_allowed)
        self.list_view.header().customContextMenuRequested.connect(self._header_menu)
        self.list_model.counts_changed.connect(self._counts_changed)
        self.list_model.load_failed.connect(self._load_failed)
        self.list_model.loaded.connect(self._restore_row_selection)
        self.search_box.returnPressed.connect(self._apply_view)
        self.search_box.textChanged.connect(self._search_text_changed)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.filter_chip.clicked.connect(self._clear_advanced)
        for button in (self.filter_unread, self.filter_flagged, self.filter_attachments):
            button.toggled.connect(self._apply_view)
        self.reader.attachment_save_requested.connect(self._save_attachment)
        self.reader.link_activated.connect(self._open_link)
        self.backend.busy_changed.connect(self._busy_changed)
        self.backend.progress.connect(self._progress)

    # --- state ------------------------------------------------------------------------------------------

    def _restore_state(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        sizes = self.settings.value("splitter2")
        if isinstance(sizes, list) and len(sizes) == 2:
            self.splitter.setSizes([int(size) for size in sizes])
        inner = self.settings.value("content_splitter")
        if isinstance(inner, list) and len(inner) == 2 and self._reading_pane != "off":
            self.content_splitter.setSizes([int(size) for size in inner])

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.tray.visible and bool_setting(self.settings, "close_to_tray", False):
            self.hide()  # the program keeps running in the notification area
            event.ignore()
            return
        for composer in list(self._composers):
            if not composer.close():
                event.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter2", self.splitter.sizes())
        if self._reading_pane != "off":
            self.settings.setValue("content_splitter", self.content_splitter.sizes())
        self.settings.setValue("sort", self._sort)
        self.settings.setValue("oldest_first", self._oldest_first)
        self.settings.setValue("auto_check", self.act_auto.isChecked())
        self.settings.setValue("last_account", self._account_id or "")
        self.settings.setValue("last_folder", self._tree_path)
        self.settings.setValue("last_kind", self._tree_key_kind)
        self._quitting_for_good = True
        self.backend.engine.stop_event.set()  # waits for new mail end now
        self._due_timer.stop()
        for timer in self._account_timers.values():
            timer.stop()
        self.backend.wait_for_done(3000)
        self.tray.set_visible(False)
        event.accept()
        self.closed_for_good.emit()

    def _clear_reader(self) -> None:
        self.reader.clear()
        self.conversation_bar.clear()

    def _toggle_conversations(self, on: bool) -> None:
        self.settings.setValue("conversations", on)
        self.conversation_bar.clear()
        self._apply_view()

    def _show_conversation_member(self, folder: str, message_id: str, seen: bool) -> None:
        """A message of the conversation was chosen in the bar: show it in the reading pane."""
        if self._account_id is None:
            return
        self._mark_read_timer.stop()
        self._show_request += 1
        request, account = self._show_request, self._account_id

        def shown(result: object) -> None:
            if request != self._show_request:
                return
            self._shown = (account, folder, message_id)
            self.reader.show_message(result)
            if not seen and self._mark_read_mode() == "delay":
                self._mark_read_timer.start(prefs.get_int(self.settings, "mark_read_delay_ms", 0, 10_000))

        def failed(error: ChronoMailError) -> None:
            if request == self._show_request:
                self._report("Could not open the message", error)

        self.backend.call("message.show", {"account": account, "folder": folder, "message": message_id}, shown, failed, owner=self)

    def _load_conversation(self, account: str, folder: str, message_id: str, request: int) -> None:
        def arrived(result: object) -> None:
            if request == self._show_request:
                self.conversation_bar.show_thread(list(result) if isinstance(result, tuple) else [], message_id)

        self.backend.call("message.thread", {"account": account, "folder": folder, "message": message_id}, arrived, lambda error: self.conversation_bar.clear(), owner=self)

    # --- tray, notifications, closing ---------------------------------------------------------------------

    def quit_program(self) -> None:
        """Really quit (closing the window may only hide it, when the tray is used)."""
        self._quitting = True
        if not self.close():
            self._quitting = False

    def toggle_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _settings_from_tray(self) -> None:
        if not self.isVisible():
            self.toggle_visibility()
        self.open_settings()

    def _total_unread(self) -> int:
        return sum(int(getattr(info, "unread")) for infos in self._folders.values() for info in infos if str(getattr(info, "role")) == "inbox")

    def _update_unread_summary(self) -> None:
        unread = self._total_unread()
        self.setWindowTitle(f"Chrono Mail ({unread} unread)" if unread else "Chrono Mail")
        self.tray.set_unread(unread)

    def _notify_new_mail(self, new_by_account: dict[str, int]) -> None:
        """A desktop notification for mail that arrived while the window was not in use. It tells how many, never what."""
        total = sum(new_by_account.values())
        if not total or not bool_setting(self.settings, "notifications", True) or self.isActiveWindow():
            return
        names = {str(getattr(account, "account_id")): str(getattr(account, "email_address")) for account in self._accounts}
        if len(new_by_account) > 1:
            lines = [f"{count} new in {names.get(account, account)}" for account, count in sorted(new_by_account.items()) if count]
            self.tray.notify("New mail", "\n".join(lines))
        else:
            self.tray.notify("New mail", f"{total} new message{'s' if total != 1 else ''}")

    def _auto_minutes(self) -> int:
        try:
            minutes = int(str(self.settings.value("auto_check_minutes", AUTO_CHECK_MINUTES)))
        except ValueError:
            return AUTO_CHECK_MINUTES
        return minutes if 1 <= minutes <= 240 else AUTO_CHECK_MINUTES

    def _account_of(self, row: object) -> str:
        """The account a row of the list belongs to: rows of the combined views say so themselves."""
        return str(getattr(row, "account", "") or self._account_id or "")

    def _mail_accounts(self) -> list[object]:
        """The accounts that have servers: Local Folders is a place to keep mail, not an account to check or send from."""
        return [account for account in self._accounts if str(getattr(account, "kind", "pop3")) != "local"]

    def _apply_account_timers(self) -> None:
        """A timer for every account that has an interval of its own (and only while automatic checking is on)."""
        wanted = {
            str(getattr(account, "account_id")): int(getattr(account, "check_minutes", 0)) for account in self._mail_accounts()
            if int(getattr(account, "check_minutes", 0)) > 0 and self.act_auto.isChecked()
        }
        for account_id in list(self._account_timers):
            if account_id not in wanted:
                self._account_timers.pop(account_id).stop()
        for account_id, minutes in wanted.items():
            timer = self._account_timers.get(account_id)
            if timer is None:
                timer = QTimer(self)
                timer.timeout.connect(lambda chosen=account_id: self.check_accounts({chosen}))
                self._account_timers[account_id] = timer
            if timer.interval() != minutes * 60_000 or not timer.isActive():
                timer.start(minutes * 60_000)

    def _start_watchers(self) -> None:
        """For every IMAP account: wait for the server to report new mail (IDLE) and check at once when it does."""
        if not self.act_auto.isChecked():
            return
        for account in self._mail_accounts():
            if str(getattr(account, "kind", "pop3")) == "imap":
                self._watch(str(getattr(account, "account_id")))

    def _watch(self, account_id: str) -> None:
        if account_id in self._watching or self._quitting_for_good or account_id in self._no_push:
            return
        self._watching.add(account_id)

        def waited(result: object) -> None:
            self._watching.discard(account_id)
            if not bool(getattr(result, "supported")):
                self._no_push.add(account_id)  # this server cannot tell: the timer checks
                return
            if self.act_auto.isChecked() and not self._quitting_for_good:
                if bool(getattr(result, "changed")):
                    self.check_mail()
                self._watch(account_id)

        def failed(error: ChronoMailError) -> None:
            self._watching.discard(account_id)
            if not self._quitting_for_good and self.act_auto.isChecked():
                QTimer.singleShot(WATCH_RETRY_MS, lambda: self._watch(account_id))  # a lost connection is tried again after a pause

        self.backend.call("mail.wait", {"account": account_id, "seconds": WATCH_SECONDS}, waited, failed, owner=self, waiting=True)

    def _toggle_auto_check(self, on: bool) -> None:
        self.settings.setValue("auto_check", on)
        if on:
            self._start_watchers()
            self._timer.start(self._auto_minutes() * 60 * 1000)
        else:
            self._timer.stop()
        self._apply_account_timers()

    def run_due(self) -> None:
        """Every minute: send the scheduled messages whose time has come and bring snoozed messages back."""
        if not self._accounts or self._quitting_for_good:
            return
        changes = {"sent": 0, "woken": 0}

        def finish() -> None:
            if changes["sent"] or changes["woken"]:
                self._after_change(f"{changes['sent']} scheduled message(s) sent, {changes['woken']} snoozed message(s) back")

        def sent(result: object) -> None:
            changes["sent"] = int(getattr(result, "sent"))
            for text in getattr(result, "failed"):
                self.show_status(f"Scheduled message: {text}", error=True)
            self.backend.call("message.wake", {}, woke, lambda error: finish(), owner=self)

        def woke(result: object) -> None:
            changes["woken"] = int(getattr(result, "woken"))
            finish()

        self.backend.call("mail.send-due", {}, sent, lambda error: self._report("Scheduled messages", error), owner=self)

    def open_scheduled(self) -> None:
        """Settings > Scheduled messages (also reachable by a key chosen for it)."""
        self.open_settings("Scheduled messages")

    def snooze_selected(self) -> None:
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        dialog = WhenDialog("Snooze", "Bring the message back, unread, at:", "Snooze", self)
        if dialog.exec() != WhenDialog.DialogCode.Accepted:
            return
        account, until = self._account_id, dialog.chosen()
        remaining = len(rows)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Snoozed {len(rows)} message(s) until {until.replace('T', ' ')}")

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining
            remaining -= 1
            self._report("Could not snooze", error)

        for row in rows:
            self.backend.call("message.snooze", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "until": until}, done, failed, owner=self)

    def open_rules(self) -> None:
        """Settings > Rules: what is done with mail as it arrives (also reachable by a key chosen for it)."""
        self.open_settings("Rules")

    def _rule_folders(self) -> list[str]:
        return sorted({str(getattr(info, "path")) for infos in self._folders.values() for info in infos})

    def _rule_folder_now(self) -> tuple[str, str] | None:
        return (self._account_id, self._folder) if self._account_id is not None and self._folder is not None and self._tree_kind == "folder" else None

    def _shortcut_registry(self) -> dict[str, tuple[str, str]]:
        """Every action that can be given keys: its name, the text, and the standard keys."""
        return {name[4:]: (action.text(), self._default_shortcuts.get(name, "")) for name, action in vars(self).items() if name.startswith("act_") and isinstance(action, QAction)}

    def open_settings(self, page: str | None = None) -> None:
        """File > Settings: the way the program behaves, how it looks, writing, the accounts, rules, scheduled messages, the keys and the safety of the mail."""
        dialog = SettingsDialog(
            self.backend, self.settings, self.tray.available, self._shortcut_registry(), self,
            accounts=self._mail_accounts(), folders=self._rule_folders, current_folder=self._rule_folder_now,
        )
        if page is not None:
            dialog.show_page(page)
        dialog.add_account_requested.connect(lambda: self.add_account(after=dialog.reload))
        dialog.accounts_changed.connect(lambda: self.reload_accounts(select_saved=False))
        dialog.restore_key_requested.connect(self.restore_key)
        accepted = dialog.exec() == SettingsDialog.DialogCode.Accepted
        if dialog.rules_panel.ran:  # rules and scheduled messages act at once, also when the settings are cancelled
            self._after_change("Rules applied")
        elif dialog.scheduled_panel.changed:
            self._after_change("Scheduled messages changed")
        if not accepted:
            return
        dialog.save()
        self.apply_preferences()

    def apply_preferences(self) -> None:
        """Take the settings (as they are in the settings file now) into use."""
        settings = self.settings
        app = QApplication.instance()
        if isinstance(app, QApplication):
            appearance.apply(app, settings)  # the colours and the style take effect at once
            self._restyle_status()  # the red of an error follows the colours
        self.tray.set_visible(prefs.get_bool(settings, "tray"))
        minutes = self._auto_minutes()
        auto = prefs.get_bool(settings, "auto_check")
        if auto and self.act_auto.isChecked():
            self._timer.start(minutes * 60 * 1000)  # a new interval
        self.act_auto.setChecked(auto)
        self.act_conversations.setChecked(prefs.get_bool(settings, "conversations"))
        self._layout = prefs.get_text(settings, "list_layout", ("columns", "cards"))
        self._columns = valid_columns([part for part in str(settings.value("columns", "")).split(",") if part])
        self._date_style = prefs.get_text(settings, "date_style", ("short", "full", "iso"))
        self._apply_list_layout()
        self._apply_list_look()
        self.set_reading_pane(prefs.get_text(settings, "reading_pane", ("right", "bottom", "off")))
        self._apply_reader_preferences()
        self._apply_shortcuts()
        if self._accounts:
            self._populate_tree()  # the sections of the folder list follow the settings

    def _apply_list_look(self) -> None:
        size = prefs.get_int(self.settings, "list_font_size", 0, 24)
        font = QApplication.font()
        if size > 0:
            font.setPointSize(size)
        self.list_view.setFont(font)
        padding = {"compact": 0, "normal": 2, "roomy": 6}[prefs.get_text(self.settings, "list_density", prefs.DENSITIES)]
        self.list_view.setStyleSheet(f"QTreeView::item {{ padding-top: {padding}px; padding-bottom: {padding}px; }}")

    def _apply_reader_preferences(self) -> None:
        for reader in (self.reader, *(window.reader for window in self._message_windows)):
            reader.always_remote = prefs.get_bool(self.settings, "remote_always")
            reader.allowed_senders = prefs.allowed_senders(self.settings)
            reader.prefer_plain = prefs.get_bool(self.settings, "prefer_plain")
            reader.set_zoom(prefs.get_int(self.settings, "reader_zoom", 50, 300))

    def _sender_allowed(self, address: str) -> None:
        senders = prefs.allowed_senders(self.settings) | {address.lower()}
        self.settings.setValue("remote_senders", "\n".join(sorted(senders)))

    def _apply_shortcuts(self) -> None:
        chosen = prefs.shortcuts(self.settings)
        for name, action in vars(self).items():
            if name.startswith("act_") and isinstance(action, QAction) and name in self._default_shortcuts:
                action.setShortcut(QKeySequence(chosen.get(name[4:], self._default_shortcuts[name])))
                self._scope_shortcut(action)

    def _scope_shortcut(self, action: QAction) -> None:
        """A key that is a letter by itself (A, N, S ...) works in the message list and the folder list only: it is text anywhere else."""
        text = action.shortcut().toString()
        letter = len(text) == 1 and text.isalpha()
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut if letter else Qt.ShortcutContext.WindowShortcut)
        if letter:
            for widget in (self.list_view, self.tree):
                widget.addAction(action)

    def _mark_read_mode(self) -> str:
        return prefs.get_text(self.settings, "mark_read", prefs.MARK_READ)

    def _compose_options(self) -> ComposeOptions:
        return ComposeOptions(
            prefs.get_int(self.settings, "autosave_seconds", 0, 600), prefs.get_bool(self.settings, "warn_no_subject"),
            prefs.get_int(self.settings, "compose_font_size", 0, 24), prefs.get_text(self.settings, "reply_quote", prefs.REPLY_QUOTES) == "below",
        )

    # --- status ---------------------------------------------------------------------------------------

    def show_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self._status_is_error = error
        self._restyle_status()

    def _restyle_status(self) -> None:
        self.status_label.setStyleSheet(f"color: {red()}" if self._status_is_error else "")

    def _load_failed(self, text: str) -> None:
        self.show_status(f"Could not load the messages: {text}", error=True)

    def _busy_changed(self, count: int) -> None:
        self.progress_bar.setVisible(count > 0)

    def _progress(self, what: str, done: int, total: object) -> None:
        self.show_status(f"{what}… {done}" + (f" of {total}" if total else ""))

    def _counts_changed(self, total: int, unread: int) -> None:
        self.count_label.setText(f"{total:,} messages" + (f", {unread:,} unread" if unread else ""))

    def _report(self, title: str, error: ChronoMailError) -> None:
        self.show_status(f"{title}: {error}", error=True)

    # --- accounts and folders ----------------------------------------------------------------------------

    def start(self) -> None:
        """Called once the window is shown: make sure the key is there, load the accounts (or ask for the
        first one), then check for mail."""
        check = self.check_mail if bool_setting(self.settings, "check_on_start", True) else None
        self._with_key(lambda: self.reload_accounts(select_saved=True, then=check))

    def _with_key(self, then: Callable[[], None]) -> None:
        """Continue when the encryption key can be read; otherwise ask for it first."""

        def got(status: object) -> None:
            if getattr(status, "key_id") is not None and not getattr(status, "in_credential_store"):
                self._ask_for_key(then)
            else:
                then()

        def failed(error: ChronoMailError) -> None:
            self._report("Could not check the encryption key", error)
            then()

        self.backend.call("key.status", {}, got, failed, owner=self)

    def _ask_for_key(self, then: Callable[[], None]) -> None:
        dialog = RestoreKeyDialog(self.backend, self)
        if dialog.exec() == RestoreKeyDialog.DialogCode.Accepted:
            self.backend.engine.forget_keys()
            self.show_status("The encryption key is back.")
            then()
        else:
            self.show_status("The encryption key is missing, so your mail cannot be read yet. Use File > Restore the encryption key.", error=True)

    def restore_key(self) -> None:
        """File > Restore the encryption key: ask for the key and, when it is back, load everything."""
        self._ask_for_key(lambda: self.reload_accounts(select_saved=True))

    def reload_accounts(self, select_saved: bool = False, then: Callable[[], None] | None = None) -> None:
        def listed(result: object) -> None:
            self._accounts = list(result) if isinstance(result, (tuple, list)) else []
            if not self._accounts:
                self._folders = {}
                self.tree_model.populate([], {})
                self.add_account(first=True)
                return
            self._load_folders(select_saved, then)
            self._start_watchers()
            self._apply_account_timers()

        self.backend.call("account.list", {}, listed, lambda error: self._report("Accounts", error), owner=self)

    def _load_folders(self, select_saved: bool, then: Callable[[], None] | None = None) -> None:
        remaining = {str(getattr(account, "account_id")) for account in self._accounts} | {SIDEBAR_KEY}
        collected: dict[str, list[object]] = {}

        def sidebar_done(result: object) -> None:
            self._sidebar = result
            remaining.discard(SIDEBAR_KEY)
            if not remaining:
                finish_loading()

        def sidebar_failed(error: ChronoMailError) -> None:
            self._sidebar = None
            remaining.discard(SIDEBAR_KEY)
            self._report("Favorites and groups", error)
            if not remaining:
                finish_loading()

        def finish_loading() -> None:
            self._folders = collected
            self._populate_tree(select_saved)
            self._update_unread_summary()
            if then is not None:
                then()

        def make_done(account_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                collected[account_id] = list(result) if isinstance(result, (tuple, list)) else []
                remaining.discard(account_id)
                if not remaining:
                    finish_loading()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                collected[account_id] = []
                remaining.discard(account_id)
                self._report("Folders", error)
                if not remaining:
                    finish_loading()

            return failed

        self.backend.call("sidebar.show", {}, sidebar_done, sidebar_failed, owner=self)
        for account_id in list(remaining - {SIDEBAR_KEY}):
            self.backend.call("folder.list", {"account": account_id}, make_done(account_id), make_failed(account_id), owner=self)

    def _group_accounts(self, group_id: str) -> tuple[str, ...]:
        for group in getattr(self._sidebar, "groups", ()):
            if str(getattr(group, "group_id")) == group_id:
                return tuple(str(member) for member in getattr(group, "accounts"))
        return ()

    def _selection_key(self) -> tuple[str, str, str] | None:
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return (str(index.data(ACCOUNT_ROLE)), str(index.data(KIND_ROLE)), str(index.data(FOLDER_PATH_ROLE) or ""))

    @staticmethod
    def _key_of(index: QModelIndex) -> tuple[str, str, str]:
        return (str(index.data(ACCOUNT_ROLE)), str(index.data(KIND_ROLE)), str(index.data(FOLDER_PATH_ROLE) or ""))

    def _populate_tree(self, select_saved: bool = False) -> None:
        """Rebuild the tree (new counts) and keep the selection; the message list is only reloaded when the selection really changed."""
        keep = self._selection_key()
        if keep is None and select_saved:
            saved_account, saved_folder = str(self.settings.value("last_account", "")), str(self.settings.value("last_folder", ""))
            saved_kind = str(self.settings.value("last_kind", "folder"))
            keep = (saved_account, saved_kind, saved_folder) if saved_folder and (saved_account or saved_kind in ("favorite", "groupinbox")) else None
        self._updating_tree = True
        self._remember_expanded()
        self.tree_model.pins = [(str(getattr(pin, "pin_id")), str(getattr(pin, "account")), str(getattr(pin, "folder")), str(getattr(pin, "name"))) for pin in getattr(self._sidebar, "pins", ())]
        self.tree_model.groups = [(str(getattr(group, "group_id")), str(getattr(group, "name")), tuple(str(m) for m in getattr(group, "accounts"))) for group in getattr(self._sidebar, "groups", ())]
        self.tree_model.hidden = {str(view) for view in getattr(self._sidebar, "hidden", ())}
        self.tree_model.show_favorites = bool_setting(self.settings, "show_favorites", True)
        self.tree_model.show_inboxes = bool_setting(self.settings, "show_inboxes", True)
        self.tree_model.show_local_folders = bool_setting(self.settings, "show_local_folders", True)
        self.tree_model.populate(self._accounts, self._folders)
        target = self._find_tree_item(keep) if keep is not None else None
        if target is None:
            target = self._first_inbox()
        self._restore_expanded(target)
        if target is not None and target.isValid():
            self.tree.setCurrentIndex(target)
        self._updating_tree = False
        if target is not None and target.isValid() and self._key_of(target) != (self._account_id, self._tree_key_kind, self._tree_path):
            self._tree_changed(target, QModelIndex())

    def _remember_expanded(self) -> None:
        """Which accounts are open in the tree: with many accounts only the ones in use stay open."""
        if not self.tree_model.rowCount():
            return
        self._expanded = {
            str(index.data(ACCOUNT_ROLE)) for row in range(self.tree_model.rowCount())
            for index in (self.tree_model.index(row, 0),) if index.data(KIND_ROLE) == "account" and self.tree.isExpanded(index)
        }

    def _restore_expanded(self, selected: QModelIndex | None) -> None:
        chosen = str(selected.data(ACCOUNT_ROLE)) if selected is not None and selected.isValid() else ""
        accounts = [self.tree_model.index(row, 0) for row in range(self.tree_model.rowCount()) if self.tree_model.index(row, 0).data(KIND_ROLE) == "account"]
        for row in range(self.tree_model.rowCount()):
            index = self.tree_model.index(row, 0)
            if index.data(KIND_ROLE) == "section":
                self.tree.expand(index)
        for index in accounts:
            account = str(index.data(ACCOUNT_ROLE))
            opened = account in self._expanded if self._expanded is not None else len(accounts) <= 3
            self.tree.setExpanded(index, opened or account == chosen)

    def _find_tree_item(self, key: tuple[str, str, str]) -> QModelIndex | None:
        def walk(parent: QModelIndex) -> QModelIndex | None:
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if (str(index.data(ACCOUNT_ROLE)), str(index.data(KIND_ROLE)), str(index.data(FOLDER_PATH_ROLE) or "")) == key:
                    return index
                found = walk(index)
                if found is not None:
                    return found
            return None

        return walk(QModelIndex())

    def _first_inbox(self) -> QModelIndex | None:
        for row in range(self.tree_model.rowCount(QModelIndex())):
            account = self.tree_model.index(row, 0, QModelIndex())
            if account.data(KIND_ROLE) != "account":
                continue
            for child in range(self.tree_model.rowCount(account)):
                index = self.tree_model.index(child, 0, account)
                if index.data(FOLDER_ROLE_ROLE) == "inbox":
                    return index
        return None

    def _tree_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if self._updating_tree or not current.isValid():
            return
        kind = str(current.data(KIND_ROLE))
        if kind in ("account", "section", "group"):
            return
        self._tree_key_kind = kind
        self._tree_path = str(current.data(FOLDER_PATH_ROLE) or "")
        self._combined = "inboxes" if kind == "groupinbox" else (str(current.data(FOLDER_PATH_ROLE)) if kind == "favorite" else None)
        self._combined_accounts = self._group_accounts(str(current.data(GROUP_ROLE))) if kind == "groupinbox" else ()
        if kind in ("inboxlink", "pinlink"):
            kind = "folder"
        self._account_id = str(current.data(ACCOUNT_ROLE))  # "" in a favorite: the rows say which account they belong to
        self._tree_kind = kind
        self._folder = str(current.data(FOLDER_PATH_ROLE)) if kind == "folder" else None
        self._folder_role = str(current.data(FOLDER_ROLE_ROLE) or "other")
        self.act_empty_folder.setEnabled(kind == "folder" and self._folder_role in ("trash", "junk"))
        self.act_not_junk.setEnabled(kind == "folder" and self._folder_role == "junk")
        self.act_mark_all_read.setEnabled(kind == "folder")
        self._select_quick_filters_for(kind)
        self._advanced = None
        self.filter_chip.hide()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._clear_reader()
        self._shown = None
        self._apply_view()

    def _select_quick_filters_for(self, kind: str) -> None:
        for button, wanted in ((self.filter_unread, kind == "unread"), (self.filter_flagged, kind == "flagged")):
            button.blockSignals(True)
            button.setChecked(wanted)
            button.blockSignals(False)
        self.filter_attachments.blockSignals(True)
        self.filter_attachments.setChecked(False)
        self.filter_attachments.blockSignals(False)

    def _search_text_changed(self, text: str) -> None:
        if not text:
            self._advanced = None
            self.filter_chip.hide()
            self._apply_view()

    def _scope_changed(self) -> None:
        if self.search_box.text() or self._advanced is not None:
            self._apply_view()

    def _clear_advanced(self) -> None:
        self._advanced = None
        self.filter_chip.hide()
        self._apply_view()

    def advanced_search(self) -> None:
        """Ask for several conditions at once and show the result in the message list."""
        if self._account_id is None:
            return
        dialog = AdvancedSearchDialog(self)
        if dialog.exec() != AdvancedSearchDialog.DialogCode.Accepted:
            return
        wanted = dialog.search()
        self._advanced = wanted if wanted.extra_filters else None
        self.filter_chip.setVisible(self._advanced is not None)
        self.search_box.blockSignals(True)
        self.search_box.setText(wanted.query)
        self.search_box.blockSignals(False)
        self.scope.blockSignals(True)
        self.scope.setCurrentIndex(1 if wanted.all_folders else 0)
        self.scope.blockSignals(False)
        for button, on in ((self.filter_unread, wanted.unread), (self.filter_flagged, wanted.flagged), (self.filter_attachments, wanted.attachments)):
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
        self._apply_view()

    def _focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()

    # --- the message list ------------------------------------------------------------------------------

    def view_spec(self) -> ViewSpec | None:
        if self._account_id is None:
            return None
        if self._combined is not None:
            return ViewSpec("", None, self.search_box.text().strip(), combined=self._combined, combined_accounts=self._combined_accounts)
        query = self.search_box.text().strip()
        unread = True if self.filter_unread.isChecked() else None
        flagged = True if self.filter_flagged.isChecked() else None
        attachments = True if self.filter_attachments.isChecked() else None
        folder = self._folder
        advanced = self._advanced
        if (query or advanced is not None) and self.scope.currentData() == "all":
            folder = None
        elif self._tree_kind in ("unread", "flagged"):
            folder = None
        grouped = self.act_conversations.isChecked() and self._tree_kind == "folder" and not (query or unread or flagged or attachments or self._advanced is not None)
        return ViewSpec(
            self._account_id, folder, query, self._sort, self._oldest_first, unread, flagged, attachments,
            date_from=advanced.date_from if advanced else None, date_to=advanced.date_to if advanced else None,
            minimum_size=advanced.minimum_size if advanced else None, maximum_size=advanced.maximum_size if advanced else None,
            conversations=grouped,
        )

    def _apply_view(self) -> None:
        self.delegate.show_recipients = self._folder_role in ("sent", "drafts") or self._folder == "Outbox" or self._combined == "outbox"
        self.list_model.recipients_first = self.delegate.show_recipients
        self.list_model.show(self.view_spec())

    def _update_sort_menu(self) -> None:
        self.sort_menu.clear()
        for value, label in SORT_CHOICES:
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == self._sort)
            action.triggered.connect(lambda checked=False, chosen=value: self._set_sort(chosen, self._oldest_first))
        self.sort_menu.addSeparator()
        for flag, label in ((False, "Newest / Z–A / largest first"), (True, "Oldest / A–Z / smallest first")):
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(flag == self._oldest_first)
            action.triggered.connect(lambda checked=False, chosen=flag: self._set_sort(self._sort, chosen))
        label = dict(SORT_CHOICES)[self._sort]
        self.sort_button.setText(f"Sorted by {label} ▾")

    def _set_sort(self, sort: str, oldest_first: bool) -> None:
        self._sort, self._oldest_first = sort, oldest_first
        self._update_sort_menu()
        self._apply_view()
        if self._layout == "columns":
            self._show_sort_indicator()

    # --- reading ------------------------------------------------------------------------------------------

    def _current_row(self) -> object:
        index = self.list_view.currentIndex()
        return self.list_model.row_at(index.row()) if index.isValid() else None

    def _selected_rows(self) -> list[object]:
        rows: list[object] = []
        for index in self.list_view.selectionModel().selectedRows():
            row = self.list_model.row_at(index.row())
            if row is not None:
                rows.append(row)
        return rows

    def _row_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._mark_read_timer.stop()
        if self._reading_pane == "off":
            return  # nothing is shown, so nothing is read: a message is opened in a window of its own
        row = self.list_model.row_at(current.row()) if current.isValid() else None
        if row is None or self._account_id is None:
            if not self.list_view.selectionModel().hasSelection():
                self._clear_reader()
                self._shown = None
            return
        folder, message_id = str(getattr(row, "folder")), str(getattr(row, "message_id"))
        account = self._account_of(row)
        if self._shown == (account, folder, message_id):
            return
        self._show_request += 1
        request = self._show_request

        def shown(result: object) -> None:
            if request != self._show_request:
                return
            self._shown = (account, folder, message_id)
            self.reader.show_message(result)
            if int(getattr(row, "thread_size", 1)) > 1:
                self._load_conversation(account, folder, message_id, request)
            else:
                self.conversation_bar.clear()
            if "seen" not in getattr(row, "flags") and self._mark_read_mode() == "delay":
                self._mark_read_timer.start(prefs.get_int(self.settings, "mark_read_delay_ms", 0, 10_000))

        def failed(error: ChronoMailError) -> None:
            if request == self._show_request:  # an answer for a message that is no longer the one being opened is of no interest
                self._report("Could not open the message", error)

        self.backend.call("message.show", {"account": account, "folder": folder, "message": message_id}, shown, failed, owner=self)

    def _mark_shown_as_read(self) -> None:
        if self._shown is None:
            return
        account, folder, message_id = self._shown
        self._set_flags([(folder, message_id)], {"seen": True}, account)

    def _select_unread_after_load(self) -> None:
        step, self._first_unread_on_load = self._first_unread_on_load, 0
        if not step:
            return
        count = self.list_model.rowCount()
        positions = range(count) if step > 0 else range(count - 1, -1, -1)
        for position in positions:
            row = self.list_model.row_at(position)
            if row is not None and "seen" not in getattr(row, "flags"):
                self.list_view.setCurrentIndex(self.list_model.index(position))
                return

    def _restore_row_selection(self) -> None:
        self._select_unread_after_load()
        pending = getattr(self, "_reselect", None)
        if not pending:
            return
        position = self.list_model.find(pending)
        if position >= 0:
            self._reselect = None
            index = self.list_model.index(position)
            self.list_view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    # --- actions on messages --------------------------------------------------------------------------------

    def _set_flags(self, targets: list[tuple[str, str]], values: dict[str, bool], account: str | None = None) -> None:
        account_id = account or self._account_id
        if account_id is None or not targets:
            return
        remaining = len(targets)

        def make_done(message_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                nonlocal remaining
                self.list_model.replace_flags(message_id, tuple(getattr(result, "flags")))
                remaining -= 1
                if remaining == 0:
                    self._refresh_counts()

            return done

        for folder, message_id in targets:
            self.backend.call("message.flag", {"account": account_id, "folder": folder, "message": message_id, **values}, make_done(message_id), lambda error: self._report("Could not change the message", error), owner=self)

    def mark_selected(self, seen: bool | None = None, flagged: bool | None = None) -> None:
        values: dict[str, bool] = {}
        if seen is not None:
            values["seen"] = seen
        if flagged is not None:
            values["flagged"] = flagged
        rows = self._selected_rows()
        for account in dict.fromkeys(self._account_of(row) for row in rows):  # the combined views mix accounts
            self._set_flags([(str(getattr(row, "folder")), str(getattr(row, "message_id"))) for row in rows if self._account_of(row) == account], values, account)

    def delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        in_trash = self._folder_role == "trash"
        if in_trash and QMessageBox.question(self, "Delete", f"Delete {len(rows)} message(s) for good? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        account = self._account_id
        remaining = len(rows)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Deleted {len(rows)} message(s)" if in_trash else f"Moved {len(rows)} message(s) to Trash")

        for row in rows:
            self.backend.call("message.delete", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, lambda error: self._report("Could not delete", error), owner=self)

    def archive_selected(self) -> None:
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account = self._account_id
        remaining = len(rows)
        created = False

        def done(result: object) -> None:
            nonlocal remaining, created
            created = created or bool(getattr(result, "created_folder"))
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Archived {len(rows)} message(s)")

        for row in rows:
            if str(getattr(row, "folder")) == "Archive":
                remaining -= 1  # already there
                continue
            self.backend.call("message.archive", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, lambda error: self._report("Could not archive", error), owner=self)
        if remaining == 0:
            self.show_status("The selected message(s) are in the archive already")

    def junk_selected(self, junk: bool) -> None:
        """Move the selected messages to the Junk folder, or (in Junk) back to the Inbox."""
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account, command = self._account_id, "message.junk" if junk else "message.not-junk"
        remaining = len(rows)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Moved {len(rows)} message(s) to Junk" if junk else f"Moved {len(rows)} message(s) back to the Inbox")

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining
            remaining -= 1
            self._report("Could not move", error)

        for row in rows:
            self.backend.call(command, {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, failed, owner=self)

    def _step_message(self, step: int) -> None:
        """Select the next (or the previous) message of the list."""
        count = self.list_model.rowCount()
        current = self.list_view.currentIndex().row() if self.list_view.currentIndex().isValid() else (-1 if step > 0 else count)
        position = current + step
        if 0 <= position < count:
            self.list_view.setCurrentIndex(self.list_model.index(position))
            self.list_view.scrollTo(self.list_model.index(position))

    def toggle_read(self) -> None:
        rows = self._selected_rows()
        if rows:
            self.mark_selected(seen=any("seen" not in getattr(row, "flags") for row in rows))  # all read: unread, else read

    def toggle_flag(self) -> None:
        rows = self._selected_rows()
        if rows:
            self.mark_selected(flagged=any("flagged" not in getattr(row, "flags") for row in rows))

    def delete_permanently(self) -> None:
        """Shift+Delete: remove the selected messages for good, without a stop in Trash, after asking."""
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        if QMessageBox.question(self, "Delete permanently", f"Delete {len(rows)} message(s) for good, without moving them to Trash? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        remaining = len(rows)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Deleted {len(rows)} message(s) for good")

        for row in rows:
            self.backend.call(
                "message.delete", {"account": self._account_of(row) or self._account_id, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "permanent": True},
                done, lambda error: self._report("Could not delete", error), owner=self,
            )

    def _popup_folders(self, how: str) -> None:
        """Move or copy the selected messages: the folders of their account, as a menu at the pointer."""
        rows = self._selected_rows()
        if not rows:
            return
        account = self._account_of(rows[0])
        menu = QMenu(self)
        for info in self._folders.get(account, []):
            path = str(getattr(info, "path"))
            if path == str(getattr(rows[0], "folder")) or path == "Outbox":
                continue
            menu.addAction(path).triggered.connect(lambda checked=False, target=path: self.copy_selected(target) if how == "copy" else self.move_selected(target))
        self._folder_popup = menu  # kept: a menu shown with popup() is gone when its last reference is
        menu.popup(QCursor.pos())

    def copy_selected(self, target: str) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        remaining = len(rows)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Copied {len(rows)} message(s) to {target}")

        for row in rows:
            self.backend.call(
                "message.copy", {"account": self._account_of(row), "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "target": target},
                done, lambda error: self._report("Could not copy", error), owner=self,
            )

    def go_to_folder(self) -> None:
        """G: type a part of a folder's name and go to that folder."""
        names: dict[str, tuple[str, str]] = {}
        accounts = {str(getattr(account, "account_id")): account for account in self._accounts}
        for account_id, infos in self._folders.items():
            account = accounts.get(account_id)
            if account is None or str(getattr(account, "storage", "own")) == "local":
                continue
            label = "Local Folders" if str(getattr(account, "kind", "pop3")) == "local" else str(getattr(account, "email_address"))
            for info in infos:
                names[f"{label} / {getattr(info, 'path')}"] = (account_id, str(getattr(info, "path")))
        if not names:
            return
        chosen, ok = QInputDialog.getItem(self, "Go to folder", "Folder:", sorted(names), 0, True)
        if ok and chosen in names:
            index = self._find_tree_item((names[chosen][0], "folder", names[chosen][1]))
            if index is not None:
                self.tree.setCurrentIndex(index)
                self.list_view.setFocus()

    def _toggle_folder_list(self) -> None:
        self.tree.setVisible(self.act_toggle_folders.isChecked())

    def go_to_unread(self, step: int) -> bool:
        """Select the next (or previous) unread message of the list; at the end of the list, the first unread one of the next folder that has some."""
        count = self.list_model.rowCount()
        current = self.list_view.currentIndex().row() if self.list_view.currentIndex().isValid() else (-1 if step > 0 else count)
        position = current + step
        while 0 <= position < count:
            row = self.list_model.row_at(position)
            if row is not None and "seen" not in getattr(row, "flags"):
                self.list_view.setCurrentIndex(self.list_model.index(position))
                self.list_view.scrollTo(self.list_model.index(position))
                return True
            position += step
        return self._unread_in_another_folder(step)

    def _unread_in_another_folder(self, step: int) -> bool:
        account_index = self.tree_model.index(0, 0)
        for position in range(self.tree_model.rowCount()):
            if str(self.tree_model.index(position, 0).data(ACCOUNT_ROLE)) == (self._account_id or ""):
                account_index = self.tree_model.index(position, 0)
        folders = [
            self.tree_model.index(row, 0, account_index) for row in range(self.tree_model.rowCount(account_index))
            if self.tree_model.index(row, 0, account_index).data(KIND_ROLE) == "folder"
        ]
        keys = [self._key_of(index) for index in folders]
        selected = self._selection_key()
        here = keys.index(selected) if selected is not None and selected in keys else (-1 if step > 0 else len(folders))
        order = range(here + step, len(folders), step) if step > 0 else range(here + step, -1, step)
        for position in order:
            if int(folders[position].data(UNREAD_ROLE) or 0) > 0:
                self._first_unread_on_load = step
                self.tree.setCurrentIndex(folders[position])
                return True
        return False

    def mark_folder_read(self) -> None:
        """Mark every message of the folder being shown as read."""
        if self._account_id is None or self._folder is None:
            return
        folder = self._folder

        def done(result: object) -> None:
            changed = int(getattr(result, "changed"))
            self._after_change(f"Marked {changed} message(s) in {folder} as read" if changed else f"Nothing to mark in {folder}")

        self.backend.call("folder.mark-read", {"account": self._account_id, "folder": folder}, done, lambda error: self._report("Could not mark the messages", error), owner=self)

    def empty_folder(self) -> None:
        """Delete everything in Trash or Junk for good, after asking."""
        if self._account_id is None or self._folder is None or self._folder_role not in ("trash", "junk"):
            return
        folder = self._folder
        answer = QMessageBox.question(self, "Empty folder", f"Delete all messages in {folder} for good? This cannot be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result: object) -> None:
            self._after_change(f"Deleted {int(getattr(result, 'removed'))} message(s) from {folder}")

        self.backend.call("folder.empty", {"account": self._account_id, "folder": folder}, done, lambda error: self._report("Could not empty the folder", error), owner=self)

    def _messages_dropped(self, account: str, target: str, messages: list[tuple[str, str]]) -> None:
        """Messages were dragged to a folder of the tree."""
        remaining = len(messages)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Moved {len(messages)} message(s) to {target}")

        for folder, message_id in messages:
            self.backend.call("message.move", {"account": account, "folder": folder, "message": message_id, "target": target}, done, lambda error: self._report("Could not move", error), owner=self)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        if self._account_id is None:
            return
        for info in self._folders.get(self._account_id, []):
            path = str(getattr(info, "path"))
            if path == self._folder or path == "Outbox":
                continue
            self.move_menu.addAction(path).triggered.connect(lambda checked=False, target=path: self.move_selected(target))

    def move_selected(self, target: str) -> None:
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        remaining = len(rows)
        account = self._account_id

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Moved {len(rows)} message(s) to {target}")

        for row in rows:
            self.backend.call("message.move", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "target": target}, done, lambda error: self._report("Could not move", error), owner=self)

    def _after_change(self, text: str) -> None:
        """Something changed in the store: show the same folder again and update the counts."""
        self.show_status(text)
        self._show_request += 1  # whatever was being opened may no longer exist
        self._clear_reader()
        self._shown = None
        self.list_model.reload()
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        self._load_folders(select_saved=False)

    # --- composing ---------------------------------------------------------------------------------------------

    def new_message(self) -> None:
        if self._account_id is None:
            return
        wanted = str(self.settings.value("default_account", ""))
        known = {str(getattr(account, "account_id")) for account in self._mail_accounts()}
        self._open_composer(wanted if wanted in known else self._account_id, None, None)

    def _open_composer(self, account_id: str, draft: object | None, source: tuple[str, str] | None, to: str = "", resume: tuple[str, str] | None = None) -> ComposeWindow:
        mail_ids = [str(getattr(account, "account_id")) for account in self._mail_accounts()]
        if account_id not in mail_ids:  # Local Folders has no server: the answer comes from the account chosen in the settings, or the first
            wanted = str(self.settings.value("default_account", ""))
            account_id = wanted if wanted in mail_ids else (mail_ids[0] if mail_ids else account_id)
        window = ComposeWindow(self.backend, self._mail_accounts(), account_id, draft, source, resume, bool_setting(self.settings, "compose_html", False), self._compose_options())
        if to:
            window.to.setText(to)
        window.sent.connect(self._after_sent)
        window.draft_saved.connect(self._after_sent)
        window.destroyed.connect(lambda: self._composers.remove(window) if window in self._composers else None)
        self._composers.append(window)
        window.show()
        return window

    def _row_double_clicked(self, index: QModelIndex) -> None:
        self.open_selected()

    def open_selected(self) -> None:
        """Enter or a double click: edit a draft, or read a message in a window of its own."""
        if self._in_drafts():
            self.edit_draft()
            return
        chosen = self._selected_message()
        if chosen is None:
            return
        account, folder, message_id, _ = chosen

        def shown(result: object) -> None:
            window = MessageWindow(self.backend, account, folder, message_id, result, self._compose_in_window, self._open_link)
            self._message_windows.append(window)
            window.destroyed.connect(lambda: self._message_windows.remove(window) if window in self._message_windows else None)
            window.show()
            if self._mark_read_mode() != "never":
                self._set_flags([(folder, message_id)], {"seen": True}, account)
            self._apply_reader_preferences()

        self.backend.call("message.show", {"account": account, "folder": folder, "message": message_id}, shown, lambda error: self._report("Could not open the message", error), owner=self)

    def _compose_in_window(self, kind: str, account: str, folder: str, message_id: str) -> None:
        def drafted(result: object) -> None:
            self._open_composer(str(getattr(result, "from_account", None) or account), result, (folder, message_id))

        self.backend.call(
            "message.compose", {"account": account, "folder": folder, "message": message_id, "kind": kind, "quote": prefs.get_text(self.settings, "reply_quote", prefs.REPLY_QUOTES)},
            drafted, lambda error: self._report("Could not start the message", error), owner=self,
        )

    # --- the message list: layout, columns, reading pane -----------------------------------------------------------

    def _apply_list_layout(self) -> None:
        cards = self._layout == "cards"
        self.list_model.date_style = self._date_style
        header = self.list_view.header()
        if cards:
            self.list_model.set_columns((CARD,))
            header.hide()
            self.list_view.setItemDelegate(self.delegate)
            self.list_view.setUniformRowHeights(True)
        else:
            self.list_model.set_columns(self._columns)
            header.show()
            self.list_view.setItemDelegate(self.plain_delegate)
            for position, column_id in enumerate(self._columns):
                self.list_view.setColumnWidth(position, self._widths.get(column_id, BY_ID[column_id].width))
            header.setSortIndicatorShown(True)
            self._show_sort_indicator()
        self.list_model.refresh_cells()

    def _show_sort_indicator(self) -> None:
        sort_of = {"sender": ("from_to", "from"), "subject": ("subject",), "date": ("date",), "size": ("size",)}
        header = self.list_view.header()
        for position, column_id in enumerate(self.list_model.columns):
            if column_id in sort_of.get(self._sort, ()):
                header.setSortIndicator(position, Qt.SortOrder.AscendingOrder if self._oldest_first else Qt.SortOrder.DescendingOrder)
                return
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def _header_clicked(self, section: int) -> None:
        columns = self.list_model.columns
        if self._layout != "columns" or not 0 <= section < len(columns):
            return
        sort = BY_ID[columns[section]].sort
        if sort is None:
            return
        if sort == self._sort:
            self._set_sort(sort, not self._oldest_first)
        else:
            self._set_sort(sort, sort != "date" and sort != "size")  # text columns A to Z first, dates and sizes newest and largest first
        self._show_sort_indicator()

    def _columns_moved(self, logical: int, old_visual: int, new_visual: int) -> None:
        header = self.list_view.header()
        order = [self.list_model.columns[header.logicalIndex(visual)] for visual in range(header.count())]
        if self._layout == "columns" and tuple(order) != self._columns and set(order) == set(self._columns):
            self._columns = tuple(order)
            self.settings.setValue("columns", ",".join(order))

    def _column_resized(self, section: int, old: int, new: int) -> None:
        columns = self.list_model.columns
        if self._layout == "columns" and 0 <= section < len(columns) and new >= 16 and self.list_view.header().isVisible():
            self._widths[columns[section]] = new
            self.settings.setValue("column_widths", json.dumps(self._widths))

    def _header_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        chosen = set(self._columns)
        for column_id, column in BY_ID.items():
            action = menu.addAction(column.label)
            action.setCheckable(True)
            action.setChecked(column_id in chosen)
            action.triggered.connect(lambda checked=False, wanted=column_id: self._toggle_column(wanted))
        menu.addSeparator()
        menu.addAction(self.act_columns)
        menu.exec(self.list_view.header().mapToGlobal(position))

    def _toggle_column(self, column_id: str) -> None:
        columns = list(self._columns)
        if column_id in columns:
            if len(columns) > 1:
                columns.remove(column_id)
        else:
            columns.append(column_id)
        self._set_columns(tuple(columns))

    def _set_columns(self, columns: tuple[str, ...]) -> None:
        self._columns = columns
        self.settings.setValue("columns", ",".join(columns))
        self._apply_list_layout()

    def edit_columns(self) -> None:
        """View > Message list: layout, columns, reading pane and date style."""
        dialog = ColumnsDialog(ListChoice(self._layout, self._columns, self._reading_pane, self._date_style), self)
        if dialog.exec() != ColumnsDialog.DialogCode.Accepted:
            return
        self.apply_list_choice(dialog.choice())

    def apply_list_choice(self, choice: ListChoice) -> None:
        self._layout, self._columns, self._date_style = choice.layout, choice.columns, choice.date_style
        for key, value in (("list_layout", self._layout), ("columns", ",".join(self._columns)), ("date_style", self._date_style)):
            self.settings.setValue(key, value)
        self._apply_list_layout()
        self.set_reading_pane(choice.reading_pane)

    def set_reading_pane(self, where: str) -> None:
        self._reading_pane = where if where in ("right", "bottom", "off") else "right"
        self.settings.setValue("reading_pane", self._reading_pane)
        self._apply_reading_pane()

    def _apply_reading_pane(self) -> None:
        for value, action in self.pane_actions.items():
            action.setChecked(value == self._reading_pane)
        if self._reading_pane == "off":
            self.reading_pane.hide()
            self._clear_reader()
            self._shown = None
            return
        self.content_splitter.setOrientation(Qt.Orientation.Horizontal if self._reading_pane == "right" else Qt.Orientation.Vertical)
        self.reading_pane.show()
        wide = self._layout == "columns"
        self.content_splitter.setSizes(([640, 400] if wide else [400, 640]) if self._reading_pane == "right" else [320, 380])
        current = self.list_view.currentIndex()
        if current.isValid():
            self._row_changed(current, QModelIndex())

    def _in_drafts(self) -> bool:
        return self._folder_role == "drafts" or self._folder == "Outbox"

    def edit_draft(self) -> None:
        """Open the selected message of Drafts (or Outbox) in the composer, to continue writing or to send it."""
        row = self._current_row()
        if row is None or self._account_id is None or not self._in_drafts():
            return
        account, folder, message_id = self._account_of(row), str(getattr(row, "folder")), str(getattr(row, "message_id"))
        for window in self._composers:
            if window.stored_as() == (folder, message_id):
                window.raise_()
                window.activateWindow()
                return

        def opened(result: object) -> None:
            self._open_composer(account, result, None, resume=(str(getattr(result, "folder")), str(getattr(result, "message_id"))))

        self.backend.call("draft.open", {"account": account, "folder": folder, "message": message_id}, opened, lambda error: self._report("Could not open the draft", error), owner=self)

    def compose_from_selection(self, kind: str) -> None:
        row = self._current_row()
        if row is None or self._account_id is None:
            return
        account, folder, message_id = self._account_of(row), str(getattr(row, "folder")), str(getattr(row, "message_id"))

        def drafted(result: object) -> None:
            self._open_composer(str(getattr(result, "from_account", None) or account), result, (folder, message_id))

        self.backend.call(
            "message.compose", {"account": account, "folder": folder, "message": message_id, "kind": kind, "quote": prefs.get_text(self.settings, "reply_quote", prefs.REPLY_QUOTES)},
            drafted, lambda error: self._report("Could not start the message", error), owner=self,
        )

    def _after_sent(self, account_id: str = "") -> None:
        self._load_folders(select_saved=False)
        if self._folder in ("Sent", "Outbox") or self._folder_role in ("sent", "drafts"):
            self.list_model.reload()

    # --- mail ---------------------------------------------------------------------------------------------------

    def check_mail(self) -> None:
        self.check_accounts(None)

    def _check_on_general_interval(self) -> None:
        """The general timer: the accounts that have no interval of their own."""
        self.check_accounts({str(getattr(account, "account_id")) for account in self._mail_accounts() if int(getattr(account, "check_minutes", 0)) == 0})

    def check_accounts(self, only: set[str] | None) -> None:
        """Check these accounts (all when None) for new mail."""
        mail_ids = {str(getattr(account, "account_id")) for account in self._mail_accounts()}
        wanted = mail_ids if only is None else only & mail_ids
        if self._checking or not wanted:
            return
        self._checking = True
        self.act_refresh.setEnabled(False)
        remaining = set(wanted)
        totals = {"new": 0}
        new_by_account: dict[str, int] = {}
        problems: list[str] = []

        def finish() -> None:
            self._checking = False
            self.act_refresh.setEnabled(True)
            if problems:
                self.show_status("Mail check: " + "; ".join(problems), error=True)
            else:
                self.show_status(f"Mail checked: {totals['new']} new message(s)" if totals["new"] else "Mail checked: nothing new")
            self._notify_new_mail(new_by_account)
            if totals["new"]:
                self._reselect = self._shown[2] if self._shown is not None else None  # keep the message that is being read
                self.list_model.reload()
            self._load_folders(select_saved=False)

        def make_done(account_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                totals["new"] += int(getattr(result, "downloaded"))
                new_by_account[account_id] = int(getattr(result, "downloaded"))
                for text in getattr(result, "failed"):
                    problems.append(str(text))
                if getattr(result, "aborted"):
                    problems.append(str(getattr(result, "aborted")))
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                problems.append(str(error))
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return failed

        self.show_status("Checking for mail…")
        for account_id in list(remaining):
            self.backend.call("mail.fetch", {"account": account_id}, make_done(account_id), make_failed(account_id), owner=self)

    def add_account(self, first: bool = False, after: Callable[[], None] | None = None) -> None:
        dialog = AccountDialog(self.backend, first_account=first, parent=self)
        if dialog.exec() != AccountDialog.DialogCode.Accepted:
            return
        key_created = dialog.key_created

        def loaded() -> None:
            if key_created:
                RecoveryDialog(self.backend, self).exec()
            if after is not None:
                after()
            self.check_mail()

        self.reload_accounts(select_saved=False, then=loaded)

    def show_recovery(self) -> None:
        RecoveryDialog(self.backend, self).exec()

    # --- folders ----------------------------------------------------------------------------------------------------

    def _sidebar_call(self, command: str, values: dict[str, object]) -> None:
        self.backend.call(command, values, lambda result: self._load_folders(select_saved=False), lambda error: self._report("Could not change the folder list", error), owner=self)

    def _ask_name(self, title: str, label: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, label, text=text)
        return name.strip() if ok and (name.strip() or text) else None

    def _pin_order(self) -> list[str]:
        return [str(getattr(pin, "pin_id")) for pin in getattr(self._sidebar, "pins", ())]

    def _tree_extras(self, menu: QMenu, index: QModelIndex) -> None:
        """What the clicked item of the folder list offers about Favorites and groups."""
        kind = str(index.data(KIND_ROLE)) if index.isValid() else ""
        account = str(index.data(ACCOUNT_ROLE) or "")
        if kind in ("folder", "inboxlink"):
            folder = str(index.data(FOLDER_PATH_ROLE))
            pinned = any(str(getattr(pin, "account")) == account and str(getattr(pin, "folder")) == folder for pin in getattr(self._sidebar, "pins", ()))
            if not pinned:
                menu.addSeparator()
                menu.addAction("Add to Favorites", lambda: self._sidebar_call("favorite.pin", {"account": account, "folder": folder}))
        elif kind == "pinlink":
            pin = str(index.data(PIN_ROLE))
            order = self._pin_order()
            position = order.index(pin) if pin in order else -1
            menu.addSeparator()
            menu.addAction("Remove from Favorites", lambda: self._sidebar_call("favorite.unpin", {"pin": pin}))
            menu.addAction("Rename…", lambda: self._rename_pin(pin, str(index.data(Qt.ItemDataRole.DisplayRole))))
            up = menu.addAction("Move up", lambda: self._move_pin(order, position, -1))
            up.setEnabled(position > 0)
            down = menu.addAction("Move down", lambda: self._move_pin(order, position, 1))
            down.setEnabled(0 <= position < len(order) - 1)
        elif kind == "favorite":
            view = str(index.data(FOLDER_PATH_ROLE))
            menu.addSeparator()
            menu.addAction("Hide from Favorites", lambda: self._sidebar_call("favorite.show", {"view": view, "visible": False}))
        elif kind == "account" and str(index.data(ACCOUNT_ROLE)) and not self._is_local(account):
            self._group_menu(menu, account)
        elif kind == "group":
            group = str(index.data(GROUP_ROLE))
            menu.addSeparator()
            menu.addAction("Rename group…", lambda: self._rename_group(group, str(index.data(Qt.ItemDataRole.DisplayRole))))
            menu.addAction("Remove group", lambda: self._sidebar_call("group.remove", {"group": group}))
        if kind in ("section", "favorite") or not kind:
            hidden = [str(view) for view in getattr(self._sidebar, "hidden", ())]
            if kind == "section" and hidden:
                shown = menu.addMenu("Show again")
                for view in hidden:
                    shown.addAction(view.capitalize(), lambda chosen=view: self._sidebar_call("favorite.show", {"view": chosen, "visible": True}))
            menu.addSeparator()
            menu.addAction("New group…", self._new_group)

    def _is_local(self, account: str) -> bool:
        return any(str(getattr(item, "account_id")) == account and str(getattr(item, "kind", "pop3")) == "local" for item in self._accounts)

    def _group_menu(self, menu: QMenu, account: str) -> None:
        menu.addSeparator()
        groups = list(getattr(self._sidebar, "groups", ()))
        submenu = menu.addMenu("Put in group")
        current = next((str(getattr(group, "group_id")) for group in groups if account in getattr(group, "accounts")), None)
        for group in groups:
            group_id = str(getattr(group, "group_id"))
            action = submenu.addAction(str(getattr(group, "name")), lambda chosen=group_id: self._sidebar_call("group.assign", {"account": account, "group": chosen}))
            action.setCheckable(True)
            action.setChecked(group_id == current)
        submenu.addAction("New group…", lambda: self._new_group(account))
        if current is not None:
            menu.addAction("Take out of its group", lambda: self._sidebar_call("group.assign", {"account": account}))

    def _new_group(self, account: str = "") -> None:
        name = self._ask_name("New group", "Name of the group:")
        if name:
            self._sidebar_call("group.add", {"name": name, **({"account": [account]} if account else {})})

    def _rename_group(self, group: str, current: str) -> None:
        name = self._ask_name("Rename group", "New name:", current)
        if name:
            self._sidebar_call("group.rename", {"group": group, "name": name})

    def _rename_pin(self, pin: str, current: str) -> None:
        name = self._ask_name("Rename favorite", "New name (empty: the name of the folder):", re.sub(r"\s+\(\d+\)$", "", current))
        if name is not None:
            self._sidebar_call("favorite.rename", {"pin": pin, "name": name})

    def _move_pin(self, order: list[str], position: int, step: int) -> None:
        target = position + step
        if 0 <= position < len(order) and 0 <= target < len(order):
            order[position], order[target] = order[target], order[position]
            self._sidebar_call("favorite.reorder", {"pin": order})

    def _tree_menu(self, position: QPoint) -> None:
        clicked = self.tree.indexAt(position)
        if clicked.isValid() and clicked != self.tree.currentIndex():
            self.tree.setCurrentIndex(clicked)
        menu = QMenu(self)
        menu.addAction(self.act_new_folder)
        if self._tree_kind == "folder":
            menu.addAction(self.act_rename_folder)
            menu.addAction(self.act_delete_folder)
            menu.addSeparator()
            menu.addAction(self.act_mark_all_read)
            if self._folder_role in ("trash", "junk"):
                menu.addAction(self.act_empty_folder)
        self._tree_extras(menu, clicked)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        if self._in_drafts():
            menu.addAction(self.act_edit_draft)
            menu.addSeparator()
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            menu.addAction(action)
        menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            menu.addAction(action)
        menu.addSeparator()
        self._fill_move_menu()
        menu.addMenu(self.move_menu).setText("Move to")
        menu.addAction(self.act_archive)
        menu.addAction(self.act_snooze)
        menu.addAction(self.act_not_junk if self._folder_role == "junk" else self.act_junk)
        menu.addAction(self.act_delete)
        menu.addSeparator()
        for action in (self.act_save_as, self.act_print, self.act_source):
            menu.addAction(action)
        menu.exec(self.list_view.viewport().mapToGlobal(position))

    def new_folder(self) -> None:
        if not self._account_id:
            return
        start = f"{self._folder}/" if self._folder and self._tree_kind == "folder" else ""
        name, ok = QInputDialog.getText(self, "New folder", "Path of the new folder (use / for a subfolder, for example Archive/2026):", text=start)
        if not ok or not name.strip().strip("/"):
            return
        self.backend.call("folder.create", {"account": self._account_id, "folder": name.strip().strip("/")}, lambda result: self._after_folder_change("Folder created"), lambda error: self._report("Could not create the folder", error), owner=self)

    def rename_folder(self) -> None:
        if self._account_id is None or self._folder is None:
            return
        name, ok = QInputDialog.getText(self, "Rename folder", "New name (with its path, for example Archive/Old):", text=self._folder)
        if not ok or not name.strip() or name.strip() == self._folder:
            return
        self.backend.call("folder.rename", {"account": self._account_id, "folder": self._folder, "new_name": name.strip()}, lambda result: self._after_folder_change("Folder renamed"), lambda error: self._report("Could not rename the folder", error), owner=self)

    def delete_folder(self) -> None:
        if self._account_id is None or self._folder is None:
            return
        answer = QMessageBox.question(self, "Delete folder", f"Delete the folder “{self._folder}”, its subfolders and all the messages in them?\n\nThe files are not erased: they are kept in the store's .deleted directory.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.backend.call("folder.delete", {"account": self._account_id, "folder": self._folder, "with_messages": True, "include_subfolders": True}, lambda result: self._after_folder_change("Folder deleted"), lambda error: self._report("Could not delete the folder", error), owner=self)

    def _after_folder_change(self, text: str) -> None:
        self.show_status(text)
        self._folder = None
        self._tree_kind = "account"
        self._tree_key_kind = "account"
        self._load_folders(select_saved=False)

    # --- attachments and links ----------------------------------------------------------------------------------------

    def _save_attachment(self, position: int, suggested: str) -> None:
        if self._shown is None:
            return
        account, folder, message_id = self._shown
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", start)
        if not path:
            return
        self.backend.call(
            "message.attachment.save",
            {"account": account, "folder": folder, "message": message_id, "index": position, "path": path, "overwrite": True},
            lambda result: self.show_status(f"Saved {getattr(result, 'path')}"),
            lambda error: self._report("Could not save the attachment", error),
            owner=self,
        )

    def _selected_message(self) -> tuple[str, str, str, str] | None:
        """(account, folder, message id, subject) of the message in the list, if there is one."""
        row = self._current_row()
        if row is None or self._account_id is None:
            return None
        return self._account_of(row), str(getattr(row, "folder")), str(getattr(row, "message_id")), str(getattr(row, "subject"))

    def save_selected_as(self) -> None:
        """Message > Save as: a copy of the message as an .eml file."""
        chosen = self._selected_message()
        if chosen is None:
            return
        account, folder, message_id, subject = chosen
        name = re.sub(r"[^\w\- ]+", "", subject).strip()[:60] or "message"
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / f"{name}.eml")
        path, _ = QFileDialog.getSaveFileName(self, "Save message as", start, "E-mail messages (*.eml);;All files (*)")
        if not path:
            return
        self.backend.call(
            "message.export", {"account": account, "folder": folder, "message": message_id, "path": path, "overwrite": True},
            lambda result: self.show_status(f"Saved {getattr(result, 'path')}"),
            lambda error: self._report("Could not save the message", error), owner=self,
        )

    def view_source(self) -> None:
        chosen = self._selected_message()
        if chosen is None:
            return
        account, folder, message_id, subject = chosen

        def shown(result: object) -> None:
            SourceDialog(subject, result, self).exec()

        self.backend.call("message.source", {"account": account, "folder": folder, "message": message_id}, shown, lambda error: self._report("Could not read the message", error), owner=self)

    def print_selected(self) -> None:
        chosen = self._selected_message()
        if chosen is None:
            return
        account, folder, message_id, _ = chosen

        def ready(result: object) -> None:
            if print_message(self, result):
                self.show_status("Sent to the printer")

        self.backend.call("message.text", {"account": account, "folder": folder, "message": message_id}, ready, lambda error: self._report("Could not print the message", error), owner=self)

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() == "mailto":
            if self._account_id is not None:
                self._open_composer(self._account_id, None, None, to=url.path())
            return
        if url.scheme() not in ("http", "https"):
            return
        if not prefs.get_bool(self.settings, "links_ask"):
            QDesktopServices.openUrl(url)
            return
        answer = QMessageBox.question(self, "Open link", f"Open this link in your web browser?\n\n{url.toString()}\n\nLinks in e-mail can lead to harmful sites. Only open it if you trust the sender.")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

    def _about(self) -> None:
        QMessageBox.about(self, "About Chrono Mail", f"<b>Chrono Mail</b> version {program_version()}<br>An open-source e-mail client for Linux.<br><br>License: AGPL-3.0")

