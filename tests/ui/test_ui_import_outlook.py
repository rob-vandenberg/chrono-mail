from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_outlook import FileScan, ImportResult, ScanResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui import importdialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.importdialog import WINDOWS_ONLY, ImportDialog, data_file_import_summary, data_file_scan_summary, merge_data_files
from tests.enginefactory import make_engine
from tests.test_import_outlook import FakeFile, data_file, files  # noqa: F401 - files is the fixture of the made-up data files
from tests.test_import_outlook_profile import machine  # noqa: F401 - the fixture of the made-up profile
from tests.ui.world import wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_import_outlook.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: on the Windows computer with a readable profile: Accounts, Rules and Signatures offered, its data files listed without asking
# v4.2.385: the items that got their tags later in the end summary
# v4.2.385: a path holding "; " stays one file
# v4.2.385: Initial tests: Outlook in the Import window, its page, its greyed choices, the data files, the summary, the batches, Stop (made-up
#           data files)


class Setup:
    def __init__(self, backend: Backend, path: Path) -> None:
        self.backend, self.path = backend, path
        self.changed = 0

    def dialog(self, qtbot: QtBot) -> ImportDialog:
        def changed() -> None:
            self.changed += 1

        dialog = ImportDialog(self.backend, changed)
        qtbot.addWidget(dialog)
        dialog.sources.setCurrentItem(dialog.data_file_item)
        dialog.data_files.setText(str(self.path))
        return dialog

    def messages(self) -> int:
        with open_mailbox(self.backend.engine, LOCAL_ACCOUNT_ID) as mailbox:
            return sum(len(mailbox.index.page(folder.path, limit=1000)) for folder in mailbox.store.list_folders())


@pytest.fixture
def setup(tmp_path: Path, files: dict[Path, FakeFile]) -> Generator[Setup]:  # noqa: F811 - the fixture of the made-up data files
    backend = Backend(make_engine(tmp_path / "profile"))
    yield Setup(backend, data_file(files, tmp_path, "personal.pst", "Personal Folders"))
    backend.wait_for_done(5000)


def test_the_data_file_program_has_its_own_page_with_greyed_choices(qtbot: QtBot, setup: Setup) -> None:
    dialog = ImportDialog(setup.backend, lambda: None)
    qtbot.addWidget(dialog)
    group = dialog.source_item.parent()
    assert group is not None and group.text(0) == "Applications"
    assert group.indexOfChild(dialog.data_file_item) == group.indexOfChild(dialog.source_item) + 1 and dialog.data_file_item.text(0) == "Outlook"
    assert dialog.pages.currentWidget() is dialog.exports_page
    dialog.sources.setCurrentItem(dialog.data_file_item)
    assert dialog.pages.currentWidget() is dialog.data_file_page
    assert [box.text() for box in dialog._data_file_types()] == ["Mail", "Calendar", "Contacts", "Tasks", "Tags (categories)"]
    assert all(box.isChecked() and box.isEnabled() for box in dialog._data_file_types())
    assert [box.text() for box in dialog.data_file_later] == ["Accounts", "Rules", "Signatures"]
    assert all(not box.isEnabled() and box.toolTip() == WINDOWS_ONLY and box.isVisibleTo(dialog) for box in dialog.data_file_later)  # greyed, never hidden
    assert not dialog.import_button.isEnabled() and dialog.summary.text() == importdialog.DATA_FILE_HINT
    dialog.sources.setCurrentItem(dialog.source_item)
    assert dialog.pages.currentWidget() is dialog.exports_page and dialog.summary.text().startswith("Choose the exports")


def test_browse_takes_one_or_more_data_files_and_reads_them(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def answer(parent: QWidget, caption: str, folder: str, filters: str) -> tuple[list[str], str]:
        asked.append(filters)
        return [str(setup.path), str(setup.path)], filters

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", answer)
    dialog = setup.dialog(qtbot)
    dialog.data_files.clear()
    dialog._browse_data_files()
    assert asked == ["Outlook data files (*.pst)"] and dialog._chosen_data_files() == (str(setup.path), str(setup.path))
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    assert dialog.summary.text().startswith("Personal Folders:")


def test_the_window_shows_what_would_be_imported_and_where(qtbot: QtBot, setup: Setup) -> None:
    dialog = setup.dialog(qtbot)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    text = dialog.summary.text()
    assert "Mail: 6 messages, into Local Folders, in the folders as Outlook showed them." in text
    assert "Into Local Folders: 2 appointments, 3 contacts and groups, 1 task." in text
    assert "Stays in the data file (Chrono Mail has no place for it): Notities: 4 notes." in text
    assert dialog.progress.maximum() == 16


def test_import_runs_all_batches_and_reloads_the_window(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importdialog, "BATCH", 5)
    dialog = setup.dialog(qtbot)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    assert not dialog.close_button.isEnabled() and dialog.stop_button.isEnabled() and not dialog.sources.isEnabled()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    text = dialog.summary.text()
    assert "Messages imported: 6; already here: 0; failed: 0." in text and "Appointments imported: 2;" in text and "Groups imported: 1;" in text
    assert setup.messages() == 6 and setup.changed == 1
    assert dialog.close_button.isEnabled() and dialog.sources.isEnabled() and not any(box.isEnabled() for box in dialog.data_file_later)


def test_stop_ends_after_the_batch_and_import_continues(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importdialog, "BATCH", 3)
    dialog = setup.dialog(qtbot)
    dialog.scan()
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    dialog.import_button.click()
    dialog.stop_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import was stopped"))
    assert setup.messages() == 3
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert setup.messages() == 6


def test_a_file_that_cannot_be_imported_says_why_and_import_stays_off(qtbot: QtBot, setup: Setup, tmp_path: Path) -> None:
    offline = tmp_path / "anna.ost"
    offline.write_bytes(b"made up")
    dialog = setup.dialog(qtbot)
    dialog.data_files.setText(str(offline))
    dialog.scan()
    wait(qtbot, lambda: "takes that mail from the server itself" in dialog.summary.text())
    assert not dialog.import_button.isEnabled()


def test_the_data_file_summaries_in_plain_words() -> None:
    scan = ScanResult((FileScan("a.pst", "anna@example.com", place="", events=1, contacts=1, tasks=2, done=1200), FileScan("b.pst", problem="The data file is not there")))
    text = data_file_scan_summary(scan)
    assert "Mail: comes from the account's server, so it is taken from there." in text and "1,200 items done by an earlier import (it continues)." in text
    assert "Into Local Folders: 1 appointment, 1 contact and groups, 2 tasks." in text and "b.pst: The data file is not there" in text
    total = merge_data_files(ImportResult(mail_imported=5, done=5, left=9), ImportResult(mail_imported=4, failures=("Inbox: a message larger than the limit",), done=4))
    assert (total.mail_imported, total.done, total.left) == (9, 9, 0)
    end = data_file_import_summary(total, stopped=False)
    assert end.startswith("The import is done.") and "Messages imported: 9; already here: 0; failed: 0." in end and "Failed: Inbox: a message larger" in end
    assert data_file_import_summary(ImportResult(tags_added=2), stopped=True) == "The import was stopped; Import continues where it stopped.\nTags made: 2."
    assert "Imported earlier, now with their tags: 4 items." in data_file_import_summary(merge_data_files(ImportResult(items_tagged=1), ImportResult(items_tagged=3)), stopped=False)


def test_a_chosen_path_holding_the_separator_stays_one_file(qtbot: QtBot, setup: Setup, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    odd = tmp_path / "a; b.pst"

    def answer(parent: QWidget, caption: str, folder: str, filters: str) -> tuple[list[str], str]:
        return [str(odd), str(setup.path)], filters

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", answer)
    dialog = setup.dialog(qtbot)
    dialog._browse_data_files()
    assert dialog._chosen_data_files() == (str(odd), str(setup.path))
    dialog.data_files.setText(str(odd))  # typed: one file
    assert dialog._chosen_data_files() == (str(odd),)
    wait(qtbot, lambda: setup.backend._pending == {})


def test_a_readable_profile_offers_its_accounts_rules_signatures_and_data_files(
    qtbot: QtBot, tmp_path: Path, files: dict[Path, FakeFile], machine: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixtures
) -> None:
    monkeypatch.setattr(importdialog, "PROFILE_HERE", True)
    pst = data_file(files, machine, "anna.pst", "anna@example.com")
    backend = Backend(make_engine(tmp_path / "profile"))
    dialog = ImportDialog(backend, lambda: None)
    qtbot.addWidget(dialog)
    dialog.sources.setCurrentItem(dialog.data_file_item)
    wait(qtbot, lambda: dialog.import_button.isEnabled())
    assert dialog.data_files.text() == str(pst) and dialog.summary.text().startswith("Accounts: anna@example.com (POP3)")
    assert all(box.isEnabled() and box.isChecked() and box.toolTip() == "" for box in dialog.data_file_later)
    backend.wait_for_done(5000)
