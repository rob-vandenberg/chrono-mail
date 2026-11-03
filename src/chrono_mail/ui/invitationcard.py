"""The card above the text of a mail that holds a calendar message: an invitation, an answer, a cancellation (D-310)."""
import html
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from chrono_mail.core.colours import Theme
from chrono_mail.engine.invitations import InvitationInfo, InvitationPerson
from chrono_mail.pim.times import local_time
from chrono_mail.ui.colours import css, theme
from chrono_mail.ui.flowlayout import FlowRow
from chrono_mail.ui.icons import bundled_icon, place_icon
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.scale import px_for

# --- Version ------------------------------------------------------------
__version__ = "invitationcard.py 4.4.504"

# --- Version history ----------------------------------------------------
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
_YOU: dict[str, str] = {"ACCEPTED": "You accepted.", "DECLINED": "You declined.", "TENTATIVE": "You accepted tentatively.", "DELEGATED": "You delegated it."}
# An attendee's answer as a mark in front of the name, with its hover text (OR-17); the colour is the theme's.
_MARKS: dict[str, tuple[str, Callable[[Theme], str], str]] = {
    "ACCEPTED": ("✓", lambda colours: colours.green, "Accepted"),
    "DECLINED": ("✗", lambda colours: colours.red, "Declined"),
    "TENTATIVE": ("~", lambda colours: colours.amber, "Accepted tentatively"),
    "DELEGATED": ("→", lambda colours: colours.amber, "Delegated"),
    "NEEDS-ACTION": ("?", lambda colours: colours.amber, "Not answered yet"),
}


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


def intro_text(info: InvitationInfo) -> str:
    item = "task" if info.kind == "task" else "meeting"
    writer = next((person for person in info.attendees if not person.you), info.attendees[0] if info.attendees else None)
    who = (writer.name or writer.address) if writer is not None else "Someone"
    answered = _ANSWERED.get(writer.answer, "answered") if writer is not None else "answered"
    return _INTRO.get(info.method, "This {item} was sent to you.").format(item=item, who=who, answered=answered)


def status_text(info: InvitationInfo) -> str:
    """What your calendar holds of it: nothing (empty), the copy and your answer, or a newer version."""
    if not info.stored_in:
        return ""
    if info.version == "older":
        return f"A newer version is in {info.stored_in}."
    answer = _YOU.get(info.your_answer, "")
    return f"{answer} In {info.stored_in}.".strip() if info.version != "newer" else f"{answer} This version is newer than the one in {info.stored_in}.".strip()


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("invitationCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._sections: list[QVBoxLayout] = []  # their margins follow the size factor (restyle)
        self.intro = self._plain()
        self.title = self._plain()
        self.when = self._plain()
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
        outer.addWidget(self._line())
        middle = self._section(outer)
        middle.addWidget(self.title)
        middle.addWidget(self.when)
        outer.addWidget(self._line())
        bottom = self._section(outer)
        bottom.addWidget(self._organizer_row)
        bottom.addWidget(self._place_row)
        bottom.addWidget(self._people_row)
        bottom.addWidget(self.status)
        self.info: InvitationInfo | None = None
        self._theme_shown: str = ""
        if parent is not None:
            parent.installEventFilter(self)  # its palette tells of another theme (eventFilter)

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
        )
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
        self.people.clear()
        for label in (self.intro, self.title, self.when, self.organizer, self.place, self.status):
            label.clear()

    def show_invitation(self, info: InvitationInfo) -> None:
        self.info = info
        self.restyle()
        colours = self._theme()
        self.intro.setText(intro_text(info))
        self.title.setText(info.title or "(no title)")
        self.when.setText(when_text(info.start, info.end, info.all_day) + (" (repeats)" if info.repeats else ""))
        self.when.setVisible(bool(self.when.text()))
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
