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
from chrono_mail.ui.columns import BY_ID, CARD, SEND_AT_FOLDERS, SEND_AT_LABEL, SNOOZED_COLUMN, SYMBOLS, cell_text, display_name, person_address
from chrono_mail.ui.dragdata import MESSAGES_MIME_TYPE, messages_mime_data
from chrono_mail.ui.icons import grey_icon
from chrono_mail.ui.messageorder import MessageOrder, message_key, split_key
from chrono_mail.ui.models import LOCAL_PLACES
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "messagelist.py 2.4.329"

# --- Version history ----------------------------------------------------
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
        self._arriving: dict[str, MessageRow] = {}  # messages that came in, by key -> their row (kept current by later facts), placed together
        self._arrival_planned: bool = False
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
        self._order, self._complete, self._total, self._unread_total = MessageOrder(), True, 0, 0
        self._unread.clear()
        self._rows.clear()
        self._ids.clear()
        self._asked.clear()
        self._wanted_pages.clear()

    @property
    def spec(self) -> ViewSpec | None:
        return self._spec

    def show(self, spec: ViewSpec | None) -> None:
        """Start showing something else (or nothing). The order is asked (for a large view, its start first)."""
        self.pending.clear()
        self._stay.clear()  # the view is left: what was changed in it no longer stays
        self._arriving.clear()
        self._placing = None
        self.settled = spec is None or (self.settled and spec == self._spec and self._display_rows == 0)  # an empty view reloaded stays settled; one with messages waits for them
        self.beginResetModel()
        self._view += 1
        self._spec = spec
        self._hold_nothing()
        self._group_counts = None
        self._layout_groups()
        self.endResetModel()
        if spec is None:
            self.counts_changed.emit(0, 0)
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

        def failed(error: ChronoMailError) -> None:
            self._answered(question)
            if view != self._view or number != self._ordering:
                return
            self._ordering = None
            self._failed(error)
            self.settled = True
            if purpose == "rearrange":
                self.show(spec)  # the new order without the rows kept: the error is shown

        if isinstance(parameters, CombinedOrderParameters):
            self._backend.ask(MESSAGE_COMBINED_ORDER, parameters, answered, failed, owner=self)
        else:
            self._backend.ask(MESSAGE_ORDER, parameters, answered, failed, owner=self)

    def _take_order(self, spec: ViewSpec, order: ViewOrder, facts: list[Change], purpose: str) -> None:
        """An order arrived: it takes the place of what the list held, then the facts heard since it was asked are applied to it."""
        keys = [message_key(mailbox, message_id) for mailbox, message_id in zip(order.mailboxes, order.messages, strict=True)] if order.mailboxes else list(order.messages)
        if purpose == "show":
            self.beginResetModel()  # the list is empty: there is nothing on it to keep
            self._set_order(spec, order, keys, keep_rows=False)
            self.endResetModel()
            self.groups_changed.emit()
        else:
            self._swap_in(spec, order, keys, keep_rows=purpose != "reload")
        for fact in facts:
            self._follow(fact)
        self._counts_changed()
        self.settled = True
        if purpose in ("rearrange", "reload"):
            self.rearranged.emit()
        self.loaded.emit()
        if not self._complete:
            self._ask_order(spec, whole=True, rows_of=[], purpose="complete")
            return
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

    def _swap_in(self, spec: ViewSpec, order: ViewOrder, keys: list[str], keep_rows: bool) -> None:
        """The new order replaces the old one in one layout change. Every index Qt holds (its selection, the current message) is
        moved to the row its message has now; one whose message is not in the view any more goes."""
        self.layoutAboutToBeChanged.emit()
        held = self.persistentIndexList()
        held_keys = [self._key_at_row(index.row()) if index.isValid() else None for index in held]
        self._set_order(spec, order, keys, keep_rows)
        moved: list[QModelIndex] = []
        for index, key in zip(held, held_keys, strict=True):
            shown = self.display_row(self._order.position(key)) if key is not None and key in self._order else -1
            moved.append(self.index(shown, index.column()) if 0 <= shown < self._display_rows else QModelIndex())
        self.changePersistentIndexList(held, moved)
        self.layoutChanged.emit()
        self.groups_changed.emit()

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
        if not index.isValid():
            return None
        group = self._group_at(index.row())
        if group is not None and group.display_start == index.row():
            if role == GROUP_HEADER_ROLE:
                return (group.name, group.folded)
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return group.name if index.column() == 0 else None
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
            return cell_text(column_id, row, self.recipients_first, self.date_style)
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
        if index.isValid() and self.is_header(index.row()):
            return Qt.ItemFlag.ItemNeverHasChildren  # not enabled: the arrow keys pass it, it cannot be selected
        base = super().flags(index) | Qt.ItemFlag.ItemNeverHasChildren  # the list is flat: Qt never asks a row for children
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    @override
    def mimeTypes(self) -> list[str]:
        return [MESSAGES_MIME_TYPE]

    @override
    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        """The messages being dragged: where they are, never their content."""
        messages: list[tuple[str, str]] = []
        account = self._spec.account if self._spec is not None else ""
        for index in indexes:
            row = self.row_at(index.row())
            if row is None:
                continue
            owner = row.account or account
            if not messages:
                account = owner
            if owner == account:  # in a combined view the rows of other accounts cannot go along: one drag, one account
                messages.append((row.folder, row.message_id))
        return messages_mime_data(account, messages)

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
            facts = self._answered(question)
            if view != self._view:
                if then is not None:
                    then()
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
            if then is not None:
                then()

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
        self._follow(change)

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
            everywhere = role not in ("trash", "junk") and folder not in LOCAL_PLACES
            wanted = {
                "inboxes": role == "inbox", "sent": role == "sent", "drafts": role == "drafts", "junk": role == "junk", "trash": role == "trash",
                "outbox": folder == "Outbox", "scheduled": folder == "Scheduled", "unread": everywhere and "seen" not in flags, "flagged": everywhere and "flagged" in flags,
                "snoozed": folder == "Snoozed", "unreplied": role == "inbox" and "replied" not in flags, "archive": role == "archive",
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

    def _take_in(self) -> None:
        """The messages that came in at once: the engine says, in one answer, under which heading each falls and before which
        message the list holds it comes; then each is put at its place. A burst larger than BURST is one new order (it keeps
        the selection). While an order is on its way they wait for it: it may count them already."""
        self._arrival_planned = False
        spec = self._spec
        if spec is None or not self._arriving or self._placing is not None or self._ordering is not None:
            return
        for key in [key for key in self._arriving if key in self._order]:
            del self._arriving[key]  # the order that came meanwhile counted it already
        if not self._arriving:
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
            if self._arriving:
                self._take_in()  # what came in meanwhile, with one question

        def failed(error: ChronoMailError) -> None:
            if self._placing is placing:
                self._placing = None
            self._failed(error)

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

    def _take_out_many(self, keys: list[str]) -> None:
        """Many messages left the view at once: one layout change instead of one removal each (Qt's selection of the others stays)."""
        if not keys:
            return
        if len(keys) == 1:
            self._take_out(keys[0], keys[0] in self._unread)
            return
        self.layoutAboutToBeChanged.emit()
        held = self.persistentIndexList()
        held_keys = [self._key_at_row(index.row()) if index.isValid() else None for index in held]
        gone = set(keys)
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
        for index, key in zip(held, held_keys, strict=True):
            shown = self.display_row(self._order.position(key)) if key is not None and key not in gone else -1
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

