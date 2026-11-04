"""The card above the text of a mail that holds a calendar message: an invitation, an answer, a cancellation (D-310)."""
import html
from functools import partial
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal, SignalInstance
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core.colours import Theme
from chrono_mail.engine.invitations import InvitationInfo, InvitationPerson
from chrono_mail.pim.times import local_time
from chrono_mail.ui.colours import css, theme
from chrono_mail.ui.flowlayout import FlowRow
from chrono_mail.ui.icons import bundled_icon, place_icon
from chrono_mail.ui.infomark import InfoMark
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.scale import px_for

# --- Version ------------------------------------------------------------
__version__ = "invitationcard.py 4.4.508"

# --- Version history ----------------------------------------------------
# v4.4.508: a text with several items: one block per item with its own details and buttons, and Add all at the top for items that ask no
#           answer; the card says what the text holds and what is left out (D-310)
# v4.4.507: a task says task and task list, with a due moment and the answers COMPLETED and IN-PROCESS; busy times with Send my busy times…;
#           a file that asks nothing or holds several items says so (D-310 step 4)
# v4.4.506: your own meeting: Take their answer, Accept proposal and Decline proposal, Send the latest version (D-310 step 3)
# v4.4.505: Accept (green), Tentative and Decline below an invitation, Add to calendar below a shared item; greyed with an (i) when a
#           newer version is in your calendar (D-310)
# v4.4.504: Initial card: what the message asks or tells, the title, when, the organizer, the place, the attendees with their answers, and
#           what your calendar holds of it (D-310)

# What the message is, in one line, per method; {item} is "meeting" or "task", {who} the attendee who wrote it.
_INTRO: dict[str, str] = {
    "REQUEST": "You have been invited to this {item}.",
    "PUBLISH": "This {item} was sent to you.",
    "ADD": "An occurrence was added to this {item}.",
    "CANCEL": "This {item} was cancelled by its organizer.",
    "REPLY": "{who} {answered} your invitation.",
    "COUNTER": "{who} proposes a change to this {item}.",
    "DECLINECOUNTER": "The organizer declined your proposed change to this {item}.",
    "REFRESH": "{who} asks for the latest version of this {item}.",
}
_ANSWERED: dict[str, str] = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentatively accepted", "DELEGATED": "delegated"}
_PROGRESS: dict[str, str] = {"COMPLETED": "{who} has completed this task.", "IN-PROCESS": "{who} is working on this task."}  # an answer about a task's progress
_YOU: dict[str, str] = {"ACCEPTED": "You accepted.", "DECLINED": "You declined.", "TENTATIVE": "You accepted tentatively.", "DELEGATED": "You delegated it.",
                        "COMPLETED": "You completed it.", "IN-PROCESS": "You are working on it."}
# An attendee's answer as a mark in front of the name, with its hover text (OR-17); the colour is the theme's.
_MARKS: dict[str, tuple[str, Callable[[Theme], str], str]] = {
    "ACCEPTED": ("✓", lambda colours: colours.green, "Accepted"),
    "DECLINED": ("✗", lambda colours: colours.red, "Declined"),
    "TENTATIVE": ("~", lambda colours: colours.amber, "Accepted tentatively"),
    "DELEGATED": ("→", lambda colours: colours.amber, "Delegated"),
    "NEEDS-ACTION": ("?", lambda colours: colours.amber, "Not answered yet"),
    "COMPLETED": ("✓", lambda colours: colours.green, "Completed"),
    "IN-PROCESS": ("…", lambda colours: colours.amber, "Working on it"),
}
_BUSY_SHOWN: int = 20  # busy times listed on the card; the others are counted
_BUSY_WORD: dict[str, str] = {"BUSY-TENTATIVE": " (tentative)", "BUSY-UNAVAILABLE": " (unavailable)"}


def when_text(start: date | datetime | None, end: date | datetime | None, all_day: bool) -> str:
    """When it is, in local time: "Thursday 29 October 2026, 11:00 - 11:30"; a whole day without a time; an end on another day with its date."""
    if start is None:
        return ""
    if all_day or not isinstance(start, datetime):
        first = start if not isinstance(start, datetime) else start.date()
        try:
            last = (end if not isinstance(end, datetime) else end.date()) - timedelta(days=1) if end is not None else first  # the end is the day after
        except OverflowError:  # an end at the first day there is (hostile input): no last day to show
            last = first
        text = first.strftime("%A %d %B %Y")
        return text if last <= first else f"{text} - {last.strftime('%A %d %B %Y')}"
    begins = local_time(start)
    text = begins.strftime("%A %d %B %Y, %H:%M")
    if not isinstance(end, datetime):
        return text
    ends = local_time(end)
    return f"{text} - {ends.strftime('%H:%M')}" if ends.date() == begins.date() else f"{text} - {ends.strftime('%A %d %B %Y, %H:%M')}"


def _person_text(person: InvitationPerson) -> str:
    return f"{person.name} ({person.address})" if person.name and person.name.lower() != person.address.lower() else person.address


def item_word(info: InvitationInfo) -> str:
    """What the person calls the item."""
    return "task" if info.kind == "task" else "meeting"


def home_word(info: InvitationInfo) -> str:
    """What the person calls the place it is kept."""
    return "task list" if info.kind == "task" else "calendar"


def _busy_intro(info: InvitationInfo) -> str:
    """Whose busy times these are: the organizer asks for them or publishes their own; an answer holds the attendee's."""
    person = info.organizer if info.method != "REPLY" else next((each for each in info.attendees if not each.you), None)
    who = (person.name or person.address) if person is not None else "Someone"
    return f"{who} asks when you are busy." if info.method == "REQUEST" else f"The busy times of {who}."


def intro_text(info: InvitationInfo) -> str:
    if info.kind == "freebusy":
        return _busy_intro(info)
    if info.parts or info.journals or info.damaged:
        return _holds_text(info)
    if info.plain:
        return "" if info.item >= 0 else "This file holds one item."  # a block of a file: the top of the card said it
    item = item_word(info)
    writer = next((person for person in info.attendees if not person.you), info.attendees[0] if info.attendees else None)
    who = (writer.name or writer.address) if writer is not None else "Someone"
    if info.method == "REPLY" and writer is not None and writer.answer in _PROGRESS:
        return _PROGRESS[writer.answer].format(who=who)
    answered = _ANSWERED.get(writer.answer, "answered") if writer is not None else "answered"
    return _INTRO.get(info.method, "This {item} was sent to you.").format(item=item, who=who, answered=answered)


def _counted(number: int, one: str, more: str) -> str:
    return f"{number} {one if number == 1 else more}"


def _holds_text(info: InvitationInfo) -> str:
    """What a text holds, by kind, and what is left out of it."""
    kinds = [_counted(number, one, more) for number, one, more in ((info.events, "event", "events"), (info.tasks, "task", "tasks")) if number]
    others = info.items - info.events - info.tasks  # busy times
    if others:
        kinds.append(_counted(others, "list of busy times", "lists of busy times"))
    text = f"This file holds {', '.join(kinds[:-1])} and {kinds[-1]}." if len(kinds) > 1 else f"This file holds {kinds[0] if kinds else 'no items'}."
    if info.journals:
        text += f" {_counted(info.journals, 'journal entry is', 'journal entries are')} left out."
    if info.damaged:
        text += f" {_counted(info.damaged, 'item cannot be read and is', 'items cannot be read and are')} left out."
    if len(info.parts) < info.items and info.parts:
        text += f" The first {len(info.parts)} are shown here."
    return text


def adds_all(info: InvitationInfo) -> bool:
    """Whether the card offers Add all: several items, and they ask no answer."""
    return bool(info.parts) and info.method == "PUBLISH" and info.events + info.tasks > 0


def all_kept(info: InvitationInfo) -> bool:
    """Whether every item on the card that can be kept is in one of your calendars."""
    blocks = info.parts or (info,)
    return len(blocks) == info.items and all(each.stored_in or each.kind == "freebusy" for each in blocks)


def busy_text(info: InvitationInfo) -> str:
    """The busy times on the card, each on a line, in local time; a request holds none, and shows none."""
    if info.method == "REQUEST":
        return ""
    lines = [when_text(period.start, period.end, False) + _BUSY_WORD.get(period.kind, "") for period in info.busy[:_BUSY_SHOWN]]
    if len(info.busy) > _BUSY_SHOWN:
        lines.append(f"and {len(info.busy) - _BUSY_SHOWN} more")
    return "\n".join(lines) or "No busy times in this period."


def status_text(info: InvitationInfo) -> str:
    """What your calendar holds of it: nothing (empty), the copy and your answer, or a newer version."""
    if not info.stored_in:
        return ""
    if info.version == "older":
        return f"A newer version is in {info.stored_in}."
    answer = _YOU.get(info.your_answer, "")
    return f"{answer} In {info.stored_in}.".strip() if info.version != "newer" else f"{answer} This version is newer than the one in {info.stored_in}.".strip()


def organize_choices(info: InvitationInfo) -> list[tuple[str, str]]:
    """The buttons of a message about your own meeting (you are its organizer, D-310 step 3): its label and what it does."""
    if info.organizer is None or not info.organizer.you or info.kind == "freebusy":
        return []
    if info.method == "REPLY":
        return [("Take their answer", "take")]
    if info.method == "COUNTER":
        return [("Accept proposal", "accept_proposal"), ("Decline proposal", "decline_proposal")]
    if info.method == "REFRESH":
        return [("Send the latest version", "send_latest")]
    return []


def organize_reason(info: InvitationInfo) -> str:
    """Why the buttons of a message about your own meeting are greyed (OR-15); "": they work."""
    item, home = item_word(info), home_word(info)
    if not info.stored_in:
        return f"This {item} is not in your {home} any more."
    if not info.organized_by_you:
        return f"The {item} in your {home} has another organizer."
    if info.method != "REFRESH" and not info.occurrence_kept:
        return f"This date is not in your {item} any more."
    if info.method == "COUNTER" and info.version == "newer":
        return f"This proposal is about a version of the {item} you do not have."
    return ""


def apply_text(info: InvitationInfo) -> str:
    """The button that takes the organizer's change into your calendar, for this message; "" when there is none (D-310)."""
    home = home_word(info)
    if info.kind == "freebusy":
        return ""
    if info.method == "CANCEL" and info.stored_in and info.occurrence_kept:
        return f"Remove from {home}"
    if info.method == "ADD":  # occurrence_kept: dates still to add (none: added already, nothing to offer)
        return (f"Add to {home}" if info.occurrence_kept else "") if info.stored_in else "Ask for the latest version"
    if info.method in ("REQUEST", "PUBLISH") and info.stored_in and info.version == "newer":
        return f"Update {home}"
    return ""


class _IconRow(QWidget):
    """An icon at the left of a part, as the rows of the card have."""

    def __init__(self, icon: str | QIcon, part: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._icon_source = icon
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel(self)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self._row.addWidget(part, 1)
        self.restyle()

    def restyle(self) -> None:
        """The icon in the colours and at the size of now: its pixmap is drawn from the palette and the size factor of this moment."""
        self._row.setSpacing(px_for(self, 12))
        size = px_for(self, 20)
        icon = bundled_icon(self._icon_source) if isinstance(self._icon_source, str) else self._icon_source
        self.icon.setPixmap(icon.pixmap(QSize(size, size)))
        self.icon.setFixedWidth(size)


class InvitationCard(QFrame):
    """The card. Everything in it that comes from the message is shown as plain text, never as markup."""

    link_activated = Signal(QUrl)  # the organizer's address was clicked
    link_hovered = Signal(str)  # the pointer came on the organizer's address (its link), or left it ("")
    answer_requested = Signal(str)  # ACCEPTED, TENTATIVE or DECLINED (Add to calendar: ACCEPTED of a shared item)
    apply_requested = Signal()  # the organizer's change into your calendar: Update calendar, Remove from calendar, Add to calendar, Ask…
    delegate_requested = Signal()  # Delegate…: someone else goes in your place
    organize_requested = Signal(str)  # your own meeting: take, accept_proposal, decline_proposal, send_latest
    busy_requested = Signal()  # Send my busy times…
    add_all_requested = Signal()  # Add all

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("invitationCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._sections: list[QVBoxLayout] = []  # their margins follow the size factor (restyle)
        self._section_parts: list[QWidget] = []
        self.intro = self._plain()
        self.title = self._plain()
        self.when = self._plain()
        self.busy_times = self._plain()
        self.organizer = QLabel(self)
        self.organizer.setTextFormat(Qt.TextFormat.RichText)  # built with html.escape
        self.organizer.setWordWrap(True)
        self.organizer.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.organizer.setOpenExternalLinks(False)
        self.organizer.linkActivated.connect(self._organizer_clicked)
        self.organizer.linkHovered.connect(self.link_hovered)
        self.place = self._plain()
        self.people = FlowRow(self, spacing=px_for(self, 6))
        self.status = self._plain()
        self._organizer_row = _IconRow("user", self.organizer, self)
        self._place_row = _IconRow(place_icon(), self.place, self)
        self._people_row = _IconRow("system-users", self.people, self)

        top = self._section(outer)
        top.addWidget(self.intro)
        self.add_all_button = QPushButton("Add all", self)
        self.add_all_button.clicked.connect(self.add_all_requested)
        top.addWidget(self.add_all_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._intro_line = self._line()
        outer.addWidget(self._intro_line)
        self._blocks = QWidget(self)  # a text with several items: a card per item
        self._block_rows = QVBoxLayout(self._blocks)
        self._sections.append(self._block_rows)
        outer.addWidget(self._blocks)
        self.blocks: list[InvitationCard] = []
        self.pressed_item: int = -1  # the block whose button was pressed last (read while its signal is handled); -1: the card itself
        middle = self._section(outer)
        middle.addWidget(self.title)
        middle.addWidget(self.when)
        middle.addWidget(self.busy_times)
        self._details_line = self._line()
        outer.addWidget(self._details_line)
        bottom = self._section(outer)
        bottom.addWidget(self._organizer_row)
        bottom.addWidget(self._place_row)
        bottom.addWidget(self._people_row)
        bottom.addWidget(self.status)
        self._intro_section, self._middle_section, self._bottom_section = self._section_parts
        self._answer_line = self._line()
        outer.addWidget(self._answer_line)
        answers = QWidget(self)
        row = QHBoxLayout(answers)
        self._answer_row = row
        self.accept_button = QPushButton("Accept", answers)
        self.accept_button.setObjectName("invitationAccept")
        self.tentative_button = QPushButton("Tentative", answers)
        self.decline_button = QPushButton("Decline", answers)
        self.decline_button.setObjectName("invitationDecline")
        self.add_button = QPushButton("Add to calendar", answers)
        for button, answer in ((self.accept_button, "ACCEPTED"), (self.tentative_button, "TENTATIVE"), (self.decline_button, "DECLINED"), (self.add_button, "ACCEPTED")):
            button.clicked.connect(partial(self.answer_requested.emit, answer))
            row.addWidget(button)
        self.delegate_button = QPushButton("Delegate…", answers)  # made after the answers: Tab goes along the row as it reads
        self.delegate_button.clicked.connect(self.delegate_requested)
        row.addWidget(self.delegate_button)
        self.apply_button = QPushButton("Update calendar", answers)
        self.apply_button.clicked.connect(self.apply_requested)
        row.addWidget(self.apply_button)
        self.busy_button = QPushButton("Send my busy times…", answers)
        self.busy_button.clicked.connect(self.busy_requested)
        row.addWidget(self.busy_button)
        self.organize_buttons: list[QPushButton] = []
        for _ in range(2):  # your own meeting: at most two choices (accept or decline a proposal)
            button = QPushButton(answers)
            button.clicked.connect(partial(self._organize, len(self.organize_buttons)))
            row.addWidget(button)
            self.organize_buttons.append(button)
        self._organize_actions: list[str] = []
        self.answers_mark = InfoMark(answers)
        row.addWidget(self.answers_mark)
        row.addStretch(1)
        self._answers = answers
        outer.addWidget(answers)
        self.info: InvitationInfo | None = None
        self._theme_shown: str = ""
        if parent is not None:
            parent.installEventFilter(self)  # its palette tells of another theme (eventFilter)

    def _forward(self, position: int, block: "InvitationCard") -> None:
        """The buttons of a block reach whoever listens to this card; pressed_item says which block it was."""

        def pressed(signal: SignalInstance) -> Callable[..., None]:
            def forward(*values: str) -> None:
                self.pressed_item = position
                signal.emit(*values)
            return forward

        block.answer_requested.connect(pressed(self.answer_requested))
        block.organize_requested.connect(pressed(self.organize_requested))
        block.apply_requested.connect(pressed(self.apply_requested))
        block.delegate_requested.connect(pressed(self.delegate_requested))
        block.busy_requested.connect(pressed(self.busy_requested))
        block.link_activated.connect(self.link_activated)
        block.link_hovered.connect(self.link_hovered)

    def _organize(self, position: int) -> None:
        if position < len(self._organize_actions):
            self.organize_requested.emit(self._organize_actions[position])

    def _organizer_clicked(self, link: str) -> None:
        self.link_activated.emit(QUrl(link))

    def _plain(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)  # what a message says is hostile input: never markup
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _section(self, outer: QVBoxLayout) -> QVBoxLayout:
        section = QWidget(self)
        layout = QVBoxLayout(section)
        self._sections.append(layout)
        self._section_parts.append(section)
        outer.addWidget(section)
        return layout

    def _line(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("invitationLine")
        line.setFixedHeight(1)
        return line

    def _theme(self) -> Theme:
        """The theme in use where the card is: read from its parent, since a part with a style sheet of its own (the card) keeps the palette
        it had when it was styled."""
        parent = self.parentWidget()
        return theme((parent if parent is not None else self).palette())

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:  # noqa: N802 - Qt's name
        if watched is self.parentWidget() and event.type() == QEvent.Type.PaletteChange:
            QTimer.singleShot(0, self, self._theme_check)  # tied to the card (never after it is gone); it is never told of a new palette itself
        return super().eventFilter(watched, event)

    def _theme_check(self) -> None:
        if self.info is not None and self._theme().name != self._theme_shown:
            self.show_invitation(self.info)  # another theme: the colours, marks and icons of the new one

    def restyle(self) -> None:
        """The colours of the theme and the sizes of the size factor of now (D-158, D-135)."""
        colours = self._theme()
        self._theme_shown = colours.name
        border = colours.roles["Mid"][0]
        self.setStyleSheet(
            f"QFrame#invitationCard {{ border: 1px solid {css(border)}; border-radius: {px_for(self, 4)}px; }}"
            f" QFrame#invitationLine {{ background: {css(border)}; border: none; }}"
            f" QLabel#invitationPerson {{ background: {css(colours.roles['Button'][0])}; border-radius: {px_for(self, 10)}px;"
            f" padding: {px_for(self, 2)}px {px_for(self, 8)}px; }}"
            f" QPushButton#invitationAccept {{ background: {css(colours.accept_button)}; color: {css(colours.on_answer_button)}; }}"
            f" QPushButton#invitationDecline {{ background: {css(colours.decline_button)}; color: {css(colours.on_answer_button)}; }}"
            f" QPushButton#invitationAccept:disabled, QPushButton#invitationDecline:disabled {{ background: {css(colours.roles['Button'][2])};"
            f" color: {css(colours.roles['ButtonText'][2])}; }}"
        )
        margin = px_for(self, 24)
        self._answer_row.setContentsMargins(margin, px_for(self, 16), margin, px_for(self, 16))
        self._answer_row.setSpacing(px_for(self, 12))
        for button in (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button, self.add_button, self.apply_button):
            button.setMinimumWidth(px_for(self, 120))
        margin = px_for(self, 24)
        for layout in self._sections:
            layout.setContentsMargins(margin, px_for(self, 16), margin, px_for(self, 16))
            layout.setSpacing(px_for(self, 12))
        for row in (self._organizer_row, self._place_row, self._people_row):
            row.restyle()
        self.people.set_spacing(px_for(self, 6))
        title_font = QFont(self.font())  # the title larger than the text, at the size factor of now
        title_font.setPointSizeF(title_font.pointSizeF() * 1.45)
        title_font.setBold(True)
        self.title.setFont(title_font)

    def clear(self) -> None:
        """Nothing of the message before stays: its card and its attendees go."""
        self.info = None
        self._clear_blocks()
        self.people.clear()
        for label in (self.intro, self.title, self.when, self.busy_times, self.organizer, self.place, self.status):
            label.clear()

    def _clear_blocks(self) -> None:
        for block in self.blocks:
            block.setParent(None)
            block.deleteLater()
        self.blocks = []

    def _show_blocks(self, info: InvitationInfo) -> None:
        """A text with several items: what it holds at the top, then a block per item (D-310)."""
        self._clear_blocks()
        self._blocks.setVisible(bool(info.parts))
        for part in (self._middle_section, self._details_line, self._bottom_section):
            part.setVisible(not info.parts)
        self.add_all_button.setVisible(adds_all(info))
        for part in info.parts:
            block = InvitationCard(self._blocks)
            block.show_invitation(part)
            self._forward(part.item, block)
            self._block_rows.addWidget(block)
            self.blocks.append(block)

    def show_invitation(self, info: InvitationInfo) -> None:
        self.info = info
        self.pressed_item = -1
        self.restyle()
        colours = self._theme()
        self.intro.setText(intro_text(info))
        for part in (self._intro_section, self._intro_line):
            part.setVisible(bool(self.intro.text()))
        self._show_blocks(info)
        if info.parts:
            self._answers.setVisible(False)
            self._answer_line.setVisible(False)
            return
        busy = info.kind == "freebusy"
        self.title.setText("Busy times" if busy else info.title or "(no title)")
        when = when_text(info.start, info.end, info.all_day) + (" (repeats)" if info.repeats else "")
        self.when.setText(f"Due {when}" if when and info.kind == "task" else when)
        self.when.setVisible(bool(self.when.text()))
        self.busy_times.setText(busy_text(info) if busy else "")
        self.busy_times.setVisible(bool(self.busy_times.text()))
        organizer = info.organizer
        name = html.escape(organizer.name.strip()) if organizer is not None else ""
        address = html.escape(organizer.address.strip()) if organizer is not None else ""
        link = f'<a href="mailto:{address}">{address}</a>' if address else ""
        if name and link:
            self.organizer.setText(f"Organizer is <b>{name}</b> ({link})")
        else:
            self.organizer.setText(f"Organizer is <b>{name}</b>" if name else f"Organizer is {link}")
        self._organizer_row.setVisible(bool(name or address))
        self.place.setText(info.location)
        self._place_row.setVisible(bool(info.location))
        self.people.clear()
        for person in info.attendees:
            mark, colour_of, hover = _MARKS.get(person.answer, _MARKS["NEEDS-ACTION"])
            chip = QLabel(self.people)
            chip.setObjectName("invitationPerson")
            chip.setTextFormat(Qt.TextFormat.RichText)  # the mark's colour; the name and address escaped
            chip.setText(f'<span style="color:{css(colour_of(colours))}; font-weight:bold">{mark}</span>&nbsp; {html.escape(_person_text(person))}')
            chip.setToolTip(plain_tip(f"{_person_text(person)}: {hover}"))  # the whole name, also when the chip is cut off; never markup (D-259)
            self.people.add(chip)
        self._people_row.setVisible(bool(info.attendees))
        self.status.setText(status_text(info))
        self.status.setVisible(bool(self.status.text()))
        self._show_answers(info)

    def _show_answers(self, info: InvitationInfo) -> None:
        """The buttons a message asks for: Accept, Tentative and Decline for an invitation, Add to calendar for a shared item; greyed with an (i)
        when a newer version is in a calendar already (OR-15)."""
        yours = info.organizer is not None and info.organizer.you  # your own meeting: nothing to answer
        busy = info.kind == "freebusy"
        asks = info.method == "REQUEST" and not yours and not busy  # an added occurrence is added first (its own button)
        shared = info.method == "PUBLISH" and not busy
        wants_busy = info.method == "REQUEST" and not yours and busy
        self.busy_button.setVisible(wants_busy)
        for button in (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button):
            button.setVisible(asks)
        self.add_button.setText(f"Add to {home_word(info)}")
        self.add_button.setVisible(shared and not info.stored_in)
        apply = apply_text(info) if not yours else ""
        self.apply_button.setText(apply)
        self.apply_button.setVisible(bool(apply))
        choices = organize_choices(info)
        self._organize_actions = [action for _, action in choices]
        for button, (label, _) in zip(self.organize_buttons, [*choices, ("", ""), ("", "")], strict=False):
            button.setText(label)
            button.setVisible(bool(label))
        shown = asks or (shared and not info.stored_in) or bool(apply) or bool(choices) or wants_busy
        self._answers.setVisible(shown)
        self._answer_line.setVisible(shown)
        outdated = info.version == "older"
        reason = f"A newer version of this {item_word(info)} is in your {home_word(info)}: this message is out of date." if outdated else (organize_reason(info) if choices else "")
        buttons = (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button, self.add_button, self.apply_button, *self.organize_buttons)
        for button in buttons:
            button.setEnabled(not reason)
        if not reason and "accept_proposal" in self._organize_actions and not info.proposal_usable:
            reason = f"This proposal changes nothing in your {item_word(info)}, or its times cannot be used."
            self.organize_buttons[self._organize_actions.index("accept_proposal")].setEnabled(False)  # Decline proposal still answers it
        self.answers_mark.explain("Answer", reason, *buttons)
