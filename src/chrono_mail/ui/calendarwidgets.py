from collections.abc import Callable
from datetime import date, datetime, tzinfo
from typing import override

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QFont, QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPalette, QPen, QWheelEvent
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QToolTip, QWidget

from chrono_mail.core.colours import NEW_CALENDAR
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarlayout import Bar, Cell, Entry, Segment
from chrono_mail.ui.colours import theme
from chrono_mail.ui.syncmarks import CONFLICT, MARK_SIZE, MARK_TIPS, mark_icon, paint_mark
from chrono_mail.ui.tagchips import TagColourBook

WHEEL_NOTCH: int = 120  # one notch of a mouse wheel, as Qt counts it

# --- Version ------------------------------------------------------------
__version__ = "calendarwidgets.py 4.2.438"

# --- Version history ----------------------------------------------------
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


def fill_colour(entry: Entry) -> QColor:
    return QColor(entry.colour) if entry.colour and QColor(entry.colour).isValid() else QColor(NEW_CALENDAR)


def no_mark(item: str) -> str:
    return ""


def item_of(entry: Entry) -> str:
    return entry.row.item


DOT_CENTRE: float = 7.0  # a timed event's dot in Month: its centre from the left of its line, its radius, the room before its text
DOT_RADIUS: float = 3.5
DOT_ROOM: int = 9


def paint_block(painter: QPainter, rect: QRectF, entry: Entry, selected: bool, font: QFont, lines: list[str], faded: bool = False, mark: str = "",
                dot: bool = False) -> QRectF | None:
    """An event as a rounded block in the colour of its calendar, with its text; selected ones get an outline; a dot in the text's colour first
    when asked (a timed event in Month, D-297), then its sync mark (D-194) before the first line. Returns where the mark was drawn (None
    without one), so a click or a hover there can be told apart."""
    colour = fill_colour(entry)
    if faded:
        colour = QColor(colour)
        colour.setAlpha(150)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(theme().swatch_selected), 1.6) if selected else Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawRoundedRect(rect, 4, 4)
    text_colour = TagColourBook.text_colour(colour)  # white or black, whichever reads better on the colour; the dot follows it (D-297)
    painter.setPen(text_colour)
    painter.setClipRect(rect.adjusted(3, 0, -2, 0))
    bold = QFont(font)
    bold.setBold(True)
    painter.setFont(bold)
    metrics = QFontMetrics(bold)
    y = rect.top() + (2 if rect.height() >= 2 * metrics.height() else max(0.0, (rect.height() - metrics.height()) / 2))  # a short block: the title in the middle
    size = min(MARK_SIZE, metrics.height())
    lead = DOT_ROOM if dot else 0  # the dot comes first, then the mark and the text, as in a timed event without a background
    mark_left = rect.left() + (lead + 5 if dot else 4)  # with the dot the mark and the text start where they do on a line without a background
    mark_area = QRectF(mark_left, int(y + metrics.height() / 2) - size // 2, size, size) if mark else None
    for index, line in enumerate(lines):
        painter.setFont(bold if index == 0 else font)
        if dot and index == 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(text_colour)
            painter.drawEllipse(QPointF(rect.left() + DOT_CENTRE, y + metrics.height() / 2), DOT_RADIUS, DOT_RADIUS)
            painter.setPen(text_colour)
        mark_room = paint_mark(painter, int(mark_left), int(y + metrics.height() / 2), mark, size) if index == 0 else 0
        text_left = mark_left + mark_room if mark_room else rect.left() + lead + 5  # after a mark the same small gap as after the dot (the owner)
        painter.drawText(QRectF(text_left, y, rect.right() - 3 - text_left, metrics.height()), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(font).elidedText(line, Qt.TextElideMode.ElideRight, int(rect.right() - 3 - text_left)))
        y += metrics.height()
        if y + metrics.height() > rect.bottom():
            break
    painter.restore()
    return mark_area


class EntryHits(QWidget):
    """What the views have in common: the places where events were drawn (to find what is clicked), the selected event, and the signals."""

    entry_clicked = Signal(object)  # an Entry
    conflict_clicked = Signal(str)  # the triangle of an event was clicked: its item (D-194, the owner)
    entry_activated = Signal(object)  # double click on an event
    slot_activated = Signal(object, int)  # double click on a free place: the day and the minute (-1: the all-day area)
    nothing_clicked = Signal()
    context_requested = Signal(object, object, QPoint)  # the Entry or None, the (day, minute) or None, where
    period_step = Signal(int)  # the wheel: one day, week or month back (-1) or forward (1)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wheel: int = 0  # what the wheel moved that is not a whole notch yet (a touchpad moves in small amounts)
        self.hits: list[tuple[QRectF, Entry]] = []
        self.selected: Entry | None = None
        self.mark_of: Callable[[str], str] = no_mark  # an event's sync mark: a conflict, waiting for the server, or none (D-194)
        self.mark_hits: list[tuple[QRectF, Entry]] = []  # where the marks were drawn: a click on a triangle opens its conflict, a hover explains
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # a click on an event gives the view the keyboard, so Delete acts on the event (D-217, RV-22)

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
            marked = self.mark_at(QPointF(event.pos()))
            if marked is not None:
                QToolTip.showText(event.globalPos(), MARK_TIPS.get(self.mark_of(item_of(marked)), ""), self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        marked = self.mark_at(event.position())
        if marked is not None and self.mark_of(item_of(marked)) == CONFLICT:  # the triangle: what a person would guess, its conflict opens
            self.select(marked)
            self.conflict_clicked.emit(item_of(marked))
            return
        entry = self.entry_at(event.position())
        self.select(entry)
        if entry is not None:
            self.entry_clicked.emit(entry)
        else:
            self.nothing_clicked.emit()
        super().mousePressEvent(event)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
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
        self.selected = next((entry for entry in entries if self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start), None)
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
        for index, day in enumerate(self.days):
            for segment in self.segments.get(day, []):
                column_width = (width - 3) / segment.columns
                left = GUTTER + index * width + 1 + segment.column * column_width
                top = segment.start / 60 * self.hour_height
                height = max(14.0, (segment.end - segment.start) / 60 * self.hour_height - 1)
                rect = QRectF(left, top, column_width - 1, height)
                entry = segment.entry
                lines = [entry.summary]
                if height > 30:
                    lines.append(layout.time_text(entry))
                if height > 46 and entry.row.location:
                    lines.append(entry.row.location)
                self.note_mark(paint_block(painter, rect, entry, self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start, font, lines,
                                           faded=not entry.row.busy, mark=self.mark_of(item_of(entry))), entry)
                self.hits.append((rect, entry))
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
        self.selected = next((entry for entry in entries if self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start), None)
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
        for bar in self.bars:
            left = GUTTER + bar.first * width + 1
            rect = QRectF(left, DAY_ROW + 4 + bar.row * (BAR_HEIGHT + BAR_GAP), (bar.last - bar.first + 1) * width - 3, BAR_HEIGHT)
            self.note_mark(paint_block(painter, rect, bar.entry, self.selected is not None and bar.entry.row == self.selected.row and bar.entry.start == self.selected.start, font,
                                       [bar.entry.summary], mark=self.mark_of(item_of(bar.entry))), bar.entry)
            self.hits.append((rect, bar.entry))
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
        self.selected = next((entry for entry in entries if self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start), None)
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
        self.more = []
        if len(self.days) < 42:
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
                self.note_mark(paint_block(painter, rect, bar.entry, self.selected is not None and bar.entry.row == self.selected.row and bar.entry.start == self.selected.start, small,
                                           [bar.entry.summary], mark=self.mark_of(item_of(bar.entry))), bar.entry)
                self.hits.append((rect, bar.entry))
            single_ids = {id(bar.entry) for bar in week_bars}
            for column, day in enumerate(week_days):
                left = column * width
                timed = [entry for entry in self.entries if entry.first_day == day and entry.last_day == day and not entry.all_day and id(entry) not in single_ids]
                timed.sort(key=lambda entry: (entry.start, entry.summary.casefold()))
                free_rows = max(0, room - bar_rows)
                for position, entry in enumerate(timed[:free_rows]):
                    y = top + 22 + (bar_rows + position) * line_height
                    if entry.filled:  # in its colour as an all-day event, its dot in the text's colour (D-297)
                        rect = QRectF(left + 2, y, width - 4, line_height - 2)
                        selected = self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start
                        self.note_mark(paint_block(painter, rect, entry, selected, small, [f"{entry.start.strftime('%H:%M')} {entry.summary}"],
                                                   mark=self.mark_of(item_of(entry)), dot=True), entry)
                        self.hits.append((rect, entry))
                        continue
                    rect = QRectF(left + 3, y, width - 6, line_height - 1)
                    marker = fill_colour(entry)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(marker)
                    painter.drawEllipse(QPointF(left + 9, y + line_height / 2 - 1), 3.5, 3.5)
                    selected = self.selected is not None and entry.row == self.selected.row and entry.start == self.selected.start
                    if selected:
                        painter.setBrush(QColor(palette.color(QPalette.ColorRole.Highlight)))
                        painter.drawRoundedRect(rect, 3, 3)
                    painter.setPen(palette.color(QPalette.ColorRole.HighlightedText) if selected else text)
                    mark_size = min(MARK_SIZE, line_height - 3)
                    mark_state = self.mark_of(item_of(entry))
                    mark_room = paint_mark(painter, int(left + 16), int(y + line_height / 2 - 1), mark_state, mark_size)  # where the text would start
                    if mark_state:
                        self.mark_hits.append((QRectF(int(left + 16), int(y + line_height / 2 - 1) - mark_size // 2, mark_size, mark_size), entry))
                    painter.drawText(QRectF(left + 16 + mark_room, y, width - 20 - mark_room, line_height - 1), Qt.AlignmentFlag.AlignVCenter,
                                     QFontMetrics(small).elidedText(f"{entry.start.strftime('%H:%M')} {entry.summary}", Qt.TextElideMode.ElideRight, int(width - 22 - mark_room)))
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
        painter.end()


class AgendaView(QListWidget):
    """The events in order of time, a heading for each day that has some."""

    entry_clicked = Signal(object)
    entry_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.currentItemChanged.connect(self._current_changed)
        self.mark_of: Callable[[str], str] = no_mark  # an event's sync mark (D-194)

    def set_data(self, groups: list[tuple[date, list[Entry]]], today: date) -> None:
        self.blockSignals(True)
        self.clear()
        for day, entries in groups:
            title = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}" + ("   — Today" if day == today else "")
            header = QListWidgetItem(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addItem(header)
            for entry in entries:
                place = entry.row.location
                item = QListWidgetItem(f"   {layout.time_text(entry):<14}{entry.summary}" + (f"   ·  {place}" if place else "") + f"   ({entry.row.calendar})")
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setForeground(fill_colour(entry))
                item.setIcon(mark_icon(self.mark_of(item_of(entry))))
                item.setToolTip(MARK_TIPS.get(self.mark_of(item_of(entry)), ""))
                self.addItem(item)
        if not groups:
            placeholder = QListWidgetItem("No events")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(placeholder)
        self.blockSignals(False)

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

    def selected_entry(self) -> Entry | None:
        return self._entry(self.currentItem())
