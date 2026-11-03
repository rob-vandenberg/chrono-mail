"""Settings > Sync conflicts (D-194): one row per item that differs between Chrono Mail and its server, ticked and settled with Use Chrono Mail or
Use Server data (every clashing field of each ticked item from that side), or per field in the popup a double click opens. Nothing here asks
questions during the work: a conflict waits on this page until the person settles it, or leaves it."""

import html
import time
from datetime import date, datetime
from typing import Literal, override

from PySide6.QtCore import QDate, QEvent, QLocale, QModelIndex, QObject, QPersistentModelIndex, QRect, QRectF, QSettings, QSize, Qt, QTime, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent, QKeySequence, QPainter, QPalette, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.pim.conflictwords import Words, rows
from chrono_mail.engine.commands_conflicts import CONFLICT_SETTLE, ConflictSummary, SettleParameters, SettleResult
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import css, red, theme
from chrono_mail.ui.forms import part
from chrono_mail.ui.icons import keep_both_icon, split_icon
from chrono_mail.ui.prefs import shortcuts
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.scale import scale_css, wpx
from chrono_mail.ui.syncmarks import BOTH, GONE_HERE, GONE_THERE, REFUSED, WHY_WORDS, SyncMarks, paint_why, why_kind, why_pixmap
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "syncconflicts.py 4.3.498"

# --- Version history ----------------------------------------------------
# v4.3.498: Keep both (B-30, B-31, the owner): the split icon at the end of a row of a field that holds several values turns it into two rows,
#           the server's and Chrono Mail's, both kept, without outline or circles; the person's Undo key joins the last split again; the Keep
#           both button keeps the whole item as two, every circle and split set aside; the split icon almost as tall as a row's outline, the
#           window of a conflict opens at the size the person gave it last, What as wide as its widest text (the owner)
# v4.2.497: a field that has a row never gets a second, raw row (a photo is "A photo" with its choice); Retry sync and Keep here only clear
#           the field circles, and a circle chosen after them lets the fields decide again (the owner)
# v4.2.496: a difference that is no choice (a jar's entry, a field of an occurrence on one side only) is shown without circles
# v4.2.494: the side is what the circles show: every field chosen by hand for one side is that side, and Apply works then (the owner)
# v4.2.493: the table has the gap between its columns on its left and right, so the outlines never touch the scrollbar (the owner)
# v4.2.492: a pair of list entries is one row with one choice; Apply gives that side to both entries (the owner, D-308)
# v4.2.491: a value the same on both sides starts in line with the text beside the circles (the owner)
# v4.2.489: the table grows with the window until every field shows; it opens at exactly 11 fields, by the rows' real heights (the owner)
# v4.2.487: the table of one conflict opens showing at most 11 fields (the owner)
# v4.2.486: the table of one conflict scrolls when the window is small, never taller than the screen; Retry sync and Keep here only
#           share the bottom row with Apply and Cancel (the owner)
# v4.2.485: the header's circles in line with the fields' circles (the owner)
# v4.2.483: every field that differs is a choice, nothing chosen beforehand (the owner)
# v4.2.481: the window of one conflict shows every field of the item in words, outlines what differs with its choices in the second accent,
#           and the header's Chrono Mail and Server choose every field (replacing Use Chrono Mail and Use Server data, the owner, D-308)
# v4.2.480: the window of one conflict: Use Chrono Mail and Use Server data (were All from …), Apply before Cancel (the owner)
# v4.0.375: the window, its panel and the window of one conflict are built from their Qt Designer forms (ui/forms/sync_conflicts.ui,
#           sync_conflicts_panel.ui, sync_conflict.ui, D-276); the columns, the field choices and the legend's icons stay the program's
# v3.6.350: an error text inside markup is escaped, the hover texts and the details of a conflict are plain: a server's or a file's words are
#           never drawn as markup (D-259)
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
WHAT_COLUMN: int = 2
CONFLICT_SIZE_KEY: str = "sync_conflict_size"  # the size the person gave the window of a conflict last: every conflict opens at it (the owner)
WHERE_COLUMN: int = 4
ROLE_KIND: int = Qt.ItemDataRole.UserRole + 2  # the Why cell: the kind of conflict it draws
LOCAL: str = "local"
SERVER: str = "server"
KEEP: str = "keep"
RETRY: str = "retry"
KEEP_BOTH: str = "both"  # the whole item kept as two (B-30); a field's choice of this keeps the entry of each side (B-31)
WHOLE_WAYS: tuple[str, ...] = (KEEP, RETRY, KEEP_BOTH)  # ways that settle the item as a whole: they set every circle and split aside
UNDO_KEY: str = "Ctrl+Z"  # the main window's Undo key when the person chose none of their own


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


def _difference_look() -> str:
    """The outline of a field that differs and its choices, in the theme's second accent (the owner, D-308)."""
    colour = css(theme().accent_alternative)
    return scale_css(
        f'QFrame[accent="alternative"] {{ border: 2px solid {colour}; border-radius: 4px; padding: 2px 4px; }}'
        f'QRadioButton[accent="alternative"]::indicator {{ width: 10px; height: 10px; border: 2px solid {colour}; border-radius: 7px; }}'
        f'QRadioButton[accent="alternative"]::indicator:checked {{ background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,'
        f' stop: 0 {colour}, stop: 0.55 {colour}, stop: 0.65 transparent, stop: 1 transparent); }}'
        'QRadioButton[header="true"] { margin-left: 6px; }'
        'QFrame[accent="same"] { border: 2px solid transparent; border-radius: 4px; padding: 2px 4px; }'  # the outline's border (2) and inner space (4) of the fields below
        'QFrame[accent="line"] { border: 2px solid transparent; border-left: none; border-right: none; padding: 2px 0px; }'  # a split row's label: as tall, not indented
        'QRadioButton[accent="alternative"]::indicator:disabled { border-color: palette(mid); }'
        'QRadioButton[accent="alternative"]::indicator:checked:disabled { background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5,'
        ' fy: 0.5, stop: 0 palette(mid), stop: 0.55 palette(mid), stop: 0.65 transparent, stop: 1 transparent); }'
    )


OPEN_ROWS: int = 11  # the table of one conflict opens showing at most this many fields (the owner: 780 pixels on his screen)


class _FieldsArea(QScrollArea):
    """The table of one conflict: as tall as it needs when there is room, scrolling up and down when the window is smaller; at least about
    three rows in view."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)  # a taller window shows more fields (the owner)

    @override
    def sizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:
            return super().sizeHint()
        hint = inner.sizeHint()
        return QSize(hint.width() + self.verticalScrollBar().sizeHint().width(), min(hint.height(), self._rows_height(OPEN_ROWS + 1)))

    @override
    def minimumSizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:
            return super().minimumSizeHint()
        return QSize(self.sizeHint().width(), min(inner.sizeHint().height(), self._rows_height(4)))  # the header and three rows

    def _rows_height(self, count: int) -> int:
        """The height of the first rows of the table as they are laid out: each row as tall as its tallest part, with the spacing between."""
        inner = self.widget()
        layout = inner.layout() if inner is not None else None
        if inner is None or not isinstance(layout, QGridLayout):
            return 0
        rows = min(count, layout.rowCount())
        heights = [max((item.sizeHint().height() for column in range(layout.columnCount()) if (item := layout.itemAtPosition(row, column)) is not None),
                       default=0) for row in range(rows)]
        return sum(heights) + layout.verticalSpacing() * max(rows - 1, 0)


def _aligned(text: str, parent: QWidget) -> QFrame:
    """A value the same on both sides: an outline that cannot be seen, its text indented by a circle and its gap, measured in the style."""
    frame = QFrame(parent)
    frame.setProperty("accent", "same")
    layout = QHBoxLayout(frame)
    probe = QRadioButton(frame)
    probe.setProperty("accent", "alternative")
    probe.ensurePolished()
    option = QStyleOptionButton()
    probe.initStyleOption(option)
    indent = probe.style().subElementRect(QStyle.SubElement.SE_RadioButtonContents, option, probe).left()
    probe.setParent(None)
    layout.setContentsMargins(indent, 0, 0, 0)
    layout.addWidget(_plain(text, frame))
    return frame


def _outlined(text: str, parent: QWidget, grid: QGridLayout, row: int, column: int) -> QRadioButton:
    """A value that differs: outlined, with its choice in front of it inside the outline."""
    frame = QFrame(parent)
    frame.setProperty("accent", "alternative")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    choice = QRadioButton(text, frame)  # its text is plain: QRadioButton never reads markup
    choice.setProperty("accent", "alternative")
    layout.addWidget(choice)
    grid.addWidget(frame, row, column)
    return choice


def _when(moment: date | datetime) -> str:
    """A date, or a date and time, in the computer's language: "12 August 2026", "12 August 2026, 09:00"."""
    locale = QLocale.system()
    day = locale.toString(QDate(moment.year, moment.month, moment.day), "d MMMM yyyy")
    if isinstance(moment, datetime):
        return f"{day}, {locale.toString(QTime(moment.hour, moment.minute), QLocale.FormatType.ShortFormat)}"
    return day


def _rows(conflict: ConflictSummary) -> list[tuple[str, str, str, str, bool, tuple[str, ...], bool]]:  # differs: the person chooses it
    """The item's fields in words (key, label, Chrono Mail's, the server's, differs, the entries its choice settles, whether it can be split into
    both); none for a conflict without both versions (mail, a whole collection, an item deleted on a side)."""
    if not conflict.local_text or not conflict.server_text:
        return []
    names = dict(conflict.colour_names)
    own = conflict.colour_names[0][1] if conflict.colour_names else "Calendar colour"

    def colour(colour_id: str) -> str:
        return names.get(colour_id, own if not colour_id else "A colour no longer in this calendar")

    found = rows(conflict.base_text or None, conflict.local_text, conflict.server_text, Words(_when, colour))
    return [(row.key, row.label, row.local, row.server, row.choice, row.keys, row.split) for row in found]


def _line(text: str, parent: QWidget) -> QFrame:
    """A label in a split row: as tall as the values beside it (the outline's border and inner space, unseen)."""
    frame = QFrame(parent)
    frame.setProperty("accent", "line")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(_plain(text, frame))
    return frame


def _two(first: QWidget, second: QWidget, parent: QWidget, spacing: int) -> QWidget:
    """Two lines of a split row, one below the other."""
    both = QWidget(parent)
    layout = QVBoxLayout(both)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for line in (first, second):
        line.setParent(both)  # which hides it: shown with its new parent
        layout.addWidget(line)
        line.show()
    return both


def _plain(text: str, parent: QWidget) -> QLabel:
    """A label that shows its text as it is: names and the server's words are never markup (D-259)."""
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class ConflictDialog(QDialog):
    """Every detail of one conflict, and a choice per clashing field (or of a whole side: an item changed before the agreed version was kept, or
    one deleted on a side). After Apply: `choices` (field key -> local, server or both), or `side` for a whole side (also Keep both). A row of a
    field that holds several values can be split: both kept (B-31); `undo_key` (the person's Undo key) joins the last split again."""

    def __init__(self, conflict: ConflictSummary, parent: QWidget | None = None, undo_key: str = UNDO_KEY, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self._opened: QSize | None = None  # the size it had when it was shown: another size at closing is the person's
        self.conflict: ConflictSummary = conflict
        self.choices: dict[str, str] = {}
        self.side: str = ""
        self.setWindowTitle(f"Sync conflict: {conflict.name}")
        self.groups: dict[str, tuple[QRadioButton, QRadioButton]] = {}
        self.settles: dict[str, tuple[str, ...]] = {}  # a row's choice -> every entry it settles: both entries of a pair (D-308)
        self.splitters: dict[str, QToolButton] = {}  # a row that can be split -> its split icon (B-31)
        self.split: list[str] = []  # the rows split, in the order they were split: Undo joins the last (the owner)
        self._places: dict[str, tuple[int, QLabel, str, str, QRadioButton | None]] = {}  # a row: its grid row, label, both texts, circle before
        self._joined: dict[str, tuple[QWidget, ...]] = {}  # a split row: what shows it as two lines
        form = forms.fill(self, "sync_conflict", {"fields": self._field_choices})  # the details, the fields, the sides, Apply and Cancel (D-276)
        details = part(form, QFormLayout, "details")
        for name, text in (
            ("what", KIND_WORDS.get(conflict.kind, conflict.kind)), ("where", _where(conflict)), ("why", conflict.reason_text),
            ("found", time.strftime("%Y-%m-%d %H:%M", time.localtime(conflict.when))), ("serverSaid", conflict.detail),
        ):
            value = part(form, QLabel, f"{name}Value")
            value.setTextFormat(Qt.TextFormat.PlainText)  # names and the server's own words: an HTML error page stays text (D-259)
            value.setText(text)
        details.setRowVisible(part(form, QLabel, "serverSaidValue"), bool(conflict.detail))  # only when the server said something
        self.all_local.setEnabled(conflict.local_allowed)  # greyed, never hidden: the server does not take Chrono Mail's version
        self.all_server.setEnabled(conflict.server_allowed)  # greyed: the server's version would delete mail that exists only here
        self.retry: QPushButton = part(form, QPushButton, "retrySync")  # a refusal: ask the server once more (the owner)
        self.retry.setEnabled(conflict.retry_allowed)
        self.keep: QPushButton = part(form, QPushButton, "keepHereOnly")
        self.keep.setEnabled(conflict.keep_allowed)
        self.all_local.clicked.connect(lambda: self._all(LOCAL))  # the header's choices: every field to that side (the owner)
        self.all_server.clicked.connect(lambda: self._all(SERVER))
        self.keep_both: QPushButton = part(form, QPushButton, "keepBoth")  # the whole item as two (B-30, the owner)
        self.keep_both.setIcon(keep_both_icon())
        self.keep_both.setEnabled(conflict.both_allowed)
        self.keep.clicked.connect(lambda: self._all(KEEP))
        self.retry.clicked.connect(lambda: self._all(RETRY))
        self.keep_both.clicked.connect(lambda: self._all(KEEP_BOTH))
        self.undo_split: QShortcut = QShortcut(QKeySequence(undo_key), self)  # the person's Undo key: the last split joined again (the owner)
        self.undo_split.activated.connect(self._join_last)
        box = part(form, QDialogButtonBox, "buttons")
        self.apply_button = box.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)  # an accepting button: Apply first, Cancel last (the owner)
        self.apply_button.clicked.connect(self._apply)
        box.rejected.connect(self.reject)
        forms.smallest(self, 560)  # its texts are never cut off, also at the largest size
        free = self.screen().availableGeometry().height()
        if self.sizeHint().height() > free:  # never taller than the screen: the table scrolls instead (the owner)
            self.resize(self.sizeHint().width(), free)
        saved = settings.value(CONFLICT_SIZE_KEY) if settings is not None else None
        if isinstance(saved, QSize) and saved.isValid():  # the size the person gave it last, within the screen (the owner)
            screen = self.screen().availableGeometry()
            self.resize(min(saved.width(), screen.width()), min(saved.height(), free))
        self._ready()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._opened = self.size()

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        if self._settings is not None and self._opened is not None and self.size() != self._opened:  # the person gave it another size
            self._settings.setValue(CONFLICT_SIZE_KEY, self.size())
        super().hideEvent(event)

    def _field_choices(self, parent: QWidget) -> QWidget:
        """Every field of the item with Chrono Mail's and the server's value in words; a field that differs is outlined, with a choice per side
        where it clashes (made at its place in the form, so Tab reaches the choices before the buttons below them, D-308)."""
        conflict = self.conflict
        area = _FieldsArea(parent)  # a long item scrolls; what is above and below it stays in view (the owner)
        choices = QWidget(area)
        choices.setStyleSheet(_difference_look())
        grid = QGridLayout(choices)
        gap = max(grid.horizontalSpacing(), 0)  # the style's gap between the columns, also at its left and right (the owner)
        grid.setContentsMargins(gap, 0, gap, 0)
        grid.addWidget(QLabel("<b>Field</b>", choices), 0, 0)
        self.all_local = QRadioButton("Chrono Mail", choices)  # every field from Chrono Mail; on while all are (the owner)
        self.all_server = QRadioButton("Server", choices)
        for column, header in ((1, self.all_local), (2, self.all_server)):
            header.setAutoExclusive(False)  # both are off for a mix: set by what the fields hold, never by each other
            header.setProperty("accent", "alternative")
            header.setProperty("header", "true")  # its circle in line with the circles inside the outlines below it (the owner)
            header.setStyleSheet("font-weight: bold;")
            grid.addWidget(header, 0, column)
        per_field = not conflict.whole and bool(conflict.fields) and conflict.local_allowed and conflict.server_allowed  # else a whole side
        shown = _rows(conflict)
        row = 0
        self._grid: QGridLayout = grid
        self._inner: QWidget = choices
        for row, (key, label, ours_text, theirs_text, differs, keys, splits) in enumerate(shown, start=1):
            title = _plain(label, choices)
            grid.addWidget(title, row, 0)
            if not differs:  # the same on both sides, or taken by itself: its text in line with the text beside the circles (the owner)
                grid.addWidget(_aligned(ours_text, choices), row, 1)
                grid.addWidget(_aligned(theirs_text, choices), row, 2)
                continue
            ours, theirs = _outlined(ours_text, choices, grid, row, 1), _outlined(theirs_text, choices, grid, row, 2)
            group = QButtonGroup(self)  # every difference is the person's choice, nothing chosen beforehand (the owner, 4.2.483)
            group.addButton(ours)
            group.addButton(theirs)
            group.buttonToggled.connect(self._toggled)
            ours.setEnabled(per_field)
            theirs.setEnabled(per_field)
            self.groups[key] = (ours, theirs)
            self.settles[key] = keys
            if splits:  # a field that holds several values: both can be kept (B-31, the owner)
                splitter = QToolButton(choices)
                splitter.setIcon(split_icon())
                frame = theirs.parentWidget()
                outline = frame.sizeHint().height() if frame is not None else splitter.sizeHint().height()
                splitter.setFixedSize(outline, outline)  # almost as tall as the row's outline (the owner)
                splitter.setIconSize(QSize(outline - 2, outline - 2))
                splitter.setAutoRaise(True)
                splitter.setToolTip("Keep both")
                splitter.setAccessibleName("Keep both")
                splitter.setEnabled(per_field)
                splitter.clicked.connect(lambda _=False, key=key: self._split(key))
                grid.addWidget(splitter, row, 3)
                self.splitters[key] = splitter
                self._places[key] = (row, title, ours_text, theirs_text, None)
        covered = {each for *_, keys, _ in shown for each in keys}  # a field with a row never gets a second, raw one (D-308)
        for field in conflict.fields:  # a clash without a field of its own above (mail, a collection, an item that cannot be read)
            if field.key in covered:
                continue
            row += 1
            grid.addWidget(_plain(field.label, choices), row, 0)
            ours, theirs = _outlined(field.local or "(empty)", choices, grid, row, 1), _outlined(field.server or "(empty)", choices, grid, row, 2)
            group = QButtonGroup(self)
            group.addButton(ours)
            group.addButton(theirs)
            group.buttonToggled.connect(self._toggled)
            ours.setEnabled(per_field)
            theirs.setEnabled(per_field)
            self.groups[field.key] = (ours, theirs)
        if not conflict.fields and not shown:
            grid.addWidget(_plain(conflict.reason_text, choices), 1, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        area.setWidget(choices)
        area.setMaximumHeight(choices.sizeHint().height())  # every field in view: more height goes below the table
        self._area: _FieldsArea = area
        return area

    def _fit_area(self) -> None:
        """After a split or a join: the table as tall as its rows again, where the window has room (as when it opened)."""
        layout = self._inner.layout()
        if layout is not None:
            layout.activate()
        self._area.setMaximumHeight(self._inner.sizeHint().height())
        self._area.updateGeometry()

    def _toggled(self, button: QAbstractButton, checked: bool) -> None:
        if checked and self.side in WHOLE_WAYS:  # a circle chosen after Retry sync, Keep here only or Keep both: the fields decide again (the owner)
            self.side = ""
        self._ready()

    @staticmethod
    def _clear(ours: QRadioButton, theirs: QRadioButton) -> None:
        """Neither circle of a row on."""
        group = ours.group()
        if group is not None:
            group.setExclusive(False)
        ours.setChecked(False)
        theirs.setChecked(False)
        if group is not None:
            group.setExclusive(True)

    def _all(self, side: str) -> None:
        if side in WHOLE_WAYS:  # a whole way that chooses no field: every split joined again, no circle shows a side (the owner)
            while self.split:
                self._join(self.split[-1])
        for key, (ours, theirs) in self.groups.items():
            if side in WHOLE_WAYS:
                self._clear(ours, theirs)
            elif key not in self.split:  # a split row keeps both: the header chooses the rows that have circles
                (ours if side == LOCAL else theirs).setChecked(True)
        self.side = side
        self._ready()

    def _split(self, key: str) -> None:
        """A row turned into two: the server's value and Chrono Mail's, each kept, without outline or circles (B-31, the owner)."""
        if key in self.split or key not in self._places:
            return
        ours, theirs = self.groups[key]
        row, title, ours_text, theirs_text, _ = self._places[key]
        self._places[key] = (row, title, ours_text, theirs_text, ours if ours.isChecked() else theirs if theirs.isChecked() else None)
        self._clear(ours, theirs)
        grid, inner, spacing = self._grid, self._inner, max(self._grid.verticalSpacing(), 0)
        frames = [circle.parentWidget() for circle in (ours, theirs)]
        for widget in (title, *frames):
            if widget is not None:
                grid.removeWidget(widget)
                widget.hide()
        lines = (
            _two(_line(title.text(), inner), _line(title.text(), inner), inner, spacing),
            _two(_aligned(theirs_text, inner), _aligned(ours_text, inner), inner, spacing),  # the server's first, then Chrono Mail's
            _two(_aligned(theirs_text, inner), _aligned(ours_text, inner), inner, spacing),
        )
        for column, widget in enumerate(lines):
            grid.addWidget(widget, row, column)
            widget.show()
        self._joined[key] = lines
        self.splitters[key].setEnabled(False)  # greyed: it is split (Undo joins it again)
        self.split.append(key)
        self._fit_area()
        if self.side in WHOLE_WAYS:  # a split after Retry sync, Keep here only or Keep both: the fields decide again
            self.side = ""
        self._ready()

    def _join(self, key: str) -> None:
        """A split row back as one, with its outline and the circle it had."""
        if key not in self.split:
            return
        self.split.remove(key)
        grid = self._grid
        for widget in self._joined.pop(key):
            grid.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        row, title, _, _, before = self._places[key]
        ours, theirs = self.groups[key]
        for column, widget in ((0, title), (1, ours.parentWidget()), (2, theirs.parentWidget())):
            if widget is not None:
                grid.addWidget(widget, row, column)
                widget.show()
        if before is not None:
            before.setChecked(True)
        self.splitters[key].setEnabled(True)
        self._fit_area()
        self._ready()

    def _join_last(self) -> None:
        if self.split:
            self._join(self.split[-1])

    def _ready(self) -> None:
        open_rows = {key: pair for key, pair in self.groups.items() if key not in self.split}  # a split row is decided: both kept
        whole_way = self.side in WHOLE_WAYS
        chosen = whole_way or (all(ours.isChecked() or theirs.isChecked() for ours, theirs in open_rows.values()) if self.groups else bool(self.side))
        mixed = bool(self.split) or (any(ours.isChecked() for ours, _ in open_rows.values()) and any(theirs.isChecked() for _, theirs in open_rows.values()))
        if mixed:
            self.side = ""
        elif chosen and open_rows and not whole_way:  # the side is what the circles show, never held apart from them (the owner)
            self.side = LOCAL if all(ours.isChecked() for ours, _ in open_rows.values()) else SERVER
        self.apply_button.setEnabled(chosen and (bool(self.side) or mixed))
        whole = self.side in (LOCAL, SERVER) and not self.groups
        every = not whole_way and bool(open_rows)
        self.all_local.setChecked((whole and self.side == LOCAL) or (every and all(ours.isChecked() for ours, _ in open_rows.values())))
        self.all_server.setChecked((whole and self.side == SERVER) or (every and all(theirs.isChecked() for _, theirs in open_rows.values())))

    def _apply(self) -> None:
        if not self.side:
            self.choices = {each: (KEEP_BOTH if key in self.split else LOCAL if ours.isChecked() else SERVER) for key, (ours, _) in self.groups.items()
                            for each in self.settles.get(key, (key,))}  # a pair: its side's entry kept, the other's left out; split: both
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
        form = forms.fill(self, "sync_conflicts_panel")  # Check all, the table, the buttons, the problems and the legend (D-276)
        self.check_all: QCheckBox = part(form, QCheckBox, "checkAll")
        self.check_all.clicked.connect(self._check_all)
        self.table: QTreeWidget = part(form, QTreeWidget, "table")
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
        widths = {0: 28, 1: metrics.horizontalAdvance("Location, Starts") + 16, 5: metrics.horizontalAdvance("Why") + 28}
        for column, width in widths.items():
            header.resizeSection(column, width)
        header.sectionResized.connect(self._name_dragged)
        self.table.viewport().installEventFilter(self)  # a new width of the window: Name and Where take their shares again
        self.table.headerItem().setTextAlignment(WHY_COLUMN, Qt.AlignmentFlag.AlignCenter)  # "Why" and its icons in the middle (the owner)
        self.table.setItemDelegateForColumn(WHY_COLUMN, WhyDelegate(self.table))
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemDoubleClicked.connect(self._open)
        self.use_local: QPushButton = part(form, QPushButton, "useLocal")
        self.use_server: QPushButton = part(form, QPushButton, "useServer")
        self.retry: QPushButton = part(form, QPushButton, "retrySync")  # a refusal: ask the server once more; refused again, the same row comes back (the owner)
        self.keep_here: QPushButton = part(form, QPushButton, "keepHereOnly")  # mail made here that the server will not take: it stays here, never sent (D-195)
        self.invert: QPushButton = part(form, QPushButton, "invertSelection")
        self.use_local.clicked.connect(lambda: self._settle(self.ticked(), LOCAL))
        self.use_server.clicked.connect(lambda: self._settle(self.ticked(), SERVER))
        self.keep_here.clicked.connect(lambda: self._settle(self.ticked(), KEEP))
        self.retry.clicked.connect(lambda: self._settle(self.ticked(), RETRY))
        self.invert.clicked.connect(self._invert)
        self.problems: QLabel = part(form, QLabel, "problems")
        for name, kind in (("Both", BOTH), ("Refused", REFUSED), ("GoneThere", GONE_THERE), ("GoneHere", GONE_HERE)):  # what the Why icons mean (the owner)
            part(form, QLabel, f"why{name}Icon").setPixmap(why_pixmap(kind, 16, self.palette().color(QPalette.ColorRole.Window)))
            part(form, QLabel, f"why{name}Text").setText(WHY_WORDS[kind])  # the same words as the icons' hover texts
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
                    item.setToolTip(column, plain_tip(text))  # a subject or a folder name: never markup (D-259)
            item.setData(WHY_COLUMN, ROLE_KIND, kind)
            item.setToolTip(WHY_COLUMN, plain_tip(f"{WHY_WORDS[kind]}: {conflict.reason_text}"))  # the server's words: never markup
        self.table.blockSignals(False)
        self._fit_what()
        self._buttons()

    def _fit_what(self) -> None:
        """What exactly as wide as its widest text, the header's included (the owner); Name and Where take what is left."""
        items = (self.table.topLevelItem(number) for number in range(self.table.topLevelItemCount()))
        texts = [item.text(WHAT_COLUMN) for item in items if item is not None]
        widest = max([self.table.fontMetrics().horizontalAdvance(text) for text in texts] + [self.table.header().fontMetrics().horizontalAdvance(COLUMNS[WHAT_COLUMN])])
        self.table.header().resizeSection(WHAT_COLUMN, widest + 16)

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
        undo_key = shortcuts(self._settings).get("undo", UNDO_KEY) if self._settings is not None else UNDO_KEY  # the person's own Undo key
        dialog = ConflictDialog(conflict, self, undo_key, self._settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.side:
                self._settle([conflict.conflict], dialog.side)
            else:
                choices = tuple(f"{conflict.conflict}\t{key}\t{side}" for key, side in dialog.choices.items())
                self._ask(SettleParameters(choices=choices))

    def _settle(self, conflicts: list[str], side: str) -> None:
        if conflicts:
            sides: dict[str, Literal["local", "server", "keep", "retry", "both"]] = {
                LOCAL: "local", SERVER: "server", KEEP: "keep", RETRY: "retry", KEEP_BOTH: "both",
            }
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
            self.problems.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")
            self._buttons()

        self._backend.ask(CONFLICT_SETTLE, parameters, settled, failed, owner=self)


class SyncConflictsWindow(QDialog):
    """Sync conflicts in a window of its own (the owner: it is work to do, not a setting): wide, its size remembered, and it never blocks the rest
    of the program, so an appointment can be looked at while it is open. One for the main window; opened again, it comes to the front."""

    def __init__(self, backend: Backend, marks: SyncMarks, settings: QSettings | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: QSettings | None = settings
        self.setModal(False)
        form = forms.fill(self, "sync_conflicts", {"panel": lambda parent: SyncConflictsPanel(backend, marks, parent, settings)})  # D-276
        self.panel: SyncConflictsPanel = part(form, SyncConflictsPanel, "panel")
        close = part(form, QDialogButtonBox, "buttons")
        close.rejected.connect(self.close)
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
