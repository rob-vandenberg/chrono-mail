from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import green_style, red_style

# --- Version ------------------------------------------------------------
__version__ = "restorekeydialog.py 0.0.51"

# --- Version history ----------------------------------------------------
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.15: Initial dialog for restoring the encryption key



class RestoreKeyDialog(QDialog):
    """Asks for the key when the credential store does not have it (a new computer, a reset wallet,
    a locked wallet). The key comes back from the recovery code or from the recovery file."""

    def __init__(self, backend: Backend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.setWindowTitle("The encryption key is missing")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Your mail is stored encrypted on this computer, but the key that opens it is not in this computer's credential store. "
            "Perhaps the wallet is locked or was reset, or this is a new computer.<br><br>"
            "<b>Your mail is safe.</b> It can be read again as soon as the key is back. Enter the recovery code you saved when the "
            "account was created, or use the recovery file with its passphrase. If the wallet was only locked, unlock it and choose "
            "<i>Try again</i>.",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.code = QPlainTextEdit(self)
        self.code.setPlaceholderText("Recovery code, for example ABCD-EFGH-…")
        self.code.setFixedHeight(70)
        layout.addWidget(self.code)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        self.restore_code_button = QPushButton("Restore with this code", self)
        self.restore_code_button.setDefault(True)
        self.file_button = QPushButton("Use a recovery file…", self)
        self.retry_button = QPushButton("Try again", self)
        self.cancel_button = QPushButton("Not now", self)
        for button in (self.restore_code_button, self.file_button, self.retry_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.restore_code_button.clicked.connect(self._restore_with_code)
        self.file_button.clicked.connect(self._restore_with_file)
        self.retry_button.clicked.connect(self._try_again)
        self.cancel_button.clicked.connect(self.reject)

    def _busy(self, busy: bool) -> None:
        for button in (self.restore_code_button, self.file_button, self.retry_button):
            button.setEnabled(not busy)

    def _show(self, text: str, style: str) -> None:
        self.result_label.setStyleSheet(style)
        self.result_label.setText(text)

    def _run(self, command: str, values: dict[str, object]) -> None:
        self._busy(True)
        self._show("Working…", "")

        def done(result: object) -> None:
            self._busy(False)
            if getattr(result, "in_credential_store"):
                self._show("The key is back. Your mail can be read again.", green_style())
                self.accept()
            else:
                self._show("The key is still not in the credential store. Unlock the wallet and try again.", red_style())

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        self._backend.call(command, values, done, failed, owner=self)

    def _restore_with_code(self) -> None:
        text = self.code.toPlainText().strip()
        if not text:
            self._show("Type or paste the recovery code first.", red_style())
            return
        self._run("key.restore-code", {"code": text})

    def _restore_with_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the recovery file")
        if not path:
            return
        passphrase, accepted = QInputDialog.getText(self, "Recovery file", "The passphrase of the recovery file:", QLineEdit.EchoMode.Password)
        if not accepted or not passphrase:
            return
        self._run("key.restore-file", {"path": path, "passphrase": passphrase})

    def _try_again(self) -> None:
        self._busy(True)
        self._show("Checking…", "")

        def done(result: object) -> None:
            self._busy(False)
            if getattr(result, "in_credential_store"):
                self.accept()
            else:
                self._show("The key is still not available in the credential store.", red_style())

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        self._backend.call("key.status", {}, done, failed, owner=self)
