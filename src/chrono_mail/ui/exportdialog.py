from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.core.words import counted
from chrono_mail.engine.commands_outlook_export import (
    EXPORT_OUTLOOK, EXPORT_OUTLOOK_CHOICES, EXPORT_OUTLOOK_STOP, PROGRESS_TEXT, ChoicesParameters, ExportChoices, ExportParameters, ExportResult,
    StopParameters, StopResult,
)
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.importdialog import starting_place
from chrono_mail.ui.prefs import Remembered
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "exportdialog.py 4.2.389"

# --- Version history ----------------------------------------------------
# v4.2.389: the target folder starts as the one exported to last (remembered when an export starts, on this computer); one that is gone
#           is left out and Browse opens in the home folder
# v4.2.386: Initial version: Menu > File > Export: the export to Outlook's data files, built from its Qt Designer form (ui/forms/export.ui,
#           D-276) as the Import window is; the program's name only in this file (OR-10); Rules greyed off Windows with why (OR-15)

PROGRAM_NAME: str = "Outlook"
KINDS: dict[str, str] = {"pop3": "POP3: a data file of its own", "imap": "IMAP: the account only", "local": "a data file with the calendars, contacts and tasks"}
LISTED: int = 10


def export_summary(result: ExportResult) -> str:
    """What the export wrote, in plain words."""
    lines = ["The export was stopped; the files written before it stopped are complete." if result.stopped else "The export is done."]
    if result.files:
        lines.append("Written: " + "; ".join(result.files) + ".")
    lines.append(
        f"{counted(result.messages, 'message')}, {counted(result.events, 'event')}, {counted(result.contacts, 'contact')}, "
        f"{counted(result.groups, 'contact group')}, {counted(result.tasks, 'task')}."
    )
    if result.accounts:
        lines.append(f"Accounts in the profile file: {result.accounts}.")
    if result.rules:
        lines.append(f"Rules made in {PROGRAM_NAME}: {result.rules}.")
    if result.failed:
        lines.append(f"Not exported: {result.failed}: " + "; ".join(result.failures[:LISTED]) + ("; …" if len(result.failures) > LISTED else "") + ".")
    lines += list(result.notes)
    return "\n".join(lines)


class ExportDialog(QDialog):
    """File > Export: what to export, which accounts, where to; then the progress, Stop and what was written. All work is the engine's."""

    def __init__(self, backend: Backend, parent: QWidget | None = None, memory: Remembered | None = None) -> None:
        super().__init__(parent)
        self._memory = memory  # the folder of the last export, on this computer (None: nothing remembered)
        self._backend = backend
        self._running = False
        form = forms.fill(self, "export")  # D-276
        self.programs: QTreeWidget = forms.part(form, QTreeWidget, "programs")
        self.programs.setHeaderHidden(True)
        self.programs.setRootIsDecorated(False)
        group = QTreeWidgetItem(self.programs, ["Applications"])
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        self.program_item = QTreeWidgetItem(group, [PROGRAM_NAME])
        group.setExpanded(True)
        self.programs.setCurrentItem(self.program_item)
        self.programs.setFixedWidth(wpx(200))
        forms.part(form, QLabel, "title").setText(f"Export data to {PROGRAM_NAME}")
        self.mail: QCheckBox = forms.part(form, QCheckBox, "mail")
        self.calendar: QCheckBox = forms.part(form, QCheckBox, "calendar")
        self.contacts: QCheckBox = forms.part(form, QCheckBox, "contacts")
        self.tasks: QCheckBox = forms.part(form, QCheckBox, "tasks")
        self.tags: QCheckBox = forms.part(form, QCheckBox, "tags")
        self.accounts: QCheckBox = forms.part(form, QCheckBox, "accounts")
        self.rules: QCheckBox = forms.part(form, QCheckBox, "rules")
        for box in (self.mail, self.calendar, self.contacts, self.tasks, self.tags, self.accounts):
            box.setChecked(True)
        self.rules.setEnabled(False)  # until the choices say it can be done here
        self.account_list: QListWidget = forms.part(form, QListWidget, "accountList")
        self.folder: QLineEdit = forms.part(form, QLineEdit, "folder")
        self.folder.textChanged.connect(self._show_state)
        forms.part(form, QPushButton, "browseFolder").clicked.connect(self._browse)
        self.summary: QLabel = forms.part(form, QLabel, "summary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # file and account names: never markup
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress: QProgressBar = forms.part(form, QProgressBar, "progress")
        self.progress.setVisible(False)
        self.export_button: QPushButton = forms.part(form, QPushButton, "export")
        self.stop_button: QPushButton = forms.part(form, QPushButton, "stop")
        self.close_button: QPushButton = forms.part(form, QPushButton, "close")
        self.export_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.request_stop)
        self.close_button.clicked.connect(self.reject)
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(False)
        if memory is not None:
            self.folder.setText(memory.export_folder)
        self.setMinimumHeight(wpx(560))
        forms.smallest(self, 760)
        backend.progress.connect(self._progress)
        backend.ask(EXPORT_OUTLOOK_CHOICES, ChoicesParameters(), self._chosen, self._failed, owner=self)

    def _chosen(self, choices: ExportChoices) -> None:
        self.account_list.clear()
        for account in choices.accounts:
            item = QListWidgetItem(f"{account.name} ({KINDS.get(account.kind, account.kind)})", self.account_list)
            item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
        self.rules.setEnabled(choices.rules_available)
        self.rules.setChecked(choices.rules_available)
        self.rules.setToolTip("" if choices.rules_available else choices.rules_reason)
        self._show_state()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "The folder to write the files in", starting_place(self.folder.text()))
        if path:
            self.folder.setText(path)

    def chosen_accounts(self) -> tuple[str, ...]:
        found: list[str] = []
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                found.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(found)

    def _show_state(self) -> None:
        self.export_button.setEnabled(not self._running and bool(self.folder.text().strip()) and self.account_list.count() > 0)

    def _inputs(self) -> tuple[QWidget, ...]:
        return (self.export_button, self.close_button, self.programs, self.mail, self.calendar, self.contacts, self.tasks, self.tags, self.accounts,
                self.account_list, self.folder)

    def start(self) -> None:
        if self._running or not self.export_button.isEnabled():
            return
        accounts = self.chosen_accounts()
        if not accounts:
            self.summary.setText("Choose at least one account.")
            return
        if self._memory is not None:
            self._memory.export_folder = self.folder.text().strip()
        self._running = True
        for widget in (*self._inputs(), self.rules):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.summary.setText("Exporting…")
        parameters = ExportParameters(
            self.folder.text().strip(), mail=self.mail.isChecked(), calendar=self.calendar.isChecked(), contacts=self.contacts.isChecked(),
            tasks=self.tasks.isChecked(), tags=self.tags.isChecked(), accounts=self.accounts.isChecked(), rules=self.rules.isChecked(),
            account_ids=accounts,
        )
        self._backend.ask(EXPORT_OUTLOOK, parameters, self._done, self._failed, owner=self)

    def _done(self, result: ExportResult) -> None:
        self._finish()
        self.summary.setText(export_summary(result))

    def _failed(self, error: ChronoMailError) -> None:
        self._finish()
        self.summary.setText(str(error))

    def request_stop(self) -> None:
        self.stop_button.setEnabled(False)
        self.summary.setText("Stopping…")
        self._backend.ask(EXPORT_OUTLOOK_STOP, StopParameters(), self._stopping, self._failed, owner=self)

    def _stopping(self, result: StopResult) -> None:
        pass  # the export's own answer says what was written

    def _finish(self) -> None:
        was_running = self._running
        self._running = False
        for widget in self._inputs():
            widget.setEnabled(True)
        if was_running:
            self.rules.setEnabled(bool(self.rules.toolTip() == ""))
        self.stop_button.setEnabled(False)
        self.progress.setVisible(False)
        self._show_state()

    def _progress(self, what: str, done: int, total: object) -> None:
        if self._running and what == PROGRESS_TEXT and isinstance(total, int) and total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))

    @override
    def reject(self) -> None:
        if not self._running:  # while it runs, Stop first
            super().reject()
