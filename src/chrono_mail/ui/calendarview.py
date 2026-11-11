import dataclasses
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import Signal, SignalInstance, QDate, QEvent, QLocale, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFontMetrics, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
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

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.changes import Change
from chrono_mail.pim.times import local_zone
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_calendarmail import EVENT_MAILING, MailingParameters, MailingResult
from chrono_mail.ui.calendarmail import EXPLANATION, CalendarMailDialog, Choice
from chrono_mail.engine.commands_calendar import EVENT_CANCEL, EVENT_DELETE, EVENT_EDIT, EVENT_LIST, EVENT_RESTORE, EVENT_SHOW, DeleteResult, EventInfo, EventList, StatusParameters
from chrono_mail.engine.commands_calendar import DeleteParameters as EventDelete
from chrono_mail.engine.commands_calendar import EditParameters as EventEdit
from chrono_mail.engine.commands_calendar import ItemParameters as EventItem
from chrono_mail.engine.commands_pim import TAG_LIST, TagInfo
from chrono_mail.engine.commands_calendar import ListParameters as EventListParameters
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.colours import theme
from chrono_mail.ui.paletteeditor import colour_from
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.syncmarks import SyncMarks
from chrono_mail.ui.calendarlayout import Entry
from chrono_mail.ui.dayheadings import DayWatch
from chrono_mail.ui.calendarwidgets import GUTTER, AgendaView, DayHeader, GridCanvas, MonthView, PendingTitle, event_key, followed
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.icons import theme_icon
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.searchfield import SearchField
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.tagfilter import TagFilter, tags_shown
from chrono_mail.ui.toolbar import view_button
from chrono_mail.core.words import counted
from chrono_mail.core.words import digits

# --- Version ------------------------------------------------------------
__version__ = "calendarview.py 4.4.575"

# --- Version history ----------------------------------------------------
# v4.4.575: The calendar sidebar knows when Month is shown, for its Fill colour tick (D-297); the Tags list follows Settings > Sidebar's Tags tick (D-327)
# v4.4.574: Only the answer to the latest request for the tags counts: an older one arriving later brought back old tag colours or numbers (D-329)
# v4.4.573: An appointment takes the colour of its first tag, and knows its tags' colours (D-329)
# v4.4.572: open_event: an event opens read-only from the Tag overview, a Cancelled one not until it is Restored (D-328)
# v4.4.571: Tags under the calendars in the sidebar: a click on a tag shows only the events with that tag in every view, All events shows every event again (D-327)
# v4.4.568: New appointments go to the calendar with the blue plus (D-326)
# v4.4.567: An event stays selected after a change and its blocks stay until its new version comes; a title typed in place shows at once with the clock until the server answers, a refusal brings the old one back (D-325)
# v4.4.566: The title of an event edited in place goes to the server with event.edit: this date only, no mail from Chrono Mail (D-324)
# v4.4.564: After midnight the calendar looks as after a restart: today is the new day in every view, and a calendar that showed today shows the new today (D-322)
# v4.4.558: The two final event menus in every view: invited and self-created, greyed by the date right-clicked; New only on an empty space, Edit off the menu; every action asks this occurrence or all occurrences after the click
# v4.4.557: Cancel and Restore on the right-click menu, with the mail question on your own meeting; a Cancelled event does not open and its Edit is greyed until Restore (D-310)
# v4.4.554: A declined event can be selected and opens with a double click; Delete from the toolbar asks as its menu (D-310)
# v4.4.553: Delete of a date in an object of only changed dates deletes that date (D-310)
# v4.4.552: A deleted date has no way back; Delete of a declined date deletes that date, never the series; Delete of a Cancelled meeting asks nothing about a mail (D-310)
# v4.4.550: A cancelled event or date is grey instead of its colour, in every view (D-316)
# v4.4.535: A double click opens an appointment read-only; Edit in the list and the menu still go straight to the editor; New starts All day (round 4)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.528: A declined invite is kept hidden on this computer, Show declined, the menu of a hidden event (D-310)
# v4.4.528: Show declined; hidden events have a menu: answer again, delete, or Restore (D-310)
# v4.4.521: Show deleted and declined dates: dates of a series that are hidden show greyed and can only be looked at (D-310)
# v4.4.518: Deleting your meeting asks whether the attendees are told; the mail opens through mail_ready (D-310)
# v4.3.502: the event editor gets the settings, for the width of Event colours (the owner)
# v4.2.438: an entry is filled when its calendar's account says so (UR-02, D-297)
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError
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
READ_ONLY_TIP: str = "This calendar is read-only"


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
    answer_wanted = Signal(str, str, str)  # an invited event: its item, its date ("": all of it), the answer
    delegate_wanted = Signal(str, str)
    mail_ready = Signal(str, str, str, str)  # a mail to the attendees to send: account, folder, message, the person's own message
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
        self.day_watch: DayWatch = DayWatch(self)  # the local day; its change shows the new today (D-322)
        self.anchor: date = self._today()
        self._day_shown: date = self.anchor  # the today the shown days were asked with; another day after midnight is asked again
        self.entries: list[Entry] = []
        self.selected: Entry | None = None
        self.first_weekday: int = QLocale().firstDayOfWeek().value - 1
        self.zone = local_zone()  # the named zone (Europe/Amsterdam): events past a daylight-saving change keep their hour (RV-15)
        self.hide_past: bool = str(settings.value("calendar_hide_past", "false")).lower() == "true"
        self.show_hidden: bool = str(settings.value("calendar_show_hidden", "false")).lower() == "true"
        self._generation: int = 0
        self._fetches: int = 0  # how many fetches of events were asked: a title on its way counts only the ones after it (D-325)
        self._pending: dict[tuple[str, str], PendingTitle] = {}  # titles typed in place on their way to the server, shared with the views (D-325)
        self._tags_known: list[TagInfo] = []
        self._tags_asked: int = 0  # requests for the tags: only the answer to the latest counts, an older one may come after it
        self._scrolled: bool = False
        # sidebar: the calendars, and the small month
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        side_layout.addWidget(self.panel, 3)
        self.tag_filter = TagFilter("All events", self.colours, side)
        self.tag_filter.setVisible(tags_shown(settings, "events"))  # Settings > Sidebar's Tags tick (D-327)
        side_layout.addWidget(self.tag_filter, 2)
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
        self.hidden_button = QPushButton("Show declined", nav)
        self.hidden_button.setCheckable(True)
        self.hidden_button.setChecked(self.show_hidden)
        nav_layout.addWidget(self.previous_button)
        nav_layout.addWidget(self.next_button)
        nav_layout.addWidget(self.title)
        nav_layout.addStretch(1)
        nav_layout.addWidget(self.hidden_button)
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
        self.today_button.clicked.connect(self._goto_today)
        self.day_watch.looked.connect(self._look)
        self.tag_filter.chosen.connect(self._tag_chosen)
        self.panel.month_shown = lambda: self.view == layout.MONTH  # the Fill colour tick shows only then (the owner, D-297)
        self.past_button.toggled.connect(self._past_toggled)
        self.hidden_button.toggled.connect(self._hidden_toggled)
        self.mini.clicked.connect(self._mini_clicked)
        self.search.textChanged.connect(self._search_changed)
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.entry_clicked.connect(self._entry_clicked)
            widget.entry_activated.connect(self._entry_activated)
            widget.slot_activated.connect(self._slot_activated)
            widget.nothing_clicked.connect(self._nothing_clicked)
            widget.context_requested.connect(self._context)
            widget.period_step.connect(self.step)
            widget.title_edit_wanted.connect(self._title_edit_wanted)
            widget.title_edited.connect(self._title_edited)
            widget.pending = self._pending
        self.agenda.pending = self._pending
        self.panel.infos_changed.connect(self._recolour)
        self.month.day_activated.connect(self._day_activated)
        self.agenda.entry_clicked.connect(self._entry_clicked)
        self.agenda.entry_activated.connect(self._entry_activated)
        self.agenda.context_requested.connect(self._context)
        self.grid.canvas.hour_height_changed.connect(self._zoomed)
        height = str(self._settings.value("calendar_hour_height", ""))
        if digits(height):
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

    def _hidden_toggled(self, on: bool) -> None:
        self.show_hidden = on
        self._settings.setValue("calendar_show_hidden", "true" if on else "false")
        self.reload()

    def _tick(self) -> None:
        """Every minute: the line of the present moment moves."""
        self.grid.canvas.now = datetime.now(self.zone)
        self.grid.canvas.update()

    def _today(self) -> date:
        return date.fromisoformat(self.day_watch.day)

    def _goto_today(self) -> None:
        self.day_watch.look()  # the day of this moment, also when the computer just woke past midnight
        self.goto(self._today())

    def _look(self, day: str) -> None:
        """A look at the local day (DayWatch). After midnight the calendar looks as after a restart (D-322): today is the new day in
        every view, and a calendar that showed today shows the new today; days the person went to stay. A load that fails stays
        silent and is tried again at the next look."""
        today = date.fromisoformat(day)
        if today == self._day_shown:
            return
        if self.anchor == self._day_shown:
            self.anchor = today
        self.reload(quiet=True)

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

    def _tag_chosen(self, tag: str) -> None:
        """A tag was chosen in the sidebar (or everything): only its events are shown (D-327)."""
        self.reload()

    def days(self) -> list[date]:
        return layout.days_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, self._today())

    def reload(self, quiet: bool = False) -> None:
        """The days of the view asked again; quiet: a failure says nothing (the look after midnight, tried again at the next look)."""
        visible = set(self.panel.visible_ids())
        today = self._today()
        first, last = layout.range_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, today)
        self._generation += 1
        generation = self._generation
        self.title.setText(layout.title_for(self.view, self.anchor, self.days()))
        self.mini.range = (first, last)
        self.mini.setSelectedDate(QDate(self.anchor.year, self.anchor.month, self.anchor.day))
        self.mini.updateCells()
        if not visible:
            self.entries = []
            self._day_shown = today
            self._show_data()
            self._ask_tags()  # the tags still show with no calendar ticked
            return

        self._fetches += 1
        number = self._fetches

        def arrived(result: EventList) -> None:
            if generation != self._generation:
                return
            self._day_shown = today
            self.entries = self._coloured(layout.entries_from(list(result.events), self.zone))
            self._fetched(None, number)
            self._show_data(keep=quiet)
            self.status(counted(len(self.entries), "event") + (f"; {CUT_WARNING}" if result.cut else ""))
            self._ask_tags()

        parameters = EventListParameters(from_date=first.isoformat(), to_date=last.isoformat(), calendars=tuple(sorted(visible)), text=self.search.text().strip(), tag=self.tag_filter.tag(), hidden=self.show_hidden)
        self._backend.ask(EVENT_LIST, parameters, arrived, (lambda error: None) if quiet else self._failed, owner=self)

    def reload_tags(self) -> None:
        """The tags asked again (their colours or names changed in Settings): the Tags list follows (D-327)."""
        self._ask_tags()

    def _ask_tags(self) -> None:
        """The tags asked again; their answers come on threads of their own, so one asked earlier can arrive later: only the latest counts
        (an older one would bring back old colours or numbers, D-329)."""
        self._tags_asked += 1
        number = self._tags_asked

        def arrived(result: tuple[TagInfo, ...]) -> None:
            if number == self._tags_asked:
                self._tags_arrived(result)

        self._backend.ask(TAG_LIST, NoParameters(), arrived, lambda error: None, owner=self)

    def _tags_arrived(self, result: tuple[TagInfo, ...]) -> None:
        tags = list(result)
        before = [(tag.name, tag.colour) for tag in self._tags_known]
        self.colours.update(tags)
        self._tags_known = tags
        if [(tag.name, tag.colour) for tag in tags] != before and self.entries:
            self._recolour()  # a tag's colour is the colour of the appointments it leads (D-329)
        self.tag_filter.follow([(tag.name, tag.events) for tag in tags if tag.events > 0])

    def _scroll_to_morning(self) -> None:
        self.grid.scroll_to_hour(FIRST_HOUR)

    def _show_data(self, keep: bool = False) -> None:
        """keep: the fill of the new day; Agenda keeps its selected event and the line at its top (D-322)."""
        days = self.days()
        today = self._today()
        now = datetime.now(self.zone)
        if self.view == layout.MONTH:
            self.month.set_data(days, self.entries, self.anchor.month, today, self.first_weekday)
        elif self.view == layout.AGENDA:
            self.agenda.set_data(layout.agenda_groups(self.entries, days, self.hide_past, now), today, keep, self.selected)
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
        return followed(entry, self.entries)  # its new version after a change (D-325)

    def _entry_clicked(self, entry: object) -> None:
        self.selected = entry if isinstance(entry, Entry) else None
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.select(self.selected)
        self._selection_changed()

    def _nothing_clicked(self) -> None:
        self._entry_clicked(None)

    def _selection_changed(self) -> None:
        self.selected = self._same(self.selected)
        self.act_edit.setEnabled(self.selected is not None and not self.selected.row.cancelled)  # a Cancelled event is Restored first (D-310)
        self.act_delete.setEnabled(self.selected is not None)

    # --- changing -----------------------------------------------------------------------------

    def _coloured(self, entries: list[Entry]) -> list[Entry]:
        """The entries in their colours: the colour of their first tag (D-329), else as the calendars' palettes are now (D-191), a cancelled one grey
        (D-316), and whether Month fills their timed ones (D-297)."""
        coloured: list[Entry] = []
        for entry in entries:
            calendar = self.panel.info(entry.row.collection)
            if entry.row.hidden or entry.row.cancelled:
                colour = theme().task_grey
            elif entry.row.tags and not (entry.row.colour_id and any(colour.colour_id == entry.row.colour_id for colour in (calendar.palette if calendar is not None else ()))):
                colour = self.colours.colour(entry.row.tags[0]).name()  # its colour tag: the first of its tags (D-329); a colour not yet made a tag still shows
            else:
                colour = colour_from(calendar, entry.row.colour_id, entry.row.other_colour)
            tags = tuple((name, self.colours.colour(name).name()) for name in entry.row.tags)
            coloured.append(dataclasses.replace(entry, colour=colour, filled=calendar is not None and calendar.timed_background, tag_colours=tags))
        return coloured

    def _recolour(self) -> None:
        """A calendar's or a tag's colour changed: the appointments on the screen take their colours again, nothing is fetched; the Agenda keeps
        its selected line and its place (D-329)."""
        self.entries = self._coloured(self.entries)
        self._show_data(keep=True)

    def _calendars(self) -> list[CollectionSummary]:
        return list(self.panel.collections)

    def _current_calendar(self) -> str:
        """The calendar new appointments go to: the one with the blue plus (D-326)."""
        return self.panel.new_items_id()

    def _takes_new(self) -> bool:
        """A calendar takes new appointments; with only read-only ones the status line says why nothing opens (D-326)."""
        if self._current_calendar():
            return True
        if self._calendars():
            self.status("Calendar: every calendar is read only, so none takes new appointments")
        return False

    def _editor(self, event: EventInfo | None, collection: str, occurrence: str = "", start: str = "", end: str = "", reading: bool = False, ask_scope: Callable[[], str] | None = None) -> None:
        editor = EventEditor(self._backend, self._calendars(), self._tags_known, event, collection, occurrence, start, end, self.content, self._settings, reading, ask_scope)
        editor.saved.connect(self._saved)
        editor.mail_ready.connect(self.mail_ready)
        editor.exec()

    @override
    def new_item(self) -> None:
        if not self._takes_new():
            return
        self._editor(None, self._current_calendar(), start=self.anchor.isoformat(), end=(self.anchor + timedelta(days=1)).isoformat())  # All day ticked on the date in view

    def _slot_activated(self, day: object, minute: int) -> None:
        """A double click on a free place: a new event starting there (all day when it was the all-day area or the month)."""
        if not isinstance(day, date) or not self._takes_new():
            return
        if minute < 0:
            self._editor(None, self._current_calendar(), start=day.isoformat(), end=(day + timedelta(days=1)).isoformat())
            return
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
        self._editor(None, self._current_calendar(), start=start.strftime("%Y-%m-%dT%H:%M"), end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))

    def _entry_activated(self, entry: object) -> None:
        if isinstance(entry, Entry):
            self._entry_clicked(entry)
            self.read_selected()

    def _ask_scope(self, verb: str) -> str:
        """For a repeating event: 'occurrence' (only this one), 'series' (all of them) or '' (cancel)."""
        box = QMessageBox(QApplication.activeModalWidget() or self.content)  # asked from the open event too
        box.setTextFormat(Qt.TextFormat.PlainText)  # never markup (RV-167)
        box.setWindowTitle(verb)
        box.setText(f"This event repeats. {verb} only this occurrence, or all of them?")
        only = box.addButton("This occurrence", QMessageBox.ButtonRole.AcceptRole)
        everything = box.addButton("All occurrences", QMessageBox.ButtonRole.DestructiveRole if verb == "Delete" else QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close if verb == "Cancel" else QMessageBox.StandardButton.Cancel)  # (a button Cancel beside the question to Cancel says two things)
        box.exec()
        clicked = box.clickedButton()
        return "occurrence" if clicked is only else ("series" if clicked is everything else "")

    def open_event(self, item: str) -> None:
        """An event opened from the Tag overview (D-328): read-only, as a double click opens it here; a repeating one as its whole series. A Cancelled
        event does not open until it is Restored (D-310)."""

        def opened(result: EventInfo) -> None:
            if result.status == "CANCELLED":
                self.status("Calendar: a cancelled appointment opens once it is restored")
                return
            self._editor(result, result.collection, reading=True)

        self._backend.ask(EVENT_SHOW, EventItem(item), opened, self._failed, owner=self)

    def read_selected(self) -> None:
        """A double click: the appointment opens read-only; its Edit button asks for a repeating event what the change is for. A Cancelled event does not open
        until it is Restored (D-310), nor while its title waits for the server (D-325)."""
        entry = self.selected
        if entry is None or entry.row.cancelled or self._title_waiting(entry):
            return
        row = entry.row

        def opened(result: EventInfo) -> None:
            if row.repeats:
                self._editor(result, result.collection, row.occurrence, *_moments(entry), reading=True, ask_scope=lambda: self._ask_scope("Change"))
            else:
                self._editor(result, result.collection, reading=True)

        self._backend.ask(EVENT_SHOW, EventItem(row.item), opened, self._failed, owner=self)

    def edit_selected(self) -> None:
        entry = self.selected
        if entry is None or entry.row.cancelled or self._title_waiting(entry):  # (a title on its way: the window would show the old one, D-325)
            return
        row = entry.row
        scope = self._ask_scope("Change") if row.repeats else "series"
        if not scope:
            return
        occurrence = row.occurrence if scope == "occurrence" else ""

        def opened(result: EventInfo) -> None:
            self._editor(result, result.collection, occurrence, *(_moments(entry) if scope == "occurrence" else ("", "")))

        self._backend.ask(EVENT_SHOW, EventItem(row.item), opened, self._failed, owner=self)

    def _title_edit_wanted(self, entry: object) -> None:
        """A click on the selected event, or F2: its title edited in place where it is drawn (D-324). Not in a read-only calendar, nor a
        Cancelled or declined event: Edit does not change those either."""
        if not isinstance(entry, Entry):
            return
        row = entry.row
        if row.read_only or row.cancelled or row.hidden:
            return
        if self._title_waiting(entry):  # its clock shows: the server has not answered the title before (D-146, D-325)
            return
        for widget in (self.grid.header, self.grid.canvas) if self.view != layout.MONTH else (self.month,):
            if widget.edit_title(entry):
                if widget is self.grid.canvas and widget.title_editor is not None:
                    self.grid.area.ensureWidgetVisible(widget.title_editor)  # F2 on an event scrolled out of sight
                return

    def _title_edited(self, entry: object, title: str) -> None:
        """The new title, on the server first (D-193): this date only for a repeating event, and Chrono Mail mails no one (D-324). It shows at
        once with the clock before it; the server's yes takes the clock away and the title stays, its no brings the old title back and the
        status line says why (D-325). Offline the engine keeps it here and says yes: the sync mark's clock then stays until it is sent (D-194)."""
        if not isinstance(entry, Entry):
            return
        row = entry.row
        key = event_key(entry)
        pending = PendingTitle(title, sent=self._fetches)
        self._pending[key] = pending
        self._repaint_views()

        def done(result: EventInfo) -> None:
            pending.waiting = False
            pending.answered = self._fetches
            if pending.fetched and self._pending.get(key) is pending:
                del self._pending[key]  # its new version is in the view already
            self._repaint_views()

        def refused(error: ChronoMailError) -> None:
            if self._pending.get(key) is pending:
                del self._pending[key]
            self._repaint_views()
            self._failed(error)

        self._backend.ask(EVENT_EDIT, EventEdit(item=row.item, occurrence=row.occurrence, summary=title), done, refused, owner=self)

    def _forget_titles(self, item: str) -> None:
        """Titles typed for this event that the server said yes to (one still waiting keeps its clock until the answer)."""
        for key, pending in list(self._pending.items()):
            if key[0] == item and not pending.waiting:
                del self._pending[key]

    def _title_waiting(self, entry: Entry) -> bool:
        pending = self._pending.get(event_key(entry))
        return pending is not None and pending.waiting

    def _repaint_views(self) -> None:
        """A title on its way changed: the views draw it again; the Agenda's lines are written when it is filled, so it is filled again."""
        for widget in (self.grid.header, self.grid.canvas, self.month):
            widget.update()
        if self.view == layout.AGENDA:
            self._show_data(keep=True)

    def _fetched(self, item: str | None, number: int) -> None:
        """The events came as they are now: a title the server said yes to is now theirs (D-325). The fetch of its own event is asked at the
        fact of its change, so one asked after the title was sent has it; a fetch of all events has it only when asked after the server's yes.
        Any other shows the event as it was, and counts for nothing."""
        for key, pending in list(self._pending.items()):
            if (key[0] == item and number > pending.sent) or (item is None and not pending.waiting and number > pending.answered):
                pending.fetched = True
                if not pending.waiting:
                    del self._pending[key]

    @override
    def delete_selected(self) -> None:
        if self.selected is not None:
            self._delete_wanted(self.selected)

    def _date_meant(self, entry: Entry, verb: str) -> str | None:
        """The date an action is for, asked after the click for a repeating event: this one, or "" for all of it. (Not repeating: a date in an object of only
        changed dates has one, a single event none.) None: the person closed the question."""
        row = entry.row
        if not row.repeats:
            return row.occurrence
        scope = self._ask_scope(verb)
        if not scope:
            return None
        return row.occurrence if scope == "occurrence" else ""

    def _delete_wanted(self, entry: Entry) -> None:
        occurrence = self._date_meant(entry, "Delete")
        if occurrence is not None:
            self._delete_asked(entry, EventDelete(entry.row.item, occurrence))

    def _delete_asked(self, entry: Entry, values: EventDelete) -> None:
        def asked(result: EventInfo) -> None:
            self._delete_chosen(entry, values, result)

        self._backend.ask(EVENT_SHOW, EventItem(values.item), asked, lambda _: self._delete_chosen(entry, values, None), owner=self)  # a damaged event can still go

    def _delete_chosen(self, entry: Entry, values: EventDelete, event: EventInfo | None) -> None:
        """Your own meeting: the attendees hear of it as the person says (D-310); anything else goes on to the question."""
        if event is not None and event.yours:
            self._backend.ask(EVENT_MAILING, MailingParameters(item=values.item, deleting=True, occurrence=values.occurrence), lambda mailing: self._delete_meeting(entry, values, mailing), self._failed, owner=self)
            return
        self._delete_confirmed(entry, values)

    def _delete_confirmed(self, entry: Entry, values: EventDelete) -> None:
        """Asks only when no choice of this occurrence or all occurrences came first."""
        if not entry.row.repeats:
            text = f"Delete this date of “{entry.summary}”?" if values.occurrence else f"Delete “{entry.summary}”?\n\nThe file is kept in the store's .deleted directory for 30 days."
            if QMessageBox.question(self.content, "Delete", text) != QMessageBox.StandardButton.Yes:
                return
        self._delete(values, "")

    def _delete_meeting(self, entry: Entry, values: EventDelete, mailing: MailingResult) -> None:
        """Chrono Mail would mail the cancellation: the person says so first; closing the question keeps the meeting. The server mails: it is told to.
        A Cancelled meeting never asks about a mail (D-310)."""
        if mailing.cancelled:
            self._delete_confirmed(entry, values)
            return
        note = ""
        tell = True
        if mailing.chrono:
            dialog = CalendarMailDialog("Delete", f"Send the attendees a mail that “{entry.summary}” is cancelled?", EXPLANATION, self.content)
            choice = dialog.ask()
            if choice is Choice.BACK:
                return
            tell, note = choice is Choice.SEND, dialog.note
        self._delete(dataclasses.replace(values, tell=tell), note)

    def _delete(self, values: EventDelete, note: str) -> None:
        self.selected = None

        def deleted(result: DeleteResult) -> None:
            self._saved("")
            if result.draft.message:
                self.mail_ready.emit(result.draft.account, result.draft.folder, result.draft.message, note)

        self._backend.ask(EVENT_DELETE, values, deleted, self._failed, owner=self)

    def _status_wanted(self, entry: Entry, verb: str) -> None:
        """Cancel or Restore: a repeating event asks which."""
        row = entry.row
        occurrence = self._date_meant(entry, verb)
        if occurrence is None:
            return
        values = StatusParameters(row.item, occurrence)

        def asked(result: EventInfo) -> None:
            if result.yours:
                self._backend.ask(EVENT_MAILING, MailingParameters(item=row.item, status=True), lambda mailing: self._status_meeting(entry, verb, values, mailing), self._failed, owner=self)
            else:
                self._status(verb, values, "")

        self._backend.ask(EVENT_SHOW, EventItem(row.item), asked, self._failed, owner=self)

    def _status_meeting(self, entry: Entry, verb: str, values: StatusParameters, mailing: MailingResult) -> None:
        """Chrono Mail would mail the attendees: the person says so first; closing the question keeps the meeting as it was. The server mails: it is told to."""
        note = ""
        tell = True
        if mailing.chrono:
            question = f"Send the attendees a mail that “{entry.summary}” is cancelled?" if verb == "Cancel" else f"Send the attendees a mail that “{entry.summary}” is on again, so they answer again?"
            dialog = CalendarMailDialog(verb, question, EXPLANATION, self.content)
            choice = dialog.ask()
            if choice is Choice.BACK:
                return
            tell, note = choice is Choice.SEND, dialog.note
        self._status(verb, dataclasses.replace(values, tell=tell), note)

    def _status(self, verb: str, values: StatusParameters, note: str) -> None:
        def done(result: EventInfo) -> None:
            self._saved("")
            if result.draft.message:
                self.mail_ready.emit(result.draft.account, result.draft.folder, result.draft.message, note)

        self._backend.ask(EVENT_CANCEL if verb == "Cancel" else EVENT_RESTORE, values, done, self._failed, owner=self)

    def _saved(self, item: str) -> None:
        """An event was saved or deleted: its blocks followed the fact (D-146)."""

    @override
    def apply_item(self, change: Change) -> None:
        """An event was added, changed, deleted or moved: when it is in a calendar shown its occurrences in the days shown are asked for and
        replace its blocks (only that event, D-146). Until they come its blocks stay as they were, so nothing blinks, the selection stays and a
        title edited in place stays open (D-324, D-325); a deleted event, or one gone to another or an unticked calendar, goes at once."""
        kind, item = str(change.kind), change.message_id
        wanted = change.new_message_id if kind == "item_moved" else item
        target = change.to_folder if kind == "item_moved" else change.folder
        self._ask_tags()  # the numbers of the tags (D-327)
        stays = kind not in ("item_removed", "item_moved") and target in set(self.panel.visible_ids())
        if kind in ("item_removed", "item_moved"):
            self._forget_titles(item)  # gone: a title typed for it is nobody's (D-325)
        moved = self.selected if kind == "item_moved" and self.selected is not None and self.selected.row.item == item else None  # selected again there
        if not stays:
            before = len(self.entries)
            self.entries = [entry for entry in self.entries if entry.row.item not in (item, wanted)]
            if len(self.entries) != before:
                self._show_data(keep=True)
        if kind == "item_removed" or target not in set(self.panel.visible_ids()):
            return
        first, last = layout.range_of(self.view, self.anchor, self.first_weekday, WORK_DAYS, self._today())
        generation = self._generation
        self._fetches += 1
        number = self._fetches

        def arrived(result: EventList) -> None:
            self._fetched(wanted, number)  # asked after the change was stored: its new version, whatever is shown now (D-325)
            if generation != self._generation:
                self._repaint_views()
                return  # another week or view is shown now: it was fetched for that one
            kept = [entry for entry in self.entries if entry.row.item != wanted]
            arriving = self._coloured(layout.entries_from(list(result.events), self.zone))
            self.entries = sorted(kept + arriving, key=lambda entry: (entry.start, entry.summary.casefold()))
            if moved is not None and self.selected is None:  # moved to another calendar shown: it is selected again, its own date (D-325)
                self.selected = next((entry for entry in arriving if entry.row.occurrence == moved.row.occurrence and entry.first_day == moved.first_day), None)
            self._show_data(keep=True)  # the Agenda keeps its selected event and its place too
            if moved is not None and self.selected is not None:
                for widget in (self.grid.header, self.grid.canvas, self.month):
                    widget.select(self.selected)
            if result.cut:
                self.status(f"Calendar: {CUT_WARNING}")

        def not_fetched(error: ChronoMailError) -> None:
            self._forget_titles(wanted)
            if generation == self._generation:  # its blocks as they were must not stay: they go, as a reload would show (D-325)
                self.entries = [entry for entry in self.entries if entry.row.item != wanted]
                self._show_data(keep=True)
            self._failed(error)

        parameters = EventListParameters(from_date=first.isoformat(), to_date=last.isoformat(), text=self.search.text().strip(), tag=self.tag_filter.tag(), item=wanted, hidden=self.show_hidden)
        self._backend.ask(EVENT_LIST, parameters, arrived, not_fetched, owner=self)

    def _answer_wanted(self, entry: Entry, verb: str, answer: str) -> None:
        occurrence = self._date_meant(entry, verb)
        if occurrence is not None:
            self.answer_wanted.emit(entry.row.item, occurrence, answer)

    def _delegate_wanted(self, entry: Entry) -> None:
        occurrence = self._date_meant(entry, "Delegate")
        if occurrence is not None:
            self.delegate_wanted.emit(entry.row.item, occurrence)

    def _event_actions(self, menu: QMenu, entry: Entry) -> None:
        """The two menus of an event, invited and self-created, greyed by the date right-clicked (D-318). The commands refuse the same (OR-4)."""
        row = entry.row
        writable = not row.read_only
        if row.invited:
            open_to_answers = writable and not row.cancelled
            menu.addAction("Accept", lambda: self._answer_wanted(entry, "Accept", "ACCEPTED")).setEnabled(open_to_answers and row.answer != "ACCEPTED")
            menu.addAction("Tentative", lambda: self._answer_wanted(entry, "Tentative", "TENTATIVE")).setEnabled(open_to_answers and row.answer != "TENTATIVE")
            menu.addAction("Delegate…", lambda: self._delegate_wanted(entry)).setEnabled(open_to_answers and row.answer != "DELEGATED")
            menu.addAction("Decline", lambda: self._answer_wanted(entry, "Decline", "DECLINED")).setEnabled(open_to_answers and row.answer != "DECLINED")
            menu.addSeparator()
        menu.addAction("Cancel", lambda: self._status_wanted(entry, "Cancel")).setEnabled(writable and not row.cancelled and not row.hidden)
        menu.addAction("Restore", lambda: self._status_wanted(entry, "Restore")).setEnabled(writable and row.cancelled)
        menu.addSeparator()
        menu.addAction("Delete", lambda: self._delete_wanted(entry)).setEnabled(writable)
        if row.read_only:
            menu.setToolTipsVisible(True)
            for action in menu.actions():
                action.setToolTip(READ_ONLY_TIP)

    def _context(self, entry: object, slot: object, position: QPoint) -> None:
        menu = QMenu(self.content)
        if isinstance(entry, Entry):
            self._event_actions(menu, entry)
        elif isinstance(slot, tuple) and len(slot) == 2:
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
        self.agenda.mark_lines()  # with the clock of a title on its way (D-325)

