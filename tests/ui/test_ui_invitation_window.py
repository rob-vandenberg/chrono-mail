import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.commands_invitations import CalendarChoice, ChoicesResult
from chrono_mail.engine.invitations import InvitationPerson
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.pim.itip import BusyPeriod
from chrono_mail.ui import app
from chrono_mail.ui.invitationcalendar import InvitationBusyDialog, InvitationCalendarDialog
from chrono_mail.ui.invitationcard import apply_text, busy_text, intro_text, organize_choices, only_update, organize_done, proposal_when, status_text, organize_reason, status_text
from chrono_mail.ui.invitationwindow import CalendarSource, InvitationWindow, is_calendar_file
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.singleinstance import ShowRequests, ask_to_open, requested_file
from tests.ui.test_ui_invitation_answers import CHOICES
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_window.py 4.4.522"

# --- Version history ----------------------------------------------------
# v4.4.522: Update calendar is for an answer that stands (D-310)
# v4.4.520: an answer that waits in Drafts is said (D-310)
# v4.4.519: the two states of a message about your own meeting, its writer, a proposal with the time now (D-310)
# v4.4.516: handled and not handled: Change my answer, your own answer on your line, a meeting you never answered (D-310)
# v4.4.515: every item of a big file gets its block as the person scrolls, and a click changes only its block, the place in the list stays (D-310)
# v4.4.514: a window stays open until the person closes it (D-310)
# v4.4.512: a date added to a series is answered, it has no button of its own (D-310)
# v4.4.508: one block per item, Add all, Replace and Skip, and the window closing when everything is added (D-310)
# v4.4.507: Initial tests: a task on the card, busy times with their window, an .ics attachment or file as a card in a window of its own, a
#           second start handing a file over (D-310 step 4)

TASK = replace(CARD, kind="task")
BUSY = replace(
    CARD, kind="freebusy", method="REPLY", title="", location="",
    busy=(BusyPeriod(datetime(2026, 11, 3, 10, tzinfo=UTC), datetime(2026, 11, 3, 11, tzinfo=UTC)), BusyPeriod(datetime(2026, 11, 4, 10, tzinfo=UTC), datetime(2026, 11, 4, 11, tzinfo=UTC), "BUSY-TENTATIVE")),
)
SECOND_START = (
    "import sys; from PySide6.QtCore import QCoreApplication; from chrono_mail.ui.singleinstance import ask_to_open; "
    "application = QCoreApplication([]); sys.exit(0 if ask_to_open(sys.argv[2], sys.argv[1]) else 3)"
)
CALENDAR_FILE = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\nUID:file-1@example.com\r\nDTSTAMP:20261028T090000Z\r\nDTSTART:20261105T100000Z\r\nDTEND:20261105T110000Z\r\nSUMMARY:Open day\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def test_a_task_speaks_of_a_task_and_its_task_list() -> None:
    assert intro_text(TASK) == "You have been invited to this task."
    assert apply_text(replace(TASK, method="CANCEL", stored_in="Tasks (Work)", occurrence_kept=True)) == "Remove from task list"
    assert apply_text(replace(TASK, stored_in="Tasks (Work)", version="newer", your_answer="ACCEPTED")) == "Update task list"
    yours = replace(TASK, method="REPLY", organizer=replace(TASK.organizer, you=True))  # type: ignore[arg-type]
    assert organize_reason(yours) == "This task is not in your task list any more."


def test_an_attendees_progress_on_a_task_has_its_own_words_and_marks(reader: ReaderWidget, qtbot: QtBot) -> None:
    people = (replace(CARD.attendees[1], name="Sam", answer="COMPLETED"), replace(CARD.attendees[0], answer="IN-PROCESS"))
    info = replace(TASK, method="REPLY", attendees=people)
    assert intro_text(info) == "Sam has completed this task."
    assert intro_text(replace(info, attendees=(replace(people[0], answer="IN-PROCESS"),))) == "Sam is working on this task."
    assert status_text(replace(TASK, stored_in="Tasks (Work)", your_answer="COMPLETED", version="same")) == "You completed it. In Tasks (Work)."
    reader.show_message(replace(shown(html="<p></p>"), invitation=info))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.when.isVisible())
    tips = [chip.toolTip() for chip in card.people.children() if hasattr(chip, "toolTip") and chip.toolTip()]
    assert any(tip.endswith("Completed") for tip in tips) and any(tip.endswith("Working on it") for tip in tips)
    assert card.when.text().startswith("Due ")


def test_a_task_is_added_to_a_task_list(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(TASK, method="PUBLISH")))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.add_button.isVisible())
    assert card.add_button.text() == "Add to task list"


def test_the_window_for_a_task_says_task_list_also_of_a_read_only_one(qtbot: QtBot) -> None:
    dialog = InvitationCalendarDialog(CHOICES, task=True)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Choose a task list"


def test_busy_times_are_listed_and_nothing_can_be_answered(reader: ReaderWidget, qtbot: QtBot) -> None:
    info = replace(BUSY, organizer=replace(CARD.organizer, you=True))  # type: ignore[arg-type]
    reader.show_message(replace(shown(html="<p></p>"), invitation=info))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.busy_times.isVisible())
    assert card.title.text() == "Busy times" and card.intro.text() == "The busy times of sam@example.net."
    assert len(card.busy_times.text().split("\n")) == 2 and card.busy_times.text().endswith("(tentative)")
    assert organize_choices(info) == [] and apply_text(info) == "" and not card.busy_button.isVisible() and not card.accept_button.isVisible()


def test_a_long_list_of_busy_times_is_cut_with_how_many_more() -> None:
    many = replace(BUSY, busy=BUSY.busy[:1] * 23)
    assert busy_text(many).split("\n")[-1] == "and 3 more" and len(busy_text(many).split("\n")) == 21
    assert busy_text(replace(BUSY, busy=())) == "No busy times in this period."


def test_a_request_for_your_busy_times_offers_your_answer_and_nothing_else(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(BUSY, method="REQUEST", busy=())))
    card = reader._card  # noqa: SLF001
    qtbot.waitUntil(lambda: card.busy_button.isVisible())
    assert card.intro.text().endswith("asks when you are busy.") and not card.accept_button.isVisible() and not card.busy_times.isVisible()
    with qtbot.waitSignal(reader.invitation_busy_requested, timeout=2000):
        card.busy_button.click()


def test_your_own_request_for_busy_times_offers_nothing(reader: ReaderWidget, qtbot: QtBot) -> None:
    reader.show_message(replace(shown(html="<p></p>"), invitation=replace(BUSY, method="REQUEST", busy=(), organizer=replace(CARD.organizer, you=True))))  # type: ignore[arg-type]
    qtbot.waitUntil(lambda: reader._card.intro.isVisible())  # noqa: SLF001
    assert not reader._card.busy_button.isVisible()  # noqa: SLF001


def test_the_busy_window_starts_on_the_receiving_accounts_own_calendars(qtbot: QtBot) -> None:
    choices = ChoicesResult((*CHOICES.calendars, CalendarChoice("d" * 32, "Birthdays", "acc1", "Office", False, False)), "a" * 32, "", "acc1")
    dialog = InvitationBusyDialog(choices)
    qtbot.addWidget(dialog)
    assert dialog.chosen == ("a" * 32, "d" * 32)  # not the public one, not the other account's
    assert dialog.boxes["b" * 32].isEnabled()  # a read-only calendar can count: it is only read
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok is not None and ok.isEnabled()
    dialog.boxes["a" * 32].setChecked(False)
    dialog.boxes["d" * 32].setChecked(False)
    assert not ok.isEnabled() and dialog.chosen == ()


def calendar_file(tmp_path: Path, text: str = CALENDAR_FILE) -> str:
    path = tmp_path / "open day.ics"
    path.write_text(text, newline="")
    return str(path)


def test_an_answered_invitation_shows_change_my_answer_instead_of_the_answers(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    card = reader.card
    reader.show()
    card.show_invitation(replace(CARD, stored_in="Personal (Me)", your_answer="ACCEPTED", occurrence_kept=True, version="same"))
    assert not card.change_button.isHidden() and all(button.isHidden() for button in (card.accept_button, card.tentative_button, card.decline_button, card.delegate_button))
    assert status_text(card.info) == "You accepted. In Personal (Me)."  # type: ignore[arg-type]


def test_a_meeting_in_your_calendar_you_never_answered_says_so_and_can_be_answered(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    card = reader.card
    reader.show()
    for answer in ("", "NEEDS-ACTION"):
        card.show_invitation(replace(CARD, stored_in="Personal (Me)", your_answer=answer, occurrence_kept=True))
        assert not card.accept_button.isHidden() and card.change_button.isHidden()
        assert status_text(card.info) == "You have not answered yet. In Personal (Me)."  # type: ignore[arg-type]


def test_an_update_that_asks_a_new_answer_is_not_handled(reader: ReaderWidget, qtbot: QtBot) -> None:  # noqa: F811
    reader.show()
    reader.card.show_invitation(replace(CARD, stored_in="Personal (Me)", your_answer="ACCEPTED", occurrence_kept=True, version="newer", answer_again=True))
    assert not reader.card.accept_button.isHidden() and reader.card.change_button.isHidden() and reader.card.apply_button.isHidden()


def test_change_my_answer_asks_first_and_then_the_four_answers_return(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, REQUEST_FILE))
    first_choice(monkeypatch)
    window.card.accept_button.click()
    qtbot.waitUntil(lambda: window.card.change_button.isVisible(), timeout=5000)
    assert window.invitation is not None and [person.answer for person in window.invitation.attendees if person.you] == ["ACCEPTED"]  # never "Not answered yet" beside "You accepted"
    asked: list[str] = []

    def cancelled(box: QMessageBox) -> int:
        asked.append(box.text())
        return QMessageBox.StandardButton.Cancel.value

    def confirmed(box: QMessageBox) -> int:
        return QMessageBox.StandardButton.Ok.value

    monkeypatch.setattr(QMessageBox, "exec", cancelled)
    window.card.change_button.click()
    assert asked == ["Changing the status of the invitation will remove the appointment from your calendar until you make a new choice."]
    assert kept_items(world, CollectionKind.EVENTS) == 1
    monkeypatch.setattr(QMessageBox, "exec", confirmed)
    window.card.change_button.click()
    qtbot.waitUntil(lambda: window.card.accept_button.isVisible(), timeout=5000)
    assert kept_items(world, CollectionKind.EVENTS) == 0 and not window.card.change_button.isVisible()
    window.close()


def test_only_an_ics_name_is_a_calendar_file() -> None:
    assert is_calendar_file("Agenda.ICS") and not is_calendar_file("agenda.ics.exe") and not is_calendar_file("")


def test_a_file_opens_as_a_card_in_a_window_of_its_own_and_can_be_added(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_local_pim(world.engine)
    world.window.reading.open_calendar_file(calendar_file(tmp_path))
    qtbot.waitUntil(lambda: bool(world.window.reading.card_windows))
    (window,) = world.window.reading.card_windows
    assert window.isVisible() and window.windowTitle() == "open day.ics" and window.card.title.text() == "Open day"
    assert window.card.intro.text() == "This file holds one item." and window.card.add_button.isVisible()

    def chosen(dialog: InvitationCalendarDialog) -> int:
        next(iter(dialog.radios.values())).setChecked(True)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(InvitationCalendarDialog, "exec", chosen)
    window.card.add_button.click()
    qtbot.waitUntil(lambda: window.invitation is not None and bool(window.invitation.stored_in), timeout=5000)
    assert window.isVisible()  # it is always the person who closes the window
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        assert len(store.index.item_ids(calendar.collection_id)) == 1
    window.close()


TWO_ITEMS = CALENDAR_FILE.replace("END:VCALENDAR", "BEGIN:VTODO\r\nUID:file-2@example.com\r\nDTSTAMP:20261028T090000Z\r\nSUMMARY:Bring cake\r\nEND:VTODO\r\nBEGIN:VJOURNAL\r\nUID:file-3@example.com\r\nEND:VJOURNAL\r\nEND:VCALENDAR")


def first_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    def chosen(dialog: InvitationCalendarDialog) -> int:
        next(iter(dialog.radios.values())).setChecked(True)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(InvitationCalendarDialog, "exec", chosen)


def opened_file(world: World, qtbot: QtBot, path: str) -> InvitationWindow:
    ensure_local_pim(world.engine)
    world.window.reading.open_calendar_file(path)
    qtbot.waitUntil(lambda: bool(world.window.reading.card_windows))
    return world.window.reading.card_windows[0]


def kept_items(world: World, kind: CollectionKind) -> int:
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        return sum(len(store.index.item_ids(each.collection_id)) for each in store.collections(kind))


def test_several_items_show_a_block_each_with_add_all_at_the_top(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, TWO_ITEMS))
    card = window.card
    assert card.intro.text() == "This file holds 1 event and 1 task. 1 journal entry is left out."
    assert card.add_all_button.isVisible() and [block.title.text() for block in card.blocks] == ["Open day", "Bring cake"]
    assert [block.add_button.text() for block in card.blocks] == ["Add to calendar", "Add to task list"]
    assert all(block.add_button.isVisible() and not block.intro.isVisible() for block in card.blocks)
    assert not card.title.isVisible() and not card.add_button.isVisible()
    window.close()


REQUEST_FILE = CALENDAR_FILE.replace("BEGIN:VEVENT", "METHOD:REQUEST\r\nBEGIN:VEVENT", 1).replace("END:VEVENT", "ORGANIZER:mailto:olivia@example.org\r\nATTENDEE:mailto:me@example.com\r\nEND:VEVENT", 1)


def many_items(count: int) -> str:
    events = "".join(f"BEGIN:VEVENT\r\nUID:many-{number}@example.com\r\nDTSTAMP:20261028T090000Z\r\nDTSTART:20261110T100000Z\r\nSUMMARY:Item {number}\r\nEND:VEVENT\r\n" for number in range(count))
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\n{events}END:VCALENDAR\r\n"


def scrolled_to_the_end(window: InvitationWindow, qtbot: QtBot, wanted: int) -> None:
    area = window.findChild(QScrollArea)
    assert area is not None
    for _ in range(wanted):  # each step of the bar may bring the next blocks
        bar = area.verticalScrollBar()
        bar.setValue(bar.maximum())
        built = len(window.card.blocks)
        if built >= wanted:
            return
        qtbot.waitUntil(lambda: len(window.card.blocks) > built, timeout=5000)


def test_a_big_file_opens_with_the_first_blocks_and_every_item_comes_as_you_scroll(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, many_items(75)))
    window.show()
    assert window.card.intro.text() == "This file holds 75 events."
    assert len(window.card.blocks) < 75
    scrolled_to_the_end(window, qtbot, 75)
    assert [block.title.text() for block in window.card.blocks] == [f"Item {number}" for number in range(75)]
    assert window.invitation is not None and len(window.invitation.parts) == 75
    window.close()


def test_blocks_that_could_not_be_read_come_when_the_file_is_back_and_you_scroll_on(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    path = Path(calendar_file(tmp_path, many_items(45)))
    window = opened_file(world, qtbot, str(path))
    text = path.read_bytes()
    path.unlink()
    window.show()
    area = window.findChild(QScrollArea)
    assert area is not None
    qtbot.waitUntil(lambda: not window.card._asking and area.verticalScrollBar().maximum() > 0, timeout=5000)  # noqa: SLF001 - the answer that failed
    path.write_bytes(text)
    scrolled_to_the_end(window, qtbot, 45)
    assert len(window.card.blocks) == 45
    window.close()


def test_blocks_asked_for_another_card_never_land_on_the_card_shown_now(qtbot: QtBot) -> None:
    window = InvitationWindow(replace(CARD, items=30, parts=tuple(replace(CARD, item=place, title=f"Item {place}") for place in range(20))), "Two", lambda _: None)
    qtbot.addWidget(window)
    asked = window.card.showing
    window.show_card(replace(CARD, title="Another"))
    assert window.card.showing != asked  # what the answer is compared with (ReadingPane._more_blocks)
    window.close()


def test_a_file_that_fits_the_window_asks_for_more_blocks_by_itself(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, many_items(55)))
    window.resize(640, 16000)
    window.show()
    qtbot.waitUntil(lambda: len(window.card.blocks) == 55, timeout=5000)
    window.close()


def test_a_click_on_a_block_changes_that_block_and_you_stay_where_you_were(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, many_items(45)))
    window.show()
    scrolled_to_the_end(window, qtbot, 30)
    built = list(window.card.blocks)
    qtbot.waitUntil(lambda: built[25].add_button.isVisible(), timeout=5000)  # the layout shows new blocks a moment later
    area = window.findChild(QScrollArea)
    assert area is not None
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum() // 2)
    place = area.verticalScrollBar().value()
    first_choice(monkeypatch)
    built[25].add_button.click()
    qtbot.waitUntil(lambda: built[25].info is not None and bool(built[25].info.stored_in), timeout=5000)
    assert window.card.blocks[:len(built)] == built and built[24].add_button.isVisible() and not built[25].add_button.isVisible()  # the same blocks, only that one changed
    assert area.verticalScrollBar().value() == place and kept_items(world, CollectionKind.EVENTS) == 1
    window.close()


def test_a_block_adds_only_its_own_item_and_the_window_stays_open_after_the_last(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, TWO_ITEMS))
    first_choice(monkeypatch)
    window.card.blocks[1].add_button.click()
    qtbot.waitUntil(lambda: bool(window.card.blocks) and not window.card.blocks[1].add_button.isVisible(), timeout=5000)
    assert kept_items(world, CollectionKind.TASKS) == 1 and kept_items(world, CollectionKind.EVENTS) == 0 and window.isVisible()
    window.card.blocks[0].add_button.click()
    qtbot.waitUntil(lambda: bool(window.card.blocks) and not window.card.blocks[0].add_button.isVisible(), timeout=5000)
    assert kept_items(world, CollectionKind.EVENTS) == 1 and window.isVisible()
    window.close()


def test_add_all_puts_each_kind_in_its_place_and_the_window_stays_open(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, TWO_ITEMS))
    first_choice(monkeypatch)
    window.card.add_all_button.click()
    qtbot.waitUntil(lambda: bool(window.card.blocks) and not window.card.blocks[0].add_button.isVisible() and not window.card.blocks[1].add_button.isVisible(), timeout=5000)
    assert kept_items(world, CollectionKind.EVENTS) == 1 and kept_items(world, CollectionKind.TASKS) == 1 and window.isVisible()
    window.close()


def test_items_already_there_ask_replace_or_skip_with_the_owners_words(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = opened_file(world, qtbot, calendar_file(tmp_path, TWO_ITEMS))
    first_choice(monkeypatch)
    window.card.blocks[0].add_button.click()
    qtbot.waitUntil(lambda: bool(window.card.blocks) and not window.card.blocks[0].add_button.isVisible(), timeout=5000)
    asked: list[str] = []

    def skip(box: QMessageBox) -> int:
        asked.append(box.text())
        assert [button.text() for button in box.buttons()] == ["Replace", "Skip"] or [button.text() for button in box.buttons()] == ["Skip", "Replace"]
        next(button for button in box.buttons() if button.text() == "Skip").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", skip)
    window.card.add_all_button.click()
    qtbot.waitUntil(lambda: kept_items(world, CollectionKind.TASKS) == 1, timeout=5000)
    assert asked == ["1 of these items is already in this calendar and task list. Replace it?"]
    assert kept_items(world, CollectionKind.EVENTS) == 1 and window.isVisible()
    window.close()


def test_a_window_that_was_opened_with_everything_kept_stays_open(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = calendar_file(tmp_path)
    window = opened_file(world, qtbot, path)
    first_choice(monkeypatch)
    window.card.add_button.click()
    qtbot.waitUntil(lambda: window.invitation is not None and bool(window.invitation.stored_in), timeout=5000)
    window.close()
    qtbot.waitUntil(lambda: not world.window.reading.card_windows, timeout=5000)
    again = opened_file(world, qtbot, path)
    qtbot.wait(50)
    assert again.isVisible() and "In " in again.card.status.text()
    again.close()


def test_declining_keeps_the_window_open(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    request = CALENDAR_FILE.replace("VERSION:2.0", "VERSION:2.0\r\nMETHOD:REQUEST").replace("SUMMARY:", "ORGANIZER:mailto:olivia@example.org\r\nATTENDEE:mailto:me@example.com\r\nSUMMARY:")
    window = opened_file(world, qtbot, calendar_file(tmp_path, request))
    window.card.decline_button.click()
    qtbot.waitUntil(lambda: "declined" in world.window.reading._status.label.text(), timeout=5000)  # noqa: SLF001
    qtbot.wait(100)  # the card is read again after the answer
    assert window.isVisible()
    window.close()


def test_a_file_with_nothing_to_show_says_so_and_brings_the_window_forward(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    called: list[bool] = []
    world.window.reading.open_calendar_file(calendar_file(tmp_path, "not a calendar"), lambda: called.append(True))
    qtbot.waitUntil(lambda: bool(called))
    assert world.window.reading.card_windows == []


def test_a_file_given_before_the_window_has_started_waits_for_it(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = world.window
    path = calendar_file(tmp_path)
    window.open_calendar_file(path)
    assert window.reading.card_windows == [] and window._files_waiting == [path]  # noqa: SLF001
    window.start()
    qtbot.waitUntil(lambda: bool(window.reading.card_windows), timeout=10000)
    assert window._files_waiting == []  # noqa: SLF001


def test_open_attachment_on_an_ics_file_shows_the_card(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    pane = world.window.reading
    asked: list[tuple[CalendarSource, str]] = []

    def wanted(source: CalendarSource, title: str) -> None:
        asked.append((source, title))

    monkeypatch.setattr(pane, "open_calendar_card", wanted)
    pane.shown = ("acc", "Inbox", "m" * 32)
    pane._open_attachment(2, "Agenda.ics")  # noqa: SLF001
    assert asked == [(CalendarSource("acc", "Inbox", "m" * 32, part=2), "Agenda.ics")]


def test_a_message_window_asks_for_the_card_of_an_ics_file(world: World, qtbot: QtBot) -> None:
    window = MessageWindow(world.backend, world.account_id, "Inbox", "a" * 32, shown(text="See the file"), lambda *args: None, lambda url: None, lambda reader: None)
    qtbot.addWidget(window)
    with qtbot.waitSignal(window.calendar_open_requested, timeout=2000) as signal:
        window._open_attachment(1, "agenda.ics")  # noqa: SLF001
    assert signal.args == [1, "agenda.ics"]


def test_the_card_window_shows_a_new_card(qtbot: QtBot) -> None:
    window = InvitationWindow(CARD, "agenda.ics", lambda url: None)
    qtbot.addWidget(window)
    assert window.invitation is CARD
    window.show_card(replace(CARD, title="Another title"))
    assert window.card.title.text() == "Another title"


def test_a_second_start_hands_its_file_to_the_running_program(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    name = f"chrono-mail-test-open-{os.getpid()}"
    requests = ShowRequests(name)
    requests.file_requested.connect(world.window.open_calendar_file)
    assert requests.listen()
    world.window._started = True  # noqa: SLF001 - the key and the accounts are there
    path = calendar_file(tmp_path)
    second = subprocess.Popen([sys.executable, "-c", SECOND_START, name, path], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    qtbot.waitUntil(lambda: bool(world.window.reading.card_windows), timeout=10000)
    qtbot.waitUntil(lambda: second.poll() is not None, timeout=10000)
    assert second.returncode == 0


def test_only_a_calendar_file_is_taken_from_a_request() -> None:
    assert requested_file(b'open "/tmp/agenda.ICS"\n') == "/tmp/agenda.ICS"
    assert requested_file(b'open "/tmp/line\\nbreak.ics"\n') == "/tmp/line\nbreak.ics"  # a path keeps every character
    assert requested_file(b'open "/tmp/agenda.sh"\n') == "" and requested_file(b"open nonsense\n") == "" and requested_file(b"open 12\n") == "" and requested_file(b"show\n") == ""


def test_a_request_for_another_file_is_not_answered(qtbot: QtBot) -> None:
    name = f"chrono-mail-test-other-{os.getpid()}"
    requests = ShowRequests(name)
    assert requests.listen()
    wanted: list[str] = []
    requests.file_requested.connect(wanted.append)
    requests.show_requested.connect(lambda: wanted.append("show"))
    second = subprocess.Popen([sys.executable, "-c", SECOND_START, name, "/tmp/script.sh"], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    qtbot.waitUntil(lambda: second.poll() is not None, timeout=15000)
    assert second.returncode == 3 and wanted == []


def test_the_first_argument_is_taken_when_it_is_an_ics_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    arguments = ["chrono-mail", "agenda.ICS", "-platform", "offscreen"]
    assert app.take_calendar_file(arguments) == str((tmp_path / "agenda.ICS").resolve()) and arguments == ["chrono-mail", "-platform", "offscreen"]
    assert app.take_calendar_file(["chrono-mail", "notes.txt"]) == "" and app.take_calendar_file(["chrono-mail"]) == ""


def test_a_second_start_with_a_file_asks_to_open_it(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def handed(path: str) -> bool:
        asked.append(path)
        return True

    monkeypatch.setattr(app, "ask_to_open", handed)
    assert app.second_start("/tmp/agenda.ics") == 0 and asked == ["/tmp/agenda.ics"]


def test_nobody_listening_means_no_answer(qtbot: QtBot) -> None:
    assert QApplication.instance() is not None
    assert ask_to_open("/tmp/agenda.ics", f"chrono-mail-test-nobody-{os.getpid()}", 300) is False


PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


def test_the_program_is_offered_for_calendar_files_on_both_systems() -> None:
    """OR-40, D-310: Chrono Mail registers for .ics files, as a program to open them with, never taking the person's default."""
    desktop = (PACKAGING / "chrono-mail.desktop").read_text()
    assert "Exec=/usr/bin/chrono-mail %f\n" in desktop and "MimeType=text/calendar;\n" in desktop
    setup_script = (PACKAGING / "chrono-mail.iss").read_text()
    assert "ChangesAssociations=yes" in setup_script and 'Subkey: "Software\\Classes\\.ics\\OpenWithProgids"' in setup_script
    assert '"""{app}\\chrono-mail.exe"" ""%1"""' in setup_script and 'Subkey: "Software\\Classes\\.ics"; ' not in setup_script  # Open with, never the default


SAM = InvitationPerson("sam@example.net", "Sam", "ACCEPTED", False)
PAT = InvitationPerson("pat@example.net", "Pat", "DECLINED", False)
OWN = replace(CARD, organizer=replace(CARD.organizer, you=True), stored_in="Personal (Work)", occurrence_kept=True, version="same", attendees=(PAT, SAM), writer="sam@example.net")  # type: ignore[arg-type]


def test_the_sentence_names_who_wrote_the_answer() -> None:
    assert intro_text(replace(OWN, method="REPLY")) == "Sam accepted your invitation."
    assert intro_text(replace(OWN, method="REPLY", organizer=CARD.organizer)) == "Sam accepted the invitation."


def test_an_answer_is_handled_when_your_copy_says_the_same() -> None:
    waiting = replace(OWN, method="REPLY", kept_answers=(("sam@example.net", "NEEDS-ACTION"), ("pat@example.net", "DECLINED")))
    assert organize_done(waiting) == "" and organize_reason(waiting) == ""
    taken = replace(waiting, kept_answers=(("sam@example.net", "ACCEPTED"),))
    assert organize_reason(taken) == "Their answer is in your meeting."
    assert status_text(taken) == "Their answer is in your meeting. In Personal (Work)."


def test_an_accepted_proposal_and_a_declined_one_are_handled() -> None:
    start, end = datetime(2026, 11, 1, 10, 0, tzinfo=UTC), datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
    open_one = replace(OWN, method="COUNTER", start=start, end=end, kept_start=start.replace(hour=9), kept_end=start)
    assert organize_done(open_one) == ""
    assert organize_done(replace(open_one, kept_start=start, kept_end=end, proposal_usable=False)) == "Your meeting follows this proposal."
    assert organize_done(replace(open_one, proposal_usable=False)) == ""  # times that cannot be used are no accepted proposal
    assert organize_done(replace(open_one, answered_on_mail=True)) == "You declined this proposal."


def test_a_request_answered_is_handled_and_says_so_also_with_the_meeting_gone() -> None:
    answered = replace(OWN, method="REFRESH", answered_on_mail=True, stored_in="")
    assert status_text(answered) == "You answered this request." and organize_reason(answered) == "You answered this request."


def test_a_proposal_shows_the_time_now_beside_the_time_proposed() -> None:
    start, end = datetime(2026, 11, 1, 10, 0, tzinfo=UTC), datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
    proposal = replace(OWN, method="COUNTER", start=start, end=end, kept_start=start.replace(hour=9), kept_end=start)
    shown = proposal_when(proposal, "the new time")
    assert shown.startswith("Proposed: the new time\nNow: ") and proposal_when(replace(proposal, kept_start=start, kept_end=end), "the new time") == "the new time"


def test_an_answer_not_sent_yet_is_said_beside_what_your_calendar_holds() -> None:
    accepted = replace(CARD, stored_in="Personal (Work)", occurrence_kept=True, version="same", your_answer="ACCEPTED")
    assert status_text(replace(accepted, answer_waiting=True)) == f"{status_text(accepted)} Your answer is waiting in Drafts."


KEPT = replace(CARD, stored_in="Personal (Work)", occurrence_kept=True, version="newer", your_answer="ACCEPTED")


def test_a_cancellation_has_no_newer_version_sentence() -> None:
    assert status_text(replace(KEPT, method="CANCEL")) == "You accepted. In Personal (Work)."


def test_an_update_that_keeps_your_answer_only_updates() -> None:
    assert intro_text(KEPT) == "This meeting was changed." and only_update(KEPT) and apply_text(KEPT) == "Update calendar"


def test_an_update_that_moved_asks_the_answer_again_with_only_the_four_answers() -> None:
    moved = replace(KEPT, answer_again=True)
    assert intro_text(moved) == "This meeting was changed. Please answer again." and not only_update(moved) and apply_text(moved) == ""


def test_an_update_you_never_answered_keeps_the_four_answers() -> None:
    assert not only_update(replace(KEPT, your_answer="")) and apply_text(replace(KEPT, your_answer="")) == ""


def test_an_update_shows_the_old_time_and_the_new() -> None:
    start, end = datetime(2026, 11, 1, 10, 0, tzinfo=UTC), datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
    shown = proposal_when(replace(KEPT, start=start, end=end, kept_start=start.replace(hour=9), kept_end=start), "the new time")
    assert shown.startswith("From: ") and shown.endswith("\nTo: the new time")


def test_a_mail_about_one_date_says_so_also_on_its_button() -> None:
    date_of = replace(KEPT, one_date=True)
    assert intro_text(replace(date_of, method="CANCEL")) == "This date of the series was cancelled by its organizer."
    assert apply_text(replace(date_of, method="CANCEL")) == "Remove this date from calendar" and apply_text(date_of) == "Update this date in calendar"
    assert intro_text(replace(date_of, version="same")) == "You have been invited to this date of the series."

