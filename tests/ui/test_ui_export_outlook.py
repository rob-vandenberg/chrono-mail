import sys
from pathlib import Path

from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.exportdialog import ExportDialog
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.ui.world import wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_export_outlook.py 4.2.386"

# --- Version history ----------------------------------------------------
# v4.2.386: Initial test: the Export window lists the accounts, greys Rules off Windows with why, and writes the data file of Local Folders


def test_the_export_window_writes_the_chosen_accounts(qtbot: QtBot, tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    ensure_local_pim(engine)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(mailbox.store.list_folders()[0].path, raw_message(subject="Kept"), frozenset())
    backend = Backend(engine)
    dialog = ExportDialog(backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.account_list.count() == 1)
    assert dialog.rules.isEnabled() is (sys.platform == "win32") and dialog.rules.isVisibleTo(dialog)  # greyed, never hidden
    assert sys.platform == "win32" or dialog.rules.toolTip()
    assert not dialog.export_button.isEnabled()
    out = tmp_path / "out"
    out.mkdir()
    dialog.folder.setText(str(out))
    dialog.accounts.setChecked(False)
    assert dialog.export_button.isEnabled()
    dialog.export_button.click()
    wait(qtbot, lambda: dialog.summary.text().startswith("The export is done"))
    assert (out / "Local Folders.pst").is_file() and "1 message" in dialog.summary.text()
    assert dialog.export_button.isEnabled() and not dialog.stop_button.isEnabled()
    backend.wait_for_done(5000)
