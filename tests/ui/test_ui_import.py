import dataclasses
from collections.abc import Callable, Generator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings
from PySide6.QtWidgets import QFileDialog, QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID, ChronoMailError
from chrono_mail.engine.commands_emclient import IMPORT_EMCLIENT, ImportResult, ScanResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui import importdialog
from chrono_mail.ui.backend import Backend, Failure, Success
from chrono_mail.ui.prefs import Remembered
from chrono_mail.ui.importdialog import MENU_TEXT, ImportDialog, import_summary, merge, scan_summary
from tests.enginefactory import make_engine
from tests.test_import_emclient import Exports, make_exports
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_import.py 4.2.452"

# --- Version history ----------------------------------------------------
# v4.2.452: an account the import cannot make is "of a kind Chrono Mail cannot make" (IMAP is made now, UR-01)
# v4.2.449: nothing is checked after an import that made no account; the automatic check is check_accounts(None) (D-300)
# v4.2.431: Import's reason is in its (i), not in a hover text
# v4.2.391: while the exports are read the bar moves as a busy bar and the (i) notice shows, Import grey with its hover text; both stop at the answer
# v4.2.390: every Browse with an empty field opens where the last Browse picked, remembered at once (the owner's report)
# v4.2.389: the paths of the last import come back in a new window and Browse opens there; a path that is gone is left out;
#           an empty field's Browse opens in the folder of the last pick, also in a new window
# v4.2.388: exports imported before: Import asks first (Cancel does nothing, Import again starts over and says nothing new came in);
#           after an import no check when send and receive never runs by itself
# v3.6.359: CR-07: a failed batch reloads the window, keeps what was imported in the summary, and Import finishes the rest
# v2.4.327: the check after an import only receives (D-245)
# v2.3.316: the one refresh after an import sends the Outbox first, as the automatic refresh does (D-030, since 2.2.314)
# v1.9.271: The import window's sources are typed (R-19)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v0.5.151: While an import runs no mail is checked and Refresh is off; one check when it is done
# v0.5.143: Import waits for the reading of the exports; a late or older reading answer changes nothing; the bar takes the count of the whole import; separators in the status line
# v0.5.137: the tags in the summaries
# v0.5.133: After an import the list on screen shows the imported mail
# v0.5.132: Initial tests: the Import window and its place in the menu (made-up exports)


class Setup:
    def __init__(self, backend: Backend, exports: Exports) -> None:
        self.backend, self.exports = backend, exports
        self.changed = 0

    def dialog(self) -> ImportDialog:
        def changed() -> None:
            self.changed += 1

        dialog = ImportDialog(self.backend, changed)
        dialog.settings_file.setText(str(self.exports.settings))
        dialog.mail_folder.setText(str(self.exports.mail))
        dialog.backup_file.setText(str(self.exports.backup))
        return dialog

    def messages(self) -> int:
        with open_mailbox(self.backend.engine, LOCAL_ACCOUNT_ID) as mailbox:
            return sum(len(mailbox.index.page(folder.path, limit=1000)) for folder in mailbox.store.list_folders())


@pytest.fixture
def setup(tmp_path: Path) -> Generator[Setup]:
    backend = Backend(make_engine(tmp_path / "profile"))
    yield Setup(backend, make_exports(tmp_path))
    backend.wait_for_done(5000)


def test_the_window_shows_what_would_be_imported(qtbot: QtBot, setup: Setup) -> None:
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    assert dialog.sources.currentItem() is dialog.source_item and dialog.source_item.text(0) == "eM Client"
    assert all(box.isChecked() for box in (dialog.accounts, dialog.rules, dialog.mail, dialog.contacts))
    dialog.scan()
    wait(qtbot, lambda: dialog.summary.text().startswith("Accounts"))
    assert "Mail: 7 messages in 5 folders." in dialog.summary.text()
    assert "Empty: no action that Chrono Mail has" in dialog.summary.text()


def test_import_runs_all_batches_and_reloads_the_window(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importdialog, "BATCH", 3)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.scan()
    wait(qtbot, lambda: dialog.summary.text().startswith("Accounts"))
    dialog.import_button.click()
    assert not dialog.close_button.isEnabled() and dialog.stop_button.isEnabled()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert "Messages imported: 7; already here: 0; failed: 0." in dialog.summary.text()
    assert setup.messages() == 7 and setup.changed == 1
    assert dialog.close_button.isEnabled() and not dialog.stop_button.isEnabled()


def test_stop_ends_after_the_batch_and_import_continues(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importdialog, "BATCH", 3)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    dialog.stop_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import was stopped"))
    assert setup.messages() == 3
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert setup.messages() == 7


def test_a_failed_batch_reloads_the_window_and_import_continues(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """CR-07: the batches before the failure are shown, the check after an import runs, and Import finishes the rest."""
    monkeypatch.setattr(importdialog, "BATCH", 3)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    ask = setup.backend.ask
    calls = [0]

    def failing_second(command: object, parameters: object, done: Callable[[object], None], failed: Callable[[ChronoMailError], None], owner: QObject) -> None:
        if command is IMPORT_EMCLIENT:
            calls[0] += 1
            if calls[0] == 2:
                failed(ChronoMailError("The disk is full"))
                return
        ask(command, parameters, done, failed, owner=owner)  # pyrefly: ignore[bad-argument-type]

    monkeypatch.setattr(setup.backend, "ask", failing_second)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The disk is full"))
    assert "Messages imported: 3;" in dialog.summary.text() and setup.changed == 1
    assert setup.messages() == 3 and dialog.close_button.isEnabled()
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert setup.messages() == 7 and setup.changed == 2


def test_the_summaries_in_plain_words() -> None:
    scan = ScanResult(3, 1, 1, ("a@example.com",), 2, ("Script: it is written as a script",), 4, 1200, 200, 1100, 5)
    text = scan_summary(scan)
    assert "Accounts: 3, of which 1 already here (not made again); 1 of a kind Chrono Mail cannot make (not imported)." in text
    assert "Passwords that cannot be read (you type them later): a@example.com." in text
    assert "Mail: 1,200 messages in 4 folders, 200 of them done by an earlier import (it continues)." in text
    assert scan_summary(ScanResult(0, 0, 0, (), 0, (), 0, 0, 0, 0, 0)) == "Nothing to import was found."
    assert scan_summary(ScanResult(0, 0, 0, (), 0, (), 0, 0, 0, 0, 0, 34, 9)) == "Tags: 34 categories; 9 tagged messages."
    assert "Tags made: 3; messages that got their tags: 9." in import_summary(ImportResult(tags_added=3, messages_tagged=9), stopped=False)
    total = merge(ImportResult(accounts_added=2, messages_imported=5, messages_left=9), ImportResult(messages_imported=4, messages_failed=("x: bad",), messages_left=0))
    assert (total.accounts_added, total.messages_imported, total.messages_failed, total.messages_left) == (2, 9, ("x: bad",), 0)
    assert "Failed: x: bad" in import_summary(total, stopped=False)
    many = tuple(f"n{number}" for number in range(15))
    assert import_summary(ImportResult(notes=many), stopped=True).endswith("n9; and 5 more")


def test_the_file_menu_has_the_import(world: World) -> None:
    file_menu = next(menu for menu in world.window.findChildren(QMenu) if menu.title() == "&File")
    assert MENU_TEXT == "Import…"  # the program is chosen in the window, not in the menu
    assert MENU_TEXT in [action.text() for action in file_menu.actions()]


def test_a_long_summary_can_be_scrolled_to_its_end(qtbot: QtBot, setup: Setup) -> None:
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.summary.setText("\n".join(f"Line {number}" for number in range(60)))
    scroll = dialog.summary_area
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    assert dialog.summary.height() >= dialog.summary.sizeHint().height()  # the whole text is laid out, none of it cut off


def test_after_an_import_the_list_on_screen_shows_the_imported_mail(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = world.window
    world.call("local.enable")
    window.reload_accounts()
    inbox = (LOCAL_ACCOUNT_ID, "folder", "Inbox")
    wait(qtbot, lambda: window.folders.find_item(inbox) is not None)
    found = window.folders.find_item(inbox)
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.account == LOCAL_ACCOUNT_ID and world.backend._pending == {})
    assert window.list_model.rowCount() == 0
    (tmp_path / "exports").mkdir()
    exports = make_exports(tmp_path / "exports")
    world.call("import.emclient", settings_file=None, mail_folder=str(exports.mail), backup_file=None, accounts=False, rules=False, contacts=False, calendars=False)
    window._after_import()  # what the Import window calls when it is done
    wait(qtbot, lambda: window.list_model.rowCount() > 0)


def hold_scans(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[object], None]]:
    """The answers of the readings of the exports are kept back; the test delivers them when it wants."""
    held: list[Callable[[object], None]] = []
    real_call = setup.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == "import.emclient-scan":
            held.append(on_success)
            return 0
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(setup.backend, "call", call)
    return held


def scanned(setup: Setup, dialog: ImportDialog) -> ScanResult:
    result = setup.backend.run_now("import.emclient-scan", dataclasses.asdict(dialog._sources()))
    assert isinstance(result, ScanResult)
    return result


def test_import_waits_until_the_exports_are_read(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    held = hold_scans(setup, monkeypatch)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    assert not dialog.import_button.isEnabled()
    dialog.scan()
    assert not dialog.import_button.isEnabled() and dialog.busy.text.text() == importdialog.READING_EXPORTS
    assert dialog.progress.maximum() == 0 and not dialog.progress.isHidden() and not dialog.busy.isHidden()
    assert dialog.import_info.reason == importdialog.WAIT_FOR_EXPORTS and dialog.import_button.toolTip() == ""  # the (i) says why (D-294)
    held[0](scanned(setup, dialog))
    assert dialog.import_button.isEnabled() and dialog.progress.maximum() == 7
    assert dialog.progress.isHidden() and dialog.busy.isHidden() and dialog.import_info.reason == ""


def test_a_reading_answer_during_the_import_changes_nothing(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importdialog, "BATCH", 3)
    held = hold_scans(setup, monkeypatch)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.scan()
    result = scanned(setup, dialog)
    held[0](result)
    dialog.import_button.click()
    held[0](replace(result, messages=999, messages_done=500))  # a reading that answers while the import runs
    assert dialog.summary.text() == "Importing…" and dialog.progress.maximum() == 7
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert setup.messages() == 7 and dialog.progress.value() == 7


def test_an_older_reading_answer_does_not_replace_a_newer_one(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    held = hold_scans(setup, monkeypatch)
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.scan()
    dialog.scan()
    result = scanned(setup, dialog)
    held[1](result)
    held[0](replace(result, messages=999))
    assert dialog.summary.text() == scan_summary(result) and dialog.progress.maximum() == 7


def test_the_bar_takes_the_count_of_the_whole_import(qtbot: QtBot, setup: Setup) -> None:
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog._running, dialog._done_before = True, 3  # 3 done by an earlier batch; the engine counts them in already
    dialog.progress.setRange(0, 7)
    setup.backend.progress.emit("Importing mail", 5, 7)
    assert dialog.progress.value() == 5


def test_the_status_line_shows_progress_with_thousands_separators(world: World) -> None:
    world.window.report.progress("Importing mail", 45225, 140269)
    assert world.window.status_label.text() == "Importing mail… 45,225 of 140,269"
    world.window.report.progress("Rebuilding the index", 12000, None)
    assert world.window.status_label.text() == "Rebuilding the index… 12,000"


def test_the_window_tells_when_an_import_starts_and_ends(qtbot: QtBot, setup: Setup) -> None:
    told: list[bool] = []
    dialog = ImportDialog(setup.backend, lambda: None, on_running=told.append)
    qtbot.addWidget(dialog)
    dialog.settings_file.setText(str(setup.exports.settings))
    dialog.mail_folder.setText(str(setup.exports.mail))
    dialog.backup_file.setText(str(setup.exports.backup))
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    assert told == [True]
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert told == [True, False]


def fetches(world: World, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The mail checks the window asks for (mail.fetch and mail.send-outbox)."""
    asked: list[str] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command in ("mail.fetch", "mail.send-outbox"):
            asked.append(command)
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    return asked


def test_no_mail_is_checked_while_an_import_runs_nor_after_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    wait(qtbot, lambda: bool(window.mail_state.accounts) and world.backend._pending == {})
    asked = fetches(world, monkeypatch)
    window.checking.import_running(True)  # what the Import window tells when an import starts
    assert not window.act_refresh.isEnabled()
    window.checking.check_accounts(None)  # the timer and the push
    window.checking.check_accounts({world.account_id})  # an account's own timer
    window.checking.send_and_receive()  # F5 while the action is off does nothing either
    window.checking.send_outbox(manual=True)
    assert asked == [] and not window.checking.checking
    window.checking.import_running(False)
    assert window.act_refresh.isEnabled()
    window._after_import()  # what the Import window calls when it is done
    wait(qtbot, lambda: world.backend._pending == {})
    assert asked == [] and not window.checking.checking  # no account was made: its POP3 mail comes with the next refresh (D-300)
    assert window.act_refresh.isEnabled()


def test_exports_imported_before_ask_first_and_import_again_says_nothing_new(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = setup.dialog()
    qtbot.addWidget(dialog)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    later = setup.dialog()  # the window opened again: the reading finds the same exports imported to the end
    qtbot.addWidget(later)
    later.scan()
    wait(qtbot, lambda: later.import_button.isEnabled())
    answers = [False, True]
    asked: list[bool] = []

    def answer(parent: object) -> bool:
        asked.append(answers[len(asked)])
        return asked[-1]

    monkeypatch.setattr(importdialog, "ask_import_again", answer)
    later.import_button.click()
    assert asked == [False] and not later.progress.isVisible() and setup.changed == 1  # Cancel: nothing happens
    later.import_button.click()
    wait(qtbot, lambda: later.summary.text().startswith("Nothing new: everything is already here."))
    text = later.summary.text()
    assert "Messages imported: 0; already here: 7; failed: 0." in text and "Contacts imported: 0; already here: 2." in text
    assert "Rules imported: 0; already here: 2" in text and "Accounts made: 0; already here: 2." in text
    assert setup.messages() == 7


def test_after_an_import_no_check_when_send_and_receive_never_runs_by_itself(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    wait(qtbot, lambda: bool(window.mail_state.accounts) and world.backend._pending == {})
    window.prefs.keep(replace(window.prefs.read(), check_on_start=False, auto_check=False))
    asked = fetches(world, monkeypatch)
    window._after_import()
    wait(qtbot, lambda: world.backend._pending == {})
    assert asked == []


def test_the_paths_of_the_last_import_come_back_and_a_gone_one_is_left_out(
    qtbot: QtBot, setup: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = Remembered(QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat))  # its own file: the program's settings stay as they are
    opened: list[str] = []

    def open_file(parent: object, title: str, start: str, *rest: object) -> tuple[str, str]:
        opened.append(start)
        return str(setup.exports.settings), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(open_file))
    dialog = ImportDialog(setup.backend, lambda: None, memory=memory)
    qtbot.addWidget(dialog)
    assert dialog.settings_file.text() == ""
    dialog.mail_folder.setText(str(setup.exports.mail))
    dialog.backup_file.setText(str(setup.exports.backup))
    dialog._browse_settings()
    assert opened == [str(Path.home())]  # nothing remembered: the home folder
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    again = ImportDialog(setup.backend, lambda: None, memory=memory)
    qtbot.addWidget(again)
    assert (again.settings_file.text(), again.mail_folder.text(), again.backup_file.text()) == (
        str(setup.exports.settings), str(setup.exports.mail), str(setup.exports.backup)
    )
    again._browse_settings()
    assert opened[-1] == str(setup.exports.settings)
    setup.exports.backup.unlink()
    gone = ImportDialog(setup.backend, lambda: None, memory=memory)
    qtbot.addWidget(gone)
    assert gone.backup_file.text() == "" and gone.mail_folder.text() == str(setup.exports.mail)


def test_an_empty_fields_browse_opens_where_the_last_browse_picked(qtbot: QtBot, setup: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = Remembered(QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat))  # its own file: the program's settings stay as they are
    opened: list[str] = []
    picks = [str(setup.exports.settings), ""]  # the settings file, then Cancel

    def open_file(parent: object, title: str, start: str, *rest: object) -> tuple[str, str]:
        opened.append(start)
        return picks.pop(0) if picks else "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(open_file))
    dialog = ImportDialog(setup.backend, lambda: None, memory=memory)
    qtbot.addWidget(dialog)
    dialog._browse_settings()
    dialog._browse_backup()
    folder = str(setup.exports.settings.parent)
    assert opened == [str(Path.home()), folder]
    again = ImportDialog(setup.backend, lambda: None, memory=memory)
    qtbot.addWidget(again)
    again._browse_backup()
    assert opened[-1] == folder



def test_the_summaries_name_imap_accounts_calendar_servers_appointments_tasks_and_notes() -> None:
    scan = ScanResult(26, 0, 0, (), 0, (), 0, 0, 0, 0, 0, imap=1, calendar_servers=1, events=12, tasks=3, notes=2)
    text = scan_summary(scan)
    assert "Accounts: 26 (25 POP3, 1 IMAP); calendars and contacts: 1 server." in text
    assert "Calendars and tasks of Local Folders: 12 appointments, 3 tasks." in text and "Notes: 2 notes (Chrono Mail has no notes; they are not imported)." in text
    done = import_summary(ImportResult(accounts_added=26, calendars_given=1, events_added=12, tasks_added=2, tasks_existing=1), stopped=False)
    assert "Accounts made: 26; already here: 0." in done and "Calendars and contacts given to the mail account of their address: 1." in done
    assert "Appointments imported: 12; already here: 0. Tasks imported: 2; already here: 1." in done
