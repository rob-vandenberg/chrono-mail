"""The card above the text of a mail that holds a calendar message: an invitation, an answer, a cancellation (D-310)."""
import html
from dataclasses import replace
from functools import partial
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import override

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal, SignalInstance
from PySide6.QtGui import QFont, QIcon, QShowEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.core.colours import Theme, answer_button_hover
from chrono_mail.engine.invitations import InvitationInfo, InvitationPerson
from chrono_mail.pim.times import local_time
from chrono_mail.ui.colours import css, theme
from chrono_mail.ui.flowlayout import FlowRow
from chrono_mail.ui.icons import bundled_icon, place_icon
from chrono_mail.ui.infomark import InfoMark
from chrono_mail.ui.linkhint import linkify
from chrono_mail.ui.plainboxes import plain_tip
from chrono_mail.ui.scale import px_for

# --- Version ------------------------------------------------------------
__version__ = "invitationcard.py 4.4.554"

# --- Version history ----------------------------------------------------
# v4.4.554: The mail of a declined event says where it is hidden; Change my answer on it brings back the four answers (D-310)
# v4.4.534: the four answer buttons are gradients (Accept green, Tentative orange, Delegate blue, Decline red) and stand in that order (D-310)
# v4.4.533: A meeting text shows 250 characters and the dots up to 2048, a mark set here reaches the server after a move, and blocks that cannot be read offer Try again (D-310)
# v4.4.532: One dialog for every calendar mail in place of the composer, a cancellation for an attendee taken off, and an address asked for an invitation with no organizer (D-310)
# v4.4.527: every busy time is listed; attendees past those read are counted (D-310)
# v4.4.526: whole sentences and the right words: is in your calendar, event, mail or file, a task you are asked to do, a date; a web address in the place is a link (D-310)
# v4.4.525: a mail of your own says what you sent, with no buttons; a file that does not name you greys its answers with an (i); Add all only with more than one item, greyed when fewer than two are left (D-310)
# v4.4.523: the meeting's own text and an attendee's comment are shown (D-310)
# v4.4.522: an update says so and what changed; Update calendar when your answer stands, the four answers when it moved; a mail about one date says so; a cancellation has no newer-version sentence (D-310)
# v4.4.520: the card says when your answer waits in Drafts (D-310)
# v4.4.519: a message about your own meeting has two states (organize_done); the sentence names who wrote the mail; a proposal shows the time now beside its own (D-310)
# v4.4.517: An answer known from the mail counts as handled and says so (D-310)
# v4.4.516: handled: Change my answer instead of the answer buttons; a meeting you never answered says so (D-310)
# v4.4.515: every item of a text gets its block, built as the person scrolls near it (blocks_wanted, add_blocks); update_blocks changes only some blocks (D-310)
# v4.4.514: all_kept is gone: a window never closes by itself (D-310)
# v4.4.512: a date added to a series you have shows the four answers; of a series you do not have: the text, and only the button Request (D-310)
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
    "ADD": "A date was added to this {item}.",
    "CANCEL": "This {item} was cancelled by its organizer.",
    "REPLY": "{who} {answered} {whose} invitation.",
    "COUNTER": "{who} proposes a change to this {item}.",
    "DECLINECOUNTER": "The organizer declined your proposed change to this {item}.",
    "REFRESH": "{who} asks for the latest version of this {item}.",
}
SHORT_TEXT = 250  # characters of a meeting text shown at first (D-310)
LONGEST_TEXT = 2048  # the most of it the dots show: the rest stays in the data

_NOT_KEPT: str = ("This invite was part of a series that is not in your {home}, so it cannot be added or answered on its own. If you click [Request] below, Chrono Mail "
                   "will create a special email which contains a request to the organizer to resend the original invite. You can change the accompanying text "
                   "if you wish, then click [Send] to send the email. The organizer's calendar program should recognize the request, and the organizer can then "
                   "send you the invite again.")
_OWN: dict[str, str] = {"REQUEST": "invitation", "PUBLISH": "{item}", "ADD": "added date", "CANCEL": "cancellation", "REPLY": "answer", "COUNTER": "proposal",
                        "DECLINECOUNTER": "refusal of a proposal", "REFRESH": "request for the latest version"}  # a mail of your own
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
    """What the person calls the item: an event with nobody else in it is no meeting."""
    return "task" if info.kind == "task" else "meeting" if info.attendees or info.organizer is not None else "event"


def home_word(info: InvitationInfo) -> str:
    """What the person calls the place it is kept."""
    return "task list" if info.kind == "task" else "calendar"


def _busy_intro(info: InvitationInfo) -> str:
    """Whose busy times these are: the organizer asks for them or publishes their own; an answer holds the attendee's."""
    person = info.organizer if info.method != "REPLY" else next((each for each in info.attendees if not each.you), None)
    who = (person.name or person.address) if person is not None else "Someone"
    return f"{who} asks when you are busy." if info.method == "REQUEST" else f"The busy times of {who}."


def asks_for_series(info: InvitationInfo) -> bool:
    """A date added to a series you do not have: it cannot be answered on its own, only the original invite can be asked for."""
    return info.method == "ADD" and not info.stored_in and not (info.organizer is not None and info.organizer.you)


def _writer(info: InvitationInfo) -> InvitationPerson | None:
    """Who wrote the message, as the commands choose: their line, or only their address when they have none; a file does not say: the first who is not you."""
    wrote = info.writer.strip().lower()
    found = next((person for person in info.attendees if wrote and person.address.strip().lower() == wrote), None)
    if found is None and wrote:
        return InvitationPerson(info.writer, "", "NEEDS-ACTION", False)
    return found or next((person for person in info.attendees if not person.you), info.attendees[0] if info.attendees else None)


def comment_text(info: InvitationInfo) -> str:
    """What an attendee wrote with an answer or a proposal, with who wrote it."""
    if not info.comment:
        return ""
    writer = _writer(info)
    return f"{(writer.name or writer.address) if writer is not None else 'Someone'} wrote: {info.comment}"


def is_update(info: InvitationInfo) -> bool:
    """A new version of an invitation you keep, from its organizer."""
    return info.method == "REQUEST" and bool(info.stored_in) and info.version == "newer" and not (info.organizer is not None and info.organizer.you)


def only_update(info: InvitationInfo) -> bool:
    """An update that leaves your answer standing: Update calendar is all it needs. One that moved, or that you never answered, asks for an answer."""
    return is_update(info) and not info.answer_again and info.your_answer in _ANSWERED


def intro_text(info: InvitationInfo) -> str:
    if info.kind == "freebusy":
        return _own_text(info) if info.own_mail else _busy_intro(info)
    if info.parts or info.journals or info.damaged:
        return _holds_text(info)
    if info.own_mail:
        return _own_text(info)
    if info.plain:
        return "" if info.item >= 0 else f"This {_holder(info)} holds one item."  # a block of a file: the top of the card said it
    if asks_for_series(info):
        return _NOT_KEPT.format(home=home_word(info))
    item = "date of the series" if info.one_date and info.method != "ADD" else item_word(info)
    if is_update(info):
        return f"This {item} was changed. Please answer again." if info.answer_again else f"This {item} was changed."
    writer = _writer(info)
    who = (writer.name or writer.address) if writer is not None else "Someone"
    if info.method == "REPLY" and writer is not None and writer.answer in _PROGRESS:
        return _PROGRESS[writer.answer].format(who=who)
    answered = _ANSWERED.get(writer.answer, "answered") if writer is not None else "answered"
    whose = "your" if info.organizer is not None and info.organizer.you else "the"  # a copy of someone's answer to a meeting that is not yours
    if info.method == "REQUEST" and info.kind == "task":
        return "You have been asked to do this task."
    return _INTRO.get(info.method, "This {item} was sent to you.").format(item=item, who=who, answered=answered, whose=whose)


def _holder(info: InvitationInfo) -> str:
    return "mail" if info.in_mail else "file"


def _kept_in(info: InvitationInfo) -> str:
    """Where your copy is, as a sentence."""
    return f"This {item_word(info)} is in your {home_word(info)} {info.stored_in}."


def _counted(number: int, one: str, more: str) -> str:
    return f"{number} {one if number == 1 else more}"


def _holds_text(info: InvitationInfo) -> str:
    """What a text holds, by kind, and what is left out of it."""
    kinds = [_counted(number, one, more) for number, one, more in ((info.events, "event", "events"), (info.tasks, "task", "tasks")) if number]
    others = info.items - info.events - info.tasks  # busy times
    if others:
        kinds.append(_counted(others, "list of busy times", "lists of busy times"))
    holder = _holder(info)
    text = f"This {holder} holds {', '.join(kinds[:-1])} and {kinds[-1]}." if len(kinds) > 1 else f"This {holder} holds {kinds[0] if kinds else 'no items'}."
    if info.journals:
        text += f" {_counted(info.journals, 'journal entry is', 'journal entries are')} left out."
    if info.damaged:
        text += f" {_counted(info.damaged, 'item cannot be read and is', 'items cannot be read and are')} left out."
    return text


def adds_all(info: InvitationInfo) -> bool:
    """Whether the card offers Add all: more than one item that can be added, and they ask no answer."""
    return bool(info.parts) and info.method == "PUBLISH" and info.events + info.tasks > 1 and not info.own_mail


def not_for_you(info: InvitationInfo) -> bool:
    """A file that does not name you: no account of yours received it, so there is no address to answer with."""
    return info.position < 0 and not any(person.you for person in info.attendees)


def _own_text(info: InvitationInfo) -> str:
    """What a mail of your own is, in your Sent or Drafts folder."""
    word = _OWN.get(info.method, "invitation") if info.kind != "freebusy" else ("request for busy times" if info.method == "REQUEST" else "list of busy times")
    word = word.format(item=item_word(info))
    if info.own_mail == "draft":
        return f"This {word} is waiting in Drafts."
    answer = next((person.answer for person in info.attendees if person.you), "") if info.method == "REPLY" else ""
    return f"You sent this {word}. {_YOU.get(answer, '')}".strip()


def busy_text(info: InvitationInfo) -> str:
    """The busy times on the card, each on a line, in local time; a request holds none, and shows none."""
    if info.method == "REQUEST":
        return ""
    lines = [when_text(period.start, period.end, False) + _BUSY_WORD.get(period.kind, "") for period in info.busy]
    return "\n".join(lines) or "No busy times in this period."


def status_text(info: InvitationInfo) -> str:
    """What became of it. A mail of your own says what it is, at the top."""
    return "" if info.own_mail else _kept_text(info)


def _kept_text(info: InvitationInfo) -> str:
    """What your calendar holds of it: nothing (empty), the copy and your answer, or a newer version. An answer that left no copy: only that answer."""
    done = organize_done(info)
    if done:
        return f"{done} {_kept_in(info)}" if info.stored_in and not info.answered_on_mail else done
    if info.answered_on_mail:
        return _YOU[info.your_answer]
    if not info.stored_in:
        return ""
    if info.version == "older":
        return f"A newer version is in {info.stored_in}."
    asks = info.method in ("REQUEST", "ADD") and not (info.organizer is not None and info.organizer.you)
    answer = _YOU.get(info.your_answer, "You have not answered yet." if asks else "")
    if info.your_answer == "DECLINED" and info.kind == "event" and info.version != "newer":
        return f"{answer} It is in your {info.stored_in}, marked hidden."
    return f"{answer} {_kept_in(info)}".strip() if info.version != "newer" or info.method in ("ADD", "CANCEL") else f"{answer} This version is newer than the one in {info.stored_in}.".strip()


def is_handled(info: InvitationInfo) -> bool:
    """An invitation counts as handled when you answered it, not when it merely is in a calendar: its answer buttons give way to Change my answer.
    An update that asks a new answer is not handled yet."""
    if info.asked_again:
        return False
    if info.answered_on_mail:
        return True
    if info.method == "ADD":  # (occurrence_kept: dates are still to add)
        return bool(info.stored_in) and not info.occurrence_kept and info.your_answer in _ANSWERED
    return bool(info.stored_in) and info.occurrence_kept and info.your_answer in _ANSWERED and info.version == "same"


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


def organize_done(info: InvitationInfo) -> str:
    """A message about your own meeting has two states too: "" while it waits for you, else what became of it. An answer is handled when your copy says
    the same, an accepted proposal when your meeting has its times; a declined proposal and a request answered exist only as the mark on the mail."""
    if not organize_choices(info):
        return ""
    if info.answered_on_mail:
        return "You declined this proposal." if info.method == "COUNTER" else "You answered this request."
    if not info.stored_in or not info.organized_by_you:
        return ""
    if info.method == "COUNTER":
        follows = not info.proposal_usable and info.kept_start is not None and (info.kept_start, info.kept_end) == (info.start, info.end)
        return f"Your {item_word(info)} follows this proposal." if follows else ""
    writer = _writer(info)
    if info.method != "REPLY" or writer is None or writer not in info.attendees or not info.occurrence_kept:
        return ""
    return f"Their answer is in your {item_word(info)}." if dict(info.kept_answers).get(writer.address.strip().lower()) == writer.answer else ""


def proposal_when(info: InvitationInfo, when: str) -> str:
    """The new time of a proposal or an update, beside the time the meeting has now, so the person can compare before deciding."""
    if info.kind != "event" or not when or info.kept_start is None or (info.kept_start, info.kept_end) == (info.start, info.end):
        return when
    now = when_text(info.kept_start, info.kept_end, not isinstance(info.kept_start, datetime))
    return f"Proposed: {when}\nNow: {now}" if info.method == "COUNTER" else f"From: {now}\nTo: {when}"


def organize_reason(info: InvitationInfo) -> str:
    """Why the buttons of a message about your own meeting are greyed (OR-15); "": they work."""
    item, home = item_word(info), home_word(info)
    done = organize_done(info)
    if done:
        return done
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
        return f"Remove this date from {home}" if info.one_date else f"Remove from {home}"
    if asks_for_series(info):
        return "Request"
    if only_update(info) or (info.method == "PUBLISH" and info.stored_in and info.version == "newer"):
        return f"Update this date in {home}" if info.one_date else f"Update {home}"
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

    link_activated = Signal(QUrl)  # the organizer's address or a web address in the place was clicked
    link_hovered = Signal(str)  # the pointer came on such an address (its link), or left it ("")
    answer_requested = Signal(str)  # ACCEPTED, TENTATIVE or DECLINED (Add to calendar: ACCEPTED of a shared item)
    apply_requested = Signal()  # the organizer's change into your calendar: Update calendar, Remove from calendar, Add to calendar, Ask…
    delegate_requested = Signal()  # Delegate…: someone else goes in your place
    organize_requested = Signal(str)  # your own meeting: take, accept_proposal, decline_proposal, send_latest
    busy_requested = Signal()  # Send my busy times…
    add_all_requested = Signal()  # Add all
    change_requested = Signal()  # Change my answer
    blocks_wanted = Signal(int)  # the person came near the end of the blocks built so far: the blocks from this place on, for add_blocks
    blocks_changed = Signal()  # blocks came or changed: the card has another height

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("invitationCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._next_block: int = 0  # the place of the first item without a block yet
        self._asking: bool = False  # blocks_wanted was said and add_blocks has not answered yet
        self._try_again: QWidget | None = None  # the line under the blocks when the next ones could not be read
        self.showing: int = 0  # counts what was shown here: an answer about what was shown before goes nowhere
        self._scrolled_by: QScrollArea | None = None
        self._sections: list[QVBoxLayout] = []  # their margins follow the size factor (restyle)
        self._section_parts: list[QWidget] = []
        self.intro = self._plain()
        self.title = self._plain()
        self.when = self._plain()
        self.comment = self._plain()
        self.description = self._plain()
        self.busy_times = self._plain()
        self.organizer = QLabel(self)
        self.organizer.setTextFormat(Qt.TextFormat.RichText)  # built with html.escape
        self.organizer.setWordWrap(True)
        self.organizer.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.organizer.setOpenExternalLinks(False)
        self.organizer.linkActivated.connect(self._organizer_clicked)
        self.organizer.linkHovered.connect(self.link_hovered)
        self.place = self._plain()
        self.place.setTextFormat(Qt.TextFormat.RichText)  # built with linkify, which escapes the text
        self.place.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.place.setOpenExternalLinks(False)
        self.place.linkActivated.connect(self._organizer_clicked)
        self.place.linkHovered.connect(self.link_hovered)
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
        self.add_all_mark = InfoMark(self)
        top.addWidget(self.add_all_mark, 0, Qt.AlignmentFlag.AlignLeft)
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
        middle.addWidget(self.comment)
        middle.addWidget(self.description)
        self.more_text = QPushButton("…", self)
        self.more_text.setFlat(True)
        self.more_text.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_text.setToolTip("Show more of this text")
        self.more_text.clicked.connect(self._show_more_text)
        self.more_text.hide()
        self._whole_text: str = ""  # the meeting's text the person opened with the dots: a card shown again stays open
        middle.addWidget(self.more_text, 0, Qt.AlignmentFlag.AlignLeft)
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
        self.tentative_button.setObjectName("invitationTentative")
        self.delegate_button = QPushButton("Delegate…", answers)  # made in the order they read: Tab goes along the row as it reads
        self.delegate_button.setObjectName("invitationDelegate")
        self.decline_button = QPushButton("Decline", answers)
        self.decline_button.setObjectName("invitationDecline")
        self.add_button = QPushButton("Add to calendar", answers)
        for button, answer in ((self.accept_button, "ACCEPTED"), (self.tentative_button, "TENTATIVE"), (self.delegate_button, ""), (self.decline_button, "DECLINED"), (self.add_button, "ACCEPTED")):
            if answer:
                button.clicked.connect(partial(self.answer_requested.emit, answer))
            else:
                button.clicked.connect(self.delegate_requested)
            row.addWidget(button)
        self.change_button = QPushButton("Change my answer", answers)
        self.change_button.clicked.connect(self.change_requested)
        row.addWidget(self.change_button)
        self.request_label = QLabel("Request to resend the original invite:", answers)
        row.addWidget(self.request_label)
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
        block.change_requested.connect(pressed(self.change_requested))
        block.busy_requested.connect(pressed(self.busy_requested))
        block.link_activated.connect(self.link_activated)
        block.link_hovered.connect(self.link_hovered)

    def _organize(self, position: int) -> None:
        if position < len(self._organize_actions):
            self.organize_requested.emit(self._organize_actions[position])

    def _organizer_clicked(self, link: str) -> None:
        self.link_activated.emit(QUrl(link))

    def _show_more_text(self) -> None:
        """The dots: the meeting's text up to the longest shown; the rest stays in the data."""
        self._whole_text = self.info.description if self.info is not None else ""
        self.description.setText(self._whole_text[:LONGEST_TEXT])
        self.more_text.hide()

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

    def _answer_styles(self, colours: Theme) -> str:
        """The four answer buttons: a gradient from the top colour to the bottom one; lighter under the pointer, an outline with the
        focus, the grey of a disabled button when they cannot be used."""
        radius = px_for(self, 4)
        pad = f"{px_for(self, 4)}px {px_for(self, 12)}px"
        focus = css(colours.roles["Highlight"][0])
        disabled = css(colours.roles["Button"][2])
        disabled_text = css(colours.roles["ButtonText"][2])
        parts: list[str] = []
        for name, top, bottom, text in (
            ("Accept", colours.accept_button, colours.accept_button_bottom, colours.on_answer_button),
            ("Tentative", colours.tentative_button, colours.tentative_button_bottom, colours.on_answer_button),
            ("Delegate", colours.delegate_button, colours.delegate_button_bottom, colours.on_answer_button),
            ("Decline", colours.decline_button, colours.decline_button_bottom, colours.on_answer_button),
        ):
            def gradient(first: str, second: str) -> str:
                return f"qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 {css(first)}, stop: 1 {css(second)})"
            selector = f"QPushButton#invitation{name}"
            parts.append(f" {selector} {{ background: {gradient(top, bottom)}; color: {css(text)}; border: 1px solid {css(bottom)};"
                         f" border-radius: {radius}px; padding: {pad}; }}")
            parts.append(f" {selector}:hover {{ background: {gradient(answer_button_hover(top), answer_button_hover(bottom))}; }}")
            parts.append(f" {selector}:focus {{ border: 1px solid {focus}; }}")
            parts.append(f" {selector}:disabled {{ background: {disabled}; color: {disabled_text}; border: 1px solid {disabled}; }}")
        return "".join(parts)

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
            f"{self._answer_styles(colours)}"
        )
        margin = px_for(self, 24)
        self._answer_row.setContentsMargins(margin, px_for(self, 16), margin, px_for(self, 16))
        self._answer_row.setSpacing(px_for(self, 12))
        for button in (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button, self.change_button, self.add_button, self.apply_button):
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
        for label in (self.intro, self.title, self.when, self.comment, self.description, self.busy_times, self.organizer, self.place, self.status):
            label.clear()

    def _clear_blocks(self) -> None:
        for block in self.blocks:
            block.setParent(None)
            block.deleteLater()
        self.blocks = []
        self._next_block = 0
        self._no_try_again()

    def _show_blocks(self, info: InvitationInfo) -> None:
        """A text with several items: what it holds at the top, then a block per item (D-310)."""
        self._clear_blocks()
        self._blocks.setVisible(bool(info.parts))
        for part in (self._middle_section, self._details_line, self._bottom_section):
            part.setVisible(not info.parts)
        self._show_add_all(info)
        self._asking = False
        self._build_blocks(info.parts)
        if info.parts:
            self._watch_scrolling()
            QTimer.singleShot(0, self, self._want_more)  # (the first blocks may leave room)

    def _show_add_all(self, info: InvitationInfo) -> None:
        """Add all, greyed with an (i) when fewer than two items are left to add (OR-15)."""
        self.add_all_button.setVisible(adds_all(info))
        self.add_all_button.setEnabled(info.left > 1)
        self.add_all_mark.explain("Add all", "" if info.left > 1 or not adds_all(info) else "Fewer than two items are left to add: the buttons of the items do the rest.", self.add_all_button)

    def _build_blocks(self, parts: tuple[InvitationInfo, ...]) -> None:
        for part in parts:
            block = InvitationCard(self._blocks)
            block.show_invitation(replace(part, own_mail=self.info.own_mail) if self.info is not None else part)
            self._forward(part.item, block)
            self._block_rows.addWidget(block)
            self.blocks.append(block)
            self._next_block = part.item + 1

    def _watch_scrolling(self) -> None:
        """Every item gets its block, built when the person scrolls near it: a huge file opens as fast as a small one (D-310)."""
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget, QScrollArea):
            widget = widget.parentWidget()
        if widget is None or widget is self._scrolled_by:
            return
        self._scrolled_by = widget
        widget.verticalScrollBar().valueChanged.connect(self._want_more)
        widget.verticalScrollBar().rangeChanged.connect(self._want_more)

    def _want_more(self, *_: int) -> None:
        area = self._scrolled_by
        if self.info is None or area is None or self._asking or self._try_again is not None or not self.isVisible() or self._next_block >= self.info.items or not self.info.parts:
            return
        bar = area.verticalScrollBar()
        if bar.maximum() - bar.value() <= area.viewport().height():
            self._asking = True
            self.blocks_wanted.emit(self._next_block)

    def blocks_failed(self) -> None:
        """The next blocks could not be read: a calm line says so, and the person says when to try again."""
        self._asking = False
        if self._try_again is not None:
            return
        row = QWidget(self._blocks)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("The next items could not be read just now.", row))
        button = QPushButton("Try again", row)
        button.clicked.connect(self._read_on)
        layout.addWidget(button)
        layout.addStretch(1)
        self._block_rows.addWidget(row)
        self._try_again = row

    def _no_try_again(self) -> None:
        if self._try_again is not None:
            self._try_again.setParent(None)
            self._try_again.deleteLater()
            self._try_again = None

    def _read_on(self) -> None:
        self._no_try_again()
        self._asking = True
        self.blocks_wanted.emit(self._next_block)

    def add_blocks(self, first: int, parts: tuple[InvitationInfo, ...]) -> None:
        """The answer to blocks_wanted: the blocks go below those on screen, and the place in the list stays."""
        self._asking = False
        if self.info is None or first != self._next_block:
            return  # another card came meanwhile
        if not parts:
            self.blocks_failed()
            return
        self._build_blocks(parts)
        self.info = replace(self.info, parts=self.info.parts + parts)
        self.blocks_changed.emit()
        QTimer.singleShot(0, self, self._want_more)

    def ask_again(self, item: int) -> None:
        """Change my answer on a declined event: nothing changed, the four answers are back on this card until it is shown anew (D-310)."""
        if self.info is None:
            return
        if self.info.parts:
            self.update_blocks(tuple(replace(part, asked_again=True) for part in self.info.parts if part.item == item), self.info.left)
        else:
            self.show_invitation(replace(self.info, asked_again=True))

    def update_blocks(self, parts: tuple[InvitationInfo, ...], left: int) -> None:
        """Only these blocks change, in their place: the person stays where they were in the list (D-310)."""
        if self.info is None:
            return
        changed = {part.item: part for part in parts}
        for block in self.blocks:
            if block.info is not None and block.info.item in changed:
                block.show_invitation(replace(changed[block.info.item], own_mail=self.info.own_mail))
        self.info = replace(self.info, parts=tuple(changed.get(part.item, part) for part in self.info.parts), left=left)
        self._show_add_all(self.info)
        self.blocks_changed.emit()

    @override
    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        QTimer.singleShot(0, self, self._want_more)

    def show_invitation(self, info: InvitationInfo) -> None:
        self.info = info
        self.showing += 1
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
        self.when.setText(f"Due {when}" if when and info.kind == "task" else proposal_when(info, when))
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
        self.comment.setText(comment_text(info))
        self.comment.setVisible(bool(self.comment.text()))
        opened = info.description == self._whole_text
        self.description.setText(info.description[:LONGEST_TEXT if opened else SHORT_TEXT])
        self.description.setVisible(bool(info.description))
        self.more_text.setVisible(len(info.description) > SHORT_TEXT and not opened)
        self.place.setText(linkify(info.location))
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
        if info.more_attendees:
            self.people.add(QLabel(f"and {info.more_attendees} more", self.people))
        self._people_row.setVisible(bool(info.attendees))
        self.status.setText(status_text(info))
        self.status.setVisible(bool(self.status.text()))
        self._show_answers(info)

    def _show_answers(self, info: InvitationInfo) -> None:
        """The buttons a message asks for: Accept, Tentative and Decline for an invitation and for a date added to a series you have, Add to calendar
        for a shared item; greyed with an (i) when a newer version is in a calendar already (OR-15)."""
        yours = info.organizer is not None and info.organizer.you  # your own meeting: nothing to answer
        busy = info.kind == "freebusy"
        quiet = bool(info.own_mail)  # a mail you wrote asks you nothing
        asks = (info.method == "REQUEST" or (info.method == "ADD" and bool(info.stored_in))) and not yours and not busy and not quiet
        shared = info.method == "PUBLISH" and not busy and not quiet
        wants_busy = info.method == "REQUEST" and not yours and busy and not quiet
        self.busy_button.setVisible(wants_busy)
        handled = asks and is_handled(info)
        self.change_button.setVisible(handled)
        for button in (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button):
            button.setVisible(asks and not handled and not only_update(info))
        self.add_button.setText(f"Add to {home_word(info)}")
        self.add_button.setVisible(shared and not info.stored_in)
        apply = apply_text(info) if not yours and not quiet else ""
        self.apply_button.setText(apply)
        self.apply_button.setVisible(bool(apply))
        self.request_label.setVisible(asks_for_series(info) and not quiet)
        choices: list[tuple[str, str]] = organize_choices(info) if not quiet else []
        self._organize_actions = [action for _, action in choices]
        for button, (label, _) in zip(self.organize_buttons, [*choices, ("", ""), ("", "")], strict=False):
            button.setText(label)
            button.setVisible(bool(label))
        shown = asks or (shared and not info.stored_in) or bool(apply) or bool(choices) or wants_busy
        self._answers.setVisible(shown)
        self._answer_line.setVisible(shown)
        outdated = info.version == "older"
        reason = f"A newer version of this {item_word(info)} is in your {home_word(info)}: this message is out of date." if outdated else (organize_reason(info) if choices else "")
        answering = (self.accept_button, self.tentative_button, self.decline_button, self.delegate_button, self.busy_button)
        buttons = (*answering, self.change_button, self.add_button, self.apply_button, *self.organize_buttons)
        for button in buttons:
            button.setEnabled(not reason)
        if not reason and "accept_proposal" in self._organize_actions and not info.proposal_usable:
            reason = f"This proposal changes nothing in your {item_word(info)}, or its times cannot be used."
            self.organize_buttons[self._organize_actions.index("accept_proposal")].setEnabled(False)  # Decline proposal still answers it
        if not reason and (asks or wants_busy or (asks_for_series(info) and not quiet)) and not_for_you(info):
            reason = f"None of your addresses is on this {'request' if busy else item_word(info)}, so there is no address to answer with."
            for button in (*answering, *((self.apply_button,) if asks_for_series(info) else ())):
                button.setEnabled(False)
        self.answers_mark.explain("Answer", reason, *buttons)
