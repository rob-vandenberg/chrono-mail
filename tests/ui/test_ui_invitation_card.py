from dataclasses import replace
from datetime import UTC, date, datetime

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import AttachmentInfo
from chrono_mail.engine.invitations import InvitationInfo, InvitationPerson
from chrono_mail.ui.invitationcard import intro_text, status_text, when_text
from chrono_mail.ui.reader import ReaderWidget
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_card.py 4.4.526"

# --- Version history ----------------------------------------------------
# v4.4.526: the card's new words (D-310)
# v4.4.519: "your invitation" only for your own meeting (D-310)
# v4.4.504: Initial tests: the invitation card above the text, alone when the text shows nothing, never markup, its part not a file (D-310)

PART = AttachmentInfo(None, "text/calendar", 1984, False, "attachment-1.ics", card=True)
CARD = InvitationInfo(
    position=0, method="REQUEST", kind="event", uid="meeting-1@example.com", title="<b>Choice</b> of colours", location="Main Street 1",
    start=datetime(2026, 10, 29, 10, tzinfo=UTC), end=datetime(2026, 10, 29, 10, 30, tzinfo=UTC), all_day=False, repeats=False,
    organizer=InvitationPerson("olivia@example.org", "Olivia <i>Organizer</i>", "ACCEPTED", False),
    attendees=(InvitationPerson("me@example.com", "Me", "NEEDS-ACTION", True), InvitationPerson("sam@example.net", "", "DECLINED", False)),
    stored_in="", version="new", your_answer="", text_shows_nothing=True,
)


def test_an_invitation_whose_text_shows_nothing_shows_the_card_alone_and_no_file(reader: ReaderWidget, qtbot: QtBot) -> None:
    """The owner's report: a white page, and the invitation offered as a file. Now the card takes the reader's room."""
    reader.show_message(replace(shown(html="<p>&nbsp;</p>", attachments=(PART,)), invitation=CARD))
    qtbot.waitUntil(lambda: reader._card_area.isVisible())  # noqa: SLF001 - the reader's own part
    assert not reader._stack.isVisible() and reader.chips() == []  # noqa: SLF001
    card = reader._card  # noqa: SLF001
    assert card.title.text() == "<b>Choice</b> of colours" and card.intro.text() == "You have been invited to this meeting."
    assert "Organizer is <b>Olivia &lt;i&gt;Organizer&lt;/i&gt;</b>" in card.organizer.text() and card.place.text() == "Main Street 1"
    chips = [label for label in card.people.findChildren(QLabel) if label.objectName() == "invitationPerson"]
    assert [label.toolTip() for label in chips] == ["Me (me@example.com): Not answered yet", "sam@example.net: Declined"] and not card.status.isVisible()


def test_an_invitation_with_text_shows_the_card_above_it(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(text="Please bring the samples."), invitation=replace(CARD, text_shows_nothing=False)))
    qtbot.waitUntil(lambda: reader._card_area.isVisible() and reader._stack.isVisible())  # noqa: SLF001
    assert reader._card_area.y() < reader._stack.y()  # noqa: SLF001
    reader.show_message(shown(text="An ordinary mail."))
    qtbot.waitUntil(lambda: not reader._card_area.isVisible() and reader._stack.isVisible())  # noqa: SLF001


def test_a_click_on_the_organizer_reports_the_link(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    with qtbot.waitSignal(reader.link_activated, timeout=2000) as signal:
        reader._card.organizer.linkActivated.emit("mailto:olivia@example.org")  # noqa: SLF001
    assert signal.args == [QUrl("mailto:olivia@example.org")]


def test_the_card_says_what_the_message_is_and_what_your_calendar_holds() -> None:
    assert intro_text(replace(CARD, method="CANCEL")) == "This meeting was cancelled by its organizer."
    assert intro_text(replace(CARD, method="REPLY", organizer=InvitationPerson("me@example.org", "Me", "ACCEPTED", True), attendees=(InvitationPerson("sam@example.net", "Sam", "ACCEPTED", False),))) == "Sam accepted your invitation."
    assert intro_text(replace(CARD, kind="task")) == "You have been asked to do this task."
    assert status_text(replace(CARD, stored_in="Calendar (Work)", version="same", your_answer="ACCEPTED")) == "You accepted. This meeting is in your calendar Calendar (Work)."
    assert status_text(replace(CARD, stored_in="Calendar (Work)", version="older")) == "A newer version is in Calendar (Work)."
    thursday, friday = date(2026, 10, 29).strftime("%A %d %B %Y"), date(2026, 10, 30).strftime("%A %d %B %Y")  # in the computer's language
    assert when_text(date(2026, 10, 29), date(2026, 10, 30), True) == thursday
    assert when_text(date(2026, 10, 29), date(2026, 10, 31), True) == f"{thursday} - {friday}"
    assert when_text(datetime(2026, 10, 29, 11), datetime(2026, 10, 29, 11, 30), False) == f"{thursday}, 11:00 - 11:30"  # a floating time stays


def test_with_text_below_the_whole_card_shows_when_there_is_room(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review of 4.4.504: the card's height was taken before its lines wrapped, so it was cut off and scrolled."""
    many = tuple(InvitationPerson(f"person{n}@example.com", f"Person number {n}", "ACCEPTED", False) for n in range(14))
    reader.resize(700, 1400)
    reader.show_message(replace(shown(text="Please bring the samples."), invitation=replace(CARD, attendees=many, text_shows_nothing=False)))
    area = reader._card_area  # noqa: SLF001
    qtbot.waitUntil(lambda: area.isVisible() and area.verticalScrollBar().maximum() == 0 and area.height() > 300)
    reader.resize(450, 1400)  # narrower: more rows of chips, the card follows
    qtbot.waitUntil(lambda: area.verticalScrollBar().maximum() == 0)


def test_a_long_place_scrolls_sideways_and_is_never_cut_off(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review of 4.4.504: a meeting's link as its place was cut off on the right, with no way to see it."""
    link = "https://meet.example.com/" + "x" * 160
    reader.resize(420, 700)
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, location=link)))
    area = reader._card_area  # noqa: SLF001
    qtbot.waitUntil(lambda: area.isVisible() and area.horizontalScrollBar().maximum() > 0)
    assert link in reader._card.place.text()  # noqa: SLF001


def test_another_theme_recolours_the_card(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review of 4.4.504: after a switch of theme the card kept the colours and icons of the old one."""
    from chrono_mail.ui.colours import palette_of, theme, theme_of

    card = reader._card  # noqa: SLF001
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    other = theme_of(not card._theme().dark)  # noqa: SLF001
    QApplication.setPalette(palette_of(other))  # as Settings > Appearance does; tests/ui/conftest.py puts the colours back
    qtbot.waitUntil(lambda: card._theme_shown == other.name)  # noqa: SLF001
    assert other.roles["Mid"][0].lower() in card.styleSheet().lower()


def test_the_organizers_address_under_the_pointer_reaches_the_status_line(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review of 4.4.504: the organizer's link was never reported while hovered, so D-215 never judged it."""
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    with qtbot.waitSignal(reader.link_hovered, timeout=2000) as signal:
        reader._card.organizer.linkHovered.emit("mailto:olivia@example.org")  # noqa: SLF001
    assert signal.args is not None and signal.args[0] == "mailto:olivia@example.org"


def test_the_reader_never_grows_by_itself_for_a_card(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review round 2 of 4.4.504: the card asked for half the reader as its least, which raised the reader's own least height at every resize."""
    reader.resize(700, 260)
    reader.show_message(shown(text="An ordinary mail."))
    qtbot.wait(50)
    plain = reader.minimumSizeHint().height()
    reader.show_message(replace(shown(text="Please bring the samples."), invitation=replace(CARD, text_shows_nothing=False)))
    for height in (900, 260, 900, 260):
        reader.resize(700, height)
        qtbot.wait(20)
    assert reader.minimumSizeHint().height() <= plain and reader.minimumHeight() <= plain


def test_an_attendees_hover_text_is_never_markup(reader: ReaderWidget, qtbot: QtBot) -> None:
    """Review round 2 of 4.4.504: a name with "<...>" in an attendee's hover text was read as markup."""
    hostile = InvitationPerson("x@example.com", "<img src='file:///etc/hostname'><h1>Big</h1>", "ACCEPTED", False)
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(CARD, attendees=(hostile,))))
    (chip,) = [label for label in reader._card.people.findChildren(QLabel) if label.objectName() == "invitationPerson"]  # noqa: SLF001
    assert "&lt;img" in chip.toolTip() and "<img" not in chip.toolTip()


def test_the_keyboard_focus_comes_back_after_a_card_alone(qtbot: QtBot) -> None:
    """Review rounds 2 and 8 of 4.4.504: after a card alone the focus stayed on the hidden card area, or left the reader (OR-16)."""
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

    window = QWidget()  # a field before and after the reader, as in a window: the focus must not leave the reader
    qtbot.addWidget(window)
    rows = QVBoxLayout(window)
    rows.addWidget(QLineEdit(window))
    reader = ReaderWidget(window)
    rows.addWidget(reader)
    rows.addWidget(QLineEdit(window))
    window.resize(700, 600)
    window.show()
    window.activateWindow()
    qtbot.waitExposed(window)
    reader.show_message(shown(text="An ordinary mail."))
    reader._text.setFocus()  # noqa: SLF001
    qtbot.waitUntil(lambda: reader._text.hasFocus())  # noqa: SLF001
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    focus = QApplication.focusWidget()
    assert focus is not None and focus.isVisible() and reader.isAncestorOf(focus)
    reader.show_message(shown(text="Another ordinary mail."))
    focus = QApplication.focusWidget()
    assert focus is not None and focus.isVisible() and reader.isAncestorOf(focus)


def test_clear_leaves_nothing_of_the_card_behind(reader: ReaderWidget) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=CARD))
    reader.clear()
    card = reader._card  # noqa: SLF001
    assert card.info is None and not card.title.text() and not card.place.text() and not card.organizer.text() and not card.intro.text()


def test_an_end_at_the_first_day_there_is_never_stops_the_mail() -> None:
    """Review round 3 of 4.4.504: a hostile all-day end of year 1 raised and the mail was not shown (OR-3)."""
    assert when_text(date(2026, 11, 5), date(1, 1, 1), True) == date(2026, 11, 5).strftime("%A %d %B %Y")
