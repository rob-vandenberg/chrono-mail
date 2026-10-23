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
from chrono_mail.ui.columns import SAVED_LABEL, SEND_AT_FOLDERS
from chrono_mail.ui.columnsetup import STARTING, folder_type
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.delegate import ColumnDelegate, MessageDelegate
from chrono_mail.ui.elidedlabel import ElidedLabel
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.messagelist import MessageListModel, ViewSpec
from chrono_mail.ui.messageorder import split_key
from chrono_mail.ui.messagelistview import MessageListView
from chrono_mail.ui.models import LOCAL_PLACES
from chrono_mail.ui.prefs import InUse, Remembered
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "messagelistpane.py 2.4.334"

# --- Version history ----------------------------------------------------
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

class MessageListPane(QObject):
    """The message list: the one owner of the current view (what the list shows) and of the sort order."""

    view_changed = Signal()  # the current view changed: what shows it (the filters, the search field) follows
    sort_changed = Signal()  # another sort order: the sorted column's arrow follows
    taken_away = Signal(bool)  # the message being opened went with an action; True: nothing is selected any more
    conversations_toggled = Signal()
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
        self._after_placed: list[Callable[[], None]] = []  # actions waiting for the rest of a large view's order (W12b)
        self.model.placed.connect(self._placed)
        self.model.placed.connect(self._rows_check.start)  # the Archive or Unarchive label counts every selected message then
        self.model.modelReset.connect(self._after_placed.clear)  # another view: what was selected is gone, and its actions with it
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
        self.delegate.show_recipients = view.folder_role in ("sent", "drafts") or view.folder in LOCAL_PLACES or view.combined in LOCAL_PLACES.values()
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
        return "Send at" if value == "snoozed" and self._state.view.folder in SEND_AT_FOLDERS and self._state.in_timed() else label

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
        without those that have an action under way (they get no second one)."""
        return list(self.each_selected())

    def each_selected(self) -> Iterator[SelectedMessage]:
        """The selected messages one by one, for a question that may stop at the first answer (is every one archived?)."""
        for list_row in self._selected_list_rows():
            place = self.model.place_at(list_row)
            if place is None:
                continue
            key, folder, row = place
            mailbox, message_id = split_key(key)
            if message_id not in self.model.pending:
                yield SelectedMessage(key, mailbox, folder, message_id, row)

    def deferred(self, again: Callable[[], None]) -> bool:
        """True when the selection holds messages whose places are still on their way (Ctrl+A just after a large folder opened: the list
        knows its first 100 for a moment): the action runs again, on every selected message, once they are in (W12b). Qt keeps the
        selection meanwhile."""
        if self.model.complete or all(self.model.place_at(row) is not None or self.model.is_header(row) for row in self._selected_list_rows()):
            return False
        self._after_placed.append(again)
        return True

    def _placed(self) -> None:
        waiting, self._after_placed = self._after_placed, list[Callable[[], None]]()
        for again in waiting:
            again()

    def with_rows(self, chosen: list[SelectedMessage], act: Callable[[list[MessageRow]], None]) -> None:
        """Act on the rows of these messages, for a choice that needs their marks or tags: the rows not loaded yet are asked first, so the
        choice counts every message, not only those that came into view (W12)."""

        def loaded() -> None:
            rows = [row for row in (item.row or self.model.loaded_row(item.key) for item in chosen) if row is not None]
            if rows:
                act(rows)

        self.model.load_rows([item.key for item in chosen if item.row is None], loaded)

    def take_away(self, message_ids: list[str]) -> None:
        """At the key press of an action that takes messages out of the view: they are greyed until the engine reports the
        change (or the action fails), and the selection and the reading pane move at once to the next message without an
        action under way (the one after them, else the one before), so a next key press acts on what the person sees."""
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
            return
        index = model.index(target, 0)
        self.view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.view.scrollTo(index)

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
