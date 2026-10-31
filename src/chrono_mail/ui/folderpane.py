import re
from collections.abc import Callable
from dataclasses import replace as changed

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractItemView, QInputDialog, QMenu, QMessageBox, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_folders import FOLDER_CREATE, FOLDER_DELETE, FOLDER_RENAME, FolderDeleteParameters, FolderParameters, FolderRenameParameters
from chrono_mail.engine.commands_sidebar import (
    FAVORITE_PIN, FAVORITE_RENAME, FAVORITE_REORDER, FAVORITE_SHOW, FAVORITE_UNPIN, GROUP_ADD, GROUP_ASSIGN, GROUP_REMOVE, GROUP_RENAME, AssignParameters, GroupAddParameters,
    GroupParameters, GroupRenameParameters, PinnedParameters, PinParameters, RenamePinParameters, ReorderParameters, VisibleParameters,
)
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_accounts import AccountParameters, NoParameters
from chrono_mail.engine.commands_folders import FOLDER_LIST, FolderInfo
from chrono_mail.engine.commands_sidebar import EMPTY_SIDEBAR, SIDEBAR_SHOW, SidebarInfo
from chrono_mail.ui import forms
from chrono_mail.ui.backend import AnyStep, Backend
from chrono_mail.ui.burst import Burst
from chrono_mail.ui.currentview import EntryKind
from chrono_mail.ui.deletekey import DeleteKey
from chrono_mail.ui.foldertree import FolderTree
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.mailtags import MailTags
from chrono_mail.ui.messagelistpane import MessageListPane
from chrono_mail.ui.models import (
    ACCOUNT_ROLE, FOLDER_PATH_ROLE, FOLDER_ROLE_ROLE, GROUP_ROLE, KIND_ROLE, NAME_ROLE, PIN_ROLE, UNREAD_ROLE, FolderNode,
    FolderTreeModel, TreeEntry,
)
from chrono_mail.ui.prefs import InUse, Remembered
from chrono_mail.ui.scale import px
from chrono_mail.ui.sections import CALENDAR, CONTACTS, TASKS
from chrono_mail.ui.sidebarpage import startup_forces
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.tagging import offered_for_mail
from chrono_mail.ui.textbox import TextBox

# --- Version ------------------------------------------------------------
__version__ = "folderpane.py 4.2.454"

# --- Version history ----------------------------------------------------
# v4.2.454: Rename folder opens 360 pixels wide on the owner's screen (TextBox, the owner)
# v4.2.453: Rename folder and Delete folder… say what the rules that move mail to the folder did (D-302)
# v4.2.450: a load started while one runs takes it over: the older one's answers are ignored, what was to follow it runs when the newer
#           one is done, in the order asked, and the saved folder is opened when either asked for it
# v4.2.449: a mailbox that told a fact while the folder lists loaded is counted again once they are in place: the lists may have been
#           read before it (a new account's first fill makes its folders then, D-300)
# v4.2.399: a top row (an account, Local Folders) offers no Rename folder or Delete folder; one place says whether the row
#           chosen is a folder
# v4.2.398: New folder and Rename folder ask only for the folder's name (New: inside the folder chosen, else at the top of
#           its account; Rename: the folder stays where it is); a name with / is refused
# v4.2.397: the Tags heading starts closed until the person opens it
# v4.1.379: Next and Previous unread go on from the folder shown (also opened through its pin or its account's row), never to that folder,
#           through subfolders and in an account inside a group too (CR-15)
# v3.6.354: Delete in the folder list: never a standard folder, known by its role, also Snoozed and Recovered (CR-36)
# v2.4.334: the folder numbers of the facts of one moment are shown once (they laid the list out once per message)
# v2.0.300: the Favorites menu lists only the favorites that can appear (Snoozed, Scheduled, Archive once a mailbox has them, D-234)
# v2.0.299: a right-click in Favorites lists every standard favorite with a tick; a click shows or hides it (D-234); Show again and
#           Hide from Favorites gone
# v2.0.280: folder_moved: a folder renamed or deleted, so its column setup follows (B-16)
# v1.9.274: R-19 round 4: the accounts, folders and facts are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: every engine call typed; sidebar_call takes a typed command, change_favorites typed steps
# v1.9.264: R-17: the folder list of the main window as a part of its own (out of ui/mainwindow.py): the folders of every place where mail
#           is kept with the engine's numbers, Favorites, pins, groups and Tags, laid out once and then in place (R-11), what the person
#           opened or closed, the facts of folders made, renamed or deleted, its menu, New, Rename and Delete folder, Show on startup

SIDEBAR_KEY: str = "sidebar"  # stands for the sidebar answer among the folder lists that are awaited
MAXIMUM_TREE_STATE: int = 2000  # opened or closed nodes that are remembered
RENAME_WIDTH: int = forms.owner_width(360)  # the owner: Rename folder opens 360 pixels wide on his screen


def with_rules(text: str, rules: tuple[str, ...]) -> str:
    """The line of a folder renamed or deleted, with what the rules that move mail to it did (D-302)."""
    return ". ".join((text, *rules))


class FolderMenu:
    """The window's actions the folder list's menu offers (they are the window's: their keys and the Folder menu share them)."""

    def __init__(self, new_folder: QAction, rename_folder: QAction, delete_folder: QAction, mark_all_read: QAction, empty_folder: QAction) -> None:
        self.new_folder: QAction = new_folder
        self.rename_folder: QAction = rename_folder
        self.delete_folder: QAction = delete_folder
        self.mark_all_read: QAction = mark_all_read
        self.empty_folder: QAction = empty_folder


class FolderPane(QObject):
    """The folder list: the owner of every mailbox's folders and numbers, the sidebar (pins and groups) and what the person opened."""

    entry_chosen = Signal(object)  # another entry was selected: the CurrentView it opens (kind NONE: an account without an Inbox)
    numbers_changed = Signal()  # the numbers or the entries changed: the window title and the tray follow
    folder_moved = Signal(str, str, str)  # a folder was renamed (account, old path, new path) or deleted (new path ""): its column setup follows

    def __init__(
        self, backend: Backend, state: MailState, status: StatusReport, prefs_in_use: InUse, memory: Remembered, tags: MailTags, messages: MessageListPane,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._prefs: InUse = prefs_in_use
        self._memory: Remembered = memory
        self._tags: MailTags = tags
        self._messages: MessageListPane = messages
        self._parent: QWidget = parent
        self._numbers: Burst = Burst(self._show_numbers, self)  # the numbers of a large delete go on the list ten times a second, not per message
        self._round: Callable[[], None] | None = None  # while the folder lists load: what a tags answer calls instead of a lay-out
        self._told_while_loading: set[str] = set()  # mailboxes that told a fact while the folder lists loaded: counted again after
        self._loading: object | None = None  # the load under way: answers of an older one it took over are ignored
        self._follow_ups: list[Callable[[], None]] = []  # what was to follow the loads under way, in the order asked
        self._select_saved: bool = False  # one of the loads under way opens the saved folder
        self.recounting: set[str] = set()  # mailboxes whose folders are being counted again after a change from outside
        self.recount_again: set[str] = set()
        self.tree_state: dict[str, bool] = memory.tree_state  # node -> opened (True) or closed (False) by the person
        self.menu: FolderMenu | None = None
        self.model: FolderTreeModel = FolderTreeModel(parent)
        self.tree: FolderTree = FolderTree(parent)
        self.tree.setModel(self.model)
        self.model.leaving.connect(self._entries_leaving)  # the selected entry going: its account's Inbox first (before Qt picks a neighbour)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setMinimumWidth(px(180))
        self.tree.setObjectName("folderTree")
        self.tree.selectionModel().currentChanged.connect(self._current_changed)
        self.tree.clicked.connect(self._clicked)
        self.tree.expanded.connect(self._opened)
        self.tree.collapsed.connect(self._closed)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self._delete_key: DeleteKey = DeleteKey(self.tree, self._delete_key_pressed)  # Delete in the folder list is the folder's, never the messages'
        tags.arrived.connect(self._tags_arrived)
        messages.unread_elsewhere = self.unread_in_another_folder

    def use_menu(self, menu: FolderMenu) -> None:
        self.menu = menu

    # --- loading ------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        """No account at all. A load under way ends here (its answers are ignored); what was to follow it waits for the next load."""
        self._round, self._loading, self._select_saved = None, None, False
        self._state.folders = {}
        self.model.populate([], {})

    def load(self, select_saved: bool, then: Callable[[], None] | None = None) -> None:
        """The folders of every place where mail is kept, the sidebar file and the tags; once they are all in, the folder list
        is laid out once (at start, select_saved, the folder to open is selected then). Each place is asked once: an account
        that keeps its mail in Local Folders has no folders of its own."""
        remaining = set(self._state.accounts_with_mail()) | {SIDEBAR_KEY}
        collected: dict[str, list[FolderInfo]] = {}
        this = object()  # a load started while another runs takes it over: that one's answers are ignored from now on
        self._loading = this
        self._select_saved = self._select_saved or select_saved
        if then is not None:
            self._follow_ups.append(then)

        def current() -> bool:
            return self._loading is this

        def arrived() -> None:
            if current() and not remaining and self._tags.ready:
                finish_loading()

        def sidebar_done(result: SidebarInfo) -> None:
            if not current():
                return
            self._state.sidebar = result
            remaining.discard(SIDEBAR_KEY)
            arrived()

        def sidebar_failed(error: ChronoMailError) -> None:
            if not current():
                return
            self._state.sidebar = EMPTY_SIDEBAR
            remaining.discard(SIDEBAR_KEY)
            self._status.report("Favorites and groups", error)
            arrived()

        def finish_loading() -> None:
            self._round, self._loading = None, None
            select, self._select_saved = self._select_saved, False
            follow_ups, self._follow_ups = self._follow_ups, list[Callable[[], None]]()
            self._state.folders = collected
            self.lay_out(select)
            self.numbers_changed.emit()
            told, self._told_while_loading = self._told_while_loading, set()
            for mailbox in sorted(told):
                self.recount(mailbox)  # its list may have been read before that fact: asked again, only what differs changes
            for follow_up in follow_ups:  # of this load and of those it took over, in the order asked
                follow_up()

        def make_done(account_id: str) -> Callable[[tuple[FolderInfo, ...]], None]:
            def done(result: tuple[FolderInfo, ...]) -> None:
                if not current():
                    return
                collected[account_id] = [info for info in result if isinstance(info, FolderInfo)]
                remaining.discard(account_id)
                arrived()

            return done

        def make_failed(account_id: str) -> Callable[[ChronoMailError], None]:
            def failed(error: ChronoMailError) -> None:
                if not current():
                    return
                collected[account_id] = []
                remaining.discard(account_id)
                self._status.report("Folders", error)
                arrived()

            return failed

        self._round = arrived  # a tags answer meanwhile only says it is there: the lay-out above takes the tags in

        self._backend.ask(SIDEBAR_SHOW, NoParameters(), sidebar_done, sidebar_failed, owner=self)
        for account_id in list(remaining - {SIDEBAR_KEY}):
            self._backend.ask(FOLDER_LIST, AccountParameters(account_id), make_done(account_id), make_failed(account_id), owner=self)

    def _tags_arrived(self) -> None:
        """While the folder lists load, the tags wait for the lay-out of that round (never one of their own: it would come before
        the folders and take the place of the folder to open); afterwards only the Tags entries change."""
        if self._round is not None:
            self._round()
        elif self._state.accounts:
            self.lay_out()

    # --- laying out -----------------------------------------------------------------------------------------------

    def lay_out(self, select_saved: bool = False) -> None:
        """Bring the folder list in line with what is known (accounts, folders, sidebar, tags, settings): only the entries that differ
        change, in place, so the selection, the open branches and the scroll stay by themselves (R-11). New entries open or close
        as the person left them. Only when nothing is selected yet is something chosen: at start the folder to open, else the
        last selected folder, the first Inbox, or the first entry the list shows."""
        model, sidebar = self.model, self._state.sidebar
        model.pins = [(pin.pin_id, pin.account, pin.folder, pin.name) for pin in sidebar.pins]
        model.groups = [(group.group_id, group.name, group.accounts) for group in sidebar.groups]
        model.hidden = set(sidebar.hidden)
        model.hidden_trees = set(sidebar.hidden_trees)
        model.hidden_local = set(sidebar.hidden_local_folders)
        chosen = self._prefs.read()
        forced = startup_forces(chosen.startup)
        model.forced_folders = set(forced.folders)
        model.forced, model.forced_trees = set(forced.views), set(forced.trees)
        model.force_favorites, model.force_tags = forced.favorites, forced.tags
        model.show_favorites = chosen.show_favorites
        model.show_local_folders = chosen.show_local_folders
        model.show_tags = chosen.show_tags
        model.tags = [(tag.name, self._tags.book.colour(tag.name).name()) for tag in self._state.tags if offered_for_mail(tag)]
        new = model.populate(self._state.accounts, self._state.folders)
        target: QModelIndex | None = None
        if not self.tree.currentIndex().isValid():
            wanted: list[tuple[str, str, str]] = []
            if select_saved:
                startup = chosen.startup
                if startup.kind == "mail":
                    wanted.append((startup.account, startup.entry_kind, startup.path))  # the folder chosen in Settings; when it is gone, the last selected one
                saved_account, saved_folder, saved_kind = self._memory.last_account, self._memory.last_folder, self._memory.last_kind
                if saved_folder and (saved_account or saved_kind in (EntryKind.FAVORITE, EntryKind.GROUPINBOX)):
                    wanted.append((saved_account, saved_kind, saved_folder))
            target = next((found for found in (self.find_item(key) for key in wanted) if found is not None), None)
            if target is None:
                target = self._first_inbox()
            if target is None:
                target = self._first_visible()  # no Inbox shown (every tree hidden): the first entry the folder list shows
        if target is not None and target.isValid():
            self._select_entry(target)  # its view opens (the list follows the current entry)
        self._open_as_left(new, target)

    def _select_entry(self, index: QModelIndex) -> None:
        """Make an entry of the folder list the current one, as a click does, without scrolling to it: scrolling opens every entry
        above it, also one the person closed (which would then be remembered as opened)."""
        scrolls = self.tree.hasAutoScroll()
        self.tree.setAutoScroll(False)  # Qt scrolls to a new current entry by itself while this is on
        try:
            self.tree.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self.tree.setAutoScroll(scrolls)

    def startup_choices(self) -> list[tuple[str, str, bool, int, bool]]:
        """The choices of Settings > General > Show on startup: (text, value, can be chosen, depth, shown). The last selected
        folder, the Favorites, Local Folders, Calendar, Contacts, Tasks, then the other accounts and groups, as the folder list
        has them with nothing switched off in Settings > Sidebar. What the folder list does not show is greyed and cannot be
        chosen (a heading still unfolds)."""
        choices: list[tuple[str, str, bool, int, bool]] = [("Last selected folder", "", True, 0, True)]
        everything = self.model.plan(self._state.accounts, self._state.folders, everything=True)  # what the list holds with nothing hidden (no second list)
        shown: set[str] = set()

        def collect(parent: QModelIndex) -> None:
            for row in range(self.model.rowCount(parent)):
                index = self.model.index(row, 0, parent)
                shown.add(self._node_key(index))
                collect(index)

        collect(QModelIndex())

        def data(item: TreeEntry, role: int) -> str:
            return str(item.data(role) or "")

        def node_key(item: TreeEntry) -> str:  # as _node_key
            kind = data(item, KIND_ROLE)
            detail = data(item, Qt.ItemDataRole.DisplayRole) if kind == EntryKind.SECTION else data(item, GROUP_ROLE) if kind == EntryKind.GROUP else data(item, FOLDER_PATH_ROLE)
            return "|".join((kind, data(item, ACCOUNT_ROLE), detail))

        def entry(item: TreeEntry, depth: int) -> None:
            visible = node_key(item) in shown
            name = data(item, NAME_ROLE) or data(item, Qt.ItemDataRole.DisplayRole)
            if data(item, KIND_ROLE) in (EntryKind.ACCOUNT, EntryKind.GROUP, EntryKind.SECTION):
                choices.append((name, "-", False, depth, visible))  # a heading
            else:
                choices.append((name, "mail\t" + "\t".join((data(item, ACCOUNT_ROLE), data(item, KIND_ROLE), data(item, FOLDER_PATH_ROLE))), visible, depth, visible))

        def walk(item: TreeEntry, depth: int) -> None:
            for child in item.children:
                entry(child, depth)
                walk(child, depth + 1)

        local_ids = {account.account_id for account in self._state.accounts if account.kind == "local"}
        for item in everything:
            if data(item, KIND_ROLE) == EntryKind.SECTION:  # Favorites, Tags: a heading, its entries below it
                entry(item, 0)
                walk(item, 1)
        local = [item for item in everything if data(item, KIND_ROLE) == EntryKind.ACCOUNT and data(item, ACCOUNT_ROLE) in local_ids]
        for item in local:
            entry(item, 0)
            walk(item, 1)
        choices.extend((label, f"section\t{section}", True, 0, True) for label, section in (("Calendar", CALENDAR), ("Contacts", CONTACTS), ("Tasks", TASKS)))
        for item in everything:
            if data(item, KIND_ROLE) in (EntryKind.ACCOUNT, EntryKind.GROUP) and item not in local:
                entry(item, 0)
                walk(item, 1)
        return choices

    # --- opened and closed -------------------------------------------------------------------------------------------

    @staticmethod
    def _node_key(index: QModelIndex) -> str:
        kind = str(index.data(KIND_ROLE))
        detail = str(index.data(Qt.ItemDataRole.DisplayRole)) if kind == EntryKind.SECTION else str(index.data(GROUP_ROLE)) if kind == EntryKind.GROUP else str(index.data(FOLDER_PATH_ROLE) or "")
        return "|".join((kind, str(index.data(ACCOUNT_ROLE) or ""), detail))

    @staticmethod
    def _key_of(index: QModelIndex) -> tuple[str, str, str]:
        return (str(index.data(ACCOUNT_ROLE)), str(index.data(KIND_ROLE)), str(index.data(FOLDER_PATH_ROLE) or ""))

    def _opened(self, index: QModelIndex) -> None:
        self._toggled(index, True)

    def _closed(self, index: QModelIndex) -> None:
        self._toggled(index, False)

    def _toggled(self, index: QModelIndex, opened: bool) -> None:
        """A node was opened or closed: remembered, also for the next start, when it now differs from what the list remembers or
        does by itself (opening an entry as the person left it changes nothing, so nothing needs to tell it apart)."""
        key = self._node_key(index)
        if self.tree_state.get(key, self._opened_by_itself(index)) == opened:
            return
        self.tree_state[key] = opened
        if len(self.tree_state) > MAXIMUM_TREE_STATE:
            self.tree_state.pop(next(iter(self.tree_state)))
        self._memory.tree_state = self.tree_state

    def _opened_by_itself(self, index: QModelIndex, holding: frozenset[str] = frozenset()) -> bool:
        """How the list shows a node the person never opened or closed: headings open, accounts open (with more than three
        accounts only the one holding the selection), folders closed; the Tags heading closed."""
        kind = str(index.data(KIND_ROLE))
        accounts = sum(1 for row in range(self.model.rowCount()) if self.model.index(row, 0).data(KIND_ROLE) == EntryKind.ACCOUNT)
        return (kind == EntryKind.SECTION and index.data(Qt.ItemDataRole.DisplayRole) != "Tags") or (kind == EntryKind.ACCOUNT and accounts <= 3) or self._node_key(index) in holding

    def _open_as_left(self, new: list[FolderNode], selected: QModelIndex | None) -> None:
        """New entries of the folder list open and close as the person left them (or as the list does by itself); entries that
        were there already keep how they are. The selection is never hidden."""
        holding: set[str] = set()
        node = selected if selected is not None and selected.isValid() else self.tree.currentIndex()
        while node.isValid():
            holding.add(self._node_key(node))
            node = node.parent()
        for entry in new:
            index = entry.index()
            if index.isValid() and self.model.hasChildren(index):
                key = self._node_key(index)
                self.tree.setExpanded(index, self.tree_state[key] if key in self.tree_state else self._opened_by_itself(index, frozenset(holding)))

    # --- finding entries -------------------------------------------------------------------------------------------------

    def find_item(self, key: tuple[str, str, str]) -> QModelIndex | None:
        def walk(parent: QModelIndex) -> QModelIndex | None:
            for row in range(self.model.rowCount(parent)):
                index = self.model.index(row, 0, parent)
                if (str(index.data(ACCOUNT_ROLE)), str(index.data(KIND_ROLE)), str(index.data(FOLDER_PATH_ROLE) or "")) == key:
                    return index
                found = walk(index)
                if found is not None:
                    return found
            return None

        return walk(QModelIndex())

    def _inbox_under(self, row: QModelIndex) -> QModelIndex | None:
        """The Inbox of an account row, or the All Inboxes of a group row; None when there is none."""
        wanted = EntryKind.GROUPINBOX if str(row.data(KIND_ROLE)) == EntryKind.GROUP else EntryKind.FOLDER
        children = [self.model.index(child, 0, row) for child in range(self.model.rowCount(row))]
        return next((index for index in children if index.data(KIND_ROLE) == wanted and index.data(FOLDER_ROLE_ROLE) == "inbox"), None)

    def _first_visible(self) -> QModelIndex | None:
        """The first entry of the folder list that can be selected (a heading cannot)."""
        pending = [self.model.index(row, 0, QModelIndex()) for row in range(self.model.rowCount(QModelIndex()))]
        while pending:
            index = pending.pop(0)
            item = self.model.itemFromIndex(index)
            if item is not None and item.isSelectable():
                return index
            pending[0:0] = [self.model.index(row, 0, index) for row in range(self.model.rowCount(index))]
        return None

    def _first_inbox(self) -> QModelIndex | None:
        """The Inbox of the first account, also when the accounts are in groups."""
        top = [self.model.index(row, 0, QModelIndex()) for row in range(self.model.rowCount(QModelIndex()))]
        accounts: list[QModelIndex] = []
        for index in top:
            if index.data(KIND_ROLE) == EntryKind.ACCOUNT:
                accounts.append(index)
            elif index.data(KIND_ROLE) == EntryKind.GROUP:
                accounts.extend(child for child in (self.model.index(row, 0, index) for row in range(self.model.rowCount(index))) if child.data(KIND_ROLE) == EntryKind.ACCOUNT)
        for account in accounts:
            for child in range(self.model.rowCount(account)):
                index = self.model.index(child, 0, account)
                if index.data(FOLDER_ROLE_ROLE) == "inbox":
                    return index
        return None

    def title(self) -> str:
        """The window title in the mail section: the folder that is open and its unread messages, then the program."""
        current = self.tree.currentIndex()
        if current.isValid() and str(current.data(KIND_ROLE)) in (EntryKind.ACCOUNT, EntryKind.GROUP):
            current = self._inbox_under(current) or current  # the row shows its Inbox
        name = str(current.data(NAME_ROLE) or "") if current.isValid() else ""
        if not name:
            return "Chrono Mail"
        unread = int(current.data(UNREAD_ROLE) or 0)
        return f"{name} ({unread}) - Chrono Mail" if unread else f"{name} - Chrono Mail"

    def pin_labels(self) -> list[tuple[str, str]]:
        """The pinned folders for Settings > Favorites: (pin id, "name - account")."""
        labels = {account.account_id: account.label for account in self._state.accounts}
        return [(pin_id, f"{name or folder.rsplit('/', 1)[-1]} - {labels.get(account, account)}") for pin_id, account, folder, name in self.model.pins]

    # --- choosing an entry ----------------------------------------------------------------------------------------------------

    def _clicked(self, index: QModelIndex) -> None:
        """A click on a heading folds it open or shut (a heading is never selected)."""
        if str(index.data(KIND_ROLE)) == EntryKind.SECTION:
            self.tree.setExpanded(index, not self.tree.isExpanded(index))

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        view = self._state.view
        if not current.isValid() or self._key_of(current) == (view.account, view.key_kind, view.path):
            return  # the entry of the view that is shown (the highlight going back from a heading): nothing to open again
        kind = EntryKind(str(current.data(KIND_ROLE)))
        if kind is EntryKind.SECTION:  # a heading is never what the list shows: the highlight goes back to the folder that is shown
            if previous.isValid() and str(previous.data(KIND_ROLE)) != EntryKind.SECTION:
                self.tree.setCurrentIndex(previous)
            return
        if kind in (EntryKind.ACCOUNT, EntryKind.GROUP):  # the row stays selected and shows its Inbox: never a folder of another account
            inbox = self._inbox_under(current)
            if inbox is None:  # an account without an Inbox: an empty list that says so
                self.entry_chosen.emit(changed(view, account=None, folder=None, combined=None, kind=EntryKind.NONE))
                return
            current = inbox
            kind = EntryKind(str(current.data(KIND_ROLE)))
        path = str(current.data(FOLDER_PATH_ROLE) or "")
        shown_kind = EntryKind.FOLDER if kind is EntryKind.PINLINK else kind
        role = str(current.data(FOLDER_ROLE_ROLE) or "other")
        combined = "inboxes" if kind is EntryKind.GROUPINBOX else "tagged" if kind is EntryKind.TAGVIEW else path if kind is EntryKind.FAVORITE else None
        # another entry: its own view, with the quick filters it stands for; the search ends, where it looks and Tagged with stay
        self.entry_chosen.emit(changed(
            view, account=str(current.data(ACCOUNT_ROLE)), kind=shown_kind, key_kind=kind, path=path,  # account "" in a favorite: the rows say their account
            folder=path if shown_kind is EntryKind.FOLDER else None, folder_role=role, combined=combined,
            combined_accounts=self._state.group_accounts(str(current.data(GROUP_ROLE))) if kind is EntryKind.GROUPINBOX else (),
            combined_tag=path if kind is EntryKind.TAGVIEW else "", query="", advanced=None,
            unread=kind is EntryKind.UNREAD, flagged=kind is EntryKind.FLAGGED, attachments=False,
        ))

    def go_to_folder(self) -> None:
        """G: type a part of a folder's name and go to that folder."""
        names: dict[str, tuple[str, str]] = {}
        accounts = {account.account_id: account for account in self._state.accounts}
        for account_id, infos in self._state.folders.items():
            account = accounts.get(account_id)
            if account is None or account.storage == "local":
                continue
            label = "Local Folders" if account.kind == "local" else account.shown_name
            for info in infos:
                names[f"{label} / {info.path}"] = (account_id, info.path)
        if not names:
            return
        chosen, ok = QInputDialog.getItem(self._parent, "Go to folder", "Folder:", sorted(names), 0, True)
        if ok and chosen in names:
            index = self.find_item((names[chosen][0], "folder", names[chosen][1]))
            if index is not None:
                self.tree.setCurrentIndex(index)
                self._messages.view.setFocus()

    def unread_in_another_folder(self, step: int) -> bool:
        """The next (or previous) folder of the account shown that has unread mail, in the order of the folder list (subfolders too,
        also of an account in a group), from the folder shown (also opened through its pin or its account's row), never that folder
        itself: selected, its first (or last) unread message once it is in."""
        view = self._state.view
        account = view.account or ""
        folders: list[QModelIndex] = []

        def walk(parent: QModelIndex) -> None:
            for row in range(self.model.rowCount(parent)):
                index = self.model.index(row, 0, parent)
                if str(index.data(KIND_ROLE)) == EntryKind.FOLDER and str(index.data(ACCOUNT_ROLE)) == account:
                    folders.append(index)
                walk(index)

        if account:
            walk(QModelIndex())
        keys = [self._key_of(index) for index in folders]
        shown = (account, str(EntryKind.FOLDER), view.path) if view.key_kind in (EntryKind.FOLDER, EntryKind.PINLINK) else None
        current = self.tree.currentIndex()
        selected = shown if shown in keys else self._key_of(current) if current.isValid() else None
        here = keys.index(selected) if selected is not None and selected in keys else (-1 if step > 0 else len(folders))
        order = range(here + step, len(folders), step) if step > 0 else range(here + step, -1, step)
        for position in order:
            if keys[position] != shown and int(folders[position].data(UNREAD_ROLE) or 0) > 0:  # never the folder shown: it would not open again
                self._messages.select_unread_after_next_load(step)
                self.tree.setCurrentIndex(folders[position])
                return True
        return False

    # --- the facts of the engine (D-146) --------------------------------------------------------------------------------------

    def fact(self, change: Change) -> None:
        """A fact of the engine: the numbers per folder, a folder made, renamed or deleted, or a change from outside."""
        kind, mailbox, folder = str(change.kind), change.mailbox, change.folder
        if self._round is not None and mailbox in self._state.accounts_with_mail():
            self._told_while_loading.add(mailbox)  # the list being loaded may be older than this fact
        if kind == "mailbox_changed":
            self.recount(mailbox)  # a big job changed a mailbox: only its folders are counted again, once, one at a time
            return
        if kind in ("folder_made", "folder_renamed", "folder_deleted"):
            self._structure(kind, mailbox, folder, change.to_folder, (change.role or "other"))
            return
        infos = self._state.folders.get(mailbox)
        numbers = change.numbers if isinstance(change, Change) else ()
        if infos is None or not numbers:
            return
        for found in numbers:  # the engine's numbers, read with the change: put in place, never worked out here (R-10)
            for number, info in enumerate(infos):
                if info.path == found.folder:
                    infos[number] = FolderInfo(info.path, info.role, found.messages, found.unread)
        self._numbers.ask()

    def _show_numbers(self) -> None:
        self.model.recount(self._state.folders)
        self.numbers_changed.emit()

    def _structure(self, kind: str, mailbox: str, folder: str, to_folder: str, role: str = "other") -> None:
        """A folder was made, renamed or deleted: the folder list follows in place (that entry with what is below it, its pins in
        Favorites, Scheduled, Snoozed and Archive in Favorites once used). A renamed folder keeps its entry: it takes its new name first, so the
        selection and the open branches stay with it. When the folder on screen went, its account's Inbox is selected before it
        goes (_entries_leaving)."""
        infos = self._state.folders.setdefault(mailbox, [])

        def below(path: str, top: str) -> bool:
            return path == top or path.startswith(top + "/")

        if kind == "folder_made":
            if not any(info.path == folder for info in infos):
                infos.append(FolderInfo(folder, role, 0, 0))
        elif kind == "folder_renamed":
            infos[:] = [FolderInfo(to_folder + info.path[len(folder):], info.role, info.messages, info.unread) if below(info.path, folder) else info for info in infos]
            self.model.rename_folder(mailbox, folder, to_folder)
            view = self._state.view
            if view.account == mailbox and view.folder is not None and below(view.folder, folder):  # the list on screen follows the new name (it renamed its rows itself)
                self._state.view = changed(view, folder=to_folder + view.folder[len(folder):], path=to_folder + view.path[len(folder):] if below(view.path, folder) else view.path)
            self.folder_moved.emit(mailbox, folder, to_folder)
            if any(account == mailbox and below(pinned, folder) for _, account, pinned, _ in self.model.pins):
                # the engine moved its pins along (R-41): the list is laid out with them, so a pin keeps its entry and its selection
                self._backend.ask(SIDEBAR_SHOW, NoParameters(), self.sidebar_arrived, lambda error: self._status.report("Favorites and groups", error), owner=self)
                self.numbers_changed.emit()
                return
        else:
            infos[:] = [info for info in infos if not below(info.path, folder)]
            self.folder_moved.emit(mailbox, folder, "")
        self.lay_out()
        self.numbers_changed.emit()

    def sidebar_arrived(self, sidebar: SidebarInfo) -> None:
        """A new state of the pins and groups (the answer of a change of them, or of sidebar.show): the list follows in place."""
        self._state.sidebar = sidebar
        self.lay_out()

    def _entries_leaving(self, parent: QModelIndex, first: int, last: int) -> None:
        """Entries of the folder list are about to go. When the selected entry is one of them (or inside one), its account's Inbox
        is selected first, else the first Inbox, else the first entry that stays: the selection changes only because its entry
        goes, and never to a neighbour Qt would pick."""
        current = self.tree.currentIndex()

        def leaving(index: QModelIndex) -> bool:
            node = index
            while node.isValid():
                if node.parent() == parent and first <= node.row() <= last:
                    return True
                node = node.parent()
            return False

        if not current.isValid() or not leaving(current):
            return
        account = str(current.data(ACCOUNT_ROLE) or "")
        inbox = self.model.find_item(account, "folder", "Inbox") if account else None
        candidates = [inbox.index() if inbox is not None else None, self._first_inbox(), self._first_visible()]
        replacement = next((index for index in candidates if index is not None and index.isValid() and not leaving(index)), None)
        if replacement is not None:
            self._select_entry(replacement)

    def recount(self, mailbox: str) -> None:
        if mailbox in self.recounting:
            self.recount_again.add(mailbox)  # once more when this one is done
            return
        self.recounting.add(mailbox)

        def ended() -> None:
            self.recounting.discard(mailbox)
            if mailbox in self.recount_again:
                self.recount_again.discard(mailbox)
                self.recount(mailbox)

        def counted(result: tuple[FolderInfo, ...]) -> None:
            found = [info for info in result if isinstance(info, FolderInfo)]
            before = {info.path for info in self._state.folders.get(mailbox, [])}
            self._state.folders[mailbox] = found
            if {info.path for info in found} != before:
                self.lay_out()  # folders came or went from outside: only those entries change
            else:
                self.model.recount(self._state.folders)
            self.numbers_changed.emit()
            ended()

        def failed(error: ChronoMailError) -> None:
            self._status.report("Folders", error)
            ended()

        self._backend.ask(FOLDER_LIST, AccountParameters(mailbox), counted, failed, owner=self)

    # --- Favorites, pins and groups ---------------------------------------------------------------------------------------------

    def sidebar_call[P](self, command: TypedCommand[P, SidebarInfo], parameters: P) -> None:
        """A change of the pins, groups or Sidebar settings: the command answers with the new sidebar, and the list follows it in
        place (never every account's folders again)."""
        self._backend.ask(command, parameters, self.sidebar_arrived, lambda error: self._status.report("Could not change the folder list", error), owner=self)

    def change_favorites(self, steps: list[AnyStep[SidebarInfo]]) -> None:
        """Settings > Favorites after OK: the changes one after the other, then the folder list once."""
        if not steps:
            return
        step = steps.pop(0)

        def next_step(result: SidebarInfo) -> None:
            if steps:
                self.change_favorites(steps)
            else:
                self.sidebar_arrived(result)  # the last answer holds every change: the list follows in place

        def failed(error: ChronoMailError) -> None:
            self._status.report("Could not change Favorites", error)
            self._backend.ask(SIDEBAR_SHOW, NoParameters(), self.sidebar_arrived, lambda error: self._status.report("Favorites and groups", error), owner=self)

        step.start(self._backend, next_step, failed, owner=self)

    def _ask_name(self, title: str, label: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self._parent, title, label, text=text)
        return name.strip() if ok and (name.strip() or text) else None

    def pin_order(self) -> list[str]:
        return [pin.pin_id for pin in self._state.sidebar.pins]

    def tree_extras(self, menu: QMenu, index: QModelIndex) -> None:
        """What the clicked item of the folder list offers about Favorites and groups."""
        kind = str(index.data(KIND_ROLE)) if index.isValid() else ""
        account = str(index.data(ACCOUNT_ROLE) or "")
        sidebar = self._state.sidebar
        if kind == EntryKind.FOLDER:
            folder = str(index.data(FOLDER_PATH_ROLE))
            pinned = any(pin.account == account and pin.folder == folder for pin in sidebar.pins)
            if not pinned:
                menu.addSeparator()
                menu.addAction("Add to Favorites", lambda: self.sidebar_call(FAVORITE_PIN, PinParameters(account, folder)))
        elif kind == EntryKind.PINLINK:
            pin = str(index.data(PIN_ROLE))
            order = self.pin_order()
            position = order.index(pin) if pin in order else -1
            menu.addSeparator()
            menu.addAction("Remove from Favorites", lambda: self.sidebar_call(FAVORITE_UNPIN, PinnedParameters(pin)))
            menu.addAction("Rename…", lambda: self._rename_pin(pin, str(index.data(Qt.ItemDataRole.DisplayRole))))
            up = menu.addAction("Move up", lambda: self.move_pin(order, position, -1))
            up.setEnabled(position > 0)
            down = menu.addAction("Move down", lambda: self.move_pin(order, position, 1))
            down.setEnabled(0 <= position < len(order) - 1)
        elif kind == EntryKind.ACCOUNT and str(index.data(ACCOUNT_ROLE)) and not self._state.is_local(account):
            self._group_menu(menu, account)
        elif kind == EntryKind.GROUP:
            group = str(index.data(GROUP_ROLE))
            menu.addSeparator()
            menu.addAction("Rename group…", lambda: self._rename_group(group, str(index.data(Qt.ItemDataRole.DisplayRole))))
            menu.addAction("Remove group", lambda: self.sidebar_call(GROUP_REMOVE, GroupParameters(group)))
        if kind in (EntryKind.FAVORITE, EntryKind.PINLINK) or (kind == EntryKind.SECTION and index.data(Qt.ItemDataRole.DisplayRole) == "Favorites"):
            self._favorite_ticks(menu)  # a group is made from an account's Put in group menu, so it always holds that account

    def _favorite_ticks(self, menu: QMenu) -> None:
        """Every standard favorite that can appear (Snoozed, Scheduled and Archive once a mailbox has them), ticked while shown; a click shows or hides it (the setting of Settings > Sidebar, D-234). What Show
        on startup chose stays ticked and greyed; the last entry left in Favorites is greyed, so the heading and this menu stay."""
        hidden, forced = set(self._state.sidebar.hidden), self.model.forced
        in_use = self._state.favorites_in_use()  # the one list of Favorites and Settings > Sidebar
        shown = [view for view, _ in in_use if view not in hidden or view in forced]
        alone = len(shown) == 1 and not self.model.pins
        menu.addSeparator()
        for view, label in in_use:
            on = view not in hidden or view in forced
            action = menu.addAction(label, lambda _checked=False, chosen=view, now=on: self.sidebar_call(FAVORITE_SHOW, VisibleParameters(chosen, not now)))
            action.setCheckable(True)
            action.setChecked(on)
            action.setEnabled(view not in forced and not (alone and view in shown))

    def _group_menu(self, menu: QMenu, account: str) -> None:
        menu.addSeparator()
        groups = list(self._state.sidebar.groups)
        submenu = menu.addMenu("Put in group")
        current = next((group.group_id for group in groups if account in group.accounts), None)
        for group in groups:
            group_id = group.group_id
            action = submenu.addAction(group.name, lambda chosen=group_id: self.sidebar_call(GROUP_ASSIGN, AssignParameters(account, chosen)))
            action.setCheckable(True)
            action.setChecked(group_id == current)
        submenu.addAction("New group…", lambda: self._new_group(account))
        if current is not None:
            menu.addAction("Take out of its group", lambda: self.sidebar_call(GROUP_ASSIGN, AssignParameters(account)))

    def _new_group(self, account: str) -> None:
        name = self._ask_name("New group", "Name of the group:")
        if name:
            self.sidebar_call(GROUP_ADD, GroupAddParameters(name, (account,)))

    def _rename_group(self, group: str, current: str) -> None:
        name = self._ask_name("Rename group", "New name:", current)
        if name:
            self.sidebar_call(GROUP_RENAME, GroupRenameParameters(group, name))

    def _rename_pin(self, pin: str, current: str) -> None:
        name = self._ask_name("Rename favorite", "New name (empty: the name of the folder):", re.sub(r"\s+\(\d+\)$", "", current))
        if name is not None:
            self.sidebar_call(FAVORITE_RENAME, RenamePinParameters(pin, name))

    def move_pin(self, order: list[str], position: int, step: int) -> None:
        target = position + step
        if 0 <= position < len(order) and 0 <= target < len(order):
            order[position], order[target] = order[target], order[position]
            self.sidebar_call(FAVORITE_REORDER, ReorderParameters(tuple(order)))

    def _tree_menu(self, position: QPoint) -> None:
        clicked = self.tree.indexAt(position)
        if clicked.isValid() and clicked != self.tree.currentIndex():
            self.tree.setCurrentIndex(clicked)
        menu = QMenu(self._parent)
        view, actions = self._state.view, self.menu
        if actions is not None:
            if self._state.can_make_folder():  # only a row of one account: Favorites, tags and groups have no account to make it in
                menu.addAction(actions.new_folder)
            if view.kind == EntryKind.FOLDER:
                if self.chosen_row_is_folder():  # never on a top row: an account or Local Folders shows its Inbox
                    menu.addAction(actions.rename_folder)
                    menu.addAction(actions.delete_folder)
                menu.addSeparator()
                menu.addAction(actions.mark_all_read)
                if view.folder_role in ("trash", "junk"):
                    menu.addAction(actions.empty_folder)
        self.tree_extras(menu, clicked)
        if menu.isEmpty():
            return  # nothing to offer here: no empty menu
        menu.exec(self.tree.viewport().mapToGlobal(position))

    # --- folders ------------------------------------------------------------------------------------------------------------------

    def chosen_row_is_folder(self) -> bool:
        """The row chosen in the folder list (a right-click makes it current) is a folder or a pinned folder, not the view: an
        account row shows its Inbox, yet it is no folder to make a folder in, rename or delete."""
        return str(self.tree.currentIndex().data(KIND_ROLE)) in (EntryKind.FOLDER, EntryKind.PINLINK)

    def new_folder(self) -> None:
        view = self._state.view
        if not self._state.can_make_folder() or view.account is None:  # it has one account (can_make_folder); said so for the type checker
            return
        name, ok = QInputDialog.getText(self._parent, "New folder", "Name of the new folder:")
        name = name.strip()
        if not ok or not name or not self._folder_name_allowed(name):
            return
        path = f"{view.folder}/{name}" if view.folder and self.chosen_row_is_folder() else name  # inside that folder; an account, Unread or Flagged: at the top
        self._backend.ask(FOLDER_CREATE, FolderParameters(view.account, path), lambda result: self._status.show("Folder created"), lambda error: self._status.report("Could not create the folder", error), owner=self)

    def rename_folder(self) -> None:
        view = self._state.view
        if view.account is None or view.folder is None:
            return
        parent, _, current = view.folder.rpartition("/")
        name, ok = TextBox.ask(self._parent, "Rename folder", "New name of the folder:", current, RENAME_WIDTH)
        name = name.strip()
        if not ok or not name or name == current or not self._folder_name_allowed(name):
            return
        path = f"{parent}/{name}" if parent else name  # the folder stays where it is
        self._backend.ask(FOLDER_RENAME, FolderRenameParameters(view.account, view.folder, path), lambda result: self._status.show(with_rules("Folder renamed", result.rules)), lambda error: self._status.report("Could not rename the folder", error), owner=self)

    def _folder_name_allowed(self, name: str) -> bool:
        """A folder's name is one name: "/" nests folders, so it would quietly make folders the person did not ask for."""
        if "/" in name:
            self._status.show("A folder name cannot contain /", error=True)
            return False
        return True

    def _delete_key_pressed(self) -> None:
        """Delete (or Shift+Delete) while the folder list has the keyboard: the folder selected, as Delete folder… does (it asks
        first), never the messages selected in the list. Nothing on a standard folder, a heading, an account or an entry of
        Favorites (a pinned folder there is not the folder itself)."""
        view = self._state.view
        if view.key_kind is EntryKind.FOLDER and view.kind is EntryKind.FOLDER and view.folder_role == "other":  # never a standard folder (CR-36)
            self.delete_folder()

    def delete_folder(self) -> None:
        view = self._state.view
        if view.account is None or view.folder is None:
            return
        answer = QMessageBox.question(self._parent, "Delete folder", f"Delete the folder “{view.folder}”, its subfolders and all the messages in them?\n\nThe files are not erased: they are kept in the store's .deleted directory.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._backend.ask(FOLDER_DELETE, FolderDeleteParameters(view.account, view.folder, with_messages=True, include_subfolders=True), lambda result: self._status.show(with_rules("Folder deleted", result.rules)), lambda error: self._status.report("Could not delete the folder", error), owner=self)
