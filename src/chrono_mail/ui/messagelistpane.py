import bisect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from dataclasses import replace as changed

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QMenu, QToolButton, QTreeView, QVBoxLayout, QWidget

from chrono_mail.engine.changes import Change, MessageRow
from chrono_mail.engine.commands_addressbook import CONTACT_ADDRESSES, AddressesParameters, ContactAddress
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columnfit import MINIMUM_ICON
from chrono_mail.ui.columns import SAVED_LABEL
from chrono_mail.ui.columnsetup import STARTING, folder_type
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.delegate import ColumnDelegate, MessageDelegate
from chrono_mail.ui.elidedlabel import ElidedLabel
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelist import LOST_PLACES, MessageListModel, ViewSpec
from chrono_mail.ui.messageorder import split_key
from chrono_mail.ui.messagelistview import MessageListView
from chrono_mail.ui.models import HERE_ONLY_ROLES
from chrono_mail.ui.prefs import InUse, Remembered
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "messagelistpane.py 3.6.354"

# --- Version history ----------------------------------------------------
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
        self.unread_elsewhere: Callable[[int], bool] = lambda step: False  # the folder list: the next folder with unread mail
        self.model: MessageListModel = MessageListModel(backend, parent)
        self.delegate: MessageDelegate = MessageDelegate()
        self.column_delegate: ColumnDelegate = ColumnDelegate()
        self.model.tag_book = tag_book
        self.delegate.tag_book = tag_book
        self.column_delegate.tag_book = tag_book
        self.view: MessageListView = MessageListView(parent)
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
        self._rows_check: QTimer = QTimer(self)
        self._rows_check.setSingleShot(True)
        self._rows_check.timeout.connect(self.rows_changed)
        self.model.dataChanged.connect(self._rows_check.start)
        self._queue: list[_Waiting] = []  # actions, drops and Undo presses waiting their turn, in the order made (W12b, D-146, B-18)
        self._acting_on: list[SelectedMessage] | None = None  # while a waiting action runs: what was selected when it was made
        self._draining: bool = False  # the queue is being run (an action may ask a question, and more may come inside it: they wait their turn)
        self._holding: int = 0  # what holds the queue until it gave its command: an action fetching its rows (with_rows), an Undo being answered
        self.actions: Callable[[], int] = lambda: 0  # how many actions the person made so far (Undoing gives it): whether one gave a command
        self._selection_unknown: bool = False  # the selection held places not known yet when the list was last laid out again
        self.model.placed.connect(self._placed)
        self.model.placed.connect(self._rows_check.start)  # the Archive or Unarchive label counts every selected message then
        self.model.modelReset.connect(lambda: self._places_lost(LOST_PLACES, reset=True))  # another view: what was selected is gone, its actions too
        self.model.places_lost.connect(self._places_lost)
        self.model.layoutAboutToBeChanged.connect(self._note_selection)
        self.model.rearranged.connect(self._show_current_row)
        self._to_select: list[str] = []  # messages Undo brought back, selected as they take their place in the list
        for placed in (self.model.rowsInserted, self.model.rearranged, self.model.loaded):
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
        if view.combined is not None:  # the filters narrow a Favorites or Tags view as they narrow a folder (R-03)
            return ViewSpec(
                "", None, query, self.effective_sort(), self.oldest_first, unread, flagged, attachments,
                date_from=advanced.date_from if advanced else None, date_to=advanced.date_to if advanced else None,
                minimum_size=advanced.minimum_size if advanced else None, maximum_size=advanced.maximum_size if advanced else None,
                combined=view.combined, combined_accounts=view.combined_accounts, combined_tag=view.combined_tag or None, tag=view.tag,
            )
        folder = view.folder
        if (query or advanced is not None) and view.all_folders:
            folder = None
        elif view.kind in (EntryKind.UNREAD, EntryKind.FLAGGED):
            folder = None
        grouped = self._conversations_on() and view.kind == EntryKind.FOLDER and not (query or unread or flagged or attachments or view.advanced is not None)
        return ViewSpec(
            view.account, folder, query, self.effective_sort(), self.oldest_first, unread, flagged, attachments,
            date_from=advanced.date_from if advanced else None, date_to=advanced.date_to if advanced else None,
            minimum_size=advanced.minimum_size if advanced else None, maximum_size=advanced.maximum_size if advanced else None,
            conversations=grouped and view.tag is None, tag=view.tag,
        )

    def apply_view(self) -> None:
        view = self._state.view
        self.delegate.show_recipients = view.folder_role in ("sent", "drafts", *HERE_ONLY_ROLES) or view.combined in HERE_ONLY_ROLES
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

        def run(chosen: list[SelectedMessage]) -> None:
            self._acting_on = chosen
            try:
                again()
            finally:
                self._acting_on = None

        self._after_selection(run)
        return True

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

        def free(row: int) -> bool:
            found = model.row_at(row)
            return not model.is_header(row) and found is not None and found.message_id not in model.pending

        last, first = (max(rows), min(rows)) if rows else (current.row(), current.row())
        target = next((row for row in range(last + 1, model.rowCount()) if free(row)), None)
        if target is None:
            target = next((row for row in range(first - 1, -1, -1) if free(row)), None)
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
        wanted = self._to_select
        if not wanted:
            return
        if not any(self.model.arriving(message_id) for message_id in wanted):
            self._to_select = []  # every one is placed, or not in this view
        rows = sorted(row for row in (self.model.find(message_id) for message_id in wanted) if row >= 0)
        if not rows:
            return
        last_column = self.model.columnCount() - 1
        selection = QItemSelection()
        for row in rows:
            selection.select(self.model.index(row, 0), self.model.index(row, last_column))
        first = self.model.index(rows[0], 0)
        flags = QItemSelectionModel.SelectionFlag
        self.view.selectionModel().setCurrentIndex(first, flags.ClearAndSelect | flags.Rows)
        self.view.selectionModel().select(selection, flags.Select | flags.Rows)
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
        """Select the next (or previous) unread message of the list; at the end of the list, the first unread one of the next folder that has some."""
        count = self.model.rowCount()
        current = self.view.currentIndex().row() if self.view.currentIndex().isValid() else (-1 if step > 0 else count)
        position = current + step
        while 0 <= position < count:
            row = self.model.row_at(position)
            if row is not None and "seen" not in row.flags:
                self.view.setCurrentIndex(self.model.index(position, 0))
                self.view.scrollTo(self.model.index(position, 0))
                return True
            position += step
        return self.unread_elsewhere(step)

    def select_unread_after_next_load(self, step: int) -> None:
        self._first_unread_on_load = step

    def _select_unread_after_load(self) -> None:
        step, self._first_unread_on_load = self._first_unread_on_load, 0
        if not step:
            return
        count = self.model.rowCount()
        positions = range(count) if step > 0 else range(count - 1, -1, -1)
        for position in positions:
            row = self.model.row_at(position)
            if row is not None and "seen" not in row.flags:
                self.view.setCurrentIndex(self.model.index(position, 0))
                return

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
