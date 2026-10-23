from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountedit import AccountEditDialog
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_junkfilter.py 2.1.310"

# --- Version history ----------------------------------------------------
# v2.1.310: the first learning says what it does: the bar's hover text while it runs, the status line when it is done
# v2.1.308: Initial: Edit account > General > Filter junk mail (D-238)


def test_filter_junk_mail_is_on_by_default_and_saved_when_changed(world: World, qtbot: QtBot) -> None:
    edit = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(edit)
    edit.show()
    assert edit.junk_filter.text() == "Filter junk mail" and edit.junk_filter.isChecked() and edit.junk_filter.toolTip()
    assert edit._values() is None  # noqa: SLF001 - nothing changed yet
    edit.junk_filter.setChecked(False)
    edit.save_button.click()
    wait(qtbot, lambda: edit.result() == QDialog.DialogCode.Accepted)
    assert world.call("account.list")[0].junk_filter is False
    again = AccountEditDialog(world.backend, world.call("account.list")[0])
    qtbot.addWidget(again)
    assert not again.junk_filter.isChecked()


def test_the_first_learning_says_what_it_does_while_it_runs_and_when_it_is_done(world: World, qtbot: QtBot) -> None:
    status = world.window.report
    world.window.checking.learn_existing_junk()
    assert status.progress_bar.toolTip() == "Junk filter: learning from your mail"  # set before the job can answer
    wait(qtbot, lambda: status.label.text().startswith("Junk filter: learned from "))
    assert status.progress_bar.toolTip() == "Show the Operations window"
