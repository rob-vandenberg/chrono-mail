import html
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import override

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QIcon, QFocusEvent, QFont, QFontMetrics, QGuiApplication, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPalette, QPen, QWheelEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip, QWidget

from chrono_mail.core.colours import NEW_CALENDAR
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Bar, Cell, Entry, Segment
from chrono_mail.ui.colours import theme
from chrono_mail.ui.syncmarks import CONFLICT, MARK_GAP, MARK_SIZE, MARK_TIPS, WAITING, mark_icon, paint_mark
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.tagging import tag_path

WHEEL_NOTCH: int = 120  # one notch of a mouse wheel, as Qt counts it

# --- Version ------------------------------------------------------------
__version__ = "calendarwidgets.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: Every tag of an appointment as a small tag in its colour at the end of its title; hovering shows its title, from, to, calendar and tags (D-329)
# v4.4.567: An event is known by its item and date, so it stays selected after a change; a title typed in place shows at once, with the clock until the server answers (D-325)
# v4.4.566: A click on the selected event, or F2, edits its title in place, where it is drawn; Enter or a click elsewhere saves, Escape does not (D-324)
# v4.4.564: The agenda keeps its selected event and the line at its top when it is filled again for the new day (D-322)
# v4.4.558: The agenda has the right-click menus of the other views
# v4.4.550: Hovering a cancelled event says Cancelled and its calendar, in every view (D-316)
# v4.4.535: A location of several lines is drawn on one line (round 4)
# v4.2.438: Month draws a filled timed event as an all-day one, its dot in the text's colour; paint_block draws the dot (UR-02, D-297)
# v3.5.346: Every view takes the keyboard focus from its first click (it did only after a turn of the wheel), so Delete deletes the event, not the calendar (RV-22)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.230: The mark where the text would start, with the same small gap after it as after the dot (the owner)
# v1.1.229: The marks 20% smaller; a click on an event's triangle opens its conflict (conflict_clicked); hovering a mark says what it is (the owner)
# v1.1.226: An event's sync mark before its title in every view: the triangle for a conflict, the waiting mark (D-194)
# v1.1.217: The wheel goes a period back or forward: over the month, over the row of day names, and Shift+wheel over the hours; a touchpad adds up to whole notches (D-189)
# v1.1.172: Colours from the colour file: the line of the present moment, the outline of a selected event, an event without a calendar colour (D-158)
# v0.0.64: Initial views: the time grid (day, week), the all-day header, the month and the agenda

GUTTER: int = 54
DAY_ROW: int = 40
BAR_HEIGHT: int = 20
BAR_GAP: int = 2
DEFAULT_HOUR_HEIGHT: int = 48
MINIMUM_HOUR_HEIGHT: int = 24
MAXIMUM_HOUR_HEIGHT: int = 140
SNAP_MINUTES: int = 30


def one_line(text: str) -> str:
    """A location of several lines, as the calendar draws it."""
    return ", ".join(line.strip() for line in text.splitlines() if line.strip())


def event_tip(entry: Entry) -> str:
    """The hover text of an appointment (D-329): its title, from and to, its calendar, and its tags, each as a label in its colour; a cancelled
    one says so (D-316)."""
    def when(moment: datetime) -> str:
        return moment.strftime("%x") if entry.all_day else moment.strftime("%x %H:%M")

    last = entry.end - timedelta(days=1) if entry.all_day else entry.end
    rows = [("From", html.escape(when(entry.start))), ("To", html.escape(when(last))), ("Calendar", html.escape(entry.row.calendar))]
    if entry.row.cancelled:
        rows.insert(0, ("Status", "Cancelled"))
    if entry.tag_colours:
        labels = "<br>".join(
            f"<span style='background:{colour}; color:{TagColourBook.text_colour(QColor(colour)).name()}'>&nbsp;{html.escape(name)}&nbsp;</span>" for name, colour in entry.tag_colours
        )
        rows.append(("Tags", labels))
    table = "".join(f"<tr><td style='padding-right:12px'><b>{label}:</b></td><td>{value}</td></tr>" for label, value in rows)
    return f"<b>{html.escape(entry.summary or '(no title)')}</b><table cellspacing=2>{table}</table>"


def paint_tags(painter: QPainter, rect: QRectF, y: float, height: float, entry: Entry, fill: QColor, text_colour: QColor) -> float:
    """The tags of an appointment as small tag shapes at the right end of its first line, each in its colour; the one of the appointment's own
    colour as an outline in the text's colour, so it stays visible (D-329). Returns where they begin, for the room left to the text."""
    if not entry.tag_colours:
        return rect.right()
    size = max(6, int(height * 0.7))
    right = rect.right() - 4
    top = int(y + (height - size) / 2)
    fits = max(0, int((right - (rect.left() + size * 3)) // (size + 3)))  # a narrow block keeps room for its title
    for _, colour in reversed(entry.tag_colours[:fits]):  # the first tags, the colour tag above all, are the ones that stay
        left = int(right - size)
        shape = tag_path(QRect(left, top + size // 6, size, size - 2 * (size // 6)))
        own = QColor(colour)
        if own.name() == fill.name():
            painter.setPen(QPen(text_colour, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.setPen(QPen(own.darker(130), 1))
            painter.setBrush(own)
        painter.drawPath(shape)
        right = left - 3
    return right


def fill_colour(entry: Entry) -> QColor:
    return QColor(entry.colour) if entry.colour and QColor(entry.colour).isValid() else QColor(NEW_CALENDAR)


def no_mark(item: str) -> str:
    return ""


def item_of(entry: Entry) -> str:
    return entry.row.item


DOT_CENTRE: float = 7.0  # a timed event's dot in Month: its centre from the left of its line, its radius, the room before its text
DOT_RADIUS: float = 3.5
DOT_ROOM: int = 9


def same_event(one: Entry | None, other: Entry | None) -> bool:
    """The same occurrence of the same event, whatever changed in it: its title, its time or its day (D-324, D-325). An event is its item; a date of a
    series, or of an object of only changed dates, is its occurrence (the date it had first, which a change never moves)."""
    return one is not None and other is not None and (one.row.item, one.row.occurrence) == (other.row.item, other.row.occurrence)


def first_line(rect: QRectF, font: QFont, mark: str, dot: bool) -> tuple[float, float, int, float]:
    """Where paint_block puts the first line of a block: its top, the left of its sync mark, the mark's size, and the left of its text.
    The title edited in place lies exactly there (D-324)."""
    metrics = QFontMetrics(bold_of(font))
    y = rect.top() + (2 if rect.height() >= 2 * metrics.height() else max(0.0, (rect.height() - metrics.height()) / 2))  # a short block: the title in the middle
    size = min(MARK_SIZE, metrics.height())
    lead = DOT_ROOM if dot else 0  # the dot comes first, then the mark and the text, as in a timed event without a background
    mark_left = rect.left() + (lead + 5 if dot else 4)  # with the dot the mark and the text start where they do on a line without a background
    text_left = mark_left + size + MARK_GAP if mark else rect.left() + lead + 5  # after a mark the same small gap as after the dot (the owner)
    return y, mark_left, size, text_left


def block_colours(entry: Entry, faded: bool = False) -> tuple[QColor, QColor]:
    """A block's colour (lighter for a free event) and its text's: white or black, whichever reads better on it; the dot follows the text (D-297)."""
    colour = fill_colour(entry)
    if faded:
        colour = QColor(colour)
        colour.setAlpha(150)
    return colour, TagColourBook.text_colour(colour)


def bold_of(font: QFont) -> QFont:
    bold = QFont(font)
    bold.setBold(True)
    return bold


def paint_block(painter: QPainter, rect: QRectF, entry: Entry, selected: bool, font: QFont, lines: list[str], faded: bool = False, mark: str = "",
                dot: bool = False) -> QRectF | None:
    """An event as a rounded block in the colour of its calendar, with its text; selected ones get an outline; a dot in the text's colour first
    when asked (a timed event in Month, D-297), then its sync mark (D-194) before the first line. Returns where the mark was drawn (None
    without one), so a click or a hover there can be told apart."""
    colour, text_colour = block_colours(entry, faded)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(theme().swatch_selected), 1.6) if selected else Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(text_colour)
    painter.setClipRect(rect.adjusted(3, 0, -2, 0))
    bold = bold_of(font)
    painter.setFont(bold)
    metrics = QFontMetrics(bold)
    y, mark_left, size, first_left = first_line(rect, font, mark, dot)
    lead = DOT_ROOM if dot else 0
    mark_area = QRectF(mark_left, int(y + metrics.height() / 2) - size // 2, size, size) if mark else None
    for index, line in enumerate(lines):
        painter.setFont(bold if index == 0 else font)
        if dot and index == 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(text_colour)
            painter.drawEllipse(QPointF(rect.left() + DOT_CENTRE, y + metrics.height() / 2), DOT_RADIUS, DOT_RADIUS)
            painter.setPen(text_colour)
        text_right = rect.right()
        if index == 0:
            paint_mark(painter, int(mark_left), int(y + metrics.height() / 2), mark, size)
            text_right = paint_tags(painter, rect, y, metrics.height(), entry, colour, text_colour)  # the tag icons at the end of the title (D-329)
            painter.setPen(text_colour)
        text_left = first_left if index == 0 else rect.left() + lead + 5
        painter.drawText(QRectF(text_left, y, text_right - 3 - text_left, metrics.height()), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(font).elidedText(line, Qt.TextElideMode.ElideRight, int(text_right - 3 - text_left)))
        y += metrics.height()
        if y + metrics.height() > rect.bottom():
            break
    painter.restore()
    return mark_area


@dataclass(slots=True)
class PendingTitle:
    """A title typed in place on its way to the server (D-325): shown at once, with the clock while the server has not answered; after its yes it
    stays shown until the event's new version is in the view."""

    title: str
    sent: int = 0  # how many fetches of events the page had asked when it was sent: only a later fetch of its own event brings its new version
    answered: int = 0  # the same at the server's yes: only a later fetch of all events brings it (one asked before read the event as it was)
    waiting: bool = True  # the server has not answered yet: the clock, and no new edit
    fetched: bool = False  # the event's new version came into the view meanwhile


def followed(entry: Entry | None, candidates: Iterable[Entry]) -> Entry | None:
    """The event among the candidates that is this one after a change (D-325): the same date of the same event; when a change of the series
    moved its dates (they are known by the time they first had), its date on the same day. Never another day: a date deleted on its own must
    not hand its selection to the next date of the series."""
    if entry is None:
        return None
    own = [candidate for candidate in candidates if candidate.row.item == entry.row.item]
    exact = next((candidate for candidate in own if same_event(candidate, entry)), None)
    if exact is not None:
        return exact
    return next((candidate for candidate in own if candidate.first_day == entry.first_day), None)


def event_key(entry: Entry) -> tuple[str, str]:
    """An event's own identity (same_event): its item and the date of the series it is."""
    return entry.row.item, entry.row.occurrence


@dataclass(frozen=True, slots=True)
class TitlePlace:
    """Where an event's title was drawn, and how: the title edited in place takes exactly this (D-324)."""

    area: QRectF  # the title's text, without a time before it
    entry: Entry
    font: QFont
    colour: QColor  # the text's colour
    background: QColor  # what lies under the text


class TitleEditor(QLineEdit):
    """An event's title edited in place: no frame and no background of its own, over the drawn title (D-324). Enter or a click elsewhere
    ends it with the text, Escape with None. A menu of its own or another window coming to the front leaves it open."""

    finished = Signal(object)  # the text, or None: cancelled

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("titleInPlace")  # the look of theme.py: no border, no background, no padding
        self.setFrame(False)
        self.setTextMargins(0, 0, 0, 0)
        self._open: bool = True
        self.returnPressed.connect(lambda: self._end(self.text()))

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._end(None)
            return
        if event.key() == Qt.Key.Key_F2:  # already editing: F2 must not reach the view, which would start again from the old title
            event.accept()
            return
        super().keyPressEvent(event)

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if event.reason() not in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            self._end(self.text())

    def end_with_text(self) -> None:
        self._end(self.text())

    def _end(self, text: str | None) -> None:
        if self._open:
            self._open = False
            self.finished.emit(text)


class EntryHits(QWidget):
    """What the views have in common: the places where events were drawn (to find what is clicked), the selected event, and the signals."""

    entry_clicked = Signal(object)  # an Entry
    conflict_clicked = Signal(str)  # the triangle of an event was clicked: its item (D-194, the owner)
    entry_activated = Signal(object)  # double click on an event
    slot_activated = Signal(object, int)  # double click on a free place: the day and the minute (-1: the all-day area)
    nothing_clicked = Signal()
    context_requested = Signal(object, object, QPoint)  # the Entry or None, the (day, minute) or None, where
    period_step = Signal(int)  # the wheel: one day, week or month back (-1) or forward (1)
    title_edit_wanted = Signal(object)  # a click on the selected event, or F2: the page says whether its title may be edited in place (D-324)
    title_edited = Signal(object, str)  # the event and the new title typed in place (D-324)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel: int = 0  # what the wheel moved that is not a whole notch yet (a touchpad moves in small amounts)
        self.hits: list[tuple[QRectF, Entry]] = []
        self.titles: list[TitlePlace] = []  # where the titles were drawn (D-324)
        self.selected: Entry | None = None
        self.mark_of: Callable[[str], str] = no_mark  # an event's sync mark: a conflict, waiting for the server, or none (D-194)
        self.mark_hits: list[tuple[QRectF, Entry]] = []  # where the marks were drawn: a click on a triangle opens its conflict, a hover explains
        self.title_editor: TitleEditor | None = None
        self._editing: Entry | None = None
        self.pending: dict[tuple[str, str], PendingTitle] = {}  # titles on their way to the server; the page shares one with every view (D-325)
        self._title_click = QTimer(self)  # a click on the selected event edits its title once no second click made it a double click
        self._title_click.setSingleShot(True)
        self._title_click.timeout.connect(self._title_click_ended)
        self._clicked_again: Entry | None = None
        self._ended_by_click: bool = False  # the press being handled just ended a title edit (its focus left first)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # a click on an event gives the view the keyboard, so Delete acts on the event (D-217, RV-22)

    def note_title(self, rect: QRectF, entry: Entry, font: QFont, mark: str, dot: bool, colour: QColor, background: QColor, prefix: str = "") -> None:
        """Where an event's title was drawn by paint_block (or a line like it): prefix is a time drawn before the title on the same line."""
        y, _mark_left, _size, text_left = first_line(rect, font, mark, dot)
        title_font = bold_of(font)
        text_left += QFontMetrics(title_font).horizontalAdvance(prefix)
        self.titles.append(TitlePlace(QRectF(text_left, y, max(1.0, rect.right() - 3 - text_left), QFontMetrics(title_font).height()), entry, title_font, colour,
                                      background))

    def edit_title(self, entry: Entry) -> bool:
        """The title of this event edited in place, where it was drawn; False when it is not drawn here."""
        place = next((place for place in self.titles if same_event(place.entry, entry)), None)
        if place is None:
            return False
        self.close_title()
        editor = TitleEditor(self)
        editor.setText(self.current_title(entry))
        editor.selectAll()
        editor.finished.connect(self._title_finished)
        self.title_editor = editor
        self._editing = entry
        self._place_title(place)
        editor.show()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def close_title(self) -> None:
        """An open title edit goes without a change."""
        editor = self.title_editor
        if editor is not None:
            self.title_editor = None
            self._editing = None
            editor.blockSignals(True)
            editor.hide()
            editor.deleteLater()

    def _place_title(self, place: TitlePlace) -> None:
        """The edit where the title is drawn now, in its font and colours now (a Month line turns blue when it is selected)."""
        editor = self.title_editor
        if editor is None:
            return
        editor.setGeometry(QRect(int(place.area.left()) - 2, int(place.area.top()), int(place.area.width()) + 2, int(place.area.height())))  # Qt's line edit
        if editor.font() != place.font:  # keeps 2 pixels before its text: the text stands exactly on the drawn title
            editor.setFont(place.font)
        palette = editor.palette()
        palette.setColor(QPalette.ColorRole.Text, place.colour)
        palette.setColor(QPalette.ColorRole.Highlight, place.colour)  # the selected text the other way round: it reads on any block
        palette.setColor(QPalette.ColorRole.HighlightedText, place.background)
        if palette != editor.palette():
            editor.setPalette(palette)

    def follow_title(self) -> None:
        """After a paint: the open title edit stays on its event, wherever it is drawn now; it goes when its event is no longer drawn."""
        if self.title_editor is None or self._editing is None:
            return
        place = next((place for place in self.titles if same_event(place.entry, self._editing)), None)
        if place is None:
            QTimer.singleShot(0, self, self._title_left)  # not during the paint itself
            return
        self._place_title(place)

    def _title_left(self) -> None:
        """The event is no longer drawn here (another week, another view): leaving it saves what was typed, as a click elsewhere does."""
        editor, entry = self.title_editor, self._editing
        if editor is not None and entry is not None and not any(same_event(place.entry, entry) for place in self.titles):
            editor.end_with_text()

    def editing_item(self) -> str:
        """The event whose title is being edited here, or ""."""
        return self._editing.row.item if self.title_editor is not None and self._editing is not None else ""

    def _title_finished(self, text: object) -> None:
        entry, editor = self._editing, self.title_editor
        self.title_editor = None
        self._editing = None
        if editor is not None:
            keep_keyboard = editor.hasFocus()  # Enter or Escape: the keyboard comes back to the view; a click elsewhere keeps it there
            editor.hide()
            editor.deleteLater()
            if keep_keyboard:
                self.setFocus(Qt.FocusReason.OtherFocusReason)
            else:  # a click elsewhere ended it, also a click on its own block: that press must not open the edit again
                self._ended_by_click = True
                QTimer.singleShot(0, self, self._click_handled)
        title = text.strip() if isinstance(text, str) else ""
        if entry is not None and title and title != self.current_title(entry):  # an empty or unchanged title saves nothing
            self.title_edited.emit(entry, title)

    def _click_handled(self) -> None:
        self._ended_by_click = False

    def title_shown(self, entry: Entry) -> str:
        """The title to draw: none for the event whose title is being edited, the edit stands there (D-324); a title on its way to the server
        as typed (D-325)."""
        if self.title_editor is not None and same_event(entry, self._editing):
            return ""
        pending = self.pending.get(event_key(entry))
        return pending.title if pending is not None else entry.summary

    def current_title(self, entry: Entry) -> str:
        """The title the event has as far as the person can see: a title on its way, else its own (empty when it has none)."""
        pending = self.pending.get(event_key(entry))
        return pending.title if pending is not None else entry.row.summary

    def mark_for(self, entry: Entry) -> str:
        """The mark before the title: the clock while its title waits for the server's answer (D-325), else its sync mark (D-194)."""
        own = self.mark_of(item_of(entry))
        pending = self.pending.get(event_key(entry))
        return WAITING if own != CONFLICT and pending is not None and pending.waiting else own

    def _title_click_ended(self) -> None:
        entry, self._clicked_again = self._clicked_again, None
        if entry is not None and same_event(entry, self.selected):
            self.title_edit_wanted.emit(self.selected)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F2 and event.modifiers() == Qt.KeyboardModifier.NoModifier and self.selected is not None:
            self.title_edit_wanted.emit(self.selected)
            event.accept()
            return
        super().keyPressEvent(event)

    def step_by_wheel(self, event: QWheelEvent) -> None:
        """One period per notch: up is back in time, down is forward. Small movements are added up until they make a notch."""
        delta = event.angleDelta()
        self._wheel += delta.y() or delta.x()  # with Shift some desktops send the wheel as sideways
        while abs(self._wheel) >= WHEEL_NOTCH:
            direction = -1 if self._wheel > 0 else 1
            self._wheel += WHEEL_NOTCH * direction
            self.period_step.emit(direction)
        event.accept()

    def entry_at(self, position: QPointF) -> Entry | None:
        for rect, entry in reversed(self.hits):  # what was drawn last is on top
            if rect.contains(position):
                return entry
        return None

    def slot_at(self, position: QPointF) -> tuple[date, int] | None:
        return None

    def select(self, entry: Entry | None) -> None:
        self.selected = entry
        self.update()

    def mark_at(self, position: QPointF) -> Entry | None:
        """The event whose sync mark is at this place (what was drawn last is on top)."""
        return next((entry for rect, entry in reversed(self.mark_hits) if rect.adjusted(-2, -2, 2, 2).contains(position)), None)

    def note_mark(self, area: QRectF | None, entry: Entry) -> None:
        if area is not None:
            self.mark_hits.append((area, entry))

    @override
    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            position = QPointF(event.pos())
            marked = self.mark_at(position)
            entry = self.entry_at(position)
            tip = MARK_TIPS.get(self.mark_for(marked), "") if marked is not None else event_tip(entry) if entry is not None else ""
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        marked = self.mark_at(event.position())
        if marked is not None and self.mark_for(marked) == CONFLICT:  # the triangle: what a person would guess, its conflict opens
            self.select(marked)
            self.conflict_clicked.emit(item_of(marked))
            return
        entry = self.entry_at(event.position())
        again = event.button() == Qt.MouseButton.LeftButton and same_event(entry, self.selected) and not self._ended_by_click
        self.select(entry)
        self._title_click.stop()
        self._clicked_again = entry if again else None
        if again:
            self._title_click.start(QGuiApplication.styleHints().mouseDoubleClickInterval())
        if entry is not None:
            self.entry_clicked.emit(entry)
        else:
            self.nothing_clicked.emit()
        super().mousePressEvent(event)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._title_click.stop()  # a double click opens the event, its title is not edited
        self._clicked_again = None
        entry = self.entry_at(event.position())
        if entry is not None:
            self.entry_activated.emit(entry)
            return
        slot = self.slot_at(event.position())
        if slot is not None:
            self.slot_activated.emit(slot[0], slot[1])

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        position = QPointF(event.pos())
        entry = self.entry_at(position)
        if entry is not None:
            self.select(entry)
            self.entry_clicked.emit(entry)
        self.context_requested.emit(entry, self.slot_at(position) if entry is None else None, event.globalPos())


def _typed_palette(widget: QWidget) -> QPalette:
    return widget.palette()


class GridCanvas(EntryHits):
    """The hours of one or several days as columns, with the timed events as blocks."""

    hour_height_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hour_height: int = DEFAULT_HOUR_HEIGHT
        self.days: list[date] = []
        self.zone: tzinfo | None = None
        self.segments: dict[date, list[Segment]] = {}
        self.today: date = date.today()
        self.now: datetime | None = None
        self.work_hours: tuple[int, int] = (8, 18)

    def set_data(self, days: list[date], entries: list[Entry], zone: tzinfo, today: date) -> None:
        self.days = days
        self.zone = zone
        self.today = today
        self.segments = {day: layout.day_segments(entries, day, zone) for day in days}
        self.selected = followed(self.selected, entries)
        self.setFixedHeight(24 * self.hour_height)
        self.update()

    def set_hour_height(self, height: int) -> None:
        self.hour_height = max(MINIMUM_HOUR_HEIGHT, min(MAXIMUM_HOUR_HEIGHT, height))
        self.setFixedHeight(24 * self.hour_height)
        self.update()

    @override
    def sizeHint(self) -> QSize:
        return QSize(600, 24 * self.hour_height)

    def column_width(self) -> float:
        return max(1.0, (self.width() - GUTTER) / max(1, len(self.days)))

    @override
    def slot_at(self, position: QPointF) -> tuple[date, int] | None:
        if not self.days or position.x() < GUTTER:
            return None
        index = min(len(self.days) - 1, int((position.x() - GUTTER) // self.column_width()))
        minute = int(position.y() / self.hour_height * 60) // SNAP_MINUTES * SNAP_MINUTES
        return self.days[index], max(0, min(24 * 60 - SNAP_MINUTES, minute))

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl and the wheel: more or fewer hours on the screen
            self.set_hour_height(self.hour_height + (6 if event.angleDelta().y() > 0 else -6))
            self.hour_height_changed.emit(self.hour_height)
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:  # Shift and the wheel: a period back or forward
            self.step_by_wheel(event)
            return
        super().wheelEvent(event)  # the wheel alone scrolls the hours

    @override
    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(self.rect(), base)
        line = QColor(text)
        line.setAlpha(40)
        faint = QColor(text)
        faint.setAlpha(18)
        font = self.font()
        small = QFont(font)
        small.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        width = self.column_width()
        for index, day in enumerate(self.days):
            left = GUTTER + index * width
            if day == self.today:
                tint = QColor(palette.color(QPalette.ColorRole.Highlight))
                tint.setAlpha(22)
                painter.fillRect(QRectF(left, 0, width, self.height()), tint)
            elif day.weekday() >= 5:
                painter.fillRect(QRectF(left, 0, width, self.height()), faint)
        painter.setFont(small)
        for hour in range(24):
            y = hour * self.hour_height
            painter.setPen(line)
            painter.drawLine(GUTTER - 6, y, self.width(), y)
            painter.setPen(faint)
            painter.drawLine(GUTTER, y + self.hour_height // 2, self.width(), y + self.hour_height // 2)
            if hour:
                label = QColor(text)
                label.setAlpha(150)
                painter.setPen(label)
                painter.drawText(QRect(0, y - 8, GUTTER - 10, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{hour:02d}:00")
        painter.setPen(line)
        for index in range(len(self.days) + 1):
            x = int(GUTTER + index * width)
            painter.drawLine(x, 0, x, self.height())
        painter.setFont(font)
        self.hits = []
        self.mark_hits = []
        self.titles = []
        for index, day in enumerate(self.days):
            for segment in self.segments.get(day, []):
                column_width = (width - 3) / segment.columns
                left = GUTTER + index * width + 1 + segment.column * column_width
                top = segment.start / 60 * self.hour_height
                height = max(14.0, (segment.end - segment.start) / 60 * self.hour_height - 1)
                rect = QRectF(left, top, column_width - 1, height)
                entry = segment.entry
                lines = [self.title_shown(entry)]
                if height > 30:
                    lines.append(layout.time_text(entry))
                if height > 46 and entry.row.location:
                    lines.append(one_line(entry.row.location))
                mark = self.mark_for(entry)
                self.note_mark(paint_block(painter, rect, entry, same_event(entry, self.selected), font, lines,
                                           faded=not entry.row.busy, mark=mark), entry)
                fill, text_colour = block_colours(entry, not entry.row.busy)
                self.note_title(rect, entry, font, mark, False, text_colour, fill)
                self.hits.append((rect, entry))
        self.follow_title()
        if self.now is not None and self.today in self.days:
            index = self.days.index(self.today)
            y = (self.now.hour * 60 + self.now.minute) / 60 * self.hour_height
            painter.setPen(QPen(QColor(theme(palette).now_line), 2))
            painter.drawLine(int(GUTTER + index * width), int(y), int(GUTTER + (index + 1) * width), int(y))
            painter.setBrush(QColor(theme(palette).now_line))
            painter.drawEllipse(QPointF(GUTTER + index * width, y), 4, 4)
        painter.end()


class DayHeader(EntryHits):
    """The names of the days above the hours, and under them the all-day events as bars."""

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt's name
        self.step_by_wheel(event)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.days: list[date] = []
        self.bars: list[Bar] = []
        self.today: date = date.today()
        self.setMinimumHeight(DAY_ROW + BAR_HEIGHT)

    def set_data(self, days: list[date], entries: list[Entry], today: date) -> None:
        self.days = days
        self.today = today
        self.bars = layout.bars(entries, days, include_timed_spanning=True)
        rows = max((bar.row for bar in self.bars), default=-1) + 1
        self.setFixedHeight(DAY_ROW + 6 + max(1, rows) * (BAR_HEIGHT + BAR_GAP))
        self.selected = followed(self.selected, entries)
        self.update()

    def column_width(self) -> float:
        return max(1.0, (self.width() - GUTTER) / max(1, len(self.days)))

    @override
    def slot_at(self, position: QPointF) -> tuple[date, int] | None:
        if not self.days or position.x() < GUTTER:
            return None
        return self.days[min(len(self.days) - 1, int((position.x() - GUTTER) // self.column_width()))], -1

    @override
    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Base))
        text = palette.color(QPalette.ColorRole.Text)
        line = QColor(text)
        line.setAlpha(40)
        width = self.column_width()
        font = self.font()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, day in enumerate(self.days):
            left = GUTTER + index * width
            painter.setPen(line)
            painter.drawLine(int(left), 0, int(left), self.height())
            if day == self.today:
                tint = QColor(palette.color(QPalette.ColorRole.Highlight))
                tint.setAlpha(22)
                painter.fillRect(QRectF(left, 0, width, self.height()), tint)
            weekday = QFont(font)
            weekday.setPointSizeF(max(6.0, font.pointSizeF() * 0.9))
            painter.setFont(weekday)
            painter.setPen(text)
            painter.drawText(QRectF(left, 4, width, 16), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, day.strftime("%a"))
            number = QFont(font)
            number.setBold(True)
            painter.setFont(number)
            if day == self.today:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
                painter.drawEllipse(QRectF(left + width / 2 - 11, 19, 22, 20))
                painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
            else:
                painter.setPen(text)
            painter.drawText(QRectF(left, 19, width, 20), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, str(day.day))
        painter.setPen(line)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.setFont(font)
        label = QColor(text)
        label.setAlpha(150)
        painter.setPen(label)
        painter.drawText(QRect(0, DAY_ROW + 2, GUTTER - 6, BAR_HEIGHT), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "All day")
        self.hits = []
        self.mark_hits = []
        self.titles = []
        for bar in self.bars:
            left = GUTTER + bar.first * width + 1
            rect = QRectF(left, DAY_ROW + 4 + bar.row * (BAR_HEIGHT + BAR_GAP), (bar.last - bar.first + 1) * width - 3, BAR_HEIGHT)
            mark = self.mark_for(bar.entry)
            self.note_mark(paint_block(painter, rect, bar.entry, same_event(bar.entry, self.selected), font,
                                       [self.title_shown(bar.entry)], mark=mark), bar.entry)
            fill, text_colour = block_colours(bar.entry)
            self.note_title(rect, bar.entry, font, mark, False, text_colour, fill)
            self.hits.append((rect, bar.entry))
        self.follow_title()
        painter.end()


class MonthView(EntryHits):
    """Six weeks of days as a grid; each day lists its events."""

    day_activated = Signal(object)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt's name
        self.step_by_wheel(event)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.days: list[date] = []
        self.entries: list[Entry] = []
        self.month: int = date.today().month
        self.today: date = date.today()
        self.first_weekday: int = 0
        self.more: list[tuple[QRectF, date]] = []

    def set_data(self, days: list[date], entries: list[Entry], month: int, today: date, first_weekday: int) -> None:
        self.days = days
        self.entries = entries
        self.month = month
        self.today = today
        self.first_weekday = first_weekday
        self.selected = followed(self.selected, entries)
        self.update()

    def _cell_size(self) -> tuple[float, float]:
        return self.width() / 7, max(40.0, (self.height() - 22) / 6)

    @override
    def slot_at(self, position: QPointF) -> tuple[date, int] | None:
        if len(self.days) < 42 or position.y() < 22:
            return None
        width, height = self._cell_size()
        column, row = min(6, int(position.x() // width)), min(5, int((position.y() - 22) // height))
        return self.days[row * 7 + column], -1

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        for rect, day in self.more:
            if rect.contains(event.position()):
                self.day_activated.emit(day)
                return
        super().mousePressEvent(event)

    @override
    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(self.rect(), base)
        line = QColor(text)
        line.setAlpha(40)
        quiet = QColor(text)
        quiet.setAlpha(120)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self._cell_size()
        font = self.font()
        small = QFont(font)
        small.setPointSizeF(max(6.0, font.pointSizeF() * 0.9))
        painter.setFont(small)
        for column in range(7):
            if len(self.days) >= 7:
                painter.setPen(quiet)
                painter.drawText(QRectF(column * width, 2, width, 18), Qt.AlignmentFlag.AlignCenter, self.days[column].strftime("%A"))
        self.hits = []
        self.mark_hits = []
        self.titles = []
        self.more = []
        if len(self.days) < 42:
            self.follow_title()
            painter.end()
            return
        line_height = QFontMetrics(small).height() + 2
        for week in range(6):
            week_days = self.days[week * 7 : week * 7 + 7]
            top = 22 + week * height
            week_bars = layout.bars(self.entries, week_days, include_timed_spanning=True)
            used = {bar.entry.row for bar in week_bars}
            bar_rows = max((bar.row for bar in week_bars), default=-1) + 1
            for column, day in enumerate(week_days):
                left = column * width
                cell = QRectF(left, top, width, height)
                if day == self.today:
                    tint = QColor(palette.color(QPalette.ColorRole.Highlight))
                    tint.setAlpha(22)
                    painter.fillRect(cell, tint)
                elif day.month != self.month:
                    dim = QColor(text)
                    dim.setAlpha(12)
                    painter.fillRect(cell, dim)
                painter.setPen(line)
                painter.setBrush(Qt.BrushStyle.NoBrush)  # the brush of the circle drawn for today must not fill the cells that follow
                painter.drawRect(cell)
                painter.setFont(small)
                if day == self.today:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
                    painter.drawEllipse(QRectF(left + width - 24, top + 2, 20, 18))
                    painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
                else:
                    painter.setPen(text if day.month == self.month else quiet)
                painter.drawText(QRectF(left, top + 2, width - 6, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(day.day) if day.day != 1 else f"{day.strftime('%b')} 1")
            room = int((height - 22) // line_height)
            for bar in week_bars:
                if bar.row >= room:
                    continue
                rect = QRectF(bar.first * width + 2, top + 22 + bar.row * line_height, (bar.last - bar.first + 1) * width - 4, line_height - 2)
                mark = self.mark_for(bar.entry)
                self.note_mark(paint_block(painter, rect, bar.entry, same_event(bar.entry, self.selected), small,
                                           [self.title_shown(bar.entry)], mark=mark), bar.entry)
                fill, text_colour = block_colours(bar.entry)
                self.note_title(rect, bar.entry, small, mark, False, text_colour, fill)
                self.hits.append((rect, bar.entry))
            single_ids = {id(bar.entry) for bar in week_bars}
            for column, day in enumerate(week_days):
                left = column * width
                timed = [entry for entry in self.entries if entry.first_day == day and entry.last_day == day and not entry.all_day and id(entry) not in single_ids]
                timed.sort(key=lambda entry: (entry.start, entry.summary.casefold()))
                free_rows = max(0, room - bar_rows)
                for position, entry in enumerate(timed[:free_rows]):
                    y = top + 22 + (bar_rows + position) * line_height
                    prefix = f"{entry.start.strftime('%H:%M')} "
                    if entry.filled:  # in its colour as an all-day event, its dot in the text's colour (D-297)
                        rect = QRectF(left + 2, y, width - 4, line_height - 2)
                        selected = same_event(entry, self.selected)
                        mark = self.mark_for(entry)
                        self.note_mark(paint_block(painter, rect, entry, selected, small, [prefix + self.title_shown(entry)], mark=mark, dot=True), entry)
                        fill, text_colour = block_colours(entry)
                        self.note_title(rect, entry, small, mark, True, text_colour, fill, prefix)
                        self.hits.append((rect, entry))
                        continue
                    rect = QRectF(left + 3, y, width - 6, line_height - 1)
                    marker = fill_colour(entry)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(marker)
                    painter.drawEllipse(QPointF(left + 9, y + line_height / 2 - 1), 3.5, 3.5)
                    selected = same_event(entry, self.selected)
                    if selected:
                        painter.setBrush(QColor(palette.color(QPalette.ColorRole.Highlight)))
                        painter.drawRoundedRect(rect, 3, 3)
                    line_colour = palette.color(QPalette.ColorRole.HighlightedText) if selected else text
                    painter.setPen(line_colour)
                    mark_size = min(MARK_SIZE, line_height - 3)
                    mark_state = self.mark_for(entry)
                    mark_room = paint_mark(painter, int(left + 16), int(y + line_height / 2 - 1), mark_state, mark_size)  # where the text would start
                    if mark_state:
                        self.mark_hits.append((QRectF(int(left + 16), int(y + line_height / 2 - 1) - mark_size // 2, mark_size, mark_size), entry))
                    behind = QColor(palette.color(QPalette.ColorRole.Highlight)) if selected else QColor(base)
                    tags_left = paint_tags(painter, rect, y, line_height - 1, entry, behind, line_colour)  # the tag icons at the end of the line (D-329)
                    painter.setPen(line_colour)
                    text_room = min(width - 20 - mark_room, tags_left - (left + 16 + mark_room) - 2)
                    painter.drawText(QRectF(left + 16 + mark_room, y, text_room, line_height - 1), Qt.AlignmentFlag.AlignVCenter,
                                     QFontMetrics(small).elidedText(prefix + self.title_shown(entry), Qt.TextElideMode.ElideRight, int(text_room - 2)))
                    title_left = left + 16 + mark_room + QFontMetrics(small).horizontalAdvance(prefix)  # the title of this line, after its time (D-324)
                    title_height = QFontMetrics(small).height()
                    self.titles.append(TitlePlace(QRectF(title_left, y + (line_height - 1 - title_height) / 2, max(1.0, left + width - 4 - title_left), title_height), entry,
                                                  QFont(small), line_colour, palette.color(QPalette.ColorRole.Highlight) if selected else base))
                    self.hits.append((rect, entry))
                hidden = len(timed) - free_rows if len(timed) > free_rows else 0
                if hidden > 0 or bar_rows > room:
                    extra = hidden + max(0, sum(1 for bar in week_bars if bar.row >= room and bar.first <= column <= bar.last))
                    if extra:
                        rect = QRectF(left + 3, top + height - line_height, width - 6, line_height - 1)
                        painter.setPen(quiet)
                        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, f"+{extra} more")
                        self.more.append((rect, day))
            del used
        self.follow_title()
        painter.end()


class AgendaTags(QStyledItemDelegate):
    """An Agenda line as Qt draws it, with the appointment's tags as small tags in their colours at its right end (D-329)."""

    @staticmethod
    def text_room(line: QRect, entry: Entry) -> QRect:
        """The part of a line its text is drawn and cut off in: all of it but the room of the tags at its right end."""
        size = max(6, int(line.height() * 0.7))
        return QRect(line.left(), line.top(), max(0, line.width() - len(entry.tag_colours) * (size + 3) - 6), line.height())

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        entry = index.data(Qt.ItemDataRole.UserRole)
        line = QStyleOptionViewItem(option)
        if isinstance(entry, Entry) and entry.tag_colours:  # the text stops before the tags (D-329)
            whole = QStyleOptionViewItem(option)
            self.initStyleOption(whole, index)
            setattr(whole, "text", "")  # setattr: the stubs type these fields wrongly
            setattr(whole, "icon", QIcon())
            widget = _typed(option, "widget", QWidget) if isinstance(getattr(option, "widget", None), QWidget) else None  # getattr: absent when drawn without a view
            (widget.style() if widget is not None else QApplication.style()).drawControl(QStyle.ControlElement.CE_ItemViewItem, whole, painter, widget)  # the line's background and selection, all of it
            setattr(line, "rect", self.text_room(_rect(option), entry))
        super().paint(painter, line, index)
        if isinstance(entry, Entry) and entry.tag_colours:
            rect = QRectF(_rect(option))
            selected = _is_selected(option)
            colours = _typed(option, "palette", QPalette)
            behind = colours.color(QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base)
            text = colours.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_tags(painter, rect, rect.top(), rect.height(), entry, behind, text)
            painter.restore()


class AgendaView(QListWidget):
    """The events in order of time, a heading for each day that has some."""

    entry_clicked = Signal(object)
    entry_activated = Signal(object)
    context_requested = Signal(object, object, QPoint)  # the Entry or None, no place on a day, where

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(AgendaTags(self))
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.currentItemChanged.connect(self._current_changed)
        self.mark_of: Callable[[str], str] = no_mark  # an event's sync mark (D-194)
        self.pending: dict[tuple[str, str], PendingTitle] = {}  # titles typed in place on their way to the server, the page's (D-325)

    def _key(self, item: QListWidgetItem | None) -> tuple[object, ...] | None:
        """What a line stands for, the same after the list is filled again: an event's occurrence, or a day's heading."""
        if item is None:
            return None
        entry = self._entry(item)
        if entry is not None:
            return ("event", entry.row.item, entry.row.occurrence)  # the same after a change of the event (D-325)
        day = item.data(Qt.ItemDataRole.UserRole)
        return ("day", day) if isinstance(day, date) else None

    def set_data(self, groups: list[tuple[date, list[Entry]]], today: date, keep: bool = False, chosen: Entry | None = None) -> None:
        """Filled; it starts at the top, with no line selected. keep (the new day, D-322; a change, D-325): the line selected here stays
        selected, an event or a day's heading; with none, the event selected in another view (chosen); the line at the top stays where it is,
        to the pixel."""
        self.blockSignals(True)
        current = self.currentItem() if keep else None
        heading = self._key(current) if current is not None and self._entry(current) is None else None
        chosen = (self._entry(current) if current is not None else chosen) if keep else None
        top_line = self.itemAt(0, 0) if keep and self.verticalScrollBar().value() > 0 else None
        top = self._key(top_line)
        hidden = -self.visualItemRect(top_line).top() if top_line is not None else 0  # the part of the top line scrolled out of sight
        self.clear()
        for day, entries in groups:
            title = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}" + ("   — Today" if day == today else "")
            header = QListWidgetItem(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            header.setData(Qt.ItemDataRole.UserRole, day)
            self.addItem(header)
            for entry in entries:
                place = one_line(entry.row.location)
                pending = self.pending.get(event_key(entry))  # a title typed in place shows as typed, with the clock until the server answers (D-325)
                title = pending.title if pending is not None else entry.summary
                item = QListWidgetItem(f"   {layout.time_text(entry):<14}{title}" + (f"   ·  {place}" if place else "") + f"   ({entry.row.calendar})")
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setForeground(fill_colour(entry))
                self._mark_line(item, entry)
                self.addItem(item)
        if not groups:
            placeholder = QListWidgetItem("No events")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(placeholder)
        lines = [self.item(row) for row in range(self.count())]
        again = followed(chosen, [entry for line in lines if (entry := self._entry(line)) is not None])
        if again is not None:
            self.setCurrentItem(next(line for line in lines if self._entry(line) is again))
        elif heading is not None and (same_heading := next((line for line in lines if self._key(line) == heading), None)) is not None:
            self.setCurrentItem(same_heading)
        if top is not None and (first := next((line for line in lines if self._key(line) == top), None)) is not None:
            self.scrollToItem(first, QListWidget.ScrollHint.PositionAtTop)
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + hidden)
        self.blockSignals(False)

    def _mark_line(self, item: QListWidgetItem, entry: Entry) -> None:
        """A line's mark and hover text: the clock while its title waits for the server (D-325), else its sync mark (D-194)."""
        own = self.mark_of(item_of(entry))
        pending = self.pending.get(event_key(entry))
        mark = WAITING if own != CONFLICT and pending is not None and pending.waiting else own
        item.setIcon(mark_icon(mark))
        item.setToolTip(event_tip(entry) + (f"<p>{html.escape(MARK_TIPS[mark])}</p>" if MARK_TIPS.get(mark) else ""))  # D-329

    def mark_lines(self) -> None:
        """The marks changed: every line's mark again, in place."""
        for row in range(self.count()):
            item = self.item(row)
            entry = self._entry(item)
            if item is not None and entry is not None:
                self._mark_line(item, entry)

    def _entry(self, item: QListWidgetItem | None) -> Entry | None:
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, Entry) else None

    def _clicked(self, item: QListWidgetItem) -> None:
        entry = self._entry(item)
        if entry is not None:
            self.entry_clicked.emit(entry)

    def _current_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        entry = self._entry(current)
        if entry is not None:
            self.entry_clicked.emit(entry)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        entry = self._entry(item)
        if entry is not None:
            self.entry_activated.emit(entry)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        item = self.itemAt(event.pos())
        entry = self._entry(item)
        if item is not None and entry is not None:
            self.setCurrentItem(item)
        self.context_requested.emit(entry, None, event.globalPos())

    def selected_entry(self) -> Entry | None:
        return self._entry(self.currentItem())
