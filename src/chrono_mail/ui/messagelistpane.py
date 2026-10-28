import bisect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from dataclasses import replace as changed
from typing import override

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QMenu, QToolButton, QTreeView, QVBoxLayout, QWidget

from chrono_mail.engine.changes import Change, MessageRow
from chrono_mail.engine.commands_addressbook import CONTACT_ADDRESSES, AddressesParameters, ContactAddress
from chrono_mail.engine.commands_combined import EVERYWHERE
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columnfit import MINIMUM_ICON
from chrono_mail.ui.columns import SAVED_LABEL
from chrono_mail.ui.columnsetup import STARTING, folder_type
from chrono_mail.ui.advancedsearch import SearchScope
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.delegate import ColumnDelegate, MessageDelegate
from chrono_mail.ui.elidedlabel import ElidedLabel
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelist import LOST_PLACES, PAGE_SIZE, MessageListModel, ViewSpec, with_days
from chrono_mail.ui.messageorder import split_key
from chrono_mail.ui.messagelistview import MessageListView
from chrono_mail.ui.models import HERE_ONLY_ROLES
from chrono_mail.ui.prefs import InUse, Remembered
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "messagelistpane.py 4.2.414"

# --- Version history ----------------------------------------------------
# v4.2.414: a search of several folders shows the sender in From, also when it starts from Sent, Drafts, the Outbox or Scheduled (D-287)
# v4.2.413: a search of All folders from a Favorites or Tags view looks in every folder of every account (the view everywhere), not in the
#           view's own folders (D-287)
# v4.2.408: Advanced search's days (Received, Sent, Snoozed until, Send at) and sizes go to the list in one step (with_days, D-288)
# v4.2.406: a search with This folder + subfolders asks for the folder and every folder inside it; a folder fact that changes what the
#           view holds fetches it again through refetch, once, one at a time (D-287)
# v4.2.396: reads_away: Remove messages from Unread when I mark them as read is on and the list shows only unread mail (D-282)
# v4.1.383: the messages Undo brought back are selected also when they come back as a new order whose rest comes later, loaded or not,
#           also in a Favorites view (D-266)
# v4.1.381: the messages Undo brought back are selected once per batch that takes its place, only the new ones, one range per run of
#           neighbouring rows (all of them again at every row froze the window for 29 s with 15,875)
# v4.1.379: Next and Previous unread find the next unread message of the whole folder, loaded or not; its rows come first; the rest of a large
#           folder's order is awaited; a folder a step opens gets its first (or last) unread message wherever it is; a held key's repeats are
#           forgotten while a step waits, one separate press is done after it (CR-15)
# v3.6.362: drop_repeats: a held key's repeats that piled up are thrown away after each action and during a pause; a separate press always counts (D-266)
# v3.6.360: paused_at_edge: a move whose next message has not come yet loads and paints it first; presses meanwhile are forgotten; after the last message nothing is selected (D-266)
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) known by their role, no longer by their name (CR-36)
# v3.6.353: take_away at_once: the rows of an action that needs no server's word go at the key press and the list is painted before the next key press is read (D-266)
# v3.6.350: the selected places not known yet held for a drop made then; an action waiting for places that could not be followed is not done, and
#           says so (RV-206); one queue runs actions, drops and Undo presses one at a time in the order made: one fetching its rows, or an Undo
#           being answered, holds the rest; an Undo pressed right after an action that gives no command goes with it
# v2.4.334: each_selected: the selected messages one by one, so a yes-or-no question stops at the first answer
# v2.4.334: deferred: an action on a selection whose places are still on their way runs once they are in (W12b)
# v2.4.329: selected_rows gives every selected message, loaded or not (Ctrl+A acted on 100 of 1,142, W12), read from the selection's
#           ranges (W13); with_rows asks the rows a choice of marks or tags needs; rows_changed once per moment of changed rows (W13)
# v2.2.314: Sorted by Send at in the Outbox too (a delayed send, B-21)
# v2.2.313: select_messages: the messages Undo brought back are selected where the list shows them, also those placed after the answer (B-18)
# v2.0.280: the sort is the open folder's (use_sort, given by its setup, B-16); Sent (Saved in Drafts) in the Sorted by menu
# v1.9.276: the count line says "1 message" (it said "1 messages")
# v1.9.274: R-19 round 4: the selected rows and facts are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.270: R-19: the contacts' addresses are a typed call
# v1.9.265: The count line is cut with "…" when it does not fit (the whole text on hover), so it never holds the window wider than 768 px (the owner)
# v1.9.264: R-17: the message list of the main window as a part of its own (out of ui/mainwindow.py): what it shows (the current view,
#           changed in one step), the sort order, the count line, the selection, the messages taken away at the key press, moving to the
#           next message or unread message, fetching the list again after a change from outside

SORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("date", "Received"), ("sent", "Sent"), ("subject", "Subject"), ("sender", "Sender"), ("to", "To"), ("size", "Size"),
    ("unread", "Unread"), ("flagged", "Flagged"), ("attachment", "Attachment"), ("replied", "Replied"), ("contact", "Contact"),
    ("folder", "Folder"), ("conversation", "Messages in conversation"), ("preview", "Preview"), ("snoozed", "Snoozed until"),
)



@dataclass(frozen=True, slots=True)
class SelectedMessage:
    """A selected message by its place, whether its row is loaded or not: an action needs only its account, folder and id."""

    key: str  # its key in the list (its mailbox and id in a view of several mailboxes)
    account: str  # its mailbox in a view of several mailboxes; "" in a view of one (MailState.account_of gives the view's)
    folder: str
    message_id: str
    row: MessageRow | None  # None while it has not come into view

@dataclass(slots=True)
class _Waiting:
    """An action, a drop, or an Undo or Redo press made while something waited: done in turn, in the order made (D-146, B-18, RV-206)."""

    epoch: int  # the list's places_epoch then: when it changed, the places no longer mean the messages selected
    known: list[tuple[str, str]]  # (key, folder) of the selected messages the list knew then; the places' too once the whole order came
    positions: list[int]  # the places of those it did not know yet (until the whole order came)
    act: Callable[[list[SelectedMessage]], None]
    undo: bool = False  # an Undo or Redo press: it goes when the action right before it gives no command (it would undo an older one)


class _KeyRepeats(QObject):
    """The keys that reach the message list: whether the last press was the automatic repeat of a held key, and, while eating, those
    repeats thrown away before their shortcut acts. A separate press always goes through: four quick taps are four actions (D-266)."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.repeat: bool = False
        self.eating: bool = False

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and isinstance(event, QKeyEvent):
            if self.eating and event.isAutoRepeat():
                event.accept()  # an accepted shortcut override: no shortcut acts; the key press after it is eaten here too
                return True
            self.repeat = event.isAutoRepeat()
        elif kind == QEvent.Type.KeyRelease and isinstance(event, QKeyEvent) and not event.isAutoRepeat():
            self.repeat = False  # the key is let go
        return False


def _selected(model: MessageListModel, key: str, folder: str) -> SelectedMessage:
    mailbox, message_id = split_key(key)
    return SelectedMessage(key, mailbox, folder, message_id, model.loaded_row(key))


class MessageListPane(QObject):
    """The message list: the one owner of the current view (what the list shows) and of the sort order."""

    view_changed = Signal()  # the current view changed: what shows it (the filters, the search field) follows
    sort_changed = Signal()  # another sort order: the sorted column's arrow follows
    taken_away = Signal(bool)  # the message being opened went with an action; True: nothing is selected any more
    conversations_toggled = Signal()
    waiting_started = Signal()  # an action waits (for the rest of a large view's order, or for the rows it needs): Undo waits in turn (B-18)
    waiting_ended = Signal()  # nothing waits any more
    rows_changed = Signal()  # rows of the list changed (marks, folder, greyed): once for all the changes of one moment, never once per row (W13)

    def __init__(
        self, backend: Backend, state: MailState, status: StatusReport, prefs: InUse, memory: Remembered, tag_book: TagColourBook,
        conversations_on: Callable[[], bool], parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._state: MailState = state
        self._status: StatusReport = status
        self._prefs: InUse = prefs
        self._memory: Remembered = memory
        self._conversations_on: Callable[[], bool] = conversations_on
        self.sort: str = STARTING["incoming"].sort  # the open folder's sort, given by its setup (MessageColumns.follow_view, B-16)
        self.oldest_first: bool = STARTING["incoming"].oldest_first
        self._refetching: bool = False  # the list is being fetched again after a change from outside (one at a time)
        self._refetch_again: bool = False  # another change from outside came meanwhile: once more afterwards
        self._first_unread_on_load: int = 0  # after the next folder has loaded: select its first (or last) unread message
        self._unread_busy: bool = False  # a step to an unread message waits for its rows or the rest of the order (CR-15)
        self._first_unread_view: int = 0  # the view of the folder a step opened (-1: not shown yet)
        self._first_unread_from: int = 0  # the view that step was made in
        self._unread_presses: list[tuple[int, int]] = []  # Next or Previous unread pressed (not a held key's repeat) while a step waited: the last one, with its view
        self.unread_elsewhere: Callable[[int], bool] = lambda step: False  # the folder list: the next folder with unread mail
        self.model: MessageListModel = MessageListModel(backend, parent)
        self.delegate: MessageDelegate = MessageDelegate()
        self.column_delegate: ColumnDelegate = ColumnDelegate()
        self.model.tag_book = tag_book
        self.delegate.tag_book = tag_book
        self.column_delegate.tag_book = tag_book
        self.view: MessageListView = MessageListView(parent)
        self._repeats: _KeyRepeats = _KeyRepeats(self)
        self.view.installEventFilter(self._repeats)
        self._build_view()
        self.pane: QWidget = QWidget(parent)
        self.header_layout: QHBoxLayout = QHBoxLayout()
        self.sort_button: QToolButton = QToolButton(parent)
        self.sort_menu: QMenu = QMenu(self.sort_button)
        self.count_label: ElidedLabel = ElidedLabel("", parent)  # "23,753 messages, 512 unread", cut with "…" when room runs out (the owner)
        self._build_pane()
        self.model.groups_changed.connect(self._span_group_headers)
        self.model.header_rows_added.connect(self._span_new_headers)
        self.model.modelReset.connect(self._span_group_headers)  # a reset (another layout, other columns) forgets the spans
        self.view.header_clicked.connect(self.model.toggle_group)
        self.model.counts_changed.connect(self._counts_changed)
        self.model.load_failed.connect(status.load_failed)
        self.model.loaded.connect(self._select_unread_after_load)
        self.model.load_failed.connect(self._unread_load_failed)
        self.model.modelReset.connect(self._unread_view_shown)
        self._rows_check: QTimer = QTimer(self)
        self._rows_check.setSingleShot(True)
        self._rows_check.timeout.connect(self.rows_changed)
        self.model.dataChanged.connect(self._rows_check.start)
        self._queue: list[_Waiting] = []  # actions, drops and Undo presses waiting their turn, in the order made (W12b, D-146, B-18)
        self._acting_on: list[SelectedMessage] | None = None  # while a waiting action runs: what was selected when it was made
        self._paused: bool = False  # the next rows are loaded and painted before a move selects among them; key presses meanwhile are forgotten (D-266)
        self._unreadable: set[str] = set()  # rows the last pause asked that did not come: the folder ends there, no pause again for them
        self.model.modelReset.connect(self._forget_unreadable)  # another view, or the list fetched again: they may come now
        self._draining: bool = False  # the queue is being run (an action may ask a question, and more may come inside it: they wait their turn)
        self._holding: int = 0  # what holds the queue until it gave its command: an action fetching its rows (with_rows), an Undo being answered
        self.actions: Callable[[], int] = lambda: 0  # how many actions the person made so far (Undoing gives it): whether one gave a command
        self._selection_unknown: bool = False  # the selection held places not known yet when the list was last laid out again
        self.model.placed.connect(self._placed)
        self.model.refetch_wanted.connect(self.refetch)
        self.model.placed.connect(self._rows_check.start)  # the Archive or Unarchive label counts every selected message then
        self.model.modelReset.connect(lambda: self._places_lost(LOST_PLACES, reset=True))  # another view: what was selected is gone, its actions too
        self.model.places_lost.connect(self._places_lost)
        self.model.layoutAboutToBeChanged.connect(self._note_selection)
        self.model.rearranged.connect(self._show_current_row)
        self._to_select: list[str] = []  # messages Undo brought back, selected as they take their place in the list
        self._selected_back: set[str] = set()  # those of them selected already (a later batch adds only its own)
        self.model.load_failed.connect(self._forget_to_select)  # the rest of the order will not come: they are not waited for any longer
        self.model.took_in.connect(self._select_newly_placed)
        for placed in (self.model.rearranged, self.model.loaded, self.model.placed):  # placed: the rest of a large view's order came
            placed.connect(self._select_placed)
        self.model.selected_rows = self.selected_and_current_rows
        self.model.shown_rows = self._rows_on_screen
        self.model.loaded.connect(self._list_refetched)
        self.model.load_failed.connect(self._list_refetch_failed)
        self.update_sort_menu()

    def _build_view(self) -> None:
        view = self.view
        view.setObjectName("messageList")
        view.setModel(self.model)
        view.setRootIsDecorated(False)
        view.setItemsExpandable(False)
        view.setUniformRowHeights(True)
        view.setAllColumnsShowFocus(True)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.setDragEnabled(True)
        view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        view.setDefaultDropAction(Qt.DropAction.MoveAction)
        view.setFrameShape(QTreeView.Shape.NoFrame)
        view.header().setSectionsMovable(True)
        view.header().setFirstSectionMovable(True)  # a tree view keeps its first column in place for a real tree; this list is flat
        view.header().setStretchLastSection(False)  # the columns fill the list themselves (column fit)
        view.header().setMinimumSectionSize(MINIMUM_ICON)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.is_header = self.model.is_header
        view.header().setSectionsClickable(True)
        view.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _build_pane(self) -> None:
        layout = QVBoxLayout(self.pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget(self.pane)
        header.setLayout(self.header_layout)
        self.header_layout.setContentsMargins(10, 6, 10, 6)
        self.sort_button.setParent(header)
        self.sort_button.setObjectName("sortButton")
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_button.setAutoRaise(True)
        self.sort_button.setMenu(self.sort_menu)
        self.count_label.setParent(header)
        self.header_layout.addWidget(self.sort_button)
        self.header_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

    def finish_header(self, *widgets: QWidget) -> None:
        """The widgets of the other parts in the header of the list (the Filter button, the filter chip), then the count line."""
        for widget in widgets:
            self.header_layout.addWidget(widget)
        self.header_layout.addWidget(self.count_label)

    # --- what the list shows ---------------------------------------------------------------------------------

    def change_view(self, view: CurrentView) -> None:
        """What the list shows changes, in one step: the value, then everything that shows it; the list asks again only when
        what it shows changed."""
        before = self.view_spec()
        self._state.view = view
        self.view_changed.emit()
        if self.view_spec() != before:
            self.apply_view()

    def view_spec(self) -> ViewSpec | None:
        """What the list should ask for: made from the current view only, never from the widgets (R-14)."""
        view = self._state.view
        if view.account is None:
            return None
        query = view.query
        unread = True if view.unread else None
        flagged = True if view.flagged else None
        attachments = True if view.attachments else None
        advanced = view.advanced
        if view.combined is not None and view.searching and view.scope is SearchScope.ALL:  # All folders is every folder of every account (D-287)
            return with_days(ViewSpec(
                "", None, query, self.effective_sort(), self.oldest_first, unread, flagged, attachments, combined=EVERYWHERE, tag=view.tag,
            ), advanced)
        if view.combined is not None:  # the filters narrow a Favorites or Tags view as they narrow a folder (R-03)
            return with_days(ViewSpec(
                "", None, query, self.effective_sort(), self.oldest_first, unread, flagged, attachments,
                combined=view.combined, combined_accounts=view.combined_accounts, combined_tag=view.combined_tag or None, tag=view.tag,
            ), advanced)
        folder = view.folder
        if view.searching and view.scope is SearchScope.ALL:
            folder = None
        elif view.kind in (EntryKind.UNREAD, EntryKind.FLAGGED):
            folder = None
        subfolders = folder is not None and view.searching and view.scope is SearchScope.SUBFOLDERS
        grouped = self._conversations_on() and view.kind == EntryKind.FOLDER and not (query or unread or flagged or attachments or view.advanced is not None)
        return with_days(ViewSpec(
            view.account, folder, query, self.effective_sort(), self.oldest_first, unread, flagged, attachments,
            conversations=grouped and view.tag is None, tag=view.tag, subfolders=subfolders,
        ), advanced)

    def apply_view(self) -> None:
        view = self._state.view
        self.delegate.show_recipients = not view.several_folders and (view.folder_role in ("sent", "drafts", *HERE_ONLY_ROLES) or view.combined in HERE_ONLY_ROLES)
        self.model.recipients_first = self.delegate.show_recipients
        self.model.show(self.view_spec())

    def open(self, view: CurrentView) -> None:
        """Another entry of the folder list: its own view in one step (the window then has what shows it follow, and asks for it)."""
        self._state.view = view

    def show_empty(self) -> None:
        """An account without an Inbox: an empty list that says so."""
        self.model.show(None)

    def toggle_groups(self, on: bool) -> None:
        self._memory.show_groups = on
        self.model.show_groups = on
        self.apply_view()

    def toggle_conversations(self, on: bool) -> None:
        self._prefs.keep(changed(self._prefs.read(), conversations=on))
        self.conversations_toggled.emit()
        self.apply_view()

    # --- the sort order --------------------------------------------------------------------------------------

    def update_sort_menu(self) -> None:
        self.sort_menu.clear()
        sort = self.effective_sort()
        for value, label in SORT_CHOICES:
            if value == "snoozed" and not self._state.in_timed():
                continue  # only the Snoozed and the Scheduled folder know when their messages leave
            action = self.sort_menu.addAction(self._sort_label(value, label))
            action.setCheckable(True)
            action.setChecked(value == sort)
            action.triggered.connect(lambda checked=False, chosen=value: self.set_sort(chosen, self.oldest_first))
        self.sort_menu.addSeparator()
        for flag, label in ((False, "Newest / Z–A / largest first"), (True, "Oldest / A–Z / smallest first")):
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(flag == self.oldest_first)
            action.triggered.connect(lambda checked=False, chosen=flag: self.set_sort(self.sort, chosen))
        label = self._sort_label(sort, dict(SORT_CHOICES).get(sort, "Received"))
        self.sort_button.setText(f"Sorted by {label} ▾")

    def _sort_label(self, value: str, label: str) -> str:
        if value == "sent" and folder_type(self._state.view) == "drafts":
            return SAVED_LABEL
        return "Send at" if value == "snoozed" and self._state.view.folder_role in ("scheduled", "outbox") and self._state.in_timed() else label

    def effective_sort(self) -> str:
        """The chosen sort order; sorting by Snoozed until (Send at) is sorting by Received outside Snoozed and Scheduled."""
        return "date" if self.sort == "snoozed" and not self._state.in_timed() else self.sort

    def use_sort(self, sort: str, oldest_first: bool) -> None:
        """The sort of the folder about to be shown (its setup): taken into use without asking the list, which is asked next."""
        self.sort, self.oldest_first = sort, oldest_first
        self.update_sort_menu()

    def set_sort(self, sort: str, oldest_first: bool) -> None:
        self.sort, self.oldest_first = sort, oldest_first
        self.update_sort_menu()
        self.model.rearrange(self.view_spec())  # the same messages in the new order: the selected ones stay selected
        self.sort_changed.emit()

    # --- the rows ---------------------------------------------------------------------------------------------

    def _span_group_headers(self) -> None:
        """A group header is one bar over all columns: every header, after a reset or a new layout (the view keeps a span
        with its row as rows come and go, so a change of single rows does not come here)."""
        for row in self.model.header_rows():
            self.view.setFirstColumnSpanned(row, QModelIndex(), True)

    def _span_new_headers(self, first: int, last: int) -> None:
        """Rows came in: only the new headers among them get their bar."""
        starts = self.model.header_rows()
        for row in starts[bisect.bisect_left(starts, first):bisect.bisect_right(starts, last)]:
            self.view.setFirstColumnSpanned(row, QModelIndex(), True)

    def _counts_changed(self, total: int, unread: int) -> None:
        self.count_label.setText(f"{total:,} {'message' if total == 1 else 'messages'}" + (f", {unread:,} unread" if unread else ""))

    def load_contact_addresses(self) -> None:
        """The addresses of the contacts, for the Contact column."""

        def arrived(result: tuple[ContactAddress, ...]) -> None:
            self.model.contacts = {entry.address: (entry.item, entry.collection) for entry in result}
            self.model.refresh_cells()

        self._backend.ask(CONTACT_ADDRESSES, AddressesParameters(), arrived, lambda error: None, owner=self)

    # --- the selection ---------------------------------------------------------------------------------------

    def _selected_list_rows(self) -> list[int]:
        """The rows of the list that are selected, read from the selection's ranges: Qt's selectedRows asks every cell of every selected
        row, which cost seconds for a large selection (W13)."""
        rows: set[int] = set()
        selection = self.view.selectionModel().selection()
        for number in range(selection.count()):
            chosen = selection.at(number)
            rows.update(range(chosen.top(), chosen.bottom() + 1))
        return sorted(rows)

    def selected_and_current_rows(self) -> list[int]:
        """The rows of the list that are selected, and the current row (a new order keeps them selected)."""
        rows = set(self._selected_list_rows())
        current = self.view.currentIndex()
        if current.isValid():
            rows.add(current.row())
        return sorted(rows)

    def _rows_on_screen(self) -> list[int]:
        """The rows of the list the person sees now (a list fetched again brings their rows with it, so nothing on screen blinks)."""
        viewport = self.view.viewport()
        top, bottom = self.view.indexAt(viewport.rect().topLeft()), self.view.indexAt(viewport.rect().bottomLeft())
        if not top.isValid():
            return []
        last = bottom.row() if bottom.isValid() else self.model.rowCount() - 1
        return list(range(top.row(), last + 1))

    def _show_current_row(self) -> None:
        """After the list was rearranged: the current message is in view."""
        current = self.view.currentIndex()
        if current.isValid():
            self.view.scrollTo(current)

    def has_selection(self) -> bool:
        return self.view.selectionModel().hasSelection()

    def current_row(self) -> MessageRow | None:
        index = self.view.currentIndex()
        return self.model.row_at(index.row()) if index.isValid() else None

    def selected_rows(self) -> list[SelectedMessage]:
        """The messages selected now, every one of them, also those whose rows have not come into view yet (Ctrl+A on a large folder; W12),
        without those that have an action under way (they get no second one). While a waiting action runs: what was selected when it was
        made (D-146), whatever was clicked since."""
        return list(self.each_selected())

    def each_selected(self) -> Iterator[SelectedMessage]:
        """The selected messages one by one, for a question that may stop at the first answer (is every one archived?)."""
        if self._acting_on is not None:
            yield from (item for item in self._acting_on if item.message_id not in self.model.pending)
            return
        for list_row in self._selected_list_rows():
            place = self.model.place_at(list_row)
            if place is None:
                continue
            key, folder, row = place
            mailbox, message_id = split_key(key)
            if message_id not in self.model.pending:
                yield SelectedMessage(key, mailbox, folder, message_id, row)

    def hold_selected_places(self, then: Callable[[list[SelectedMessage]], None]) -> None:
        """The selected messages, once the list knows every one (a drop made while it did not yet, RV-206): in turn with the actions waiting
        for it, so what was done first goes first; at once when it knows them all."""
        self._after_selection(then)

    @property
    def queued(self) -> bool:
        """Whether something waits its turn, or holds what comes after it (an action fetching its rows, an Undo being answered): an action or
        an Undo made now goes in turn after it (D-146, B-18)."""
        return bool(self._queue) or self._holding > 0 or self._draining

    @property
    def waiting(self) -> bool:
        """Whether an action or a drop waits for the rest of a large view's order, or for the rows it needs."""
        return self.queued

    def deferred(self, again: Callable[[], None]) -> bool:
        """True when the selection holds messages whose places are still on their way (Ctrl+A just after a large folder opened: the list
        knows its first 100 for a moment), or something waits before it: the action runs in its turn, on what was selected now, the key
        press (W12b, D-146)."""
        if self._acting_on is not None or (
            (self.model.complete or not self.model.unknown_positions(self._selected_list_rows())) and not self.queued
        ):
            return False

        self._after_selection(lambda chosen: self._act_on(chosen, again))
        return True

    def _act_on(self, chosen: list[SelectedMessage], again: Callable[[], None]) -> None:
        """A waiting action in its turn: on what was selected when it was made."""
        self._acting_on = chosen
        try:
            again()
        finally:
            self._acting_on = None

    def _after_selection(self, act: Callable[[list[SelectedMessage]], None]) -> None:
        """Act on what is selected now: the messages the list knows now, and the places whose messages it does not know yet, which are
        read when its whole order is in, unless the list could not keep those places meanwhile (places_epoch): then nothing is done, and
        that is said (RV-206)."""
        rows = self._selected_list_rows()
        positions = self.model.unknown_positions(rows)
        known = list(self.each_selected())
        if not positions and not self.queued:  # nothing waits before it: at once
            act(known)
            return
        if positions and not self.model.loading:  # the rest of the order could not be read: it will not come by itself
            self._status.show("Nothing was done: the list could not be read to its end. Open the folder again.", error=True)
            return
        self._enqueue(_Waiting(self.model.places_epoch, [(entry.key, entry.folder) for entry in known], positions, act))

    def _note_selection(self) -> None:
        """Before the list is laid out again: whether the selection holds places not known yet (only while the rest of the order is on its way;
        once it is in, a later layout keeps what was noted: places_lost or the order's arrival says what became of them)."""
        if not self.model.complete:
            self._selection_unknown = bool(self.model.unknown_positions(self._selected_list_rows()))

    def after_waiting(self, act: Callable[[], None], undo: bool = False) -> None:
        """An action that takes no selection (a drop of messages the list knows, Mark all as read), or an Undo or Redo press (undo): at once,
        or, while something waits, in its turn behind it, so everything is done in the order made."""
        if not self.queued:
            act()
            return
        self._enqueue(_Waiting(self.model.places_epoch, [], [], lambda chosen: act(), undo))

    def hold(self) -> Callable[[], None]:
        """What comes after waits until the returned call (an Undo until it is answered): it sees the facts of what came before it."""
        self._holding += 1
        self.waiting_started.emit()
        released: list[bool] = []

        def release() -> None:
            if not released:
                released.append(True)
                self._holding -= 1
                self._drain()

        return release

    def _enqueue(self, item: _Waiting) -> None:
        self._queue.append(item)
        self.waiting_started.emit()
        self._drain()

    def _drop(self, lost: Callable[[_Waiting], bool]) -> None:
        """The waiting actions that are not done go, each with the Undo and Redo presses right after it (meant for it)."""
        kept: list[_Waiting] = []
        dropping = False
        for item in self._queue:
            if item.undo and dropping:
                continue
            dropping = not item.undo and lost(item)
            if not dropping:
                kept.append(item)
        self._queue = kept

    def _skip_undo(self) -> None:
        """The action before them gave no command (nothing of it was left, or the person said no): the Undo and Redo presses made right
        after it go too; they would undo an older action the person never asked about (B-18)."""
        while self._queue and self._queue[0].undo:
            self._queue.pop(0)

    def _places_lost(self, why: str, reset: bool = False) -> None:
        """Selected places whose messages were not known yet could not be kept: what waits for them is not done, and says so; what waited
        only behind them (its messages known by key) goes on, in order."""
        cut, self._selection_unknown = self._selection_unknown and not reset, False  # (a view that went takes its selection with it)
        if cut:  # the selection lost what it held past the start: a later key press would act on part of it; it is cleared
            self.view.clearSelection()
        if any(item.positions for item in self._queue):
            self._status.show(why, error=True)
            self._drop(lambda item: bool(item.positions))
        elif cut:
            self._status.show("The list changed while it loaded: select the messages again", error=True)
        self._drain()

    def _placed(self) -> None:
        """The whole order is in: every waiting place becomes its message now (an action that asks a question may let the list change before
        the next runs); those whose places the list could not keep are not done, and say so."""
        self._selection_unknown = False
        epoch = self.model.places_epoch
        if any(item.positions and item.epoch != epoch for item in self._queue):
            self._status.show(LOST_PLACES, error=True)
            self._drop(lambda item: bool(item.positions) and item.epoch != epoch)
        for item in self._queue:
            if item.positions:
                item.known = item.known + self.model.places_at(item.positions)
                item.positions = list[int]()
        self._drain()

    def _drain(self) -> None:
        """Run what waits, one at a time, in the order made: each with its messages as they are now (without those that left or have an
        action under way, D-146); one that holds (fetches its rows, an Undo being answered) holds the rest until it gave its command."""
        if self._draining:
            return  # inside one that runs (a question it asks): the loop below goes on with what came meanwhile
        self._draining = True
        try:
            while self._queue and not self._holding and not self._queue[0].positions:  # places not known yet: their order is on its way
                item = self._queue.pop(0)
                chosen = [_selected(self.model, key, folder) for key, folder in ((key, self.model.folder_of(key)) for key, _ in item.known) if folder is not None]
                if item.known and not chosen:  # none of its messages is in the list any more (another view opened, or they left): said
                    self._status.show("Nothing was done: the messages it was for are no longer in the list", error=True)
                before = self.actions()
                item.act([entry for entry in chosen if entry.message_id not in self.model.pending])
                if not item.undo and not self._holding and self.actions() == before:
                    self._skip_undo()
        finally:
            self._draining = False
        self._ended_waiting()

    def _ended_waiting(self) -> None:
        if not self.waiting:
            self.waiting_ended.emit()

    def with_rows(self, chosen: list[SelectedMessage], act: Callable[[list[SelectedMessage], list[MessageRow]], None], action: bool = True) -> None:
        """Act on these messages and their rows, for a choice that needs their marks or tags: the rows not loaded yet are asked first, so the
        choice counts every message, not only those that came into view (W12). act gets the messages still in the view without an action under
        way by then (a Delete made meanwhile took some: no second action, D-146). action: it gives a command, and what is made meanwhile (an
        action, an Undo) waits for it; a menu that only shows ticks does not."""
        release = self.hold() if action else (lambda: None)

        def loaded() -> None:
            try:
                still = [_selected(self.model, item.key, folder) for item in chosen if (folder := self.model.folder_of(item.key)) is not None]  # as
                still = [item for item in still if item.message_id not in self.model.pending]  # they are now: their folder may have changed
                rows = [row for row in (item.row or self.model.loaded_row(item.key) for item in still) if row is not None]
                if still and rows:
                    act(still, rows)
                elif action:
                    self._skip_undo()
            finally:
                release()

        self.model.load_rows([item.key for item in chosen if item.row is None], loaded)

    def paused_at_edge(self, keyed: bool, rows: list[int] | None = None, again: Callable[[], None] | None = None) -> bool:
        """Before an action that takes messages out of the list (these rows, else the selected ones) and selects the next message (D-266):
        when that message has not come yet, the next rows are loaded and painted first, and the list pauses meanwhile. True: not now. A key
        press (keyed: an action without a question, as a held Delete, Archive or Junk key) is then forgotten, as is every such press until
        those rows are painted and the keyboard buffer is cleared. An action after a question or a menu (not keyed) waits in turn and is
        checked again when its turn comes (again: how it is done then). Rows that could not be read, or an order that will not come, end
        the folder there: the action is done and nothing is selected after it."""
        repeat = keyed and self._repeats.repeat  # a held key's automatic repeat, not a separate press
        if self._paused:
            return repeat  # a repeat is forgotten; a separate press or a button waits its turn behind the pause
        taken = self._selected_list_rows() if rows is None else rows
        current = self.view.currentIndex()
        if not current.isValid() or current.row() not in taken:
            return False  # the selection does not move
        following = self.model.next_row_after(max(taken))
        if not isinstance(following, list):
            return False
        if (following and following[0] in self._unreadable) or (not following and not self.model.loading):
            return False  # asked and not come, or the rest of the order could not be read: it ends there
        self._pause(following)
        if keyed and self._acting_on is None:
            return repeat  # a held key's repeat now: forgotten; a separate press is done once the rows are painted
        if self._acting_on is not None and again is not None:  # its turn came (it was accepted then) and the next rows are not there yet: first in turn again
            chosen = self._acting_on
            self._queue.insert(0, _Waiting(self.model.places_epoch, [(item.key, item.folder) for item in chosen], [], lambda now: self._act_on(now, again)))
            return True
        return False  # it goes in turn behind the pause (deferred, after_waiting)

    def _pause(self, keys: list[str]) -> None:
        """The next rows are loaded (keys), or the rest of the order is awaited (no keys), painted, and the keyboard buffer cleared."""
        self._paused = True
        self._repeats.eating = True  # a held key's repeats are thrown away meanwhile
        release = self.hold()  # an action after a question or a menu waits behind it
        ended: list[bool] = []

        def painted() -> None:
            QCoreApplication.processEvents()  # the keyboard buffer: the repeats that came meanwhile are read now, and thrown away
            self._repeats.eating = False
            self._paused = False
            self._unreadable = {key for key in keys if self.model.loaded_row(key) is None and self.model.holds(key)}
            release()

        def loaded(*_: object) -> None:
            if ended:
                return
            ended.append(True)
            if not keys:
                for signal in (self.model.placed, self.model.load_failed, self.model.modelReset):
                    signal.disconnect(loaded)
            self.view.viewport().repaint()
            QTimer.singleShot(0, self, painted)  # tied to the pane: not run when it is already gone

        if keys:
            self.model.load_rows(keys, loaded)
        else:  # the rest of the order comes, cannot come, or the view goes
            for signal in (self.model.placed, self.model.load_failed, self.model.modelReset):
                signal.connect(loaded)

    def reads_away(self) -> bool:
        """Whether a message the person marks as read leaves the list now: Remove messages from Unread when I mark them as read is on,
        and the list shows only unread mail (D-282)."""
        return self._prefs.applied.unread_leaves_on_read and self.model.shows_unread_only()

    def drop_repeats(self) -> None:
        """After an action that took messages out of the list: the automatic repeats of a held key that piled up meanwhile are thrown
        away, so letting go of the key stops at once; a separate press is never thrown away (D-266)."""
        if self._repeats.eating:
            return  # inside one already, or during a pause
        self._repeats.eating = True
        try:
            QCoreApplication.processEvents()
        finally:
            self._repeats.eating = self._paused

    def _forget_unreadable(self) -> None:
        self._unreadable = set()

    def take_away(self, message_ids: list[str], at_once: bool = False) -> None:
        """At the key press of an action that takes messages out of the view: they are greyed until the engine reports the
        change (or the action fails), and the selection and the reading pane move at once to the next message without an
        action under way (the one after them, else the one before), so a next key press acts on what the person sees. An action
        that needs no server's word (at_once: POP3, Local Folders) takes the rows out now, and the list is painted before the next
        key press is read (D-266)."""
        model = self.model
        model.mark_pending(message_ids)
        taken = set(message_ids)
        rows = [model.find(message_id) for message_id in message_ids]
        rows = [row for row in rows if row >= 0]
        current = self.view.currentIndex()
        current_row = model.row_at(current.row()) if current.isValid() else None
        if current_row is not None and current_row.message_id in taken:
            self.taken_away.emit(False)  # the message that is being opened is moved away: its opening no longer counts
        elif current_row is not None:
            if at_once:
                model.leave_now(message_ids)  # the selected message stays where it is shown: it follows its message
                self.view.viewport().repaint()
            return  # the selected message stays: nothing moves under the person's eyes

        following = model.next_row_after(max(rows) if rows else current.row())
        target = following if isinstance(following, int) else None  # after the last message nothing is selected (D-266)
        if target is None:
            self.view.selectionModel().clearSelection()
            self.view.setCurrentIndex(QModelIndex())
            self.taken_away.emit(True)
            if at_once:
                model.leave_now(message_ids)
                self.view.viewport().repaint()
            return
        if at_once:
            found = model.row_at(target)
            model.leave_now(message_ids)  # the rows go at the key press; the selection follows its message (D-266)
            target = model.find(found.message_id) if found is not None else -1
            if target < 0:
                self.view.selectionModel().clearSelection()
                self.view.setCurrentIndex(QModelIndex())
                self.taken_away.emit(True)
                self.view.viewport().repaint()
                return
        index = model.index(target, 0)
        self.view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.view.scrollTo(index)
        if at_once:
            self.view.viewport().repaint()  # on screen before the next key press is read: a press always acts on the row it shows highlighted (D-266)

    def select_messages(self, message_ids: Iterable[str]) -> None:
        """Select these messages where the list shows them (Undo brought them back); the first is the current one, in view. Those still
        on their way into the list (the engine is asked where they go) are selected with the others when they are placed."""
        self._to_select = list(message_ids)
        self._select_placed()

    def _select_placed(self, *_: object) -> None:
        """Select every message Undo brought back that the list shows; the first is the current one, in view (a new order or a new load:
        all of them again, once)."""
        self._selected_back = set()
        self._select_back(True)

    def _forget_to_select(self, *_: object) -> None:
        if self.model.loading:
            return  # a page of rows that failed: the order still comes, and with it their places
        self._to_select = []
        self._selected_back = set()

    def _select_newly_placed(self) -> None:
        """Messages that came in at one moment took their places: those Undo brought back are added to the selection, the others stay
        selected (selecting all of them again at every row froze the window for 29 s with 15,875)."""
        self._select_back(False)

    def _select_back(self, whole: bool) -> None:
        wanted = self._to_select
        if not wanted:
            return
        if not self.model.arriving_any(wanted) and self.model.complete:
            self._to_select = []  # every one is placed and the whole order is in, or not in this view (a failed order ends it too)
            done, self._selected_back = self._selected_back, set()
        else:
            done = self._selected_back
        found = self.model.rows_of_messages(message_id for message_id in wanted if message_id not in done)
        if not found:
            return
        fresh = whole or not done  # the first of them to be selected: what was selected before goes
        if self._to_select:
            done.update(message_id for _, message_id in found)
        rows = sorted(row for row, _ in found)
        last_column = self.model.columnCount() - 1
        selection = QItemSelection()
        start = previous = rows[0]
        for row in rows[1:] + [-1]:
            if row != previous + 1:  # one range per run of neighbouring rows, not one per row
                selection.select(self.model.index(start, 0), self.model.index(previous, last_column))
                start = row
            previous = row
        flags = QItemSelectionModel.SelectionFlag
        selected = self.view.selectionModel()
        current = self.view.currentIndex()
        first = self.model.index(rows[0], 0)
        if fresh or not current.isValid():
            selected.setCurrentIndex(first, flags.ClearAndSelect | flags.Rows)
        elif rows[0] < current.row():
            selected.setCurrentIndex(first, flags.NoUpdate)  # the first of them is the current one
        else:
            first = current
        selected.select(selection, flags.Select | flags.Rows)
        self.view.scrollTo(first)

    def step_message(self, step: int) -> None:
        """Select the next (or the previous) message of the list."""
        count = self.model.rowCount()
        current = self.view.currentIndex().row() if self.view.currentIndex().isValid() else (-1 if step > 0 else count)
        position = current + step
        while 0 <= position < count and self.model.is_header(position):  # group headers are passed
            position += step
        if 0 <= position < count:
            self.view.setCurrentIndex(self.model.index(position, 0))
            self.view.scrollTo(self.model.index(position, 0))

    def go_to_unread(self, step: int) -> bool:
        """Select the next (or previous) unread message of the list; at its end, the first (or last) unread one of the next folder that has
        some (D-054). The list knows which of its messages are unread, loaded or not (D-218): the rows of one not loaded yet are loaded first and
        it is selected once they came, never before (D-266); where it may be among places not known yet, the rest of the order is awaited. While
        a step waits, the repeats of a held key are forgotten and one separate press is kept and done after it (the key's repeats are told
        apart only while the list has the focus: elsewhere, as in the reading pane, a held key goes on at most one step after it is let go)."""
        if self._unread_busy or self._first_unread_on_load or not self.model.settled:
            if not self._repeats.repeat:
                self._unread_presses[:] = [(step, self.model.view_number)]
            return True
        return self._unread_step(step, self._unread_start(step), False)

    def _unread_start(self, step: int) -> int:
        """The place of the view a step to an unread message starts at (included): the one after (or before) the current message."""
        model = self.model
        current = self.view.currentIndex()
        if not current.isValid():
            return 0 if step > 0 else model.message_count() - 1
        position = model.message_position(current.row())
        if position is not None:
            return position + step
        rows = range(current.row() + 1, model.rowCount()) if step > 0 else range(current.row() - 1, -1, -1)  # on a group header
        return next((found for found in (model.message_position(row) for row in rows) if found is not None), model.message_count() if step > 0 else -1)

    def _unread_step(self, step: int, start: int, edge: bool) -> bool:
        """Select the first unread message from this place on (edge: the folder was just opened by a step, which ends there). Its rows are
        loaded first when they have not come; when they could not be read, the step ends there (the list says why)."""
        model = self.model
        view = model.view_number
        position, sure = model.unread_place(start, step)
        if not sure:
            self._unread_busy = True  # the rest of the order is on its way: it may hold the next unread message
            self._unread_after_order(lambda: self._unread_again(view, step, edge))
            return True
        if position is None:
            self._unread_busy = False
            if not edge and self.unread_elsewhere(step):
                return True  # its first (or last) unread message is selected once it came (_select_unread_after_load)
            self._next_unread_press()
            return False
        place = model.places_at([position])
        key = place[0][0] if place else ""
        if model.loaded_row(key) is not None:
            index = model.index(model.display_row(position), 0)
            self.view.setCurrentIndex(index)
            self.view.scrollTo(index)
            self._unread_busy = False
            self._next_unread_press()
            return True
        self._unread_busy = True
        first = position // PAGE_SIZE * PAGE_SIZE  # its page, as the list loads them

        def loaded() -> None:
            if model.view_number == view and model.holds(key) and model.loaded_row(key) is None:
                self._unread_busy = False  # its rows could not be read: the step ends here, never on a later message without a word
                self._unread_presses.clear()
                return
            self._unread_again(view, step, edge)  # loaded, or it left the view meanwhile: from where the current message is now

        model.load_rows([each for each, _ in model.places_at(range(first, first + PAGE_SIZE))], loaded)
        return True

    def _unread_again(self, view: int, step: int, edge: bool) -> None:
        """A waiting step goes on: from where the current message is now, in the view it was made in (another view: it is not done, and
        only a press made in the view shown now is kept)."""
        if self.model.view_number != view:
            self._unread_busy = False
            self._unread_presses[:] = [press for press in self._unread_presses if press[1] == self.model.view_number]
            self._next_unread_press()
            return
        start = (0 if step > 0 else self.model.message_count() - 1) if edge else self._unread_start(step)
        self._unread_step(step, start, edge)

    def _unread_after_order(self, then: Callable[[], None]) -> None:
        """Call then once the rest of the order came, could not come, or the view went."""
        signals = (self.model.placed, self.model.load_failed, self.model.modelReset)

        def ended(*_: object) -> None:
            for signal in signals:
                signal.disconnect(ended)
            then()

        for signal in signals:
            signal.connect(ended)

    def _next_unread_press(self) -> None:
        """A press made while a step waited is done now."""
        if self._unread_presses and not self._unread_busy and not self._first_unread_on_load and self.model.settled:
            step, view = self._unread_presses.pop(0)
            if view == self.model.view_number:
                self.go_to_unread(step)

    def select_unread_after_next_load(self, step: int) -> None:
        self._first_unread_on_load = step
        self._first_unread_view = -1  # the next view shown is the folder this step opens
        self._first_unread_from = self.model.view_number

    def _unread_view_shown(self) -> None:
        """Another view is shown: the folder a step opens, or one opened by hand before it came, which ends the step (nothing is selected
        there unasked) and keeps only a press made in it."""
        if not self._first_unread_on_load:
            return
        if self._first_unread_view < 0:
            if self.model.view_number != self._first_unread_from:  # not a reset of the view it left (its columns)
                self._first_unread_view = self.model.view_number
        elif self.model.view_number != self._first_unread_view:
            self._first_unread_on_load = 0
            self._unread_presses[:] = [press for press in self._unread_presses if press[1] == self.model.view_number]

    def _select_unread_after_load(self) -> None:
        """The folder a step opened came: its first (or last) unread message is selected, once the question for the rest of its order is
        on its way (a large view comes start first)."""
        step, self._first_unread_on_load = self._first_unread_on_load, 0
        if step and self.model.view_number != self._first_unread_view:
            step = 0  # another folder was opened by hand meanwhile: nothing is selected there unasked
        if not step:
            if not self._unread_busy:
                QTimer.singleShot(0, self, self._next_unread_press)  # a press made while the view loaded
            return
        self._unread_busy = True
        view = self.model.view_number
        QTimer.singleShot(0, self, lambda: self._unread_again(view, step, True))

    def _unread_load_failed(self) -> None:
        """The folder a step opened, or one opened by a click, could not be read: the step ends there, and a press made meanwhile with it
        (it would act later, unasked); a step that waits for its rows goes on by itself."""
        self._first_unread_on_load = 0
        if not self._unread_busy:
            self._unread_presses.clear()

    # --- a change from outside ---------------------------------------------------------------------------------

    @property
    def refetching(self) -> bool:
        return self._refetching

    def fact(self, change: Change) -> None:
        """A big job changed a mailbox (a mail check, an import, rules run, a rebuilt index): the list is fetched again only when it
        shows that mailbox, once, one at a time (D-146)."""
        if str(change.kind) != "mailbox_changed":
            return
        mailbox = change.mailbox
        spec = self.model.spec
        if spec is not None and (spec.combined is not None or spec.account == mailbox):
            self.refetch()

    def refetch(self) -> None:
        """The list is fetched again (a change from outside), keeping the selected message selected; one at a time."""
        if self._refetching:
            self._refetch_again = True
            return
        self._refetching = True
        self.model.reload()

    def _list_refetch_failed(self, text: str) -> None:
        self._list_refetched()  # the error itself is said by the status line's load_failed

    def _list_refetched(self) -> None:
        if not self._refetching:
            return
        self._refetching = False
        if self._refetch_again:
            self._refetch_again = False
            self.refetch()
