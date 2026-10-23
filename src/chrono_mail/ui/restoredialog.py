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
    QListWidget,
    QListWidgetItem,
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
    ContentsRow,
    KeyEnsured,
    ReadParameters,
    RestoreParameters,
    RestoreResult,
)
from chrono_mail.engine.restore import DOING_ITEMS, DOING_MESSAGES, BackupPasswordNeeded, RestoreCancelled, WrongBackupPassword
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.recoverydialog import RecoveryDialog
from chrono_mail.ui.windowsettings import apply_window_settings
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "restoredialog.py 2.4.334"

# --- Version history ----------------------------------------------------
# v2.4.334: the report names where the damaged messages and items were
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v2.4.328: the report says how many damaged messages and items were left out (W5)
# v2.4.327: the report says what of the Outbox went to Drafts and which scheduled messages were paused (D-245)
# v2.4.326: ticks for what to restore (only the parts the backup holds), the accounts with Select all and their counts, and the
#           account passwords when the backup holds them; greyed, never hidden (D-244)
# v2.4.325: restored tells whether the window's settings came back: the window takes them in at once (D-244)
# v2.4.324: Initial: Menu > File > Restore…: this computer's key first (its recovery code in its own window), then the backup, then its
#           password, then the Restore window with what the backup holds, "Remove all data on this computer first" and "Replace items
#           that already exist", progress and Cancel, and afterwards a test of every account it made (D-244)

MENU_TEXT: str = "Restore…"
PART_NAMES: dict[str, str] = {MESSAGES: "Messages", RULES: "Mail rules", CALENDARS: "Calendar items", CONTACTS: "Contacts", TASKS: "Tasks", SETTINGS: "Settings"}
TESTING: str = "testing the connection…"
SHOWN_DOING: dict[str, str] = {DOING_MESSAGES: "Messages", DOING_ITEMS: "Calendar items, contacts and tasks"}
REMOVE_QUESTION: str = (
    "Remove all mail, calendars, contacts, tasks, rules and settings on this computer, and put the backup in their place?\n\n"
    "Everything on this computer is removed, also what you did not tick. Nothing on your mail or calendar servers is touched. "
    "If the restore does not finish, everything is put back as it was."
)
EVERY_ACCOUNT: frozenset[str] = frozenset({RULES, SETTINGS})  # parts of the program, not of an account: never greyed
ENTRY_ROLE: int = int(Qt.ItemDataRole.UserRole)
PARTS_ROLE: int = int(Qt.ItemDataRole.UserRole) + 1


def contents_text(contents: ContentsResult) -> str:
    """What a backup holds, in plain words."""
    try:
        made = datetime.fromisoformat(contents.created).strftime("%d %B %Y, %H:%M")
    except ValueError:
        made = contents.created or "an unknown date"
    lines = [f"This backup was made on {made}" + (f" by Chrono Mail {contents.version}." if contents.version else ".")]
    if not contents.passwords:
        lines.append("The account passwords are not in it: you type them after the restore.")
    return "\n".join(lines)


def entry_text(row: ContentsRow) -> str:
    """An account's line in the list: its name, how many messages and items, and whether it is here already."""
    counts: list[str] = []
    if MESSAGES in row.parts:
        counts.append(counted(row.messages, "message"))
    if any(part in row.parts for part in (CALENDARS, CONTACTS, TASKS)):
        counts.append(f"{row.items:,} calendar items, contacts and tasks")
    return row.name + (": " + ", ".join(counts) if counts else "") + (" (already here: what it lacks is added)" if row.here else "")


class RestoreDialog(QDialog):
    """The Restore window: what the backup holds, the two choices, the restore with its progress, and the test of the accounts it made."""

    restored = Signal(bool)  # the data on this computer changed (and whether the window's settings came back): the window takes them in
    running_changed = Signal(bool)

    def __init__(self, backend: Backend, settings: QSettings, path: Path, password: str | None, contents: ContentsResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._path: Path = path
        self._password: str | None = password
        self._names: dict[str, str] = {row.entry_id: row.name for row in contents.accounts}
        self._finished: bool = False  # after a restore that finished, the choices are what was done
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
        layout.addWidget(QLabel("<b>What to restore</b>", self))
        self.part_boxes: dict[str, QCheckBox] = {}
        for part in (MESSAGES, RULES, CALENDARS, CONTACTS, TASKS, SETTINGS):
            if part in contents.parts:  # only what the backup holds
                box = QCheckBox(PART_NAMES[part], self)
                box.setChecked(True)
                box.toggled.connect(self._update)
                self.part_boxes[part] = box
                layout.addWidget(box)
        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Accounts</b>", self))
        self.select_all = QCheckBox("Select all", self)
        self.select_all.setChecked(True)
        self.select_all.clicked.connect(self._select_all_clicked)
        layout.addWidget(self.select_all)
        self.accounts = QListWidget(self)
        self.accounts.setMinimumHeight(100)
        for row in contents.accounts:
            item = QListWidgetItem(entry_text(row), self.accounts)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(ENTRY_ROLE, row.entry_id)
            item.setData(PARTS_ROLE, list(row.parts))
        self.accounts.itemChanged.connect(self._item_changed)
        layout.addWidget(self.accounts)
        self.passwords = QCheckBox("Account passwords", self)
        self.passwords.setChecked(contents.passwords)
        self.passwords.setVisible(contents.passwords)  # only when the backup holds them: nothing to choose otherwise
        self.passwords.toggled.connect(self._update)
        layout.addWidget(self.passwords)
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

    def _items(self) -> list[QListWidgetItem]:
        return [item for number in range(self.accounts.count()) if (item := self.accounts.item(number)) is not None]

    def chosen_accounts(self) -> tuple[str, ...]:
        return tuple(str(item.data(ENTRY_ROLE)) for item in self._items() if item.checkState() == Qt.CheckState.Checked)

    def chosen_parts(self) -> frozenset[str]:
        """The parts ticked that a chosen account has (a greyed part is not restored)."""
        return frozenset(part for part, box in self.part_boxes.items() if box.isChecked() and box.isEnabled())

    def _item_changed(self, item: QListWidgetItem) -> None:
        del item  # a tick of an account: what can be chosen follows
        self._update()

    def _select_all_clicked(self, checked: bool) -> None:
        self.accounts.blockSignals(True)
        for item in self._items():
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.accounts.blockSignals(False)
        self._update()

    def _update(self) -> None:
        """Grey what cannot be used now (never hidden): parts no chosen account has, Replace when everything is removed first, Restore…
        until a part and an account are chosen, and every choice while the restore runs and after it finished."""
        before = not self.running and not self._finished
        items = self._items()
        ticked = [item for item in items if item.checkState() == Qt.CheckState.Checked]
        self.select_all.setChecked(bool(items) and len(ticked) == len(items))
        available: set[str] = set(EVERY_ACCOUNT)
        for item in ticked:
            parts = item.data(PARTS_ROLE)
            if isinstance(parts, list):
                available.update(str(part) for part in parts)
        for part, box in self.part_boxes.items():
            box.setEnabled(before and part in available)
        for widget in (self.select_all, self.accounts, self.passwords, self.remove_first):
            widget.setEnabled(before)
        self.replace_existing.setEnabled(before and not self.remove_first.isChecked())  # nothing exists after removing it all
        self.restore_button.setEnabled(before and bool(self.chosen_accounts()) and bool(self.chosen_parts()))
        self.close_button.setText("Cancel" if self.running else "Close")

    def start(self) -> None:
        if self.running or not self.restore_button.isEnabled():
            return
        if self.remove_first.isChecked() and QMessageBox.question(self, "Restore", REMOVE_QUESTION) != QMessageBox.StandardButton.Yes:
            return
        parts = self.chosen_parts()  # the choices as they are now: the run greys them
        parameters = RestoreParameters(
            str(self._path), self._password, self.remove_first.isChecked(), self.replace_existing.isChecked() and not self.remove_first.isChecked(),
            self.chosen_accounts(), messages=MESSAGES in parts, rules=RULES in parts, calendars=CALENDARS in parts, contacts=CONTACTS in parts,
            tasks=TASKS in parts, settings=SETTINGS in parts, passwords=self.passwords.isChecked(),
        )
        self.running = True
        self.running_changed.emit(True)
        self._update()
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText("Restoring…")
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
        self._finished = True
        self._finish()
        lines = [f"The backup is restored: {counted(result.messages, 'message')} and {result.items:,} calendar items, contacts and tasks came back."]
        if result.skipped:
            lines.append(f"{result.skipped:,} were here already, or are on their server, and were left as they are.")
        settings_back = bool(result.window_settings) and apply_window_settings(self._settings, result.window_settings, self.remove_first.isChecked() or self.replace_existing.isChecked()) > 0
        held: list[str] = []
        if result.to_drafts:
            held.append(f"{result.to_drafts:,} {'message' if result.to_drafts == 1 else 'messages'} that waited in the Outbox {'is' if result.to_drafts == 1 else 'are'} in Drafts")
        if result.paused:
            held.append(f"{result.paused:,} scheduled {'message is' if result.paused == 1 else 'messages are'} paused (the time had passed)")
        if held:
            lines.append("; ".join(held) + ". Nothing is sent until you send it.")  # D-245
        if result.damaged:
            places = "; ".join(result.damaged_places)
            lines.append(f"Damaged in the backup and left out: {places or counted(result.damaged, 'message or item', 'messages and items')}. Everything else came back.")
        lines.extend(result.notes)
        self._lines = lines
        self._tested = {account_id: TESTING for account_id in result.made if account_id != LOCAL_ACCOUNT_ID}
        self._show_report()
        self.restored.emit(settings_back)
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
        put_back = " Everything on this computer is as it was." if self.remove_first.isChecked() else " What came back whole stays."
        if isinstance(error, RestoreCancelled):
            self.status.setText("The restore was cancelled." + put_back)
        else:
            self.status.setText(f"The restore did not finish: {error}." + put_back)
        self.restored.emit(False)  # what came back is shown

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

    restored = Signal(bool)
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
