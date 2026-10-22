import html
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Literal, override

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QStyleOptionViewItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from chrono_mail.engine.changes import Change
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_tasklist import (
    TASK_ADD, TASK_COMPLETE, TASK_DELETE, TASK_EDIT, TASK_LIST, TASK_MOVE, TASK_PLACES, TASK_REOPEN, TASK_SHOW, TaskInfo, TaskList, TaskPlace, TaskPlaces, TaskRow,
)
from chrono_mail.engine.commands_tasklist import AddParameters as TaskAdd
from chrono_mail.engine.commands_tasklist import EditParameters as TaskEdit
from chrono_mail.engine.commands_tasklist import ItemParameters as TaskItem
from chrono_mail.engine.commands_tasklist import MoveParameters as TaskMove
from chrono_mail.engine.commands_tasklist import ListParameters as TaskListParameters
from chrono_mail.engine.commands_tasklist import PlacesParameters as TaskPlacesParameters
from chrono_mail.ui.splitter import PaneSplitter
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.syncmarks import CONFLICT, MARK_SIZE, MARK_TIPS, SyncMarks, paint_mark
from chrono_mail.core.colours import NEW_TAG
from chrono_mail.ui.colours import green, red, theme
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimlist import MarkDelegate, PimItemModel, PimListPage
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.tagchips import TagColourBook, paint_chips
from chrono_mail.ui.tagging import tag_icon
from chrono_mail.ui.taskeditor import TaskEditor

# --- Version ------------------------------------------------------------
__version__ = "tasksview.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: every engine call typed; delete and move are typed steps
# v1.9.265: A row's height comes from its kind (KIND_ROLE: heading or task), which asks for nothing: the list fetched every task's row to work out
#           the heights (5,104 tasks: 52 requests at once, the last row after about 10 s); now only the rows that come into view (R-02, R-44)
# v1.1.245: Delete, Move to, New tag…, the greying, the search delay, the error line and the triangle are the shared ones of pimlist (R-07)
# v1.1.244: A change of one task moves, adds or removes only its row and the heading it needs or leaves (follow, drop); the list is never laid
#           out again for it and the selection, current task and scroll stay by themselves (R-06, D-146)
# v1.1.229: A click on a task's triangle opens its conflict; hovering a mark says what it is (the owner)
# v1.1.226: A task's sync mark before its title: the triangle for a conflict, the waiting mark for a change not yet on the server (D-194)
# v1.1.215: The separator between the list and the details is the program's one separator (D-188)
# v1.1.205: the list holds every task's place (task.places, from the index, never cut) and asks for the rows a page at a time as they come into view; only the lists shown are asked for, so hidden lists take no room; the count is of every task (R-02); task.list and task.places typed
# v1.1.172: Colours from the colour file: amber, grey and the tick of a task, the Tag button (D-158)
# v0.5.167: the toolbar folds like Mail's; Tag and Move to list have icons; the search field looks like Mail's (D-154)
# v0.5.159: the list follows the facts (D-146 step 5): one task in, out or changed (grouped again here, the selection kept by identity); the selection moves at the key press of a delete or move; tasks with an action under way are greyed; no reload after an action
# v0.0.63: Initial tasks section: lists, quick add, groups by date, status marks, detail pane, editor, tags, move, delete

ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
KIND_ROLE: int = Qt.ItemDataRole.UserRole + 2  # "header" or "task": known without the row, so asking it fetches nothing
HEADER_HEIGHT: int = 30
ROW_HEIGHT: int = 52
MARK: int = 22
PAGE: int = 100  # task rows asked for at a time, as they come into view
TABS: tuple[tuple[str, str], ...] = (("all", "All"), ("upcoming", "Upcoming"), ("completed", "Completed"))


def parse_when(text: str) -> date | datetime | None:
    if not text:
        return None
    return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text)


def due_text(text: str) -> str:
    """A due date for a list: 'Oct 12, 2026' or 'Oct 12, 2026 (17:00)'."""
    moment = parse_when(text)
    if moment is None:
        return ""
    if isinstance(moment, datetime):
        local = moment.astimezone() if moment.tzinfo else moment
        return f"{local.strftime('%b')} {local.day}, {local.year} ({local.strftime('%H:%M')})"
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def group_of(place: TaskPlace, today: date) -> tuple[int, str]:
    """The heading a task goes under, with its place in the order of the headings."""
    if place.done:
        return 90, "Completed"
    if not place.due_day:
        return 80, "No due date"
    day = date.fromisoformat(place.due_day)
    if day < today:
        return 0, "Overdue"
    if day == today:
        return 10, "Today"
    if day == today + timedelta(days=1):
        return 20, "Tomorrow"
    if day <= today + timedelta(days=7):
        return 30, "This week"
    if day <= today + timedelta(days=31):
        return 40, "This month"
    return 50, "Later"


HEADING_ORDER: dict[str, int] = {"Overdue": 0, "Today": 10, "Tomorrow": 20, "This week": 30, "This month": 40, "Later": 50, "No due date": 80, "Completed": 90}


def grouped(places: Sequence[TaskPlace], day: date) -> list[tuple[str, TaskPlace | str]]:
    """The tasks in the engine's order under their headings (a stable sort by the heading keeps that order inside each group)."""
    entries: list[tuple[str, TaskPlace | str]] = []
    last = ""
    for place in sorted(places, key=lambda place: group_of(place, day)[0]):
        heading = group_of(place, day)[1]
        if heading != last:
            entries.append(("header", heading))
            last = heading
        entries.append(("task", place))
    return entries


class TaskListModel(PimItemModel):
    """Every task asked for, in groups: a heading row for each group, then its tasks (headings are rows that cannot be
    selected). The list holds the place of every task (from the index, never cut); the row of a task is asked for when it
    comes into view, a page at a time, and painted when it is in."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entries: list[tuple[str, TaskPlace | str]] = []  # ("header", text) or ("task", place)
        self.loaded: dict[str, TaskRow] = {}  # the rows that have come in, by task
        self.asked: dict[str, int] = {}  # rows asked for and not in yet: the number of the request that asked
        self.fetch: Callable[[list[str], int], None] | None = None  # asks the rows of these tasks for that request number
        self._requests: int = 0

    def load(self, places: Sequence[TaskPlace], today: date | None = None) -> None:
        """Another view (lists ticked, tab, search): the tasks in the engine's order, grouped; every row is asked for again. A change of one
        task never comes here (follow, drop)."""
        entries = grouped(places, today or date.today())
        self.beginResetModel()
        self.entries = entries
        self.loaded = {}
        self.asked = {}
        self.endResetModel()
        self.want(0)  # the first page, also before anything is painted

    # --- one task changed (D-146): only its row moves, comes or goes, with the heading it needs or leaves ----------------------------------

    def follow(self, places: Sequence[TaskPlace], item: str, today: date | None = None) -> None:
        """The places as they are now, where only this task differs from what the list shows: it is moved (so a selection on it stays with it),
        added, or taken away; a heading it needs comes, one it leaves empty goes; every other row stays where it is."""
        wanted = grouped(places, today or date.today())
        at = next((position for position, (_, payload) in enumerate(wanted) if isinstance(payload, TaskPlace) and payload.item == item), -1)
        if at < 0:
            self.drop(item)
            return
        place = wanted[at][1]
        heading = next(payload for _, payload in reversed(wanted[:at]) if isinstance(payload, str))
        self._ensure_heading(heading)
        before = wanted[at - 1]  # the heading or the task it follows in the new order
        old = self.position_of(item)
        rest = [entry for entry in self.entries if not (isinstance(entry[1], TaskPlace) and entry[1].item == item)]
        after = next(position for position, entry in enumerate(rest) if _same_entry(entry, before)) + 1  # its place among the others
        if old < 0:
            self.beginInsertRows(QModelIndex(), after, after)
            self.entries.insert(after, ("task", place))
            self.endInsertRows()
        else:
            target = after if after < old else after + 1  # Qt counts the place before the move, the row itself included
            if target not in (old, old + 1):
                self.beginMoveRows(QModelIndex(), old, old, QModelIndex(), target)
                self.entries.insert(after, self.entries.pop(old))
                self.endMoveRows()
            position = self.position_of(item)
            self.entries[position] = ("task", place)
            self.dataChanged.emit(self.index(position), self.index(position))
        self._drop_empty_headings()

    def drop(self, item: str) -> None:
        """A task left the list: its row goes, and its heading when it was the last under it."""
        self.forget(item)
        position = self.position_of(item)
        if position < 0:
            return
        self.beginRemoveRows(QModelIndex(), position, position)
        del self.entries[position]
        self.endRemoveRows()
        self._drop_empty_headings()

    def _ensure_heading(self, heading: str) -> None:
        if any(kind == "header" and payload == heading for kind, payload in self.entries):
            return
        order = HEADING_ORDER.get(heading, 100)
        position = next((number for number, (kind, payload) in enumerate(self.entries) if kind == "header" and HEADING_ORDER.get(str(payload), 100) > order), len(self.entries))
        self.beginInsertRows(QModelIndex(), position, position)
        self.entries.insert(position, ("header", heading))
        self.endInsertRows()

    def _drop_empty_headings(self) -> None:
        position = 0
        while position < len(self.entries):
            if self.entries[position][0] == "header" and (position + 1 == len(self.entries) or self.entries[position + 1][0] == "header"):
                self.beginRemoveRows(QModelIndex(), position, position)
                del self.entries[position]
                self.endRemoveRows()
                continue
            position += 1

    def place_at(self, position: int) -> TaskPlace | None:
        payload = self.entries[position][1] if 0 <= position < len(self.entries) else None
        return payload if isinstance(payload, TaskPlace) else None

    def want(self, position: int) -> None:
        """The rows of the page from this row on that are neither in nor asked for are asked for."""
        if self.fetch is None:
            return
        items: list[str] = []
        seen = 0
        for _, payload in self.entries[position:]:
            if not isinstance(payload, TaskPlace):
                continue
            seen += 1
            if payload.item not in self.loaded and payload.item not in self.asked:
                items.append(payload.item)
            if seen >= PAGE:
                break
        if items:
            self._requests += 1
            for item in items:
                self.asked[item] = self._requests
            self.fetch(items, self._requests)

    def forget(self, item: str) -> None:
        """A task changed: its row is asked for again."""
        self.loaded.pop(item, None)
        self.asked.pop(item, None)

    def arrive(self, rows: Sequence[TaskRow], request: int) -> None:
        """Rows asked for came in; a row asked for again since (its task changed) waits for the newer answer."""
        taken = [row for row in rows if self.asked.get(row.item) == request]
        for row in taken:
            self.loaded[row.item] = row
            del self.asked[row.item]
        for row in taken:
            position = self.position_of(row.item)
            if position >= 0:
                self.dataChanged.emit(self.index(position), self.index(position))

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid() or self.entries[index.row()][0] == "header":
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        kind, payload = self.entries[index.row()]
        if role == KIND_ROLE:
            return kind  # the list's layout asks every row for this: it must never fetch a row
        row = self.loaded.get(payload.item) if isinstance(payload, TaskPlace) else None
        if isinstance(payload, TaskPlace) and row is None and payload.item not in self.asked:
            self.want(index.row())  # come into view: its page is asked for
        if role == Qt.ItemDataRole.DisplayRole:
            return payload if isinstance(payload, str) else (row.summary if row is not None else "")
        if role == ROW_ROLE:
            return (kind, payload) if isinstance(payload, str) else (("task", row) if row is not None else ("waiting", payload))
        return None

    def places(self) -> list[TaskPlace]:
        return [payload for _, payload in self.entries if isinstance(payload, TaskPlace)]

    def rows(self) -> list[TaskRow]:
        """The rows that came in, in the order of the list."""
        return [self.loaded[place.item] for place in self.places() if place.item in self.loaded]

    @override
    def position_of(self, item: str) -> int:
        return next((position for position, (_, payload) in enumerate(self.entries) if isinstance(payload, TaskPlace) and payload.item == item), -1)

    @override
    def item_at(self, position: int) -> str:
        place = self.place_at(position)
        return place.item if place is not None else ""


def _same_entry(first: tuple[str, TaskPlace | str], second: tuple[str, TaskPlace | str]) -> bool:
    """The same heading, or the same task (its place may have changed)."""
    if isinstance(first[1], TaskPlace) and isinstance(second[1], TaskPlace):
        return first[1].item == second[1].item
    return first == second


def draw_mark(painter: QPainter, centre: QPoint, row: TaskRow, size: int = MARK) -> None:
    """The round mark of a task: a ring (amber: to do, red with a bar: overdue), a green disc with a tick when done, a grey ring when cancelled."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRect(centre.x() - size // 2, centre.y() - size // 2, size, size)
    status = row.status
    if row.done:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(green()))
        painter.drawEllipse(box)
        painter.setPen(QPen(QColor(theme().on_colour), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(box.left() + size * 28 // 100, box.top() + size * 54 // 100, box.left() + size * 44 // 100, box.top() + size * 70 // 100)
        painter.drawLine(box.left() + size * 44 // 100, box.top() + size * 70 // 100, box.left() + size * 74 // 100, box.top() + size * 32 // 100)
    else:
        colour = QColor(red()) if row.overdue else QColor(theme().task_grey if status == "CANCELLED" else theme().amber)
        painter.setPen(QPen(colour, 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box.adjusted(1, 1, -1, -1))
        if status == "IN-PROCESS":
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(box.adjusted(5, 5, -5, -5), 90 * 16, -max(30, row.percent * 360 // 100 or 180) * 16)
        if row.overdue:
            painter.setPen(QPen(colour, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(centre.x(), centre.y() - size // 5, centre.x(), centre.y() + size // 12)
            painter.drawPoint(centre.x(), centre.y() + size // 4)
    painter.restore()


class TaskDelegate(MarkDelegate):
    @override
    def _mark_area(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> tuple[QRect | None, str, str]:
        """Where a task's sync mark is drawn, its state and its item (no area without a mark): the same place paint uses."""
        entry = index.data(ROW_ROLE)
        model = index.model()
        if entry is None or entry[0] != "task" or not isinstance(model, TaskListModel):
            return None, "", ""
        found = entry[1]
        if not isinstance(found, TaskRow):
            return None, "", ""
        item = found.item
        state = model.mark_of(item)
        if not state:
            return None, "", item
        rect = _rect(option)
        metrics = QFontMetrics(_typed(option, "font", QFont))
        centre = rect.top() + 7 + metrics.height() // 2
        return QRect(rect.left() + 24 + MARK // 2 + 12, centre - MARK_SIZE // 2, MARK_SIZE, MARK_SIZE), state, item

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        """The height of a heading or a task: the list asks it of every row to lay itself out, so it is asked without the row (KIND_ROLE)."""
        return QSize(0, HEADER_HEIGHT if index.data(KIND_ROLE) == "header" else ROW_HEIGHT)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        entry = index.data(ROW_ROLE)
        if entry is None:
            return
        kind, payload = entry
        rect = _rect(option)
        palette = _typed(option, "palette", QPalette)
        font = _typed(option, "font", QFont)
        painter.save()
        base = palette.color(QPalette.ColorRole.Base)
        if kind == "header":
            painter.fillRect(rect, base)
            heading = QFont(font)
            heading.setBold(True)
            painter.setFont(heading)
            colour = QColor(palette.color(QPalette.ColorRole.Text))
            colour.setAlpha(190)
            painter.setPen(colour)
            painter.drawText(rect.adjusted(12, 6, 0, 0), Qt.AlignmentFlag.AlignVCenter, str(payload))
            painter.restore()
            return
        if kind == "waiting" or not isinstance(payload, TaskRow):  # its row is on its way: the place is kept, the text comes when the row is in
            painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight) if _is_selected(option) else base)
            painter.restore()
            return
        selected = _is_selected(option)
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight) if selected else base)
        text = palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        model = index.model()
        if isinstance(model, TaskListModel) and payload.item in model.pending:
            text = palette.color(QPalette.ColorRole.PlaceholderText)  # an action is under way: greyed until it ends
        quiet = QColor(text)
        quiet.setAlpha(255 if selected else 170)
        draw_mark(painter, QPoint(rect.left() + 24, rect.center().y()), payload)
        left = rect.left() + 24 + MARK // 2 + 12
        tags = tuple(payload.tags)
        chips = paint_chips(painter, QRect(left, rect.top(), rect.right() - left - 10, rect.height()), tags, self._colours, font) if tags else 0
        width = rect.right() - left - 10 - chips
        metrics = QFontMetrics(font)
        title = QFont(font)
        title.setStrikeOut(payload.done)
        painter.setFont(title)
        painter.setPen(text)
        summary = payload.summary
        mark = model.mark_of(payload.item) if isinstance(model, TaskListModel) else ""
        mark_room = paint_mark(painter, left, rect.top() + 7 + metrics.height() // 2, mark)
        painter.drawText(QRect(left + mark_room, rect.top() + 7, width - mark_room, metrics.height()), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(summary, Qt.TextElideMode.ElideRight, width - mark_room))
        painter.setFont(font)
        due = due_text(payload.due)
        if due:
            painter.setPen(QColor(red()) if payload.overdue and not selected else quiet)
            painter.drawText(QRect(left, rect.top() + 7 + metrics.height() + 2, width, metrics.height()), Qt.AlignmentFlag.AlignVCenter, due + ("  ⟳" if payload.repeats else ""))
        line = QColor(text)
        line.setAlpha(28)
        painter.setPen(line)
        painter.drawLine(rect.left() + 12, rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class TasksPage(PimListPage):
    """Task lists on the left, the tasks in the middle (a quick-add field, tabs, groups by date), and one task on the right."""

    kind = "tasks"
    multiple = True
    new_label = "New task"
    new_icon = "list-add"
    section_name = "Tasks"
    item_word = "task"

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(backend, settings, parent)
        self.colours = TagColourBook()
        self._tab: str = "all"
        self._generation: int = 0
        self._select_when_there: str = ""  # a task just saved: selected when its row has come in
        self._tags_known: list[str] = []
        self.model = TaskListModel()
        self.model.fetch = self._fetch_rows
        self.delegate = TaskDelegate(self.colours)
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)
        tabs = QWidget(pane)
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(8, 6, 8, 4)
        self.tab_group = QButtonGroup(tabs)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key, label in TABS:
            button = QPushButton(label, tabs)
            button.setCheckable(True)
            button.setFlat(True)
            button.setChecked(key == self._tab)
            self.tab_group.addButton(button)
            self.tab_buttons[key] = button
            tabs_layout.addWidget(button)
            button.clicked.connect(lambda checked=False, chosen=key: self._choose_tab(chosen))
        tabs_layout.addStretch(1)
        self.count_label = QLabel("", tabs)
        tabs_layout.addWidget(self.count_label)
        pane_layout.addWidget(tabs)
        self.quick = QLineEdit(pane)
        self.quick.setPlaceholderText("Click to add a new task")
        self.quick.setClearButtonEnabled(True)
        self.quick.setContentsMargins(8, 0, 8, 0)
        pane_layout.addWidget(self.quick)
        self.list = QListView(pane)
        self.list.setModel(self.model)
        self.list.setItemDelegate(self.delegate)
        self.list.setFrameShape(QListView.Shape.NoFrame)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane_layout.addWidget(self.list, 1)
        self.detail = QTextBrowser()
        self.detail.setFrameShape(QTextBrowser.Shape.NoFrame)
        splitter = PaneSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(pane)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([430, 450])
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(splitter)
        self.content = content

        self.search_field = SearchField(hint="Search tasks", focused_hint="Search tasks", scope=False)
        self.search = self.search_field.line_edit
        self.act_complete = QAction("Mark as completed", self.toolbar)
        self.act_complete.setIcon(theme_icon("task-complete", "mail-mark-read"))
        self.act_complete.triggered.connect(self.complete_selected)
        self.act_edit = QAction("Edit", self.toolbar)
        self.act_edit.setIcon(theme_icon("document-edit"))
        self.act_edit.triggered.connect(self.edit_selected)
        self.act_delete = QAction("Delete", self.toolbar)
        self.act_delete.setIcon(theme_icon("edit-delete"))
        self.act_delete.triggered.connect(self.delete_selected)
        self.tag_button = QToolButton()
        self.tag_button.setText("Tag")
        self.tag_button.setIcon(tag_icon(QColor(NEW_TAG)))
        self.tag_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_menu.aboutToShow.connect(self._fill_tag_menu)
        self.tag_button.setMenu(self.tag_menu)
        self.move_button = QToolButton()
        self.move_button.setText("Move to list")
        self.move_button.setIcon(theme_icon("go-parent-folder"))
        self.move_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_button)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_button.setMenu(self.move_menu)
        self.toolbar.add_center(self.act_complete)
        self.toolbar.add_center(self.act_edit)
        self.toolbar.add_center(self.tag_button)
        self.toolbar.add_center(self.move_button)
        self.toolbar.add_center(self.act_delete)
        self.toolbar.set_search(self.search_field)

        self.quick.returnPressed.connect(self._quick_add)
        self.search.textChanged.connect(self._search_changed)
        self.list.selectionModel().selectionChanged.connect(self._selection_changed)
        self.list.doubleClicked.connect(self._double_clicked)
        self.list.customContextMenuRequested.connect(self._list_menu)

    # --- showing ------------------------------------------------------------------------------

    def _choose_tab(self, key: str) -> None:
        self._tab = key
        self.reload_list()

    @override
    def refresh(self) -> None:
        self.panel.reload()

    @override
    def selection_changed(self) -> None:
        """A task list was ticked or unticked: show what is ticked."""
        self.reload_list()

    def _state(self) -> Literal["open", "done", "all"]:
        return "open" if self._tab == "upcoming" else ("done" if self._tab == "completed" else "all")

    @override
    def reload_list(self, keep: list[str] | None = None) -> None:
        self._generation += 1
        generation = self._generation
        visible = self.panel.visible_ids()
        selected = keep if keep is not None else self._selected_items()
        parameters = TaskPlacesParameters(lists=tuple(sorted(visible)), state=self._state(), text=self.search.text().strip())  # only the lists shown: never a cut shared with hidden ones

        def arrived(result: TaskPlaces) -> None:
            if generation != self._generation:
                return
            self.model.load(result.places)  # every task's place; the rows are asked for as they come into view
            self._counted()
            self.status(self.count_label.text())
            positions = [position for position in (self.model.position_of(item) for item in selected) if position >= 0]
            self.list.selectionModel().clear()
            for position in positions:
                self.list.selectionModel().select(self.model.index(position), QItemSelectionModel.SelectionFlag.Select)
            if positions:
                self.list.selectionModel().setCurrentIndex(self.model.index(positions[0]), QItemSelectionModel.SelectionFlag.NoUpdate)
            self._selection_changed()
            self._backend.ask(TAG_LIST, NoParameters(), self._tags_arrived, lambda error: None, owner=self)

        if not visible:
            self.model.load([])
            self.count_label.setText("")
            self._selection_changed()
            return
        self._backend.ask(TASK_PLACES, parameters, arrived, self._failed, owner=self)

    def _fetch_rows(self, items: list[str], request: int) -> None:
        """The rows of a page of the list (or of a task that changed), as they come into view."""
        parameters = TaskListParameters(state="all", items=tuple(items))
        self._backend.ask(TASK_LIST, parameters, lambda result: self._rows_arrived(result, request), self._failed, owner=self)

    def _rows_arrived(self, result: TaskList, request: int) -> None:
        self.model.arrive(result.tasks, request)
        selected = set(self._selected_items())
        if any(row.item in selected for row in result.tasks):
            self.act_complete.setText(self._complete_text())

    def _counted(self) -> None:
        places = self.model.places()
        self.count_label.setText(f"{sum(1 for place in places if not place.done)} active, {sum(1 for place in places if place.done)} completed")

    def _tags_arrived(self, result: tuple[TagInfo, ...]) -> None:
        tags = list(result)
        self.colours.update(tags)
        self._tags_known = [tag.name for tag in tags]
        self.list.viewport().update()

    @override
    def _selected_items(self) -> list[str]:
        """The tasks selected now, without those that have an action under way (they get no second one)."""
        found: list[str] = []
        for index in self.list.selectionModel().selectedIndexes():
            place = self.model.place_at(index.row())
            if place is not None and place.item not in self.model.pending:
                found.append(place.item)
        return found

    @override
    def _items(self) -> PimItemModel:
        return self.model

    @override
    def _view(self) -> QListView:
        return self.list

    @override
    def _marks_delegate(self) -> MarkDelegate:
        return self.delegate

    @override
    def _search_box(self) -> QLineEdit:
        return self.search

    @override
    def _set_tag(self, name: str, on: bool) -> None:
        self._edit_selected(add_tag=(name,)) if on else self._edit_selected(remove_tag=(name,))

    def _selected_rows(self) -> list[TaskPlace]:
        """The places of the selected tasks: they hold what an action needs, also for a task whose row is not in yet."""
        wanted = set(self._selected_items())
        return [place for place in self.model.places() if place.item in wanted]

    def _complete_text(self) -> str:
        places = self._selected_rows()
        return "Mark as open again" if places and all(place.done for place in places) else "Mark as completed"

    def _selection_changed(self, selected: object = None, deselected: object = None) -> None:
        items = self._selected_items()
        if len(items) == 1:
            self._backend.ask(TASK_SHOW, TaskItem(items[0]), lambda result: self._show_if_current(items[0], result), self._failed, owner=self)
        elif not items:
            self.detail.setHtml(f"<p style='margin:30px' align=center><i>{'There are no tasks here' if not self.model.places() else 'Select a task'}</i></p>")
        else:
            self.detail.setHtml(f"<p style='margin:30px' align=center><i>{len(items)} tasks selected</i></p>")
        self.act_complete.setText(self._complete_text())

    def _show_if_current(self, item: str, result: TaskInfo) -> None:
        if self._selected_items() != [item]:
            return
        c = result
        done = c.done
        status = "Completed" if done else {"NEEDS-ACTION": "Not started", "IN-PROCESS": "In progress", "CANCELLED": "Cancelled"}.get(c.status, c.status)
        colour = green() if done else (red() if c.overdue else theme().amber)

        def row(label: str, value: str) -> str:
            return f"<tr><td style='color:gray' align=right>{html.escape(label)}</td><td>{value}</td></tr>" if value else ""

        details = "".join((
            row("Start", html.escape(due_text(c.start))), row("Due", html.escape(due_text(c.due))),
            row("Completed", html.escape(due_text(c.completed))), row("Repeats", html.escape(c.rrule)),
            row("Priority", {1: "High", 5: "Normal", 9: "Low"}.get(c.priority, str(c.priority) if c.priority else "")),
            row("Progress", f"{c.percent} %" if c.percent else ""), row("Location", html.escape(c.location)),
        ))
        note = c.description
        tags = self.colours.html(c.tags)
        self.detail.setHtml(
            f"<h2>{html.escape(c.summary)}</h2><p style='color:{colour}'><b>{status}</b></p>{('<p>' + tags + '</p>') if tags else ''}"
            f"<h4 style='margin-bottom:2px'>Details</h4><table cellspacing=4>{details}</table>"
            + (f"<h4 style='margin-bottom:2px'>Note</h4><p>{html.escape(note).replace(chr(10), '<br>')}</p>" if note else "")
        )

    # --- changing -----------------------------------------------------------------------------

    def _lists(self) -> list[CollectionSummary]:
        return list(self.panel.collections)

    def _current_list(self) -> str:
        visible = self.panel.visible_ids()
        return visible[0] if visible else ((self._lists()[0].collection) if self._lists() else "")

    @override
    def new_item(self) -> None:
        if not self._lists():
            return
        editor = TaskEditor(self._backend, self._lists(), self._tags_known, None, self._current_list(), self.content)
        editor.saved.connect(self._after_save)
        editor.exec()

    def _quick_add(self) -> None:
        text = self.quick.text().strip()
        collection = self._current_list()
        if not text or not collection:
            return
        self.quick.clear()
        self._backend.ask(TASK_ADD, TaskAdd(list=collection, summary=text), lambda result: self._after_save(result.item), self._failed, owner=self)

    @override
    def edit_selected(self) -> None:
        items = self._selected_items()
        if len(items) != 1:
            return

        def opened(result: TaskInfo) -> None:
            editor = TaskEditor(self._backend, self._lists(), self._tags_known, result, result.collection, self.content)
            editor.saved.connect(self._after_save)
            editor.exec()

        self._backend.ask(TASK_SHOW, TaskItem(items[0]), opened, self._failed, owner=self)

    def _after_save(self, item: str) -> None:
        """A task was saved: its row follows the fact; it is selected (now, or when its fact has come)."""
        if not item:
            return
        if self.model.position_of(item) >= 0:
            self._select_task(item)
        else:
            self._select_when_there = item

    def _select_task(self, item: str) -> None:
        position = self.model.position_of(item)
        if position >= 0:
            self.list.setCurrentIndex(self.model.index(position))
            self.list.scrollTo(self.model.index(position))

    def _changed_rows(self) -> None:
        """After one task moved, came or went: the numbers, and the right side when nothing is selected any more."""
        self._counted()
        if not self._selected_items():
            self._selection_changed()  # "There are no tasks here", "Select a task"

    @override
    def apply_item(self, change: Change) -> None:
        """A task was added, changed, deleted or moved: only that task changes (D-146)."""
        kind, collection, item = str(change.kind), change.folder, change.message_id
        visible = self.panel.visible_ids()
        if kind in ("item_removed", "item_moved") and self.model.position_of(item) >= 0:
            self.model.pending.discard(item)
            self.model.drop(item)
            self._changed_rows()
        wanted = change.new_message_id if kind == "item_moved" else item
        target = change.to_folder if kind == "item_moved" else collection
        if kind in ("item_added", "item_changed", "item_moved") and target in visible:
            parameters = TaskPlacesParameters(lists=tuple(sorted(visible)), state=self._state(), text=self.search.text().strip(), item=wanted)

            def arrived(result: TaskPlaces) -> None:
                others = [place for place in self.model.places() if place.item != wanted]
                self.model.forget(wanted)  # its row changed: asked for again
                if result.position is not None and result.places:  # its place in the engine's order, among the others
                    others.insert(min(result.position, len(others)), result.places[0])
                    self.model.follow(others, wanted)
                else:
                    self.model.drop(wanted)  # it no longer matches the tab or the search
                if self._select_when_there == wanted:
                    self._select_when_there = ""
                    self._select_task(wanted)
                self._changed_rows()
                if self.model.position_of(wanted) >= 0:
                    self.model.want(self.model.position_of(wanted))
                if self._selected_items() == [wanted]:
                    self._selection_changed()  # the task on the right changed: shown again

            self._backend.ask(TASK_PLACES, parameters, arrived, self._failed, owner=self)

    def complete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        reopen = all(row.done for row in rows)
        for row in rows:  # each task's fact changes its row (D-146)
            if reopen:
                self._backend.ask(TASK_REOPEN, TaskItem(row.item), lambda result: None, self._failed, owner=self)
            else:
                self._backend.ask(TASK_COMPLETE, TaskItem(row.item), lambda result: None, self._failed, owner=self)

    def _fill_tag_menu(self) -> None:
        self.tag_menu.clear()
        rows = self._selected_rows()
        if not rows:
            self.tag_menu.addAction("(select tasks first)").setEnabled(False)
            return
        have: dict[str, int] = {}
        for row in rows:
            for tag in row.tags:
                have[tag.casefold()] = have.get(tag.casefold(), 0) + 1
        for name in self._tags_known:
            action = self.tag_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(have.get(name.casefold(), 0) == len(rows))
            action.triggered.connect(lambda checked=False, tag=name, on=not action.isChecked(): self._edit_selected(add_tag=(tag,)) if on else self._edit_selected(remove_tag=(tag,)))
        self.tag_menu.addSeparator()
        self.tag_menu.addAction("New tag…", self._new_tag)
        self.tag_menu.addAction("Remove all tags", lambda: self._edit_selected(clear=("tag",)))

    def _edit_selected(self, add_tag: tuple[str, ...] = (), remove_tag: tuple[str, ...] = (), clear: tuple[str, ...] = ()) -> None:
        for item in self._selected_items():  # each task's fact changes its row (D-146)
            self._backend.ask(TASK_EDIT, TaskEdit(item=item, add_tag=add_tag, remove_tag=remove_tag, clear=clear), lambda result: None, self._failed, owner=self)

    @override
    def _delete_step(self, item: str) -> AnyStep[object]:
        return Step(TASK_DELETE, TaskItem(item))

    @override
    def _move_step(self, item: str, target: str) -> AnyStep[object]:
        return Step(TASK_MOVE, TaskMove(item, target))

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        rows = self._selected_rows()
        if not rows:
            self.move_menu.addAction("(select tasks first)").setEnabled(False)
            return
        first = rows[0]
        targets = [info for info in self._lists() if info.account == first.account and info.collection != first.collection]
        if not targets:
            self.move_menu.addAction("(no other task list)").setEnabled(False)
            return
        for info in targets:
            target = info.collection
            self.move_menu.addAction(info.name, lambda checked=False, chosen=target: self._move_selected(chosen))

    def _list_menu(self, position: QPoint) -> None:
        menu = QMenu(self.content)
        menu.addAction("New task", self.new_item)
        if self._selected_items():
            menu.addSeparator()
            menu.addAction(self.act_complete.text(), self.complete_selected)
            if len(self._selected_items()) == 1:
                menu.addAction("Edit", self.edit_selected)
            self._fill_tag_menu()
            menu.addMenu(self.tag_menu).setText("Tag")
            self._fill_move_menu()
            menu.addMenu(self.move_menu).setText("Move to list")
            menu.addAction("Delete", self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(position))

