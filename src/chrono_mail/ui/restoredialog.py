from datetime import datetime
from pathlib import Path
from typing import override

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import LOCAL_ACCOUNT_ID, ChronoMailError
from chrono_mail.engine.backup import CALENDARS, CONTACTS, EXTENSION, MESSAGES, RULES, SETTINGS, TASKS
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_backup import (
    BACKUP_CANCEL,
    BACKUP_CHECK_ACCOUNT,
    BACKUP_CONTENTS,
    BACKUP_RESTORE,
    KEY_ENSURE,
    CancelResult,
    CheckAccountParameters,
    CheckAccountResult,
    ContentsResult,
    KeyEnsured,
    ReadParameters,
    RestoreParameters,
    RestoreResult,
)
from chrono_mail.engine.restore import DOING_ITEMS, DOING_MESSAGES, BackupPasswordNeeded, RestoreCancelled, WrongBackupPassword
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.windowsettings import apply_window_settings

# --- Version ------------------------------------------------------------
__version__ = "restoredialog.py 2.4.324"

# --- Version history ----------------------------------------------------
# v2.4.324: Initial: Menu > File > Restore…: this computer's key first (its recovery code in its own window), then the backup, then its
#           password, then the Restore window with what the backup holds, "Remove all data on this computer first" and "Replace items
#           that already exist", progress and Cancel, and afterwards a test of every account it made (D-244)

MENU_TEXT: str = "Restore…"
PART_NAMES: dict[str, str] = {MESSAGES: "Messages", RULES: "Mail rules", CALENDARS: "Calendar items", CONTACTS: "Contacts", TASKS: "Tasks", SETTINGS: "Settings"}
TESTING: str = "testing the connection…"
SHOWN_DOING: dict[str, str] = {DOING_MESSAGES: "Messages", DOING_ITEMS: "Calendar items, contacts and tasks"}
REMOVE_QUESTION: str = (
    "Remove all mail, calendars, contacts, tasks, rules and settings on this computer, and put the backup in their place?\n\n"
    "Nothing on your mail or calendar servers is touched. If the restore does not finish, everything is put back as it was."
)


def contents_text(contents: ContentsResult) -> str:
    """What a backup holds, in plain words."""
    try:
        made = datetime.fromisoformat(contents.created).strftime("%d %B %Y, %H:%M")
    except ValueError:
        made = contents.created or "an unknown date"
    lines = [f"This backup was made on {made}" + (f" by Chrono Mail {contents.version}." if contents.version else ".")]
    names = [row.name + (" (already here: what it lacks is added)" if row.here else "") for row in contents.accounts]
    lines.append("Accounts: " + ("; ".join(names) if names else "none") + ".")
    lines.append("What it holds: " + ", ".join(PART_NAMES.get(part, part) for part in contents.parts) + ".")
    lines.append(f"Messages: {contents.messages:,}. Calendar items, contacts and tasks: {contents.items:,}.")
    lines.append("The account passwords are in it." if contents.passwords else "The account passwords are not in it: you type them after the restore.")
    return "\n".join(lines)


class RestoreDialog(QDialog):
    """The Restore window: what the backup holds, the two choices, the restore with its progress, and the test of the accounts it made."""

    restored = Signal()  # the data on this computer changed: the window loads the accounts again
    running_changed = Signal(bool)

    def __init__(self, backend: Backend, settings: QSettings, path: Path, password: str | None, contents: ContentsResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._path: Path = path
        self._password: str | None = password
        self._names: dict[str, str] = {row.account_id: row.name for row in contents.accounts}
        self._lines: list[str] = []  # the report after the restore
        self._tested: dict[str, str] = {}  # per account the restore made: what its test said (until then: testing)
        self.running: bool = False
        self.setWindowTitle("Restore")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{path.name}</b>", self))
        self.summary = QLabel(contents_text(contents), self)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        layout.addSpacing(8)
        self.remove_first = QCheckBox("Remove all data on this computer first", self)
        self.replace_existing = QCheckBox("Replace items that already exist", self)
        self.remove_first.toggled.connect(self._update)
        layout.addWidget(self.remove_first)
        layout.addWidget(self.replace_existing)
        layout.addSpacing(8)
        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        area = QScrollArea(self)  # many accounts: the report scrolls instead of falling off the window
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(self.status)
        layout.addWidget(area, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.restore_button = QPushButton(MENU_TEXT, self)
        self.restore_button.clicked.connect(self.start)
        self.close_button = QPushButton("Close", self)  # Cancel while the restore runs: the one action that applies
        self.close_button.clicked.connect(self._close_or_cancel)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        backend.progress.connect(self._progress)
        self._update()

    def _update(self) -> None:
        before = not self.running and self.restore_button.isEnabled()  # after a restore that finished, the choices are what was done
        self.remove_first.setEnabled(before)
        self.replace_existing.setEnabled(before and not self.remove_first.isChecked())  # nothing exists after removing it all
        self.close_button.setText("Cancel" if self.running else "Close")

    def start(self) -> None:
        if self.running:
            return
        if self.remove_first.isChecked() and QMessageBox.question(self, "Restore", REMOVE_QUESTION) != QMessageBox.StandardButton.Yes:
            return
        self.running = True
        self.restore_button.setEnabled(False)
        self.running_changed.emit(True)
        self._update()
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText("Restoring…")
        parameters = RestoreParameters(str(self._path), self._password, self.remove_first.isChecked(), self.replace_existing.isChecked() and not self.remove_first.isChecked())
        self._backend.ask(BACKUP_RESTORE, parameters, self._done, self._failed, owner=self)

    def _progress(self, what: str, done: int, total: object) -> None:
        shown = SHOWN_DOING.get(what)
        if not self.running or shown is None:
            return
        maximum = total if isinstance(total, int) and total > 0 else 0
        self.progress.setRange(0, maximum)
        self.progress.setValue(min(done, maximum))
        self.status.setText(f"{shown}: {done:,} of {maximum:,}" if maximum else f"{shown}: {done:,}")

    def _finish(self) -> None:
        self.running = False
        self.progress.setVisible(False)
        self.close_button.setEnabled(True)
        self.running_changed.emit(False)
        self._update()

    def _done(self, result: RestoreResult) -> None:
        self._finish()
        lines = [f"The backup is restored: {result.messages:,} messages and {result.items:,} calendar items, contacts and tasks came back."]
        if result.skipped:
            lines.append(f"{result.skipped:,} were here already, or are on their server, and were left as they are.")
        if result.window_settings and apply_window_settings(self._settings, result.window_settings, self.remove_first.isChecked() or self.replace_existing.isChecked()):
            lines.append("The window's sizes, columns and look from the backup are used from the next start of Chrono Mail.")
        lines.extend(result.notes)
        self._lines = lines
        self._tested = {account_id: TESTING for account_id in result.made if account_id != LOCAL_ACCOUNT_ID}
        self._show_report()
        self.restored.emit()
        for account_id in self._tested:
            self._backend.ask(
                BACKUP_CHECK_ACCOUNT, CheckAccountParameters(account_id), self._checked, lambda error, account=account_id: self._tell_test(account, str(error)), owner=self,
            )

    def _checked(self, result: CheckAccountResult) -> None:
        self._tell_test(result.account_id, "Connected" if result.connected else result.text)

    def _tell_test(self, account_id: str, text: str) -> None:
        self._tested[account_id] = text
        self._show_report()

    def _show_report(self) -> None:
        tests = [f"{self._names.get(account_id, account_id)}: {text}" for account_id, text in self._tested.items()]
        self.status.setText("\n".join(self._lines + (["", "Accounts:", *tests] if tests else [])))

    def tests_done(self) -> bool:
        return all(text != TESTING for text in self._tested.values())

    def _failed(self, error: ChronoMailError) -> None:
        self._finish()
        self.restore_button.setEnabled(True)
        put_back = " Everything on this computer is as it was." if self.remove_first.isChecked() else " What came back whole stays."
        if isinstance(error, RestoreCancelled):
            self.status.setText("The restore was cancelled." + put_back)
        else:
            self.status.setText(f"The restore did not finish: {error}." + put_back)
        self.restored.emit()  # what came back is shown

    def _close_or_cancel(self) -> None:
        if self.running:
            self.close_button.setEnabled(False)  # until the restore has stopped
            self.status.setText("Stopping the restore…")
            self._backend.ask(BACKUP_CANCEL, NoParameters(), self._cancel_asked, None, owner=self)
            return
        self.reject()

    def _cancel_asked(self, result: CancelResult) -> None:
        if not result.cancelled:
            self.close_button.setEnabled(True)

    @override
    def reject(self) -> None:
        if not self.running:  # while it runs, Cancel first
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running:
            event.ignore()
            return
        super().closeEvent(event)


class RestoreWindow(QObject):
    """The main window's part for File > Restore…: this computer's key first (its recovery code shown in its own window when it is new),
    then the backup, then its password when it is protected, then the Restore window. One secret at a time, each named for what it is."""

    restored = Signal()
    running_changed = Signal(bool)

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._parent: QWidget = parent
        self.window: RestoreDialog | None = None

    @property
    def running(self) -> bool:
        return self.window is not None and self.window.running

    def start(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.raise_()
            return
        self._backend.ask(KEY_ENSURE, NoParameters(), self._key_ready, self._failed, owner=self)

    def _key_ready(self, ensured: KeyEnsured) -> None:
        if ensured.made and RecoveryDialog(self._backend, self._parent, restoring=True).exec() != QDialog.DialogCode.Accepted:
            return  # the recovery code is not stored yet: the restore does not go on
        path, _ = QFileDialog.getOpenFileName(self._parent, "Restore", str(Path.home()), f"Chrono Mail backup (*{EXTENSION})")
        if path:
            self._read(Path(path), None)

    def _read(self, path: Path, password: str | None) -> None:
        self._backend.ask(
            BACKUP_CONTENTS, ReadParameters(str(path), password), lambda contents: self._show(path, password, contents),
            lambda error: self._not_read(path, error), owner=self,
        )

    def _not_read(self, path: Path, error: ChronoMailError) -> None:
        if isinstance(error, WrongBackupPassword):
            QMessageBox.warning(self._parent, "Backup password", str(error))
        elif not isinstance(error, BackupPasswordNeeded):
            QMessageBox.warning(self._parent, "Restore", str(error))
            return
        typed, ok = QInputDialog.getText(self._parent, "Backup password", f"The backup password of {path.name}:", QLineEdit.EchoMode.Password)
        if ok and typed.strip():
            self._read(path, typed.strip())

    def _show(self, path: Path, password: str | None, contents: ContentsResult) -> None:
        window = RestoreDialog(self._backend, self._settings, path, password, contents, self._parent)
        window.restored.connect(self.restored)
        window.running_changed.connect(self.running_changed)
        window.finished.connect(self._closed)
        self.window = window
        window.show()

    def _closed(self) -> None:
        if self.window is not None and not self.window.running:
            self.window.deleteLater()
            self.window = None

    def _failed(self, error: ChronoMailError) -> None:
        QMessageBox.warning(self._parent, "Restore", str(error))
