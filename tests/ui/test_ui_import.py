from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_emclient import ImportResult, ScanResult
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui import importdialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.importdialog import MENU_TEXT, ImportDialog, import_summary, merge, scan_summary
from tests.enginefactory import make_engine
from tests.test_import_emclient import Exports, make_exports
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_import.py 0.5.132"

# --- Version history ----------------------------------------------------
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
    dialog.import_button.click()
    dialog.stop_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import was stopped"))
    assert setup.messages() == 3
    dialog.import_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The import is done."))
    assert setup.messages() == 7


def test_the_summaries_in_plain_words() -> None:
    scan = ScanResult(3, 1, 1, ("a@example.com",), 2, ("Script: it is written as a script",), 4, 1200, 200, 1100, 5)
    text = scan_summary(scan)
    assert "Accounts: 3, of which 1 already here (not made again); 1 not POP3 (not imported)." in text
    assert "Passwords that cannot be read (you type them later): a@example.com." in text
    assert "Mail: 1,200 messages in 4 folders, 200 of them done by an earlier import (it continues)." in text
    assert scan_summary(ScanResult(0, 0, 0, (), 0, (), 0, 0, 0, 0, 0)) == "Nothing to import was found."
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
