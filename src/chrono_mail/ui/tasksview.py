import html
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import green, red
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.tagchips import TagColourBook, paint_chips
from chrono_mail.ui.taskeditor import TaskEditor

# --- Version ------------------------------------------------------------
__version__ = "tasksview.py 0.0.63"

# --- Version history ----------------------------------------------------
# v0.0.63: Initial tasks section: lists, quick add, groups by date, status marks, detail pane, editor, tags, move, delete

ROW_ROLE: int = Qt.ItemDataRole.UserRole + 1
HEADER_HEIGHT: int = 30
ROW_HEIGHT: int = 52
MARK: int = 22
LIST_LIMIT: int = 5000
AMBER: str = "#e0a000"
GREY: str = "#8a8a8a"
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


def group_of(row: object, today: date) -> tuple[int, str]:
    """The heading a task goes under, with its place in the order of the headings."""
    if bool(getattr(row, "done")):
        return 90, "Completed"
    moment = parse_when(str(getattr(row, "due")))
    if moment is None:
        return 80, "No due date"
    day = (moment.astimezone().date() if moment.tzinfo else moment.date()) if isinstance(moment, datetime) else moment
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


class TaskListModel(QAbstractListModel):
    """The tasks in groups: a heading row for each group, then its tasks (headings are rows that cannot be selected)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entries: list[tuple[str, object]] = []  # ("header", text) or ("task", row)
        self.total: int = 0

    def load(self, rows: list[object], total: int, today: date | None = None) -> None:
        day = today or date.today()
        ordered = sorted(rows, key=lambda row: (group_of(row, day)[0], str(getattr(row, "due")) or "9999", int(getattr(row, "priority")) or 10, str(getattr(row, "summary")).casefold()))
        entries: list[tuple[str, object]] = []
        last = ""
        for row in ordered:
            heading = group_of(row, day)[1]
            if heading != last:
                entries.append(("header", heading))
                last = heading
            entries.append(("task", row))
        self.beginResetModel()
        self.entries = entries
        self.total = total
        self.endResetModel()

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
        if role == Qt.ItemDataRole.DisplayRole:
            return payload if kind == "header" else str(getattr(payload, "summary"))
        if role == ROW_ROLE:
            return self.entries[index.row()]
        return None

    def rows(self) -> list[object]:
        return [payload for kind, payload in self.entries if kind == "task"]

    def position_of(self, item: str) -> int:
        return next((position for position, (kind, payload) in enumerate(self.entries) if kind == "task" and str(getattr(payload, "item")) == item), -1)


def draw_mark(painter: QPainter, centre: QPoint, row: object, size: int = MARK) -> None:
    """The round mark of a task: a ring (amber: to do, red with a bar: overdue), a green disc with a tick when done, a grey ring when cancelled."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRect(centre.x() - size // 2, centre.y() - size // 2, size, size)
    status = str(getattr(row, "status"))
    if bool(getattr(row, "done")):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(green()))
        painter.drawEllipse(box)
        painter.setPen(QPen(QColor("white"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(box.left() + size * 28 // 100, box.top() + size * 54 // 100, box.left() + size * 44 // 100, box.top() + size * 70 // 100)
        painter.drawLine(box.left() + size * 44 // 100, box.top() + size * 70 // 100, box.left() + size * 74 // 100, box.top() + size * 32 // 100)
    else:
        colour = QColor(red()) if bool(getattr(row, "overdue")) else QColor(GREY if status == "CANCELLED" else AMBER)
        painter.setPen(QPen(colour, 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box.adjusted(1, 1, -1, -1))
        if status == "IN-PROCESS":
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(box.adjusted(5, 5, -5, -5), 90 * 16, -max(30, int(getattr(row, "percent")) * 360 // 100 or 180) * 16)
        if bool(getattr(row, "overdue")):
            painter.setPen(QPen(colour, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(centre.x(), centre.y() - size // 5, centre.x(), centre.y() + size // 12)
            painter.drawPoint(centre.x(), centre.y() + size // 4)
    painter.restore()


class TaskDelegate(QStyledItemDelegate):
    def __init__(self, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colours: TagColourBook = colours

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        entry = index.data(ROW_ROLE)
        return QSize(0, HEADER_HEIGHT if entry is not None and entry[0] == "header" else ROW_HEIGHT)

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
        selected = _is_selected(option)
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Highlight) if selected else base)
        text = palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        quiet = QColor(text)
        quiet.setAlpha(255 if selected else 170)
        draw_mark(painter, QPoint(rect.left() + 24, rect.center().y()), payload)
        left = rect.left() + 24 + MARK // 2 + 12
        tags = tuple(getattr(payload, "tags"))
        chips = paint_chips(painter, QRect(left, rect.top(), rect.right() - left - 10, rect.height()), tags, self._colours, font) if tags else 0
        width = rect.right() - left - 10 - chips
        metrics = QFontMetrics(font)
        title = QFont(font)
        title.setStrikeOut(bool(getattr(payload, "done")))
        painter.setFont(title)
        painter.setPen(text)
        summary = str(getattr(payload, "summary"))
        painter.drawText(QRect(left, rect.top() + 7, width, metrics.height()), Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(summary, Qt.TextElideMode.ElideRight, width))
        painter.setFont(font)
        due = due_text(str(getattr(payload, "due")))
        if due:
            painter.setPen(QColor(red()) if bool(getattr(payload, "overdue")) and not selected else quiet)
            painter.drawText(QRect(left, rect.top() + 7 + metrics.height() + 2, width, metrics.height()), Qt.AlignmentFlag.AlignVCenter, due + ("  ⟳" if bool(getattr(payload, "repeats")) else ""))
        line = QColor(text)
        line.setAlpha(28)
        painter.setPen(line)
        painter.drawLine(rect.left() + 12, rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class TasksPage(PimPage):
    """Task lists on the left, the tasks in the middle (a quick-add field, tabs, groups by date), and one task on the right."""

    kind = "tasks"
    multiple = True
    new_label = "New task"
    new_icon = "list-add"

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(backend, settings, parent)
        self.colours = TagColourBook()
        self._tab: str = "all"
        self._generation: int = 0
        self._tags_known: list[str] = []
        self.model = TaskListModel()
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
        splitter = QSplitter(Qt.Orientation.Horizontal)
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

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.reload_list)
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
        self.tag_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tag_menu = QMenu(self.tag_button)
        self.tag_menu.aboutToShow.connect(self._fill_tag_menu)
        self.tag_button.setMenu(self.tag_menu)
        self.move_button = QToolButton()
        self.move_button.setText("Move to list")
        self.move_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.move_menu = QMenu(self.move_button)
        self.move_menu.aboutToShow.connect(self._fill_move_menu)
        self.move_button.setMenu(self.move_menu)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.act_complete)
        self.toolbar.addAction(self.act_edit)
        self.toolbar.addWidget(self.tag_button)
        self.toolbar.addWidget(self.move_button)
        self.toolbar.addAction(self.act_delete)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy().Preferred)
        self.toolbar.addWidget(spacer)
        self.toolbar.addWidget(self.search)

        self.quick.returnPressed.connect(self._quick_add)
        self.search.textChanged.connect(self._search_changed)
        self.list.selectionModel().selectionChanged.connect(self._selection_changed)
        self.list.doubleClicked.connect(self._double_clicked)
        self.list.customContextMenuRequested.connect(self._list_menu)

    # --- showing ------------------------------------------------------------------------------

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()

    def _double_clicked(self, index: QModelIndex) -> None:
        self.edit_selected()

    def _choose_tab(self, key: str) -> None:
        self._tab = key
        self.reload_list()

    @override
    def refresh(self) -> None:
        self.panel.reload()

    @override
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    @override
    def selection_changed(self) -> None:
        """A task list was ticked or unticked: show what is ticked."""
        self.reload_list()

    def reload_list(self, keep: list[str] | None = None) -> None:
        self._generation += 1
        generation = self._generation
        visible = set(self.panel.visible_ids())
        selected = keep if keep is not None else self._selected_items()
        values: dict[str, object] = {"state": {"all": "all", "upcoming": "open", "completed": "done"}[self._tab], "text": self.search.text().strip(), "limit": LIST_LIMIT}

        def arrived(result: object) -> None:
            if generation != self._generation:
                return
            rows = [row for row in getattr(result, "tasks") if str(getattr(row, "collection")) in visible]
            self.model.load(rows, len(rows))
            self.count_label.setText(f"{sum(1 for r in rows if not getattr(r, 'done'))} active, {sum(1 for r in rows if getattr(r, 'done'))} completed")
            self.status(self.count_label.text())
            positions = [position for position in (self.model.position_of(item) for item in selected) if position >= 0]
            self.list.selectionModel().clear()
            for position in positions:
                self.list.selectionModel().select(self.model.index(position), QItemSelectionModel.SelectionFlag.Select)
            if positions:
                self.list.selectionModel().setCurrentIndex(self.model.index(positions[0]), QItemSelectionModel.SelectionFlag.NoUpdate)
            self._selection_changed()
            self._backend.call("tag.list", {}, self._tags_arrived, lambda error: None, owner=self)

        if not visible:
            self.model.load([], 0)
            self.count_label.setText("")
            self._selection_changed()
            return
        self._backend.call("task.list", values, arrived, self._failed, owner=self)

    def _tags_arrived(self, result: object) -> None:
        tags = list(result) if isinstance(result, (tuple, list)) else []
        self.colours.update(tags)
        self._tags_known = [str(getattr(tag, "name")) for tag in tags]
        self.list.viewport().update()

    def _failed(self, error: ChronoMailError) -> None:
        self.status(f"Tasks: {error}")

    def _selected_items(self) -> list[str]:
        found: list[str] = []
        for index in self.list.selectionModel().selectedIndexes():
            entry = index.data(ROW_ROLE)
            if entry is not None and entry[0] == "task":
                found.append(str(getattr(entry[1], "item")))
        return found

    def _selected_rows(self) -> list[object]:
        wanted = set(self._selected_items())
        return [row for row in self.model.rows() if str(getattr(row, "item")) in wanted]

    def _selection_changed(self, selected: object = None, deselected: object = None) -> None:
        items = self._selected_items()
        if len(items) == 1:
            self._backend.call("task.show", {"item": items[0]}, lambda result: self._show_if_current(items[0], result), self._failed, owner=self)
        elif not items:
            self.detail.setHtml(f"<p style='margin:30px' align=center><i>{'There are no tasks here' if not self.model.rows() else 'Select a task'}</i></p>")
        else:
            self.detail.setHtml(f"<p style='margin:30px' align=center><i>{len(items)} tasks selected</i></p>")
        self.act_complete.setText("Mark as open again" if items and all(bool(getattr(row, "done")) for row in self._selected_rows()) else "Mark as completed")

    def _show_if_current(self, item: str, result: object) -> None:
        if self._selected_items() != [item]:
            return
        c = result
        done = bool(getattr(c, "done"))
        status = "Completed" if done else {"NEEDS-ACTION": "Not started", "IN-PROCESS": "In progress", "CANCELLED": "Cancelled"}.get(str(getattr(c, "status")), str(getattr(c, "status")))
        colour = green() if done else (red() if bool(getattr(c, "overdue")) else AMBER)

        def row(label: str, value: str) -> str:
            return f"<tr><td style='color:gray' align=right>{html.escape(label)}</td><td>{value}</td></tr>" if value else ""

        details = "".join((
            row("Start", html.escape(due_text(str(getattr(c, "start"))))), row("Due", html.escape(due_text(str(getattr(c, "due"))))),
            row("Completed", html.escape(due_text(str(getattr(c, "completed"))))), row("Repeats", html.escape(str(getattr(c, "rrule")))),
            row("Priority", {1: "High", 5: "Normal", 9: "Low"}.get(int(getattr(c, "priority")), str(getattr(c, "priority")) if int(getattr(c, "priority")) else "")),
            row("Progress", f"{getattr(c, 'percent')} %" if int(getattr(c, "percent")) else ""), row("Location", html.escape(str(getattr(c, "location")))),
        ))
        note = str(getattr(c, "description"))
        tags = self.colours.html(getattr(c, "tags"))
        self.detail.setHtml(
            f"<h2>{html.escape(str(getattr(c, 'summary')))}</h2><p style='color:{colour}'><b>{status}</b></p>{('<p>' + tags + '</p>') if tags else ''}"
            f"<h4 style='margin-bottom:2px'>Details</h4><table cellspacing=4>{details}</table>"
            + (f"<h4 style='margin-bottom:2px'>Note</h4><p>{html.escape(note).replace(chr(10), '<br>')}</p>" if note else "")
        )

    # --- changing -----------------------------------------------------------------------------

    def _lists(self) -> list[object]:
        return list(self.panel.collections)

    def _current_list(self) -> str:
        visible = self.panel.visible_ids()
        return visible[0] if visible else (str(getattr(self._lists()[0], "collection")) if self._lists() else "")

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
        self._backend.call("task.add", {"list": collection, "summary": text}, lambda result: self._after_save(str(getattr(result, "item"))), self._failed, owner=self)

    def edit_selected(self) -> None:
        items = self._selected_items()
        if len(items) != 1:
            return

        def opened(result: object) -> None:
            editor = TaskEditor(self._backend, self._lists(), self._tags_known, result, str(getattr(result, "collection")), self.content)
            editor.saved.connect(self._after_save)
            editor.exec()

        self._backend.call("task.show", {"item": items[0]}, opened, self._failed, owner=self)

    def _after_save(self, item: str) -> None:
        self.reload_list([item] if item else None)
        self.panel.reload()

    def complete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        reopen = all(bool(getattr(row, "done")) for row in rows)
        remaining = {"count": len(rows)}

        def done(result: object) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self.reload_list([str(getattr(row, "item")) for row in rows])

        for row in rows:
            self._backend.call("task.reopen" if reopen else "task.complete", {"item": str(getattr(row, "item"))}, done, self._failed, owner=self)

    @override
    def delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        word = "this task" if len(items) == 1 else f"these {len(items)} tasks"
        if QMessageBox.question(self.content, "Delete", f"Delete {word}?\n\nThe files are kept in the store's .deleted directory for 30 days.") != QMessageBox.StandardButton.Yes:
            return
        remaining = {"count": len(items)}

        def done(result: object) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self._after_save("")

        for item in items:
            self._backend.call("task.delete", {"item": item}, done, self._failed, owner=self)

    def _fill_tag_menu(self) -> None:
        self.tag_menu.clear()
        rows = self._selected_rows()
        if not rows:
            self.tag_menu.addAction("(select tasks first)").setEnabled(False)
            return
        have: dict[str, int] = {}
        for row in rows:
            for tag in getattr(row, "tags"):
                have[tag.casefold()] = have.get(tag.casefold(), 0) + 1
        for name in self._tags_known:
            action = self.tag_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(have.get(name.casefold(), 0) == len(rows))
            action.triggered.connect(lambda checked=False, tag=name, on=not action.isChecked(): self._edit_selected({"add_tag": [tag]} if on else {"remove_tag": [tag]}))
        self.tag_menu.addSeparator()
        self.tag_menu.addAction("New tag…", self._new_tag)
        self.tag_menu.addAction("Remove all tags", lambda: self._edit_selected({"clear": ["tag"]}))

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self.content, "New tag", "Name of the tag:")
        if ok and name.strip():
            self._edit_selected({"add_tag": [name.strip()]})

    def _edit_selected(self, values: dict[str, object]) -> None:
        items = self._selected_items()
        remaining = {"count": len(items)}

        def done(result: object) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self.reload_list(items)

        for item in items:
            self._backend.call("task.edit", {"item": item, **values}, done, self._failed, owner=self)

    def _fill_move_menu(self) -> None:
        self.move_menu.clear()
        rows = self._selected_rows()
        if not rows:
            self.move_menu.addAction("(select tasks first)").setEnabled(False)
            return
        first = rows[0]
        account = str(getattr(first, "account"))
        targets = [info for info in self._lists() if str(getattr(info, "account")) == account and str(getattr(info, "collection")) != str(getattr(first, "collection"))]
        if not targets:
            self.move_menu.addAction("(no other task list)").setEnabled(False)
            return
        for info in targets:
            target = str(getattr(info, "collection"))
            self.move_menu.addAction(str(getattr(info, "name")), lambda checked=False, chosen=target: self._move_selected(chosen))

    def _move_selected(self, target: str) -> None:
        items = self._selected_items()
        remaining = {"count": len(items)}

        def done(result: object) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                self._after_save("")

        for item in items:
            self._backend.call("task.move", {"item": item, "list": target}, done, self._failed, owner=self)

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
