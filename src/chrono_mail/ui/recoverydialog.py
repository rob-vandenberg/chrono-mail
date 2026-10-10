from pathlib import Path

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
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "recoverydialog.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial key recovery dialog


class RecoveryDialog(QDialog):
    """Shows the key recovery code. The key that protects your mail lives only in this computer's
    credential store; without a backup, losing it means losing the mail."""

    def __init__(self, backend: Backend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.setWindowTitle("Back up your encryption key")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Your mail is stored encrypted on this computer. The key that opens it is kept in this computer's "
            "credential store. <b>If that key is lost, your mail can no longer be read.</b><br><br>"
            "Keep this recovery code somewhere safe, away from this computer (for example printed, or in a password manager). "
            "Anyone who has the code can read your mail, so treat it like a password.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
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
        self.copy_button = QPushButton("Copy the code", self)
        self.file_button = QPushButton("Save a recovery file…", self)
        buttons.addWidget(self.copy_button)
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
        self.done_button.clicked.connect(self.accept)
        self.copy_button.clicked.connect(self._copy)
        self.file_button.clicked.connect(self._save_file)
        backend.call("key.recovery-code", {}, self._show_code, self._failed, owner=self)

    def _show_code(self, result: object) -> None:
        self.code.setPlainText(str(getattr(result, "code")))

    def _failed(self, error: ChronoMailError) -> None:
        self.code.setPlainText("(the code could not be shown)")
        QMessageBox.warning(self, "Recovery code", str(error))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code.toPlainText())

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

        def saved(result: object) -> None:
            QMessageBox.information(self, "Recovery file", f"The recovery file was saved as:\n{getattr(result, 'path')}\n\nYou need the passphrase to use it. Keep both safe, but not together.")

        self._backend.call("key.export-file", {"path": path, "passphrase": passphrase, "overwrite": True}, saved, self._failed_save, owner=self)

    def _failed_save(self, error: ChronoMailError) -> None:
        QMessageBox.warning(self, "Recovery file", str(error))
