"""Settings > Sync conflicts (D-194): one row per item that differs between Chrono Mail and its server, ticked and settled with Use Chrono Mail or
Use Server data (every clashing field of each ticked item from that side), or per field in the popup a double click opens. Nothing here asks
questions during the work: a conflict waits on this page until the person settles it, or leaves it."""

import html
import time
from typing import Literal, override

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_conflicts import CONFLICT_SETTLE, ConflictSummary, SettleParameters, SettleResult
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.syncmarks import BOTH, GONE_HERE, GONE_THERE, REFUSED, WHY_WORDS, SyncMarks, paint_why, why_kind, why_pixmap
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "syncconflicts.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: the problems of Settle are escaped: they hold server replies (RV-167)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.237: A mail folder deleted on the server can be the conflict (What: Folder, R-30)
# v1.1.234: A whole calendar, task list or address book can be the conflict (What: Calendar, Task list, Address book; R-25)
# v1.1.233: The table always fills the width: the tick box, Fields, What and Why fixed, Name and Where sharing the rest (60/40 at first, the line
#           between them dragged and remembered); no triangle in the rows; the kinds of conflict in lines with a solid badge (the owner)
# v1.1.232: The legend one under the other; the window's margin all round, so Close lines up with the rest (the owner)
# v1.1.231: "Why" and its icons centred in their column (CentredIconDelegate, the owner)
# v1.1.229: A window of its own (SyncConflictsWindow), out of Settings; Fields first; Why as one of four icons with a legend; columns dragged and
#           remembered, "…" and hover texts; open_conflict for a click on a triangle (the owner)
# v1.1.228: Retry sync below the list (for the ticked refusals; greyed for a clash) and in the popup; the accounts are synchronised at once (the owner)
# v1.1.227: Mail rows (Message); Keep here only for mail made here that the server will not take; Use Server data greyed where it would delete mail (D-195)
# v1.1.226: Initial: Settings > Sync conflicts with its table, Use Chrono Mail, Use Server data, Invert selection, and the popup per field (D-194)

KIND_WORDS: dict[str, str] = {
    "events": "Event", "tasks": "Task", "contacts": "Contact", "mail": "Message", "folder": "Folder", "calendar": "Calendar", "tasklist": "Task list", "addressbook": "Address book",
}
ROLE_ID: int = Qt.ItemDataRole.UserRole + 1
COLUMNS: tuple[str, ...] = ("", "Fields", "What", "Name", "Where", "Why")  # Fields first, Why as an icon (the owner)
WHY_COLUMN: int = 5
SHARE_KEY: str = "sync_conflicts_name_share"  # how much of the free width Name has (Where the rest), dragged and remembered
FIRST_SHARE: float = 0.6  # names run longer than places (the owner agreed 60/40)
FIXED_COLUMNS: tuple[int, ...] = (0, 1, 2, 5)  # the tick box, Fields, What and Why keep their width; Name and Where share the rest
NAME_COLUMN: int = 3
WHERE_COLUMN: int = 4
ROLE_KIND: int = Qt.ItemDataRole.UserRole + 2  # the Why cell: the kind of conflict it draws
LOCAL: str = "local"
SERVER: str = "server"
KEEP: str = "keep"
RETRY: str = "retry"


class WhyDelegate(QStyledItemDelegate):
    """The Why cell: the icon of its kind of conflict drawn in the middle (Qt puts a cell's icon at its left edge), in lines of the text colour on
    the row's background, the selection's colours on a selected row (the owner)."""

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        widget = getattr(cell, "widget")  # getattr: PySide's stubs give these fields no type: read through getattr
        style = widget.style() if isinstance(widget, QWidget) else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, cell, painter, widget if isinstance(widget, QWidget) else None)  # background, selection
        rect, state, palette = getattr(option, "rect"), getattr(option, "state"), getattr(option, "palette")  # getattr: PySide's stubs give the fields of a style option no type
        kind = index.data(ROLE_KIND)
        if not isinstance(rect, QRect) or not isinstance(palette, QPalette) or not isinstance(kind, str) or not kind:
            return
        selected = isinstance(state, QStyle.StateFlag) and bool(state & QStyle.StateFlag.State_Selected)
        size = min(16, rect.height() - 2)
        area = QRectF(rect.center().x() - size / 2 + 1, rect.center().y() - size / 2 + 1, size, size)
        paint_why(painter, area, kind, palette.color(QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base), selected)


def _where(conflict: ConflictSummary) -> str:
    return f"{conflict.account_name} — {conflict.collection_name}" if conflict.collection_name else conflict.account_name


def _fields(conflict: ConflictSummary) -> str:
    return ", ".join(dict.fromkeys(field.label for field in conflict.fields)) or "—"


class ConflictDialog(QDialog):
    """Every detail of one conflict, and a choice per clashing field (or of a whole side: an item changed before the agreed version was kept, or
    one deleted on a side). After Apply: `choices` (field key -> local or server), or `side` for a whole side."""

    def __init__(self, conflict: ConflictSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conflict: ConflictSummary = conflict
        self.choices: dict[str, str] = {}
        self.side: str = ""
        self.setWindowTitle(f"Sync conflict: {conflict.name}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        details = QFormLayout()
        details.addRow("What", QLabel(KIND_WORDS.get(conflict.kind, conflict.kind), self))
        details.addRow("Where", QLabel(_where(conflict), self))
        details.addRow("Why", QLabel(conflict.reason_text, self))
        details.addRow("Found", QLabel(time.strftime("%Y-%m-%d %H:%M", time.localtime(conflict.when)), self))
        if conflict.detail:
            reason = QLabel(conflict.detail, self)
            reason.setWordWrap(True)
            details.addRow("The server said", reason)
        layout.addLayout(details)
        grid = QGridLayout()
        for column, title in enumerate(("Field", "Chrono Mail", "Server")):
            heading = QLabel(f"<b>{title}</b>", self)
            grid.addWidget(heading, 0, column)
        self.groups: dict[str, tuple[QRadioButton, QRadioButton]] = {}
        per_field = not conflict.whole and bool(conflict.fields) and conflict.local_allowed and conflict.server_allowed  # else a whole side
        for row, field in enumerate(conflict.fields, start=1):
            grid.addWidget(QLabel(field.label, self), row, 0)
            ours = QRadioButton(field.local or "(empty)", self)
            theirs = QRadioButton(field.server or "(empty)", self)
            group = QButtonGroup(self)
            group.addButton(ours)
            group.addButton(theirs)
            group.buttonToggled.connect(self._toggled)
            ours.setEnabled(per_field)
            theirs.setEnabled(per_field)
            grid.addWidget(ours, row, 1)
            grid.addWidget(theirs, row, 2)
            self.groups[field.key] = (ours, theirs)
        if not conflict.fields:
            grid.addWidget(QLabel(conflict.reason_text, self), 1, 0, 1, 3)
        layout.addLayout(grid)
        sides = QHBoxLayout()
        self.all_local = QPushButton("All from Chrono Mail", self)
        self.all_server = QPushButton("All from Server", self)
        self.all_local.setEnabled(conflict.local_allowed)  # greyed, never hidden: the server does not take Chrono Mail's version
        self.all_server.setEnabled(conflict.server_allowed)  # greyed: the server's version would delete mail that exists only here
        self.retry = QPushButton("Retry sync", self)  # a refusal: ask the server once more (the owner)
        self.retry.setEnabled(conflict.retry_allowed)
        self.keep = QPushButton("Keep here only", self)
        self.keep.setEnabled(conflict.keep_allowed)
        self.all_local.clicked.connect(lambda: self._all(LOCAL))
        self.all_server.clicked.connect(lambda: self._all(SERVER))
        self.keep.clicked.connect(lambda: self._all(KEEP))
        self.retry.clicked.connect(lambda: self._all(RETRY))
        sides.addWidget(self.all_local)
        sides.addWidget(self.all_server)
        sides.addWidget(self.retry)
        sides.addWidget(self.keep)
        sides.addStretch(1)
        layout.addLayout(sides)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel, self)
        self.apply_button = box.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.clicked.connect(self._apply)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._ready()

    def _toggled(self, button: QAbstractButton, checked: bool) -> None:
        self._ready()

    def _all(self, side: str) -> None:
        self.side = side
        if side not in (KEEP, RETRY):
            for ours, theirs in self.groups.values():
                (ours if side == LOCAL else theirs).setChecked(True)
        self._ready()

    def _ready(self) -> None:
        chosen = self.side in (KEEP, RETRY) or (all(ours.isChecked() or theirs.isChecked() for ours, theirs in self.groups.values()) if self.groups else bool(self.side))
        mixed = any(ours.isChecked() for ours, _ in self.groups.values()) and any(theirs.isChecked() for _, theirs in self.groups.values())
        if mixed:
            self.side = ""
        self.apply_button.setEnabled(chosen and (bool(self.side) or mixed))

    def _apply(self) -> None:
        if not self.side:
            self.choices = {key: (LOCAL if ours.isChecked() else SERVER) for key, (ours, _) in self.groups.items()}
        self.accept()


class SyncConflictsPanel(QWidget):
    """The Sync conflicts page: the table follows what the engine keeps (rows added, changed and taken away in place, D-146)."""

    sync_requested = Signal(list)  # Retry sync released refusals: these accounts are synchronised at once (their ids)

    def __init__(self, backend: Backend, marks: SyncMarks, parent: QWidget | None = None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._marks: SyncMarks = marks
        self._settings: QSettings | None = settings
        self._share: float = self._saved_share()
        self._fitting: bool = False
        layout = QVBoxLayout(self)
        self.check_all = QCheckBox("Check all", self)
        self.check_all.clicked.connect(self._check_all)
        layout.addWidget(self.check_all)
        self.table = QTreeWidget(self)
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels(list(COLUMNS))
        self.table.setUniformRowHeights(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)  # what does not fit ends in "…"; hovering shows it whole
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the table always fills the width exactly (the owner)
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive if column == NAME_COLUMN else QHeaderView.ResizeMode.Fixed)
        metrics = self.table.fontMetrics()
        widths = {0: 28, 1: metrics.horizontalAdvance("Location, Starts") + 16, 2: metrics.horizontalAdvance("Message") + 16, 5: metrics.horizontalAdvance("Why") + 28}
        for column, width in widths.items():
            header.resizeSection(column, width)
        header.sectionResized.connect(self._name_dragged)
        self.table.viewport().installEventFilter(self)  # a new width of the window: Name and Where take their shares again
        self.table.headerItem().setTextAlignment(WHY_COLUMN, Qt.AlignmentFlag.AlignCenter)  # "Why" and its icons in the middle (the owner)
        self.table.setItemDelegateForColumn(WHY_COLUMN, WhyDelegate(self.table))
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.use_local = QPushButton("Use Chrono Mail", self)
        self.use_server = QPushButton("Use Server data", self)
        self.retry = QPushButton("Retry sync", self)  # a refusal: ask the server once more; refused again, the same row comes back (the owner)
        self.keep_here = QPushButton("Keep here only", self)  # mail made here that the server will not take: it stays here, never sent (D-195)
        self.invert = QPushButton("Invert selection", self)
        self.use_local.clicked.connect(lambda: self._settle(self.ticked(), LOCAL))
        self.use_server.clicked.connect(lambda: self._settle(self.ticked(), SERVER))
        self.keep_here.clicked.connect(lambda: self._settle(self.ticked(), KEEP))
        self.retry.clicked.connect(lambda: self._settle(self.ticked(), RETRY))
        self.invert.clicked.connect(self._invert)
        buttons.addWidget(self.use_local)
        buttons.addWidget(self.use_server)
        buttons.addWidget(self.retry)
        buttons.addWidget(self.keep_here)
        buttons.addWidget(self.invert)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.problems = QLabel("", self)
        self.problems.setWordWrap(True)
        layout.addWidget(self.problems)
        legend = QGridLayout()  # what the icons of the Why column mean, at the bottom left, one under the other (the owner)
        for number, kind in enumerate((BOTH, REFUSED, GONE_THERE, GONE_HERE)):
            icon = QLabel(self)
            icon.setPixmap(why_pixmap(kind, 16, self.palette().color(QPalette.ColorRole.Window)))
            legend.addWidget(icon, number, 0)
            legend.addWidget(QLabel(WHY_WORDS[kind], self), number, 1)
        legend.setColumnStretch(2, 1)
        layout.addLayout(legend)
        marks.changed.connect(self._take)
        self._take()

    # --- the rows -------------------------------------------------------------------------------

    def _row(self, conflict_id: str) -> QTreeWidgetItem | None:
        for number in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(number)
            if item is not None and item.data(0, ROLE_ID) == conflict_id:
                return item
        return None

    def _conflict(self, conflict_id: str) -> ConflictSummary | None:
        return next((c for c in self._marks.conflicts if c.conflict == conflict_id), None)

    def _take(self) -> None:
        """What the engine keeps now: new conflicts get a row, changed ones their new text, settled ones leave; ticks and the order stay."""
        wanted = {c.conflict: c for c in self._marks.conflicts}
        self.table.blockSignals(True)  # the rows' own updates are no ticks of the person
        for number in reversed(range(self.table.topLevelItemCount())):
            item = self.table.topLevelItem(number)
            if item is not None and item.data(0, ROLE_ID) not in wanted:
                self.table.takeTopLevelItem(number)
        for conflict in self._marks.conflicts:
            item = self._row(conflict.conflict)
            if item is None:
                item = QTreeWidgetItem()
                item.setData(0, ROLE_ID, conflict.conflict)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)  # no triangle: every row is a conflict (the owner)
                self.table.addTopLevelItem(item)
            kind = why_kind(conflict.reason)
            for column, text in enumerate(("", _fields(conflict), KIND_WORDS.get(conflict.kind, conflict.kind), conflict.name, _where(conflict), "")):
                if column:
                    item.setText(column, text)
                    item.setToolTip(column, text)
            item.setData(WHY_COLUMN, ROLE_KIND, kind)
            item.setToolTip(WHY_COLUMN, f"{WHY_WORDS[kind]}: {conflict.reason_text}")
        self.table.blockSignals(False)
        self._buttons()

    def _saved_share(self) -> float:
        value = self._settings.value(SHARE_KEY, FIRST_SHARE) if self._settings is not None else FIRST_SHARE
        try:
            share = float(str(value))
        except ValueError:
            return FIRST_SHARE
        return share if 0.1 <= share <= 0.9 else FIRST_SHARE

    def _free_width(self) -> int:
        """The width Name and Where share: the table's less the fixed columns."""
        header = self.table.header()
        return max(0, self.table.viewport().width() - sum(header.sectionSize(column) for column in FIXED_COLUMNS))

    def fit_columns(self) -> None:
        """Name and Where take the free width in their shares, so the table always fills it exactly (no scrollbar, the owner)."""
        free = self._free_width()
        name = max(40, round(free * self._share))
        header = self.table.header()
        self._fitting = True
        header.resizeSection(NAME_COLUMN, min(name, max(40, free - 40)))
        header.resizeSection(WHERE_COLUMN, max(40, free - header.sectionSize(NAME_COLUMN)))
        self._fitting = False

    def _name_dragged(self, column: int, old: int, new: int) -> None:
        """The line between Name and Where was dragged: Where gives or takes the difference, and the new share is remembered."""
        if self._fitting:
            return
        if column != NAME_COLUMN:  # a fixed column got another width (a larger font): the shared ones take what is left
            self.fit_columns()
            return
        free = self._free_width()
        if free <= 80:
            return
        self._share = min(0.9, max(0.1, new / free))
        if self._settings is not None:
            self._settings.setValue(SHARE_KEY, self._share)
        self.fit_columns()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self.fit_columns()
        return super().eventFilter(watched, event)

    def ticked(self) -> list[str]:
        found: list[str] = []
        for number in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(number)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked:
                found.append(str(item.data(0, ROLE_ID)))
        return found

    def _set_all(self, state: bool | None) -> None:
        """Tick every row (True), none (False), or flip each (None)."""
        self.table.blockSignals(True)
        for number in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(number)
            if item is None:
                continue
            on = (item.checkState(0) != Qt.CheckState.Checked) if state is None else state
            item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._buttons()

    def _check_all(self) -> None:
        self._set_all(self.check_all.isChecked())

    def _invert(self) -> None:
        self._set_all(None)

    def _buttons(self) -> None:
        """Greyed, never hidden: nothing ticked; Use Chrono Mail also while a ticked conflict is one the server will not take."""
        ticked = self.ticked()
        chosen = [c for conflict in ticked if (c := self._conflict(conflict)) is not None]
        self.use_server.setEnabled(bool(chosen) and all(c.server_allowed for c in chosen))
        self.use_local.setEnabled(bool(chosen) and all(c.local_allowed for c in chosen))
        self.keep_here.setEnabled(bool(chosen) and all(c.keep_allowed for c in chosen))
        self.retry.setEnabled(bool(chosen) and all(c.retry_allowed for c in chosen))
        self.invert.setEnabled(self.table.topLevelItemCount() > 0)
        self.check_all.setEnabled(self.table.topLevelItemCount() > 0)
        count = self.table.topLevelItemCount()
        self.check_all.setChecked(count > 0 and len(ticked) == count)

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        self._buttons()

    # --- settling -------------------------------------------------------------------------------

    def _open(self, item: QTreeWidgetItem, column: int) -> None:
        conflict = self._conflict(str(item.data(0, ROLE_ID)))
        if conflict is not None:
            self._open_conflict(conflict)

    def open_item(self, item: str) -> bool:
        """The popup of the conflict of this item (a click on its triangle elsewhere in the program); False when it has none any more."""
        conflict = next((c for c in self._marks.conflicts if c.item == item), None)
        if conflict is None:
            return False
        self._open_conflict(conflict)
        return True

    def _open_conflict(self, conflict: ConflictSummary) -> None:
        dialog = ConflictDialog(conflict, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.side:
                self._settle([conflict.conflict], dialog.side)
            else:
                choices = tuple(f"{conflict.conflict}\t{key}\t{side}" for key, side in dialog.choices.items())
                self._ask(SettleParameters(choices=choices))

    def _settle(self, conflicts: list[str], side: str) -> None:
        if conflicts:
            sides: dict[str, Literal["local", "server", "keep", "retry"]] = {LOCAL: "local", SERVER: "server", KEEP: "keep", RETRY: "retry"}
            self._ask(SettleParameters(conflicts=tuple(conflicts), side=sides[side]))

    def _ask(self, parameters: SettleParameters) -> None:
        self.use_local.setEnabled(False)
        self.use_server.setEnabled(False)
        self.keep_here.setEnabled(False)
        self.retry.setEnabled(False)
        self.problems.setText("")

        def settled(result: SettleResult) -> None:
            if result.problems:
                self.problems.setText(f"<span style='color:{red()}'>" + "<br>".join(html.escape(problem) for problem in result.problems) + "</span>")
            self._marks.refresh()  # the rows that were settled leave (the fact also asks; one question at a time)
            if result.retried:
                self.sync_requested.emit(list(result.retried))

        def failed(error: ChronoMailError) -> None:
            self.problems.setText(f"<span style='color:{red()}'>{error}</span>")
            self._buttons()

        self._backend.ask(CONFLICT_SETTLE, parameters, settled, failed, owner=self)


class SyncConflictsWindow(QDialog):
    """Sync conflicts in a window of its own (the owner: it is work to do, not a setting): wide, its size remembered, and it never blocks the rest
    of the program, so an appointment can be looked at while it is open. One for the main window; opened again, it comes to the front."""

    def __init__(self, backend: Backend, marks: SyncMarks, settings: QSettings | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self.setWindowTitle("Sync conflicts")
        self.setModal(False)
        layout = QVBoxLayout(self)  # the window's own margin all round; the panel inside it none, so the Close button lines up with the rest (the owner)
        self.panel = SyncConflictsPanel(backend, marks, self, settings)
        panel_layout = self.panel.layout()
        if panel_layout is not None:
            panel_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.close)
        layout.addWidget(close)
        self.resize(wpx(1100), wpx(520))
        if settings is not None:
            restore_geometry(self, settings, "sync_conflicts_geometry")

    def open_conflict(self, item: str) -> bool:
        """The popup of an item's conflict, straight away (a click on its triangle: what a person would guess, the owner)."""
        return self.panel.open_item(item)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        if self._settings is not None:
            save_geometry(self, self._settings, "sync_conflicts_geometry")
        super().hideEvent(event)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
