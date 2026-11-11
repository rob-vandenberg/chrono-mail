import html
from collections.abc import Callable
from datetime import date, datetime

from dataclasses import replace

from PySide6.QtCore import QDate, QDateTime, QSettings, Qt, QTime, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit, QListWidget, QMenu, QPlainTextEdit, QPushButton, QTimeEdit, QVBoxLayout, QWidget

from chrono_mail.pim.repeat import choice_of, describe, fitted, repeat_choices, same_rule
from chrono_mail.pim.times import in_local_zone, local_zone
from chrono_mail.engine.commands_contacts import CONTACT_SUGGEST, Suggestion, SuggestParameters
from chrono_mail.engine.commands_pim import CollectionSummary, TagInfo
from chrono_mail.core import ChronoMailError
from chrono_mail.ui.calendarmail import EXPLANATION, CalendarMailDialog, Choice
from chrono_mail.engine.commands_calendarmail import EVENT_MAILING, MailingParameters, MailingResult
from chrono_mail.engine.commands_calendar import EVENT_ADD, EVENT_EDIT, EventInfo
from chrono_mail.engine.commands_calendar import AddParameters as EventAdd
from chrono_mail.engine.commands_calendar import EditParameters as EventEdit
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.collectionpanel import grey_read_only
from chrono_mail.ui.colours import red
from chrono_mail.ui.eventattendees import AttendeesDialog, Person
from chrono_mail.ui.eventreminders import ReminderList
from chrono_mail.ui.eventrepeat import RepeatDialog
from chrono_mail.ui.forms import part
from chrono_mail.ui.paletteeditor import circle_icon
from chrono_mail.ui.tagbox import TagBox
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "eventeditor.py 4.4.574"

# --- Version history ----------------------------------------------------
# v4.4.574: Add tag… lists the tags not on the appointment and closes at a click; Remove tag beside it removes the selected tag; opens 780 pixels wide on the owner's screen (D-329)
# v4.4.573: No Colour field: the Tags box beside Show as and the attendees, each tag in its colour, a radio button chooses the tag that colours the appointment; Description starts lower (D-329)
# v4.4.569: The Calendar field of a new appointment greys a read-only calendar, with why (D-326)
# v4.4.536: Reminders: up to 5, each Notification or Email with a number and a unit (round 4)
# v4.4.535: A saved appointment opens read-only with an Edit button; attendees one per line, changed in a dialog of their own; tags picked from a list; the location has several lines; the repeat list follows the date, with Custom; date and times are separate fields (round 4)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.518: Asks whether the attendees hear of a change, new attendees are invited (D-310)
# v4.3.502: Event colours gets the settings, so it opens at the width last given it (the owner)
# v4.0.378: built from its Qt Designer form (ui/forms/event_editor.ui, D-276); its sizes follow the size factor
# v3.6.351: numbers are read only from the digits 0 to 9 (core.words.digits): "²" passed isdigit() and stopped the work with a ValueError; a first reminder at a set time shows as "At a set time" (it showed "No reminder", and choosing that
#           kept it)
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v3.6.349: an event's times through the zone the engine reads them back in (also never the platform's conversion, RV-201)
# v3.2.342: the reminders are sent only when the reminder choice was changed: a second alarm or one at a fixed time stays (RV-18)
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: parameters gives the typed edit (EditParameters); add and edit are typed calls; the event is an EventInfo
# v1.1.219: Colour: one of the calendar's colours (its own by default), with Edit colours… (D-191)
# v0.0.64: Initial editor for an event

OPENING_WIDTH: int = 780  # the owner's pixels on his screen (forms.owner_width)
NARROW: int = 300  # Repeat, Show as and the attendees: the Tags box is on their right (D-329)
DESCRIPTION_LINES: int = 3
CUSTOM: str = "custom"  # the last line of the repeat list: it opens the Custom window
DATE_FORMAT: str = "yyyy-MM-dd"
TIME_FORMAT: str = "HH:mm"


def to_qdatetime(text: str) -> QDateTime:
    """From the ISO text of the commands (a date, or a time with an offset) to a time on the clock of this computer."""
    if len(text) == 10:
        return QDateTime(QDate.fromString(text, DATE_FORMAT), QTime(0, 0))
    moment = datetime.fromisoformat(text)
    local = in_local_zone(moment)  # shown in the zone the engine reads the times back in
    return QDateTime(QDate(local.year, local.month, local.day), QTime(local.hour, local.minute))


class EventEditor(QDialog):
    """Makes an event or changes one (the series, or one occurrence of it). The commands do the saving."""

    saved = Signal(str)
    mail_ready = Signal(str, str, str, str)  # a mail to the attendees to send: account, folder, message, the person's own message

    def __init__(
        self, backend: Backend, calendars: list[CollectionSummary], known_tags: list[TagInfo], event: EventInfo | None = None, collection: str = "", occurrence: str = "",
        start: str = "", end: str = "", parent: QWidget | None = None, settings: QSettings | None = None, reading: bool = False, ask_scope: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings | None = settings
        self._original: EventInfo | None = event
        self._item: str | None = None if event is None else event.item
        self._occurrence: str = occurrence
        self._reading: bool = reading  # a saved appointment read: nothing can be changed until Edit
        self._ask_scope: Callable[[], str] | None = ask_scope  # a repeating event read: Edit asks whether this occurrence or all of them
        self.setWindowTitle("New event" if event is None else (f"Occurrence: {event.summary}" if occurrence else f"Event: {event.summary}"))
        self.tag_box = TagBox()
        form = forms.fill(self, "event_editor", {"tagBox": self._place_tag_box})  # D-276
        self.summary: QLineEdit = part(form, QLineEdit, "summary")
        self.calendar: QComboBox = part(form, QComboBox, "calendar")
        for info in calendars:
            self.calendar.addItem(f"{info.account_name} — {info.name}", info.collection)
        self.calendar.setCurrentIndex(max(0, self.calendar.findData(collection)))
        self.calendar.setEnabled(event is None and self.calendar.count() > 1)
        if event is None:  # a new appointment: a read-only calendar takes none (D-326)
            grey_read_only(self.calendar, list(calendars), "calendar", "appointments")
        self._calendars: list[CollectionSummary] = list(calendars)
        self.location: QPlainTextEdit = part(form, QPlainTextEdit, "location")  # Enter gives a new line here, it never accepts the window
        self.location.setTabChangesFocus(True)
        self.location.setFixedHeight(wpx(54))
        self.all_day: QCheckBox = part(form, QCheckBox, "allDay")
        self.several_days: QCheckBox = part(form, QCheckBox, "severalDays")
        self.start_date: QDateEdit = part(form, QDateEdit, "startDate")
        self.end_date: QDateEdit = part(form, QDateEdit, "endDate")  # shown only for an event of several days
        self.start_time: QTimeEdit = part(form, QTimeEdit, "startTime")
        self.end_time: QTimeEdit = part(form, QTimeEdit, "endTime")
        self.to_label: QLabel = part(form, QLabel, "toLabel")
        for day_field in (self.start_date, self.end_date):
            day_field.setCalendarPopup(True)
            day_field.setDisplayFormat(DATE_FORMAT)
        for time_field in (self.start_time, self.end_time):
            time_field.setDisplayFormat(TIME_FORMAT)
        now = QDateTime.currentDateTime()
        self.set_start(QDateTime(now.date(), QTime(now.time().hour() + 1 if now.time().hour() < 23 else 23, 0)))
        self.set_end(self.start_moment().addSecs(3600))
        self._start_seen: QDateTime = self.start_moment()  # a move of the start moves the end by as much
        self.repeat: QComboBox = part(form, QComboBox, "repeat")
        self.repeat.setMaximumWidth(wpx(NARROW))  # room for the Tags box on the right (D-329)
        self._rule_before: str = ""  # the rule as the event has it: said in other words it stays as it is
        self._all_day_before: bool = False
        self._custom_rule: str = ""  # a rule that is none of the choices has a line of its own
        self._repeat_day: date = self._day()
        self._repeat_index: int = 0
        self._show_repeats(0)
        self.repeat.activated.connect(self._repeat_chosen)
        self.reminders = ReminderList(part(form, QVBoxLayout, "reminderRows"), part(form, QPushButton, "addReminder"))
        self.busy: QComboBox = part(form, QComboBox, "busy")
        self.busy.setMaximumWidth(wpx(NARROW))
        self.busy.addItem("Busy", True)
        self.busy.addItem("Free", False)
        self.attendees: QListWidget = part(form, QListWidget, "attendees")  # one per line, the name when it is known
        self.attendees.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.attendees.setFixedHeight(wpx(72))
        self.attendees.setMaximumWidth(wpx(NARROW))
        self.attendees_button: QPushButton = part(form, QPushButton, "attendeesButton")
        self.attendees_button.clicked.connect(self._change_attendees)
        self.people: list[Person] = []
        self._names_asked: set[str] = set()
        self.add_tag: QPushButton = part(form, QPushButton, "addTag")  # its list: the tags not on the appointment; a click adds one and closes it
        self.tag_menu = QMenu(self.add_tag)
        self.add_tag.setMenu(self.tag_menu)
        self.remove_tag: QPushButton = part(form, QPushButton, "removeTag")  # removes the tag selected in the box; greyed until one is
        self.remove_tag.clicked.connect(self.tag_box.remove_selected)
        self.tag_box.selection_changed.connect(self._show_remove_tag)
        self._known_tags: list[TagInfo] = [tag for tag in known_tags if "events" in tag.kinds]
        tag_colours = TagColourBook()
        tag_colours.update(known_tags)
        self.tag_box.set_colours(tag_colours)
        self.description: QPlainTextEdit = part(form, QPlainTextEdit, "description")
        self.description.setMinimumHeight(wpx(DESCRIPTION_LINES * 18))  # three lines; a taller window gives it the rest (D-329)
        self.result_label: QLabel = part(form, QLabel, "result")
        buttons = part(form, QDialogButtonBox, "buttons")
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.edit_button: QPushButton = buttons.addButton("Edit", QDialogButtonBox.ButtonRole.ActionRole)
        self.edit_button.clicked.connect(self.start_editing)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        forms.open_at(self, 540, forms.owner_width(OPENING_WIDTH))  # its texts are never cut off; it opens 780 pixels wide on the owner's screen
        self.all_day.toggled.connect(self._show_when)
        self.several_days.toggled.connect(self._several_days_changed)
        self.start_date.dateChanged.connect(self._start_moved)
        self.start_time.timeChanged.connect(self._start_moved)
        self._told_before: tuple[object, ...] = ()
        if event is not None:
            self._fill(event, start, end)
            self._told_before = self._told_fields(self.parameters())
        elif start:
            all_day = len(start) == 10
            self.all_day.setChecked(all_day)
            self.set_start(to_qdatetime(start))
            stop = to_qdatetime(end) if end else self.start_moment().addSecs(3600)
            self.set_end(stop.addDays(-1) if all_day and end else stop)  # all day: the editor shows the last day, the end is the day after it
            self._times_for_all_day()
        self.several_days.setChecked(self.end_date.date() != self.start_date.date())
        self._show_when()
        self.tag_menu.aboutToShow.connect(self._fill_tag_menu)
        self._show_tags()
        self._show_reading()

    # --- filling ------------------------------------------------------------------------------

    def _fill(self, event: EventInfo, start: str, end: str) -> None:
        self.summary.setText(event.summary)
        self.location.setPlainText(event.location)
        self.description.setPlainText(event.description)
        first, last = start or event.start, end or event.end
        all_day = event.all_day if not start else len(start) == 10
        self.all_day.setChecked(all_day)
        self.set_start(to_qdatetime(first))
        stop = to_qdatetime(last)
        self.set_end(stop.addDays(-1) if all_day else stop)  # an all-day event ends the day after its last day: the editor shows the last day
        self._times_for_all_day()
        self.several_days.setChecked(self.end_date.date() != self.start_date.date())
        self._rule_before = event.rrule
        self._all_day_before = all_day
        self._show_rule(event.rrule)
        self.reminders.show_reminders(tuple(event.reminders))
        self.busy.setCurrentIndex(0 if event.busy else 1)
        self.set_people([Person(person.address, person.name, person.address) for person in event.attendees])
        self.tag_box.set_tags(list(event.tags))  # its colour tag first

    # --- reading, and Edit ------------------------------------------------------------------------

    def _show_reading(self) -> None:
        """Read-only until Edit: the texts can still be selected, the rest is greyed; the buttons are Edit and Close, then Save and Cancel."""
        reading = self._reading
        for text in (self.location, self.description):
            text.setReadOnly(reading)
        self.summary.setReadOnly(reading)
        for field in (self.all_day, self.several_days, self.start_date, self.start_time, self.end_date, self.end_time, self.busy, self.attendees_button, self.add_tag):
            field.setEnabled(not reading)
        self.tag_box.set_enabled(not reading)
        self._show_remove_tag()
        self.reminders.set_enabled(not reading)
        self.repeat.setEnabled(not reading and not self._occurrence)  # one occurrence has no rule of its own
        calendar = self._current_calendar()
        self.edit_button.setVisible(reading)
        self.edit_button.setEnabled(calendar is None or not calendar.read_only)
        self.edit_button.setToolTip("This calendar can only be read." if not self.edit_button.isEnabled() else "")
        self.save_button.setVisible(not reading)
        self.cancel_button.setText("Close" if reading else "Cancel")
        if reading:
            self.edit_button.setDefault(True)
        else:
            self.save_button.setDefault(True)

    def start_editing(self) -> None:
        """Edit: this same window becomes the editor. A repeating event first asks what the change is for."""
        event = self._original
        if self._ask_scope is not None and event is not None:
            scope = self._ask_scope()
            if not scope:
                return
            if scope == "series":
                self._occurrence = ""
                self._fill(event, "", "")
                self.setWindowTitle(f"Event: {event.summary}")
                self._show_tags()
                self._show_when()
            self._ask_scope = None
        self._reading = False
        self._show_reading()
        self._told_before = self._told_fields(self.parameters())
        self.summary.setFocus()

    # --- attendees --------------------------------------------------------------------------------

    def set_people(self, people: list[Person]) -> None:
        """The list one per line; for an attendee without a name the contacts are asked, the event itself stays as it is."""
        self.people = list(people)
        self._show_people()
        for person in self.people:
            if not person.name and person.address.lower() not in self._names_asked:
                self._names_asked.add(person.address.lower())
                self._backend.ask(CONTACT_SUGGEST, SuggestParameters(person.address), lambda found, address=person.address: self._name_arrived(address, found), lambda _error: None, owner=self)

    def _show_people(self) -> None:
        self.attendees.clear()
        self.attendees.addItems([person.shown for person in self.people])

    def _name_arrived(self, address: str, found: tuple[Suggestion, ...]) -> None:
        name = next((item.name for item in found if item.name and item.address.lower() == address.lower()), "")
        if name:
            self.people = [replace(person, name=name) if person.address.lower() == address.lower() and not person.name else person for person in self.people]
            self._show_people()

    def _change_attendees(self) -> None:
        dialog = AttendeesDialog(self._backend, self.people, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_people(dialog.people)

    # --- tags (D-329) -------------------------------------------------------------------------------

    def _place_tag_box(self, parent: QWidget) -> QWidget:
        self.tag_box.setParent(parent)
        return self.tag_box

    @property
    def chosen_tags(self) -> list[str]:
        """The tags in the order they were added (the colour tag is the one with its radio button checked)."""
        return self.tag_box.names()

    def _show_tags(self) -> None:
        """The box shows the tags of the event as they are now."""
        self.tag_box.set_tags(self.tag_box.tags())

    def _fill_tag_menu(self) -> None:
        """The tags the appointment does not have yet, each in its colour, then New tag…; a click adds one and the list closes."""
        self.tag_menu.clear()
        colours = {tag.name: tag.colour for tag in self._known_tags}
        chosen = {name.casefold() for name in self.chosen_tags}
        for name in (name for name in colours if name.casefold() not in chosen):
            self.tag_menu.addAction(circle_icon(colours[name]), name, lambda tag=name: self.choose_tag(tag, True))
        if not self.tag_menu.isEmpty():
            self.tag_menu.addSeparator()
        self.tag_menu.addAction("New tag…", self._new_tag)

    def _show_remove_tag(self) -> None:
        """Remove tag works on the selected tag: greyed, with why, until one is selected (OR-15)."""
        usable = bool(self.tag_box.selected()) and not self._reading
        self.remove_tag.setEnabled(usable)
        self.remove_tag.setToolTip("" if usable else "Click a tag in the list first" if not self._reading else "")

    def choose_tag(self, name: str, on: bool) -> None:
        if on:
            self.tag_box.add(name)
        else:
            self.tag_box.remove(name)

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New tag", "Name of the new tag:")
        if ok and " ".join(name.split()):
            self.choose_tag(" ".join(name.split()), True)

    def _current_calendar(self) -> CollectionSummary | None:
        wanted = str(self.calendar.currentData())
        return next((info for info in self._calendars if info.collection == wanted), None)

    # --- date and time ---------------------------------------------------------------------------

    def start_moment(self) -> QDateTime:
        return QDateTime(self.start_date.date(), self.start_time.time())

    def end_moment(self) -> QDateTime:
        return QDateTime(self.end_date.date(), self.end_time.time())

    def set_start(self, moment: QDateTime) -> None:
        self.start_date.setDate(moment.date())
        self.start_time.setTime(moment.time())

    def set_end(self, moment: QDateTime) -> None:
        self.end_date.setDate(moment.date())
        self.end_time.setTime(moment.time())

    def _day(self) -> date:
        day = self.start_date.date()
        return date(day.year(), day.month(), day.day())

    def _times_for_all_day(self) -> None:
        """An all-day event has no times: taking away the tick gives an hour on that day, not midnight to midnight."""
        if self.all_day.isChecked():
            now = QDateTime.currentDateTime()
            hour = min(now.time().hour() + 1, 22) if self.start_date.date() == now.date() else 9
            self.start_time.setTime(QTime(hour, 0))
            self.end_time.setTime(QTime(hour + 1, 0))

    def _show_when(self) -> None:
        """One date, with a start and an end time; all day: no times; several days: a second date."""
        timed = not self.all_day.isChecked()
        several = self.several_days.isChecked()
        self.start_time.setVisible(timed)
        self.end_time.setVisible(timed)
        self.end_date.setVisible(several)
        self.to_label.setVisible(timed or several)

    def _several_days_changed(self, on: bool) -> None:
        if on and self.end_date.date() <= self.start_date.date():
            self.end_date.setDate(self.start_date.date().addDays(1))
        elif not on:
            self.end_date.setDate(self.start_date.date())
            if self.end_time.time() < self.start_time.time():
                self.end_time.setTime(self.start_time.time())
        self._show_when()

    def _start_moved(self) -> None:
        """When the start moves the end moves by the same amount; past midnight the event has become one of several days, and back."""
        moment = self.start_moment()
        self.set_end(self.end_moment().addSecs(self._start_seen.secsTo(moment)))
        self._start_seen = moment
        self.several_days.setChecked(self.end_date.date() != self.start_date.date())
        if self._day() != self._repeat_day:
            self._repeat_day = self._day()
            self._show_repeats(self.repeat.currentIndex())

    # --- repeat ------------------------------------------------------------------------------------

    def _show_repeats(self, index: int) -> None:
        """The list as it follows the date: a line that was chosen stays chosen, and says the new day."""
        self.repeat.clear()
        for choice in repeat_choices(self._day()):
            self.repeat.addItem(choice.label, choice.rule)
        if self._custom_rule:
            self.repeat.addItem(describe(self._custom_rule, self._day(), local_zone()), self._custom_rule)
        self.repeat.addItem("Custom…", CUSTOM)
        self.repeat.setCurrentIndex(index)
        self._repeat_index = index

    def _show_rule(self, rule: str) -> None:
        index = choice_of(rule, self._day())
        self._custom_rule = rule if index < 0 else ""
        self._show_repeats(len(repeat_choices(self._day())) if index < 0 else index)

    def _repeat_chosen(self, index: int) -> None:
        if self.repeat.itemData(index) != CUSTOM:
            self._repeat_index = index
            return
        before = str(self.repeat.itemData(self._repeat_index))
        self.repeat.setCurrentIndex(self._repeat_index)  # closing the Custom window leaves the list as it was
        dialog = RepeatDialog(before, self._day(), self.all_day.isChecked(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._show_rule(dialog.rule())

    def _rule(self) -> str:
        """The rule as the event has it when it means the same (another program's way of writing it is no change); else the chosen one, its last day in the form of the event."""
        rule = str(self.repeat.currentData())
        if same_rule(rule, self._rule_before, self._day()) and self.all_day.isChecked() == self._all_day_before:
            return self._rule_before
        return fitted(rule, self.all_day.isChecked(), local_zone())

    # --- the command --------------------------------------------------------------------------

    def parameters(self) -> EventEdit:
        """What is in the editor, as event.add and event.edit take it: the fields, and the lists emptied here (clear)."""
        if self.all_day.isChecked():
            start = self.start_date.date().toString(DATE_FORMAT)
            end = self.end_date.date().addDays(1).toString(DATE_FORMAT)
        else:
            start = self.start_moment().toString("yyyy-MM-dd'T'HH:mm")
            end = self.end_moment().toString("yyyy-MM-dd'T'HH:mm")
        attendees = tuple(person.given for person in self.people)
        tags = tuple(self.tag_box.tags())  # the colour tag first: its place says which one it is (D-329)
        original = self._original
        reminder = self.reminders.values() if self.reminders.changed() else ()  # sent only when changed; one left alone is kept whole (keep:, RV-18)
        clear: list[str] = []
        for parameter, entries, had in (("attendee", attendees, original is not None and bool(original.attendees)), ("tag", tags, original is not None and bool(original.tags)),
                                        ("reminder", reminder, original is not None and bool(original.reminders) and self.reminders.changed())):
            if not entries and had:
                clear.append(parameter)
        return EventEdit(
            summary=self.summary.text().strip(), start=start, end=end, location=self.location.toPlainText().strip(), description=self.description.toPlainText().strip(),
            busy=bool(self.busy.currentData()), rrule=None if self._occurrence else self._rule(), attendee=attendees, tag=tags, reminder=reminder,
            item=self._item or "", occurrence=self._occurrence or "", clear=tuple(clear),
        )

    @staticmethod
    def _told_fields(values: EventEdit) -> tuple[object, ...]:
        """What attendees see of a meeting: a change of anything else tells them nothing new."""
        return values.summary, values.start, values.end, values.location, values.description, values.rrule, values.attendee

    def _news(self, values: EventEdit) -> bool:
        """Whether this save is news for attendees: a new meeting, or what they see of yours changed, an attendee taken off included."""
        original = self._original
        if original is None:
            return bool(values.attendee)
        if not original.attendees:
            return bool(values.attendee)
        return original.yours and self._told_fields(values) != self._told_before

    def _ask_and_store(self, values: EventEdit, mailing: MailingResult) -> None:
        """Chrono Mail would mail: the person says so first (D-310). Closing the question goes back to the window, nothing saved. The server mails: it is told to."""
        note = ""
        tell = True
        if mailing.chrono:
            new = self._original is None or not self._original.attendees
            dialog = CalendarMailDialog("Invite attendees" if new else "Change meeting", "Send the attendees an invitation?" if new else "Send the attendees a mail about this change?", EXPLANATION, self)
            choice = dialog.ask()
            if choice is Choice.BACK:
                self.save_button.setEnabled(True)
                return
            tell, note = choice is Choice.SEND, dialog.note
        self._store(replace(values, tell=tell), note)

    def _save(self) -> None:
        values = self.parameters()
        if not values.summary:
            self.result_label.setText(f"<span style='color:{red()}'>The event needs a title.</span>")
            return
        if not self.all_day.isChecked() and self.end_moment() < self.start_moment() or self.all_day.isChecked() and self.end_date.date() < self.start_date.date():
            self.result_label.setText(f"<span style='color:{red()}'>The event cannot end before it starts.</span>")
            return
        if not self._news(values):
            self._store(replace(values, tell=False), "")
            return
        self.save_button.setEnabled(False)  # (a second click would ask, and save, twice)
        place = MailingParameters(item=self._item) if self._item is not None else MailingParameters(calendar=str(self.calendar.currentData()))
        self._backend.ask(EVENT_MAILING, place, lambda mailing: self._ask_and_store(values, mailing), self._refused, owner=self)

    def _refused(self, error: ChronoMailError) -> None:
        self.save_button.setEnabled(True)
        self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

    def _store(self, values: EventEdit, note: str) -> None:
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: EventInfo) -> None:
            self.saved.emit(result.item)
            for draft in (result.draft, result.cancel_draft):
                if draft.message:
                    self.mail_ready.emit(draft.account, draft.folder, draft.message, note)
            self.accept()

        if self._item is None:
            self._backend.ask(EVENT_ADD, carried(values, EventAdd, calendar=str(self.calendar.currentData())), done, self._refused, owner=self)
        else:
            self._backend.ask(EVENT_EDIT, values, done, self._refused, owner=self)
