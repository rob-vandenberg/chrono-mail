from collections.abc import Callable
from dataclasses import replace
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_emclient import IMPORT_EMCLIENT, IMPORT_EMCLIENT_SCAN, ImportParameters, ImportResult, ScanParameters, ScanResult
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.scale import wpx
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "importdialog.py 4.0.376"

# --- Version history ----------------------------------------------------
# v4.0.376: built from its Qt Designer form (ui/forms/import.ui, D-276); the source program's name stays in this file (D-046); the summary is plain text (names from the exports); its sizes
#           follow the size factor
# v3.6.359: CR-07: a failed batch reloads the window and runs the check after an import; the summary keeps what was imported
# v2.4.333: every count is written one way, "1 message", "2 messages", "1,041" (core/words.py counted)
# v1.9.271: R-19: reading the exports and the import are typed calls
# v0.5.151: on_running: the main window learns when an import starts and ends (mail checks wait meanwhile)
# v0.5.143: Import waits for the reading of the exports; a late or older reading answer is ignored; the progress bar follows the count, which the engine gives over the whole import
# v0.5.137: Tags: a type of its own; the summaries count the categories and the tagged messages
# v0.5.132: Initial version: Menu > File > Import…, the program (eM Client) chosen in the window; the summary scrolls

MENU_TEXT: str = "Import…"  # the program to import from is chosen in the window (more programs later)
SOURCE_NAME: str = "eM Client"
BATCH: int = 1000  # messages per command; Stop takes effect between batches
LISTED: int = 10  # names listed in a summary before "and N more"


def _listed(items: tuple[str, ...]) -> str:
    shown = "; ".join(items[:LISTED])
    return shown + (f"; and {len(items) - LISTED} more" if len(items) > LISTED else "")


def scan_summary(scan: ScanResult) -> str:
    """What an import would bring in, in plain words."""
    lines: list[str] = []
    if scan.accounts or scan.accounts_not_pop3:
        line = f"Accounts: {scan.accounts}"
        if scan.accounts_existing:
            line += f", of which {scan.accounts_existing} already here (not made again)"
        if scan.accounts_not_pop3:
            line += f"; {scan.accounts_not_pop3} not POP3 (not imported)"
        lines.append(line + ".")
    if scan.passwords_missing:
        lines.append(f"Passwords that cannot be read (you type them later): {_listed(scan.passwords_missing)}.")
    if scan.rules or scan.rules_not_imported:
        lines.append(f"Rules: {scan.rules}" + (f"; not imported: {_listed(scan.rules_not_imported)}" if scan.rules_not_imported else "") + ".")
    if scan.messages:
        line = f"Mail: {counted(scan.messages, 'message')} in {counted(scan.folders, 'folder')}"
        if scan.messages_done:
            line += f", {scan.messages_done:,} of them done by an earlier import (it continues)"
        lines.append(line + ".")
    if scan.marks:
        lines.append(f"Read, flagged and replied marks for {counted(scan.marks, 'message')}.")
    if scan.contacts:
        lines.append(f"Contacts: {scan.contacts}.")
    if scan.tags or scan.tagged:
        lines.append(f"Tags: {scan.tags} categories; {scan.tagged} tagged messages.")
    return "\n".join(lines) if lines else "Nothing to import was found."


def merge(total: ImportResult, batch: ImportResult) -> ImportResult:
    """The results of two runs of one import as one."""
    return replace(
        total,
        accounts_added=total.accounts_added + batch.accounts_added,
        accounts_existing=total.accounts_existing + batch.accounts_existing,
        accounts_without_password=total.accounts_without_password + batch.accounts_without_password,
        rules_added=total.rules_added + batch.rules_added,
        rules_existing=total.rules_existing + batch.rules_existing,
        rules_not_imported=total.rules_not_imported + batch.rules_not_imported,
        contacts_added=total.contacts_added + batch.contacts_added,
        contacts_existing=total.contacts_existing + batch.contacts_existing,
        messages_imported=total.messages_imported + batch.messages_imported,
        messages_duplicate=total.messages_duplicate + batch.messages_duplicate,
        messages_failed=total.messages_failed + batch.messages_failed,
        messages_left=batch.messages_left,
        tags_added=total.tags_added + batch.tags_added,
        messages_tagged=total.messages_tagged + batch.messages_tagged,
        notes=total.notes + batch.notes,
    )


def import_summary(result: ImportResult, stopped: bool) -> str:
    lines = ["The import was stopped; Import continues where it stopped." if stopped else "The import is done."]
    if result.accounts_added or result.accounts_existing:
        lines.append(f"Accounts made: {result.accounts_added}; already here: {result.accounts_existing}.")
    if result.accounts_without_password:
        lines.append(f"Type the password in Settings > Accounts for: {_listed(result.accounts_without_password)}.")
    if result.rules_added or result.rules_existing or result.rules_not_imported:
        lines.append(f"Rules imported: {result.rules_added}" + (f"; not imported: {_listed(result.rules_not_imported)}" if result.rules_not_imported else "") + ".")
    if result.contacts_added or result.contacts_existing:
        lines.append(f"Contacts imported: {result.contacts_added}; already here: {result.contacts_existing}.")
    if result.tags_added or result.messages_tagged:
        lines.append(f"Tags made: {result.tags_added}; messages that got their tags: {result.messages_tagged}.")
    if result.messages_imported or result.messages_duplicate or result.messages_failed:
        lines.append(f"Messages imported: {result.messages_imported:,}; already here: {result.messages_duplicate:,}; failed: {len(result.messages_failed)}.")
    if result.messages_failed:
        lines.append(f"Failed: {_listed(result.messages_failed)}")
    if result.notes:
        lines.append(f"Notes: {_listed(result.notes)}")
    return "\n".join(lines)


class ImportDialog(QDialog):
    """Menu > File > Import…: the program to import from, what to import, from which exports, and the import itself, in one window."""

    def __init__(self, backend: Backend, on_changed: Callable[[], None], parent: QWidget | None = None, on_running: Callable[[bool], None] | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._on_changed = on_changed
        self._on_running = on_running  # told True when an import starts and False when it ends (the main window holds its mail checks)
        self._running = False
        self._stop = False
        self._total = 0
        self._done_before = 0
        self._scan_number = 0  # the latest reading of the exports; the answer of an older one is ignored
        self._result = ImportResult()
        form = forms.fill(self, "import")  # D-276
        self.sources: QTreeWidget = forms.part(form, QTreeWidget, "sources")
        self.sources.setHeaderHidden(True)
        self.sources.setRootIsDecorated(False)
        group = QTreeWidgetItem(self.sources, ["Applications"])
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a heading: never selected
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        self.source_item = QTreeWidgetItem(group, [SOURCE_NAME])
        group.setExpanded(True)
        self.sources.setCurrentItem(self.source_item)
        self.sources.setFixedWidth(wpx(220))
        forms.part(form, QLabel, "title").setText(f"Import data from {SOURCE_NAME}")  # the program's name only here (D-046)
        self.accounts: QCheckBox = forms.part(form, QCheckBox, "accounts")
        self.rules: QCheckBox = forms.part(form, QCheckBox, "rules")
        self.mail: QCheckBox = forms.part(form, QCheckBox, "mail")
        self.contacts: QCheckBox = forms.part(form, QCheckBox, "contacts")
        self.tags: QCheckBox = forms.part(form, QCheckBox, "tags")
        for box in (self.accounts, self.rules, self.mail, self.contacts, self.tags):
            box.setChecked(True)
        self.settings_file: QLineEdit = forms.part(form, QLineEdit, "settingsFile")
        self.mail_folder: QLineEdit = forms.part(form, QLineEdit, "mailFolder")
        self.backup_file: QLineEdit = forms.part(form, QLineEdit, "backupFile")
        for field, browse, button in (
            (self.settings_file, self._browse_settings, "browseSettings"), (self.mail_folder, self._browse_mail, "browseMail"),
            (self.backup_file, self._browse_backup, "browseBackup"),
        ):
            forms.part(form, QPushButton, button).clicked.connect(browse)
            field.editingFinished.connect(self.scan)
        self.summary: QLabel = forms.part(form, QLabel, "summary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # names from the exports: never markup
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_area: QScrollArea = forms.part(form, QScrollArea, "summaryArea")  # a long summary scrolls instead of falling off the window
        self.progress: QProgressBar = forms.part(form, QProgressBar, "progress")
        self.progress.setVisible(False)
        self.import_button: QPushButton = forms.part(form, QPushButton, "import")
        self.stop_button: QPushButton = forms.part(form, QPushButton, "stop")
        self.close_button: QPushButton = forms.part(form, QPushButton, "close")
        self.stop_button.setEnabled(False)
        self.import_button.setEnabled(False)  # until the reading of the exports has answered: the import needs its count
        self.import_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.request_stop)
        self.close_button.clicked.connect(self.reject)
        self.setMinimumHeight(wpx(560))
        forms.smallest(self, 820)  # its texts are never cut off, also at the largest size
        backend.progress.connect(self._progress)

    # --- choosing the exports -----------------------------------------------------------------

    def _browse_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "The settings export", self.settings_file.text(), "Settings (*.xml)")
        if path:
            self.settings_file.setText(path)
            self.scan()

    def _browse_mail(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "The folder of the mail export", self.mail_folder.text())
        if path:
            self.mail_folder.setText(path)
            self.scan()

    def _browse_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "The backup", self.backup_file.text(), "Backup (*.zip)")
        if path:
            self.backup_file.setText(path)
            self.scan()

    def _sources(self) -> ScanParameters:
        return ScanParameters(self.settings_file.text().strip() or None, self.mail_folder.text().strip() or None, self.backup_file.text().strip() or None)

    def scan(self) -> None:
        if self._running:
            return
        self._scan_number += 1
        number = self._scan_number
        self.import_button.setEnabled(False)
        sources = self._sources()
        if not (sources.settings_file or sources.mail_folder or sources.backup_file):
            return
        self.summary.setText("Reading the exports…")
        self._backend.ask(IMPORT_EMCLIENT_SCAN, sources, lambda result: self._scanned(number, result), lambda error: self._scan_failed(number, error), owner=self)

    def _scanned(self, number: int, result: ScanResult) -> None:
        if self._running or number != self._scan_number:
            return  # an import runs with the count it started with, or a newer reading was started
        self._total = result.messages
        self._done_before = result.messages_done
        self.progress.setRange(0, max(1, self._total))
        self.summary.setText(scan_summary(result))
        self.import_button.setEnabled(True)

    def _scan_failed(self, number: int, error: ChronoMailError) -> None:
        if self._running or number != self._scan_number:
            return
        self.summary.setText(str(error))  # Import stays off: there is nothing it could import

    def _failed(self, error: ChronoMailError) -> None:
        """A batch failed: the window is reloaded (this batch may have stored part of its work), the summary counts the earlier batches, and the check after an import runs (D-143)."""
        self._finish()
        self.summary.setText(f"{error}\n{import_summary(self._result, stopped=True)}")
        self._on_changed()

    # --- the import ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._running or not self.import_button.isEnabled():
            return  # only after the reading of the exports has answered
        self._running = True
        if self._on_running is not None:
            self._on_running(True)
        self._stop = False
        self._result = ImportResult()
        for widget in (self.import_button, self.close_button, self.accounts, self.rules, self.mail, self.contacts, self.tags, self.settings_file, self.mail_folder, self.backup_file):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, self._total))
        self.progress.setValue(self._done_before)
        self.summary.setText("Importing…")
        self._next()

    def _next(self) -> None:
        sources = self._sources()
        parameters = ImportParameters(
            sources.settings_file, sources.mail_folder, sources.backup_file, accounts=self.accounts.isChecked(), rules=self.rules.isChecked(), mail=self.mail.isChecked(),
            contacts=self.contacts.isChecked(), tags=self.tags.isChecked(), batch=BATCH,
        )
        self._backend.ask(IMPORT_EMCLIENT, parameters, self._batch_done, self._failed, owner=self)

    def _batch_done(self, result: ImportResult) -> None:
        self._result = merge(self._result, result)
        self._done_before += result.messages_imported + result.messages_duplicate + len(result.messages_failed)
        self.progress.setValue(min(self._done_before, self.progress.maximum()))
        if result.messages_left > 0 and not self._stop:
            self._next()
            return
        stopped = result.messages_left > 0
        self._finish()
        self.summary.setText(import_summary(self._result, stopped))
        self._on_changed()

    def request_stop(self) -> None:
        self._stop = True
        self.stop_button.setEnabled(False)
        self.summary.setText("Stopping after the messages that are being imported now…")

    def _finish(self) -> None:
        self._running = False
        if self._on_running is not None:
            self._on_running(False)
        for widget in (self.import_button, self.close_button, self.accounts, self.rules, self.mail, self.contacts, self.tags, self.settings_file, self.mail_folder, self.backup_file):
            widget.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _progress(self, what: str, done: int, total: object) -> None:
        if self._running and what == "Importing mail":
            self.progress.setValue(min(done, self.progress.maximum()))  # the engine counts over the whole import

    @override
    def reject(self) -> None:
        if not self._running:  # while it runs, Stop first: the batch that runs is finished either way
            super().reject()
