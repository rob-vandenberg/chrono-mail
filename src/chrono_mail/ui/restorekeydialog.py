import html
from typing import override

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
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
    KEY_RESTORE_CODE, KEY_RESTORE_FILE, KEY_START_WITHOUT_KEPT, KEY_STATUS, LOCAL_TAKE_BACK, KeptSetAside, KeyRestoreCodeParameters, KeyRestoreFileParameters,
    KeyStatus, NoParameters,
)
from chrono_mail.ui.backend import AnyStep, Backend, Step
from chrono_mail.ui.colours import green_style, red_style
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "restorekeydialog.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: mail of Local Folders kept on this computer whose settings were lost: the dialog says so, and Start without this mail sets it
#           aside (never deleted) when its key is gone for good, so a new key can be made (before, a new key made it unreadable for good);
#           when its older key is here and only its take-back failed, Start without says that Try again can still bring the mail back
# v1.9.271: R-19: restoring and checking the key are typed calls
# v1.1.197: The dialog says why the key could not be had (the store's own reason), and tells a wallet that could not be opened
#           apart from a key that is not there; Try again shows the new reason when it fails again
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.15: Initial dialog for restoring the encryption key



MISSING_TITLE: str = "The encryption key is missing"
LOCKED_TITLE: str = "The wallet could not be opened"
OTHER_KEY_TITLE: str = "Local Folders cannot be opened"
TAKE_BACK_TITLE: str = "Taking Local Folders back"
TAKEN_BACK: str = "Local Folders is back, with its mail, calendars, contacts and tasks."
_SAFE: str = "<b>Your mail is safe.</b> It can be read again as soon as the key is back."
_RECOVER: str = "Enter the recovery code you saved when the account was created, or use the recovery file with its passphrase."


START_WITHOUT_QUESTION: str = (
    "The mail, calendars, contacts and tasks of Local Folders on this computer can only be opened with their recovery code or recovery "
    "file.\n\nWithout them, Local Folders starts again, empty. They are not deleted: they stay on this computer in a folder of their own, "
    "but Chrono Mail cannot open them.\n\nStart without them?"
)
START_WITHOUT_KEY_HERE_QUESTION: str = (
    "The older key of Local Folders is on this computer, so its mail, calendars, contacts and tasks can still be taken back: make room on "
    "the disk, or solve what the window says, and choose Try again.\n\nWithout them, Local Folders starts again, empty. They are not "
    "deleted: they stay on this computer in a folder of their own, but Chrono Mail does not open them again.\n\nStart without them?"
)
START_WITHOUT_HINT: str = "Only for Local Folders kept on this computer whose key is gone and whose recovery code is lost"
KEY_BACK: str = "The encryption key is back."
NOT_TAKEN_BACK: str = "Local Folders is not taken back yet; the rest of your mail is open. Use File > Use a recovery code to try again."
STILL_MISSING: str = "The encryption key is missing, so your mail cannot be read yet. Use File > Use a recovery code."
OTHER_STILL_MISSING: str = "Local Folders cannot be opened yet: its older key is missing. Use File > Use a recovery code."


def explanation_for(status: KeyStatus | None) -> tuple[str, str]:
    """The title and the text of the dialog: why the key could not be had, in the credential store's own words, and what to do.
    None: the person opened the dialog from the menu (nothing is known about why)."""
    said = f"<br><br>The wallet said: <i>{html.escape(status.problem)}</i>" if status is not None and status.problem else ""
    if status is not None and status.take_back and status.in_credential_store:
        return TAKE_BACK_TITLE, (
            "The mail, calendars, contacts and tasks of Local Folders were written with an older key of this computer. They are written again "
            "with this computer's key now, so Chrono Mail can open them; for much mail this takes a while. The old files are kept."
        )
    if status is not None and status.kept_other and status.in_credential_store:  # this computer's key is there; only Local Folders' is not
        return OTHER_KEY_TITLE, (
            "The mail, calendars, contacts and tasks of Local Folders are still on this computer, but they were written with an older key of "
            "this computer, which the wallet no longer has. Your other mail is not affected.<br><br>Enter the recovery code of that older key, "
            "or use its recovery file, to bring them back. If you no longer have either, choose <i>Start without this mail</i>."
        )
    if status is not None and status.kept and status.store_answered:
        return MISSING_TITLE, (
            "The mail, calendars, contacts and tasks of Local Folders are still on this computer, stored encrypted, but the settings that "
            f"named their key were lost or reset, and the wallet (the credential store) does not have the key.{said}<br><br>{_SAFE} {_RECOVER} "
            "If you no longer have either, choose <i>Start without this mail</i>."
        )
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


def worth_telling(status: KeyStatus) -> KeyStatus | None:
    """The status the window explains when it is opened from the menu: a key that is missing, or mail kept here that cannot be opened; None
    when the key is there (nothing to say why)."""
    return status if status.kept_other or status.take_back or (status.key_id is not None and not status.in_credential_store) else None


class RestoreKeyDialog(QDialog):
    """Asks for the key when the credential store does not have it (a new computer, a reset wallet,
    a locked wallet), and says why. The key comes back from the recovery code or from the recovery file."""

    def __init__(self, backend: Backend, parent: QWidget | None = None, status: KeyStatus | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._kept: bool = False  # the key is that of Local Folders' mail kept here, and the wallet answered that it does not have it
        self._other: bool = False  # this computer's key is there, but Local Folders was written with an older one the wallet lacks
        self._key_here: bool = False  # Local Folders' older key is in the wallet: only its take-back failed (no room on the disk)
        self.declined: str = ""
        self.only_local_folders: bool = False
        self._working: bool = False  # a take-back runs: the window stays until it ends
        self.start_without_button = QPushButton("Start without this mail", self)  # made first: show_status greys it or not
        self.start_without_button.setToolTip(START_WITHOUT_HINT)
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
        self.started_without: str = ""  # where the mail kept here was set aside, once the person chose to start without it
        self.outcome: str = KEY_BACK  # for the status line once the window is accepted
        for button in (self.restore_code_button, self.file_button, self.retry_button, self.start_without_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.restore_code_button.clicked.connect(self._restore_with_code)
        self.file_button.clicked.connect(self._restore_with_file)
        self.retry_button.clicked.connect(self._try_again)
        self.start_without_button.clicked.connect(self._start_without)
        self.cancel_button.clicked.connect(self.reject)
        if status is not None and status.take_back and status.in_credential_store:
            self._take_back()  # nothing to ask: the older key is here

    def show_status(self, status: KeyStatus | None) -> None:
        """Say why the key could not be had (the title and the text follow the newest answer)."""
        self._other = status is not None and status.kept_other and status.in_credential_store  # only Local Folders' older key is missing
        back = status is not None and status.take_back and status.in_credential_store  # Local Folders' older key is here: taken back
        self._key_here = back  # Start without asks knowing the mail could still come back
        self.only_local_folders = self._other or back  # this computer's own key is there: the rest of the mail opens whatever happens here
        self._kept = status is not None and (  # only a wallet that answered "not there" is gone for good; a take-back that cannot be done
            (status.kept and status.store_answered and not status.in_credential_store) or self._other or back  # (no room) may start without
        )
        self.declined = (  # the status line when closed without it; nothing when nothing was missing (opened from the menu with the key here)
            OTHER_STILL_MISSING if self._other else NOT_TAKEN_BACK if back else STILL_MISSING if status is not None else ""
        )
        title, text = explanation_for(status)
        self.setWindowTitle(title)
        self.explanation.setText(text)
        self.start_without_button.setEnabled(self._kept)

    def tell(self, report: StatusReport, accepted: bool) -> None:
        """The status line once the window is closed: what came of it, or what is still missing; nothing when nothing was."""
        text = self.outcome if accepted else self.declined
        if text:
            report.show(text, error=not accepted)

    def _busy(self, busy: bool) -> None:
        self._working = busy
        for button in (self.restore_code_button, self.file_button, self.retry_button, self.cancel_button):
            button.setEnabled(not busy)
        self.start_without_button.setEnabled(not busy and self._kept)  # greyed, never hidden, when it does not apply

    @override
    def reject(self) -> None:
        """Not now, Escape or the window's close button: not while work runs (its result would be lost, the start would not go on)."""
        if not self._working:
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._working:
            event.ignore()
            return
        super().closeEvent(event)

    def _show(self, text: str, style: str) -> None:
        self.result_label.setStyleSheet(style)
        self.result_label.setText(text)

    def _run(self, step: AnyStep[KeyStatus]) -> None:
        self._busy(True)
        self._show("Working…", "")

        def done(result: KeyStatus) -> None:
            self._busy(False)
            if result.in_credential_store and not result.kept_other and not result.take_back:
                self._show("The key is back. Your mail can be read again.", green_style())
                self.accept()
            elif result.take_back and result.in_credential_store:  # this computer's key is back; Local Folders follows with its older one
                self.show_status(result)
                self._take_back()
            elif result.in_credential_store:  # this computer's key is there; the code given was not Local Folders' older one
                self.show_status(result)
                self._show("Local Folders' older key is still missing: enter the recovery code of that key.", red_style())
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

    def _start_without(self) -> None:
        """Local Folders kept here cannot be opened without its key: after a question it is set aside (never deleted) and starts again."""
        question = START_WITHOUT_KEY_HERE_QUESTION if self._key_here else START_WITHOUT_QUESTION
        if not self._kept or QMessageBox.question(self, "Start without this mail", question) != QMessageBox.StandardButton.Yes:
            return
        self._busy(True)
        self._show("Working…", "")

        def done(result: KeptSetAside) -> None:
            self.started_without = result.folder
            self.outcome = f"Local Folders started again, empty. Its mail, calendars, contacts and tasks are not deleted: they are in {result.folder}"
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        self._backend.ask(KEY_START_WITHOUT_KEPT, NoParameters(), done, failed, owner=self)

    def _take_back(self) -> None:
        """Local Folders written with an older key the store has: written again with this computer's key, while the window says so."""
        self._busy(True)
        self._show("Working… (for much mail this takes a while)", "")

        def done(result: KeyStatus) -> None:
            self._busy(False)
            self.outcome = TAKEN_BACK + (f" {result.problem}" if result.problem else "")
            if result.in_credential_store and not result.kept_other and not result.take_back:
                self.accept()
            else:
                self.show_status(result)

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(f"{error}. Nothing was lost; choose Try again when that is solved, or Not now.", red_style())

        self._backend.ask(LOCAL_TAKE_BACK, NoParameters(), done, failed, owner=self)

    def _try_again(self) -> None:
        self._busy(True)
        self._show("Checking…", "")

        def done(result: KeyStatus) -> None:
            self._busy(False)
            if result.take_back and result.in_credential_store:
                self.show_status(result)
                self._take_back()
            elif result.in_credential_store and not result.kept_other:
                self.accept()
            else:
                self.show_status(result)  # the newest reason
                self._show("The key is still not available: see the reason above.", red_style())

        def failed(error: ChronoMailError) -> None:
            self._busy(False)
            self._show(str(error), red_style())

        self._backend.ask(KEY_STATUS, NoParameters(), done, failed, owner=self)
