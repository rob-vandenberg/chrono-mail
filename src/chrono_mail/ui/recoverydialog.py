from pathlib import Path

from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import (
    KEY_CODE_STORED, KEY_EXPORT_FILE, KEY_RECOVERY_CODE, KeyExportParameters, KeyExportResult, KeyStatus, NoParameters, RecoveryCode,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.forms import part
from chrono_mail.ui.passwordfield import PasswordBox

# --- Version ------------------------------------------------------------
__version__ = "recoverydialog.py 4.2.437"

# --- Version history ----------------------------------------------------
# v4.2.437: the recovery file's passphrase is asked in the password box, with the eye (UR-03, D-296)
# v4.0.376: built from its Qt Designer form (ui/forms/recovery_code.ui, D-276); Copy comes back with the form's text; its smallest width follows the size factor
# v3.6.350: a code that could not be shown can be asked again with Try again (during a restore "Stop the restore" was the only way on)
# v3.6.349: the tick and Copy work only once the code is shown: a code never seen is never noted as stored (RV-210); a saved recovery file allows the tick too
# v3.5.346: Done tells the engine the code is stored (key.code-stored); until then it is shown again before a restore and after an account is added (RV-03)
# v2.4.324: "Your recovery code"; "this computer's password store"; Copy says "Copied" for 2 s; closing without the tick asks first;
#           restoring: one line that the backup's password comes next, and no recovery file (one secret at a time, D-244)
# v1.9.271: R-19: the recovery code and file are typed calls
# v0.0.13: Initial key recovery dialog


RESTORING_TEXT: str = "This code is for this computer. The password of your backup is asked for in the next step."
CLOSE_QUESTION: str = "You have not ticked that the recovery code is stored in a safe place. Close anyway?"
STOP_RESTORE_QUESTION: str = "The restore goes on only after the recovery code is stored in a safe place. Stop the restore?"
COPIED_MILLISECONDS: int = 2000


class RecoveryDialog(QDialog):
    """Shows the key recovery code. The key that protects your mail lives only in this computer's
    credential store; without a backup, losing it means losing the mail."""

    def __init__(self, backend: Backend, parent: QWidget | None = None, restoring: bool = False) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._restoring: bool = restoring  # shown before a restore: the backup's own password is asked for in the next step
        form = forms.fill(self, "recovery_code")  # D-276
        part(form, QLabel, "restoring").setVisible(restoring)
        self.code: QPlainTextEdit = part(form, QPlainTextEdit, "code")
        self.code.setFixedHeight(70)
        monospace = QFont("monospace")
        monospace.setStyleHint(QFont.StyleHint.Monospace)
        monospace.setPointSizeF(monospace.pointSizeF() * 1.2)
        self.code.setFont(monospace)
        self.code.setPlainText("Loading…")
        self.copy_button: QPushButton = part(form, QPushButton, "copy")
        self._copy_text: str = self.copy_button.text()  # the form's text, back after "Copied"
        self.file_button: QPushButton = part(form, QPushButton, "saveFile")
        self.file_button.setVisible(not restoring)  # a third secret during a restore would confuse: the file can be saved later from the menu
        self.retry_button: QPushButton = part(form, QPushButton, "tryAgain")  # greyed until the code could not be shown: then it is asked again
        self.retry_button.setEnabled(False)
        self.confirm: QCheckBox = part(form, QCheckBox, "confirm")
        self.confirm.setEnabled(False)  # only once the code is there to be stored (RV-210)
        self.copy_button.setEnabled(False)
        self.done_button: QPushButton = part(form, QPushButton, "done")
        self.done_button.setEnabled(False)
        forms.smallest(self, 560)  # its texts are never cut off, also at the largest size
        self.confirm.toggled.connect(self.done_button.setEnabled)
        self.done_button.clicked.connect(self._stored)
        self.copy_button.clicked.connect(self._copy)
        self.file_button.clicked.connect(self._save_file)
        self.retry_button.clicked.connect(self._ask_code)
        self._ask_code()

    def _ask_code(self) -> None:
        """Ask the engine for the code; after a failure (a locked or slow password store) the person asks again with Try again."""
        self.retry_button.setEnabled(False)
        self.code.setPlainText("Loading…")
        self._backend.ask(KEY_RECOVERY_CODE, NoParameters(), self._show_code, self._failed, owner=self)

    def _stored(self) -> None:
        """Done with the tick: the engine notes that the code is stored, so it is not shown again before a restore (RV-03)."""
        self.done_button.setEnabled(False)

        def noted(_status: KeyStatus) -> None:
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.done_button.setEnabled(self.confirm.isChecked())
            QMessageBox.warning(self, "Recovery code", str(error))

        self._backend.ask(KEY_CODE_STORED, NoParameters(), noted, failed, owner=self)

    def _show_code(self, result: RecoveryCode) -> None:
        self.code.setPlainText(result.code)
        self.confirm.setEnabled(True)
        self.copy_button.setEnabled(True)

    def _failed(self, error: ChronoMailError) -> None:
        self.code.setPlainText("(the code could not be shown)")
        self.retry_button.setEnabled(True)
        QMessageBox.warning(self, "Recovery code", str(error))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code.toPlainText())
        self.copy_button.setText("Copied")
        QTimer.singleShot(COPIED_MILLISECONDS, self._copy_again)

    def _copy_again(self) -> None:
        try:
            self.copy_button.setText(self._copy_text)
        except RuntimeError:
            pass  # the window is gone

    @override
    def reject(self) -> None:
        """Escape or the window's close button: without the tick it asks first; during a restore, stopping it."""
        if self.isVisible() and not self.confirm.isChecked():
            question = STOP_RESTORE_QUESTION if self._restoring else CLOSE_QUESTION
            if QMessageBox.question(self, self.windowTitle(), question) != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def _save_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the recovery file", str(Path.home() / "chrono-mail-recovery.key"))
        if not path:
            return
        passphrase, ok = PasswordBox.ask(self, "Protect the recovery file", "Choose a passphrase for the file:")
        if not ok or not passphrase:
            return
        again, ok = PasswordBox.ask(self, "Protect the recovery file", "Type the passphrase again:")
        if not ok or again != passphrase:
            QMessageBox.warning(self, "Recovery file", "The two passphrases are not the same. Nothing was saved.")
            return

        def saved(result: KeyExportResult) -> None:
            self.confirm.setEnabled(True)  # a recovery file is stored: the tick says "or a recovery file" (the review of RV-210)
            QMessageBox.information(self, "Recovery file", f"The recovery file was saved as:\n{result.path}\n\nYou need the passphrase to use it. Keep both safe, but not together.")

        self._backend.ask(KEY_EXPORT_FILE, KeyExportParameters(path, passphrase, overwrite=True), saved, self._failed_save, owner=self)

    def _failed_save(self, error: ChronoMailError) -> None:
        QMessageBox.warning(self, "Recovery file", str(error))
