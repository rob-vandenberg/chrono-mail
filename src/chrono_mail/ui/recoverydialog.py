from pathlib import Path

from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import (
    KEY_CODE_STORED, KEY_EXPORT_FILE, KEY_RECOVERY_CODE, KeyExportParameters, KeyExportResult, KeyStatus, NoParameters, RecoveryCode,
)
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "recoverydialog.py 3.5.346"

# --- Version history ----------------------------------------------------
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
        self.setWindowTitle("Your recovery code")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Your mail is stored encrypted on this computer. The key that opens it is kept in this computer's "
            "password store. <b>If that key is lost, your mail can no longer be read.</b><br><br>"
            "Keep this recovery code somewhere safe, away from this computer (for example printed, or in a password manager). "
            "Anyone who has the code can read your mail, so treat it like a password.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if restoring:
            layout.addWidget(QLabel(RESTORING_TEXT, self))
        self.code = QPlainTextEdit(self)
        self.code.setReadOnly(True)
        self.code.setFixedHeight(70)
        monospace = QFont("monospace")
        monospace.setStyleHint(QFont.StyleHint.Monospace)
        monospace.setPointSizeF(monospace.pointSizeF() * 1.2)
        self.code.setFont(monospace)
        self.code.setPlainText("Loading…")
        layout.addWidget(self.code)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy", self)
        self.file_button = QPushButton("Save a recovery file…", self)
        buttons.addWidget(self.copy_button)
        if restoring:
            self.file_button.setVisible(False)  # a third secret during a restore would confuse: the file can be saved later from the menu
        else:
            buttons.addWidget(self.file_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.confirm = QCheckBox("I have stored the recovery code (or a recovery file) in a safe place", self)
        layout.addWidget(self.confirm)
        done_row = QHBoxLayout()
        done_row.addStretch(1)
        self.done_button = QPushButton("Done", self)
        self.done_button.setEnabled(False)
        done_row.addWidget(self.done_button)
        layout.addLayout(done_row)
        self.confirm.toggled.connect(self.done_button.setEnabled)
        self.done_button.clicked.connect(self._stored)
        self.copy_button.clicked.connect(self._copy)
        self.file_button.clicked.connect(self._save_file)
        backend.ask(KEY_RECOVERY_CODE, NoParameters(), self._show_code, self._failed, owner=self)

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

    def _failed(self, error: ChronoMailError) -> None:
        self.code.setPlainText("(the code could not be shown)")
        QMessageBox.warning(self, "Recovery code", str(error))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code.toPlainText())
        self.copy_button.setText("Copied")
        QTimer.singleShot(COPIED_MILLISECONDS, self._copy_again)

    def _copy_again(self) -> None:
        try:
            self.copy_button.setText("Copy")
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
        passphrase, ok = QInputDialog.getText(self, "Protect the recovery file", "Choose a passphrase for the file:", QLineEdit.EchoMode.Password)
        if not ok or not passphrase:
            return
        again, ok = QInputDialog.getText(self, "Protect the recovery file", "Type the passphrase again:", QLineEdit.EchoMode.Password)
        if not ok or again != passphrase:
            QMessageBox.warning(self, "Recovery file", "The two passphrases are not the same. Nothing was saved.")
            return

        def saved(result: KeyExportResult) -> None:
            QMessageBox.information(self, "Recovery file", f"The recovery file was saved as:\n{result.path}\n\nYou need the passphrase to use it. Keep both safe, but not together.")

        self._backend.ask(KEY_EXPORT_FILE, KeyExportParameters(path, passphrase, overwrite=True), saved, self._failed_save, owner=self)

    def _failed_save(self, error: ChronoMailError) -> None:
        QMessageBox.warning(self, "Recovery file", str(error))
