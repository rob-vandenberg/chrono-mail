import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import override

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "models.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial folder and message list models

PAGE_SIZE: int = 100
ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
FOLDER_PATH_ROLE: int = Qt.ItemDataRole.UserRole + 2
ACCOUNT_ROLE: int = Qt.ItemDataRole.UserRole + 3
KIND_ROLE: int = Qt.ItemDataRole.UserRole + 4
UNREAD_ROLE: int = Qt.ItemDataRole.UserRole + 5
FOLDER_ROLE_ROLE: int = Qt.ItemDataRole.UserRole + 6
ROW_PLACEHOLDER: None = None


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

    @property
    def kind(self) -> ViewKind:
        searching = bool(self.query) or self.unread is not None or self.flagged is not None or self.has_attachments is not None or self.folder is None
        return ViewKind.QUERY if searching else ViewKind.FOLDER


@dataclass
class _Rows:
    pages: dict[int, list[object]] = field(default_factory=dict)  # folder views
    requested: set[int] = field(default_factory=set)
    loaded: list[object] = field(default_factory=list)  # query views
    exhausted: bool = False
    loading_more: bool = False


class MessageListModel(QAbstractListModel):
    """The rows of one folder or search, fetched a page at a time through engine commands."""

    counts_changed = Signal(int, int)  # total, unread
    load_failed = Signal(str)
    loaded = Signal()

    def __init__(self, backend: Backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._spec: ViewSpec | None = None
        self._generation: int = 0
        self._total: int = 0
        self._unread: int = 0
        self._rows: _Rows = _Rows()

    # --- the view -----------------------------------------------------------------------------

    @property
    def spec(self) -> ViewSpec | None:
        return self._spec

    def show(self, spec: ViewSpec | None) -> None:
        """Start showing something else (or nothing)."""
        self.beginResetModel()
        self._generation += 1
        self._spec = spec
        self._total = self._unread = 0
        self._rows = _Rows()
        self.endResetModel()
        if spec is None:
            self.counts_changed.emit(0, 0)
            return
        if spec.kind is ViewKind.FOLDER:
            self._request_page(0)
        else:
            self._request_more()

    def reload(self) -> None:
        """Fetch the same view again (after mail arrived or messages were changed)."""
        self.show(self._spec)

    # --- Qt model interface -------------------------------------------------------------------

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        if self._spec is not None and self._spec.kind is ViewKind.QUERY:
            return len(self._rows.loaded)
        return self._total

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or role != ROW_ROLE:
            return None
        return self.row_at(index.row(), fetch=True)

    @override
    def canFetchMore(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> bool:
        return self._spec is not None and self._spec.kind is ViewKind.QUERY and not self._rows.exhausted and not self._rows.loading_more

    @override
    def fetchMore(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> None:
        if self.canFetchMore():
            self._request_more()

    def row_at(self, row: int, fetch: bool = False) -> object:
        """The message row at this position, or None while its page is still being fetched."""
        if self._spec is None or row < 0:
            return None
        if self._spec.kind is ViewKind.QUERY:
            return self._rows.loaded[row] if row < len(self._rows.loaded) else None
        page, offset = divmod(row, PAGE_SIZE)
        rows = self._rows.pages.get(page)
        if rows is None:
            if fetch:
                self._request_page(page)
            return None
        return rows[offset] if offset < len(rows) else None

    def replace_flags(self, message_id: str, flags: tuple[str, ...]) -> None:
        """Show new flags on a loaded row without fetching anything (after a flag change)."""
        position = self.find(message_id)
        if position < 0:
            return
        row = self.row_at(position)
        if row is None or not dataclasses.is_dataclass(row) or isinstance(row, type):
            return
        was_unread = "seen" not in getattr(row, "flags")
        replaced = dataclasses.replace(row, flags=flags)
        if self._spec is not None and self._spec.kind is ViewKind.QUERY:
            self._rows.loaded[position] = replaced
            self._unread = sum(1 for loaded in self._rows.loaded if "seen" not in getattr(loaded, "flags"))
            self.counts_changed.emit(len(self._rows.loaded), self._unread)
        else:
            self._rows.pages[position // PAGE_SIZE][position % PAGE_SIZE] = replaced
            now_unread = "seen" not in flags
            if was_unread != now_unread:
                self._unread = max(0, self._unread + (1 if now_unread else -1))
                self.counts_changed.emit(self._total, self._unread)
        self.dataChanged.emit(self.index(position), self.index(position))

    def message_ids(self) -> list[str]:
        """The ids of the rows that are loaded now (for finding a message again after a reload)."""
        pages = sorted(self._rows.pages.items())
        rows = [row for _, page in pages for row in page] if self._spec is not None and self._spec.kind is ViewKind.FOLDER else self._rows.loaded
        return [str(getattr(row, "message_id")) for row in rows]

    def find(self, message_id: str) -> int:
        """The position of a loaded message, or -1."""
        if self._spec is None:
            return -1
        if self._spec.kind is ViewKind.QUERY:
            for position, row in enumerate(self._rows.loaded):
                if getattr(row, "message_id") == message_id:
                    return position
            return -1
        for page, rows in self._rows.pages.items():
            for offset, row in enumerate(rows):
                if getattr(row, "message_id") == message_id:
                    return page * PAGE_SIZE + offset
        return -1

    # --- fetching -----------------------------------------------------------------------------

    def _values(self, spec: ViewSpec, offset: int) -> dict[str, object]:
        if spec.kind is ViewKind.FOLDER:
            return {"account": spec.account, "folder": spec.folder, "sort": spec.sort, "oldest_first": spec.oldest_first, "offset": offset, "limit": PAGE_SIZE}
        values: dict[str, object] = {"account": spec.account, "query": spec.query, "offset": offset, "limit": PAGE_SIZE}
        if spec.folder is not None:
            values["folder"] = spec.folder
        for name in ("unread", "flagged", "has_attachments"):
            value = getattr(spec, name)
            if value is not None:
                values[name] = value
        return values

    def _request_page(self, page: int) -> None:
        spec = self._spec
        if spec is None or page in self._rows.requested:
            return
        self._rows.requested.add(page)
        generation = self._generation

        def arrived(result: object) -> None:
            if generation != self._generation:
                return  # the view has changed since: this answer is for something that is no longer shown
            rows = list(getattr(result, "messages"))
            if page == 0:
                self.beginResetModel()
                self._total, self._unread = int(getattr(result, "total")), int(getattr(result, "unread"))
                self._rows.pages[0] = rows
                self.endResetModel()
                self.counts_changed.emit(self._total, self._unread)
            else:
                self._rows.pages[page] = rows
                first = page * PAGE_SIZE
                self.dataChanged.emit(self.index(first), self.index(min(first + len(rows), self._total) - 1))
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
                self.beginInsertRows(QModelIndex(), offset, offset + len(rows) - 1)
                self._rows.loaded.extend(rows)
                self.endInsertRows()
            self.counts_changed.emit(len(self._rows.loaded), sum(1 for row in self._rows.loaded if "seen" not in getattr(row, "flags")))
            self.loaded.emit()

        def failed(error: ChronoMailError) -> None:
            self._rows.loading_more = False
            self._failed(error)

        self._backend.call("message.search", self._values(spec, offset), arrived, failed, owner=self)

    def _failed(self, error: ChronoMailError) -> None:
        self.load_failed.emit(str(error))


class FolderTreeModel(QStandardItemModel):
    """Accounts and their folders, with the number of unread messages."""

    def populate(self, accounts: list[object], folders: dict[str, list[object]]) -> None:
        """Rebuild the tree. accounts: AccountSummary results; folders: account id -> FolderInfo results."""
        self.clear()
        root = self.invisibleRootItem()
        for account in accounts:
            account_id = str(getattr(account, "account_id"))
            top = QStandardItem(str(getattr(account, "email_address")))
            top.setEditable(False)
            top.setData("account", KIND_ROLE)
            top.setData(account_id, ACCOUNT_ROLE)
            root.appendRow(top)
            nodes: dict[tuple[str, ...], QStandardItem] = {}
            ordered = sorted(folders.get(account_id, []), key=lambda info: (_order(str(getattr(info, "role"))), str(getattr(info, "path")).lower()))
            for kind, label in (("unread", "Unread"), ("flagged", "Flagged")):
                item = QStandardItem(label)
                item.setEditable(False)
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
                if path not in nodes:
                    nodes[path] = item
                    (nodes[path[:-1]] if len(path) > 1 else top).appendRow(item)

    @staticmethod
    def _label(item: QStandardItem, name: str, unread: int) -> None:
        """The folder's name, with the number of unread messages after it, and in bold when there are some."""
        item.setText(f"{name}  ({unread})" if unread else name)
        font = item.font()
        font.setBold(unread > 0)
        item.setFont(font)

    @staticmethod
    def _folder_item(account_id: str, path: tuple[str, ...], role: str, unread: int) -> QStandardItem:
        item = QStandardItem(path[-1])
        item.setEditable(False)
        item.setData("folder", KIND_ROLE)
        item.setData(account_id, ACCOUNT_ROLE)
        item.setData("/".join(path), FOLDER_PATH_ROLE)
        item.setData(role, FOLDER_ROLE_ROLE)
        item.setData(unread, UNREAD_ROLE)
        FolderTreeModel._label(item, path[-1], unread)
        return item


_ROLE_ORDER: dict[str, int] = {"inbox": 0, "drafts": 1, "sent": 2, "other": 3, "junk": 5, "trash": 6, "archive": 4}


def _order(role: str) -> int:
    return _ROLE_ORDER.get(role, 3)
