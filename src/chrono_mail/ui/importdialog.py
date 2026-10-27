import os
import sys
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_emclient import IMPORT_EMCLIENT, IMPORT_EMCLIENT_SCAN, PARTS, ImportParameters, ImportResult, ScanParameters, ScanResult
from chrono_mail.engine import commands_outlook
from chrono_mail.engine.commands_outlook import IMPORT_OUTLOOK, IMPORT_OUTLOOK_SCAN, PROGRESS_TEXT
from chrono_mail.engine.outlook_settings import WINDOWS_ONLY, ProfileImport, ProfileScan
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.prefs import Remembered
from chrono_mail.ui.scale import wpx
from chrono_mail.core.words import counted

# --- Version ------------------------------------------------------------
__version__ = "importdialog.py 4.2.390"

# --- Version history ----------------------------------------------------
# v4.2.390: every Browse with an empty field opens where the last Browse picked, remembered at once (the owner's report)
# v4.2.389: the paths of the last import come back (remembered when an import starts, on this computer): the three export fields filled,
#           each Browse opens there, the data files' Browse opens in their folder; a path that is gone is left out; a Browse whose field is empty opens in the folder of the
#           last pick of any Browse (remembered at once, also for a new window), else the home folder
# v4.2.388: exports or data files imported to the end before: Import asks "Import them again?" (Import again starts over, Cancel does
#           nothing); a stopped import continues without a question; the end says "Nothing new: everything is already here." when nothing
#           new came in, with the counts of what is already here (the rules too)
# v4.2.386: Outlook on the Windows computer where it keeps its accounts: Accounts and Signatures offered when its profile is readable, Rules
#           while Outlook runs (else greyed with why); the profile's data files are listed without asking for a file; the summaries count them
# v4.2.385: Outlook under Applications: its own page (a page per program in the form), Mail, Calendar, Contacts, Tasks and Tags (categories);
#           Accounts, Rules and Signatures greyed with a hover text; one or more data files (.pst); the same summary, progress, Stop and end;
#           the files chosen are kept as the picker gave them (a path may hold "; "), the field only shows them; the items that got their
#           tags in a later run are counted
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
DATA_FILE_SOURCE_NAME: str = "Outlook"
DATA_FILE_HINT: str = "Choose the data files to see what will be imported."
DATA_FILE_SEPARATOR: str = "; "  # between the data files shown in their field (only shown: a path may hold this too)
BATCH: int = 1000  # messages per command; Stop takes effect between batches
PROFILE_HERE: bool = sys.platform == "win32"  # only on Windows can Outlook's profile be read
LISTED: int = 10  # names listed in a summary before "and N more"
DONE_TEXT: str = "The import is done."
STOPPED_TEXT: str = "The import was stopped; Import continues where it stopped."
NOTHING_NEW_TEXT: str = "Nothing new: everything is already here."
AGAIN_QUESTION: str = "These were imported before. Import them again?"
AGAIN_BUTTON: str = "Import again"


def ask_import_again(parent: QWidget) -> bool:
    """These were imported to the end before: True when Import again is clicked (Cancel or closing the box: False)."""
    box = QMessageBox(QMessageBox.Icon.Question, "Import", AGAIN_QUESTION, QMessageBox.StandardButton.NoButton, parent)
    again = box.addButton(AGAIN_BUTTON, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is again


def _first_line(stopped: bool, nothing_new: bool) -> str:
    return STOPPED_TEXT if stopped else NOTHING_NEW_TEXT if nothing_new else DONE_TEXT


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
    nothing_new = not (result.accounts_added or result.rules_added or result.contacts_added or result.messages_imported or result.tags_added or result.messages_tagged)
    lines = [_first_line(stopped, nothing_new)]
    if result.accounts_added or result.accounts_existing:
        lines.append(f"Accounts made: {result.accounts_added}; already here: {result.accounts_existing}.")
    if result.accounts_without_password:
        lines.append(f"Type the password in Settings > Accounts for: {_listed(result.accounts_without_password)}.")
    if result.rules_added or result.rules_existing or result.rules_not_imported:
        lines.append(f"Rules imported: {result.rules_added}; already here: {result.rules_existing}" + (f"; not imported: {_listed(result.rules_not_imported)}" if result.rules_not_imported else "") + ".")
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


def profile_scan_lines(profile: ProfileScan) -> list[str]:
    """What the profile on this computer holds, in plain words (nothing when it cannot be read here)."""
    if not profile.available:
        return []
    lines = [f"Accounts: {_listed(profile.accounts) or 'none'}" + (f" ({profile.accounts_here} already here)." if profile.accounts_here else ".")]
    if profile.signatures:
        lines.append(f"Signatures: {profile.signatures}.")
    if profile.rules_available:
        lines.append(f"Rules: {profile.rules}" + (f", of which blocked senders: {profile.blocked:,}" if profile.blocked else "") + ".")
        if profile.rules_not_imported:
            lines.append(f"Rules not imported: {_listed(profile.rules_not_imported)}")
    return lines


def profile_import_lines(result: ProfileImport) -> list[str]:
    lines: list[str] = []
    if result.accounts_added or result.accounts_existing:
        lines.append(f"Accounts made: {result.accounts_added}; already here: {result.accounts_existing}.")
    if result.accounts_without_password:
        lines.append(f"Without a password (type it in Edit account): {_listed(result.accounts_without_password)}")
    if result.signatures:
        lines.append(f"Signatures given: {result.signatures}.")
    if result.rules_added or result.rules_existing:
        lines.append(f"Rules imported: {result.rules_added}; already here: {result.rules_existing}.")
    if result.rules_not_imported:
        lines.append(f"Rules not imported: {_listed(result.rules_not_imported)}")
    if result.notes:
        lines.append(f"Notes: {_listed(result.notes)}")
    return lines


def data_file_scan_summary(scan: commands_outlook.ScanResult) -> str:
    """What an import of the data files would bring in and where it goes, in plain words."""
    lines: list[str] = profile_scan_lines(scan.profile)
    for found in scan.files:
        if found.problem:
            lines.append(f"{found.file}: {found.problem}")
            continue
        lines.append(f"{found.name}:")
        if found.place:
            lines.append(f"  Mail: {counted(found.mail, 'message')}, into {found.place}, in the folders as {DATA_FILE_SOURCE_NAME} showed them.")
        else:
            lines.append("  Mail: comes from the account's server, so it is taken from there.")
        lines.append(
            f"  Into Local Folders: {counted(found.events, 'appointment')}, {counted(found.contacts, 'contact')} and groups, {counted(found.tasks, 'task')}."
        )
        if found.left_out:
            lines.append(f"  Stays in the data file (Chrono Mail has no place for it): {_listed(found.left_out)}.")
        if found.done:
            lines.append(f"  {counted(found.done, 'item')} done by an earlier import (it continues).")
    return "\n".join(lines) if lines else "Nothing to import was found."


def merge_data_files(total: commands_outlook.ImportResult, batch: commands_outlook.ImportResult) -> commands_outlook.ImportResult:
    """The results of two runs of one import from data files as one."""
    return replace(
        total,
        mail_imported=total.mail_imported + batch.mail_imported, mail_existing=total.mail_existing + batch.mail_existing, mail_failed=total.mail_failed + batch.mail_failed,
        events_imported=total.events_imported + batch.events_imported, events_existing=total.events_existing + batch.events_existing,
        events_failed=total.events_failed + batch.events_failed, contacts_imported=total.contacts_imported + batch.contacts_imported,
        contacts_existing=total.contacts_existing + batch.contacts_existing, contacts_failed=total.contacts_failed + batch.contacts_failed,
        groups_imported=total.groups_imported + batch.groups_imported, groups_existing=total.groups_existing + batch.groups_existing,
        groups_failed=total.groups_failed + batch.groups_failed, tasks_imported=total.tasks_imported + batch.tasks_imported,
        tasks_existing=total.tasks_existing + batch.tasks_existing, tasks_failed=total.tasks_failed + batch.tasks_failed, unreadable=total.unreadable + batch.unreadable,
        profile=batch.profile if batch.profile != ProfileImport() else total.profile,
        failures=total.failures + batch.failures, tags_added=total.tags_added + batch.tags_added, items_tagged=total.items_tagged + batch.items_tagged, done=total.done + batch.done, left=batch.left, notes=total.notes + batch.notes,
    )


def data_file_import_summary(result: commands_outlook.ImportResult, stopped: bool) -> str:
    profile = result.profile
    nothing_new = not (
        result.mail_imported or result.events_imported or result.contacts_imported or result.groups_imported or result.tasks_imported or result.tags_added
        or result.items_tagged or profile.accounts_added or profile.rules_added or profile.signatures
    )
    lines = [_first_line(stopped, nothing_new)]
    lines.extend(profile_import_lines(result.profile))
    for label, imported, existing, failed in (
        ("Messages", result.mail_imported, result.mail_existing, result.mail_failed), ("Appointments", result.events_imported, result.events_existing, result.events_failed),
        ("Contacts", result.contacts_imported, result.contacts_existing, result.contacts_failed), ("Groups", result.groups_imported, result.groups_existing, result.groups_failed),
        ("Tasks", result.tasks_imported, result.tasks_existing, result.tasks_failed),
    ):
        if imported or existing or failed:
            lines.append(f"{label} imported: {imported:,}; already here: {existing:,}; failed: {failed:,}.")
    if result.tags_added:
        lines.append(f"Tags made: {result.tags_added}.")
    if result.items_tagged:
        lines.append(f"Imported earlier, now with their tags: {counted(result.items_tagged, 'item')}.")
    if result.unreadable:
        lines.append(f"Items the data file could not give: {result.unreadable:,}.")
    if result.failures:
        lines.append(f"Failed: {_listed(result.failures)}")
    if result.notes:
        lines.append(f"Notes: {_listed(result.notes)}")
    return "\n".join(lines)


def starting_place(path: str, memory: Remembered | None) -> str:
    """Where a picker opens: the path in its own field; else the folder of the last pick of any Browse; else the home folder."""
    return path or (memory.last_picked_folder if memory is not None else "") or os.path.expanduser("~")


def remember_pick(memory: Remembered | None, path: str) -> None:
    """A Browse picked this file or folder: the next Browse with an empty field opens in its folder, also in a new window."""
    if memory is not None and path:
        memory.last_picked_folder = os.path.dirname(os.path.normpath(path))


class ImportDialog(QDialog):
    """Menu > File > Import…: the program to import from, what to import, from which exports, and the import itself, in one window."""

    def __init__(
        self, backend: Backend, on_changed: Callable[[], None], parent: QWidget | None = None, on_running: Callable[[bool], None] | None = None,
        memory: Remembered | None = None,
    ) -> None:
        super().__init__(parent)
        self._memory = memory  # the paths of the last import, on this computer (None: nothing remembered)
        self._backend = backend
        self._on_changed = on_changed
        self._on_running = on_running  # told True when an import starts and False when it ends (the main window holds its mail checks)
        self._running = False
        self._stop = False
        self._total = 0
        self._done_before = 0
        self._scan_number = 0  # the latest reading of the exports; the answer of an older one is ignored
        self._result = ImportResult()
        self._data_result = commands_outlook.ImportResult()
        self._profile = ProfileScan()  # what the latest reading found of the profile on this computer
        self._profile_sent = False  # the accounts, rules and signatures go with the first batch of an import only
        self._finished_parts: tuple[str, ...] = ()  # what an earlier import of the chosen exports went through to the end
        self._scanned_files: tuple[commands_outlook.FileScan, ...] = ()  # the latest reading of the chosen data files
        self._again = False  # this import starts over (sent with its first batch only)
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
        self.data_file_item = QTreeWidgetItem(group, [DATA_FILE_SOURCE_NAME])
        group.setExpanded(True)
        self.sources.setCurrentItem(self.source_item)
        self.sources.setFixedWidth(wpx(220))
        self.pages: QStackedWidget = forms.part(form, QStackedWidget, "pages")  # a page per program
        self.exports_page: QWidget = forms.part(form, QWidget, "exportsPage")
        self.data_file_page: QWidget = forms.part(form, QWidget, "dataFilePage")
        forms.part(form, QLabel, "title").setText(f"Import data from {SOURCE_NAME}")  # the programs' names only here (D-046)
        forms.part(form, QLabel, "dataFileTitle").setText(f"Import data from {DATA_FILE_SOURCE_NAME}")
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
        if memory is not None:
            self.settings_file.setText(memory.import_settings_file)
            self.mail_folder.setText(memory.import_mail_folder)
            self.backup_file.setText(memory.import_backup_file)
        self.data_file_mail: QCheckBox = forms.part(form, QCheckBox, "dataFileMail")
        self.data_file_calendar: QCheckBox = forms.part(form, QCheckBox, "dataFileCalendar")
        self.data_file_contacts: QCheckBox = forms.part(form, QCheckBox, "dataFileContacts")
        self.data_file_tasks: QCheckBox = forms.part(form, QCheckBox, "dataFileTasks")
        self.data_file_tags: QCheckBox = forms.part(form, QCheckBox, "dataFileTags")
        for box in self._data_file_types():
            box.setChecked(True)
        self.data_file_later: tuple[QCheckBox, ...] = tuple(forms.part(form, QCheckBox, name) for name in ("dataFileAccounts", "dataFileRules", "dataFileSignatures"))
        for box in self.data_file_later:
            box.setEnabled(False)  # greyed, never hidden (OR-15): these are read on the Windows computer itself
            box.setToolTip(WINDOWS_ONLY)
        self.data_files: QLineEdit = forms.part(form, QLineEdit, "dataFiles")
        self._picked: tuple[str, ...] = ()  # the files chosen with Browse, as the picker gave them
        forms.part(form, QPushButton, "browseDataFiles").clicked.connect(self._browse_data_files)
        self.data_files.editingFinished.connect(self.scan)
        self.summary: QLabel = forms.part(form, QLabel, "summary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # names from the exports: never markup
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_area: QScrollArea = forms.part(form, QScrollArea, "summaryArea")  # a long summary scrolls instead of falling off the window
        self._exports_hint = self.summary.text()
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
        self.sources.currentItemChanged.connect(self._show_page)

    def _data_file_types(self) -> tuple[QCheckBox, ...]:
        return (self.data_file_mail, self.data_file_calendar, self.data_file_contacts, self.data_file_tasks, self.data_file_tags)

    def _reading_data_files(self) -> bool:
        """The data file page is shown (else the page of the exports)."""
        return self.pages.currentWidget() is self.data_file_page

    def _show_page(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if current is None or current not in (self.source_item, self.data_file_item):
            return  # the heading is never a choice
        data_files = current is self.data_file_item
        self.pages.setCurrentWidget(self.data_file_page if data_files else self.exports_page)
        self._scan_number += 1  # an answer for the other page changes nothing here
        self.import_button.setEnabled(False)
        self.summary.setText(DATA_FILE_HINT if data_files else self._exports_hint)
        self.scan()

    # --- choosing the exports -----------------------------------------------------------------

    def _browse_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "The settings export", starting_place(self.settings_file.text(), self._memory), "Settings (*.xml)")
        remember_pick(self._memory, path)
        if path:
            self.settings_file.setText(path)
            self.scan()

    def _browse_mail(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "The folder of the mail export", starting_place(self.mail_folder.text(), self._memory))
        remember_pick(self._memory, path)
        if path:
            self.mail_folder.setText(path)
            self.scan()

    def _browse_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "The backup", starting_place(self.backup_file.text(), self._memory), "Backup (*.zip)")
        remember_pick(self._memory, path)
        if path:
            self.backup_file.setText(path)
            self.scan()

    def _browse_data_files(self) -> None:
        start = self._picked[0] if self._picked else self.data_files.text().strip()
        if not start and self._memory is not None:
            start = self._memory.last_picked_folder or self._memory.import_data_file_folder
        start = starting_place(start, self._memory)
        paths, _ = QFileDialog.getOpenFileNames(self, "The data files", start, "Outlook data files (*.pst)")
        if paths:
            remember_pick(self._memory, paths[0])
            self._picked = tuple(paths)
            self.data_files.setText(DATA_FILE_SEPARATOR.join(paths))
            self.scan()

    def _chosen_data_files(self) -> tuple[str, ...]:
        """The files chosen with Browse while the field shows them; a path typed into the field is one file."""
        text = self.data_files.text().strip()
        if self._picked and text == DATA_FILE_SEPARATOR.join(self._picked):
            return self._picked
        return (text,) if text else ()

    def _sources(self) -> ScanParameters:
        return ScanParameters(self.settings_file.text().strip() or None, self.mail_folder.text().strip() or None, self.backup_file.text().strip() or None)

    def scan(self) -> None:
        if self._running:
            return
        self._scan_number += 1
        number = self._scan_number
        self.import_button.setEnabled(False)
        if self._reading_data_files():
            files = self._chosen_data_files()
            profile = PROFILE_HERE
            if files or profile:
                self.summary.setText("Reading the data files…")
                self._backend.ask(
                    IMPORT_OUTLOOK_SCAN, commands_outlook.ScanParameters(files, profile=profile), lambda result: self._data_files_scanned(number, result),
                    lambda error: self._scan_failed(number, error), owner=self,
                )
            return
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
        self._finished_parts = result.finished
        self.progress.setRange(0, max(1, self._total))
        self.summary.setText(scan_summary(result))
        self.import_button.setEnabled(True)

    def _data_files_scanned(self, number: int, result: commands_outlook.ScanResult) -> None:
        if self._running or number != self._scan_number:
            return
        self._total = result.items
        self._done_before = result.done
        self._scanned_files = result.files
        self.progress.setRange(0, max(1, self._total))
        if not self._chosen_data_files() and result.profile.files:  # the profile's own data files, without asking for a file
            self._picked = result.profile.files
            self.data_files.setText(DATA_FILE_SEPARATOR.join(self._picked))
        self._profile = result.profile
        self._offer_profile()
        self.summary.setText(data_file_scan_summary(result))
        self.import_button.setEnabled(any(not found.problem for found in result.files) or result.profile.available)  # off when there is nothing to import

    def _offer_profile(self) -> None:
        """Accounts and Signatures when the profile is readable, Rules while Outlook runs; greyed with why otherwise (OR-15)."""
        accounts, rules, signatures = self.data_file_later
        for box, available, reason in (
            (accounts, self._profile.available, self._profile.reason), (rules, self._profile.rules_available, self._profile.rules_reason),
            (signatures, self._profile.available, self._profile.reason),
        ):
            if available and not box.isEnabled():
                box.setChecked(True)
            box.setEnabled(available and not self._running)
            box.setToolTip("" if available else reason or WINDOWS_ONLY)

    def _scan_failed(self, number: int, error: ChronoMailError) -> None:
        if self._running or number != self._scan_number:
            return
        self.summary.setText(str(error))  # Import stays off: there is nothing it could import

    def _failed(self, error: ChronoMailError) -> None:
        """A batch failed: the window is reloaded (this batch may have stored part of its work), the summary counts the earlier batches, and the check after an import runs (D-143)."""
        self._finish()
        done = data_file_import_summary(self._data_result, stopped=True) if self._reading_data_files() else import_summary(self._result, stopped=True)
        self.summary.setText(f"{error}\n{done}")
        self._on_changed()

    # --- the import ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._running or not self.import_button.isEnabled():
            return  # only after the reading of the exports has answered
        self._again = self._imported_before()
        if self._again and not ask_import_again(self):
            return  # Cancel: nothing happens
        self._remember_paths()
        self._running = True
        if self._on_running is not None:
            self._on_running(True)
        self._stop = False
        self._result = ImportResult()
        self._data_result = commands_outlook.ImportResult()
        self._profile_sent = False
        for widget in self._inputs():
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, self._total))
        self.progress.setValue(0 if self._again else self._done_before)
        if self._again:
            self._done_before = 0
        self.summary.setText("Importing…")
        self._next()

    def _remember_paths(self) -> None:
        """The paths of this import, for the next Import window on this computer."""
        if self._memory is None:
            return
        if self._reading_data_files():
            files = self._chosen_data_files()
            if files:
                self._memory.import_data_file_folder = os.path.dirname(files[0])
            return
        sources = self._sources()
        self._memory.import_settings_file = sources.settings_file or ""
        self._memory.import_mail_folder = sources.mail_folder or ""
        self._memory.import_backup_file = sources.backup_file or ""

    def _chosen_parts(self) -> frozenset[str]:
        """The parts of the exports this import goes through (as the engine names them, commands_emclient.PARTS)."""
        sources = self._sources()
        chosen = (
            (self.accounts, sources.settings_file), (self.rules, sources.settings_file), (self.mail, sources.mail_folder), (self.contacts, sources.backup_file),
            (self.tags, "tags come from any export"),
        )
        return frozenset(part for part, (box, export) in zip(PARTS, chosen, strict=True) if box.isChecked() and export)

    def _imported_before(self) -> bool:
        """Everything chosen was imported to the end by an earlier import of these very exports or data files (a stopped one just continues)."""
        if self._reading_data_files():
            readable = [found for found in self._scanned_files if not found.problem]
            kinds = {"mail": self.data_file_mail, "events": self.data_file_calendar, "contacts": self.data_file_contacts, "tasks": self.data_file_tasks}
            chosen = {kind for kind, box in kinds.items() if box.isChecked()}
            return bool(readable) and all(found.finished and not chosen & set(found.left_out_kinds) for found in readable)
        parts = self._chosen_parts()
        return bool(parts) and parts <= set(self._finished_parts)

    def _inputs(self) -> tuple[QWidget, ...]:
        """What is off while an import runs."""
        return (
            self.import_button, self.close_button, self.sources, self.accounts, self.rules, self.mail, self.contacts, self.tags, self.settings_file, self.mail_folder,
            self.backup_file, *self._data_file_types(), self.data_files, *self.data_file_later,
        )

    def _next(self) -> None:
        if self._reading_data_files():
            choice = commands_outlook.ImportParameters(
                self._chosen_data_files(), mail=self.data_file_mail.isChecked(), calendar=self.data_file_calendar.isChecked(),
                contacts=self.data_file_contacts.isChecked(), tasks=self.data_file_tasks.isChecked(), tags=self.data_file_tags.isChecked(), batch=BATCH,
                again=self._again,
            )
            self._again = False  # the first batch starts over; the next ones continue
            if not self._profile_sent:
                accounts, rules, signatures = self.data_file_later
                choice = replace(
                    choice, accounts=self._profile.available and accounts.isChecked(), rules=self._profile.rules_available and rules.isChecked(),
                    signatures=self._profile.available and signatures.isChecked(),
                )
                self._profile_sent = True
            self._backend.ask(IMPORT_OUTLOOK, choice, self._data_file_batch_done, self._failed, owner=self)
            return
        sources = self._sources()
        parameters = ImportParameters(
            sources.settings_file, sources.mail_folder, sources.backup_file, accounts=self.accounts.isChecked(), rules=self.rules.isChecked(), mail=self.mail.isChecked(),
            contacts=self.contacts.isChecked(), tags=self.tags.isChecked(), batch=BATCH, again=self._again,
        )
        self._again = False  # the first batch starts over; the next ones continue
        self._backend.ask(IMPORT_EMCLIENT, parameters, self._batch_done, self._failed, owner=self)

    def _batch_done(self, result: ImportResult) -> None:
        self._result = merge(self._result, result)
        self._done_before += result.messages_imported + result.messages_duplicate + len(result.messages_failed)
        self.progress.setValue(min(self._done_before, self.progress.maximum()))
        if result.messages_left > 0 and not self._stop:
            self._next()
            return
        stopped = result.messages_left > 0
        if not stopped:  # Import once more in this window asks too
            self._finished_parts = tuple(sorted({*self._finished_parts, *self._chosen_parts()}))
        self._finish()
        self.summary.setText(import_summary(self._result, stopped))
        self._on_changed()

    def _data_file_batch_done(self, result: commands_outlook.ImportResult) -> None:
        self._data_result = merge_data_files(self._data_result, result)
        self._done_before += result.done
        self.progress.setValue(min(self._done_before, self.progress.maximum()))
        if result.left > 0 and not self._stop:
            self._next()
            return
        stopped = result.left > 0
        if not stopped:  # Import once more in this window asks too
            kinds = {"mail": self.data_file_mail, "events": self.data_file_calendar, "contacts": self.data_file_contacts, "tasks": self.data_file_tasks}
            left_out = tuple(kind for kind, box in kinds.items() if not box.isChecked())
            self._scanned_files = tuple(replace(found, finished=True, left_out_kinds=left_out) for found in self._scanned_files)
        self._finish()
        self.summary.setText(data_file_import_summary(self._data_result, stopped))
        self._on_changed()

    def request_stop(self) -> None:
        self._stop = True
        self.stop_button.setEnabled(False)
        self.summary.setText("Stopping after the items that are being imported now…" if self._reading_data_files() else "Stopping after the messages that are being imported now…")

    def _finish(self) -> None:
        self._running = False
        if self._on_running is not None:
            self._on_running(False)
        for widget in self._inputs():
            widget.setEnabled(True)
        self._offer_profile()  # what the profile cannot give stays greyed
        self.stop_button.setEnabled(False)

    def _progress(self, what: str, done: int, total: object) -> None:
        if self._running and what in ("Importing mail", PROGRESS_TEXT):
            self.progress.setValue(min(done, self.progress.maximum()))  # the engine counts over the whole import

    @override
    def reject(self) -> None:
        if not self._running:  # while it runs, Stop first: the batch that runs is finished either way
            super().reject()
