import dataclasses
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import SignalInstance, QDate, QEvent, QLocale, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFontMetrics, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.changes import Change
from chrono_mail.pim.times import local_zone
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_calendar import EVENT_DELETE, EVENT_LIST, EVENT_SHOW, EventInfo, EventList
from chrono_mail.engine.commands_calendar import DeleteParameters as EventDelete
from chrono_mail.engine.commands_calendar import ItemParameters as EventItem
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_calendar import ListParameters as EventListParameters
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.paletteeditor import colour_from
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.syncmarks import SyncMarks, mark_icon
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.calendarwidgets import GUTTER, AgendaView, DayHeader, GridCanvas, MonthView, item_of
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.toolbar import view_button
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "calendarview.py 3.5.348"

# --- Version history ----------------------------------------------------
# v3.5.348: The question "this occurrence or all" shows plain text (RV-167, without a filter on the whole program)
# v3.5.346: The computer's named time zone, as the engine uses (today's fixed offset put every event past a daylight-saving change an hour off; RV-15)
# v3.2.342: This occurrence of an all-day event opens as an all-day event (RV-21)
# v2.4.334: the last "(s)" texts counted one way (F1b)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: every engine call typed
# v1.1.245: The error line is the shared one of the sections (PimPage._failed, R-07)
# v1.1.226: The events' sync marks drawn again in place when they change (D-194)
# v1.1.219: Each appointment in its colour of its calendar's palette; a changed palette or calendar colour redraws them at once (D-191)
# v1.1.217: The wheel of the month, the row of day names and Shift+wheel over the hours go a period back or forward (D-189)
# v1.1.205: every event of the days shown, only of the calendars shown (no cut of 5,000 shared with hidden calendars); the status line says when a limit against hostile calendars was reached (R-02); event.list typed
# v0.5.169: The day columns of the small month calendar shrink with it (at 240 px its last days were cut off)
# v0.5.168: The small month calendar can be 240 px narrow (it made the sidebar at least 266 px)
# v0.5.167: The views have icons and the chosen one a blue line under it; they merge into one button showing the chosen view when the toolbar is narrow; the search field looks like Mail's (D-154)
# v0.5.159: follows the facts (D-146 step 5): only the blocks of the event that changed are replaced (its occurrences in the days shown are asked for); no reload after saving or deleting
# v0.0.64: Initial calendar section: day, work week, week, upcoming, month and agenda views, event editor, mini calendar


VIEW_ICONS: dict[str, str] = {
    layout.DAY: "view-calendar-day",
    layout.WORK_WEEK: "view-calendar-workweek",
    layout.WEEK: "view-calendar-week",
    layout.UPCOMING: "view-calendar-upcoming-days",
    layout.MONTH: "view-calendar-month",
    layout.AGENDA: "view-calendar-agenda",
}
CUT_WARNING: str = "not every event of these days is shown (a calendar has more than the program takes in)"
WORK_DAYS: tuple[int, ...] = (0, 1, 2, 3, 4)
FIRST_HOUR: int = 7  # the hour the grid is scrolled to
DAY_MARGIN: int = 6  # room around a day's number in the small month calendar



def _moments(entry: Entry) -> tuple[str, str]:
    """The start and end of one occurrence as the event window takes them: days for an all-day event (it opened as a timed 00:00 event,
    and Save made it a 24-hour one, RV-21), moments otherwise."""
    if entry.all_day:
        return entry.start.date().isoformat(), entry.end.date().isoformat()
    return entry.start.isoformat(), entry.end.isoformat()

class RangeCalendar(QCalendarWidget):
    """The small calendar of the sidebar: the days that are shown in the main area are tinted."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.range: tuple[date, date] = (date.today(), date.today())
        self.setGridVisible(False)
        self.setNavigationBarVisible(True)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames)
        self.setFirstDayOfWeek(QLocale().firstDayOfWeek())
        self.setFixedHeight(190)
        self.setMinimumWidth(240)  # its own wish (266) kept the whole sidebar wide, in every section
        self._fit_columns()

    def _fit_columns(self) -> None:
        """A day column needs only room for "00" (or a day's letter): Qt's own minimum for a column is that of a column
        heading (38 px at 9 points), and seven of those did not fit in 240 px, so the last days were cut off."""
        grid = self.findChild(QTableView)
        if grid is not None:
            grid.horizontalHeader().setMinimumSectionSize(QFontMetrics(grid.font()).horizontalAdvance("00") + DAY_MARGIN)

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):  # another size of the interface
            self._fit_columns()

    @override
    def paintCell(self, painter: QPainter, rect: QRect, date: QDate) -> None:
        super().paintCell(painter, rect, date)
        moment = datetime(date.year(), date.month(), date.day()).date()
        if self.range[0] <= moment < self.range[1]:
            tint = QColor(self.palette().highlight().color())
            tint.setAlpha(46)
            painter.fillRect(rect, tint)


class TimeGridPanel(QWidget):
    """The names of the days with the all-day events on top, and the hours below in a scroll area."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout_ = QVBoxLayout(self)
        layout_.setContentsMargins(0, 0, 0, 0)
        layout_.setSpacing(0)
        self.header = DayHeader(self)
        self.canvas = GridCanvas()
        self.area = QScrollArea(self)
        self.area.setWidget(self.canvas)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout_.addWidget(self.header)
        layout_.addWidget(self.area, 1)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.header.setContentsMargins(0, 0, self.area.width() - self.area.viewport().width(), 0)

    def scroll_to_hour(self, hour: int) -> None:
        self.area.verticalScrollBar().setValue(hour * self.canvas.hour_height)


class CalendarPage(PimPage):
    """Calendars (with ticks) and a small month on the left; the days, the weeks, the month or the agenda on the right."""

    kind = "events"
    multiple = True
    new_label = "New event"
    new_icon = "appointment-new"
    section_name = "Calendar"

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(backend, settings, parent)
        self.colours = TagColourBook()
        self.view: str = str(settings.value("calendar_view", layout.WEEK))
        if self.view not in {name for name, _ in layout.VIEWS}:
            self.view = layout.WEEK
        self.anchor: date = date.today()
        self.entries: list[Entry] = []
        self.selected: Entry | None = None
        self.first_weekday: int = QLocale().firstDayOfWeek().value - 1
        self.zone = local_zone()  # the named zone (Europe/Amsterdam): events past a daylight-saving change keep their hour (RV-15)
        self.hide_past: bool = str(settings.value("calendar_hide_past", "false")).lower() == "true"
        self._generation: int = 0
        self._tags_known: list[str] = []
        self._scrolled: bool = False
        # sidebar: the calendars, and the small month
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addWidget(self.panel, 1)
        self.mini = RangeCalendar(side)
        side_layout.addWidget(self.mini)
        self.side = side
        # the main area
        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(8, 6, 8, 4)
        self.previous_button = QToolButton(nav)
        self.previous_button.setText("‹")
        self.previous_button.setToolTip("Previous")
        self.next_button = QToolButton(nav)
        self.next_button.setText("›")
        self.next_button.setToolTip("Next")
        self.title = QLabel("", nav)
        font = self.title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.15)
        self.title.setFont(font)
        self.today_button = QPushButton("Today", nav)
        self.past_button = QPushButton("Hide past events", nav)
        self.past_button.setCheckable(True)
        self.past_button.setChecked(self.hide_past)
        nav_layout.addWidget(self.previous_button)
        nav_layout.addWidget(self.next_button)
        nav_layout.addWidget(self.title)
        nav_layout.addStretch(1)
        nav_layout.addWidget(self.past_button)
        nav_layout.addWidget(self.today_button)
        self.grid = TimeGridPanel()
        self.month = MonthView()
        self.agenda = AgendaView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.month)
        self.stack.addWidget(self.agenda)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(nav)
        content_layout.addWidget(self.stack, 1)
        self.content = content
        # the toolbar
        self.view_actions: dict[str, QAction] = {}
        group = QActionGroup(self.toolbar)
        group.setExclusive(True)
        self.views_button = view_button()  # the views merged into one, when the toolbar is narrow
        views_menu = QMenu(self.views_button)
        for name, label in layout.VIEWS:
            action = QAction(theme_icon(VIEW_ICONS[name]), label, self.toolbar)
            action.setCheckable(True)
            action.setChecked(name == self.view)
            action.triggered.connect(lambda checked=False, chosen=name: self.set_view(chosen))
            group.addAction(action)
            views_menu.addAction(action)
            self.view_actions[name] = action
        self.views_button.setMenu(views_menu)
        self._show_chosen_view()
        self.toolbar.add_views(list(self.view_actions.values()), self.views_button)
        self.act_edit = QAction("Edit", self.toolbar)
        self.act_edit.setIcon(theme_icon("document-edit"))
        self.act_edit.triggered.connect(self.edit_selected)
        self.act_delete = QAction("Delete", self.toolbar)
        self.act_delete.setIcon(theme_icon("edit-delete"))
        self.act_delete.triggered.connect(self.delete_selected)
        self.toolbar.add_center(self.act_edit)
        self.toolbar.add_center(self.act_delete)
        self.search_field = SearchField(hint="Search events", focused_hint="Search events", scope=False)
        self.search = self.search_field.line_edit
        self.toolbar.set_search(self.search_field)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.reload)
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self._tick)
        # connections
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.today_button.clicked.connect(lambda: self.goto(date.today()))
        self.past_button.toggled.connect(self._past_toggled)
        self.mini.clicked.connect(self._mini_clicked)
        self.search.textChanged.connect(self._search_changed)
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.entry_clicked.connect(self._entry_clicked)
            widget.entry_activated.connect(self._entry_activated)
            widget.slot_activated.connect(self._slot_activated)
            widget.nothing_clicked.connect(self._nothing_clicked)
            widget.context_requested.connect(self._context)
            widget.period_step.connect(self.step)
        self.panel.infos_changed.connect(self._recolour)
        self.month.day_activated.connect(self._day_activated)
        self.agenda.entry_clicked.connect(self._entry_clicked)
        self.agenda.entry_activated.connect(self._entry_activated)
        self.grid.canvas.hour_height_changed.connect(self._zoomed)
        height = str(self._settings.value("calendar_hour_height", ""))
        if height.isdigit():
            self.grid.canvas.set_hour_height(int(height))
        self._apply_view()

    def _zoomed(self, height: int) -> None:
        self._settings.setValue("calendar_hour_height", height)  # Ctrl and the wheel changed the hours: remembered

    # --- showing ------------------------------------------------------------------------------

    @override
    def on_show(self) -> None:
        self._clock.start()
        super().on_show()

    @override
    def refresh(self) -> None:
        self.panel.reload()

    @override
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    @override
    def selection_changed(self) -> None:
        """A calendar was ticked or unticked, or the calendars were loaded."""
        self.reload()

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()

    def _past_toggled(self, on: bool) -> None:
        self.hide_past = on
        self._settings.setValue("calendar_hide_past", "true" if on else "false")
        self._show_data()

    def _tick(self) -> None:
        """Every minute: the line of the present moment moves."""
        self.grid.canvas.now = datetime.now(self.zone)
        self.grid.canvas.today = date.today()
        self.grid.canvas.update()

    def set_view(self, name: str) -> None:
        self.view = name
        self._settings.setValue("calendar_view", name)
        self.view_actions[name].setChecked(True)
        self._show_chosen_view()
        self._scrolled = False
        self._apply_view()
        self.reload()

    def _show_chosen_view(self) -> None:
        """The merged button shows the view that is chosen: its icon and its name."""
        self.views_button.setText(dict(layout.VIEWS)[self.view])
        self.views_button.setIcon(theme_icon(VIEW_ICONS[self.view]))

    def _apply_view(self) -> None:
        self.stack.setCurrentIndex({layout.MONTH: 1, layout.AGENDA: 2}.get(self.view, 0))
        self.past_button.setVisible(self.view == layout.AGENDA)

    def goto(self, day: date) -> None:
        self.anchor = day
        self.reload()

    def step(self, direction: int) -> None:
        self.anchor = layout.step(self.view, self.anchor, direction)
        self.reload()

    def _mini_clicked(self, day: QDate) -> None:
        self.goto(date(day.year(), day.month(), day.day()))

    def _day_activated(self, day: date) -> None:
        self.anchor = day
        self.set_view(layout.DAY)

    def days(self) -> list[date]:
        return layout.days_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, date.today())

    def reload(self) -> None:
        visible = set(self.panel.visible_ids())
        first, last = layout.range_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, date.today())
        self._generation += 1
        generation = self._generation
        self.title.setText(layout.title_for(self.view, self.anchor, self.days()))
        self.mini.range = (first, last)
        self.mini.setSelectedDate(QDate(self.anchor.year, self.anchor.month, self.anchor.day))
        self.mini.updateCells()
        if not visible:
            self.entries = []
            self._show_data()
            return

        def arrived(result: EventList) -> None:
            if generation != self._generation:
                return
            self.entries = self._coloured(layout.entries_from(list(result.events), self.zone))
            self._show_data()
            self.status(counted(len(self.entries), "event") + (f"; {CUT_WARNING}" if result.cut else ""))
            self._backend.ask(TAG_LIST, NoParameters(), self._tags_arrived, lambda error: None, owner=self)

        parameters = EventListParameters(from_date=first.isoformat(), to_date=last.isoformat(), calendars=tuple(sorted(visible)), text=self.search.text().strip())
        self._backend.ask(EVENT_LIST, parameters, arrived, self._failed, owner=self)

    def _tags_arrived(self, result: tuple[TagInfo, ...]) -> None:
        tags = list(result)
        self.colours.update(tags)
        self._tags_known = [tag.name for tag in tags]

    def _scroll_to_morning(self) -> None:
        self.grid.scroll_to_hour(FIRST_HOUR)

    def _show_data(self) -> None:
        days = self.days()
        today = date.today()
        now = datetime.now(self.zone)
        if self.view == layout.MONTH:
            self.month.set_data(days, self.entries, self.anchor.month, today, self.first_weekday)
        elif self.view == layout.AGENDA:
            self.agenda.set_data(layout.agenda_groups(self.entries, days, self.hide_past, now), today)
        else:
            self.grid.header.set_data(days, self.entries, today)
            self.grid.canvas.now = now
            self.grid.canvas.set_data(days, self.entries, self.zone, today)
            if not self._scrolled:
                self._scrolled = True
                QTimer.singleShot(0, self.grid, self._scroll_to_morning)  # tied to the grid: not run when it is already gone
        self._selection_changed()

    # --- the selection ------------------------------------------------------------------------

    def _same(self, entry: Entry | None) -> Entry | None:
        if entry is None:
            return None
        return next((candidate for candidate in self.entries if candidate.row == entry.row and candidate.start == entry.start), None)

    def _entry_clicked(self, entry: object) -> None:
        self.selected = entry if isinstance(entry, Entry) else None
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.select(self.selected)
        self._selection_changed()

    def _nothing_clicked(self) -> None:
        self._entry_clicked(None)

    def _selection_changed(self) -> None:
        self.selected = self._same(self.selected)
        self.act_edit.setEnabled(self.selected is not None)
        self.act_delete.setEnabled(self.selected is not None)

    # --- changing -----------------------------------------------------------------------------

    def _coloured(self, entries: list[Entry]) -> list[Entry]:
        """The entries in their colours as the calendars' palettes are now (D-191)."""
        return [
            dataclasses.replace(entry, colour=colour_from(self.panel.info(entry.row.collection), entry.row.colour_id,
                                                          entry.row.other_colour))
            for entry in entries
        ]

    def _recolour(self) -> None:
        """A calendar's colour or palette changed: the appointments on the screen take their colours again, nothing is fetched."""
        self.entries = self._coloured(self.entries)
        self._show_data()

    def _calendars(self) -> list[CollectionSummary]:
        return list(self.panel.collections)

    def _current_calendar(self) -> str:
        visible = self.panel.visible_ids()
        return visible[0] if visible else ((self._calendars()[0].collection) if self._calendars() else "")

    def _editor(self, event: EventInfo | None, collection: str, occurrence: str = "", start: str = "", end: str = "") -> None:
        editor = EventEditor(self._backend, self._calendars(), self._tags_known, event, collection, occurrence, start, end, self.content)
        editor.saved.connect(self._saved)
        editor.exec()

    @override
    def new_item(self) -> None:
        if not self._calendars():
            return
        now = datetime.now(self.zone)
        start = datetime.combine(self.anchor, datetime.min.time()).replace(hour=min(23, now.hour + 1 if self.anchor == now.date() else 9))
        self._editor(None, self._current_calendar(), start=start.strftime("%Y-%m-%dT%H:%M"), end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))

    def _slot_activated(self, day: object, minute: int) -> None:
        """A double click on a free place: a new event starting there (all day when it was the all-day area or the month)."""
        if not isinstance(day, date) or not self._calendars():
            return
        if minute < 0:
            self._editor(None, self._current_calendar(), start=day.isoformat(), end=(day + timedelta(days=1)).isoformat())
            return
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
        self._editor(None, self._current_calendar(), start=start.strftime("%Y-%m-%dT%H:%M"), end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))

    def _entry_activated(self, entry: object) -> None:
        if isinstance(entry, Entry):
            self._entry_clicked(entry)
            self.edit_selected()

    def _ask_scope(self, verb: str) -> str:
        """For a repeating event: 'occurrence' (only this one), 'series' (all of them) or '' (cancel)."""
        box = QMessageBox(self.content)
        box.setTextFormat(Qt.TextFormat.PlainText)  # never markup (RV-167)
        box.setWindowTitle(verb)
        box.setText(f"This event repeats. {verb} only this occurrence, or all of them?")
        only = box.addButton("This occurrence", QMessageBox.ButtonRole.AcceptRole)
        everything = box.addButton("All occurrences", QMessageBox.ButtonRole.DestructiveRole if verb == "Delete" else QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        return "occurrence" if clicked is only else ("series" if clicked is everything else "")

    def edit_selected(self) -> None:
        entry = self.selected
        if entry is None:
            return
        row = entry.row
        scope = self._ask_scope("Change") if row.repeats else "series"
        if not scope:
            return
        occurrence = row.occurrence if scope == "occurrence" else ""

        def opened(result: EventInfo) -> None:
            self._editor(result, result.collection, occurrence, *(_moments(entry) if scope == "occurrence" else ("", "")))

        self._backend.ask(EVENT_SHOW, EventItem(row.item), opened, self._failed, owner=self)

    @override
    def delete_selected(self) -> None:
        entry = self.selected
        if entry is None:
            return
        row = entry.row
        values = EventDelete(row.item)
        if row.repeats:
            scope = self._ask_scope("Delete")
            if not scope:
                return
            if scope == "occurrence":
                values = EventDelete(values.item, row.occurrence)
        elif QMessageBox.question(self.content, "Delete", f"Delete “{entry.summary}”?\n\nThe file is kept in the store's .deleted directory for 30 days.") != QMessageBox.StandardButton.Yes:
            return
        self.selected = None
        self._backend.ask(EVENT_DELETE, values, lambda result: self._saved(""), self._failed, owner=self)

    def _saved(self, item: str) -> None:
        """An event was saved or deleted: its blocks followed the fact (D-146)."""

    @override
    def apply_item(self, change: Change) -> None:
        """An event was added, changed, deleted or moved: its blocks go, and when it is in a calendar shown its
        occurrences in the days shown are asked for and put in (only that event, D-146)."""
        kind, item = str(change.kind), change.message_id
        wanted = change.new_message_id if kind == "item_moved" else item
        target = change.to_folder if kind == "item_moved" else change.folder
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.row.item not in (item, wanted)]
        if len(self.entries) != before:
            self._show_data()
        if kind == "item_removed" or target not in set(self.panel.visible_ids()):
            return
        first, last = layout.range_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, date.today())
        generation = self._generation

        def arrived(result: EventList) -> None:
            if generation != self._generation:
                return  # another week or view is shown now: it was fetched for that one
            kept = [entry for entry in self.entries if entry.row.item != wanted]
            self.entries = sorted(kept + self._coloured(layout.entries_from(list(result.events), self.zone)), key=lambda entry: (entry.start, entry.summary.casefold()))
            self._show_data()
            if result.cut:
                self.status(f"Calendar: {CUT_WARNING}")

        parameters = EventListParameters(from_date=first.isoformat(), to_date=last.isoformat(), text=self.search.text().strip(), item=wanted)
        self._backend.ask(EVENT_LIST, parameters, arrived, self._failed, owner=self)

    def _context(self, entry: object, slot: object, position: QPoint) -> None:
        menu = QMenu(self.content)
        if isinstance(entry, Entry):
            menu.addAction("Edit", self.edit_selected)
            menu.addAction("Delete", self.delete_selected)
            menu.addSeparator()
        if isinstance(slot, tuple) and len(slot) == 2:
            day, minute = slot
            menu.addAction("New event here", lambda: self._slot_activated(day, int(minute)))
        else:
            menu.addAction("New event", self.new_item)
        menu.exec(position)

    @override
    def conflict_sources(self) -> list[SignalInstance]:
        return [self.grid.header.conflict_clicked, self.grid.canvas.conflict_clicked, self.month.conflict_clicked]

    @override
    def marks_changed(self, marks: SyncMarks) -> None:
        """The events' sync marks: drawn again in place (nothing is fetched)."""
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.mark_of = marks.state
            widget.update()
        self.agenda.mark_of = marks.state
        for row in range(self.agenda.count()):
            item = self.agenda.item(row)
            entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if item is not None and isinstance(entry, Entry):
                item.setIcon(mark_icon(marks.state(item_of(entry))))

