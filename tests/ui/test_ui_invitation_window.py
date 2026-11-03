import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind
from chrono_mail.engine.commands_invitations import CalendarChoice, ChoicesResult
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.pim.itip import BusyPeriod
from chrono_mail.ui import app
from chrono_mail.ui.invitationcalendar import InvitationBusyDialog, InvitationCalendarDialog
from chrono_mail.ui.invitationcard import apply_text, busy_text, intro_text, organize_choices, organize_reason, status_text
from chrono_mail.ui.invitationwindow import CalendarSource, InvitationWindow, is_calendar_file
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.singleinstance import ShowRequests, ask_to_open, requested_file
from tests.ui.test_ui_invitation_answers import CHOICES
from tests.ui.test_ui_invitation_card import CARD
from tests.ui.test_ui_reader import reader, shown  # noqa: F401 - reader is a fixture
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_invitation_window.py 4.4.507"

# --- Version history ----------------------------------------------------
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
    assert apply_text(replace(TASK, stored_in="Tasks (Work)", version="newer")) == "Update task list"
    assert apply_text(replace(TASK, method="ADD", stored_in="Tasks (Work)", occurrence_kept=True)) == "Add to task list"
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
    qtbot.waitUntil(lambda: not window.card.add_button.isVisible(), timeout=5000)  # the card says it is kept now
    assert "In " in window.card.status.text()
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        (calendar,) = store.collections(CollectionKind.EVENTS)
        assert len(store.index.item_ids(calendar.collection_id)) == 1
    window.close()
    qtbot.waitUntil(lambda: not world.window.reading.card_windows)


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
