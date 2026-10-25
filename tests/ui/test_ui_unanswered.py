from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from tests.ui import conftest

# --- Version ------------------------------------------------------------
__version__ = "test_ui_unanswered.py 3.5.347"

# --- Version history ----------------------------------------------------
# v3.5.347: Initial: a window or picker no test answers comes back at once as Cancel and is noted (AGENTS.md 4.5.5.2)


def test_a_window_no_test_answers_is_cancelled_at_once_and_noted(qtbot: QtBot) -> None:
    """A restore's recovery code window waited for a click that never came: the run hung (3.5.346). Now it is answered with Cancel and
    noted, so the test that caused it fails at once."""
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setWindowTitle("Your recovery code")
    box = QMessageBox(QMessageBox.Icon.Question, "Delete", "Which ones?")
    qtbot.addWidget(box)
    assert dialog.exec() == QDialog.DialogCode.Rejected.value and not dialog.isVisible()
    box.exec()
    assert QInputDialog.getText(None, "Backup password", "Password:") == ("", False)
    assert QFileDialog.getOpenFileName(None, "Restore") == ("", "")
    noted = list(conftest._unanswered.shown)  # noqa: SLF001 - what the guard noted
    conftest._unanswered.shown.clear()  # noqa: SLF001 - this test expects them: it must not fail for them
    assert noted == ["window 'Your recovery code' (QDialog)", "window 'Delete' (QMessageBox)", "picker QInputDialog.getText", "picker QFileDialog.getOpenFileName"]
