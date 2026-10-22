import html

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
from chrono_mail.engine.commands_accounts import KEY_RESTORE_CODE, KEY_RESTORE_FILE, KEY_STATUS, KeyRestoreCodeParameters, KeyRestoreFileParameters, KeyStatus, NoParameters
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.colours import green_style, red_style

# --- Version ------------------------------------------------------------
__version__ = "restorekeydialog.py 1.9.271"

# --- Version history ----------------------------------------------------
# v1.9.271: R-19: restoring and checking the key are typed calls
# v1.1.197: The dialog says why the key could not be had (the store's own reason), and tells a wallet that could not be opened
#           apart from a key that is not there; Try again shows the new reason when it fails again
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.15: Initial dialog for restoring the encryption key



MISSING_TITLE: str = "The encryption key is missing"
LOCKED_TITLE: str = "The wallet could not be opened"
_SAFE: str = "<b>Your mail is safe.</b> It can be read again as soon as the key is back."
_RECOVER: str = "Enter the recovery code you saved when the account was created, or use the recovery file with its passphrase."


def explanation_for(status: KeyStatus | None) -> tuple[str, str]:
    """The title and the text of the dialog: why the key could not be had, in the credential store's own words, and what to do.
    None: the person opened the dialog from the menu (nothing is known about why)."""
    said = f"<br><br>The wallet said: <i>{html.escape(status.problem)}</i>" if status is not None and status.problem else ""
    if status is not None and not status.store_answered:
        return LOCKED_TITLE, (
            "Your mail is stored encrypted on this computer. The key that opens it is kept in this computer's wallet (the "
            f"credential store), but the wallet could not be asked for it.{said}<br><br>{_SAFE} Most often the wallet is locked "
            "or has not started yet: unlock it and choose <i>Try again</i>. If the key really is gone, enter the recovery code you "
            "saved when the account was created, or use the recovery file with its passphrase."
        )
    if status is not None:
        return MISSING_TITLE, (
            "Your mail is stored encrypted on this computer, but the wallet (the credential store) answered that it does not "
            f"have the key that opens it. Perhaps the wallet was reset, or this is a new computer.{said}<br><br>{_SAFE} {_RECOVER} "
            "If the wallet was only locked, unlock it and choose <i>Try again</i>."
        )
    return MISSING_TITLE, (
        "Your mail is stored encrypted on this computer, but the key that opens it is not in this computer's credential store. "
        f"Perhaps the wallet is locked or was reset, or this is a new computer.<br><br>{_SAFE} {_RECOVER} If the wallet was only "
        "locked, unlock it and choose <i>Try again</i>."
    )


class RestoreKeyDialog(QDialog):
    """Asks for the key when the credential store does not have it (a new computer, a reset wallet,
    a locked wallet), and says why. The key comes back from the recovery code or from the recovery file."""

    def __init__(self, backend: Backend, parent: QWidget | None = None, status: KeyStatus | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.explanation = QLabel("", self)
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)
        self.show_status(status)
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

    def show_status(self, status: KeyStatus | None) -> None:
        """Say why the key could not be had (the title and the text follow the newest answer)."""
        title, text = explanation_for(status)
        self.setWindowTitle(title)
        self.explanation.setText(text)

    def _busy(self, busy: bool) -> None:
        for button in (self.restore_code_button, self.file_button, self.retry_button):
            button.setEnabled(not busy)

    def _show(self, text: str, style: str) -> None:
        self.result_label.setStyleSheet(style)
        self.result_label.setText(text)

    def _run(self, step: AnyStep[KeyStatus]) -> None:
        self._busy(True)
        self._show("Working…", "")

        def done(result: KeyStatus) -> None:
            self._busy(False)
            if result.in_credential_store:
                self._show("The key is back. Your mail can be read again.", green_style())
                self.accept()
            else:
                self._show("The key is still not in the credential store. Unlock the wallet and try again.", red_style())

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        step.start(self._backend, done, failed, owner=self)

    def _restore_with_code(self) -> None:
        text = self.code.toPlainText().strip()
        if not text:
            self._show("Type or paste the recovery code first.", red_style())
            return
        self._run(Step(KEY_RESTORE_CODE, KeyRestoreCodeParameters(text)))

    def _restore_with_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the recovery file")
        if not path:
            return
        passphrase, accepted = QInputDialog.getText(self, "Recovery file", "The passphrase of the recovery file:", QLineEdit.EchoMode.Password)
        if not accepted or not passphrase:
            return
        self._run(Step(KEY_RESTORE_FILE, KeyRestoreFileParameters(path, passphrase)))

    def _try_again(self) -> None:
        self._busy(True)
        self._show("Checking…", "")

        def done(result: KeyStatus) -> None:
            self._busy(False)
            if result.in_credential_store:
                self.accept()
            else:
                self.show_status(result)  # the newest reason
                self._show("The key is still not available: see the reason above.", red_style())

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        self._backend.ask(KEY_STATUS, NoParameters(), done, failed, owner=self)
