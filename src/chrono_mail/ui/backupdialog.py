from datetime import date
from pathlib import Path
from typing import override

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QMessageBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.backup import CALENDARS, BackupCancelled, CONTACTS, DOING_ITEMS, DOING_MESSAGES, DOING_SETTINGS, EXTENSION, MESSAGES, RULES, SETTINGS, TASKS
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_backup import (
    BACKUP_CANCEL,
    BACKUP_CHOICES,
    BACKUP_MAKE,
    BACKUP_NEW_PASSWORD,
    BackupChoices,
    BackupParameters,
    BackupPassword,
    BackupResult,
    CancelResult,
)
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.statusreport import StatusReport
from chrono_mail.ui.windowsettings import window_settings_text

# --- Version ------------------------------------------------------------
__version__ = "backupdialog.py 2.5.338"

# --- Version history ----------------------------------------------------
# v2.5.338: Copy stays usable while the backup is made (the owner)
# v2.4.324: may_quit: Quit while a backup is made or restored asks first (D-244)
# v2.4.323: "Backup" is one word (the owner): the menu item and the button Backup…, the window and its save window Backup
# v2.4.322: Initial: Menu > File > Back up…: what to back up, which accounts, the passwords only in a protected backup, the backup
#           password made by Chrono Mail with Copy and "I have saved the backup password", progress, Cancel, Run in background (D-244)

MENU_TEXT: str = "Backup…"
PARTS: tuple[tuple[str, str], ...] = (
    (MESSAGES, "Messages"), (RULES, "Mail rules"), (CALENDARS, "Calendar items"), (CONTACTS, "Contacts"), (TASKS, "Tasks"), (SETTINGS, "Settings"),
)
EVERY_ACCOUNT: frozenset[str] = frozenset({RULES, SETTINGS})  # parts that belong to the program, not to an account: never greyed
SHOWN_DOING: dict[str, str] = {DOING_MESSAGES: "Messages", DOING_ITEMS: "Calendar items, contacts and tasks", DOING_SETTINGS: "Settings"}
PASSWORD_TEXT: str = "Save this password somewhere safe. Without it the backup cannot be opened, and it cannot be recovered."
COPIED_MILLISECONDS: int = 2000
ENTRY_ROLE: int = int(Qt.ItemDataRole.UserRole)
PARTS_ROLE: int = int(Qt.ItemDataRole.UserRole) + 1


def suggested_name(today: date) -> str:
    return f"Chrono Mail backup {today.isoformat()}{EXTENSION}"


def size_text(size: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= step:
            return f"{size / step:,.1f} {unit}"
    return f"{size:,} bytes"


class BackupDialog(QDialog):
    """Menu > File > Backup…: a .cmz file of the chosen parts of the chosen accounts. While it runs, the window may be put away (Run in
    background); a click on the busy bar brings it back. The backup only reads: nothing changes here or on a server (D-193)."""

    running_changed = Signal(bool)  # the busy bar's click and hover text follow it
    backup_ended = Signal(str, bool)  # what came of it (text, error), for the status line when the window was put away

    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self.running: bool = False
        self.setWindowTitle("Backup")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>What to back up</b>", self))
        self.part_boxes: dict[str, QCheckBox] = {}
        parts_row = QHBoxLayout()
        columns = (QVBoxLayout(), QVBoxLayout())
        for number, (part, text) in enumerate(PARTS):
            box = QCheckBox(text, self)
            box.setChecked(True)
            box.toggled.connect(self._update)
            self.part_boxes[part] = box
            columns[number // 3].addWidget(box)
        for column in columns:
            parts_row.addLayout(column)
        parts_row.addStretch(1)
        layout.addLayout(parts_row)

        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>Accounts</b>", self))
        self.select_all = QCheckBox("Select all", self)
        self.select_all.setTristate(False)
        self.select_all.setChecked(True)
        self.select_all.clicked.connect(self._select_all_clicked)
        layout.addWidget(self.select_all)
        self.accounts = QListWidget(self)
        self.accounts.setMinimumHeight(120)
        self.accounts.itemChanged.connect(self._item_changed)
        layout.addWidget(self.accounts, 1)

        layout.addSpacing(8)
        self.include_passwords = QCheckBox("Include account passwords", self)
        self.include_passwords.toggled.connect(self._passwords_toggled)
        layout.addWidget(self.include_passwords)
        self.protect = QCheckBox("Protect the backup with a password", self)
        self.protect.setChecked(True)
        self.protect.toggled.connect(self._update)
        layout.addWidget(self.protect)
        password_row = QHBoxLayout()
        self.password = QLineEdit(self)
        self.password.setReadOnly(True)
        monospace = QFont("monospace")
        monospace.setStyleHint(QFont.StyleHint.Monospace)
        self.password.setFont(monospace)
        password_row.addWidget(self.password, 1)
        self.copy_button = QPushButton("Copy", self)
        self.copy_button.clicked.connect(self._copy)
        password_row.addWidget(self.copy_button)
        layout.addLayout(password_row)
        self.password_note = QLabel(PASSWORD_TEXT, self)
        self.password_note.setWordWrap(True)
        layout.addWidget(self.password_note)
        self.saved = QCheckBox("I have saved the backup password", self)
        self.saved.toggled.connect(self._update)
        layout.addWidget(self.saved)

        layout.addSpacing(8)
        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.back_up_button = QPushButton(MENU_TEXT, self)
        self.back_up_button.clicked.connect(self.start)
        self.background_button = QPushButton("Run in background", self)
        self.background_button.clicked.connect(self.hide)
        self.close_button = QPushButton("Close", self)  # Cancel while the backup runs: one button, the one action that applies
        self.close_button.clicked.connect(self._close_or_cancel)
        for button in (self.back_up_button, self.background_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(COPIED_MILLISECONDS)
        self._copied_timer.timeout.connect(lambda: self.copy_button.setText("Copy"))
        backend.progress.connect(self._progress)
        self._update()
        backend.ask(BACKUP_CHOICES, NoParameters(), self._listed, self._failed, owner=self)
        backend.ask(BACKUP_NEW_PASSWORD, NoParameters(), self._password_made, self._failed, owner=self)

    # --- choosing ---------------------------------------------------------------------------------

    def _listed(self, choices: BackupChoices) -> None:
        self.accounts.blockSignals(True)  # filling the list is not a change of the person's choice
        for row in choices.rows:
            item = QListWidgetItem(row.name, self.accounts)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(ENTRY_ROLE, row.entry_id)
            item.setData(PARTS_ROLE, list(row.parts))
        self.accounts.blockSignals(False)
        self._update()

    def _password_made(self, made: BackupPassword) -> None:
        self.password.setText(made.password)
        self._update()

    def _items(self) -> list[QListWidgetItem]:
        return [item for number in range(self.accounts.count()) if (item := self.accounts.item(number)) is not None]

    def chosen_accounts(self) -> tuple[str, ...]:
        return tuple(str(item.data(ENTRY_ROLE)) for item in self._items() if item.checkState() == Qt.CheckState.Checked)

    def chosen_parts(self) -> frozenset[str]:
        """The parts ticked that a chosen account has (a greyed part is not backed up)."""
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

    def _passwords_toggled(self, included: bool) -> None:
        if included:
            self.protect.setChecked(True)  # passwords never go into a backup without a password
        self._update()

    def _update(self) -> None:
        """Grey what cannot be used now (never hidden): parts no chosen account has, the protection while the passwords are included,
        the backup password while the backup is not protected, and Backup… until everything it needs is chosen."""
        idle = not self.running
        items = self._items()
        ticked = [item for item in items if item.checkState() == Qt.CheckState.Checked]
        self.select_all.setChecked(bool(items) and len(ticked) == len(items))
        available: set[str] = set(EVERY_ACCOUNT)
        for item in ticked:
            parts = item.data(PARTS_ROLE)
            if isinstance(parts, list):
                available.update(str(part) for part in parts)
        for part, box in self.part_boxes.items():
            box.setEnabled(idle and part in available)
        self.select_all.setEnabled(idle)
        self.accounts.setEnabled(idle)
        self.include_passwords.setEnabled(idle)
        self.protect.setEnabled(idle and not self.include_passwords.isChecked())
        protected = self.protect.isChecked()
        for widget in (self.password, self.password_note, self.saved):
            widget.setEnabled(idle and protected)
        self.copy_button.setEnabled(protected)  # only reads the password: it can still be copied while the backup is made
        ready = bool(self.chosen_accounts()) and bool(self.chosen_parts()) and (not protected or (self.saved.isChecked() and bool(self.password.text())))
        self.back_up_button.setEnabled(idle and ready)
        self.background_button.setEnabled(self.running)
        self.close_button.setText("Cancel" if self.running else "Close")

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.password.text())
        self.copy_button.setText("Copied")
        self._copied_timer.start()

    # --- the backup -------------------------------------------------------------------------------

    def start(self) -> None:
        if self.running or not self.back_up_button.isEnabled():
            return
        start_in = Path.home() / suggested_name(date.today())
        path, _ = QFileDialog.getSaveFileName(self, "Backup", str(start_in), f"Chrono Mail backup (*{EXTENSION})")
        if not path:
            return
        parts = self.chosen_parts()
        protected = self.protect.isChecked()
        parameters = BackupParameters(
            path, self.chosen_accounts(), messages=MESSAGES in parts, rules=RULES in parts, calendars=CALENDARS in parts, contacts=CONTACTS in parts,
            tasks=TASKS in parts, settings=SETTINGS in parts, passwords=self.include_passwords.isChecked() and protected,
            password=self.password.text() if protected else None, window_settings=window_settings_text(self._settings) if SETTINGS in parts else "",
            overwrite=True,  # the save window asked already
        )
        self._set_running(True)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.status.setText("Starting the backup…")
        self._backend.ask(BACKUP_MAKE, parameters, self._done, self._failed_backup, owner=self)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.close_button.setEnabled(True)
        self._update()
        self.running_changed.emit(running)

    def _progress(self, what: str, done: int, total: object) -> None:
        shown = SHOWN_DOING.get(what)
        if not self.running or shown is None:
            return
        maximum = total if isinstance(total, int) and total > 0 else 0
        self.progress.setRange(0, maximum)
        self.progress.setValue(min(done, maximum))
        self.status.setText(f"{shown}: {done:,} of {maximum:,}" if maximum else f"{shown}: {done:,}")

    def _done(self, result: BackupResult) -> None:
        self._set_running(False)
        self.progress.setVisible(False)
        lines = [f"The backup was saved as {result.path} ({size_text(result.size)})."]
        lines.append(f"Messages: {result.messages:,}. Calendar items, contacts and tasks: {result.items:,}.")
        if result.unreadable:
            lines.append(f"{result.unreadable:,} files could not be read here and were backed up exactly as they are.")
        self.status.setText("\n".join(lines))
        self.backup_ended.emit(f"The backup was saved as {Path(result.path).name}", False)

    def _failed_backup(self, error: ChronoMailError) -> None:
        self._set_running(False)
        self.progress.setVisible(False)
        text = "The backup was cancelled. No file was left behind." if isinstance(error, BackupCancelled) else f"The backup was not made: {error}"
        self.status.setText(text)
        self.backup_ended.emit(text, not isinstance(error, BackupCancelled))

    def _failed(self, error: ChronoMailError) -> None:
        self.status.setText(str(error))

    def _close_or_cancel(self) -> None:
        if self.running:
            self.close_button.setEnabled(False)  # until the backup has stopped
            self.status.setText("Stopping the backup…")
            self._backend.ask(BACKUP_CANCEL, NoParameters(), self._cancel_asked, self._failed, owner=self)
            return
        self.reject()

    def _cancel_asked(self, result: CancelResult) -> None:
        if not result.cancelled:  # it had just ended: its own answer says how
            self.close_button.setEnabled(True)

    @override
    def reject(self) -> None:
        """Escape or the window's close button: while the backup runs, the window is put away and the backup goes on."""
        if self.running:
            self.hide()
            return
        super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)


class BackupWindow(QObject):
    """The main window's part for File > Backup…: one Backup window at a time, kept while a backup runs so a click on the busy bar
    brings it back; the status line says what came of it."""

    def __init__(self, backend: Backend, settings: QSettings, report: StatusReport, parent: QWidget) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._settings: QSettings = settings
        self._report: StatusReport = report
        self._parent: QWidget = parent
        self.window: BackupDialog | None = None

    @property
    def running(self) -> bool:
        return self.window is not None and self.window.running

    def show(self) -> None:
        window = self.window
        if window is None:
            window = BackupDialog(self._backend, self._settings, self._parent)
            window.running_changed.connect(self._report.backup_hint)
            window.backup_ended.connect(self._report.show)  # an error in red
            window.finished.connect(self._closed)
            self.window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _closed(self) -> None:
        if self.window is not None and not self.window.running:
            self.window.deleteLater()
            self.window = None


def may_quit(parent: QWidget, backing_up: bool, restoring: bool) -> bool:
    """Quit while a backup is made or restored: asked first (the backup is stopped and no file is kept; a restore stops, and what was
    set aside comes back)."""
    if not (backing_up or restoring):
        return True
    text = (
        "A backup is being made. Quit anyway? The backup stops and no file is kept." if backing_up
        else "A backup is being restored. Quit anyway? The restore stops; what came back whole stays, and data that was set aside is put back."
    )
    return QMessageBox.question(parent, "Quit", text) == QMessageBox.StandardButton.Yes
