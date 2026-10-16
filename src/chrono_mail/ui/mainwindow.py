import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import override

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QCursor, QDesktopServices, QFont, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError, program_version
from chrono_mail.store import MessageNotFoundError
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.engine.schedule import snooze_presets
from chrono_mail.ui.advancedsearch import AdvancedSearch, AdvancedSearchDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columnfit import MINIMUM_ICON, ColumnLayout, drag, fit, layout_from_pixels, read_layout
from chrono_mail.ui.columns import (
    BY_ID, CARD, DEFAULT_COLUMNS, SNOOZED_COLUMN, SNOOZED_FOLDER_PATH, column_widths, keep_snoozed_column, valid_columns, with_new_columns, with_snoozed_column,
    without_snoozed_column,
)
from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
from chrono_mail.ui import appearance, prefs, scale, theme
from chrono_mail.ui.scale import px
from chrono_mail.ui.colours import red
from chrono_mail.ui.composer import ComposeOptions, ComposeWindow
from chrono_mail.ui.conversationbar import ConversationBar
from chrono_mail.ui.delegate import COLUMN_ROW_HEIGHTS, ColumnDelegate, MessageDelegate
from chrono_mail.ui.foldertree import FolderTree
from chrono_mail.ui.messagelistview import MessageListView
from chrono_mail.ui.icons import application_icon, section_icon, theme_icon
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.operations import ClickableBar, OperationsWindow
from chrono_mail.ui.models import (
    ACCOUNT_ROLE,
    CONTACT_ROLE,
    YES_NO_SORTS,
    FOLDER_PATH_ROLE,
    FOLDER_ROLE_ROLE,
    GROUP_ROLE,
    KIND_ROLE,
    NAME_ROLE,
    PIN_ROLE,
    UNREAD_ROLE,
    FolderTreeModel,
    MessageListModel,
    ViewKind,
    ViewSpec,
)
from chrono_mail.ui.contactsview import ContactsPage
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.tasksview import TasksPage
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.importdialog import MENU_TEXT as IMPORT_MENU_TEXT
from chrono_mail.ui.importdialog import ImportDialog
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.restorekeydialog import RestoreKeyDialog
from chrono_mail.ui.rulespanel import RulesPanel
from chrono_mail.ui.printing import print_message
from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.searchhelp import HelpLine, SearchHelp, send_key
from chrono_mail.ui.searchhistory import SearchHistory, history_text, matching, read_history, with_search
from chrono_mail.ui.windowstate import restore_geometry, save_geometry
from chrono_mail.ui.sections import CALENDAR, CONTACTS, MAIL, SECTIONS, TASKS, SectionBar
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.sourcedialog import SourceDialog
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import fill_tag_menu, offered_for_mail, tag_icon
from chrono_mail.ui.tagsdialog import TagsDialog
from chrono_mail.ui.tray import TrayController
from chrono_mail.ui.whendialog import WhenDialog

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.5.153"

# --- Version history ----------------------------------------------------
# v0.5.153: A new sort order keeps the selected message selected and in view, also when it lands on a part of the list that was not loaded
# v0.5.152: A Favorites view is sorted by the chosen column too (its request passed no sort order)
# v0.5.151: While an import runs no mail is checked (timers and push skip their turn) and Refresh is off; one check runs when the import is done
# v0.5.146: Every send and receive opens the Operations window (also at startup, by the timers and the push); only Refresh gives it the keyboard focus
# v0.5.145: The Operations window: rows, errors and log of send and receive; opened by Refresh, by an error, by View > Operations… and by a click on the busy bar
# v0.5.144: bring_forward: a second start of the program shows this window (also from the tray)
# v0.5.144: The main window and its menus at the size of the Main window slider, the message windows and composers at that of Other windows and dialogs (both tried out from Settings)
# v0.5.143: Progress numbers in the status line have thousands separators
# v0.5.140: The first column of the message list can be dragged to another place too
# v0.5.135: Tags for mail: Menu > Tags, the Tag button and menu, Tag in the right-click menu, Filter > Tagged with, Tags in the folder list, the tags in the reading pane, tag shortcuts, the tag colour for the text
# v0.5.133: After an import the message list on screen is loaded again too (it kept the old messages); mail checks run in their own lane
# v0.5.132: Menu > File > Import (accounts, passwords, rules, contacts and mail from another program's exports)
# v0.5.126: The changes of a message (marks, delete, move, copy, archive, junk, snooze) run in the order they were given
# v0.5.125: A message deleted or moved right after it was clicked no longer reports that messages are missing
# v0.5.124: Show on startup: a chosen folder, Calendar, Contacts or Tasks, or the last selected folder (D-121)
# v0.5.123: An account or group row is selected and shows its Inbox (nothing for an account without one); the Favorites heading is never selected, a click folds it; the first Inbox at start is found inside groups too (D-119)
# v0.4.120: The engine's notices (an IMAP change waits for the server) in the red line; the IMAP connections close when the program ends
# v0.4.119: An IMAP folder held back by a safety rule is a question to the person (delete on the server or bring back; remove here or keep), answered with mail.settle
# v0.4.117: Alt+M opens the Menu (Open the menu, changeable in Settings > Keyboard); the contact addresses are loaded again after the calendar and contacts sync
# v0.3.116: Delete, Delete for good, Move to Junk, Move (also by dragging), Archive and Copy refresh the list also when a message failed (the error stays)
# v0.3.112: A message whose file is gone gives one warning per action (a window and the red line) with the advice to repair the search index
# v0.3.109: Every column but Delete sorts (icon columns: yes first; Messages in conversation: largest first; Folder and Preview: A to Z), the eight new sorts in the Sorted by menu; a click on Delete leaves the sort arrow where it was
# v0.3.108: The message list has its own size factor (text, rows, cards, icons and columns), tried out at once from Settings; the size of the text of messages is a factor for reading and writing, tried out at once from Settings (Cancel puts it back); open composers follow
# v0.3.106: The size factor of the interface is tried out while the slider in Settings moves (Cancel puts it back); the fixed sizes of the window follow it
# v0.2.102: In the Archive folder Archive is Unarchive (back to the folder it came from); Move to Junk and Not junk (move to Inbox) say what they do
# v0.2.101: Cancel snooze (in the Snoozed folder: the menu of the Snooze button and the right-click menu) brings the message back now
# v0.2.100: The Snoozed folder has the column Snoozed until (sortable, with its groups; added once after Received, kept off when switched off)
# v0.2.99: After a delete, move, archive, junk or snooze the list keeps its place and selects the next message (or keeps the selection when another row was deleted); F2 renames the folder while the folder list has the focus
# v0.2.97: The Message list window sets the row height too
# v0.2.96: In the card layout the one column always fills the list (it kept the width of the first column of the column layout); View > Message list: Columns or Cards; in the card layout the rows are not all one height (group headers are lower than cards); Message list… is no longer in the View menu (it stays in the menu of the column headers)
# v0.2.94: Group headers in the message list (fold by a click; Show groups in the View menu); sorting by To; searches and combined views follow the sort order
# v0.1.93: The search field has three looks (not in use, clicked in, search active) and a red ✕ that clears the search; clicking in it shows the last 10 searches
# v0.1.92: Search box hint "Search (type '?' for help)"; typing "?" opens the search help under the search box
# v0.1.91: Clear filter at the bottom of the Filter menu; the filters that are on are bold in the menu
# v0.1.90: The Unread, Flagged and Attachments buttons are replaced by a Filter button (funnel) with a menu: Filter by, Flagged, Unread, With attachments; the button is blue and bold while a filter is on
# v0.1.89: The columns always fill the list (column fit); fixed row heights; Contact and Delete columns; Delete and Contact added once to a saved set of columns
# v0.0.88: Object names for the two splitters (1 px lines) and the Sorted by button (bold)
# v0.0.86: Refresh sends and receives and has a menu (send and receive all, receive all, send all, one account only); a Snooze button with ready-made times; Forward has a menu with Forward as attachment; the Snooze button opens its menu wherever it is clicked; message buttons and actions are greyed out while no message is selected
# v0.0.85: Window title: the open folder and its unread messages, then the program; icons on Mark and Move to; Archive in the toolbar; Snooze uses the clock icon by its own name
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.82: Text size of the interface (8 to 48 pt, the system size at the first start) under Appearance; the Menu button uses the three-bar character U+2630
# v0.0.81: The Menu button is the first, blue button of every toolbar and the row of the menu bar is gone; New is a plain toolbar button
# v0.0.78: Composer: new look (Send in blue on top, rounded fields, flat toolbar) and rich text is the default for new messages
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.76: Blue accent, Settings Accounts Rules and Exit in the root of the Menu, plain section icons with the open one in blue, Contacts before Tasks
# v0.0.75: The folder list remembers which headings, accounts and folders are open, also after a restart
# v0.0.74: A message window gets the privacy settings before it shows the message and saves Always for this sender
# v0.0.73: The main window gets a new look: the Menu button, the toolbar, the search field, the folder list and the section buttons
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.66: Synchronisation of calendars, task lists and address books with a CalDAV and CardDAV server
# v0.0.64: The calendar section
# v0.0.63: The tasks section
# v0.0.62: The contacts section
# v0.0.61: The four section buttons and the sidebars of the calendar, task and contact sections
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
SORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("date", "Received"), ("subject", "Subject"), ("sender", "Sender"), ("to", "To"), ("size", "Size"),
    ("unread", "Unread"), ("flagged", "Flagged"), ("attachment", "Attachment"), ("replied", "Replied"), ("contact", "Contact"),
    ("folder", "Folder"), ("conversation", "Messages in conversation"), ("preview", "Preview"), ("snoozed", "Snoozed until"),
)
_WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # in the snooze menu, English like the rest of the interface
_ICONS: dict[str, str] = {"inbox": "mail-folder-inbox", "sent": "mail-folder-outbox", "drafts": "document-edit", "trash": "user-trash", "junk": "mail-mark-junk", "archive": "folder-archive", "other": "folder"}


MISSING_WARNING: str = "Some messages no longer exist on disk. Go to Settings > Security and storage and click Repair the search index."
BLOCKED_QUESTIONS: dict[str, tuple[str, tuple[tuple[str, str, str], ...]]] = {  # kind -> question, (button, choice, what was done)
    "missing_here": (
        "{count} messages that you deleted in Chrono Mail are still in {folder} on the server of {account}. What should happen?",
        (("Delete them on the server too", "delete_on_server", "deleted on the server"), ("Bring them back here", "restore_here", "brought back")),
    ),
    "gone_from_server": (
        "{count} messages in {folder} of {account} are gone from the server, but are still here. What should happen?",
        (("Remove them here too", "remove_here", "removed here"), ("Keep them here", "keep_here", "kept and uploaded again")),
    ),
}


def ask_choice(parent: QWidget, title: str, text: str, buttons: list[str]) -> int | None:
    """A question with buttons of its own: the position of the button that was clicked, None when the window was closed."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    made = [box.addButton(label, QMessageBox.ButtonRole.AcceptRole) for label in buttons]
    box.exec()
    clicked = box.clickedButton()
    return next((position for position, button in enumerate(made) if button is clicked), None)


SIDEBAR_KEY: str = "sidebar"  # stands for the sidebar answer among the folder lists that are awaited


def bool_setting(settings: QSettings, key: str, default: bool) -> bool:
    """QSettings may hand back the text 'true' or 'false' from an ini file."""
    value = settings.value(key, default)
    return value if isinstance(value, bool) else str(value).lower() == "true"


MAXIMUM_TREE_STATE: int = 2000  # opened or closed nodes that are remembered


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
        self._tree_key_kind: str = "folder"
        self._status_is_error: bool = False
        self._folder_popup: QMenu | None = None
        self._tree_path: str = ""  # the folder, favorite or group of the item that is selected in the tree
        self._combined_accounts: tuple[str, ...] = ()  # the accounts of the group whose inbox is shown
        self._sidebar: object | None = None  # the answer of sidebar.show: pinned folders and groups
        self._combined: str | None = None  # the favorite that is shown (all inboxes, unread, ...), or None
        self._combined_tag: str = ""  # the tag of the combined view "tagged" (Tags in the folder list)
        self._filter_tag: str | None = None  # Filter > Tagged with
        self._tags: tuple[object, ...] = ()  # the answer of tag.list, in the person's order
        self._tag_shortcuts: list[QAction] = []
        self._tree_state: dict[str, bool] = self._load_tree_state()  # node -> opened (True) or closed (False) by the person
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
        self._reselect: str | None = None  # a message to select again once the list has it (after a mail check or a new sort order)
        self._reselect_asked: str | None = None  # the message whose place in the folder was asked for (its page is loaded next)
        self._after_delete: tuple[str | None, int, int | None] | None = None  # after a delete: the message to select (id, position) and the scroll position
        self._composers: list[ComposeWindow] = []
        self._checking: bool = False
        self._sending: bool = False  # Send All is running
        self._importing: bool = False  # an import runs: mail checks wait (a check meanwhile could store a message the import stores again)
        self._dav_syncing: bool = False
        self._mail_action_states: dict[str, bool] = {}
        self._tried_interface: float | None = None  # sizes being tried out in Settings (not saved yet)
        self._tried_windows: float | None = None
        self.setProperty(scale.MAIN_WINDOW_PROPERTY, True)  # this window and its menus follow the Main window slider
        self._apply_main_font()
        self.setStyleSheet(theme.stylesheet())  # only this window: a stylesheet on the application would hide the style from appearance.py
        self._build_widgets()
        self.operations = OperationsWindow(self._operation_name, settings, self)
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
        self.tray.check_requested.connect(lambda: self.check_accounts(None, manual=True))
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
        self._asking_blocked: bool = False  # a question about a folder held back by a safety rule is open
        self._missing_timer = QTimer(self)  # the answers of an action come one message at a time: one warning for all of them
        self._missing_timer.setSingleShot(True)
        self._missing_timer.setInterval(300)
        self._missing_timer.timeout.connect(self._show_missing_warning)

    # --- construction ---------------------------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.tree_model = FolderTreeModel(self)
        self.tree = FolderTree(self)
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setMinimumWidth(px(180))
        self.tree.setObjectName("folderTree")

        self.list_model = MessageListModel(self.backend, self)
        self.delegate = MessageDelegate()
        self.column_delegate = ColumnDelegate()
        self.column_delegate.column_of = self._column_id
        self._tag_book = TagColourBook()  # one set of tag colours for the list, the cards and the reading pane
        self.list_model.tag_book = self._tag_book
        self.delegate.tag_book = self._tag_book
        self.column_delegate.tag_book = self._tag_book
        self._layout: str = str(self.settings.value("list_layout", "columns"))
        if self._layout not in ("columns", "cards"):
            self._layout = "columns"
        self._columns: tuple[str, ...] = self._saved_columns()
        self._widths: dict[str, int] = column_widths(str(self.settings.value("column_widths", "")))  # pixels saved by versions before 0.1.89, taken over once
        self._column_layout: ColumnLayout | None = read_layout(str(self.settings.value("column_layout", "")))
        self._fitting: bool = False  # the program itself is setting the widths of the columns
        self._reading_pane: str = str(self.settings.value("reading_pane", "right"))
        if self._reading_pane not in ("right", "bottom", "off"):
            self._reading_pane = "right"
        self._date_style: str = str(self.settings.value("date_style", "short"))
        self._message_windows: list[MessageWindow] = []
        self.list_view = MessageListView(self)
        self.list_view.setObjectName("messageList")
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
        self.list_view.header().setFirstSectionMovable(True)  # a tree view keeps its first column in place for a real tree; this list is flat
        self.list_view.header().setStretchLastSection(False)  # the columns fill the list themselves (column fit)
        self.list_view.header().setMinimumSectionSize(MINIMUM_ICON)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.column_of = self._column_id
        self.list_view.is_header = self.list_model.is_header
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
        self.sort_button.setObjectName("sortButton")
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_button.setAutoRaise(True)
        self.sort_menu = QMenu(self.sort_button)
        self.sort_button.setMenu(self.sort_menu)
        self.filter_button = QToolButton(header)
        self.filter_button.setObjectName("filterButton")
        self.filter_button.setText("Filter")
        self.filter_button.setIcon(section_icon("view-filter"))  # blue while a filter is on
        self.filter_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_button.setAutoRaise(True)
        self.filter_button.setCheckable(True)  # checked = at least one filter is on; set by _show_filter_state only
        self.filter_menu = QMenu(self.filter_button)
        heading = self.filter_menu.addAction("Filter by")
        heading.setEnabled(False)
        self.filter_flagged = self._filter_entry("Flagged", "flag")
        self.filter_unread = self._filter_entry("Unread", "mail-unread")
        self.filter_attachments = self._filter_entry("With attachments", "mail-attachment")
        self.filter_tag_menu = self.filter_menu.addMenu("Tagged with")
        self.filter_tag_menu.aboutToShow.connect(self._fill_filter_tags)
        self.filter_menu.addSeparator()
        self.filter_clear = self.filter_menu.addAction("Clear filter")
        self.filter_clear.setEnabled(False)
        self.filter_clear.triggered.connect(self._clear_filters)
        self.filter_button.setMenu(self.filter_menu)
        self.count_label = QLabel("", header)
        self.filter_chip = QToolButton(header)
        self.filter_chip.setAutoRaise(True)
        self.filter_chip.setText("Advanced filters ✕")
        self.filter_chip.setToolTip("Click to remove the dates and sizes of the advanced search")
        self.filter_chip.hide()
        header_layout.addWidget(self.sort_button)
        header_layout.addStretch(1)
        header_layout.addWidget(self.filter_button)
        header_layout.addWidget(self.filter_chip)
        header_layout.addWidget(self.count_label)
        list_layout.addWidget(header)
        list_layout.addWidget(self.list_view, 1)

        self.reader = ReaderWidget(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, self)  # the list and the reading pane, side by side or one above the other
        self.splitter.setObjectName("mainSplitter")
        self.content_splitter.setObjectName("contentSplitter")
        self.splitter.setHandleWidth(1)
        self.content_splitter.setHandleWidth(1)
        self.side_stack = QStackedWidget(self)  # the left side: the folders of the mail, or the sidebar of the section that is shown
        self.side_stack.addWidget(self.tree)
        self.content_stack = QStackedWidget(self)
        self.content_stack.addWidget(self.content_splitter)
        self.section_bar = SectionBar(self)
        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.side_heading = QLabel("Mail", left_panel)
        self.side_heading.setObjectName("sideHeading")
        left_layout.addWidget(self.side_heading)
        left_layout.addWidget(self.side_stack, 1)
        left_layout.addWidget(self.section_bar)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.content_stack)
        self._section: str = MAIL
        self.contacts_page = ContactsPage(self.backend, self.settings, self)
        self.pages: dict[str, PimPage] = {CALENDAR: CalendarPage(self.backend, self.settings, self), TASKS: TasksPage(self.backend, self.settings, self), CONTACTS: self.contacts_page}
        for page in self.pages.values():
            self.side_stack.addWidget(page.side)
            self.content_stack.addWidget(page.content)
            page.status = self.show_status
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

    def _filter_entry(self, text: str, icon: str) -> QAction:
        action = self.filter_menu.addAction(theme_icon(icon), text)
        action.setCheckable(True)
        return action

    def _show_filter_state(self) -> None:
        """The Filter button is blue and bold while at least one filter is on; in the menu the filters that are on are bold."""
        entries = (self.filter_flagged, self.filter_unread, self.filter_attachments)
        for entry in entries:
            font = entry.font()
            font.setBold(entry.isChecked())
            entry.setFont(font)
        on = any(entry.isChecked() for entry in entries) or self._filter_tag is not None
        font = self.filter_tag_menu.menuAction().font()
        font.setBold(self._filter_tag is not None)
        self.filter_tag_menu.menuAction().setFont(font)
        self.filter_button.setChecked(on)
        self.filter_clear.setEnabled(on)

    def _clear_filters(self) -> None:
        self._filter_tag = None
        for entry in (self.filter_flagged, self.filter_unread, self.filter_attachments):
            entry.blockSignals(True)
            entry.setChecked(False)
            entry.blockSignals(False)
        self._show_filter_state()
        self._apply_view()

    def _action(self, text: str, handler: Callable[..., object], shortcut: str | None = None, icon: str | None = None) -> QAction:
        action = QAction(text, self)
        if icon:
            action.setIcon(theme_icon(icon))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(handler)
        return action

    def _build_actions(self) -> None:
        self.act_new = self._action("New", lambda: self._in_section("new"), "Ctrl+N", "mail-message-new")
        self.act_refresh = self._action("Refresh", lambda: self._in_section("refresh"), "F5", "view-refresh")
        self.act_reply = self._action("Reply", lambda: self.compose_from_selection("reply"), "Ctrl+R", "mail-reply-sender")
        self.act_reply_all = self._action("Reply all", lambda: self.compose_from_selection("reply_all"), "Ctrl+Shift+R", "mail-reply-all")
        self.act_forward = self._action("Forward", lambda: self.compose_from_selection("forward"), "Ctrl+F", "mail-forward")
        self.act_forward_attachment = self._action("Forward as attachment", lambda: self.compose_from_selection("forward_attachment"))
        self.act_edit_draft = self._action("Edit draft", self.edit_draft, None, "document-edit")
        self.act_delete = self._action("Delete", lambda: self._in_section("delete"), "Delete", "edit-delete")
        self.act_save_as = self._action("Save as…", self.save_selected_as, "Ctrl+Shift+S", "document-save-as")
        self.act_source = self._action("View source", self.view_source, "Ctrl+Alt+U")
        self.act_print = self._action("Print…", self.print_selected, "Ctrl+P", "document-print")
        self.act_open = self._action("Open", self.open_selected, "Return", "document-open")
        self.act_columns = self._action("Message list…", self.edit_columns)
        self.act_junk = self._action("Move to Junk", lambda: self.junk_selected(self._folder_role != "junk"), "Ctrl+J", "mail-mark-junk")  # one key does both, as the folder decides
        self.act_not_junk = self._action("Not junk (move to Inbox)", lambda: self.junk_selected(False), None, "mail-mark-notjunk")
        self.act_cancel_snooze = self._action("Cancel snooze", self.cancel_snooze_selected)
        self.act_not_junk.setEnabled(False)
        self.act_next_unread = self._action("Next unread message", lambda: self.go_to_unread(1), "N")
        self.act_previous_unread = self._action("Previous unread message", lambda: self.go_to_unread(-1), "P")
        self.act_archive = self._action("Archive", self.archive_selected, "A", "archive-insert")
        self.act_mark_all_read = self._action("Mark all as read", self.mark_folder_read, icon="mail-mark-read")
        self.act_empty_folder = self._action("Empty folder…", self.empty_folder, icon="edit-clear")
        self.act_empty_folder.setEnabled(False)
        self.act_read = self._action("Mark as read", lambda: self.mark_selected(seen=True), "Ctrl+Q")
        self.section_actions: dict[str, QAction] = {}
        for section, name, icons, shortcut in SECTIONS:
            action = self._action(name, lambda checked=False, chosen=section: self.switch_section(chosen), shortcut, icons[0])
            self.section_actions[section] = action
            setattr(self, f"act_section_{section}", action)
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
        self.act_import = self._action(IMPORT_MENU_TEXT, self.show_import)
        self.act_restore_key = self._action("Restore the encryption key…", self.restore_key)
        self.act_settings = self._action("Settings…", lambda: self.open_settings(), "Ctrl+,", "configure")
        self.act_scheduled = self._action("Scheduled and snoozed messages…", self.open_scheduled)
        self.act_snooze = self._action("Snooze…", self.snooze_selected, "S", "clock")
        self.act_snooze.setIconText("Snooze")  # on the toolbar button
        self.act_rules = self._action("Rules…", self.open_rules, None, "view-filter")
        self.act_tags = self._action("Tags…", self.open_tags)
        self.act_accounts = self._action("Accounts…", lambda: self.open_settings("Accounts"), None, "user-identity")
        self.act_quit = self._action("Exit", self.quit_program, None, "application-exit")
        self.act_open_menu = self._action("Open the menu", self.open_menu, "Alt+M")
        self.act_search = self._action("Search", lambda: self._in_section("search"), "Ctrl+E")
        self.act_advanced = self._action("Advanced search…", self.advanced_search, "Ctrl+Shift+F")
        self.act_auto = QAction("Check for mail automatically", self)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool_setting(self.settings, "auto_check", True))
        self.act_auto.toggled.connect(self._toggle_auto_check)
        self.act_conversations = QAction("Show conversations", self)
        self.act_conversations.setCheckable(True)
        self.act_conversations.setChecked(bool_setting(self.settings, "conversations", False))
        self.act_show_groups = QAction("Show groups", self)
        self.act_show_groups.setCheckable(True)
        self.act_show_groups.setChecked(bool_setting(self.settings, "show_groups", True))
        self.list_model.show_groups = self.act_show_groups.isChecked()
        self.act_new_folder = self._action("New folder…", self.new_folder)
        self.act_rename_folder = self._action("Rename folder…", self.rename_folder, "F2")
        self.act_delete_folder = self._action("Delete folder…", self.delete_folder)
        self.act_about = self._action("About Chrono Mail", self._about)

    def _split_button(self, action: QAction, menu: QMenu) -> None:
        """The toolbar button of an action does the action when clicked and opens the menu from its arrow."""
        button = self.toolbar.widgetForAction(action)
        if isinstance(button, QToolButton):
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def _fill_refresh_menu(self) -> None:
        """Send and receive all, receive all, send all, or receive the mail of one account only."""
        self.refresh_menu.clear()
        self.refresh_menu.addAction("Send and Receive All", self.send_and_receive)
        self.refresh_menu.addAction("Receive All", lambda: self.check_accounts(None, manual=True))
        self.refresh_menu.addAction("Send All", lambda: self.send_outbox(manual=True))
        accounts = self._mail_accounts()
        if accounts:
            self.refresh_menu.addSeparator()
        for account in accounts:
            account_id = str(getattr(account, "account_id"))
            self.refresh_menu.addAction(f"\"{getattr(account, 'shown_name')}\" only", lambda chosen=account_id: self.check_accounts({chosen}, manual=True))

    def _fill_snooze_menu(self) -> None:
        """The ready-made times, counted from now, and a choice of one's own."""
        self.snooze_menu.clear()
        if self._in_snoozed():
            self.snooze_menu.addAction(self.act_cancel_snooze)
            self.snooze_menu.addSeparator()
        for name, moment in snooze_presets(datetime.now()):
            label = f"{name} ({_WEEKDAYS[moment.weekday()]} {moment:%H:%M})"
            self.snooze_menu.addAction(label, lambda until=moment.strftime("%Y-%m-%dT%H:%M"): self.snooze_until(until))
        self.snooze_menu.addSeparator()
        self.snooze_menu.addAction("Custom date and time…", self.snooze_selected)

    def _build_toolbar(self) -> None:
        self.toolbar = QToolBar("Main", self)
        self.toolbar.setMovable(False)
        self.toolbar.setObjectName("mainToolbar")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(self.toolbar)
        self.toolbar.addAction(self.act_new)
        self.toolbar.addAction(self.act_refresh)
        self.refresh_menu = QMenu(self)
        self.refresh_menu.aboutToShow.connect(self._fill_refresh_menu)
        self._split_button(self.act_refresh, self.refresh_menu)
        self.toolbar.addWidget(self._stretch())
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            self.toolbar.addAction(action)
        self.forward_menu = QMenu(self)
        self.forward_menu.addAction(self.act_forward_attachment)
        self._split_button(self.act_forward, self.forward_menu)
        self.mark_button = QToolButton(self)
        self.mark_button.setText("Mark")
        self.mark_button.setIcon(theme_icon("crosshairs"))
        self.mark_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.mark_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mark_menu = QMenu(self.mark_button)
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            mark_menu.addAction(action)
        self.mark_button.setMenu(mark_menu)
        self.toolbar.addWidget(self.mark_button)
        self.tag_button = QToolButton(self)
        self.tag_button.setText("Tag")
        self.tag_button.setIcon(tag_icon(QColor("#808080")))
        self.tag_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_menu.aboutToShow.connect(lambda: self._fill_tag_menu(self.tag_menu))
        self.tag_button.setMenu(self.tag_menu)
        self.toolbar.addWidget(self.tag_button)
        self.toolbar.addAction(self.act_archive)
        self.toolbar.addAction(self.act_snooze)
        self.snooze_menu = QMenu(self)
        self.snooze_menu.aboutToShow.connect(self._fill_snooze_menu)
        snooze_button = self.toolbar.widgetForAction(self.act_snooze)
        if isinstance(snooze_button, QToolButton):  # a click anywhere on it opens the menu: the time dialog is its last entry
            snooze_button.setMenu(self.snooze_menu)
            snooze_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_button = QToolButton(self)
        self.move_button.setText("Move to")
        self.move_button.setIcon(theme_icon("go-parent-folder"))
        self.move_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_button)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_button.setMenu(self.move_menu)
        self.toolbar.addWidget(self.move_button)
        self.toolbar.addAction(self.act_delete)
        self.toolbar.addWidget(self._stretch())
        self.search_pill = SearchField(self)
        self.search_box = self.search_pill.line_edit
        self.scope = self.search_pill.scope
        self.toolbar.addWidget(self.search_pill)
        self.search_pill.clicked.connect(self._show_search_history)
        self.search_pill.cleared.connect(self._clear_search)
        self._search_history: list[str] = read_history(str(self.settings.value("search_history", "")))
        self.search_history = SearchHistory(self)
        self.search_history.picked.connect(self._search_history_picked)
        self.search_history.removed.connect(self._search_history_removed)
        self.search_history.typed.connect(self._search_help_typed)
        self.search_help = SearchHelp(self)
        self.search_help.picked.connect(self._search_help_picked)
        self.search_help.typed.connect(self._search_help_typed)
        for page in self.pages.values():  # the toolbars of the other sections: one is shown at a time, in the same place
            self.addToolBar(page.toolbar)
            page.toolbar.hide()

    def _menu_actions(self, menu: QMenu) -> list[QAction]:
        """Every action in a menu and in its submenus: they are also given to the window, so that their keys work while the menu bar is hidden."""
        found: list[QAction] = []
        for action in menu.actions():
            submenu = action.menu()
            if isinstance(submenu, QMenu):
                found.extend(self._menu_actions(submenu))
            elif not action.isSeparator():
                found.append(action)
        return found

    def _stretch(self) -> QWidget:
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return spacer

    def _build_menus(self) -> None:
        bar = self.menuBar()
        root = bar.addMenu("\u2261  &Menu")  # one Menu button holds the menus that make up the menu bar
        for action in (self.act_settings, self.act_accounts, self.act_rules, self.act_tags):
            root.addAction(action)
        root.addSeparator()
        file_menu = root.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_refresh)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addSeparator()
        file_menu.addAction(self.act_recovery)
        file_menu.addAction(self.act_restore_key)
        message_menu = root.addMenu("&Message")
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
        folder_menu = root.addMenu("F&older")
        for action in (self.act_new_folder, self.act_rename_folder, self.act_delete_folder):
            folder_menu.addAction(action)
        folder_menu.addSeparator()
        folder_menu.addAction(self.act_mark_all_read)
        folder_menu.addAction(self.act_empty_folder)
        view_menu = root.addMenu("&View")
        for section_action in self.section_actions.values():
            view_menu.addAction(section_action)
        view_menu.addSeparator()
        view_menu.addAction(self.act_search)
        view_menu.addAction(self.act_toggle_folders)
        layout_menu = view_menu.addMenu("Message list")
        self.layout_actions: dict[str, QAction] = {}
        for value, label in (("columns", "Columns"), ("cards", "Cards")):
            action = layout_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._layout == value)
            action.triggered.connect(lambda checked=False, chosen=value: self.set_list_layout(chosen))
            self.layout_actions[value] = action
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
        view_menu.addAction(self.act_show_groups)
        view_menu.addAction(self.act_auto)
        view_menu.addSeparator()
        view_menu.addAction("Operations…", self.show_operations)
        help_menu = root.addMenu("&Help")
        help_menu.addAction(self.act_about)
        root.addSeparator()
        root.addAction(self.act_quit)
        # The Menu button is the first button of every toolbar, so that no row of the window is used for it. The menu bar
        # stays as the home of the menus (and of their keys) but is not shown.
        self.menu_buttons: list[QToolButton] = []
        for toolbar in (self.toolbar, *(page.toolbar for page in self.pages.values())):
            button = QToolButton(self)
            button.setObjectName("menuButton")
            button.setText("\u2630  Menu")
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMenu(root)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            toolbar.insertWidget(toolbar.actions()[0], button)
            self.menu_buttons.append(button)
        self.addActions(self._menu_actions(root))
        self.addAction(self.act_open_menu)  # not in a menu: the window itself listens for its key
        bar.hide()

    def _build_status(self) -> None:
        self.status_label = QLabel("", self)
        self.progress_bar = ClickableBar(self)
        self.progress_bar.clicked.connect(self.show_operations)
        self.progress_bar.setToolTip("Show the Operations window")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _wire(self) -> None:
        self.section_bar.chosen.connect(self.switch_section)
        self.contacts_page.compose_requested.connect(self._compose_to)
        self.contacts_page.mail_accounts_source = self._accounts_with_mail
        self.tree.selectionModel().currentChanged.connect(self._tree_changed)
        self.tree.clicked.connect(self._tree_clicked)
        self.tree.expanded.connect(self._tree_opened)
        self.tree.collapsed.connect(self._tree_closed)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.messages_dropped.connect(self._messages_dropped)
        self.act_conversations.toggled.connect(self._toggle_conversations)
        self.act_show_groups.toggled.connect(self._toggle_groups)
        self.list_model.groups_changed.connect(self._span_group_headers)
        self.list_model.modelReset.connect(self._span_group_headers)  # a reset (another layout, other columns) forgets the spans
        self.list_view.header_clicked.connect(self.list_model.toggle_group)
        self.conversation_bar.message_chosen.connect(self._show_conversation_member)
        self.list_view.selectionModel().currentChanged.connect(self._row_changed)
        self.list_view.selectionModel().selectionChanged.connect(self._update_message_actions)
        self.list_model.modelReset.connect(self._update_message_actions)
        self.list_model.rowsRemoved.connect(self._update_message_actions)
        self.list_model.rowsInserted.connect(self._update_message_actions)
        self._update_message_actions()
        self.list_view.customContextMenuRequested.connect(self._list_menu)
        self.list_view.doubleClicked.connect(self._row_double_clicked)
        self.list_view.header().sectionClicked.connect(self._header_clicked)
        self.list_view.header().sectionMoved.connect(self._columns_moved)
        self.list_view.header().sectionResized.connect(self._column_resized)
        self.list_view.width_changed.connect(self._fit_columns)
        self.list_view.icon_clicked.connect(self._icon_clicked)
        self.list_view.list_header.column_dragged.connect(self._column_line_dragged)
        self.reader.sender_allowed.connect(self._sender_allowed)
        self.list_view.header().customContextMenuRequested.connect(self._header_menu)
        self.list_model.counts_changed.connect(self._counts_changed)
        self.list_model.load_failed.connect(self._load_failed)
        self.list_model.loaded.connect(self._restore_row_selection)
        self.search_box.returnPressed.connect(self._run_search)
        self.search_box.textChanged.connect(self._search_text_changed)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.filter_chip.clicked.connect(self._clear_advanced)
        for button in (self.filter_unread, self.filter_flagged, self.filter_attachments):
            button.toggled.connect(self._show_filter_state)
            button.toggled.connect(self._apply_view)
        self.reader.attachment_save_requested.connect(self._save_attachment)
        self.reader.link_activated.connect(self._open_link)
        self.backend.busy_changed.connect(self._busy_changed)
        self.backend.progress.connect(self._progress)
        self.backend.notice.connect(self._notice)
        self.backend.activity.connect(self._activity)

    # --- state ------------------------------------------------------------------------------------------

    def _restore_state(self) -> None:
        restore_geometry(self, self.settings, "geometry")
        startup = str(self.settings.value("startup_view", "") or "")
        saved_section = startup.split("\t", 1)[1] if startup.startswith("section\t") else MAIL if startup.startswith("mail\t") else str(self.settings.value("last_section", MAIL))
        if saved_section != MAIL:
            self.switch_section(saved_section)
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
        save_geometry(self, self.settings, "geometry")
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
        self.backend.engine.close_imap_links()  # log out of the connections kept open for the actions on the server
        self.tray.set_visible(False)
        event.accept()
        self.closed_for_good.emit()

    def _clear_reader(self) -> None:
        self.reader.clear()
        self.conversation_bar.clear()

    def _toggle_groups(self, on: bool) -> None:
        self.settings.setValue("show_groups", on)
        self.list_model.show_groups = on
        self._apply_view()

    def _span_group_headers(self) -> None:
        """A group header is one bar over all columns (the view keeps a span with its row as rows come and go)."""
        for row in self.list_model.header_rows():
            self.list_view.setFirstColumnSpanned(row, QModelIndex(), True)

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
            self.reader.set_tags(self._tag_book.html(self._tags_of_message(message_id)))
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

    def bring_forward(self) -> None:
        """A second start of the program asked for the window: show it (also when it was hidden in the tray), raise it and give it the focus."""
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
        if self._section == MAIL:
            self.setWindowTitle(self._mail_title())
        self.tray.set_unread(unread)

    def _mail_title(self) -> str:
        """The window title in the mail section: the folder that is open and its unread messages, then the program."""
        current = self.tree.currentIndex()
        if current.isValid() and str(current.data(KIND_ROLE)) in ("account", "group"):
            current = self._inbox_under(current) or current  # the row shows its Inbox
        name = str(current.data(NAME_ROLE) or "") if current.isValid() else ""
        if not name:
            return "Chrono Mail"
        unread = int(current.data(UNREAD_ROLE) or 0)
        return f"{name} ({unread}) - Chrono Mail" if unread else f"{name} - Chrono Mail"

    def _notify_new_mail(self, new_by_account: dict[str, int]) -> None:
        """A desktop notification for mail that arrived while the window was not in use. It tells how many, never what."""
        total = sum(new_by_account.values())
        if not total or not bool_setting(self.settings, "notifications", True) or self.isActiveWindow():
            return
        names = {str(getattr(account, "account_id")): str(getattr(account, "shown_name")) for account in self._accounts}
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

    def _accounts_with_mail(self) -> list[str]:
        """The places where mail is kept (accounts that share Local Folders count once), for searching the mail of a contact."""
        return [str(getattr(account, "account_id")) for account in self._accounts if str(getattr(account, "storage", "own")) != "local"]

    def _compose_to(self, address: str) -> None:
        """A new message to this address (from a contact)."""
        if self._mail_accounts():
            self._open_composer(self._account_id or "", None, None, to=address)

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
        self.snooze_until(dialog.chosen())

    def cancel_snooze_selected(self) -> None:
        """In the Snoozed folder: the selected messages go back now to the folder they came from (unread, as when their time comes)."""
        rows = self._selected_rows()
        if not rows or self._account_id is None or not self._in_snoozed():
            return
        account = self._account_id
        remaining = len(rows)
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Brought back {len(rows)} message(s)")

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining
            remaining -= 1
            self._report("Could not cancel the snooze", error)

        for row in rows:
            self.backend.call("message.wake", {"account": self._account_of(row) or account, "message": str(getattr(row, "message_id"))}, done, failed, owner=self, in_order=True)

    def snooze_until(self, until: str) -> None:
        """Put the selected messages away until this local time (2026-10-13T09:00)."""
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account = self._account_id
        remaining = len(rows)
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])

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
            self.backend.call("message.snooze", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "until": until}, done, failed, owner=self, in_order=True)

    def open_rules(self) -> None:
        """Settings > Rules: what is done with mail as it arrives (also reachable by a key chosen for it)."""
        self.open_settings("Rules")

    def _rule_folders(self) -> list[str]:
        return sorted({str(getattr(info, "path")) for infos in self._folders.values() for info in infos})

    def _rule_folder_now(self) -> tuple[str, str] | None:
        return (self._account_id, self._folder) if self._account_id is not None and self._folder is not None and self._tree_kind == "folder" else None

    def open_menu(self) -> None:
        """Alt+M: open the Menu button of the section that is shown."""
        for button in self.menu_buttons:
            if button.isVisible():
                button.showMenu()
                return

    def _shortcut_registry(self) -> dict[str, tuple[str, str]]:
        """Every action that can be given keys: its name, the text, and the standard keys."""
        return {name[4:]: (action.text(), self._default_shortcuts.get(name, "")) for name, action in vars(self).items() if name.startswith("act_") and isinstance(action, QAction)}

    def open_settings(self, page: str | None = None) -> None:
        """File > Settings: the way the program behaves, how it looks, writing, the accounts, rules, scheduled messages, the keys and the safety of the mail."""
        dialog = SettingsDialog(
            self.backend, self.settings, self.tray.available, self._shortcut_registry(), self,
            accounts=self._mail_accounts(), folders=self._rule_folders, current_folder=self._rule_folder_now, startup_choices=self._startup_choices(),
        )
        if page is not None:
            dialog.show_page(page)
        dialog.add_account_requested.connect(lambda: self.add_account(after=dialog.reload))
        dialog.accounts_changed.connect(lambda: self.reload_accounts(select_saved=False, then=self._sync_calendars))
        dialog.restore_key_requested.connect(self.restore_key)
        dialog.interface_scale_tried.connect(self.try_interface_scale)
        dialog.windows_scale_tried.connect(self.try_windows_scale)
        dialog.reading_scale_tried.connect(self.try_reading_scale)
        dialog.list_scale_tried.connect(self.try_list_scale)
        accepted = dialog.exec() == SettingsDialog.DialogCode.Accepted
        if not accepted:  # Cancel: the sizes they had
            if dialog.interface_slider.value() != scale.slider_position(dialog.interface_scale_saved):
                self.try_interface_scale(dialog.interface_scale_saved)
            if dialog.windows_slider.value() != scale.slider_position(dialog.windows_scale_saved):
                self.try_windows_scale(dialog.windows_scale_saved)
            self.try_reading_scale(dialog.reading_scale_saved)
            self.try_list_scale(dialog.list_scale_saved)
        self._tried_interface = self._tried_windows = None  # from now on the saved sizes
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
        self.operations.sync_hide_setting()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            appearance.apply(app, settings)  # the colours and the style take effect at once
            self._apply_main_font()
            self.setStyleSheet(theme.stylesheet())  # the colours in it come from the palette: ask for them again
            self._restyle_status()  # the red of an error follows the colours
            self._apply_scaled_parts()
        self.tray.set_visible(prefs.get_bool(settings, "tray"))
        minutes = self._auto_minutes()
        auto = prefs.get_bool(settings, "auto_check")
        if auto and self.act_auto.isChecked():
            self._timer.start(minutes * 60 * 1000)  # a new interval
        self.act_auto.setChecked(auto)
        self.act_conversations.setChecked(prefs.get_bool(settings, "conversations"))
        self._layout = prefs.get_text(settings, "list_layout", ("columns", "cards"))
        self._columns = self._saved_columns()
        self._date_style = prefs.get_text(settings, "date_style", ("short", "full", "iso"))
        self._apply_list_layout()
        self._apply_list_look()
        self.set_reading_pane(prefs.get_text(settings, "reading_pane", ("right", "bottom", "off")))
        self._apply_reader_preferences()
        self._apply_shortcuts()
        if self._accounts:
            self._populate_tree()  # the sections of the folder list follow the settings

    def try_interface_scale(self, factor: float) -> None:
        """Show the main window at this size factor now, without saving it (the slider in Settings moves, or Cancel)."""
        self._tried_interface = factor
        self._apply_tried_sizes()

    def try_windows_scale(self, factor: float) -> None:
        """Show the other windows and dialogs at this size factor now, without saving it."""
        self._tried_windows = factor
        self._apply_tried_sizes()

    def _apply_tried_sizes(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        appearance.apply(app, self.settings, self._tried_interface, self._tried_windows)  # a size not being tried out is the saved one
        self._apply_main_font()
        self.setStyleSheet(theme.stylesheet())
        self._apply_scaled_parts()
        self._apply_list_look(scale.message_list())  # the list keeps the size it shows (also one being tried out, not saved yet)

    def _apply_main_font(self) -> None:
        """The text of this window and of the menus and popups opened from it, at the size of the Main window slider."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        font = appearance.main_font(app)
        self.setFont(font)
        for child in self.findChildren(QWidget):
            if child.isWindow() and child.windowType() == Qt.WindowType.Popup:
                child.setFont(font)  # menus made already; new ones get it when they are first shown (appearance.MainWindowPopups)

    def _writing_points(self, factor: float | None = None) -> float:
        """The text size of the composer: the system's size times the size of the text of messages (reading and writing are one size)."""
        app = QApplication.instance()
        system = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        return round(system * (factor if factor is not None else scale.saved_reading(self.settings)), 2)

    def try_list_scale(self, factor: float) -> None:
        """Show the message list at this size now, without saving it."""
        self._apply_list_look(factor)

    def try_reading_scale(self, factor: float) -> None:
        """Show the text of messages, read and being written, at this size now, without saving it."""
        for reader in (self.reader, *(window.reader for window in self._message_windows)):
            reader.set_zoom(round(factor * 100))
        for composer in self._composers:
            composer.set_text_size(self._writing_points(factor))

    def _apply_scaled_parts(self) -> None:
        """The sizes the style list does not hold: this window's at its own size factor, the message windows' and the
        composers' at that of the other windows."""
        self.section_bar.apply_scale()
        self.search_pill.apply_scale()
        self.tree.setMinimumWidth(px(180))
        for reader in (self.reader, *(window.reader for window in self._message_windows)):
            reader.apply_scale()
        for window in (*self._message_windows, *self._composers):
            window.setStyleSheet(theme.stylesheet(scale.windows()))

    def _apply_list_look(self, factor: float | None = None) -> None:
        """The message list at its own size factor (a factor given here is tried out, not saved): its text is the
        system's size times the factor, and its rows, icons and columns follow (scale.lpx)."""
        app = QApplication.instance()
        system = appearance.system_font_size(app) if isinstance(app, QApplication) else appearance.FALLBACK_FONT_POINTS
        scale.set_message_list(factor if factor is not None else scale.saved_list(self.settings, system))
        font = QFont(QApplication.font())
        font.setPointSizeF(round(system * scale.message_list(), 2))
        self.list_view.setFont(font)
        self.list_view.header().setFont(font)
        self.list_view.header().setStyleSheet(f"QHeaderView::section {{ font-size: {font.pointSizeF()}pt; }}")  # the style rules of the header draw with their own font otherwise
        self.list_view.header().setMinimumSectionSize(scale.lpx(MINIMUM_ICON))  # icon columns may be narrower at a small size
        self.column_delegate.row_height = COLUMN_ROW_HEIGHTS[prefs.get_text(self.settings, "list_density", prefs.DENSITIES)]
        self.list_view.doItemsLayout()
        self._fit_columns()

    def _apply_reader_preferences(self) -> None:
        for reader in (self.reader, *(window.reader for window in self._message_windows)):
            self._set_reader_preferences(reader)
        for window in self._message_windows:
            window.setStyleSheet(theme.stylesheet(scale.windows()))

    def _set_reader_preferences(self, reader: ReaderWidget) -> None:
        reader.always_remote = prefs.get_bool(self.settings, "remote_always")
        reader.allowed_senders = prefs.allowed_senders(self.settings)
        reader.prefer_plain = prefs.get_bool(self.settings, "prefer_plain")
        reader.set_zoom(round(scale.saved_reading(self.settings) * 100))

    def _prepare_message_reader(self, reader: ReaderWidget) -> None:
        """A message window: the settings first, and its Always for this sender saves like the one in the reading pane."""
        self._set_reader_preferences(reader)
        reader.sender_allowed.connect(self._sender_allowed)

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
        folder_only = action is self.act_rename_folder  # F2 renames the folder only while the folder list has the focus
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut if letter or folder_only else Qt.ShortcutContext.WindowShortcut)
        if letter or folder_only:
            for widget in (self.tree,) if folder_only else (self.list_view, self.tree):
                widget.addAction(action)

    def _mark_read_mode(self) -> str:
        return prefs.get_text(self.settings, "mark_read", prefs.MARK_READ)

    def _compose_options(self) -> ComposeOptions:
        return ComposeOptions(
            prefs.get_int(self.settings, "autosave_seconds", 0, 600), prefs.get_bool(self.settings, "warn_no_subject"),
            self._writing_points(), prefs.get_text(self.settings, "reply_quote", prefs.REPLY_QUOTES) == "below",
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

    def _notice(self, text: str) -> None:
        """Something the engine wants the person to know (an IMAP change waits for the server)."""
        self.show_status(text, error=True)

    def _progress(self, what: str, done: int, total: object) -> None:
        self.show_status(f"{what}… {done:,}" + (f" of {total:,}" if isinstance(total, int) and total else f" of {total}" if total else ""))

    def _counts_changed(self, total: int, unread: int) -> None:
        self.count_label.setText(f"{total:,} messages" + (f", {unread:,} unread" if unread else ""))

    def _report(self, title: str, error: ChronoMailError) -> None:
        self.show_status(f"{title}: {error}", error=True)
        if isinstance(error, MessageNotFoundError):
            self._missing_timer.start()

    def _note_gone(self, result: object) -> None:
        """A delete or a move to Junk found the file of its message gone (it only left the index): warn about it."""
        if getattr(result, "outcome", "") == "gone" or getattr(result, "gone", False):
            self._missing_timer.start()

    def _show_missing_warning(self) -> None:
        self.show_status(MISSING_WARNING, error=True)
        QMessageBox.warning(self, "Messages missing", MISSING_WARNING)

    # --- accounts and folders ----------------------------------------------------------------------------

    def start(self) -> None:
        """Called once the window is shown: make sure the key is there, load the accounts (or ask for the
        first one), then check for mail."""
        check = self.check_mail if bool_setting(self.settings, "check_on_start", True) else None
        self._load_contact_addresses()
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

        self._load_tags()
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
        wanted: list[tuple[str, str, str]] = [keep] if keep is not None else []
        if keep is None and select_saved:
            startup = str(self.settings.value("startup_view", "") or "").split("\t")
            if startup[0] == "mail" and len(startup) == 4:
                wanted.append((startup[1], startup[2], startup[3]))  # the folder chosen in Settings; when it is gone, the last selected one
            saved_account, saved_folder = str(self.settings.value("last_account", "")), str(self.settings.value("last_folder", ""))
            saved_kind = str(self.settings.value("last_kind", "folder"))
            if saved_folder and (saved_account or saved_kind in ("favorite", "groupinbox")):
                wanted.append((saved_account, saved_kind, saved_folder))
        self._updating_tree = True
        self.tree_model.pins = [(str(getattr(pin, "pin_id")), str(getattr(pin, "account")), str(getattr(pin, "folder")), str(getattr(pin, "name"))) for pin in getattr(self._sidebar, "pins", ())]
        self.tree_model.groups = [(str(getattr(group, "group_id")), str(getattr(group, "name")), tuple(str(m) for m in getattr(group, "accounts"))) for group in getattr(self._sidebar, "groups", ())]
        self.tree_model.hidden = {str(view) for view in getattr(self._sidebar, "hidden", ())}
        self.tree_model.show_favorites = bool_setting(self.settings, "show_favorites", True)
        self.tree_model.show_local_folders = bool_setting(self.settings, "show_local_folders", True)
        self.tree_model.show_tags = bool_setting(self.settings, "show_tags", True)
        self.tree_model.tags = [(str(getattr(tag, "name")), self._tag_book.colour(str(getattr(tag, "name"))).name()) for tag in self._tags if offered_for_mail(tag)]
        self.tree_model.populate(self._accounts, self._folders)
        target = next((found for found in (self._find_tree_item(key) for key in wanted) if found is not None), None)
        if target is None:
            target = self._first_inbox()
        if target is not None and target.isValid():
            self.tree.setCurrentIndex(target)  # this opens the parents of the item: what the person closed is closed again below
        self._restore_expanded(target)
        self._updating_tree = False
        if target is not None and target.isValid() and self._key_of(target) != (self._account_id, self._tree_key_kind, self._tree_path):
            self._tree_changed(target, QModelIndex())

    def _startup_choices(self) -> list[tuple[str, str, bool, int]]:
        """The choices of Settings > General > Show on startup: (text, value, can be chosen, depth). The last selected folder,
        the Favorites, Local Folders, Calendar, Contacts, Tasks, then the other accounts and groups, as the folder list has them."""
        choices: list[tuple[str, str, bool, int]] = [("Last selected folder", "", True, 0)]

        def name(index: QModelIndex) -> str:
            return str(index.data(NAME_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or "")

        def walk(parent: QModelIndex, depth: int) -> None:
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if str(index.data(KIND_ROLE)) in ("account", "group"):
                    choices.append((name(index), "-", False, depth))  # a heading
                else:
                    choices.append((name(index), "mail\t" + "\t".join(self._key_of(index)), True, depth))
                walk(index, depth + 1)

        top = [self.tree_model.index(row, 0, QModelIndex()) for row in range(self.tree_model.rowCount(QModelIndex()))]
        local_ids = {str(getattr(account, "account_id")) for account in self._accounts if str(getattr(account, "kind", "")) == "local"}
        for index in top:
            if str(index.data(KIND_ROLE)) == "section":
                for row in range(self.tree_model.rowCount(index)):
                    favorite = self.tree_model.index(row, 0, index)
                    choices.append((f"Favorites {name(favorite)}", "mail\t" + "\t".join(self._key_of(favorite)), True, 0))
        local = [index for index in top if str(index.data(KIND_ROLE)) == "account" and str(index.data(ACCOUNT_ROLE)) in local_ids]
        for index in local:
            choices.append((name(index), "-", False, 0))
            walk(index, 1)
        choices.extend((label, f"section\t{section}", True, 0) for label, section in (("Calendar", CALENDAR), ("Contacts", CONTACTS), ("Tasks", TASKS)))
        for index in top:
            if str(index.data(KIND_ROLE)) in ("account", "group") and index not in local:
                choices.append((name(index), "-", False, 0))
                walk(index, 1)
        return choices

    def _load_tree_state(self) -> dict[str, bool]:
        """Which headings, accounts and folders the person opened or closed in the folder list (kept in the settings)."""
        try:
            data = json.loads(str(self.settings.value("tree_state", "{}")))
        except ValueError:
            return {}
        return {key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, bool)} if isinstance(data, dict) else {}

    @staticmethod
    def _node_key(index: QModelIndex) -> str:
        kind = str(index.data(KIND_ROLE))
        detail = str(index.data(Qt.ItemDataRole.DisplayRole)) if kind == "section" else str(index.data(GROUP_ROLE)) if kind == "group" else str(index.data(FOLDER_PATH_ROLE) or "")
        return "|".join((kind, str(index.data(ACCOUNT_ROLE) or ""), detail))

    def _tree_opened(self, index: QModelIndex) -> None:
        self._tree_toggled(index, True)

    def _tree_closed(self, index: QModelIndex) -> None:
        self._tree_toggled(index, False)

    def _tree_toggled(self, index: QModelIndex, opened: bool) -> None:
        """The person opened or closed a node: remember it, also for the next start."""
        if self._updating_tree:
            return
        self._tree_state[self._node_key(index)] = opened
        if len(self._tree_state) > MAXIMUM_TREE_STATE:
            self._tree_state.pop(next(iter(self._tree_state)))
        self.settings.setValue("tree_state", json.dumps(self._tree_state))

    def _restore_expanded(self, selected: QModelIndex | None) -> None:
        """Open and close the nodes as the person left them. What was never opened or closed: headings open, accounts open
        (with more than three accounts only the one holding the selection), folders closed. The selection is never hidden."""
        holding: set[str] = set()
        node = selected if selected is not None and selected.isValid() else QModelIndex()
        while node.isValid():
            holding.add(self._node_key(node))
            node = node.parent()
        accounts = sum(1 for row in range(self.tree_model.rowCount()) if self.tree_model.index(row, 0).data(KIND_ROLE) == "account")

        def walk(parent: QModelIndex) -> None:
            for row in range(self.tree_model.rowCount(parent)):
                index = self.tree_model.index(row, 0, parent)
                if not self.tree_model.hasChildren(index):
                    continue
                key = self._node_key(index)
                kind = str(index.data(KIND_ROLE))
                if key in self._tree_state:
                    opened = self._tree_state[key]
                else:
                    opened = kind == "section" or (kind == "account" and accounts <= 3) or key in holding
                self.tree.setExpanded(index, opened)
                walk(index)

        walk(QModelIndex())

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

    def _inbox_under(self, row: QModelIndex) -> QModelIndex | None:
        """The Inbox of an account row, or the All Inboxes of a group row; None when there is none."""
        wanted = "groupinbox" if str(row.data(KIND_ROLE)) == "group" else "folder"
        children = [self.tree_model.index(child, 0, row) for child in range(self.tree_model.rowCount(row))]
        return next((index for index in children if index.data(KIND_ROLE) == wanted and index.data(FOLDER_ROLE_ROLE) == "inbox"), None)

    def _tree_clicked(self, index: QModelIndex) -> None:
        """A click on a heading folds it open or shut (a heading is never selected)."""
        if str(index.data(KIND_ROLE)) == "section":
            self.tree.setExpanded(index, not self.tree.isExpanded(index))

    def _first_inbox(self) -> QModelIndex | None:
        """The Inbox of the first account, also when the accounts are in groups."""
        top = [self.tree_model.index(row, 0, QModelIndex()) for row in range(self.tree_model.rowCount(QModelIndex()))]
        accounts: list[QModelIndex] = []
        for index in top:
            if index.data(KIND_ROLE) == "account":
                accounts.append(index)
            elif index.data(KIND_ROLE) == "group":
                accounts.extend(child for child in (self.tree_model.index(row, 0, index) for row in range(self.tree_model.rowCount(index))) if child.data(KIND_ROLE) == "account")
        for account in accounts:
            for child in range(self.tree_model.rowCount(account)):
                index = self.tree_model.index(child, 0, account)
                if index.data(FOLDER_ROLE_ROLE) == "inbox":
                    return index
        return None

    def _tree_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if self._updating_tree or not current.isValid():
            return
        kind = str(current.data(KIND_ROLE))
        if kind == "section":  # a heading is never what the list shows: the highlight goes back to the folder that is shown
            if previous.isValid() and str(previous.data(KIND_ROLE)) != "section":
                self._updating_tree = True
                self.tree.setCurrentIndex(previous)
                self._updating_tree = False
            return
        if kind in ("account", "group"):  # the row stays selected and shows its Inbox: never a folder of another account
            inbox = self._inbox_under(current)
            if inbox is None:  # an account without an Inbox: an empty list that says so
                self._account_id, self._folder, self._combined, self._tree_kind = None, None, None, "none"
                self.act_empty_folder.setEnabled(False)
                self.act_mark_all_read.setEnabled(False)
                self._clear_reader()
                self._shown = None
                self.list_model.show(None)
                self._update_unread_summary()
                return
            current = inbox
            kind = str(current.data(KIND_ROLE))
        self._tree_key_kind = kind
        self._tree_path = str(current.data(FOLDER_PATH_ROLE) or "")
        self._combined = "inboxes" if kind == "groupinbox" else (str(current.data(FOLDER_PATH_ROLE)) if kind == "favorite" else None)
        self._combined_accounts = self._group_accounts(str(current.data(GROUP_ROLE))) if kind == "groupinbox" else ()
        self._combined_tag = str(current.data(FOLDER_PATH_ROLE)) if kind == "tagview" else ""
        if kind == "tagview":
            self._combined = "tagged"
        if kind == "pinlink":
            kind = "folder"
        self._account_id = str(current.data(ACCOUNT_ROLE))  # "" in a favorite: the rows say which account they belong to
        self._tree_kind = kind
        self._folder = str(current.data(FOLDER_PATH_ROLE)) if kind == "folder" else None
        self._folder_role = str(current.data(FOLDER_ROLE_ROLE) or "other")
        self.act_empty_folder.setEnabled(kind == "folder" and self._folder_role in ("trash", "junk"))
        self.act_not_junk.setEnabled(kind == "folder" and self._folder_role == "junk")
        self._show_archive_action()
        self.act_mark_all_read.setEnabled(kind == "folder")
        self._select_quick_filters_for(kind)
        self._advanced = None
        self.filter_chip.hide()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.search_pill.set_active(False)
        self._clear_reader()
        self._shown = None
        self._update_unread_summary()
        self._update_sort_menu()
        if self._layout == "columns" and self.list_model.columns != self._list_columns():
            self._apply_list_layout()  # Snoozed until comes or goes
        self._apply_view()

    def _select_quick_filters_for(self, kind: str) -> None:
        for button, wanted in ((self.filter_unread, kind == "unread"), (self.filter_flagged, kind == "flagged")):
            button.blockSignals(True)
            button.setChecked(wanted)
            button.blockSignals(False)
        self.filter_attachments.blockSignals(True)
        self.filter_attachments.setChecked(False)
        self.filter_attachments.blockSignals(False)
        self._show_filter_state()

    def _search_text_changed(self, text: str) -> None:
        if text == "?":
            self.search_history.hide()
            self.search_help.open_under(self.search_pill)
            return
        if self.search_history.isVisible():
            self.search_history.show_entries(matching(self._search_history, text), self.search_pill)
        if not text:
            self._advanced = None
            self.filter_chip.hide()
            self.search_pill.set_active(False)
            self._apply_view()

    def _run_search(self) -> None:
        """Enter in the search box: the search is kept in the history and the field shows that a search is active."""
        self.search_history.hide()
        text = self.search_box.text().strip()
        if text:
            self._search_history = with_search(self._search_history, text)
            self.settings.setValue("search_history", history_text(self._search_history))
        self.search_pill.set_active(bool(text))
        self._apply_view()

    def _clear_search(self) -> None:
        """The red ✕: the search ends and the whole folder is shown again."""
        self.search_box.clear()
        self.search_pill.set_active(False)
        self.search_box.setFocus()

    def _show_search_history(self) -> None:
        if self.search_box.text() != "?":
            self.search_history.show_entries(matching(self._search_history, self.search_box.text()), self.search_pill)

    def _search_history_picked(self, text: str) -> None:
        self.search_box.blockSignals(True)
        self.search_box.setText(text)
        self.search_box.blockSignals(False)
        self.search_box.setFocus()
        self._run_search()

    def _search_history_removed(self, text: str) -> None:
        self._search_history = [entry for entry in self._search_history if entry != text]
        self.settings.setValue("search_history", history_text(self._search_history))
        self.search_history.show_entries(matching(self._search_history, self.search_box.text()), self.search_pill)

    def _search_help_picked(self, line: HelpLine) -> None:
        """Puts what the picked help line stands for in the search box, in place of the "?"."""
        self.search_box.blockSignals(True)
        self.search_box.setText(line.inserted)
        self.search_box.blockSignals(False)
        self.search_box.setCursorPosition(len(line.inserted) - line.cursor_back)
        self.search_box.setFocus()

    def _search_help_typed(self, event: QKeyEvent) -> None:
        send_key(self.search_box, event)

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
        self.search_pill.set_active(bool(wanted.query.strip()))
        self.scope.blockSignals(True)
        self.scope.setCurrentIndex(1 if wanted.all_folders else 0)
        self.scope.blockSignals(False)
        for button, on in ((self.filter_unread, wanted.unread), (self.filter_flagged, wanted.flagged), (self.filter_attachments, wanted.attachments)):
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
        self._show_filter_state()
        self._apply_view()

    def _focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()

    # --- the message list ------------------------------------------------------------------------------

    def view_spec(self) -> ViewSpec | None:
        if self._account_id is None:
            return None
        if self._combined is not None:
            return ViewSpec(
                "", None, self.search_box.text().strip(), self._effective_sort(), self._oldest_first,
                combined=self._combined, combined_accounts=self._combined_accounts, tag=self._combined_tag or None,
            )
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
            self._account_id, folder, query, self._effective_sort(), self._oldest_first, unread, flagged, attachments,
            date_from=advanced.date_from if advanced else None, date_to=advanced.date_to if advanced else None,
            minimum_size=advanced.minimum_size if advanced else None, maximum_size=advanced.maximum_size if advanced else None,
            conversations=grouped and self._filter_tag is None, tag=self._filter_tag,
        )

    def _apply_view(self) -> None:
        self.delegate.show_recipients = self._folder_role in ("sent", "drafts") or self._folder == "Outbox" or self._combined == "outbox"
        self.list_model.recipients_first = self.delegate.show_recipients
        self.list_model.show(self.view_spec())

    def _update_sort_menu(self) -> None:
        self.sort_menu.clear()
        sort = self._effective_sort()
        for value, label in SORT_CHOICES:
            if value == "snoozed" and not self._in_snoozed():
                continue  # only the Snoozed folder knows when its messages come back
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == sort)
            action.triggered.connect(lambda checked=False, chosen=value: self._set_sort(chosen, self._oldest_first))
        self.sort_menu.addSeparator()
        for flag, label in ((False, "Newest / Z–A / largest first"), (True, "Oldest / A–Z / smallest first")):
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(flag == self._oldest_first)
            action.triggered.connect(lambda checked=False, chosen=flag: self._set_sort(self._sort, chosen))
        label = dict(SORT_CHOICES).get(sort, "Received")
        self.sort_button.setText(f"Sorted by {label} ▾")

    def _in_snoozed(self) -> bool:
        """The Snoozed folder of an account is shown (its messages know when they come back)."""
        return self._combined is None and self._tree_kind == "folder" and self._folder == SNOOZED_FOLDER_PATH

    def _effective_sort(self) -> str:
        """The chosen sort order; sorting by Snoozed until is sorting by Received outside the Snoozed folder."""
        return "date" if self._sort == "snoozed" and not self._in_snoozed() else self._sort

    def _list_columns(self) -> tuple[str, ...]:
        """The columns of the list: Snoozed until only in the Snoozed folder."""
        return self._columns if self._in_snoozed() else without_snoozed_column(self._columns)

    def _set_sort(self, sort: str, oldest_first: bool) -> None:
        current = self._current_row()
        self._reselect = str(getattr(current, "message_id")) if current is not None else None  # the selected message stays selected
        self._reselect_asked = None
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
        chosen = self.list_model.row_at(current.row()) if current.isValid() else None
        if chosen is not None and self._reselect is not None and str(getattr(chosen, "message_id")) != self._reselect:
            self._reselect = None  # the person chose another message meanwhile
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
            self.reader.set_tags(self._tag_book.html(self._tags_of_message(message_id)))
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
        self._select_after_delete()
        pending = getattr(self, "_reselect", None)
        if not pending:
            return
        position = self.list_model.find(pending)
        if position >= 0:
            self._reselect = None
            index = self.list_model.index(position)
            self.list_view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.list_view.scrollTo(index)
            return
        self._find_unloaded(pending)

    def _find_unloaded(self, message_id: str) -> None:
        """The message to select again is not among the loaded rows: a folder asks where it stands and loads that page; a
        search or a Favorites view loads its next batch (each arrival comes back here) until it has it or has no more."""
        spec = self.list_model.spec
        if spec is None:
            self._reselect = None
            return
        if spec.kind is not ViewKind.FOLDER:
            if self.list_model.canFetchMore():
                self.list_model.fetchMore()
            else:
                self._reselect = None  # not in this view (any more)
            return
        if self._reselect_asked == message_id:
            return  # its page is on its way
        self._reselect_asked = message_id

        def found(result: object) -> None:
            position = getattr(result, "position", None)
            if self._reselect != message_id or self.list_model.spec != spec:
                return  # the person chose another message or view meanwhile
            if not isinstance(position, int) or self.list_model.display_row(position) < 0:
                self._reselect = None  # not in the folder any more, or in a folded group
                return
            self.list_model.load_position(position)

        values = {"account": spec.account, "folder": spec.folder or "", "message": message_id, "sort": spec.sort, "oldest_first": spec.oldest_first}
        self.backend.call("message.position", values, found, lambda error: setattr(self, "_reselect", None), owner=self)

    def _keep_place_after_delete(self, message_ids: list[str]) -> None:
        """Before a delete, move, archive or junk: remember the scroll position and which message to select once the list is back.

        The selected message stays selected when it is not deleted; else the message right after the deleted ones, or the one
        just before them when they were at the end."""
        model = self.list_model
        deleted_ids = set(message_ids)
        deleted: list[int] = []
        for message_id in deleted_ids:
            position = model.message_position(model.find(message_id))
            if position is not None:
                deleted.append(position)
        current = self.list_view.currentIndex()
        current_row = model.row_at(current.row()) if current.isValid() else None
        current_position = model.message_position(current.row()) if current.isValid() else None
        if current_row is not None and str(getattr(current_row, "message_id", "")) in deleted_ids:
            self._show_request += 1  # the message that is being opened is moved away: its opening no longer counts
            self._mark_read_timer.stop()
        target: tuple[str | None, int] | None = None
        if current_row is not None and current_position is not None and str(getattr(current_row, "message_id")) not in deleted_ids:
            target = (str(getattr(current_row, "message_id")), current_position - sum(1 for position in deleted if position < current_position))
        elif deleted:
            last, total = max(deleted), model.message_count()
            after = next((position for position in range(last + 1, total) if position not in deleted), None)
            before = next((position for position in range(last - 1, -1, -1) if position not in deleted), None)
            chosen = after if after is not None else before
            if chosen is not None:
                message = model.row_at(model.display_row(chosen))
                target = (str(getattr(message, "message_id")) if message is not None else None, chosen - sum(1 for position in deleted if position < chosen))
        self._after_delete = None if target is None else (target[0], target[1], self.list_view.verticalScrollBar().value())

    def _select_after_delete(self) -> None:
        """After the list came back from a delete: the same scroll position, and the message chosen before the delete selected."""
        pending = self._after_delete
        if pending is None:
            return
        message_id, position, scroll = pending
        if scroll is not None:
            self.list_view.verticalScrollBar().setValue(scroll)
            self._after_delete = (message_id, position, None)  # the list scrolls once; the selection may wait for its page
        model = self.list_model
        row = model.find(message_id) if message_id is not None else -1
        if row < 0:
            row = model.display_row(position)
        if row < 0 or row >= model.rowCount():
            self._after_delete = None  # in a folded group, or gone
            return
        if model.row_at(row, fetch=True) is None:
            return  # its page is on the way: tried again when it has loaded
        self._after_delete = None
        self.list_view.selectionModel().setCurrentIndex(model.index(row), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)

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
            self.backend.call("message.flag", {"account": account_id, "folder": folder, "message": message_id, **values}, make_done(message_id), lambda error: self._report("Could not change the message", error), owner=self, in_order=True)

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
        self._delete_rows(self._selected_rows())

    def _delete_rows(self, rows: list[object]) -> None:
        if not rows or self._account_id is None:
            return
        in_trash = self._folder_role == "trash"
        if in_trash and QMessageBox.question(self, "Delete", f"Delete {len(rows)} message(s) for good? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        account = self._account_id
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])
        done, failed = self._answers(len(rows), f"Deleted {len(rows)} message(s)" if in_trash else f"Moved {len(rows)} message(s) to Trash", "Could not delete", self._note_gone)
        for row in rows:
            self.backend.call("message.delete", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, failed, owner=self, in_order=True)

    def _show_archive_action(self) -> None:
        """In the Archive folder the Archive button, key and menu item are Unarchive."""
        archived = self._tree_kind == "folder" and self._folder_role == "archive"
        self.act_archive.setText("Unarchive" if archived else "Archive")
        self.act_archive.setIconText("Unarchive" if archived else "Archive")
        self.act_archive.setIcon(theme_icon("archive-extract" if archived else "archive-insert"))

    def unarchive_selected(self) -> None:
        """Back to the folder each message was archived from (the Inbox when that is not known)."""
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account = self._account_id
        remaining = len(rows)
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Unarchived {len(rows)} message(s)")

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining
            remaining -= 1
            self._report("Could not unarchive", error)

        for row in rows:
            self.backend.call("message.unarchive", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, failed, owner=self, in_order=True)

    def archive_selected(self) -> None:
        if self._tree_kind == "folder" and self._folder_role == "archive":
            self.unarchive_selected()
            return
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account = self._account_id
        remaining = len(rows)
        created = False
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])

        failures = 0

        def done(result: object) -> None:
            nonlocal remaining, created
            created = created or bool(getattr(result, "created_folder"))
            remaining -= 1
            if remaining == 0:
                self._after_change(f"Archived {len(rows)} message(s)", keep_status=failures > 0)

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining, failures
            remaining -= 1
            failures += 1
            self._report("Could not archive", error)
            if remaining == 0:
                self._after_change(f"Archived {len(rows)} message(s)", keep_status=True)

        for row in rows:
            if str(getattr(row, "folder")) == "Archive":
                remaining -= 1  # already there
                continue
            self.backend.call("message.archive", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, failed, owner=self, in_order=True)
        if remaining == 0:
            self.show_status("The selected message(s) are in the archive already")

    def junk_selected(self, junk: bool) -> None:
        """Move the selected messages to the Junk folder, or (in Junk) back to the Inbox."""
        rows = self._selected_rows()
        if not rows or self._account_id is None:
            return
        account, command = self._account_id, "message.junk" if junk else "message.not-junk"
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])
        finished = f"Moved {len(rows)} message(s) to Junk" if junk else f"Moved {len(rows)} message(s) back to the Inbox"
        done, failed = self._answers(len(rows), finished, "Could not move", self._note_gone)

        for row in rows:
            self.backend.call(command, {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, failed, owner=self, in_order=True)

    # --- the sections: mail, calendar, tasks, contacts ---------------------------------------------------------

    MESSAGE_ACTIONS: tuple[str, ...] = (  # need a selected message: greyed out while none is selected
        "reply", "reply_all", "forward", "forward_attachment", "archive", "snooze", "read", "unread", "flag", "unflag", "toggle_read", "toggle_flag",
        "move_to", "quick_move", "copy_to", "quick_copy", "junk", "delete_forever", "open", "save_as", "source", "print", "edit_draft",
    )

    def _update_message_actions(self, *_: object) -> None:
        """Grey out what needs a selected message while none is selected (in the mail section; Delete also serves the other sections)."""
        if self._section != MAIL:
            return
        selected = self.list_view.selectionModel().hasSelection()
        for name in self.MESSAGE_ACTIONS:
            getattr(self, f"act_{name}").setEnabled(selected)
        self.act_delete.setEnabled(selected)
        self.mark_button.setEnabled(selected)
        self.tag_button.setEnabled(selected)
        self.move_button.setEnabled(selected)

    MAIL_ONLY: tuple[str, ...] = (
        "reply", "reply_all", "forward", "edit_draft", "save_as", "source", "print", "open", "junk", "not_junk", "next_unread", "previous_unread", "archive", "mark_all_read",
        "empty_folder", "read", "unread", "flag", "unflag", "snooze", "toggle_read", "toggle_flag", "delete_forever", "move_to", "quick_move", "copy_to", "quick_copy",
        "go_to_folder", "next_message", "previous_message", "advanced", "new_folder", "rename_folder", "delete_folder",
    )

    @property
    def section(self) -> str:
        return self._section

    def _in_section(self, what: str) -> None:
        """File > New, Refresh, Delete and Search do what the section that is shown needs."""
        page = self.pages.get(self._section)
        if page is not None:
            {"new": page.new_item, "refresh": page.refresh, "delete": page.delete_selected, "search": page.focus_search}[what]()
            return
        {"new": self.new_message, "refresh": self.send_and_receive, "delete": self.delete_selected, "search": self._focus_search}[what]()

    def switch_section(self, section: str) -> None:
        """Show another section: its sidebar, its main area and its toolbar. The mail-only actions are off while another section is shown."""
        if section not in {name for name, *_ in SECTIONS} or section == self._section:
            self.section_bar.show_section(self._section)  # the button of the section that is shown stays pressed
            return
        if self._section == MAIL and section != MAIL:
            self._mail_action_states = {name: getattr(self, f"act_{name}").isEnabled() for name in self.MAIL_ONLY if hasattr(self, f"act_{name}")}
            for name in self._mail_action_states:
                getattr(self, f"act_{name}").setEnabled(False)
        elif self._section != MAIL and section == MAIL:
            for name, enabled in self._mail_action_states.items():
                getattr(self, f"act_{name}").setEnabled(enabled)
        came_from = self._section
        self._section = section
        if section == MAIL:
            self._update_message_actions()
            if came_from == CONTACTS:
                self._load_contact_addresses()  # contacts may have been added or changed
        else:
            self.act_delete.setEnabled(True)  # Delete of the calendar, contacts and tasks
        index = self._stack_index(section)
        self.side_stack.setCurrentIndex(index)
        self.content_stack.setCurrentIndex(index)
        self.toolbar.setVisible(section == MAIL)
        for name, page in self.pages.items():
            page.toolbar.setVisible(name == section)
        self.section_bar.show_section(section)
        self.side_heading.setText(next(name for key, name, *_ in SECTIONS if key == section))
        page = self.pages.get(section)
        if page is not None:
            page.on_show()
        self.settings.setValue("last_section", section)
        if page is not None:
            self.setWindowTitle(self._title_for(section))
        else:
            self._update_unread_summary()

    def _stack_index(self, section: str) -> int:
        return 0 if section == MAIL else 1 + list(self.pages).index(section)

    def _title_for(self, section: str) -> str:
        return "Chrono Mail" if section == MAIL else f"Chrono Mail — {dict((name, label) for name, label, *_ in SECTIONS)[section]}"

    def _step_message(self, step: int) -> None:
        """Select the next (or the previous) message of the list."""
        count = self.list_model.rowCount()
        current = self.list_view.currentIndex().row() if self.list_view.currentIndex().isValid() else (-1 if step > 0 else count)
        position = current + step
        while 0 <= position < count and self.list_model.is_header(position):  # group headers are passed
            position += step
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
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])
        done, failed = self._answers(len(rows), f"Deleted {len(rows)} message(s) for good", "Could not delete", self._note_gone)
        for row in rows:
            self.backend.call(
                "message.delete", {"account": self._account_of(row) or self._account_id, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "permanent": True},
                done, failed, owner=self, in_order=True,
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
        done, failed = self._answers(len(rows), f"Copied {len(rows)} message(s) to {target}", "Could not copy")
        for row in rows:
            self.backend.call(
                "message.copy", {"account": self._account_of(row), "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "target": target},
                done, failed, owner=self, in_order=True,
            )

    def go_to_folder(self) -> None:
        """G: type a part of a folder's name and go to that folder."""
        names: dict[str, tuple[str, str]] = {}
        accounts = {str(getattr(account, "account_id")): account for account in self._accounts}
        for account_id, infos in self._folders.items():
            account = accounts.get(account_id)
            if account is None or str(getattr(account, "storage", "own")) == "local":
                continue
            label = "Local Folders" if str(getattr(account, "kind", "pop3")) == "local" else str(getattr(account, "shown_name"))
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
        self._keep_place_after_delete([message_id for _, message_id in messages])
        done, failed = self._answers(len(messages), f"Moved {len(messages)} message(s) to {target}", "Could not move")
        for folder, message_id in messages:
            self.backend.call("message.move", {"account": account, "folder": folder, "message": message_id, "target": target}, done, failed, owner=self, in_order=True)

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
        account = self._account_id
        self._keep_place_after_delete([str(getattr(row, "message_id")) for row in rows])
        done, failed = self._answers(len(rows), f"Moved {len(rows)} message(s) to {target}", "Could not move")
        for row in rows:
            self.backend.call("message.move", {"account": self._account_of(row) or account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "target": target}, done, failed, owner=self, in_order=True)

    def _answers(self, count: int, finished: str, title: str, each: Callable[[object], None] | None = None) -> tuple[Callable[[object], None], Callable[[ChronoMailError], None]]:
        """The answers of an action on several messages, one per message: when all are in, failed ones too, the list is
        shown again; the summary only when none failed, else the error stays in the red line."""
        remaining, failures = count, 0

        def finish() -> None:
            if remaining == 0:
                self._after_change(finished, keep_status=failures > 0)

        def done(result: object) -> None:
            nonlocal remaining
            remaining -= 1
            if each is not None:
                each(result)
            finish()

        def failed(error: ChronoMailError) -> None:
            nonlocal remaining, failures
            remaining -= 1
            failures += 1
            self._report(title, error)
            finish()

        return done, failed

    def _after_change(self, text: str, keep_status: bool = False) -> None:
        """Something changed in the store: show the same folder again and update the counts (keep_status: an error is shown)."""
        if not keep_status:
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
        window = ComposeWindow(self.backend, self._mail_accounts(), account_id, draft, source, resume, bool_setting(self.settings, "compose_html", True), self._compose_options(), settings=self.settings)
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
            window = MessageWindow(self.backend, account, folder, message_id, result, self._compose_in_window, self._open_link, self._prepare_message_reader, self.settings)
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

    def set_list_layout(self, layout: str) -> None:
        """View > Message list: the column layout or the card layout, kept in the settings as in the Message list window."""
        self._layout = layout if layout in ("columns", "cards") else "columns"
        self.settings.setValue("list_layout", self._layout)
        self._apply_list_layout()

    def _apply_list_layout(self) -> None:
        cards = self._layout == "cards"
        for value, action in self.layout_actions.items():
            action.setChecked(value == self._layout)
        self.list_model.date_style = self._date_style
        self.list_model.tag_text = bool_setting(self.settings, "tag_text_colour", False)
        header = self.list_view.header()
        if cards:
            self.list_model.set_columns((CARD,))
            header.hide()
            self.list_view.setItemDelegate(self.delegate)
            self.list_view.setUniformRowHeights(False)  # group headers are lower than cards; costs time on very large lists (D-095)
            self._fit_columns()
        else:
            self.list_model.set_columns(self._list_columns())
            header.show()
            self.list_view.setItemDelegate(self.column_delegate)
            self.list_view.setUniformRowHeights(True)  # headers are as high as the rows: fast with any number of messages
            self._fit_columns()
            header.setSortIndicatorShown(True)
            self._show_sort_indicator()
        self.list_model.refresh_cells()

    def _show_sort_indicator(self) -> None:
        sort_of = {
            "sender": ("from_to", "from"), "subject": ("subject",), "date": ("date",), "size": ("size",), "to": ("to",), "snoozed": (SNOOZED_COLUMN,),
            "unread": ("status",), "flagged": ("flag",), "attachment": ("attachment",), "replied": ("replied",), "contact": ("contact",),
            "folder": ("folder",), "conversation": ("conversation",), "preview": ("preview",),
        }
        header = self.list_view.header()
        for position, column_id in enumerate(self.list_model.columns):
            if column_id in sort_of.get(self._effective_sort(), ()):
                header.setSortIndicator(position, Qt.SortOrder.AscendingOrder if self._oldest_first else Qt.SortOrder.DescendingOrder)
                return
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def _header_clicked(self, section: int) -> None:
        columns = self.list_model.columns
        if self._layout != "columns" or not 0 <= section < len(columns):
            return
        sort = BY_ID[columns[section]].sort
        if sort is None:
            self._show_sort_indicator()  # Qt moved the arrow to the clicked column by itself: put it back
            return
        if sort == self._effective_sort():
            self._set_sort(sort, not self._oldest_first)
        else:
            # text columns A to Z first, dates, sizes and conversations newest and largest first, the icon columns yes first, snoozed soonest first
            self._set_sort(sort, sort not in ("date", "size", "conversation") and sort not in YES_NO_SORTS)
        self._show_sort_indicator()

    def _columns_moved(self, logical: int, old_visual: int, new_visual: int) -> None:
        header = self.list_view.header()
        order = [self.list_model.columns[header.logicalIndex(visual)] for visual in range(header.count())]
        columns = keep_snoozed_column(tuple(order), self._columns)  # outside the Snoozed folder its column keeps its place
        if self._layout == "columns" and columns != self._columns and set(order) == set(self.list_model.columns):
            self._columns = columns
            self.settings.setValue("columns", ",".join(columns))
        self._fit_columns()  # the last column has no drag line at its right edge

    def _column_resized(self, section: int, old: int, new: int) -> None:
        """A column edge was dragged: the column changes, the text columns to its right take or give the difference (column fit)."""
        header = self.list_view.header()
        if self._fitting or self._layout != "columns" or not header.isVisible() or not 0 <= section < len(self.list_model.columns):
            return
        logical = self._visual_sections()
        order = [self.list_model.columns[index] for index in logical]
        icon = [BY_ID[column_id].symbol for column_id in order]
        position = logical.index(section)
        widths = [header.sectionSize(index) for index in logical]
        widths[position] = old
        self._apply_drag(logical, order, icon, drag(icon, widths, position, new))

    def _column_line_dragged(self, section: int, distance: int) -> None:
        """A column line is dragged with the mouse: all widths are worked out from the widths at the press and the whole distance moved."""
        start = self.list_view.list_header.drag_start_sizes
        if self._layout != "columns" or len(start) != len(self.list_model.columns) or not 0 <= section < len(start):
            return
        logical = self._visual_sections()
        order = [self.list_model.columns[index] for index in logical]
        icon = [BY_ID[column_id].symbol for column_id in order]
        widths = [start[index] for index in logical]
        position = logical.index(section)
        self._apply_drag(logical, order, icon, drag(icon, widths, position, widths[position] + distance))

    def _apply_drag(self, logical: list[int], order: list[str], icon: list[bool], result: list[int]) -> None:
        self._set_column_widths(logical, result)
        self._column_layout = layout_from_pixels(order, icon, result)
        self.settings.setValue("column_layout", self._column_layout.to_text())

    def _column_id(self, column: int) -> str:
        columns = self.list_model.columns
        return columns[column] if 0 <= column < len(columns) else ""

    def _visual_sections(self) -> list[int]:
        """The model columns in the order they are shown."""
        header = self.list_view.header()
        return [header.logicalIndex(visual) for visual in range(header.count())]

    def _fit_columns(self) -> None:
        """The columns fill exactly the width of the list: icon columns keep their pixels, text columns share the rest.
        In the card layout the one column is the width of the list."""
        if self.list_model.columns == (CARD,):
            header = self.list_view.header()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(0, self.list_view.viewport().width())
            return
        if self._layout != "columns" or not self.list_model.columns:
            return
        logical = self._visual_sections()
        order = [self.list_model.columns[index] for index in logical if 0 <= index < len(self.list_model.columns)]
        if len(order) != len(logical):
            return
        icon = [BY_ID[column_id].symbol for column_id in order]
        if self._column_layout is None:
            self._column_layout = layout_from_pixels(order, icon, [self._widths.get(column_id, BY_ID[column_id].width) for column_id in order])
        widths = fit(order, icon, self._column_layout, [BY_ID[column_id].width for column_id in order], self.list_view.viewport().width())
        self._set_column_widths(logical, widths)

    def _set_column_widths(self, logical: list[int], widths: list[int]) -> None:
        header = self.list_view.header()
        self._fitting = True
        try:
            for position, (section, width) in enumerate(zip(logical, widths)):
                last = position == len(logical) - 1
                header.setSectionResizeMode(section, QHeaderView.ResizeMode.Fixed if last else QHeaderView.ResizeMode.Interactive)  # no drag line at the right edge of the last column
                header.resizeSection(section, width)
        finally:
            self._fitting = False

    def _saved_columns(self) -> tuple[str, ...]:
        """The saved columns; the Contact and Delete columns, and Snoozed until, are added once to a set saved before they existed
        (switched off later, they stay off)."""
        columns = valid_columns([part for part in str(self.settings.value("columns", "")).split(",") if part])
        if not bool_setting(self.settings, "columns_contact_and_delete_added", False):
            columns = with_new_columns(columns)
            self.settings.setValue("columns", ",".join(columns))
            self.settings.setValue("columns_contact_and_delete_added", True)
        if not bool_setting(self.settings, "columns_snoozed_added", False):
            columns = with_snoozed_column(columns)
            self.settings.setValue("columns", ",".join(columns))
            self.settings.setValue("columns_snoozed_added", True)
        return columns

    def _icon_clicked(self, row: int, column_id: str) -> None:
        """A click on the Delete icon deletes that message; a click on the Contact icon opens the contact."""
        message = self.list_model.row_at(row)
        if message is None:
            return
        if column_id == "trash":
            self._delete_rows([message])
            return
        found = self.list_model.index(row, 0).data(CONTACT_ROLE)
        if column_id == "contact" and isinstance(found, tuple) and len(found) == 2:
            self.contacts_page.show_contact(str(found[0]), str(found[1]))
            self.switch_section(CONTACTS)

    def _load_contact_addresses(self) -> None:
        """The addresses of the contacts, for the Contact column."""

        def arrived(result: object) -> None:
            entries = result if isinstance(result, tuple) else ()
            self.list_model.contacts = {str(getattr(entry, "address")): (str(getattr(entry, "item")), str(getattr(entry, "collection"))) for entry in entries}
            self.list_model.refresh_cells()

        self.backend.call("contact.addresses", {}, arrived, lambda error: None, owner=self)

    def _header_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        chosen = set(self._columns)
        for column_id, column in BY_ID.items():
            if column_id == SNOOZED_COLUMN and not self._in_snoozed():
                continue
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
        """Message list…: layout, columns, reading pane, date style and row height."""
        density = prefs.get_text(self.settings, "list_density", prefs.DENSITIES)
        dialog = ColumnsDialog(ListChoice(self._layout, self._columns, self._reading_pane, self._date_style, density), self)
        if dialog.exec() != ColumnsDialog.DialogCode.Accepted:
            return
        self.apply_list_choice(dialog.choice())

    def apply_list_choice(self, choice: ListChoice) -> None:
        self._layout, self._columns, self._date_style = choice.layout, keep_snoozed_column(choice.columns, self._columns), choice.date_style
        for key, value in (("list_layout", self._layout), ("columns", ",".join(self._columns)), ("date_style", self._date_style), ("list_density", choice.density)):
            self.settings.setValue(key, value)
        self._apply_list_look()
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

    def send_and_receive(self) -> None:
        """Refresh: first send what waits in the Outbox, then check every account. Refresh stays greyed out meanwhile."""
        self.act_refresh.setEnabled(False)

        if self._importing:
            return

        def receive() -> None:
            if not self._checking:
                self.act_refresh.setEnabled(not self._importing)
            self.check_accounts(None, manual=True, new_round=False)
            self._settle_operations()  # nothing to check: the round ends with the send

        self.send_outbox(then=receive, manual=True)

    def send_outbox(self, then: Callable[[], None] | None = None, manual: bool = False) -> None:
        """Send All: the messages that wait in the Outbox (scheduled ones wait for their time). Manual: the person asked
        for it (the Operations window may open). Nothing is sent while an import runs."""
        if self._importing:
            return
        self._begin_operations(manual)
        self._sending = True

        def done(result: object) -> None:
            self._sending = False
            sent, failed = int(getattr(result, "sent")), tuple(str(text) for text in getattr(result, "failed"))
            accounts = tuple(str(account_id) for account_id in getattr(result, "failed_accounts", ()))
            for position, text in enumerate(failed):
                self._finish_operation(accounts[position] if position < len(accounts) else "", "send", text)
            self.operations.finish_all("send")
            if failed:
                self.show_status("Sending: " + "; ".join(failed), error=True)
            elif sent:
                self.show_status(f"Sent {sent} message(s) from the Outbox")
            if sent or failed:
                self._after_sent()
            if then is not None:
                then()
            else:
                self._settle_operations()

        def failed(error: ChronoMailError) -> None:
            self._sending = False
            self._report("Could not send the Outbox", error)
            self._finish_operation("", "send", str(error))
            self.operations.finish_all("send")
            if then is not None:
                then()
            else:
                self._settle_operations()

        self.backend.call("mail.send-outbox", {}, done, failed, owner=self)

    def _check_on_general_interval(self) -> None:
        """The general timer: the accounts that have no interval of their own."""
        self.check_accounts({str(getattr(account, "account_id")) for account in self._mail_accounts() if int(getattr(account, "check_minutes", 0)) == 0})

    def check_accounts(self, only: set[str] | None, manual: bool = False, new_round: bool = True) -> None:
        """Check these accounts (all when None) for new mail. Manual: the person asked for it (the Operations window may
        open); new_round False: it goes on from a send (Refresh), whose rows stay. Nothing is checked while an import runs."""
        if self._importing:
            return  # the timers and the push skip their turn; one check runs when the import is done
        self._load_contact_addresses()  # Refresh also brings contacts that came from a server
        mail_ids = {str(getattr(account, "account_id")) for account in self._mail_accounts()}
        wanted = mail_ids if only is None else only & mail_ids
        if only is None:
            self._sync_calendars()
        if self._checking or not wanted:
            return
        if new_round:
            self._begin_operations(manual)
        for account_id in sorted(wanted, key=self._operation_name_of):
            self.operations.add(account_id, "receive")
        self._checking = True
        self.act_refresh.setEnabled(False)
        remaining = set(wanted)
        totals = {"new": 0}
        new_by_account: dict[str, int] = {}
        problems: list[str] = []
        blocked: list[tuple[str, object]] = []  # (account, folder held back by a safety rule)

        def finish() -> None:
            self._checking = False
            self.act_refresh.setEnabled(not self._importing)
            if problems:
                self.show_status("Mail check: " + "; ".join(problems), error=True)
            else:
                self.show_status(f"Mail checked: {totals['new']} new message(s)" if totals["new"] else "Mail checked: nothing new")
            self._notify_new_mail(new_by_account)
            if not self._asking_blocked:  # a check during an open question (the timer) does not ask a second time
                self._asking_blocked = True
                try:
                    for account_id, item in blocked:
                        self._ask_about_blocked(account_id, item)
                finally:
                    self._asking_blocked = False
            if totals["new"]:
                self._reselect = self._shown[2] if self._shown is not None else None  # keep the message that is being read
                self.list_model.reload()
            self._load_folders(select_saved=False)
            self._settle_operations()

        def make_done(account_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                totals["new"] += int(getattr(result, "downloaded"))
                new_by_account[account_id] = int(getattr(result, "downloaded"))
                own = [str(text) for text in getattr(result, "failed")]
                blocked.extend((account_id, item) for item in getattr(result, "blocked", ()))
                if getattr(result, "aborted"):
                    own.append(str(getattr(result, "aborted")))
                problems.extend(own)
                self._finish_operation(account_id, "receive", "; ".join(own) if own else None)
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                problems.append(str(error))
                self._finish_operation(account_id, "receive", str(error))
                remaining.discard(account_id)
                if not remaining:
                    finish()

            return failed

        self.show_status("Checking for mail…")
        for account_id in list(remaining):
            self.backend.call("mail.fetch", {"account": account_id}, make_done(account_id), make_failed(account_id), owner=self, checking=True)

    # --- the Operations window --------------------------------------------------------------------------------------

    def show_operations(self) -> None:
        self.operations.show()
        self.operations.raise_()
        self.operations.activateWindow()

    def _operation_name_of(self, account_id: str) -> str:
        account = next((account for account in self._accounts if str(getattr(account, "account_id")) == account_id), None)
        return str(getattr(account, "shown_name", "") or getattr(account, "email_address", "")) if account is not None else account_id

    def _operation_name(self, account_id: str, direction: str) -> str:
        """The name of a row: the address and the protocol, as "rob@example.com (POP3)"."""
        account = next((account for account in self._accounts if str(getattr(account, "account_id")) == account_id), None)
        protocol = "SMTP" if direction == "send" else str(getattr(account, "kind", "pop3")).upper() if account is not None else ""
        return f"{self._operation_name_of(account_id)} ({protocol})" if protocol else self._operation_name_of(account_id)

    def _show_operations_quietly(self) -> None:
        """Open the Operations window without taking the keyboard from what the person is doing."""
        if self.operations.isVisible():
            return
        self.operations.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.operations.show()
        self.operations.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

    def _begin_operations(self, manual: bool) -> None:
        """A send and receive starts: the rows of the last one go and the window opens when that is wanted; only one the
        person started (manual: Refresh) brings it to the front with the keyboard focus."""
        if self._checking or self._sending:
            return
        self.operations.begin_round()
        if not bool_setting(self.settings, "operations_show_on_send_receive", True):
            return
        if manual:
            self.show_operations()
        else:
            self._show_operations_quietly()

    def _activity(self, account_id: str, direction: str, step: str, done: object, total: object) -> None:
        self.operations.step(account_id, direction, step, done if isinstance(done, int) else None, total if isinstance(total, int) else None)

    def _finish_operation(self, account_id: str, direction: str, error: str | None) -> None:
        self.operations.finish(account_id, direction, error)
        if error is not None and bool_setting(self.settings, "operations_show_on_error", True):
            self._show_operations_quietly()

    def _settle_operations(self) -> None:
        """Everything is done: the window closes itself when that is wanted, but stays after an error when errors show it."""
        if self._checking or self._sending or self.operations.running() or not self.operations.isVisible():
            return
        if self.operations.round_errors and bool_setting(self.settings, "operations_show_on_error", True):
            return
        if bool_setting(self.settings, "operations_hide_when_done", True):
            self.operations.hide()

    def _ask_about_blocked(self, account_id: str, item: object) -> None:
        """A safety rule held an IMAP folder back: the person decides, and the account is synchronised with that answer."""
        kind, folder, count = str(getattr(item, "kind")), str(getattr(item, "folder")), int(getattr(item, "count"))
        if kind not in BLOCKED_QUESTIONS:
            return
        account = next((account for account in self._accounts if str(getattr(account, "account_id")) == account_id), None)
        name = str(getattr(account, "shown_name", "") or getattr(account, "email_address", "")) if account is not None else account_id
        question, answers = BLOCKED_QUESTIONS[kind]
        chosen = ask_choice(self, "Messages out of step", question.format(count=count, folder=folder, account=name), [label for label, _, _ in answers])
        if chosen is None:
            return  # asked again at the next check
        _, choice, done_text = answers[chosen]

        def done(result: object) -> None:
            self._after_change(f"{folder} of {name}: {count} message(s) {done_text}")

        self.show_status(f"{folder} of {name}: synchronising…")
        self.backend.call("mail.settle", {"account": account_id, "folder": folder, "choice": choice}, done, lambda error: self._report(f"Could not settle {folder}", error), owner=self)

    def _sync_calendars(self) -> None:
        """Bring the calendars, task lists and address books of the accounts that have a server in step with it (in the background)."""
        if self._dav_syncing or not any(str(getattr(account, "dav_url", "")) for account in self._accounts):
            return
        self._dav_syncing = True

        def done(result: object) -> None:
            self._dav_syncing = False
            self._load_contact_addresses()  # contacts that came from a server get their icon in the message list at once
            rows: list[object] = list(result) if isinstance(result, tuple) else []
            problems = [str(text) for row in rows for text in getattr(row, "errors", ())]
            if sum(int(getattr(row, "pulled", 0)) + int(getattr(row, "deleted_here", 0)) + int(getattr(row, "created", 0)) for row in rows):
                for page in self.pages.values():
                    page.refresh()
            if problems:
                self.show_status("Calendars and contacts: " + "; ".join(problems), error=True)

        def failed(error: ChronoMailError) -> None:
            self._dav_syncing = False
            self.show_status(f"Calendars and contacts: {error}", error=True)

        self.backend.call("dav.sync", {}, done, failed, owner=self)

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

    # --- tags ----------------------------------------------------------------------------------------------------

    def _load_tags(self) -> None:
        """The tags with their colours, order and shortcuts: for the menus, the list, the reading pane and the folder list."""

        def loaded(result: object) -> None:
            self._tags = tuple(result) if isinstance(result, (tuple, list)) else ()
            self._tag_book.update(self._tags)
            self._make_tag_shortcuts()
            if self._accounts:
                self._populate_tree()
            self.list_view.viewport().update()

        self.backend.call("tag.list", {}, loaded, lambda error: self._report("Tags", error), owner=self)

    def _tags_of_message(self, message_id: str) -> tuple[str, ...]:
        """The tags of a message of the list (a message of the conversation bar that is not in the list: none shown)."""
        position = self.list_model.find(message_id)
        row = self.list_model.row_at(position) if position >= 0 else None
        return tuple(getattr(row, "tags", ())) if row is not None else ()

    def _make_tag_shortcuts(self) -> None:
        for action in self._tag_shortcuts:
            self.removeAction(action)
            action.deleteLater()
        self._tag_shortcuts = []
        for tag in self._tags:
            shortcut = str(getattr(tag, "shortcut", ""))
            if not shortcut or not offered_for_mail(tag):
                continue
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            name = str(getattr(tag, "name"))
            action.triggered.connect(lambda checked=False, chosen=name: self._toggle_tag_by_key(chosen))
            self.addAction(action)
            self._tag_shortcuts.append(action)

    def _toggle_tag_by_key(self, name: str) -> None:
        """A tag's shortcut: give the tag to the selected messages, or take it away when all of them have it."""
        rows = self._selected_rows()
        everyone = bool(rows) and all(any(have.casefold() == name.casefold() for have in tuple(getattr(row, "tags", ()))) for row in rows)
        self._toggle_tag(name, not everyone)

    def _fill_tag_menu(self, menu: QMenu) -> None:
        rows = self._selected_rows()
        fill_tag_menu(menu, self._tags, self._tag_book, [tuple(getattr(row, "tags", ())) for row in rows], self._toggle_tag, self._new_tag, self.open_tags)

    def _toggle_tag(self, name: str, add: bool) -> None:
        """Give a tag to the selected messages (add) or take it away; the rows show the new tags at once."""
        rows = self._selected_rows()
        groups: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            groups.setdefault((self._account_of(row), str(getattr(row, "folder"))), []).append(str(getattr(row, "message_id")))
        new = not any(str(getattr(tag, "name")).casefold() == name.casefold() for tag in self._tags)

        def done(result: object) -> None:
            for item in result if isinstance(result, (tuple, list)) else ():
                message_id, tags = str(getattr(item, "message")), tuple(getattr(item, "tags"))
                self.list_model.replace_tags(message_id, tags)
                if self._shown is not None and self._shown[2] == message_id:
                    self.reader.set_tags(self._tag_book.html(tags))
            if new and add:
                self._load_tags()
            elif self._combined == "tagged" or self._filter_tag is not None:
                self.list_model.reload()  # a message that loses the tag leaves this list

        for (account, folder), ids in groups.items():
            values: dict[str, object] = {"account": account, "folder": folder, "messages": ids, ("add" if add else "remove"): [name]}
            self.backend.call("message.tag", values, done, lambda error: self._report("Could not change the tags", error), owner=self)

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New tag", "Name of the new tag (the selected messages get it):")
        if ok and " ".join(name.split()):
            self._toggle_tag(" ".join(name.split()), True)

    def _fill_filter_tags(self) -> None:
        self.filter_tag_menu.clear()
        for tag in self._tags:
            if not offered_for_mail(tag):
                continue
            name = str(getattr(tag, "name"))
            chosen = self._filter_tag is not None and self._filter_tag.casefold() == name.casefold()
            action = self.filter_tag_menu.addAction(tag_icon(self._tag_book.colour(name), chosen), name)
            action.triggered.connect(lambda checked=False, picked=name, off=chosen: self._set_filter_tag(None if off else picked))
        if not self.filter_tag_menu.actions():
            self.filter_tag_menu.addAction("No tags yet").setEnabled(False)

    def _set_filter_tag(self, name: str | None) -> None:
        self._filter_tag = name
        self._show_filter_state()
        self._apply_view()

    def open_tags(self) -> None:
        """Menu > Tags: make, change, order and delete tags."""
        TagsDialog(self.backend, self._tags, self._after_tags_changed, self).exec()

    def _after_tags_changed(self) -> None:
        self._load_tags()
        self.list_model.reload()  # renamed or deleted tags change the rows

    def show_recovery(self) -> None:
        RecoveryDialog(self.backend, self).exec()

    def show_import(self) -> None:
        """File > Import: accounts, rules, contacts and mail; the folder list is loaded again after each import."""
        ImportDialog(self.backend, self._after_import, self, on_running=self._import_running).exec()

    def _import_running(self, running: bool) -> None:
        """An import starts or ends: no mail checks meanwhile, and Refresh is off."""
        self._importing = running
        self.act_refresh.setEnabled(not running and not self._checking)

    def _after_import(self) -> None:
        """New accounts, folders and mail: the folder list with its counts, then the list on screen (its selection did not change, so it would keep the old messages)."""
        def then() -> None:
            self.list_model.reload()
            self.check_mail()  # the checks that waited for the import: once, with the accounts it made

        self.reload_accounts(select_saved=True, then=then)

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
        if kind == "folder":
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
        self._fill_tag_menu(menu.addMenu(tag_icon(QColor("#808080")), "Tag"))
        menu.addSeparator()
        self._fill_move_menu()
        menu.addMenu(self.move_menu).setText("Move to")
        menu.addAction(self.act_archive)
        menu.addAction(self.act_snooze)
        if self._in_snoozed():
            menu.addAction(self.act_cancel_snooze)
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

