from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
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

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.delegate import MessageDelegate
from chrono_mail.ui.models import (
    ACCOUNT_ROLE,
    FOLDER_PATH_ROLE,
    FOLDER_ROLE_ROLE,
    KIND_ROLE,
    FolderTreeModel,
    MessageListModel,
    ViewSpec,
)
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.recoverydialog import RecoveryDialog

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial main window

AUTO_CHECK_MINUTES: int = 5
MARK_READ_DELAY_MS: int = 700
SORT_CHOICES: tuple[tuple[str, str], ...] = (("date", "Date"), ("subject", "Subject"), ("sender", "Sender"), ("size", "Size"))
_ICONS: dict[str, str] = {"inbox": "mail-folder-inbox", "sent": "mail-folder-outbox", "drafts": "document-edit", "trash": "user-trash", "junk": "mail-mark-junk", "archive": "folder-archive", "other": "folder"}


def theme_icon(name: str) -> QIcon:
    return QIcon.fromTheme(name)


def bool_setting(settings: QSettings, key: str, default: bool) -> bool:
    """QSettings may hand back the text 'true' or 'false' from an ini file."""
    value = settings.value(key, default)
    return value if isinstance(value, bool) else str(value).lower() == "true"


class MainWindow(QMainWindow):
    """The three-pane window: folders, the messages of a folder, and the message being read.

    It contains no mail logic. Everything it does is a command of the engine, run on a worker thread.
    """

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend: Backend = backend
        self.settings: QSettings = settings
        self.setWindowTitle("Chrono Mail")
        self.resize(1280, 800)
        self._accounts: list[object] = []
        self._folders: dict[str, list[object]] = {}
        self._account_id: str | None = None
        self._folder: str | None = None
        self._folder_role: str = "other"
        self._tree_kind: str = "folder"
        self._shown: tuple[str, str, str] | None = None  # account, folder, message id of what the reader shows
        self._show_request: int = 0
        self._updating_tree: bool = False
        self._sort: str = str(settings.value("sort", "date"))
        self._oldest_first: bool = bool_setting(settings, "oldest_first", False)
        self._reselect: str | None = None
        self._composers: list[ComposeWindow] = []
        self._checking: bool = False
        self._build_widgets()
        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_status()
        self._wire()
        self._restore_state()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_mail)
        if bool_setting(settings, "auto_check", True):
            self._timer.start(AUTO_CHECK_MINUTES * 60 * 1000)
        self._mark_read_timer = QTimer(self)
        self._mark_read_timer.setSingleShot(True)
        self._mark_read_timer.timeout.connect(self._mark_shown_as_read)

    # --- construction ---------------------------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.tree_model = FolderTreeModel(self)
        self.tree = QTreeView(self)
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setMinimumWidth(180)

        self.list_model = MessageListModel(self.backend, self)
        self.delegate = MessageDelegate()
        self.list_view = QListView(self)
        self.list_view.setModel(self.list_model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.setFrameShape(QListView.Shape.NoFrame)

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
        header_layout.addWidget(self.sort_button)
        header_layout.addStretch(1)
        header_layout.addWidget(self.filter_unread)
        header_layout.addWidget(self.filter_flagged)
        header_layout.addWidget(self.filter_attachments)
        header_layout.addWidget(self.count_label)
        list_layout.addWidget(header)
        list_layout.addWidget(self.list_view, 1)

        self.reader = ReaderWidget(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(list_pane)
        self.splitter.addWidget(self.reader)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([240, 400, 640])
        self.splitter.setChildrenCollapsible(False)
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
        self.act_refresh = self._action("Get mail", self.check_mail, "F5", "mail-receive")
        self.act_reply = self._action("Reply", lambda: self.compose_from_selection("reply"), "Ctrl+R", "mail-reply-sender")
        self.act_reply_all = self._action("Reply all", lambda: self.compose_from_selection("reply_all"), "Ctrl+Shift+R", "mail-reply-all")
        self.act_forward = self._action("Forward", lambda: self.compose_from_selection("forward"), "Ctrl+L", "mail-forward")
        self.act_delete = self._action("Delete", self.delete_selected, "Delete", "edit-delete")
        self.act_read = self._action("Mark as read", lambda: self.mark_selected(seen=True))
        self.act_unread = self._action("Mark as unread", lambda: self.mark_selected(seen=False))
        self.act_flag = self._action("Flag", lambda: self.mark_selected(flagged=True), icon="mail-mark-important")
        self.act_unflag = self._action("Remove flag", lambda: self.mark_selected(flagged=False))
        self.act_add_account = self._action("Add an account…", lambda: self.add_account(), icon="list-add")
        self.act_recovery = self._action("Back up the encryption key…", self.show_recovery)
        self.act_quit = self._action("Quit", self.close, "Ctrl+Q", "application-exit")
        self.act_search = self._action("Search", self._focus_search, "Ctrl+F")
        self.act_auto = QAction("Check for mail automatically", self)
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool_setting(self.settings, "auto_check", True))
        self.act_auto.toggled.connect(self._toggle_auto_check)
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
        self.search_box.setPlaceholderText("Search (from:  to:  subject:  body:)")
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
        file_menu.addAction(self.act_add_account)
        file_menu.addAction(self.act_recovery)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        message_menu = bar.addMenu("&Message")
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            message_menu.addAction(action)
        message_menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            message_menu.addAction(action)
        message_menu.addSeparator()
        message_menu.addAction(self.act_delete)
        folder_menu = bar.addMenu("F&older")
        for action in (self.act_new_folder, self.act_rename_folder, self.act_delete_folder):
            folder_menu.addAction(action)
        view_menu = bar.addMenu("&View")
        view_menu.addAction(self.act_search)
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
        self.list_view.selectionModel().currentChanged.connect(self._row_changed)
        self.list_view.customContextMenuRequested.connect(self._list_menu)
        self.list_model.counts_changed.connect(self._counts_changed)
        self.list_model.load_failed.connect(self._load_failed)
        self.list_model.loaded.connect(self._restore_row_selection)
        self.search_box.returnPressed.connect(self._apply_view)
        self.search_box.textChanged.connect(self._search_text_changed)
        self.scope.currentIndexChanged.connect(lambda: self._apply_view() if self.search_box.text() else None)
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
        sizes = self.settings.value("splitter")
        if isinstance(sizes, list):
            self.splitter.setSizes([int(size) for size in sizes])

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        for composer in list(self._composers):
            if not composer.close():
                event.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.sizes())
        self.settings.setValue("sort", self._sort)
        self.settings.setValue("oldest_first", self._oldest_first)
        self.settings.setValue("auto_check", self.act_auto.isChecked())
        self.settings.setValue("last_account", self._account_id or "")
        self.settings.setValue("last_folder", self._folder or "")
        self.backend.wait_for_done(3000)
        event.accept()

    def _toggle_auto_check(self, on: bool) -> None:
        self.settings.setValue("auto_check", on)
        if on:
            self._timer.start(AUTO_CHECK_MINUTES * 60 * 1000)
        else:
            self._timer.stop()

    # --- status ---------------------------------------------------------------------------------------

    def show_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #c0392b" if error else "")

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
        """Called once the window is shown: load the accounts (or ask for the first one), then check for mail."""
        self.reload_accounts(select_saved=True, then=self.check_mail if bool_setting(self.settings, "check_on_start", True) else None)

    def reload_accounts(self, select_saved: bool = False, then: Callable[[], None] | None = None) -> None:
        def listed(result: object) -> None:
            self._accounts = list(result) if isinstance(result, (tuple, list)) else []
            if not self._accounts:
                self._folders = {}
                self.tree_model.populate([], {})
                self.add_account(first=True)
                return
            self._load_folders(select_saved, then)

        self.backend.call("account.list", {}, listed, lambda error: self._report("Accounts", error), owner=self)

    def _load_folders(self, select_saved: bool, then: Callable[[], None] | None = None) -> None:
        remaining = {str(getattr(account, "account_id")) for account in self._accounts}
        collected: dict[str, list[object]] = {}

        def make_done(account_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                collected[account_id] = list(result) if isinstance(result, (tuple, list)) else []
                remaining.discard(account_id)
                if not remaining:
                    self._folders = collected
                    self._populate_tree(select_saved)
                    if then is not None:
                        then()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                collected[account_id] = []
                remaining.discard(account_id)
                self._report("Folders", error)
                if not remaining:
                    self._folders = collected
                    self._populate_tree(select_saved)

            return failed

        for account_id in list(remaining):
            self.backend.call("folder.list", {"account": account_id}, make_done(account_id), make_failed(account_id), owner=self)

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
            keep = (saved_account, "folder", saved_folder) if saved_account and saved_folder else None
        self._updating_tree = True
        self.tree_model.populate(self._accounts, self._folders)
        self.tree.expandAll()
        target = self._find_tree_item(keep) if keep is not None else None
        if target is None:
            target = self._first_inbox()
        if target is not None and target.isValid():
            self.tree.setCurrentIndex(target)
        self._updating_tree = False
        if target is not None and target.isValid() and self._key_of(target) != (self._account_id, self._tree_kind, self._folder or ""):
            self._tree_changed(target, QModelIndex())

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
            for child in range(self.tree_model.rowCount(account)):
                index = self.tree_model.index(child, 0, account)
                if index.data(FOLDER_ROLE_ROLE) == "inbox":
                    return index
        return None

    def _tree_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if self._updating_tree or not current.isValid():
            return
        kind = str(current.data(KIND_ROLE))
        if kind == "account":
            return
        self._account_id = str(current.data(ACCOUNT_ROLE))
        self._tree_kind = kind
        self._folder = str(current.data(FOLDER_PATH_ROLE)) if kind == "folder" else None
        self._folder_role = str(current.data(FOLDER_ROLE_ROLE) or "other")
        self._select_quick_filters_for(kind)
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.reader.clear()
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
            self._apply_view()

    def _focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()

    # --- the message list ------------------------------------------------------------------------------

    def view_spec(self) -> ViewSpec | None:
        if self._account_id is None:
            return None
        query = self.search_box.text().strip()
        unread = True if self.filter_unread.isChecked() else None
        flagged = True if self.filter_flagged.isChecked() else None
        attachments = True if self.filter_attachments.isChecked() else None
        folder = self._folder
        if query and self.scope.currentData() == "all":
            folder = None
        elif self._tree_kind in ("unread", "flagged"):
            folder = None
        return ViewSpec(self._account_id, folder, query, self._sort, self._oldest_first, unread, flagged, attachments)

    def _apply_view(self) -> None:
        self.delegate.show_recipients = self._folder_role in ("sent", "drafts") or self._folder == "Outbox"
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
        row = self.list_model.row_at(current.row()) if current.isValid() else None
        if row is None or self._account_id is None:
            if not self.list_view.selectionModel().hasSelection():
                self.reader.clear()
                self._shown = None
            return
        folder, message_id = str(getattr(row, "folder")), str(getattr(row, "message_id"))
        if self._shown == (self._account_id, folder, message_id):
            return
        self._show_request += 1
        request = self._show_request
        account = self._account_id

        def shown(result: object) -> None:
            if request != self._show_request:
                return
            self._shown = (account, folder, message_id)
            self.reader.show_message(result)
            if "seen" not in getattr(row, "flags"):
                self._mark_read_timer.start(MARK_READ_DELAY_MS)

        def failed(error: ChronoMailError) -> None:
            if request == self._show_request:  # an answer for a message that is no longer the one being opened is of no interest
                self._report("Could not open the message", error)

        self.backend.call("message.show", {"account": account, "folder": folder, "message": message_id}, shown, failed, owner=self)

    def _mark_shown_as_read(self) -> None:
        if self._shown is None:
            return
        account, folder, message_id = self._shown
        self._set_flags([(folder, message_id)], {"seen": True}, account)

    def _restore_row_selection(self) -> None:
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
        self._set_flags([(str(getattr(row, "folder")), str(getattr(row, "message_id"))) for row in self._selected_rows()], values)

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
            self.backend.call("message.delete", {"account": account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id"))}, done, lambda error: self._report("Could not delete", error), owner=self)

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
            self.backend.call("message.move", {"account": account, "folder": str(getattr(row, "folder")), "message": str(getattr(row, "message_id")), "target": target}, done, lambda error: self._report("Could not move", error), owner=self)

    def _after_change(self, text: str) -> None:
        """Something changed in the store: show the same folder again and update the counts."""
        self.show_status(text)
        self._show_request += 1  # whatever was being opened may no longer exist
        self.reader.clear()
        self._shown = None
        self.list_model.reload()
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        self._load_folders(select_saved=False)

    # --- composing ---------------------------------------------------------------------------------------------

    def new_message(self) -> None:
        if self._account_id is None:
            return
        self._open_composer(self._account_id, None, None)

    def _open_composer(self, account_id: str, draft: object | None, source: tuple[str, str] | None, to: str = "") -> ComposeWindow:
        window = ComposeWindow(self.backend, self._accounts, account_id, draft, source)
        if to:
            window.to.setText(to)
        window.sent.connect(self._after_sent)
        window.destroyed.connect(lambda: self._composers.remove(window) if window in self._composers else None)
        self._composers.append(window)
        window.show()
        return window

    def compose_from_selection(self, kind: str) -> None:
        row = self._current_row()
        if row is None or self._account_id is None:
            return
        account, folder, message_id = self._account_id, str(getattr(row, "folder")), str(getattr(row, "message_id"))

        def drafted(result: object) -> None:
            self._open_composer(account, result, (folder, message_id))

        self.backend.call("message.compose", {"account": account, "folder": folder, "message": message_id, "kind": kind}, drafted, lambda error: self._report("Could not start the message", error), owner=self)

    def _after_sent(self, account_id: str = "") -> None:
        self._load_folders(select_saved=False)
        if self._folder in ("Sent", "Outbox"):
            self.list_model.reload()

    # --- mail ---------------------------------------------------------------------------------------------------

    def check_mail(self) -> None:
        if self._checking or not self._accounts:
            return
        self._checking = True
        self.act_refresh.setEnabled(False)
        remaining = {str(getattr(account, "account_id")) for account in self._accounts}
        totals = {"new": 0}
        problems: list[str] = []

        def finish() -> None:
            self._checking = False
            self.act_refresh.setEnabled(True)
            if problems:
                self.show_status("Mail check: " + "; ".join(problems), error=True)
            else:
                self.show_status(f"Mail checked: {totals['new']} new message(s)" if totals["new"] else "Mail checked: nothing new")
            if totals["new"]:
                self._reselect = self._shown[2] if self._shown is not None else None  # keep the message that is being read
                self.list_model.reload()
            self._load_folders(select_saved=False)

        def make_done(account_id: str) -> Callable[[object], None]:
            def done(result: object) -> None:
                totals["new"] += int(getattr(result, "downloaded"))
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

    def add_account(self, first: bool = False) -> None:
        dialog = AccountDialog(self.backend, first_account=first, parent=self)
        if dialog.exec() != AccountDialog.DialogCode.Accepted:
            return
        key_created = dialog.key_created

        def loaded() -> None:
            if key_created:
                RecoveryDialog(self.backend, self).exec()
            self.check_mail()

        self.reload_accounts(select_saved=False, then=loaded)

    def show_recovery(self) -> None:
        RecoveryDialog(self.backend, self).exec()

    # --- folders ----------------------------------------------------------------------------------------------------

    def _tree_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction(self.act_new_folder)
        if self._tree_kind == "folder":
            menu.addAction(self.act_rename_folder)
            menu.addAction(self.act_delete_folder)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        for action in (self.act_reply, self.act_reply_all, self.act_forward):
            menu.addAction(action)
        menu.addSeparator()
        for action in (self.act_read, self.act_unread, self.act_flag, self.act_unflag):
            menu.addAction(action)
        menu.addSeparator()
        self._fill_move_menu()
        menu.addMenu(self.move_menu).setText("Move to")
        menu.addAction(self.act_delete)
        menu.exec(self.list_view.viewport().mapToGlobal(position))

    def new_folder(self) -> None:
        if self._account_id is None:
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

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() == "mailto":
            if self._account_id is not None:
                self._open_composer(self._account_id, None, None, to=url.path())
            return
        if url.scheme() not in ("http", "https"):
            return
        answer = QMessageBox.question(self, "Open link", f"Open this link in your web browser?\n\n{url.toString()}\n\nLinks in e-mail can lead to harmful sites. Only open it if you trust the sender.")
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

    def _about(self) -> None:
        QMessageBox.about(self, "About Chrono Mail", "<b>Chrono Mail</b><br>An open-source e-mail client for Linux, inspired by eM Client.<br><br>License: AGPL-3.0")

