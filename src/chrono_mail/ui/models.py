import bisect
import dataclasses
from dataclasses import dataclass, field
from collections.abc import Sequence
from enum import Enum
from typing import override

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QObject, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPalette, QStandardItem, QStandardItemModel

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.columns import BY_ID, CARD, SYMBOLS, cell_text, display_name, person_address
from chrono_mail.ui.dragdata import MESSAGES_MIME_TYPE, messages_mime_data
from chrono_mail.ui.icons import folder_icon, grey_icon
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagging import tag_icon

# --- Version ------------------------------------------------------------
__version__ = "models.py 0.5.156"

# --- Version history ----------------------------------------------------
# v0.5.156: the list applies the facts of the engine to its own rows (D-146 step 2): a folder keeps its rows as one ordered list with empty places for what is not loaded; an answer asked before a change that moved places is asked again
# v0.5.153: load_position: fetch the page of a message whose place is known (the selection kept after a sort)
# v0.5.139: A reload keeps the rows on screen until the new headers and first rows are there, then swaps them in at once (the list no longer goes blank after a delete)
# v0.5.135: Tags: the Tag column (drawn by the delegate), the tag colour as the text colour, replace_tags; a combined view of one tag; a list filtered by a tag; the Tags in the folder list
# v0.5.125: A list with messages that is reloaded does not say for a moment that it is empty
# v0.5.123: The Favorites heading cannot be selected; settled: the view has finished loading (D-119)
# v0.3.109: No group headers for the yes/no sorts (unread, flagged, attachment, replied, contact); names of the folder, conversation and preview groups
# v0.2.99: message_count (how many messages the view holds)
# v0.2.96: The rows of the group headers are kept in a list (the card layout measures every row)
# v0.2.94: Group headers in the message list (by the sort order), folded and unfolded; search and combined views in any sort order; sorting by To
# v0.1.89: The contact of a row (for the Contact column); grey icons in the headers of Contact and Delete
# v0.0.87: Folder list: the name without the number and never bold; the number of unread messages in its own role (drawn by the tree); Local Folders uses the home icon
# v0.0.85: A folder item keeps its plain name (for the window title)
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.54: A flag change redraws every column of the row
# v0.0.47: Pinned favorites, account groups and search in combined views
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.41: columns
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.18: Conversations: counts, grouped list, conversation bar
# v0.0.17: messages can be dragged out of the list
# v0.0.14: icons in the folder tree
# v0.0.13: Initial folder and message list models

PAGE_SIZE: int = 100
ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
FOLDER_PATH_ROLE: int = Qt.ItemDataRole.UserRole + 2
ACCOUNT_ROLE: int = Qt.ItemDataRole.UserRole + 3
KIND_ROLE: int = Qt.ItemDataRole.UserRole + 4
UNREAD_ROLE: int = Qt.ItemDataRole.UserRole + 5
FOLDER_ROLE_ROLE: int = Qt.ItemDataRole.UserRole + 6
GROUP_ROLE: int = Qt.ItemDataRole.UserRole + 7
PIN_ROLE: int = Qt.ItemDataRole.UserRole + 8
NAME_ROLE: int = Qt.ItemDataRole.UserRole + 9  # the folder's name without the number of unread messages
CONTACT_ROLE: int = Qt.ItemDataRole.UserRole + 11  # (contact id, address book) of the person a row shows, or None
COUNT_ROLE: int = Qt.ItemDataRole.UserRole + 10  # the number of unread messages the folder list draws at the right edge (0: none)
ROW_PLACEHOLDER: None = None
HEADER_ICON_ROLE: int = Qt.ItemDataRole.UserRole + 12  # a header icon that the list header draws in the middle of the column
HEADER_ICONS: dict[str, str] = {"contact": "user", "trash": "edit-delete"}  # columns whose header is a grey icon
GROUP_HEADER_ROLE: int = Qt.ItemDataRole.UserRole + 13  # (name, folded) on a group header row, None on a message row


@dataclass(frozen=True, slots=True)
class _Group:
    """One group header in the list and the messages under it: where it is in the list (display rows) and in the messages."""

    key: str
    name: str
    display_start: int  # the row of the header
    message_start: int  # the position of its first message in the messages of the view
    count: int  # its messages that are known (loaded, for a search)
    shown: int  # of those, the rows under the header: 0 while folded
    folded: bool


YES_NO_SORTS: frozenset[str] = frozenset({"unread", "flagged", "attachment", "replied", "contact"})  # the icon columns: no group headers


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


class ViewKind(Enum):
    FOLDER = "folder"  # a folder: ordered, with a known total, loaded page by page
    QUERY = "query"  # a search or a quick filter: newest first, loaded as the list is scrolled


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
    tag: str | None = None  # Filter > Tagged with, or the tag of the combined view "tagged" (Tags in the folder list)

    @property
    def kind(self) -> ViewKind:
        if self.combined is not None:
            return ViewKind.QUERY
        searching = self.tag is not None or self.conversations or self.date_from is not None or self.date_to is not None or self.minimum_size is not None or self.maximum_size is not None or bool(self.query) or self.unread is not None or self.flagged is not None or self.has_attachments is not None or self.folder is None
        return ViewKind.QUERY if searching else ViewKind.FOLDER


@dataclass
class _Rows:
    slots: list[object | None] = field(default_factory=list)  # folder views: every message of the folder in order, None while not loaded
    requested: set[int] = field(default_factory=set)  # pages asked for since the last change that moved places
    loaded: list[object] = field(default_factory=list)  # query views
    exhausted: bool = False
    loading_more: bool = False
    shifts: int = 0  # changes that moved places (a row in or out): an answer asked for before one is asked for again


class MessageListModel(QAbstractTableModel):
    """The rows of one folder or search, fetched a page at a time through engine commands."""

    counts_changed = Signal(int, int)  # total, unread
    groups_changed = Signal()  # the group header rows moved (the window spans them over all columns)
    load_failed = Signal(str)
    loaded = Signal()

    def __init__(self, backend: Backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._spec: ViewSpec | None = None
        self._generation: int = 0
        self.settled: bool = True  # the view has finished loading (or shows nothing): an empty list is really empty
        self._total: int = 0
        self._unread: int = 0
        self._rows: _Rows = _Rows()
        self._columns: tuple[str, ...] = (CARD,)
        self.recipients_first: bool = False  # in Sent, Drafts and Outbox the From / To column shows the recipients
        self.date_style: str = "short"
        self.tag_book: TagColourBook = TagColourBook()  # the colours of the tags (from tag.list)
        self.tag_text: bool = False  # Settings > Message list: the text of a tagged message in the colour of its first tag
        self.contacts: dict[str, tuple[str, str]] = {}  # address (lower case) -> (contact id, address book)
        self.show_groups: bool = True
        self._group_counts: list[tuple[str, str, int]] | None = None  # key, name, count of each group; None: no headers
        self._groups: list[_Group] = []
        self._group_starts: list[int] = []  # the row of each header, for finding the group of a row quickly
        self._display_rows: int = 0
        self._folded: dict[tuple[str, str | None, str], set[str]] = {}  # per folder (or combined view) and sort order: the folded groups, until the program closes

    # --- the columns --------------------------------------------------------------------------

    @property
    def columns(self) -> tuple[str, ...]:
        return self._columns

    def set_columns(self, columns: tuple[str, ...]) -> None:
        """Show these columns (ids from ui.columns), or the single card column."""
        if columns == self._columns:
            return
        self.beginResetModel()
        self._columns = columns
        self.endResetModel()

    def refresh_cells(self) -> None:
        """What the cells show depends on settings (the date style, recipients): draw them again."""
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, max(0, len(self._columns) - 1)))

    # --- the view -----------------------------------------------------------------------------

    @property
    def spec(self) -> ViewSpec | None:
        return self._spec

    def show(self, spec: ViewSpec | None) -> None:
        """Start showing something else (or nothing). With groups, their headers are asked for first, then the messages."""
        self.settled = spec is None or (self.settled and spec == self._spec and self._available() == 0)  # an empty view reloaded stays settled; one with messages waits for them
        self.beginResetModel()
        self._generation += 1
        self._spec = spec
        self._total = self._unread = 0
        self._rows = _Rows()
        self._group_counts = None
        self._layout_groups()
        self.endResetModel()
        if spec is None:
            self.counts_changed.emit(0, 0)
            return
        if self.show_groups and spec.sort not in YES_NO_SORTS:
            self._request_groups(spec)
        else:
            self._request_first(spec)

    def _request_first(self, spec: ViewSpec) -> None:
        if spec.kind is ViewKind.FOLDER:
            self._request_page(0)
        else:
            self._request_more()

    # --- group headers ------------------------------------------------------------------------

    def _fold_key(self) -> tuple[str, str | None, str]:
        spec = self._spec
        if spec is None:
            return ("", None, "")
        return ((spec.combined or spec.account) + (f":{spec.tag}" if spec.tag and spec.combined else ""), spec.folder if spec.combined is None else ",".join(spec.combined_accounts), spec.sort)

    def _available(self) -> int:
        """How many messages of the view are known: all of a folder (fetched as they are scrolled to), the loaded ones of a search."""
        if self._spec is not None and self._spec.kind is ViewKind.QUERY:
            return len(self._rows.loaded)
        return self._total

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
            known = available - message if number == last else min(count, available - message)  # a message that arrived since the counts were made joins the last group
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
        """How many messages the view holds (all of a folder, the loaded ones of a search); group headers not counted."""
        return self._available()

    def display_row(self, position: int) -> int:
        """The row of the message at this position among the messages of the view; -1 while its group is folded."""
        if self._group_counts is None:
            return position
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
        self.dataChanged.emit(self.index(row, 0), self.index(row, max(0, self.columnCount() - 1)))
        self.groups_changed.emit()

    def _request_groups(self, spec: ViewSpec) -> None:
        generation = self._generation

        def arrived(result: object) -> None:
            if generation != self._generation:
                return
            groups: list[object] = list(result) if isinstance(result, (tuple, list)) else []
            self._group_counts = [(str(getattr(group, "key")), group_name(spec.sort, str(getattr(group, "value"))), int(getattr(group, "count"))) for group in groups]
            self._request_first(spec)

        def failed(error: ChronoMailError) -> None:
            if generation == self._generation:
                self._request_first(spec)  # without headers rather than without messages

        command = "message.combined-groups" if spec.combined is not None else "message.groups"
        values = {name: value for name, value in self._values(spec, 0).items() if name not in ("offset", "limit")}
        self._backend.call(command, values, arrived, failed, owner=self)

    def reload(self) -> None:
        """Fetch the same view again (after mail arrived or messages were changed). The rows on screen stay until the new
        group headers and the first rows have arrived; then they are swapped in at once, so the list never goes blank."""
        current = self._spec
        if current is None or self._display_rows == 0:
            self.show(current)  # nothing on screen to keep
            return
        spec: ViewSpec = current
        self._generation += 1  # answers for the rows on screen (pages, more rows) are no longer wanted
        generation = self._generation

        def first(groups: list[tuple[str, str, int]] | None) -> None:
            def arrived(result: object) -> None:
                if generation == self._generation:
                    self._swap_in(spec, groups, result)

            def failed(error: ChronoMailError) -> None:
                if generation == self._generation:
                    self._failed(error)  # the rows on screen stay

            if spec.kind is ViewKind.FOLDER:
                self._backend.call("message.list", self._values(spec, 0), arrived, failed, owner=self)
            else:
                self._backend.call("message.combined" if spec.combined is not None else "message.search", self._values(spec, 0), arrived, failed, owner=self)

        if not (self.show_groups and spec.sort not in YES_NO_SORTS):
            first(None)
            return

        def groups_arrived(result: object) -> None:
            if generation != self._generation:
                return
            found: list[object] = list(result) if isinstance(result, (tuple, list)) else []
            first([(str(getattr(group, "key")), group_name(spec.sort, str(getattr(group, "value"))), int(getattr(group, "count"))) for group in found])

        def groups_failed(error: ChronoMailError) -> None:
            if generation == self._generation:
                first(None)  # without headers rather than without messages

        command = "message.combined-groups" if spec.combined is not None else "message.groups"
        values = {name: value for name, value in self._values(spec, 0).items() if name not in ("offset", "limit")}
        self._backend.call(command, values, groups_arrived, groups_failed, owner=self)

    def _swap_in(self, spec: ViewSpec, groups: list[tuple[str, str, int]] | None, result: object) -> None:
        """The reloaded headers and first rows replace the rows on screen in one reset."""
        self.beginResetModel()
        self._spec = spec
        self._rows = _Rows()
        self._group_counts = groups
        if spec.kind is ViewKind.FOLDER:
            self._total, self._unread = int(getattr(result, "total")), int(getattr(result, "unread"))
            self._fill(0, list(getattr(result, "messages")))
            self._rows.requested.add(0)
        else:
            rows: list[object] = list(result) if isinstance(result, (tuple, list)) else []
            self._rows.loaded = rows
            self._rows.exhausted = len(rows) < PAGE_SIZE
            self._total, self._unread = 0, 0
        self._layout_groups()
        self.endResetModel()
        self.groups_changed.emit()
        if spec.kind is ViewKind.FOLDER:
            self.counts_changed.emit(self._total, self._unread)
        else:
            self.counts_changed.emit(len(self._rows.loaded), sum(1 for row in self._rows.loaded if "seen" not in getattr(row, "flags")))
        self.settled = True
        self.loaded.emit()

    # --- Qt model interface -------------------------------------------------------------------

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._display_rows

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    @override
    def index(self, row: int, column: int = 0, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> QModelIndex:
        return super().index(row, column, parent)

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(self._columns) or self._columns[section] not in BY_ID:
            return None
        column = BY_ID[self._columns[section]]
        if role == Qt.ItemDataRole.DisplayRole:
            return SYMBOLS.get(column.column_id, column.label)
        if role == Qt.ItemDataRole.ToolTipRole:
            return column.label
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
        if role == Qt.ItemDataRole.DisplayRole and column_id == "tag":
            return ""  # the delegate draws the tags; the tooltip names them
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return cell_text(column_id, row, self.recipients_first, self.date_style)
        tags: tuple[str, ...] = getattr(row, "tags", ())
        if role == Qt.ItemDataRole.ForegroundRole and self.tag_text and tags and not column.symbol:
            return QBrush(self.tag_book.colour(tags[0]))
        if role == Qt.ItemDataRole.ForegroundRole and column_id == "status":
            palette = QGuiApplication.palette()
            return QBrush(palette.color(QPalette.ColorRole.Highlight if "seen" not in getattr(row, "flags") else QPalette.ColorRole.PlaceholderText))  # blue dot: unread; faint circle: read
        if role == Qt.ItemDataRole.FontRole and "seen" not in getattr(row, "flags"):
            font = QFont()
            font.setBold(True)
            return font
        return None

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if index.isValid() and self.is_header(index.row()):
            return Qt.ItemFlag.ItemNeverHasChildren  # not enabled: the arrow keys pass it, it cannot be selected
        base = super().flags(index)
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
            owner = str(getattr(row, "account", "") or account)
            if not messages:
                account = owner
            if owner == account:  # in a combined view the rows of other accounts cannot go along: one drag, one account
                messages.append((str(getattr(row, "folder")), str(getattr(row, "message_id"))))
        return messages_mime_data(account, messages)

    @override
    def canFetchMore(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> bool:
        return self._spec is not None and self._spec.kind is ViewKind.QUERY and not self._rows.exhausted and not self._rows.loading_more

    @override
    def fetchMore(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> None:
        if self.canFetchMore():
            self._request_more()

    def row_at(self, row: int, fetch: bool = False) -> object:
        """The message at this row of the list, or None on a group header or while its page is still being fetched."""
        position = self.message_position(row)
        return None if position is None else self._message_at(position, fetch)

    def _message_at(self, row: int, fetch: bool = False) -> object:
        """The message at this position among the messages of the view."""
        if self._spec is None or row < 0:
            return None
        if self._spec.kind is ViewKind.QUERY:
            return self._rows.loaded[row] if row < len(self._rows.loaded) else None
        found = self._rows.slots[row] if row < len(self._rows.slots) else None
        if found is None and fetch and row < self._total:
            self._request_page(row // PAGE_SIZE)
        return found

    def _fill(self, offset: int, rows: list[object]) -> None:
        """Put the rows of a page in their places (a folder holds a place for each of its messages)."""
        slots = self._rows.slots
        if len(slots) != self._total:
            del slots[self._total:]
            slots.extend([None] * (self._total - len(slots)))
        for number, row in enumerate(rows[: max(0, self._total - offset)]):
            slots[offset + number] = row

    def load_position(self, position: int) -> None:
        """Make sure the page of the message at this position of a folder is loaded (loaded is emitted when it arrives)."""
        if self._spec is not None and self._spec.kind is ViewKind.FOLDER and 0 <= position < self._total:
            self._message_at(position, fetch=True)

    def replace_flags(self, message_id: str, flags: tuple[str, ...]) -> None:
        """Show new flags on a loaded row without fetching anything (after a flag change)."""
        position = self._find_position(message_id)
        if position < 0:
            return
        row = self._message_at(position)
        if row is None or not dataclasses.is_dataclass(row) or isinstance(row, type):
            return
        was_unread = "seen" not in getattr(row, "flags")
        replaced = dataclasses.replace(row, flags=flags)
        if self._spec is not None and self._spec.kind is ViewKind.QUERY:
            self._rows.loaded[position] = replaced
            self._unread = sum(1 for loaded in self._rows.loaded if "seen" not in getattr(loaded, "flags"))
            self.counts_changed.emit(len(self._rows.loaded), self._unread)
        else:
            self._rows.slots[position] = replaced
            now_unread = "seen" not in flags
            if was_unread != now_unread:
                self._unread = max(0, self._unread + (1 if now_unread else -1))
                self.counts_changed.emit(self._total, self._unread)
        last = max(0, self.columnCount() - 1)
        shown = self.display_row(position)
        if shown >= 0:
            self.dataChanged.emit(self.index(shown, 0), self.index(shown, last))  # every column of the row: each cell is drawn on its own

    def replace_tags(self, message_id: str, tags: tuple[str, ...]) -> None:
        """Show new tags on a loaded row without fetching anything (after message.tag)."""
        position = self._find_position(message_id)
        if position < 0:
            return
        row = self._message_at(position)
        if row is None or not dataclasses.is_dataclass(row) or isinstance(row, type):
            return
        replaced = dataclasses.replace(row, tags=tags)
        if self._spec is not None and self._spec.kind is ViewKind.QUERY:
            self._rows.loaded[position] = replaced
        else:
            self._rows.slots[position] = replaced
        shown = self.display_row(position)
        if shown >= 0:
            self.dataChanged.emit(self.index(shown, 0), self.index(shown, max(0, self.columnCount() - 1)))

    def message_ids(self) -> list[str]:
        """The ids of the rows that are loaded now (for finding a message again after a reload)."""
        rows = [row for row in self._rows.slots if row is not None] if self._spec is not None and self._spec.kind is ViewKind.FOLDER else self._rows.loaded
        return [str(getattr(row, "message_id")) for row in rows]

    def find(self, message_id: str) -> int:
        """The row of a loaded message in the list, or -1 (also while its group is folded)."""
        position = self._find_position(message_id)
        return self.display_row(position) if position >= 0 else -1

    def _find_position(self, message_id: str) -> int:
        """The position of a loaded message among the messages of the view, or -1."""
        if self._spec is None:
            return -1
        if self._spec.kind is ViewKind.QUERY:
            for position, row in enumerate(self._rows.loaded):
                if getattr(row, "message_id") == message_id:
                    return position
            return -1
        for position, row in enumerate(self._rows.slots):
            if row is not None and getattr(row, "message_id") == message_id:
                return position
        return -1

    # --- the facts of the engine (D-146): each change applied to the rows on screen ------------------------------

    def apply(self, change: object) -> None:
        """One fact of a change of mail (a Change of the engine): apply exactly that change to the rows, without fetching the
        view again. A message that comes into the view is put at its place (the engine is asked where that is)."""
        spec = self._spec
        if spec is None or not self._concerns(spec, str(getattr(change, "mailbox", ""))):
            return
        kind = str(getattr(change, "kind", ""))
        message_id = str(getattr(change, "message_id", ""))
        folder = str(getattr(change, "folder", ""))
        if kind == "flags":
            self._apply_flags(message_id, folder, tuple(getattr(change, "flags")), bool(getattr(change, "was_unread")), bool(getattr(change, "unread")))
        elif kind == "tags":
            self.replace_tags(message_id, tuple(getattr(change, "tags")))
        elif kind == "removed":
            self._take_out(message_id, folder, bool(getattr(change, "was_unread")))
        elif kind == "moved":
            self._take_out(message_id, folder, bool(getattr(change, "was_unread")))
            self._bring_in(change)
        elif kind == "added":
            self._bring_in(change)
        elif kind == "folder_read":
            self._folder_read(folder)
        elif kind == "folder_emptied":
            self._folder_emptied(folder)
        elif kind == "folder_renamed":
            self._folder_renamed(folder, str(getattr(change, "to_folder")))

    def _concerns(self, spec: ViewSpec, mailbox: str) -> bool:
        """Whether the view may show mail of this mailbox (a Favorites view shows every mailbox, or those of a group)."""
        return spec.combined is not None or spec.account == mailbox

    def _shows_folder(self, folder: str) -> bool:
        spec = self._spec
        return spec is not None and spec.combined is None and spec.folder == folder

    def _counts_changed(self) -> None:
        if self._spec is not None and self._spec.kind is ViewKind.FOLDER:
            self.counts_changed.emit(self._total, self._unread)
        else:
            self.counts_changed.emit(len(self._rows.loaded), sum(1 for row in self._rows.loaded if "seen" not in getattr(row, "flags")))

    def _apply_flags(self, message_id: str, folder: str, flags: tuple[str, ...], was_unread: bool, unread: bool) -> None:
        position = self._find_position(message_id)
        if position >= 0:
            self.replace_flags(message_id, flags)  # the row changes in place and its unread count with it
        elif self._shows_folder(folder) and was_unread != unread:
            self._unread = max(0, self._unread + (1 if unread else -1))  # a message of this folder that is not loaded
            self._counts_changed()

    def _group_index(self, position: int) -> int:
        """The group (in the full group counts) of the message at this position."""
        start = 0
        counts = self._group_counts or []
        for number, (_, _, count) in enumerate(counts):
            if position < start + count:
                return number
            start += count
        return len(counts) - 1

    def _take_out(self, message_id: str, folder: str, was_unread: bool) -> None:
        """A message left this view: its row goes, its group count follows, an emptied group heading goes too."""
        position = self._find_position(message_id)
        if position < 0:
            if self._shows_folder(folder):
                self._take_out_unseen(was_unread)
            return
        rows = self._remove_rows_of(position)
        if rows is not None:
            self.beginRemoveRows(QModelIndex(), rows[0], rows[1])
        self._drop(position, was_unread)
        self._layout_groups()
        if rows is not None:
            self.endRemoveRows()
        self.groups_changed.emit()
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

    def _drop(self, position: int, was_unread: bool) -> None:
        """The data of a row that goes (call between begin and end of the removal)."""
        if self._group_counts is not None:
            number = self._group_index(position)
            key, name, count = self._group_counts[number]
            if count <= 1:
                del self._group_counts[number]
            else:
                self._group_counts[number] = (key, name, count - 1)
        if self._spec is not None and self._spec.kind is ViewKind.FOLDER:
            del self._rows.slots[position]
            self._total = max(0, self._total - 1)
            if was_unread:
                self._unread = max(0, self._unread - 1)
        else:
            del self._rows.loaded[position]
        self._rows.shifts += 1
        self._rows.requested.clear()  # the pages asked for counted places that have moved

    def _take_out_unseen(self, was_unread: bool) -> None:
        """A message of this folder that is not loaded went: it was in a part not loaded yet. Its place is not known, so the
        loaded parts after the first part not loaded are let go (fetched again when they are shown), and one place less."""
        slots = self._rows.slots
        gap = next((number for number, slot in enumerate(slots) if slot is None), len(slots))
        reset = any(slot is not None for slot in slots[gap:])  # loaded parts after the gap: their places may have moved
        if reset:
            self.beginResetModel()
        elif self._group_counts is None and self._display_rows > 0:
            self.beginRemoveRows(QModelIndex(), self._display_rows - 1, self._display_rows - 1)  # one empty place less at the end
        for number in range(gap, len(slots)):
            slots[number] = None
        if slots:
            slots.pop()
        self._total = max(0, self._total - 1)
        if was_unread:
            self._unread = max(0, self._unread - 1)
        self._rows.shifts += 1
        self._rows.requested.clear()
        if self._group_counts is not None:
            self._refresh_group_counts(reset)  # which heading lost a message is not known: the counts are asked again
        elif reset:
            self._layout_groups()
            self.endResetModel()
        elif self._display_rows > 0:
            self._layout_groups()
            self.endRemoveRows()
        self._counts_changed()

    def _refresh_group_counts(self, resetting: bool) -> None:
        """The counts of the headings are fetched again (only the counts, not the rows), when a change happened where the
        list cannot see which heading it belongs to."""
        spec = self._spec
        if spec is None:
            return
        generation, shifts, sort = self._generation, self._rows.shifts, spec.sort

        def arrived(result: object) -> None:
            if generation != self._generation or shifts != self._rows.shifts:
                return
            found: list[object] = list(result) if isinstance(result, (tuple, list)) else []
            self._group_counts = [(str(getattr(group, "key")), group_name(sort, str(getattr(group, "value"))), int(getattr(group, "count"))) for group in found]
            self._relayout()

        if resetting:
            self._layout_groups()
            self.endResetModel()
        else:
            self._relayout()
        command = "message.combined-groups" if spec.combined is not None else "message.groups"
        values = {name: value for name, value in self._values(spec, 0).items() if name not in ("offset", "limit")}
        self._backend.call(command, values, arrived, self._failed, owner=self)

    def _relayout(self) -> None:
        """The headings were counted anew: when the rows on screen keep their places nothing is redrawn; only when they
        moved (rare: a change where the list cannot see it) is the list laid out again."""
        groups, starts, display = self._computed_layout()
        if starts == self._group_starts and display == self._display_rows and [group.count for group in groups] == [group.count for group in self._groups]:
            self._groups = groups
            return
        self.beginResetModel()
        self._groups, self._group_starts, self._display_rows = groups, starts, display
        self.endResetModel()
        self.groups_changed.emit()

    def _may_hold(self, change: object) -> bool:
        """Whether a message that came into a folder may belong in this view; when it may, the engine says where."""
        spec = self._spec
        if spec is None:
            return False
        folder, role, row = str(getattr(change, "to_folder", "") or getattr(change, "folder", "")), str(getattr(change, "role", "")), getattr(change, "row", None)
        if row is None:
            return False
        flags, tags = tuple(getattr(row, "flags")), tuple(str(tag).casefold() for tag in getattr(row, "tags"))
        if spec.combined is not None:
            everywhere = role not in ("trash", "junk") and folder != "Outbox"
            wanted = {
                "inboxes": role == "inbox", "sent": role == "sent", "drafts": role == "drafts", "junk": role == "junk", "trash": role == "trash",
                "outbox": folder == "Outbox", "unread": everywhere and "seen" not in flags, "flagged": everywhere and "flagged" in flags,
                "tagged": everywhere and (spec.tag or "").casefold() in tags,
            }
            return wanted.get(spec.combined, False)
        if spec.folder is not None and spec.folder != folder:
            return False
        if spec.unread is not None and spec.unread == ("seen" in flags):
            return False
        if spec.flagged is not None and spec.flagged != ("flagged" in flags):
            return False
        return spec.tag is None or spec.tag.casefold() in tags

    def _bring_in(self, change: object) -> None:
        """A message came into a folder: when it belongs in this view, the engine says where it stands and under which
        heading, and its row is put there."""
        if not self._may_hold(change):
            return
        spec = self._spec
        row = getattr(change, "row", None)
        if spec is None or row is None:
            return
        message_id = str(getattr(row, "message_id"))
        generation, shifts = self._generation, self._rows.shifts

        def located(result: object) -> None:
            if generation != self._generation:
                return
            if shifts != self._rows.shifts:
                self._bring_in(change)  # a row came in or went out meanwhile: its place is asked again
                return
            position = getattr(result, "position", None)
            if isinstance(position, int) and self._find_position(message_id) < 0:
                self._insert(position, row, str(getattr(result, "group_key", "")), str(getattr(result, "group_name", "")))

        values = {name: value for name, value in self._values(spec, 0).items() if name not in ("offset", "limit")}
        values["message"] = message_id
        if spec.combined is not None:
            values["account"] = str(getattr(change, "mailbox"))
        self._backend.call("message.combined-locate" if spec.combined is not None else "message.locate", values, located, self._failed, owner=self)

    def _insert(self, position: int, row: object, key: str, name: str) -> None:
        """Put a row at its place, under its heading (a new heading when its group was not there)."""
        spec = self._spec
        if spec is None:
            return
        folder_view = spec.kind is ViewKind.FOLDER
        if not folder_view and position > len(self._rows.loaded):
            if self._group_counts is not None:
                self._count_into_group(position, key, name)  # it comes with a later batch: only its heading counts it now
            return
        new_group = self._count_into_group(position, key, name) if self._group_counts is not None else False
        if folder_view:
            self._rows.slots.insert(position, row)
            self._total += 1
            if "seen" not in getattr(row, "flags"):
                self._unread += 1
        else:
            self._rows.loaded.insert(position, row)
        self._rows.shifts += 1
        self._rows.requested.clear()
        groups, starts, display = self._computed_layout()
        rows: tuple[int, int] | None
        if self._group_counts is None:
            rows = (position, position)
        else:
            group = next((group for group in groups if group.message_start <= position < group.message_start + group.count), None)
            if group is None:
                rows = None
            elif new_group:
                rows = (group.display_start, group.display_start + group.shown)
            elif group.folded:
                rows = None
            else:
                rows = (group.display_start + 1 + position - group.message_start,) * 2
        if rows is not None:
            self.beginInsertRows(QModelIndex(), rows[0], rows[1])
        self._groups, self._group_starts, self._display_rows = groups, starts, display
        if rows is not None:
            self.endInsertRows()
        self.groups_changed.emit()
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

    def _folder_read(self, folder: str) -> None:
        """Every message of a folder was marked read: its rows turn read."""
        rows = self._rows.slots if self._spec is not None and self._spec.kind is ViewKind.FOLDER else self._rows.loaded
        for position, row in enumerate(rows):
            if row is not None and str(getattr(row, "folder")) == folder and "seen" not in getattr(row, "flags"):
                self.replace_flags(str(getattr(row, "message_id")), tuple(sorted((*getattr(row, "flags"), "seen"))))
        if self._shows_folder(folder):
            self._unread = 0
        self._counts_changed()

    def _folder_emptied(self, folder: str) -> None:
        """Every message of a folder was deleted for good: its rows go."""
        if self._shows_folder(folder):
            self.beginResetModel()  # the whole view is empty now: nothing on it to keep
            self._rows = _Rows()
            self._total = self._unread = 0
            self._group_counts = [] if self._group_counts is not None else None
            self._layout_groups()
            self.endResetModel()
            self.groups_changed.emit()
            self._counts_changed()
            return
        for row in [row for row in self._rows.loaded if str(getattr(row, "folder")) == folder]:
            self._take_out(str(getattr(row, "message_id")), folder, "seen" not in getattr(row, "flags"))

    def _folder_renamed(self, old: str, new: str) -> None:
        """A folder got another name: the rows show it, and a view of that folder (or one below it) follows."""
        def renamed(name: str) -> str:
            return new + name[len(old):] if name == old or name.startswith(old + "/") else name

        rows = self._rows.slots if self._spec is not None and self._spec.kind is ViewKind.FOLDER else self._rows.loaded
        for position, row in enumerate(rows):
            if row is None or not dataclasses.is_dataclass(row) or isinstance(row, type):
                continue
            if renamed(str(getattr(row, "folder"))) != str(getattr(row, "folder")):
                rows[position] = dataclasses.replace(row, folder=renamed(str(getattr(row, "folder"))))
        if self._spec is not None and self._spec.folder is not None:
            self._spec = dataclasses.replace(self._spec, folder=renamed(self._spec.folder))
        self.refresh_cells()

    # --- fetching -----------------------------------------------------------------------------

    def _values(self, spec: ViewSpec, offset: int) -> dict[str, object]:
        if spec.combined is not None:
            combined: dict[str, object] = {"view": spec.combined, "query": spec.query, "sort": spec.sort, "oldest_first": spec.oldest_first, "offset": offset, "limit": PAGE_SIZE}
            if spec.tag:
                combined["tag"] = spec.tag
            if spec.combined_accounts:
                combined["accounts"] = list(spec.combined_accounts)
            return combined
        if spec.kind is ViewKind.FOLDER:
            return {"account": spec.account, "folder": spec.folder, "sort": spec.sort, "oldest_first": spec.oldest_first, "offset": offset, "limit": PAGE_SIZE}
        values: dict[str, object] = {"account": spec.account, "query": spec.query, "sort": spec.sort, "oldest_first": spec.oldest_first, "offset": offset, "limit": PAGE_SIZE}
        if spec.folder is not None:
            values["folder"] = spec.folder
        if spec.conversations:
            values["conversations"] = True
        for name in ("unread", "flagged", "has_attachments", "date_from", "date_to", "minimum_size", "maximum_size", "tag"):
            value = getattr(spec, name)
            if value is not None:
                values[name] = value
        return values

    def _request_page(self, page: int) -> None:
        spec = self._spec
        if spec is None or page in self._rows.requested:
            return
        self._rows.requested.add(page)
        generation, shifts = self._generation, self._rows.shifts

        def arrived(result: object) -> None:
            if generation != self._generation:
                return  # the view has changed since: this answer is for something that is no longer shown
            rows = list(getattr(result, "messages"))
            if shifts != self._rows.shifts:  # a row came in or went out since it was asked: its places are not those of now
                self._rows.requested.discard(page)
                if page == 0 or any(slot is None for slot in self._rows.slots[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]):
                    self._request_page(page)
                return
            if page == 0 and not self._rows.slots:
                self.beginResetModel()
                self._total, self._unread = int(getattr(result, "total")), int(getattr(result, "unread"))
                self._fill(0, rows)
                self._layout_groups()
                self.endResetModel()
                self.groups_changed.emit()
                self.counts_changed.emit(self._total, self._unread)
            else:
                self._fill(page * PAGE_SIZE, rows)
                first, last = self.display_row(page * PAGE_SIZE), self.display_row(min((page + 1) * PAGE_SIZE, self._total) - 1)
                if self._group_counts is not None:  # folded groups may hide either end: redraw from the first row to the last
                    first, last = 0, self._display_rows - 1
                if first >= 0 and last >= first:
                    self.dataChanged.emit(self.index(first, 0), self.index(last, max(0, self.columnCount() - 1)))
            self.settled = True
            self.loaded.emit()

        self._backend.call("message.list", self._values(spec, page * PAGE_SIZE), arrived, self._failed, owner=self)

    def _request_more(self) -> None:
        spec = self._spec
        if spec is None or self._rows.loading_more or self._rows.exhausted:
            return
        self._rows.loading_more = True
        generation = self._generation
        offset = len(self._rows.loaded)

        def arrived(result: object) -> None:
            if generation != self._generation:
                return
            rows: list[object] = list(result) if isinstance(result, (tuple, list)) else []
            self._rows.loading_more = False
            if len(rows) < PAGE_SIZE:
                self._rows.exhausted = True
            if rows:
                before = self._display_rows
                self._rows.loaded.extend(rows)
                self._layout_groups()  # new messages only ever add rows at the end (to the last group or under new headers)
                after, self._display_rows = self._display_rows, before
                if after > before:
                    self.beginInsertRows(QModelIndex(), before, after - 1)
                    self._display_rows = after
                    self.endInsertRows()
                    self.groups_changed.emit()
                else:
                    self._display_rows = after
            self.counts_changed.emit(len(self._rows.loaded), sum(1 for row in self._rows.loaded if "seen" not in getattr(row, "flags")))
            self.settled = True
            self.loaded.emit()

        def failed(error: ChronoMailError) -> None:
            self._rows.loading_more = False
            self._failed(error)

        self._backend.call("message.combined" if spec.combined is not None else "message.search", self._values(spec, offset), arrived, failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        self.load_failed.emit(str(error))


FAVORITES: tuple[tuple[str, str, str], ...] = (  # view id (message.combined), label, icon
    ("inboxes", "All Inboxes", "inbox"),
    ("unread", "Unread", "unread"),
    ("flagged", "Flagged", "flagged"),
    ("outbox", "Outbox", "outbox"),
    ("sent", "Sent", "sent"),
    ("drafts", "Drafts", "drafts"),
    ("junk", "Junk", "junk"),
    ("trash", "Trash", "trash"),
)
_FAVORITE_ROLE: dict[str, str] = {"inboxes": "inbox", "sent": "sent", "drafts": "drafts", "junk": "junk", "trash": "trash"}


class FolderTreeModel(QStandardItemModel):
    """Favorites, and then the accounts with their folders and the number of unread messages."""

    show_favorites: bool = True
    show_local_folders: bool = True
    show_tags: bool = True

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tags: list[tuple[str, str]] = []  # (name, #rrggbb) of the tags for mail, in the person's order: the Tags at the bottom
        self.pins: list[tuple[str, str, str, str]] = []  # pin id, account, folder, name
        self.groups: list[tuple[str, str, tuple[str, ...]]] = []  # group id, name, account ids
        self.hidden: set[str] = set()  # standard favorites that are switched off

    def populate(self, accounts: list[object], folders: dict[str, list[object]]) -> None:
        """Rebuild the tree. accounts: AccountSummary results; folders: account id -> FolderInfo results."""
        self.clear()
        root = self.invisibleRootItem()
        # Accounts that keep their mail in Local Folders have no folders of their own: Local Folders shows them (and counts them once).
        places = [account for account in accounts if str(getattr(account, "storage", "own")) != "local"]
        places.sort(key=lambda account: str(getattr(account, "kind", "pop3")) == "local")  # Local Folders last
        if not self.show_local_folders:
            places = [account for account in places if str(getattr(account, "kind", "pop3")) != "local"]
        mailboxes = places
        if mailboxes and self.show_favorites:
            self._favorites(root, mailboxes, folders)
        real = [account for account in places if str(getattr(account, "kind", "pop3")) != "local"]
        members = {member for _, _, accounts_of in self.groups for member in accounts_of}
        shown = {str(getattr(account, "account_id")): account for account in places}
        for group_id, name, member_ids in self.groups:
            present = [shown[member] for member in member_ids if member in shown]
            if not present and not any(member in {str(getattr(a, "account_id")) for a in accounts} for member in member_ids):
                continue
            self._group(root, group_id, name, present, member_ids, folders)
        for account in places:
            if str(getattr(account, "account_id")) not in members:
                self._account_node(root, account, folders)
        if mailboxes and self.show_tags and self.tags:
            self._tags(root)

    def _tags(self, root: QStandardItem) -> None:
        """Tags: one entry per tag, showing the mail of every mailbox that carries it (the combined view "tagged")."""
        section = self._section(root, "Tags", "section")
        section.setSelectable(False)
        for name, colour in self.tags:
            item = QStandardItem(name)
            item.setEditable(False)
            item.setIcon(tag_icon(QColor(colour)))
            item.setData("tagview", KIND_ROLE)
            item.setData("", ACCOUNT_ROLE)
            item.setData(name, FOLDER_PATH_ROLE)
            self._label(item, name, 0)
            section.appendRow(item)

    def _group(self, root: QStandardItem, group_id: str, name: str, present: list[object], member_ids: tuple[str, ...], folders: dict[str, list[object]]) -> None:
        """A named set of accounts: one combined inbox, then the accounts."""
        node = self._section(root, name, "group")
        node.setData(group_id, GROUP_ROLE)
        node.setIcon(folder_icon("account"))
        unread = sum(
            int(getattr(info, "unread")) for member in member_ids for info in folders.get(member, []) if str(getattr(info, "role")) == "inbox"
        )
        inbox = QStandardItem("All Inboxes")
        inbox.setEditable(False)
        inbox.setIcon(folder_icon("inbox"))
        inbox.setData("groupinbox", KIND_ROLE)
        inbox.setData("", ACCOUNT_ROLE)
        inbox.setData(group_id, FOLDER_PATH_ROLE)  # tells the groups' inboxes apart in the selection
        inbox.setData(group_id, GROUP_ROLE)
        inbox.setData("inbox", FOLDER_ROLE_ROLE)
        self._label(inbox, "All Inboxes", unread)
        node.appendRow(inbox)
        for account in present:
            self._account_node(node, account, folders)

    def _account_node(self, parent: QStandardItem, account: object, folders: dict[str, list[object]]) -> None:
        account_id = str(getattr(account, "account_id"))
        top = QStandardItem("Local Folders" if str(getattr(account, "kind", "pop3")) == "local" else str(getattr(account, "shown_name")))
        top.setEditable(False)
        top.setIcon(folder_icon("local" if str(getattr(account, "kind", "pop3")) == "local" else "account"))
        top.setData("account", KIND_ROLE)
        top.setData(account_id, ACCOUNT_ROLE)
        parent.appendRow(top)
        nodes: dict[tuple[str, ...], QStandardItem] = {}
        ordered = sorted(folders.get(account_id, []), key=lambda info: (_order(str(getattr(info, "role"))), str(getattr(info, "path")).lower()))
        for kind, label in (("unread", "Unread"), ("flagged", "Flagged")):
            item = QStandardItem(label)
            item.setEditable(False)
            item.setIcon(folder_icon(kind))
            item.setData(kind, KIND_ROLE)
            item.setData(account_id, ACCOUNT_ROLE)
            top.appendRow(item)
        for info in ordered:
            path = tuple(str(getattr(info, "path")).split("/"))
            for depth in range(1, len(path)):  # parents that exist only as part of a path
                if path[:depth] not in nodes:
                    nodes[path[:depth]] = self._folder_item(account_id, path[:depth], "other", 0)
                    (nodes[path[:depth - 1]] if depth > 1 else top).appendRow(nodes[path[:depth]])
            item = nodes.get(path) or self._folder_item(account_id, path, str(getattr(info, "role")), int(getattr(info, "unread")))
            item.setData(str(getattr(info, "role")), FOLDER_ROLE_ROLE)
            item.setData(int(getattr(info, "unread")), UNREAD_ROLE)
            self._label(item, path[-1], int(getattr(info, "unread")))
            item.setIcon(folder_icon("outbox" if path == ("Outbox",) else str(getattr(info, "role"))))  # a parent that was made earlier takes its real role now
            if path not in nodes:
                nodes[path] = item
                (nodes[path[:-1]] if len(path) > 1 else top).appendRow(item)

    @staticmethod
    def _section(root: QStandardItem, title: str, kind: str) -> QStandardItem:
        section = QStandardItem(title)
        section.setEditable(False)
        section.setData(kind, KIND_ROLE)
        section.setData("", ACCOUNT_ROLE)
        font = section.font()
        font.setBold(True)
        section.setFont(font)
        root.appendRow(section)
        return section

    def _favorites(self, root: QStandardItem, mailboxes: list[object], folders: dict[str, list[object]]) -> None:
        """The same folder of every mailbox in one list: all inboxes, all unread messages and so on."""
        section = self._section(root, "Favorites", "section")
        section.setSelectable(False)  # a heading: the list never shows it
        for view, label, icon in FAVORITES:
            if view in self.hidden:
                continue
            unread = 0
            for account in mailboxes:
                for info in folders.get(str(getattr(account, "account_id")), []):
                    if (view in _FAVORITE_ROLE and str(getattr(info, "role")) == _FAVORITE_ROLE[view]) or (view == "outbox" and str(getattr(info, "path")) == "Outbox"):
                        unread += int(getattr(info, "unread"))
                    elif view == "unread" and str(getattr(info, "role")) not in ("trash", "junk") and str(getattr(info, "path")) != "Outbox":
                        unread += int(getattr(info, "unread"))
            item = QStandardItem(label)
            item.setEditable(False)
            item.setIcon(folder_icon(icon))
            item.setData("favorite", KIND_ROLE)
            item.setData("", ACCOUNT_ROLE)
            item.setData(view, FOLDER_PATH_ROLE)
            item.setData(_FAVORITE_ROLE.get(view, "other"), FOLDER_ROLE_ROLE)
            item.setData(unread, UNREAD_ROLE)
            self._label(item, label, unread)
            section.appendRow(item)
        known = {str(getattr(account, "account_id")) for account in mailboxes}
        for pin_id, account_id, folder, name in self.pins:
            if account_id not in known:
                continue
            info = next((i for i in folders.get(account_id, []) if str(getattr(i, "path")) == folder), None)
            if info is None:
                continue  # the folder is gone: the pin stays in the file, but there is nothing to show
            unread = int(getattr(info, "unread"))
            item = QStandardItem()
            item.setEditable(False)
            item.setIcon(folder_icon("outbox" if folder == "Outbox" else str(getattr(info, "role"))))
            item.setData("pinlink", KIND_ROLE)
            item.setData(account_id, ACCOUNT_ROLE)
            item.setData(folder, FOLDER_PATH_ROLE)
            item.setData(str(getattr(info, "role")), FOLDER_ROLE_ROLE)
            item.setData(pin_id, PIN_ROLE)
            item.setData(unread, UNREAD_ROLE)
            self._label(item, name or folder.rsplit("/", 1)[-1], unread)
            section.appendRow(item)

    @staticmethod
    def _label(item: QStandardItem, name: str, unread: int) -> None:
        """The folder's name; the number of unread messages is drawn by the folder list at the right edge, in bold."""
        item.setText(name)
        item.setData(name, NAME_ROLE)
        item.setData(unread, COUNT_ROLE)

    @staticmethod
    def _folder_item(account_id: str, path: tuple[str, ...], role: str, unread: int) -> QStandardItem:
        item = QStandardItem(path[-1])
        item.setEditable(False)
        item.setData("folder", KIND_ROLE)
        item.setData(account_id, ACCOUNT_ROLE)
        item.setData("/".join(path), FOLDER_PATH_ROLE)
        item.setData(role, FOLDER_ROLE_ROLE)
        item.setData(unread, UNREAD_ROLE)
        item.setIcon(folder_icon("outbox" if path == ("Outbox",) else role))
        FolderTreeModel._label(item, path[-1], unread)
        return item


_ROLE_ORDER: dict[str, int] = {"inbox": 0, "drafts": 1, "sent": 2, "other": 3, "junk": 5, "trash": 6, "archive": 4}


def _order(role: str) -> int:
    return _ROLE_ORDER.get(role, 3)
