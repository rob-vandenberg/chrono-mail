from collections.abc import Callable

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import mainwindow
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.currentview import CurrentView
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait, world  # noqa: F401 - world is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_ui_small_faults.py 2.4.332"

# --- Version history ----------------------------------------------------
# v2.4.332: Initial (the Windows computer's W9, W3, W2): New while no folder is selected; Exit asks about a running backup last; Restore a
#           backup… in the first-account window


def test_new_opens_a_message_window_while_no_folder_is_selected(world: World, qtbot: QtBot) -> None:
    """W9: New did nothing, without a word, just after the start and after a restore (no folder selected yet)."""
    started(qtbot, world)
    world.window.mail_state.view = CurrentView()
    world.window.composing.new_message()
    assert len(world.window.composing.composers) == 1
    world.window.composing.composers[0].close()


@pytest.mark.parametrize("outbox_answer", [False, True])
def test_exit_asks_about_a_running_backup_last(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, outbox_answer: bool) -> None:
    """W3: "The backup stops and no file is kept" was asked first; a Cancel on the Outbox question after it left the backup running."""
    started(qtbot, world)
    asked: list[str] = []
    monkeypatch.setattr(world.window.composing, "close_all", lambda: asked.append("windows") or True)

    def outbox_question(then: Callable[[], None]) -> bool:
        asked.append("outbox")
        return outbox_answer

    monkeypatch.setattr(world.window.notices, "before_quit", outbox_question)

    def backup_question(parent: QWidget, backing_up: bool, restoring: bool) -> bool:
        asked.append("backup")
        return False  # No: the program stays

    monkeypatch.setattr(mainwindow, "may_quit", backup_question)
    world.window.quit_program()
    assert asked == (["windows", "outbox", "backup"] if outbox_answer else ["windows", "outbox"]) and world.window.isVisible()


def test_the_first_account_window_offers_restore_a_backup(world: World, qtbot: QtBot) -> None:
    """W2: a person moving to a new computer had to press Cancel and find Menu > File > Restore…."""
    chosen: list[str] = []
    dialog = AccountDialog(world.backend, first_account=True, parent=world.window, restore=lambda: chosen.append("restore"))
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.restore_button.isVisible()
    dialog.restore_button.click()
    wait(qtbot, lambda: chosen == ["restore"])
    assert dialog.result() == AccountDialog.DialogCode.Rejected and not dialog.isVisible()
    plain = AccountDialog(world.backend, parent=world.window, restore=lambda: chosen.append("again"))
    qtbot.addWidget(plain)
    plain.show()
    assert not plain.restore_button.isVisible()  # only in the window of the first account
