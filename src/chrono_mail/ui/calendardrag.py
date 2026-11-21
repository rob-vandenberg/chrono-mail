from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from enum import Enum

from PySide6.QtCore import QObject, QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from chrono_mail.ui.calendarlayout import Entry

# --- Version ------------------------------------------------------------
__version__ = "calendardrag.py 4.8.660"

# --- Version history ----------------------------------------------------
# v4.8.660: Initial (B-36, D-371): an appointment dragged to another time or day inside the view it is in, or its end dragged; where it
#           would land shown while dragging; Escape or a release outside the days changes nothing

DRAG_MINUTES: int = 15  # a dragged time lands on a quarter of an hour
EDGE_PIXELS: int = 5  # how near the end of a block the mouse must be to drag its end
SCROLL_ROOM: int = 24  # the hours scroll while the mouse is this near their top or bottom
SCROLL_STEP: int = 12
SCROLL_EVERY: int = 40  # milliseconds
GHOST_OPACITY: float = 0.7


class Grip(Enum):
    MOVE = "move"  # the whole appointment
    END = "end"  # only its end: its bottom edge in the hours, the right end of a bar


@dataclass(frozen=True, slots=True)
class Drop:
    """Where a dragged appointment would land: its new start and end on this computer's clock, all day or not (all day: midnights)."""

    entry: Entry
    start: datetime
    end: datetime
    all_day: bool

    def ghost(self) -> Entry:
        """The appointment as it would be after the drop, to draw where it would land."""
        return replace(self.entry, start=self.start, end=self.end, all_day=self.all_day)

    def changes(self) -> bool:
        return (self.start, self.end, self.all_day) != (self.entry.start, self.entry.end, self.entry.all_day)


def midnight(day: date, zone: tzinfo | None) -> datetime:
    return datetime.combine(day, time(0), tzinfo=zone)


def snapped(moment: datetime) -> datetime:
    """The quarter of an hour nearest to this moment, on its own day's clock."""
    minutes = moment.hour * 60 + moment.minute + moment.second / 60
    return moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=round(minutes / DRAG_MINUTES) * DRAG_MINUTES)


def days_long(entry: Entry) -> int:
    """How many days it touches."""
    return (entry.last_day - entry.first_day).days + 1


@dataclass(frozen=True, slots=True)
class Place:
    """What is under the mouse: a day, and in the hours the moment there (None in the all-day row, Month and n weeks)."""

    day: date
    moment: datetime | None


class CalendarDrag(QObject):
    """One drag of an appointment at a time, inside the view it started in (the owner): the hours with their all-day row above them, or
    Month and n weeks. The view where the mouse was pressed keeps the mouse until it is released (Qt), so it hands every move here; where the
    appointment would land is worked out from the place under the mouse, in whichever part of the view that is."""

    dropped = Signal(object)  # a Drop that changes something

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.allowed: Callable[[Entry], bool] = lambda entry: False  # the page says which appointments may be dragged
        self.zone: tzinfo | None = None  # the page's zone: all-day midnights are on it
        self.header: QWidget | None = None  # the all-day row of the hours
        self.canvas: QWidget | None = None  # the hours
        self.area: QScrollArea | None = None  # the scroll area of the hours
        self.month: QWidget | None = None  # Month and n weeks
        self.place_of: Callable[[QWidget, QPointF], Place | None] = lambda widget, position: None  # the page's views answer what is under a point
        self.preview: Drop | None = None
        self._entry: Entry | None = None
        self._grip: Grip = Grip.MOVE
        self._source: QWidget | None = None
        self._pressed_at: QPoint = QPoint()
        self._grabbed: Place | None = None
        self._started: bool = False
        self._last: QPoint = QPoint()
        self._scroller = QTimer(self)
        self._scroller.setInterval(SCROLL_EVERY)
        self._scroller.timeout.connect(self._scroll)

    @property
    def dragging(self) -> bool:
        return self._started

    def pressed(self, source: QWidget, entry: Entry | None, position: QPointF, grip: Grip) -> None:
        """The left button went down on an appointment (or elsewhere: None); a drag starts only once the mouse moved far enough."""
        self._reset()
        if entry is None or not self.allowed(entry):
            return
        self._entry, self._grip, self._source = entry, grip, source
        self._pressed_at = source.mapToGlobal(position.toPoint())
        self._grabbed = self.place_of(source, position)

    def moved(self, global_position: QPoint) -> bool:
        """The mouse moved with the button down: True while a drag runs (the view then does nothing else with it)."""
        if self._entry is None or self._source is None:
            return False
        self._last = global_position
        if not self._started:
            if (global_position - self._pressed_at).manhattanLength() < QApplication.startDragDistance():
                return False
            self._started = True
            self._source.setCursor(Qt.CursorShape.SizeVerCursor if self._grip is Grip.END and self._source is self.canvas
                                   else Qt.CursorShape.SizeHorCursor if self._grip is Grip.END else Qt.CursorShape.ClosedHandCursor)
            if self.canvas is not None and self.area is not None and self._source in (self.canvas, self.header):
                self._scroller.start()
        self._show(self._landing(global_position))
        return True

    def released(self) -> bool:
        """The button came up: True when it ended a drag (the drop is told when it changes something)."""
        started, drop = self._started, self.preview
        self._reset()
        if started and drop is not None and drop.changes():
            self.dropped.emit(drop)
        return started

    def cancel(self) -> None:
        """Escape, or the view went: nothing changes."""
        self._reset()

    def _reset(self) -> None:
        if self._source is not None:
            self._source.unsetCursor()
        self._scroller.stop()
        self._entry, self._source, self._grabbed = None, None, None
        self._started = False
        self._show(None)

    def _show(self, drop: Drop | None) -> None:
        if drop == self.preview:
            return
        self.preview = drop
        for widget in (self.header, self.canvas, self.month):
            if widget is not None:
                widget.update()

    def _scroll(self) -> None:
        """The hours scroll while the mouse is near their top or bottom edge, and the landing place follows."""
        area = self.area
        if area is None or not self._started:
            return
        viewport = area.viewport()
        point = viewport.mapFromGlobal(self._last)
        bar = area.verticalScrollBar()
        if not 0 <= point.x() < viewport.width():
            return
        if 0 <= point.y() < SCROLL_ROOM and bar.value() > bar.minimum():
            bar.setValue(bar.value() - SCROLL_STEP)
        elif viewport.height() - SCROLL_ROOM < point.y() < viewport.height() + SCROLL_ROOM * 4 and bar.value() < bar.maximum():
            bar.setValue(bar.value() + SCROLL_STEP)
        else:
            return
        self._show(self._landing(self._last))

    def _under(self, global_position: QPoint) -> tuple[QWidget, Place] | None:
        """The part of the view under the mouse, and the place there; None outside the days of the view."""
        if self._source is self.month:
            parts: tuple[QWidget | None, ...] = (self.month,)
        else:
            parts = (self.canvas, self.header)
        for widget in parts:
            if widget is None or not widget.isVisible():
                continue
            point = widget.mapFromGlobal(global_position)
            inside = widget.visibleRegion().contains(point) if widget is self.canvas else widget.rect().contains(point)
            if not inside:
                continue
            place = self.place_of(widget, QPointF(point))
            if place is not None:
                return widget, place
        return None

    def _landing(self, global_position: QPoint) -> Drop | None:
        entry, grabbed, under = self._entry, self._grabbed, self._under(global_position)
        if entry is None or grabbed is None or under is None:
            return None
        widget, place = under
        if self._grip is Grip.END:
            return self._end_at(entry, widget, place)
        if place.moment is not None:  # in the hours: a time
            if grabbed.moment is not None and not entry.all_day:
                start = snapped(entry.start + (place.moment - grabbed.moment))
                return Drop(entry, start, start + (entry.end - entry.start), False)
            start = snapped(place.moment)  # from the all-day row into the hours: an hour long, as a new appointment
            return Drop(entry, start, start + timedelta(hours=1), False)
        if entry.all_day or grabbed.moment is None:  # by whole days, its times kept
            shift = timedelta(days=(place.day - grabbed.day).days)
            return Drop(entry, entry.start + shift, entry.end + shift, entry.all_day)
        start = midnight(place.day, self.zone)  # from the hours into the all-day row: all day, as many days as it touched
        return Drop(entry, start, midnight(place.day + timedelta(days=days_long(entry)), self.zone), True)

    def _end_at(self, entry: Entry, widget: QWidget, place: Place) -> Drop | None:
        if entry.all_day:
            if place.moment is not None and widget is self.canvas:
                return None  # a bar's end stays in its own row
            last = max(place.day, entry.first_day)
            return Drop(entry, entry.start, midnight(last + timedelta(days=1), self.zone), True)
        if place.moment is None:
            return None  # a block's end stays in the hours
        end = max(snapped(place.moment), entry.start + timedelta(minutes=DRAG_MINUTES))
        return Drop(entry, entry.start, end, False)


def paint_ghost(painter: QPainter, paint: Callable[[], None]) -> None:
    """Where the appointment would land, drawn a little see-through over the days."""
    painter.save()
    painter.setOpacity(GHOST_OPACITY)
    paint()
    painter.restore()


def cursor_for(grip: Grip | None, in_hours: bool) -> QCursor | None:
    """The cursor over the end of an appointment that can be dragged: up and down in the hours, sideways on a bar; None elsewhere."""
    if grip is not Grip.END:
        return None
    return QCursor(Qt.CursorShape.SizeVerCursor if in_hours else Qt.CursorShape.SizeHorCursor)
