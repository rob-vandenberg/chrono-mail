import bisect
import dataclasses
import typing
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Sequence
from typing import cast, override

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QObject, QPersistentModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont, QGuiApplication, QPalette

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import Change, MessageRow
from chrono_mail.engine.commands_combined import MESSAGE_COMBINED_PLACES, VIEWS, CombinedPlacesParameters, CombinedSort, ViewName
from chrono_mail.engine.commands_mail import MESSAGE_PLACES, ListSort, PlacesParameters, PlacesResult
from chrono_mail.engine.commands_view import MESSAGE_COMBINED_ORDER, MESSAGE_ORDER, MESSAGE_ROWS, CombinedOrderParameters, OrderParameters, RowsAnswer, RowsParameters, ViewOrder
from chrono_mail.index.model import GroupCount
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.burst import Burst
from chrono_mail.ui.columns import BY_ID, CARD, SEND_AT_FOLDERS, SEND_AT_LABEL, SNOOZED_COLUMN, SYMBOLS, cell_text, display_name, person_address
from chrono_mail.ui.dragdata import MESSAGES_MIME_TYPE, messages_mime_data
from chrono_mail.ui.icons import grey_icon
from chrono_mail.ui.messageorder import MessageOrder, message_key, split_key
from chrono_mail.ui.models import HERE_ONLY_ROLES
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "messagelist.py 4.1.383"

# --- Version history ----------------------------------------------------
# v4.1.383: hold_arrivals: messages that come in while an Undo runs are placed in one step when it ends; after_arrivals: what waits
#           until they have their places; rows_of_messages: the rows of messages loaded or not, also in a Favorites view (D-266)
# v4.1.381: leave_now takes the rows out per run of neighbouring rows (one removal each froze the window for 17 s with 15,875); took_in
#           once the messages of one moment are placed; arriving_any looks at what waits once
# v4.1.379: unread_place: the next unread message of the whole view, loaded or not, and whether its place is known yet; a view whose
#           order cannot be asked is settled (CR-15)
# v3.6.367: a drag of messages of several accounts names each message's account, so a folder takes them all (D-272)
# v3.6.360: next_row_after: the row a move selects next, or the rows to load first when it has not come yet (D-266)
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) known by their role, no longer by their name (CR-36)
# v3.6.353: leave_now, done_early, come_back: a message whose action needs no server's word leaves the list at its key press and comes back when the action fails (D-266)
# v3.6.352: rows that leave the view go together only when they arrive in the same pass of the event loop, never held back for 100 ms (a held Delete key removed at most ten rows a second, D-266)
# v3.6.350: a selected place whose message was not known yet keeps its place when the rest of the order comes and nothing in the view
#           changed meanwhile (Qt dropped it); otherwise it goes and what waits for it says so (places_lost); a drag started then carries the
#           rest of the selection with the list's places_epoch, its badge counts every selected message (RV-206);
#           the hover texts of rows and group headings show mail text as text, never as markup (D-259); view_number
# v3.5.346: A drag takes every selected message, loaded or not, without those under way; messages of several accounts go to no folder (RV-77)
# v2.5.337: data answers the questions it never answers (icon, tick box, background...) at once, without looking for the row; flags is one of
#           two ready answers (Qt asks every row after each change of the list: 404,000 times in a delete of 4,000)
# v2.4.334: _take_out_runs: the rows that leave together go per run of neighbouring rows (Qt keeps the others' selection cheaply)
# v2.4.334: the messages that leave the view in one moment go in one layout change (16,000 deleted: one row at a time took minutes)
# v2.4.334: placed: the whole order is in; complete tells whether it is (W12b)
# v2.4.329: place_at gives every message's key and folder, loaded or not, and load_rows asks the rows a decision needs (W12); a redraw
#           of many messages is one per run of rows (W13)
# v2.2.314: the column of the Outbox is called Send at too (a delayed send, B-21)
# v2.2.313: arriving: a message that came into the view and waits for its place (Undo selects it once it is placed, B-18)
# v2.0.280: set_column_labels: a column called otherwise in the open kind of folder (Sent is Saved in Drafts, B-16)
# v1.9.274: R-19 round 4: the rows and the facts heard are their real types (MessageRow, AccountSummary, FolderInfo, TagInfo, Change); the fields are read by name, checked by Pyrefly
# v1.9.273: R-19: carried moved to backend.py
# v1.9.270: R-19: the places question typed (carried: the view's fields from its order question)
# v1.6.260: The message list, out of models.py (R-15): one way of holding every kind of view (folder, search, Favorites, Tags): the order of
#           every message of the view (MessageOrder) plus the rows loaded so far, by the message. The order comes in one answer (a large view:
#           its start first); rows are asked by the message, never by a place; the facts heard while a question was on its way are applied
#           again to its answer, so no answer is ever thrown away and asked again; a message that comes in is put before the one the engine
#           names; a new sort finds the selected messages in the new order itself. The count line counts the whole view, everywhere.
#           Earlier history of the message list is in models.py.

PAGE_SIZE: int = 100  # rows asked at once when they come into view
BURST: int = 200  # more messages than this coming in at once (a first sync): the order is asked again once instead of a place each
ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
CONTACT_ROLE: int = Qt.ItemDataRole.UserRole + 11  # (contact id, address book) of the person a row shows, or None
HEADER_ICON_ROLE: int = Qt.ItemDataRole.UserRole + 12  # a header icon that the list header draws in the middle of the column
HEADER_ICONS: dict[str, str] = {"contact": "user", "trash": "edit-delete"}  # columns whose header is a grey icon
GROUP_HEADER_ROLE: int = Qt.ItemDataRole.UserRole + 13  # (name, folded) on a group header row, None on a message row
PENDING_ROLE: int = Qt.ItemDataRole.UserRole + 14  # true on a message with an action under way (drawn greyed)
ANSWERED_ROLES: frozenset[int] = frozenset({
    int(Qt.ItemDataRole.DisplayRole), int(Qt.ItemDataRole.ToolTipRole), int(Qt.ItemDataRole.TextAlignmentRole), int(Qt.ItemDataRole.ForegroundRole),
    int(Qt.ItemDataRole.FontRole), ROW_ROLE, CONTACT_ROLE, GROUP_HEADER_ROLE, PENDING_ROLE,
})  # what data answers; Qt asks more of every cell it draws (icon, tick box, background, size...), and those are None without looking
HEADER_FLAGS: Qt.ItemFlag = Qt.ItemFlag.ItemNeverHasChildren  # not enabled: the arrow keys pass it, it cannot be selected
MESSAGE_FLAGS: Qt.ItemFlag = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren | Qt.ItemFlag.ItemIsDragEnabled  # the list is flat
YES_NO_SORTS: frozenset[str] = frozenset({"unread", "flagged", "attachment", "replied", "contact"})  # the icon columns: no group headers


@dataclass(frozen=True, slots=True)
class _Group:
    """One group header in the list and the messages under it: where it is in the list (display rows) and in the messages."""

    key: str
    name: str
    display_start: int  # the row of the header
    message_start: int  # the position of its first message in the messages of the view
    count: int  # its messages
    shown: int  # of those, the rows under the header: 0 while folded
    folded: bool


LOST_PLACES: str = "Nothing was done: the list changed before it knew every selected message. Select the messages again."




def group_name(sort: str, value: str) -> str:
    """The text of a group header: the date or size group's name, or the sender, recipients or subject as the columns show them."""
    if sort == "sender":
        return display_name(value) or "(unknown)"
    if sort == "to":
        return value or "(no recipients)"
    if sort == "subject":
        return value or "(no subject)"
    if sort == "conversation":
        return "1 message" if value == "1" else f"{value} messages"
    if sort == "preview":
        return value or "(no preview)"
    return value


@dataclass(frozen=True, slots=True)
class ViewSpec:
    """What the message list shows."""

    account: str
    folder: str | None = None  # "Inbox", "Archive/2026"; None means every folder (search only)
    query: str = ""
    sort: str = "date"
    oldest_first: bool = False
    unread: bool | None = None
    flagged: bool | None = None
    has_attachments: bool | None = None
    date_from: str | None = None  # YYYY-MM-DD
    date_to: str | None = None
    minimum_size: int | None = None
    maximum_size: int | None = None
    conversations: bool = False  # only the newest message of each conversation (a plain folder view only)
    combined: str | None = None  # a favorite: the same kind of folder in every mailbox (message.combined)
    combined_accounts: tuple[str, ...] = ()  # ... of these accounts only (a group)
    combined_tag: str | None = None  # the tag of the combined view "tagged" (Tags in the folder list)
    tag: str | None = None  # Filter > Tagged with


def _filters_marks(spec: ViewSpec) -> bool:
    """Whether a view shows only messages with certain marks or a tag (unread, flagged, tagged): what a person changes there."""
    return spec.combined in ("unread", "flagged", "unreplied", "tagged") or spec.unread is not None or spec.flagged is not None or spec.tag is not None


def _groups_of(sort: str, groups: Sequence[GroupCount]) -> list[tuple[str, str, int]]:
    return [(group.key, group_name(sort, group.value), group.count) for group in groups]


def _unread(row: MessageRow) -> bool:
    return "seen" not in row.flags


class MessageListModel(QAbstractTableModel):
    """The messages of one folder, search, Favorites or Tags view (R-15). The list holds the order of every message of the
    view (its key and folder, and whether it is unread) and the rows loaded so far, by the message; rows are asked by the
    message as they come into view. Every change the engine tells is applied to exactly what it changes (D-146)."""

    counts_changed = Signal(int, int)  # total, unread
    groups_changed = Signal()  # the group header rows were laid out anew without a reset (the window spans them all over all columns again)
    header_rows_added = Signal(int, int)  # first and last row of a part of the list where new group headers may have come (only those are spanned)
    load_failed = Signal(str)
    loaded = Signal()
    rearranged = Signal()  # the same messages were put in another order; Qt's selection moved with them
    placed = Signal()  # the whole order of the view is in: every message has its place (a large view comes start first, W12b)
    took_in = Signal()  # the messages that came in at one moment are at their places (once for all of them, not once per row)
    places_lost = Signal(str)  # selected places whose messages were not known yet could not be followed (another sort, a change meanwhile)

    def __init__(self, backend: Backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._spec: ViewSpec | None = None
        self._view: int = 0  # counts the views shown: an answer about another view than the one shown is not used
        self._questions: int = 0  # numbers the questions about the order
        self._ordering: int | None = None  # the question about the order on its way (only the last one asked is used)
        self.settled: bool = True  # the view has its first answer (or shows nothing): an empty list is really empty
        self._order: MessageOrder = MessageOrder()  # every message of the view in order (only the first ones while not complete)
        self._complete: bool = True
        self._changed_while_loading: bool = False  # something in the view changed while its whole order was on its way (RV-206)
        self._start_stamp: str = ""  # the index files' write stamp of the start answer: the whole order's must be the same (RV-206)
        self._lost_to_tell: bool = False  # places were given up in a swap: places_lost goes out after the answer's facts
        self.places_epoch: int = 0  # counts the times selected places not known yet were given up: a drag made before one is refused
        self._total: int = 0  # while not complete: how many messages the view holds
        self._unread_total: int = 0  # ... and how many of them are unread
        self._unread: set[str] = set()  # the keys of the unread messages of the order
        self._rows: dict[str, MessageRow] = {}  # the rows loaded, by key
        self._ids: dict[str, str] = {}  # a Favorites view: message id -> key of the loaded rows (the window names messages by their id)
        self._asked: set[str] = set()  # the keys whose rows are on their way
        self._wanted_pages: set[int] = set()  # parts of the view drawn before its whole order was there: their rows are asked when it is
        self._log: list[Change] = []  # the facts heard while questions are on their way: applied again to each answer
        self._log_start: int = 0  # the number of the first fact in the log
        self._open: dict[int, int] = {}  # a question on its way -> the number of the first fact heard after it was asked
        self._next_question: int = 0
        self._columns: tuple[str, ...] = (CARD, *BY_ID)  # every column: the shown ones first, in their order (R-13)
        self._shown: int = 1  # how many of them are shown; the window hides the others in the header
        self.recipients_first: bool = False  # in Sent, Drafts and Outbox the From / To column shows the recipients
        self._column_labels: dict[str, str] = {}  # columns called otherwise in the open kind of folder (Sent is Saved in Drafts)
        self.date_style: str = "short"
        self.tag_book: TagColourBook = TagColourBook()  # the colours of the tags (from tag.list)
        self.tag_text: bool = False  # Settings > Message list: the text of a tagged message in the colour of its first tag
        self.contacts: dict[str, tuple[str, str]] = {}  # address (lower case) -> (contact id, address book)
        self.show_groups: bool = True
        self._group_counts: list[tuple[str, str, int]] | None = None  # key, name, count of each group; None: no headers
        self._groups: list[_Group] = []
        self._group_starts: list[int] = []  # the row of each header, for finding the group of a row quickly
        self._display_rows: int = 0
        self.pending: set[str] = set()  # message ids with an action under way: greyed, and no second action (D-146)
        self._stay: set[str] = set()  # keys of messages read, unflagged or untagged while this view showed them: they stay until it is left
        self._leaving: list[Change] = []  # messages that left the view in this moment: their rows go together (16,000 one by one took minutes)
        self._left_early: dict[str, tuple[str, MessageRow]] = {}  # message id -> its key and row: taken out at the key press, its action not done yet (D-266)
        self.role_of: Callable[[str, str], str] = lambda account, folder: "other"  # the role of an account's folder (a row brought back is placed by it)
        self._leave_soon: Burst = Burst(self._flush_leaving, self, interval_ms=0)  # those that arrive in the same pass of the event loop go together; none waits (D-266)
        self._arriving: dict[str, MessageRow] = {}  # messages that came in, by key -> their row (kept current by later facts), placed together
        self._arrival_planned: bool = False
        self._arrivals_held: int = 0  # while above 0 (an Undo runs) messages that come in are gathered, not placed: placed in one step after
        self._after_arrivals: list[Callable[[], None]] = []  # what waits until every message that came in has its place (after_arrivals)
        self._placing: dict[str, MessageRow] | None = None  # the messages whose places are being asked (one question at a time)
        self._seen: dict[str, int] = {}  # per mailbox: the number of the last fact heard (the engine counts only what came before it, D-152)
        self.selected_rows: Callable[[], list[int]] = lambda: []  # the rows selected in the window and its current row (a new order keeps them)
        self.shown_rows: Callable[[], list[int]] = lambda: []  # the rows on screen (a view fetched again brings their rows with it)
        self._folded: dict[tuple[str, str | None, str], set[str]] = {}  # per folder (or combined view) and sort order: the folded groups, until the program closes

    # --- the columns --------------------------------------------------------------------------

    @property
    def columns(self) -> tuple[str, ...]:
        """The columns shown, in their order (they are the model's first columns)."""
        return self._columns[:self._shown]

    def set_columns(self, columns: tuple[str, ...]) -> None:
        """Show these columns (ids from ui.columns), or the single card column. Every column stays in the model: the chosen ones
        are moved to the front in their order, the others stay behind them, hidden by the window. Nothing is reset, so the rows,
        the selection, the current message and the scroll stay (R-13)."""
        wanted = tuple(column_id for column_id in dict.fromkeys(columns) if column_id in self._columns)
        if wanted == self.columns or not wanted:
            return
        for position, column_id in enumerate(wanted):
            self._move(self._columns.index(column_id), position)
        self._shown = len(wanted)
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self._columns) - 1)

    def set_column_labels(self, labels: dict[str, str]) -> None:
        """Columns that the open kind of folder calls otherwise (Sent is Saved in Drafts)."""
        if labels != self._column_labels:
            self._column_labels = dict(labels)
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, max(0, len(self._columns) - 1))

    def move_column(self, source: int, destination: int) -> None:
        """Move one shown column to another place among the shown ones; the rows, the selection and the widths stay (D-179)."""
        if not 0 <= source < self._shown or not 0 <= destination < self._shown:
            return
        self._move(source, destination)

    def _move(self, source: int, destination: int) -> None:
        if source == destination:
            return
        self.beginMoveColumns(QModelIndex(), source, source, QModelIndex(), destination + 1 if destination > source else destination)
        columns = list(self._columns)
        columns.insert(destination, columns.pop(source))
        self._columns = tuple(columns)
        self.endMoveColumns()

    def refresh_cells(self) -> None:
        """What the cells show depends on settings (the date style, recipients): draw them again."""
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, max(0, self.columnCount() - 1)))

    # --- who a message is ---------------------------------------------------------------------

    def _combined(self) -> bool:
        return self._spec is not None and self._spec.combined is not None

    def _key(self, mailbox: str, message_id: str) -> str:
        """The key of a message in this view: its id; in a Favorites view, where ids are unique only within one mailbox, its
        mailbox and its id."""
        return message_key(mailbox if self._combined() else "", message_id)

    def _row_key(self, row: MessageRow) -> str:
        return self._key(row.account or "", row.message_id)

    def _held(self, row: MessageRow, mailbox: str) -> MessageRow:
        """A row as this view holds it: in a Favorites view it says its mailbox, in a view of one mailbox it says none."""
        account = mailbox if self._combined() else ""
        return row if (row.account or "") == account else dataclasses.replace(row, account=account)

    def _loaded_key(self, message_id: str) -> str | None:
        """The key of a message the window names by its id: in a view of one mailbox the id itself (when the view holds it),
        in a Favorites view the key of its loaded row."""
        if self._combined():
            return self._ids.get(message_id)
        return message_id if message_id in self._order else None

    def _store(self, key: str, row: MessageRow) -> None:
        self._rows[key] = row
        if self._combined():
            self._ids[row.message_id] = key

    def _forget(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row is not None and self._combined():
            self._ids.pop(row.message_id, None)

    # --- the view -----------------------------------------------------------------------------

    def _hold_nothing(self) -> None:
        """Forget every message held (call between the begin and end of a reset)."""
        self.places_epoch += 1  # selected places not known yet are gone with them: a drag made before is refused (RV-206)
        self._changed_while_loading = False
        self._order, self._complete, self._total, self._unread_total = MessageOrder(), True, 0, 0
        self._unread.clear()
        self._rows.clear()
        self._ids.clear()
        self._leaving.clear()
        self._left_early.clear()  # the new order comes from the engine: it holds every message whose action was not done
        self._asked.clear()
        self._wanted_pages.clear()

    @property
    def complete(self) -> bool:
        """Whether every message of the view has its place yet (the rest of a large view's order may still be on its way)."""
        return self._complete

    @property
    def spec(self) -> ViewSpec | None:
        return self._spec

    @property
    def view_number(self) -> int:
        """Which view is shown: it changes only when something else is shown (another folder, a filter), never by a sort, a reload or a
        rename of the folder shown."""
        return self._view

    def show(self, spec: ViewSpec | None) -> None:
        """Start showing something else (or nothing). The order is asked (for a large view, its start first)."""
        self.pending.clear()
        self._stay.clear()  # the view is left: what was changed in it no longer stays
        self._arriving.clear()
        self._placing = None
        self.settled = spec is None or (self.settled and spec == self._spec and self._display_rows == 0)  # an empty view reloaded stays settled; one with messages waits for them
        self._changed_while_loading = False
        self.beginResetModel()
        self._view += 1
        self._spec = spec
        self._hold_nothing()
        self._group_counts = None
        self._layout_groups()
        self.endResetModel()
        if spec is None:
            self.counts_changed.emit(0, 0)
            self._arrivals_done()
            return
        self._ask_order(spec, whole=False, rows_of=[], purpose="show")

    def reload(self) -> None:
        """Fetch the same view again (after a change from outside: new mail, another program, a big job). The rows on screen stay
        until the new order and their new rows are there; each selected message keeps its selection."""
        self.rearrange(self._spec, fresh=True)

    def rearrange(self, spec: ViewSpec | None, fresh: bool = False) -> None:
        """Show the same view in another order (a new sort), or the same order fetched again (fresh). Nothing is thrown away: the
        rows on screen stay until the new order has arrived; then the list changes in one step and tells Qt where each message
        went, so Qt's own selection moves with its message. It is never cleared, and never set again by hand."""
        current = self._spec
        if spec is None or current is None or self._display_rows == 0 or not self._same_messages(current, spec):
            self.show(spec)  # nothing on screen to keep, or other messages
            return
        kept = self._kept_keys()
        wanted = kept + [key for key in self._shown_keys() if key not in kept] if fresh else [key for key in kept if key not in self._rows]
        self._ask_order(spec, whole=True, rows_of=wanted, purpose="reload" if fresh else "rearrange")

    @staticmethod
    def _same_messages(old: ViewSpec, new: ViewSpec) -> bool:
        """Whether two views hold the same messages (only the order may differ)."""
        return dataclasses.replace(old, sort=new.sort, oldest_first=new.oldest_first) == new

    def _key_at_row(self, row: int) -> str | None:
        position = self.message_position(row)
        return None if position is None else self._order.key_at(position)

    def place_at(self, row: int) -> tuple[str, str, MessageRow | None] | None:
        """The message at this row by its key and folder, with its row when it is loaded: every message of the view has them, loaded or
        not, so an action on a selection larger than what came into view acts on all of it (W12). None on a group header."""
        position = self.message_position(row)
        key = None if position is None else self._order.key_at(position)
        if position is None or key is None:
            return None
        return key, self._order.folder_at(position), self._rows.get(key)

    def next_row_after(self, last: int) -> int | list[str] | None:
        """The row a move selects when the rows down to this one leave (D-266): the first message below without an action under way; None at
        the end of the view; or, when the first message below has not come yet, the keys of its part of the view still to load (empty while
        its place is not known yet), since a row is never selected before it is drawn."""
        for row in range(last + 1, self._display_rows):
            if self.is_header(row):
                continue
            place = self.place_at(row)
            if place is None:
                return []
            key, _, found = place
            if found is None:
                start = self._order.position(key)  # the next 100 from it (places shift up as messages leave: never a page that is half loaded)
                return [each for each in self._order.keys(start, start + PAGE_SIZE) if each not in self._rows]
            if found.message_id not in self.pending:
                return row
        return None

    def unread_place(self, start: int, step: int) -> tuple[int | None, bool]:
        """The place of the first unread message of the view from this place on (start included), going down (step 1) or up (-1), loaded
        or not (the order knows which are unread, D-218), shown (not in a folded group); with whether the answer is sure: not while the
        places it may be among are still on their way (a large view's order comes start first)."""
        known = len(self._order)
        if step < 0 and start >= known and not self._complete and self.loading:
            return None, False  # the last ones are on their way (when the rest could not be read, the places known are all there is)
        folded: list[tuple[int, int]] = [(group.message_start, group.message_start + group.count) for group in self._groups if group.folded] if self._group_counts is not None else []
        starts = [first for first, _ in folded]
        covered = sum(group.count for group in self._groups) if self._group_counts is not None else known  # past the groups nothing is shown
        for position, key in self._order.walk(max(0, start) if step > 0 else min(start, known - 1), step):
            if key not in self._unread or position >= covered:
                continue
            found = bisect.bisect_right(starts, position) - 1
            if found < 0 or position >= folded[found][1]:  # not in a folded group
                return position, True
        return None, self._complete or step < 0 or not self.loading  # the rest could not be read: the places known are all there is

    def loaded_row(self, key: str) -> MessageRow | None:
        return self._rows.get(key)

    def load_rows(self, keys: Sequence[str], then: Callable[[], None]) -> None:
        """Ask the rows of these messages that are not loaded yet, then call then (also when a question failed: what came is loaded)."""
        spec = self._spec
        missing = [key for key in keys if key not in self._rows]
        if spec is None or not missing:
            then()
            return
        per_mailbox: dict[str, list[str]] = {}
        for key in missing:
            mailbox, message_id = split_key(key)
            per_mailbox.setdefault(mailbox or spec.account, []).append(message_id)
        remaining = len(per_mailbox)

        def one_answered() -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                then()

        self._asked.update(missing)
        for mailbox, message_ids in per_mailbox.items():
            self._ask_rows(spec, mailbox, message_ids, one_answered)

    def _kept_keys(self) -> list[str]:
        """The messages that keep their place in Qt's selection (the selected ones and the current one, as the window tells)."""
        return list(dict.fromkeys(key for key in (self._key_at_row(row) for row in self.selected_rows()) if key is not None))

    def _shown_keys(self) -> list[str]:
        return [key for key in (self._key_at_row(row) for row in self.shown_rows()) if key is not None]

    # --- questions and the facts heard while they are on their way ----------------------------

    def _ask(self) -> int:
        """A question goes out: the facts heard from now on are kept, to be applied again to its answer."""
        self._next_question += 1
        self._open[self._next_question] = self._log_start + len(self._log)
        return self._next_question

    def _answered(self, question: int) -> list[Change]:
        self._flush_leaving()  # the list as it has heard every fact before the answer is laid over it
        return self._facts_since(question)

    def _facts_since(self, question: int) -> list[Change]:
        """A question was answered (or failed): the facts heard since it was asked. The engine read its answer after it told
        every change the answer counts, and Qt delivers the facts and the answer in the order they were sent, so every change
        the answer counts was heard before it; applying again the facts heard since the question was asked brings the answer
        exactly to what the list has heard (a fact the answer counts already changes nothing when applied again)."""
        first = self._open.pop(question, None)
        facts = self._log[first - self._log_start:] if first is not None else []
        keep = min(self._open.values(), default=self._log_start + len(self._log))
        del self._log[:keep - self._log_start]
        self._log_start = keep
        return facts

    def _order_parameters(self, spec: ViewSpec, whole: bool, rows_of: Sequence[str]) -> OrderParameters | CombinedOrderParameters:
        """The question about the order of this view: what it holds, the rows of the start and of the messages named."""
        headings = self.show_groups and spec.sort not in YES_NO_SORTS
        stay: list[str] = sorted(self._stay) if _filters_marks(spec) else []
        if spec.combined is not None:
            if spec.combined not in VIEWS or spec.sort not in typing.get_args(CombinedSort):
                raise ChronoMailError(f"A Favorites view {spec.combined} sorted by {spec.sort} does not exist")
            named = [split_key(key) for key in rows_of]
            kept = [split_key(key) for key in stay]
            return CombinedOrderParameters(
                view=typing.cast(ViewName, spec.combined), tag=spec.combined_tag or "", accounts=spec.combined_accounts, query=spec.query, unread=spec.unread,
                flagged=spec.flagged, has_attachments=spec.has_attachments, filter_tag=spec.tag, date_from=spec.date_from, date_to=spec.date_to,
                minimum_size=spec.minimum_size, maximum_size=spec.maximum_size, sort=typing.cast(CombinedSort, spec.sort), oldest_first=spec.oldest_first,
                stay_messages=tuple(message for _, message in kept), stay_mailboxes=tuple(mailbox for mailbox, _ in kept),
                whole=whole, first=PAGE_SIZE, rows_of=tuple(message for _, message in named), rows_of_mailboxes=tuple(mailbox for mailbox, _ in named), headings=headings,
            )
        if spec.sort not in typing.get_args(ListSort):
            raise ChronoMailError(f"A list sorted by {spec.sort} does not exist")
        return OrderParameters(
            account=spec.account, query=spec.query, folder=spec.folder, unread=spec.unread, flagged=spec.flagged, has_attachments=spec.has_attachments,
            date_from=spec.date_from, date_to=spec.date_to, minimum_size=spec.minimum_size, maximum_size=spec.maximum_size, tag=spec.tag,
            conversations=spec.conversations, sort=typing.cast(ListSort, spec.sort), oldest_first=spec.oldest_first, stay=tuple(stay),
            whole=whole, first=PAGE_SIZE, rows_of=tuple(rows_of), headings=headings,
        )

    def _ask_order(self, spec: ViewSpec, whole: bool, rows_of: Sequence[str], purpose: str) -> None:
        """Ask the order of the view: purpose show (the first answer of a view shown), complete (the whole order after its
        start), rearrange (a new sort: the rows loaded stay) or reload (fetched again: the rows come again)."""
        try:
            parameters = self._order_parameters(spec, whole, rows_of)
        except ChronoMailError as error:
            self._failed(error)
            self.settled = True  # nothing more comes for this view: what waits for its answer is not kept waiting
            self._arrivals_done()
            return
        question, view = self._ask(), self._view
        self._questions += 1
        number = self._ordering = self._questions

        def answered(order: ViewOrder) -> None:
            facts = self._answered(question)
            if view != self._view or number != self._ordering:
                return  # another view, or another order was asked for meanwhile
            self._ordering = None
            self._take_order(spec, order, facts, purpose)
            self._arrivals_done()

        def failed(error: ChronoMailError) -> None:
            self._answered(question)
            if view != self._view or number != self._ordering:
                return
            self._ordering = None
            self._failed(error)
            if not self._complete:  # what waits for the rest of the order would wait for ever: it is not done, and that is said (RV-206)
                self.places_epoch += 1
                self.places_lost.emit(f"Nothing was done: the list could not be read to its end ({error})")
            self.settled = True
            if purpose == "rearrange":
                self.show(spec)  # the new order without the rows kept: the error is shown
            self._arrivals_done()

        if isinstance(parameters, CombinedOrderParameters):
            self._backend.ask(MESSAGE_COMBINED_ORDER, parameters, answered, failed, owner=self)
        else:
            self._backend.ask(MESSAGE_ORDER, parameters, answered, failed, owner=self)

    def _take_order(self, spec: ViewSpec, order: ViewOrder, facts: list[Change], purpose: str) -> None:
        """An order arrived: it takes the place of what the list held, then the facts heard since it was asked are applied to it."""
        keys = [message_key(mailbox, message_id) for mailbox, message_id in zip(order.mailboxes, order.messages, strict=True)] if order.mailboxes else list(order.messages)
        if purpose == "show":
            self._start_stamp = order.stamp
            self.beginResetModel()  # the list is empty: there is nothing on it to keep
            self._set_order(spec, order, keys, keep_rows=False)
            self.endResetModel()
            self.groups_changed.emit()
        else:
            self._swap_in(spec, order, keys, keep_rows=purpose != "reload", completed=purpose == "complete")
        if not self._complete and any(self._touches_view(fact) for fact in facts):
            self._changed_while_loading = True  # heard while the start was on its way: the start may or may not count it
        for fact in facts:
            self._follow(fact)
        if self._lost_to_tell:
            self._lost_to_tell = False
            self.places_lost.emit(LOST_PLACES)  # after the facts: an action known by key that runs now sees the list as it is
        self._counts_changed()
        self.settled = True
        if purpose in ("rearrange", "reload"):
            self.rearranged.emit()
        self.loaded.emit()
        if not self._complete:
            self._ask_order(spec, whole=True, rows_of=[], purpose="complete")
            return
        self.placed.emit()
        wanted, self._wanted_pages = sorted(self._wanted_pages), set()
        for page in wanted:
            self._fetch(page * PAGE_SIZE)
        if self._arriving:
            self._take_in()  # what came in while the order was on its way

    def _set_order(self, spec: ViewSpec, order: ViewOrder, keys: list[str], keep_rows: bool) -> None:
        """The state of an order answer (call between the begin and end of a model change)."""
        self._spec = spec
        self._order = MessageOrder(keys, order.folders)
        self._complete = order.complete
        self._total, self._unread_total = order.total, order.unread_total
        self._unread = {key for key, unread in zip(keys, order.unread, strict=True) if unread}
        if not keep_rows:
            self._rows.clear()
            self._ids.clear()
        mailbox = spec.account
        for row in order.rows:
            held = self._held(row, row.account or mailbox)
            self._store(self._row_key(held), held)
        self._group_counts = _groups_of(spec.sort, order.groups) if self.show_groups and spec.sort not in YES_NO_SORTS else None
        self._layout_groups()

    def _swap_in(self, spec: ViewSpec, order: ViewOrder, keys: list[str], keep_rows: bool, completed: bool = False) -> None:
        """The new order replaces the old one in one layout change. Every index Qt holds (its selection, the current message) is
        moved to the row its message has now; one whose message is not in the view any more goes. A place whose message was not known
        yet keeps its place when the rest of the order came and nothing in the view changed meanwhile: the place then holds exactly the
        message the person selected (Ctrl+A or a Shift range right after the view opened, RV-206). Otherwise which message it was cannot be
        told: such places go, and an action or a drop waiting for them is not done, and says so (places_lost)."""
        self.layoutAboutToBeChanged.emit()
        held = self.persistentIndexList()
        held_places: list[tuple[str | None, int | None]] = []
        for index in held:
            position = self.message_position(index.row()) if index.isValid() else None
            held_places.append((None if position is None else self._order.key_at(position), position))
        had_unknown = self._available() > len(self._order)  # before the new order: places not known yet were there
        start: list[str | None] = [self._order.key_at(position) for position in range(len(self._order))] if had_unknown and completed else []
        same_places = completed and not self._changed_while_loading and (  # also a big job that tells nothing until its end (rules, an import)
            order.total == self._available() and keys[: len(start)] == start and order.stamp == self._start_stamp  # leaves its mark: another
        )  # total, another start, or another write stamp of the index (no write told only later between the two answers)
        self._set_order(spec, order, keys, keep_rows)
        moved: list[QModelIndex] = []
        dropped = False
        for index, (key, position) in zip(held, held_places, strict=True):
            if key is not None:
                shown = self.display_row(self._order.position(key)) if key in self._order else -1
            elif position is not None:  # a place whose message was not known yet
                shown = self.display_row(position) if same_places and position < len(self._order) else -1
                dropped = dropped or shown < 0
            else:
                shown = -1
            moved.append(self.index(shown, index.column()) if 0 <= shown < self._display_rows else QModelIndex())
        self.changePersistentIndexList(held, moved)
        self.layoutChanged.emit()
        self.groups_changed.emit()
        if self._complete:
            self._changed_while_loading = False
        if dropped or (had_unknown and not same_places):  # also when nothing on screen sits on such a place: what waits keeps them (RV-206)
            self.places_epoch += 1
            self._lost_to_tell = True  # told once the facts heard meanwhile are applied (_take_order): what waits then sees the list as it is

    def _touches_view(self, change: Change) -> bool:
        """Whether a fact may change which messages the view holds or their order (a fact of another folder of a folder's view does not, nor a
        change of marks or tags where the view neither filters nor sorts by them: a message read while the view loads changes nothing)."""
        spec = self._spec
        if spec is None:
            return False
        if str(change.kind) == "mailbox_changed":
            return True  # a big job ended: anything in the mailbox may have changed
        if str(change.kind) in ("flags", "tags", "folder_read") and not (  # also a Favorites view: Unread, Flagged and Tags are (a changed message
            _filters_marks(spec) or spec.tag or spec.query or spec.sort in YES_NO_SORTS or spec.sort == "tags"  # in _filters_marks; a search
        ):  # may name tags (tag:) or words a change of the message touches; may have another subject, date or size: always counts)
            return False
        return spec.combined is not None or spec.folder is None or change.folder == spec.folder or change.to_folder == spec.folder

    @property
    def loading(self) -> bool:
        """Whether an answer about the order is on its way (after a failed one the rest of a large view's order never comes by itself)."""
        return self._ordering is not None

    def folder_of(self, key: str) -> str | None:
        """The folder of a message of the view now; None when the view does not hold it."""
        return self._order.folder_of(key) if key in self._order else None

    def holds(self, key: str) -> bool:
        """Whether the view holds this message now."""
        return key in self._order

    def places_at(self, positions: Iterable[int]) -> list[tuple[str, str]]:
        """The key and folder of the messages at these places of the view (those it knows)."""
        found: list[tuple[str, str]] = []
        for position in positions:
            key = self._order.key_at(position)
            if key is not None:
                found.append((key, self._order.folder_at(position)))
        return found

    # --- group headers ------------------------------------------------------------------------

    def _fold_key(self) -> tuple[str, str | None, str]:
        spec = self._spec
        if spec is None:
            return ("", None, "")
        return ((spec.combined or spec.account) + (f":{spec.combined_tag}" if spec.combined_tag and spec.combined else ""), spec.folder if spec.combined is None else ",".join(spec.combined_accounts), spec.sort)

    def _available(self) -> int:
        """How many messages the view holds (also those whose place is not known yet while its whole order is on its way)."""
        return len(self._order) if self._complete else max(self._total, len(self._order))

    def _layout_groups(self) -> None:
        """Work out where the headers go and which rows they hide (call between begin and end of a model change)."""
        self._groups, self._group_starts, self._display_rows = self._computed_layout()

    def _computed_layout(self) -> tuple[list[_Group], list[int], int]:
        """The headers, their rows and the number of rows, for the rows and groups as they are now (nothing is changed)."""
        available = self._available()
        groups: list[_Group] = []
        starts: list[int] = []
        if self._group_counts is None:
            return groups, starts, available
        folded = self._folded.get(self._fold_key(), set())
        display = message = 0
        last = len(self._group_counts) - 1
        for number, (key, name, count) in enumerate(self._group_counts):
            if message >= available:
                break
            known = available - message if number == last else min(count, available - message)  # a message the counts did not know joins the last group
            if known <= 0:
                continue
            closed = key in folded
            shown = 0 if closed else known
            groups.append(_Group(key, name, display, message, known, shown, closed))
            starts.append(display)
            display += 1 + shown
            message += known
        return groups, starts, display

    def _group_at(self, row: int) -> _Group | None:
        if not self._groups or row < 0 or row >= self._display_rows:
            return None
        found = bisect.bisect_right(self._group_starts, row) - 1
        return self._groups[found] if found >= 0 else None

    def message_position(self, row: int) -> int | None:
        """The position among the messages of the view of the message at this row; None on a group header."""
        if self._group_counts is None:
            return row if 0 <= row < self._display_rows else None
        group = self._group_at(row)
        if group is None or row == group.display_start:
            return None
        return group.message_start + row - group.display_start - 1

    def message_count(self) -> int:
        """How many messages the view holds; group headers not counted."""
        return self._available()

    def display_row(self, position: int) -> int:
        """The row of the message at this position among the messages of the view; -1 while its group is folded."""
        if self._group_counts is None:
            return position if 0 <= position < self._display_rows else -1
        for group in self._groups:
            if group.message_start <= position < group.message_start + group.count:
                return group.display_start + 1 + position - group.message_start if not group.folded else -1
        return -1

    def is_header(self, row: int) -> bool:
        group = self._group_at(row)
        return group is not None and group.display_start == row

    def header_rows(self) -> list[int]:
        return list(self._group_starts)

    def toggle_group(self, row: int) -> None:
        """Fold the group of this header row, or open it again."""
        group = self._group_at(row)
        if group is None or group.display_start != row:
            return
        folded = self._folded.setdefault(self._fold_key(), set())
        if group.folded:
            folded.discard(group.key)
            self.beginInsertRows(QModelIndex(), row + 1, row + group.count)
            self._layout_groups()
            self.endInsertRows()
        else:
            folded.add(group.key)
            self.beginRemoveRows(QModelIndex(), row + 1, row + group.shown)
            self._layout_groups()
            self.endRemoveRows()
        self.dataChanged.emit(self.index(row, 0), self.index(row, max(0, self.columnCount() - 1)))  # the rows under it came or went; the header keeps its span

    def _group_index(self, position: int) -> int:
        """The group (in the full group counts) of the message at this position."""
        start = 0
        counts = self._group_counts or []
        for number, (_, _, count) in enumerate(counts):
            if position < start + count:
                return number
            start += count
        return len(counts) - 1

    # --- Qt model interface -------------------------------------------------------------------

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._display_rows

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(self._columns) or self._columns[section] not in BY_ID:
            return None
        column = BY_ID[self._columns[section]]
        label = SEND_AT_LABEL if column.column_id == SNOOZED_COLUMN and self._spec is not None and self._spec.folder in SEND_AT_FOLDERS else self._column_labels.get(column.column_id, column.label)
        if role == Qt.ItemDataRole.DisplayRole:
            return SYMBOLS.get(column.column_id, label)
        if role == Qt.ItemDataRole.ToolTipRole:
            return label
        if role == HEADER_ICON_ROLE and column.column_id in HEADER_ICONS:
            return grey_icon(HEADER_ICONS[column.column_id])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter) if column.symbol else int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if column.right else Qt.AlignmentFlag.AlignLeft))
        return None

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or role not in ANSWERED_ROLES:
            return None
        group = self._group_at(index.row())
        if group is not None and group.display_start == index.row():
            if role == GROUP_HEADER_ROLE:
                return (group.name, group.folded)
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return (group.name if role == Qt.ItemDataRole.DisplayRole else plain_tip(group.name)) if index.column() == 0 else None
            return None
        if role == GROUP_HEADER_ROLE:
            return None
        if role == ROW_ROLE:
            return self.row_at(index.row(), fetch=True)
        column_id = self._columns[index.column()] if 0 <= index.column() < len(self._columns) else CARD
        if column_id == CARD or column_id not in BY_ID:
            return None
        row = self.row_at(index.row(), fetch=True)
        column = BY_ID[column_id]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter) if column.symbol else int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if column.right else Qt.AlignmentFlag.AlignLeft))
        if row is None:
            return "…" if role == Qt.ItemDataRole.DisplayRole and index.column() == 0 else None
        if role == CONTACT_ROLE:
            return self.contacts.get(person_address(row, self.recipients_first))
        if role == PENDING_ROLE:
            return row.message_id in self.pending
        if role == Qt.ItemDataRole.ForegroundRole and row.message_id in self.pending:
            return QBrush(QGuiApplication.palette().color(QPalette.ColorRole.PlaceholderText))  # an action is under way: greyed
        if role == Qt.ItemDataRole.DisplayRole and column_id == "tag":
            return ""  # the delegate draws the tags; the tooltip names them
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            text = cell_text(column_id, row, self.recipients_first, self.date_style)
            return text if role == Qt.ItemDataRole.DisplayRole else plain_tip(text)  # a subject or a sender: never markup (D-259)
        tags: tuple[str, ...] = row.tags
        if role == Qt.ItemDataRole.ForegroundRole and self.tag_text and tags and not column.symbol:
            return QBrush(self.tag_book.colour(tags[0]))
        if role == Qt.ItemDataRole.ForegroundRole and column_id == "status":
            palette = QGuiApplication.palette()
            return QBrush(palette.color(QPalette.ColorRole.Highlight if _unread(row) else QPalette.ColorRole.PlaceholderText))  # blue dot: unread; faint circle: read
        if role == Qt.ItemDataRole.FontRole and _unread(row):
            font = QFont()
            font.setBold(True)
            return font
        return None

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        return MESSAGE_FLAGS if index.isValid() and not self.is_header(index.row()) else HEADER_FLAGS

    @override
    def mimeTypes(self) -> list[str]:
        return [MESSAGES_MIME_TYPE]

    @override
    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        """The messages being dragged: where they are, never their content."""
        return self.drag_data(sorted({index.row() for index in indexes}))[0]

    def drag_data(self, list_rows: Iterable[int]) -> tuple[QMimeData, int]:
        """The drag of the messages at these rows, every one of them, loaded or not (D-218), without those with an action under way, and how
        many go along (the badge). Messages of several accounts (a Favorites view) name each one's account: a folder of any account takes
        them all (D-272). Rows whose message is not known yet (the rest of a large view's order is
        on its way) make the drag say so: the drop waits for them and moves every selected message (RV-206)."""
        default = self._spec.account if self._spec is not None else ""
        messages: list[tuple[str, str]] = []
        sources: list[str] = []
        accounts: set[str] = set()
        unknown = 0
        for list_row in list_rows:
            place = self.place_at(list_row)
            if place is None:
                if not self.is_header(list_row) and self.message_position(list_row) is not None:
                    unknown += 1  # its message is not known yet: it goes along once it is
                continue
            key, folder, _row = place
            mailbox, message_id = split_key(key)
            if message_id in self.pending:
                continue
            accounts.add(mailbox or default)
            sources.append(mailbox or default)
            messages.append((folder, message_id))
        if unknown and not accounts and default:
            accounts.add(default)  # a view of one account: the messages not known yet are of that account too
        several = len(accounts) > 1
        account = "" if several else next(iter(accounts), "")  # several accounts: each message says its own (sources, D-272)
        one_folder = self._spec.folder if self._spec is not None and self._spec.combined is None else None
        return messages_mime_data(
            account, messages, rest=self.places_epoch if unknown else None, rest_folder=one_folder, sources=sources if several else None,
        ), len(messages) + unknown

    def unknown_positions(self, list_rows: Iterable[int]) -> list[int]:
        """The places in the view of the messages at these rows that the list does not know yet (the rest of a large view's order is on its
        way); group headers are no messages."""
        positions: list[int] = []
        for list_row in list_rows:
            position = self.message_position(list_row)
            if position is not None and self._order.key_at(position) is None:
                positions.append(position)
        return positions

    # --- the rows -----------------------------------------------------------------------------

    def row_at(self, row: int, fetch: bool = False) -> MessageRow | None:
        """The message at this row of the list, or None on a group header or while its row is still on its way (fetch: ask it)."""
        position = self.message_position(row)
        if position is None:
            return None
        key = self._order.key_at(position)
        if key is None:
            if fetch and not self._complete:
                self._wanted_pages.add(position // PAGE_SIZE)  # asked once the whole order says which messages are there
            return None
        found = self._rows.get(key)
        if found is None and fetch:
            self._fetch(position)
        return found

    def _fetch(self, position: int) -> None:
        """Ask the rows of the part of the view around this place that are not loaded yet, by the message (never by a place,
        so a row that moved meanwhile still gets its own row)."""
        spec = self._spec
        if spec is None:
            return
        page = position // PAGE_SIZE
        missing = [key for key in self._order.keys(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) if key not in self._rows and key not in self._asked]
        if not missing:
            return
        self._asked.update(missing)
        per_mailbox: dict[str, list[str]] = {}
        for key in missing:
            mailbox, message_id = split_key(key)
            per_mailbox.setdefault(mailbox or spec.account, []).append(message_id)
        for mailbox, message_ids in per_mailbox.items():
            self._ask_rows(spec, mailbox, message_ids)

    def _ask_rows(self, spec: ViewSpec, mailbox: str, message_ids: list[str], then: Callable[[], None] | None = None) -> None:
        question, view, combined = self._ask(), self._view, spec.combined is not None
        asked = [self._key(mailbox, message_id) for message_id in message_ids]

        def answered(answer: RowsAnswer) -> None:
            try:
                facts = self._answered(question)
                if view != self._view:
                    return
                self._asked.difference_update(asked)
                changed: list[int] = []
                for row in answer.rows:
                    held = self._held(row, mailbox)
                    key = self._row_key(held)
                    if key not in self._order:
                        continue  # it left the view meanwhile
                    for fact in facts:  # what changed it since it was asked (or changes nothing when the answer counts it)
                        held = self._followed_row(held, fact)
                    self._store(key, held)
                    changed.append(self._order.position(key))
                self._redraw_positions(changed)
                self.loaded.emit()
            finally:
                if then is not None:
                    then()  # always: an action waiting for these rows is never left waiting for ever (Undo waits for it)

        def failed(error: ChronoMailError) -> None:
            self._answered(question)
            if view == self._view:
                self._asked.difference_update(asked)
                self._failed(error)
            if then is not None:
                then()

        self._backend.ask(MESSAGE_ROWS, RowsParameters(mailbox, tuple(message_ids), combined), answered, failed, owner=self)

    def _redraw_positions(self, positions: Iterable[int]) -> None:
        shown = [row for row in (self.display_row(position) for position in positions if position >= 0) if row >= 0]
        if shown:
            self.dataChanged.emit(self.index(min(shown), 0), self.index(max(shown), max(0, self.columnCount() - 1)))  # every column: each cell is drawn on its own

    def _redraw_key(self, key: str) -> None:
        position = self._order.position(key)
        if position >= 0:
            self._redraw_positions([position])

    def _replace(self, key: str, row: MessageRow) -> None:
        """A loaded row (or one that waits to be placed) shows what it is now."""
        if key in self._rows:
            self._store(key, row)
            self._redraw_key(key)
        for waiting in self._waiting():
            if key in waiting:
                waiting[key] = row

    def _waiting(self) -> list[dict[str, MessageRow]]:
        """The messages that came in and wait to be placed: those not asked about yet, and those whose places are being asked."""
        return [self._arriving] if self._placing is None else [self._arriving, self._placing]

    def _row_of(self, key: str) -> MessageRow | None:
        """The row of a message loaded or waiting to be placed (None when it is neither)."""
        found = self._rows.get(key)
        if found is None:
            found = self._arriving.get(key)
        if found is None and self._placing is not None:
            found = self._placing.get(key)
        return found

    def replace_flags(self, message_id: str, flags: tuple[str, ...]) -> None:
        """Show new marks on a message without fetching anything (after a change of marks)."""
        key = self._loaded_key(message_id)
        if key is not None:
            self._set_flags(key, flags)
            self._counts_changed()

    def replace_tags(self, message_id: str, tags: tuple[str, ...]) -> None:
        """Show new tags on a loaded row without fetching anything (after message.tag)."""
        key = self._loaded_key(message_id)
        row = self._row_of(key) if key is not None else None
        if key is not None and row is not None:
            self._replace(key, dataclasses.replace(row, tags=tags))

    def arriving(self, message_id: str) -> bool:
        """Whether a message came into the view and waits for the engine to say where it goes (it is in the list once placed)."""
        return any(row.message_id == message_id for waiting in self._waiting() for row in waiting.values())

    def arriving_any(self, message_ids: Iterable[str]) -> bool:
        """Whether any of these messages waits for its place: one look at what waits, not one per message."""
        waiting = {row.message_id for rows in self._waiting() for row in rows.values()}
        return bool(waiting) and any(message_id in waiting for message_id in message_ids)

    def rows_of_messages(self, message_ids: Iterable[str]) -> list[tuple[int, str]]:
        """The rows of these messages in the list (row, message id), those it shows: loaded or not, also in a Favorites view (one walk
        over its order; a message's key there is its mailbox and its id)."""
        wanted = set(message_ids)
        if not self._combined():
            return [(row, message_id) for message_id in wanted if (row := self.find(message_id)) >= 0]
        found: list[tuple[int, str]] = []
        for position, key in enumerate(self._order):
            message_id = split_key(key)[1]
            if message_id in wanted and (row := self.display_row(position)) >= 0:
                found.append((row, message_id))
        return found

    def find(self, message_id: str) -> int:
        """The row of a message in the list, or -1 (also while its group is folded)."""
        key = self._loaded_key(message_id)
        position = self._order.position(key) if key is not None else -1
        return self.display_row(position) if position >= 0 else -1

    # --- the facts of the engine (D-146): each change applied to exactly what it changes --------------------------

    def apply(self, change: Change) -> None:
        """One fact of a change of mail (a Change of the engine): apply exactly that change, without fetching the view again.
        A message that comes into the view is put at its place (the engine is asked where that is)."""
        mailbox, number = change.mailbox, change.sequence
        if number > self._seen.get(mailbox, 0):
            self._seen[mailbox] = number  # every fact, also of mail this view does not show: the engine counts by these numbers
        spec = self._spec
        if spec is None or not self._concerns(spec, mailbox):
            return
        if self._open:
            self._log.append(change)  # applied again to the answers on their way
        if not self._complete and self._touches_view(change):
            self._changed_while_loading = True  # selected places not known yet can no longer be told by their number (_swap_in)
        if str(change.kind) == "removed" or (str(change.kind) == "moved" and not self._may_hold(change)):  # it only leaves this view
            if self._leave_soon.busy:
                self._leaving.append(change)  # more leave in the same moment (a large delete): they go together when it ends
                self._leave_soon.wait_for_end()
                return
            self._leave_soon.ask()  # nothing gathered: the first of a quiet moment goes at once, below
        self._flush_leaving()  # every fact in the order it came
        self._follow(change)

    def _flush_leaving(self) -> None:
        """The messages that left the view in this moment go together: one change of the list's layout instead of one removal each, so a
        delete of 16,000 messages no longer lays the list out and paints it 16,000 times. A move that brings the message back into the view
        (a combined view) is followed after."""
        changes, self._leaving = self._leaving, list[Change]()
        if not changes:
            return
        keys: list[str] = []
        coming: list[Change] = []
        for change in changes:
            key = self._key(change.mailbox, change.message_id)
            if str(change.kind) == "moved" and self._key(change.mailbox, change.new_message_id or change.message_id) == key and key in self._order and self._order.folder_of(key) == change.to_folder:
                self._follow(change)  # the order counted the move already
                continue
            self._arriving.pop(key, None)
            if self._placing is not None:
                self._placing.pop(key, None)
            if key in self._order and key not in keys:
                keys.append(key)
            if str(change.kind) == "moved":
                coming.append(change)
        self._take_out_runs(keys)
        for change in coming:
            self._bring_in(change)

    def _concerns(self, spec: ViewSpec, mailbox: str) -> bool:
        """Whether the view may show mail of this mailbox (a Favorites view shows every mailbox, or those of a group)."""
        return spec.combined is not None or spec.account == mailbox

    def _follow(self, change: Change) -> None:
        kind = str(change.kind)
        mailbox, folder = change.mailbox, change.folder
        key = self._key(mailbox, change.message_id)
        if kind == "flags":
            self._keep_here(key)
            self._set_flags(key, tuple(change.flags))
            self._counts_changed()
        elif kind == "tags":
            self._keep_here(key)
            row = self._row_of(key)
            if row is not None:
                self._replace(key, dataclasses.replace(row, tags=tuple(change.tags)))
        elif kind == "changed":
            row = change.row
            if row is not None and self._row_of(key) is not None:
                self._replace(key, self._held(row, mailbox))
        elif kind == "removed":
            self._take_out(key, change.was_unread)
        elif kind == "moved":
            to_folder, row = change.to_folder, change.row
            moved_to = self._key(mailbox, change.new_message_id or change.message_id)
            if moved_to == key and key in self._order and self._order.folder_of(key) == to_folder:
                if row is not None and self._row_of(key) is not None:  # the order counted the move already (a fact applied again to an answer)
                    self._replace(key, self._held(row, mailbox))
                return
            self._take_out(key, change.was_unread)
            self._bring_in(change)
        elif kind == "added":
            self._bring_in(change)
        elif kind == "folder_read":
            self._folder_read(mailbox, folder)
        elif kind == "folder_emptied":
            self._folder_emptied(mailbox, folder)
        elif kind == "folder_renamed":
            self._folder_renamed(mailbox, folder, change.to_folder)

    def _followed_row(self, row: MessageRow, change: Change) -> MessageRow:
        """A row as a fact about its message (or its folder) leaves it; a fact about another message leaves it as it is."""
        kind = str(change.kind)
        mailbox = change.mailbox
        if not self._concerns_row(row, mailbox):
            return row
        if kind in ("flags", "tags", "changed", "moved") and change.message_id != row.message_id:
            return row
        if kind == "moved":
            new = change.row
            return row if new is None else self._held(new, mailbox)
        if kind == "flags":
            return dataclasses.replace(row, flags=tuple(change.flags))
        if kind == "tags":
            return dataclasses.replace(row, tags=tuple(change.tags))
        if kind == "changed":
            new = change.row
            return row if new is None else self._held(new, mailbox)
        if kind == "folder_read" and row.folder == change.folder and _unread(row):
            return dataclasses.replace(row, flags=tuple(sorted((*row.flags, "seen"))))
        if kind == "folder_renamed":
            old, new_name, name = change.folder, change.to_folder, row.folder
            if name == old or name.startswith(old + "/"):
                return dataclasses.replace(row, folder=new_name + name[len(old):])
        return row

    def _concerns_row(self, row: MessageRow, mailbox: str) -> bool:
        """Whether a fact of this mailbox may be about this row (in a Favorites view: only one of its own mailbox)."""
        return not self._combined() or (row.account or "") == mailbox

    def _counts_changed(self) -> None:
        if self._complete:
            self.counts_changed.emit(len(self._order), len(self._unread))
        else:
            self.counts_changed.emit(self._available(), self._unread_total)

    def _set_flags(self, key: str, flags: tuple[str, ...]) -> None:
        """A message got other marks: its unread mark in the order, and its row when it is loaded or waits to be placed."""
        if key in self._order:
            unread, was = "seen" not in flags, key in self._unread
            if unread != was:
                (self._unread.add if unread else self._unread.discard)(key)
                if not self._complete:
                    self._unread_total = max(0, self._unread_total + (1 if unread else -1))
        row = self._row_of(key)
        if row is not None:
            self._replace(key, dataclasses.replace(row, flags=flags))

    def _keep_here(self, key: str) -> None:
        """A message shown in a view filtered by unread, flagged or a tag got other marks or tags: it stays in the view until
        the view is left, also when the view is fetched again or sorted (the engine is told, D-149)."""
        spec = self._spec
        if spec is not None and key in self._order and _filters_marks(spec):
            self._stay.add(key)

    def _take_out(self, key: str, was_unread: bool) -> None:
        """A message left this view: its row goes, its group count follows, an emptied group heading goes too."""
        self._arriving.pop(key, None)
        if self._placing is not None:
            self._placing.pop(key, None)  # it is not placed when the answer comes
        position = self._order.position(key)
        if position < 0:
            return  # not in the view (or, while the whole order is on its way, not known yet: the order brings it)
        rows = self._remove_rows_of(position)
        if rows is not None:
            self.beginRemoveRows(QModelIndex(), rows[0], rows[1])
        self._drop(position, key, was_unread)
        self._layout_groups()
        if rows is not None:
            self.endRemoveRows()  # rows only went: every header left keeps its span
        self._counts_changed()

    def _remove_rows_of(self, position: int) -> tuple[int, int] | None:
        """The rows of the list that go when the message at this position goes (None: none, its group is folded)."""
        if self._group_counts is None:
            return (position, position)
        group = next((group for group in self._groups if group.message_start <= position < group.message_start + group.count), None)
        if group is None:
            return None
        number = self._group_index(position)
        if self._group_counts[number][2] <= 1:  # its group empties: the heading goes too
            return (group.display_start, group.display_start + group.shown)
        if group.folded:
            return None
        return (group.display_start + 1 + position - group.message_start, group.display_start + 1 + position - group.message_start)

    def _drop(self, position: int, key: str, was_unread: bool) -> None:
        """The data of a message that goes (call between begin and end of the removal)."""
        if self._group_counts is not None and self._group_counts:
            number = self._group_index(position)
            group_key, name, count = self._group_counts[number]
            if count <= 1:
                del self._group_counts[number]
            else:
                self._group_counts[number] = (group_key, name, count - 1)
        row = self._rows.get(key)
        self._order.remove(key)
        self._forget(key)
        self._unread.discard(key)
        self._asked.discard(key)
        if not self._complete:
            self._total = max(0, self._total - 1)
            if was_unread:
                self._unread_total = max(0, self._unread_total - 1)
        if row is not None:
            self.pending.discard(row.message_id)
        else:
            self.pending.discard(split_key(key)[1])

    def mark_pending(self, message_ids: list[str]) -> None:
        """An action is under way for these messages: they are drawn greyed until it ends."""
        self.pending.update(message_ids)
        self._redraw(message_ids)

    def leave_now(self, message_ids: list[str]) -> None:
        """An action that needs no server's word (POP, Local Folders) takes these messages out of the view at its key press, not when
        its fact comes: the row goes, and the rows below close up, at once (D-266). Kept until the action is done: a failed one comes back.
        They go per run of neighbouring rows, as their facts would: one removal each laid the whole list out again per message (a delete
        of 15,875 froze the window for 17 s)."""
        keys: list[str] = []
        unread: set[str] = set()
        gone: list[str] = []
        for message_id in message_ids:
            key = self._loaded_key(message_id)
            row = self._rows.get(key) if key is not None else None
            if key is None or row is None or message_id in self._left_early:
                continue  # not shown or not loaded: it leaves with its fact, as before (or named twice: it goes once)
            self._left_early[message_id] = (key, row)
            self._arriving.pop(key, None)
            if self._placing is not None:
                self._placing.pop(key, None)  # it is not placed when the answer comes
            keys.append(key)
            if _unread(row):
                unread.add(key)
            gone.append(message_id)
        self._take_out_runs(keys, unread)
        self.pending.update(gone)  # still under way until its action answers: what waits in turn never acts on it again (D-146)

    def done_early(self, message_id: str) -> None:
        """The action of a message taken out at its key press is done: nothing comes back."""
        self._left_early.pop(message_id, None)

    def come_back(self, message_id: str) -> None:
        """The action of a message taken out at its key press was not done: it comes back at its place, the engine says where."""
        left = self._left_early.pop(message_id, None)
        spec = self._spec
        if left is None or spec is None:
            return
        key, row = left
        mailbox = split_key(key)[0] or spec.account or ""
        self._bring_in(Change("added", mailbox, folder=row.folder, message_id=message_id, row=row, unread=_unread(row), role=self.role_of(mailbox, row.folder)))

    def clear_pending(self, message_id: str) -> None:
        """The action of this message ended (its fact took the row out already, or it failed): drawn as normal again."""
        if message_id in self.pending:
            self.pending.discard(message_id)
            self._redraw([message_id])

    def _redraw(self, message_ids: list[str]) -> None:
        """One redraw for each run of rows next to each other, not one per message: whatever follows the redraws then runs once per run,
        not once per message (an action on 1,000 selected messages froze the window for minutes, W13)."""
        last = max(0, self.columnCount() - 1)
        rows = sorted(row for row in (self.find(message_id) for message_id in message_ids) if row >= 0)
        start = 0
        for position in range(1, len(rows) + 1):
            if position == len(rows) or rows[position] != rows[position - 1] + 1:
                self.dataChanged.emit(self.index(rows[start], 0), self.index(rows[position - 1], last))
                start = position

    def _may_hold(self, change: Change) -> bool:
        """Whether a message that came into a folder may belong in this view; when it may, the engine says where."""
        spec = self._spec
        if spec is None:
            return False
        folder, role, row = change.to_folder or change.folder, change.role, change.row
        if row is None:
            return False
        flags, tags = tuple(row.flags), tuple(tag.casefold() for tag in row.tags)
        if spec.combined is not None:
            everywhere = role not in ("trash", "junk", *HERE_ONLY_ROLES)
            wanted = {
                "inboxes": role == "inbox", "sent": role == "sent", "drafts": role == "drafts", "junk": role == "junk", "trash": role == "trash",
                "outbox": role == "outbox", "scheduled": role == "scheduled", "unread": everywhere and "seen" not in flags, "flagged": everywhere and "flagged" in flags,
                "snoozed": role == "snoozed", "unreplied": role == "inbox" and "replied" not in flags, "archive": role == "archive",
                "tagged": everywhere and (spec.combined_tag or "").casefold() in tags,
            }
            if not wanted.get(spec.combined, False):
                return False
        elif spec.folder is not None and spec.folder != folder:
            return False
        if spec.unread is not None and spec.unread == ("seen" in flags):
            return False
        if spec.flagged is not None and spec.flagged != ("flagged" in flags):
            return False
        return spec.tag is None or spec.tag.casefold() in tags

    def _bring_in(self, change: Change) -> None:
        """A message came into a folder: when it may belong in this view it is taken in together with the others that came at
        the same moment (a mail check brings several), with one question for all of them."""
        row = change.row
        if row is None or not self._may_hold(change):
            return
        mailbox = change.mailbox
        held = self._held(row, mailbox)
        key = self._row_key(held)
        if key in self._order or (self._placing is not None and key in self._placing):
            return  # it is there already (the order counted it), or its place is being asked
        self._arriving[key] = held
        if not self._arrival_planned:
            self._arrival_planned = True
            QTimer.singleShot(0, self, self._take_in)  # the facts of one burst arrive one after the other: gathered into one question

    def hold_arrivals(self) -> Callable[[], None]:
        """Messages that come in are gathered until the returned function is called (once), then placed in one step: up to BURST with
        one question, more as one new order. An Undo of 15,346 messages placed them a few at a time, painting the list again each time,
        and took three times as long as the Delete."""
        self._arrivals_held += 1
        released: list[bool] = []

        def release() -> None:
            if released:
                return
            released.append(True)
            self._arrivals_held -= 1
            if not self._arrivals_held and self._arriving and not self._arrival_planned:
                self._arrival_planned = True
                QTimer.singleShot(0, self, self._take_in)
            self._arrivals_done()

        return release

    def after_arrivals(self, then: Callable[[], None]) -> None:
        """then runs once every message that came in has its place in the list (none gathered, none whose place is asked, no order on
        its way; after another view is shown, once its order is in): what comes after an Undo acts on the list with its messages back."""
        self._after_arrivals.append(then)
        self._arrivals_done()

    def _arrivals_done(self) -> None:
        if not self._after_arrivals or self._arrivals_held or self._arrival_planned or self._placing is not None or self._ordering is not None:
            return
        if self._arriving and self._spec is not None:
            self._arrival_planned = True  # still to be placed: in one step, then this is looked at again
            QTimer.singleShot(0, self, self._take_in)
            return
        waiting, self._after_arrivals = self._after_arrivals, list[Callable[[], None]]()
        for then in waiting:
            then()

    def _take_in(self) -> None:
        """The messages that came in at once: the engine says, in one answer, under which heading each falls and before which
        message the list holds it comes; then each is put at its place. A burst larger than BURST is one new order (it keeps
        the selection). While an order is on its way they wait for it: it may count them already."""
        self._arrival_planned = False
        spec = self._spec
        if spec is None or not self._arriving or self._placing is not None or self._ordering is not None or self._arrivals_held:
            self._arrivals_done()
            return
        for key in [key for key in self._arriving if key in self._order]:
            del self._arriving[key]  # the order that came meanwhile counted it already
        if not self._arriving:
            self._arrivals_done()
            return
        if len(self._arriving) > BURST:
            self._arriving.clear()
            self.rearrange(spec)
            return
        placing = self._arriving
        self._arriving = {}
        self._placing = placing
        keys = list(placing)
        view = self._view

        def located(result: PlacesResult) -> None:
            if view != self._view or self._placing is not placing:
                return
            self._placing = None
            if self._ordering is not None:  # an order went out meanwhile: they are placed after it (it may count them)
                for key, row in placing.items():
                    self._arriving.setdefault(key, row)
                return
            if not result.known:
                self.rearrange(self._spec)  # what came in is no longer known one by one: the order is asked once
                return
            positions, befores, before_mailboxes = result.positions, result.before, result.before_mailboxes
            group_keys, group_names = result.group_keys, result.group_names
            placed = sorted((position, number) for number, position in enumerate(positions) if isinstance(position, int))
            for _, number in placed:  # the first place first: messages that come before the same message keep their order
                key = keys[number]
                row = placing.get(key)
                if row is None or key in self._order:
                    continue  # it went again meanwhile, or it is there already
                before = befores[number] if number < len(befores) else ""
                if before:
                    place = self._order.position(self._key(before_mailboxes[number] if number < len(before_mailboxes) else "", before))
                    if place < 0:
                        self._arriving.setdefault(key, row)  # the message it comes before went meanwhile: asked again
                        continue
                else:
                    place = len(self._order)
                self._insert(place, key, row, group_keys[number] if group_keys else "", group_names[number] if group_names else "")
            self.took_in.emit()
            if self._arriving:
                self._take_in()  # what came in meanwhile, with one question
            self._arrivals_done()

        def failed(error: ChronoMailError) -> None:
            if self._placing is placing:
                self._placing = None
            self._failed(error)
            self._arrivals_done()

        messages = tuple(split_key(key)[1] for key in keys)
        heard = tuple(f"{mailbox}={number}" for mailbox, number in self._seen.items())  # only what came before these facts is counted: what came later is not here yet (D-152)
        order = self._order_parameters(spec, True, [])
        if isinstance(order, CombinedOrderParameters):
            mailboxes = tuple(split_key(key)[0] for key in keys)
            combined = carried(order, CombinedPlacesParameters, messages=messages, mailboxes=mailboxes, headings=True, heard=heard)
            self._backend.ask(MESSAGE_COMBINED_PLACES, combined, located, failed, owner=self)
        else:
            self._backend.ask(MESSAGE_PLACES, carried(order, PlacesParameters, messages=messages, headings=True, heard=heard), located, failed, owner=self)

    def _insert(self, position: int, key: str, row: MessageRow, group_key: str, name: str) -> None:
        """Put a message at its place, under its heading (a new heading when its group was not there)."""
        new_group = self._count_into_group(position, group_key, name) if self._group_counts is not None else False
        self._order.insert(position, key, row.folder)
        self._store(key, row)
        if _unread(row):
            self._unread.add(key)
        groups, starts, display = self._computed_layout()
        rows: tuple[int, int] | None
        if self._group_counts is None:
            rows = (position, position)
        else:
            group = next((group for group in groups if group.message_start <= position < group.message_start + group.count), None)
            if group is None or (group.folded and not new_group):
                rows = None
            elif new_group:
                rows = (group.display_start, group.display_start + group.shown)
            else:
                rows = (group.display_start + 1 + position - group.message_start,) * 2
        if rows is not None:
            self.beginInsertRows(QModelIndex(), rows[0], rows[1])
        self._groups, self._group_starts, self._display_rows = groups, starts, display
        if rows is not None:
            self.endInsertRows()
            if new_group:
                self.header_rows_added.emit(rows[0], rows[0])  # the new heading is the first row inserted
        self._counts_changed()

    def _count_into_group(self, position: int, key: str, name: str) -> bool:
        """Count a message that came in into its group (by its key); a new group at its place. True when the group is new."""
        counts = self._group_counts
        if counts is None:
            return False
        for number, (known, shown, count) in enumerate(counts):
            if known == key:
                counts[number] = (known, shown, count + 1)
                return False
        start = 0
        place = len(counts)
        for number, (_, _, count) in enumerate(counts):
            if start >= position:
                place = number
                break
            start += count
        spec = self._spec
        counts.insert(place, (key, group_name(spec.sort, name) if spec is not None else name, 1))
        return True

    def _in_folder(self, mailbox: str, folder: str) -> list[str]:
        """The keys of the messages of the view that are in this folder of this mailbox."""
        if self._shows_folder(folder):
            return list(self._order)
        return self._order.keys_in(folder, mailbox if self._combined() else "")

    def _shows_folder(self, folder: str) -> bool:
        spec = self._spec
        return spec is not None and spec.combined is None and spec.folder == folder

    def _folder_read(self, mailbox: str, folder: str) -> None:
        """Every message of a folder was marked read: they are read in the order, and their rows say so."""
        keys = self._in_folder(mailbox, folder)
        if not self._complete:
            self._unread_total = 0 if self._shows_folder(folder) else max(0, self._unread_total - sum(1 for key in keys if key in self._unread))
        self._unread.difference_update(keys)
        for key in keys:
            row = self._row_of(key)
            if row is not None and _unread(row):
                self._replace(key, dataclasses.replace(row, flags=tuple(sorted((*row.flags, "seen")))))
        for waiting in self._waiting():  # one that waits to be placed is in that folder too
            for key, row in list(waiting.items()):
                if row.folder == folder and self._concerns_row(row, mailbox) and _unread(row):
                    waiting[key] = dataclasses.replace(row, flags=tuple(sorted((*row.flags, "seen"))))
        self._counts_changed()

    def _folder_emptied(self, mailbox: str, folder: str) -> None:
        """Every message of a folder was deleted for good: they leave the view in one step."""
        for waiting in self._waiting():
            for key, row in list(waiting.items()):
                if row.folder == folder and self._concerns_row(row, mailbox):
                    del waiting[key]
        if self._shows_folder(folder):
            self.beginResetModel()  # the whole view is empty now: nothing on it to keep
            self._hold_nothing()
            self._group_counts = [] if self._group_counts is not None else None
            self._layout_groups()
            self.endResetModel()
            self._counts_changed()
            return
        self._take_out_many(self._order.keys_in(folder, mailbox if self._combined() else ""))

    def _take_out_runs(self, keys: list[str], unread: set[str] | None = None) -> None:
        """Many messages left the view at once: every run of rows next to each other goes in one removal, bottom up (a group whose
        messages all leave goes with its heading), so Qt moves the selection of the others by itself, cheaply, and the list is laid out
        once per run. (One layout change for all of them made Qt check every selected row again: slower than removing them one by one.)
        unread: the keys that were unread, when their rows tell it (else the view's own unread marks)."""
        was_unread = self._unread if unread is None else unread
        places = sorted(((position, key) for key in keys if (position := self._order.position(key)) >= 0), reverse=True)
        leaving: dict[int, int] = {}
        if self._group_counts is not None:
            for position, _ in places:
                number = self._group_index(position)
                leaving[number] = leaving.get(number, 0) + 1
        spans: list[tuple[int, int, int, str]] = []  # first and last row that go (-1: none, a folded group), position, key
        for position, key in places:
            if self._group_counts is None:
                spans.append((position, position, position, key))
                continue
            group = next((found for found in self._groups if found.message_start <= position < found.message_start + found.count), None)
            number = self._group_index(position)
            if group is not None and leaving[number] >= self._group_counts[number][2]:
                spans.append((group.display_start, group.display_start + group.shown, position, key))  # the whole group, heading too
            elif group is None or group.folded:
                spans.append((-1, -1, position, key))
            else:
                row = group.display_start + 1 + position - group.message_start
                spans.append((row, row, position, key))
        run: list[tuple[int, str]] = []
        first = last = -1
        headings_changed = False

        def remove_run() -> None:
            if not run:
                return
            self.beginRemoveRows(QModelIndex(), first, last)
            for position, key in run:
                self._drop(position, key, key in was_unread)
            self._layout_groups()
            self.endRemoveRows()
            run.clear()

        for start, end, position, key in spans:
            if start < 0:  # in a folded group: no row goes, its heading's count changes
                remove_run()
                self._drop(position, key, key in was_unread)
                self._layout_groups()
                headings_changed = True
            elif run and end >= first - 1:
                first, last = min(first, start), max(last, end)
                run.append((position, key))
            else:
                remove_run()
                first, last = start, end
                run.append((position, key))
        remove_run()
        if headings_changed:
            for row in self.header_rows():
                self.dataChanged.emit(self.index(row, 0), self.index(row, max(0, self.columnCount() - 1)))
        self._counts_changed()

    def _take_out_many(self, keys: list[str]) -> None:
        """Many messages left the view at once: one layout change instead of one removal each (Qt's selection of the others stays)."""
        if not keys:
            return
        if len(keys) == 1:
            self._take_out(keys[0], keys[0] in self._unread)
            return
        self.layoutAboutToBeChanged.emit()
        held = self.persistentIndexList()
        held_places: list[tuple[str | None, int | None]] = []
        for index in held:
            position = self.message_position(index.row()) if index.isValid() else None
            held_places.append((None if position is None else self._order.key_at(position), position))
        known = len(self._order)
        gone = {key for key in keys if key in self._order}
        if self._group_counts is not None:
            lost: dict[int, int] = {}
            for key in keys:
                number = self._group_index(self._order.position(key))
                lost[number] = lost.get(number, 0) + 1
            self._group_counts = [(group_key, name, count - lost.get(number, 0)) for number, (group_key, name, count) in enumerate(self._group_counts) if count - lost.get(number, 0) > 0]
        if not self._complete:
            self._total = max(0, self._total - len(gone))
            self._unread_total = max(0, self._unread_total - sum(1 for key in gone if key in self._unread))
        self._order = self._order.without(gone)
        for key in gone:
            row = self._rows.get(key)
            if row is not None:
                self.pending.discard(row.message_id)
            self._forget(key)
            self._unread.discard(key)
            self._asked.discard(key)
        self._layout_groups()
        moved: list[QModelIndex] = []
        for index, (key, position) in zip(held, held_places, strict=True):
            if key is not None:
                shown = self.display_row(self._order.position(key)) if key not in gone else -1
            elif position is not None and position >= known:  # a place whose message is not known yet (after the known start): it moves up
                shown = self.display_row(position - (known - len(self._order)))  # by the messages that left before it (RV-206)
            else:
                shown = -1
            moved.append(self.index(shown, index.column()) if 0 <= shown < self._display_rows else QModelIndex())
        self.changePersistentIndexList(held, moved)
        self.layoutChanged.emit()
        self.groups_changed.emit()
        self._counts_changed()

    def _folder_renamed(self, mailbox: str, old: str, new: str) -> None:
        """A folder got another name: the rows show it, and a view of that folder (or one below it) follows."""
        def renamed(name: str) -> str:
            return new + name[len(old):] if name == old or name.startswith(old + "/") else name

        self._order.rename_folder(old, new, mailbox if self._combined() else "")
        for key, row in list(self._rows.items()):
            if self._concerns_row(row, mailbox) and renamed(row.folder) != row.folder:
                self._store(key, dataclasses.replace(row, folder=renamed(row.folder)))
        for waiting in self._waiting():
            for key, row in list(waiting.items()):
                if self._concerns_row(row, mailbox) and renamed(row.folder) != row.folder:
                    waiting[key] = dataclasses.replace(row, folder=renamed(row.folder))
        if self._spec is not None and self._spec.folder is not None and self._spec.combined is None:
            self._spec = dataclasses.replace(self._spec, folder=renamed(self._spec.folder))
        self.refresh_cells()

    def _failed(self, error: ChronoMailError) -> None:
        self.load_failed.emit(str(error))

